#!/usr/bin/env python3
"""SQLite task store + dispatcher logic + CLI for K2B orchestrator."""

import argparse
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta

try:
    import fcntl
    _HAVE_FLOCK = hasattr(fcntl, "flock")
except ImportError:  # pragma: no cover - non-POSIX platforms only
    fcntl = None
    _HAVE_FLOCK = False

# Path resolution (shared across modules)
K2B_VAULT = os.environ.get("K2B_VAULT_PATH") or os.path.expanduser("~/Projects/K2B-Vault")
DB_PATH = os.environ.get("K2B_ORCH_DB") or f"{K2B_VAULT}/System/orchestrator/orchestrator.sqlite"
RESULTS_DIR = f"{K2B_VAULT}/raw/orchestrator-results"
BOARD_PATH = f"{K2B_VAULT}/System/orchestrator/board.md"
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

LOCK_FILE = "/tmp/k2b-orchestrator.lock"


class FlightLockError(Exception):
    """Raised when a flight cannot be created because a non-terminal task
    already exists for the same entity_key."""
    pass


# Centralized status enum (Codex round-1 finding 5). A task is terminal iff it
# is one of these; everything else is non-terminal (holds the entity_key lock,
# shows on /portfolio active, is sweepable). A task may be CREATED only in one
# of VALID_INITIAL_STATUSES -- every other live state is reached via an explicit
# transition, never a bare add_task, so a typo'd or terminal-looking initial
# status cannot create a stuck or metadata-less row.
TERMINAL_STATUSES = frozenset({"done", "failed", "cancelled"})
ALL_STATUSES = frozenset({
    "ready", "running", "blocked", "zombie",
    "waiting_for_kimi_output", "needs_human", "returned",
    "done", "failed", "cancelled",
})
VALID_INITIAL_STATUSES = frozenset({"ready", "waiting_for_kimi_output", "needs_human"})


def _completion_sentinel(task_id: str) -> str:
    """The task-bound completion marker the deep-research prompt instructs the
    engine to emit as its final content line: `=== END OF <ENGINE> RESEARCH: <id> ===`.
    The conductor defaults to the KIMI form below, but the return gate is
    ENGINE-AGNOSTIC -- it accepts any engine token (KIMI / CHATGPT / PERPLEXITY /
    GEMINI / ...) via _sentinel_line_re(). Anti-truncation + paste-to-task binding;
    the engine *name* is irrelevant to either purpose."""
    return f"=== END OF KIMI RESEARCH: {task_id} ==="


def _sentinel_line_re(task_id: str):
    """Engine-agnostic completion-sentinel matcher, ANCHORED to a whole line. The
    sentinel must BE its own line `=== END OF <ENGINE> RESEARCH: <id> ===` (the `===`
    wrapper is optional; the engine token is bounded to <=40 chars of one or more
    words). Used with .fullmatch() on the stripped+lowercased line so embedded prose
    like 'at the end of market research: <id> and more...' is NOT treated as the
    sentinel (Codex finding 3). Binds to this task's id so a wrong-flight paste fails."""
    return re.compile(
        r"=*\s*end of\s+[a-z0-9][a-z0-9 ._\-]{0,40}\s+research\s*[:\-]?\s*"
        + re.escape(task_id.lower())
        + r"\s*=*"
    )


def _is_trailing_citation_line(stripped: str) -> bool:
    """After the completion sentinel, ONLY a genuine trailing citation/reference line
    is allowed -- a footnote definition, a line that IS a URL, a *named* references
    heading, or the HTML/decorative artifacts platforms (e.g. Perplexity) append.
    A line that merely *contains* a URL inside prose is NOT allowed: now that the
    prompt mandates a URL on every claim, substantive prose could otherwise disguise
    itself as a citation and slip past after the sentinel (Codex finding 1). Any
    disallowed non-empty line after the sentinel -> truncated / garbled / wrong-flight
    paste -> reject. `stripped` is assumed already .strip()'d and non-empty."""
    s = stripped
    low = s.lower()
    # footnote definition that is a pure URL reference -- FULL-LINE: marker, then one
    # or more whitespace-separated URL tokens, then nothing but trailing punctuation /
    # whitespace. `[^1]: https://...` passes (the shape every engine emits in a
    # trailing block, e.g. Perplexity); `[^x]: prose ... https://...` (prose before)
    # AND `[^x]: https://... more prose` (prose after) both FAIL -- substantive
    # continuation in disguise is not a citation (Codex round-2 -> round-4). Verbose
    # author/title/date citations belong in the body's References section ABOVE the
    # sentinel, where they are not gated by this allowlist.
    if re.match(r"^\[\^?[^\]]+\]:(\s*<?https?://\S+>?[.,;]?)+\s*$", s):
        return True
    if re.match(r"^<?https?://\S+>?[.,;]?$", s):         # a line that IS a url (not prose-with-url)
        return True
    # references heading / bold label -- heading-only text (no trailing prose).
    if re.match(r"^#{1,6}\s*(references|sources|citations|bibliography|notes|footnotes|works cited)\s*:?\s*$", low):
        return True
    if re.match(r"^\*\*\s*(references|sources|citations|bibliography|notes|footnotes|works cited)\s*\*\*:?$", low):
        return True
    # standalone html artifact line -- allowed ONLY if, after stripping tags, the
    # residue is non-prose (footnote markers / symbols / digits only). `<p>more
    # analysis</p>` carries prose and must NOT pass (Codex round-2 finding 1).
    if re.match(r"^<[^>]+>.*</[^>]+>$", s) or re.match(r"^<[^>]+/?>$", s):
        detagged = re.sub(r"<[^>]+>", "", s)
        return re.match(r"^[\s\W0-9_]*$", detagged) is not None
    if re.match(r"^[\W_]+$", s):                          # ***, ---, ⁂ decorative separators
        return True
    return False


def telegram_cmd():
    return os.environ.get("K2B_ORCH_TELEGRAM_CMD") or os.path.join(REPO_ROOT, "scripts", "send-telegram.sh")


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA busy_timeout=5000;")
    return conn


def init_db(conn) -> None:
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA busy_timeout=5000;")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS tasks (
          id               TEXT PRIMARY KEY,
          flight_id        TEXT NOT NULL,
          stage_name       TEXT NOT NULL,
          entity_key       TEXT,
          assignee_profile TEXT NOT NULL,
          parent_task      TEXT,
          depends_on       TEXT DEFAULT '[]',
          status           TEXT NOT NULL,
          command_key      TEXT,
          payload          TEXT DEFAULT '{}',
          workspace_path   TEXT,
          success_criteria TEXT,
          permissions      TEXT,
          lineage          TEXT DEFAULT '[]',
          preview_hash     TEXT,
          blocked_by       TEXT,
          blocker_reason   TEXT,
          result_url       TEXT,
          worker_pid       INTEGER,
          created_at       TEXT NOT NULL,
          updated_at       TEXT NOT NULL,
          started_at       TEXT,
          heartbeat_at     TEXT,
          finished_at      TEXT
        );
        """
    )
    conn.commit()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def gen_task_id(conn) -> str:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    cursor = conn.execute(
        "SELECT COUNT(*) FROM tasks WHERE id LIKE ?",
        (f"{today}-%",),
    )
    count = cursor.fetchone()[0]
    return f"{today}-{count + 1:03d}"


@contextmanager
def _acquire_lock():
    """Process-wide mutex for multi-statement DB transactions.

    Uses a BLOCKING fcntl.flock when available, so concurrent contenders queue
    on the SAME lock object (no split-brain, no starvation, no timeout). Only
    when fcntl/flock is genuinely unavailable do we use a blocking mkdir-spin
    fallback. The two primitives are never mixed: mixing a non-blocking flock
    with a mkdir fallback let a flock-holder and a mkdir-holder enter the
    critical section at once, which is the bug this replaces.
    """
    if _HAVE_FLOCK:
        lock_fd = os.open(LOCK_FILE, os.O_RDWR | os.O_CREAT, 0o644)
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX)  # blocking; waits its turn
            yield
        finally:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
            except OSError:
                pass
            try:
                os.close(lock_fd)
            except OSError:
                pass
    else:
        lock_dir = f"{LOCK_FILE}.d"
        while True:
            try:
                os.mkdir(lock_dir)
                break
            except FileExistsError:
                time.sleep(0.05)
        try:
            yield
        finally:
            try:
                os.rmdir(lock_dir)
            except OSError:
                pass


def add_task(
    *,
    assignee_profile,
    command_key,
    success_criteria,
    permissions,
    flight_id=None,
    stage_name="dispatch",
    entity_key=None,
    workspace_path=None,
    payload=None,
    parent_task=None,
    depends_on=None,
    status: str = "ready",
) -> str:
    # Import here to avoid circular dependency at module load
    from scripts.lib import orchestrator_profiles as profiles

    # A task may only be created in a valid initial state. Rejects typos (which
    # would otherwise become a stuck non-terminal row holding the entity lock)
    # and terminal-looking creates (which would skip terminal metadata).
    if status not in VALID_INITIAL_STATUSES:
        raise ValueError(
            f"invalid initial status {status!r}; allowed: {sorted(VALID_INITIAL_STATUSES)}"
        )

    with _acquire_lock():
        conn = connect()
        init_db(conn)
        conn.execute("BEGIN IMMEDIATE;")
        task_id = gen_task_id(conn)
        fid = flight_id if flight_id is not None else task_id
        now = now_iso()

        # One-flight lock: at most one non-terminal task per entity_key
        if entity_key is not None and entity_key.strip() != "":
            row = conn.execute(
                """
                SELECT id FROM tasks
                WHERE lower(trim(entity_key)) = lower(trim(?))
                  AND status NOT IN ('done', 'failed', 'cancelled')
                LIMIT 1
                """,
                (entity_key,),
            ).fetchone()
            if row:
                conn.execute("ROLLBACK;")
                conn.close()
                raise FlightLockError(
                    f"flight already active for {entity_key!r} (task {row['id']})"
                )

        # Workspace trust boundary: k2bi workspace comes ONLY from profile config/env
        if assignee_profile == "k2bi":
            stored_workspace = profiles.resolve_workspace("k2bi")
        else:
            stored_workspace = workspace_path

        conn.execute(
            """
            INSERT INTO tasks
            (id, flight_id, stage_name, entity_key, assignee_profile, parent_task,
             depends_on, status, command_key, payload, workspace_path,
             success_criteria, permissions, lineage, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                task_id,
                fid,
                stage_name,
                entity_key,
                assignee_profile,
                parent_task,
                json.dumps(depends_on if depends_on is not None else []),
                status,
                command_key,
                json.dumps(payload if payload is not None else {}),
                stored_workspace,
                success_criteria,
                permissions,
                json.dumps([]),
                now,
                now,
            ),
        )
        conn.commit()
        conn.close()
    return task_id


def get_task(task_id) -> dict | None:
    conn = connect()
    init_db(conn)
    row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def list_tasks(status=None) -> list[dict]:
    conn = connect()
    init_db(conn)
    if status is not None:
        rows = conn.execute(
            "SELECT * FROM tasks WHERE status = ? ORDER BY created_at",
            (status,),
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM tasks ORDER BY created_at").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def assignee_lock_held(assignee_profile, exclude_id=None) -> str | None:
    # A 'zombie' task holds the lock just like a 'running' one: it was left
    # zombie precisely because reclaim could NOT confirm its process group is
    # dead, so its K2Bi command may still be alive. Blocking same-assignee
    # dispatch until an operator resolves it is what prevents double-dispatch.
    conn = connect()
    init_db(conn)
    sql = "SELECT id FROM tasks WHERE assignee_profile = ? AND status IN ('running', 'zombie')"
    params = [assignee_profile]
    if exclude_id is not None:
        sql += " AND id != ?"
        params.append(exclude_id)
    sql += " LIMIT 1"
    row = conn.execute(sql, params).fetchone()
    conn.close()
    return row["id"] if row else None


def mark_running(task_id) -> bool:
    with _acquire_lock():
        conn = connect()
        init_db(conn)
        now = now_iso()
        cur = conn.execute(
            """
            UPDATE tasks SET status='running', started_at=?, heartbeat_at=?, updated_at=?
            WHERE id=? AND status='ready'
            """,
            (now, now, now, task_id),
        )
        conn.commit()
        changed = cur.rowcount == 1
        conn.close()
    return changed


def set_worker_pid(task_id, pid) -> bool:
    # Returns True only if it updated a still-'running' row. A worker that gets
    # False here was cancelled/reclaimed out from under it before it started and
    # MUST exit without running the command.
    with _acquire_lock():
        conn = connect()
        init_db(conn)
        now = now_iso()
        cur = conn.execute(
            """
            UPDATE tasks SET worker_pid=?, heartbeat_at=?, updated_at=?
            WHERE id=? AND status='running'
            """,
            (pid, now, now, task_id),
        )
        conn.commit()
        changed = cur.rowcount == 1
        conn.close()
    return changed


def cas_cancel(task_id, expect_status, expect_pid) -> bool:
    """Compare-and-swap transition to 'cancelled': only succeeds if the row is
    STILL the same status + worker_pid snapshot the caller killed. Prevents a
    cancel from clobbering a row that changed (e.g. was re-dispatched) between
    the death-confirmation and the transition.
    """
    with _acquire_lock():
        conn = connect()
        init_db(conn)
        now = now_iso()
        if expect_pid is None:
            cur = conn.execute(
                "UPDATE tasks SET status='cancelled', finished_at=?, updated_at=? "
                "WHERE id=? AND status=? AND worker_pid IS NULL",
                (now, now, task_id, expect_status),
            )
        else:
            cur = conn.execute(
                "UPDATE tasks SET status='cancelled', finished_at=?, updated_at=? "
                "WHERE id=? AND status=? AND worker_pid=?",
                (now, now, task_id, expect_status, expect_pid),
            )
        conn.commit()
        changed = cur.rowcount == 1
        conn.close()
    return changed


def _finalize_return(task_id, content, payload_json, return_path) -> bool:
    """Serialize the publish + state claim under the orchestrator lock so two
    concurrent return attempts (or a return racing a cancel / TTL sweep) cannot
    interleave. Only the attempt that, while holding the lock, still finds the
    flight 'waiting_for_kimi_output' writes the raw file and flips it to
    'returned'. A loser rechecks under the lock, finds it no longer waiting, and
    returns False WITHOUT writing anything -- so it can never overwrite the
    winner's evidence (Codex round-3) and leaves no orphan file. The raw file is
    fsync'd + os.replace'd BEFORE the in-transaction state flip, so the DB never
    records 'returned' for a missing file (Codex round-2). Returns True iff this
    attempt claimed the flight."""
    with _acquire_lock():
        conn = connect()
        init_db(conn)
        row = conn.execute(
            "SELECT status FROM tasks WHERE id=?", (task_id,)
        ).fetchone()
        if row is None or row["status"] != "waiting_for_kimi_output":
            conn.close()
            return False
        # Lock held + still waiting -> this attempt is the sole writer of this path.
        fd, tmp_path = tempfile.mkstemp(
            prefix=f".tmp_{os.path.basename(return_path)}.",
            suffix=".part",
            dir=os.path.dirname(return_path),
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(content)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, return_path)
            now = now_iso()
            conn.execute(
                "UPDATE tasks SET status='returned', payload=?, updated_at=? "
                "WHERE id=? AND status='waiting_for_kimi_output'",
                (payload_json, now, task_id),
            )
            conn.commit()
        except Exception:
            try:
                os.unlink(tmp_path)
            except FileNotFoundError:
                pass
            conn.close()
            raise
        conn.close()
    return True


def cas_to_ready(task_id, expect_status, payload_json=None) -> bool:
    """Locked compare-and-swap <expect_status> -> 'ready' (clearing blocker_reason).
    Only flips a row STILL in expect_status, so a concurrent cancel / TTL-expiry
    occurring between a caller's pre-lock read and this transition cannot be
    silently undone (no resurrecting a terminal row back to ready). rowcount 0 ==
    lost the race. Used by every blocked/needs_human/unblock -> ready path."""
    with _acquire_lock():
        conn = connect()
        init_db(conn)
        now = now_iso()
        if payload_json is not None:
            cur = conn.execute(
                "UPDATE tasks SET status='ready', blocker_reason=NULL, payload=?, updated_at=? "
                "WHERE id=? AND status=?",
                (payload_json, now, task_id, expect_status),
            )
        else:
            cur = conn.execute(
                "UPDATE tasks SET status='ready', blocker_reason=NULL, updated_at=? "
                "WHERE id=? AND status=?",
                (now, task_id, expect_status),
            )
        conn.commit()
        changed = cur.rowcount == 1
        conn.close()
    return changed


def mark_blocked(task_id, reason) -> None:
    with _acquire_lock():
        conn = connect()
        init_db(conn)
        now = now_iso()
        conn.execute(
            """
            UPDATE tasks SET status='blocked', blocker_reason=?, updated_at=?
            WHERE id=? AND status='ready'
            """,
            (reason, now, task_id),
        )
        conn.commit()
        conn.close()


def set_blocked_by(task_id, holder_id) -> None:
    with _acquire_lock():
        conn = connect()
        init_db(conn)
        now = now_iso()
        conn.execute(
            "UPDATE tasks SET blocked_by=?, updated_at=? WHERE id=?",
            (holder_id, now, task_id),
        )
        conn.commit()
        conn.close()


def clear_blocked_by(task_id) -> None:
    with _acquire_lock():
        conn = connect()
        init_db(conn)
        now = now_iso()
        conn.execute(
            "UPDATE tasks SET blocked_by=NULL, updated_at=? WHERE id=?",
            (now, task_id),
        )
        conn.commit()
        conn.close()


def transition(task_id, status, **fields) -> None:
    with _acquire_lock():
        conn = connect()
        init_db(conn)
        now = now_iso()
        updates = ["status = ?", "updated_at = ?"]
        params = [status, now]
        if status in {"done", "failed", "cancelled"}:
            updates.append("finished_at = ?")
            params.append(now)
        for k, v in fields.items():
            updates.append(f"{k} = ?")
            params.append(v)
        params.append(task_id)
        sql = f"UPDATE tasks SET {', '.join(updates)} WHERE id = ?"
        conn.execute(sql, params)
        conn.commit()
        conn.close()


def heartbeat(task_id) -> None:
    with _acquire_lock():
        conn = connect()
        init_db(conn)
        now = now_iso()
        conn.execute(
            "UPDATE tasks SET heartbeat_at=?, updated_at=? WHERE id=? AND status='running'",
            (now, now, task_id),
        )
        conn.commit()
        conn.close()


def _kill_group_and_confirm(worker_pid) -> bool:
    """SIGTERM/SIGKILL the worker's whole process group and confirm it is gone.

    Returns True only when the group is confirmed empty (killpg -> Process-
    LookupError). Returns False when death cannot be confirmed within the
    deadline (e.g. the PID was reused under another owner -> persistent
    PermissionError); callers MUST then NOT release any lock the task holds.

    Platform notes: a killed process becomes an unreaped zombie and
    `killpg(pgid, 0)` then returns PermissionError (never ProcessLookupError)
    until it is reaped, so we reap our own leader zombie via waitpid. The analyst
    command is a member of the worker's group, so signalling the group kills a
    surviving child even when the group leader already exited.
    """
    if worker_pid is None:
        return True
    import signal as _signal

    def _reap_leader():
        try:
            os.waitpid(worker_pid, os.WNOHANG)
        except (ChildProcessError, OSError):
            pass

    def _group_gone():
        # True = group empty; False = a member is alive; None = cannot confirm.
        _reap_leader()
        try:
            os.killpg(worker_pid, 0)
            return False
        except ProcessLookupError:
            return True
        except (PermissionError, OSError):
            return None

    def _poll_gone(window):
        # Poll THROUGH transient PermissionError (members reaped by init, not us)
        # until the group is confirmed empty or the deadline expires.
        deadline = time.time() + window
        while time.time() < deadline:
            if _group_gone() is True:
                return True
            time.sleep(0.3)
        return _group_gone() is True

    # Signal the whole group first -- kills live members even when the group
    # leader already exited.
    try:
        os.killpg(worker_pid, _signal.SIGTERM)
    except (ProcessLookupError, OSError):
        pass
    if _poll_gone(10):
        return True
    try:
        os.killpg(worker_pid, _signal.SIGKILL)
    except (ProcessLookupError, OSError):
        pass
    return _poll_gone(3)


def reclaim_zombies(timeout_s=300) -> list[str]:
    # KNOWN SHIP 1a RESIDUAL (accepted 2026-05-30; Ship 1b fencing token):
    # a task whose worker_pid is still NULL when its heartbeat goes >5min stale
    # is re-readied via _kill_group_and_confirm(None)==True. set_worker_pid is
    # the worker's 2nd line and writes a fresh heartbeat, so NULL-pid + 5min-stale
    # means the worker died at startup -> re-ready is safe and auto-recovers. The
    # unsafe case (a worker alive 5min without registering) is not realistically
    # reachable but is not *proven* impossible without a spawn-generation token.
    # See feature_orchestrator-fencing-token + the feature note's "Known Ship 1a
    # residual" section.
    from scripts.lib import orchestrator_profiles as profiles

    reclaimed = []
    with _acquire_lock():
        conn = connect()
        init_db(conn)
        cutoff = (datetime.now(timezone.utc).isoformat(),)
        # Find running tasks with stale heartbeat
        rows = conn.execute(
            """
            SELECT id, worker_pid, COALESCE(heartbeat_at, started_at) AS last_beat
            FROM tasks
            WHERE status = 'running'
            """
        ).fetchall()
        stale_ids = []
        for row in rows:
            last_beat = row["last_beat"]
            if last_beat is None:
                stale_ids.append((row["id"], row["worker_pid"]))
                continue
            try:
                lb = datetime.fromisoformat(last_beat)
                if datetime.now(timezone.utc) - lb > __import__("datetime").timedelta(seconds=timeout_s):
                    stale_ids.append((row["id"], row["worker_pid"]))
            except Exception:
                stale_ids.append((row["id"], row["worker_pid"]))

        for task_id, worker_pid in stale_ids:
            reason = f"zombie reclaim: no heartbeat > {timeout_s}s"
            conn.execute(
                "UPDATE tasks SET status='zombie', blocker_reason=?, updated_at=? WHERE id=?",
                (reason, now_iso(), task_id),
            )
            conn.commit()

            # Kill the worker's whole process group and CONFIRM it is gone
            # before re-readying. If death cannot be confirmed, leave the task
            # 'zombie' (it keeps holding the assignee lock) for operator
            # attention -- do NOT re-ready.
            if not _kill_group_and_confirm(worker_pid):
                continue

            # Now flip back to ready
            conn.execute(
                """
                UPDATE tasks SET status='ready', worker_pid=NULL, started_at=NULL,
                heartbeat_at=NULL, blocker_reason=?, updated_at=? WHERE id=?
                """,
                (reason, now_iso(), task_id),
            )
            conn.commit()
            reclaimed.append(task_id)
        conn.close()
    return reclaimed


def notify(message) -> None:
    cmd = telegram_cmd()
    subprocess.run([cmd, message], check=False)


def render_board(path=BOARD_PATH) -> None:
    tasks = list_tasks()
    lines = ["# Orchestrator Board\n"]
    # Group by flight_id
    flights = {}
    for t in tasks:
        fid = t.get("flight_id") or "default"
        flights.setdefault(fid, []).append(t)
    def _cell(v):
        # Flatten newlines / pipes and truncate so one value can't corrupt the
        # markdown table (e.g. a multi-line git-status blocker_reason).
        s = str(v or "").replace("\n", " · ").replace("|", "\\|").strip()
        return (s[:117] + "...") if len(s) > 120 else s

    for fid in sorted(flights.keys()):
        lines.append(f"\n## Flight: {fid}\n")
        lines.append("| Task | Stage | Status | Worker? | Blocker/Lock | Result |")
        lines.append("|---|---|---|---|---|---|")
        for t in flights[fid]:
            worker = "yes" if t.get("status") == "running" else "no"
            blocker = t.get("blocker_reason") or ""
            if t.get("blocked_by"):
                blocker = f"blocked by {t['blocked_by']}"
            result = t.get("result_url") or ""
            lines.append(
                f"| {_cell(t['id'])} | {_cell(t['stage_name'])} | {_cell(t['status'])} | "
                f"{worker} | {_cell(blocker)} | {_cell(result)} |"
            )
    content = "\n".join(lines) + "\n"
    tmp = f"{path}.tmp.{os.getpid()}"
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(tmp, "w") as f:
        f.write(content)
    os.replace(tmp, path)


def poll_once() -> dict:
    from scripts.lib import orchestrator_profiles as profiles

    reclaimed = reclaim_zombies()
    ttl_expired = []

    # TTL sweep: auto-cancel parked flights older than their TTL
    WAIT_TTL_DAYS = int(os.environ.get("K2B_ORCH_KIMI_TTL_DAYS", "14"))
    NEEDS_HUMAN_TTL_DAYS = int(os.environ.get("K2B_ORCH_NEEDS_HUMAN_TTL_DAYS", "7"))
    now = datetime.now(timezone.utc)

    with _acquire_lock():
        conn = connect()
        init_db(conn)
        rows = conn.execute(
            """
            SELECT id, status, updated_at FROM tasks
            WHERE status IN ('waiting_for_kimi_output', 'needs_human')
            """
        ).fetchall()
        for row in rows:
            updated_at = row["updated_at"]
            if not updated_at:
                continue
            try:
                updated_dt = datetime.fromisoformat(updated_at)
            except (ValueError, TypeError):
                continue
            # A naive parsed timestamp (legacy / manual insert) would raise
            # TypeError on subtraction from tz-aware `now` and abort poll_once.
            # Normalize to UTC instead of crashing the whole dispatch cycle.
            if updated_dt.tzinfo is None:
                updated_dt = updated_dt.replace(tzinfo=timezone.utc)
            age = now - updated_dt
            # >= timedelta, NOT age.days > N: age.days floors, so `> 14` would
            # not fire until ~15 days. Use exact-duration comparison.
            expired = (
                row["status"] == "waiting_for_kimi_output"
                and age >= timedelta(days=WAIT_TTL_DAYS)
            ) or (
                row["status"] == "needs_human"
                and age >= timedelta(days=NEEDS_HUMAN_TTL_DAYS)
            )
            if expired:
                n = now_iso()
                conn.execute(
                    "UPDATE tasks SET status='cancelled', blocker_reason='ttl-expired', "
                    "finished_at=?, updated_at=? WHERE id=?",
                    (n, n, row["id"]),
                )
                ttl_expired.append(row["id"])
        conn.commit()
        conn.close()

    spawned = None

    for task in list_tasks(status="ready"):
        if spawned is not None:
            break
        with _acquire_lock():
            conn = connect()
            init_db(conn)
            now = now_iso()
            # assignee lock check (same connection, inside flock). 'zombie'
            # holds the lock too -- see assignee_lock_held: an unconfirmed-dead
            # group must block same-assignee dispatch to prevent double-dispatch.
            row = conn.execute(
                "SELECT id FROM tasks WHERE assignee_profile = ? AND status IN ('running', 'zombie') AND id != ? LIMIT 1",
                (task["assignee_profile"], task["id"]),
            ).fetchone()
            if row:
                conn.execute(
                    "UPDATE tasks SET blocked_by=?, updated_at=? WHERE id=?",
                    (row["id"], now, task["id"]),
                )
                conn.commit()
                conn.close()
                continue
            else:
                conn.execute(
                    "UPDATE tasks SET blocked_by=NULL, updated_at=? WHERE id=?",
                    (now, task["id"]),
                )
            ok, reason = profiles.preflight(task)
            if not ok:
                cur = conn.execute(
                    "UPDATE tasks SET status='blocked', blocker_reason=?, updated_at=? WHERE id=? AND status='ready'",
                    (reason, now, task["id"]),
                )
                conn.commit()
                conn.close()
                notify(f"[orchestrator] Task {task['id']} BLOCKED: {reason}")
                continue
            # Atomic claim
            cur = conn.execute(
                "UPDATE tasks SET status='running', started_at=?, heartbeat_at=?, updated_at=? WHERE id=? AND status='ready'",
                (now, now, now, task["id"]),
            )
            claimed = cur.rowcount == 1
            conn.commit()
            conn.close()
        if claimed:
            subprocess.Popen(
                [sys.executable, "-m", "scripts.lib.orchestrator_worker", task["id"]],
                cwd=REPO_ROOT,
                start_new_session=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env={**os.environ, "PYTHONPATH": REPO_ROOT},
            )
            spawned = task["id"]

    render_board()
    return {"reclaimed": reclaimed, "spawned": spawned, "ttl_expired": ttl_expired}


def _print_task(task: dict) -> str:
    return json.dumps(task, indent=2, default=str)


def _main():
    parser = argparse.ArgumentParser(description="K2B Orchestrator Store CLI")
    sub = parser.add_subparsers(dest="cmd")

    sub.add_parser("init", help="Initialize DB and directories")
    sub.add_parser("print-db", help="Print resolved DB path")

    add_p = sub.add_parser("add", help="Add a task")
    add_p.add_argument("--profile", required=True)
    add_p.add_argument("--command-key", required=True)
    add_p.add_argument("--success", required=True)
    add_p.add_argument("--permissions", default="analyst-command")
    add_p.add_argument("--flight")
    add_p.add_argument("--entity")
    add_p.add_argument("--payload")
    add_p.add_argument("--workspace")
    add_p.add_argument("--status", default="ready")

    list_p = sub.add_parser("list", help="List tasks")
    list_p.add_argument("--status")
    list_p.add_argument("--json", action="store_true")

    sub.add_parser("flights", help="List flights")

    show_p = sub.add_parser("show", help="Show a task")
    show_p.add_argument("id")
    show_p.add_argument("--json", action="store_true")

    claim_p = sub.add_parser("claim", help="Claim a task (mark running)")
    claim_p.add_argument("id")

    complete_p = sub.add_parser("complete", help="Complete a task")
    complete_p.add_argument("id")
    complete_p.add_argument("--result")

    block_p = sub.add_parser("block", help="Block a task")
    block_p.add_argument("id")
    block_p.add_argument("--reason", required=True)

    unblock_p = sub.add_parser("unblock", help="Unblock a task (return to ready)")
    unblock_p.add_argument("id")

    cancel_p = sub.add_parser("cancel", help="Cancel a task")
    cancel_p.add_argument("id")

    return_p = sub.add_parser("return", help="Return a task")
    return_p.add_argument("id")
    return_p.add_argument("--text")
    return_p.add_argument("--path")

    sub.add_parser("poll-once", help="Run one dispatcher poll")
    sub.add_parser("render-board", help="Render the board markdown")

    args = parser.parse_args()

    if args.cmd == "print-db":
        print(DB_PATH)
        return

    if args.cmd == "init":
        os.makedirs(f"{K2B_VAULT}/System/orchestrator", exist_ok=True)
        os.makedirs(RESULTS_DIR, exist_ok=True)
        conn = connect()
        init_db(conn)
        conn.close()
        print("Initialized")
        return

    if args.cmd == "add":
        if args.profile == "k2bi" and args.workspace:
            print("Error: --workspace is not allowed for k2bi profile", file=sys.stderr)
            sys.exit(1)
        payload = None
        if args.payload:
            payload = json.loads(args.payload)
        try:
            tid = add_task(
                assignee_profile=args.profile,
                command_key=args.command_key,
                success_criteria=args.success,
                permissions=args.permissions,
                flight_id=args.flight,
                entity_key=args.entity,
                workspace_path=args.workspace,
                payload=payload,
                status=args.status,
            )
        except (FlightLockError, ValueError) as e:
            print(str(e), file=sys.stderr)
            sys.exit(1)
        print(f"Added task {tid}")
        return

    if args.cmd == "list":
        tasks = list_tasks(status=args.status)
        if args.json:
            print(json.dumps(tasks, indent=2, default=str))
        else:
            for t in tasks:
                print(f"{t['id']} | {t['status']} | {t['assignee_profile']} | {t['command_key']}")
        return

    if args.cmd == "flights":
        tasks = list_tasks()
        flights = sorted({t.get("flight_id") or "default" for t in tasks})
        for f in flights:
            print(f)
        return

    if args.cmd == "show":
        t = get_task(args.id)
        if t is None:
            print(f"Task {args.id} not found", file=sys.stderr)
            sys.exit(1)
        if args.json:
            print(json.dumps(t, indent=2, default=str))
        else:
            print(_print_task(t))
        return

    if args.cmd == "claim":
        ok = mark_running(args.id)
        print(f"Claimed: {ok}")
        return

    if args.cmd == "complete":
        t = get_task(args.id)
        if t is None:
            print(f"Task {args.id} not found", file=sys.stderr)
            sys.exit(1)
        # complete moves a task to a terminal state, which drops it out of the
        # running/zombie assignee-lock set. Refuse it for live/unconfirmed tasks
        # (the worker or reclaim resolves those; 'cancel' confirms death).
        if t["status"] in ("running", "zombie"):
            print(
                f"Error: refusing to complete a '{t['status']}' task ({args.id}); "
                f"it is resolved by the worker/reclaim, or by 'cancel' (which confirms process death)",
                file=sys.stderr,
            )
            sys.exit(1)
        # A parked agent-managed flight must NOT be completed directly: that would
        # bypass the return acceptance gate (raw output + sha256 + sentinel) and
        # silently release the one-flight entity lock. It must go through `return`
        # (-> returned) first; `complete` then finishes the returned flight.
        if t["status"] in ("waiting_for_kimi_output", "needs_human"):
            print(
                f"Error: refusing to complete a '{t['status']}' task ({args.id}); "
                f"resolve it via 'return' first (which runs the acceptance gate), or 'cancel' to drop it",
                file=sys.stderr,
            )
            sys.exit(1)
        # Locked CAS to 'done': the WHERE clause only flips from a completable live
        # state, so a concurrent cancel / TTL-expiry between the read above and here
        # cannot resurrect a terminal task to 'done' (Codex round-5 race class).
        with _acquire_lock():
            conn = connect()
            init_db(conn)
            now = now_iso()
            cur = conn.execute(
                "UPDATE tasks SET status='done', finished_at=?, updated_at=?, result_url=? "
                "WHERE id=? AND status NOT IN "
                "('done','failed','cancelled','running','zombie','waiting_for_kimi_output','needs_human')",
                (now, now, args.result, args.id),
            )
            conn.commit()
            done_ok = cur.rowcount == 1
            conn.close()
        if done_ok:
            print(f"Task {args.id} marked done")
            return
        print(
            f"Error: {args.id} is no longer in a completable state "
            f"(terminal, running, or parked); not completing",
            file=sys.stderr,
        )
        sys.exit(1)

    if args.cmd == "block":
        mark_blocked(args.id, args.reason)
        print(f"Task {args.id} blocked")
        return

    if args.cmd == "unblock":
        t = get_task(args.id)
        if t is None:
            print(f"Task {args.id} not found", file=sys.stderr)
            sys.exit(1)
        if t["status"] != "blocked":
            print(
                f"Error: can only unblock a 'blocked' task; {args.id} is '{t['status']}'",
                file=sys.stderr,
            )
            sys.exit(1)
        # Locked CAS so a concurrent cancel/TTL between the read above and here
        # cannot be resurrected back to 'ready' (Codex round-5).
        if cas_to_ready(args.id, "blocked"):
            print(f"Task {args.id} unblocked")
            return
        print(
            f"Error: {args.id} no longer blocked (cancelled or changed); not unblocking",
            file=sys.stderr,
        )
        sys.exit(1)

    if args.cmd == "cancel":
        t = get_task(args.id)
        if t is None:
            print(f"Task {args.id} not found", file=sys.stderr)
            sys.exit(1)
        # Cancelling a live/unconfirmed task drops it out of the assignee-lock
        # set, so we MUST confirm its process group is dead first -- otherwise a
        # surviving K2Bi command could run alongside a freshly dispatched one.
        if t["status"] in ("running", "zombie"):
            if t["worker_pid"] is None:
                # A running/zombie row with no registered PID is in the spawn
                # window (worker alive but not yet registered) or never started.
                # We cannot confirm death -> refuse. Reclaim handles a genuinely
                # dead startup after the heartbeat goes stale.
                print(
                    f"Error: task {args.id} is '{t['status']}' with no registered worker PID "
                    f"(it may be starting up); cannot confirm its process is dead. "
                    f"Wait for it to register or for reclaim, then retry.",
                    file=sys.stderr,
                )
                sys.exit(1)
            if not _kill_group_and_confirm(t["worker_pid"]):
                print(
                    f"Error: could not confirm task {args.id}'s process group is dead; "
                    f"NOT cancelling (it keeps holding the assignee lock). "
                    f"Verify the process is gone, then retry.",
                    file=sys.stderr,
                )
                sys.exit(1)
            # Compare-and-swap: only cancel if the row is still the same
            # status + worker_pid we just killed (guards against the row being
            # re-dispatched between the read and the kill-confirm).
            if not cas_cancel(args.id, t["status"], t["worker_pid"]):
                print(
                    f"Error: task {args.id} changed during cancel; not cancelling. Re-check and retry.",
                    file=sys.stderr,
                )
                sys.exit(1)
        else:
            transition(args.id, "cancelled")
        print(f"Task {args.id} cancelled")
        return

    if args.cmd == "return":
        t = get_task(args.id)
        if t is None:
            print(f"Task {args.id} not found", file=sys.stderr)
            sys.exit(1)
        # blocked -> ready re-dispatch behavior (no gate)
        if t["status"] == "blocked":
            payload = {}
            if args.text:
                payload["return_text"] = args.text
            # Locked CAS: do not resurrect a flight a concurrent cancel/TTL flipped
            # out of 'blocked' between the read above and here.
            if cas_to_ready(args.id, "blocked", json.dumps(payload)):
                print(f"Task {args.id} returned")
                return
            print(
                f"rejected: flight {args.id} no longer blocked (cancelled or changed)",
                file=sys.stderr,
            )
            sys.exit(1)
        # Only PARKED states may be returned. A 'zombie' is parked precisely
        # because its process group death is UNCONFIRMED, so returning it would
        # bypass the no-re-ready safety and risk double-dispatch; a 'running'
        # task is live; 'done'/'cancelled' are terminal.
        RETURNABLE = {"blocked", "needs_human", "waiting_for_kimi_output"}
        if t["status"] not in RETURNABLE:
            if t["status"] == "returned":
                print(
                    f"rejected: already returned (task {args.id})",
                    file=sys.stderr,
                )
            else:
                print(
                    f"Error: refusing to return a '{t['status']}' task ({args.id}); "
                    f"return is only valid from {sorted(RETURNABLE)}",
                    file=sys.stderr,
                )
            sys.exit(1)
        # Acceptance gate for waiting_for_kimi_output
        if t["status"] == "waiting_for_kimi_output":
            # Collect content from --text or --path (exactly one required)
            if bool(args.text) + bool(args.path) != 1:
                print(
                    "Error: return for waiting_for_kimi_output requires exactly one of --text or --path",
                    file=sys.stderr,
                )
                sys.exit(1)
            if args.path:
                with open(args.path, "r", encoding="utf-8") as f:
                    content = f.read()
            else:
                content = args.text

            # 1. size gate (whole paste)
            content_bytes = content.encode("utf-8")
            size = len(content_bytes)
            if size < 500 or size > 2_000_000:
                print(
                    f"rejected: size {size} bytes outside [500, 2000000]",
                    file=sys.stderr,
                )
                sys.exit(1)

            # 2. completion sentinel = the PRIMARY non-truncation + paste-to-flight
            # binding proof. Located FIRST so the body-quality gates below run on the
            # real pre-sentinel report body, not on trailing reference lines that a
            # near-the-top sentinel would otherwise let satisfy the gates (Codex
            # finding 2).
            #
            # ENGINE-AGNOSTIC: accept `=== END OF <ENGINE> RESEARCH: <id> ===` for any
            # engine token (KIMI / CHATGPT / PERPLEXITY / ...); the engine *name* is
            # irrelevant to anti-truncation or paste-binding.
            #
            # WHOLE-LINE: the sentinel must be its own line (anchored .fullmatch), so
            # prose that merely contains a sentinel-shaped phrase cannot pose as it
            # (Codex finding 3).
            #
            # POSITION-TOLERANT: the sentinel need not be the strict last line -- some
            # engines (Perplexity) append a `[^N]: url` reference block after it AND
            # echo the sentinel instruction near the top, so multiple exact-sentinel
            # lines is a NORMAL case. Take the LAST exact-line match as the boundary.
            sentinel_re = _sentinel_line_re(args.id)
            lines = content.splitlines()
            sentinel_idx = -1
            for i, ln in enumerate(lines):
                if sentinel_re.fullmatch(ln.strip().lower()):
                    sentinel_idx = i  # last exact whole-line match wins
            if sentinel_idx < 0:
                print(
                    f"rejected: missing completion sentinel (output must end with a line "
                    f"'=== END OF <ENGINE> RESEARCH: {args.id} ===', e.g. KIMI / CHATGPT / "
                    f"PERPLEXITY); looks truncated or not from this flight",
                    file=sys.stderr,
                )
                sys.exit(1)

            # 3. everything AFTER the sentinel must be a genuine trailing citation
            # block only (footnote defs / bare-url lines / named references heading /
            # html / decorative). Substantive prose after the sentinel -> truncated,
            # garbled, or a second report appended -> reject.
            stray = next(
                (ln.strip() for ln in lines[sentinel_idx + 1:]
                 if ln.strip() and not _is_trailing_citation_line(ln.strip())),
                None,
            )
            if stray is not None:
                print(
                    "rejected: substantive content after the completion sentinel "
                    f"(line: {stray[:80]!r}); looks like a truncated or garbled paste",
                    file=sys.stderr,
                )
                sys.exit(1)

            # 4. >= 5 substantive lines in the PRE-SENTINEL body. Citation/reference
            # lines are EXCLUDED from the count (Codex round-2 finding 2): a body made
            # only of long `[^N]: url` reference lines (or a sentinel near the top with
            # all content in trailing refs) is not a real research body and must fail.
            body_lines = lines[:sentinel_idx]
            substantive = sum(
                1 for ln in body_lines
                if len(ln.strip()) > 40 and not _is_trailing_citation_line(ln.strip())
            )
            if substantive < 5:
                print(
                    f"rejected: fewer than 5 substantive lines before the sentinel (found {substantive})",
                    file=sys.stderr,
                )
                sys.exit(1)

            # 5. >= 3 distinct URLs across the whole paste (sources legitimately live
            # in a trailing references block, so count whole-doc; the substantive-body
            # gate above already guarantees a real body exists).
            urls = set(re.findall(r"https?://[^\s)>\]\"']+", content))
            if len(urls) < 3:
                print(
                    f"rejected: fewer than 3 source URLs (found {len(urls)})",
                    file=sys.stderr,
                )
                sys.exit(1)

            # 6. cheap truncation heuristic -- unbalanced opening brackets in EITHER
            # the body tail (mid-body truncation) OR the whole-paste tail (a truncated
            # trailing reference, Codex finding 5). Real footnote defs `[^N]:` and url
            # lines are bracket-balanced, so a genuine reference block passes.
            body_text = "\n".join(lines[:sentinel_idx + 1])
            for label, chunk in (("body", body_text), ("tail", content)):
                tail = chunk[-200:] if len(chunk) >= 200 else chunk
                if (tail.count("[") + tail.count("(")) > (tail.count("]") + tail.count(")")):
                    print(
                        f"rejected: looks truncated (unbalanced brackets in {label})",
                        file=sys.stderr,
                    )
                    sys.exit(1)

            # 5. PASS -> publish raw + claim the state, serialized under the
            # orchestrator lock by _finalize_return (recheck-still-waiting + fsync'd
            # file replace + in-txn state flip). A concurrent cancel / TTL-expiry /
            # duplicate-return either loses (writes nothing, returns False) or the
            # DB is left consistent -- never 'returned' pointing at a missing file,
            # never a loser overwriting the winner's evidence (Codex rounds 1-3).
            return_bytes = size
            return_sha256 = hashlib.sha256(content_bytes).hexdigest()
            returned_at = now_iso()
            os.makedirs(RESULTS_DIR, exist_ok=True)
            return_path = f"{RESULTS_DIR}/{args.id}-kimi-raw.md"
            payload = json.loads(t.get("payload") or "{}")
            payload.update({
                "return_bytes": return_bytes,
                "return_sha256": return_sha256,
                "return_path": return_path,
                "returned_at": returned_at,
            })
            # File published BEFORE the state is claimed (see _finalize_return).
            if _finalize_return(args.id, content, json.dumps(payload), return_path):
                print(
                    f"Task {args.id} returned ({return_bytes} bytes, sha {return_sha256[:12]}) -> returned"
                )
                return
            print(
                f"rejected: flight {args.id} no longer waiting "
                f"(already returned, cancelled, or TTL-expired)",
                file=sys.stderr,
            )
            sys.exit(1)
        # needs_human -> ready (no gate, same as blocked). Locked CAS so a
        # concurrent cancel / TTL-expiry between the pre-lock read and here cannot
        # be silently undone (Codex round-4).
        payload = {}
        if args.text:
            payload["return_text"] = args.text
        if cas_to_ready(args.id, "needs_human", json.dumps(payload)):
            print(f"Task {args.id} returned")
            return
        print(
            f"rejected: flight {args.id} no longer needs_human (cancelled or TTL-expired)",
            file=sys.stderr,
        )
        sys.exit(1)

    if args.cmd == "poll-once":
        result = poll_once()
        print(json.dumps(result))
        return

    if args.cmd == "render-board":
        render_board()
        print(f"Board written to {BOARD_PATH}")
        return

    parser.print_help()
    sys.exit(1)


if __name__ == "__main__":
    sys.exit(_main())
