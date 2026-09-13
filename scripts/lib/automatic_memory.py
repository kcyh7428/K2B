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
ALLOWED_SCOPES = {"K2B", "K2Bi"}
ALLOWED_KINDS = {"fact", "decision", "preference", "commitment"}
ALLOWED_SPEAKERS = {"keith", "assistant_confirmed"}
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
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, raw_tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    tmp_path = Path(raw_tmp)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(_canonical_json(value) + b"\n")
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
    if not isinstance(value, dict) or set(value) != {"schema_version", "records"}:
        raise StateCorruptionError("memory state must contain schema_version and records")
    if (
        type(value["schema_version"]) is not int
        or value["schema_version"] != MEMORY_SCHEMA_VERSION
    ):
        raise StateCorruptionError("memory state schema_version is unsupported")
    if not isinstance(value["records"], dict):
        raise StateCorruptionError("memory state records must be an object")
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
    return deepcopy(value)


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
        _validate_memory_state(candidate)
        if applied:
            _atomic_write_json(memory_path, candidate)
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
        _recall_record(key, record["kind"], record["versions"])
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
    state = _validate_memory_state(snapshot)
    record = state["records"].get(key)
    if record is None:
        return {
            "key": key,
            "status": "missing",
            "value": None,
            "citation": None,
            "history": [],
        }
    return _recall_record(key, record["kind"], record["versions"])


def load_home_envelope(home_root: Path, delivery_id: str) -> dict:
    """Load one accepted Home inbox envelope through the public validator."""

    _required_string(delivery_id, "delivery_id", pattern=DELIVERY_ID_RE)
    path = home_root / "inbox" / f"{delivery_id}.json"
    if not path.exists():
        raise ValidationError(f"delivery_id is not accepted by Home: {delivery_id}")
    return _validate_envelope(_read_json(path, "Home inbox state"), durable=True)
