#!/usr/bin/env python3
"""SQLite task store + dispatcher logic + CLI for K2B orchestrator."""

import argparse
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime, timezone

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
) -> str:
    # Import here to avoid circular dependency at module load
    from scripts.lib import orchestrator_profiles as profiles

    with _acquire_lock():
        conn = connect()
        init_db(conn)
        task_id = gen_task_id(conn)
        fid = flight_id if flight_id is not None else task_id
        now = now_iso()

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
                "ready",
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
    return {"reclaimed": reclaimed, "spawned": spawned}


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

    return_p = sub.add_parser("return", help="Return a task (stub)")
    return_p.add_argument("id")
    return_p.add_argument("--text")

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
        tid = add_task(
            assignee_profile=args.profile,
            command_key=args.command_key,
            success_criteria=args.success,
            permissions=args.permissions,
            flight_id=args.flight,
            entity_key=args.entity,
            workspace_path=args.workspace,
            payload=payload,
        )
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
        transition(args.id, "done", result_url=args.result)
        print(f"Task {args.id} marked done")
        return

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
        transition(args.id, "ready", blocker_reason=None)
        print(f"Task {args.id} unblocked")
        return

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
        # Ship 1b stub: store payload, no chain
        t = get_task(args.id)
        if t is None:
            print(f"Task {args.id} not found", file=sys.stderr)
            sys.exit(1)
        # Only PARKED states may be returned to ready. A 'zombie' is parked
        # precisely because its process group death is UNCONFIRMED, so returning
        # it would bypass the no-re-ready safety and risk double-dispatch; a
        # 'running' task is live; 'done'/'cancelled' are terminal. To clear a
        # zombie, confirm the process is dead and `cancel` it.
        RETURNABLE = {"blocked", "needs_human", "waiting_for_kimi_output"}
        if t["status"] not in RETURNABLE:
            print(
                f"Error: refusing to return a '{t['status']}' task ({args.id}); "
                f"return is only valid from {sorted(RETURNABLE)}",
                file=sys.stderr,
            )
            sys.exit(1)
        payload = {}
        if args.text:
            payload["return_text"] = args.text
        transition(args.id, "ready", payload=json.dumps(payload))
        print(f"Task {args.id} returned")
        return

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
