"""Purpose-bound SQLite preparation helper: capture an offline logical snapshot
and verify it later. No CLI, network, subprocess, or application imports."""
import datetime as _dt
import hashlib
import json
import math
import os
import re
import sqlite3
import stat as _stat
import time
import urllib.parse

_SCHEMA_VERSION = 1
_DEADLINE_SECONDS = 30.0
_MAX_DB_BYTES = 512 * 1024 * 1024
_MAX_AUX_TOTAL = 512 * 1024 * 1024
_MAX_CODE_BYTES = 512 * 1024
_MAX_REPORT_BYTES = 65536
_MAX_RECEIPT_BYTES = 1024 * 1024
_MAX_VALUE_BYTES = 1024 * 1024
_MAX_ROWS = 500000
_MAX_ENCODED_TOTAL = 256 * 1024 * 1024
_MAX_SCHEMA_ROWS = 128
_BACKUP_PAGES = 64
_BACKUP_SLEEP = 0.01

_MEMORY_DDL = (
    "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);\n"
    "CREATE TABLE IF NOT EXISTS sources (\n"
    "  id TEXT PRIMARY KEY,\n  guild_id TEXT NOT NULL,\n  channel_id TEXT NOT NULL,\n"
    "  parent_id TEXT,\n  author_id TEXT NOT NULL,\n  author_name TEXT NOT NULL,\n"
    "  content TEXT NOT NULL,\n  source_time INTEGER NOT NULL,\n"
    "  received_time INTEGER NOT NULL,\n  revision INTEGER NOT NULL DEFAULT 0,\n"
    "  content_hash TEXT NOT NULL\n);\n"
    "CREATE TABLE IF NOT EXISTS memories (\n"
    "  id TEXT PRIMARY KEY,\n  guild_id TEXT NOT NULL,\n  text TEXT NOT NULL,\n"
    "  requester_id TEXT NOT NULL,\n  speaker_ids TEXT NOT NULL,\n"
    "  source_ids TEXT NOT NULL,\n  admission TEXT NOT NULL,\n"
    "  admission_evidence TEXT NOT NULL,\n  status TEXT NOT NULL,\n"
    "  revision INTEGER NOT NULL DEFAULT 0,\n  active INTEGER NOT NULL DEFAULT 1,\n"
    "  created_at INTEGER NOT NULL,\n  request_id TEXT,\n  request_hash TEXT,\n"
    "  review_evidence TEXT,\n  kind TEXT,\n  valid_until INTEGER\n);\n"
    "CREATE TABLE IF NOT EXISTS memory_sources (\n"
    "  memory_id TEXT NOT NULL REFERENCES memories(id) ON DELETE CASCADE,\n"
    "  source_id TEXT NOT NULL REFERENCES sources(id),\n"
    "  pin INTEGER NOT NULL DEFAULT 0,\n  PRIMARY KEY (memory_id, source_id)\n);\n"
    "CREATE TABLE IF NOT EXISTS candidates (\n"
    "  id TEXT PRIMARY KEY,\n  guild_id TEXT NOT NULL,\n  text TEXT NOT NULL,\n"
    "  kind TEXT NOT NULL,\n  source_ids TEXT NOT NULL,\n  author_ids TEXT NOT NULL,\n"
    "  evidence_hashes TEXT NOT NULL,\n  text_hash TEXT NOT NULL,\n"
    "  valid_until INTEGER,\n  created_at INTEGER NOT NULL,\n"
    "  status TEXT NOT NULL DEFAULT 'pending',\n  batch_id TEXT\n);\n"
    "CREATE TABLE IF NOT EXISTS batches (\n"
    "  id TEXT PRIMARY KEY,\n  guild_id TEXT NOT NULL,\n  hash TEXT NOT NULL,\n"
    "  snapshot TEXT NOT NULL,\n  status TEXT NOT NULL DEFAULT 'open',\n"
    "  message_id TEXT,\n  created_at INTEGER NOT NULL\n);\n"
    "CREATE TABLE IF NOT EXISTS outbox (\n"
    "  seq INTEGER PRIMARY KEY AUTOINCREMENT,\n  operation_id TEXT NOT NULL UNIQUE,\n"
    "  memory_id TEXT NOT NULL,\n  revision INTEGER NOT NULL,\n"
    "  action TEXT NOT NULL,\n  status TEXT NOT NULL DEFAULT 'pending',\n"
    "  payload TEXT NOT NULL,\n  created_at INTEGER NOT NULL,\n  receipt TEXT\n);\n"
    "CREATE INDEX IF NOT EXISTS idx_candidates_status ON candidates(status, created_at);\n"
    "CREATE INDEX IF NOT EXISTS idx_outbox_status ON outbox(status, seq);\n"
    "CREATE INDEX IF NOT EXISTS idx_memories_active ON memories(active, status);\n"
)
_METER_DDL = (
    "CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);\n"
    "CREATE TABLE IF NOT EXISTS checkpoints(id TEXT PRIMARY KEY,up INTEGER,down INTEGER,ip TEXT,mac TEXT,owner TEXT,route TEXT,node TEXT);\n"
    "CREATE TABLE IF NOT EXISTS usage_minute(bucket INTEGER,owner TEXT,mac TEXT,ip TEXT,node TEXT,route TEXT,up INTEGER,down INTEGER,PRIMARY KEY(bucket,owner,mac,ip,node,route));\n"
    "CREATE TABLE IF NOT EXISTS usage_day(bucket TEXT,owner TEXT,mac TEXT,ip TEXT,node TEXT,route TEXT,up INTEGER,down INTEGER,PRIMARY KEY(bucket,owner,mac,ip,node,route));\n"
    "CREATE TABLE IF NOT EXISTS health(bucket TEXT PRIMARY KEY,samples INTEGER DEFAULT 0,observed_seconds REAL DEFAULT 0,gaps INTEGER DEFAULT 0,resets INTEGER DEFAULT 0,baselines INTEGER DEFAULT 0);\n"
)

_WHITELIST = {
    "memory": frozenset(
        ("meta", "sources", "memories", "memory_sources", "candidates",
         "batches", "outbox", "sqlite_sequence")
    ),
    "meter": frozenset(
        ("meta", "checkpoints", "usage_minute", "usage_day", "health")
    ),
}
_CODE_KEYS = {
    "memory": ("src/memory.mjs", "src/vpn.mjs"),
    "meter": ("meter.py",),
}
_DB_BASENAMES = {"memory": "conversations.sqlite3", "meter": "usage.sqlite"}

_CUTOFF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
_HEX64_RE = re.compile(r"^[0-9a-f]{64}$")
_ID_RE = re.compile(r"^[0-9]{17,20}$")
_CTRL_RE = re.compile(r"[\x00-\x1f\x7f]")


class RecoveryError(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def _fail(code):
    raise RecoveryError(code)


def _now(clock=None):
    return time.monotonic() if clock is None else clock()


class _Deadline:
    def __init__(self, clock=None):
        self._clock = clock
        self._end = _now(clock) + _DEADLINE_SECONDS

    def check(self):
        if _now(self._clock) >= self._end:
            _fail("deadline_exceeded")

    def remaining(self):
        self.check()
        return max(0.001, self._end - _now(self._clock))

    def remaining_ms(self):
        return max(1, int(self.remaining() * 1000))


# ---- validation helpers --------------------------------------------------

def _is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def _is_hex64(v):
    return isinstance(v, str) and _HEX64_RE.fullmatch(v) is not None


def _is_id(v):
    return isinstance(v, str) and _ID_RE.fullmatch(v) is not None


# ---- path / stat helpers -------------------------------------------------

def _absnorm(name, p):
    if not isinstance(p, str) or not p or "\x00" in p:
        _fail("path_invalid:" + name)
    if _CTRL_RE.search(p):
        _fail("path_control:" + name)
    if not os.path.isabs(p):
        _fail("path_not_absolute:" + name)
    if os.path.normpath(p) != p or p == "/":
        _fail("path_not_normalized:" + name)
    return p


def _ancestors(p):
    parts = p.split("/")[1:]
    cur = ""
    for part in parts:
        cur = cur + "/" + part
        yield cur


def _require_real_dirs_up_to_final(p, name):
    try:
        for anc in _ancestors(p):
            if anc == p:
                break
            st = os.lstat(anc)
            if not _stat.S_ISDIR(st.st_mode) or _stat.S_ISLNK(st.st_mode):
                _fail("ancestor_invalid:" + name)
    except OSError:
        _fail("ancestor_invalid:" + name)


def _own_regular(p, name, uid, max_bytes=None):
    try:
        st = os.lstat(p)
    except OSError:
        _fail("not_regular:" + name)
    if _stat.S_ISLNK(st.st_mode) or not _stat.S_ISREG(st.st_mode):
        _fail("not_regular:" + name)
    if st.st_uid != uid:
        _fail("not_owner:" + name)
    if st.st_nlink != 1:
        _fail("hardlink:" + name)
    if max_bytes is not None and st.st_size > max_bytes:
        _fail("too_large:" + name)
    return st


def _mode(st):
    return _stat.S_IMODE(st.st_mode)


def _full_sig(st):
    return (st.st_dev, st.st_ino, st.st_size, st.st_nlink, st.st_mode,
            st.st_mtime_ns, st.st_ctime_ns, st.st_uid)


def _within(child, parent):
    return child == parent or child.startswith(parent + "/")


def _disjoint_roots(a, b):
    if _within(a, b) or _within(b, a):
        _fail("paths_overlap")


def _sha_file(p, limit, deadline):
    h = hashlib.sha256()
    n = 0
    try:
        with open(p, "rb") as f:
            while True:
                deadline.check()
                chunk = f.read(65536)
                if not chunk:
                    break
                n += len(chunk)
                if n > limit:
                    _fail("code_too_large")
                h.update(chunk)
    except OSError:
        _fail("read_io_error")
    return h.hexdigest(), n


# ---- trusted schema / value encoding -------------------------------------

def _schema_rows(conn, deadline):
    deadline.check()
    rows = conn.execute(
        "SELECT type,name,tbl_name,sql FROM sqlite_master"
        " ORDER BY type,name,tbl_name,sql LIMIT %d" % (_MAX_SCHEMA_ROWS + 1)
    ).fetchall()
    if len(rows) > _MAX_SCHEMA_ROWS:
        _fail("schema_row_cap")
    return rows


def _schema_signature(rows):
    h = hashlib.sha256()
    for r in rows:
        ent = json.dumps([r[0], r[1], r[2], r[3]], ensure_ascii=True,
                         separators=(",", ":"))
        h.update(ent.encode("ascii") + b"\n")
    return h.hexdigest()


def _reference_schema_sha(profile):
    if profile == "memory":
        ddl = _MEMORY_DDL
    elif profile == "meter":
        ddl = _METER_DDL
    else:
        _fail("profile_invalid")
    mem = sqlite3.connect(":memory:")
    try:
        mem.executescript(ddl)
        sig = _schema_signature(_schema_rows(mem, _Deadline()))
    finally:
        mem.close()
    return sig


def _encode_cell(v, budget):
    if v is None:
        b = b""
        tag = b"N"
    else:
        t = type(v)
        if t is int:
            b = str(v).encode("ascii")
            tag = b"I"
        elif t is float:
            if v != v or v in (float("inf"), float("-inf")):
                _fail("nonfinite_float")
            b = v.hex().encode("ascii")
            tag = b"F"
        elif t is str:
            try:
                b = v.encode("utf-8", "surrogatepass")
            except UnicodeError:
                _fail("value_unencodable")
            tag = b"T"
        elif t is bytes:
            b = bytes(v)
            tag = b"B"
        else:
            _fail("unknown_value_type")
        if len(b) > _MAX_VALUE_BYTES:
            _fail("value_cap")
    enc = tag + len(b).to_bytes(8, "big") + b
    budget[0] += len(enc)
    if budget[0] > _MAX_ENCODED_TOTAL:
        _fail("encoded_cap")
    return enc


def _table_columns(conn, table, deadline):
    deadline.check()
    return [r[1] for r in conn.execute(
        'PRAGMA table_info("' + table.replace('"', '""') + '")').fetchall()]


def _logical_fingerprint(conn, profile, deadline):
    allowed = _WHITELIST[profile]
    tables = sorted(allowed)
    budget = [0]
    total_rows = 0
    digests = {}
    counters = _empty_counters(profile)
    for table in tables:
        deadline.check()
        cols = _table_columns(conn, table, deadline)
        if not cols:
            if table == "sqlite_sequence":
                continue
            _fail("schema_table_missing")
        collist = ", ".join('"' + c.replace('"', '""') + '"' for c in cols)
        rowhashes = []
        cur = conn.execute('SELECT ' + collist + ' FROM "' +
                           table.replace('"', '""') + '"')
        while True:
            deadline.check()
            row = cur.fetchone()
            if row is None:
                break
            deadline.check()
            total_rows += 1
            if total_rows > _MAX_ROWS:
                _fail("row_cap")
            rh = hashlib.sha256()
            rowbytes = 0
            for cell in row:
                enc = _encode_cell(cell, budget)
                rowbytes += len(enc)
                rh.update(enc)
            if rowbytes > _MAX_VALUE_BYTES:
                _fail("row_cap")
            rowhashes.append(rh.digest())
            _step_counters(counters, profile, table, cols, row)
        deadline.check()
        rowhashes.sort()
        deadline.check()
        th = hashlib.sha256()
        th.update(json.dumps(cols, ensure_ascii=True,
                             separators=(",", ":")).encode("ascii"))
        th.update(len(rowhashes).to_bytes(8, "big"))
        for d in rowhashes:
            deadline.check()
            th.update(d)
        digests[table] = {"columns": cols, "rows": len(rowhashes),
                          "digest": th.hexdigest()}
    _finalize_counters(counters, profile, digests)
    return digests, counters


def _empty_counters(profile):
    if profile == "memory":
        return {"active": 0, "shared": 0, "local_saved": 0, "inactive": 0,
                "pending_candidates": 0, "open_batches": 0,
                "outbox_pending": 0, "outbox_acknowledged": 0,
                "outbox_receipt_present": 0}
    return {"usage_minute_up": 0, "usage_minute_down": 0,
            "usage_day_up": 0, "usage_day_down": 0}


def _strict_int_usage(v, counter, key):
    if v is None:
        _fail("usage_invalid")
    if not _is_int(v) or v < 0:
        _fail("usage_invalid")
    counter[key] += v


def _step_counters(c, profile, table, cols, row):
    rec = dict(zip(cols, row))
    if profile == "memory":
        if table == "memories":
            status = rec.get("status")
            if rec.get("active") == 1:
                c["active"] += 1
                if status == "shared":
                    c["shared"] += 1
                elif status == "local_saved":
                    c["local_saved"] += 1
            else:
                c["inactive"] += 1
        elif table == "candidates":
            if rec.get("status") == "pending":
                c["pending_candidates"] += 1
        elif table == "batches":
            if rec.get("status") == "open":
                c["open_batches"] += 1
        elif table == "outbox":
            stv = rec.get("status")
            if stv == "pending":
                c["outbox_pending"] += 1
            elif stv == "acknowledged":
                c["outbox_acknowledged"] += 1
            if rec.get("receipt") is not None:
                c["outbox_receipt_present"] += 1
    elif table == "usage_minute":
        _strict_int_usage(rec.get("up"), c, "usage_minute_up")
        _strict_int_usage(rec.get("down"), c, "usage_minute_down")
    elif table == "usage_day":
        _strict_int_usage(rec.get("up"), c, "usage_day_up")
        _strict_int_usage(rec.get("down"), c, "usage_day_down")


def _finalize_counters(counters, profile, digests):
    for t in digests:
        counters.setdefault("table_rows_" + t, digests[t]["rows"])


def _validate_schema(conn, profile, deadline):
    rows = _schema_rows(conn, deadline)
    seen = set()
    for typ, name, tbl, sql in rows:
        if typ == "table":
            if name.startswith("sqlite_") and name != "sqlite_sequence":
                _fail("schema_internal_table")
            if name not in _WHITELIST[profile]:
                _fail("schema_extra_table")
            sql_u = (sql or "").lstrip().upper()
            if sql_u.startswith("CREATE VIRTUAL"):
                _fail("schema_virtual_table")
            seen.add(name)
        elif typ in ("view", "trigger"):
            _fail("schema_extra_object")
        elif typ == "index":
            continue
        else:
            _fail("schema_extra_object")
    expected = set(_WHITELIST[profile])
    if profile == "meter":
        expected.discard("sqlite_sequence")
    missing = expected - seen
    if missing:
        _fail("schema_missing_table")
    if profile == "memory" and "sqlite_sequence" not in seen:
        _fail("schema_missing_sequence")
    return rows


def _integrity(conn, deadline):
    deadline.check()
    ic = conn.execute("PRAGMA integrity_check").fetchall()
    if ic != [("ok",)]:
        _fail("integrity_failed")
    deadline.check()
    if conn.execute("PRAGMA foreign_key_check").fetchone() is not None:
        _fail("foreign_key_failed")


def _set_progress(conn, deadline):
    def _ph():
        if _now(deadline._clock) >= deadline._end:
            return 1
        return 0
    conn.set_progress_handler(_ph, 10000)


def _prepare_read_txn(conn, deadline):
    deadline.check()
    conn.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, _MAX_VALUE_BYTES)
    conn.execute("PRAGMA trusted_schema=OFF")
    conn.execute("PRAGMA query_only=ON")
    conn.execute("PRAGMA foreign_keys=ON")
    _set_progress(conn, deadline)
    deadline.check()
    conn.execute("BEGIN")
    conn.execute("PRAGMA defer_foreign_keys=OFF")


def _bind_source(conn, source_inode, source_dev, deadline):
    deadline.check()
    row = conn.execute(
        "SELECT file FROM pragma_database_list WHERE name='main'").fetchone()
    if not row or not isinstance(row[0], str):
        _fail("source_binding_failed")
    st = os.lstat(row[0])
    if _stat.S_ISLNK(st.st_mode) or not _stat.S_ISREG(st.st_mode):
        _fail("source_binding_failed")
    if st.st_dev != source_dev or st.st_ino != source_inode:
        _fail("source_identity_mismatch")


def _db_disk_bound(conn, deadline):
    deadline.check()
    pc = conn.execute("PRAGMA page_count").fetchone()
    ps = conn.execute("PRAGMA page_size").fetchone()
    if not pc or not ps or not _is_int(pc[0]) or not _is_int(ps[0]):
        _fail("source_size_unknown")
    if pc[0] < 0 or ps[0] <= 0 or pc[0] * ps[0] > _MAX_DB_BYTES:
        _fail("source_too_large")


# ---- report --------------------------------------------------------------

def _read_report(p, guild, channel, uid, deadline):
    _require_real_dirs_up_to_final(p, "report")
    st = _own_regular(p, "report", uid, _MAX_REPORT_BYTES)
    if _mode(st) != 0o600:
        _fail("report_mode")
    pre = _full_sig(st)
    deadline.check()
    try:
        with open(p, "rb") as f:
            data = f.read(_MAX_REPORT_BYTES + 1)
    except OSError:
        _fail("report_io_error")
    if len(data) > _MAX_REPORT_BYTES:
        _fail("report_too_large")
    st2 = os.lstat(p)
    if _full_sig(st2) != pre:
        _fail("report_changed")
    try:
        text = data.decode("utf-8")
    except UnicodeError:
        _fail("report_not_utf8")
    obj = _strict_json(text)
    _validate_report(obj, guild, channel)
    return data, pre


def _reject_float(s):
    v = float(s)
    if not math.isfinite(v):
        _fail("report_nonfinite")
    return v


def _strict_json(text):
    def reject(pairs):
        keys = [k for k, _ in pairs]
        if len(set(keys)) != len(keys):
            _fail("report_duplicate_key")
        return dict(pairs)

    def bad_const(_v):
        _fail("report_nonfinite")

    try:
        return json.loads(text, object_pairs_hook=reject,
                          parse_constant=bad_const,
                          parse_float=_reject_float)
    except RecoveryError:
        raise
    except (ValueError, TypeError, OverflowError, RecursionError):
        _fail("report_malformed")


def _valid_day(k):
    if not isinstance(k, str) or len(k) != 10 or k[4] != "-" or k[7] != "-":
        return False
    if not k.isascii():
        return False
    if not (k[:4].isdigit() and k[5:7].isdigit() and k[8:].isdigit()):
        return False
    try:
        _dt.date(int(k[:4]), int(k[5:7]), int(k[8:]))
    except ValueError:
        return False
    return True


def _validate_report(obj, guild, channel):
    if not isinstance(obj, dict):
        _fail("report_malformed")
    ver = obj.get("version")
    if not _is_int(ver) or ver != 1:
        _fail("report_version")
    if obj.get("guildId") != guild or obj.get("channelId") != channel:
        _fail("report_identity")
    days = obj.get("days")
    if not isinstance(days, dict):
        _fail("report_malformed")
    phases = {"preparing", "sending", "sent", "uncertain", "failed"}
    for day, ent in days.items():
        if not _valid_day(day) or not isinstance(ent, dict):
            _fail("report_malformed")
        if ent.get("phase") not in phases:
            _fail("report_malformed")


# ---- capture -------------------------------------------------------------

def _check_source_aux(source_db, uid):
    for suffix in ("-journal",):
        p = source_db + suffix
        try:
            st = os.lstat(p)
        except FileNotFoundError:
            continue
        if _stat.S_ISLNK(st.st_mode) or not _stat.S_ISREG(st.st_mode):
            _fail("source_aux_invalid")
        if st.st_uid != uid:
            _fail("source_aux_owner")
        if st.st_nlink != 1:
            _fail("source_aux_hardlink")
        if st.st_size > 0:
            _fail("source_hot_journal")
    aux_total = 0
    for suffix in ("-wal", "-shm"):
        p = source_db + suffix
        try:
            ast = os.lstat(p)
        except FileNotFoundError:
            continue
        if _stat.S_ISLNK(ast.st_mode) or not _stat.S_ISREG(ast.st_mode):
            _fail("source_aux_invalid")
        if ast.st_uid != uid:
            _fail("source_aux_owner")
        if ast.st_nlink != 1:
            _fail("source_aux_hardlink")
        aux_total += ast.st_size
    if aux_total > _MAX_AUX_TOTAL:
        _fail("source_aux_cap")


def _validate_code_root(code_root, uid):
    st = os.lstat(code_root)
    if _stat.S_ISLNK(st.st_mode) or not _stat.S_ISDIR(st.st_mode):
        _fail("code_root_invalid")
    if st.st_uid != uid:
        _fail("code_root_owner")


def _hash_code(code_paths, expected_code_sha256, uid, deadline):
    pre = {}
    for rel, p in code_paths.items():
        _require_real_dirs_up_to_final(p, "code")
        st = _own_regular(p, "code", uid, _MAX_CODE_BYTES)
        if _mode(st) & 0o022:
            _fail("code_writable")
        sha, size = _sha_file(p, _MAX_CODE_BYTES, deadline)
        if sha != expected_code_sha256[rel]:
            _fail("code_hash_mismatch")
        pre[rel] = (sha, size, _full_sig(st))
    return pre


def _reverify_code(code_paths, pre, uid, deadline):
    for rel, p in code_paths.items():
        st = _own_regular(p, "code", uid, _MAX_CODE_BYTES)
        sha, size = _sha_file(p, _MAX_CODE_BYTES, deadline)
        prev = pre[rel]
        if sha != prev[0] or size != prev[1] or _full_sig(st) != prev[2]:
            _fail("code_drift")


def _open_source_ro(source_db, deadline):
    uri = "file:" + urllib.parse.quote(source_db, safe="/") + "?mode=ro"
    try:
        return sqlite3.connect(
            uri, uri=True, timeout=min(0.25, deadline.remaining()))
    except sqlite3.Error:
        _fail("source_open_failed")


def _snapshot_standalone_clean(snap_path):
    for suffix in ("-wal", "-shm", "-journal"):
        if os.path.lexists(snap_path + suffix):
            _fail("snapshot_aux_present")


def capture(source_db, profile, expected_dev, expected_ino, expected_schema_sha,
            expected_sqlite_version, code_root, expected_code_sha256,
            cutoff_id, output_parent, *,
            report_path=None, expected_guild_id=None, expected_channel_id=None,
            clock=None):
    try:
        return _capture(
            source_db, profile, expected_dev, expected_ino,
            expected_schema_sha, expected_sqlite_version, code_root,
            expected_code_sha256, cutoff_id, output_parent,
            report_path, expected_guild_id, expected_channel_id, clock)
    except RecoveryError:
        raise
    except (sqlite3.Error, OSError, UnicodeError, ValueError, TypeError,
            OverflowError, RecursionError):
        raise RecoveryError("capture_io_error") from None


def _capture(source_db, profile, expected_dev, expected_ino,
             expected_schema_sha, expected_sqlite_version, code_root,
             expected_code_sha256, cutoff_id, output_parent, report_path,
             expected_guild_id, expected_channel_id, clock):
    deadline = _Deadline(clock)
    uid = os.geteuid()
    if profile not in ("memory", "meter"):
        _fail("profile_invalid")
    if not isinstance(cutoff_id, str) or ".." in cutoff_id or \
            _CUTOFF_RE.fullmatch(cutoff_id) is None:
        _fail("cutoff_invalid")
    if not _is_int(expected_dev) or expected_dev < 0 or \
            not _is_int(expected_ino) or expected_ino < 0:
        _fail("source_identity_invalid")
    if not _is_hex64(expected_schema_sha):
        _fail("schema_sha_invalid")
    if not isinstance(expected_sqlite_version, str) or not expected_sqlite_version:
        _fail("runtime_expected_invalid")
    if sqlite3.sqlite_version != expected_sqlite_version:
        _fail("runtime_mismatch")
    if not isinstance(expected_code_sha256, dict) or \
            set(expected_code_sha256) != set(_CODE_KEYS[profile]):
        _fail("code_keys_invalid")
    for k in expected_code_sha256:
        if not _is_hex64(expected_code_sha256[k]):
            _fail("code_sha_invalid")

    source_db = _absnorm("source", source_db)
    code_root = _absnorm("code_root", code_root)
    output_parent = _absnorm("output_parent", output_parent)
    src_parent = os.path.dirname(source_db)
    if profile == "memory":
        if report_path is None or expected_guild_id is None or \
                expected_channel_id is None:
            _fail("report_inputs_required")
        report_path = _absnorm("report", report_path)
        if not _is_id(expected_guild_id) or not _is_id(expected_channel_id):
            _fail("report_identity_invalid")
        rep_parent = os.path.dirname(report_path)
    else:
        if report_path is not None:
            _fail("report_not_allowed")
        report_path = None
        rep_parent = None
    if os.path.basename(source_db) != _DB_BASENAMES[profile]:
        _fail("source_basename")

    _require_real_dirs_up_to_final(source_db, "source")
    _require_real_dirs_up_to_final(code_root, "code_root")
    _require_real_dirs_up_to_final(output_parent, "output_parent")
    if report_path is not None:
        _require_real_dirs_up_to_final(report_path, "report")

    src_st = _own_regular(source_db, "source", uid, _MAX_DB_BYTES)
    if _mode(src_st) != 0o600:
        _fail("source_mode")
    if src_st.st_dev != expected_dev or src_st.st_ino != expected_ino:
        _fail("source_identity_mismatch")
    _validate_code_root(code_root, uid)
    _check_source_aux(source_db, uid)

    op_st = os.lstat(output_parent)
    if _stat.S_ISLNK(op_st.st_mode) or not _stat.S_ISDIR(op_st.st_mode):
        _fail("output_parent_invalid")
    if op_st.st_uid != uid:
        _fail("output_parent_owner")
    if _mode(op_st) != 0o700:
        _fail("output_parent_mode")

    _disjoint_roots(output_parent, code_root)
    _disjoint_roots(output_parent, src_parent)
    if rep_parent is not None:
        _disjoint_roots(output_parent, rep_parent)
        if _within(report_path, source_db) or _within(source_db, report_path):
            _fail("paths_overlap")
        if _within(report_path, code_root) or _within(code_root, report_path):
            _fail("paths_overlap")
    if _within(source_db, code_root) or _within(code_root, source_db):
        _fail("paths_overlap")

    mem_sha = _reference_schema_sha(profile)
    if mem_sha != expected_schema_sha:
        _fail("trusted_schema_unrecognized")

    code_paths = {rel: os.path.join(code_root, rel)
                  for rel in _CODE_KEYS[profile]}
    code_pre = _hash_code(code_paths, expected_code_sha256, uid, deadline)

    rep_sha = None
    rep_size = None
    rep_sig = None
    if profile == "memory":
        rep_bytes, rep_sig = _read_report(
            report_path, expected_guild_id, expected_channel_id, uid, deadline)
        rep_sha = hashlib.sha256(rep_bytes).hexdigest()
        rep_size = len(rep_bytes)

    out_dir = os.path.join(output_parent, "k2b-%s-%s" % (profile, cutoff_id))
    try:
        os.mkdir(out_dir, 0o700)
    except FileExistsError:
        _fail("output_exists")
    try:
        os.chmod(out_dir, 0o700)
        res = _capture_into(
            deadline, uid, output_parent, out_dir, source_db, profile,
            expected_dev, expected_ino, expected_schema_sha,
            expected_sqlite_version, code_paths, code_pre, cutoff_id,
            report_path, rep_sha, rep_size, rep_sig, expected_guild_id,
            expected_channel_id)
    except BaseException:
        _remove_receipt(out_dir)
        try:
            _fsync_dir(out_dir)
            _fsync_dir(output_parent)
        except RecoveryError:
            pass
        raise
    return res


def _remove_receipt(out_dir):
    p = os.path.join(out_dir, "receipt.json")
    try:
        os.unlink(p)
    except FileNotFoundError:
        pass
    except OSError:
        _fail("receipt_cleanup_failed")


def _capture_into(deadline, uid, output_parent, out_dir, source_db, profile,
                  dev, ino, schema_sha, runtime_version, code_paths, code_pre,
                  cutoff_id, report_path, rep_sha, rep_size, rep_sig, guild,
                  channel):
    snap_path = os.path.join(out_dir, "snapshot.sqlite3")
    report_copy = os.path.join(out_dir, "report.json")
    rec_path = os.path.join(out_dir, "receipt.json")

    sfd = os.open(snap_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                  getattr(os, "O_NOFOLLOW", 0), 0o600)
    os.close(sfd)
    os.chmod(snap_path, 0o600)

    src = _open_source_ro(source_db, deadline)
    dst = None
    try:
        _prepare_read_txn(src, deadline)
        _bind_source(src, ino, dev, deadline)
        _db_disk_bound(src, deadline)
        rows = _validate_schema(src, profile, deadline)
        if _schema_signature(rows) != schema_sha:
            _fail("schema_mismatch")
        _integrity(src, deadline)
        live_digests, live_counters = _logical_fingerprint(src, profile, deadline)

        def _bcb(status, remaining, total):
            deadline.check()

        dst = sqlite3.connect(
            snap_path, timeout=min(0.25, deadline.remaining()))
        dst.execute("PRAGMA journal_mode=DELETE")
        src.backup(dst, pages=_BACKUP_PAGES, progress=_bcb, name="main",
                   sleep=_BACKUP_SLEEP)
        dst.execute("PRAGMA journal_mode=DELETE")
        dst.close()
        dst = None
        src.execute("ROLLBACK")
        src.close()
        src = None

        _snapshot_standalone_clean(snap_path)
        snap_st = _own_regular(snap_path, "snapshot", uid)
        if _mode(snap_st) != 0o600:
            _fail("snapshot_mode")

        uri = "file:" + urllib.parse.quote(snap_path, safe="/") + \
            "?mode=ro&immutable=1"
        off = None
        try:
            off = sqlite3.connect(
                uri, uri=True, timeout=min(0.25, deadline.remaining()))
            _prepare_read_txn(off, deadline)
            off_rows = _validate_schema(off, profile, deadline)
            if _schema_signature(off_rows) != schema_sha:
                _fail("snapshot_schema_mismatch")
            _integrity(off, deadline)
            _db_disk_bound(off, deadline)
            off_digests, off_counters = _logical_fingerprint(
                off, profile, deadline)
            off.execute("ROLLBACK")
        finally:
            if off is not None:
                off.close()
        if off_digests != live_digests or off_counters != live_counters:
            _fail("snapshot_drift")

    finally:
        if dst is not None:
            dst.close()
        if src is not None:
            src.close()

    # Source identity recheck; no db mutations performed.
    st = _own_regular(source_db, "source", uid, _MAX_DB_BYTES)
    if st.st_dev != dev or st.st_ino != ino:
        _fail("source_identity_mismatch")

    _reverify_code(code_paths, code_pre, uid, deadline)

    if profile == "memory":
        data, sig = _read_report(report_path, guild, channel, uid, deadline)
        if sig != rep_sig or hashlib.sha256(data).hexdigest() != rep_sha or \
                len(data) != rep_size:
            _fail("report_drift")
        rfd = os.open(report_copy, os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                      getattr(os, "O_NOFOLLOW", 0), 0o600)
        try:
            with os.fdopen(rfd, "wb", closefd=True) as rf:
                rf.write(data)
                rf.flush()
                os.fsync(rf.fileno())
        except OSError:
            _fail("report_write_failed")
        os.chmod(report_copy, 0o600)
        data2, sig2 = _read_report(report_path, guild, channel, uid, deadline)
        if sig2 != rep_sig or data2 != data:
            _fail("report_drift")

    try:
        sd = os.open(snap_path, os.O_RDONLY)
        try:
            os.fsync(sd)
        finally:
            os.close(sd)
    except OSError:
        _fail("snapshot_fsync_failed")

    snap_sha, snap_size = _sha_file(snap_path, _MAX_DB_BYTES, deadline)
    receipt = {
        "schema_version": _SCHEMA_VERSION,
        "profile": profile,
        "cutoff_id": cutoff_id,
        "source": {"path": source_db, "dev": dev, "ino": ino},
        "code": {rel: {"path": code_paths[rel],
                        "sha256": code_pre[rel][0],
                        "size": code_pre[rel][1]} for rel in code_pre},
        "schema_sha256": schema_sha,
        "tables": live_digests,
        "counters": live_counters,
        "snapshot": {"path": snap_path, "sha256": snap_sha,
                     "size": snap_size},
        "report": ({"path": report_copy, "sha256": rep_sha,
                    "size": rep_size, "guild_id": guild,
                    "channel_id": channel}
                   if profile == "memory" else None),
        "runtime": {"python": "%d.%d" % os.sys.version_info[:2],
                    "sqlite": runtime_version,
                    "capture_uid": uid},
        "scope_limits": [
            "preparation_snapshot_only",
            "no_restore_or_startup",
            "not_admission_or_sending",
            "no_cross_application_atomicity",
            "live_wal_shm_not_byte_immutable",
            "code_hashes_are_on_disk_evidence",
            "report_consistency_not_atomic",
            "trusted_caller_owns_cutoff_and_window",
        ],
    }
    rbytes = json.dumps(receipt, sort_keys=True,
                        separators=(",", ":")).encode("utf-8")
    if len(rbytes) > _MAX_RECEIPT_BYTES:
        _fail("receipt_too_large")
    _fsync_dir(out_dir)
    _fsync_dir(output_parent)
    if profile == "memory":
        try:
            data3, sig3 = _read_report(report_path, guild, channel, uid, deadline)
        except RecoveryError:
            raise
        if sig3 != rep_sig or hashlib.sha256(data3).hexdigest() != rep_sha or \
                len(data3) != rep_size:
            _fail("report_drift")
    try:
        fd = os.open(rec_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                     getattr(os, "O_NOFOLLOW", 0), 0o600)
        with os.fdopen(fd, "wb", closefd=True) as rf:
            rf.write(rbytes)
            rf.flush()
            os.fsync(rf.fileno())
    except OSError:
        _remove_receipt(out_dir)
        _fail("receipt_write_failed")
    os.chmod(rec_path, 0o600)
    _fsync_dir(out_dir)
    _fsync_dir(output_parent)
    rec_sha = hashlib.sha256(rbytes).hexdigest()
    return {"receipt": receipt, "receipt_sha256": rec_sha,
            "bundle_dir": out_dir, "snapshot_path": snap_path}


def _fsync_dir(p):
    try:
        fd = os.open(p, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    except OSError:
        _fail("dir_open_failed")
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


# ---- verify --------------------------------------------------------------

def verify(bundle_dir, expected_profile, trusted_receipt_sha256,
           expected_sqlite_version, *, clock=None):
    try:
        return _verify(bundle_dir, expected_profile, trusted_receipt_sha256,
                       expected_sqlite_version, clock)
    except RecoveryError:
        raise
    except (sqlite3.Error, OSError, UnicodeError, ValueError, TypeError,
            OverflowError, RecursionError):
        raise RecoveryError("verify_io_error") from None


def _verify(bundle_dir, expected_profile, trusted_receipt_sha256,
            expected_sqlite_version, clock):
    deadline = _Deadline(clock)
    uid = os.geteuid()
    bundle_dir = _absnorm("bundle", bundle_dir)
    if expected_profile not in ("memory", "meter"):
        _fail("profile_invalid")
    if not isinstance(expected_sqlite_version, str) or not expected_sqlite_version:
        _fail("runtime_expected_invalid")
    if sqlite3.sqlite_version != expected_sqlite_version:
        _fail("runtime_mismatch")
    if not _is_hex64(trusted_receipt_sha256):
        _fail("trusted_receipt_invalid")
    _require_real_dirs_up_to_final(bundle_dir, "bundle")
    ds = os.lstat(bundle_dir)
    if _stat.S_ISLNK(ds.st_mode) or not _stat.S_ISDIR(ds.st_mode):
        _fail("bundle_invalid")
    if ds.st_uid != uid or _mode(ds) != 0o700:
        _fail("bundle_mode")
    members = sorted(os.listdir(bundle_dir))
    expected = ["receipt.json", "snapshot.sqlite3"]
    if expected_profile == "memory":
        expected.append("report.json")
    expected.sort()
    if members != expected:
        _fail("membership_mismatch")

    stats = {}
    for name in members:
        p = os.path.join(bundle_dir, name)
        st = _own_regular(p, "bundle_member", uid)
        if _mode(st) != 0o600:
            _fail("member_mode")
        stats[name] = _full_sig(st)

    rec_path = os.path.join(bundle_dir, "receipt.json")
    try:
        with open(rec_path, "rb") as f:
            rbytes = f.read(_MAX_RECEIPT_BYTES + 1)
    except OSError:
        _fail("receipt_read_failed")
    if len(rbytes) > _MAX_RECEIPT_BYTES:
        _fail("receipt_too_large")
    if hashlib.sha256(rbytes).hexdigest() != trusted_receipt_sha256:
        _fail("receipt_hash_mismatch")
    try:
        text = rbytes.decode("utf-8")
    except UnicodeError:
        _fail("receipt_malformed")
    receipt = _strict_json(text)
    if not isinstance(receipt, dict):
        _fail("receipt_malformed")
    ver = receipt.get("schema_version")
    if not _is_int(ver) or ver != 1:
        _fail("receipt_malformed")
    if receipt.get("profile") != expected_profile:
        _fail("profile_mismatch")
    schema_sha = receipt.get("schema_sha256")
    if not _is_hex64(schema_sha):
        _fail("trusted_schema_unrecognized")
    if schema_sha != _reference_schema_sha(expected_profile):
        _fail("trusted_schema_unrecognized")
    cutoff = receipt.get("cutoff_id")
    if not isinstance(cutoff, str) or ".." in cutoff or \
            _CUTOFF_RE.fullmatch(cutoff) is None:
        _fail("cutoff_invalid")
    code = receipt.get("code")
    if not isinstance(code, dict) or set(code) != set(_CODE_KEYS[expected_profile]):
        _fail("code_keys_invalid")
    for k, ent in code.items():
        if not isinstance(ent, dict) or not _is_hex64(ent.get("sha256")) or \
                not _is_int(ent.get("size")) or ent.get("size") < 0:
            _fail("code_sha_invalid")
    tables = receipt.get("tables")
    counters = receipt.get("counters")
    if not isinstance(tables, dict) or not isinstance(counters, dict):
        _fail("receipt_malformed")

    snap_path = os.path.join(bundle_dir, "snapshot.sqlite3")
    snap_sha, snap_size = _sha_file(snap_path, _MAX_DB_BYTES, deadline)
    exp_snap = receipt.get("snapshot") or {}
    if not isinstance(exp_snap, dict) or \
            snap_sha != exp_snap.get("sha256") or snap_size != exp_snap.get("size"):
        _fail("snapshot_hash_mismatch")

    if expected_profile == "memory":
        rep_path = os.path.join(bundle_dir, "report.json")
        try:
            with open(rep_path, "rb") as f:
                data = f.read(_MAX_REPORT_BYTES + 1)
        except OSError:
            _fail("report_read_failed")
        if len(data) > _MAX_REPORT_BYTES:
            _fail("report_too_large")
        try:
            robj = _strict_json(data.decode("utf-8"))
        except UnicodeError:
            _fail("report_malformed")
        exp = receipt.get("report") or {}
        if not isinstance(exp, dict):
            _fail("report_malformed")
        _validate_report(robj, exp.get("guild_id"), exp.get("channel_id"))
        if hashlib.sha256(data).hexdigest() != exp.get("sha256") or \
                len(data) != exp.get("size"):
            _fail("report_hash_mismatch")

    _snapshot_standalone_clean(snap_path)
    conn = None
    try:
        conn = sqlite3.connect(
            "file:" + urllib.parse.quote(snap_path, safe="/") +
            "?mode=ro&immutable=1", uri=True, timeout=min(0.25, deadline.remaining()))
        _prepare_read_txn(conn, deadline)
        _db_disk_bound(conn, deadline)
        rows = _validate_schema(conn, expected_profile, deadline)
        if _schema_signature(rows) != schema_sha:
            _fail("schema_mismatch")
        _integrity(conn, deadline)
        digests, counters = _logical_fingerprint(conn, expected_profile, deadline)
        conn.execute("ROLLBACK")
    finally:
        if conn is not None:
            conn.close()
    if digests != tables:
        _fail("logical_digest_mismatch")
    if counters != receipt.get("counters"):
        _fail("continuity_mismatch")

    deadline.check()
    if sorted(os.listdir(bundle_dir)) != expected:
        _fail("membership_changed")
    for name, prev in stats.items():
        st = os.lstat(os.path.join(bundle_dir, name))
        if _full_sig(st) != prev:
            _fail("bundle_changed")
    return {"schema_version": _SCHEMA_VERSION,
            "profile": expected_profile,
            "cutoff_id": cutoff,
            "snapshot_sha256": snap_sha,
            "snapshot_size": snap_size,
            "tables_verified": sorted(digests)}
