#!/usr/bin/env python3
"""SQLite task store + dispatcher logic + CLI for K2B orchestrator."""

import argparse
import hashlib
import json
import os
import re
import secrets
import sqlite3
import stat
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import urlparse

from scripts.lib.orchestrator_a5_utils import (
    path_has_symlink_component as _a5_path_has_symlink_component,
    safe_read_text as _a5_safe_read_text,
)

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
TERMINAL_STATUSES = frozenset({
    "done",
    "failed",
    "cancelled",
    "terminal_bear_veto",
    "terminal_shipped",
    "terminal_limits_applied",
    "terminal_deployed",
})
ALL_STATUSES = frozenset({
    "ready", "running", "blocked", "zombie",
    "waiting_for_kimi_output", "needs_human", "returned",
    "waiting_for_agent_theme",
    "done", "failed", "cancelled", "terminal_bear_veto", "terminal_shipped",
    "terminal_limits_applied", "terminal_deployed",
})
NON_COMPLETABLE_STATUSES = tuple(sorted(
    TERMINAL_STATUSES
    | {
        "running",
        "zombie",
        "waiting_for_kimi_output",
        "needs_human",
        "waiting_for_agent_theme",
    }
))
# waiting_for_agent_theme: Chat-2 agent-native parked state. The in-session agent
# generates candidates + validates/repairs citations + writes the theme itself
# (no Kimi, no dispatched command); the flight is created PARKED here so poll_once
# never dispatches it. The ONLY exit is `verify-theme` (gate -> done); `complete`
# refuses it, mirroring the other parked states.
VALID_INITIAL_STATUSES = frozenset(
    {"ready", "waiting_for_kimi_output", "needs_human", "waiting_for_agent_theme"}
)


def _terminal_status_not_in_clause(column: str = "status") -> tuple[str, tuple[str, ...]]:
    statuses = tuple(sorted(TERMINAL_STATUSES))
    placeholders = ",".join("?" for _ in statuses)
    return (f"{column} NOT IN ({placeholders})", statuses)


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

        # One-flight lock: at most one non-terminal CHAIN per entity_key.
        # The lock is chain-scoped, not row-scoped: a task may join an existing
        # chain (and so coexist with its parent/siblings on the same entity) ONLY
        # when it presents a `parent_task` that resolves -- inside THIS transaction
        # -- to a still-live (non-terminal) row in the SAME flight with the SAME
        # normalized entity_key. We never trust a bare caller-supplied flight_id:
        # a standalone task (no parent_task) or a spoofed/reused flight id without
        # a matching live parent gets the FULL one-per-entity lock. This is what
        # lets an A1 parent-ledger flight (entity=TICKER, needs_human) coexist with
        # its bounded k2bi child dispatches (entity=TICKER, parent_task=<ledger>),
        # which the child preflight requires to match -- without letting an
        # independent second operation group itself under the same flight to slip
        # past the collision check (Codex Checkpoint-2 HIGH, 2026-06-07). Concurrent
        # same-profile worker dispatch is separately barred by poll_once's
        # running/zombie check + the worker lock, so chain-scoping the entity lock
        # does not weaken double-dispatch protection.
        if entity_key is not None and entity_key.strip() != "":
            # The exemption excludes ONLY the validated parent row (id != parent),
            # NOT the whole flight. This keeps the chain to at-most-one live child
            # besides the parent: a duplicate second child under the same parent
            # still collides (Codex Checkpoint-2 MEDIUM round-3) -- A1 children run
            # strictly sequentially (thesis terminal before bear is created), so a
            # second live child is never legitimate. A parent counts as a valid
            # exemption anchor only when it is a live, same-flight, same-entity row
            # AND is not LOGICALLY terminal: an A1 revision-limit park is
            # status=needs_human + payload terminal_reason, which the resume oracle
            # treats as terminal, so we fail closed on it here too (Codex
            # Checkpoint-2 HIGH round-3, 2026-06-07).
            exempt_parent = None
            terminal_clause, terminal_params = _terminal_status_not_in_clause("status")
            if parent_task:
                parent_row = conn.execute(
                    f"""
                    SELECT payload FROM tasks
                    WHERE id = ?
                      AND flight_id = ?
                      AND lower(trim(entity_key)) = lower(trim(?))
                      AND {terminal_clause}
                    LIMIT 1
                    """,
                    (parent_task, fid, entity_key, *terminal_params),
                ).fetchone()
                if parent_row is not None and not _payload_is_logically_terminal(
                    parent_row["payload"]
                ):
                    exempt_parent = parent_task
            if exempt_parent is not None:
                row = conn.execute(
                    f"""
                    SELECT id FROM tasks
                    WHERE lower(trim(entity_key)) = lower(trim(?))
                      AND {terminal_clause}
                      AND id != ?
                    LIMIT 1
                    """,
                    (entity_key, *terminal_params, exempt_parent),
                ).fetchone()
            else:
                row = conn.execute(
                    f"""
                    SELECT id FROM tasks
                    WHERE lower(trim(entity_key)) = lower(trim(?))
                      AND {terminal_clause}
                    LIMIT 1
                    """,
                    (entity_key, *terminal_params),
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
                "UPDATE tasks SET status='cancelled', blocker_reason=NULL, finished_at=?, updated_at=? "
                "WHERE id=? AND status=? AND worker_pid IS NULL",
                (now, now, task_id, expect_status),
            )
        else:
            cur = conn.execute(
                "UPDATE tasks SET status='cancelled', blocker_reason=NULL, finished_at=?, updated_at=? "
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


def _verify_theme_gate(theme_path) -> tuple[bool, str]:
    """Fail-closed gate for an AGENT-WRITTEN Chat-2 theme (no Kimi in the path).

    PASS requires, parsed from the theme's YAML frontmatter:
      * candidate-count is an int >= 5
      * a citation_ledger list, each row a mapping with status in
        {cite-ok, repaired, unverified}
      * >= 5 SUPPORTED rows (status cite-ok|repaired), each carrying a non-empty
        url + support_note + checked_at (proves the agent actually fetched/repaired)
      * >= 1 supported row with order in {2nd, 3rd} (a non-obvious beneficiary)
      * supported-ratio (supported / total ledger rows) >= 0.60

    Any malformation or shortfall returns (False, reason) -- the caller leaves the
    flight parked. Counting alone (the old dispatched-path gate) cannot prove a
    citation is real/supporting; the ledger makes the honesty rule checkable.
    """
    try:
        with open(theme_path) as f:
            text = f.read()
    except OSError:
        return (False, f"theme file unreadable: {theme_path}")
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return (False, "theme has no opening frontmatter fence")
    close_idx = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            close_idx = i
            break
    if close_idx is None:
        return (False, "theme frontmatter not closed")
    try:
        import yaml
    except ImportError:
        return (False, "yaml unavailable -- cannot validate (fail closed)")
    try:
        data = yaml.safe_load("\n".join(lines[1:close_idx]))
    except Exception:
        return (False, "theme frontmatter not parseable")
    if not isinstance(data, dict):
        return (False, "theme frontmatter is not a mapping")

    cc = data.get("candidate-count")
    if isinstance(cc, bool) or not isinstance(cc, int) or cc < 5:
        return (False, f"candidate-count must be an int >= 5 (got {cc!r})")

    ledger = data.get("citation_ledger")
    if not isinstance(ledger, list) or not ledger:
        return (False, "citation_ledger missing or empty")

    SUPPORTED = {"cite-ok", "repaired"}
    VALID_STATUS = {"cite-ok", "repaired", "unverified"}
    supported_symbols = set()
    supported_count = 0
    has_non_obvious = False
    seen_symbols = set()
    for r in ledger:
        if not isinstance(r, dict):
            return (False, "citation_ledger row is not a mapping")
        sym = str(r.get("symbol", "")).strip().upper()
        if not sym:
            return (False, "citation_ledger row missing symbol")
        # F3: one canonical candidate set -- no duplicate symbols inflating the count.
        if sym in seen_symbols:
            return (False, f"duplicate citation_ledger row for {sym}")
        seen_symbols.add(sym)
        st = str(r.get("status", "")).strip()
        if st not in VALID_STATUS:
            return (False, f"citation_ledger row {sym} has invalid status {st!r}")
        if st in SUPPORTED:
            url = str(r.get("url", "")).strip()
            note = str(r.get("support_note", "")).strip()
            checked = str(r.get("checked_at", "")).strip()
            if not (url and note and checked):
                return (False, f"supported ledger row {sym} missing url/support_note/checked_at")
            # F4 (r2): citations cannot be self-attested junk -- a structurally valid
            # http(s) URL (scheme + non-empty netloc, no whitespace/control chars), not
            # just an "http"-prefixed string like "https://" or "https://not a url".
            parsed = urlparse(url)
            if (
                parsed.scheme not in ("http", "https")
                or not parsed.netloc
                or any(ch.isspace() for ch in url)
            ):
                return (False, f"supported ledger row {sym} url is not a valid http(s) URL: {url!r}")
            try:
                datetime.fromisoformat(checked.replace("Z", "+00:00"))
            except (ValueError, TypeError):
                return (False, f"supported ledger row {sym} checked_at is not ISO-8601: {checked!r}")
            supported_count += 1
            supported_symbols.add(sym)
            if str(r.get("order", "")).strip() in ("2nd", "3rd"):
                has_non_obvious = True

    if len(supported_symbols) < 5:
        return (False, f"fewer than 5 distinct supported candidates ({len(supported_symbols)})")
    # F3: candidate-count must equal the distinct supported set (no inflation/mismatch).
    if cc != len(supported_symbols):
        return (
            False,
            f"candidate-count ({cc}) != distinct supported candidates ({len(supported_symbols)})",
        )
    if not has_non_obvious:
        return (False, "no 2nd/3rd-order beneficiary among supported candidates")
    ratio = supported_count / len(ledger)
    if ratio < 0.60:
        return (False, f"citation supported-ratio {ratio:.0%} < 60%")

    # F5 (r3 finding): the DISPLAYED candidate set must EXACTLY equal the supported
    # ledger symbols -- both the structured frontmatter `candidate_ark_scores` (what
    # K2Bi promotion reads) AND the body candidate-table ticker rows (what Keith reads
    # to pick). Otherwise a theme could pass on a clean ledger while displaying no
    # candidates, an extra unsupported ticker, or a different one -- and Keith could
    # promote an unvalidated body row.
    ark = data.get("candidate_ark_scores")
    if not isinstance(ark, dict):
        return (False, "candidate_ark_scores missing or not a mapping")
    ark_syms = {str(k).strip().upper() for k in ark.keys()}
    if ark_syms != supported_symbols:
        return (
            False,
            f"candidate_ark_scores symbols {sorted(ark_syms)} != supported ledger "
            f"symbols {sorted(supported_symbols)}",
        )
    body = "\n".join(lines[close_idx + 1:])
    body_syms = set()
    for m in re.finditer(r"(?m)^\|\s*([A-Za-z][A-Za-z0-9.]{0,6})\s*\|", body):
        tok = m.group(1).strip().upper()
        if tok in ("SYMBOL", "TICKER", "SYM", "NAME"):  # header cells
            continue
        if tok.isupper() or any(c.isdigit() for c in tok):
            body_syms.add(tok)
    if body_syms != supported_symbols:
        return (
            False,
            f"body candidate-table symbols {sorted(body_syms)} != supported ledger "
            f"symbols {sorted(supported_symbols)}",
        )
    return (True, "")


def verify_theme_complete(task_id, theme_path) -> tuple[bool, str]:
    """Run the Chat-2 theme gate and, on PASS, transition the parked
    waiting_for_agent_theme flight -> done (locked CAS, like _finalize_return).
    On gate failure the flight stays parked. Returns (ok, reason)."""
    # F2: the theme must be a DURABLE, INDEXED vault artifact -- not an arbitrary
    # temp file. Otherwise a temp file could pass the gate and release the
    # one-flight lock while no durable K2Bi macro-theme (or index row) exists.
    real = os.path.realpath(theme_path)
    vault = os.environ.get("K2BI_VAULT_PATH") or os.path.expanduser("~/Projects/K2Bi-Vault")
    macro_dir = os.path.realpath(os.path.join(vault, "wiki", "macro-themes"))
    base = os.path.basename(real)
    try:
        under = os.path.commonpath([real, macro_dir]) == macro_dir
    except ValueError:
        under = False
    if not under or real == macro_dir:
        return (False, f"theme must live under {macro_dir}/ (got {real})")
    if not (base.startswith("theme_") and base.endswith(".md")):
        return (False, f"theme filename must match theme_*.md (got {base})")
    try:
        with open(os.path.join(macro_dir, "index.md")) as f:
            idx = f.read()
    except OSError:
        return (False, "macro-themes/index.md unreadable -- theme not indexed")
    stem = base[:-3]  # theme_<slug>
    # F2 (r2): EXACT bounded membership, not substring -- a wikilink target
    # [[theme_slug|... / [[theme_slug\|... / [[theme_slug]] / [[theme_slug#..., or
    # the bare filename theme_slug.md as a whole token. Prevents theme_ai.md from
    # passing on a prefix match against theme_ai-compute-... .
    indexed = bool(
        re.search(r"\[\[" + re.escape(stem) + r"(?![\w-])", idx)
        or re.search(r"(?<![\w.-])" + re.escape(base) + r"(?![\w-])", idx)
    )
    if not indexed:
        return (False, f"theme {base} is not referenced in macro-themes/index.md")

    ok, reason = _verify_theme_gate(real)
    if not ok:
        return (False, reason)
    with _acquire_lock():
        conn = connect()
        init_db(conn)
        row = conn.execute(
            "SELECT status FROM tasks WHERE id=?", (task_id,)
        ).fetchone()
        if row is None:
            conn.close()
            return (False, f"task {task_id} not found")
        if row["status"] != "waiting_for_agent_theme":
            conn.close()
            return (
                False,
                f"task {task_id} not in waiting_for_agent_theme (is {row['status']})",
            )
        now = now_iso()
        cur = conn.execute(
            "UPDATE tasks SET status='done', finished_at=?, updated_at=?, result_url=? "
            "WHERE id=? AND status='waiting_for_agent_theme'",
            (now, now, real, task_id),
        )
        conn.commit()
        won = cur.rowcount == 1
        conn.close()
    if won:
        return (True, "")
    return (False, "lost the transition race (concurrent cancel / TTL-expiry)")


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
    if status not in ALL_STATUSES:
        raise ValueError(f"invalid status {status!r}")
    with _acquire_lock():
        conn = connect()
        init_db(conn)
        now = now_iso()
        row = conn.execute("SELECT status, payload FROM tasks WHERE id=?", (task_id,)).fetchone()
        if row is not None and row["status"] in TERMINAL_STATUSES and status != row["status"]:
            conn.close()
            raise ValueError(f"terminal status is irreversible: {row['status']}")
        if row is not None:
            payload = _payload_dict(row["payload"])
            try:
                revision_count = int(payload.get("revision_count") or 0)
            except (TypeError, ValueError):
                revision_count = 0
            if (
                payload.get("terminal_reason") == "revision_limit_exceeded"
                or revision_count > A1_MAX_REVISIONS
            ) and status != "cancelled":
                conn.close()
                raise ValueError("revision limit exceeded; use a fresh human decision")
        updates = ["status = ?", "updated_at = ?"]
        params = [status, now]
        if status in TERMINAL_STATUSES:
            updates.append("finished_at = ?")
            params.append(now)
        if status == "cancelled" and "blocker_reason" not in fields:
            fields["blocker_reason"] = None
        for k, v in fields.items():
            updates.append(f"{k} = ?")
            params.append(v)
        params.append(task_id)
        sql = f"UPDATE tasks SET {', '.join(updates)} WHERE id = ?"
        conn.execute(sql, params)
        conn.commit()
        conn.close()


A1_MAX_REVISIONS = 3
A3_MAX_SHIP_ATTEMPTS = 3
A4_MAX_APPLY_ATTEMPTS = 3
A5_MAX_DEPLOY_ATTEMPTS = 3
A1_BACKTEST_LOOK_AHEAD_VALUES = frozenset({"passed", "suspicious"})
TERMINAL_TTL_CREATED_AT_FLOOR = datetime(2024, 1, 1, tzinfo=timezone.utc)


def _payload_dict(raw_payload) -> dict:
    if raw_payload is None:
        return {}
    if isinstance(raw_payload, dict):
        return dict(raw_payload)
    if isinstance(raw_payload, str) and raw_payload.strip():
        parsed = json.loads(raw_payload)
        if isinstance(parsed, dict):
            return parsed
    return {}


def _payload_is_logically_terminal(raw_payload) -> bool:
    """True when an A1 chain parent is logically terminal even though its row
    status is still non-terminal. An A1 revision-limit park is status=needs_human
    + payload terminal_reason (revision_count > A1_MAX_REVISIONS); the resume
    oracle treats that as terminal, so the entity-lock exemption and the
    dispatch-time liveness check must too -- a child must never be admitted or
    dispatched under an exhausted chain (Codex Checkpoint-2 HIGH round-3)."""
    payload = _payload_dict(raw_payload)
    if payload.get("terminal_reason"):
        return True
    try:
        if int(payload.get("revision_count") or 0) > A1_MAX_REVISIONS:
            return True
    except (TypeError, ValueError):
        return False
    return False


def _a4_limits_resume_from_payload(status: str, payload: dict) -> str:
    # status is ALWAYS non-terminal here (the parent oracle returns on terminal
    # status before the chain_kind branch), so a non-terminal row carrying
    # limits_verified=True is corruption -- do NOT trust the flag to claim terminal
    # (fail closed: fall through to verify_limits, which re-inspects). CP2 r1 fix C.
    if status == "terminal_limits_applied":
        return "terminal_limits_applied"
    if payload.get("terminal_reason"):
        return "needs_human_terminal"
    # Attempt bounding lives ONLY at mark-dispatch/retry (>= A4_MAX_APPLY_ATTEMPTS),
    # which terminalizes via terminal_reason (handled above). The oracle carries no
    # raw attempt check (mirrors A3): a `>` would disagree with the dispatch gate and
    # a `>=` would strand the in-flight final attempt's verify. CP2 r1 fix B.
    if payload.get("limits_partial_detected_at"):
        return "limits_partial"
    if payload.get("limits_rolled_back_at"):
        return "limits_rolled_back"
    if not payload.get("limits_proposal_recorded"):
        return "author_limits_proposal"
    if not payload.get("limits_authorized"):
        return "await_limits_approval"
    if not payload.get("limits_dispatch_started_at"):
        return "dispatch_limits"
    return "verify_limits"


def _a5_deploy_resume_from_payload(status: str, payload: dict) -> str:
    # status is non-terminal here except for defensive direct calls. As with A4,
    # a non-terminal row carrying deploy_verified=True is corruption; only the
    # terminal row status can claim completion.
    if status == "terminal_deployed":
        return "terminal_deployed"
    if payload.get("terminal_reason"):
        return "needs_human_terminal"
    if payload.get("deploy_partial_detected_at"):
        return "deploy_partial"
    if payload.get("deploy_rolled_back_at"):
        return "deploy_rolled_back"
    if payload.get("deploy_deferred_at"):
        return "deploy_deferred"
    if not payload.get("deploy_preview_recorded"):
        return "preview_deploy"
    if not payload.get("deploy_authorized"):
        return "await_deploy_approval"
    if not payload.get("deploy_approval_token") or not payload.get("deploy_approved_at"):
        return "needs_human_terminal"
    if not payload.get("deploy_dispatch_started_at"):
        return "dispatch_deploy"
    return "verify_deploy"


def _update_payload_locked(
    conn,
    task_id: str,
    payload: dict,
    *,
    status: str | None = None,
    blocker_reason: str | None = None,
) -> bool:
    row = conn.execute("SELECT status FROM tasks WHERE id=?", (task_id,)).fetchone()
    if row and row["status"] in TERMINAL_STATUSES and status != row["status"]:
        raise ValueError(
            f"terminal status is irreversible: {row['status']} cannot update payload/status"
        )
    now = now_iso()
    updates = ["payload=?", "updated_at=?"]
    params = [json.dumps(payload, sort_keys=True), now]
    if status is not None:
        if status not in ALL_STATUSES:
            raise ValueError(f"invalid status {status!r}")
        updates.insert(0, "status=?")
        params.insert(0, status)
        if status in TERMINAL_STATUSES:
            updates.append("finished_at=?")
            params.append(now)
    if blocker_reason is not None:
        updates.append("blocker_reason=?")
        params.append(blocker_reason)
    params.append(task_id)
    cur = conn.execute(
        f"UPDATE tasks SET {', '.join(updates)} WHERE id=?",
        params,
    )
    return cur.rowcount == 1


def a1_claim_decisions_hash(claim_decisions) -> str:
    """Stable digest for a T7 claim-decision package.

    The digest is used only for idempotency bookkeeping; the adapter still
    receives the exact claim dicts and enforces the real safety contract.
    """
    canonical = json.dumps(claim_decisions, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def a1_t7_context_hash(chain_payload: dict, claim_decisions: list[dict]) -> str:
    """Stable digest for the full T7 decision context that affects thesis output."""
    payload = chain_payload or {}
    package = {
        "thesis_input": payload.get("thesis_input"),
        "claim_decisions": claim_decisions,
        "operator_override_reason": payload.get("operator_override_reason"),
        "calx_override_acknowledged": bool(payload.get("calx_override_acknowledged", False)),
        "vendor_warning_acknowledged": bool(payload.get("vendor_warning_acknowledged", False)),
        "vendor_provenance": payload.get("vendor_provenance"),
    }
    return a1_claim_decisions_hash(package)


def a1_prepare_thesis_dispatch_payload(chain_payload: dict, claim_decisions: list[dict]) -> dict:
    """Build the adapter payload for a thesis re/dispatch.

    If a thesis has already been written, a repeated or revised Stage 5-7 run
    must overwrite through the K2Bi adapter's `refresh=True` kwarg rather than
    appending a duplicate thesis.
    """
    payload = dict(chain_payload or {})
    digest = a1_t7_context_hash(payload, claim_decisions)
    existing_digest = payload.get("claim_decisions_hash")
    if existing_digest and existing_digest != digest:
        raise ValueError(
            "claim_decisions_hash mismatch; call a1_register_revision before "
            "dispatching amended T7 decisions"
        )
    existing_claim_decisions = payload.get("claim_decisions")
    if existing_claim_decisions is not None:
        existing_claim_digest = a1_t7_context_hash(payload, existing_claim_decisions)
        if existing_claim_digest != digest:
            raise ValueError(
                "claim_decisions changed without a registered revision; call "
                "a1_register_revision before dispatching amended T7 decisions"
            )
    payload["claim_decisions"] = claim_decisions
    payload["claim_decisions_hash"] = digest
    payload["thesis_dispatch_started_at"] = now_iso()
    if payload.get("thesis_written"):
        if not payload.get("thesis_artifact_verified"):
            raise ValueError("cannot refresh thesis before thesis_artifact_verified=true")
        payload["refresh"] = True
    else:
        payload["refresh"] = bool(payload.get("refresh", False))
    return payload


def a1_resume_action(task_id: str) -> str:
    """Return the next A1 conductor action from payload completion flags.

    This deliberately ignores legacy/freeform `payload.stage`: partial resume
    is driven by the explicit subtask flags required by the A1 contract.
    """
    with _acquire_lock():
        conn = connect()
        init_db(conn)
        try:
            return a1_resume_action_locked(conn, task_id)
        finally:
            conn.close()


def a1_resume_action_locked(conn, task_id: str) -> str:
    """Locked A1 resume oracle for callers already holding `_acquire_lock()`."""
    row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
    if row is None:
        raise KeyError(f"task {task_id} not found")
    task = dict(row)
    status = task.get("status")
    if status == "terminal_bear_veto":
        return "terminal_bear_veto"
    if status == "terminal_shipped":
        return "terminal_shipped"
    if status == "terminal_limits_applied":
        return "terminal_limits_applied"
    if status == "terminal_deployed":
        return "terminal_deployed"
    if status in TERMINAL_STATUSES:
        return f"terminal_{status}"
    payload = _payload_dict(task.get("payload"))
    if payload.get("chain_kind") == "limits":
        return _a4_limits_resume_from_payload(status, payload)
    if payload.get("chain_kind") == "deploy":
        return _a5_deploy_resume_from_payload(status, payload)
    if payload.get("bear_verdict") == "VETO":
        return "terminal_bear_veto"
    try:
        revision_count = int(payload.get("revision_count") or 0)
    except (TypeError, ValueError):
        revision_count = 0
    # Any terminal_reason (thesis revision-limit OR A2 strategy revision-limit) is
    # logically terminal -- the flight holds the entity lock and needs a fresh human
    # decision, not another dispatch.
    if payload.get("terminal_reason"):
        return "needs_human_terminal"
    if revision_count > A1_MAX_REVISIONS:
        return "needs_human_terminal"
    if not payload.get("promote_done"):
        return "await_promote"
    if not payload.get("screen_done"):
        return "dispatch_screen"
    if not payload.get("thesis_written") and not payload.get("screen_approved_by_operator"):
        return "await_screen_approval"
    if not payload.get("thesis_written"):
        return "dispatch_thesis"
    if payload.get("thesis_artifact_drift_detected_at"):
        return "thesis_artifact_invalid"
    if not payload.get("thesis_artifact_verified"):
        return "verify_thesis_artifact"
    ok, reason = _a1_thesis_artifact_payload_valid(payload)
    if not ok:
        payload["thesis_artifact_verified"] = False
        payload.pop("thesis_artifact_verified_at", None)
        payload["thesis_artifact_drift_detected_at"] = now_iso()
        payload["thesis_artifact_drift_reason"] = reason
        _update_payload_locked(conn, task_id, payload, status=status)
        conn.commit()
        return "thesis_artifact_invalid"
    if not payload.get("bear_done"):
        return "dispatch_bear_case"
    # Fail closed unless the bear verdict is exactly PROCEED (Checkpoint-2 #1). VETO
    # is already terminalized above; a bear_done payload with a missing/!=PROCEED
    # verdict (only reachable via a hand-built/partially-recovered payload, since
    # a1_mark_bear_verdict enforces the enum) must re-run bear, never silently open A2.
    if payload.get("bear_verdict") != "PROCEED":
        return "dispatch_bear_case"
    # A1 ends at the thesis gate. A2 (strategy half, Stages 9-10) extends past an
    # APPROVED thesis. Backward-compatible: with no thesis_approved flag (every A1
    # flight + every existing test), this returns "thesis_approval_gate" exactly as
    # A1 did -- A2 only advances once the operator records approval via approve-thesis.
    if not payload.get("thesis_approved"):
        return "thesis_approval_gate"
    if not payload.get("strategy_spec_written"):
        return "dispatch_strategy"
    if payload.get("strategy_artifact_drift_detected_at"):
        return "strategy_artifact_invalid"
    if not payload.get("strategy_artifact_verified"):
        return "verify_strategy_artifact"
    ok, reason = _a1_strategy_artifact_payload_valid(payload)
    if not ok:
        payload["strategy_artifact_verified"] = False
        payload.pop("strategy_artifact_verified_at", None)
        payload["strategy_artifact_drift_detected_at"] = now_iso()
        payload["strategy_artifact_drift_reason"] = reason
        _update_payload_locked(conn, task_id, payload, status=status)
        conn.commit()
        return "strategy_artifact_invalid"
    if not payload.get("backtest_done"):
        return "dispatch_backtest"
    # Fail closed at the gate on a missing/invalid sanity verdict (Checkpoint-2
    # round-2 #3): a stale/hand-recovered backtest_done without a valid
    # look_ahead_check must re-run the backtest, not expose the approval gate.
    if not _a1_backtest_payload_valid(payload)[0]:
        return "dispatch_backtest"
    if not payload.get("strategy_approved"):
        return "strategy_approval_gate"
    # A3 (Stage 11, ship-to-engine -- the capital path) extends the same parent
    # chain. It never goes terminal until the independent git/file inspector sees
    # the K2Bi-owned ship commit.
    if not payload.get("ship_authorized"):
        return "strategy_approved_await_ship"
    if payload.get("ship_verified"):
        return "terminal_shipped"
    if payload.get("ship_partial_detected_at"):
        return "ship_partial"
    if payload.get("ship_rolled_back_at"):
        return "ship_rolled_back"
    if not payload.get("ship_repo_authored"):
        return "author_strategy_to_repo"
    if not payload.get("ship_dispatch_started_at") and not payload.get("ship_proposed_commit_sha"):
        return "commit_strategy_proposed"
    if not payload.get("ship_dispatch_started_at"):
        return "dispatch_ship"
    return "verify_ship"


def _parse_iso_dt(value: str | None):
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _a1_thesis_artifact_payload_valid(payload: dict) -> tuple[bool, str]:
    raw_path = payload.get("thesis_path") or payload.get("thesis_artifact_path")
    if not raw_path:
        return (False, "thesis artifact path missing")
    path = Path(str(raw_path)).expanduser()
    if not path.is_file():
        return (False, f"thesis artifact missing or not a file: {path}")
    if path.stat().st_size == 0:
        return (False, f"thesis artifact is empty: {path}")
    expected_sha = payload.get("thesis_artifact_sha256")
    actual_sha = _sha256_file(path)
    if expected_sha:
        if expected_sha != actual_sha:
            return (False, f"thesis artifact sha256 mismatch: {path}")
        return (True, "")
    dispatch_dt = _parse_iso_dt(payload.get("thesis_dispatch_started_at"))
    if dispatch_dt is not None:
        artifact_dt = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        if artifact_dt < dispatch_dt:
            return (False, f"thesis artifact older than dispatch: {path}")
    return (True, "")


def _a1_strategy_artifact_payload_valid(payload: dict) -> tuple[bool, str]:
    """Integrity check for the A2 strategy artifact.

    Unlike the thesis file (which bear-case legitimately appends to), the
    strategy file is NEVER written by the backtest (K2Bi Check-D immutability),
    so once the sha is recorded it is stable across the backtest dispatch -- no
    re-anchor is needed here.
    """
    raw_path = payload.get("strategy_path")
    if not raw_path:
        return (False, "strategy artifact path missing")
    path = Path(str(raw_path)).expanduser()
    if not path.is_file():
        return (False, f"strategy artifact missing or not a file: {path}")
    if path.stat().st_size == 0:
        return (False, f"strategy artifact is empty: {path}")
    expected_sha = payload.get("strategy_artifact_sha256")
    if expected_sha:
        if expected_sha != _sha256_file(path):
            return (False, f"strategy artifact sha256 mismatch: {path}")
    return (True, "")


_BEAR_SECTION_RE = re.compile(r"(?im)^#{1,6}\s*bear case\b")


def _thesis_file_has_bear_section(path: Path) -> bool:
    """True iff the ticker file carries a `## Bear Case` heading -- the visible
    evidence that the bear worker legitimately appended its section. Used to gate
    re-anchoring so a corrupted/truncated file (which would NOT carry the marker)
    cannot become the new trusted baseline (Checkpoint-2 #2)."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return _BEAR_SECTION_RE.search(text) is not None


def _parse_md_frontmatter(path: Path) -> tuple[dict | None, str]:
    """Parse a markdown file's YAML frontmatter to a dict (or (None, reason)).

    Used to read evidence from the ARTIFACT itself rather than a caller's CLI claim:
    the backtest capture's strategy_slug + look_ahead_check (Checkpoint-2 round-3),
    and the strategy file's ticker/order.ticker for entity binding (round-5)."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return (None, f"unreadable: {path}")
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return (None, "no opening frontmatter fence")
    close_idx = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            close_idx = i
            break
    if close_idx is None:
        return (None, "frontmatter not closed")
    try:
        import yaml
    except ImportError:
        return (None, "yaml unavailable -- cannot validate frontmatter")
    try:
        data = yaml.safe_load("\n".join(lines[1:close_idx]))
    except Exception:
        return (None, "frontmatter not parseable")
    if not isinstance(data, dict):
        return (None, "frontmatter is not a mapping")
    return (data, "")


def _a1_backtest_payload_valid(payload: dict) -> tuple[bool, str]:
    """Durable, ARTIFACT-bound backtest-evidence invariant used by BOTH the resume
    oracle and a1_approve_strategy (Checkpoint-2 round-2 #3 + round-4): the gate
    must not rest on a cached DB enum alone. It re-opens the capture and re-binds
    it to the recorded sha (symmetric to the thesis + strategy artifact checks), so
    a deleted/edited/stale capture fails the gate closed rather than leaving the
    chain approvable on evidence that no longer exists."""
    if not payload.get("backtest_done"):
        return (False, "backtest not done")
    lac = str(payload.get("backtest_look_ahead_check") or "").strip().lower()
    if lac not in A1_BACKTEST_LOOK_AHEAD_VALUES:
        return (False, "backtest look_ahead_check missing or invalid")
    raw_path = payload.get("backtest_artifact_path")
    if not raw_path:
        return (False, "backtest artifact path missing")
    path = Path(str(raw_path)).expanduser()
    if not path.is_file():
        return (False, f"backtest artifact missing or not a file: {path}")
    if path.stat().st_size == 0:
        return (False, f"backtest artifact is empty: {path}")
    expected_sha = payload.get("backtest_artifact_sha256")
    if expected_sha:
        if expected_sha != _sha256_file(path):
            return (False, f"backtest artifact sha256 mismatch: {path}")
        return (True, "")
    # No recorded sha (flight recorded before this fix): re-validate by reparsing the
    # capture and confirming its verdict still matches the recorded enum, so a
    # post-record edit to the capture is still caught.
    fm, perr = _parse_md_frontmatter(path)
    if fm is None:
        return (False, f"backtest capture invalid: {perr}")
    bt_block = fm.get("backtest")
    capture_lac = ""
    if isinstance(bt_block, dict):
        capture_lac = str(bt_block.get("look_ahead_check", "")).strip().lower()
    if capture_lac != lac:
        return (False, "backtest capture verdict no longer matches the recorded value")
    return (True, "")


def _reanchor_thesis_sha_in_payload(payload: dict) -> bool:
    """Re-anchor thesis_artifact_sha256 to the CURRENT thesis file content.

    Bear-case appends a `## Bear Case` section to the SAME wiki/tickers/<SYM>.md
    file the thesis verify recorded a sha for, so the pre-bear sha no longer
    matches after bear runs (latent A1 finding #5). This advances the verified
    baseline to the post-bear content. Returns True iff re-anchored. Re-anchors
    ONLY when the current file carries the bear-section marker (Checkpoint-2 #2),
    so a corrupted/truncated file is NOT blessed -- the caller leaves the recorded
    sha as-is and the resume oracle then surfaces drift for operator recovery.
    """
    if payload.get("thesis_artifact_verified") is not True:
        return False
    raw_path = payload.get("thesis_path") or payload.get("thesis_artifact_path")
    if not raw_path:
        return False
    path = Path(str(raw_path)).expanduser()
    if not path.is_file() or path.stat().st_size == 0:
        return False
    if not _thesis_file_has_bear_section(path):
        return False
    payload["thesis_artifact_sha256"] = _sha256_file(path)
    payload["thesis_artifact_reanchored_at"] = now_iso()
    payload.pop("thesis_artifact_drift_detected_at", None)
    payload.pop("thesis_artifact_drift_reason", None)
    return True


def a1_verify_thesis_artifact(
    task_id: str,
    artifact_path: str | None = None,
    *,
    recovery_log_checked: bool = False,
) -> tuple[bool, str]:
    """Verify and record the thesis artifact before A1 can dispatch bear-case."""
    with _acquire_lock():
        conn = connect()
        init_db(conn)
        row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if row is None:
            conn.close()
            return (False, f"task {task_id} not found")
        task = dict(row)
        if task["status"] in TERMINAL_STATUSES:
            conn.close()
            return (False, f"task {task_id} is terminal: {task['status']}")
        payload = _payload_dict(task.get("payload"))
        if payload.get("thesis_artifact_drift_detected_at"):
            conn.close()
            return (
                False,
                "thesis artifact drift already detected; run clear-thesis-artifact before re-verifying",
            )
        raw_path = (
            artifact_path
            or payload.get("thesis_path")
            or payload.get("thesis_artifact_path")
        )
        if not raw_path:
            conn.close()
            return (False, "thesis artifact path missing")
        if artifact_path and not payload.get("thesis_dispatch_started_at"):
            conn.close()
            return (False, "explicit thesis artifact path requires thesis_dispatch_started_at")
        path = Path(str(raw_path)).expanduser()
        ok, reason = _a1_thesis_artifact_payload_valid({**payload, "thesis_path": str(path)})
        if not ok:
            conn.close()
            return (False, reason)
        payload["thesis_path"] = str(path)
        payload["thesis_artifact_sha256"] = _sha256_file(path)
        payload["thesis_artifact_verified"] = True
        verified_at = now_iso()
        payload["thesis_artifact_verified_at"] = verified_at
        if recovery_log_checked:
            payload["thesis_recovery_log_checked_at"] = verified_at
        _update_payload_locked(conn, task_id, payload, status=task["status"])
        conn.commit()
        conn.close()
    return (True, "")


def a1_force_verify_thesis_artifact(
    task_id: str,
    artifact_path: str,
    *,
    checked_log: bool,
) -> tuple[bool, str]:
    """Recovery helper for operator-verified artifacts after worker death/reclaim."""
    if not checked_log:
        return (False, "force verification requires --i-checked-the-log")
    if not artifact_path:
        return (False, "force verification requires an explicit thesis artifact path")
    return a1_verify_thesis_artifact(
        task_id,
        artifact_path,
        recovery_log_checked=True,
    )


def a1_clear_thesis_artifact(task_id: str, *, reason: str | None = None) -> tuple[bool, str]:
    """Explicitly clear invalid thesis/bear progress after the resume oracle reports drift."""
    with _acquire_lock():
        conn = connect()
        init_db(conn)
        row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if row is None:
            conn.close()
            return (False, f"task {task_id} not found")
        task = dict(row)
        if task["status"] in TERMINAL_STATUSES:
            conn.close()
            return (False, f"task {task_id} is terminal: {task['status']}")
        payload = _payload_dict(task.get("payload"))
        payload["thesis_written"] = False
        payload["bear_done"] = False
        for key in (
            "bear_verdict",
            "bear_conviction",
            "thesis_artifact_verified",
            "thesis_artifact_verified_at",
            "thesis_artifact_sha256",
            "thesis_recovery_log_checked_at",
            "thesis_artifact_drift_detected_at",
            "thesis_artifact_drift_reason",
        ):
            payload.pop(key, None)
        payload["thesis_artifact_invalid_at"] = now_iso()
        if reason:
            payload["thesis_artifact_invalid_reason"] = reason
        _update_payload_locked(conn, task_id, payload, status=task["status"])
        conn.commit()
        conn.close()
    return (True, "")


def a1_record_screen_done(task_id: str, artifact_path: str) -> tuple[bool, str]:
    """Verify a screen artifact and mark the A1 parent screen stage complete."""
    path = Path(str(artifact_path)).expanduser()
    if not path.is_file():
        return (False, f"screen artifact missing or not a file: {path}")
    if path.stat().st_size == 0:
        return (False, f"screen artifact is empty: {path}")
    with _acquire_lock():
        conn = connect()
        init_db(conn)
        row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if row is None:
            conn.close()
            return (False, f"task {task_id} not found")
        task = dict(row)
        if task["status"] in TERMINAL_STATUSES:
            conn.close()
            return (False, f"task {task_id} is terminal: {task['status']}")
        payload = _payload_dict(task.get("payload"))
        payload["screen_done"] = True
        payload["screen_artifact_path"] = str(path)
        payload["screen_done_at"] = now_iso()
        _update_payload_locked(conn, task_id, payload, status=task["status"])
        conn.commit()
        conn.close()
    return (True, "")


def a1_approve_screen(task_id: str) -> tuple[bool, str]:
    """Record Keith/operator approval to continue from A1 screen into thesis."""
    with _acquire_lock():
        conn = connect()
        init_db(conn)
        row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if row is None:
            conn.close()
            return (False, f"task {task_id} not found")
        task = dict(row)
        if task["status"] in TERMINAL_STATUSES:
            conn.close()
            return (False, f"task {task_id} is terminal: {task['status']}")
        payload = _payload_dict(task.get("payload"))
        if payload.get("screen_done") is not True:
            conn.close()
            return (False, "screen artifact must be recorded before approval")
        payload["screen_approved_by_operator"] = True
        payload["screen_approved_at"] = now_iso()
        _update_payload_locked(conn, task_id, payload, status=task["status"])
        conn.commit()
        conn.close()
    return (True, "")


def a1_mark_bear_verdict(task_id: str, verdict: str, *, conviction=None) -> bool:
    """Record the bear-case outcome and hard-stop VETO flights."""
    normalized = str(verdict or "").strip().upper()
    if normalized not in {"PROCEED", "VETO"}:
        raise ValueError("bear verdict must be PROCEED or VETO")
    with _acquire_lock():
        conn = connect()
        init_db(conn)
        row = conn.execute("SELECT payload, status FROM tasks WHERE id=?", (task_id,)).fetchone()
        if row is None:
            conn.close()
            raise KeyError(f"task {task_id} not found")
        if row["status"] in TERMINAL_STATUSES:
            conn.close()
            raise ValueError(f"task {task_id} is already terminal: {row['status']}")
        payload = _payload_dict(row["payload"])
        if payload.get("bear_done"):
            conn.close()
            return False
        payload["bear_done"] = True
        payload["bear_verdict"] = normalized
        if conviction is not None:
            payload["bear_conviction"] = conviction
        # Forward drift fix (latent A1 finding #5): bear-case appended to the SAME
        # ticker file the thesis verify recorded a sha for. Re-anchor the verified
        # baseline to the post-bear content NOW -- synchronously, the moment the
        # conductor records the worker's `done`, so no other writer is in the window
        # (same threat model as A1.2's deferred post-claim race). Only on PROCEED:
        # a VETO flight is terminal_bear_veto and never resumes.
        if normalized == "PROCEED":
            _reanchor_thesis_sha_in_payload(payload)
        status = "terminal_bear_veto" if normalized == "VETO" else "needs_human"
        changed = _update_payload_locked(conn, task_id, payload, status=status)
        conn.commit()
        conn.close()
    return changed


def a1_register_revision(task_id: str) -> bool:
    """Increment the bounded thesis-revision loop.

    Returns True while the conductor may re-run Stage 5-7. On the fourth
    request, leaves the flight parked in `needs_human` with a terminal reason
    so resume will surface escalation instead of dispatching again.
    """
    with _acquire_lock():
        conn = connect()
        init_db(conn)
        row = conn.execute("SELECT payload, status FROM tasks WHERE id=?", (task_id,)).fetchone()
        if row is None:
            conn.close()
            return False
        if row["status"] in TERMINAL_STATUSES:
            conn.close()
            return False
        if row["status"] not in {"needs_human", "blocked", "returned"}:
            conn.close()
            raise ValueError(f"cannot register revision while task is {row['status']}")
        payload = _payload_dict(row["payload"])
        count = int(payload.get("revision_count") or 0) + 1
        payload["revision_count"] = count
        if count > A1_MAX_REVISIONS:
            payload["terminal_reason"] = "revision_limit_exceeded"
            payload.setdefault("terminal_reason_at", now_iso())
            payload["thesis_written"] = False
            payload["bear_done"] = False
            for key in (
                "bear_verdict",
                "claim_decisions",
                "claim_decisions_hash",
                "thesis_artifact_verified",
                "thesis_artifact_verified_at",
                "thesis_artifact_sha256",
                "thesis_dispatch_started_at",
                "operator_override_reason",
                "calx_override_acknowledged",
                "vendor_warning_acknowledged",
                "vendor_provenance",
            ):
                payload.pop(key, None)
            _update_payload_locked(
                conn,
                task_id,
                payload,
                status="needs_human",
                blocker_reason=(
                    "revision limit exceeded; this flight keeps the entity lock. "
                    "Cancel it before starting a fresh A1 chain."
                ),
            )
            conn.commit()
            conn.close()
            return False
        # A revision restarts Stage 5-7 from a fresh source fetch and keeps the
        # previous decisions available for amendment.
        payload["thesis_written"] = False
        payload["bear_done"] = False
        for key in (
            "bear_verdict",
            "claim_decisions",
            "claim_decisions_hash",
            "thesis_artifact_verified",
            "thesis_artifact_verified_at",
            "thesis_artifact_sha256",
            "thesis_dispatch_started_at",
            "operator_override_reason",
            "calx_override_acknowledged",
            "vendor_warning_acknowledged",
            "vendor_provenance",
        ):
            payload.pop(key, None)
        changed = _update_payload_locked(conn, task_id, payload, status="needs_human")
        conn.commit()
        conn.close()
    return changed


def a1_reanchor_thesis_artifact(
    task_id: str,
    artifact_path: str | None = None,
    *,
    checked_log: bool,
    reason: str | None = None,
) -> tuple[bool, str]:
    """Operator-attested recovery: re-anchor the thesis sha to the current file.

    The ONLY legitimate cause of post-thesis drift is bear-case appending to the
    shared wiki/tickers/<SYM>.md, so this is guarded by `bear_done=True`. Because
    the bear child result carries no post-write sha (the bear runner returns
    path/verdict only; capturing a child sha would need a K2Bi change), the
    operator must explicitly attest via `--i-checked-the-log` that the current
    file is the legitimate post-bear thesis+bear artifact and not corruption
    (Checkpoint-1 C3). Without the ack, refuse. Pre-bear thesis drift is real
    corruption -> use clear-thesis-artifact, not this.
    """
    if not checked_log:
        return (
            False,
            "re-anchor requires --i-checked-the-log (operator must confirm the "
            "current file is the legitimate post-bear thesis artifact, not corruption)",
        )
    with _acquire_lock():
        conn = connect()
        init_db(conn)
        row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if row is None:
            conn.close()
            return (False, f"task {task_id} not found")
        task = dict(row)
        if task["status"] in TERMINAL_STATUSES:
            conn.close()
            return (False, f"task {task_id} is terminal: {task['status']}")
        payload = _payload_dict(task.get("payload"))
        if payload.get("bear_done") is not True:
            conn.close()
            return (
                False,
                "re-anchor is only valid after bear_done=true; pre-bear thesis drift "
                "is corruption -- use clear-thesis-artifact",
            )
        # Evidence of a PRIOR verification (a recorded sha or verified-at), NOT the
        # live thesis_artifact_verified flag -- the resume oracle clears that flag the
        # moment it detects the bear-append drift, so requiring it True here would
        # make recovery impossible exactly when it is needed.
        if not (
            payload.get("thesis_artifact_sha256")
            or payload.get("thesis_artifact_verified_at")
        ):
            conn.close()
            return (False, "re-anchor requires a previously verified thesis artifact")
        raw_path = (
            artifact_path
            or payload.get("thesis_path")
            or payload.get("thesis_artifact_path")
        )
        if not raw_path:
            conn.close()
            return (False, "thesis artifact path missing")
        path = Path(str(raw_path)).expanduser()
        if not path.is_file():
            conn.close()
            return (False, f"thesis artifact missing or not a file: {path}")
        if path.stat().st_size == 0:
            conn.close()
            return (False, f"thesis artifact is empty: {path}")
        # Even with the operator ack, require the bear-section marker (Checkpoint-2
        # #2): a corrupted/truncated file would not carry it, so re-anchoring it
        # would bless corruption. Absent the marker, refuse -- the operator must
        # clear + redispatch instead.
        if not _thesis_file_has_bear_section(path):
            conn.close()
            return (
                False,
                "thesis file is missing the '## Bear Case' section; it is not the "
                "expected post-bear artifact -- use clear-thesis-artifact + redispatch",
            )
        payload["thesis_path"] = str(path)
        payload["thesis_artifact_sha256"] = _sha256_file(path)
        payload["thesis_artifact_verified"] = True
        payload["thesis_artifact_reanchored_at"] = now_iso()
        payload["thesis_recovery_log_checked_at"] = now_iso()
        if reason:
            payload["thesis_artifact_reanchor_reason"] = reason
        payload.pop("thesis_artifact_drift_detected_at", None)
        payload.pop("thesis_artifact_drift_reason", None)
        _update_payload_locked(conn, task_id, payload, status=task["status"])
        conn.commit()
        conn.close()
    return (True, "")


def a1_approve_thesis(task_id: str) -> tuple[bool, str]:
    """Record operator approval at the thesis gate (Stage 8), opening A2.

    Guard: the thesis must actually be AT the gate -- thesis written + artifact
    verified + bear done with a non-VETO verdict. A1 ended here conversationally;
    A2 needs the approval recorded as a flag so the resume oracle can advance.
    """
    with _acquire_lock():
        conn = connect()
        init_db(conn)
        row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if row is None:
            conn.close()
            return (False, f"task {task_id} not found")
        task = dict(row)
        if task["status"] in TERMINAL_STATUSES:
            conn.close()
            return (False, f"task {task_id} is terminal: {task['status']}")
        payload = _payload_dict(task.get("payload"))
        # Require an explicit PROCEED, not merely "not VETO" (Checkpoint-2 #1) -- a
        # missing/invalid verdict must not be approvable.
        if payload.get("bear_verdict") != "PROCEED":
            conn.close()
            return (
                False,
                f"bear verdict must be PROCEED to approve (got {payload.get('bear_verdict')!r})",
            )
        if not (
            payload.get("thesis_written")
            and payload.get("thesis_artifact_verified")
            and payload.get("bear_done")
        ):
            conn.close()
            return (
                False,
                "thesis is not at the approval gate "
                "(need thesis_written + thesis_artifact_verified + bear_done)",
            )
        if payload.get("thesis_artifact_drift_detected_at"):
            conn.close()
            return (
                False,
                "thesis artifact drift detected; re-anchor or clear before approving",
            )
        payload["thesis_approved"] = True
        payload["thesis_approved_at"] = now_iso()
        _update_payload_locked(conn, task_id, payload, status=task["status"])
        conn.commit()
        conn.close()
    return (True, "")


def a1_record_strategy_done(task_id: str, artifact_path: str) -> tuple[bool, str]:
    """Verify a strategy artifact and mark strategy_spec_written on the parent.

    Mirrors a1_record_screen_done: record the flag ONLY after the worker reached
    `done` AND the artifact exists + is non-empty. Refuses before the thesis is
    approved (chain validity).
    """
    path = Path(str(artifact_path)).expanduser()
    if not path.is_file():
        return (False, f"strategy artifact missing or not a file: {path}")
    if path.stat().st_size == 0:
        return (False, f"strategy artifact is empty: {path}")
    with _acquire_lock():
        conn = connect()
        init_db(conn)
        row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if row is None:
            conn.close()
            return (False, f"task {task_id} not found")
        task = dict(row)
        if task["status"] in TERMINAL_STATUSES:
            conn.close()
            return (False, f"task {task_id} is terminal: {task['status']}")
        payload = _payload_dict(task.get("payload"))
        if not payload.get("thesis_approved"):
            conn.close()
            return (False, "strategy cannot be recorded before the thesis is approved")
        # Bind the strategy artifact to the parent ticker (Checkpoint-2 round-5):
        # A2 dispatches use payload_path, so profile preflight only sees the task
        # payload's top-level symbol while the adapter writes the FILE's own decision
        # ticker. Validate the recorded strategy's frontmatter ticker == entity_key
        # so a wrong/stale carrier file (e.g. strategy_spy.md) can never become a
        # CDNS flight's strategy and SHA-bind the gate to the wrong ticker.
        entity = str(task.get("entity_key") or "").strip().upper()
        if entity:
            fm, perr = _parse_md_frontmatter(path)
            if fm is None:
                conn.close()
                return (False, f"strategy artifact invalid: {perr}")
            order = fm.get("order")
            order_ticker = (
                str(order.get("ticker", "")).strip().upper()
                if isinstance(order, dict)
                else ""
            )
            strat_ticker = order_ticker or str(fm.get("ticker", "")).strip().upper()
            if strat_ticker != entity:
                conn.close()
                return (
                    False,
                    f"strategy artifact ticker {strat_ticker!r} does not match the "
                    f"parent flight entity {entity!r}",
                )
        payload["strategy_spec_written"] = True
        payload["strategy_path"] = str(path)
        payload["strategy_done_at"] = now_iso()
        # A fresh strategy supersedes any prior verification/drift AND any downstream
        # backtest/approval evidence from a prior strategy (Checkpoint-2 #3): a
        # re-recorded strategy must not inherit a stale backtest or approval, which
        # would let resume skip dispatch_backtest or jump to the ship gate with old
        # evidence attached to a new artifact.
        payload["strategy_artifact_verified"] = False
        payload["backtest_done"] = False
        for key in (
            "strategy_artifact_verified_at",
            "strategy_artifact_sha256",
            "strategy_artifact_drift_detected_at",
            "strategy_artifact_drift_reason",
            "backtest_artifact_path",
            "backtest_artifact_sha256",
            "backtest_look_ahead_check",
            "backtest_done_at",
            "strategy_approved",
            "strategy_approved_at",
        ):
            payload.pop(key, None)
        _update_payload_locked(conn, task_id, payload, status=task["status"])
        conn.commit()
        conn.close()
    return (True, "")


def a1_verify_strategy_artifact(
    task_id: str, artifact_path: str | None = None
) -> tuple[bool, str]:
    """Verify and record the strategy artifact before A2 can dispatch the backtest."""
    with _acquire_lock():
        conn = connect()
        init_db(conn)
        row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if row is None:
            conn.close()
            return (False, f"task {task_id} not found")
        task = dict(row)
        if task["status"] in TERMINAL_STATUSES:
            conn.close()
            return (False, f"task {task_id} is terminal: {task['status']}")
        payload = _payload_dict(task.get("payload"))
        if not payload.get("strategy_spec_written"):
            conn.close()
            return (False, "strategy artifact not recorded yet (run record-strategy-done)")
        if payload.get("strategy_artifact_drift_detected_at"):
            conn.close()
            return (
                False,
                "strategy artifact drift detected; re-run record-strategy-done after redispatch",
            )
        raw_path = artifact_path or payload.get("strategy_path")
        if not raw_path:
            conn.close()
            return (False, "strategy artifact path missing")
        path = Path(str(raw_path)).expanduser()
        if not path.is_file():
            conn.close()
            return (False, f"strategy artifact missing or not a file: {path}")
        if path.stat().st_size == 0:
            conn.close()
            return (False, f"strategy artifact is empty: {path}")
        existing_sha = payload.get("strategy_artifact_sha256")
        actual_sha = _sha256_file(path)
        if existing_sha and existing_sha != actual_sha:
            conn.close()
            return (False, f"strategy artifact sha256 mismatch: {path}")
        payload["strategy_path"] = str(path)
        payload["strategy_artifact_sha256"] = actual_sha
        payload["strategy_artifact_verified"] = True
        payload["strategy_artifact_verified_at"] = now_iso()
        _update_payload_locked(conn, task_id, payload, status=task["status"])
        conn.commit()
        conn.close()
    return (True, "")


def a1_record_backtest_done(
    task_id: str, artifact_path: str, *, look_ahead_check: str | None = None
) -> tuple[bool, str]:
    """Verify a backtest capture and mark backtest_done on the parent.

    Records the sanity-gate verdict (`passed` / `suspicious`) so the strategy
    gate can surface the overfit/look-ahead flag. Refuses before the strategy
    artifact is verified, and refuses a missing/unknown sanity verdict
    (Checkpoint-2 #4) -- the strategy gate must never approve on an absent or
    garbage look_ahead_check.
    """
    path = Path(str(artifact_path)).expanduser()
    if not path.is_file():
        return (False, f"backtest artifact missing or not a file: {path}")
    if path.stat().st_size == 0:
        return (False, f"backtest artifact is empty: {path}")
    normalized_lac = str(look_ahead_check or "").strip().lower()
    with _acquire_lock():
        conn = connect()
        init_db(conn)
        row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if row is None:
            conn.close()
            return (False, f"task {task_id} not found")
        task = dict(row)
        if task["status"] in TERMINAL_STATUSES:
            conn.close()
            return (False, f"task {task_id} is terminal: {task['status']}")
        payload = _payload_dict(task.get("payload"))
        if not payload.get("strategy_artifact_verified"):
            conn.close()
            return (False, "backtest cannot be recorded before the strategy artifact is verified")
        # Evidence comes from the ARTIFACT (Checkpoint-2 round-3): parse the capture
        # frontmatter for strategy_slug + backtest.look_ahead_check rather than
        # trusting the CLI claim.
        fm, perr = _parse_md_frontmatter(path)
        if fm is None:
            conn.close()
            return (False, f"backtest capture invalid: {perr}")
        capture_slug = str(fm.get("strategy_slug", "")).strip()
        bt_block = fm.get("backtest")
        capture_lac = ""
        if isinstance(bt_block, dict):
            capture_lac = str(bt_block.get("look_ahead_check", "")).strip().lower()
        # Bind the capture to the recorded strategy by its frontmatter strategy_slug
        # (Checkpoint-2 round-2/round-3): a capture for a different strategy must not
        # attach to this chain.
        strategy_path = payload.get("strategy_path")
        if strategy_path:
            strat_base = Path(str(strategy_path)).name
            if strat_base.startswith("strategy_") and strat_base.endswith(".md"):
                strat_slug = strat_base[len("strategy_"):-len(".md")]
                if capture_slug != strat_slug:
                    conn.close()
                    return (
                        False,
                        f"backtest capture strategy_slug {capture_slug!r} does not "
                        f"match the recorded strategy slug {strat_slug!r}",
                    )
        if capture_lac not in A1_BACKTEST_LOOK_AHEAD_VALUES:
            conn.close()
            return (
                False,
                "backtest capture look_ahead_check must be one of "
                f"{sorted(A1_BACKTEST_LOOK_AHEAD_VALUES)} (got {capture_lac!r})",
            )
        # If the caller supplied a verdict, it must MATCH the artifact (typo catch);
        # but the persisted value is always the artifact's, never the CLI claim.
        if look_ahead_check is not None and normalized_lac != capture_lac:
            conn.close()
            return (
                False,
                f"supplied look_ahead_check {normalized_lac!r} does not match the "
                f"backtest capture's verdict {capture_lac!r}",
            )
        payload["backtest_done"] = True
        payload["backtest_artifact_path"] = str(path)
        payload["backtest_artifact_sha256"] = _sha256_file(path)
        payload["backtest_look_ahead_check"] = capture_lac
        payload["backtest_done_at"] = now_iso()
        _update_payload_locked(conn, task_id, payload, status=task["status"])
        conn.commit()
        conn.close()
    return (True, "")


def a1_approve_strategy(task_id: str) -> tuple[bool, str]:
    """Record operator approval at the strategy gate (Stage 9) -- the END of A2.

    A2 ends parked here; A3 (Stage 11 ship-to-engine, the capital path) is the
    next increment. Guard: the backtest must be recorded first.
    """
    with _acquire_lock():
        conn = connect()
        init_db(conn)
        row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if row is None:
            conn.close()
            return (False, f"task {task_id} not found")
        task = dict(row)
        if task["status"] in TERMINAL_STATUSES:
            conn.close()
            return (False, f"task {task_id} is terminal: {task['status']}")
        payload = _payload_dict(task.get("payload"))
        ok, reason = _a1_backtest_payload_valid(payload)
        if not ok:
            conn.close()
            return (False, f"strategy cannot be approved: {reason}")
        payload["strategy_approved"] = True
        payload["strategy_approved_at"] = now_iso()
        _update_payload_locked(conn, task_id, payload, status=task["status"])
        conn.commit()
        conn.close()
    return (True, "")


def _a3_strategy_slug_from_path(path: Path) -> str | None:
    name = path.name
    if not (name.startswith("strategy_") and name.endswith(".md")):
        return None
    slug = name[len("strategy_"):-len(".md")]
    return slug or None


def _a3_git_repo_root_for_path(path: Path) -> Path | None:
    start = path if path.is_dir() else path.parent
    try:
        result = subprocess.run(
            ["git", "-C", str(start), "rev-parse", "--show-toplevel"],
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    root = (result.stdout or "").strip()
    return Path(root) if root else None


def _a3_git_head(repo: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "HEAD"],
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    head = (result.stdout or "").strip()
    return head or None


def _a4_git_head_commit_message(repo: Path) -> str | None:
    """The HEAD commit body, used to prove a limits commit was authored by THIS
    dispatch (CP2 r1 fix A). K2Bi's _build_limits_commit_message emits
    `Approval-Captured-At: <approved_at>` verbatim, so the dispatch's approved_at
    appears here iff the HEAD commit is the one this apply produced."""
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), "log", "-1", "--format=%B"],
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return result.stdout or ""


def _a4_git_head_commit_parent(repo: Path) -> str | None:
    """The first-parent sha of HEAD. A legitimate limits apply is a SINGLE commit on
    top of the dispatch baseline, so HEAD's parent must equal limits_repo_head_before
    (Codex CP2 final F2) -- this rejects an amended/squashed/unrelated HEAD that merely
    advanced past the baseline."""
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "--verify", "--quiet", "HEAD^"],
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    parent = (result.stdout or "").strip()
    return parent or None


def _a3_git_status_for_path(repo: Path, path: Path) -> str | None:
    try:
        rel = path.resolve(strict=False).relative_to(repo.resolve(strict=False)).as_posix()
    except ValueError:
        return None
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), "status", "--porcelain=v1", "--", rel],
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def _a3_git_path_tracked_at_head(repo: Path, path: Path) -> bool:
    try:
        rel = path.resolve(strict=False).relative_to(repo.resolve(strict=False)).as_posix()
    except ValueError:
        return False
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), "cat-file", "-e", f"HEAD:{rel}"],
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def _parse_md_frontmatter_bytes(data: bytes) -> tuple[dict | None, str]:
    text = data.decode("utf-8", errors="replace")
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return (None, "no opening frontmatter fence")
    close_idx = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            close_idx = i
            break
    if close_idx is None:
        return (None, "frontmatter not closed")
    try:
        import yaml
    except ImportError:
        return (None, "yaml unavailable -- cannot validate frontmatter")
    try:
        parsed = yaml.safe_load("\n".join(lines[1:close_idx]))
    except Exception:
        return (None, "frontmatter not parseable")
    if not isinstance(parsed, dict):
        return (None, "frontmatter is not a mapping")
    return (parsed, "")


def _a3_git_staged_blob_sha(repo: Path, rel_path: str) -> tuple[str | None, bytes | None]:
    result = subprocess.run(
        ["git", "-C", str(repo), "show", f":{rel_path}"],
        capture_output=True,
    )
    if result.returncode != 0:
        return (None, None)
    blob = result.stdout
    return (hashlib.sha256(blob).hexdigest(), blob)


def _a3_git_commit_only(repo: Path, rel_path: str, message: str) -> tuple[bool, str]:
    result = subprocess.run(
        ["git", "-C", str(repo), "commit", "--only", "-m", message, "--", rel_path],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return (False, (result.stderr or result.stdout).strip())
    return (True, (result.stdout or "").strip())


def _a3_compact_ship_inspect(inspect: dict) -> dict:
    return {
        key: inspect.get(key)
        for key in ("state", "head", "file_sha256", "strategy_status")
        if key in inspect
    }


def _a3_terminalize_attempt_limit_locked(
    conn,
    task_id: str,
    payload: dict,
    *,
    status: str = "needs_human",
) -> None:
    payload["terminal_reason"] = "ship_attempt_limit_exceeded"
    payload.setdefault("terminal_reason_at", now_iso())
    _update_payload_locked(
        conn,
        task_id,
        payload,
        status=status,
        blocker_reason=(
            "ship attempt limit exceeded; inspect rollback state and make a fresh "
            "operator decision before retrying"
        ),
    )


def _a4_limits_slug_from_path(path: Path) -> str | None:
    match = re.match(r"^\d{4}-\d{2}-\d{2}_limits-proposal_(.+)$", path.stem)
    slug = match.group(1) if match else path.stem
    return slug or None


def _a4_limits_token(
    slug: str,
    proposal_sha: str,
    config_sha: str,
    approved_at: str,
    lease: str,
) -> str:
    return f"APPROVE_LIMITS:{slug}:{proposal_sha}:{config_sha}:{approved_at}:{lease}"


def _a4_proposal_repo_and_paths(path: Path) -> tuple[Path | None, str | None, str]:
    repo = _a3_git_repo_root_for_path(path)
    if repo is None:
        return (None, None, f"limits proposal path is not inside a git repo: {path}")
    try:
        rel = path.resolve(strict=False).relative_to(repo.resolve(strict=False)).as_posix()
    except ValueError:
        return (None, None, f"limits proposal path is outside git repo: {path}")
    if not rel.startswith("review/strategy-approvals/"):
        return (None, None, f"limits proposal must be under review/strategy-approvals: {path}")
    if path.suffix != ".md" or "_limits-proposal_" not in path.stem:
        return (None, None, f"limits proposal filename must be *_limits-proposal_*.md: {path}")
    return (repo, rel, "")


def _a4_markdown_yaml_block_after_heading(text: str, heading: str) -> tuple[dict | None, str]:
    pattern = re.compile(
        rf"(?ims)^##\s+{re.escape(heading)}\s*$.*?^```yaml\s*$\n(.*?)^```\s*$"
    )
    match = pattern.search(text)
    if not match:
        return (None, f"missing ## {heading} yaml block")
    try:
        import yaml
    except ImportError:
        return (None, "yaml unavailable -- cannot parse limits proposal")
    try:
        data = yaml.safe_load(match.group(1))
    except Exception as exc:
        return (None, f"limits proposal ## {heading} block is not parseable YAML: {exc}")
    if not isinstance(data, dict):
        return (None, f"limits proposal ## {heading} block is not a mapping")
    return (data, "")


def _a4_limits_change_from_path(path: Path) -> tuple[dict | None, str]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return (None, f"limits proposal unreadable: {exc}")
    return _a4_markdown_yaml_block_after_heading(text, "Change")


def _a4_normalize_symbols(value) -> list[str] | None:
    if not isinstance(value, list):
        return None
    symbols = sorted({str(item).strip().upper() for item in value if str(item).strip()})
    return symbols


def _a4_config_symbols(config_path: Path) -> tuple[list[str] | None, str]:
    try:
        import yaml
    except ImportError:
        return (None, "yaml unavailable -- cannot read validator config")
    try:
        data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        return (None, f"validator config unreadable: {exc}")
    if not isinstance(data, dict):
        return (None, "validator config is not a mapping")
    block = data.get("instrument_whitelist")
    if not isinstance(block, dict):
        return (None, "validator config missing instrument_whitelist block")
    symbols = _a4_normalize_symbols(block.get("symbols"))
    if symbols is None:
        return (None, "instrument_whitelist.symbols is not a list")
    return (symbols, "")


def _a4_scope_change(path: Path) -> tuple[dict | None, list[str] | None, str]:
    change, reason = _a4_limits_change_from_path(path)
    if change is None:
        return (None, None, reason)
    rule = str(change.get("rule") or "").strip()
    change_type = str(change.get("change_type") or "").strip()
    if rule != "instrument_whitelist" or change_type != "add":
        return (
            None,
            None,
            "A4 supports only instrument_whitelist/add this increment; route others "
            "to the manual /invest-ship --approve-limits path",
        )
    expected_after = _a4_normalize_symbols(change.get("after"))
    if expected_after is None:
        return (None, None, "limits proposal ## Change after must be a symbol list")
    return (change, expected_after, "")


def _a4_validate_proposed_limits_file(path: Path) -> tuple[dict | None, list[str] | None, str]:
    if not path.is_file():
        return (None, None, f"limits proposal missing or not a file: {path}")
    try:
        if path.stat().st_size == 0:
            return (None, None, f"limits proposal is empty: {path}")
    except OSError as exc:
        return (None, None, f"limits proposal unreadable: {exc}")
    fm, perr = _parse_md_frontmatter(path)
    if fm is None:
        return (None, None, f"limits proposal frontmatter invalid: {perr}")
    if str(fm.get("type", "")).strip() != "limits-proposal":
        return (None, None, "limits proposal type must be limits-proposal")
    status = str(fm.get("status") or "").strip().lower()
    if status != "proposed":
        return (None, None, f"limits proposal status must be proposed; got {status!r}")
    applies_to = str(fm.get("applies-to") or "").strip()
    if applies_to != "execution/validators/config.yaml":
        return (None, None, "limits proposal applies-to must be execution/validators/config.yaml")
    change, expected_after, reason = _a4_scope_change(path)
    if change is None:
        return (None, None, reason)
    return (change, expected_after, "")


def _a4_terminalize_attempt_limit_locked(
    conn,
    task_id: str,
    payload: dict,
    *,
    status: str = "needs_human",
) -> None:
    payload["terminal_reason"] = "limits_apply_attempt_limit_exceeded"
    payload.setdefault("terminal_reason_at", now_iso())
    _update_payload_locked(
        conn,
        task_id,
        payload,
        status=status,
        blocker_reason=(
            "limits apply attempt limit exceeded; inspect rollback state and make "
            "a fresh operator decision before retrying"
        ),
    )


def a4_record_limits_proposal(task_id: str, proposal_path: str) -> tuple[bool, str]:
    path = Path(str(proposal_path)).expanduser()
    repo, _rel, reason = _a4_proposal_repo_and_paths(path)
    if repo is None:
        return (False, reason)
    slug = _a4_limits_slug_from_path(path)
    if slug is None:
        return (False, f"limits proposal filename has no slug: {path}")
    change, expected_after, reason = _a4_validate_proposed_limits_file(path)
    if change is None or expected_after is None:
        return (False, reason)
    try:
        proposal_sha = _sha256_file(path)
    except OSError as exc:
        return (False, f"limits proposal unreadable: {exc}")

    with _acquire_lock():
        conn = connect()
        init_db(conn)
        row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if row is None:
            conn.close()
            return (False, f"task {task_id} not found")
        task = dict(row)
        if task["status"] in TERMINAL_STATUSES:
            conn.close()
            return (False, f"task {task_id} is terminal: {task['status']}")
        payload = _payload_dict(task.get("payload"))
        if payload.get("chain_kind") != "limits":
            conn.close()
            return (False, "record-limits-proposal requires payload.chain_kind=limits")
        # Refuse to (re-)record over a partial_approved_uncommitted apply (Codex CP2
        # final F3). That state means the K2Bi adapter mutated the validator but did NOT
        # commit -- the repo is in a dangerous half-applied state that needs manual
        # recovery. Re-recording here would pop limits_partial_detected_at + the inspect
        # evidence below, silently erasing the durable "human recovery required" signal.
        # The next dispatch's clean-tree preflight would still block, but the recovery
        # state must not be hidden. Recover the repo first, then start a fresh flight.
        if payload.get("limits_partial_detected_at"):
            conn.close()
            return (
                False,
                "limits apply left a partial (approved-but-uncommitted) state; recover the "
                "K2Bi repo manually and start a fresh flight -- record-limits-proposal will "
                "not clear the partial recovery evidence",
            )
        payload["limits_proposal_recorded"] = True
        payload["limits_proposal_recorded_at"] = now_iso()
        payload["limits_proposal_path"] = str(path)
        payload["limits_proposal_sha256"] = proposal_sha
        payload["limits_proposal_slug"] = slug
        payload["limits_change_rule"] = str(change.get("rule") or "").strip()
        payload["limits_change_type"] = str(change.get("change_type") or "").strip()
        payload["limits_expected_after_symbols"] = expected_after
        for key in (
            "limits_authorized",
            "limits_authorized_at",
            "limits_dispatch_started_at",
            "limits_dispatch_proposal_sha256",
            "limits_dispatch_config_sha256",
            "limits_repo_head_before",
            "limits_apply_lease_id",
            "limits_approved_at",
            "limits_approval_token",
            "limits_verified",
            "limits_verified_at",
            "limits_commit_sha",
            "limits_failed_at",
            "limits_failure_reason",
            "limits_rolled_back_at",
            "limits_rollback_reason",
            "limits_rollback_clean",
            "limits_partial_detected_at",
            "limits_partial_reason",
            "limits_verify_failed_at",
            "limits_verify_failed_reason",
            "limits_inspect_state",
            "limits_inspect",
        ):
            payload.pop(key, None)
        _update_payload_locked(conn, task_id, payload, status=task["status"], blocker_reason=None)
        conn.commit()
        conn.close()
    return (True, "")


def _a4_proposal_sha_matches_record(payload: dict) -> tuple[bool, str]:
    """Re-read the recorded proposal file and confirm its sha still matches the
    sha captured at record-limits-proposal (CP2 r1 fix D: defense-in-depth +
    clearer early error than a late mark-dispatch refusal). The dual-sha token at
    dispatch is the real fail-closed guard; this just surfaces drift sooner."""
    raw_path = payload.get("limits_proposal_path")
    recorded_sha = str(payload.get("limits_proposal_sha256") or "").strip()
    if not raw_path or not recorded_sha:
        return (False, "proposal not recorded; re-run record-limits-proposal")
    path = Path(str(raw_path)).expanduser()
    if not path.is_file():
        return (False, "proposal changed since record; re-run record-limits-proposal")
    if _sha256_file(path) != recorded_sha:
        return (False, "proposal changed since record; re-run record-limits-proposal")
    return (True, "")


def a4_authorize_limits(task_id: str) -> tuple[bool, str]:
    with _acquire_lock():
        conn = connect()
        init_db(conn)
        row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if row is None:
            conn.close()
            return (False, f"task {task_id} not found")
        task = dict(row)
        if task["status"] in TERMINAL_STATUSES:
            conn.close()
            return (False, f"task {task_id} is terminal: {task['status']}")
        payload = _payload_dict(task.get("payload"))
        if payload.get("limits_authorized"):
            conn.close()
            return (True, "")
        action = a1_resume_action_locked(conn, task_id)
        if action != "await_limits_approval":
            conn.close()
            return (
                False,
                f"limits authorization requires resume action await_limits_approval (got {action!r})",
            )
        ok, reason = _a4_proposal_sha_matches_record(payload)
        if not ok:
            conn.close()
            return (False, reason)
        payload["limits_authorized"] = True
        payload["limits_authorized_at"] = now_iso()
        _update_payload_locked(conn, task_id, payload, status=task["status"], blocker_reason=None)
        conn.commit()
        conn.close()
    return (True, "")


def a4_mark_limits_dispatch_started(task_id: str) -> tuple[bool, dict | str]:
    with _acquire_lock():
        conn = connect()
        init_db(conn)
        row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if row is None:
            conn.close()
            return (False, f"task {task_id} not found")
        task = dict(row)
        if task["status"] in TERMINAL_STATUSES:
            conn.close()
            return (False, f"task {task_id} is terminal: {task['status']}")
        payload = _payload_dict(task.get("payload"))
        try:
            attempts = int(payload.get("limits_attempt_count") or 0)
        except (TypeError, ValueError):
            attempts = 0
        if attempts >= A4_MAX_APPLY_ATTEMPTS:
            _a4_terminalize_attempt_limit_locked(conn, task_id, payload)
            conn.commit()
            conn.close()
            return (False, f"limits apply attempt limit exceeded ({A4_MAX_APPLY_ATTEMPTS})")
        action = a1_resume_action_locked(conn, task_id)
        if action != "dispatch_limits":
            conn.close()
            return (False, f"limits dispatch requires resume action dispatch_limits (got {action!r})")
        raw_path = payload.get("limits_proposal_path")
        if not raw_path:
            conn.close()
            return (False, "limits_proposal_path missing")
        path = Path(str(raw_path)).expanduser()
        if not path.is_file():
            conn.close()
            return (False, f"limits proposal missing or not a file: {path}")
        slug = _a4_limits_slug_from_path(path)
        if slug is None:
            conn.close()
            return (False, f"limits proposal filename has no slug: {path}")
        current_proposal_sha = _sha256_file(path)
        recorded_sha = str(payload.get("limits_proposal_sha256") or "").strip()
        if not recorded_sha:
            conn.close()
            return (False, "limits_proposal_sha256 missing; cannot dispatch limits")
        if current_proposal_sha != recorded_sha:
            conn.close()
            return (
                False,
                f"limits proposal sha256 {current_proposal_sha} changed since record {recorded_sha}",
            )
        repo = _a3_git_repo_root_for_path(path)
        if repo is None:
            conn.close()
            return (False, "cannot establish limits baseline: K2Bi repo git HEAD unavailable")
        head = _a3_git_head(repo)
        if not head:
            conn.close()
            return (False, "cannot establish limits baseline: K2Bi repo git HEAD unavailable")
        config_path = repo / "execution" / "validators" / "config.yaml"
        if not config_path.is_file() or config_path.stat().st_size == 0:
            conn.close()
            return (False, "K2Bi validators config missing or empty; cannot dispatch limits")
        # Run the cheap read-only capital gates BEFORE minting the token / burning an
        # attempt (Codex CP2 final F1). The child preflight re-checks these, but minting
        # a capital token + consuming one of the 3 attempts over a kill-switched or dirty
        # tree -- only to have the child refuse -- wastes the bound and leaves stale token
        # state. The proposal/config integrity is already covered by the sha match above;
        # kill-switch + clean-tree are the states that can change between authorize and
        # dispatch. (The token-bind itself cannot run here -- it is what we mint.)
        from scripts.lib import orchestrator_profiles as profiles

        if (Path(profiles.k2bi_vault()).expanduser() / "System" / ".killed").exists():
            conn.close()
            return (False, "engine kill-switch engaged (System/.killed present); limits dispatch refused")
        ok, reason = profiles._git_tree_clean_for_limits_apply(repo, path, config_path)
        if not ok:
            conn.close()
            return (False, reason)
        current_config_sha = _sha256_file(config_path)
        dt = datetime.now(timezone.utc)
        approved_at = dt.isoformat()
        apply_lease_id = f"{slug}-limits-a1-{dt.strftime('%Y%m%dT%H%M%SZ')}"
        token = _a4_limits_token(
            slug,
            current_proposal_sha,
            current_config_sha,
            approved_at,
            apply_lease_id,
        )
        payload["limits_attempt_count"] = attempts + 1
        payload["limits_dispatch_started_at"] = approved_at
        payload["limits_dispatch_proposal_sha256"] = current_proposal_sha
        payload["limits_dispatch_config_sha256"] = current_config_sha
        payload["limits_repo_head_before"] = head
        payload["limits_apply_lease_id"] = apply_lease_id
        payload["limits_approved_at"] = approved_at
        payload["limits_approval_token"] = token
        for key in (
            "limits_failed_at",
            "limits_failure_reason",
            "limits_rolled_back_at",
            "limits_rollback_reason",
            "limits_rollback_clean",
            "limits_partial_detected_at",
            "limits_partial_reason",
            "limits_verify_failed_at",
            "limits_verify_failed_reason",
            "limits_inspect_state",
            "limits_inspect",
        ):
            payload.pop(key, None)
        _update_payload_locked(conn, task_id, payload, status=task["status"], blocker_reason=None)
        conn.commit()
        conn.close()
    return (
        True,
        {
            "lease_id": apply_lease_id,
            "apply_lease_id": apply_lease_id,
            "proposal_sha": current_proposal_sha,
            "config_sha": current_config_sha,
            "approved_at": approved_at,
            "approval_token": token,
            "proposal_path": str(path),
            "config_path": str(config_path),
        },
    )


def _a4_compact_limits_inspect(inspect: dict) -> dict:
    return {
        key: inspect.get(key)
        for key in (
            "state",
            "head",
            "proposal_sha256",
            "config_sha256",
            "proposal_status",
            "config_symbols",
        )
        if key in inspect
    }


def _a4_inspect_limits_state_from_payload(task_id: str, payload: dict) -> dict:
    raw_path = payload.get("limits_proposal_path")
    if not raw_path:
        return {"state": "unknown", "reason": "limits_proposal_path missing"}
    path = Path(str(raw_path)).expanduser()
    slug = _a4_limits_slug_from_path(path)
    if slug is None:
        return {
            "state": "unknown",
            "reason": f"limits proposal filename must be *_limits-proposal_*.md: {path}",
            "proposal_path": str(path),
        }
    repo = _a3_git_repo_root_for_path(path)
    if repo is None:
        return {
            "state": "unknown",
            "reason": f"limits proposal path is not inside a git repo: {path}",
            "proposal_path": str(path),
        }
    config_path = repo / "execution" / "validators" / "config.yaml"
    marker = repo / ".k2bi-orchestrator" / "rollback" / f"limits_{slug}.json"
    head = _a3_git_head(repo)
    proposal_status_text = _a3_git_status_for_path(repo, path)
    config_status_text = _a3_git_status_for_path(repo, config_path)
    proposal_tracked_at_head = _a3_git_path_tracked_at_head(repo, path)
    config_tracked_at_head = _a3_git_path_tracked_at_head(repo, config_path)
    result = {
        "state": "unknown",
        "proposal_path": str(path),
        "config_path": str(config_path),
        "repo_root": str(repo),
        "slug": slug,
        "head": head,
        "proposal_git_status": proposal_status_text,
        "config_git_status": config_status_text,
        "proposal_tracked_at_head": proposal_tracked_at_head,
        "config_tracked_at_head": config_tracked_at_head,
        "rollback_marker_path": str(marker),
        "rollback_marker_exists": marker.exists(),
    }
    if marker.exists():
        result["state"] = "incomplete_rollback_marker"
        return result
    if not path.is_file():
        result["reason"] = "limits proposal file missing"
        return result
    if not config_path.is_file():
        result["reason"] = "validator config missing"
        return result
    try:
        result["proposal_sha256"] = _sha256_file(path)
        result["config_sha256"] = _sha256_file(config_path)
    except OSError as exc:
        result["reason"] = f"limits target unreadable: {exc}"
        return result
    fm, perr = _parse_md_frontmatter(path)
    if fm is None:
        result["reason"] = f"limits proposal frontmatter invalid: {perr}"
        return result
    proposal_status = str(fm.get("status") or "").strip().lower()
    result["proposal_status"] = proposal_status
    change, expected_after, reason = _a4_scope_change(path)
    if change is None or expected_after is None:
        result["reason"] = reason
        if proposal_status == "approved":
            result["state"] = "partial_approved_uncommitted"
        return result
    result["expected_after_symbols"] = expected_after
    config_symbols, reason = _a4_config_symbols(config_path)
    if config_symbols is None:
        result["reason"] = reason
        if proposal_status == "approved":
            result["state"] = "partial_approved_uncommitted"
        return result
    result["config_symbols"] = config_symbols

    if proposal_status == "approved":
        head_before = str(payload.get("limits_repo_head_before") or "").strip()
        head_advanced = bool(head and head_before and head != head_before)
        result["head_advanced"] = head_advanced
        # Compare the committed whitelist to the RECORDED operator-approved snapshot
        # (Codex CP2 final F2), NOT to the re-parsed HEAD proposal's after-list. The
        # HEAD proposal is mutable: a hand/amended commit could change BOTH the
        # proposal's `## Change.after` AND config.yaml together (plus the trailer) and
        # otherwise pass -- but the committed whitelist must equal exactly what Keith
        # approved at record time, regardless of any later proposal tampering.
        recorded_after = _a4_normalize_symbols(payload.get("limits_expected_after_symbols"))
        exact_symbols = recorded_after is not None and config_symbols == recorded_after
        result["config_exact_expected_after"] = exact_symbols
        # Bind the commit to the dispatch baseline: a legitimate apply is ONE commit
        # whose parent is limits_repo_head_before (Codex CP2 final F2). This rejects an
        # amended/unrelated HEAD that merely advanced past the baseline.
        commit_parent = _a4_git_head_commit_parent(repo)
        parent_ok = bool(head_before and commit_parent and commit_parent == head_before)
        result["commit_parent_ok"] = parent_ok
        targets_clean = proposal_status_text == "" and config_status_text == ""
        # Provenance (CP2 r1 fix A): prove the HEAD commit was authored by THIS
        # dispatch, not a coincidental/amended/hand-made commit that happens to
        # leave the right tree. K2Bi's commit message carries this dispatch's
        # approved_at verbatim ("Approval-Captured-At: <approved_at>").
        dispatch_approved_at = str(payload.get("limits_approved_at") or "").strip()
        head_commit_message = _a4_git_head_commit_message(repo)
        # Anchor to the exact K2Bi trailer line "Approval-Captured-At: <approved_at>"
        # (CP2 r2 fix A), not a bare timestamp substring -- the raw approved_at also
        # appears in the proposal's own `approved_at:` frontmatter, so an unanchored
        # `in` match could be satisfied by a coincidental occurrence.
        expected_trailer = f"Approval-Captured-At: {dispatch_approved_at}"
        provenance_ok = bool(
            dispatch_approved_at
            and head_commit_message is not None
            and expected_trailer in head_commit_message
        )
        result["commit_provenance_ok"] = provenance_ok
        if (
            proposal_tracked_at_head
            and config_tracked_at_head
            and head_advanced
            and parent_ok
            and exact_symbols
            and targets_clean
            and provenance_ok
        ):
            result["state"] = "committed"
        else:
            result["state"] = "partial_approved_uncommitted"
            if not exact_symbols:
                result["reason"] = (
                    "config instrument_whitelist.symbols is not the recorded operator-approved after list"
                )
            elif not parent_ok:
                result["reason"] = (
                    "HEAD commit parent is not the recorded dispatch baseline (limits_repo_head_before)"
                )
            elif not proposal_tracked_at_head:
                result["reason"] = "approved limits proposal is not tracked at HEAD"
            elif not config_tracked_at_head:
                result["reason"] = "validator config is not tracked at HEAD"
            elif not head_advanced:
                result["reason"] = "approved limits did not land in a new HEAD commit"
            elif not targets_clean:
                result["reason"] = "limits proposal or validator config has dirty target lines"
            elif not provenance_ok:
                result["reason"] = (
                    "HEAD commit does not carry this dispatch's approval timestamp (provenance)"
                )
        return result

    if proposal_status == "proposed":
        recorded_sha = str(payload.get("limits_proposal_sha256") or "").strip()
        dispatch_config_sha = str(payload.get("limits_dispatch_config_sha256") or "").strip()
        config_clean = not dispatch_config_sha or dispatch_config_sha == result["config_sha256"]
        proposal_clean = not recorded_sha or recorded_sha == result["proposal_sha256"]
        if proposal_clean and config_clean:
            result["state"] = "clean_rollback"
        else:
            result["reason"] = "proposed limits proposal or config sha differs from recorded baseline"
        return result
    result["reason"] = f"unexpected limits proposal status {proposal_status!r}"
    return result


def a4_inspect_limits_state(task_id: str) -> dict:
    task = get_task(task_id)
    if task is None:
        return {"state": "unknown", "reason": f"task {task_id} not found"}
    payload = _payload_dict(task.get("payload"))
    return _a4_inspect_limits_state_from_payload(task_id, payload)


def a4_verify_limits(task_id: str) -> tuple[bool, str]:
    with _acquire_lock():
        conn = connect()
        init_db(conn)
        row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if row is None:
            conn.close()
            return (False, f"task {task_id} not found")
        task = dict(row)
        if task["status"] in TERMINAL_STATUSES:
            conn.close()
            return (False, f"task {task_id} is terminal: {task['status']}")
        payload = _payload_dict(task.get("payload"))
        inspect = _a4_inspect_limits_state_from_payload(task_id, payload)
        state = inspect.get("state")
        payload["limits_inspect_state"] = state
        payload["limits_inspect"] = _a4_compact_limits_inspect(inspect)
        if state == "committed":
            payload["limits_verified"] = True
            payload["limits_verified_at"] = now_iso()
            payload["limits_commit_sha"] = inspect.get("head")
            _update_payload_locked(conn, task_id, payload, status="terminal_limits_applied")
            conn.commit()
            conn.close()
            return (True, "")
        reason = f"limits verification refused: inspect state {state!r}"
        payload["limits_verify_failed_at"] = now_iso()
        payload["limits_verify_failed_reason"] = reason
        # Self-heal a stale/corrupted limits_verified flag on a non-committed row
        # (CP2 r2 fix C/F5): only a confirmed commit may carry it. The oracle already
        # ignores it, but clearing it keeps external readers/dashboards honest.
        payload.pop("limits_verified", None)
        payload.pop("limits_verified_at", None)
        if state == "partial_approved_uncommitted":
            payload["limits_partial_detected_at"] = now_iso()
            payload["limits_partial_reason"] = reason
            _update_payload_locked(
                conn,
                task_id,
                payload,
                status="needs_human",
                blocker_reason=reason,
            )
        else:
            _update_payload_locked(conn, task_id, payload, status=task["status"])
        conn.commit()
        conn.close()
    return (False, reason)


def a4_record_limits_failed(task_id: str, *, reason: str) -> tuple[bool, str]:
    with _acquire_lock():
        conn = connect()
        init_db(conn)
        row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if row is None:
            conn.close()
            return (False, f"task {task_id} not found")
        task = dict(row)
        if task["status"] in TERMINAL_STATUSES:
            conn.close()
            return (False, f"task {task_id} is terminal: {task['status']}")
        payload = _payload_dict(task.get("payload"))
        inspect = _a4_inspect_limits_state_from_payload(task_id, payload)
        state = inspect.get("state")
        n = now_iso()
        if not payload.get("limits_failed_at"):
            payload["limits_failed_at"] = n
        if not payload.get("limits_failure_reason"):
            payload["limits_failure_reason"] = reason
        payload["limits_inspect_state"] = state
        payload["limits_inspect"] = inspect
        payload["limits_rollback_clean"] = state == "clean_rollback"
        blocker_reason = reason
        if state == "clean_rollback":
            payload["limits_rolled_back_at"] = n
            payload["limits_rollback_reason"] = reason
        elif state == "partial_approved_uncommitted":
            payload["limits_partial_detected_at"] = n
            payload["limits_partial_reason"] = reason
            blocker_reason = f"{reason}; partial_approved_uncommitted"
        _update_payload_locked(
            conn,
            task_id,
            payload,
            status="needs_human",
            blocker_reason=blocker_reason,
        )
        conn.commit()
        conn.close()
    return (True, "")


def a4_retry_limits_after_rollback(task_id: str) -> tuple[bool, str]:
    with _acquire_lock():
        conn = connect()
        init_db(conn)
        row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if row is None:
            conn.close()
            return (False, f"task {task_id} not found")
        task = dict(row)
        if task["status"] in TERMINAL_STATUSES:
            conn.close()
            return (False, f"task {task_id} is terminal: {task['status']}")
        payload = _payload_dict(task.get("payload"))
        inspect = _a4_inspect_limits_state_from_payload(task_id, payload)
        if inspect.get("state") != "clean_rollback":
            conn.close()
            return (
                False,
                f"retry requires live inspect state clean_rollback (got {inspect.get('state')!r})",
            )
        try:
            attempts = int(payload.get("limits_attempt_count") or 0)
        except (TypeError, ValueError):
            attempts = 0
        if attempts >= A4_MAX_APPLY_ATTEMPTS:
            _a4_terminalize_attempt_limit_locked(conn, task_id, payload)
            conn.commit()
            conn.close()
            return (False, f"limits apply attempt limit exceeded ({A4_MAX_APPLY_ATTEMPTS})")
        if not payload.get("limits_rolled_back_at"):
            conn.close()
            return (False, "retry requires a recorded limits_rolled_back_at")
        ok, reason = _a4_proposal_sha_matches_record(payload)
        if not ok:
            conn.close()
            return (False, reason)
        # `limits_authorized` is INTENTIONALLY preserved (mirrors A3 retry-ship):
        # retry only fires on a confirmed clean_rollback of the EXACT proposal Keith
        # approved (its sha is re-checked above), and attempts are bounded at 3, so a
        # bounded retry re-applies the same human-approved, sha-bound change without
        # re-authorization friction. Only the dispatch/failure/recovery markers below
        # are cleared so the resume oracle re-enters at dispatch_limits.
        for key in (
            "limits_dispatch_started_at",
            "limits_dispatch_proposal_sha256",
            "limits_dispatch_config_sha256",
            "limits_repo_head_before",
            "limits_apply_lease_id",
            "limits_approved_at",
            "limits_approval_token",
            "limits_failed_at",
            "limits_failure_reason",
            "limits_rolled_back_at",
            "limits_rollback_reason",
            "limits_rollback_clean",
            "limits_partial_detected_at",
            "limits_partial_reason",
            "limits_verify_failed_at",
            "limits_verify_failed_reason",
            "limits_inspect_state",
            "limits_inspect",
        ):
            payload.pop(key, None)
        payload["limits_retry_ready_at"] = now_iso()
        _update_payload_locked(conn, task_id, payload, status="needs_human", blocker_reason=None)
        conn.commit()
        conn.close()
    return (True, "")


_SHA40_RE = re.compile(r"^[0-9a-f]{40}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_A5_DEPLOY_TOKEN_RE = re.compile(
    r"^APPROVE_DEPLOY:"
    r"(?P<target>[0-9a-f]{40}):"
    r"(?P<remote_baseline>[0-9a-f]{40}):"
    r"(?P<manifest>[0-9a-f]{64}):"
    r"(?P<approved_at>.+):"
    r"(?P<lease>[^:]+)$"
)


def _a5_deploy_token(
    target_sha: str,
    remote_baseline_sha: str,
    manifest_sha: str,
    approved_at: str,
    lease: str,
) -> str:
    return (
        f"APPROVE_DEPLOY:{target_sha}:{remote_baseline_sha}:"
        f"{manifest_sha}:{approved_at}:{lease}"
    )


def _a5_deploy_token_matches_payload(payload: dict) -> tuple[bool, str]:
    token = str(payload.get("deploy_approval_token") or "").strip()
    match = _A5_DEPLOY_TOKEN_RE.match(token)
    if match is None:
        return (False, "deploy approval_token malformed")
    target_sha = str(payload.get("deploy_target_sha") or "").strip().lower()
    remote_baseline_sha = str(payload.get("deploy_remote_baseline_sha") or "").strip().lower()
    manifest_sha = str(payload.get("deploy_preview_manifest_sha256") or "").strip().lower()
    approved_at = str(payload.get("deploy_approved_at") or "").strip()
    lease_id = str(payload.get("deploy_lease_id") or "").strip()
    if not target_sha:
        return (False, "deploy_target_sha missing from deploy approval token payload")
    if not remote_baseline_sha:
        return (False, "deploy_remote_baseline_sha missing from deploy approval token payload")
    if not manifest_sha:
        return (False, "deploy_preview_manifest_sha256 missing from deploy approval token payload")
    if not approved_at:
        return (False, "deploy_approved_at missing from deploy approval token payload")
    if not lease_id:
        return (False, "deploy_lease_id missing from deploy approval token payload")
    if match.group("target").lower() != target_sha:
        return (False, "deploy_target_sha does not match deploy approval token")
    if match.group("remote_baseline").lower() != remote_baseline_sha:
        return (False, "deploy_remote_baseline_sha does not match deploy approval token")
    if match.group("manifest").lower() != manifest_sha:
        return (False, "deploy_preview_manifest_sha256 does not match deploy approval token")
    if match.group("approved_at") != approved_at:
        return (False, "deploy_approved_at does not match deploy approval token")
    if match.group("lease") != lease_id:
        return (False, "deploy_lease_id does not match deploy approval token")
    return (True, "")


def _a5_load_manifest_with_sha(path: Path) -> tuple[dict | None, str | None, str]:
    text, reason = _a5_safe_read_text(path, "deploy preview manifest")
    if text is None:
        return (None, None, reason)
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        return (None, None, f"deploy preview manifest unreadable: {exc}")
    if not isinstance(data, dict):
        return (None, None, "deploy preview manifest must be a JSON object")
    manifest_sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return (data, manifest_sha, "")


def _a5_load_manifest(path: Path) -> tuple[dict | None, str]:
    data, _manifest_sha, reason = _a5_load_manifest_with_sha(path)
    return (data, reason)


def _a5_validate_manifest(
    manifest: dict,
    *,
    target_sha: str,
    expected_remote_baseline_sha: str | None = None,
) -> tuple[bool, str]:
    manifest_target = str(manifest.get("target_sha") or "").strip().lower()
    if not _SHA40_RE.match(target_sha):
        return (False, "deploy_target_sha must be a 40-character git sha")
    if manifest_target != target_sha:
        return (
            False,
            f"deploy preview target_sha {manifest_target!r} does not match "
            f"task deploy_target_sha {target_sha!r}",
        )
    remote = str(manifest.get("remote_baseline_sha") or "").strip().lower()
    if not _SHA40_RE.match(remote):
        return (False, "deploy preview remote_baseline_sha must be a 40-character git sha")
    if expected_remote_baseline_sha and remote != expected_remote_baseline_sha:
        return (
            False,
            f"deploy preview remote_baseline_sha {remote!r} changed from recorded "
            f"{expected_remote_baseline_sha!r}",
        )
    categories = manifest.get("categories")
    if not isinstance(categories, list) or not all(
        isinstance(item, str) and item.strip() for item in categories
    ):
        return (False, "deploy preview categories must be a non-empty string list")
    restart_services = manifest.get("restart_services")
    if restart_services is None:
        restart_services = []
    if not isinstance(restart_services, list) or not all(
        isinstance(item, str) and item.strip() for item in restart_services
    ):
        return (False, "deploy preview restart_services must be a string list")
    return (True, "")


def _a5_manifest_sha_matches_record(payload: dict) -> tuple[bool, str]:
    raw_path = payload.get("deploy_preview_manifest_path")
    recorded_sha = str(payload.get("deploy_preview_manifest_sha256") or "").strip().lower()
    if not raw_path or not recorded_sha:
        return (False, "deploy preview not recorded; re-run record-deploy-preview")
    path = Path(str(raw_path)).expanduser()
    if _a5_path_has_symlink_component(path):
        return (False, "deploy preview manifest must not be a symlink; re-run preview")
    if not path.is_file():
        return (False, "deploy preview manifest changed since record; re-run preview")
    recorded_realpath = str(payload.get("deploy_preview_manifest_realpath") or "").strip()
    if recorded_realpath and str(path.resolve(strict=False)) != recorded_realpath:
        return (False, "deploy preview manifest realpath changed since record; re-run preview")
    manifest, current_sha, reason = _a5_load_manifest_with_sha(path)
    if manifest is None or current_sha is None:
        return (False, reason)
    if current_sha != recorded_sha:
        return (False, "deploy preview manifest changed since record; re-run preview")
    target_sha = str(payload.get("deploy_target_sha") or "").strip().lower()
    remote_sha = str(payload.get("deploy_remote_baseline_sha") or "").strip().lower()
    ok, reason = _a5_validate_manifest(
        manifest,
        target_sha=target_sha,
        expected_remote_baseline_sha=remote_sha,
    )
    return (ok, reason)


def _a5_current_k2bi_head() -> tuple[str | None, str]:
    try:
        from scripts.lib import orchestrator_profiles as profiles
    except Exception as exc:
        return (None, f"K2Bi profile unavailable for deploy resume: {exc}")
    repo = Path(profiles.k2bi_repo()).expanduser()
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as exc:
        return (None, f"K2Bi repo HEAD unavailable for deploy resume: {exc}")
    head = result.stdout.strip().lower()
    if result.returncode != 0 or not _SHA40_RE.match(head):
        return (None, "K2Bi repo HEAD unavailable for deploy resume")
    return (head, "")


def _a5_terminalize_attempt_limit_locked(
    conn,
    task_id: str,
    payload: dict,
    *,
    status: str = "needs_human",
) -> None:
    payload["terminal_reason"] = "deploy_attempt_limit_exceeded"
    payload.setdefault("terminal_reason_at", now_iso())
    _update_payload_locked(
        conn,
        task_id,
        payload,
        status=status,
        blocker_reason=(
            "deploy attempt limit exceeded; inspect the pending deploy/remote "
            "state and make a fresh operator decision before retrying"
        ),
    )


def a5_record_deploy_preview(task_id: str, manifest_path: str) -> tuple[bool, str]:
    path = Path(str(manifest_path)).expanduser()
    manifest, manifest_sha, reason = _a5_load_manifest_with_sha(path)
    if manifest is None or manifest_sha is None:
        return (False, reason)
    with _acquire_lock():
        conn = connect()
        init_db(conn)
        row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if row is None:
            conn.close()
            return (False, f"task {task_id} not found")
        task = dict(row)
        if task["status"] in TERMINAL_STATUSES:
            conn.close()
            return (False, f"task {task_id} is terminal: {task['status']}")
        payload = _payload_dict(task.get("payload"))
        if payload.get("chain_kind") != "deploy":
            conn.close()
            return (False, "record-deploy-preview requires payload.chain_kind=deploy")
        if payload.get("deploy_authorized"):
            conn.close()
            return (
                False,
                "deploy already authorized; cancel or clear before re-recording preview",
            )
        source_status = str(payload.get("deploy_source_status") or "").strip()
        if source_status not in {"terminal_shipped", "terminal_limits_applied"}:
            conn.close()
            return (
                False,
                "deploy_source_status must be terminal_shipped or terminal_limits_applied",
            )
        target_sha = str(payload.get("deploy_target_sha") or "").strip().lower()
        ok, reason = _a5_validate_manifest(manifest, target_sha=target_sha)
        if not ok:
            conn.close()
            return (False, reason)
        payload["deploy_preview_recorded"] = True
        payload["deploy_preview_recorded_at"] = now_iso()
        payload["deploy_preview_manifest_path"] = str(path)
        payload["deploy_preview_manifest_realpath"] = str(path.resolve(strict=False))
        payload["deploy_preview_manifest_sha256"] = manifest_sha
        payload["deploy_remote_baseline_sha"] = str(manifest["remote_baseline_sha"]).strip().lower()
        payload["deploy_categories"] = [
            str(item).strip() for item in manifest.get("categories", []) if str(item).strip()
        ]
        payload["deploy_restart_services"] = [
            str(item).strip()
            for item in manifest.get("restart_services", [])
            if str(item).strip()
        ]
        payload["deploy_live_effect"] = str(manifest.get("live_effect") or "").strip()
        for key in (
            "deploy_deferred_at",
            "deploy_defer_reason",
            "deploy_pending_marker_path",
            "deploy_authorized",
            "deploy_authorized_at",
            "deploy_lease_id",
            "deploy_approved_at",
            "deploy_approval_token",
            "deploy_dispatch_started_at",
            "deploy_dispatch_nonce",
            "deploy_verified",
            "deploy_verified_at",
            "deploy_deployed_sha",
            "deploy_failed_at",
            "deploy_failure_reason",
            "deploy_partial_detected_at",
            "deploy_partial_reason",
            "deploy_verify_failed_at",
            "deploy_verify_failed_reason",
            "deploy_inspect_state",
            "deploy_inspect",
            "deploy_verification_scope",
        ):
            payload.pop(key, None)
        _update_payload_locked(conn, task_id, payload, status=task["status"], blocker_reason=None)
        conn.commit()
        conn.close()
    return (True, "")


def a5_authorize_deploy(task_id: str) -> tuple[bool, str]:
    with _acquire_lock():
        conn = connect()
        init_db(conn)
        row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if row is None:
            conn.close()
            return (False, f"task {task_id} not found")
        task = dict(row)
        if task["status"] in TERMINAL_STATUSES:
            conn.close()
            return (False, f"task {task_id} is terminal: {task['status']}")
        payload = _payload_dict(task.get("payload"))
        if payload.get("deploy_authorized"):
            conn.close()
            return (True, "")
        action = a1_resume_action_locked(conn, task_id)
        if action != "await_deploy_approval":
            conn.close()
            return (
                False,
                "deploy authorization requires resume action await_deploy_approval "
                f"(got {action!r})",
            )
        ok, reason = _a5_manifest_sha_matches_record(payload)
        if not ok:
            conn.close()
            return (False, reason)
        target_sha = str(payload.get("deploy_target_sha") or "").strip().lower()
        remote_sha = str(payload.get("deploy_remote_baseline_sha") or "").strip().lower()
        manifest_sha = str(payload.get("deploy_preview_manifest_sha256") or "").strip().lower()
        entity = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(task.get("entity_key") or "deploy")).strip("-")
        entity = entity.lower() or "deploy"
        dt = datetime.now(timezone.utc)
        approved_at = dt.isoformat()
        lease_id = f"{entity}-deploy-a1-{dt.strftime('%Y%m%dT%H%M%SZ')}"
        token = _a5_deploy_token(target_sha, remote_sha, manifest_sha, approved_at, lease_id)
        payload["deploy_authorized"] = True
        payload["deploy_authorized_at"] = approved_at
        payload["deploy_lease_id"] = lease_id
        payload["deploy_approved_at"] = approved_at
        payload["deploy_approval_token"] = token
        for key in ("deploy_deferred_at", "deploy_defer_reason"):
            payload.pop(key, None)
        _update_payload_locked(conn, task_id, payload, status=task["status"], blocker_reason=None)
        conn.commit()
        conn.close()
    return (True, "")


def a5_mark_deploy_dispatch_started(task_id: str) -> tuple[bool, dict | str]:
    with _acquire_lock():
        conn = connect()
        init_db(conn)
        row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if row is None:
            conn.close()
            return (False, f"task {task_id} not found")
        task = dict(row)
        if task["status"] in TERMINAL_STATUSES:
            conn.close()
            return (False, f"task {task_id} is terminal: {task['status']}")
        payload = _payload_dict(task.get("payload"))
        try:
            attempts = int(payload.get("deploy_attempt_count") or 0)
        except (TypeError, ValueError):
            attempts = 0
        if attempts >= A5_MAX_DEPLOY_ATTEMPTS:
            _a5_terminalize_attempt_limit_locked(conn, task_id, payload)
            conn.commit()
            conn.close()
            return (False, f"deploy attempt limit exceeded ({A5_MAX_DEPLOY_ATTEMPTS})")
        action = a1_resume_action_locked(conn, task_id)
        if action != "dispatch_deploy":
            conn.close()
            return (False, f"deploy dispatch requires resume action dispatch_deploy (got {action!r})")
        ok, reason = _a5_manifest_sha_matches_record(payload)
        if not ok:
            conn.close()
            return (False, reason)
        token = str(payload.get("deploy_approval_token") or "").strip()
        target_sha = str(payload.get("deploy_target_sha") or "").strip().lower()
        remote_sha = str(payload.get("deploy_remote_baseline_sha") or "").strip().lower()
        manifest_sha = str(payload.get("deploy_preview_manifest_sha256") or "").strip().lower()
        approved_at = str(payload.get("deploy_approved_at") or "").strip()
        lease_id = str(payload.get("deploy_lease_id") or "").strip()
        expected = _a5_deploy_token(target_sha, remote_sha, manifest_sha, approved_at, lease_id)
        if token != expected:
            conn.close()
            return (False, "deploy approval token does not bind current preview manifest")
        payload["deploy_attempt_count"] = attempts + 1
        dispatch_started_at = now_iso()
        dispatch_nonce = secrets.token_hex(16)
        payload["deploy_dispatch_started_at"] = dispatch_started_at
        payload["deploy_dispatch_nonce"] = dispatch_nonce
        for key in (
            "deploy_failed_at",
            "deploy_failure_reason",
            "deploy_partial_detected_at",
            "deploy_partial_reason",
            "deploy_verify_failed_at",
            "deploy_verify_failed_reason",
            "deploy_inspect_state",
            "deploy_inspect",
            "deploy_verification_scope",
        ):
            payload.pop(key, None)
        _update_payload_locked(conn, task_id, payload, status=task["status"], blocker_reason=None)
        conn.commit()
        conn.close()
    return (
        True,
        {
            "lease_id": lease_id,
            "deploy_lease_id": lease_id,
            "target_sha": target_sha,
            "remote_baseline_sha": remote_sha,
            "manifest_sha256": manifest_sha,
            "manifest_realpath": payload.get("deploy_preview_manifest_realpath"),
            "approved_at": approved_at,
            "approval_token": token,
            "dispatch_nonce": dispatch_nonce,
            "manifest_path": payload.get("deploy_preview_manifest_path"),
            "categories": payload.get("deploy_categories") or [],
            "restart_services": payload.get("deploy_restart_services") or [],
        },
    )


def _a5_pending_deploy_path(task_id: str, payload: dict) -> Path:
    target_sha = str(payload.get("deploy_target_sha") or "unknown").strip().lower()
    source_parent = str(payload.get("deploy_source_parent") or "unknown").strip()
    safe_source = re.sub(r"[^A-Za-z0-9_.-]+", "-", source_parent).strip("-") or "unknown"
    safe_task = re.sub(r"[^A-Za-z0-9_.-]+", "-", task_id).strip("-") or "unknown"
    return (
        Path(K2B_VAULT)
        / "System"
        / "orchestrator"
        / "pending-deploy"
        / f"{target_sha}-{safe_source}-{safe_task}.json"
    )


def a5_defer_deploy(task_id: str, *, reason: str = "operator_deferred") -> tuple[bool, str]:
    with _acquire_lock():
        conn = connect()
        init_db(conn)
        row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if row is None:
            conn.close()
            return (False, f"task {task_id} not found")
        task = dict(row)
        if task["status"] in TERMINAL_STATUSES:
            conn.close()
            return (False, f"task {task_id} is terminal: {task['status']}")
        payload = _payload_dict(task.get("payload"))
        action = a1_resume_action_locked(conn, task_id)
        if action != "await_deploy_approval":
            conn.close()
            return (
                False,
                f"deploy defer requires resume action await_deploy_approval (got {action!r})",
            )
        marker_path = _a5_pending_deploy_path(task_id, payload)
        marker = {
            "type": "k2bi-pending-deploy",
            "created_at": now_iso(),
            "source_parent": payload.get("deploy_source_parent"),
            "source_status": payload.get("deploy_source_status"),
            "target_sha": payload.get("deploy_target_sha"),
            "remote_baseline_sha": payload.get("deploy_remote_baseline_sha"),
            "preview_manifest_sha256": payload.get("deploy_preview_manifest_sha256"),
            "reason": reason,
            "next_action": "run A5 preview again, then approve-deploy or defer",
        }
        marker_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = marker_path.with_suffix(marker_path.suffix + f".tmp.{os.getpid()}")
        with tmp.open("w", encoding="utf-8") as fh:
            fh.write(json.dumps(marker, indent=2, sort_keys=True) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, marker_path)
        try:
            dir_fd = os.open(marker_path.parent, os.O_RDONLY)
        except OSError:
            dir_fd = None
        if dir_fd is not None:
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        payload["deploy_deferred_at"] = marker["created_at"]
        payload["deploy_defer_reason"] = reason
        payload["deploy_pending_marker_path"] = str(marker_path)
        payload.pop("deploy_verification_scope", None)
        _update_payload_locked(conn, task_id, payload, status=task["status"], blocker_reason=None)
        conn.commit()
        conn.close()
    return (True, str(marker_path))


def a5_resume_deferred_deploy(task_id: str) -> tuple[bool, str]:
    with _acquire_lock():
        conn = connect()
        init_db(conn)
        row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if row is None:
            conn.close()
            return (False, f"task {task_id} not found")
        task = dict(row)
        if task["status"] in TERMINAL_STATUSES:
            conn.close()
            return (False, f"task {task_id} is terminal: {task['status']}")
        payload = _payload_dict(task.get("payload"))
        action = a1_resume_action_locked(conn, task_id)
        if action != "deploy_deferred":
            conn.close()
            return (
                False,
                f"resume deferred deploy requires resume action deploy_deferred (got {action!r})",
            )
        marker_path = Path(str(payload.get("deploy_pending_marker_path") or "")).expanduser()
        if not marker_path.is_file():
            conn.close()
            return (False, "pending deploy marker missing; re-run record-deploy-preview")
        try:
            marker = json.loads(marker_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            conn.close()
            return (False, f"pending deploy marker unreadable: {exc}")
        expected = {
            "type": "k2bi-pending-deploy",
            "source_parent": payload.get("deploy_source_parent"),
            "source_status": payload.get("deploy_source_status"),
            "target_sha": payload.get("deploy_target_sha"),
            "remote_baseline_sha": payload.get("deploy_remote_baseline_sha"),
            "preview_manifest_sha256": payload.get("deploy_preview_manifest_sha256"),
        }
        for key, value in expected.items():
            if marker.get(key) != value:
                conn.close()
                return (False, f"pending deploy marker {key} does not match current payload")
        ok, reason = _a5_manifest_sha_matches_record(payload)
        if not ok:
            conn.close()
            return (False, reason)
        current_head, reason = _a5_current_k2bi_head()
        if current_head is None:
            conn.close()
            return (False, reason)
        target_sha = str(payload.get("deploy_target_sha") or "").strip().lower()
        if current_head != target_sha:
            conn.close()
            return (
                False,
                "K2Bi repo advanced while deferred; create a fresh A5 flight "
                "for the current HEAD before approving deploy",
            )
        for key in (
            "deploy_deferred_at",
            "deploy_defer_reason",
            "deploy_preview_recorded",
            "deploy_preview_recorded_at",
            "deploy_preview_manifest_path",
            "deploy_preview_manifest_realpath",
            "deploy_preview_manifest_sha256",
            "deploy_remote_baseline_sha",
            "deploy_categories",
            "deploy_restart_services",
            "deploy_live_effect",
            "deploy_authorized",
            "deploy_authorized_at",
            "deploy_lease_id",
            "deploy_approved_at",
            "deploy_approval_token",
            "deploy_dispatch_nonce",
            "deploy_verification_scope",
        ):
            payload.pop(key, None)
        payload["deploy_deferred_resumed_at"] = now_iso()
        _update_payload_locked(conn, task_id, payload, status=task["status"], blocker_reason=None)
        conn.commit()
        conn.close()
    return (True, "")


def _a5_inspect_deploy_state_from_payload(task_id: str, payload: dict) -> dict:
    verify_path = payload.get("deploy_verify_result_path")
    inspect_path = payload.get("deploy_inspect_path")
    if verify_path and inspect_path and str(verify_path) != str(inspect_path):
        return {
            "state": "unknown",
            "reason": "ambiguous deploy verification paths; keep only deploy_verify_result_path",
        }
    raw_path = verify_path or inspect_path
    if raw_path:
        path = Path(str(raw_path)).expanduser()
        trusted_root_raw = Path(K2B_VAULT) / "System" / "orchestrator" / "deploy-results"
        if _a5_path_has_symlink_component(trusted_root_raw):
            return {"state": "unknown", "reason": "deploy verify result root must not be a symlink"}
        try:
            root_stat = os.stat(trusted_root_raw, follow_symlinks=False)
        except OSError:
            return {
                "state": "unknown",
                "reason": "deploy verify result root missing or not a directory",
            }
        if not stat.S_ISDIR(root_stat.st_mode):
            return {
                "state": "unknown",
                "reason": "deploy verify result root missing or not a directory",
            }
        trusted_root = trusted_root_raw.resolve(strict=False)
        resolved = path.resolve(strict=False)
        if _a5_path_has_symlink_component(path):
            return {"state": "unknown", "reason": "deploy verify result path must not be a symlink"}
        try:
            under_trusted_root = os.path.commonpath([str(resolved), str(trusted_root)]) == str(
                trusted_root
            )
        except ValueError:
            under_trusted_root = False
        if not under_trusted_root:
            return {
                "state": "unknown",
                "reason": f"deploy verify result path must be under {trusted_root}",
            }
        dispatch_dt = _parse_iso_dt(payload.get("deploy_dispatch_started_at"))
        if dispatch_dt is not None and path.exists():
            try:
                mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
            except OSError as exc:
                return {"state": "unknown", "reason": f"deploy verify result unreadable: {exc}"}
            if mtime < dispatch_dt:
                return {
                    "state": "unknown",
                    "reason": "deploy verify result is older than dispatch start",
                }
        try:
            before_stat = os.stat(resolved, follow_symlinks=False)
        except OSError as exc:
            return {"state": "unknown", "reason": f"deploy verify result unreadable: {exc}"}
        if not hasattr(os, "O_NOFOLLOW"):
            return {
                "state": "unknown",
                "reason": "deploy verify result requires O_NOFOLLOW for symlink-safe reads",
            }
        flags = os.O_RDONLY | os.O_NOFOLLOW
        try:
            fd = os.open(resolved, flags)
        except OSError as exc:
            return {"state": "unknown", "reason": f"deploy verify result unreadable: {exc}"}
        try:
            fd_stat = os.fstat(fd)
            if (fd_stat.st_dev, fd_stat.st_ino) != (before_stat.st_dev, before_stat.st_ino):
                return {"state": "unknown", "reason": "deploy verify result changed while opening"}
            with os.fdopen(fd, "r", encoding="utf-8") as fh:
                fd = None
                data = json.load(fh)
        except (OSError, json.JSONDecodeError) as exc:
            return {"state": "unknown", "reason": f"deploy verify result unreadable: {exc}"}
        finally:
            if fd is not None:
                os.close(fd)
        if isinstance(data, dict):
            return data
        return {"state": "unknown", "reason": "deploy verify result must be a JSON object"}
    return {"state": "unknown", "reason": "deploy independent verification result missing"}


def _a5_deploy_inspect_clean(payload: dict, inspect: dict) -> tuple[bool, str]:
    target_sha = str(payload.get("deploy_target_sha") or "").strip().lower()
    if inspect.get("state") != "deployed":
        return (False, inspect.get("reason") or f"inspect state {inspect.get('state')!r}")
    verification_scope = str(inspect.get("verification_scope") or "").strip()
    if verification_scope and verification_scope != "category_scoped":
        return (False, f"unknown verification_scope: {verification_scope!r}")
    remote_head = str(inspect.get("remote_head") or "").strip().lower()
    if verification_scope == "category_scoped":
        ok, reason = _a5_category_scoped_deploy_inspect_clean(payload, inspect)
        if not ok:
            return (False, reason)
    elif remote_head != target_sha:
        return (False, "remote_head does not match deploy target sha")
    if str(inspect.get("sync_state_sha") or "").strip().lower() != target_sha:
        return (False, "sync_state_sha does not match deploy target sha")
    approval_token = str(payload.get("deploy_approval_token") or "").strip()
    inspect_approval_token = str(inspect.get("approval_token") or "").strip()
    if not approval_token:
        return (False, "deploy approval_token missing from current dispatch")
    if not inspect_approval_token:
        return (False, "deploy verification approval_token missing")
    if inspect_approval_token != approval_token:
        return (False, "deploy verification approval_token does not match current dispatch")
    ok, reason = _a5_deploy_token_matches_payload(payload)
    if not ok:
        return (False, reason)
    dispatch_started_at = str(payload.get("deploy_dispatch_started_at") or "").strip()
    inspect_dispatch_started_at = str(inspect.get("dispatch_started_at") or "").strip()
    if not dispatch_started_at:
        return (False, "deploy dispatch_started_at missing from current dispatch")
    if not inspect_dispatch_started_at:
        return (False, "deploy verification dispatch_started_at missing")
    if inspect_dispatch_started_at != dispatch_started_at:
        return (False, "deploy verification dispatch_started_at does not match current dispatch")
    dispatch_nonce = str(payload.get("deploy_dispatch_nonce") or "").strip()
    inspect_dispatch_nonce = str(inspect.get("dispatch_nonce") or "").strip()
    if not dispatch_nonce:
        return (False, "deploy dispatch nonce missing from current dispatch")
    if not inspect_dispatch_nonce:
        return (False, "deploy verification dispatch_nonce missing")
    if inspect_dispatch_nonce != dispatch_nonce:
        return (False, "deploy verification dispatch_nonce does not match current dispatch")
    restart_services = payload.get("deploy_restart_services")
    if restart_services:
        services = inspect.get("services")
        if isinstance(services, dict):
            for service in restart_services:
                if services.get(service) is not True:
                    return (False, f"expected restarted service is not active: {service}")
        elif inspect.get("service_active") is not True:
            return (False, "expected restarted service is not active")
    try:
        mismatch_count = int(inspect.get("recovery_state_mismatch_count") or 0)
    except (TypeError, ValueError):
        return (False, "recovery_state_mismatch_count is not an integer")
    if mismatch_count != 0:
        return (False, "recovery_state_mismatch detected")
    return (True, "")


def _a5_strict_category_set(raw: object, label: str) -> tuple[set[str] | None, str]:
    if not isinstance(raw, list):
        return (None, f"{label} must be a string list")
    categories: set[str] = set()
    for item in raw:
        if not isinstance(item, str) or not item.strip():
            return (None, f"{label} contains empty category")
        category = item.strip()
        if category in categories:
            return (None, f"{label} contains duplicate category: {category}")
        categories.add(category)
    if not categories:
        return (None, f"{label} missing")
    return (categories, "")


def _a5_category_scoped_deploy_inspect_clean(payload: dict, inspect: dict) -> tuple[bool, str]:
    if inspect.get("verification_scope") != "category_scoped":
        return (False, "verification_scope must be category_scoped")
    remote_head = str(inspect.get("remote_head") or "").strip().lower()
    baseline_sha = str(payload.get("deploy_remote_baseline_sha") or "").strip().lower()
    if not baseline_sha:
        return (False, "deploy_remote_baseline_sha missing from payload")
    if not _SHA40_RE.match(baseline_sha):
        return (False, "deploy_remote_baseline_sha malformed in payload")
    if remote_head != baseline_sha:
        return (False, "category-scoped remote_head does not match deploy baseline sha")
    sync_state_sha = str(inspect.get("sync_state_sha") or "").strip().lower()
    if sync_state_sha == baseline_sha:
        return (False, "category-scoped sync_state_sha did not advance from deploy baseline sha")
    expected, reason = _a5_strict_category_set(
        payload.get("deploy_categories"), "deploy preview categories"
    )
    if expected is None:
        return (False, reason)
    observed, reason = _a5_strict_category_set(
        inspect.get("deployed_categories"), "deploy verification deployed_categories"
    )
    if observed is None:
        return (False, reason)
    if observed != expected:
        return (False, "deploy verification categories do not match preview categories")
    results = inspect.get("category_results")
    if not isinstance(results, dict):
        return (False, "deploy verification category_results missing")
    result_categories, reason = _a5_strict_category_set(
        list(results.keys()), "deploy verification category_results"
    )
    if result_categories is None:
        return (False, reason)
    if result_categories != expected:
        return (False, "deploy verification category_results do not match preview categories")
    for category in sorted(expected):
        result = results.get(category)
        if not isinstance(result, dict):
            return (False, f"deploy verification category result missing: {category}")
        if result.get("matched_target") is not True:
            return (False, f"deploy verification category did not match target: {category}")
        if "path_count" not in result:
            return (False, f"deploy verification category path_count missing: {category}")
        path_count = result.get("path_count")
        if not isinstance(path_count, int) or isinstance(path_count, bool):
            return (False, f"deploy verification category path_count invalid: {category}")
        if path_count <= 0:
            return (False, f"deploy verification category has no matched paths: {category}")
        for key in ("missing_paths", "mismatched_paths", "extra_paths"):
            if key not in result:
                return (False, f"deploy verification category {key} missing: {category}")
            values = result.get(key)
            if not isinstance(values, list):
                return (False, f"deploy verification category {key} must be a list: {category}")
            if values:
                return (False, f"deploy verification category has {key}: {category}")
    return (True, "")


def _a5_compact_deploy_inspect(inspect: dict) -> dict:
    return {
        key: inspect.get(key)
        for key in (
            "state",
            "verification_scope",
            "remote_head",
            "sync_state_sha",
            "deployed_categories",
            "category_results",
            "service_active",
            "recovery_state_mismatch_count",
            "reason",
        )
        if key in inspect
    }


def a5_inspect_deploy_state(task_id: str) -> dict:
    task = get_task(task_id)
    if task is None:
        return {"state": "unknown", "reason": f"task {task_id} not found"}
    payload = _payload_dict(task.get("payload"))
    return _a5_inspect_deploy_state_from_payload(task_id, payload)


def a5_verify_deploy(task_id: str) -> tuple[bool, str]:
    with _acquire_lock():
        conn = connect()
        init_db(conn)
        row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if row is None:
            conn.close()
            return (False, f"task {task_id} not found")
        task = dict(row)
        if task["status"] in TERMINAL_STATUSES:
            conn.close()
            return (False, f"task {task_id} is terminal: {task['status']}")
        payload = _payload_dict(task.get("payload"))
        action = a1_resume_action_locked(conn, task_id)
        if action != "verify_deploy":
            conn.close()
            return (False, f"deploy verify requires resume action verify_deploy (got {action!r})")
        inspect = _a5_inspect_deploy_state_from_payload(task_id, payload)
        payload["deploy_inspect_state"] = inspect.get("state")
        payload["deploy_inspect"] = _a5_compact_deploy_inspect(inspect)
        ok, refusal = _a5_deploy_inspect_clean(payload, inspect)
        if ok:
            payload["deploy_verified"] = True
            payload["deploy_verified_at"] = now_iso()
            payload["deploy_deployed_sha"] = str(inspect.get("sync_state_sha") or "").strip().lower()
            verification_scope = str(inspect.get("verification_scope") or "").strip()
            if verification_scope:
                payload["deploy_verification_scope"] = verification_scope
            else:
                payload.pop("deploy_verification_scope", None)
            _update_payload_locked(conn, task_id, payload, status="terminal_deployed")
            conn.commit()
            conn.close()
            return (True, "")
        reason = f"deploy verification refused: {refusal}"
        payload["deploy_verify_failed_at"] = now_iso()
        payload["deploy_verify_failed_reason"] = reason
        payload.pop("deploy_verified", None)
        payload.pop("deploy_verified_at", None)
        payload.pop("deploy_verification_scope", None)
        _update_payload_locked(conn, task_id, payload, status=task["status"])
        conn.commit()
        conn.close()
    return (False, reason)


def a5_record_deploy_failed(task_id: str, *, reason: str) -> tuple[bool, str]:
    with _acquire_lock():
        conn = connect()
        init_db(conn)
        row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if row is None:
            conn.close()
            return (False, f"task {task_id} not found")
        task = dict(row)
        if task["status"] in TERMINAL_STATUSES:
            conn.close()
            return (False, f"task {task_id} is terminal: {task['status']}")
        payload = _payload_dict(task.get("payload"))
        inspect = _a5_inspect_deploy_state_from_payload(task_id, payload)
        n = now_iso()
        payload.setdefault("deploy_failed_at", n)
        payload.setdefault("deploy_failure_reason", reason)
        payload["deploy_inspect_state"] = inspect.get("state")
        payload["deploy_inspect"] = _a5_compact_deploy_inspect(inspect)
        payload.pop("deploy_verification_scope", None)
        _update_payload_locked(conn, task_id, payload, status="needs_human", blocker_reason=reason)
        conn.commit()
        conn.close()
    return (True, "")


def a5_retry_deploy_after_rollback(task_id: str) -> tuple[bool, str]:
    with _acquire_lock():
        conn = connect()
        init_db(conn)
        row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if row is None:
            conn.close()
            return (False, f"task {task_id} not found")
        task = dict(row)
        if task["status"] in TERMINAL_STATUSES:
            conn.close()
            return (False, f"task {task_id} is terminal: {task['status']}")
        payload = _payload_dict(task.get("payload"))
        inspect = _a5_inspect_deploy_state_from_payload(task_id, payload)
        if inspect.get("state") != "clean_rollback":
            conn.close()
            return (
                False,
                f"retry requires deploy inspect state clean_rollback "
                f"(got {inspect.get('state')!r})",
            )
        ok, reason = _a5_manifest_sha_matches_record(payload)
        if not ok:
            conn.close()
            return (False, reason)
        current_head, reason = _a5_current_k2bi_head()
        if current_head is None:
            conn.close()
            return (False, reason)
        target_sha = str(payload.get("deploy_target_sha") or "").strip().lower()
        if current_head != target_sha:
            conn.close()
            return (
                False,
                "K2Bi repo advanced since deploy authorization; create a fresh A5 flight "
                "for the current HEAD before retrying deploy",
            )
        try:
            attempts = int(payload.get("deploy_attempt_count") or 0)
        except (TypeError, ValueError):
            attempts = 0
        if attempts >= A5_MAX_DEPLOY_ATTEMPTS:
            _a5_terminalize_attempt_limit_locked(conn, task_id, payload)
            conn.commit()
            conn.close()
            return (False, f"deploy attempt limit exceeded ({A5_MAX_DEPLOY_ATTEMPTS})")
        if not payload.get("deploy_failed_at"):
            conn.close()
            return (False, "retry requires a recorded deploy_failed_at")
        for key in (
            "deploy_dispatch_started_at",
            "deploy_dispatch_nonce",
            "deploy_failed_at",
            "deploy_failure_reason",
            "deploy_partial_detected_at",
            "deploy_partial_reason",
            "deploy_verify_failed_at",
            "deploy_verify_failed_reason",
            "deploy_inspect_state",
            "deploy_inspect",
            "deploy_verification_scope",
        ):
            payload.pop(key, None)
        payload["deploy_retry_ready_at"] = now_iso()
        _update_payload_locked(conn, task_id, payload, status="needs_human", blocker_reason=None)
        conn.commit()
        conn.close()
    return (True, "")


def a1_authorize_ship(task_id: str) -> tuple[bool, str]:
    """Record the fourth human gate that authorizes A3 ship-to-engine."""
    with _acquire_lock():
        conn = connect()
        init_db(conn)
        row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if row is None:
            conn.close()
            return (False, f"task {task_id} not found")
        task = dict(row)
        if task["status"] in TERMINAL_STATUSES:
            conn.close()
            return (False, f"task {task_id} is terminal: {task['status']}")
        payload = _payload_dict(task.get("payload"))
        if payload.get("ship_authorized"):
            conn.close()
            return (True, "")
        action = a1_resume_action_locked(conn, task_id)
        if action != "strategy_approved_await_ship":
            conn.close()
            return (
                False,
                f"ship authorization requires resume action strategy_approved_await_ship "
                f"(got {action!r})",
            )
        payload["ship_authorized"] = True
        payload["ship_authorized_at"] = now_iso()
        _update_payload_locked(conn, task_id, payload, status=task["status"])
        conn.commit()
        conn.close()
    return (True, "")


def _strategy_normalized_for_bind(data: bytes) -> bytes:
    """Strategy-file bytes with the volatile forward-guidance write timestamp removed.

    K2Bi's `write_complete_strategy_spec` stamps `forward_guidance_check.completed_at`
    at write time, so re-authoring the SAME approved decision into the repo produces a
    file that differs from the A2-approved vault file on EXACTLY that one line. The raw
    A2 sha-bind would then never match a legitimate re-author (live-MVP finding,
    2026-06-08). Strip that single non-material line so the bind can prove the strategy
    (order / rules / thresholds / how-it-works) is byte-identical to what was backtested
    and approved, ignoring only the auto-generated write timestamp. Any OTHER byte
    difference survives and still refuses.

    Operates on bytes the caller already read ONCE (no TOCTOU between the sha check and
    this normalization -- both run on the same in-memory content). Strips only an
    INDENTED `completed_at:` line (`^\\s+`), i.e. the one nested under
    `forward_guidance_check:`; a hypothetical top-level `completed_at:` is left intact so
    a material difference cannot be masked.
    """
    text = data.decode("utf-8", errors="replace")
    return "".join(
        ln for ln in text.splitlines(keepends=True)
        if not re.match(r"^\s+completed_at:\s*", ln)
    ).encode("utf-8")


def a1_record_ship_repo_authored(task_id: str, strategy_path: str) -> tuple[bool, str]:
    """Bind the repo-authored proposed strategy to the approved A2 artifact sha."""
    path = Path(str(strategy_path)).expanduser()
    if not path.is_file():
        return (False, f"repo strategy missing or not a file: {path}")
    if path.stat().st_size == 0:
        return (False, f"repo strategy is empty: {path}")
    try:
        repo_bytes = path.read_bytes()  # read ONCE: sha + normalize from the same bytes
    except OSError as exc:
        return (False, f"repo strategy unreadable: {exc}")
    actual_sha = hashlib.sha256(repo_bytes).hexdigest()
    fm, perr = _parse_md_frontmatter(path)
    if fm is None:
        return (False, f"repo strategy frontmatter invalid: {perr}")
    with _acquire_lock():
        conn = connect()
        init_db(conn)
        row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if row is None:
            conn.close()
            return (False, f"task {task_id} not found")
        task = dict(row)
        if task["status"] in TERMINAL_STATUSES:
            conn.close()
            return (False, f"task {task_id} is terminal: {task['status']}")
        payload = _payload_dict(task.get("payload"))
        if payload.get("ship_authorized") is not True:
            conn.close()
            return (False, "ship must be authorized before recording repo authorship")
        expected_sha = str(payload.get("strategy_artifact_sha256") or "").strip()
        if not expected_sha:
            conn.close()
            return (False, "strategy_artifact_sha256 missing; cannot bind repo strategy")
        if actual_sha != expected_sha:
            # Re-authoring the SAME approved decision differs from the A2-approved vault
            # file ONLY by the write-time forward_guidance_check.completed_at. Accept ONLY
            # when (a) the recorded vault strategy file is STILL the approved artifact
            # (its sha still equals the recorded A2 sha -- so we are comparing against the
            # real backtested+approved bytes, not a drifted vault) AND (b) the repo file
            # equals that vault file modulo the single volatile completed_at line. Any
            # other byte difference, a drifted vault, or an unreadable file still refuses.
            vault_raw = payload.get("strategy_path")
            vault_path = Path(str(vault_raw)).expanduser() if vault_raw else None
            vault_bytes = None
            if vault_path is not None and vault_path.is_file():
                try:
                    vault_bytes = vault_path.read_bytes()  # read ONCE (no TOCTOU)
                except OSError:
                    vault_bytes = None
            # The recorded vault strategy must STILL be the approved artifact (its sha,
            # on the very bytes we normalize, equals the recorded A2 sha), AND the repo
            # file equals it modulo the single volatile completed_at line. Same-bytes
            # sha+normalize closes the read-after-check race.
            vault_is_approved = bool(
                vault_bytes is not None
                and hashlib.sha256(vault_bytes).hexdigest() == expected_sha
            )
            if not (
                vault_is_approved
                and _strategy_normalized_for_bind(repo_bytes)
                == _strategy_normalized_for_bind(vault_bytes)
            ):
                conn.close()
                return (
                    False,
                    f"repo strategy sha256 {actual_sha} does not match approved A2 "
                    f"strategy_artifact_sha256 {expected_sha}, and its normalized content "
                    f"(modulo the forward-guidance write timestamp) does not match the "
                    f"approved vault strategy",
                )
        entity = str(task.get("entity_key") or "").strip().upper()
        order = fm.get("order")
        order_ticker = (
            str(order.get("ticker", "")).strip().upper()
            if isinstance(order, dict)
            else ""
        )
        ticker = order_ticker or str(fm.get("ticker", "")).strip().upper()
        if entity and ticker != entity:
            conn.close()
            return (
                False,
                f"repo strategy ticker {ticker!r} does not match parent flight entity {entity!r}",
            )
        slug = _a3_strategy_slug_from_path(path)
        if slug is None:
            conn.close()
            return (False, f"repo strategy filename must be strategy_<slug>.md: {path}")
        payload["ship_repo_authored"] = True
        payload["ship_repo_authored_at"] = now_iso()
        payload["ship_strategy_repo_path"] = str(path)
        payload["ship_strategy_repo_sha256"] = actual_sha
        payload["ship_strategy_slug"] = slug
        # A fresh repo authoring pass resets any downstream ship dispatch/recovery
        # markers, but it keeps ship_attempt_count so retry bounding survives.
        for key in (
            "ship_proposed_commit_sha",
            "ship_proposed_committed_at",
            "ship_dispatch_started_at",
            "ship_repo_head_before",
            "ship_dispatch_repo_sha256",
            "ship_lease_id",
            "ship_approved_at",
            "ship_approval_token",
            "ship_verified",
            "ship_verified_at",
            "ship_commit_sha",
            "ship_failed_at",
            "ship_failure_reason",
            "ship_rolled_back_at",
            "ship_rollback_reason",
            "ship_rollback_clean",
            "ship_partial_detected_at",
            "ship_partial_reason",
            "ship_verify_failed_at",
            "ship_inspect_state",
        ):
            payload.pop(key, None)
        _update_payload_locked(conn, task_id, payload, status=task["status"])
        conn.commit()
        conn.close()
    return (True, "")


def a1_commit_ship_repo_proposed(task_id: str) -> tuple[bool, str]:
    """Commit the repo-authored strategy as status=proposed.

    This creates the inert draft commit K2Bi hooks allow. The later approved
    transition remains owned by run_full_ship.
    """
    with _acquire_lock():
        conn = connect()
        init_db(conn)
        row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if row is None:
            conn.close()
            return (False, f"task {task_id} not found")
        task = dict(row)
        if task["status"] in TERMINAL_STATUSES:
            conn.close()
            return (False, f"task {task_id} is terminal: {task['status']}")
        payload = _payload_dict(task.get("payload"))
        raw_path = payload.get("ship_strategy_repo_path")
        if not raw_path:
            conn.close()
            return (False, "ship_strategy_repo_path missing")
        path = Path(str(raw_path)).expanduser()
        if not path.is_file():
            conn.close()
            return (False, f"repo strategy missing or not a file: {path}")
        recorded_sha = str(payload.get("ship_strategy_repo_sha256") or "").strip()
        if not recorded_sha:
            conn.close()
            return (False, "ship_strategy_repo_sha256 missing; record repo authorship first")
        fm, perr = _parse_md_frontmatter(path)
        if fm is None:
            conn.close()
            return (False, f"repo strategy frontmatter invalid: {perr}")
        status_value = str(fm.get("status") or "").strip().lower()
        if status_value != "proposed":
            conn.close()
            return (
                False,
                f"repo strategy status must be 'proposed' to commit a draft, "
                f"got {status_value!r}",
            )
        current_sha = _sha256_file(path)
        if current_sha != recorded_sha:
            conn.close()
            return (
                False,
                f"repo strategy sha256 {current_sha} changed since repo authorship "
                f"record {recorded_sha}",
            )
        repo = _a3_git_repo_root_for_path(path)
        if repo is None:
            conn.close()
            return (False, "cannot resolve K2Bi git repo for strategy path")
        try:
            rel_path = path.resolve(strict=False).relative_to(repo.resolve(strict=False)).as_posix()
        except ValueError:
            conn.close()
            return (False, f"strategy path is outside git repo: {path}")
        if _a3_git_path_tracked_at_head(repo, path) and _a3_git_status_for_path(repo, path) == "":
            head = _a3_git_head(repo)
            payload["ship_proposed_commit_sha"] = head
            payload.setdefault("ship_proposed_committed_at", now_iso())
            _update_payload_locked(conn, task_id, payload, status=task["status"])
            conn.commit()
            conn.close()
            return (True, "")
        action = a1_resume_action_locked(conn, task_id)
        if action != "commit_strategy_proposed":
            conn.close()
            return (
                False,
                f"proposed commit requires resume action commit_strategy_proposed "
                f"(got {action!r})",
            )
        status_all = subprocess.run(
            [
                "git",
                "-C",
                str(repo),
                "status",
                "--porcelain=v1",
                "--untracked-files=all",
                "--ignore-submodules=none",
            ],
            capture_output=True,
            text=True,
        )
        if status_all.returncode != 0:
            conn.close()
            return (False, f"git status failed: {(status_all.stderr or status_all.stdout).strip()}")
        dirty_other = [
            line for line in status_all.stdout.splitlines()
            if line and line[3:].strip() != rel_path
        ]
        if dirty_other:
            conn.close()
            return (
                False,
                "K2Bi tree has unrelated changes; refuse proposed commit: "
                + "; ".join(dirty_other[:5]),
            )
        add = subprocess.run(
            ["git", "-C", str(repo), "add", "--", rel_path],
            capture_output=True,
            text=True,
        )
        if add.returncode != 0:
            conn.close()
            return (False, f"git add failed: {(add.stderr or add.stdout).strip()}")
        staged_sha, staged_bytes = _a3_git_staged_blob_sha(repo, rel_path)
        if staged_sha != recorded_sha:
            subprocess.run(
                ["git", "-C", str(repo), "reset", "-q", "HEAD", "--", rel_path],
                capture_output=True,
                text=True,
            )
            conn.close()
            return (
                False,
                f"staged blob sha {staged_sha} != bound proposed sha {recorded_sha}; "
                "refusing commit (mid-flight mutation)",
            )
        if staged_bytes is None:
            subprocess.run(
                ["git", "-C", str(repo), "reset", "-q", "HEAD", "--", rel_path],
                capture_output=True,
                text=True,
            )
            conn.close()
            return (False, "staged blob missing after git add")
        staged_fm, staged_perr = _parse_md_frontmatter_bytes(staged_bytes)
        if staged_fm is None:
            subprocess.run(
                ["git", "-C", str(repo), "reset", "-q", "HEAD", "--", rel_path],
                capture_output=True,
                text=True,
            )
            conn.close()
            return (False, f"staged strategy frontmatter invalid: {staged_perr}")
        staged_status = str(staged_fm.get("status") or "").strip().lower()
        if staged_status != "proposed":
            subprocess.run(
                ["git", "-C", str(repo), "reset", "-q", "HEAD", "--", rel_path],
                capture_output=True,
                text=True,
            )
            conn.close()
            return (
                False,
                f"staged strategy status must be 'proposed', got {staged_status!r}",
            )
        slug = _a3_strategy_slug_from_path(path)
        message = (
            f"feat(strategy): propose strategy_{slug} (status: proposed)\n\n"
            f"Orchestrator A3 proposed-first commit for flight {task_id}.\n"
            "Strategy-Transition: (new file) -> proposed"
        )
        ok, out = _a3_git_commit_only(repo, rel_path, message)
        if not ok:
            subprocess.run(
                ["git", "-C", str(repo), "reset", "-q", "HEAD", "--", rel_path],
                capture_output=True,
                text=True,
            )
            conn.close()
            return (False, f"proposed commit refused: {out}")
        head = _a3_git_head(repo)
        payload["ship_proposed_commit_sha"] = head
        payload["ship_proposed_committed_at"] = now_iso()
        _update_payload_locked(conn, task_id, payload, status=task["status"])
        conn.commit()
        conn.close()
    return (True, "")


def a1_mark_ship_dispatch_started(task_id: str) -> tuple[bool, dict | str]:
    """Mint the A3 ship lease/token evidence and record it before dispatch."""
    with _acquire_lock():
        conn = connect()
        init_db(conn)
        row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if row is None:
            conn.close()
            return (False, f"task {task_id} not found")
        task = dict(row)
        if task["status"] in TERMINAL_STATUSES:
            conn.close()
            return (False, f"task {task_id} is terminal: {task['status']}")
        payload = _payload_dict(task.get("payload"))
        try:
            attempts = int(payload.get("ship_attempt_count") or 0)
        except (TypeError, ValueError):
            attempts = 0
        if attempts >= A3_MAX_SHIP_ATTEMPTS:
            _a3_terminalize_attempt_limit_locked(conn, task_id, payload)
            conn.commit()
            conn.close()
            return (False, f"ship attempt limit exceeded ({A3_MAX_SHIP_ATTEMPTS})")
        action = a1_resume_action_locked(conn, task_id)
        if action != "dispatch_ship":
            conn.close()
            return (False, f"ship dispatch requires resume action dispatch_ship (got {action!r})")
        raw_path = payload.get("ship_strategy_repo_path")
        if not raw_path:
            conn.close()
            return (False, "ship_strategy_repo_path missing")
        path = Path(str(raw_path)).expanduser()
        if not path.is_file():
            conn.close()
            return (False, f"repo strategy missing or not a file: {path}")
        slug = _a3_strategy_slug_from_path(path)
        if slug is None:
            conn.close()
            return (False, f"repo strategy filename must be strategy_<slug>.md: {path}")
        current_sha = _sha256_file(path)
        recorded_sha = str(payload.get("ship_strategy_repo_sha256") or "").strip()
        if not recorded_sha:
            conn.close()
            return (False, "ship_strategy_repo_sha256 missing; cannot dispatch ship")
        if current_sha != recorded_sha:
            conn.close()
            return (
                False,
                f"repo strategy sha256 {current_sha} changed since repo authorship "
                f"record {recorded_sha}",
            )
        repo = _a3_git_repo_root_for_path(path)
        if repo is None:
            conn.close()
            return (False, "cannot establish ship baseline: K2Bi repo git HEAD unavailable")
        head = _a3_git_head(repo)
        if not head:
            conn.close()
            return (False, "cannot establish ship baseline: K2Bi repo git HEAD unavailable")
        dt = datetime.now(timezone.utc)
        approved_at = dt.isoformat()
        lease_id = f"{slug}-ship-a1-{dt.strftime('%Y%m%dT%H%M%SZ')}"
        token = f"APPROVE_STRATEGY:{slug}:{current_sha}:{approved_at}:{lease_id}"
        payload["ship_attempt_count"] = attempts + 1
        payload["ship_dispatch_started_at"] = approved_at
        payload["ship_dispatch_repo_sha256"] = current_sha
        payload["ship_repo_head_before"] = head
        payload["ship_lease_id"] = lease_id
        payload["ship_approved_at"] = approved_at
        payload["ship_approval_token"] = token
        for key in (
            "ship_failed_at",
            "ship_failure_reason",
            "ship_rolled_back_at",
            "ship_rollback_reason",
            "ship_rollback_clean",
            "ship_partial_detected_at",
            "ship_partial_reason",
            "ship_verify_failed_at",
            "ship_inspect_state",
        ):
            payload.pop(key, None)
        _update_payload_locked(conn, task_id, payload, status=task["status"])
        conn.commit()
        conn.close()
    return (
        True,
        {
            "lease_id": lease_id,
            "ship_lease_id": lease_id,
            "repo_sha": current_sha,
            "approved_at": approved_at,
            "approval_token": token,
            "strategy_path": str(path),
        },
    )


def _a1_inspect_ship_state_from_payload(task_id: str, payload: dict) -> dict:
    raw_path = payload.get("ship_strategy_repo_path")
    if not raw_path:
        return {"state": "unknown", "reason": "ship_strategy_repo_path missing"}
    path = Path(str(raw_path)).expanduser()
    slug = _a3_strategy_slug_from_path(path)
    if slug is None:
        return {
            "state": "unknown",
            "reason": f"strategy filename must be strategy_<slug>.md: {path}",
            "strategy_path": str(path),
        }
    repo = _a3_git_repo_root_for_path(path)
    if repo is None:
        return {
            "state": "unknown",
            "reason": f"strategy path is not inside a git repo: {path}",
            "strategy_path": str(path),
        }
    marker = repo / ".k2bi-orchestrator" / "rollback" / f"{slug}.json"
    head = _a3_git_head(repo)
    status_text = _a3_git_status_for_path(repo, path)
    tracked_at_head = _a3_git_path_tracked_at_head(repo, path)
    result = {
        "state": "unknown",
        "strategy_path": str(path),
        "repo_root": str(repo),
        "slug": slug,
        "head": head,
        "git_status": status_text,
        "tracked_at_head": tracked_at_head,
        "rollback_marker_path": str(marker),
        "rollback_marker_exists": marker.exists(),
    }
    if marker.exists():
        result["state"] = "incomplete_rollback_marker"
        return result
    if not path.is_file():
        result["reason"] = "strategy file missing"
        return result
    try:
        result["file_sha256"] = _sha256_file(path)
    except OSError as exc:
        result["reason"] = f"strategy file unreadable: {exc}"
        return result
    fm, perr = _parse_md_frontmatter(path)
    if fm is None:
        result["reason"] = f"strategy frontmatter invalid: {perr}"
        return result
    strategy_status = str(fm.get("status") or "").strip().lower()
    result["strategy_status"] = strategy_status
    if strategy_status == "approved":
        head_before = str(payload.get("ship_repo_head_before") or "").strip()
        head_advanced = bool(head and head_before and head != head_before)
        result["head_advanced"] = head_advanced
        if status_text == "" and tracked_at_head and head_advanced:
            result["state"] = "committed"
        else:
            result["state"] = "partial_approved_uncommitted"
            if status_text == "" and not tracked_at_head:
                result["reason"] = "approved strategy is not tracked at HEAD"
            elif status_text == "" and not head_advanced:
                result["reason"] = "approved strategy did not land in a new HEAD commit"
        return result
    if strategy_status == "proposed":
        recorded_sha = str(payload.get("ship_strategy_repo_sha256") or "").strip()
        if not recorded_sha or recorded_sha == result["file_sha256"]:
            result["state"] = "clean_rollback"
        else:
            result["reason"] = "proposed strategy sha256 differs from recorded repo-authored sha"
        return result
    result["reason"] = f"unexpected strategy status {strategy_status!r}"
    return result


def a1_inspect_ship_state(task_id: str) -> dict:
    """Classify live A3 ship state from git + strategy file + rollback marker."""
    task = get_task(task_id)
    if task is None:
        return {"state": "unknown", "reason": f"task {task_id} not found"}
    payload = _payload_dict(task.get("payload"))
    return _a1_inspect_ship_state_from_payload(task_id, payload)


def a1_verify_ship(task_id: str) -> tuple[bool, str]:
    """Terminalize A3 only after the independent inspector sees a committed ship."""
    with _acquire_lock():
        conn = connect()
        init_db(conn)
        row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if row is None:
            conn.close()
            return (False, f"task {task_id} not found")
        task = dict(row)
        if task["status"] in TERMINAL_STATUSES:
            conn.close()
            return (False, f"task {task_id} is terminal: {task['status']}")
        payload = _payload_dict(task.get("payload"))
        inspect = _a1_inspect_ship_state_from_payload(task_id, payload)
        state = inspect.get("state")
        payload["ship_inspect_state"] = state
        payload["ship_inspect"] = _a3_compact_ship_inspect(inspect)
        if state == "committed":
            payload["ship_verified"] = True
            payload["ship_verified_at"] = now_iso()
            payload["ship_commit_sha"] = inspect.get("head")
            _update_payload_locked(conn, task_id, payload, status="terminal_shipped")
            conn.commit()
            conn.close()
            return (True, "")
        reason = f"ship verification refused: inspect state {state!r}"
        payload["ship_verify_failed_at"] = now_iso()
        payload["ship_verify_failed_reason"] = reason
        if state == "partial_approved_uncommitted":
            payload["ship_partial_detected_at"] = now_iso()
            payload["ship_partial_reason"] = reason
            _update_payload_locked(
                conn,
                task_id,
                payload,
                status="needs_human",
                blocker_reason=reason,
            )
        else:
            _update_payload_locked(conn, task_id, payload, status=task["status"])
        conn.commit()
        conn.close()
    return (False, reason)


def a1_record_ship_failed(task_id: str, *, reason: str) -> tuple[bool, str]:
    """Record a failed A3 ship attempt using the live independent inspector."""
    with _acquire_lock():
        conn = connect()
        init_db(conn)
        row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if row is None:
            conn.close()
            return (False, f"task {task_id} not found")
        task = dict(row)
        if task["status"] in TERMINAL_STATUSES:
            conn.close()
            return (False, f"task {task_id} is terminal: {task['status']}")
        payload = _payload_dict(task.get("payload"))
        inspect = _a1_inspect_ship_state_from_payload(task_id, payload)
        state = inspect.get("state")
        n = now_iso()
        if not payload.get("ship_failed_at"):
            payload["ship_failed_at"] = n
        if not payload.get("ship_failure_reason"):
            payload["ship_failure_reason"] = reason
        payload["ship_inspect_state"] = state
        payload["ship_inspect"] = inspect
        payload["ship_rollback_clean"] = state == "clean_rollback"
        blocker_reason = reason
        if state == "clean_rollback":
            payload["ship_rolled_back_at"] = n
            payload["ship_rollback_reason"] = reason
        elif state == "partial_approved_uncommitted":
            payload["ship_partial_detected_at"] = n
            payload["ship_partial_reason"] = reason
            blocker_reason = f"{reason}; partial_approved_uncommitted"
        _update_payload_locked(
            conn,
            task_id,
            payload,
            status="needs_human",
            blocker_reason=blocker_reason,
        )
        conn.commit()
        conn.close()
    return (True, "")


def a1_retry_ship_after_rollback(task_id: str) -> tuple[bool, str]:
    """Allow a bounded retry only when live inspection shows a clean rollback."""
    with _acquire_lock():
        conn = connect()
        init_db(conn)
        row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if row is None:
            conn.close()
            return (False, f"task {task_id} not found")
        task = dict(row)
        if task["status"] in TERMINAL_STATUSES:
            conn.close()
            return (False, f"task {task_id} is terminal: {task['status']}")
        payload = _payload_dict(task.get("payload"))
        inspect = _a1_inspect_ship_state_from_payload(task_id, payload)
        if inspect.get("state") != "clean_rollback":
            conn.close()
            return (
                False,
                f"retry requires live inspect state clean_rollback "
                f"(got {inspect.get('state')!r})",
            )
        try:
            attempts = int(payload.get("ship_attempt_count") or 0)
        except (TypeError, ValueError):
            attempts = 0
        if attempts >= A3_MAX_SHIP_ATTEMPTS:
            _a3_terminalize_attempt_limit_locked(conn, task_id, payload)
            conn.commit()
            conn.close()
            return (False, f"ship attempt limit exceeded ({A3_MAX_SHIP_ATTEMPTS})")
        if not payload.get("ship_rolled_back_at"):
            conn.close()
            return (False, "retry requires a recorded ship_rolled_back_at")
        for key in (
            "ship_dispatch_started_at",
            "ship_repo_head_before",
            "ship_dispatch_repo_sha256",
            "ship_lease_id",
            "ship_approved_at",
            "ship_approval_token",
            "ship_failed_at",
            "ship_failure_reason",
            "ship_rolled_back_at",
            "ship_rollback_reason",
            "ship_rollback_clean",
            "ship_partial_detected_at",
            "ship_partial_reason",
            "ship_verify_failed_at",
            "ship_verify_failed_reason",
            "ship_inspect_state",
            "ship_inspect",
        ):
            payload.pop(key, None)
        payload["ship_retry_ready_at"] = now_iso()
        _update_payload_locked(conn, task_id, payload, status="needs_human", blocker_reason=None)
        conn.commit()
        conn.close()
    return (True, "")


def a1_reset_ship_attempts(
    task_id: str,
    *,
    checked_log: bool,
    reason: str | None,
) -> tuple[bool, str]:
    """Operator-attested reset of the A3 ship attempt budget."""
    if not checked_log:
        return (
            False,
            "reset requires --i-checked-the-log (operator must confirm the spent "
            "attempts died on fixed flow bugs)",
        )
    if not (reason and reason.strip()):
        return (False, "reset requires a --reason")
    with _acquire_lock():
        conn = connect()
        init_db(conn)
        row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if row is None:
            conn.close()
            return (False, f"task {task_id} not found")
        task = dict(row)
        if task["status"] in TERMINAL_STATUSES:
            conn.close()
            return (False, f"task {task_id} is terminal: {task['status']}")
        payload = _payload_dict(task.get("payload"))
        inspect = _a1_inspect_ship_state_from_payload(task_id, payload)
        if inspect.get("state") != "clean_rollback":
            conn.close()
            return (
                False,
                f"reset requires live inspect state clean_rollback "
                f"(got {inspect.get('state')!r})",
            )
        payload["ship_attempt_count"] = 0
        payload["ship_attempts_reset_at"] = now_iso()
        payload["ship_attempts_reset_reason"] = reason.strip()
        if payload.get("terminal_reason") == "ship_attempt_limit_exceeded":
            payload.pop("terminal_reason", None)
            payload.pop("terminal_reason_at", None)
        _update_payload_locked(conn, task_id, payload, status=task["status"])
        conn.commit()
        conn.close()
    return (True, "")


def a1_register_strategy_revision(task_id: str) -> bool:
    """Bounded A2 strategy-revision loop (max 3), paralleling a1_register_revision.

    A revision re-runs Stage 9-10 from a fresh strategy spec: clears the strategy
    + backtest progress and resumes at `dispatch_strategy`. The fourth request
    leaves the flight parked `needs_human` with terminal_reason
    `strategy_revision_limit_exceeded`. Uses a SEPARATE `strategy_revision_count`
    so it never disturbs the thesis `revision_count`.
    """
    with _acquire_lock():
        conn = connect()
        init_db(conn)
        row = conn.execute("SELECT payload, status FROM tasks WHERE id=?", (task_id,)).fetchone()
        if row is None:
            conn.close()
            return False
        if row["status"] in TERMINAL_STATUSES:
            conn.close()
            return False
        if row["status"] not in {"needs_human", "blocked", "returned"}:
            conn.close()
            raise ValueError(f"cannot register strategy revision while task is {row['status']}")
        payload = _payload_dict(row["payload"])
        if not payload.get("thesis_approved"):
            conn.close()
            raise ValueError("cannot register a strategy revision before the thesis is approved")
        count = int(payload.get("strategy_revision_count") or 0) + 1
        payload["strategy_revision_count"] = count
        payload["strategy_spec_written"] = False
        payload["backtest_done"] = False
        for key in (
            "strategy_path",
            "strategy_artifact_verified",
            "strategy_artifact_verified_at",
            "strategy_artifact_sha256",
            "strategy_artifact_drift_detected_at",
            "strategy_artifact_drift_reason",
            "strategy_dispatch_started_at",
            "strategy_approved",
            "strategy_approved_at",
            "backtest_artifact_path",
            "backtest_artifact_sha256",
            "backtest_look_ahead_check",
            "backtest_done_at",
        ):
            payload.pop(key, None)
        if count > A1_MAX_REVISIONS:
            payload["terminal_reason"] = "strategy_revision_limit_exceeded"
            payload.setdefault("terminal_reason_at", now_iso())
            _update_payload_locked(
                conn,
                task_id,
                payload,
                status="needs_human",
                blocker_reason=(
                    "strategy revision limit exceeded; this flight keeps the entity lock. "
                    "Cancel it before starting a fresh A1 chain."
                ),
            )
            conn.commit()
            conn.close()
            return False
        changed = _update_payload_locked(conn, task_id, payload, status="needs_human")
        conn.commit()
        conn.close()
    return changed


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


def _cleared_a1_partial_worker_progress(payload: dict, reason: str) -> dict | None:
    a1_keys = {
        "thesis_written",
        "thesis_artifact_verified",
        "thesis_artifact_verified_at",
        "thesis_artifact_sha256",
        "thesis_artifact_drift_detected_at",
        "thesis_artifact_drift_reason",
        "bear_done",
        "bear_verdict",
        "bear_conviction",
        "screen_approved_by_operator",
        "screen_approved_at",
    }
    if not any(key in payload for key in a1_keys):
        return None
    cleared = dict(payload)
    cleared["thesis_written"] = False
    cleared["bear_done"] = False
    for key in (
        "thesis_artifact_verified",
        "thesis_artifact_verified_at",
        "thesis_artifact_sha256",
        "thesis_artifact_drift_detected_at",
        "thesis_artifact_drift_reason",
        "bear_verdict",
        "bear_conviction",
        "screen_approved_by_operator",
        "screen_approved_at",
    ):
        cleared.pop(key, None)
    cleared["worker_reclaim_reset_at"] = now_iso()
    cleared["worker_reclaim_reset_reason"] = reason
    return cleared


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
            SELECT id, worker_pid, payload, COALESCE(heartbeat_at, started_at) AS last_beat
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

        row_by_id = {row["id"]: row for row in rows}
        for task_id, worker_pid in stale_ids:
            stale_row = row_by_id.get(task_id)
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

            # Now flip back to ready. A confirmed-dead A1 worker may have died
            # after writing partial K2Bi files but before verification; clear the
            # optimistic thesis/bear flags so resume must re-run/verify.
            payload = _payload_dict(stale_row["payload"] if stale_row is not None else None)
            cleared_payload = _cleared_a1_partial_worker_progress(payload, reason)
            if cleared_payload is not None:
                conn.execute(
                    """
                    UPDATE tasks SET status='ready', worker_pid=NULL, started_at=NULL,
                    heartbeat_at=NULL, blocker_reason=?, payload=?, updated_at=? WHERE id=?
                    """,
                    (reason, json.dumps(cleared_payload, sort_keys=True), now_iso(), task_id),
                )
            else:
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
    TERMINAL_REASON_TTL_DAYS = int(os.environ.get("K2B_ORCH_TERMINAL_REASON_TTL_DAYS", "7"))
    # Agent-native Chat-2 theme work completes in one in-session pass, so an
    # abandoned waiting_for_agent_theme flight (agent crashed mid-build) should
    # clean up fast -- short TTL, default 2 days.
    AGENT_THEME_TTL_DAYS = int(os.environ.get("K2B_ORCH_AGENT_THEME_TTL_DAYS", "2"))
    now = datetime.now(timezone.utc)

    with _acquire_lock():
        conn = connect()
        init_db(conn)
        rows = conn.execute(
            """
            SELECT id, status, created_at, updated_at, payload FROM tasks
            WHERE status IN ('waiting_for_kimi_output', 'needs_human', 'waiting_for_agent_theme')
            """
        ).fetchall()
        for row in rows:
            terminal_reason = None
            if row["status"] == "needs_human":
                payload = _payload_dict(row["payload"])
                terminal_reason = payload.get("terminal_reason")
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
            if terminal_reason:
                terminal_dt = _parse_iso_dt(payload.get("terminal_reason_at"))
                if terminal_dt is not None:
                    updated_dt = min(terminal_dt, updated_dt, now)
                else:
                    created_dt = _parse_iso_dt(row["created_at"])
                    if (
                        created_dt is not None
                        and created_dt >= TERMINAL_TTL_CREATED_AT_FLOOR
                    ):
                        updated_dt = min(created_dt, updated_dt, now)
            age = now - updated_dt
            # >= timedelta, NOT age.days > N: age.days floors, so `> 14` would
            # not fire until ~15 days. Use exact-duration comparison.
            expired = (
                row["status"] == "waiting_for_kimi_output"
                and age >= timedelta(days=WAIT_TTL_DAYS)
            ) or (
                row["status"] == "needs_human"
                and terminal_reason
                and age >= timedelta(days=TERMINAL_REASON_TTL_DAYS)
            ) or (
                row["status"] == "needs_human"
                and not terminal_reason
                and age >= timedelta(days=NEEDS_HUMAN_TTL_DAYS)
            ) or (
                row["status"] == "waiting_for_agent_theme"
                and age >= timedelta(days=AGENT_THEME_TTL_DAYS)
            )
            if expired:
                n = now_iso()
                if terminal_reason:
                    payload = _payload_dict(row["payload"])
                    history = payload.get("terminal_history")
                    if not isinstance(history, list):
                        history = []
                    history.append({
                        "reason": terminal_reason,
                        "expired_at": n,
                        "task_id": row["id"],
                    })
                    payload["terminal_history"] = history
                    conn.execute(
                        "UPDATE tasks SET payload=? WHERE id=?",
                        (json.dumps(payload, sort_keys=True), row["id"]),
                    )
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
            # Dispatch-time parent-liveness: a child that joined a chain at INSERT
            # time must STILL have a live parent at CLAIM time. The insert-time
            # exemption proof is only point-in-time; if the parent chain was
            # cancelled / TTL-expired / terminalized between child creation and
            # dispatch, the queued child must NOT run -- otherwise it would write
            # K2Bi thesis/bear artifacts under a dead A1 chain (Codex Checkpoint-2
            # HIGH round-2, 2026-06-07). Re-validate inside this same flock'd
            # transaction, mirroring the add_task exemption check.
            parent_task_id = task.get("parent_task")
            if parent_task_id:
                terminal_clause, terminal_params = _terminal_status_not_in_clause("status")
                parent_row = conn.execute(
                    f"""
                    SELECT payload FROM tasks
                    WHERE id = ?
                      AND flight_id = ?
                      AND lower(trim(entity_key)) = lower(trim(?))
                      AND {terminal_clause}
                    LIMIT 1
                    """,
                    (parent_task_id, task["flight_id"], task["entity_key"] or "", *terminal_params),
                ).fetchone()
                if parent_row is None or _payload_is_logically_terminal(
                    parent_row["payload"]
                ):
                    # Orphaned child: its parent chain is dead (terminal / cancelled
                    # / TTL-expired / revision-limited). CANCEL it (terminal), not
                    # block: a dead-chain child can never become valid, and leaving
                    # it 'blocked' (non-terminal) would keep holding the entity lock
                    # and reject a fresh A1 chain for the ticker until manual
                    # cleanup (Codex Checkpoint-2 MEDIUM round-4, 2026-06-07).
                    # Terminalizing releases the lock so recovery is automatic.
                    conn.execute(
                        "UPDATE tasks SET status='cancelled', blocker_reason=?, "
                        "finished_at=?, updated_at=? WHERE id=? AND status='ready'",
                        (
                            "parent chain no longer live (terminal/cancelled/expired); "
                            "orphan child cancelled to release the entity lock",
                            now,
                            now,
                            task["id"],
                        ),
                    )
                    conn.commit()
                    conn.close()
                    notify(
                        f"[orchestrator] Task {task['id']} CANCELLED: parent chain no longer live"
                    )
                    continue
                # A2/A3 child dispatch must match the parent's resume action
                # (Checkpoint-2 #6): the chain lock only proves the parent is live,
                # not that it is at the right STAGE. A hand-queued strategy/backtest
                # or ship child must not run before the exact parent gate opens.
                # The resume oracle is authoritative and ALSO re-validates the
                # strategy artifact, so downstream children are refused if prior
                # evidence drifted. Cancel (terminal) an out-of-order child to
                # release the entity lock, mirroring the orphan path.
                stage_expected = {
                    "k2bi-write-strategy-spec": "dispatch_strategy",
                    "k2bi-run-backtest": "dispatch_backtest",
                    "k2bi-author-strategy-to-repo": "author_strategy_to_repo",
                    # The ship child is dispatched AFTER mark-ship-dispatch-started
                    # records the dispatch intent + mints the sha-bound token (the
                    # replay-guard anchor), which advances the oracle to verify_ship.
                    # So the in-flight ship child legitimately runs during verify_ship,
                    # NOT dispatch_ship (live-MVP finding, 2026-06-08). A double-ship is
                    # still barred by the sha-bound token (a committed file's sha no
                    # longer matches the token), the status:proposed ship gate, and the
                    # one-live-child chain lock.
                    "k2bi-run-full-ship": "verify_ship",
                    "k2bi-apply-limits": "verify_limits",
                }.get(task.get("command_key", ""))
                if stage_expected is not None:
                    try:
                        parent_action = a1_resume_action_locked(conn, parent_task_id)
                    except KeyError:
                        parent_action = None
                    if parent_action != stage_expected:
                        conn.execute(
                            "UPDATE tasks SET status='cancelled', blocker_reason=?, "
                            "finished_at=?, updated_at=? WHERE id=? AND status='ready'",
                            (
                                f"parent chain stage is {parent_action!r}, not "
                                f"{stage_expected!r}; chain child out of order, cancelled",
                                now,
                                now,
                                task["id"],
                            ),
                        )
                        conn.commit()
                        conn.close()
                        notify(
                            f"[orchestrator] Task {task['id']} CANCELLED: parent stage "
                            f"{parent_action!r} != {stage_expected!r}"
                        )
                        continue
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
    # --parent-task joins an existing chain: the new task is exempt from the
    # one-flight entity lock only when this resolves to a live, same-flight,
    # same-entity parent (a bare --flight reuse is treated as spoofed and locks).
    # Required to create A1 child dispatches (thesis/bear) from the CLI.
    add_p.add_argument("--parent-task", dest="parent_task")

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

    verify_p = sub.add_parser(
        "verify-theme",
        help="Gate an agent-written Chat-2 theme + complete the parked flight",
    )
    verify_p.add_argument("id")
    verify_p.add_argument("path", help="Path to the agent-written theme_<slug>.md")

    verify_thesis_p = sub.add_parser(
        "verify-thesis-artifact",
        help="Verify an A1 thesis artifact before bear-case dispatch",
    )
    verify_thesis_p.add_argument("id")
    verify_thesis_p.add_argument("path", nargs="?")

    force_verify_thesis_p = sub.add_parser(
        "force-verify-thesis-artifact",
        help="Recovery-only A1 thesis verification after worker death/log inspection",
    )
    force_verify_thesis_p.add_argument("id")
    force_verify_thesis_p.add_argument("path")
    force_verify_thesis_p.add_argument(
        "--i-checked-the-log",
        action="store_true",
        help="Required acknowledgement that the worker artifact/log was manually inspected",
    )

    clear_thesis_p = sub.add_parser(
        "clear-thesis-artifact",
        help="Explicitly clear invalid A1 thesis/bear progress after resume drift",
    )
    clear_thesis_p.add_argument("id")
    clear_thesis_p.add_argument("--reason")

    screen_done_p = sub.add_parser(
        "record-screen-done",
        help="Verify an A1 screen artifact and mark screen_done on the parent flight",
    )
    screen_done_p.add_argument("id")
    screen_done_p.add_argument("path")

    approve_screen_p = sub.add_parser(
        "approve-screen",
        help="Record operator approval to continue A1 after screen artifact review",
    )
    approve_screen_p.add_argument("id")

    register_revision_p = sub.add_parser(
        "register-revision",
        help="Increment the bounded A1 thesis revision counter",
    )
    register_revision_p.add_argument("id")

    reanchor_thesis_p = sub.add_parser(
        "reanchor-thesis-artifact",
        help="Operator-attested re-anchor of the thesis sha after a legit bear append",
    )
    reanchor_thesis_p.add_argument("id")
    reanchor_thesis_p.add_argument("path", nargs="?")
    reanchor_thesis_p.add_argument(
        "--i-checked-the-log",
        action="store_true",
        help="Required ack that the current file is the legitimate post-bear thesis artifact",
    )
    reanchor_thesis_p.add_argument("--reason")

    approve_thesis_p = sub.add_parser(
        "approve-thesis",
        help="Record operator approval at the A1 thesis gate (opens A2)",
    )
    approve_thesis_p.add_argument("id")

    record_strategy_p = sub.add_parser(
        "record-strategy-done",
        help="Verify an A2 strategy artifact and mark strategy_spec_written",
    )
    record_strategy_p.add_argument("id")
    record_strategy_p.add_argument("path")

    verify_strategy_p = sub.add_parser(
        "verify-strategy-artifact",
        help="Verify an A2 strategy artifact before backtest dispatch",
    )
    verify_strategy_p.add_argument("id")
    verify_strategy_p.add_argument("path", nargs="?")

    record_backtest_p = sub.add_parser(
        "record-backtest-done",
        help="Verify an A2 backtest capture and mark backtest_done",
    )
    record_backtest_p.add_argument("id")
    record_backtest_p.add_argument("path")
    record_backtest_p.add_argument("--look-ahead-check")

    approve_strategy_p = sub.add_parser(
        "approve-strategy",
        help="Record operator approval at the A2 strategy gate (end of A2)",
    )
    approve_strategy_p.add_argument("id")

    approve_ship_p = sub.add_parser(
        "approve-ship",
        help="Record operator approval at the A3 ship-to-engine gate",
    )
    approve_ship_p.add_argument("id")

    record_ship_repo_p = sub.add_parser(
        "record-ship-repo-authored",
        help="Verify and record the A3 repo-authored proposed strategy",
    )
    record_ship_repo_p.add_argument("id")
    record_ship_repo_p.add_argument("path")

    commit_ship_proposed_p = sub.add_parser(
        "commit-ship-repo-proposed",
        help="Commit the A3 repo-authored strategy as proposed",
    )
    commit_ship_proposed_p.add_argument("id")

    mark_ship_dispatch_p = sub.add_parser(
        "mark-ship-dispatch-started",
        help="Mint and record the A3 ship lease/token before dispatch",
    )
    mark_ship_dispatch_p.add_argument("id")

    verify_ship_p = sub.add_parser(
        "verify-ship",
        help="Verify the A3 ship commit and terminalize only when committed",
    )
    verify_ship_p.add_argument("id")

    record_ship_failed_p = sub.add_parser(
        "record-ship-failed",
        help="Record a failed A3 ship attempt using live rollback inspection",
    )
    record_ship_failed_p.add_argument("id")
    record_ship_failed_p.add_argument("--reason", required=True)

    retry_ship_p = sub.add_parser(
        "retry-ship",
        help="Clear a clean-rollback A3 failure for one bounded retry",
    )
    retry_ship_p.add_argument("id")

    reset_ship_attempts_p = sub.add_parser(
        "reset-ship-attempts",
        help="Operator-attested reset of the A3 ship attempt budget",
    )
    reset_ship_attempts_p.add_argument("id")
    reset_ship_attempts_p.add_argument(
        "--i-checked-the-log",
        action="store_true",
        help="Required ack that the spent attempts died on fixed flow bugs",
    )
    reset_ship_attempts_p.add_argument("--reason", required=True)

    inspect_ship_p = sub.add_parser(
        "inspect-ship-state",
        help="Inspect live A3 ship state from git/file/rollback marker evidence",
    )
    inspect_ship_p.add_argument("id")

    record_limits_p = sub.add_parser(
        "record-limits-proposal",
        help="Verify and record an A4 limits proposal before operator approval",
    )
    record_limits_p.add_argument("id")
    record_limits_p.add_argument("path")

    authorize_limits_p = sub.add_parser(
        "authorize-limits",
        help="Record operator approval at the A4 limits gate",
    )
    authorize_limits_p.add_argument("id")

    mark_limits_dispatch_p = sub.add_parser(
        "mark-limits-dispatch-started",
        help="Mint and record the A4 limits lease/token before dispatch",
    )
    mark_limits_dispatch_p.add_argument("id")

    verify_limits_p = sub.add_parser(
        "verify-limits",
        help="Verify the A4 limits commit and terminalize only when committed",
    )
    verify_limits_p.add_argument("id")

    record_limits_failed_p = sub.add_parser(
        "record-limits-failed",
        help="Record a failed A4 limits apply attempt using live rollback inspection",
    )
    record_limits_failed_p.add_argument("id")
    record_limits_failed_p.add_argument("--reason", required=True)

    retry_limits_p = sub.add_parser(
        "retry-limits",
        help="Clear a clean-rollback A4 failure for one bounded retry",
    )
    retry_limits_p.add_argument("id")

    inspect_limits_p = sub.add_parser(
        "inspect-limits-state",
        help="Inspect live A4 limits state from git/file/config/rollback evidence",
    )
    inspect_limits_p.add_argument("id")

    record_deploy_preview_p = sub.add_parser(
        "record-deploy-preview",
        help="Record the A5 deploy dry-run/manifest preview before approval",
    )
    record_deploy_preview_p.add_argument("id")
    record_deploy_preview_p.add_argument("path")

    authorize_deploy_p = sub.add_parser(
        "authorize-deploy",
        help="Mint the A5 approve-deploy token after preview review",
    )
    authorize_deploy_p.add_argument("id")

    mark_deploy_dispatch_p = sub.add_parser(
        "mark-deploy-dispatch-started",
        help="Record the A5 deploy dispatch attempt before worker launch",
    )
    mark_deploy_dispatch_p.add_argument("id")

    verify_deploy_p = sub.add_parser(
        "verify-deploy",
        help="Verify the A5 deploy state and terminalize only when deployed",
    )
    verify_deploy_p.add_argument("id")

    record_deploy_failed_p = sub.add_parser(
        "record-deploy-failed",
        help="Record a failed A5 deploy attempt using independent inspection",
    )
    record_deploy_failed_p.add_argument("id")
    record_deploy_failed_p.add_argument("--reason", required=True)

    defer_deploy_p = sub.add_parser(
        "defer-deploy",
        help="Write a durable pending-deploy marker without dispatching deploy",
    )
    defer_deploy_p.add_argument("id")
    defer_deploy_p.add_argument("--reason", default="operator_deferred")

    resume_deferred_deploy_p = sub.add_parser(
        "resume-deferred-deploy",
        help="Clear a valid A5 pending-deploy deferral back to the approval gate",
    )
    resume_deferred_deploy_p.add_argument("id")

    retry_deploy_p = sub.add_parser(
        "retry-deploy",
        help="Clear a clean-rollback A5 deploy failure for one bounded retry",
    )
    retry_deploy_p.add_argument("id")

    inspect_deploy_p = sub.add_parser(
        "inspect-deploy-state",
        help="Inspect recorded A5 deploy verification evidence",
    )
    inspect_deploy_p.add_argument("id")

    register_strategy_revision_p = sub.add_parser(
        "register-strategy-revision",
        help="Increment the bounded A2 strategy revision counter",
    )
    register_strategy_revision_p.add_argument("id")

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
                parent_task=args.parent_task,
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
        if t["status"] in TERMINAL_STATUSES:
            print(
                f"Cannot complete: task is {t['status']}; no longer in a completable state",
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
        # A parked agent-native Chat-2 flight must go through `verify-theme` (which
        # runs the >=5 + 2nd/3rd-order + citation-ledger gate), never `complete` --
        # otherwise the gate is bypassed and an unvalidated theme is marked done.
        if t["status"] == "waiting_for_agent_theme":
            print(
                f"Error: refusing to complete a 'waiting_for_agent_theme' task ({args.id}); "
                f"resolve it via 'verify-theme <id> <theme-path>' (which runs the candidate+citation gate), or 'cancel' to drop it",
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
            non_completable = NON_COMPLETABLE_STATUSES
            placeholders = ",".join("?" for _ in non_completable)
            cur = conn.execute(
                "UPDATE tasks SET status='done', finished_at=?, updated_at=?, result_url=? "
                f"WHERE id=? AND status NOT IN ({placeholders})",
                (now, now, args.result, args.id, *non_completable),
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

    if args.cmd == "verify-theme":
        ok, reason = verify_theme_complete(args.id, args.path)
        if ok:
            render_board()
            print(f"Task {args.id} verified + marked done. theme: {args.path}")
            return
        print(f"verify-theme rejected {args.id}: {reason}", file=sys.stderr)
        sys.exit(1)

    if args.cmd == "verify-thesis-artifact":
        ok, reason = a1_verify_thesis_artifact(args.id, args.path)
        if ok:
            render_board()
            print(f"Task {args.id} thesis artifact verified")
            return
        print(f"verify-thesis-artifact rejected {args.id}: {reason}", file=sys.stderr)
        sys.exit(1)

    if args.cmd == "force-verify-thesis-artifact":
        ok, reason = a1_force_verify_thesis_artifact(
            args.id,
            args.path,
            checked_log=args.i_checked_the_log,
        )
        if ok:
            render_board()
            print(f"Task {args.id} thesis artifact force-verified")
            return
        print(f"force-verify-thesis-artifact rejected {args.id}: {reason}", file=sys.stderr)
        sys.exit(1)

    if args.cmd == "clear-thesis-artifact":
        ok, reason = a1_clear_thesis_artifact(args.id, reason=args.reason)
        if ok:
            render_board()
            print(f"Task {args.id} thesis artifact cleared")
            return
        print(f"clear-thesis-artifact rejected {args.id}: {reason}", file=sys.stderr)
        sys.exit(1)

    if args.cmd == "record-screen-done":
        ok, reason = a1_record_screen_done(args.id, args.path)
        if ok:
            render_board()
            print(f"Task {args.id} screen artifact recorded")
            return
        print(f"record-screen-done rejected {args.id}: {reason}", file=sys.stderr)
        sys.exit(1)

    if args.cmd == "approve-screen":
        ok, reason = a1_approve_screen(args.id)
        if ok:
            render_board()
            print(f"Task {args.id} screen approved")
            return
        print(f"approve-screen rejected {args.id}: {reason}", file=sys.stderr)
        sys.exit(1)

    if args.cmd == "register-revision":
        ok = a1_register_revision(args.id)
        render_board()
        print(f"Task {args.id} revision registered: {ok}")
        return

    if args.cmd == "reanchor-thesis-artifact":
        ok, reason = a1_reanchor_thesis_artifact(
            args.id,
            args.path,
            checked_log=args.i_checked_the_log,
            reason=args.reason,
        )
        if ok:
            render_board()
            print(f"Task {args.id} thesis artifact re-anchored")
            return
        print(f"reanchor-thesis-artifact rejected {args.id}: {reason}", file=sys.stderr)
        sys.exit(1)

    if args.cmd == "approve-thesis":
        ok, reason = a1_approve_thesis(args.id)
        if ok:
            render_board()
            print(f"Task {args.id} thesis approved")
            return
        print(f"approve-thesis rejected {args.id}: {reason}", file=sys.stderr)
        sys.exit(1)

    if args.cmd == "record-strategy-done":
        ok, reason = a1_record_strategy_done(args.id, args.path)
        if ok:
            render_board()
            print(f"Task {args.id} strategy artifact recorded")
            return
        print(f"record-strategy-done rejected {args.id}: {reason}", file=sys.stderr)
        sys.exit(1)

    if args.cmd == "verify-strategy-artifact":
        ok, reason = a1_verify_strategy_artifact(args.id, args.path)
        if ok:
            render_board()
            print(f"Task {args.id} strategy artifact verified")
            return
        print(f"verify-strategy-artifact rejected {args.id}: {reason}", file=sys.stderr)
        sys.exit(1)

    if args.cmd == "record-backtest-done":
        ok, reason = a1_record_backtest_done(
            args.id, args.path, look_ahead_check=args.look_ahead_check
        )
        if ok:
            render_board()
            print(f"Task {args.id} backtest recorded")
            return
        print(f"record-backtest-done rejected {args.id}: {reason}", file=sys.stderr)
        sys.exit(1)

    if args.cmd == "approve-strategy":
        ok, reason = a1_approve_strategy(args.id)
        if ok:
            render_board()
            print(f"Task {args.id} strategy approved")
            return
        print(f"approve-strategy rejected {args.id}: {reason}", file=sys.stderr)
        sys.exit(1)

    if args.cmd == "approve-ship":
        ok, reason = a1_authorize_ship(args.id)
        if ok:
            render_board()
            print(f"Task {args.id} ship approved")
            return
        print(f"approve-ship rejected {args.id}: {reason}", file=sys.stderr)
        sys.exit(1)

    if args.cmd == "record-ship-repo-authored":
        ok, reason = a1_record_ship_repo_authored(args.id, args.path)
        if ok:
            render_board()
            print(f"Task {args.id} ship repo strategy recorded")
            return
        print(f"record-ship-repo-authored rejected {args.id}: {reason}", file=sys.stderr)
        sys.exit(1)

    if args.cmd == "commit-ship-repo-proposed":
        ok, reason = a1_commit_ship_repo_proposed(args.id)
        if ok:
            render_board()
            print(f"Task {args.id} proposed strategy committed")
            return
        print(f"commit-ship-repo-proposed rejected {args.id}: {reason}", file=sys.stderr)
        sys.exit(1)

    if args.cmd == "mark-ship-dispatch-started":
        ok, result = a1_mark_ship_dispatch_started(args.id)
        if ok:
            render_board()
            print(json.dumps(result, sort_keys=True))
            return
        print(f"mark-ship-dispatch-started rejected {args.id}: {result}", file=sys.stderr)
        sys.exit(1)

    if args.cmd == "verify-ship":
        ok, reason = a1_verify_ship(args.id)
        if ok:
            render_board()
            print(f"Task {args.id} ship verified")
            return
        print(f"verify-ship rejected {args.id}: {reason}", file=sys.stderr)
        sys.exit(1)

    if args.cmd == "record-ship-failed":
        ok, reason = a1_record_ship_failed(args.id, reason=args.reason)
        if ok:
            render_board()
            print(f"Task {args.id} ship failure recorded")
            return
        print(f"record-ship-failed rejected {args.id}: {reason}", file=sys.stderr)
        sys.exit(1)

    if args.cmd == "retry-ship":
        ok, reason = a1_retry_ship_after_rollback(args.id)
        if ok:
            render_board()
            print(f"Task {args.id} ship retry opened")
            return
        print(f"retry-ship rejected {args.id}: {reason}", file=sys.stderr)
        sys.exit(1)

    if args.cmd == "reset-ship-attempts":
        ok, reason = a1_reset_ship_attempts(
            args.id,
            checked_log=args.i_checked_the_log,
            reason=args.reason,
        )
        if ok:
            render_board()
            print(f"Task {args.id} ship attempt budget reset")
            return
        print(f"reset-ship-attempts rejected {args.id}: {reason}", file=sys.stderr)
        sys.exit(1)

    if args.cmd == "inspect-ship-state":
        print(json.dumps(a1_inspect_ship_state(args.id), indent=2, sort_keys=True))
        return

    if args.cmd == "record-limits-proposal":
        ok, reason = a4_record_limits_proposal(args.id, args.path)
        if ok:
            render_board()
            print(f"Task {args.id} limits proposal recorded")
            return
        print(f"record-limits-proposal rejected {args.id}: {reason}", file=sys.stderr)
        sys.exit(1)

    if args.cmd == "authorize-limits":
        ok, reason = a4_authorize_limits(args.id)
        if ok:
            render_board()
            print(f"Task {args.id} limits authorized")
            return
        print(f"authorize-limits rejected {args.id}: {reason}", file=sys.stderr)
        sys.exit(1)

    if args.cmd == "mark-limits-dispatch-started":
        ok, result = a4_mark_limits_dispatch_started(args.id)
        if ok:
            render_board()
            print(json.dumps(result, sort_keys=True))
            return
        print(f"mark-limits-dispatch-started rejected {args.id}: {result}", file=sys.stderr)
        sys.exit(1)

    if args.cmd == "verify-limits":
        ok, reason = a4_verify_limits(args.id)
        if ok:
            render_board()
            print(f"Task {args.id} limits verified")
            return
        print(f"verify-limits rejected {args.id}: {reason}", file=sys.stderr)
        sys.exit(1)

    if args.cmd == "record-limits-failed":
        ok, reason = a4_record_limits_failed(args.id, reason=args.reason)
        if ok:
            render_board()
            print(f"Task {args.id} limits failure recorded")
            return
        print(f"record-limits-failed rejected {args.id}: {reason}", file=sys.stderr)
        sys.exit(1)

    if args.cmd == "retry-limits":
        ok, reason = a4_retry_limits_after_rollback(args.id)
        if ok:
            render_board()
            print(f"Task {args.id} limits retry opened")
            return
        print(f"retry-limits rejected {args.id}: {reason}", file=sys.stderr)
        sys.exit(1)

    if args.cmd == "inspect-limits-state":
        print(json.dumps(a4_inspect_limits_state(args.id), indent=2, sort_keys=True))
        return

    if args.cmd == "record-deploy-preview":
        ok, reason = a5_record_deploy_preview(args.id, args.path)
        if ok:
            render_board()
            print(f"Task {args.id} deploy preview recorded")
            return
        print(f"record-deploy-preview rejected {args.id}: {reason}", file=sys.stderr)
        sys.exit(1)

    if args.cmd == "authorize-deploy":
        ok, reason = a5_authorize_deploy(args.id)
        if ok:
            render_board()
            print(f"Task {args.id} deploy authorized")
            return
        print(f"authorize-deploy rejected {args.id}: {reason}", file=sys.stderr)
        sys.exit(1)

    if args.cmd == "mark-deploy-dispatch-started":
        ok, result = a5_mark_deploy_dispatch_started(args.id)
        if ok:
            render_board()
            print(json.dumps(result, sort_keys=True))
            return
        print(f"mark-deploy-dispatch-started rejected {args.id}: {result}", file=sys.stderr)
        sys.exit(1)

    if args.cmd == "verify-deploy":
        ok, reason = a5_verify_deploy(args.id)
        if ok:
            render_board()
            print(f"Task {args.id} deploy verified")
            return
        print(f"verify-deploy rejected {args.id}: {reason}", file=sys.stderr)
        sys.exit(1)

    if args.cmd == "record-deploy-failed":
        ok, reason = a5_record_deploy_failed(args.id, reason=args.reason)
        if ok:
            render_board()
            print(f"Task {args.id} deploy failure recorded")
            return
        print(f"record-deploy-failed rejected {args.id}: {reason}", file=sys.stderr)
        sys.exit(1)

    if args.cmd == "defer-deploy":
        ok, result = a5_defer_deploy(args.id, reason=args.reason)
        if ok:
            render_board()
            print(result)
            return
        print(f"defer-deploy rejected {args.id}: {result}", file=sys.stderr)
        sys.exit(1)

    if args.cmd == "resume-deferred-deploy":
        ok, reason = a5_resume_deferred_deploy(args.id)
        if ok:
            render_board()
            print(f"Task {args.id} deploy deferral resumed")
            return
        print(f"resume-deferred-deploy rejected {args.id}: {reason}", file=sys.stderr)
        sys.exit(1)

    if args.cmd == "retry-deploy":
        ok, reason = a5_retry_deploy_after_rollback(args.id)
        if ok:
            render_board()
            print(f"Task {args.id} deploy retry opened")
            return
        print(f"retry-deploy rejected {args.id}: {reason}", file=sys.stderr)
        sys.exit(1)

    if args.cmd == "inspect-deploy-state":
        print(json.dumps(a5_inspect_deploy_state(args.id), indent=2, sort_keys=True))
        return

    if args.cmd == "register-strategy-revision":
        ok = a1_register_strategy_revision(args.id)
        render_board()
        print(f"Task {args.id} strategy revision registered: {ok}")
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
