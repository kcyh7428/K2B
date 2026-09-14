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
    "memory_home_drain": {"reconciled"},
    "memory_publish": {"published", "not_attempted"},
}
COMMAND_NONTERMINAL_STATUSES = {"ready"}
COMMAND_FAILURE_STATUSES = {"failed"}
COMMAND_COUNT_FIELDS = ("local_failed", "sjm_failed", "failed")
EXCEPTION_COUNT_FIELDS = ("needs_attention", "retry_backoff", "exhausted")
# Bounded schema-v1 exception object shapes. Free-text fields (reason,
# work_id) are validated for shape and never returned.
EXCEPTION_RETRY_STATUSES = {"resolved_with_reviewed_empty"}
EXCEPTION_HOLD_TYPES = {"input_budget_exceeded"}
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


def _safe_read(path: Path, root: Path) -> tuple[str, bytes | None]:
    """Read a regular file without following links; reject oversize input
    instead of truncating it into an apparently valid prefix."""
    status, st = _safe_stat(path, root)
    if status != "ok" or st is None:
        return status, None
    if st.st_size > MAX_READ_BYTES:
        return "oversize", None
    try:
        with open(path, "rb") as handle:
            data = handle.read(MAX_READ_BYTES + 1)
    except OSError:
        return "unreadable", None
    if len(data) > MAX_READ_BYTES:
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
        if not isinstance(work_id, str) or not work_id:
            return None
        # A held receipt exception is unresolved evidence even without an
        # explicit action flag.
        return "action"
    return None


def _classify_receipt(
    data: object, *, job_id: str, writer_role: str
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
    if type(data.get("schema_version")) is not int or data.get("schema_version") != 1:
        return None, None, None, "run receipt schema is unsupported"

    identity = [data.get(key) for key in RECEIPT_ID_KEYS if data.get(key) is not None]
    if (
        len(identity) != 1
        or not isinstance(identity[0], str)
        or identity[0] != job_id
        or data.get("writer_role") != writer_role
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
    saw_terminal = False
    verdict = "ok"
    for name, command in commands.items():
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
            if status not in COMMAND_NONTERMINAL_STATUSES:
                verdict = "ambiguous"
    worklist = commands.get("memory_worklist")
    if isinstance(worklist, dict):
        selected = worklist.get("selected")
        if "selected" in worklist and (type(selected) is not int or selected < 0):
            return None, None, None, "run receipt selected count is malformed"
        needs_extraction = (isinstance(selected, int) and selected > 0) or worklist.get("status") in {"ready", "ready_with_exceptions"}
        extraction = commands.get("memory_record_extraction")
        if needs_extraction and (not isinstance(extraction, dict) or _evaluate_command("memory_record_extraction", extraction) != "ok") and verdict != "failed":
            verdict = "ambiguous"
    if verdict == "failed":
        outcome = "failed"
    elif not saw_terminal or verdict == "ambiguous":
        outcome = UNKNOWN
        diagnostics.append("run outcome is ambiguous")
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
    for key in ("hold_check",):
        value = data.get(key)
        if value is not None and not isinstance(value, str):
            return None, None, None, f"run receipt {key} is malformed"

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

    if outcome != "completed":
        return attempt_at, None, outcome, "; ".join(diagnostics) or None
    return attempt_at, finished, outcome, "; ".join(diagnostics) or None


def _read_run_receipts(
    state_root: Path, *, job_id: str, writer_role: str
) -> tuple[str | None, str, str | None]:
    """Return (last_completed_at, last_run_outcome, diagnostic)."""
    receipt_dir = state_root / RUN_RECEIPT_DIR
    # lstat before existence checks: a dangling or valid receipt-directory
    # symlink is unsafe unknown evidence, never a missing directory.
    try:
        dir_stat = _lstat(receipt_dir)
    except OSError:
        return None, UNKNOWN, "run receipt directory is not readable"
    if dir_stat is None:
        return None, UNKNOWN, None
    if stat.S_ISLNK(dir_stat.st_mode):
        return None, UNKNOWN, "run receipt directory is a symlink"
    if not stat.S_ISDIR(dir_stat.st_mode):
        return None, UNKNOWN, "run receipt directory is not a directory"
    dir_status, _ = _safe_dir(receipt_dir, state_root)
    if dir_status != "ok":
        return None, UNKNOWN, "run receipt directory is not safely readable"
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
    try:
        with os.scandir(receipt_dir) as entries:
            for entry in entries:
                if not entry.name.endswith(".json"):
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
                    data, job_id=job_id, writer_role=writer_role
                )
                if attempt_at is None:
                    candidate = (
                        (mtime, path.name),
                        diagnostic or "newest run receipt is invalid",
                    )
                    if worst_unvalidated is None or candidate[0] >= worst_unvalidated[0]:
                        worst_unvalidated = candidate
                    continue
                rank = {"completed": 0, UNKNOWN: 1, "failed": 2}[outcome]
                candidate = (
                    (attempt_at.timestamp(), rank, path.name),
                    (finished_at, outcome, diagnostic),
                )
                if best_valid is None or candidate[0] >= best_valid[0]:
                    best_valid = candidate
    except OSError:
        return None, UNKNOWN, "run receipt directory is not readable"

    if best_valid is not None:
        if (
            worst_unvalidated is not None
            and worst_unvalidated[0][0] >= best_valid[0][0]
        ):
            return None, UNKNOWN, worst_unvalidated[1] or (
                "newer run receipt cannot be validated"
            )
        finished_at, outcome, diagnostic = best_valid[1]
        if outcome != "completed":
            return None, outcome, diagnostic
        return finished_at.isoformat() if finished_at else None, outcome, diagnostic
    if worst_unvalidated is not None:
        return None, UNKNOWN, worst_unvalidated[1] or (
            "newest run receipt cannot be validated"
        )
    return None, UNKNOWN, None


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
    last_completed_at, last_run_outcome, receipt_diagnostic = _read_run_receipts(
        state_root, job_id=job_id, writer_role=writer_role
    )
    extraction_hold, hold_diagnostic = _read_hold(state_root)

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
        "last_run_outcome": last_run_outcome,
        "extraction_hold": extraction_hold,
        "diagnostic": "; ".join(diagnostics) or None,
    }
