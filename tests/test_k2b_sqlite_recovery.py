"""Synthetic, offline component tests for the k2b SQLite recovery helper.

All data is invented and created under temporary directories.  No live roots,
no application modules, no network and no subprocess use.  The reference DDL
below is copied verbatim from the deployed schemas and is used to build the
databases and the independent trusted-schema digest, never the module DDL.
"""

import hashlib
import json
import os
import sqlite3
import stat
import sys
import tempfile
import unittest
from unittest import mock

# ---- reference DDL (independently copied from deployed sources) -----------

MEMORY_DDL = (
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

METER_DDL = (
    "CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);\n"
    "CREATE TABLE IF NOT EXISTS checkpoints(id TEXT PRIMARY KEY,up INTEGER,down INTEGER,ip TEXT,mac TEXT,owner TEXT,route TEXT,node TEXT);\n"
    "CREATE TABLE IF NOT EXISTS usage_minute(bucket INTEGER,owner TEXT,mac TEXT,ip TEXT,node TEXT,route TEXT,up INTEGER,down INTEGER,PRIMARY KEY(bucket,owner,mac,ip,node,route));\n"
    "CREATE TABLE IF NOT EXISTS usage_day(bucket TEXT,owner TEXT,mac TEXT,ip TEXT,node TEXT,route TEXT,up INTEGER,down INTEGER,PRIMARY KEY(bucket,owner,mac,ip,node,route));\n"
    "CREATE TABLE IF NOT EXISTS health(bucket TEXT PRIMARY KEY,samples INTEGER DEFAULT 0,observed_seconds REAL DEFAULT 0,gaps INTEGER DEFAULT 0,resets INTEGER DEFAULT 0,baselines INTEGER DEFAULT 0);\n"
)

PROFILE_DDL = {"memory": MEMORY_DDL, "meter": METER_DDL}

# Invented 17-20 digit identifiers.
GUILD = "910000000000000001"
CHANNEL = "910000000000000002"
AUTHOR = "910000000000000003"


def reference_schema_sha(profile):
    """Independently derive the documented line-JSON schema digest."""
    conn = sqlite3.connect(":memory:")
    try:
        conn.executescript(PROFILE_DDL[profile])
        rows = conn.execute(
            "SELECT type,name,tbl_name,sql FROM sqlite_master"
            " ORDER BY type,name,tbl_name,sql"
        ).fetchall()
    finally:
        conn.close()
    h = hashlib.sha256()
    for r in rows:
        ent = json.dumps([r[0], r[1], r[2], r[3]], ensure_ascii=True,
                         separators=(",", ":"))
        h.update(ent.encode("ascii") + b"\n")
    return h.hexdigest()


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def full_stat(path):
    st = os.lstat(path)
    return (st.st_dev, st.st_ino, st.st_size, st.st_nlink, st.st_mode,
            st.st_mtime_ns, st.st_ctime_ns, st.st_uid)


def dir_entries(path):
    return sorted(os.listdir(path))


def _load_module():
    import importlib.util
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.dirname(here)
    candidate = os.path.join(root, "scripts", "lib", "k2b_sqlite_recovery.py")
    spec = importlib.util.spec_from_file_location("k2b_under_test", candidate)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---- report fixture -------------------------------------------------------

PHASES = [("2031-01-01", "preparing"), ("2031-01-02", "sending"),
          ("2031-01-03", "sent"), ("2031-01-04", "uncertain"),
          ("2031-01-05", "failed")]


def report_bytes(guild=GUILD, channel=CHANNEL, extra=None, unknown_float=True):
    days = {}
    for day, phase in PHASES:
        days[day] = {"phase": phase, "nonce": "n" + day.replace("-", ""),
                     "messageId": "10230000000000000" + day[-1],
                     "reconciliation": "settled",
                     "attempts": 1, "absenceChecks": []}
    days["2031-01-03"]["absenceChecks"] = ["mid", "attachments"]
    days["2031-01-03"]["schedule"] = {"at": 1000, "drift": 0}
    obj = {"version": 1, "guildId": guild, "channelId": channel, "days": days}
    if unknown_float:
        obj["unknownField"] = 0.1 + 0.2
    if extra:
        obj.update(extra)
    return json.dumps(obj, separators=(",", ":")).encode("utf-8")


# ---- database fixture builders -------------------------------------------

def _ins(conn, table, row):
    cols = list(row)
    conn.execute(
        'INSERT INTO "%s" (%s) VALUES (%s)' % (
            table, ", ".join('"%s"' % c for c in cols),
            ", ".join("?" for _ in cols)),
        [row[c] for c in cols])


MEM_IDS = {
    "s1": "920000000000000001", "s2": "920000000000000002",
    "m_local": "930000000000000001", "m_shared": "930000000000000002",
    "m_gone": "930000000000000003",
    "c_pend": "940000000000000001", "c_acc": "940000000000000002",
    "c_rej": "940000000000000003",
    "b_open": "950000000000000001", "b_closed": "950000000000000002",
}


def populate_memory(conn):
    _ins(conn, "meta", {"key": "schema", "value": "1"})
    for sid, content in (("s1", "alpha retained"), ("s2", "beta retained")):
        _ins(conn, "sources", {
            "id": MEM_IDS[sid], "guild_id": GUILD, "channel_id": CHANNEL,
            "parent_id": None, "author_id": AUTHOR, "author_name": "inv",
            "content": content, "source_time": 100,
            "received_time": 101, "revision": 0,
            "content_hash": hashlib.sha256(content.encode()).hexdigest()})
    mems = [
        ("m_local", "local fact", "local_saved", "direct", 1, "first"),
        ("m_shared", "shared fact", "shared", "review", 1, "second"),
        ("m_gone", "forgotten fact", "local_saved", "direct", 0, "third"),
    ]
    for key, text, status, adm, active, req in mems:
        _ins(conn, "memories", {
            "id": MEM_IDS[key], "guild_id": GUILD, "text": text,
            "requester_id": AUTHOR, "speaker_ids": "[]",
            "source_ids": json.dumps([MEM_IDS["s1"]]),
            "admission": adm, "admission_evidence": "ev-" + status,
            "status": status, "revision": 0, "active": active,
            "created_at": 200, "request_id": req, "request_hash": None,
            "review_evidence": None, "kind": "fact", "valid_until": None})
        _ins(conn, "memory_sources", {
            "memory_id": MEM_IDS[key], "source_id": MEM_IDS["s1"],
            "pin": 1})
    for key, status in (("c_pend", "pending"), ("c_acc", "accepted"),
                        ("c_rej", "rejected")):
        _ins(conn, "candidates", {
            "id": MEM_IDS[key], "guild_id": GUILD, "text": status + " cand",
            "kind": "fact", "source_ids": "[]", "author_ids": "[]",
            "evidence_hashes": "[]", "text_hash": "0" * 64,
            "valid_until": None, "created_at": 300, "status": status,
            "batch_id": MEM_IDS["b_open"]})
    _ins(conn, "batches", {"id": MEM_IDS["b_open"], "guild_id": GUILD,
                           "hash": "h1", "snapshot": "{}", "status": "open",
                           "message_id": "102300000000000001",
                           "created_at": 400})
    _ins(conn, "batches", {"id": MEM_IDS["b_closed"], "guild_id": GUILD,
                           "hash": "h2", "snapshot": "{}", "status": "closed",
                           "message_id": "102300000000000002",
                           "created_at": 401})
    # pending outbox, acknowledged outbox with receipt, then a deleted row so
    # sqlite_sequence must remain high (999).
    _ins(conn, "outbox", {"operation_id": "op-pending",
                          "memory_id": MEM_IDS["m_local"], "revision": 0,
                          "action": "upsert", "status": "pending",
                          "payload": "{\"a\":1}", "created_at": 500,
                          "receipt": None})
    _ins(conn, "outbox", {"operation_id": "op-ack",
                          "memory_id": MEM_IDS["m_shared"], "revision": 1,
                          "action": "upsert", "status": "acknowledged",
                          "payload": "{\"b\":2}", "created_at": 501,
                          "receipt": "{\"ok\":true,\"mid\":102300000000000003}"})
    conn.execute("UPDATE sqlite_sequence SET seq=999 WHERE name='outbox'")
    conn.commit()


def populate_meter(conn):
    _ins(conn, "meta", {"key": "retry", "value": "3"})
    _ins(conn, "meta", {"key": "core", "value": "on"})
    _ins(conn, "meta", {"key": "lastsample", "value": "1700000000"})
    _ins(conn, "meta", {"key": "checkpoint_id", "value":
                        "960000000000000001"})
    _ins(conn, "checkpoints", {"id": "960000000000000001", "up": 10,
                               "down": 20, "ip": "10.0.0.1",
                               "mac": "aa:bb:cc:dd:ee:ff", "owner": "inv",
                               "route": "wan", "node": "n1"})
    big = 2 ** 53
    _ins(conn, "usage_minute", {"bucket": 61000000, "owner": "inv",
                                "mac": "aa:bb:cc:dd:ee:ff", "ip": "10.0.0.1",
                                "node": "n1", "route": "wan",
                                "up": big, "down": big + 7})
    _ins(conn, "usage_minute", {"bucket": 61000001, "owner": "inv",
                                "mac": "aa:bb:cc:dd:ee:08", "ip": "10.0.0.2",
                                "node": "n1", "route": "wan",
                                "up": 5, "down": 6})
    _ins(conn, "usage_day", {"bucket": "2031-01-01", "owner": "inv",
                             "mac": "aa:bb:cc:dd:ee:ff", "ip": "10.0.0.1",
                             "node": "n1", "route": "wan",
                             "up": big, "down": 9})
    _ins(conn, "health", {"bucket": "2031-01-01", "samples": 12,
                          "observed_seconds": 59.5, "gaps": 0,
                          "resets": 1, "baselines": 3})
    conn.commit()


POPULATORS = {"memory": populate_memory, "meter": populate_meter}
DB_NAME = {"memory": "conversations.sqlite3", "meter": "usage.sqlite"}
CODE_REL = {"memory": ("src/memory.mjs", "src/vpn.mjs"), "meter": ("meter.py",)}


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = _load_module()

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = os.path.realpath(self._tmp.name)
        self.uid = os.geteuid()
        self.addCleanup(self._tmp.cleanup)

    # -- filesystem helpers --
    def mkdir700(self, parent, name):
        p = os.path.join(parent, name)
        os.mkdir(p, 0o700)
        os.chmod(p, 0o700)
        return p

    def write_owned(self, path, data, mode=0o600):
        with open(path, "wb") as f:
            f.write(data)
        os.chmod(path, mode)
        return path

    def build_layout(self, profile="memory",
                     memory_rows=True, report=True):
        """Create state dir, code_root sibling, output parent, source DB."""
        work = self.mkdir700(self.base, "work")
        state = self.mkdir700(work, "state")
        code_root = self.mkdir700(work, "code")
        output = self.mkdir700(work, "out")
        dbp = os.path.join(state, DB_NAME[profile])
        conn = sqlite3.connect(dbp, timeout=10)
        try:
            conn.executescript(PROFILE_DDL[profile])
            if memory_rows or profile != "memory":
                POPULATORS[profile](conn)
            conn.commit()
        finally:
            conn.close()
        os.chmod(dbp, 0o600)
        code_shas = {}
        for rel in CODE_REL[profile]:
            full = os.path.join(code_root, rel)
            os.makedirs(os.path.dirname(full), mode=0o700, exist_ok=True)
            os.chmod(os.path.dirname(full), 0o700)
            self.write_owned(full, ("// " + rel + "\n").encode(), 0o644)
            code_shas[rel] = sha256_file(full)
        report_path = None
        if profile == "memory" and report:
            report_path = os.path.join(state, "report.json")
            self.write_owned(report_path, report_bytes(), 0o600)
        if profile == "memory":
            report_p = report_path
            guild, channel = GUILD, CHANNEL
        else:
            report_p, guild, channel = None, None, None
        st = os.lstat(dbp)
        ctx = {
            "work": work, "state": state, "code_root": code_root,
            "output": output, "source": dbp, "source_dev": st.st_dev,
            "source_ino": st.st_ino, "code_shas": code_shas,
            "report": report_p, "guild": guild, "channel": channel,
            "schema_sha": reference_schema_sha(profile),
            "sqlite_version": sqlite3.sqlite_version, "profile": profile,
            "cutoff": "cut-0001",
        }
        return ctx

    def capture_args(self, ctx):
        return dict(
            source_db=ctx["source"], profile=ctx["profile"],
            expected_dev=ctx["source_dev"],
            expected_ino=ctx["source_ino"],
            expected_schema_sha=ctx["schema_sha"],
            expected_sqlite_version=ctx["sqlite_version"],
            code_root=ctx["code_root"],
            expected_code_sha256=ctx["code_shas"],
            cutoff_id=ctx["cutoff"], output_parent=ctx["output"],
            report_path=ctx["report"], expected_guild_id=ctx["guild"],
            expected_channel_id=ctx["channel"])

    def capture(self, ctx, **overrides):
        args = self.capture_args(ctx)
        args.update(overrides)
        return self.mod.capture(**args)

    def bundle_path(self, ctx):
        return os.path.join(ctx["output"],
                            "k2b-%s-%s" % (ctx["profile"], ctx["cutoff"]))

    def verify(self, ctx, res, **overrides):
        args = dict(bundle_dir=res["bundle_dir"],
                    expected_profile=ctx["profile"],
                    trusted_receipt_sha256=res["receipt_sha256"],
                    expected_sqlite_version=sqlite3.sqlite_version)
        args.update(overrides)
        return self.mod.verify(**args)

    def assert_recovery_error(self, code_ok, fn, *a, **k):
        with self.assertRaises(self.mod.RecoveryError) as cm:
            fn(*a, **k)
        err = cm.exception
        self.assertIsInstance(err.code, str)
        msg = str(err)
        for needle in ("/", "SELECT", "sqlite3.", "Traceback"):
            self.assertNotIn(needle, msg)
        if callable(code_ok):
            self.assertTrue(code_ok(err.code), err.code)
        elif isinstance(code_ok, (set, tuple, list)):
            self.assertIn(err.code, code_ok)
        return err

    # -- independent DB inspection --
    def sel(self, path, sql):
        conn = sqlite3.connect("file:" + path + "?mode=ro", uri=True)
        try:
            return conn.execute(sql).fetchall()
        finally:
            conn.close()

    def rowcount(self, path, table):
        return self.sel(path, 'SELECT count(*) FROM "%s"' % table)[0][0]

    def rewrite_receipt(self, res, mutate):
        """Controlled test-only receipt rewrite; returns new trusted hash."""
        rp = os.path.join(res["bundle_dir"], "receipt.json")
        with open(rp, "rb") as f:
            rec = json.loads(f.read().decode("utf-8"))
        mutate(rec, res)
        data = json.dumps(rec, sort_keys=True,
                          separators=(",", ":")).encode("utf-8")
        with open(rp, "wb") as f:
            f.write(data)
        os.chmod(rp, 0o600)
        return hashlib.sha256(data).hexdigest()

    def logical_fingerprint(self, path, profile):
        conn = sqlite3.connect(path)
        try:
            mod = self.mod
            dl = mod._Deadline()
            conn.execute("PRAGMA query_only=ON")
            return mod._logical_fingerprint(conn, profile, dl)
        finally:
            conn.close()

    def freshness_ok(self, res, profile):
        """After controlled logical mutation only the digest must differ."""
        snap = os.path.join(res["bundle_dir"], "snapshot.sqlite3")
        return sha256_file(snap)


def memory_expected_counters():
    return {"active": 2, "shared": 1, "local_saved": 1, "inactive": 1,
            "pending_candidates": 1, "open_batches": 1,
            "outbox_pending": 1, "outbox_acknowledged": 1,
            "outbox_receipt_present": 1}


# =========================================================================

class TestModuleContract(Base):
    def test_schema_digest_matches_independent_fixture(self):
        for profile in ("memory", "meter"):
            with self.subTest(profile=profile):
                self.assertEqual(self.mod._reference_schema_sha(profile),
                                 reference_schema_sha(profile))

    def test_reference_schema_shas_are_real_hex(self):
        for p in ("memory", "meter"):
            self.assertRegex(reference_schema_sha(p), r"^[0-9a-f]{64}$")

    def test_runtime_expected_mismatch_rejected(self):
        ctx = self.build_layout("meter")
        self.assert_recovery_error("runtime_mismatch", self.capture, ctx,
                                   expected_sqlite_version="0.0.0")
        self.assertFalse(os.path.exists(self.bundle_path(ctx)))

    def test_unknown_profile_rejected(self):
        ctx = self.build_layout("meter")
        self.assert_recovery_error({"profile_invalid"}, self.capture, ctx,
                                   profile="school")


class TestCaptureMemorySuccess(Base):
    def test_success_shape_permissions_and_membership(self):
        ctx = self.build_layout("memory")
        res = self.capture(ctx)
        self.assertIsInstance(res, dict)
        for key in ("receipt", "receipt_sha256", "bundle_dir",
                    "snapshot_path"):
            self.assertIn(key, res)
        bd = res["bundle_dir"]
        self.assertEqual(bd, self.bundle_path(ctx))
        self.assertEqual(stat.S_IMODE(os.lstat(bd).st_mode), 0o700)
        self.assertEqual(dir_entries(bd),
                         ["receipt.json", "report.json", "snapshot.sqlite3"])
        for name in os.listdir(bd):
            self.assertEqual(stat.S_IMODE(os.lstat(os.path.join(bd, name)).st_mode),
                             0o600)
        self.assertEqual(res["receipt_sha256"],
                         sha256_file(os.path.join(bd, "receipt.json")))
        self.assertEqual(sha256_file(res["snapshot_path"]),
                         res["receipt"]["snapshot"]["sha256"])

    def test_report_bytes_preserved(self):
        ctx = self.build_layout("memory")
        res = self.capture(ctx)
        with open(ctx["report"], "rb") as f:
            original = f.read()
        with open(os.path.join(res["bundle_dir"], "report.json"), "rb") as f:
            self.assertEqual(f.read(), original)

    def test_counters_and_sequence(self):
        ctx = self.build_layout("memory")
        res = self.capture(ctx)
        counters = res["receipt"]["counters"]
        expected = memory_expected_counters()
        for k, v in expected.items():
            self.assertEqual(counters.get(k), v, k)
        snap = res["snapshot_path"]
        self.assertEqual(self.sel(snap,
                         "SELECT seq FROM sqlite_sequence WHERE name='outbox'"),
                         [(999,)])
        # Next insert on a separate inactive copy only.
        copy = os.path.join(self.base, "nextcopy.sqlite3")
        with open(snap, "rb") as f, open(copy, "wb") as g:
            g.write(f.read())
        os.chmod(copy, 0o600)
        conn = sqlite3.connect(copy)
        try:
            conn.execute("INSERT INTO outbox(operation_id,memory_id,revision,"
                         "action,status,payload,created_at) VALUES"
                         "('op-next','%s',0,'upsert','pending','{}',1)"
                         % MEM_IDS["m_local"])
            conn.commit()
            seq = conn.execute("SELECT seq FROM outbox WHERE operation_id='op-next'").fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(seq, 1000)

    def test_logical_fields_equal_source(self):
        ctx = self.build_layout("memory")
        res = self.capture(ctx)
        snap = res["snapshot_path"]
        for table, cols in (("memories", "id,text,admission,status,active"),
                            ("sources", "id,content,revision"),
                            ("outbox", "seq,operation_id,payload,receipt,status"),
                            ("candidates", "id,status,batch_id"),
                            ("batches", "id,status,message_id")):
            with self.subTest(table=table):
                q = "SELECT %s FROM %s ORDER BY 1" % (cols, table)
                self.assertEqual(self.sel(ctx["source"], q), self.sel(snap, q))
        self.assertEqual(
            self.sel(snap, "SELECT text FROM memories WHERE active=1 AND status='shared'"),
            [("shared fact",)])

    def test_verify_success_and_deterministic(self):
        ctx = self.build_layout("memory")
        res = self.capture(ctx)
        out = self.verify(ctx, res)
        self.assertEqual(out["profile"], "memory")
        self.assertEqual(out["cutoff_id"], ctx["cutoff"])
        self.assertIn("memories", out["tables_verified"])
        self.verify(ctx, res)

    def test_wal_rows_included_and_source_has_late_row(self):
        ctx = self.build_layout("memory")
        conn = sqlite3.connect(ctx["source"], timeout=10)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("INSERT INTO sources(id,guild_id,channel_id,"
                         "author_id,author_name,content,source_time,"
                         "received_time,content_hash) VALUES"
                         "('920000000000000009','g','c','a','n','wal-row',1,2,'h')")
            conn.commit()
            res = self.capture(ctx)
            self.assertEqual(
                self.sel(res["snapshot_path"],
                         "SELECT content FROM sources WHERE id='920000000000000009'"),
                [("wal-row",)])
            self.assertEqual(
                self.sel(ctx["source"],
                         "SELECT count(*) FROM sources WHERE id='920000000000000009'"),
                [(1,)])
        finally:
            conn.close()


class TestCaptureMeterSuccess(Base):
    def test_meter_success_and_values(self):
        ctx = self.build_layout("meter")
        res = self.capture(ctx)
        self.assertEqual(dir_entries(res["bundle_dir"]),
                         ["receipt.json", "snapshot.sqlite3"])
        snap = res["snapshot_path"]
        self.assertEqual(self.rowcount(snap, "usage_minute"), 2)
        self.assertEqual(self.rowcount(snap, "usage_day"), 1)
        self.assertEqual(self.rowcount(snap, "checkpoints"), 1)
        ref = self.logical_fingerprint(ctx["source"], "meter")[0]
        got = self.logical_fingerprint(snap, "meter")[0]
        self.assertEqual(ref, got)
        up = self.sel(snap, "SELECT up FROM usage_minute WHERE bucket=61000000")
        self.assertEqual(up, [(2 ** 53,)])

    def test_meter_totals_separate_minute_and_day(self):
        ctx = self.build_layout("meter")
        res = self.capture(ctx)
        c = res["receipt"]["counters"]
        self.assertEqual(c["usage_minute_up"], 2 ** 53 + 5)
        self.assertEqual(c["usage_minute_down"], 2 ** 53 + 13)
        self.assertEqual(c["usage_day_up"], 2 ** 53)
        self.assertEqual(c["usage_day_down"], 9)

    def test_meter_verify_success(self):
        ctx = self.build_layout("meter")
        res = self.capture(ctx)
        self.verify(ctx, res)


class TestReadTxnCutoff(Base):
    def test_later_committed_row_excluded(self):
        ctx = self.build_layout("memory")
        writer = sqlite3.connect(ctx["source"], timeout=1)
        self.addCleanup(writer.close)
        writer.execute("PRAGMA journal_mode=WAL")
        real = self.mod._logical_fingerprint
        captured = {"done": False}

        def patched(conn, profile, deadline):
            baseline = real(conn, profile, deadline)
            if not captured["done"]:
                captured["done"] = True
                writer.execute(
                    "INSERT INTO sources(id,guild_id,channel_id,author_id,"
                    "author_name,content,source_time,received_time,content_hash) "
                    "VALUES('920000000000000010','g','c','a','n','later',1,2,'h')")
                writer.commit()
            return baseline

        with mock.patch.object(self.mod, "_logical_fingerprint", patched):
            res = self.capture(ctx)
        self.assertTrue(captured["done"])
        query = "SELECT count(*) FROM sources WHERE id='920000000000000010'"
        self.assertEqual(self.sel(res["snapshot_path"], query), [(0,)])
        self.assertEqual(self.sel(ctx["source"], query), [(1,)])
        self.verify(ctx, res)


class TestSourceImmutability(Base):
    def test_verify_preserves_all_bytes_write_metadata_and_membership(self):
        ctx = self.build_layout("memory")
        res = self.capture(ctx)

        def observe():
            files, directories = {}, {}
            for parent, dirs, names in os.walk(ctx["work"]):
                directories[parent] = (full_stat(parent), tuple(sorted(dirs)), tuple(sorted(names)))
                for name in names:
                    path = os.path.join(parent, name)
                    files[path] = (full_stat(path), sha256_file(path))
            return files, directories

        before = observe()
        self.verify(ctx, res)
        self.assertEqual(observe(), before)

    def test_source_and_inputs_unchanged(self):
        ctx = self.build_layout("memory")
        watched = [ctx["source"], ctx["report"]]
        watched += [os.path.join(ctx["code_root"], r) for r in CODE_REL["memory"]]
        before = {p: full_stat(p) for p in watched}
        res = self.capture(ctx)
        for p in watched:
            self.assertEqual(full_stat(p), before[p], p)
        for suffix in ("-wal", "-shm", "-journal"):
            self.assertFalse(os.path.lexists(ctx["source"] + suffix))
        self.assertEqual(sorted(os.listdir(ctx["output"])),
                         [os.path.basename(res["bundle_dir"])])

    def test_sources_unavailable_after_capture_still_verifies(self):
        ctx = self.build_layout("memory")
        res = self.capture(ctx)
        moved = os.path.join(self.base, "moved")
        os.mkdir(moved, 0o700)
        os.rename(ctx["state"], os.path.join(moved, "state"))
        os.rename(ctx["code_root"], os.path.join(moved, "code"))
        self.verify(ctx, res)


class TestLogicalMutation(Base):
    def _mutate_and_reject(self, profile, mutate, fix_physical=True):
        ctx = self.build_layout(profile)
        res = self.capture(ctx)
        # baseline logical fingerprint is captured inside the receipt; the
        # mutation below intentionally makes verify's fresh digest differ.
        snap = res["snapshot_path"]
        conn = sqlite3.connect(snap)
        try:
            mutate(conn)
            conn.commit()
        finally:
            conn.close()

        def fix(rec, r):
            rec["snapshot"]["sha256"] = sha256_file(
                os.path.join(r["bundle_dir"], "snapshot.sqlite3"))
            rec["snapshot"]["size"] = os.stat(
                os.path.join(r["bundle_dir"], "snapshot.sqlite3")).st_size
        trusted = self.rewrite_receipt(res, fix) if fix_physical else \
            res["receipt_sha256"]
        self.assert_recovery_error(
            {"logical_digest_mismatch", "continuity_mismatch",
             "integrity_failed", "foreign_key_failed"},
            self.verify, ctx, {"bundle_dir": res["bundle_dir"],
                               "receipt_sha256": trusted})

    def test_memory_text_mutation(self):
        self._mutate_and_reject(
            "memory", lambda c: c.execute(
                "UPDATE memories SET text='x' WHERE active=1 AND status='shared'"))

    def test_memory_admission_mutation(self):
        self._mutate_and_reject(
            "memory", lambda c: c.execute(
                "UPDATE memories SET admission='bogus' WHERE active=1"))

    def test_memory_source_change(self):
        self._mutate_and_reject(
            "memory", lambda c: c.execute(
                "UPDATE sources SET content='changed'"))

    def test_memory_pending_candidate(self):
        self._mutate_and_reject(
            "memory", lambda c: c.execute(
                "UPDATE candidates SET status='accepted' WHERE status='pending'"))

    def test_memory_batch_evidence(self):
        self._mutate_and_reject(
            "memory", lambda c: c.execute("UPDATE batches SET hash='zzz'"))

    def test_memory_outbox_payload(self):
        self._mutate_and_reject(
            "memory", lambda c: c.execute(
                "UPDATE outbox SET payload='{\"c\":3}' WHERE status='pending'"))

    def test_memory_ack_receipt(self):
        self._mutate_and_reject(
            "memory", lambda c: c.execute(
                "UPDATE outbox SET receipt=NULL WHERE status='acknowledged'"))

    def test_memory_sequence_mutation(self):
        self._mutate_and_reject(
            "memory", lambda c: c.execute(
                "UPDATE sqlite_sequence SET seq=5 WHERE name='outbox'"))

    def test_meter_meta_mutation(self):
        self._mutate_and_reject(
            "meter", lambda c: c.execute(
                "UPDATE meta SET value='9' WHERE key='retry'"))

    def test_meter_checkpoint_mutation(self):
        self._mutate_and_reject(
            "meter", lambda c: c.execute("UPDATE checkpoints SET up=up+1"))

    def test_meter_usage_mutation(self):
        self._mutate_and_reject(
            "meter", lambda c: c.execute("UPDATE usage_day SET down=down+1"))

    def test_meter_health_mutation(self):
        self._mutate_and_reject(
            "meter", lambda c: c.execute("UPDATE health SET gaps=gaps+1"))


class TestReceiptAndFileTamper(Base):
    def test_untrusted_receipt_change_rejected(self):
        ctx = self.build_layout("meter")
        res = self.capture(ctx)
        rp = os.path.join(res["bundle_dir"], "receipt.json")
        with open(rp, "rb") as f:
            data = f.read()
        with open(rp, "wb") as f:
            f.write(data + b" ")
        os.chmod(rp, 0o600)
        self.assert_recovery_error({"receipt_hash_mismatch"},
                                   self.verify, ctx, res)

    def test_truncated_snapshot_rejected(self):
        ctx = self.build_layout("memory")
        res = self.capture(ctx)
        snap = res["snapshot_path"]
        size = os.stat(snap).st_size
        with open(snap, "r+b") as f:
            f.truncate(max(0, size - 512))
        os.chmod(snap, 0o600)
        self.assert_recovery_error(
            {"snapshot_hash_mismatch", "integrity_failed"},
            self.verify, ctx, res)

    def test_corrupted_snapshot_rejected(self):
        ctx = self.build_layout("meter")
        res = self.capture(ctx)
        snap = res["snapshot_path"]
        with open(snap, "r+b") as f:
            f.seek(64)
            old = f.read(1)
            f.seek(64)
            f.write(bytes([(old[0] ^ 0xFF)]))
        os.chmod(snap, 0o600)
        self.assert_recovery_error(
            {"snapshot_hash_mismatch", "integrity_failed"},
            self.verify, ctx, res)

    def test_report_bytes_change_rejected(self):
        ctx = self.build_layout("memory")
        res = self.capture(ctx)
        rp = os.path.join(res["bundle_dir"], "report.json")
        with open(rp, "r+b") as f:
            data = f.read()
            f.seek(0)
            f.write(data.replace(b"settled", b"settlea", 1))
        os.chmod(rp, 0o600)
        self.assert_recovery_error({"report_hash_mismatch"},
                                   self.verify, ctx, res)

    def test_report_field_change_rejected(self):
        ctx = self.build_layout("memory")
        res = self.capture(ctx)
        rp = os.path.join(res["bundle_dir"], "report.json")
        with open(rp, "rb") as f:
            obj = json.loads(f.read().decode())
        obj["days"]["2031-01-03"]["phase"] = "failed"
        data = json.dumps(obj, separators=(",", ":")).encode()
        with open(rp, "wb") as f:
            f.write(data)
        os.chmod(rp, 0o600)
        self.assert_recovery_error(
            {"report_hash_mismatch", "report_malformed"},
            self.verify, ctx, res)


class TestRefusals(Base):
    def test_non_ascii_report_date_keys_refused(self):
        ctx = self.build_layout("memory")
        for day in ("٢٠٢٦-٠١-٠١", "２０２６-０１-０１"):
            with self.subTest(day=day):
                data = json.loads(report_bytes())
                data["days"] = {day: {"phase": "sent"}}
                self.write_owned(ctx["report"], json.dumps(data).encode())
                self.assert_recovery_error({"report_malformed"}, self.capture, ctx)
                self.assertFalse(os.path.exists(self.bundle_path(ctx)))

    def test_empty_hardlinked_journal_refused(self):
        ctx = self.build_layout("meter")
        journal = ctx["source"] + "-journal"
        self.write_owned(journal, b"")
        os.link(journal, os.path.join(self.base, "second-journal-link"))
        self.assert_recovery_error({"source_aux_hardlink"}, self.capture, ctx)
        self.assertEqual(os.stat(journal).st_nlink, 2)

    def test_empty_foreign_owner_journal_refused(self):
        from types import SimpleNamespace
        ctx = self.build_layout("meter")
        journal = ctx["source"] + "-journal"
        self.write_owned(journal, b"")
        real_lstat = os.lstat

        def observed(path, *args, **kwargs):
            value = real_lstat(path, *args, **kwargs)
            if os.fspath(path) == journal:
                return SimpleNamespace(st_mode=value.st_mode, st_size=value.st_size,
                                       st_nlink=value.st_nlink, st_uid=os.geteuid() + 10000)
            return value

        with mock.patch.object(self.mod.os, "lstat", observed):
            self.assert_recovery_error({"source_aux_owner"}, self.capture, ctx)

    def test_wrong_schema_sha(self):
        ctx = self.build_layout("memory")
        self.assert_recovery_error({"trusted_schema_unrecognized"},
                                   self.capture, ctx,
                                   expected_schema_sha="0" * 64)

    def test_wrong_code_hash(self):
        ctx = self.build_layout("memory")
        bad = dict(ctx["code_shas"])
        bad[CODE_REL["memory"][0]] = "1" * 64
        self.assert_recovery_error({"code_hash_mismatch"}, self.capture, ctx,
                                   expected_code_sha256=bad)

    def test_wrong_devino(self):
        ctx = self.build_layout("memory")
        self.assert_recovery_error({"source_identity_mismatch"},
                                   self.capture, ctx,
                                   expected_ino=ctx["source_ino"] + 1)

    def test_bad_code_key_set(self):
        ctx = self.build_layout("memory")
        self.assert_recovery_error({"code_keys_invalid"}, self.capture, ctx,
                                   expected_code_sha256={"nope": "0" * 64})

    def test_extra_table_rejected(self):
        ctx = self.build_layout("meter")
        conn = sqlite3.connect(ctx["source"])
        try:
            conn.execute("CREATE TABLE extra(x INTEGER)")
            conn.commit()
        finally:
            conn.close()
        self.assert_recovery_error({"schema_extra_table"}, self.capture, ctx)

    def test_view_rejected(self):
        ctx = self.build_layout("meter")
        conn = sqlite3.connect(ctx["source"])
        try:
            conn.execute("CREATE VIEW v AS SELECT 1")
            conn.commit()
        finally:
            conn.close()
        self.assert_recovery_error({"schema_extra_object"}, self.capture, ctx)

    def test_bad_report_phase(self):
        ctx = self.build_layout("memory")
        bad = report_bytes()
        import re as _re
        bad = _re.sub(rb'"preparing"', b'"bogus"', bad, count=1)
        self.write_owned(ctx["report"], bad)
        self.assert_recovery_error({"report_malformed"}, self.capture, ctx)

    def test_duplicate_json_keys(self):
        ctx = self.build_layout("memory")
        data = (b'{"version":1,"version":1,"guildId":"' +
                GUILD.encode() + b'","channelId":"' + CHANNEL.encode() +
                b'","days":{}}')
        self.write_owned(ctx["report"], data)
        self.assert_recovery_error({"report_duplicate_key"}, self.capture, ctx)

    def test_nonfinite_report(self):
        ctx = self.build_layout("memory")
        data = report_bytes().replace(b'{"version"', b'{"z":1e999,"version"', 1)
        self.write_owned(ctx["report"], data)
        self.assert_recovery_error({"report_nonfinite"}, self.capture, ctx)

    def test_finite_float_accepted_unchanged(self):
        ctx = self.build_layout("memory")
        res = self.capture(ctx)
        with open(os.path.join(res["bundle_dir"], "report.json"), "rb") as f:
            self.assertIn(b"0.30000000000000004", f.read())
        self.verify(ctx, res)

    def test_missing_report(self):
        ctx = self.build_layout("memory", report=False)
        self.assert_recovery_error({"report_inputs_required", "not_regular:report", "path_invalid",
                                    "ancestor_invalid:report"},
                                   self.capture, ctx)

    def test_cutoff_escape(self):
        ctx = self.build_layout("memory")
        outside_before = sorted(os.listdir(self.base))
        self.assert_recovery_error({"cutoff_invalid"}, self.capture, ctx,
                                   cutoff_id="../../escape")
        self.assertEqual(sorted(os.listdir(self.base)), outside_before)
        self.assertEqual(sorted(os.listdir(ctx["output"])), [])

    def test_existing_output_run_path_refused(self):
        ctx = self.build_layout("memory")
        os.mkdir(self.bundle_path(ctx), 0o700)
        self.assert_recovery_error({"output_exists"},
                                   self.capture, ctx)
        self.assertEqual(sorted(os.listdir(self.bundle_path(ctx))), [])

    def test_symlink_output_parent_refused(self):
        ctx = self.build_layout("memory")
        link = os.path.join(self.base, "outlink")
        os.symlink(ctx["output"], link)
        self.assert_recovery_error(
            {"ancestor_invalid:output_parent", "output_parent_invalid"},
            self.capture, ctx, output_parent=link)

    def test_output_inside_state_refused(self):
        ctx = self.build_layout("memory")
        nested = self.mkdir700(ctx["state"], "nested_out")
        self.assert_recovery_error({"paths_overlap"}, self.capture, ctx,
                                   output_parent=nested)

    def test_output_inside_code_refused(self):
        ctx = self.build_layout("memory")
        nested = self.mkdir700(ctx["code_root"], "nested_out")
        self.assert_recovery_error({"paths_overlap"}, self.capture, ctx,
                                   output_parent=nested)

    def test_source_hardlink_refused(self):
        ctx = self.build_layout("memory")
        os.link(ctx["source"], os.path.join(ctx["state"], "hard.db"))
        self.assert_recovery_error({"hardlink:source"}, self.capture, ctx)

    def test_source_symlink_ancestor_refused(self):
        ctx = self.build_layout("memory")
        link = os.path.join(self.base, "statelink")
        os.symlink(ctx["state"], link)
        self.assert_recovery_error({"ancestor_invalid:source"},
                                   self.capture, ctx,
                                   source_db=os.path.join(
                                       link, DB_NAME["memory"]))

    def test_extra_sidecar_aux_in_state(self):
        ctx = self.build_layout("meter")
        extra = os.path.join(ctx["state"], "stray.txt")
        self.write_owned(extra, b"x", 0o600)
        res = self.capture(ctx)
        self.assertEqual(self.verify(ctx, res)["profile"], "meter")

    def test_source_writable_mode_refused(self):
        ctx = self.build_layout("meter")
        os.chmod(ctx["source"], 0o666)
        self.assert_recovery_error({"source_mode"}, self.capture, ctx)


class TestCapsAndDeadlines(Base):
    def test_physical_code_and_encoded_bounds_refuse(self):
        for attribute, value in (("_MAX_DB_BYTES", 1), ("_MAX_CODE_BYTES", 1),
                                 ("_MAX_ENCODED_TOTAL", 1)):
            with self.subTest(bound=attribute):
                ctx = self.build_layout("meter")
                with mock.patch.object(self.mod, attribute, value):
                    self.assert_recovery_error(lambda code: bool(code), self.capture, ctx)
                self.assertFalse(os.path.exists(os.path.join(self.bundle_path(ctx), "receipt.json")))
                os.rename(ctx["work"], ctx["work"] + attribute)

    def test_committed_wal_auxiliary_bound_refuses(self):
        ctx = self.build_layout("meter")
        writer = sqlite3.connect(ctx["source"])
        self.addCleanup(writer.close)
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("INSERT INTO meta VALUES('invented-extra', '1')")
        writer.commit()
        self.assertGreater(os.stat(ctx["source"] + "-wal").st_size, 0)
        with mock.patch.object(self.mod, "_MAX_AUX_TOTAL", 1):
            self.assert_recovery_error({"source_aux_cap"}, self.capture, ctx)

    def test_sql_progress_handler_interrupts_expired_work(self):
        now = [0.0]
        deadline = self.mod._Deadline(lambda: now[0])
        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        self.mod._prepare_read_txn(conn, deadline)
        now[0] = 31.0
        with self.assertRaises(sqlite3.OperationalError):
            conn.execute("WITH RECURSIVE n(x) AS (VALUES(1) UNION ALL "
                         "SELECT x+1 FROM n WHERE x<1000000) SELECT sum(x) FROM n").fetchone()

    def test_backup_progress_deadline_leaves_no_receipt(self):
        ctx = self.build_layout("meter")
        now = [0.0]
        real_connect = sqlite3.connect

        class BackupClock(sqlite3.Connection):
            def backup(self, destination, **kwargs):
                now[0] = 31.0
                return super().backup(destination, **kwargs)

        def connect(*args, **kwargs):
            kwargs["factory"] = BackupClock
            return real_connect(*args, **kwargs)

        with mock.patch.object(self.mod.sqlite3, "connect", connect):
            self.assert_recovery_error({"deadline_exceeded"}, self.capture, ctx,
                                       clock=lambda: now[0])
        self.assertTrue(os.path.isdir(self.bundle_path(ctx)))
        self.assertFalse(os.path.exists(os.path.join(self.bundle_path(ctx), "receipt.json")))

    def test_trailing_newline_cutoff_and_identity_refused(self):
        ctx = self.build_layout("memory")
        self.assert_recovery_error({"cutoff_invalid"}, self.capture, ctx,
                                   cutoff_id="safe\n")
        self.assert_recovery_error({"report_identity_invalid"}, self.capture, ctx,
                                   expected_guild_id=GUILD + "\n")

    def test_row_cap_includes_sequence(self):
        ctx = self.build_layout("memory")
        count = sum(self.rowcount(ctx["source"], table) for table in
                    ("meta", "sources", "memories", "memory_sources", "candidates",
                     "batches", "outbox", "sqlite_sequence"))
        self.assertEqual(self.rowcount(ctx["source"], "sqlite_sequence"), 1)
        orig = self.mod._MAX_ROWS
        self.mod._MAX_ROWS = count - 1
        self.addCleanup(setattr, self.mod, "_MAX_ROWS", orig)
        self.assert_recovery_error({"row_cap"}, self.capture, ctx)

    def test_deadline_exceeded_via_injected_clock(self):
        ctx = self.build_layout("meter")
        ticks = iter([0.0, 1e9, 1e9, 1e9, 1e9, 1e9, 1e9, 1e9, 1e9])

        def clock():
            try:
                return next(ticks)
            except StopIteration:
                return 1e9
        self.assert_recovery_error({"deadline_exceeded"}, self.capture, ctx,
                                   clock=clock)
        self.assertFalse(os.path.exists(self.bundle_path(ctx)))
        self.assertFalse(os.path.exists(
            os.path.join(self.bundle_path(ctx), "receipt.json")))

    def test_report_size_cap(self):
        ctx = self.build_layout("memory")
        orig = self.mod._MAX_REPORT_BYTES
        self.mod._MAX_REPORT_BYTES = 16
        self.addCleanup(setattr, self.mod, "_MAX_REPORT_BYTES", orig)
        self.assert_recovery_error({"too_large:report"}, self.capture, ctx)

    def test_schema_row_cap(self):
        ctx = self.build_layout("meter")
        orig = self.mod._MAX_SCHEMA_ROWS
        self.mod._MAX_SCHEMA_ROWS = 1
        self.addCleanup(setattr, self.mod, "_MAX_SCHEMA_ROWS", orig)
        self.assert_recovery_error({"schema_row_cap"}, self.capture, ctx)


class TestFaultInjection(Base):
    def test_report_change_after_snapshot_hash_refuses_receipt(self):
        ctx = self.build_layout("memory")
        real_hash = self.mod._sha_file
        changed = []

        def late_change(path, limit, deadline):
            result = real_hash(path, limit, deadline)
            if os.path.basename(path) == "snapshot.sqlite3" and not changed:
                self.write_owned(ctx["report"], report_bytes(extra={"late": True}))
                changed.append(True)
            return result

        with mock.patch.object(self.mod, "_sha_file", late_change):
            self.assert_recovery_error({"report_drift"}, self.capture, ctx)
        self.assertEqual(changed, [True])
        self.assertFalse(os.path.exists(os.path.join(self.bundle_path(ctx), "receipt.json")))

    def test_report_changed_during_backup(self):
        ctx = self.build_layout("memory")
        real = self.mod._capture_into

        def patched(*a, **k):
            with open(ctx["report"], "wb") as f:
                f.write(report_bytes(extra={"changed": True}))
            os.chmod(ctx["report"], 0o600)
            return real(*a, **k)
        with mock.patch.object(self.mod, "_capture_into", patched):
            self.assert_recovery_error({"report_drift"}, self.capture, ctx)
        bd = self.bundle_path(ctx)
        self.assertFalse(os.path.exists(os.path.join(bd, "receipt.json")))

    def test_fsync_failure_no_receipt(self):
        ctx = self.build_layout("meter")

        def boom(fd):
            raise OSError("injected fsync failure")
        with mock.patch.object(os, "fsync", boom):
            self.assert_recovery_error(
                {"snapshot_fsync_failed", "capture_io_error",
                 "receipt_write_failed"},
                self.capture, ctx)
        bd = self.bundle_path(ctx)
        self.assertFalse(os.path.exists(os.path.join(bd, "receipt.json")))

    def test_integrity_corruption_source_rejected(self):
        ctx = self.build_layout("meter")
        # corrupt a page in the source itself
        path = ctx["source"]
        size = os.stat(path).st_size
        with open(path, "r+b") as f:
            f.seek(min(4096, size - 1))
            f.write(b"\xff")
        os.chmod(path, 0o600)
        self.assert_recovery_error(
            {"integrity_failed", "capture_io_error", "foreign_key_failed"},
            self.capture, ctx)


class TestVerifyRefusals(Base):
    def test_fresh_local_copy_accepts_different_provenance_uid(self):
        ctx = self.build_layout("meter")
        result = self.capture(ctx)
        self.assertEqual(result["receipt"]["runtime"]["capture_uid"], os.geteuid())
        foreign_uid = os.geteuid() + 10000
        trusted = self.rewrite_receipt(
            result, lambda receipt, _: receipt["runtime"].update(capture_uid=foreign_uid))
        restored = self.mkdir700(self.base, "fresh-local-restore")
        for name in os.listdir(result["bundle_dir"]):
            with open(os.path.join(result["bundle_dir"], name), "rb") as source:
                self.write_owned(os.path.join(restored, name), source.read(), 0o600)
        checked = self.verify(ctx, {"bundle_dir": restored, "receipt_sha256": trusted})
        self.assertEqual(checked["profile"], "meter")
        self.assertTrue(all(os.lstat(os.path.join(restored, name)).st_uid == os.geteuid()
                            for name in os.listdir(restored)))

    def test_profile_mismatch(self):
        ctx = self.build_layout("meter")
        res = self.capture(ctx)
        self.assert_recovery_error({"profile_mismatch", "membership_mismatch"}, self.verify, ctx, res,
                                   expected_profile="memory")

    def test_bad_trusted_hash(self):
        ctx = self.build_layout("meter")
        res = self.capture(ctx)
        self.assert_recovery_error({"trusted_receipt_invalid"}, self.verify,
                                   ctx, res,
                                   trusted_receipt_sha256="nothex")

    def test_membership_extra_member(self):
        ctx = self.build_layout("meter")
        res = self.capture(ctx)
        extra = os.path.join(res["bundle_dir"], "stray.bin")
        self.write_owned(extra, b"x", 0o600)
        self.assert_recovery_error({"membership_mismatch"}, self.verify,
                                   ctx, res)

    def test_bundle_symlink_refused(self):
        ctx = self.build_layout("meter")
        res = self.capture(ctx)
        link = os.path.join(self.base, "bundlelink")
        os.symlink(res["bundle_dir"], link)
        self.assert_recovery_error({"bundle_invalid", "ancestor_invalid:bundle"},
                                   self.verify, ctx, res, bundle_dir=link)

    def test_bundle_member_hardlink_refused(self):
        ctx = self.build_layout("meter")
        res = self.capture(ctx)
        snap = res["snapshot_path"]
        os.link(snap, os.path.join(self.base, "snap-hard"))
        self.assert_recovery_error({"hardlink:bundle_member"}, self.verify,
                                   ctx, res)

    def test_runtime_mismatch_verify(self):
        ctx = self.build_layout("meter")
        res = self.capture(ctx)
        self.assert_recovery_error({"runtime_mismatch"}, self.verify, ctx,
                                   res, expected_sqlite_version="0.0.1")

    def test_snapshot_aux_in_bundle(self):
        ctx = self.build_layout("meter")
        res = self.capture(ctx)
        aux = res["snapshot_path"] + "-wal"
        self.write_owned(aux, b"", 0o600)
        self.assert_recovery_error({"membership_mismatch"}, self.verify, ctx, res)


if __name__ == "__main__":
    unittest.main()
