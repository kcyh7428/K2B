"""Deterministic, runner-independent state for K2B automatic memory.

This module deliberately does not transport envelopes, call a provider, write
the shared vault, or schedule work.  It owns the durable boundary on either
side of a future supported runner: a local SJM outbox, an idempotent Home inbox,
and source-ordered reviewed knowledge with honest recall.
"""

from __future__ import annotations

import fcntl
import hashlib
import errno
import json
import math
import os
import re
import tempfile
import time
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator


BUNDLE_SCHEMA_VERSION = 1
ENVELOPE_SCHEMA_VERSION = 1
MEMORY_SCHEMA_VERSION = 1
MEMORY_SCHEMA_VERSION_V2 = 2
SUPPORTED_MEMORY_SCHEMA_VERSIONS = (MEMORY_SCHEMA_VERSION, MEMORY_SCHEMA_VERSION_V2)
# Correction relationships are additive metadata over already-captured
# assertions.  They never rewrite a record, its key or its item identity.
CORRECTION_RELATIONS = {"supersedes", "retracts"}
ALLOWED_SCOPES = {"K2B", "K2Bi"}
ALLOWED_KINDS = {"fact", "decision", "preference", "commitment"}
ALLOWED_SPEAKERS = {"keith", "assistant_confirmed"}
# Relative speaking authority for correction relations.  A direct Keith
# statement (``keith``) is never overruled by a weaker ``assistant_confirmed``
# claim; capture already forbids decisions, preferences and commitments from
# weaker speakers, so this only compares already-validated provenance.
SPEAKER_AUTHORITY = {"assistant_confirmed": 0, "keith": 1}
ALLOWED_DELIVERY_STATES = {"pending", "offline", "failed", "exhausted", "accepted"}
DELIVERABLE_STATES = {"pending", "offline", "failed"}
ALLOWED_THREAD_SOURCES = {"cli", "codex_app", "desktop", "user", "vscode"}
MAX_BUNDLE_ITEMS = 100
MAX_ITEM_TEXT_CHARS = 65_536
DEFAULT_LOCK_TIMEOUT_SECONDS = 30.0
WORKER_OR_IMPORT_ORIGINS = {
    "agent_created_thread",
    "automation",
    "import",
    "imported",
    "subagent",
    "worker",
}
HEX_64_RE = re.compile(r"^[0-9a-f]{64}$")
SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/-]{0,254}$")
SAFE_KEY_RE = re.compile(r"^[a-z][a-z0-9_.-]{1,126}$")
SAFE_ERROR_RE = re.compile(r"^[a-z][a-z0-9_.-]{0,126}$")
DELIVERY_ID_RE = re.compile(r"^delivery:[0-9a-f]{64}$")
ITEM_ID_RE = re.compile(r"^item:[0-9a-f]{64}$")
CORRECTION_ID_RE = re.compile(r"^correction:[0-9a-f]{64}$")
REVIEW_RECEIPT_ID_RE = re.compile(r"^review:[0-9a-f]{64}$")


class ValidationError(ValueError):
    """An external bundle or envelope violates the durable contract."""


class StateCorruptionError(RuntimeError):
    """Existing durable state is malformed and must not be overwritten."""


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha256_id(prefix: str, value: object) -> str:
    return f"{prefix}:{hashlib.sha256(_canonical_json(value)).hexdigest()}"


def _sha256_hex(value: object) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _validated_iso(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{label} must be a non-empty ISO timestamp string")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValidationError(f"{label} must be an ISO timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValidationError(f"{label} must include a timezone offset")
    return parsed.astimezone(timezone.utc).isoformat(timespec="microseconds")


def _require_exact_keys(value: object, expected: set[str], label: str) -> dict:
    if not isinstance(value, dict):
        raise ValidationError(f"{label} must be an object")
    keys = set(value)
    if keys != expected:
        missing = sorted(expected - keys)
        extra = sorted(keys - expected)
        raise ValidationError(f"{label} fields invalid; missing={missing}, extra={extra}")
    return value


def _required_string(
    value: object,
    label: str,
    *,
    pattern: re.Pattern[str] | None = None,
    max_chars: int | None = None,
) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{label} must be a non-empty string")
    normalized = value.strip()
    if max_chars is not None and len(normalized) > max_chars:
        raise ValidationError(f"{label} must contain at most {max_chars} characters")
    if pattern is not None and pattern.fullmatch(normalized) is None:
        raise ValidationError(f"{label} has unsupported characters")
    return normalized


def validate_bundle(bundle: object) -> dict:
    """Return a detached validated bundle or raise before any durable write."""

    value = _require_exact_keys(
        bundle,
        {"schema_version", "scope", "review_state", "redaction", "source", "items"},
        "bundle",
    )
    if type(value["schema_version"]) is not int or value["schema_version"] != BUNDLE_SCHEMA_VERSION:
        raise ValidationError("bundle schema_version is unsupported")
    if not isinstance(value["scope"], str) or value["scope"] not in ALLOWED_SCOPES:
        raise ValidationError("bundle scope must be K2B or K2Bi")
    if value["review_state"] != "reviewed":
        raise ValidationError("bundle must contain reviewed items")

    redaction = _require_exact_keys(
        value["redaction"], {"status", "raw_dialogue_included"}, "bundle redaction"
    )
    if redaction["status"] != "redacted" or redaction["raw_dialogue_included"] is not False:
        raise ValidationError("bundle must be redacted and exclude raw dialogue")

    source = _require_exact_keys(
        value["source"],
        {
            "host_id",
            "session_id",
            "completed_cursor",
            "completed_at",
            "source_hash",
            "transcript_hash",
            "origin",
            "source_kind",
            "thread_source",
        },
        "bundle source",
    )
    for name in ("host_id", "session_id", "completed_cursor"):
        _required_string(source[name], f"source {name}", pattern=SAFE_ID_RE)
    _validated_iso(source["completed_at"], "source completed_at")
    for name in ("source_hash", "transcript_hash"):
        raw_hash = source[name]
        if not isinstance(raw_hash, str) or HEX_64_RE.fullmatch(raw_hash) is None:
            raise ValidationError(f"source {name} must be a lowercase SHA-256")
    origin_values = {
        str(source.get(name, "")).strip().lower()
        for name in ("origin", "source_kind", "thread_source")
        if isinstance(source.get(name), str)
    }
    if origin_values & WORKER_OR_IMPORT_ORIGINS:
        raise ValidationError("source origin is worker or imported")
    if source["origin"] != "interactive" or source["source_kind"] != "codex_session":
        raise ValidationError("source origin must be an interactive Codex session")
    thread_source = _required_string(
        source["thread_source"], "source thread_source", pattern=SAFE_ID_RE
    )
    if thread_source not in ALLOWED_THREAD_SOURCES:
        raise ValidationError("source origin is unknown and fails closed")

    items = value["items"]
    if not isinstance(items, list) or not items:
        raise ValidationError("bundle items must be a non-empty array")
    if len(items) > MAX_BUNDLE_ITEMS:
        raise ValidationError(f"bundle items must contain at most {MAX_BUNDLE_ITEMS} entries")
    for index, raw_item in enumerate(items):
        item = _require_exact_keys(
            raw_item,
            {
                "key",
                "kind",
                "value",
                "speaker_source",
                "evidence_event_id",
                "evidence_quote",
            },
            f"bundle item {index}",
        )
        _required_string(item["key"], f"bundle item {index} key", pattern=SAFE_KEY_RE)
        if not isinstance(item["kind"], str) or item["kind"] not in ALLOWED_KINDS:
            raise ValidationError(f"bundle item {index} kind is unsupported")
        _required_string(
            item["value"],
            f"bundle item {index} value",
            max_chars=MAX_ITEM_TEXT_CHARS,
        )
        if (
            not isinstance(item["speaker_source"], str)
            or item["speaker_source"] not in ALLOWED_SPEAKERS
        ):
            raise ValidationError(f"bundle item {index} speaker_source is unsupported")
        if (
            item["kind"] in {"decision", "preference", "commitment"}
            and item["speaker_source"] != "keith"
        ):
            raise ValidationError(
                f"bundle item {index} requires Keith evidence for {item['kind']}"
            )
        _required_string(
            item["evidence_event_id"],
            f"bundle item {index} evidence_event_id",
            pattern=SAFE_ID_RE,
        )
        _required_string(
            item["evidence_quote"],
            f"bundle item {index} evidence_quote",
            max_chars=MAX_ITEM_TEXT_CHARS,
        )

    return deepcopy(value)


def make_envelope(
    bundle: object,
    *,
    max_attempts: int = 5,
    now: str | None = None,
    validate: bool = True,
) -> dict:
    """Create an immutable-content envelope with mutable delivery metadata."""

    detached = validate_bundle(bundle) if validate else deepcopy(bundle)
    if type(max_attempts) is not int or not 1 <= max_attempts <= 20:
        raise ValidationError("max_attempts must be an integer from 1 through 20")
    content_id = _sha256_id("bundle", detached)
    delivery_id = _sha256_id("delivery", {"content_id": content_id})
    created_at = now or _now_iso()
    _validated_iso(created_at, "envelope created_at")
    return {
        "schema_version": ENVELOPE_SCHEMA_VERSION,
        "delivery_id": delivery_id,
        "content_id": content_id,
        "bundle": detached,
        "status": "pending",
        "attempt_count": 0,
        "max_attempts": max_attempts,
        "created_at": created_at,
        "last_attempt_at": None,
        "last_error": None,
        "retry_after_seconds": None,
        "home_reconciliation": "pending",
    }


def _validate_envelope(envelope: object, *, durable: bool = False) -> dict:
    error = StateCorruptionError if durable else ValidationError
    try:
        value = _require_exact_keys(
            envelope,
            {
                "schema_version",
                "delivery_id",
                "content_id",
                "bundle",
                "status",
                "attempt_count",
                "max_attempts",
                "created_at",
                "last_attempt_at",
                "last_error",
                "retry_after_seconds",
                "home_reconciliation",
            },
            "outbox envelope" if durable else "envelope",
        )
        if (
            type(value["schema_version"]) is not int
            or value["schema_version"] != ENVELOPE_SCHEMA_VERSION
        ):
            raise ValidationError("envelope schema_version is unsupported")
        bundle = validate_bundle(value["bundle"])
        expected_content = _sha256_id("bundle", bundle)
        expected_delivery = _sha256_id("delivery", {"content_id": expected_content})
        if value["content_id"] != expected_content or value["delivery_id"] != expected_delivery:
            raise ValidationError("envelope content identity does not match bundle")
        if (
            not isinstance(value["status"], str)
            or value["status"] not in ALLOWED_DELIVERY_STATES
        ):
            raise ValidationError("envelope status is unsupported")
        attempts = value["attempt_count"]
        maximum = value["max_attempts"]
        if type(attempts) is not int or attempts < 0:
            raise ValidationError("envelope attempt_count is invalid")
        if type(maximum) is not int or not 1 <= maximum <= 20 or attempts > maximum:
            raise ValidationError("envelope max_attempts is invalid")
        _validated_iso(value["created_at"], "envelope created_at")
        if value["last_attempt_at"] is not None:
            _validated_iso(value["last_attempt_at"], "envelope last_attempt_at")
        if value["last_error"] is not None and (
            not isinstance(value["last_error"], str)
            or SAFE_ERROR_RE.fullmatch(value["last_error"]) is None
        ):
            raise ValidationError("envelope last_error is invalid")
        retry = value["retry_after_seconds"]
        if retry is not None and (type(retry) is not int or retry < 0 or retry > 3600):
            raise ValidationError("envelope retry_after_seconds is invalid")
        if value["home_reconciliation"] != "pending":
            raise ValidationError("envelope cannot claim Home reconciliation")
        status = value["status"]
        pending_state = (
            status == "pending"
            and attempts == 0
            and value["last_attempt_at"] is None
            and value["last_error"] is None
            and retry is None
        )
        retryable_state = (
            status in {"offline", "failed"}
            and 1 <= attempts < maximum
            and value["last_attempt_at"] is not None
            and value["last_error"] is not None
            and retry is not None
            and retry > 0
        )
        exhausted_state = (
            status == "exhausted"
            and attempts == maximum
            and value["last_attempt_at"] is not None
            and value["last_error"] is not None
            and retry is None
        )
        accepted_state = (
            status == "accepted"
            and 1 <= attempts <= maximum
            and value["last_attempt_at"] is not None
            and value["last_error"] is None
            and retry is None
        )
        if not any((pending_state, retryable_state, exhausted_state, accepted_state)):
            raise ValidationError("envelope delivery state is inconsistent")
        return deepcopy(value)
    except ValidationError as exc:
        if durable:
            raise error(f"outbox state is malformed: {exc}") from exc
        raise


@contextmanager
def _lock(
    path: Path, *, timeout_seconds: float | None = None
) -> Iterator[None]:
    timeout = (
        float(os.environ.get("K2B_MEMORY_LOCK_TIMEOUT_SECONDS", "30"))
        if timeout_seconds is None
        else timeout_seconds
    )
    if (
        isinstance(timeout, bool)
        or not isinstance(timeout, (int, float))
        or not math.isfinite(timeout)
        or timeout < 0
    ):
        raise ValidationError("memory lock timeout must be a finite nonnegative number")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        started = time.monotonic()
        while True:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as exc:
                if exc.errno not in (errno.EACCES, errno.EAGAIN):
                    raise
                elapsed = time.monotonic() - started
                if elapsed >= timeout:
                    raise TimeoutError(
                        f"could not acquire memory lock {path} after {timeout:g}s"
                    )
                time.sleep(min(0.05, max(0.0, timeout - elapsed)))
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _atomic_write_json(path: Path, value: object) -> None:
    _atomic_write_bytes(path, _canonical_json(value) + b"\n")


def _atomic_write_bytes(path: Path, payload: bytes) -> None:
    """Atomically replace a file with the exact bytes supplied.

    Structured data uses :func:`_atomic_write_json`; recovery copies need the
    original bytes verbatim so a recorded before-hash still matches the copy.
    """

    path.parent.mkdir(parents=True, exist_ok=True)
    fd, raw_tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    tmp_path = Path(raw_tmp)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if tmp_path.exists():
            tmp_path.unlink()


def _read_json(path: Path, label: str) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise StateCorruptionError(f"{label} is unreadable or malformed: {path}") from exc


def _envelope_path(state_root: Path, delivery_id: str) -> Path:
    _required_string(delivery_id, "delivery_id", pattern=DELIVERY_ID_RE)
    return state_root / "outbox" / f"{delivery_id}.json"


def enqueue_bundle(
    state_root: Path,
    bundle: object,
    *,
    max_attempts: int = 5,
    now: str | None = None,
) -> dict:
    envelope = make_envelope(bundle, max_attempts=max_attempts, now=now)
    path = _envelope_path(state_root, envelope["delivery_id"])
    with _lock(state_root / ".outbox.lock"):
        if path.exists():
            existing = _validate_envelope(_read_json(path, "outbox state"), durable=True)
            if existing["content_id"] != envelope["content_id"]:
                raise StateCorruptionError("outbox delivery identity collision")
            return existing
        _atomic_write_json(path, envelope)
    return deepcopy(envelope)


def _load_outbox_envelope(state_root: Path, delivery_id: str) -> tuple[Path, dict]:
    path = _envelope_path(state_root, delivery_id)
    if not path.exists():
        raise ValidationError(f"delivery_id is not queued: {delivery_id}")
    return path, _validate_envelope(_read_json(path, "outbox state"), durable=True)


def list_deliverable(
    state_root: Path,
    *,
    limit: int = 100,
    now: str | None = None,
) -> list[dict]:
    if type(limit) is not int or not 1 <= limit <= 1000:
        raise ValidationError("delivery batch limit must be an integer from 1 through 1000")
    selection_at = _validated_iso(
        _now_iso() if now is None else now, "delivery selection timestamp"
    )
    selection_time = datetime.fromisoformat(selection_at)
    with _lock(state_root / ".outbox.lock"):
        envelopes: list[dict] = []
        for path in sorted((state_root / "outbox").glob("*.json")):
            envelope = _validate_envelope(_read_json(path, "outbox state"), durable=True)
            if envelope["status"] in DELIVERABLE_STATES:
                if envelope["status"] in {"offline", "failed"}:
                    last_attempt = datetime.fromisoformat(
                        _validated_iso(
                            envelope["last_attempt_at"],
                            "envelope last_attempt_at",
                        )
                    )
                    retry_due = last_attempt + timedelta(
                        seconds=envelope["retry_after_seconds"]
                    )
                    if selection_time < retry_due:
                        continue
                envelopes.append(envelope)
                if len(envelopes) == limit:
                    break
        return envelopes


def load_outbox_envelope(state_root: Path, delivery_id: str) -> dict:
    """Reload one local delivery under the durable outbox lock."""

    with _lock(state_root / ".outbox.lock"):
        _path, envelope = _load_outbox_envelope(state_root, delivery_id)
        return envelope


def record_delivery_attempt(
    state_root: Path,
    delivery_id: str,
    *,
    outcome: str,
    error_code: str | None = None,
    now: str | None = None,
) -> dict:
    if not isinstance(outcome, str) or outcome not in {"offline", "failed", "accepted"}:
        raise ValidationError("delivery outcome must be offline, failed, or accepted")
    if outcome == "accepted" and error_code is not None:
        raise ValidationError("accepted delivery cannot include an error_code")
    if outcome != "accepted" and (
        not isinstance(error_code, str) or SAFE_ERROR_RE.fullmatch(error_code) is None
    ):
        raise ValidationError("failed delivery requires a safe error_code")
    attempt_at = now or _now_iso()
    _validated_iso(attempt_at, "delivery attempt timestamp")

    with _lock(state_root / ".outbox.lock"):
        path, envelope = _load_outbox_envelope(state_root, delivery_id)
        if envelope["status"] == "accepted":
            return envelope
        if envelope["status"] == "exhausted":
            raise ValidationError("delivery retry budget is exhausted")
        envelope["attempt_count"] += 1
        envelope["last_attempt_at"] = attempt_at
        if outcome == "accepted":
            envelope["status"] = "accepted"
            envelope["last_error"] = None
            envelope["retry_after_seconds"] = None
        else:
            envelope["last_error"] = error_code
            if envelope["attempt_count"] >= envelope["max_attempts"]:
                envelope["status"] = "exhausted"
                envelope["retry_after_seconds"] = None
            else:
                envelope["status"] = outcome
                envelope["retry_after_seconds"] = min(
                    3600, 60 * (2 ** (envelope["attempt_count"] - 1))
                )
        _validate_envelope(envelope, durable=True)
        _atomic_write_json(path, envelope)
        return deepcopy(envelope)


def accept_home_envelope(home_root: Path, envelope: object) -> dict:
    validated = _validate_envelope(envelope)
    delivery_id = validated["delivery_id"]
    inbox_path = home_root / "inbox" / f"{delivery_id}.json"
    acceptance_path = home_root / "acceptance" / f"{delivery_id}.json"
    with _lock(home_root / ".inbox.lock"):
        duplicate = inbox_path.exists()
        if not duplicate and validated["status"] not in DELIVERABLE_STATES:
            raise ValidationError(
                f"Home cannot first accept a {validated['status']} delivery"
            )
        if duplicate:
            existing = _validate_envelope(_read_json(inbox_path, "Home inbox state"))
            if existing["content_id"] != validated["content_id"]:
                raise StateCorruptionError("Home inbox delivery identity collision")
        else:
            _atomic_write_json(inbox_path, validated)
        receipt = {
            "schema_version": 1,
            "delivery_id": delivery_id,
            "content_id": validated["content_id"],
            "status": "accepted_not_reconciled",
        }
        if acceptance_path.exists():
            existing_receipt = _read_json(acceptance_path, "Home acceptance state")
            if existing_receipt != receipt:
                raise StateCorruptionError("Home acceptance state conflicts with inbox")
        else:
            _atomic_write_json(acceptance_path, receipt)
    return {
        "delivery_id": delivery_id,
        "content_id": validated["content_id"],
        "status": "accepted_not_reconciled",
        "duplicate": duplicate,
    }


def _empty_memory_state() -> dict:
    return {"schema_version": MEMORY_SCHEMA_VERSION, "records": {}}


def _item_identity(
    *,
    source_identity: str,
    key: str,
    kind: str,
    value: str,
    evidence_quote: str,
    speaker_source: str,
) -> str:
    return _sha256_id(
        "item",
        {
            "source_identity": source_identity,
            "key": key,
            "kind": kind,
            "value": value,
            "evidence_quote": evidence_quote,
            "speaker_source": speaker_source,
        },
    )


def _validate_memory_state(value: object) -> dict:
    """Validate an on-disk or published memory file and normalize it in memory.

    The returned shape always carries a ``corrections`` collection so callers
    never branch on the file version.  The file forms stay exact: v1 is
    ``{schema_version, records}`` and v2 is
    ``{schema_version, records, corrections}``.
    """

    if not isinstance(value, dict):
        raise StateCorruptionError("memory state must be an object")
    version = value.get("schema_version")
    if type(version) is not int or version not in SUPPORTED_MEMORY_SCHEMA_VERSIONS:
        raise StateCorruptionError("memory state schema_version is unsupported")
    if version == MEMORY_SCHEMA_VERSION:
        if set(value) != {"schema_version", "records"}:
            raise StateCorruptionError(
                "memory state must contain schema_version and records"
            )
        raw_corrections: list = []
    else:
        if set(value) != {"schema_version", "records", "corrections"}:
            raise StateCorruptionError(
                "memory state v2 must contain schema_version, records and corrections"
            )
        if not isinstance(value["corrections"], list):
            raise StateCorruptionError("memory state corrections must be an array")
        raw_corrections = value["corrections"]
    return _validate_records_and_corrections(value["records"], raw_corrections, version)


def _validate_normalized_state(value: object) -> dict:
    """Validate an already-normalized state held only in memory."""

    if not isinstance(value, dict) or set(value) != {
        "schema_version",
        "records",
        "corrections",
    }:
        raise StateCorruptionError(
            "normalized memory state must contain schema_version, records and corrections"
        )
    version = value.get("schema_version")
    if type(version) is not int or version not in SUPPORTED_MEMORY_SCHEMA_VERSIONS:
        raise StateCorruptionError("memory state schema_version is unsupported")
    if not isinstance(value["corrections"], list):
        raise StateCorruptionError("memory state corrections must be an array")
    if version == MEMORY_SCHEMA_VERSION and value["corrections"]:
        raise StateCorruptionError("memory state v1 cannot carry corrections")
    return _validate_records_and_corrections(value["records"], value["corrections"], version)


def _state_from_snapshot(value: object) -> dict:
    """Normalize either an in-memory state or a raw published file form.

    Published memory is the serialized file form, so a caller that hands
    :func:`recall_snapshot` the parsed JSON of a v1 snapshot would otherwise be
    rejected merely for lacking the in-memory ``corrections`` key.  Accept both
    shapes so the reader stays backward compatible with v1 publications.
    """

    if isinstance(value, dict) and "corrections" in value:
        return _validate_normalized_state(value)
    return _validate_memory_state(value)


def _validate_records_and_corrections(
    records: object, raw_corrections: list, schema_version: int
) -> dict:
    if not isinstance(records, dict):
        raise StateCorruptionError("memory state records must be an object")
    value = {"records": records}
    for key, record in value["records"].items():
        if not isinstance(key, str) or SAFE_KEY_RE.fullmatch(key) is None:
            raise StateCorruptionError("memory state contains an invalid key")
        if not isinstance(record, dict) or set(record) != {"kind", "versions"}:
            raise StateCorruptionError(f"memory record {key} is malformed")
        if (
            not isinstance(record["kind"], str)
            or record["kind"] not in ALLOWED_KINDS
            or not isinstance(record["versions"], list)
            or not record["versions"]
        ):
            raise StateCorruptionError(f"memory record {key} versions are malformed")
        for version in record["versions"]:
            if not isinstance(version, dict) or set(version) != {
                "item_id",
                "value",
                "source_time",
                "source_identity",
                "citation",
                "evidence_quote",
                "speaker_source",
            }:
                raise StateCorruptionError(f"memory record {key} has a malformed version")
            if (
                not isinstance(version["item_id"], str)
                or re.fullmatch(r"item:[0-9a-f]{64}", version["item_id"]) is None
            ):
                raise StateCorruptionError(f"memory record {key} has an invalid item identity")
            if not isinstance(version["value"], str) or not version["value"]:
                raise StateCorruptionError(f"memory record {key} has an invalid value")
            try:
                _validated_iso(version["source_time"], "memory source_time")
            except ValidationError as exc:
                raise StateCorruptionError(f"memory record {key} has invalid source time") from exc
            citation = version["citation"]
            if not isinstance(citation, dict) or set(citation) != {
                "host_id",
                "session_id",
                "completed_cursor",
                "completed_at",
                "event_id",
                "source_hash",
                "transcript_hash",
            }:
                raise StateCorruptionError(f"memory record {key} has invalid citation")
            try:
                evidence_quote = _required_string(
                    version["evidence_quote"],
                    "memory evidence_quote",
                    max_chars=MAX_ITEM_TEXT_CHARS,
                )
                speaker_source = version["speaker_source"]
                if (
                    not isinstance(speaker_source, str)
                    or speaker_source not in ALLOWED_SPEAKERS
                ):
                    raise ValidationError("memory speaker_source is unsupported")
                if (
                    record["kind"] in {"decision", "preference", "commitment"}
                    and speaker_source != "keith"
                ):
                    raise ValidationError("memory kind requires Keith evidence")
                for citation_key in (
                    "host_id",
                    "session_id",
                    "completed_cursor",
                    "event_id",
                ):
                    _required_string(
                        citation[citation_key],
                        f"memory citation {citation_key}",
                        pattern=SAFE_ID_RE,
                    )
                citation_time = _validated_iso(
                    citation["completed_at"], "memory citation completed_at"
                )
                for hash_key in ("source_hash", "transcript_hash"):
                    if (
                        not isinstance(citation[hash_key], str)
                        or HEX_64_RE.fullmatch(citation[hash_key]) is None
                    ):
                        raise ValidationError(f"memory citation {hash_key} is invalid")
            except (KeyError, ValidationError) as exc:
                raise StateCorruptionError(
                    f"memory record {key} has invalid citation"
                ) from exc
            expected_source_identity = _sha256_id("source", citation)
            if version["source_identity"] != expected_source_identity:
                raise StateCorruptionError(
                    f"memory record {key} has invalid source identity"
                )
            expected_item_identity = _item_identity(
                source_identity=expected_source_identity,
                key=key,
                kind=record["kind"],
                value=version["value"],
                evidence_quote=evidence_quote,
                speaker_source=speaker_source,
            )
            if version["item_id"] != expected_item_identity:
                raise StateCorruptionError(
                    f"memory record {key} has invalid item identity"
                )
            if citation_time != version["source_time"]:
                raise StateCorruptionError(
                    f"memory record {key} citation time conflicts with source time"
                )
    corrections = _validate_corrections(raw_corrections, value["records"])
    return {
        "schema_version": schema_version,
        "records": deepcopy(value["records"]),
        "corrections": corrections,
    }


def _memory_state_to_json(state: dict) -> dict:
    """Serialize a normalized state without silently downgrading a v2 file."""
    if (
        state.get("schema_version") == MEMORY_SCHEMA_VERSION_V2
        or state.get("corrections")
    ):
        return {
            "schema_version": MEMORY_SCHEMA_VERSION_V2,
            "records": deepcopy(state["records"]),
            "corrections": deepcopy(state.get("corrections", [])),
        }
    return {
        "schema_version": MEMORY_SCHEMA_VERSION,
        "records": deepcopy(state["records"]),
    }


def _item_index(records: dict) -> dict[str, tuple[str, str, dict]]:
    index: dict[str, tuple[str, str, dict]] = {}
    for key, record in records.items():
        for version in record["versions"]:
            index[version["item_id"]] = (key, record["kind"], version)
    return index


def _evidence_sha256(item_id: str, version: dict) -> str:
    """Bind one captured assertion, its provenance and its wording."""
    return _sha256_hex(
        {
            "item_id": item_id,
            "source_identity": version["source_identity"],
            "value": version["value"],
            "evidence_quote": version["evidence_quote"],
            "speaker_source": version["speaker_source"],
            "citation": version["citation"],
        }
    )

def _validated_relation_reference(
    value: object, label: str, *, allow_topic: bool = False
) -> str:
    text = _required_string(value, label, max_chars=255)
    if text.startswith("/") or "\\" in text or ".." in text.split("/"):
        raise ValidationError(f"{label} must be a bounded relative reference")
    if not allow_topic and SAFE_ID_RE.fullmatch(text) is None:
        raise ValidationError(f"{label} has unsupported characters")
    if text in {"", ".", ".."}:
        raise ValidationError(f"{label} is not a usable reference")
    return text


def _validate_correction(correction: object, index: dict) -> dict:
    value = _require_exact_keys(
        correction,
        {
            "correction_id",
            "relation",
            "target_item_id",
            "source_item_id",
            "topic_ref",
            "field",
            "evidence_refs",
            "review_receipt_id",
        },
        "correction",
    )
    core = _validate_relation_core(value, index)
    receipt = _required_string(
        value["review_receipt_id"], "correction review_receipt_id", pattern=REVIEW_RECEIPT_ID_RE
    )
    expected_id = _sha256_id(
        "correction", {**core, "review_receipt_id": receipt}
    )
    if value["correction_id"] != expected_id or CORRECTION_ID_RE.fullmatch(str(value["correction_id"])) is None:
        raise ValidationError("correction identity does not match its bound content")
    return {"correction_id": expected_id, **core, "review_receipt_id": receipt}


def _validate_relation_core(value: object, index: dict) -> dict:
    """Validate the six relation fields every correction and proposal shares.

    This binds the target/source item identities and the exact evidence item
    set to durable memory.  It never trusts a caller-supplied review flag.
    """

    if not isinstance(value["relation"], str) or value["relation"] not in CORRECTION_RELATIONS:
        raise ValidationError("correction relation is unsupported")
    target = _required_string(value["target_item_id"], "correction target_item_id", pattern=ITEM_ID_RE)
    source = _required_string(value["source_item_id"], "correction source_item_id", pattern=ITEM_ID_RE)
    if target == source:
        raise ValidationError("correction cannot target its own source item")
    if target not in index:
        raise ValidationError("correction target item is not in durable memory")
    if source not in index:
        raise ValidationError("correction source item is not in durable memory")
    _validate_relation_authority(index[target][2], index[source][2])
    topic_ref = _validated_relation_reference(
        value["topic_ref"], "correction topic_ref", allow_topic=True
    )
    field = _required_string(value["field"], "correction field", pattern=SAFE_KEY_RE)
    refs = value["evidence_refs"]
    if not isinstance(refs, list) or not refs or len(refs) > 32:
        raise ValidationError("correction evidence_refs must be a bounded non-empty array")
    for ref in refs:
        item = _required_string(ref, "correction evidence_ref", pattern=ITEM_ID_RE)
        if item not in index:
            raise ValidationError("correction evidence_ref is not in durable memory")
    if target not in refs or source not in refs:
        raise ValidationError("correction evidence_refs must bind the target and source items")
    return {
        "relation": value["relation"],
        "target_item_id": target,
        "source_item_id": source,
        "topic_ref": topic_ref,
        "field": field,
        "evidence_refs": list(refs),
    }


def _validate_relation_authority(target_version: dict, source_version: dict) -> None:
    """Enforce source chronology and speaker authority for one relation.

    The recorded ``source_time`` is the event time the capture already bound;
    file arrival order is never consulted.  A correction must originate at a
    strictly later recorded event than the assertion it overrules, and a
    weaker speaker may not overrule a stronger one (in particular, an
    assistant-confirmed claim may never supersede or retract Keith's own
    statement).  The placeholder index used for structural receipt checks
    carries no version data, so it skips these semantic checks here and relies
    on the real durable index at apply time.
    """

    target_time = target_version.get("source_time")
    source_time = source_version.get("source_time")
    if isinstance(target_time, str) and isinstance(source_time, str):
        if datetime.fromisoformat(source_time) <= datetime.fromisoformat(target_time):
            raise ValidationError(
                "correction source must be recorded strictly later than its target"
            )
    target_speaker = target_version.get("speaker_source")
    source_speaker = source_version.get("speaker_source")
    if target_speaker in SPEAKER_AUTHORITY and source_speaker in SPEAKER_AUTHORITY:
        if SPEAKER_AUTHORITY[source_speaker] < SPEAKER_AUTHORITY[target_speaker]:
            raise ValidationError(
                "correction source has weaker speaking authority than its target"
            )


def _validate_corrections(corrections: list, records: dict) -> list[dict]:
    index = _item_index(records)
    validated: list[dict] = []
    seen: set[str] = set()
    for raw in corrections:
        try:
            correction = _validate_correction(raw, index)
        except ValidationError as exc:
            raise StateCorruptionError(f"memory state correction is invalid: {exc}") from exc
        if correction["correction_id"] in seen:
            # A durable collection must not silently drop a paid-for relation;
            # duplicate ids are an identity collision, not a no-op.
            raise StateCorruptionError("memory state has a duplicate correction id")
        seen.add(correction["correction_id"])
        validated.append(correction)
    try:
        _reject_correction_cycles(validated)
    except ValidationError as exc:
        raise StateCorruptionError(f"memory state correction is invalid: {exc}") from exc
    return validated


def _reject_correction_cycles(corrections: list[dict]) -> None:
    edges: dict[str, list[str]] = {}
    for correction in corrections:
        if correction["relation"] == "supersedes":
            edges.setdefault(correction["target_item_id"], []).append(
                correction["source_item_id"]
            )
    state: dict[str, int] = {}
    for node in list(edges):
        stack = [(node, iter(edges.get(node, [])))]
        state[node] = 1
        while stack:
            current, children = stack[-1]
            try:
                child = next(children)
            except StopIteration:
                state[current] = 2
                stack.pop()
                continue
            mark = state.get(child, 0)
            if mark == 1:
                raise ValidationError("correction supersedes chain contains a cycle")
            if mark == 0:
                state[child] = 1
                stack.append((child, iter(edges.get(child, []))))


def _load_memory(memory_path: Path) -> dict:
    if not memory_path.exists():
        return _empty_memory_state()
    return _validate_memory_state(_read_json(memory_path, "memory state"))


def _version(bundle: dict, item: dict) -> dict:
    source = bundle["source"]
    citation = {
        "host_id": source["host_id"],
        "session_id": source["session_id"],
        "completed_cursor": source["completed_cursor"],
        "completed_at": source["completed_at"],
        "event_id": item["evidence_event_id"],
        "source_hash": source["source_hash"],
        "transcript_hash": source["transcript_hash"],
    }
    source_identity = _sha256_id("source", citation)
    item_identity = _item_identity(
        source_identity=source_identity,
        key=item["key"],
        kind=item["kind"],
        value=item["value"],
        evidence_quote=item["evidence_quote"],
        speaker_source=item["speaker_source"],
    )
    return {
        "item_id": item_identity,
        "value": item["value"],
        "source_time": _validated_iso(source["completed_at"], "source completed_at"),
        "source_identity": source_identity,
        "citation": citation,
        "evidence_quote": item["evidence_quote"],
        "speaker_source": item["speaker_source"],
    }


def apply_reviewed_envelope(memory_path: Path, envelope: object) -> dict:
    validated = _validate_envelope(envelope)
    bundle = validated["bundle"]
    # Validate every item and derive every version before acquiring the writer
    # lock.  A malformed later item therefore cannot partially mutate state.
    planned = [(item, _version(bundle, item)) for item in bundle["items"]]

    with _lock(memory_path.with_name(f".{memory_path.name}.lock")):
        state = _load_memory(memory_path)
        candidate = deepcopy(state)
        applied = 0
        duplicates = 0
        keys: set[str] = set()
        for item, version in planned:
            key = item["key"]
            keys.add(key)
            record = candidate["records"].setdefault(
                key, {"kind": item["kind"], "versions": []}
            )
            if record["kind"] != item["kind"]:
                raise ValidationError(f"memory key {key} cannot change kind")
            if any(existing["item_id"] == version["item_id"] for existing in record["versions"]):
                duplicates += 1
                continue
            record["versions"].append(version)
            record["versions"].sort(
                key=lambda row: (row["source_time"], row["source_identity"], row["item_id"])
            )
            applied += 1
        serialized = _memory_state_to_json(candidate)
        _validate_memory_state(serialized)
        if applied:
            _atomic_write_json(memory_path, serialized)
        return {"applied": applied, "duplicates": duplicates, "keys": sorted(keys)}


def _public_version(version: dict, status: str) -> dict:
    return {
        "value": version["value"],
        "status": status,
        "citation": deepcopy(version["citation"]),
    }


def _recall_record(key: str, kind: str, versions: list[dict]) -> dict:
    ordered = sorted(
        versions,
        key=lambda row: (row["source_time"], row["source_identity"], row["item_id"]),
    )
    latest_time = ordered[-1]["source_time"]
    latest = [row for row in ordered if row["source_time"] == latest_time]
    distinct_values = {row["value"] for row in latest}
    older = [row for row in ordered if row["source_time"] != latest_time]
    history = [_public_version(row, "superseded") for row in older]
    if len(distinct_values) > 1:
        alternatives = [_public_version(row, "unresolved") for row in latest]
        alternatives.sort(
            key=lambda row: (
                row["citation"]["host_id"],
                row["citation"]["session_id"],
                row["citation"]["event_id"],
            )
        )
        return {
            "key": key,
            "kind": kind,
            "status": "unresolved",
            "value": None,
            "citation": None,
            "alternatives": alternatives,
            "history": history,
        }
    chosen = min(latest, key=lambda row: (row["source_identity"], row["item_id"]))
    corroborating = [row for row in latest if row["item_id"] != chosen["item_id"]]
    history.extend(_public_version(row, "corroborating") for row in corroborating)
    return {
        "key": key,
        "kind": kind,
        "status": "current",
        "value": chosen["value"],
        "citation": deepcopy(chosen["citation"]),
        "history": history,
    }


def _terminal_correction_state(
    index: dict[str, tuple[str, str, dict]],
    by_target: dict[str, list[dict]],
    item_id: str,
    seen: frozenset[str],
) -> dict:
    """Follow accepted correction links to a terminal disposition for one item.

    A ``supersedes`` chain resolves transitively to the live replacement.  A
    ``retracts`` link only stands while its own source item is still the live
    assertion: if the retracting item is itself superseded or retracted, the
    retraction is cancelled and the original item becomes applicable again.
    That resolves a correction-of-a-correction (``B`` retracts ``A``, a later
    ``C`` supersedes ``B``) instead of stopping at the first relation.  The
    stored correction records are never mutated here; only the derived read
    disposition changes, so the full history is preserved.
    """
    if item_id in seen:
        return {
            "status": "unresolved",
            "reason": "correction_cycle",
            "alternatives": [item_id],
        }
    corrections = by_target.get(item_id)
    if not corrections:
        return {"status": "current", "item_id": item_id}
    successors = {correction["source_item_id"] for correction in corrections}
    relations = {correction["relation"] for correction in corrections}
    if len(successors) > 1 or len(relations) > 1:
        return {
            "status": "unresolved",
            "reason": "competing_successors",
            "alternatives": sorted(successors),
        }
    correction = corrections[0]
    source_id = correction["source_item_id"]
    # Resolve the correcting item first: a link is only live while its own
    # source is still the terminal assertion.  ``current`` with a different
    # ``item_id`` means the source was itself superseded; ``retracted`` means
    # the source was withdrawn; a cycle or competing branch is unresolved.
    source_state = _terminal_correction_state(
        index,
        by_target,
        source_id,
        seen | {item_id},
    )
    if source_state["status"] == "unresolved":
        return source_state
    source_live = (
        source_state["status"] == "current"
        and source_state.get("item_id") == source_id
    )
    if not source_live:
        # The correcting item is no longer live, so this relation no longer
        # stands and the target falls back to its own legacy state.  A
        # supersession instead follows through to the newer replacement.
        if correction["relation"] == "supersedes" and source_state["status"] == "current":
            return source_state
        return {"status": "current", "item_id": item_id}
    if correction["relation"] == "retracts":
        return {
            "status": "retracted",
            "item_id": item_id,
            "correction_id": correction["correction_id"],
        }
    return {"status": "current", "item_id": source_id}


def _correction_citations(index: dict, item_ids: list[str]) -> list[dict]:
    citations = []
    for item_id in item_ids:
        entry = index.get(item_id)
        if entry is None:
            continue
        key, kind, version = entry
        citations.append(
            {
                "key": key,
                "kind": kind,
                "item_id": item_id,
                "value": version["value"],
                "citation": deepcopy(version["citation"]),
            }
        )
    citations.sort(
        key=lambda row: (
            row["citation"]["host_id"],
            row["citation"]["session_id"],
            row["citation"]["event_id"],
        )
    )
    return citations


def _record_dispositions(
    state: dict, key: str, record: dict
) -> list[tuple[dict, str, str | None]]:
    """Per-version disposition for one record, never a whole-key status.

    Older versions of a key are historical/superseded rather than inheriting
    the latest version's ``current`` status, and an accepted correction marks
    only its own target item.
    """

    versions = sorted(
        record["versions"],
        key=lambda row: (row["source_time"], row["source_identity"], row["item_id"]),
    )
    index = _item_index(state["records"])
    by_target: dict[str, list[dict]] = {}
    for correction in state.get("corrections", []):
        by_target.setdefault(correction["target_item_id"], []).append(correction)
    latest_time = versions[-1]["source_time"]
    latest = [row for row in versions if row["source_time"] == latest_time]
    distinct_values = {row["value"] for row in latest}
    legacy_unresolved = len(distinct_values) > 1
    chosen_value = None
    if not legacy_unresolved:
        chosen_value = min(latest, key=lambda row: (row["source_identity"], row["item_id"]))[
            "value"
        ]
    dispositions: list[tuple[dict, str, str | None]] = []
    for version in versions:
        terminal = _terminal_correction_state(
            index, by_target, version["item_id"], frozenset()
        )
        if terminal["status"] == "retracted":
            dispositions.append((version, "retracted", None))
            continue
        if terminal["status"] == "unresolved":
            dispositions.append((version, "unresolved", None))
            continue
        if terminal.get("item_id") not in (None, version["item_id"]):
            dispositions.append((version, "superseded", terminal["item_id"]))
            continue
        if legacy_unresolved:
            disposition = "unresolved" if version["source_time"] == latest_time else "superseded"
        elif version["source_time"] == latest_time and version["value"] == chosen_value:
            disposition = "current"
        else:
            disposition = "superseded"
        dispositions.append((version, disposition, None))
    return dispositions


def item_dispositions(state: dict, key: str) -> dict[str, dict]:
    """Map each item id under one key to its validated disposition."""

    record = state["records"].get(key)
    if record is None:
        return {}
    return {
        version["item_id"]: {
            "disposition": disposition,
            "replacement_item_id": replacement,
        }
        for version, disposition, replacement in _record_dispositions(state, key, record)
    }


def _recall_with_corrections(state: dict, key: str, record: dict) -> dict:
    """Resolve accepted corrections over the legacy same-key ordering."""
    base = _recall_record(key, record["kind"], record["versions"])
    corrections = state.get("corrections", [])
    if not corrections:
        return base
    index = _item_index(state["records"])
    item_ids = {version["item_id"] for version in record["versions"]}
    by_target: dict[str, list[dict]] = {}
    for correction in corrections:
        by_target.setdefault(correction["target_item_id"], []).append(correction)
    if not item_ids & set(by_target):
        return base
    ordered = sorted(
        record["versions"],
        key=lambda row: (row["source_time"], row["source_identity"], row["item_id"]),
    )
    latest_time = ordered[-1]["source_time"]
    latest = [row for row in ordered if row["source_time"] == latest_time]
    if base["status"] == "unresolved":
        candidates = latest
    else:
        chosen_value = base["value"]
        candidates = [row for row in latest if row["value"] == chosen_value]
    dispositions = [
        _terminal_correction_state(index, by_target, row["item_id"], frozenset())
        for row in candidates
    ]
    if not dispositions:
        return base
    unchanged = all(
        disposition["status"] == "current"
        and disposition.get("item_id") == candidate["item_id"]
        for disposition, candidate in zip(dispositions, candidates)
    )
    if unchanged:
        return base
    if any(item["status"] == "unresolved" for item in dispositions):
        alternatives: list[dict] = []
        for item in dispositions:
            if item["status"] == "unresolved" and "alternatives" in item:
                alternatives.extend(
                    _correction_citations(index, item["alternatives"])
                )
        return {
            "key": key,
            "kind": record["kind"],
            "status": "unresolved",
            "value": None,
            "citation": None,
            "alternatives": alternatives,
            "history": base.get("history", []),
        }
    terminal_statuses = {item["status"] for item in dispositions}
    if len(terminal_statuses) > 1 or len({item.get("item_id") for item in dispositions}) > 1:
        alternatives = _correction_citations(
            index, sorted({item.get("item_id") for item in dispositions if item.get("item_id")})
        )
        return {
            "key": key,
            "kind": record["kind"],
            "status": "unresolved",
            "value": None,
            "citation": None,
            "alternatives": alternatives,
            "history": base.get("history", []),
        }
    original = candidates[0]
    disposition = dispositions[0]
    history = list(base.get("history", []))
    history.append(_public_version(original, disposition["status"]))
    if disposition["status"] == "retracted":
        return {
            "key": key,
            "kind": record["kind"],
            "status": "retracted",
            "value": None,
            "citation": deepcopy(original["citation"]),
            "original_item_id": original["item_id"],
            "history": history,
        }
    replacement_key, replacement_kind, replacement_version = index[disposition["item_id"]]
    return {
        "key": key,
        "kind": record["kind"],
        "status": "superseded",
        "value": None,
        "citation": deepcopy(original["citation"]),
        "original_item_id": original["item_id"],
        "replacement": {
            "key": replacement_key,
            "kind": replacement_kind,
            "item_id": disposition["item_id"],
            "value": replacement_version["value"],
            "citation": deepcopy(replacement_version["citation"]),
        },
        "history": history,
    }


def _provisional_versions(state_root: Path, key: str) -> dict[str, list[dict]]:
    versions_by_kind: dict[str, list[dict]] = {}
    with _lock(state_root / ".outbox.lock"):
        for path in sorted((state_root / "outbox").glob("*.json")):
            envelope = _validate_envelope(_read_json(path, "outbox state"), durable=True)
            if envelope["status"] == "exhausted":
                continue
            for item in envelope["bundle"]["items"]:
                if item["key"] == key:
                    versions_by_kind.setdefault(item["kind"], []).append(
                        _version(envelope["bundle"], item)
                    )
    return versions_by_kind


def _unresolved_kind_conflict(
    key: str,
    versions_by_kind: dict[str, list[dict]],
    *,
    home_result: dict | None = None,
) -> dict:
    alternatives: list[dict] = []
    if home_result is not None:
        alternatives.append(home_result)
    for kind in sorted(versions_by_kind):
        result = _recall_record(key, kind, versions_by_kind[kind])
        if result["status"] == "unresolved":
            alternatives.extend(
                {**alternative, "kind": kind}
                for alternative in result["alternatives"]
            )
        else:
            alternatives.append(result)
    return {
        "key": key,
        "kind": None,
        "status": "unresolved",
        "value": None,
        "citation": None,
        "alternatives": alternatives,
        "history": [],
        "pending_home": True,
        **({"home_current": home_result} if home_result is not None else {}),
    }


def recall(
    memory_path: Path,
    key: str,
    *,
    provisional_outbox: Path | None = None,
    read_only: bool = False,
) -> dict:
    _required_string(key, "recall key", pattern=SAFE_KEY_RE)
    if type(read_only) is not bool:
        raise ValidationError("recall read_only must be a boolean")
    if read_only:
        state = _load_memory(memory_path)
    else:
        with _lock(memory_path.with_name(f".{memory_path.name}.lock")):
            state = _load_memory(memory_path)
    record = state["records"].get(key)
    home_result = (
        _recall_with_corrections(state, key, record)
        if record is not None
        else {
            "key": key,
            "status": "missing",
            "value": None,
            "citation": None,
            "history": [],
        }
    )
    if provisional_outbox is None:
        return home_result

    provisional_by_kind = _provisional_versions(provisional_outbox, key)
    if record is not None:
        durable_ids = {version["item_id"] for version in record["versions"]}
        provisional_by_kind = {
            kind: [
                version
                for version in versions
                if version["item_id"] not in durable_ids
            ]
            for kind, versions in provisional_by_kind.items()
        }
        provisional_by_kind = {
            kind: versions
            for kind, versions in provisional_by_kind.items()
            if versions
        }
    if not provisional_by_kind:
        return home_result
    if len(provisional_by_kind) > 1:
        return _unresolved_kind_conflict(
            key,
            provisional_by_kind,
            home_result=home_result if record is not None else None,
        )
    provisional_kind, provisional = next(iter(provisional_by_kind.items()))
    provisional_result = _recall_record(key, provisional_kind, provisional)
    if record is None:
        if provisional_result["status"] == "unresolved":
            provisional_result["pending_home"] = True
        else:
            provisional_result["status"] = "pending_home"
        provisional_result["home_current"] = None
        return provisional_result

    if provisional_kind != record["kind"]:
        return {
            "key": key,
            "status": "unresolved",
            "value": None,
            "citation": None,
            "alternatives": [home_result, provisional_result],
            "history": [],
            "pending_home": True,
        }

    home_latest = max(version["source_time"] for version in record["versions"])
    provisional_latest = max(version["source_time"] for version in provisional)
    if provisional_latest < home_latest:
        return home_result
    if provisional_latest == home_latest:
        combined = _recall_record(
            key, record["kind"], [*record["versions"], *provisional]
        )
        if combined["status"] == "current":
            return home_result
        combined["pending_home"] = True
        combined["home_current"] = home_result
        return combined

    if provisional_result["status"] == "unresolved":
        provisional_result["pending_home"] = True
    else:
        provisional_result["status"] = "pending_home"
    provisional_result["home_current"] = home_result
    return provisional_result


def outbox_status(state_root: Path, *, limit: int = 10_000) -> dict:
    """Return validated durable delivery counts without implying transport health."""

    if type(limit) is not int or not 1 <= limit <= 10_000:
        raise ValidationError("outbox status limit must be an integer from 1 through 10000")
    counts = {status: 0 for status in sorted(ALLOWED_DELIVERY_STATES)}
    with _lock(state_root / ".outbox.lock"):
        paths = sorted((state_root / "outbox").glob("*.json"))
        inspected = paths[:limit]
        for path in inspected:
            envelope = _validate_envelope(
                _read_json(path, "outbox state"), durable=True
            )
            counts[envelope["status"]] += 1
    return {
        "counts": counts,
        "inspected": len(inspected),
        "total_files": len(paths),
        "truncated": len(paths) > limit,
    }


def memory_snapshot(memory_path: Path) -> dict:
    """Return a detached, fully validated snapshot for recall publication."""

    with _lock(memory_path.with_name(f".{memory_path.name}.lock")):
        return _load_memory(memory_path)


def recall_snapshot(snapshot: object, key: str) -> dict:
    """Recall from one detached validated snapshot without re-reading disk."""

    _required_string(key, "recall key", pattern=SAFE_KEY_RE)
    state = _state_from_snapshot(snapshot)
    record = state["records"].get(key)
    if record is None:
        return {
            "key": key,
            "status": "missing",
            "value": None,
            "citation": None,
            "history": [],
        }
    return _recall_with_corrections(state, key, record)


def load_home_envelope(home_root: Path, delivery_id: str) -> dict:
    """Load one accepted Home inbox envelope through the public validator."""

    _required_string(delivery_id, "delivery_id", pattern=DELIVERY_ID_RE)
    path = home_root / "inbox" / f"{delivery_id}.json"
    if not path.exists():
        raise ValidationError(f"delivery_id is not accepted by Home: {delivery_id}")
    return _validate_envelope(_read_json(path, "Home inbox state"), durable=True)


# --- Correction proposals -----------------------------------------------------
#
# A correction is honoured only when a separate, read-only review receipt binds
# the candidate relation, the exact durable evidence hashes and the records
# snapshot it was reviewed against.  A proposal carries only that receipt id,
# so caller-supplied reviewer/model/time metadata (or a bare boolean) grants no
# authority.  The receipt records that a review happened; hashes are an
# integrity binding, not a claim of mathematical or semantic proof.

REVIEW_RECEIPT_SCHEMA_VERSION = 1
REVIEW_RECEIPT_KEYS = {
    "schema_version",
    "receipt_id",
    "relation",
    "target_item_id",
    "source_item_id",
    "topic_ref",
    "field",
    "evidence_refs",
    "candidate_sha256",
    "evidence_sha256",
    "records_snapshot_sha256",
    "reviewer",
    "model",
    "reviewed_at",
}
PROPOSAL_CORE_KEYS = {
    "relation",
    "target_item_id",
    "source_item_id",
    "topic_ref",
    "field",
    "evidence_refs",
}


def _default_receipt_store(memory_path: Path) -> Path:
    """Private, machine-local receipt store beside the memory file."""

    return Path(memory_path).parent / "review-receipts"


def _records_snapshot_sha256(records: dict) -> str:
    return _sha256_hex({"records": records})


def _proposal_core(value: object, index: dict) -> dict:
    core = _validate_relation_core(value, index)
    return core


def _receipt_content(receipt: dict) -> dict:
    return {key: receipt[key] for key in receipt if key != "receipt_id"}


def _validate_receipt_object(receipt: object) -> dict:
    value = _require_exact_keys(receipt, REVIEW_RECEIPT_KEYS, "review receipt")
    if (
        type(value["schema_version"]) is not int
        or value["schema_version"] != REVIEW_RECEIPT_SCHEMA_VERSION
    ):
        raise ValidationError("review receipt schema_version is unsupported")
    receipt_id = _required_string(
        value["receipt_id"], "review receipt id", pattern=REVIEW_RECEIPT_ID_RE
    )
    _validate_relation_core(value, _index_from_receipt(value))
    if receipt_id != _sha256_id("review", _receipt_content(value)):
        raise ValidationError("review receipt identity does not match its content")
    candidate_sha = value["candidate_sha256"]
    if not isinstance(candidate_sha, str) or HEX_64_RE.fullmatch(candidate_sha) is None:
        raise ValidationError("review receipt candidate_sha256 must be a SHA-256")
    evidence_sha = value["evidence_sha256"]
    if not isinstance(evidence_sha, list) or not evidence_sha:
        raise ValidationError("review receipt evidence_sha256 must be a non-empty array")
    for digest in evidence_sha:
        if not isinstance(digest, str) or HEX_64_RE.fullmatch(digest) is None:
            raise ValidationError("review receipt evidence_sha256 entries must be SHA-256")
    snapshot = value["records_snapshot_sha256"]
    if not isinstance(snapshot, str) or HEX_64_RE.fullmatch(snapshot) is None:
        raise ValidationError("review receipt records_snapshot_sha256 must be a SHA-256")
    _required_string(value["reviewer"], "review receipt reviewer", pattern=SAFE_ID_RE)
    _required_string(value["model"], "review receipt model", pattern=SAFE_ID_RE)
    _validated_iso(value["reviewed_at"], "review receipt reviewed_at")
    return deepcopy(value)


def _index_from_receipt(receipt: dict) -> dict:
    """Placeholder index for structural receipt checks (ids checked elsewhere)."""

    def entry() -> tuple[str, str, dict]:
        return ("", "", {})

    index: dict[str, tuple[str, str, dict]] = {}
    for item_id in [receipt.get("target_item_id"), receipt.get("source_item_id")]:
        if isinstance(item_id, str):
            index[item_id] = entry()
    for item_id in receipt.get("evidence_refs") or []:
        if isinstance(item_id, str):
            index[item_id] = entry()
    return index


def create_review_receipt(
    memory_path: Path,
    proposal: object,
    *,
    reviewer: str,
    model: str,
    reviewed_at: str | None = None,
    receipt_store: Path | None = None,
) -> dict:
    """Read-only review of one candidate correction against durable state.

    The receipt is written to the private machine-local store; the shared
    memory file is never modified here.
    """

    value = _require_exact_keys(proposal, PROPOSAL_CORE_KEYS, "correction proposal core")
    state = _load_memory(Path(memory_path))
    index = _item_index(state["records"])
    core = _proposal_core(value, index)
    reviewed = _validated_iso(reviewed_at or _now_iso(), "review reviewed_at")
    receipt = {
        "schema_version": REVIEW_RECEIPT_SCHEMA_VERSION,
        "relation": core["relation"],
        "target_item_id": core["target_item_id"],
        "source_item_id": core["source_item_id"],
        "topic_ref": core["topic_ref"],
        "field": core["field"],
        "evidence_refs": list(core["evidence_refs"]),
        "candidate_sha256": _sha256_hex(core),
        "evidence_sha256": sorted(
            _evidence_sha256(ref, index[ref][2]) for ref in core["evidence_refs"]
        ),
        "records_snapshot_sha256": _records_snapshot_sha256(state["records"]),
        "reviewer": _required_string(reviewer, "review reviewer", pattern=SAFE_ID_RE),
        "model": _required_string(model, "review model", pattern=SAFE_ID_RE),
        "reviewed_at": reviewed,
    }
    receipt["receipt_id"] = _sha256_id("review", receipt)
    receipt = _validate_receipt_object(receipt)
    store = Path(receipt_store) if receipt_store is not None else _default_receipt_store(memory_path)
    path = store / f"{receipt['receipt_id']}.json"
    if not path.exists():
        _atomic_write_json(path, receipt)
    return receipt


def _load_review_receipt(receipt_store: Path, receipt_id: str) -> dict:
    store = Path(receipt_store)
    receipt_id = _required_string(
        receipt_id, "correction review_receipt_id", pattern=REVIEW_RECEIPT_ID_RE
    )
    path = store / f"{receipt_id}.json"
    if not path.is_file():
        raise ValidationError("correction review receipt is unavailable")
    receipt = _validate_receipt_object(_read_json(path, "review receipt"))
    if receipt["receipt_id"] != receipt_id:
        raise ValidationError("correction review receipt file does not match its id")
    return receipt


def validate_correction_proposal(
    records: dict, proposal: object, *, receipt_store: Path
) -> dict:
    """Return one durable correction record or fail closed before any write.

    The proposal is insufficient on its own: a separately created receipt must
    bind the candidate relation and the exact evidence item hashes, and both
    referenced items and their verified provenance must already exist.

    The receipt also records ``records_snapshot_sha256`` for audit, but that
    snapshot equality is deliberately not required here: capture is
    append-only, so an unrelated record arriving after the review must not
    invalidate an otherwise valid receipt.  The candidate hash plus the
    per-item evidence hashes still bind the referenced assertions, and a
    referenced item that was removed or altered fails closed below.
    """

    if not isinstance(records, dict):
        raise ValidationError("correction proposal requires validated memory records")
    for record in records.values():
        if (
            not isinstance(record, dict)
            or not isinstance(record.get("kind"), str)
            or not isinstance(record.get("versions"), list)
            or not record["versions"]
        ):
            raise ValidationError("correction proposal requires validated memory records")
    value = _require_exact_keys(
        proposal, PROPOSAL_CORE_KEYS | {"review_receipt_id"}, "correction proposal"
    )
    index = _item_index(records)
    core = _proposal_core(value, index)
    receipt_id = _required_string(
        value["review_receipt_id"], "correction review_receipt_id",
        pattern=REVIEW_RECEIPT_ID_RE,
    )
    receipt = _load_review_receipt(receipt_store, receipt_id)
    for field in PROPOSAL_CORE_KEYS:
        if receipt[field] != core[field]:
            raise ValidationError("correction review receipt does not bind this proposal")
    if receipt["candidate_sha256"] != _sha256_hex(core):
        raise ValidationError("correction review candidate hash does not match the proposal")
    expected_evidence = sorted(
        _evidence_sha256(ref, index[ref][2]) for ref in core["evidence_refs"]
    )
    if sorted(receipt["evidence_sha256"]) != expected_evidence:
        raise ValidationError("correction review evidence hashes do not bind the referenced items")
    correction = {
        "correction_id": _sha256_id(
            "correction", {**core, "review_receipt_id": receipt_id}
        ),
        **core,
        "review_receipt_id": receipt_id,
    }
    return _validate_correction(correction, index)


def propose_corrections(
    memory_path: Path,
    proposals: list,
    *,
    read_only: bool = False,
    receipt_store: Path | None = None,
) -> dict:
    """Read-only: validate proposals against current durable state.

    ``read_only`` avoids even creating a lock file so SJM can validate a
    private proposal against the Syncthing-delivered snapshot without writing
    into the shared vault.
    """

    if not isinstance(proposals, list):
        raise ValidationError("correction proposals must be an array")
    store = Path(receipt_store) if receipt_store is not None else _default_receipt_store(memory_path)
    if read_only:
        state = _load_memory(memory_path)
        return _proposal_results(state, proposals, store)
    with _lock(memory_path.with_name(f".{memory_path.name}.lock")):
        state = _load_memory(memory_path)
        return _proposal_results(state, proposals, store)


def _proposal_results(state: dict, proposals: list, receipt_store: Path) -> dict:
    existing = {correction["correction_id"] for correction in state["corrections"]}
    results = []
    combined = list(state["corrections"])
    for proposal in proposals:
        correction = validate_correction_proposal(
            state["records"], proposal, receipt_store=receipt_store
        )
        combined.append(correction)
        _reject_correction_cycles(combined)
        results.append(
            {
                "correction_id": correction["correction_id"],
                "relation": correction["relation"],
                "target_item_id": correction["target_item_id"],
                "source_item_id": correction["source_item_id"],
                "field": correction["field"],
                "status": "already_applied"
                if correction["correction_id"] in existing
                else "eligible",
                "review_receipt_id": correction["review_receipt_id"],
            }
        )
    return {
        "status": "ok",
        "schema_version": state["schema_version"],
        "proposals": results,
    }


def apply_corrections(
    memory_path: Path,
    proposals: list,
    *,
    receipt_store: Path | None = None,
    recovery_dir: Path | None = None,
) -> dict:
    """Home-only durable append of reviewed corrections through atomic writes.

    Applying a correction is the first v2 write for a schema-v1 state, so the
    caller must explicitly authorize a recoverable migration by supplying a
    private ``recovery_dir``.  The exact pre-migration bytes are preserved
    there and before/after hashes are recorded before the first correction can
    land; without it a v1 (or missing) state fails closed rather than silently
    upgrading to v2 with no recovery copy.  A v2 state applies in place as
    before.  Read-only review/proposal paths remain v1-compatible.
    """

    if not isinstance(proposals, list) or not proposals:
        raise ValidationError("correction apply requires a non-empty proposals array")
    store = Path(receipt_store) if receipt_store is not None else _default_receipt_store(memory_path)
    # Validate every proposal against the current state before acquiring the
    # writer lock so a later bad proposal cannot partially mutate memory.
    with _lock(memory_path.with_name(f".{memory_path.name}.lock")):
        before_bytes = memory_path.read_bytes() if memory_path.is_file() else None
        state = _load_memory(memory_path)
        planned = [
            validate_correction_proposal(
                state["records"], proposal, receipt_store=store
            )
            for proposal in proposals
        ]
        migration: dict | None = None
        if state["schema_version"] == MEMORY_SCHEMA_VERSION_V2:
            base = {
                "schema_version": MEMORY_SCHEMA_VERSION_V2,
                "records": deepcopy(state["records"]),
                "corrections": deepcopy(state["corrections"]),
            }
        else:
            # A v1 file must be migrated recoverably before it can hold a
            # correction.  Demand an explicit private recovery directory so the
            # first v2 write always has a recovery copy and before/after hashes.
            if recovery_dir is None:
                raise ValidationError(
                    "memory state is schema v1; supply a private recovery_dir (or run "
                    "migrate_memory_state_v2) before applying corrections so the first "
                    "v2 write has a recovery copy and before/after hashes"
                )
            if before_bytes is None:
                raise ValidationError(f"memory state does not exist: {memory_path}")
            base, migration = _recoverable_v1_base(
                memory_path, state, before_bytes, Path(recovery_dir)
            )
            # Persist the recoverable migration before appending any correction
            # so the recorded before/after hashes describe exactly the bytes the
            # recovery copy can restore, even if the process dies mid-apply.
            _atomic_write_json(memory_path, base)
            migration["after_sha256"] = hashlib.sha256(
                memory_path.read_bytes()
            ).hexdigest()
            migration["status"] = "migrated"
        candidate = {
            "schema_version": MEMORY_SCHEMA_VERSION_V2,
            "records": deepcopy(base["records"]),
            "corrections": deepcopy(base["corrections"]),
        }
        existing = {correction["correction_id"] for correction in candidate["corrections"]}
        applied = 0
        duplicates = 0
        for correction in planned:
            if correction["correction_id"] in existing:
                duplicates += 1
                continue
            existing.add(correction["correction_id"])
            candidate["corrections"].append(correction)
            applied += 1
        candidate["corrections"].sort(key=lambda row: row["correction_id"])
        # Detect a cycle as a proposal error before the durable validator runs,
        # so the caller sees a precise validation failure instead of corruption.
        _reject_correction_cycles(candidate["corrections"])
        _validate_memory_state(candidate)
        if applied:
            _atomic_write_json(memory_path, candidate)
        result = {
            "applied": applied,
            "duplicates": duplicates,
            "corrections": len(candidate["corrections"]),
            "schema_version": candidate["schema_version"],
        }
        if migration is not None:
            result["migration"] = migration
        return result


def _recoverable_v1_base(
    memory_path: Path,
    state: dict,
    before_bytes: bytes,
    recovery_dir: Path,
) -> tuple[dict, dict]:
    """Derive the v2 base for a v1 state and write its private recovery copy.

    The recovery copy holds the exact pre-migration bytes so the recorded
    ``before_sha256`` matches it byte-for-byte and a restore is lossless.  The
    live target is not touched here: it is written afterwards by the caller,
    so the recovery copy always exists before the first v2 bytes land.

    The copy path is derived from ``before_sha``.  When it already exists it is
    treated as durable evidence, not a scratch slot: its bytes must reproduce
    ``before_sha`` exactly or the migration fails closed.  A stale, corrupt or
    replaced copy is never overwritten, because doing so would let v2 land
    without a recovery copy that can actually restore the pre-migration state.
    """

    before_sha = hashlib.sha256(before_bytes).hexdigest()
    candidate = {
        "schema_version": MEMORY_SCHEMA_VERSION_V2,
        "records": deepcopy(state["records"]),
        "corrections": [],
    }
    _validate_memory_state(candidate)
    recovery_dir = Path(recovery_dir)
    recovery_dir.mkdir(parents=True, exist_ok=True)
    recovery_path = recovery_dir / f"{memory_path.name}.{before_sha}.v1.json"
    if recovery_path.exists():
        existing = recovery_path.read_bytes()
        if existing != before_bytes or hashlib.sha256(existing).hexdigest() != before_sha:
            raise StateCorruptionError(
                "refusing to overwrite an existing recovery copy whose bytes do "
                f"not match the expected pre-migration hash: {recovery_path}"
            )
    else:
        _atomic_write_bytes(recovery_path, before_bytes)
    return candidate, {"before_sha256": before_sha, "recovery_path": str(recovery_path)}


def migrate_memory_state_v2(memory_path: Path, recovery_dir: Path) -> dict:
    """One-way, lossless v1→v2 migration with a private recovery copy."""

    lock = memory_path.with_name(f".{memory_path.name}.lock")
    with _lock(lock):
        if not memory_path.exists():
            raise ValidationError(f"memory state does not exist: {memory_path}")
        before_bytes = memory_path.read_bytes()
        before_sha = hashlib.sha256(before_bytes).hexdigest()
        state = _load_memory(memory_path)
        if state["schema_version"] == MEMORY_SCHEMA_VERSION_V2:
            return {
                "status": "already_v2",
                "before_sha256": before_sha,
                "after_sha256": before_sha,
                "records": len(state["records"]),
                "corrections": len(state["corrections"]),
                "recovery_path": None,
            }
        candidate, recovery = _recoverable_v1_base(
            memory_path, state, before_bytes, Path(recovery_dir)
        )
        _atomic_write_json(memory_path, candidate)
        after_bytes = memory_path.read_bytes()
        return {
            "status": "migrated",
            "before_sha256": recovery["before_sha256"],
            "after_sha256": hashlib.sha256(after_bytes).hexdigest(),
            "records": len(candidate["records"]),
            "corrections": 0,
            "recovery_path": recovery["recovery_path"],
        }


def verify_recovery_copy(recovery_path: Path, expected_sha256: str) -> dict:
    """Read-only check that a recovery copy is byte-identical to its hash."""

    if not isinstance(expected_sha256, str) or HEX_64_RE.fullmatch(expected_sha256) is None:
        raise ValidationError("expected recovery hash must be a lowercase SHA-256")
    path = Path(recovery_path)
    if not path.is_file():
        raise ValidationError(f"recovery copy does not exist: {path}")
    payload = path.read_bytes()
    actual = hashlib.sha256(payload).hexdigest()
    return {
        "recovery_path": str(path),
        "expected_sha256": expected_sha256,
        "actual_sha256": actual,
        "matches": actual == expected_sha256,
        "bytes": len(payload),
    }


def _expected_post_migration_sha256(recovery_payload: bytes) -> str:
    """Derive the migration's post-state hash from the verified recovery copy.

    :func:`migrate_memory_state_v2` writes a deterministic v2 document derived
    only from the pre-migration v1 state, so the recovery copy itself fixes the
    exact bytes the live target held immediately after the migration.  Binding
    a restore to that derivation means a caller cannot nominate an arbitrary
    "post-migration" hash that merely matches whatever the target currently
    holds, which would let newer records or corrections be discarded.  A
    recovery copy that is not a valid pre-migration v1 state fails closed.
    """

    try:
        raw = json.loads(recovery_payload.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValidationError("recovery copy is not a readable memory state") from exc
    try:
        state = _validate_memory_state(raw)
    except (ValidationError, StateCorruptionError) as exc:
        raise ValidationError("recovery copy is not a valid memory state") from exc
    if state["schema_version"] != MEMORY_SCHEMA_VERSION:
        raise ValidationError("recovery copy is not a pre-migration v1 memory state")
    candidate = {
        "schema_version": MEMORY_SCHEMA_VERSION_V2,
        "records": deepcopy(state["records"]),
        "corrections": [],
    }
    return hashlib.sha256(_canonical_json(candidate) + b"\n").hexdigest()


def restore_memory_from_recovery(
    recovery_path: Path,
    target_path: Path,
    *,
    expected_sha256: str,
    expected_target_sha256: str | None = None,
) -> dict:
    """Explicit, manual restore of a v1 recovery copy. Never runs automatically.

    ``expected_sha256`` is the recorded pre-migration hash and re-verifies the
    recovery copy itself, so a corrupt or substituted copy fails closed before
    anything is written.  ``expected_target_sha256`` is the post-migration hash
    of the live target that the same migration reported.  It is required as
    soon as the target already exists with different content, and it must equal
    the post-migration hash derived from the recovery copy itself, so a caller
    cannot nominate the target's current (newer) hash to justify discarding
    records or corrections that arrived after the migration.  A nonexistent
    target is a legitimate explicit scratch restore and needs no comparison
    hash.
    """

    verification = verify_recovery_copy(recovery_path, expected_sha256)
    if not verification["matches"]:
        raise ValidationError("recovery copy hash does not match the recorded before-hash")
    if expected_target_sha256 is not None and (
        not isinstance(expected_target_sha256, str)
        or HEX_64_RE.fullmatch(expected_target_sha256) is None
    ):
        raise ValidationError("expected target hash must be a lowercase SHA-256")
    payload = Path(recovery_path).read_bytes()
    target = Path(target_path)
    result = {**verification, "target_path": str(target)}
    # Serialize the existence/hash check with every other writer of this state
    # file, so a concurrent append cannot land between the comparison and the
    # atomic replace and then be silently discarded.
    with _lock(target.with_name(f".{target.name}.lock")):
        if target.exists() and not target.is_file():
            raise ValidationError(f"restore target is not a regular file: {target}")
        if target.is_file():
            existing = target.read_bytes()
            # Restoring the exact bytes already present is a no-op, so an
            # idempotent retry never needs the post-migration hash.
            if existing == payload:
                return {"status": "already_restored", **result}
            if expected_target_sha256 is None:
                raise ValidationError(
                    "restoring over an existing memory file requires the "
                    "post-migration target hash; refusing to overwrite newer "
                    "live state"
                )
            # Bind the caller's after-hash to the recovery copy before trusting
            # it.  An after-hash that merely equals the current target content
            # is not evidence that the content is the unmodified post-migration
            # state, so the derivation must agree.
            derived_target_sha256 = _expected_post_migration_sha256(payload)
            if expected_target_sha256 != derived_target_sha256:
                raise ValidationError(
                    "expected target hash is not the post-migration state "
                    "derived from the recovery copy; refusing to overwrite "
                    "newer live state"
                )
            if hashlib.sha256(existing).hexdigest() != expected_target_sha256:
                raise ValidationError(
                    "restore target changed after the migration; refusing to "
                    "overwrite newer live state"
                )
        _atomic_write_bytes(target, payload)
    return {"status": "restored", **result}
