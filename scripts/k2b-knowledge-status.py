#!/usr/bin/env python3
"""Read-only knowledge coverage and freshness report CLI.

Collects observations from existing owning readers under explicit path
overrides, then prints a JSON report. Never stages, extracts, publishes,
writes notes or contacts another host. No network access, no credentials,
no state-tree creation. ``summarize`` itself is pure; collection functions
are best-effort and honestly report unavailable or partial results.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))

import automatic_memory  # noqa: E402
import eod_capture  # noqa: E402
import knowledge_status  # noqa: E402

HKT = knowledge_status.HKT
MAX_RECEIPT_SCAN_ENTRIES = 1000
MAX_RECEIPT_BYTES = 256 * 1024
MAX_TOTAL_RECEIPT_BYTES = 4 * 1024 * 1024
MAX_SCAN_SECONDS = 300.0
DEFAULT_SCAN_SECONDS = 30.0

# Production Home state belongs to this host role (physical user).
PRODUCTION_HOME_ROLE = "keithmbpm2"
# Production SJM source-only state belongs to this host role (physical user).
PRODUCTION_SJM_ROLE = "keithcheung"


def _is_production_state_root(state_root: Path) -> bool:
    """Return True when ``state_root`` is the live production state tree.

    Synthetic path overrides (for example a temporary directory) are not
    production and skip the host-role binding.
    """
    try:
        return Path(state_root).resolve(strict=False) == _default_state_root().resolve(strict=False)
    except OSError:
        return False


def _current_host_role() -> str:
    try:
        import getpass
        return getpass.getuser()
    except Exception:  # pragma: no cover - defensive
        return "unknown"


def _enforce_role_binding(*, state_root: Path, writer_role: str) -> None:
    """Validate the production state root against the host role.

    Raises PermissionError before any collection when the production state
    root does not belong to the caller's role. Synthetic overrides skip
    this binding. Callers must invoke this before any owning reader runs.
    """
    if not _is_production_state_root(state_root):
        return
    expected = PRODUCTION_HOME_ROLE if writer_role == "home" else PRODUCTION_SJM_ROLE
    actual = _current_host_role()
    if actual != expected:
        raise PermissionError(
            f"production {writer_role} state belongs to host role {expected!r}; "
            f"current host role is {actual!r}"
        )


def _default_state_root() -> Path:
    return Path.home() / ".local" / "state" / "k2b" / "automatic-memory"


def _default_app_root() -> Path:
    """Return the Codex application root (owner of state_5.sqlite)."""
    import os
    env = os.environ.get("CODEX_HOME")
    if env:
        return Path(env)
    return Path.home() / ".codex"


def _default_sessions_root(app_root: Path) -> Path:
    """File sessions root derived from explicit override or environment."""
    import os
    env = os.environ.get("K2B_CODEX_SESSIONS_ROOT")
    if env:
        return Path(env)
    return app_root / "sessions"


def _default_vault() -> Path:
    return Path.home() / "Projects" / "K2B-Vault"


def _default_automation_root(app_root: Path) -> Path:
    """Default automation root derived from the resolved Codex app root."""
    return Path(app_root) / "automations"


def _current_hkt_date() -> str:
    return datetime.now(HKT).date().isoformat()


def _validate_scan_seconds(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("scan budget must be a finite positive number")
    if not math.isfinite(value) or value <= 0 or value > MAX_SCAN_SECONDS:
        raise ValueError(
            f"scan budget must be finite, positive and at most {MAX_SCAN_SECONDS:g} seconds"
        )
    return float(value)


def _collect_source_coverage(*, app_root: Path, sessions_root: Path,
                             state_root: Path, writer_role: str, since: str,
                             through: str, max_scan_seconds: float,
                             report_today: str | None = None) -> dict:
    """Collect coverage from existing readers, preserving all negative
    signals including unavailable SQL, changed revisions and incomplete
    scans.

    ``sessions_root`` is passed only to the file session reader and
    ``app_root`` only to the SQL reader. The two roots are distinct owner
    contracts: file sessions live under app_root/sessions by default, while
    the SQL owner expects the application root containing state_5.sqlite.
    """
    observed_at = datetime.now(timezone.utc).isoformat()
    file_entry: dict = {
        "status": "unavailable",
        "complete": False,
        "pending_turns": None,
        "extracted_turns": None,
        "oldest_pending_at": None,
    }
    if sessions_root is None or not Path(sessions_root).is_dir():
        file_entry["diagnostic"] = (
            "file source inventory is unavailable: no source sessions directory"
        )
        file_result = None
    else:
        try:
            file_result = eod_capture.memory_backlog(
                state_root=state_root, codex_root=Path(sessions_root),
                since=since, through=through, writer_role=writer_role,
                max_scan_seconds=max_scan_seconds,
            )
        except PermissionError as exc:
            # Filesystem denial by an authorized reader degrades to
            # unavailable; it is not proof of zero backlog. Role-policy
            # denial was already enforced before collection started.
            file_entry["diagnostic"] = (
                f"file backlog unavailable: filesystem permission denied ({exc.__class__.__name__})"
            )
            file_result = None
        except Exception as exc:  # bounded; unknown evidence, not zero
            file_entry["diagnostic"] = f"file backlog unavailable: {exc.__class__.__name__}"
            file_result = None
    if file_result is not None:
        if not isinstance(file_result, dict):
            file_entry["diagnostic"] = "file backlog returned a non-mapping"
        else:
            # unavailable_turns is preserved from the owner field only. If
            # the owner omits it, the value stays unknown; it is never
            # derived from unverified_turns, which is a separate signal.
            file_entry = {
                "status": file_result.get("status", "unknown"),
                "complete": file_result.get("complete") if isinstance(
                    file_result.get("complete"), bool) else None,
                "pending_turns": knowledge_status._as_int(file_result.get("pending_turns")),
                "extracted_turns": knowledge_status._as_int(file_result.get("extracted_turns")),
                "eligible_turns": knowledge_status._as_int(file_result.get("eligible_turns")),
                "oldest_pending_at": file_result.get("oldest_pending_at"),
                "invalid_receipts": knowledge_status._as_int(file_result.get("invalid_receipts")),
                "unverified_turns": knowledge_status._as_int(file_result.get("unverified_turns")),
                "source_errors": knowledge_status._as_int(file_result.get("source_errors")),
                "parse_exceptions": knowledge_status._as_int(file_result.get("parse_exceptions")),
                "timed_out_sources": knowledge_status._as_int(file_result.get("timed_out_sources")),
            }
            if "unavailable_turns" in file_result:
                file_entry["unavailable_turns"] = knowledge_status._as_int(
                    file_result.get("unavailable_turns"))
            else:
                file_entry["unavailable_turns_unknown"] = True

    sql_entry: dict = {
        "status": "unavailable",
        "complete": False,
        "pending_turns": None,
        "extracted_turns": None,
        "changed_sources": None,
        "file_backed_pending_turns": None,
        "oldest_pending_at": None,
        "diagnostic": "SQL reader not attempted",
    }
    # The approved-window gate uses the single report date resolved by main
    # (or the caller), not a fresh wall clock reading. The SQL owner still
    # enforces its own current-day policy and may honestly return unavailable
    # at a boundary.
    if report_today is None:
        report_today = _current_hkt_date()
    if since == knowledge_status.APPROVED_WINDOW_START and through == report_today:
        try:
            from codex_sql_memory import scan as sql_scan  # local import
        except ImportError as exc:
            sql_entry["diagnostic"] = f"SQL reader import failed: {exc.__class__.__name__}"
        else:
            try:
                sql_result = sql_scan(
                    codex_root=Path(app_root), state_root=state_root,
                    writer_role=writer_role, since=since, through=through,
                    max_scan_seconds=min(max_scan_seconds, 300.0),
                )
            except PermissionError as exc:
                sql_entry["diagnostic"] = (
                    f"SQL audit unavailable: filesystem permission denied ({exc.__class__.__name__})"
                )
            except Exception as exc:  # bounded, no fabrication
                sql_entry["diagnostic"] = f"SQL audit unavailable: {exc.__class__.__name__}"
                if exc.__class__.__name__ == "SQLScanBudgetExceeded":
                    sql_entry["status"] = "partial"
                    sql_entry["complete"] = False
            else:
                if not isinstance(sql_result, dict):
                    sql_entry["diagnostic"] = "SQL audit returned a non-mapping"
                else:
                    sql_entry = {
                        "status": sql_result.get("status", "unknown"),
                        "complete": sql_result.get("complete") if isinstance(
                            sql_result.get("complete"), bool) else None,
                        "pending_turns": knowledge_status._as_int(sql_result.get("pending_turns")),
                        "extracted_turns": knowledge_status._as_int(sql_result.get("extracted_turns")),
                        "changed_sources": knowledge_status._as_int(sql_result.get("changed_sources")),
                        "file_backed_pending_turns": knowledge_status._as_int(
                            sql_result.get("file_backed_pending_turns")),
                        "oldest_pending_at": sql_result.get("oldest_pending_at"),
                    }
    else:
        sql_entry["status"] = "not_attempted"
        sql_entry["diagnostic"] = "SQL scan requires the full approved window"

    # The two readers return overlapping aggregates with no stable file
    # identities. No exact union is computed here; the report always marks
    # ``union`` unmeasured.
    return {
        "observed_at": observed_at,
        "file_backed": file_entry,
        "sql_backed": sql_entry,
        "union": "unmeasured",
    }


def _read_outbox_read_only(state_root: Path) -> dict:
    """Read the outbox using the existing non-mutating validator and JSON
    reader, without acquiring the outbox lock (which would create parent
    dirs and a lock file). Returns a validated ``counts``/``inspected``/
    ``total_files``/``truncated`` shape or ``unavailable``/``partial``.
    """
    folder = state_root / "outbox"
    try:
        if folder.is_symlink():
            return {"status": "partial", "diagnostic": "outbox folder is a symlink",
                    "counts": None, "inspected": 0, "total_files": 0,
                    "truncated": None}
        if not folder.is_dir():
            return {"status": "unknown", "diagnostic": "outbox folder is absent",
                    "counts": None, "inspected": None, "total_files": None,
                    "truncated": None}
    except OSError as exc:
        return {"status": "unavailable", "diagnostic": f"outbox folder unreadable: {exc.__class__.__name__}",
                "counts": None, "inspected": None, "total_files": None,
                "truncated": None}

    counts = {state: 0 for state in sorted(automatic_memory.ALLOWED_DELIVERY_STATES)}
    inspected = 0
    total_files = 0
    truncated = False
    corrupt = 0
    oversized = 0
    total_bytes = 0
    encountered = 0
    oldest_deliverable_at: str | None = None
    oldest_deliverable_parsed: datetime | None = None
    try:
        with __import__("os").scandir(folder) as entries:
            for entry in entries:
                encountered += 1
                if encountered > MAX_RECEIPT_SCAN_ENTRIES:
                    truncated = True
                    break
                if not entry.name.endswith(".json"):
                    continue
                total_files += 1
                if inspected >= MAX_RECEIPT_SCAN_ENTRIES:
                    truncated = True
                    break
                try:
                    if entry.is_symlink():
                        corrupt += 1
                        continue
                    path = folder / entry.name
                    # Bounds enforced before reading, and again while reading.
                    size = entry.stat(follow_symlinks=False).st_size
                    if size > MAX_RECEIPT_BYTES or total_bytes + size > MAX_TOTAL_RECEIPT_BYTES:
                        oversized += 1
                        truncated = True
                        continue
                    budget = min(MAX_RECEIPT_BYTES, MAX_TOTAL_RECEIPT_BYTES - total_bytes)
                    with path.open("rb") as source:
                        data = source.read(budget + 1)
                    total_bytes += len(data)
                    # A racing append cannot turn this into an unbounded read.
                    if len(data) > budget:
                        oversized += 1
                        truncated = True
                        continue
                    raw = data.decode("utf-8")
                    envelope = json.loads(raw)
                    automatic_memory._validate_envelope(envelope, durable=True)
                except (OSError, ValueError, UnicodeError,
                        RecursionError, automatic_memory.StateCorruptionError):
                    corrupt += 1
                    continue
                inspected += 1
                state = envelope.get("status")
                if state in counts:
                    counts[state] += 1
                if state in knowledge_status.DELIVERABLE_STATES:
                    created = envelope.get("created_at")
                    if isinstance(created, str):
                        try:
                            created_dt = datetime.fromisoformat(created)
                        except ValueError:
                            created_dt = None
                        if created_dt is not None and created_dt.tzinfo is not None \
                                and created_dt.utcoffset() is not None:
                            if oldest_deliverable_parsed is None \
                                    or created_dt < oldest_deliverable_parsed:
                                oldest_deliverable_parsed = created_dt
                                oldest_deliverable_at = created
    except OSError as exc:
        return {"status": "unavailable",
                "diagnostic": f"outbox unreadable: {exc.__class__.__name__}",
                "counts": None, "inspected": None, "total_files": None,
                "truncated": None}

    status = "observed"
    diagnostic = None
    if truncated or corrupt:
        status = "partial"
        reasons = []
        if truncated:
            reasons.append("outbox scan was truncated by entry or byte limit")
        if oversized:
            reasons.append(f"{oversized} outbox envelope(s) exceeded the read byte limit and were not inspected")
        if corrupt:
            reasons.append(f"{corrupt} outbox envelope(s) failed validation")
        diagnostic = "; ".join(reasons)
    return {
        "status": status,
        "counts": counts if not (truncated or corrupt or oversized) else None,
        "inspected": inspected,
        "total_files": total_files,
        "truncated": truncated,
        "oldest_pending_at": oldest_deliverable_at,
        "partial_scan": status == "partial",
        "diagnostic": diagnostic,
    }


def _collect_delivery(*, state_root: Path) -> dict:
    observed_at = datetime.now(timezone.utc).isoformat()
    try:
        outbox = _read_outbox_read_only(state_root)
    except Exception as exc:
        return {"status": "unavailable", "outbox": "unknown",
                "observed_at": observed_at,
                "diagnostic": f"outbox unavailable: {exc.__class__.__name__}"}
    deliverable = knowledge_status.DELIVERABLE_STATES
    counts = outbox.get("counts") if isinstance(outbox, dict) else None
    pending = None
    if isinstance(counts, dict) and all(state in counts for state in deliverable):
        pending = sum(counts[state] for state in deliverable)
    oldest = outbox.get("oldest_pending_at") if isinstance(outbox, dict) else None
    partial_scan = outbox.get("partial_scan") if isinstance(outbox, dict) else None
    return {
        "status": outbox.get("status", "unknown") if isinstance(outbox, dict) else "unknown",
        "observed_at": observed_at,
        "outbox": outbox.get("status", "unknown") if isinstance(outbox, dict) else "unknown",
        "pending_envelopes": pending,
        "oldest_pending_at": oldest,
        "partial_scan": partial_scan if isinstance(partial_scan, bool) else None,
        "counts": counts,
        "inspected": outbox.get("inspected") if isinstance(outbox, dict) else None,
        "total_files": outbox.get("total_files") if isinstance(outbox, dict) else None,
        "truncated": outbox.get("truncated") if isinstance(outbox, dict) else None,
        "diagnostic": outbox.get("diagnostic") if isinstance(outbox, dict) else None,
    }


def _read_receipt(path: Path, root: Path, *, byte_budget: int = MAX_RECEIPT_BYTES) -> tuple[dict | None, int]:
    consumed = 0
    try:
        if path.is_symlink():
            return None, consumed
        resolved = path.resolve(strict=False)
        if not resolved.is_relative_to(root.resolve()):
            return None, consumed
        size = path.stat().st_size
        limit = min(MAX_RECEIPT_BYTES, byte_budget)
        if limit <= 0 or size > limit:
            return None, consumed
        with path.open("rb") as source:
            raw = source.read(limit + 1)
        consumed = len(raw)
        if consumed > limit:
            return None, consumed
        text = raw.decode("utf-8")
    except (OSError, UnicodeError):
        return None, consumed
    try:
        data = json.loads(text)
    except (ValueError, RecursionError):
        return None, consumed
    return (data if isinstance(data, dict) else None), consumed


def _collect_publication(*, state_root: Path) -> dict:
    """Scan a bounded number of publication receipts. A best-effort
    best-by-published_at result is reported as ``scan_partial`` if limits
    were reached so it cannot claim a globally latest receipt. Artifact
    verification is unmeasured."""
    folder = state_root / "publication"
    observed_at = datetime.now(timezone.utc).isoformat()
    try:
        if folder.is_symlink():
            return {"status": "unknown", "observed_at": observed_at,
                    "diagnostic": "publication folder is a symlink"}
        if not folder.is_dir():
            return {"status": "no_receipts", "observed_at": observed_at}
    except OSError as exc:
        return {"status": "unavailable", "observed_at": observed_at,
                "diagnostic": f"publication folder unreadable: {exc.__class__.__name__}"}

    best: tuple[float, dict] | None = None
    seen = 0
    encountered = 0
    truncated = False
    rejected = 0
    total_bytes = 0
    try:
        with __import__("os").scandir(folder) as entries:
            for entry in entries:
                encountered += 1
                if encountered > MAX_RECEIPT_SCAN_ENTRIES:
                    truncated = True
                    break
                if not entry.name.endswith(".json"):
                    continue
                seen += 1
                try:
                    if entry.is_symlink():
                        truncated = True
                        continue
                    path = folder / entry.name
                    size = entry.stat(follow_symlinks=False).st_size
                except OSError:
                    truncated = True
                    continue
                if size > MAX_RECEIPT_BYTES or total_bytes + size > MAX_TOTAL_RECEIPT_BYTES:
                    truncated = True
                    continue
                data, consumed = _read_receipt(
                    path, folder, byte_budget=MAX_TOTAL_RECEIPT_BYTES - total_bytes)
                total_bytes += consumed
                if data is None:
                    # Undecodable, oversized, unreadable or deeply nested JSON
                    # is a scan budget signal, not a business rejection.
                    truncated = True
                    if total_bytes >= MAX_TOTAL_RECEIPT_BYTES:
                        break
                    continue
                if data.get("status") != "published" or data.get("schema_version") != 1:
                    rejected += 1
                    continue
                published_at = data.get("published_at")
                if not isinstance(published_at, str):
                    rejected += 1
                    continue
                try:
                    ts = datetime.fromisoformat(published_at)
                except ValueError:
                    rejected += 1
                    continue
                if ts.tzinfo is None or ts.utcoffset() is None:
                    rejected += 1
                    continue
                snapshot = data.get("snapshot_sha256")
                if not (isinstance(snapshot, str) and len(snapshot) == 64
                        and all(c in "0123456789abcdef" for c in snapshot)):
                    rejected += 1
                    continue
                try:
                    rank = ts.timestamp()
                except (OverflowError, OSError, ValueError):
                    rejected += 1
                    continue
                if not math.isfinite(rank):
                    rejected += 1
                    continue
                if best is None or rank > best[0]:
                    best = (rank, data)
    except OSError as exc:
        return {"status": "unavailable", "observed_at": observed_at,
                "diagnostic": f"publication folder unreadable: {exc.__class__.__name__}"}

    diagnostic = None
    if rejected:
        diagnostic = (f"{rejected} publication receipt(s) rejected by schema, "
                      "status, timestamp or hash validation")
        truncated = True
    if best is None:
        return {"status": "no_receipts" if not rejected else "unknown",
                "observed_at": observed_at,
                "scan_partial": truncated,
                "rejected_receipts": rejected,
                "diagnostic": diagnostic if rejected else None}
    data = best[1]
    watermark = data.get("reconciliation_watermark")
    return {
        "status": "published",
        "published_at": data.get("published_at"),
        "snapshot_sha256": data.get("snapshot_sha256"),
        "watermark_size": len(watermark) if isinstance(watermark, dict) else None,
        "observed_at": observed_at,
        "scan_partial": truncated,
        "rejected_receipts": rejected,
        "diagnostic": diagnostic,
        "artifact_verified": "unmeasured",
    }


def _collect_native_job(*, state_root: Path, automation_root: Path,
                        writer_role: str) -> dict:
    observed_at = datetime.now(timezone.utc).isoformat()
    try:
        native = eod_capture.native_job_status.read_native_job_status(
            writer_role=writer_role, state_root=state_root,
            automation_root=automation_root,
        )
    except PermissionError as exc:
        return {"registration_state": "unknown", "observed_at": observed_at,
                "diagnostic": f"native status unavailable: filesystem permission denied ({exc.__class__.__name__})"}
    except Exception as exc:
        return {"registration_state": "unknown", "observed_at": observed_at,
                "diagnostic": f"native status unavailable: {exc.__class__.__name__}"}
    if not isinstance(native, dict):
        return {"registration_state": "unknown", "observed_at": observed_at,
                "diagnostic": "native status returned a non-mapping"}
    # Registration and canonical run receipts do not prove native dispatch.
    return {
        "registration_state": native.get("registration_state"),
        "native_schedule": native.get("schedule"),
        "native_job_id": native.get("job_id"),
        "last_run_outcome": native.get("last_run_outcome"),
        "last_completed_at": native.get("last_completed_at"),
        "extraction_hold": native.get("extraction_hold"),
        "diagnostic": native.get("diagnostic"),
        "observed_dispatch": "unknown",
        "dispatch_evidence": "unverified",
        "observed_at": observed_at,
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="k2b-knowledge-status",
        description="Read-only K2B knowledge coverage and freshness report",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    report = sub.add_parser("report", help="Print the coverage report as JSON")
    report.add_argument("--codex-root", type=Path, default=None,
                        help="Codex application root containing state_5.sqlite "
                             "(default CODEX_HOME or ~/.codex); supplied to the SQL reader")
    report.add_argument("--sessions-root", type=Path, default=None,
                        help="File sessions directory (default "
                             "K2B_CODEX_SESSIONS_ROOT or APP_ROOT/sessions); "
                             "supplied only to the file reader")
    report.add_argument("--state-root", type=Path, default=None)
    report.add_argument("--vault", type=Path, default=None,
                        help="(informational) full publication artifacts are not read")
    report.add_argument("--automation-root", type=Path, default=None)
    report.add_argument("--writer-role", choices=("home", "sjm-source-only"), default="home")
    report.add_argument("--since", default=knowledge_status.APPROVED_WINDOW_START)
    report.add_argument("--through", default=None)
    report.add_argument("--max-scan-seconds", type=float, default=DEFAULT_SCAN_SECONDS)
    report.add_argument("--replication-json", type=Path, default=None,
                        help="Local read-only replication observation file")
    report.add_argument("--now", default=None,
                        help="Override the report timestamp (offset required)")
    return parser


def _read_optional_json(path: Path | None) -> dict | None:
    if path is None:
        return None
    try:
        if path.is_symlink() or not path.is_file():
            return None
        if path.stat().st_size > MAX_RECEIPT_BYTES:
            return None
        with path.open("rb") as source:
            raw = source.read(MAX_RECEIPT_BYTES + 1)
        if len(raw) > MAX_RECEIPT_BYTES:
            return None
        data = json.loads(raw.decode("utf-8"))
    except (OSError, ValueError, UnicodeError, RecursionError):
        return None
    return data if isinstance(data, dict) else None


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command != "report":
        parser.error("unknown command")
    try:
        scan_seconds = _validate_scan_seconds(args.max_scan_seconds)
    except ValueError as exc:
        parser.error(str(exc))

    explicit_now = args.now is not None
    if explicit_now:
        try:
            now_dt = datetime.fromisoformat(args.now)
        except ValueError:
            parser.error("--now is not a valid ISO 8601 timestamp")
        if now_dt.tzinfo is None or now_dt.utcoffset() is None:
            parser.error("--now must carry a timezone offset")
        now = args.now
    else:
        # Resolve the collection date up front for scope validation and the
        # SQL gate. The final report timestamp is resolved after collection
        # so fresh observations are not mislabelled as future clock skew.
        now_dt = datetime.now(timezone.utc)
        now = now_dt.isoformat()

    through = args.through or knowledge_status.default_hkt_through(now_dt)

    # Reject an out-of-scope collection window before calling any reader so
    # no filesystem scan happens for an unapproved date range.
    try:
        since_date = datetime.fromisoformat(args.since).date() \
            if isinstance(args.since, str) else None
    except ValueError:
        since_date = None
    try:
        through_date = datetime.fromisoformat(through).date() \
            if isinstance(through, str) else None
    except ValueError:
        through_date = None
    approved_start = datetime.fromisoformat(knowledge_status.APPROVED_WINDOW_START).date()
    current_hkt = datetime.fromisoformat(now).astimezone(HKT).date()
    if since_date is None or through_date is None:
        parser.error("date window values must be valid ISO dates")
    if through_date < since_date:
        parser.error("since must not be later than through")
    if since_date < approved_start:
        parser.error(
            f"since precedes the approved start {knowledge_status.APPROVED_WINDOW_START}"
        )
    if through_date > current_hkt:
        parser.error("through is later than the current UTC+8 date")

    # Canonicalize to date strings only after scope validation so the SQL
    # gate and observed source window compare canonical dates.
    since = since_date.isoformat()
    through = through_date.isoformat()

    state_root = args.state_root or _default_state_root()
    try:
        _enforce_role_binding(state_root=state_root, writer_role=args.writer_role)
    except PermissionError as exc:
        print(f"k2b-knowledge-status: {exc}", file=sys.stderr)
        return 3
    app_root = args.codex_root or _default_app_root()
    automation_root = args.automation_root or _default_automation_root(app_root)
    if args.sessions_root is not None:
        sessions_root = args.sessions_root
    else:
        sessions_root = _default_sessions_root(app_root)

    observations: dict = {
        "source_window": {"since": since, "through": through},
        "scope": {"projects": list(knowledge_status.APPROVED_PROJECTS)},
        "source_coverage": _collect_source_coverage(
            app_root=app_root, sessions_root=sessions_root,
            state_root=state_root, writer_role=args.writer_role,
            since=since, through=through,
            max_scan_seconds=scan_seconds,
            report_today=current_hkt.isoformat(),
        ),
        "delivery": _collect_delivery(state_root=state_root),
        "publication": _collect_publication(state_root=state_root),
        "native_job": _collect_native_job(
            state_root=state_root, automation_root=automation_root,
            writer_role=args.writer_role,
        ),
        "integration": {"status": "unmeasured"},
        "replication": _read_optional_json(args.replication_json) or {},
    }
    if not explicit_now:
        # Collectors timestamp their observations during collection. Re-read
        # the clock after they return so those observations are not seen as
        # future clock skew. The collection date resolved above is retained
        # for scope and the SQL gate.
        now = datetime.now(timezone.utc).isoformat()
    report = knowledge_status.summarize(observations, now=now)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
