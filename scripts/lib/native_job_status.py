"""Read-only observer for the native Codex automatic-memory jobs.

This module distinguishes three kinds of evidence that K2B previously
conflated:

- registration state (ACTIVE / PAUSED / missing / unknown) read from the
  exact per-host-role job TOML under the Codex automations root
  (keys: id, status, model, reasoning_effort, rrule);
- last finished run outcome (completed / failed / unknown) read from the
  bounded private run receipts under the host's automatic-memory state
  root (``native-run-receipts/*.json``), which record per-command semantic
  outcomes rather than a top-level status;
- operator hold state (present / absent / unknown) read from the private
  ``extraction-hold.json`` in the same state root.

ACTIVE registration is never reported as successful processing. Missing,
malformed, wrong-host or unsafe registration evidence is "unknown", never
falsely "disabled". A valid operator hold is not a failed scheduler run.
The newest run is chosen by validated receipt timestamps (finished, or
started for an unfinished attempt), never by filename or file-copy time;
when newer evidence cannot be validated, an older success is never
presented as current. ``started_at`` alone is never completion evidence.
Explicit failure signals (status, exit code or failure counts) count from
every command, including names outside the positive-terminal whitelist;
those names can never prove success on their own. ``parse_exception_counts``
is informational only: nonzero values never change a verdict, while
malformed count types invalidate the receipt. Schema-v1 ``exceptions``
accepts the existing string form plus bounded resolved/held object shapes
(free text validated, never returned), and ``reconciliation`` accepts its
bounded object form; explicit failure in either never surfaces as
completed. Only whitelisted fields are read; prompts, credentials,
receipts' free text and transcript data are never returned. All inputs are
inspected without modification: symlinks (including same-root and
dangling), non-regular files, oversize files and unreadable paths are
rejected rather than followed, opened or truncated; dangling job-directory,
receipt-directory or hold symlinks are unsafe unknown evidence, never
missing or absent.
"""
from __future__ import annotations

import hashlib
import json
import os
import stat
import tomllib
from datetime import datetime, timezone
from pathlib import Path

JOB_IDS = {
    "home": "k2b-automatic-memory-home",
    "sjm-source-only": "k2b-automatic-memory-sjm",
}
RUN_RECEIPT_DIR = "native-run-receipts"
HOME_WORKLIST_RECEIPT_SCHEMA = 2
HOME_WORKLIST_RECEIPT_REQUIRED_FROM = datetime(2026, 11, 25, 8, tzinfo=timezone.utc)
HOME_WORKLIST_SELECTION_LIMIT = 1
PUBLICATION_MOUNT_UNAVAILABLE = "publication_mount_unavailable"
PUBLICATION_MOUNT_PROBE_TIMEOUT = "publication_mount_probe_timed_out"
PUBLICATION_DUE_AGAIN = "publication_due_again"
HOME_PUBLICATION_WAIT_REASONS = {
    PUBLICATION_MOUNT_UNAVAILABLE: "Home publication waiting for the approved SMB binding",
    PUBLICATION_MOUNT_PROBE_TIMEOUT: "Home publication waiting after the SMB readiness probe timed out",
    PUBLICATION_DUE_AGAIN: "Home publication is due again and will retry in the next eligible run",
}
HOLD_FILE = "extraction-hold.json"
REGISTRATION_STATES = {"ACTIVE": "active", "PAUSED": "paused"}
REGISTRATION_CONFIG_KEYS = ("model", "reasoning_effort", "rrule")
RECEIPT_ID_KEYS = ("task_id", "native_task_id", "automation_id")
RECEIPT_TIMESTAMP_KEYS = ("started_at", "finished_at")
HOLD_TIMESTAMP_KEYS = ("created_at", "timestamp", "held_at", "recorded_at")
# Per-command statuses that are valid positive terminal outcomes. Unknown
# statuses are ambiguous (unknown outcome), never success. Known
# intermediate states (a ready worklist awaiting extraction) are real but
# not terminal: they cannot prove completion on their own.
COMMAND_OK_STATUSES = {
    # ready_with_exceptions is the deployed worklist shape when selection
    # succeeded with only informational parse exceptions; the failure/action
    # counts on the same command are still checked after the status.
    "memory_worklist": {"idle", "ready_with_exceptions"},
    "memory_record_extraction": {"queued", "reviewed_empty"},
    "memory_home_drain": {"reconciled", "idle"},
    "memory_publish": {"published", "not_attempted"},
}
COMMAND_NONTERMINAL_STATUSES = {"ready"}
COMMAND_FAILURE_STATUSES = {"failed"}
COMMAND_COUNT_FIELDS = ("local_failed", "sjm_failed", "failed")
EXCEPTION_COUNT_FIELDS = ("needs_attention", "retry_backoff", "exhausted")
# Bounded schema-v1 exception object shapes. Free-text fields (reason,
# work_id) are validated for shape and never returned.
EXCEPTION_RETRY_STATUSES = {"resolved_with_reviewed_empty"}
EXCEPTION_HOLD_TYPES = {
    "input_budget_exceeded", "item_count_exceeded", "quota_exhausted",
    "persistent_failure", "ambiguous_proposal_binding", "repeated_selection",
}
# Job-wide holds can occur before a turn is selected. Source-specific holds
# continue to require a work ID; status readers never expose these IDs.
HOLDS_WITH_OPTIONAL_WORK_ID = {"quota_exhausted", "persistent_failure", "repeated_selection"}
WORKLIST_STOP_OUTCOMES = {"blocked", "needs_attention", "waiting_retry"}
MAX_EXCEPTION_ITEMS = 32
MAX_READ_BYTES = 64 * 1024

UNKNOWN = "unknown"


def _default_automation_root() -> Path:
    return Path.home() / ".codex" / "automations"


# --- safe reads: no symlink following, no non-regular opens, no truncation ---


def _lstat(path: Path) -> os.stat_result | None:
    try:
        return os.lstat(path)
    except FileNotFoundError:
        return None


def _path_within(path: Path, root: Path) -> bool:
    try:
        return os.path.commonpath((str(path), str(root))) == str(root)
    except ValueError:
        return False


def _safe_stat(
    path: Path, root: Path, *, leaf_regular: bool = True
) -> tuple[str, os.stat_result | None]:
    """Inspect path and its parents up to (excluding) root without following
    links. Returns ("ok", st) or one of ("unsafe-symlink", "non-regular",
    "outside-root", "unreadable", None)."""
    path = Path(path)
    root = Path(root)
    if not _path_within(path, root) or path == root:
        return "outside-root", None
    current = path
    while True:
        try:
            st = _lstat(current)
        except OSError:
            return "unreadable", None
        if st is None:
            return "unreadable", None
        mode = st.st_mode
        if stat.S_ISLNK(mode):
            return "unsafe-symlink", None
        if current == path:
            if leaf_regular and not stat.S_ISREG(mode):
                return "non-regular", None
            if not leaf_regular and not stat.S_ISDIR(mode):
                return "non-regular", None
        elif not stat.S_ISDIR(mode):
            return "non-regular", None
        if current.parent == root:
            return "ok", st
        current = current.parent


def _safe_read(
    path: Path, root: Path, *, max_bytes: int = MAX_READ_BYTES,
) -> tuple[str, bytes | None]:
    """Read a regular file without following links; reject oversize input
    instead of truncating it into an apparently valid prefix."""
    status, st = _safe_stat(path, root)
    if status != "ok" or st is None:
        return status, None
    if st.st_size > max_bytes:
        return "oversize", None
    try:
        with open(path, "rb") as handle:
            data = handle.read(max_bytes + 1)
    except OSError:
        return "unreadable", None
    if len(data) > max_bytes:
        return "oversize", None
    return "ok", data


def _safe_dir(path: Path, root: Path) -> tuple[str, os.stat_result | None]:
    return _safe_stat(path, root, leaf_regular=False)


# --- registration ---


def _read_registration(
    job_id: str, automation_root: Path
) -> tuple[str, dict, str | None]:
    """Return (registration_state, config fields, diagnostic)."""
    job_dir = automation_root / job_id
    toml_path = job_dir / "automation.toml"
    # lstat before existence checks: a dangling or valid job-directory
    # symlink is unsafe unknown evidence, never a missing job; neither is a
    # regular file parked at the directory path.
    try:
        job_stat = _lstat(job_dir)
    except OSError:
        return UNKNOWN, {}, "registration path unreadable"
    if job_stat is None:
        return "missing", {}, None
    if stat.S_ISLNK(job_stat.st_mode):
        return UNKNOWN, {}, "registration job directory is a symlink"
    if not stat.S_ISDIR(job_stat.st_mode):
        return UNKNOWN, {}, "registration job directory is not a directory"
    try:
        toml_stat = _lstat(toml_path)
    except OSError:
        return UNKNOWN, {}, "registration TOML unreadable"
    if toml_stat is None:
        # A dangling symlink is unknown evidence, not a missing job.
        return "missing", {}, None

    status, raw = _safe_read(toml_path, automation_root)
    if status == "unsafe-symlink":
        return UNKNOWN, {}, "registration TOML is a symlink"
    if status == "non-regular":
        return UNKNOWN, {}, "registration TOML is not a regular file"
    if status == "oversize":
        return UNKNOWN, {}, "registration TOML exceeds safe read size"
    if status != "ok" or raw is None:
        return UNKNOWN, {}, "registration TOML unreadable"

    try:
        data = tomllib.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError):
        return UNKNOWN, {}, "registration TOML is malformed"
    if not isinstance(data, dict):
        return UNKNOWN, {}, "registration TOML is malformed"

    registered_id = data.get("id")
    if not isinstance(registered_id, str) or not registered_id:
        return UNKNOWN, {}, "registration id missing"
    if registered_id != job_id:
        return UNKNOWN, {}, "registration id does not match this host role"

    registration_state = (
        REGISTRATION_STATES.get(data.get("status"))
        if isinstance(data.get("status"), str)
        else None
    )
    if registration_state is None:
        return UNKNOWN, {}, "registration status unrecognized"

    config = {}
    for key in REGISTRATION_CONFIG_KEYS:
        value = data.get(key)
        config[key] = value if isinstance(value, str) else None
    return registration_state, config, None


# --- receipts ---


def _parse_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed


def _validate_counts(command: dict, fields: tuple[str, ...]) -> str | None:
    """Return "failed" if any count field is a nonzero int, None if all clean,
    "invalid" on wrong types."""
    for field in fields:
        value = command.get(field)
        if value is None:
            continue
        if type(value) is not int:
            return "invalid"
        if value != 0:
            return "failed"
    return None


def _evaluate_command(name: str, command: object) -> str:
    """Return "ok", "failed", "ambiguous" or "invalid" for one command map."""
    if not isinstance(command, dict):
        return "invalid"
    status = command.get("status")
    if not isinstance(status, str):
        return "invalid"
    if status in COMMAND_OK_STATUSES.get(name, set()):
        verdict = "ok"
    elif status in COMMAND_FAILURE_STATUSES:
        verdict = "failed"
    else:
        verdict = "ambiguous"

    exit_code = command.get("exit_code")
    if exit_code is not None:
        if type(exit_code) is not int:
            return "invalid"
        if exit_code != 0:
            verdict = "failed"

    counts = _validate_counts(command, COMMAND_COUNT_FIELDS)
    if counts == "invalid":
        return "invalid"
    if counts == "failed":
        verdict = "failed"

    exception_counts = command.get("exception_counts")
    if exception_counts is not None:
        if not isinstance(exception_counts, dict):
            return "invalid"
        nested = _validate_counts(exception_counts, EXCEPTION_COUNT_FIELDS)
        if nested == "invalid":
            return "invalid"
        if nested == "failed":
            verdict = "failed"

    parse_counts = command.get("parse_exception_counts")
    if parse_counts is not None:
        if not isinstance(parse_counts, dict):
            return "invalid"
        for value in parse_counts.values():
            if type(value) is not int:
                return "invalid"
        # Informational only: a nonzero parse exception count (e.g. a
        # completed source skipped for a turn-id mismatch) is not extraction
        # failure and never changes the verdict either way.
    return verdict


def _command_failure_evidence(command: object) -> bool:
    """Explicit failure signals from a command outside the positive-terminal
    whitelist. Such commands can never prove success, but a failure status,
    nonzero exit code or nonzero failure count must never be discarded
    merely because the name is unknown. Benign or malformed optional
    metadata is ignored so unused fields cannot cause false failures."""
    if not isinstance(command, dict):
        return False
    status = command.get("status")
    if isinstance(status, str) and status in COMMAND_FAILURE_STATUSES:
        return True
    exit_code = command.get("exit_code")
    if type(exit_code) is int and exit_code != 0:
        return True
    if _validate_counts(command, COMMAND_COUNT_FIELDS) == "failed":
        return True
    exception_counts = command.get("exception_counts")
    if isinstance(exception_counts, dict):
        if _validate_counts(exception_counts, EXCEPTION_COUNT_FIELDS) == "failed":
            return True
    return False


def _valid_home_publication(publication: object) -> bool:
    """Home publication metadata is a bounded dict, never raw text. The
    status is required and must name a known outcome of the publication
    family (published / not_attempted / failed); success never depends on
    optional snapshot fields, and optional metadata is type-checked when
    present so unusable metadata cannot fabricate success."""
    if not isinstance(publication, dict):
        return False
    status = publication.get("status")
    if not isinstance(status, str) or not status:
        return False
    if status not in ("published", "not_attempted", "failed"):
        return False
    published_at = publication.get("published_at")
    if published_at is not None and _parse_timestamp(published_at) is None:
        return False
    snapshot_sha256 = publication.get("snapshot_sha256")
    if snapshot_sha256 is not None and not isinstance(snapshot_sha256, str):
        return False
    return True


def _classify_exception(item: object) -> str | None:
    """Classify one schema-v1 exception entry.

    Returns "info" (existing free-text string form), "resolved" (a retry
    exception whose status names a known resolved outcome) or "action" (a
    held/unresolved exception, or any entry flagged operator_action_required).
    None means the entry is not a supported shape. Free-text fields are
    validated for shape and length but never returned.
    """
    if isinstance(item, str):
        return "info"
    if not isinstance(item, dict):
        return None
    action_required = item.get("operator_action_required", False)
    if type(action_required) is not bool:
        return None
    keys = set(item)
    # Two informational shapes emitted by the deployed native workers.
    # Match these narrowly; arbitrary failure objects must remain unknown.
    if keys == {"type", "count", "state", "actionable"}:
        if (item["type"] != "task_complete_turn_id_mismatch"
            or type(item["count"]) is not int or item["count"] < 0
            or item["state"] != "unchanged_from_prior_run"
            or type(item["actionable"]) is not bool):
            return None
        return "action" if item["actionable"] else "info"
    if keys == {"stage", "status", "details"}:
        details = item["details"]
        if (item["stage"] != "memory_worklist"
            or item["status"] != "non_blocking_parse_exceptions"
            or not isinstance(details, dict)
            or set(details) != {"task_complete_turn_id_mismatch"}
            or type(details["task_complete_turn_id_mismatch"]) is not int
            or details["task_complete_turn_id_mismatch"] < 0):
            return None
        return "info"
    if "reason" in keys and keys <= {"reason", "status", "operator_action_required"}:
        reason = item.get("reason")
        if not isinstance(reason, str) or not reason:
            return None
        if item.get("status") not in EXCEPTION_RETRY_STATUSES:
            return None
        return "action" if action_required else "resolved"
    if "type" in keys and keys <= {"type", "work_id", "operator_action_required"}:
        if item.get("type") not in EXCEPTION_HOLD_TYPES:
            return None
        work_id = item.get("work_id")
        if work_id is None and item["type"] in HOLDS_WITH_OPTIONAL_WORK_ID:
            # None rejects the whole exception as malformed. It cannot be
            # ignored and accidentally let a run grade completed.
            return "action" if action_required else None
        if not isinstance(work_id, str) or not work_id:
            return None
        # A held receipt exception is unresolved evidence even without an
        # explicit action flag.
        return "action"
    return None


def _classify_receipt(
    data: object, *, job_id: str, writer_role: str, state_root: Path,
) -> tuple[datetime | None, datetime | None, str | None, str | None]:
    """Classify one decoded receipt.

    Returns (attempt_at, finished_at, outcome, diagnostic). attempt_at is
    the validated finished timestamp, or the started timestamp for an
    unfinished attempt; None means the record supplies no usable ordering
    evidence. outcome None means the record is invalid and must never
    fabricate success. finished_at is only set for completed runs; a failed
    or ambiguous latest attempt never surfaces the timestamp of an older
    success as if current.
    """
    if not isinstance(data, dict):
        return None, None, None, "run receipt is malformed"
    schema = data.get("schema_version")
    legacy_schema = schema == "1.0"
    if legacy_schema:
        schema = 1  # Read-only compatibility; never rewrite historical evidence.
    requires_verified_worklist = writer_role == "home" and schema == HOME_WORKLIST_RECEIPT_SCHEMA
    if type(schema) is not int or (schema != 1 and not requires_verified_worklist):
        return None, None, None, "run receipt schema is unsupported"

    identity = [data.get(key) for key in RECEIPT_ID_KEYS if data.get(key) is not None]
    if (
        len(identity) != 1
        or not isinstance(identity[0], str)
        or identity[0] != job_id
        or data.get("writer_role") != writer_role
        or JOB_IDS.get(writer_role) != job_id
    ):
        return None, None, None, "run receipt identity does not match this host role"

    if "commands" in data and "command_outcomes" in data:
        return None, None, None, "run receipt mixes command outcome shapes"
    commands = data.get("commands") if "commands" in data else data.get("command_outcomes")
    if commands is None or not isinstance(commands, dict):
        return None, None, None, "run receipt command outcomes are malformed"

    started = _parse_timestamp(data.get("started_at"))
    finished = _parse_timestamp(data.get("finished_at"))
    if data.get("started_at") is not None and started is None:
        return None, None, None, "run receipt has no valid started timestamp"
    if finished is not None and started is not None and finished < started:
        return None, None, None, "run receipt timestamps are out of order"
    attempt_at = finished if finished is not None else started
    if finished is None:
        # started_at alone is never completion evidence.
        return attempt_at, None, UNKNOWN, "run receipt has no valid finished timestamp"

    diagnostics: list[str] = []
    if legacy_schema:
        diagnostics.append("legacy string schema normalized for reading")
    if writer_role != "home" and "memory_home_drain" in commands:
        return None, None, None, "only a Home run can record the Home drain command"
    drain = commands.get("memory_home_drain")
    sjm_pull = commands.get("memory_sjm_pull")
    explicit_local_count = isinstance(drain, dict) and "local_failed" in drain
    local_failed = drain["local_failed"] if explicit_local_count else None
    if explicit_local_count and (type(local_failed) is not int or local_failed < 0):
        return None, None, None, "run receipt Home delivery counts are malformed"
    has_sjm_pull = "memory_sjm_pull" in commands
    # Legacy receipts can use local_failed alone. Both counters without a
    # separate pull still prove the local outcome, but cannot attest SJM.
    missing_sjm = explicit_local_count and "failed" in drain and not has_sjm_pull
    if missing_sjm:
        diagnostics.append("SJM transfer was not recorded")
    if local_failed:
        diagnostics.append("Home drain reported local delivery failures")
    graded_drain = {**drain, "failed": local_failed} if explicit_local_count else drain
    if has_sjm_pull:
        if writer_role != "home":
            return None, None, None, "only a Home run can record the SJM pull command"
        if (not isinstance(drain, dict) or not isinstance(sjm_pull, dict)
                or not {"status", "failed", "offline"} <= sjm_pull.keys()
                or any(type(sjm_pull[key]) is not int or sjm_pull[key] < 0
                       for key in ("failed", "offline"))):
            return None, None, None, "run receipt SJM transfer is malformed"
        # Only the writer that explicitly supplies both counts attests
        # that all three values describe the same drain invocation.
        if explicit_local_count:
            failed = drain.get("failed")
            if type(failed) is not int or failed != local_failed + sjm_pull["failed"]:
                return None, None, None, "run receipt Home delivery counts conflict"
        if sjm_pull["failed"] and sjm_pull["offline"]:
            # A pull may report a connection wait alongside known errors.
            # The failed count still takes priority when grading the run.
            expected_statuses = ("failed", "waiting")
        elif sjm_pull["failed"]:
            expected_statuses = ("failed",)
        elif sjm_pull["offline"]:
            expected_statuses = ("waiting",)
        else:
            expected_statuses = ("completed", "not_attempted", UNKNOWN)
        if not isinstance(sjm_pull["status"], str) or sjm_pull["status"] not in expected_statuses:
            return None, None, None, "run receipt SJM transfer status conflicts with its counts"
    drain_status = drain.get("status") if isinstance(drain, dict) else None
    conflicts = drain.get("reconciliation_conflicts", 0) if isinstance(drain, dict) else 0
    remaining = None
    if has_sjm_pull and explicit_local_count and not {"reconciliation_conflicts", "unreconciled_remaining"} <= drain.keys():
        return None, None, None, "run receipt is missing Home reconciliation counts"
    if (type(conflicts) is not int or conflicts < 0
            or (conflicts and drain_status != "needs_attention")
            or (has_sjm_pull and drain_status == "needs_attention" and not conflicts)):
        return None, None, None, "run receipt reconciliation conflict evidence is malformed"
    if isinstance(drain, dict) and "unreconciled_remaining" in drain:
        remaining = drain["unreconciled_remaining"]
        if type(remaining) is not int or remaining < conflicts:
            return None, None, None, "run receipt reconciliation backlog evidence is malformed"
    publication_command = commands.get("memory_publish")
    publication_reason = publication_command.get("reason") if isinstance(publication_command, dict) else None
    publication_waiting = (writer_role == "home" and isinstance(publication_reason, str)
                           and publication_reason in HOME_PUBLICATION_WAIT_REASONS)
    if publication_waiting and (
        publication_command.get("status") != "not_attempted"
        or remaining != 0 or drain_status not in ("idle", "reconciled")
    ):
        return None, None, None, "run receipt publication wait conflicts with command outcomes"
    if publication_waiting:
        diagnostics.append(HOME_PUBLICATION_WAIT_REASONS[publication_reason])
    if writer_role == "home" and not conflicts and not remaining and publication_reason in (
        "reconciliation_needs_attention", "unreconciled_backlog_remaining",
    ):
        return None, None, None, "run receipt publication deferral has no verified backlog"
    if conflicts or remaining:
        # "reconciled" can describe progress in a bounded drain. Pending
        # deliveries still prevent publication and leave the run waiting.
        deferral = "reconciliation_needs_attention" if conflicts else "unreconciled_backlog_remaining"
        if (not isinstance(publication_command, dict)
                or publication_command.get("status") not in ("failed", "not_attempted")
                or (publication_command.get("status") == "not_attempted"
                    and publication_reason != deferral)):
            return None, None, None, "run receipt publication conflicts with unresolved reconciliation"
    saw_terminal = False
    verdict = "ok"
    for name, command in commands.items():
        if name == "memory_sjm_pull":
            # Validated above and graded separately below. Offline must not
            # make an otherwise completed Home command sequence ambiguous.
            continue
        # The aggregate keeps old readers compatible. Grade Home using the
        # validated local count before evaluating either command shape.
        if name == "memory_home_drain" and writer_role == "home":
            command = graded_drain
        if name not in COMMAND_OK_STATUSES:
            # Unknown commands cannot prove success, but explicit failure
            # evidence on them still fails the run.
            if _command_failure_evidence(command):
                verdict = "failed"
            continue
        result = _evaluate_command(name, command)
        if result == "invalid":
            return None, None, None, "run receipt command outcome is malformed"
        if result == "failed":
            verdict = "failed"
        elif result == "ok":
            saw_terminal = True
        elif verdict != "failed":
            status = command.get("status") if isinstance(command, dict) else None
            explicitly_stopped = (
                (name == "memory_worklist" and status in WORKLIST_STOP_OUTCOMES)
                or (name == "memory_home_drain" and writer_role == "home" and status == "needs_attention")
            )
            if status not in COMMAND_NONTERMINAL_STATUSES and not explicitly_stopped:
                verdict = "ambiguous"
    worklist = commands.get("memory_worklist")
    extraction = commands.get("memory_record_extraction")
    if isinstance(worklist, dict):
        selected = worklist.get("selected")
        if (requires_verified_worklist and "evidence_verified" in worklist
                and type(worklist["evidence_verified"]) is not bool):
            return None, None, None, "run receipt worklist verification is malformed"
        if requires_verified_worklist and "evidence_problem" in worklist:
            problem = worklist["evidence_problem"]
            problem_diagnostics = {
                "missing": "Home producer worklist evidence is missing",
                "invalid": "Home retained worklist evidence is unreadable or outside this run",
                "unreadable": "Home retained worklist evidence could not be read safely",
                "foreign": "Home retained worklist evidence belongs to an overlapping run",
                "clock_skew": "Home worklist timestamp is ahead of the local receipt clock",
                "mismatch": "Home supplied worklist differs from the retained result",
                "hold_conflict": "Home worklist reported a hold after the retained selection",
                "bookkeeping_failed": "Home run bookkeeping failed before it could record worklist evidence",
            }
            if (not isinstance(problem, str) or problem not in problem_diagnostics
                    or worklist.get("evidence_verified") is not False):
                return None, None, None, "run receipt worklist verification problem is malformed"
            if problem == "missing" and data.get("hold_check") == "present":
                diagnostics.append("Home producer worklist evidence was not recorded during the hold")
            else:
                diagnostics.append(problem_diagnostics[problem])
        if "selected" in worklist and (type(selected) is not int or selected < 0):
            return None, None, None, "run receipt selected count is malformed"
        needs_extraction = (isinstance(selected, int) and selected > 0) or worklist.get("status") in {"ready", "ready_with_exceptions"}
        if needs_extraction and (not isinstance(extraction, dict) or _evaluate_command("memory_record_extraction", extraction) != "ok") and verdict != "failed":
            verdict = "ambiguous"
        if isinstance(selected, int) and selected > 1 and verdict != "failed":
            completed = extraction.get("completed") if isinstance(extraction, dict) else None
            if type(completed) is not int or completed != selected:
                verdict = "ambiguous"
    # Batch-boundary counters are optional, but a malformed counter can never
    # claim completion. Prepared-but-unaccounted work is ambiguous evidence,
    # never a manufactured extraction failure.
    if isinstance(extraction, dict):
        for field in ("deferred", "unattributed_pending"):
            if field not in extraction:
                continue
            value = extraction[field]
            if type(value) is not int or value < 0:
                return None, None, None, "run receipt batch counters are malformed"
            if field == "unattributed_pending" and value > 0 and verdict != "failed":
                verdict = "ambiguous"
    if "batch_budget" in commands:
        budget_command = commands["batch_budget"]
        if (not isinstance(budget_command, dict)
                or budget_command.get("status") not in ("bounded", "exceeded")):
            return None, None, None, "run receipt batch budget is malformed"
        if budget_command["status"] == "exceeded" and verdict != "failed":
            verdict = "ambiguous"
            diagnostics.append("run receipt batch budget was exceeded")
    if verdict == "failed":
        outcome = "failed"
    elif not saw_terminal or verdict == "ambiguous":
        outcome = UNKNOWN
    else:
        outcome = "completed"

    exceptions = data.get("exceptions", [])
    if not isinstance(exceptions, list) or len(exceptions) > MAX_EXCEPTION_ITEMS:
        return None, None, None, "run receipt exceptions are malformed"
    saw_exception = False
    saw_resolved_exception = False
    saw_action_exception = False
    for item in exceptions:
        kind = _classify_exception(item)
        if kind is None:
            return None, None, None, "run receipt exceptions are malformed"
        saw_exception = True
        if kind == "resolved":
            saw_resolved_exception = True
        elif kind == "action":
            saw_action_exception = True
    if saw_exception:
        diagnostics.append("run reported exceptions (sanitized)")
    if saw_resolved_exception:
        diagnostics.append("run reported exceptions resolved after retry")
    if saw_action_exception:
        diagnostics.append("run reported exceptions requiring operator action")

    user_action_required = data.get("user_action_required", False)
    if type(user_action_required) is not bool:
        return None, None, None, "run receipt user_action_required is malformed"
    if user_action_required:
        diagnostics.append("run reported user action required")

    # SJM-side bounded metadata: validated for type, never returned.
    for key in ("hold_created",):
        value = data.get(key)
        if value is not None and type(value) is not bool:
            return None, None, None, f"run receipt {key} is malformed"
        if value is True:
            diagnostics.append("run created an operator hold")

    # Reconciliation metadata is the existing bounded string (e.g. SJM's
    # "not_run_sjm") or a bounded object from the reconciliation outcome
    # family; explicit failure must never surface as completed. An explicit
    # null is rejected conservatively; only a missing key means absence.
    if "reconciliation" in data:
        reconciliation = data["reconciliation"]
        if isinstance(reconciliation, dict):
            if set(reconciliation) - {"status", "new", "replayed"}:
                return None, None, None, "run receipt reconciliation is malformed"
            if reconciliation.get("status") not in ("reconciled", "failed"):
                return None, None, None, "run receipt reconciliation is malformed"
            for field in ("new", "replayed"):
                if field in reconciliation and type(reconciliation[field]) is not int:
                    return None, None, None, "run receipt reconciliation is malformed"
            if reconciliation.get("status") == "failed" and outcome != "failed":
                outcome = "failed"
                diagnostics.append("run receipt reconciliation failed")
        elif reconciliation == "failed":
            outcome = "failed"
            diagnostics.append("run receipt reconciliation failed")
        elif reconciliation != ("not_run_sjm" if writer_role == "sjm-source-only" else "reconciled"):
            return None, None, None, "run receipt reconciliation is malformed"

    # Role-specific publication metadata: SJM records the bounded string
    # "not_run_sjm"; Home records a bounded object from the publication
    # outcome family. Both are validated for shape, never returned.
    if "publication" in data:
        publication = data["publication"]
        if writer_role == "sjm-source-only":
            if publication != "not_run_sjm":
                return None, None, None, "run receipt publication is malformed"
        else:
            if not _valid_home_publication(publication):
                return None, None, None, "run receipt publication is malformed"
            publication_status = publication.get("status")
            if (
                outcome == "completed"
                and publication_status in ("published", "not_attempted")
            ):
                publish_command = commands.get("memory_publish")
                publish_status = (
                    publish_command.get("status")
                    if isinstance(publish_command, dict)
                    else None
                )
                if (
                    isinstance(publish_status, str)
                    and publish_status in ("published", "not_attempted")
                    and publication_status != publish_status
                ):
                    outcome = UNKNOWN
                    diagnostics.append(
                        "run receipt publication conflicts with command outcomes"
                    )
            if publication_status == "failed" and outcome != "failed":
                # Explicit publication failure must never surface as
                # completed, whatever the command outcomes claimed.
                outcome = "failed"
                diagnostics.append("run receipt publication failed")

    if sjm_pull is not None:
        if sjm_pull["failed"]:
            diagnostics.append("SJM transfer failed")
            outcome = "failed"
        if sjm_pull["offline"]:
            diagnostics.append("SJM transfer waiting for a connection")
        if sjm_pull["status"] == UNKNOWN:
            diagnostics.append("SJM transfer attempt could not be verified")

    worklist_status = worklist.get("status") if isinstance(worklist, dict) else None
    worklist_stopped = isinstance(worklist_status, str) and worklist_status in WORKLIST_STOP_OUTCOMES
    hold_check = data.get("hold_check")
    if "hold_check" not in data:
        # Only the split writer guarantees a hold check. Keep already
        # verified historical successes from older receipt shapes readable.
        if has_sjm_pull:
            diagnostics.append("split run receipt is missing its extraction hold observation")
            hold_check = UNKNOWN
    elif not isinstance(hold_check, str) or hold_check not in ("absent", "present", UNKNOWN):
        diagnostics.append("run receipt hold_check value is malformed")
        hold_check = UNKNOWN
    if hold_check == "present":
        diagnostics.append("extraction held")
    if ((hold_check == "present" or missing_sjm) and drain_status == "reconciled"
            and not _command_failure_evidence(graded_drain)):
        diagnostics.append("Home reconciliation completed")
    if hold_check == "present":
        if (writer_role == "home" and isinstance(publication_command, dict)
                and publication_command.get("status") == "published"
                and not _command_failure_evidence(publication_command)):
            diagnostics.append("Home publication completed")
    idle_backlog_unverified = drain_status == "idle" and remaining is None
    if idle_backlog_unverified:
        diagnostics.append("idle Home drain did not verify its remaining backlog")
    drain_needs_attention = drain_status == "needs_attention"
    if drain_needs_attention:
        diagnostics.append(
            "Home reconciliation is waiting for operator resolution" if conflicts
            else "Home reconciliation needs attention; conflict count was not reported"
        )
    if outcome != "failed":
        if worklist_stopped and outcome in (UNKNOWN, "completed"):
            # Keep the exact known extraction stop. Home conflicts are
            # reported independently in the operator diagnostic above.
            outcome = worklist_status
        elif drain_needs_attention:
            outcome = "needs_attention"
        elif hold_check == "present" and outcome in (UNKNOWN, "completed"):
            outcome = "blocked"
        elif outcome == "completed" and sjm_pull is not None and sjm_pull["status"] == UNKNOWN:
            outcome = "needs_attention"
        elif outcome == "completed" and (
            isinstance(sjm_pull, dict) and sjm_pull["status"] == "waiting"
            or publication_reason == "unreconciled_backlog_remaining" or publication_waiting
        ):
            outcome = "waiting"
        elif outcome in (UNKNOWN, "completed") and (idle_backlog_unverified or missing_sjm):
            outcome = "needs_attention"
    if hold_check == UNKNOWN:
        # An unverifiable hold requires attention even when other evidence
        # explains why this batch stopped.
        if outcome != "failed":
            outcome = "needs_attention"
        diagnostics.append("extraction hold could not be verified")
    if worklist_status == "blocked" and hold_check == "absent":
        diagnostics.append("extraction hold changed after the worklist check")
    observed_status = worklist.get("observed_status") if isinstance(worklist, dict) else None
    if requires_verified_worklist and observed_status == "ready_with_exceptions":
        diagnostics.append("Home worklist selected a turn and reported other worklist exceptions")
    observed_selected = worklist.get("observed_selected") if isinstance(worklist, dict) else None
    batch_selected = worklist.get("selected") if isinstance(worklist, dict) else None
    worklist_verified = (
        isinstance(worklist, dict) and worklist.get("evidence_verified") is True
        and observed_status in ("idle", "ready", "ready_with_exceptions")
        and type(observed_selected) is int
        and observed_selected == (HOME_WORKLIST_SELECTION_LIMIT
                                  if observed_status in ("ready", "ready_with_exceptions") else 0)
        and type(batch_selected) is int
        and (worklist_status in ("ready", "ready_with_exceptions") if batch_selected
             else worklist_status == "idle")
        and (batch_selected > 0 or observed_selected == 0
             or isinstance(extraction, dict) and extraction.get("deferred") == 1)
        and hold_check == "absent"
    )
    # Older successful receipts remain readable. A legacy recorder that
    # explicitly reports no selection observation cannot attest completion.
    # Finished is validated above and is the conservative fallback when an
    # older recorder did not save a start time.
    # Compare the receipt's timestamp with the fixed rollout boundary. The
    # classification of unchanged historical bytes does not age into v2.
    missing_worklist_observation = writer_role == "home" and (
        observed_status == "not_recorded"
        or (worklist is None and "batch_budget" in commands and "hold_check" in data)
    )
    receipt_started = (
        started if isinstance(started, datetime) and started.utcoffset() is not None else finished
    )
    obsolete_home_writer = (
        writer_role == "home" and schema == 1 and receipt_started is not None
        and receipt_started >= HOME_WORKLIST_RECEIPT_REQUIRED_FROM
    )
    if (outcome == "completed" and
            ((requires_verified_worklist and not worklist_verified)
             or missing_worklist_observation or obsolete_home_writer)):
        outcome = "needs_attention"
        diagnostics.append("Home local worklist evidence was not verified")
    elif obsolete_home_writer or (requires_verified_worklist and hold_check == "absent"
          and (not isinstance(worklist, dict) or worklist.get("evidence_verified") is not True)):
        diagnostics.append("Home local worklist evidence was not verified")
    if outcome == "completed" and requires_verified_worklist:
        proof_problem = _home_completed_worklist_problem(state_root, data)
        if proof_problem:
            outcome = "needs_attention"
            diagnostics.append(proof_problem)
    if outcome == UNKNOWN:
        diagnostics.append("run outcome is ambiguous")
    if outcome != "completed":
        # The second tuple element is success evidence, not the attempt's
        # finish time. attempt_at preserves date/order even when unverified.
        return attempt_at, None, outcome, "; ".join(diagnostics) or None
    return attempt_at, finished, outcome, "; ".join(diagnostics) or None


def home_worklist_evidence_path(state_root: Path, started: datetime) -> Path:
    stamp = started.strftime("%Y%m%dT%H%M%S%fZ")
    return state_root / f"home-worklist-evidence-{stamp}.json"


def home_worklist_evidence_sha256(evidence: dict) -> str:
    encoded = json.dumps(evidence, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _home_completed_worklist_problem(state_root: Path, receipt: dict) -> str | None:
    invalid = "Home producer worklist file does not match the completed receipt"
    started = _parse_timestamp(receipt.get("started_at"))
    finished = _parse_timestamp(receipt.get("finished_at"))
    if started is None or finished is None:
        return invalid
    path = home_worklist_evidence_path(state_root, started)
    try:
        if _lstat(path) is None:
            return "Home producer worklist file is missing"
    except OSError:
        return "Home producer worklist file is not safely readable"
    status, before = _safe_stat(path, state_root)
    if status != "ok" or before is None:
        return "Home producer worklist file is not safely readable"
    status, raw = _safe_read(path, state_root)
    if status != "ok" or raw is None:
        return "Home producer worklist file is not safely readable"
    status, after = _safe_stat(path, state_root)
    if status != "ok" or after is None or (
        before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns
    ) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns):
        return "Home producer worklist file changed while it was read"
    try:
        evidence = json.loads(raw)
    except (UnicodeDecodeError, ValueError):
        return invalid
    if not isinstance(evidence, dict) or evidence.get("invalidated") is not False:
        return invalid
    if _parse_timestamp(evidence.get("started_at")) != started:
        return invalid
    observed = _parse_timestamp(evidence.get("recorded_at"))
    result = evidence.get("result")
    commands = receipt.get("commands", receipt.get("command_outcomes"))
    worklist = commands.get("memory_worklist") if isinstance(commands, dict) else None
    verified = (
        observed is not None and started <= observed <= finished
        and isinstance(result, dict) and result.get("writer_role") == "home"
        and isinstance(worklist, dict)
        and result.get("status") == worklist.get("observed_status")
        and type(result.get("selected")) is int
        and result.get("selected") == worklist.get("observed_selected")
        and home_worklist_evidence_sha256(evidence) == worklist.get("producer_evidence_sha256")
    )
    return None if verified else invalid


def _read_run_receipts(
    state_root: Path, *, job_id: str, writer_role: str,
) -> tuple[str | None, str, str | None, str | None]:
    """Return the latest outcome and the latest classified completion time."""
    receipt_dir = state_root / RUN_RECEIPT_DIR
    # lstat before existence checks: a dangling or valid receipt-directory
    # symlink is unsafe unknown evidence, never a missing directory.
    try:
        dir_stat = _lstat(receipt_dir)
    except OSError:
        return None, UNKNOWN, "run receipt directory is not readable", None
    if dir_stat is None:
        return None, UNKNOWN, None, None
    if stat.S_ISLNK(dir_stat.st_mode):
        return None, UNKNOWN, "run receipt directory is a symlink", None
    if not stat.S_ISDIR(dir_stat.st_mode):
        return None, UNKNOWN, "run receipt directory is not a directory", None
    dir_status, _ = _safe_dir(receipt_dir, state_root)
    if dir_status != "ok":
        return None, UNKNOWN, "run receipt directory is not safely readable", None
    # Streaming scan with constant retained memory: os.scandir yields one
    # directory entry at a time (Path.iterdir would first materialize the
    # whole name list via os.listdir). Only the best validated candidate
    # (newest by validated receipt timestamp, worst-outcome and then name as
    # deterministic tie breaks) and the most conservative unvalidated
    # candidate (newest by file mtime, which blocks an older success only
    # when filesystem evidence says it may be newer) are retained. The total
    # scan is O(n) in this exact local receipt directory; nothing is cached,
    # deleted or capped.
    best_valid: tuple[tuple[float, int, str], tuple] | None = None
    worst_unvalidated: tuple[tuple[float, str], str | None] | None = None
    last_success: datetime | None = None
    clock_skew = False
    try:
        with os.scandir(receipt_dir) as entries:
            for entry in entries:
                if not entry.name.endswith(".json") or entry.name.startswith(".tmp_"):
                    continue
                path = receipt_dir / entry.name
                st = _lstat(path)
                mtime = st.st_mtime if st is not None else float("inf")
                read_status, raw = _safe_read(path, state_root)
                if read_status != "ok" or raw is None:
                    candidate = ((mtime, path.name), None)
                    if worst_unvalidated is None or candidate[0] >= worst_unvalidated[0]:
                        worst_unvalidated = candidate
                    continue
                try:
                    data = json.loads(raw.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    candidate = ((mtime, path.name), "newest run receipt is malformed")
                    if worst_unvalidated is None or candidate[0] >= worst_unvalidated[0]:
                        worst_unvalidated = candidate
                    continue
                attempt_at, finished_at, outcome, diagnostic = _classify_receipt(
                    data, job_id=job_id, writer_role=writer_role, state_root=state_root,
                )
                # The receipt classifier uses _parse_timestamp, which either
                # returns a timezone-aware UTC datetime or rejects the stamp.
                now = datetime.now(timezone.utc)
                if any(stamp is not None and stamp > now for stamp in (attempt_at, finished_at)):
                    clock_skew = True
                    continue
                if attempt_at is None or outcome is None:
                    candidate = (
                        (mtime, path.name),
                        diagnostic or "newest run receipt is invalid",
                    )
                    if worst_unvalidated is None or candidate[0] >= worst_unvalidated[0]:
                        worst_unvalidated = candidate
                    continue
                if (outcome == "completed" and finished_at is not None
                        and (last_success is None or finished_at > last_success)):
                    last_success = finished_at
                rank = 0 if outcome == "completed" else 2 if outcome == "failed" else 1
                candidate = (
                    (attempt_at.timestamp(), rank, path.name),
                    (finished_at, outcome, diagnostic),
                )
                if best_valid is None or candidate[0] >= best_valid[0]:
                    best_valid = candidate
    except OSError:
        return None, UNKNOWN, "run receipt directory is not readable", None

    success_at = last_success.isoformat() if last_success else None
    skew_warning = "run receipt timestamp is in the future; check the local clock" if clock_skew else None
    if best_valid is not None:
        if (
            worst_unvalidated is not None
            and worst_unvalidated[0][0] >= best_valid[0][0]
        ):
            diagnostic = worst_unvalidated[1] or "newer run receipt cannot be validated"
            return None, UNKNOWN, "; ".join(filter(None, (diagnostic, skew_warning))), success_at
        finished_at, outcome, diagnostic = best_valid[1]
        if skew_warning:
            diagnostic = "; ".join(filter(None, (diagnostic, skew_warning)))
        if outcome != "completed":
            return None, outcome, diagnostic, success_at
        return finished_at.isoformat() if finished_at else None, outcome, diagnostic, success_at
    if worst_unvalidated is not None:
        diagnostic = worst_unvalidated[1] or "newest run receipt cannot be validated"
        return None, UNKNOWN, "; ".join(filter(None, (diagnostic, skew_warning))), success_at
    return None, UNKNOWN, skew_warning, success_at


# --- operator hold ---


def _read_hold(state_root: Path) -> tuple[str, str | None]:
    hold_path = state_root / HOLD_FILE
    # lstat before existence checks: a dangling hold symlink is unsafe
    # unknown evidence, never absence.
    try:
        hold_stat = _lstat(hold_path)
    except OSError:
        return UNKNOWN, "extraction hold unreadable"
    if hold_stat is None:
        return "absent", None
    if stat.S_ISLNK(hold_stat.st_mode):
        return UNKNOWN, "extraction hold is a symlink"
    read_status, raw = _safe_read(hold_path, state_root)
    if read_status != "ok" or raw is None:
        return UNKNOWN, "extraction hold unreadable or unsafe"
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return UNKNOWN, "extraction hold unreadable or malformed"
    if not isinstance(data, dict):
        return UNKNOWN, "extraction hold unreadable or malformed"
    for key in HOLD_TIMESTAMP_KEYS:
        if _parse_timestamp(data.get(key)) is not None:
            return "present", None
    return UNKNOWN, "extraction hold has no valid timestamp"


def read_native_job_status(
    *,
    writer_role: str,
    state_root: Path,
    automation_root: Path | None = None,
) -> dict:
    """Observe one host role's native job registration and bounded run state.

    Unknown roles raise PermissionError rather than ever selecting a job.
    Missing or malformed evidence is reported as unknown, never fabricated
    as disabled or successful.
    """
    job_id = JOB_IDS.get(writer_role)
    if job_id is None:
        raise PermissionError(
            "native job status requires a known Home or SJM writer role"
        )
    root = Path(automation_root) if automation_root is not None else _default_automation_root()
    state_root = Path(state_root)

    registration_state, config, reg_diagnostic = _read_registration(job_id, root)
    extraction_hold, hold_diagnostic = _read_hold(state_root)
    last_completed_at, last_run_outcome, receipt_diagnostic, last_successful_at = (
        _read_run_receipts(state_root, job_id=job_id, writer_role=writer_role)
    )
    last_success = _parse_timestamp(last_successful_at)
    elapsed = (datetime.now(timezone.utc) - last_success).total_seconds() if last_success else None

    diagnostics = [
        message
        for message in (reg_diagnostic, receipt_diagnostic, hold_diagnostic)
        if message
    ]
    return {
        "job_id": job_id,
        "registration_state": registration_state,
        "configured_model": config.get("model"),
        "configured_reasoning": config.get("reasoning_effort"),
        "schedule": config.get("rrule"),
        "last_completed_at": last_completed_at,
        "last_successful_at": last_successful_at,
        "last_successful_age_hours": round(elapsed / 3600, 1) if elapsed is not None and elapsed >= 0 else None,
        "last_run_outcome": last_run_outcome,
        "extraction_hold": extraction_hold,
        "diagnostic": "; ".join(diagnostics) or None,
    }
