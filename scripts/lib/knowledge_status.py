"""Read-only knowledge coverage and freshness report for K2B automatic memory.

The report composes observations supplied by existing owning readers. It
never opens sources, stages work, publishes, writes notes or contacts any
remote host. ``summarize`` is a pure function over plain data; the CLI
collects observations using owning readers in bounded read-only form.

Honesty rules enforced here:

- A successful bounded subset is not complete coverage. Any unavailable
  bucket, changed revision, invalid or future timestamp, missing expected
  reader, or incomplete scan downgrades the report and is retained.
- File-backed and SQL-backed inventories are preserved separately. A union
  label from the caller is not trusted as proof; ``union`` is ``unmeasured``
  unless an owning reader supplies an exact identity-based union.
- Registration of a schedule, an observed native dispatch and an actual run
  outcome are separate fields. A caller assertion of ``observed=...``
  alone is not verified dispatch; only a trusted owning observation (never
  produced by the current CLI) may set it.
- Subject-note integration remains unmeasured until integration receipts
  from the owning writer exist.
"""
from __future__ import annotations

import math
from datetime import date, datetime, timedelta, timezone
from typing import Any


APPROVED_WINDOW_START = "2026-09-13"
APPROVED_PROJECTS = ("K2B", "K2Bi")
SQL_SELECTION = "operator_only"
HKT = timezone(timedelta(hours=8))

# Single source of truth for outbox envelope states that count as
# deliverable backlog. ``accepted`` is a completed delivery and is not
# deliverable; no other business rule is derived from this tuple.
DELIVERABLE_STATES = ("pending", "offline", "failed", "exhausted")

# Age thresholds used only to annotate a result; a stale observation keeps
# its own ``observed_at`` binding rather than being relabelled fresh.
STALENESS_HINT_SECONDS: dict[str, int] = {
    "source_coverage": 3600,
    "delivery": 3600,
    "publication": 7200,
    "integration": 86400,
    "replication": 1800,
    "native_job": 3600,
}

FILE_BUCKET_COUNT_KEYS = (
    "pending_turns",
    "extracted_turns",
    "eligible_turns",
    "excluded_turns",
    "unavailable_turns",
    "invalid_receipts",
    "unverified_turns",
    "source_errors",
    "parse_exceptions",
    "timed_out_sources",
)
SQL_BUCKET_COUNT_KEYS = (
    "pending_turns",
    "extracted_turns",
    "changed_sources",
    "file_backed_pending_turns",
    "invalid_receipts",
    "unverified_turns",
    "source_errors",
    "parse_exceptions",
    "timed_out_sources",
)


def _as_int(value: Any) -> int | None:
    if type(value) is int and value >= 0:
        return value
    return None


def _parse_iso(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed


def default_hkt_through(now: datetime) -> str:
    """Return the UTC+8 calendar date containing ``now`` (offset aware)."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must carry a timezone offset")
    return now.astimezone(HKT).date().isoformat()


def _validate_now(now: Any) -> datetime:
    parsed = _parse_iso(now)
    if parsed is None:
        raise ValueError("now must be an ISO 8601 timestamp with an offset")
    return parsed


def _stale(
    observed_at: Any, now: datetime, threshold: int
) -> tuple[bool | None, str | None, int | None]:
    """Return (stale, diagnostic, age_seconds).

    A future timestamp is clock skew evidence, not freshness proof; it is
    flagged and its own binding preserved.
    """
    parsed = _parse_iso(observed_at)
    if parsed is None:
        return None, None, None
    delta = (now - parsed).total_seconds()
    age = int(delta) if math.isfinite(delta) else None
    if delta < 0:
        return True, "observation timestamp is in the future (clock skew)", age
    return delta > threshold, None, age


def _normalize_source_coverage(raw: Any) -> dict:
    """Normalize reader output without discarding any negative signal."""
    result: dict[str, Any] = {
        "status": "unknown",
        "observed_at": None,
        "observed_at_valid": None,
        "complete": None,
        "partial": None,
        "file_backed": "unknown",
        "sql_backed": "unknown",
        "union": "unmeasured",
        "diagnostics": [],
    }
    if raw is None:
        result["status"] = "unavailable"
        result["complete"] = False
        result["partial"] = True
        result["file_backed"] = "unavailable"
        result["sql_backed"] = "unavailable"
        result["diagnostics"].append("no source coverage observation supplied")
        return result
    if not isinstance(raw, dict):
        result["status"] = "unavailable"
        result["complete"] = False
        result["partial"] = True
        result["file_backed"] = "unavailable"
        result["sql_backed"] = "unavailable"
        result["diagnostics"].append("source coverage observation is malformed")
        return result

    diagnostics = result["diagnostics"]
    observed_raw = raw.get("observed_at")
    if observed_raw is None:
        result["observed_at_valid"] = False
        diagnostics.append("source coverage observed_at is missing")
    elif _parse_iso(observed_raw) is None:
        result["observed_at_valid"] = False
        diagnostics.append("source coverage observed_at is not a valid timestamp")
    else:
        result["observed_at"] = observed_raw
        result["observed_at_valid"] = True

    complete = raw.get("complete")
    if isinstance(complete, bool):
        result["complete"] = complete
        result["partial"] = not complete
    else:
        result["complete"] = None
        result["partial"] = None
        diagnostics.append("complete flag missing or malformed")

    diagnostic = raw.get("diagnostic")
    if isinstance(diagnostic, str) and diagnostic:
        diagnostics.append(diagnostic)

    bucket_ok = {"file_backed": False, "sql_backed": False}
    for bucket, keys in (("file_backed", FILE_BUCKET_COUNT_KEYS),
                         ("sql_backed", SQL_BUCKET_COUNT_KEYS)):
        entry = raw.get(bucket)
        if entry is None:
            result[bucket] = "unavailable"
            diagnostics.append(f"{bucket} observation is missing")
            continue
        if not isinstance(entry, dict):
            result[bucket] = "unavailable"
            diagnostics.append(f"{bucket} observation is malformed")
            continue
        normalized: dict[str, Any] = {
            "status": entry.get("status") if isinstance(entry.get("status"), str)
            else "unknown",
            "complete": entry.get("complete") if isinstance(entry.get("complete"), bool)
            else None,
        }
        for key in keys:
            if key in entry:
                normalized[key] = _as_int(entry.get(key))
        if "oldest_pending_at" in entry:
            oldest = entry.get("oldest_pending_at")
            if oldest is None:
                normalized["oldest_pending_at"] = None
            elif _parse_iso(oldest) is None:
                normalized["oldest_pending_at"] = None
                diagnostics.append(f"{bucket} oldest_pending_at is not a valid timestamp")
            else:
                normalized["oldest_pending_at"] = oldest
        entry_diag = entry.get("diagnostic")
        bucket_status = entry.get("status")
        measured = any(normalized.get(key) is not None for key in keys)
        if bucket_status == "unavailable" and not measured:
            # An unavailable bucket with no measured counts normalizes to the
            # literal unavailable value. Its diagnostic is retained as a
            # named limitation so the negative signal survives the collapse.
            if isinstance(entry_diag, str) and entry_diag:
                diagnostics.append(f"{bucket} observation is unavailable: {entry_diag}")
            result[bucket] = "unavailable"
            continue
        if isinstance(entry_diag, str) and entry_diag:
            normalized["diagnostic"] = entry_diag
        result[bucket] = normalized
        if normalized["status"] in ("ok", "partial", "needs_attention") \
                and normalized["complete"] is True:
            bucket_ok[bucket] = True

    # Never trust a caller's union label alone. Current owning readers do
    # not return stable file identities, so no exact union is accepted from
    # their overlapping aggregates. Keep the label only in an informational
    # field, always as unmeasured.
    raw_union = raw.get("union")
    if raw_union is not None:
        result["caller_union_claim"] = (raw_union if isinstance(raw_union, str) else "malformed")
        diagnostics.append("caller union label was not accepted as exact; union is unmeasured")
    result["union"] = "unmeasured"

    # A combined pending_turns that the caller supplied is an overlapping
    # aggregate; surface it only as ``combined_pending_turns_reported`` and
    # never promote it to ``pending_turns``.
    combined = raw.get("pending_turns")
    if combined is not None:
        result["combined_pending_turns_reported"] = _as_int(combined)

    # Top-level status: complete only when every signal agrees to be
    # complete. Any unavailable, partial, invalid or missing source makes
    # the report partial or unknown, never complete.
    top_complete = result["complete"]
    file_complete = isinstance(result["file_backed"], dict) and \
        result["file_backed"].get("complete") is True
    sql_complete = isinstance(result["sql_backed"], dict) and \
        result["sql_backed"].get("complete") is True
    # A bucket may be reported either as the literal ``unavailable`` or as
    # a mapping whose status is ``unavailable``/``invalid`` or whose
    # complete flag is False. Any such signal must block completion.
    unavailable: dict[str, bool] = {}
    for bucket_name in ("file_backed", "sql_backed"):
        bucket = result.get(bucket_name)
        if bucket == "unavailable":
            unavailable[bucket_name] = True
            continue
        if not isinstance(bucket, dict):
            unavailable[bucket_name] = False
            continue
        bucket_status = bucket.get("status")
        if bucket_status in ("unavailable", "invalid"):
            unavailable[bucket_name] = True
        elif bucket.get("complete") is False:
            unavailable[bucket_name] = True
        else:
            unavailable[bucket_name] = False
    any_unavailable = any(unavailable.values())
    top_blocked = any_unavailable or not file_complete or not sql_complete \
        or result["observed_at_valid"] is not True
    if top_complete is True and not top_blocked:
        result["status"] = "complete"
        result["complete"] = True
        result["partial"] = False
    elif top_complete is False or result["partial"] is True or top_blocked:
        result["status"] = "partial"
        result["complete"] = False
        result["partial"] = True
        if any_unavailable and top_complete is True:
            diagnostics.append("complete flag cannot override an unavailable bucket")
    else:
        # Either top flag is missing/malformed, a bucket is incomplete, or
        # observed_at is invalid. Do not claim completion.
        result["status"] = "partial" if top_complete is not None else "unknown"
        result["complete"] = False
        result["partial"] = True
    return result


def _normalize_native(raw: Any) -> dict:
    result: dict[str, Any] = {
        "registration_state": "unknown",
        "native_schedule": None,
        "native_job_id": None,
        "observed_dispatch": "unknown",
        "dispatch_evidence": "unverified",
        "caller_dispatch_claim": None,
        "last_run_outcome": "unknown",
        "last_completed_at": None,
        "extraction_hold": "unknown",
        "diagnostic": None,
        "observed_at": None,
        "observed_at_valid": None,
    }
    if raw is None:
        result["diagnostic"] = "no native job observation supplied"
        return result
    if not isinstance(raw, dict):
        result["diagnostic"] = "native job observation is malformed"
        return result
    registration = raw.get("registration_state")
    if registration in ("active", "paused", "missing", "unknown"):
        result["registration_state"] = registration
    outcome = raw.get("last_run_outcome")
    if outcome in ("completed", "failed", "unknown"):
        result["last_run_outcome"] = outcome
    hold = raw.get("extraction_hold")
    if hold in ("present", "absent", "unknown"):
        result["extraction_hold"] = hold

    # Dispatch verification: current owning readers provide registration
    # and canonical run receipts only. They cannot prove native-history
    # dispatch, so no supplied JSON can verify dispatch, regardless of any
    # caller-declared ``dispatch_evidence`` string. A caller-set
    # observed_dispatch='observed' is recorded as an unverified claim and
    # the declared evidence is preserved as reported metadata only.
    observed_claim = raw.get("observed_dispatch")
    evidence = raw.get("dispatch_evidence")
    result["observed_dispatch"] = "unknown"
    result["dispatch_evidence"] = "unverified"
    if isinstance(evidence, str) and evidence:
        result["caller_dispatch_evidence_claim"] = evidence
    if observed_claim in ("observed", "not_observed"):
        result["caller_dispatch_claim"] = observed_claim

    if isinstance(raw.get("native_schedule"), str):
        result["native_schedule"] = raw["native_schedule"]
    if isinstance(raw.get("native_job_id"), str):
        result["native_job_id"] = raw["native_job_id"]
    if _parse_iso(raw.get("last_completed_at")) is not None:
        result["last_completed_at"] = raw["last_completed_at"]
    if isinstance(raw.get("diagnostic"), str) and raw["diagnostic"]:
        result["diagnostic"] = raw["diagnostic"]
    observed = raw.get("observed_at")
    if observed is None:
        result["observed_at_valid"] = False
    elif _parse_iso(observed) is None:
        result["observed_at_valid"] = False
        if not result["diagnostic"]:
            result["diagnostic"] = "native observed_at is not a valid timestamp"
    else:
        result["observed_at"] = observed
        result["observed_at_valid"] = True
    return result


def _normalize_delivery(raw: Any) -> dict:
    """Normalize outbox observations.

    The real ``automatic_memory.outbox_status`` returns
    ``counts`` / ``inspected`` / ``total_files`` / ``truncated``; the CLI
    adapts those validated counts into ``pending_envelopes`` derived from
    ``DELIVERABLE_STATES``. Preserve validated counts, ``truncated``,
    ``inspected`` and ``total_files``; missing outbox is unknown, never
    zero.
    """
    result: dict[str, Any] = {
        "status": "unknown",
        "outbox": "unknown",
        "pending_envelopes": None,
        "oldest_pending_at": None,
        "observed_at": None,
        "observed_at_valid": None,
        "counts": None,
        "inspected": None,
        "total_files": None,
        "truncated": None,
        "complete": None,
        "diagnostic": None,
    }
    if raw is None:
        result["status"] = "unavailable"
        result["diagnostic"] = "no delivery observation supplied"
        return result
    if not isinstance(raw, dict):
        result["status"] = "unavailable"
        result["diagnostic"] = "delivery observation is malformed"
        return result

    if isinstance(raw.get("diagnostic"), str):
        result["diagnostic"] = raw["diagnostic"]

    status = raw.get("status")
    if status in ("observed", "partial", "unavailable", "unknown"):
        result["status"] = status
    elif status is None:
        result["status"] = "unknown"
    else:
        result["status"] = "unknown"
        result["diagnostic"] = "delivery status is unrecognized"

    outbox = raw.get("outbox")
    if isinstance(outbox, str):
        result["outbox"] = outbox
    elif isinstance(outbox, dict):
        inner = outbox.get("status")
        if isinstance(inner, str):
            result["outbox"] = inner

    counts = raw.get("counts")
    if isinstance(counts, dict):
        cleaned = {}
        for key, value in counts.items():
            if isinstance(key, str) and type(value) is int and value >= 0:
                cleaned[key] = value
            else:
                result["status"] = "partial"
                result["diagnostic"] = "outbox counts contain malformed entries"
                break
        result["counts"] = cleaned
    elif counts is not None:
        result["status"] = "partial"
        result["diagnostic"] = "outbox counts are malformed"

    for key in ("inspected", "total_files"):
        value = raw.get(key)
        if value is not None:
            result[key] = _as_int(value)
            if result[key] is None:
                result["status"] = "partial"
                result["diagnostic"] = f"outbox {key} is malformed"
    truncated = raw.get("truncated")
    if isinstance(truncated, bool):
        result["truncated"] = truncated
        if truncated:
            result["status"] = "partial"
            if not result["diagnostic"]:
                result["diagnostic"] = "outbox scan was truncated"
    elif truncated is not None:
        result["status"] = "partial"
        result["diagnostic"] = "outbox truncated flag is malformed"

    pending = raw.get("pending_envelopes")
    if pending is None and result["counts"] is not None:
        # Derive pending from the shared deliverable-state constant; never
        # from a missing count. ``accepted`` is not deliverable.
        if all(state in result["counts"] for state in DELIVERABLE_STATES):
            pending = sum(result["counts"][state] for state in DELIVERABLE_STATES)
    if pending is not None:
        result["pending_envelopes"] = _as_int(pending)
        if result["pending_envelopes"] is None:
            result["status"] = "partial"
            if not result["diagnostic"]:
                result["diagnostic"] = "pending envelope count is malformed"
    if result["counts"] is not None and result["inspected"] is not None \
            and result["total_files"] is not None \
            and result["inspected"] < result["total_files"]:
        result["status"] = "partial"
        if not result["diagnostic"]:
            result["diagnostic"] = "outbox scan inspected fewer files than are present"

    if result["status"] == "observed" and result["truncated"] is not True \
            and (result["inspected"] is None or result["total_files"] is None
                 or result["inspected"] >= result["total_files"]):
        result["complete"] = True
    elif result["status"] == "observed":
        result["complete"] = True
    else:
        result["complete"] = False

    oldest = raw.get("oldest_pending_at")
    if oldest is None:
        result["oldest_pending_at"] = None
    elif _parse_iso(oldest) is None:
        result["diagnostic"] = (result["diagnostic"] + "; " if result["diagnostic"] else "") \
            + "oldest_pending_at is not a valid timestamp"
        result["status"] = "partial"
    else:
        result["oldest_pending_at"] = oldest
    observed = raw.get("observed_at")
    if observed is None:
        result["observed_at_valid"] = False
    elif _parse_iso(observed) is None:
        result["observed_at_valid"] = False
        result["status"] = "partial"
    else:
        result["observed_at"] = observed
        result["observed_at_valid"] = True
    return result


def _normalize_publication(raw: Any) -> dict:
    """Normalize recorded publication success receipts.

    Recorded receipt fields are preserved, but artifact verification is
    reported separately. Current owning readers do not verify that the live
    vault artifacts match the receipt. A caller ``artifact_verified`` claim
    is retained as informational metadata only; ``artifact_verified`` is
    always ``unmeasured``. The current owner writes immutable success
    receipts to ``publication`` and staged recovery journals (no timestamp,
    no failed outcome) to ``publication-pending``, so the latest publication
    attempt outcome cannot be measured; ``latest_attempt_outcome`` is always
    ``unmeasured``.
    """
    result: dict[str, Any] = {
        "status": "unknown",
        "published_at": None,
        "snapshot_sha256": None,
        "watermark_size": None,
        "observed_at": None,
        "observed_at_valid": None,
        "lag_seconds": None,
        "artifact_verified": "unmeasured",
        "latest_attempt_outcome": "unmeasured",
        "scan_partial": None,
        "diagnostic": None,
    }
    if raw is None:
        result["status"] = "unavailable"
        result["diagnostic"] = "no publication observation supplied"
        return result
    if not isinstance(raw, dict):
        result["status"] = "unavailable"
        result["diagnostic"] = "publication observation is malformed"
        return result

    status = raw.get("status")
    if status in ("published", "not_attempted", "failed", "no_receipts", "unknown"):
        result["status"] = status
    elif status is None:
        result["status"] = "unknown"
    else:
        result["status"] = "unknown"
        result["diagnostic"] = "publication status is unrecognized"

    published = raw.get("published_at")
    if published is None:
        result["published_at"] = None
    elif _parse_iso(published) is None:
        result["diagnostic"] = "published_at is not a valid timestamp"
    else:
        result["published_at"] = published

    snapshot = raw.get("snapshot_sha256")
    if isinstance(snapshot, str) and len(snapshot) == 64 \
            and all(c in "0123456789abcdef" for c in snapshot):
        result["snapshot_sha256"] = snapshot
    elif snapshot is not None:
        result["diagnostic"] = (result["diagnostic"] + "; " if result["diagnostic"] else "") \
            + "snapshot_sha256 is not a lowercase hex digest"

    watermark = raw.get("reconciliation_watermark")
    if isinstance(watermark, dict):
        result["watermark_size"] = len(watermark)
    size = raw.get("watermark_size")
    if result["watermark_size"] is None and size is not None:
        result["watermark_size"] = _as_int(size)

    scan_partial = raw.get("scan_partial")
    if isinstance(scan_partial, bool):
        result["scan_partial"] = scan_partial
        if scan_partial:
            result["diagnostic"] = (result["diagnostic"] + "; " if result["diagnostic"] else "") \
                + "publication receipt enumeration was truncated"

    # Business rejections in a bounded receipt pass are retained so a
    # rejected-only folder cannot be misreported as an empty folder. The
    # collector counts receipts that parsed but failed schema/status/
    # timestamp/hash validation; the pure normalizer keeps that count and
    # exposes it as a limitation.
    rejected = raw.get("rejected_receipts")
    if rejected is not None:
        normalized_rejected = _as_int(rejected)
        if normalized_rejected is None:
            result["rejected_receipts"] = None
            result["diagnostic"] = (result["diagnostic"] + "; " if result["diagnostic"] else "") \
                + "publication rejected receipt count is malformed"
        else:
            result["rejected_receipts"] = normalized_rejected
            if normalized_rejected > 0:
                result["scan_partial"] = True
                result["diagnostic"] = (result["diagnostic"] + "; " if result["diagnostic"] else "") \
                    + (f"{normalized_rejected} publication receipt(s) were rejected "
                       "by schema, status, timestamp or hash validation")
    collector_diag = raw.get("diagnostic")
    if isinstance(collector_diag, str) and collector_diag and collector_diag != result["diagnostic"]:
        # Preserve the collector diagnostic even when it already shaped the
        # status. Join distinctly so both signals remain visible.
        if not result["diagnostic"]:
            result["diagnostic"] = collector_diag
        elif collector_diag not in result["diagnostic"]:
            result["diagnostic"] = result["diagnostic"] + "; " + collector_diag

    # Artifact verification mirrors native dispatch: current owning readers
    # of publication receipts do not verify that live vault artifacts match
    # the receipt. A caller declaration is retained as reported metadata but
    # never promoted to a verified artifact state.
    result["artifact_verified"] = "unmeasured"
    verified = raw.get("artifact_verified")
    if isinstance(verified, str) and verified:
        result["caller_artifact_verification_claim"] = verified
    # The owner writes success receipts and timestamp-free staged recovery
    # journals, so no observation can establish the latest attempt outcome.
    result["latest_attempt_outcome"] = "unmeasured"

    observed = raw.get("observed_at")
    if observed is None:
        result["observed_at_valid"] = False
    elif _parse_iso(observed) is None:
        result["observed_at_valid"] = False
    else:
        result["observed_at"] = observed
        result["observed_at_valid"] = True
    return result


def _normalize_integration(raw: Any) -> dict:
    """Integration stays unmeasured until an owned integration receipt
    exists. Wiki note edits, mtimes or caller-asserted status cannot measure
    integration. This normalization deliberately never trusts caller status.
    """
    result: dict[str, Any] = {
        "status": "unmeasured",
        "evidence": None,
        "observed_at": None,
        "diagnostic": None,
    }
    if isinstance(raw, dict):
        observed = raw.get("observed_at")
        if isinstance(observed, str) and _parse_iso(observed) is not None:
            result["observed_at"] = observed
        if raw.get("status") == "measured":
            result["diagnostic"] = (
                "caller-declared integration measurement is not an owning receipt; "
                "integration remains unmeasured"
            )
    return result


def _normalize_replication(raw: Any) -> dict:
    result: dict[str, Any] = {
        "status": "unmeasured",
        "peers": "unknown",
        "observed_at": None,
        "observed_at_valid": None,
        "note": None,
        "caveat": (
            "Idle replication does not establish complete source capture or "
            "integrated knowledge."
        ),
    }
    if not isinstance(raw, dict):
        return result
    peers = []
    raw_peers = raw.get("peers")
    if isinstance(raw_peers, list):
        for peer in raw_peers:
            if not isinstance(peer, dict):
                continue
            name = peer.get("name")
            if not isinstance(name, str):
                continue
            seen = peer.get("last_seen_at")
            peers.append({
                "name": name,
                "state": peer.get("state") if peer.get("state") in
                ("idle", "syncing", "offline", "unknown") else "unknown",
                "last_seen_at": seen if _parse_iso(seen) is not None else None,
            })
    if raw_peers is not None and not isinstance(raw_peers, list):
        result["peers"] = "unknown"
    elif peers:
        result["peers"] = peers
    status = raw.get("status")
    if status in ("idle", "syncing", "offline", "unknown"):
        result["status"] = status
    observed = raw.get("observed_at")
    if observed is None:
        result["observed_at_valid"] = False
    elif _parse_iso(observed) is None:
        result["observed_at_valid"] = False
    else:
        result["observed_at"] = observed
        result["observed_at_valid"] = True
    note = raw.get("note")
    if isinstance(note, str):
        result["note"] = note
    return result


def _publication_lag(publication: dict, now: datetime) -> int | None:
    """Return elapsed seconds since the recorded publication event.

    A future ``published_at`` is clock skew evidence against the report
    time, not a negative lag. The caller keeps the receipt binding and
    ``published`` status, but lag is reported as unknown and the skew is
    flagged by ``summarize``. The scan ``observed_at`` is never used here
    because it is the receipt scan time, not the publication event time.
    """
    published = _parse_iso(publication.get("published_at"))
    if published is None:
        return None
    delta = (now - published).total_seconds()
    if not math.isfinite(delta) or delta < 0:
        return None
    try:
        return int(delta)
    except (OverflowError, ValueError):
        return None


def _window(observations: dict, now: datetime) -> tuple[dict, list[str], dict]:
    """Resolve the source window and identify scope anomalies explicitly.

    Default through is the UTC+8 calendar date containing ``now``. A caller
    supplied window is preserved as observed provenance: it is never
    relabelled as if it covered the approved range. A window narrowing the
    approved range is reported as ``narrowed``; a widening (since before the
    approved start) is retained and flagged ``widened``; a future through is
    retained and flagged ``future``. Only an unparseable or inverted window
    is ``invalid`` and falls back to the approved defaults. The CLI rejects
    unapproved collection windows before any reader runs; these flags only
    annotate an already observed window.
    """
    through_default = default_hkt_through(now)
    since = APPROVED_WINDOW_START
    through = through_default
    state = {"kind": "approved", "widened": False, "future": False,
             "invalid": False}
    limitations: list[str] = []
    raw = observations.get("source_window")
    if raw is None:
        return {"since": since, "through": through}, limitations, state
    if not isinstance(raw, dict):
        limitations.append("source window observation is malformed; using approved defaults")
        state = {"kind": "invalid", "widened": False, "future": False,
                 "invalid": True}
        return {"since": since, "through": through}, limitations, state

    candidate_since = raw.get("since")
    candidate_through = raw.get("through")
    if isinstance(candidate_since, str):
        try:
            date.fromisoformat(candidate_since)
        except ValueError:
            limitations.append("source window since is malformed; using approved start")
            state["invalid"] = True
        else:
            since = candidate_since
    elif candidate_since is not None:
        limitations.append("source window since is not a string; using approved start")
        state["invalid"] = True
    if state["invalid"]:
        since = APPROVED_WINDOW_START
    if isinstance(candidate_through, str):
        try:
            date.fromisoformat(candidate_through)
        except ValueError:
            limitations.append("source window through is malformed; using current HKT date")
            state["invalid"] = True
        else:
            through = candidate_through
    elif candidate_through is not None:
        limitations.append("source window through is not a string; using current HKT date")
        state["invalid"] = True
    if state["invalid"]:
        through = through_default

    try:
        since_date = date.fromisoformat(since)
        through_date = date.fromisoformat(through)
    except ValueError:
        return {"since": APPROVED_WINDOW_START, "through": through_default}, limitations, {
            "kind": "invalid", "widened": False, "future": False, "invalid": True}
    approved_start = date.fromisoformat(APPROVED_WINDOW_START)
    if since_date < approved_start:
        limitations.append("source window since precedes the approved start; classified widened")
        state["widened"] = True
    if through_date > date.fromisoformat(through_default):
        limitations.append("source window through is in the future; classified future")
        state["future"] = True
    if since_date > through_date:
        limitations.append("source window since is after through; using approved defaults")
        state["invalid"] = True
        return {"since": APPROVED_WINDOW_START, "through": through_default}, limitations, {
            "kind": "invalid", "widened": False, "future": False, "invalid": True}
    if state["widened"] or state["future"]:
        state["kind"] = "widened" if state["widened"] else "future"
    elif since != APPROVED_WINDOW_START or through != through_default:
        state["kind"] = "narrowed"
    return {"since": since, "through": through}, limitations, state


def _scope(raw: Any, date_window: dict) -> tuple[dict, list[str]]:
    limitations: list[str] = []
    projects = list(APPROVED_PROJECTS)
    excluded: list[str] = []
    if isinstance(raw, dict):
        requested = raw.get("projects")
        if isinstance(requested, list) and all(isinstance(p, str) for p in requested):
            for name in requested:
                if name not in APPROVED_PROJECTS:
                    limitations.append(f"project {name!r} is outside the approved scope")
                    excluded.append(name)
            projects = [p for p in requested if p in APPROVED_PROJECTS]
        raw_excluded = raw.get("excluded_projects")
        if isinstance(raw_excluded, list):
            for name in raw_excluded:
                if isinstance(name, str) and name not in excluded:
                    excluded.append(name)
    return {
        "projects": projects,
        "date_window": date_window,
        "metadata_only": True,
        "excluded_projects": excluded,
        "sql_selection": SQL_SELECTION,
    }, limitations


def summarize(observations: dict, *, now: str) -> dict:
    """Compose a read-only coverage and freshness report.

    ``observations`` is provided by the CLI, which calls existing readers.
    Recognized keys: ``source_window`` ({"since", "through"}), ``scope``,
    ``source_coverage`` (with nested ``file_backed`` and ``sql_backed``),
    ``delivery``, ``publication``, ``integration``, ``replication`` and
    ``native_job``. Any missing or malformed observation is reported as
    unknown or partial rather than zero.
    """
    now_dt = _validate_now(now)
    if not isinstance(observations, dict):
        raise ValueError("observations must be a dictionary")

    window, window_limits, window_state = _window(observations, now_dt)
    scope, scope_limits = _scope(observations.get("scope"), window)

    source = _normalize_source_coverage(observations.get("source_coverage"))
    delivery = _normalize_delivery(observations.get("delivery"))
    publication = _normalize_publication(observations.get("publication"))
    integration = _normalize_integration(observations.get("integration"))
    replication = _normalize_replication(observations.get("replication"))
    native = _normalize_native(observations.get("native_job"))

    publication["lag_seconds"] = _publication_lag(publication, now_dt)
    future_receipt = False
    published_dt = _parse_iso(publication.get("published_at"))
    if published_dt is not None and published_dt > now_dt:
        future_receipt = True
        publication["future_receipt"] = True
    else:
        publication["future_receipt"] = False

    limitations: list[str] = []
    limitations.extend(window_limits)
    limitations.extend(scope_limits)
    for entry in source["diagnostics"]:
        if entry not in limitations:
            limitations.append(entry)

    # Source coverage completeness: never let a top flag override an
    # unavailable bucket, changed revision, invalid timestamp or a missing
    # expected reader. Missing fields stay unknown.
    sql_bucket = source.get("sql_backed")
    if isinstance(sql_bucket, dict) and isinstance(sql_bucket.get("changed_sources"), int) \
            and sql_bucket["changed_sources"] > 0:
        if source["status"] in ("complete", "unknown"):
            source["status"] = "partial"
        source["complete"] = False
        limitations.append(
            f"SQL audit reported changed_sources={sql_bucket['changed_sources']}; "
            "completed SQL history changed after extraction and is not current"
        )
    for bucket_name in ("file_backed", "sql_backed"):
        bucket = source.get(bucket_name)
        if not isinstance(bucket, dict):
            continue
        for failure_key in ("invalid_receipts", "unverified_turns", "source_errors",
                            "parse_exceptions", "timed_out_sources"):
            value = bucket.get(failure_key)
            if isinstance(value, int) and value > 0:
                limitations.append(f"{bucket_name} audit reported {failure_key}={value}")
                source["complete"] = False
                source["partial"] = True
                if source["status"] == "complete":
                    source["status"] = "partial"
        if bucket.get("complete") is False and source["status"] == "complete":
            source["status"] = "partial"
            source["complete"] = False
            limitations.append(
                f"{bucket_name} audit did not complete within its bounded scan"
            )

    stale, stale_note, _ = _stale(
        source.get("observed_at"), now_dt, STALENESS_HINT_SECONDS["source_coverage"])
    source["stale"] = stale
    if stale_note:
        limitations.append(stale_note)
    if stale:
        limitations.append("source coverage observation is older than the staleness hint")
    if source["observed_at_valid"] is False:
        limitations.append("source coverage observed_at is missing or invalid; freshness unknown")
    if source["status"] == "partial":
        limitations.append("source coverage is partial; not every source was scanned")
    if source["status"] in ("unknown", "unavailable"):
        limitations.append("source coverage is unknown or unavailable")
    limitations.append(
        "file-backed and SQL-backed counts are kept separate; no exact union was computed"
    )

    delivery_stale, delivery_note, _ = _stale(
        delivery.get("observed_at"), now_dt, STALENESS_HINT_SECONDS["delivery"])
    delivery["stale"] = delivery_stale
    if delivery["diagnostic"]:
        limitations.append(delivery["diagnostic"])
    if delivery_note:
        limitations.append(delivery_note)
    if delivery["status"] == "unavailable":
        limitations.append("delivery backlog was not observed")
    if delivery["status"] == "partial":
        limitations.append("delivery backlog observation is partial")
        if delivery["oldest_pending_at"] is not None:
            limitations.append("oldest pending delivery age covers inspected envelopes only")
    if delivery["outbox"] in ("unknown", "unavailable"):
        limitations.append("outbox state is unknown")
    if delivery.get("truncated") is True:
        limitations.append("outbox scan was truncated; counts may understate backlog")

    pub_stale, pub_note, _ = _stale(
        publication.get("observed_at"), now_dt, STALENESS_HINT_SECONDS["publication"])
    publication["stale"] = pub_stale
    if pub_note:
        limitations.append(pub_note)
    if publication["status"] == "unavailable":
        limitations.append("published snapshot is unavailable")
    if publication["status"] == "failed":
        limitations.append("latest publication attempt failed")
    limitations.append(
        "latest publication attempt outcome is unmeasured; the current owner "
        "records success receipts but does not record the latest attempt outcome"
    )
    if future_receipt:
        limitations.append(
            "publication receipt timestamp is in the future (clock skew); "
            "lag is unknown and the receipt binding is preserved"
        )
    if publication["status"] in ("unknown", "no_receipts") or publication["lag_seconds"] is None:
        if publication["status"] == "published" and future_receipt:
            limitations.append("publication lag is unknown because the receipt timestamp is in the future")
        else:
            limitations.append("publication watermark or timestamp is unmeasured")
    if publication["scan_partial"] is True:
        limitations.append("publication receipt scan was truncated or contained rejected receipts; latest receipt may be missed")
    if publication.get("diagnostic"):
        limitations.append(publication["diagnostic"])
    if publication["artifact_verified"] != "verified" and publication["status"] == "published":
        limitations.append(
            "publication receipt recorded but live artifact verification is unmeasured"
        )

    # Integration is unmeasured without an owned receipt.
    if integration["diagnostic"]:
        limitations.append(integration["diagnostic"])
    limitations.append(
        "subject-note integration is unmeasured; no integration receipt is produced"
    )

    replication_stale, replication_note, _ = _stale(
        replication.get("observed_at"), now_dt, STALENESS_HINT_SECONDS["replication"])
    replication["stale"] = replication_stale
    if replication_note:
        limitations.append(replication_note)
    if replication.get("peers") == "unknown":
        limitations.append("replication status is unknown")
    elif isinstance(replication.get("peers"), list) and any(
        isinstance(p, dict) and p.get("state") == "offline" for p in replication["peers"]
    ):
        limitations.append("one or more replication peers are offline")
    limitations.append(replication["caveat"])

    if native["registration_state"] in ("missing", "unknown"):
        limitations.append(f"native job registration is {native['registration_state']}")
    if native["observed_dispatch"] == "unknown":
        if native.get("caller_dispatch_claim"):
            limitations.append(
                "native dispatch was asserted by the caller but not verified by an owning observation"
            )
        else:
            limitations.append("native dispatch was not observed")
    if native["last_run_outcome"] == "unknown":
        limitations.append("native run outcome is unknown")
    if native["observed_at_valid"] is False:
        limitations.append("native observation is missing or has an invalid timestamp")
    native_stale, native_note, _ = _stale(
        native.get("observed_at"), now_dt, STALENESS_HINT_SECONDS["native_job"])
    if native_note:
        limitations.append(native_note)

    if window_state["kind"] == "narrowed":
        limitations.append("source window narrowed from the approved default range")

    return {
        "observed_at": now,
        "source_window": {
            "since": window["since"],
            "through": window["through"],
            "approved_start": APPROVED_WINDOW_START,
            "scope_state": window_state,
        },
        "scope": scope,
        "source_coverage": source,
        "delivery": delivery,
        "publication": publication,
        "integration": integration,
        "replication": replication,
        "native_job": {**native, "stale": native_stale},
        "limitations": limitations,
    }
