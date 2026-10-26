#!/usr/bin/env python3
# scripts/lib/knowledge_integration.py
"""Supervised K2B knowledge integration accounting.

This module owns three read-mostly operations over an already published
automatic-memory snapshot plus local raw capture descriptors:

* ``build_queue`` derives outstanding integration work without copying facts.
* ``validate_proposal`` checks a strict version 1 proposal against the current
  snapshot and vault without writing anything.
* ``record_integration`` records a Home-only, idempotent integration receipt
  after the existing ordinary note writer has saved every target.

It never calls a provider, schedules work, writes a note, mutates automatic
memory, or transports a raw source.  Note saving stays in the existing
``vault-note-write.py`` writer; this module only validates and records.
"""

from __future__ import annotations

import fcntl
import hashlib
import importlib.util
import json
import os
import pwd
import re
import stat
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Iterator
from urllib.parse import unquote

import automatic_memory
from raw_source_status import classify


PROPOSAL_SCHEMA_VERSION = 1
RECEIPT_SCHEMA_VERSION = 1
QUEUE_SCHEMA_VERSION = 1

SOURCE_KIND_MEMORY = "memory_item"
SOURCE_KIND_RAW = "raw_capture"
ALLOWED_SOURCE_KINDS = {SOURCE_KIND_MEMORY, SOURCE_KIND_RAW}
ALLOWED_DISPOSITIONS = {"integrated", "retained_leaf"}
ALLOWED_RAW_CLASSIFICATIONS = {"human_capture", "technical_record"}

QUEUE_NEEDS_REVIEW = "needs_review"
QUEUE_INTEGRATED = "integrated"
QUEUE_RETAINED_LEAF = "retained_leaf"
QUEUE_BLOCKED = "blocked"

MAX_TARGETS = 3
MAX_SOURCES = 16
MAX_RELATIONSHIPS = 32
MAX_NOTE_BYTES = 2 * 1024 * 1024
MAX_RAW_BYTES = 2 * 1024 * 1024
MAX_JSON_BYTES = 8 * 1024 * 1024
MAX_TEXT_CHARS = 65_536
MAX_REASON_CHARS = 4_000
MAX_RECEIPT_BYTES = 4 * 1024 * 1024

HEX_64_RE = re.compile(r"^[0-9a-f]{64}$")
OPERATION_ID_RE = re.compile(r"^operation:[0-9a-f]{64}$")
RAW_ID_RE = re.compile(r"^raw:[0-9a-f]{64}$")
RECEIPT_REF_RE = re.compile(r"^note-write-receipts/([0-9a-f]{16})\.json$")
SAFE_REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/+-]{0,299}$")
NOTE_REL_RE = re.compile(r"^[^/\\]+(?:/[^/\\]+)*\.md$")
WIKILINK_RE = re.compile(r"\[\[([^\]\n]{1,512})\]\]")
MDLINK_RE = re.compile(r"\[[^\]\n]{0,512}\]\(([^)\s\n]{1,512})\)")

PROPOSAL_KEYS = {
    "assertion_status",
    "schema_version",
    "sources",
    "disposition",
    "reason",
    "targets",
    "semantic_review",
}
SOURCE_MEMORY_KEYS = {
    "kind",
    "item_id",
    "source_identity",
    "evidence_sha256",
    "speaker",
    "evidence_quote",
    "value",
}
SOURCE_RAW_KEYS = {
    "kind",
    "raw_path",
    "raw_sha256",
    "classification",
    "speaker",
    "evidence_quote",
}
TARGET_KEYS = {
    "path",
    "expected_sha256",
    "replacement_content",
    "replacement_sha256",
    "current_summary_change",
    "dated_history",
    "relationships",
}
RELATIONSHIP_KEYS = {"path", "reason"}
SEMANTIC_REVIEW_KEYS = {"supervisor", "source_reference", "statement"}


class IntegrationError(ValueError):
    """A proposal, source binding, target or receipt fails closed."""


class IntegrationConflict(IntegrationError):
    """A current hash disagrees with the recorded before/after hash."""


# --- small bounded helpers ---------------------------------------------------


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _sha256_hex(value: object) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _require_object(value: object, label: str) -> dict:
    if not isinstance(value, dict):
        raise IntegrationError(f"{label} must be an object")
    return value


def _exact_keys(value: object, expected: set[str], label: str) -> dict:
    obj = _require_object(value, label)
    keys = set(obj)
    if keys != expected:
        missing = sorted(expected - keys)
        extra = sorted(keys - expected)
        raise IntegrationError(
            f"{label} fields invalid; missing={missing}, extra={extra}"
        )
    return obj


def _required_text(
    value: object, label: str, *, max_chars: int = MAX_TEXT_CHARS
) -> str:
    if not isinstance(value, str):
        raise IntegrationError(f"{label} must be a string")
    text = value.strip()
    if not text:
        raise IntegrationError(f"{label} must be non-empty")
    if len(text) > max_chars:
        raise IntegrationError(f"{label} exceeds {max_chars} characters")
    if "\x00" in text:
        raise IntegrationError(f"{label} contains NUL bytes")
    return text


def _required_hash(value: object, label: str) -> str:
    if not isinstance(value, str) or HEX_64_RE.fullmatch(value) is None:
        raise IntegrationError(f"{label} must be a lowercase SHA-256")
    return value


def _is_within(base: Path, candidate: Path) -> bool:
    try:
        candidate.resolve().relative_to(base.resolve())
        return True
    except (OSError, ValueError):
        return False


def _has_symlink_component(base: Path, candidate: Path) -> bool:
    try:
        rel = candidate.relative_to(base)
    except ValueError:
        return True
    current = base
    for part in rel.parts:
        current = current / part
        if current.is_symlink():
            return True
    return False


def _safe_note_rel(rel: object, *, allow_missing_tail: bool = False) -> str:
    """Vault-relative ordinary note path, refusing traversal and hidden parts.

    Reuses the existing writer's ``validate_note_rel`` when that module loads
    safely so note-root and Shipped protections never drift.
    """

    text = _required_text(rel, "note path", max_chars=1024)
    if text.startswith("/") or "\\" in text:
        raise IntegrationError("note path must be a vault-relative POSIX path")
    parts = text.split("/")
    for part in parts:
        if part in ("", ".", ".."):
            raise IntegrationError("note path must not contain empty, dot or dotdot parts")
        if part.startswith("."):
            raise IntegrationError("note path must not contain hidden parts")
    if "shipped" in [p.casefold() for p in parts]:
        raise IntegrationError("Shipped is not an ordinary integration target")
    if parts[-1].casefold() == "index.md":
        raise IntegrationError("index.md is not an ordinary integration target")
    if not parts[-1].endswith(".md"):
        raise IntegrationError("ordinary notes must be Markdown")
    if ".sync-conflict-" in parts[-1].casefold():
        raise IntegrationError("a Syncthing conflict copy is not a note target")
    if NOTE_REL_RE.fullmatch(text) is None:
        raise IntegrationError("note path has unsupported characters")
    writer = _load_writer_module()
    if writer is not None:
        try:
            writer.validate_note_rel(text)
        except Exception as exc:  # noqa: BLE001 - writer contract, fail closed
            raise IntegrationError(f"note path rejected by writer: {exc}") from exc
    elif not allow_missing_tail:
        raise IntegrationError("writer validation helper unavailable")
    return text


def _load_writer_module():
    """Load the existing writer module for its read-only validators only."""

    root = Path(__file__).resolve().parents[2]
    path = root / "scripts" / "vault-note-write.py"
    if not path.is_file():
        return None
    try:
        spec = importlib.util.spec_from_file_location("k2b_vault_note_write_ro", path)
        if spec is None or spec.loader is None:
            return None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    except Exception:  # noqa: BLE001 - optional writer helper
        return None


def _default_vault_root() -> Path:
    root = os.environ.get("K2B_VAULT_PATH") or os.environ.get("K2B_VAULT_ROOT")
    if not root:
        root = os.path.join(os.path.expanduser("~"), "Projects", "K2B-Vault")
    return Path(os.path.realpath(os.path.abspath(root)))


def _writer_state_root() -> Path:
    override = os.environ.get("K2B_LOCAL_STATE")
    if override:
        return Path(os.path.abspath(override))
    return Path(os.path.expanduser("~")) / ".local" / "state" / "k2b"


def _default_state_root() -> Path:
    return Path(os.path.expanduser("~")) / ".local" / "state" / "k2b" / "digestion"


def _native_memory_root() -> Path:
    return (
        Path(os.path.expanduser("~"))
        / ".local"
        / "state"
        / "k2b"
        / "automatic-memory"
    )


# --- physical operator identity ----------------------------------------------


def _physical_operator() -> str:
    """Resolve the effective Unix account name from the OS, never the env."""

    try:
        return pwd.getpwuid(os.geteuid()).pw_name
    except KeyError as exc:  # pragma: no cover - unusual host
        raise IntegrationError("cannot resolve effective uid to a user") from exc


def _require_home_operator() -> str:
    user = _physical_operator()
    if user != "keithmbpm2":
        raise IntegrationError(
            f"record_integration is Home-only; current user {user!r} is not permitted"
        )
    return user


# --- snapshot and raw descriptor binding -------------------------------------


def _normalize_snapshot(snapshot: object) -> dict:
    try:
        return automatic_memory._state_from_snapshot(snapshot)
    except automatic_memory.StateCorruptionError as exc:
        raise IntegrationError(f"snapshot is invalid: {exc}") from exc


def _raw_identity(raw_path: str, raw_sha256: str) -> str:
    return "raw:" + hashlib.sha256(
        _canonical_json({"path": raw_path, "sha256": raw_sha256})
    ).hexdigest()


def _validate_raw_descriptor(raw: object, index: int) -> dict:
    label = f"raw descriptor {index}"
    obj = _exact_keys(
        raw,
        {"path", "sha256", "classification", "legacy_disposition"},
        label,
    )
    path = _required_text(obj["path"], f"{label} path", max_chars=1024)
    _require_safe_rel_components(path, f"{label} path")
    if not path.startswith("raw/"):
        raise IntegrationError(f"{label} path must live under raw/")
    if not path.endswith(".md"):
        raise IntegrationError(f"{label} must be Markdown under raw/")
    sha = _required_hash(obj["sha256"], f"{label} sha256")
    classification = _required_text(
        obj["classification"], f"{label} classification", max_chars=64
    )
    if classification not in ALLOWED_RAW_CLASSIFICATIONS:
        raise IntegrationError(f"{label} classification is unsupported")
    legacy = obj["legacy_disposition"]
    if legacy is not None and (
        not isinstance(legacy, str) or legacy not in {"skipped", "deferred"}
    ):
        raise IntegrationError(f"{label} legacy disposition is unsupported")
    return {
        "path": path,
        "sha256": sha,
        "classification": classification,
        "legacy_disposition": legacy,
        "raw_id": _raw_identity(path, sha),
    }


def _raw_index(raw_sources: list[dict] | None) -> dict[str, dict]:
    if raw_sources is None:
        return {}
    if not isinstance(raw_sources, list):
        raise IntegrationError("raw_sources must be an array")
    if len(raw_sources) > 10_000:
        raise IntegrationError("raw_sources exceeds the descriptor limit")
    index: dict[str, dict] = {}
    seen_paths: set[str] = set()
    for i, raw in enumerate(raw_sources):
        descriptor = _validate_raw_descriptor(raw, i)
        if descriptor["path"] in seen_paths:
            raise IntegrationError("raw_sources contains duplicate paths")
        seen_paths.add(descriptor["path"])
        if descriptor["raw_id"] in index:
            raise IntegrationError("raw_sources contains a duplicate identity")
        index[descriptor["raw_id"]] = descriptor
    return index


def _memory_source_index(state: dict) -> dict[str, dict]:
    index: dict[str, dict] = {}
    for item_id, (key, kind, version) in automatic_memory._item_index(
        state["records"]
    ).items():
        index[item_id] = {
            "key": key,
            "kind": kind,
            "version": version,
            "evidence_sha256": automatic_memory._evidence_sha256(item_id, version),
        }
    return index


def _blocking_raw_reason(descriptor: dict) -> str | None:
    if descriptor["classification"] == "technical_record":
        return "raw source is a technical record"
    if descriptor["legacy_disposition"] is not None:
        return f"raw source has legacy disposition {descriptor['legacy_disposition']}"
    return None


# --- queue --------------------------------------------------------------------


def _safe_read(path: Path, limit: int) -> bytes:
    if not isinstance(limit, int) or limit <= 0:
        raise IntegrationError("read limit must be a positive integer")
    if path.is_symlink():
        raise IntegrationError(f"refusing symlink: {path}")
    try:
        st = path.stat()
    except OSError as exc:
        raise IntegrationError(f"cannot stat file: {path}: {exc}") from exc
    if not stat.S_ISREG(st.st_mode):
        raise IntegrationError(f"not a regular file: {path}")
    if st.st_size > limit:
        raise IntegrationError(f"file exceeds {limit} bytes: {path}")
    try:
        with path.open("rb") as handle:
            payload = handle.read(limit + 1)
    except OSError as exc:
        raise IntegrationError(f"cannot read file: {path}: {exc}") from exc
    if len(payload) > limit:
        raise IntegrationError(f"file exceeds {limit} bytes: {path}")
    return payload


def _receipt_state_of(receipts_by_op: dict[str, dict], op_id: str) -> dict | None:
    return receipts_by_op.get(op_id)


def _validate_known_receipt(receipt: object) -> dict | None:
    """Validate the content binding of an owner-produced terminal receipt."""
    try:
        row = _exact_keys(receipt, set(_RECEIPT_IMMUTABLE_KEYS) | {"recorded_at"}, "receipt")
        if type(row["schema_version"]) is not int or row["schema_version"] != 1:
            return None
        if row["assertion_status"] != "reported_assertion":
            return None
        if row["disposition"] not in ALLOWED_DISPOSITIONS:
            return None
        reason = _required_text(row["reason"], "receipt reason", max_chars=MAX_REASON_CHARS)
        timestamp = datetime.fromisoformat(row["recorded_at"])
        if timestamp.utcoffset() is None:
            return None
        sources, targets = row["sources"], row["targets"]
        if not isinstance(sources, list) or not 1 <= len(sources) <= MAX_SOURCES:
            return None
        if not isinstance(targets, list) or len(targets) > MAX_TARGETS:
            return None
        identities = set()
        for source in sources:
            if source.get("kind") == SOURCE_KIND_MEMORY:
                _exact_keys(source, {"kind", "item_id", "source_identity", "evidence_sha256"}, "receipt source")
                for key in ("item_id", "source_identity"):
                    _required_text(source[key], key, max_chars=1024)
                _required_hash(source["evidence_sha256"], "evidence_sha256")
                identity = (source["kind"], source["item_id"], source["evidence_sha256"])
            elif source.get("kind") == SOURCE_KIND_RAW:
                _exact_keys(source, {"kind", "raw_id", "raw_path", "raw_sha256"}, "receipt source")
                _require_safe_rel_components(source["raw_path"], "receipt raw path")
                if not source["raw_path"].startswith("raw/") or not source["raw_path"].endswith(".md"):
                    return None
                _required_hash(source["raw_sha256"], "raw_sha256")
                if source["raw_id"] != _raw_identity(source["raw_path"], source["raw_sha256"]):
                    return None
                identity = (source["kind"], source["raw_id"], source["raw_sha256"])
            else:
                return None
            if identity in identities:
                return None
            identities.add(identity)
        target_bindings, paths = [], set()
        for target in targets:
            _exact_keys(target, {"path", "before_sha256", "after_sha256", "writer_receipt_id"}, "receipt target")
            path = _safe_note_rel(target["path"])
            if path in paths or not isinstance(target["writer_receipt_id"], str):
                return None
            if RECEIPT_REF_RE.fullmatch(target["writer_receipt_id"]) is None:
                return None
            paths.add(path)
            target_bindings.append({"path": path,
                "expected_sha256": _required_hash(target["before_sha256"], "before_sha256"),
                "replacement_sha256": _required_hash(target["after_sha256"], "after_sha256")})
        if bool(targets) != (row["disposition"] == "integrated"):
            return None
        expected = _operation_id(sources=sources, disposition=row["disposition"], reason=reason, targets=target_bindings)
        if row["operation_id"] != expected or row["source_ref"] != "k2b-digest:" + expected:
            return None
        return row
    except (IntegrationError, TypeError, ValueError, AttributeError, RecursionError):
        return None


def build_queue(
    snapshot: dict,
    receipts: list[dict],
    *,
    raw_sources: list[dict] | None = None,
) -> list[dict]:
    """Derive outstanding and completed work from identities and hashes only."""

    state = _normalize_snapshot(snapshot)
    item_index = _memory_source_index(state)
    raw_index = _raw_index(raw_sources)

    if not isinstance(receipts, list):
        raise IntegrationError("receipts must be an array")
    receipts_by_op: dict[str, dict] = {}
    for raw_receipt in receipts:
        receipt = _validate_known_receipt(raw_receipt)
        if receipt is None:
            continue
        op_id = receipt["operation_id"]
        if op_id in receipts_by_op:
            raise IntegrationError("receipts contain a duplicate operation id")
        receipts_by_op[op_id] = receipt

    coverage: dict[tuple[str, str, str], dict] = {}
    for receipt in receipts_by_op.values():
        disposition = receipt.get("disposition")
        queue_state = (
            QUEUE_INTEGRATED if disposition == "integrated" else QUEUE_RETAINED_LEAF
        )
        for source in receipt.get("sources") or []:
            if not isinstance(source, dict):
                continue
            if source.get("kind") == SOURCE_KIND_MEMORY:
                ident = (SOURCE_KIND_MEMORY, source["item_id"], source["evidence_sha256"])
            else:
                ident = (SOURCE_KIND_RAW, source["raw_id"], source["raw_sha256"])
            if ident:
                row = {
                    "state": queue_state,
                    "operation_id": receipt["operation_id"],
                    "reason": receipt["reason"],
                }
                if ident in coverage:
                    row = {"state": QUEUE_BLOCKED, "operation_id": None,
                           "reason": "overlapping integration receipts require operator review"}
                coverage[ident] = row

    rows: list[dict] = []
    for item_id in sorted(item_index):
        source = item_index[item_id]
        ident = source["version"]["source_identity"]
        row = {
            "kind": SOURCE_KIND_MEMORY,
            "source_identity": ident,
            "item_id": item_id,
            "evidence_sha256": source["evidence_sha256"],
            "record_key": source["key"],
            "record_kind": source["kind"],
            "speaker": source["version"]["speaker_source"],
        }
        covered = coverage.get((SOURCE_KIND_MEMORY, item_id, source["evidence_sha256"]))
        if covered is not None:
            row.update(
                {
                    "state": covered["state"],
                    "operation_id": covered["operation_id"],
                    "reason": covered["reason"],
                }
            )
        else:
            row.update(
                {"state": QUEUE_NEEDS_REVIEW, "operation_id": None, "reason": None}
            )
        rows.append(row)

    for raw_id in sorted(raw_index):
        descriptor = raw_index[raw_id]
        blocked = _blocking_raw_reason(descriptor)
        row = {
            "kind": SOURCE_KIND_RAW,
            "source_identity": raw_id,
            "raw_path": descriptor["path"],
            "raw_sha256": descriptor["sha256"],
            "classification": descriptor["classification"],
            "legacy_disposition": descriptor["legacy_disposition"],
        }
        covered = coverage.get((SOURCE_KIND_RAW, raw_id, descriptor["sha256"]))
        if blocked is not None:
            row.update(
                {"state": QUEUE_BLOCKED, "operation_id": None, "reason": blocked}
            )
        elif covered is not None:
            row.update(
                {
                    "state": covered["state"],
                    "operation_id": covered["operation_id"],
                    "reason": covered["reason"],
                }
            )
        else:
            row.update(
                {"state": QUEUE_NEEDS_REVIEW, "operation_id": None, "reason": None}
            )
        rows.append(row)

    rows.sort(key=lambda row: (row["kind"], row["source_identity"]))
    return rows


# --- proposal validation ------------------------------------------------------


def _validate_source_against_snapshot(
    source: object,
    item_index: dict,
    raw_index: dict,
    position: int,
    *,
    vault_root: Path | None = None,
) -> dict:
    obj = _require_object(source, f"source {position}")
    kind = obj.get("kind")
    if kind == SOURCE_KIND_MEMORY:
        _exact_keys(obj, SOURCE_MEMORY_KEYS, f"source {position}")
        item_id = _required_text(obj["item_id"], f"source {position} item_id")
        ident = _required_text(
            obj["source_identity"], f"source {position} source_identity"
        )
        evidence = _required_hash(
            obj["evidence_sha256"], f"source {position} evidence_sha256"
        )
        speaker = _required_text(obj["speaker"], f"source {position} speaker")
        quote = _required_text(
            obj["evidence_quote"], f"source {position} evidence_quote"
        )
        value = _required_text(obj["value"], f"source {position} value")
        entry = item_index.get(item_id)
        if entry is None:
            raise IntegrationError(
                f"source {position} item_id is not in the current snapshot"
            )
        version = entry["version"]
        if ident != version["source_identity"]:
            raise IntegrationError(
                f"source {position} source_identity does not match the snapshot"
            )
        if evidence != entry["evidence_sha256"]:
            raise IntegrationError(
                f"source {position} evidence_sha256 does not match the snapshot"
            )
        record_speaker = version["speaker_source"]
        if speaker != record_speaker:
            raise IntegrationError(
                f"source {position} speaker does not match the snapshot"
            )
        if quote != version["evidence_quote"]:
            raise IntegrationError(f"source {position} evidence_quote is invented")
        if value != version["value"]:
            raise IntegrationError(f"source {position} value is invented")
        meta = _source_assertion_metadata(
            {"kind": SOURCE_KIND_MEMORY}, entry
        )
        return {
            "kind": SOURCE_KIND_MEMORY,
            "item_id": item_id,
            "source_identity": ident,
            "evidence_sha256": evidence,
            "speaker": speaker,
            "evidence_quote": quote,
            "value": value,
            "record_kind": meta["record_kind"],
            "record_speaker": meta["speaker"],
        }
    if kind == SOURCE_KIND_RAW:
        _exact_keys(obj, SOURCE_RAW_KEYS, f"source {position}")
        raw_path = _required_text(obj["raw_path"], f"source {position} raw_path")
        _require_safe_rel_components(raw_path, f"source {position} raw_path")
        if not raw_path.startswith("raw/") or not raw_path.endswith(".md"):
            raise IntegrationError(
                f"source {position} raw_path must be Markdown under raw/"
            )
        raw_sha = _required_hash(obj["raw_sha256"], f"source {position} raw_sha256")
        classification = _required_text(
            obj["classification"], f"source {position} classification"
        )
        if classification not in ALLOWED_RAW_CLASSIFICATIONS:
            raise IntegrationError(f"source {position} classification unsupported")
        speaker = _required_text(obj["speaker"], f"source {position} speaker")
        quote = _required_text(
            obj["evidence_quote"], f"source {position} evidence_quote"
        )
        raw_id = _raw_identity(raw_path, raw_sha)
        descriptor = raw_index.get(raw_id)
        if descriptor is None:
            raise IntegrationError(
                f"source {position} raw descriptor is not registered as raw_capture"
            )
        if descriptor["path"] != raw_path:
            raise IntegrationError(f"source {position} raw_path disagrees")
        if descriptor["classification"] != classification:
            raise IntegrationError(f"source {position} classification disagrees")
        blocked = _blocking_raw_reason(descriptor)
        if blocked is not None:
            raise IntegrationError(f"source {position} is blocked: {blocked}")
        if vault_root is not None:
            _validate_raw_against_source(
                {
                    "raw_path": raw_path,
                    "raw_sha256": raw_sha,
                    "classification": classification,
                    "evidence_quote": quote,
                },
                vault_root,
            )
        return {
            "kind": SOURCE_KIND_RAW,
            "raw_path": raw_path,
            "raw_sha256": raw_sha,
            "raw_id": raw_id,
            "classification": classification,
            "speaker": speaker,
            "evidence_quote": quote,
            "record_kind": "raw_capture",
            "record_speaker": speaker,
        }
    raise IntegrationError(f"source {position} kind is unsupported")


def _read_raw_capture(vault_root: Path, descriptor: dict) -> bytes:
    path = vault_root / descriptor["path"]
    if _has_symlink_component(vault_root, path):
        raise IntegrationError("raw capture path contains a symlink")
    if not path.is_file():
        raise IntegrationError("raw capture file is missing")
    payload = _safe_read(path, MAX_RAW_BYTES)
    if _sha256_bytes(payload) != descriptor["sha256"]:
        raise IntegrationError("raw capture hash does not match the descriptor")
    # Classification is checked against the immutable source, not a caller's
    # label. Missing legacy compile flags never imply integration or failure.
    text = payload.decode("utf-8")
    metadata = classify(descriptor["path"], text)
    if metadata["metadata_state"] == "unreadable":
        raise IntegrationError("raw capture metadata is unreadable")
    if metadata["legacy_disposition"] is not None:
        raise IntegrationError("raw capture has a retained legacy disposition")
    if metadata["classification"] == "technical_record":
        raise IntegrationError("raw capture is a technical record")
    return payload


def _validate_target(
    target: object,
    position: int,
    *,
    vault_root: Path,
    existing_note_rels: set[str] | None = None,
) -> dict:
    obj = _exact_keys(target, TARGET_KEYS, f"target {position}")
    rel = _safe_note_rel(obj["path"])
    expected = _required_hash(obj["expected_sha256"], f"target {position} expected_sha256")
    replacement = obj["replacement_content"]
    if not isinstance(replacement, str):
        raise IntegrationError(f"target {position} replacement_content must be text")
    replacement_bytes = replacement.encode("utf-8")
    if not replacement_bytes.strip():
        raise IntegrationError(f"target {position} replacement_content is empty")
    if len(replacement_bytes) > MAX_NOTE_BYTES:
        raise IntegrationError(f"target {position} replacement_content is oversize")
    replacement_sha = _required_hash(
        obj["replacement_sha256"], f"target {position} replacement_sha256"
    )
    if _sha256_bytes(replacement_bytes) != replacement_sha:
        raise IntegrationError(f"target {position} replacement_sha256 mismatched")
    summary_change = obj["current_summary_change"]
    if summary_change is not None and not isinstance(summary_change, str):
        raise IntegrationError(f"target {position} current_summary_change must be text or null")
    if isinstance(summary_change, str) and len(summary_change) > 500:
        raise IntegrationError(f"target {position} current_summary_change is oversize")
    if isinstance(summary_change, str) and (not summary_change.strip() or "\n" in summary_change or "\r" in summary_change):
        raise IntegrationError(f"target {position} current_summary_change must be a nonempty single line")
    dated = obj["dated_history"]
    if not isinstance(dated, str) or not dated.strip():
        raise IntegrationError(f"target {position} dated_history must be text")
    if len(dated) > 500:
        raise IntegrationError(f"target {position} dated_history is oversize")
    if "\n" in dated or "\r" in dated:
        raise IntegrationError(f"target {position} dated_history must be single line")

    writer = _load_writer_module()
    if writer is not None:
        try:
            writer.validate_frontmatter(replacement)
        except Exception as exc:  # noqa: BLE001 - writer contract
            raise IntegrationError(
                f"target {position} replacement frontmatter rejected: {exc}"
            ) from exc
    else:
        _basic_frontmatter_check(replacement, position)

    abs_path = vault_root / rel
    if _has_symlink_component(vault_root, abs_path):
        raise IntegrationError(f"target {position} path crosses a symlink")
    if not abs_path.is_file():
        raise IntegrationError(f"target {position} note does not exist")
    if _real_conflict_present(abs_path):
        raise IntegrationError(f"target {position} has a Syncthing conflict copy")
    current_bytes = _safe_read(abs_path, MAX_NOTE_BYTES)
    current_sha = _sha256_bytes(current_bytes)
    if current_sha != expected and current_sha != replacement_sha:
        raise IntegrationConflict(
            f"target {position} current hash matches neither expected nor replacement"
        )

    relationships = obj["relationships"]
    if not isinstance(relationships, list):
        raise IntegrationError(f"target {position} relationships must be an array")
    if len(relationships) > MAX_RELATIONSHIPS:
        raise IntegrationError(f"target {position} relationships is oversize")
    rel_safe: list[dict] = []
    for j, relationship in enumerate(relationships):
        r_obj = _exact_keys(
            relationship, RELATIONSHIP_KEYS, f"target {position} relationship {j}"
        )
        r_path = _safe_note_rel(r_obj["path"])
        r_reason = _required_text(
            r_obj["reason"], f"target {position} relationship {j} reason", max_chars=1000
        )
        rel_abs = vault_root / r_path
        if _has_symlink_component(vault_root, rel_abs):
            raise IntegrationError(f"target {position} relationship {j} crosses symlink")
        if not rel_abs.is_file():
            raise IntegrationError(
                f"target {position} relationship {j} lacks an ordinary note"
            )
        rel_safe.append({"path": r_path, "reason": r_reason})

    _validate_introduced_links(
        replacement,
        vault_root=vault_root,
        existing_note_rels=existing_note_rels,
        target_rel=rel,
    )

    return {
        "path": rel,
        "expected_sha256": expected,
        "replacement_content": replacement,
        "replacement_sha256": replacement_sha,
        "current_summary_change": summary_change,
        "dated_history": dated,
        "relationships": rel_safe,
        "current_sha256": current_sha,
    }


def _basic_frontmatter_check(text: str, position: int) -> None:
    if not text.startswith("---\n"):
        raise IntegrationError(f"target {position} missing frontmatter block")
    end = text.find("\n---\n", 4)
    if end < 0:
        raise IntegrationError(f"target {position} frontmatter is not closed")
    body = text[end + len("\n---\n"):]
    if not body.strip():
        raise IntegrationError(f"target {position} body is empty")
    for field in ("tags", "date", "type", "origin", "up"):
        if not re.search(rf"(?m)^{re.escape(field)}\s*:", text[:end]):
            raise IntegrationError(f"target {position} missing field {field}")


def _real_conflict_present(path: Path) -> bool:
    directory = path.parent
    stem = path.stem.casefold() + ".sync-conflict-"
    try:
        for name in directory.iterdir():
            low = name.name.casefold()
            if low.startswith(stem) and low.endswith(".md"):
                return True
    except OSError:
        return False
    return False


def _link_target_note_name(raw: str) -> str | None:
    text = unquote(raw.strip().strip("<>"))
    text = text.split("#", 1)[0].split("|", 1)[0].strip()
    if not text:
        return None
    if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", text):
        raise IntegrationError(f"unsafe link scheme: {raw}")
    if text.startswith("/") or "\\" in text:
        raise IntegrationError(f"unsafe link path: {raw}")
    parts = text.split("/")
    if ".." in parts or "." in parts:
        raise IntegrationError(f"link path must not traverse: {raw}")
    return text


def _resolve_note_in_vault(vault: Path, target: str) -> list[str]:
    name = _link_target_note_name(target)
    if name is None:
        return []
    if "/" in name or name.endswith(".md"):
        rel = name if name.endswith(".md") else name + ".md"
        return [rel] if (vault / rel).is_file() else []
    matches: list[str] = []
    for entry in vault.rglob("*.md"):
        if entry.name == "index.md":
            continue
        if entry.stem.casefold() == name.casefold():
            try:
                rel = entry.relative_to(vault).as_posix()
            except ValueError:
                continue
            if ".sync-conflict-" in rel:
                continue
            if any(part.startswith(".") for part in entry.relative_to(vault).parts) or _has_symlink_component(vault, entry):
                continue
            matches.append(rel)
    return matches


def _validate_introduced_links(
    text: str,
    *,
    vault_root: Path,
    existing_note_rels: set[str] | None,
    target_rel: str,
    original_text: str | None = None,
) -> None:
    if original_text is None:
        original_text = _safe_read(vault_root / target_rel, MAX_NOTE_BYTES).decode("utf-8")
    def prose(value):
        # Example links in code are literal documentation, not graph edges.
        value = re.sub(r"(?ms)^\s*(`{3,}|~{3,})[^\n]*\n.*?^\s*\1[^\n]*(?:\n|$)", "", value)
        return re.sub(r"(`+)([^`]*?)\1", "", value)

    original_text, text = prose(original_text), prose(text)
    def links(value):
        found = [match.group(1) for pattern in (WIKILINK_RE, MDLINK_RE)
                 for match in pattern.finditer(value)]
        found += re.findall(r"(?m)^ {0,3}\[(?!\^)[^\]\n]+\]:\s*(<[^>\n]+>|[^\s\n]+)", value)

        class Anchors(HTMLParser):
            def handle_starttag(self, tag, attrs):
                found.extend(v for k, v in attrs if k in {"href", "src"} and v is not None)

        Anchors().feed(value)
        return found

    previous = set(links(original_text))
    introduced = links(text)
    for target in introduced:
        if target in previous:
            continue
        scheme = re.match(r"^([a-zA-Z][a-zA-Z0-9+.-]*):", target.strip().strip("<>"))
        if scheme and scheme.group(1).casefold() in {"https", "http", "codex"}:
            continue
        name = _link_target_note_name(target)
        if name is None:
            continue
        if name.startswith("Assets/"):
            asset = vault_root / name
            if _has_symlink_component(vault_root, asset) or not asset.is_file():
                raise IntegrationError(f"introduced asset is missing or unsafe: {target}")
            continue
        if "/" not in name and not name.endswith(".md"):
            matches = _resolve_note_in_vault(vault_root, name)
            if not matches:
                raise IntegrationError(
                    f"introduced link has no existing vault note: {target}"
                )
            if len(matches) > 1:
                raise IntegrationError(
                    f"introduced link is ambiguous: {target}"
                )
            continue
        rel = name if name.endswith(".md") else name + ".md"
        abs_note = vault_root / rel
        if _has_symlink_component(vault_root, abs_note):
            raise IntegrationError(f"introduced link crosses symlink: {target}")
        if not abs_note.is_file():
            raise IntegrationError(f"introduced link target is missing: {target}")
        if existing_note_rels is not None and rel not in existing_note_rels and rel != target_rel:
            raise IntegrationError(f"introduced link target was not in the snapshot")


def _validate_semantic_review(meta: object) -> dict:
    obj = _exact_keys(meta, SEMANTIC_REVIEW_KEYS, "semantic_review")
    supervisor = _required_text(
        obj["supervisor"], "semantic_review supervisor", max_chars=200
    )
    source_ref = _required_text(
        obj["source_reference"], "semantic_review source_reference", max_chars=500
    )
    statement = _required_text(
        obj["statement"], "semantic_review statement", max_chars=2000
    )
    return {
        "supervisor": supervisor,
        "source_reference": source_ref,
        "statement": statement,
        "readiness": (
            "semantic_review is an operator attestation that Codex read the "
            "local sources; it is not an independent factual verifier"
        ),
        "operator_attestation": True,
        "verified_by_validator": False,
    }


def _reject_promoted_assertion(source: dict) -> None:
    # Compatibility no-op: the strict top-level assertion_status guard is the
    # typed representation check. Exact quoted words are preserved verbatim,
    # including genuinely reported completed or approved statements.
    return None


def _operation_id(
    *,
    sources: list[dict],
    disposition: str,
    reason: str,
    targets: list[dict],
) -> str:
    source_bindings = []
    for source in sources:
        if source["kind"] == SOURCE_KIND_MEMORY:
            source_bindings.append(
                {
                    "kind": source["kind"],
                    "item_id": source["item_id"],
                    "evidence_sha256": source["evidence_sha256"],
                }
            )
        else:
            source_bindings.append(
                {
                    "kind": source["kind"],
                    "raw_id": source["raw_id"],
                    "raw_sha256": source["raw_sha256"],
                }
            )
    target_bindings = [
        {
            "path": t["path"],
            "before": t["expected_sha256"],
            "after": t["replacement_sha256"],
        }
        for t in targets
    ]
    payload = {
        "sources": sorted(source_bindings, key=lambda row: _canonical_json(row)),
        "disposition": disposition,
        "reason": reason,
        "targets": sorted(target_bindings, key=lambda row: row["path"]),
    }
    return "operation:" + _sha256_hex(payload)


def validate_proposal(
    proposal: dict, *, vault_root: Path, snapshot: dict
) -> dict:
    """Validate a version 1 integration proposal without writing anything."""

    vault_root = Path(vault_root)
    state = _normalize_snapshot(snapshot)
    item_index = _memory_source_index(state)
    obj = _exact_keys(proposal, PROPOSAL_KEYS, "proposal")
    _reject_event_state_keys(obj, "proposal")
    if (
        type(obj["schema_version"]) is not int
        or obj["schema_version"] != PROPOSAL_SCHEMA_VERSION
    ):
        raise IntegrationError("proposal schema_version is unsupported")
    assertion_status = _require_reported_assertion(obj["assertion_status"])
    disposition = obj["disposition"]
    if not isinstance(disposition, str) or disposition not in ALLOWED_DISPOSITIONS:
        raise IntegrationError("proposal disposition is unsupported")
    reason = _required_text(
        obj["reason"], "proposal reason", max_chars=MAX_REASON_CHARS
    )

    semantic_review = _validate_semantic_review(obj["semantic_review"])

    sources_raw = obj["sources"]
    if not isinstance(sources_raw, list):
        raise IntegrationError("proposal sources must be an array")
    if len(sources_raw) > MAX_SOURCES:
        raise IntegrationError("proposal sources exceed the bounded count")
    if not sources_raw:
        raise IntegrationError("proposal sources must be non-empty")

    raw_descriptors: list[dict] = []
    for i, raw in enumerate(sources_raw):
        if not isinstance(raw, dict):
            raise IntegrationError(f"source {i} must be an object")
        _reject_event_state_keys(raw, f"source {i}")
        if raw.get("kind") == SOURCE_KIND_RAW:
            raw_path = raw.get("raw_path")
            raw_sha = raw.get("raw_sha256")
            classification = raw.get("classification")
            if not isinstance(raw_path, str):
                raise IntegrationError(f"source {i} raw_path must be a string")
            if not isinstance(raw_sha, str):
                raise IntegrationError(f"source {i} raw_sha256 must be a string")
            if not isinstance(classification, str):
                raise IntegrationError(
                    f"source {i} classification must be a string"
                )
            raw_descriptors.append(
                {
                    "path": raw_path,
                    "sha256": raw_sha,
                    "classification": classification,
                    "legacy_disposition": None,
                }
            )
    raw_index = _raw_index(raw_descriptors)

    sources = [
        _validate_source_against_snapshot(
            raw,
            item_index,
            raw_index,
            i,
            vault_root=vault_root,
        )
        for i, raw in enumerate(sources_raw)
    ]

    seen_identities: set[str] = set()
    for i, source in enumerate(sources):
        ident = source.get("item_id") or source.get("raw_id")
        if ident in seen_identities:
            raise IntegrationError(
                f"source {i} duplicates an earlier source identity"
            )
        seen_identities.add(ident)  # type: ignore[arg-type]

    for source in sources:
        _reject_promoted_assertion(source)

    targets_raw = obj["targets"]
    if not isinstance(targets_raw, list):
        raise IntegrationError("proposal targets must be an array")
    if len(targets_raw) > MAX_TARGETS:
        raise IntegrationError("proposal targets exceed the bounded count")
    existing_note_rels = _collect_existing_note_rels(vault_root)
    targets = [
        _validate_target(
            raw,
            i,
            vault_root=vault_root,
            existing_note_rels=existing_note_rels,
        )
        for i, raw in enumerate(targets_raw)
    ]
    if len({t["path"] for t in targets}) != len(targets):
        raise IntegrationError("proposal targets contain duplicates")
    if disposition == "retained_leaf":
        if targets:
            raise IntegrationError("a retained leaf proposal must have zero targets")
    else:
        if not targets:
            raise IntegrationError(
                "an integrated proposal requires at least one target"
            )

    operation_id = _operation_id(
        sources=sources, disposition=disposition, reason=reason, targets=targets
    )
    return {
        "schema_version": PROPOSAL_SCHEMA_VERSION,
        "operation_id": operation_id,
        "writer_source_ref": f"k2b-digest:{operation_id}",
        "assertion_status": assertion_status,
        "disposition": disposition,
        "reason": reason,
        "sources": sources,
        "targets": targets,
        "semantic_review": semantic_review,
    }


def _collect_existing_note_rels(vault_root: Path) -> set[str]:
    result: set[str] = set()
    try:
        for entry in vault_root.rglob("*.md"):
            if entry.name == "index.md":
                continue
            rel = entry.relative_to(vault_root).as_posix()
            if ".sync-conflict-" in rel:
                continue
            result.add(rel)
    except OSError:
        return result
    return result


def _raw_descriptors_for_sources(sources: list[dict]) -> list[dict]:
    descriptors: list[dict] = []
    for source in sources:
        if source["kind"] != SOURCE_KIND_RAW:
            continue
        descriptors.append(
            {
                "path": source["raw_path"],
                "sha256": source["raw_sha256"],
                "classification": source["classification"],
                "legacy_disposition": None,
            }
        )
    return descriptors


def _validate_raw_against_source(source: dict, vault_root: Path) -> None:
    descriptor = _validate_raw_descriptor(
        {
            "path": source["raw_path"],
            "sha256": source["raw_sha256"],
            "classification": source["classification"],
            "legacy_disposition": None,
        },
        0,
    )
    if descriptor["classification"] == "technical_record":
        raise IntegrationError("raw source is a technical record")
    payload = _read_raw_capture(vault_root, descriptor)
    quote = source["evidence_quote"]
    if not isinstance(quote, str) or not quote:
        raise IntegrationError("raw source evidence_quote must be non-empty")
    try:
        quote_bytes = quote.encode("utf-8")
    except UnicodeError as exc:
        raise IntegrationError("raw source evidence_quote is not encodable") from exc
    if quote_bytes not in payload:
        raise IntegrationError("raw capture does not contain the evidence quote")


# --- receipt recording --------------------------------------------------------


def _default_receipt_dir(state_root: Path) -> Path:
    return Path(state_root) / "integration-receipts"


def _receipt_path(state_root: Path, operation_id: str) -> Path:
    if OPERATION_ID_RE.fullmatch(operation_id) is None:
        raise IntegrationError("operation_id is malformed")
    return _default_receipt_dir(state_root) / f"{operation_id}.json"


@contextmanager
def _integration_lock(state_root: Path) -> Iterator[None]:
    lock_path = Path(state_root) / ".integration.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+b") as handle:
        _flock(handle.fileno(), timeout=30.0)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _flock(fd: int, *, timeout: float) -> None:
    started = time.monotonic()
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        except OSError:
            if time.monotonic() - started >= timeout:
                raise IntegrationError("integration lock timeout")
            time.sleep(0.05)


def _atomic_write_private(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, raw_tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    tmp = Path(raw_tmp)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
        try:
            dir_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except OSError:
            pass
    finally:
        if tmp.exists():
            tmp.unlink()


def _verify_state_location(state_root: Path, vault_root: Path) -> None:
    state_resolved = _resolved_without_symlinks(Path(state_root), "state_root")
    vault_resolved = Path(os.path.realpath(str(vault_root)))
    native_resolved = _resolved_without_symlinks(
        Path(_native_memory_root()), "native automatic-memory root"
    )
    if _overlaps(state_resolved, vault_resolved):
        raise IntegrationError("state_root must not overlap the vault")
    if _overlaps(state_resolved, native_resolved):
        raise IntegrationError(
            "state_root must not overlap the native automatic-memory tree"
        )
    if Path(state_root).exists() and Path(state_root).is_symlink():
        raise IntegrationError("state_root must not be a symlink")


def _load_writer_receipt(
    writer_state_root: Path, receipt_ref: str
) -> tuple[Path, dict]:
    match = RECEIPT_REF_RE.fullmatch(receipt_ref)
    if match is None:
        raise IntegrationError("note receipt reference is malformed")
    op_key = match.group(1)
    path = _resolve_state_child(
        writer_state_root, receipt_ref, "note receipt", "note-write-receipts"
    )
    parsed = _read_durable_json(path)
    return path, parsed


def _verify_recovery_copy(
    writer_state_root: Path,
    backups_key: object,
    expected_bytes: bytes,
    label: str = "note recovery copy",
) -> None:
    path = _resolve_state_child(
        writer_state_root, backups_key, label, "note-write-backups"
    )
    payload = _safe_read(path, MAX_NOTE_BYTES)
    if payload != expected_bytes:
        raise IntegrationError(f"{label} disagrees with expected bytes")


def _match_writer_receipt(
    receipt_ref: str,
    *,
    writer_state_root: Path,
    expected_target: dict,
    operation_id: str,
    writer_source_ref: str,
    vault_root: Path,
) -> dict:
    match = RECEIPT_REF_RE.fullmatch(receipt_ref)
    if match is None:
        raise IntegrationError("note receipt reference is malformed")
    file_operation = match.group(1)
    _, durable = _load_writer_receipt(writer_state_root, receipt_ref)

    if durable.get("operation") != file_operation:
        raise IntegrationError("writer receipt operation disagrees with filename")
    result = durable.get("result")
    if result not in ("saved-local", "already-present"):
        raise IntegrationError(f"writer receipt result {result!r} is not completed")
    if durable.get("path") != expected_target["path"]:
        raise IntegrationError("writer receipt path disagrees with the target")
    if durable.get("source_ref") != writer_source_ref:
        raise IntegrationError("writer receipt source_ref disagrees with the operation")
    summary = durable.get("summary")
    status = durable.get("status")
    section = durable.get("section")
    if not isinstance(summary, str) or any(value is not None and not isinstance(value, str) for value in (status, section)):
        raise IntegrationError("writer receipt index metadata is malformed")
    if summary != _summary_for_target(expected_target):
        raise IntegrationError("writer receipt summary disagrees with the reviewed proposal")
    if status is not None:
        raise IntegrationError("supervised integration preserves the existing index status")

    expected_operation = _expected_writer_operation_key(
        {**expected_target, "summary": summary, "status": status, "section": section},
        writer_source_ref,
    )
    if expected_operation != file_operation:
        raise IntegrationError(
            "writer receipt operation_key does not bind the replacement bytes and fields"
        )

    expected_expected_sha = expected_target["expected_sha256"]
    recorded_expected = durable.get("expected_sha256")
    if recorded_expected != expected_expected_sha:
        raise IntegrationError("writer receipt expected_sha256 disagrees")

    note_sha = durable.get("note_sha256")
    if note_sha != expected_target["replacement_sha256"]:
        raise IntegrationError("writer receipt note_sha256 disagrees with the replacement")

    backups = durable.get("backups") or {}
    if not isinstance(backups, dict):
        raise IntegrationError("writer receipt backups is malformed")
    if "note_after" not in backups:
        raise IntegrationError("writer receipt lacks a note_after recovery copy")
    expected_after = expected_target["replacement_content"].encode("utf-8")
    _verify_recovery_copy(
        writer_state_root, backups["note_after"], expected_after, "note_after recovery copy"
    )

    before_key = backups.get("note_before")
    content_changed = (
        expected_target["expected_sha256"] != expected_target["replacement_sha256"]
    )
    if content_changed:
        if before_key is None:
            raise IntegrationError(
                "real content change requires a note_before recovery copy"
            )
        before_path = _resolve_state_child(
            writer_state_root,
            before_key,
            "note_before recovery copy",
            "note-write-backups",
        )
        before_bytes = _safe_read(before_path, MAX_NOTE_BYTES)
        if _sha256_bytes(before_bytes) != expected_target[
            "expected_sha256"
        ]:
            raise IntegrationError(
                "note_before recovery copy hash disagrees with expected_sha256"
            )
        _validate_introduced_links(
            expected_target["replacement_content"], vault_root=vault_root,
            existing_note_rels=None, target_rel=expected_target["path"],
            original_text=before_bytes.decode("utf-8"),
        )
    elif before_key is not None:
        _resolve_state_child(
            writer_state_root,
            before_key,
            "note_before recovery copy",
            "note-write-backups",
        )

    return {
        "receipt_ref": receipt_ref,
        "operation_key": durable.get("operation"),
        "result": result,
        "path": durable.get("path"),
        "note_sha256": note_sha,
        "expected_sha256": durable.get("expected_sha256"),
        "source_ref": durable.get("source_ref"),
        "summary": durable.get("summary"),
        "status": durable.get("status"),
        "section": durable.get("section"),
    }


def _validate_post_save_target(target: dict, vault_root: Path) -> None:
    abs_path = vault_root / target["path"]
    if _has_symlink_component(vault_root, abs_path):
        raise IntegrationError("saved target path crosses a symlink")
    if not abs_path.is_file():
        raise IntegrationError("saved target is missing")
    if _real_conflict_present(abs_path):
        raise IntegrationError("saved target acquired a Syncthing conflict copy")
    current = _safe_read(abs_path, MAX_NOTE_BYTES)
    sha = _sha256_bytes(current)
    if sha != target["replacement_sha256"]:
        raise IntegrationConflict("saved target does not match replacement hash")


def _validate_note_receipts(
    note_receipts: list,
    *,
    validated: dict,
    writer_state_root: Path,
    vault_root: Path,
) -> list[dict]:
    if not isinstance(note_receipts, list):
        raise IntegrationError("note_receipts must be an array")
    targets = validated["targets"]
    if validated["disposition"] == "retained_leaf":
        if note_receipts:
            raise IntegrationError("a retained leaf operation has no note receipts")
        return []
    if len(note_receipts) != len(targets):
        raise IntegrationError(
            "note receipts must exactly cover the operation's targets"
        )
    refs: dict[str, list[dict]] = {t["path"]: [] for t in targets}
    used_refs: set[str] = set()
    for raw in note_receipts:
        obj = _require_object(raw, "note receipt")
        result = obj.get("result")
        if result not in ("saved-local", "already-present"):
            raise IntegrationError("note receipt result is not completed")
        receipt_ref = obj.get("receipt")
        if not isinstance(receipt_ref, str):
            raise IntegrationError("note receipt reference is missing")
        if RECEIPT_REF_RE.fullmatch(receipt_ref) is None:
            raise IntegrationError("note receipt reference is malformed")
        if receipt_ref in used_refs:
            raise IntegrationError("duplicate note receipt reference")
        used_refs.add(receipt_ref)
        matched_target = None
        for target in targets:
            if obj.get("path") == target["path"]:
                matched_target = target
                break
        if matched_target is None:
            raise IntegrationError("note receipt path is unrelated to the operation")
        detail = _match_writer_receipt(
            receipt_ref,
            writer_state_root=writer_state_root,
            expected_target=matched_target,
            operation_id=validated["operation_id"],
            writer_source_ref=validated["writer_source_ref"],
            vault_root=vault_root,
        )
        _validate_post_save_target(matched_target, vault_root)
        refs[matched_target["path"]].append(detail)
    bound: list[dict] = []
    seen_refs: set[str] = set()
    for target in targets:
        matches = refs[target["path"]]
        if len(matches) != 1:
            raise IntegrationError(
                f"target {target['path']} must match exactly one writer receipt"
            )
        ref = matches[0]["receipt_ref"]
        if ref in seen_refs:
            raise IntegrationError("duplicate writer receipt binding")
        seen_refs.add(ref)
        bound.append(matches[0])
    return bound


def _current_vault_root() -> Path:
    return _default_vault_root()


def _build_receipt(validated: dict, writer_receipts: list[dict]) -> dict:
    source_refs: list[dict] = []
    for source in validated["sources"]:
        if source["kind"] == SOURCE_KIND_MEMORY:
            source_refs.append(
                {
                    "kind": source["kind"],
                    "item_id": source["item_id"],
                    "source_identity": source["source_identity"],
                    "evidence_sha256": source["evidence_sha256"],
                }
            )
        else:
            source_refs.append(
                {
                    "kind": source["kind"],
                    "raw_id": source["raw_id"],
                    "raw_path": source["raw_path"],
                    "raw_sha256": source["raw_sha256"],
                }
            )
    writer_by_path = {r["path"]: r for r in writer_receipts}
    targets: list[dict] = []
    for target in validated["targets"]:
        writer = writer_by_path.get(target["path"])
        targets.append(
            {
                "path": target["path"],
                "before_sha256": target["expected_sha256"],
                "after_sha256": target["replacement_sha256"],
                "writer_receipt_id": writer["receipt_ref"] if writer else None,
            }
        )
    return {
        "schema_version": RECEIPT_SCHEMA_VERSION,
        "operation_id": validated["operation_id"],
        "source_ref": validated["writer_source_ref"],
        "disposition": validated["disposition"],
        "reason": validated["reason"],
        "sources": source_refs,
        "targets": targets,
        "recorded_at": _now_iso(),
        "assertion_status": "reported_assertion",
    }


def record_integration(
    proposal: dict,
    note_receipts: list[dict],
    *,
    state_root: Path,
    vault_root: Path,
    snapshot: dict,
) -> dict:
    """Record one Home-only integration receipt after verified note saves."""

    _require_home_operator()
    state_root = Path(state_root)
    vault_root = Path(vault_root)
    if not vault_root.exists():
        raise IntegrationError(f"vault root not found: {vault_root}")
    _verify_state_location(state_root, vault_root)

    validated = validate_proposal(
        proposal, vault_root=vault_root, snapshot=snapshot
    )

    writer_state_root = _resolved_without_symlinks(
        Path(os.environ.get("K2B_LOCAL_STATE", str(state_root.parent))),
        "writer state_root",
    )

    for source in validated["sources"]:
        if source["kind"] == SOURCE_KIND_RAW:
            _validate_raw_against_source(source, vault_root)

    receipt_path = _receipt_path(state_root, validated["operation_id"])

    with _integration_lock(state_root):
        _check_prior_coverage(validated, _read_receipt_ledger(state_root))
        existing = _read_durable_json(receipt_path) if receipt_path.exists() else None
        if existing is not None:
            if _validate_known_receipt(existing) is None:
                raise IntegrationError("existing durable receipt is invalid")
            # Reconstruct only references to retained durable evidence. The
            # caller's transient stdout file is not required for replay.
            note_receipts = [{"path": target["path"], "result": "saved-local",
                "receipt": target["writer_receipt_id"]} for target in existing["targets"]]
        writer_receipts: list[dict] = []
        if validated["disposition"] == "integrated":
            writer_receipts = _validate_note_receipts(
                note_receipts,
                validated=validated,
                writer_state_root=writer_state_root,
                vault_root=vault_root,
            )
            for target in validated["targets"]:
                _validate_post_save_target(target, vault_root)
        elif note_receipts:
            raise IntegrationError("a retained leaf operation has zero note receipts")

        receipt = _build_receipt(validated, writer_receipts)

        if existing is not None:
            if not _receipts_equivalent(existing, receipt):
                raise IntegrationError("existing durable receipt disagrees with the operation")
            return {
                "operation_id": validated["operation_id"],
                "status": "already-recorded",
                "receipt_path": str(receipt_path),
                "disposition": existing.get("disposition", validated["disposition"]),
            }
        _atomic_write_private(receipt_path, _canonical_json(receipt) + b"\n")
    return {
        "operation_id": validated["operation_id"],
        "status": "recorded",
        "receipt_path": str(receipt_path),
        "disposition": receipt["disposition"],
    }


# --- CLI ----------------------------------------------------------------------


def _load_json_path(path: Path, label: str) -> object:
    payload = _safe_read(Path(path), MAX_JSON_BYTES)
    try:
        return json.loads(payload.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise IntegrationError(f"{label} is not readable JSON: {path}") from exc


def _load_proposals_list(path: Path) -> dict:
    payload = _load_json_path(path, "proposal")
    if isinstance(payload, dict) and "proposals" in payload and "schema_version" not in payload:
        return payload
    return {"proposals": [payload]}


def _load_receipts_list(path: Path) -> list[dict]:
    payload = _load_json_path(path, "receipts")
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict) and "receipts" in payload and isinstance(payload["receipts"], list):
        return payload["receipts"]
    if isinstance(payload, dict) and isinstance(payload.get("receipt"), str):
        return [payload]
    raise IntegrationError("receipts file must be an array or carry a receipts list")


def _source_keys(sources):
    return {(s["kind"], s.get("item_id") or s.get("raw_id"),
        s.get("evidence_sha256") or s.get("raw_sha256")) for s in sources}


def _read_receipt_ledger(state_root: Path) -> list[dict]:
    """Strict, bounded, read-only ledger view for first-save/replay guards."""
    folder = _default_receipt_dir(state_root)
    if folder.is_symlink() or any(p.is_symlink() for p in folder.parents):
        raise IntegrationError("receipt directory crosses a symlink")
    if not folder.exists():
        return []
    rows, budget = [], 16 * 1024 * 1024
    with os.scandir(folder) as entries:
        for count, entry in enumerate(entries):
            if count >= 1000 or budget <= 0:
                raise IntegrationError("receipt inventory exceeds the bounded scan")
            if not entry.name.endswith(".json"):
                continue
            payload = _safe_read(Path(entry.path), min(MAX_RECEIPT_BYTES, budget))
            budget -= len(payload)
            try:
                row = json.loads(payload)
            except (ValueError, UnicodeError, RecursionError) as exc:
                raise IntegrationError(f"receipt ledger contains unreadable evidence: {entry.name!r}") from exc
            if _validate_known_receipt(row) is None or entry.name != row["operation_id"] + ".json":
                raise IntegrationError(f"receipt ledger contains invalid evidence: {entry.name!r}")
            rows.append(row)
    return rows


def _check_prior_coverage(validated: dict, receipts: list[dict]) -> None:
    keys = _source_keys(validated["sources"])
    for row in receipts:
        if row["operation_id"] != validated["operation_id"] and keys & _source_keys(row["sources"]):
            raise IntegrationError("source evidence is already covered by a different operation; operator review required")


def _cmd_queue(args) -> int:
    vault = Path(args.vault_root or _default_vault_root())
    snapshot_path = Path(args.snapshot) if args.snapshot else vault / "System/memory/automatic-memory-current.json"
    snapshot = _load_json_path(snapshot_path, "snapshot")
    receipts, problems, truncated = [], [], False
    if args.receipts:
        supplied = _load_receipts_list(Path(args.receipts))
        if len(supplied) > 1000:
            supplied, truncated = supplied[:1000], True
        for receipt in supplied:
            if _validate_known_receipt(receipt) is None:
                problems.append("invalid supplied receipt binding")
            else:
                receipts.append(receipt)
    else:
        folder = _default_receipt_dir(Path(args.state_root or _default_state_root()))
        budget = 16 * 1024 * 1024
        if folder.is_symlink() or any(parent.is_symlink() for parent in folder.parents):
            raise IntegrationError("receipt directory crosses a symlink")
        if folder.exists():
            count = 0
            try:
                with os.scandir(folder) as entries:
                    for entry in entries:
                        count += 1
                        if count > 1000:
                            truncated = True
                            break
                        if not entry.name.endswith(".json"):
                            continue
                        if budget <= 0:
                            truncated = True
                            break
                        try:
                            payload = _safe_read(Path(entry.path), min(MAX_RECEIPT_BYTES, budget))
                            budget -= len(payload)
                            receipt = json.loads(payload.decode("utf-8"))
                            if _validate_known_receipt(receipt) is None:
                                raise IntegrationError("invalid receipt binding")
                            if entry.name != receipt["operation_id"] + ".json":
                                raise IntegrationError("receipt filename disagrees")
                            receipts.append(receipt)
                        except (IntegrationError, OSError, ValueError, UnicodeError, RecursionError):
                            problems.append(f"unreadable or invalid receipt: {entry.name!r}")
            except OSError as exc:
                raise IntegrationError("receipt directory unavailable") from exc
    raw_sources = (
        _load_json_path(Path(args.raw_sources), "raw_sources")
        if args.raw_sources
        else None
    )
    if raw_sources is not None and not isinstance(raw_sources, list):
        raise IntegrationError("raw_sources file must be an array")
    rows = build_queue(snapshot, receipts, raw_sources=raw_sources)
    overlaps = sum(row.get("reason") == "overlapping integration receipts require operator review" for row in rows)
    home_view = _physical_operator() == "keithmbpm2"
    print(json.dumps({"schema_version": QUEUE_SCHEMA_VERSION, "queue": rows,
        "status": "partial" if problems or truncated or overlaps or not home_view else "observed",
        "scope": {"receipt_authority": "home_local" if home_view else "unverified_on_this_host",
                  "global_completeness": "unmeasured"},
        "receipt_health": {"loaded": len(receipts), "rejected": len(problems), "truncated": truncated, "overlapping_sources": overlaps},
        "limitations": (["raw source inventory not supplied"] if raw_sources is None else [])
            + ([] if home_view else ["Home integration ledger was not inspected; local rows are not global pending counts"])
            + sorted(set(problems))}, sort_keys=True))
    return 0


def _cmd_validate(args) -> int:
    vault_root = Path(args.vault_root or _default_vault_root())
    snapshot = _load_json_path(Path(args.snapshot) if args.snapshot else vault_root / "System/memory/automatic-memory-current.json", "snapshot")
    if not vault_root.is_dir():
        raise IntegrationError(f"vault root not found: {vault_root}")
    proposal = _load_json_path(Path(args.proposal), "proposal")
    validated = validate_proposal(proposal, vault_root=vault_root, snapshot=snapshot)
    if _physical_operator() == "keithmbpm2":
        _check_prior_coverage(validated, _read_receipt_ledger(Path(args.state_root or _default_state_root())))
    out = {
        "operation_id": validated["operation_id"],
        "writer_source_ref": validated["writer_source_ref"],
        "disposition": validated["disposition"],
        "targets": [
            {
                "path": t["path"],
                "expected_sha256": t["expected_sha256"],
                "replacement_sha256": t["replacement_sha256"],
                "replacement_content": t["replacement_content"],
                "current_summary_change": t["current_summary_change"],
                "dated_history": t["dated_history"],
                "relationships": t["relationships"],
            }
            for t in validated["targets"]
        ],
        "sources": validated["sources"],
        "semantic_review": validated["semantic_review"],
        "coverage_check": "home_local" if _physical_operator() == "keithmbpm2" else "unverified_on_this_host",
    }
    print(json.dumps(out, sort_keys=True))
    return 0


def _cmd_record(args) -> int:
    state_root = Path(args.state_root or _default_state_root())
    vault_root = Path(args.vault_root or _default_vault_root())
    snapshot = _load_json_path(Path(args.snapshot) if args.snapshot else vault_root / "System/memory/automatic-memory-current.json", "snapshot")
    proposal = _load_json_path(Path(args.proposal), "proposal")
    note_receipts = (
        _load_receipts_list(Path(args.note_receipts)) if args.note_receipts else []
    )
    result = record_integration(
        proposal,
        note_receipts,
        state_root=state_root,
        vault_root=vault_root,
        snapshot=snapshot,
    )
    print(json.dumps(result, sort_keys=True))
    return 0


def _build_parser():
    import argparse

    parser = argparse.ArgumentParser(
        prog="knowledge-digest",
        description="Supervised K2B knowledge integration accounting.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    q = sub.add_parser("queue")
    q.add_argument("--snapshot", default=None)
    q.add_argument("--vault-root", default=None)
    q.add_argument("--state-root", default=None)
    q.add_argument("--receipts", default=None)
    q.add_argument("--raw-sources", dest="raw_sources", default=None)
    q.set_defaults(func=_cmd_queue)

    v = sub.add_parser("validate")
    v.add_argument("--snapshot", default=None)
    v.add_argument("--proposal", required=True)
    v.add_argument("--vault-root", dest="vault_root", default=None)
    v.add_argument("--state-root", default=None)
    v.set_defaults(func=_cmd_validate)

    r = sub.add_parser("record")
    r.add_argument("--snapshot", default=None)
    r.add_argument("--proposal", required=True)
    r.add_argument("--note-receipts", dest="note_receipts", default=None)
    r.add_argument("--state-root", dest="state_root", default=None)
    r.add_argument("--vault-root", dest="vault_root", default=None)
    r.set_defaults(func=_cmd_record)
    return parser


def main(argv: list[str] | None = None) -> int:
    import sys

    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return int(exc.code) if isinstance(exc.code, int) else 2
    try:
        return args.func(args)
    except IntegrationError as exc:
        sys.stderr.write(f"knowledge-digest: {exc}\n")
        return 1
    except (OSError, UnicodeError) as exc:
        sys.stderr.write(f"knowledge-digest: input unavailable: {exc}\n")
        return 1




ASSERTION_STATUS_REPORTED = "reported_assertion"

ASSERTION_EVENT_STATE_KEYS = {
    "state",
    "event_state",
    "lifecycle",
    "status_event",
    "completion",
    "completed",
    "approved",
}

def _require_safe_rel_components(path: str, label: str) -> None:
    if path.startswith("/") or path.startswith("~") or "\\" in path:
        raise IntegrationError(f"{label} must be a vault-relative POSIX path")
    if ":" in path.split("/", 1)[0]:
        raise IntegrationError(f"{label} must not contain a drive or scheme prefix")
    parts = path.split("/")
    for part in parts:
        if part in ("", ".", "..") or part.startswith("."):
            raise IntegrationError(f"{label} contains an unsafe component")
        if "\x00" in part:
            raise IntegrationError(f"{label} contains NUL bytes")

def _source_assertion_metadata(source: dict, snapshot_entry: dict | None) -> dict:
    if source["kind"] == SOURCE_KIND_MEMORY:
        if snapshot_entry is None:
            raise IntegrationError("memory source lacks a validated snapshot entry")
        version = snapshot_entry["version"]
        return {
            "record_kind": snapshot_entry["kind"],
            "speaker": version["speaker_source"],
        }
    return {
        "record_kind": "raw_capture",
        "speaker": source["speaker"],
    }

def _require_reported_assertion(value: object) -> str:
    if not isinstance(value, str):
        raise IntegrationError("assertion_status must be a string")
    if value != ASSERTION_STATUS_REPORTED:
        raise IntegrationError(
            "assertion_status must be the fixed value "
            f"{ASSERTION_STATUS_REPORTED!r}"
        )
    return value

def _reject_event_state_keys(obj: dict, label: str) -> None:
    keys = {k for k in obj.keys() if isinstance(k, str)}
    folded = {k.casefold() for k in keys}
    hits = sorted(folded & ASSERTION_EVENT_STATE_KEYS)
    if hits:
        raise IntegrationError(
            f"{label} may not declare event-state keys: {hits}"
        )

def _resolved_without_symlinks(path: Path, label: str) -> Path:
    """Resolve a path after rejecting any symlink component on the way down.

    Missing tail components are permitted (they are created later with
    private permissions); every existing ancestor must be a real directory.
    """
    raw = Path(os.path.abspath(str(path)))
    current = Path(raw.anchor) if raw.is_absolute() else Path(".")
    parts = raw.parts[1:] if raw.is_absolute() else raw.parts
    for part in parts:
        current = current / part
        if current.is_symlink():
            raise IntegrationError(f"{label} must not traverse a symlink: {current}")
        if not current.exists():
            continue
        if not current.is_dir():
            raise IntegrationError(f"{label} crosses a non-directory: {current}")
    return Path(os.path.realpath(str(raw)))

def _overlaps(a: Path, b: Path) -> bool:
    return a == b or _is_within(a, b) or _is_within(b, a)

def _resolve_state_child(
    writer_state_root: Path, relative: object, label: str, prefixed_root: str
) -> Path:
    if not isinstance(relative, str):
        raise IntegrationError(f"{label} reference is malformed")
    if not relative.startswith(prefixed_root + "/"):
        raise IntegrationError(f"{label} reference escapes the writer state root")
    rel = relative[len(prefixed_root) + 1:]
    if not rel or rel.startswith("/") or "\\" in rel:
        raise IntegrationError(f"{label} reference is malformed")
    parts = rel.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise IntegrationError(f"{label} reference traverses the state root")
    root_resolved = _resolved_without_symlinks(Path(writer_state_root), "writer state_root")
    candidate = root_resolved / prefixed_root / rel
    current = root_resolved
    for part in [prefixed_root, *parts]:
        current = current / part
        if current.is_symlink():
            raise IntegrationError(f"{label} crosses a symlink component")
    resolved = Path(os.path.realpath(str(candidate)))
    if not _is_within(root_resolved, resolved) or resolved == root_resolved:
        raise IntegrationError(f"{label} escapes the writer state root")
    if not resolved.is_file():
        raise IntegrationError(f"{label} is missing")
    return resolved

def _read_durable_json(path: Path) -> dict:
    payload = _safe_read(path, MAX_RECEIPT_BYTES)
    try:
        parsed = json.loads(payload.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise IntegrationError("durable receipt is not readable JSON") from exc
    except RecursionError as exc:
        raise IntegrationError("durable receipt JSON is too deeply nested") from exc
    if not isinstance(parsed, dict):
        raise IntegrationError("durable receipt is not an object")
    return parsed

def _expected_writer_operation_key(expected_target: dict, writer_source_ref: str) -> str:
    writer = _load_writer_module()
    if writer is None or not hasattr(writer, "operation_key"):
        raise IntegrationError("note writer operation_key helper unavailable")
    path = expected_target["path"]
    replacement = expected_target["replacement_content"].encode("utf-8")
    summary = expected_target["summary"]
    status = expected_target["status"]
    source_ref = writer_source_ref
    section = expected_target["section"]
    try:
        return writer.operation_key(
            path, replacement, summary, status, source_ref, section
        )
    except Exception as exc:  # noqa: BLE001 - writer contract, fail closed
        raise IntegrationError(f"note writer operation_key rejected arguments: {exc}") from exc

_RECEIPT_IMMUTABLE_KEYS = (
    "schema_version",
    "operation_id",
    "source_ref",
    "disposition",
    "reason",
    "sources",
    "targets",
    "assertion_status",
)

def _receipts_equivalent(existing: dict, fresh: dict) -> bool:
    for key in _RECEIPT_IMMUTABLE_KEYS:
        if existing.get(key) != fresh.get(key):
            return False
    return True

def _summary_for_target(target: dict) -> str:
    if isinstance(target.get("current_summary_change"), str):
        return target["current_summary_change"]
    return target["dated_history"]

if __name__ == "__main__":  # pragma: no cover
    import sys

    sys.exit(main())
