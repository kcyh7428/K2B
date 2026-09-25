"""Source-backed Codex conversation discovery and interactive K2B capture."""
from __future__ import annotations

import argparse
import errno
import fcntl
import hashlib
import json
import math
import os
import random
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Callable, Iterable

import automatic_memory
import native_job_status


# Canonical K2B timezone. See wiki/context/context_timezone-convention.md.
# K2B reasons in HKT (UTC+8). All date bucketing and "today/yesterday" defaults
# resolve to HKT calendar boundaries regardless of host system clock.
HKT = timezone(timedelta(hours=8))


REPO_ROOT = Path(__file__).resolve().parents[2]
PROMPT_PATH = REPO_ROOT / "scripts" / "prompts" / "eod-capture-extract.md"
MINIMAX_JSON_JOB = REPO_ROOT / "scripts" / "minimax-json-job.sh"
WIKI_LOG_APPEND = REPO_ROOT / "scripts" / "wiki-log-append.sh"
SHELF_WRITER = REPO_ROOT / "scripts" / "washing-machine" / "shelf-writer.sh"
EXTRACTION_SCHEMA_VERSION = "1.0"
ALL_ITEMS_REJECTED_SKIP_REASON = "all_items_rejected_after_validation"
MAX_EXTRACTOR_STDOUT_BYTES = 1_000_000
MAX_EXTRACTOR_STDERR_BYTES = 200_000
DEFAULT_EXTRACTOR_TIMEOUT_SECONDS = 1200
MAX_EXTRACTOR_TIMEOUT_SECONDS = 2400
DEFAULT_SHELF_WRITER_TIMEOUT_SECONDS = 30
DEFAULT_MEMORY_TRANSPORT_TIMEOUT_SECONDS = 60
MAX_MEMORY_TRANSPORT_STDOUT_BYTES = 8_000_000
MAX_MEMORY_EXTRACTION_ATTEMPTS = 3
MIN_EVIDENCE_QUOTE_CHARS = 10
DEFAULT_DIALOGUE_CHUNK_CHARS = 4000
INTERACTIVE_THREAD_SOURCES = {"cli", "codex_app", "desktop", "user", "vscode"}


class DialogueParseTimeout(RuntimeError):
    """A bounded dialogue parse exceeded the caller's monotonic deadline.

    Raised inside the record reader so one very large session file cannot
    block far past a scan budget. Callers turn it into an honest partial
    result, never into "no backlog".
    """
MEMORY_WORK_ID_RE = re.compile(r"^work:[0-9a-f]{64}$")
SJM_MEMORY_SSH_ALIAS = "sjm-ai"
SJM_MEMORY_REMOTE_PYTHON_REL = "Projects/K2B/venv/washing-machine/bin/python"
SJM_MEMORY_REMOTE_COMMAND_REL = "Projects/K2B/scripts/eod-capture.py"
PROJECT_SCOPE_RE = re.compile(
    r"(^|/)(?:Projects|\.codex/worktrees/[^/]+)/(K2B|K2Bi)(?:$|/)"
)
ENCODED_PROJECT_SCOPE_RE = re.compile(
    r"(?:^|[-/])Projects-(?:K2B|K2Bi)(?:/|$)"
)
PIPE_UNSAFE_FIELDS = (
    "predicate",
    "scope",
    "dedupe_key",
)
CONTROL_WHITESPACE_UNSAFE_FIELDS = (
    "subject",
    "predicate",
    "object",
    "scope",
    "dedupe_key",
)
DEDUPE_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*(?::[a-z0-9][a-z0-9._-]*)+$")
EVIDENCE_QUOTE_UNSUPPORTED_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
PREDICATE_WHITESPACE_RE = re.compile(r"\s+")
NAIVE_TIMESTAMP_DIGIT_RE = re.compile(r"\d")
SECRET_VALUE_PATTERN = r'(?:"[^"\r\n]*"|\'[^\'\r\n]*\'|[^\s]+)'
SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)\b((?:[A-Z0-9_]*(?:API_?KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL|AUTH|COOKIE|OAUTH)[A-Z0-9_]*)=)"
    + f"({SECRET_VALUE_PATTERN})"
)
SECRET_OPTION_RE = re.compile(
    r"(?i)(--[a-z0-9_-]*(?:api[-_]?key|token|secret|password|credential|auth|cookie|oauth)"
    r"[a-z0-9_-]*(?:=|\s+))"
    + f"({SECRET_VALUE_PATTERN})"
)
AUTH_VALUE_RE = re.compile(
    r"(?i)(\b(?:proxy-authorization|authorization|authentication|set-cookie|cookie|"
    r"x-api-key|x-auth-token)\s*:\s*(?:bearer\s+)?)([^\r\n]+)"
)
CREDENTIAL_URL_RE = re.compile(r"([a-zA-Z][a-zA-Z0-9+.-]*://)[^/@\s]+@")
SECRET_TOKEN_RE = re.compile(
    r"\b(?:sk-[A-Za-z0-9_-]{8,}|gh[opusr]_[A-Za-z0-9]{20,}|"
    r"github_pat_[A-Za-z0-9_]{20,}|"
    r"glpat-[A-Za-z0-9-]{20,}|AKIA[0-9A-Z]{16}|"
    r"(?:tk|tok)_[A-Za-z0-9_-]{6,}|"
    r"(?!(?:[0-9a-fA-F]{40,})\b)[A-Za-z0-9+/]{40,}={0,2})\b"
)
# Process-scoped dedup: emit at most one stderr warning per unique format shape
# (digits replaced with 'D'). Rate-limits the convention-violation log so a
# producer emitting many naive timestamps does not flood stderr.
_NAIVE_TIMESTAMP_WARNED_FORMATS: set[str] = set()

ExtractFunc = Callable[[str, Path], dict | None]


def _extractor_timeout_seconds() -> int:
    raw = os.environ.get("K2B_EOD_EXTRACTOR_TIMEOUT_SECONDS", "").strip()
    if not raw:
        return DEFAULT_EXTRACTOR_TIMEOUT_SECONDS
    try:
        value = int(raw)
    except ValueError:
        print(
            "eod-capture: warning: invalid K2B_EOD_EXTRACTOR_TIMEOUT_SECONDS="
            f"{raw!r}; using {DEFAULT_EXTRACTOR_TIMEOUT_SECONDS}",
            file=sys.stderr,
        )
        return DEFAULT_EXTRACTOR_TIMEOUT_SECONDS
    if value <= 0:
        print(
            "eod-capture: warning: K2B_EOD_EXTRACTOR_TIMEOUT_SECONDS must be positive; "
            f"using {DEFAULT_EXTRACTOR_TIMEOUT_SECONDS}",
            file=sys.stderr,
        )
        return DEFAULT_EXTRACTOR_TIMEOUT_SECONDS
    if value > MAX_EXTRACTOR_TIMEOUT_SECONDS:
        print(
            "eod-capture: warning: K2B_EOD_EXTRACTOR_TIMEOUT_SECONDS above "
            f"{MAX_EXTRACTOR_TIMEOUT_SECONDS}; using {MAX_EXTRACTOR_TIMEOUT_SECONDS}",
            file=sys.stderr,
        )
        return MAX_EXTRACTOR_TIMEOUT_SECONDS
    return value


class AllItemsRejectedError(ValueError):
    """Raised when Kimi returned items but every item failed validation."""


class ContentClassRejectionError(ValueError):
    """Marker base for per-item rejections that are content/groundability
    failures (extractor produced well-formed items but couldn't ground them).

    These are skip-eligible when ALL items in a session are rejected, because
    the cron pipeline should not halt on sessions that legitimately produce
    no extractable content. Schema/contract drift uses plain ``ValueError``.
    Subclasses are used in place of substring matching on error text, so
    extractor-controlled values echoed into error messages cannot smuggle a
    schema error into the skip path.
    """


class EvidenceQuoteNotInTranscriptError(ContentClassRejectionError):
    """Extractor invented a quote that isn't in the stripped transcript."""


class UnsupportedCanonicalHomeError(ContentClassRejectionError):
    """Extractor proposed writing the item to a destination not in the
    canonical_home allow-list."""


class SpeakerAttributionError(ContentClassRejectionError):
    """Extractor attributed evidence to a role that did not author it."""


def _normalize_predicate_value(value: object) -> str:
    text = str(value)
    if PREDICATE_WHITESPACE_RE.search(text):
        return PREDICATE_WHITESPACE_RE.sub("_", text.strip())
    return text.strip()


def _normalize_item_predicate(item: dict, *, log: bool) -> None:
    original = str(item.get("predicate", ""))
    if not PREDICATE_WHITESPACE_RE.search(original):
        item["predicate"] = original.strip()
        return
    normalized = _normalize_predicate_value(original)
    item["predicate"] = normalized
    if log:
        sys.stderr.write(
            "[shelf-writer] predicate-normalized: "
            f"{json.dumps(original, ensure_ascii=False)} -> "
            f"{json.dumps(normalized, ensure_ascii=False)}\n"
        )
        sys.stderr.flush()


def _dedupe_key_aliases(dedupe_key: str) -> set[str]:
    parts = dedupe_key.split(":")
    if len(parts) < 2:
        return {dedupe_key}
    predicate_segment = parts[-1]
    predicate_aliases = {predicate_segment}
    if "_" in predicate_segment:
        predicate_aliases.add(predicate_segment.replace("_", "-"))
    if "-" in predicate_segment:
        predicate_aliases.add(predicate_segment.replace("-", "_"))
    prefix = ":".join(parts[:-1])
    return {f"{prefix}:{predicate}" for predicate in predicate_aliases}


def _predicate_key_aliases(predicate: str) -> set[str]:
    aliases = {predicate}
    if "_" in predicate:
        aliases.add(predicate.replace("_", "-"))
    if "-" in predicate:
        aliases.add(predicate.replace("-", "_"))
    return aliases


def _truncate(text: str, *, head: int, tail: int = 0, limit: int) -> str:
    if len(text) <= limit:
        return text
    if tail:
        return f"{text[:head]}\n[truncated, {len(text)} chars]\n{text[-tail:]}"
    return f"{text[:head]}\n[truncated, {len(text)} chars]"


def _content_to_text(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                if isinstance(item.get("text"), str):
                    parts.append(item["text"])
                elif isinstance(item.get("content"), str):
                    parts.append(item["content"])
        return "\n".join(p for p in parts if p)
    if isinstance(content, dict):
        if isinstance(content.get("text"), str):
            return content["text"]
        if isinstance(content.get("content"), str):
            return content["content"]
    return ""


def _is_codex_bootstrap_text(text: str) -> bool:
    stripped = text.lstrip()
    return (
        stripped.startswith("# AGENTS.md instructions for ")
        or stripped.startswith("<environment_context>")
    )


def _event_to_text(event: dict) -> str:
    event_type = str(event.get("type", ""))
    payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
    item = payload.get("item") if isinstance(payload, dict) else None
    if not isinstance(item, dict):
        if event_type == "response_item" and isinstance(payload, dict):
            item = payload
        else:
            item = event.get("message") if isinstance(event.get("message"), dict) else event

    item_type = str(item.get("type", event_type))
    role = str(item.get("role", event.get("role", "")))

    if item_type == "message" and role in {"developer", "system"}:
        return ""

    if item_type in {"function_call_output", "tool_result"}:
        raw = item.get("output", item.get("content", ""))
        text = _content_to_text(raw)
        return "[tool_output]\n" + _truncate(text, head=100, limit=200)

    if item_type in {"function_call", "tool_use"}:
        name = item.get("name") or item.get("tool_name") or "tool"
        args = item.get("arguments") or item.get("input") or {}
        args_text = json.dumps(args, ensure_ascii=False)
        return f"[tool_call] {name} {_truncate(args_text, head=180, limit=240)}"

    text = _content_to_text(item.get("content", event.get("content", "")))
    if not text:
        text = _content_to_text(event.get("message", ""))
    if not text:
        return ""
    if role == "user" and _is_codex_bootstrap_text(text):
        return ""

    label = role or event_type or "event"
    return f"[{label}]\n{_truncate(text, head=500, limit=1000)}"


def _read_complete_jsonl_records(
    session_path: Path, *, deadline: float | None = None
) -> list[tuple[int, dict, bytes]]:
    """Read complete JSONL records, ignoring only an unterminated partial tail."""
    records: list[tuple[int, dict, bytes]] = []
    with session_path.open("rb") as handle:
        for line_no, raw_line in enumerate(handle, 1):
            if deadline is not None and time.monotonic() >= deadline:
                raise DialogueParseTimeout("bounded dialogue parse exceeded its deadline")
            if not raw_line.strip():
                continue
            try:
                line = raw_line.decode("utf-8")
                event = json.loads(line)
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                if not raw_line.endswith((b"\n", b"\r")):
                    break
                raise ValueError(
                    f"invalid JSON in transcript {session_path} line {line_no}: {exc}"
                ) from exc
            if isinstance(event, dict):
                records.append((line_no, event, raw_line))
    return records


def _message_item(event: dict) -> dict | None:
    payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
    item = payload.get("item") if isinstance(payload.get("item"), dict) else None
    if item is None:
        item = payload if event.get("type") == "response_item" else event
    item_type = str(item.get("type", event.get("type", "")))
    role = str(item.get("role", event.get("role", "")))
    if item_type not in {"message", "user", "assistant"} and role not in {
        "user",
        "assistant",
    }:
        return None
    if role not in {"user", "assistant"}:
        if item_type in {"user", "assistant"}:
            role = item_type
        else:
            return None
    text = _content_to_text(item.get("content", event.get("content", "")))
    if not text:
        text = _content_to_text(event.get("message", ""))
    if not text or (role == "user" and _is_codex_bootstrap_text(text)):
        return None
    return {"item": item, "role": role, "text": _sanitize_transport_text(text)}


def _session_provenance(meta: dict) -> tuple[str | None, str]:
    """Classify only documented metadata shapes; keep unknown origins visible."""
    worker_values = {"subagent", "automation", "agent_created_thread", "worker"}
    import_values = {"import", "imported"}
    ordinary_values = INTERACTIVE_THREAD_SOURCES
    thread_source = str(meta.get("thread_source") or "").strip().lower()
    source = meta.get("source")
    source_kind = str(source).strip().lower() if isinstance(source, str) else ""
    source_keys = (
        {str(key).strip().lower() for key in source}
        if isinstance(source, dict)
        else set()
    )
    if thread_source in worker_values or source_kind in worker_values or source_keys & worker_values:
        return "worker_origin", "known_worker"
    if thread_source in import_values or source_kind in import_values or source_keys & import_values:
        return "imported_source", "known_import"
    if meta.get("parent_thread_id") or meta.get("agent_path"):
        return "worker_origin", "known_worker"
    observed = {value for value in (thread_source, source_kind) if value}
    source_shape_is_known = source is None or isinstance(source, str)
    if source_shape_is_known and observed and observed.issubset(ordinary_values):
        return None, "known_user"
    return None, "unknown"


def _session_thread_source(meta: dict) -> str:
    """Return only a documented interactive client identity, else unknown."""

    for candidate in (meta.get("thread_source"), meta.get("source")):
        if isinstance(candidate, str):
            normalized = candidate.strip().lower()
            if normalized in INTERACTIVE_THREAD_SOURCES:
                return normalized
    return "unknown"


def _session_is_excluded(meta: dict) -> str | None:
    return _session_provenance(meta)[0]


def _stable_event_id(
    *,
    item: dict,
    event: dict,
    host_id: str,
    session_id: str,
    line_no: int,
    role: str,
    text: str,
) -> str:
    native_id = item.get("id") or event.get("id")
    if native_id:
        return f"event:{host_id.removeprefix('host:')}:{session_id}:{native_id}"
    ordinal = event.get("ordinal", line_no)
    seed = json.dumps(
        [host_id, session_id, ordinal, role, text],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return "event:" + hashlib.sha256(seed.encode("utf-8")).hexdigest()


def _bounded_dialogue_chunks(
    transcript: str, *, cursor: str, max_chunk_chars: int
) -> list[dict]:
    if max_chunk_chars <= 0:
        raise ValueError("max_chunk_chars must be positive")
    chunks: list[dict] = []
    for index, start in enumerate(range(0, len(transcript), max_chunk_chars)):
        text = transcript[start : start + max_chunk_chars]
        chunk_seed = f"{cursor}\0{index}\0{text}"
        chunks.append(
            {
                "chunk_id": "chunk:"
                + hashlib.sha256(chunk_seed.encode("utf-8")).hexdigest(),
                "index": index,
                "text": text,
            }
        )
    return chunks


def _completed_at_date(value: object) -> str:
    return _parse_event_date(value)


def _completed_at_iso(value: object) -> str:
    """Accept native epoch seconds without changing persisted source identities."""
    if type(value) in (int, float):
        try:
            return datetime.fromtimestamp(value, tz=timezone.utc).isoformat()
        except (OSError, OverflowError, ValueError) as exc:
            raise ValueError("invalid completed_at epoch seconds") from exc
    if not isinstance(value, str) or not value.strip():
        raise ValueError("invalid completed_at")
    return value.strip()


def parse_completed_dialogue(
    session_path: Path,
    *,
    source_host: str,
    max_chunk_chars: int = DEFAULT_DIALOGUE_CHUNK_CHARS,
    deadline: float | None = None,
) -> dict:
    """Parse one source once into stable, lossless completed-turn prefixes."""
    if source_host not in {"home", "sjm"}:
        raise ValueError(f"unsupported source host: {source_host}")
    records = _read_complete_jsonl_records(session_path, deadline=deadline)
    def check_deadline() -> None:
        if deadline is not None and time.monotonic() >= deadline:
            raise DialogueParseTimeout("bounded dialogue parse exceeded its deadline")

    check_deadline()
    meta: dict = {}
    for _line_no, event, _raw in records:
        if event.get("type") == "session_meta" and isinstance(event.get("payload"), dict):
            meta = event["payload"]
            break
    excluded_reason, provenance_status = _session_provenance(meta)
    host_id = f"host:{source_host}"
    native_session_id = meta.get("session_id") or meta.get("id")
    session_id = str(native_session_id or _safe_session_id(session_path))
    result = {
        "parser_schema_version": 1,
        "source_host": source_host,
        "host_id": host_id,
        "session_id": session_id,
        "eligible": excluded_reason is None,
        "exclusion_reason": excluded_reason,
        "provenance_status": provenance_status,
        "thread_source": _session_thread_source(meta),
        "explicit_turn_markers": False,
        "completed_cursor": None,
        "completed_prefix_sha256": None,
        "completed_prefixes": [],
        "parse_diagnostics": [],
    }
    if excluded_reason is not None:
        return result

    explicit_turn_markers = any(
        event.get("type") == "event_msg"
        and isinstance(event.get("payload"), dict)
        and event["payload"].get("type") in {"task_started", "task_complete"}
        for _line_no, event, _raw in records
    )
    result["explicit_turn_markers"] = explicit_turn_markers
    active_turn_id: str | None = None
    pending: list[dict] = []
    completed_events: list[dict] = []
    completed_prefixes: list[dict] = []

    def add_message(line_no: int, event: dict, turn_id: str) -> None:
        check_deadline()
        message = _message_item(event)
        if message is None:
            return
        item = message["item"]
        role = str(message["role"])
        text = str(message["text"])
        pending.append(
            {
                "event_id": _stable_event_id(
                    item=item,
                    event=event,
                    host_id=host_id,
                    session_id=session_id,
                    line_no=line_no,
                    role=role,
                    text=text,
                ),
                "turn_id": turn_id,
                "role": role,
                "text": text,
                "ordinal": event.get("ordinal", line_no),
            }
        )

    def make_prefix(
        events: list[dict], *, turn_id: str, completed_at: object, mode: str
    ) -> dict:
        check_deadline()
        transcript = "\n\n".join(
            f"[{item['role']}]\n{item['text']}" for item in events
        ).strip()
        prefix_sha256 = hashlib.sha256(transcript.encode("utf-8")).hexdigest()
        if mode == "legacy_whole_source":
            cursor_parts: list[object] = [
                host_id, session_id, [item["event_id"] for item in events], prefix_sha256
            ]
        else:
            cursor_parts = [
                host_id, session_id, turn_id,
                [item["event_id"] for item in events], prefix_sha256,
            ]
        cursor_seed = json.dumps(
            cursor_parts, ensure_ascii=False, separators=(",", ":")
        )
        cursor = "cursor:" + hashlib.sha256(cursor_seed.encode("utf-8")).hexdigest()
        completed_date = (
            _session_content_date(session_path)
            if mode == "legacy_whole_source"
            else _completed_at_date(completed_at)
        )
        return {
            "turn_id": turn_id,
            "completed_at": completed_at,
            "completed_date": completed_date,
            "mode": mode,
            "cursor": cursor,
            "prefix_sha256": prefix_sha256,
            "events": [dict(item) for item in events],
            "transcript": transcript,
            "chunks": _bounded_dialogue_chunks(
                transcript, cursor=cursor, max_chunk_chars=max_chunk_chars
            ),
        }

    if explicit_turn_markers:
        first_marker_index = next(
            index
            for index, (_line_no, event, _raw) in enumerate(records)
            if event.get("type") == "event_msg"
            and isinstance(event.get("payload"), dict)
            and event["payload"].get("type") in {"task_started", "task_complete"}
        )
        for line_no, event, _raw in records[:first_marker_index]:
            add_message(line_no, event, "legacy")
        if pending:
            legacy_events = [dict(item) for item in pending]
            legacy_prefix = make_prefix(
                legacy_events,
                turn_id="legacy",
                completed_at=None,
                mode="legacy_whole_source",
            )
            completed_prefixes.append(legacy_prefix)
            completed_events.extend(legacy_events)
        pending = []
        for line_no, event, _raw in records[first_marker_index:]:
            check_deadline()
            payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
            event_kind = payload.get("type") if event.get("type") == "event_msg" else None
            if event_kind == "task_started":
                active_turn_id = str(payload.get("turn_id") or f"turn-line-{line_no}")
                pending = []
                continue
            if event_kind == "task_complete":
                completion_turn_id = str(payload.get("turn_id") or active_turn_id or "")
                if active_turn_id and completion_turn_id == active_turn_id:
                    completed_events.extend(pending)
                    completed_at = payload.get("completed_at") or event.get("timestamp")
                    prefix = make_prefix(
                        completed_events,
                        turn_id=completion_turn_id,
                        completed_at=completed_at,
                        mode="turn",
                    )
                    completed_prefixes.append(prefix)
                elif active_turn_id:
                    completed_at = payload.get("completed_at") or event.get("timestamp")
                    result["parse_diagnostics"].append(
                        {
                            "reason": "task_complete_turn_id_mismatch",
                            "line_no": line_no,
                            "active_turn_id": active_turn_id,
                            "completion_turn_id": completion_turn_id,
                            "completed_date": _completed_at_date(completed_at),
                        }
                    )
                active_turn_id = None
                pending = []
                continue
            if active_turn_id is not None:
                add_message(line_no, event, active_turn_id)
    else:
        for line_no, event, _raw in records:
            add_message(line_no, event, "legacy")
        if pending:
            completed_prefixes.append(
                make_prefix(
                    pending,
                    turn_id="legacy",
                    completed_at=None,
                    mode="legacy_whole_source",
                )
            )

    check_deadline()
    result["completed_prefixes"] = completed_prefixes
    if completed_prefixes:
        result["completed_cursor"] = completed_prefixes[-1]["cursor"]
        result["completed_prefix_sha256"] = completed_prefixes[-1]["prefix_sha256"]
    return result


def _select_completed_prefix(
    parsed: dict, *, run_date: str | None = None, completed_cursor: str | None = None
) -> dict | None:
    prefixes = parsed.get("completed_prefixes") or []
    if not prefixes:
        return None
    if completed_cursor:
        candidates = [
            prefix for prefix in prefixes if prefix.get("cursor") == completed_cursor
        ]
        if run_date:
            candidates = [
                prefix for prefix in candidates if prefix.get("completed_date") == run_date
            ]
        return candidates[-1] if candidates else None
    if run_date and parsed.get("explicit_turn_markers"):
        dated = [prefix for prefix in prefixes if prefix.get("completed_date") == run_date]
        return dated[-1] if dated else None
    return prefixes[-1]


def strip_transcript(session_path: Path) -> str:
    """Return the latest complete, redacted user/assistant dialogue prefix."""
    parsed = parse_completed_dialogue(session_path, source_host="home")
    prefix = _select_completed_prefix(parsed)
    return str(prefix.get("transcript", "")) if prefix else ""


def strip_dialogue_for_transport(session_path: Path) -> str:
    """Return the same completed dialogue used by local extraction."""
    return strip_transcript(session_path)


def _sanitize_transport_text(value: object) -> str:
    text = str(value)
    text = CREDENTIAL_URL_RE.sub(r"\1[REDACTED]@", text)
    text = SECRET_ASSIGNMENT_RE.sub(r"\1[REDACTED]", text)
    text = SECRET_OPTION_RE.sub(r"\1[REDACTED]", text)
    text = AUTH_VALUE_RE.sub(r"\1[REDACTED]", text)
    return SECRET_TOKEN_RE.sub("[REDACTED]", text)


def _sanitize_durable_value(value: object) -> object:
    """Recursively redact strings before persisting diagnostic artifacts."""
    if isinstance(value, str):
        return _sanitize_transport_text(value)
    if isinstance(value, list):
        return [_sanitize_durable_value(item) for item in value]
    if isinstance(value, tuple):
        return [_sanitize_durable_value(item) for item in value]
    if isinstance(value, dict):
        return {
            str(_sanitize_transport_text(key)): _sanitize_durable_value(item)
            for key, item in value.items()
        }
    return value


def _source_changed_since(
    path: Path, source_stat: os.stat_result, raw_source_sha256: str
) -> bool:
    final_stat = path.stat()
    return bool(
        final_stat.st_size != source_stat.st_size
        or final_stat.st_mtime_ns != source_stat.st_mtime_ns
        or _sha256_file(path) != raw_source_sha256
    )


def build_source_bundle(
    session_path: Path,
    *,
    source_host: str,
    codex_root: Path | None = None,
    run_date: str | None = None,
    completed_cursor: str | None = None,
) -> dict:
    """Create a provenance-bound, redacted bundle for an interactive handoff."""
    if source_host not in {"home", "sjm"}:
        raise ValueError(f"unsupported source host: {source_host}")
    root = _resolve_codex_root(codex_root)
    resolved = session_path.resolve()
    if not resolved.is_relative_to(root) or detect_source_app(resolved, codex_root=root) == "unknown":
        raise ValueError("source is outside the configured Codex session root")
    if not _is_k2b_scope(resolved):
        raise ValueError("source is outside exact K2B/K2Bi scope")
    source_stat = resolved.stat()
    raw_source_sha256 = _sha256_file(resolved)
    parsed = parse_completed_dialogue(resolved, source_host=source_host)
    if not parsed.get("eligible"):
        raise ValueError(f"excluded source session: {parsed.get('exclusion_reason')}")
    prefix = _select_completed_prefix(
        parsed, run_date=run_date, completed_cursor=completed_cursor
    )
    transcript = str(prefix.get("transcript", "")) if prefix else ""
    if not transcript:
        raise ValueError("source has no eligible user/assistant dialogue")
    if _source_changed_since(resolved, source_stat, raw_source_sha256):
        raise ValueError("source changed while the handoff bundle was created")
    run_date = str(prefix.get("completed_date") or "") if prefix else ""
    run_date = run_date or _session_content_date(resolved) or _mtime_date(resolved)
    return {
        "bundle_schema_version": 1,
        "source_host": source_host,
        "source_app": "codex_desktop",
        "session_path": str(resolved),
        "run_date": run_date,
        "raw_source_sha256": raw_source_sha256,
        "transcript": transcript,
        "transcript_sha256": hashlib.sha256(transcript.encode("utf-8")).hexdigest(),
        "host_id": parsed["host_id"],
        "session_id": parsed["session_id"],
        "completed_cursor": prefix["cursor"],
        "completed_prefix_sha256": prefix["prefix_sha256"],
        "completed_at": prefix["completed_at"],
        "completed_date": prefix["completed_date"],
        "completed_prefix_mode": prefix.get("mode", "legacy_whole_source"),
        "completed_turn_id": prefix["turn_id"],
        "provenance_status": parsed["provenance_status"],
        "thread_source": parsed["thread_source"],
        "dialogue_events": prefix["events"],
        "chunks": prefix["chunks"],
        "exported_at": datetime.now(timezone.utc).isoformat(timespec="microseconds"),
    }


def _resolve_codex_root(codex_root: Path | None = None) -> Path:
    """Resolve the Codex root only from explicit arg, env, or default."""
    if codex_root is None:
        codex_root = Path(
            os.environ.get("K2B_CODEX_SESSIONS_ROOT", "~/.codex/sessions")
        ).expanduser()
    return codex_root.resolve()


def detect_source_app(session_path: Path, *, codex_root: Path | None = None) -> str:
    """Return the canonical source app for a K2B capture session.

    Task 3B makes Codex Desktop the only supported source. Paths are accepted
    only when they resolve inside the configured Codex root; everything else is
    reported as unknown so callers can fail closed.
    """
    try:
        resolved = session_path.resolve()
        root = _resolve_codex_root(codex_root)
    except OSError:
        return "unknown"
    if str(resolved).startswith(str(root) + os.sep):
        return "codex_desktop"
    return "unknown"


def _safe_session_id(session_path: Path) -> str:
    stem = session_path.stem
    digest = hashlib.sha256(str(session_path).encode("utf-8")).hexdigest()[:16]
    if stem.startswith("rollout-"):
        parts = stem.split("-")
        if parts:
            return f"{parts[-1]}_{digest}"
    return re.sub(r"[^A-Za-z0-9_.-]", "_", stem)[:80] + "_" + digest


@contextmanager
def _file_lock(path: Path):
    """Lock a stable, persistent inode for one capture artifact.

    The empty lock files intentionally remain after release. Unlinking them can
    split simultaneous waiters across different inodes and defeat flock; their
    count is bounded by the number of captured sessions and their size is zero.
    """
    timeout = float(os.environ.get("K2B_EOD_LOCK_TIMEOUT_SECONDS", "30"))
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+", encoding="utf-8") as f:
        start = time.monotonic()
        while True:
            try:
                fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as exc:
                if exc.errno not in (errno.EACCES, errno.EAGAIN):
                    raise
                if time.monotonic() - start >= timeout:
                    raise TimeoutError(f"could not acquire lock {path} after {timeout:g}s")
                time.sleep(0.05)
        try:
            yield
        finally:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)


@contextmanager
def _shell_compatible_lock(path: Path):
    """Mirror shelf-writer's flock-if-available, mkdir-fallback lock behavior."""
    if shutil.which("flock"):
        with _file_lock(path):
            yield
        return
    lock_dir = Path(f"{path}.d")
    tries = 0
    while True:
        try:
            lock_dir.mkdir(parents=True)
            break
        except FileExistsError:
            tries += 1
            if tries > 600:
                raise TimeoutError(f"could not acquire lock {lock_dir} after 30s")
            time.sleep(0.05)
    try:
        yield
    finally:
        try:
            lock_dir.rmdir()
        except OSError:
            pass


def _shelf_lock_path(shelf: str) -> Path:
    lock_root = Path(os.environ.get("K2B_SHELF_LOCK_DIR", "/tmp"))
    return lock_root / f"k2b-shelf-{shelf}.lock"


def _fsync_dir(path: Path) -> None:
    try:
        fd = os.open(str(path), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _event_cwd(event: dict) -> str:
    cwd = event.get("cwd")
    if isinstance(cwd, str) and cwd:
        return cwd
    payload = event.get("payload")
    if isinstance(payload, dict):
        payload_cwd = payload.get("cwd")
        if isinstance(payload_cwd, str) and payload_cwd:
            return payload_cwd
    return ""


def session_cwd(session_path: Path, *, max_lines: int = 80) -> str:
    """Return the first cwd recorded in a Claude Code / Codex JSONL session."""
    try:
        with session_path.open(encoding="utf-8") as f:
            for line_no, line in enumerate(f, 1):
                if line_no > max_lines:
                    break
                if not line.strip():
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(event, dict):
                    cwd = _event_cwd(event)
                    if cwd:
                        return cwd
    except OSError:
        return ""
    return ""


def _path_implies_project_scope(path: Path) -> bool:
    s = str(path)
    return bool(PROJECT_SCOPE_RE.search(s) or ENCODED_PROJECT_SCOPE_RE.search(s))


def _is_k2b_scope(session_path: Path) -> bool:
    cwd = session_cwd(session_path)
    if cwd:
        return bool(PROJECT_SCOPE_RE.search(cwd))
    return _path_implies_project_scope(session_path)


def _mtime_date(path: Path) -> str:
    # Bucket by HKT calendar day, not the host's local TZ. A file mtime carries
    # an absolute UTC epoch; the right question is "which HKT day did this
    # event belong to," which is `astimezone(HKT).date()`.
    return datetime.fromtimestamp(path.stat().st_mtime, tz=HKT).date().isoformat()


def _parse_event_date(value: object) -> str:
    # All session-event dates bucket by HKT calendar day. Sessions whose
    # timestamps are early-morning HKT (00:00-07:59) carry UTC dates from the
    # previous calendar day, so naive `.date()` would silently exclude them
    # from the intended HKT run_date filter. Convert to HKT before bucketing.
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value, tz=HKT).date().isoformat()
        except (OSError, OverflowError, ValueError):
            return ""
    if not isinstance(value, str) or not value.strip():
        return ""
    text = value.strip()
    if re.match(r"^\d{4}-\d{2}-\d{2}$", text):
        # Already a date-only string. By convention these are HKT-bucketed
        # already (per context_timezone-convention.md Rule 1). Pass through.
        return text
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return ""
    if dt.tzinfo is None:
        # Naive timestamps are FORBIDDEN by Rule 4 but we degrade gracefully:
        # treat as UTC (matches how Python normally interprets bare ISO).
        # Emit a stderr audit so a producer leaking naive timestamps is visible
        # instead of silently UTC-coerced. Rate-limited to one warning per
        # unique format shape per process to avoid stderr flooding.
        format_key = NAIVE_TIMESTAMP_DIGIT_RE.sub("D", text)
        if format_key not in _NAIVE_TIMESTAMP_WARNED_FORMATS:
            _NAIVE_TIMESTAMP_WARNED_FORMATS.add(format_key)
            print(
                f"eod-capture: warning: naive timestamp treated as UTC "
                f"(convention Rule 4 violation): {text}",
                file=sys.stderr,
            )
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(HKT).date().isoformat()


def _event_date(event: dict) -> str:
    for key in ("timestamp", "created_at", "time", "ts"):
        parsed = _parse_event_date(event.get(key))
        if parsed:
            return parsed
    payload = event.get("payload")
    if isinstance(payload, dict):
        for key in ("timestamp", "created_at", "time", "ts"):
            parsed = _parse_event_date(payload.get(key))
            if parsed:
                return parsed
    return ""


def _session_content_date(session_path: Path, *, max_lines: int = 200) -> str:
    try:
        with session_path.open(encoding="utf-8") as f:
            for line_no, line in enumerate(f, 1):
                if line_no > max_lines:
                    break
                if not line.strip():
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(event, dict):
                    parsed = _event_date(event)
                    if parsed:
                        return parsed
    except OSError:
        return ""
    return ""


def _session_relevant_dates(session_path: Path) -> set[str]:
    """Return completion dates for modern sessions, or the legacy source date."""
    parsed = parse_completed_dialogue(session_path, source_host="home")
    if not parsed.get("eligible"):
        return set()
    if parsed.get("explicit_turn_markers"):
        dates = {
            str(prefix["completed_date"])
            for prefix in parsed.get("completed_prefixes", [])
            if prefix.get("completed_date")
        }
        dates.update(
            str(diagnostic["completed_date"])
            for diagnostic in parsed.get("parse_diagnostics", [])
            if diagnostic.get("completed_date")
        )
        return dates
    legacy_date = _session_content_date(session_path)
    if not legacy_date:
        parts = session_path.parts
        for index in range(max(len(parts) - 2, 0)):
            candidate = "-".join(parts[index : index + 3])
            try:
                legacy_date = date.fromisoformat(candidate).isoformat()
                break
            except ValueError:
                continue
    legacy_date = legacy_date or _mtime_date(session_path)
    return {legacy_date} if legacy_date else set()


def _fallback_session_dates(session_path: Path) -> set[str]:
    """Keep both the first valid event day and a malformed tail's mtime day."""
    dates = {_session_content_date(session_path)}
    try:
        dates.add(_mtime_date(session_path))
    except OSError:
        pass
    return {value for value in dates if value}


def discover_session_paths(
    *,
    run_date: str,
    codex_root: Path | None = None,
) -> list[Path]:
    """Discover today's K2B/K2Bi Codex Desktop sessions only."""
    codex_root = _resolve_codex_root(codex_root)

    found: list[Path] = []
    # The directory is a UTC creation date, not necessarily the HKT source day.
    codex_candidates = sorted(codex_root.rglob("*.jsonl"))
    for path in codex_candidates:
        resolved = path.resolve()
        if not resolved.is_relative_to(codex_root) or not _is_k2b_scope(resolved):
            continue
        try:
            session_dates = _session_relevant_dates(resolved)
        except OSError:
            continue
        except ValueError:
            session_dates = _fallback_session_dates(resolved)
        if run_date not in session_dates:
            continue
        found.append(resolved)

    return found


def _date_range(since: str, through: str) -> list[str]:
    try:
        start = date.fromisoformat(since)
        end = date.fromisoformat(through)
    except ValueError as exc:
        raise ValueError("capture date range must use real YYYY-MM-DD dates") from exc
    if start > end:
        raise ValueError(f"capture date range is reversed: {since} > {through}")
    if (end - start).days > 3660:
        raise ValueError("capture date range exceeds the 10-year safety bound")
    dates: list[str] = []
    current = start
    while current <= end:
        dates.append(current.isoformat())
        current += timedelta(days=1)
    return dates


def discover_session_paths_between(
    *,
    since: str,
    through: str,
    codex_root: Path | None = None,
) -> dict[str, list[Path]]:
    """Discover every in-scope Codex session in an inclusive date range."""
    root = _resolve_codex_root(codex_root)
    result = {run_date: [] for run_date in _date_range(since, through)}
    for path in sorted(root.rglob("*.jsonl")):
        resolved = path.resolve()
        if not resolved.is_relative_to(root) or not _is_k2b_scope(resolved):
            continue
        try:
            run_dates = _session_relevant_dates(resolved)
        except OSError:
            continue
        except ValueError:
            run_dates = _fallback_session_dates(resolved)
        for run_date in sorted(run_dates):
            if run_date in result:
                result[run_date].append(resolved)
    return result


def _read_text_limited(path: Path, max_bytes: int) -> tuple[str, int]:
    size = path.stat().st_size
    if size > max_bytes:
        with path.open("rb") as f:
            head = f.read(max_bytes // 2)
            f.seek(max(size - (max_bytes // 2), 0))
            tail = f.read(max_bytes // 2)
        text = (
            head.decode("utf-8", errors="replace")
            + f"\n[truncated, {size} bytes]\n"
            + tail.decode("utf-8", errors="replace")
        )
        return text, size
    return path.read_text(encoding="utf-8", errors="replace"), size


def _run_extractor_process(cmd: list[str], *, timeout: int):
    with tempfile.TemporaryDirectory(prefix="eod-extractor-") as tmp_dir:
        stdout_path = Path(tmp_dir) / "stdout.txt"
        stderr_path = Path(tmp_dir) / "stderr.txt"
        with stdout_path.open("wb") as stdout_f, stderr_path.open("wb") as stderr_f:
            proc = subprocess.run(
                cmd,
                stdout=stdout_f,
                stderr=stderr_f,
                text=False,
                check=False,
                timeout=timeout,
            )

        # Tests monkeypatch subprocess.run and return populated stdout/stderr
        # without writing to the file handles above.
        raw_stdout = getattr(proc, "stdout", None)
        if raw_stdout is not None:
            if isinstance(raw_stdout, bytes):
                stdout = raw_stdout.decode("utf-8", errors="replace")
                stdout_size = len(raw_stdout)
            else:
                stdout = str(raw_stdout)
                stdout_size = len(stdout)
        else:
            stdout_size = stdout_path.stat().st_size
            stdout = (
                ""
                if stdout_size > MAX_EXTRACTOR_STDOUT_BYTES
                else stdout_path.read_text(encoding="utf-8", errors="replace")
            )

        raw_stderr = getattr(proc, "stderr", None)
        if raw_stderr is not None:
            stderr = (
                raw_stderr.decode("utf-8", errors="replace")
                if isinstance(raw_stderr, bytes)
                else str(raw_stderr)
            )
        else:
            stderr, _stderr_size = _read_text_limited(
                stderr_path, MAX_EXTRACTOR_STDERR_BYTES
            )

        return SimpleNamespace(
            args=getattr(proc, "args", cmd),
            returncode=proc.returncode,
            stdout=stdout,
            stderr=stderr,
            stdout_size=stdout_size,
        )


def call_kimi_extractor(
    payload: str,
    session_path: Path,
    *,
    vault_path: Path | None = None,
    run_date: str | None = None,
    transcript_sha256: str | None = None,
    raw_source_sha256: str | None = None,
    codex_root: Path | None = None,
    completed_cursor: str | None = None,
) -> dict | None:
    """Call existing synchronous Kimi wrapper through minimax-json-job.sh."""
    if not MINIMAX_JSON_JOB.is_file():
        raise RuntimeError(f"missing JSON job wrapper: {MINIMAX_JSON_JOB}")
    if not PROMPT_PATH.is_file():
        raise RuntimeError(f"missing extractor prompt: {PROMPT_PATH}")
    last_error: RuntimeError | None = None
    for attempt in range(3):
        try:
            with tempfile.TemporaryDirectory(prefix="eod-extract-") as tmp_dir:
                tmp = Path(tmp_dir) / "payload.txt"
                tmp.write_text(payload, encoding="utf-8")
                proc = _run_extractor_process(
                    [
                        str(MINIMAX_JSON_JOB),
                        "--prompt",
                        str(PROMPT_PATH),
                        "--input",
                        str(tmp),
                        "--job-name",
                        "eod-capture",
                        "--prompt-version",
                        "v1",
                        "--role-name",
                        "session-transcript",
                        "--max-tokens",
                        "4000",
                    ],
                    timeout=_extractor_timeout_seconds(),
                )
        except subprocess.TimeoutExpired as exc:
            if vault_path is None or run_date is None:
                raise RuntimeError(
                    f"eod extractor timed out for {session_path}: {exc}"
                ) from exc
            _write_extraction_skip(
                vault_path,
                session_path,
                run_date=run_date,
                reason="slow_extraction",
                error=exc,
                transcript_sha256=transcript_sha256,
                raw_source_sha256=raw_source_sha256,
                codex_root=codex_root,
                completed_cursor=completed_cursor,
            )
            print(
                f"eod-capture: slow extraction skipped for {session_path}: {exc}",
                file=sys.stderr,
            )
            return None
        except OSError as exc:
            last_error = RuntimeError(f"eod extractor failed for {session_path}: {exc}")
            if attempt < 2:
                time.sleep(_extractor_retry_delay(attempt))
                continue
            raise last_error from exc
        if proc.returncode == 0:
            if not proc.stdout.strip():
                stderr_hint = _sanitize_log_value(proc.stderr.strip())
                detail = f"; stderr: {stderr_hint}" if stderr_hint else ""
                last_error = RuntimeError(
                    f"empty extractor JSON for {session_path}{detail}"
                )
                if attempt < 2:
                    time.sleep(_extractor_retry_delay(attempt))
                    continue
                raise last_error
            if proc.stdout_size > MAX_EXTRACTOR_STDOUT_BYTES:
                raise RuntimeError(
                    f"extractor JSON too large for {session_path}: "
                    f"{proc.stdout_size} bytes"
                )
            try:
                data = json.loads(proc.stdout)
            except json.JSONDecodeError as exc:
                last_error = RuntimeError(f"invalid extractor JSON for {session_path}: {exc}")
                if attempt < 2:
                    time.sleep(_extractor_retry_delay(attempt))
                    continue
                raise last_error from exc
            return validate_extraction_shape(data, session_path)
        last_error = RuntimeError(
            f"eod extractor failed for {session_path}: "
            f"{_sanitize_log_value(proc.stderr.strip())}"
        )
        stderr = proc.stderr.strip().lower()
        transient_patterns = (
            r"\b429\b",
            r"\b5\d\d\b",
            r"rate\s*limit",
            r"too many requests",
            r"timeout",
            r"temporar(?:y|ily)",
            r"connection",
            r"remote disconnected",
            r"try again",
        )
        permanent_patterns = (
            r"\b401\b",
            r"\b403\b",
            r"api key",
            r"\bauth(?:orization|entication)?\b",
            r"forbidden",
            r"invalid key",
            r"permission",
            r"unauthorized",
            r"bad request",
            r"context length",
            r"invalid request",
            r"model not found",
            r"too many tokens",
        )
        if any(re.search(pattern, stderr) for pattern in transient_patterns):
            if attempt < 2:
                time.sleep(_extractor_retry_delay(attempt, rate_limited="429" in stderr))
                continue
            raise last_error
        if any(re.search(pattern, stderr) for pattern in permanent_patterns):
            raise last_error
        # Non-permanent non-zero exits retry by default. The wrapper can surface
        # novel transient network/API wording, and permanent markers above are
        # the only cases worth failing fast.
        if attempt < 2:
            time.sleep(_extractor_retry_delay(attempt))
            continue
        raise last_error
    raise last_error or RuntimeError(f"eod extractor failed for {session_path}")


def _extractor_retry_delay(attempt: int, *, rate_limited: bool = False) -> float:
    base = 5 * (2 ** attempt) if rate_limited else 2 ** attempt
    return min(base, 30) + random.uniform(0, 1)


def validate_extraction_shape(data: object, session_path: Path) -> dict:
    if not isinstance(data, dict):
        raise ValueError(f"extractor output for {session_path} is not an object")
    items = data.get("items", [])
    if not isinstance(items, list):
        raise ValueError(f"extractor output for {session_path} has non-list items")
    data["items"] = items
    return data


def validate_extraction(data: object, session_path: Path) -> dict:
    data = validate_extraction_shape(data, session_path)
    items = data.get("items", [])
    for idx, item in enumerate(items):
        _validate_extraction_item(item, idx=idx, source=session_path)
    return data


def _normalize_evidence_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _validate_evidence_quote_controls(quote: str, *, idx: int) -> None:
    if EVIDENCE_QUOTE_UNSUPPORTED_CONTROL_RE.search(quote):
        raise ValueError(
            f"extractor item {idx} has unsupported control character in evidence_quote"
        )


def verify_evidence_quotes(data: dict, payload: str, *, session_path: Path) -> None:
    normalized_payload = _normalize_evidence_text(payload)
    for idx, item in enumerate(data.get("items", [])):
        if not isinstance(item, dict):
            continue
        _validate_item_evidence_quote(
            item,
            idx=idx,
            normalized_payload=normalized_payload,
            session_path=session_path,
        )


def _validate_item_evidence_quote(
    item: dict,
    *,
    idx: int,
    normalized_payload: str,
    session_path: Path,
) -> None:
    # Schema-class presence/non-empty/type guard runs first so missing,
    # empty, or non-string evidence_quote cannot fall through to the
    # content-class checks below and end up masking extractor drift. Plain
    # str() coercion of a dict or list would otherwise produce a stringified
    # repr that then fails grounding and raises content-class.
    if "evidence_quote" not in item:
        raise ValueError(f"extractor item {idx} missing evidence_quote")
    raw_quote = item.get("evidence_quote")
    if not isinstance(raw_quote, str):
        raise ValueError(
            f"extractor item {idx} has non-string evidence_quote: "
            f"{type(raw_quote).__name__}"
        )
    quote = raw_quote.strip()
    if not quote:
        raise ValueError(f"extractor item {idx} has empty evidence_quote")
    _validate_evidence_quote_controls(quote, idx=idx)
    normalized_quote = _normalize_evidence_text(quote)
    if normalized_quote and len(normalized_quote) < MIN_EVIDENCE_QUOTE_CHARS:
        raise ValueError(
            f"evidence_quote for item {idx} in {session_path} is too short"
        )
    if normalized_quote and normalized_quote not in normalized_payload:
        raise EvidenceQuoteNotInTranscriptError(
            f"evidence_quote for item {idx} in {session_path} is not present "
            "in stripped transcript"
        )


def _validate_speaker_attribution(
    item: dict,
    payload: str,
    *,
    idx: int,
    dialogue_events: list[dict] | None = None,
    require_structured_attribution: bool = False,
) -> None:
    speaker_source = str(item.get("speaker_source") or "").strip()
    quote = str(item.get("evidence_quote") or "").strip()
    expected_role = {"keith": "user", "assistant_confirmed": "assistant"}.get(
        speaker_source
    )
    evidence_event_id = str(item.get("evidence_event_id") or "").strip()
    if require_structured_attribution and (not expected_role or not evidence_event_id):
        raise SpeakerAttributionError(
            f"extractor item {idx} lacks structured speaker evidence"
        )
    if (
        str(item.get("kind") or "").lower()
        in {"decision", "preference", "commitment"}
        and speaker_source == "assistant_confirmed"
    ):
        raise SpeakerAttributionError(
            f"extractor item {idx} requires user-authored acceptance evidence"
        )
    if not expected_role or not quote or not evidence_event_id or not dialogue_events:
        return
    event = next(
        (
            candidate
            for candidate in dialogue_events
            if isinstance(candidate, dict)
            and str(candidate.get("event_id") or "") == evidence_event_id
        ),
        None,
    )
    if event is None:
        raise SpeakerAttributionError(
            f"extractor item {idx} cites an unknown evidence event"
        )
    role = str(event.get("role") or "")
    if role != expected_role:
        raise SpeakerAttributionError(
            f"extractor item {idx} labels {speaker_source} evidence authored by "
            f"{role or 'unknown'}"
        )
    if _normalize_evidence_text(quote) not in _normalize_evidence_text(
        str(event.get("text") or "")
    ):
        raise SpeakerAttributionError(
            f"extractor item {idx} evidence crosses or falls outside its cited event"
        )
    if (
        str(item.get("kind") or "").lower()
        in {"decision", "preference", "commitment"}
        and role != "user"
    ):
        raise SpeakerAttributionError(
            f"extractor item {idx} requires user-authored acceptance evidence"
        )


def _bind_unambiguous_evidence_events(
    data: dict, dialogue_events: list[dict] | None
) -> None:
    """Bind a quote to one structured same-role event when identity was omitted."""
    if not dialogue_events:
        return
    for item in data.get("items", []):
        if not isinstance(item, dict) or item.get("evidence_event_id"):
            continue
        speaker = str(item.get("speaker_source") or "").strip()
        role = {"keith": "user", "assistant_confirmed": "assistant"}.get(speaker)
        quote = _normalize_evidence_text(str(item.get("evidence_quote") or ""))
        if not role or not quote:
            continue
        matches = [
            event
            for event in dialogue_events
            if isinstance(event, dict)
            and event.get("role") == role
            and quote in _normalize_evidence_text(str(event.get("text") or ""))
        ]
        if len(matches) == 1:
            item["evidence_event_id"] = matches[0].get("event_id")


def _legacy_evidence_slice_id(source_event_id: str, role: str, quote: str) -> str:
    seed = json.dumps(
        [source_event_id, role, quote],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return "evidence:" + hashlib.sha256(seed.encode("utf-8")).hexdigest()


def _validate_legacy_evidence_slices(
    data: dict,
    *,
    label: str,
    source_events: list[dict] | None = None,
) -> bool:
    """Validate generated quote slices, preserving legacy full-event artifacts."""
    if "dialogue_events" not in data:
        return False
    events = data.get("dialogue_events")
    if not isinstance(events, list):
        raise ValueError(f"{label} has invalid legacy dialogue_events container")
    if not events:
        return False
    for event in events:
        if not isinstance(event, dict):
            raise ValueError(f"{label} has invalid legacy dialogue event mapping")
        for field in ("event_id", "role", "text"):
            if not isinstance(event.get(field), str):
                raise ValueError(
                    f"{label} has invalid legacy full event {field} type"
                )
    slice_flags = [
        event["event_id"].startswith("evidence:") or "source_event_id" in event
        for event in events
    ]
    uses_slices = any(slice_flags)
    if not uses_slices:
        for event in events:
            if not event["event_id"].startswith("event:"):
                raise ValueError(f"{label} has invalid legacy full event id")
            if event["role"] not in {"user", "assistant"}:
                raise ValueError(f"{label} has invalid legacy full event role")
            if not event["text"].strip():
                raise ValueError(f"{label} has invalid legacy full event text")
        return False
    if not all(slice_flags):
        raise ValueError(f"{label} mixes legacy full events and evidence slices")
    source_by_id = {
        str(event.get("event_id") or ""): event
        for event in (source_events or [])
        if isinstance(event, dict)
    }
    allowed_fields = {"event_id", "source_event_id", "role", "text"}
    seen_slice_ids: set[str] = set()
    for event in events:
        if not isinstance(event, dict) or set(event) != allowed_fields:
            raise ValueError(f"{label} has invalid legacy evidence slice shape")
        slice_id = event.get("event_id")
        source_event_id = event.get("source_event_id")
        role = event.get("role")
        quote = event.get("text")
        if (
            not isinstance(slice_id, str)
            or not re.fullmatch(r"evidence:[0-9a-f]{64}", slice_id)
        ):
            raise ValueError(f"{label} has invalid legacy evidence slice id")
        if slice_id in seen_slice_ids:
            raise ValueError(f"{label} has duplicate legacy evidence slice id")
        seen_slice_ids.add(slice_id)
        if (
            not isinstance(source_event_id, str)
            or not source_event_id.startswith("event:")
            or not source_event_id.removeprefix("event:")
        ):
            raise ValueError(f"{label} has invalid legacy source event id")
        if not isinstance(role, str) or role not in {"user", "assistant"}:
            raise ValueError(f"{label} has invalid legacy evidence slice role")
        if not isinstance(quote, str) or not quote.strip():
            raise ValueError(f"{label} has invalid legacy evidence slice text")
        expected_id = _legacy_evidence_slice_id(source_event_id, role, quote)
        if slice_id != expected_id:
            raise ValueError(f"{label} legacy evidence slice identity does not match")
        if source_events is not None:
            source_event = source_by_id.get(source_event_id)
            if source_event is None:
                raise ValueError(f"{label} cites an unknown legacy source event")
            if source_event.get("role") != role:
                raise ValueError(f"{label} legacy evidence slice role does not match")
            if _normalize_evidence_text(quote) not in _normalize_evidence_text(
                str(source_event.get("text") or "")
            ):
                raise ValueError(f"{label} legacy evidence slice text does not match")
    cited_ids = {
        str(item.get("evidence_event_id") or "")
        for item in data.get("items", [])
        if isinstance(item, dict)
    }
    if not cited_ids.issubset(seen_slice_ids):
        raise ValueError(f"{label} item cites an unknown legacy evidence slice")
    return True


def _filter_extraction_items(
    data: dict,
    payload: str,
    session_path: Path,
    *,
    dialogue_events: list[dict] | None = None,
    require_structured_attribution: bool = False,
) -> tuple[dict, list[dict]]:
    shaped = validate_extraction_shape(data, session_path)
    _bind_unambiguous_evidence_events(shaped, dialogue_events)
    normalized_payload = _normalize_evidence_text(payload)
    valid_items: list[dict] = []
    rejections: list[dict] = []
    for idx, item in enumerate(shaped.get("items", [])):
        try:
            if not isinstance(item, dict):
                raise ValueError(
                    f"extractor item {idx} for {session_path} is not an object"
                )
            # All schema/contract checks run first across BOTH validators
            # (canonical_home content-class check is deferred). Then evidence
            # quote schema (control char, too short) + grounding. Then
            # canonical_home routing runs last so any earlier schema drift
            # raises ValueError (rejection_class=schema) before the
            # content-class UnsupportedCanonicalHomeError can fire.
            _validate_extraction_item(
                item,
                idx=idx,
                source=session_path,
                defer_content_class_checks=True,
            )
            _validate_item_evidence_quote(
                item,
                idx=idx,
                normalized_payload=normalized_payload,
                session_path=session_path,
            )
            _validate_speaker_attribution(
                item,
                payload,
                idx=idx,
                dialogue_events=dialogue_events,
                require_structured_attribution=require_structured_attribution,
            )
            _validate_canonical_home_routing(item, idx=idx, source=session_path)
        except ValueError as exc:
            rejection_class = (
                "content" if isinstance(exc, ContentClassRejectionError) else "schema"
            )
            rejections.append(
                {
                    "item_index": idx,
                    "error": f"{type(exc).__name__}: {exc}",
                    "rejection_class": rejection_class,
                    "item": item,
                }
            )
            continue
        valid_items.append(item)
    filtered = dict(shaped)
    filtered["items"] = valid_items
    if require_structured_attribution:
        filtered["dialogue_events"] = [
            dict(event) for event in (dialogue_events or []) if isinstance(event, dict)
        ]
    elif dialogue_events is not None:
        by_id = {
            str(event.get("event_id") or ""): event
            for event in dialogue_events
            if isinstance(event, dict)
        }
        evidence_slices: list[dict] = []
        seen_slice_ids: set[str] = set()
        for item in valid_items:
            source_event_id = str(item.get("evidence_event_id") or "")
            source_event = by_id.get(source_event_id)
            if source_event is None:
                continue
            quote = str(item.get("evidence_quote") or "")
            role = str(source_event.get("role") or "")
            slice_id = _legacy_evidence_slice_id(source_event_id, role, quote)
            item["evidence_event_id"] = slice_id
            if slice_id in seen_slice_ids:
                continue
            seen_slice_ids.add(slice_id)
            evidence_slices.append(
                {
                    "event_id": slice_id,
                    "source_event_id": source_event_id,
                    "role": role,
                    "text": quote,
                }
            )
        filtered["dialogue_events"] = evidence_slices
    return filtered, rejections


def _completed_prefix_mode(
    data: dict, *, label: str, require_explicit: bool = False
) -> str | None:
    """Return a recognized prefix mode without letting modern markers fail open."""
    if "completed_prefix_mode" in data:
        mode = data.get("completed_prefix_mode")
        if not isinstance(mode, str) or mode not in {"turn", "legacy_whole_source"}:
            raise ValueError(f"{label} has invalid completed_prefix_mode")
        return mode
    modern_markers = {
        "completed_cursor",
        "completed_prefix_sha256",
        "completed_at",
        "completed_date",
        "completed_turn_id",
        "dialogue_events",
        "host_id",
        "session_id",
    }
    if require_explicit or modern_markers.intersection(data):
        raise ValueError(f"{label} is missing completed_prefix_mode")
    return None


def _validate_modern_artifact_binding(
    data: dict,
    *,
    label: str = "modern extraction",
    require_chunks: bool = False,
) -> None:
    """Verify a modern artifact is structurally and cryptographically bound."""
    if _completed_prefix_mode(data, label=label, require_explicit=True) != "turn":
        raise ValueError(f"{label} is not a completed turn")
    for field in ("source_host", "host_id", "session_id", "completed_turn_id"):
        value = data.get(field)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{label} has invalid {field}")
    if data["source_host"] not in {"home", "sjm"}:
        raise ValueError(f"{label} has invalid source_host")
    if data["host_id"] != f"host:{data['source_host']}":
        raise ValueError(f"{label} host identity does not match")
    for field, pattern in (
        ("completed_cursor", r"cursor:[0-9a-f]{64}"),
        ("completed_prefix_sha256", r"[0-9a-f]{64}"),
        ("transcript_sha256", r"[0-9a-f]{64}"),
    ):
        value = data.get(field)
        if not isinstance(value, str) or not re.fullmatch(pattern, value):
            raise ValueError(f"{label} has invalid {field}")
    try:
        completed_at = _completed_at_iso(data.get("completed_at"))
    except ValueError as exc:
        raise ValueError(f"{label} has invalid completed_at") from exc
    try:
        completed_dt = datetime.fromisoformat(
            completed_at.strip().replace("Z", "+00:00")
        )
    except ValueError as exc:
        raise ValueError(f"{label} has invalid completed_at") from exc
    if completed_dt.tzinfo is None or completed_dt.utcoffset() is None:
        raise ValueError(f"{label} completed_at must include a timezone")
    completed_date = data.get("completed_date")
    if not isinstance(completed_date, str):
        raise ValueError(f"{label} has invalid completed_date")
    try:
        normalized_completed_date = date.fromisoformat(completed_date).isoformat()
    except ValueError as exc:
        raise ValueError(f"{label} has invalid completed_date") from exc
    if _parse_event_date(completed_at) != normalized_completed_date:
        raise ValueError(f"{label} completed timestamp does not match date")
    run_date = data.get("run_date")
    if not isinstance(run_date, str) or normalized_completed_date != run_date:
        raise ValueError(f"{label} completed date does not match run date")
    events = data.get("dialogue_events")
    if not isinstance(events, list) or not events:
        raise ValueError(f"{label} has invalid dialogue_events")
    event_ids: list[str] = []
    observed_turn_ids: set[str] = set()
    allowed_event_fields = {"event_id", "turn_id", "role", "text", "ordinal"}
    for event in events:
        if not isinstance(event, dict) or set(event) != allowed_event_fields:
            raise ValueError(f"{label} has invalid dialogue event shape")
        event_id = event.get("event_id")
        if (
            not isinstance(event_id, str)
            or not event_id.strip()
            or not event_id.startswith("event:")
        ):
            raise ValueError(f"{label} has invalid dialogue event id")
        if event_id in event_ids:
            raise ValueError(f"{label} has duplicate dialogue event id")
        event_ids.append(event_id)
        role = event.get("role")
        if not isinstance(role, str) or role not in {"user", "assistant"}:
            raise ValueError(f"{label} has invalid dialogue event role")
        text = event.get("text")
        if not isinstance(text, str) or not text.strip():
            raise ValueError(f"{label} has invalid dialogue event text")
        turn_id = event.get("turn_id")
        if not isinstance(turn_id, str) or not turn_id.strip():
            raise ValueError(f"{label} has invalid dialogue event turn_id")
        observed_turn_ids.add(turn_id)
        ordinal = event.get("ordinal")
        if isinstance(ordinal, bool) or not isinstance(ordinal, int) or ordinal < 0:
            raise ValueError(f"{label} has invalid dialogue event ordinal")
    if data["completed_turn_id"] not in observed_turn_ids:
        raise ValueError(f"{label} completed turn has no dialogue event")
    transcript = "\n\n".join(
        f"[{event['role']}]\n{event['text']}" for event in events
    ).strip()
    prefix_hash = hashlib.sha256(transcript.encode("utf-8")).hexdigest()
    if data.get("completed_prefix_sha256") != prefix_hash:
        raise ValueError(f"{label} completed prefix hash does not match events")
    if data.get("transcript_sha256") != prefix_hash:
        raise ValueError(f"{label} transcript hash does not match events")
    cursor_seed = json.dumps(
        [
            data.get("host_id"),
            data.get("session_id"),
            data["completed_turn_id"],
            event_ids,
            prefix_hash,
        ],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    expected_cursor = "cursor:" + hashlib.sha256(
        cursor_seed.encode("utf-8")
    ).hexdigest()
    if data.get("completed_cursor") != expected_cursor:
        raise ValueError(f"{label} cursor does not match events")
    if require_chunks:
        chunks = data.get("chunks")
        if not isinstance(chunks, list) or not chunks:
            raise ValueError(f"{label} has invalid chunks container")
        allowed_chunk_fields = {"chunk_id", "index", "text"}
        for chunk in chunks:
            if not isinstance(chunk, dict) or set(chunk) != allowed_chunk_fields:
                raise ValueError(f"{label} has invalid chunks shape")
            index = chunk.get("index")
            if isinstance(index, bool) or not isinstance(index, int) or index < 0:
                raise ValueError(f"{label} has invalid chunks index")
            chunk_id = chunk.get("chunk_id")
            if not isinstance(chunk_id, str) or not re.fullmatch(
                r"chunk:[0-9a-f]{64}", chunk_id
            ):
                raise ValueError(f"{label} has invalid chunks id")
            if not isinstance(chunk.get("text"), str) or not chunk["text"]:
                raise ValueError(f"{label} has invalid chunks text")
        expected_chunks = _bounded_dialogue_chunks(
            transcript,
            cursor=expected_cursor,
            max_chunk_chars=DEFAULT_DIALOGUE_CHUNK_CHARS,
        )
        if chunks != expected_chunks:
            raise ValueError(f"{label} chunks do not match transcript")


def _all_rejections_are_content_class(rejections: list[dict]) -> bool:
    """True when every rejection is a content/groundability failure (extractor
    produced well-formed items but couldn't ground them), not a schema/contract
    failure (extractor itself drifted from the items spec).

    Reads the structured ``rejection_class`` field set by
    ``_filter_extraction_items`` based on the exception type at raise-time
    (``ContentClassRejectionError`` subclasses vs plain ``ValueError``).
    String matching is avoided so an extractor-controlled value echoed into
    an error message cannot smuggle a schema failure into the skip path.
    """
    return bool(rejections) and all(
        rejection.get("rejection_class") == "content" for rejection in rejections
    )


def _validate_canonical_home_routing(item: dict, *, idx: int, source: Path) -> None:
    raw = item.get("canonical_home")
    if raw is None:
        return
    # Non-string canonical_home is schema-class drift, not content-class.
    # Without this check str() would stringify a dict/list and the
    # equality check would then raise UnsupportedCanonicalHomeError
    # (content-class) instead of surfacing the type drift as a failure.
    if not isinstance(raw, str):
        raise ValueError(
            f"extractor item {idx} has non-string canonical_home: "
            f"{type(raw).__name__}"
        )
    canonical_home = raw.strip()
    if canonical_home and canonical_home != "wiki/context/shelves/semantic.md":
        raise UnsupportedCanonicalHomeError(
            f"extractor item {idx} has unsupported canonical_home: {canonical_home}"
        )


def _validate_extraction_item(
    item: object,
    *,
    idx: int,
    source: Path,
    defer_content_class_checks: bool = False,
) -> None:
    if not isinstance(item, dict):
        raise ValueError(f"extractor item {idx} for {source} is not an object")
    allowed_fields = {
        "canonical_home",
        "confidence",
        "dedupe_key",
        "evidence_event_id",
        "evidence_quote",
        "kind",
        "object",
        "predicate",
        "scope",
        "session_path",
        "source_app",
        "speaker_source",
        "subject",
        "subtype",
    }
    extra_fields = sorted(set(item) - allowed_fields)
    if extra_fields:
        raise ValueError(
            f"extractor item {idx} has unsupported field(s): {', '.join(extra_fields)}"
        )
    kind = str(item.get("kind", "")).lower()
    confidence = str(item.get("confidence", "")).lower()
    if kind not in {"fact", "decision", "learning", "preference", "commitment"}:
        raise ValueError(f"extractor item {idx} has unsupported kind: {kind}")
    if confidence not in {"high", "medium", "low"}:
        raise ValueError(
            f"extractor item {idx} has unsupported confidence: {confidence}"
        )
    speaker_source = str(item.get("speaker_source", "")).strip()
    if speaker_source and speaker_source not in {"keith", "assistant_confirmed"}:
        raise ValueError(
            f"extractor item {idx} has unsupported speaker_source: {speaker_source}"
        )
    for required in ("subject", "predicate", "object"):
        if required not in item:
            raise ValueError(f"extractor item {idx} missing {required}")
        if not str(item.get(required, "")).strip():
            raise ValueError(f"extractor item {idx} has empty {required}")
    if kind != "preference":
        if not str(item.get("dedupe_key", "")).strip():
            raise ValueError(f"extractor item {idx} missing dedupe_key")
        dedupe_key = str(item.get("dedupe_key", "")).strip()
        if not DEDUPE_KEY_RE.match(dedupe_key):
            raise ValueError(f"extractor item {idx} has invalid dedupe_key: {dedupe_key}")
        for field in PIPE_UNSAFE_FIELDS:
            value = str(item.get(field, ""))
            if "|" in value:
                raise ValueError(
                    f"extractor item {idx} has unsupported pipe delimiter in {field}"
                )
        for field in CONTROL_WHITESPACE_UNSAFE_FIELDS:
            value = str(item.get(field, ""))
            if any(ch in value for ch in ("\n", "\r", "\t")):
                raise ValueError(
                    f"extractor item {idx} has unsupported control whitespace in {field}"
                )
        _validate_evidence_quote_controls(
            str(item.get("evidence_quote", "")), idx=idx
        )
    # Content-class checks (canonical_home routing) are deferred when called
    # from _filter_extraction_items so they run AFTER _validate_item_evidence_quote
    # too -- otherwise the canonical_home raise would mask schema-class
    # quote errors (too-short, control char). Other callers (validate_extraction,
    # reconcile_extractions) opt in to the full check by leaving the default.
    if not defer_content_class_checks:
        _validate_canonical_home_routing(item, idx=idx, source=source)


def _existing_valid_extraction(
    path: Path,
    *,
    transcript_sha256: str | None = None,
    raw_source_sha256: str | None = None,
    completed_cursor: str | None = None,
    review_revision: str | None = None,
    payload: str | None = None,
    session_path: Path | None = None,
    dialogue_events: list[dict] | None = None,
) -> bool:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    try:
        validate_extraction(data, path)
        mode = _completed_prefix_mode(data, label="cached extraction")
        if mode == "turn":
            _validate_modern_artifact_binding(data)
        legacy_slices = bool(
            mode == "legacy_whole_source"
            and _validate_legacy_evidence_slices(
                data,
                label="cached extraction",
                source_events=dialogue_events,
            )
        )
    except ValueError:
        return False
    if data.get("schema_version") != EXTRACTION_SCHEMA_VERSION:
        return False
    if transcript_sha256 is not None and data.get("transcript_sha256") != transcript_sha256:
        return False
    if completed_cursor is not None and data.get("completed_cursor") != completed_cursor:
        return False
    if review_revision is not None and data.get("review_revision") != review_revision:
        return False
    if (
        completed_cursor is None
        and raw_source_sha256 is not None
        and data.get("raw_source_sha256") != raw_source_sha256
    ):
        return False
    # Re-run the evidence_quote contract on cached items so a stale extraction
    # written before the tightened schema (or by a drifted extractor) is not
    # silently treated as valid. validate_extraction above only runs the item-
    # shape checks; the per-quote presence + non-empty + control + grounding
    # checks live in _validate_item_evidence_quote and need the payload.
    # session_path is only used to construct error messages; default to the
    # cache file path so any future caller that passes payload without it
    # still gets the re-validation rather than silently bypassing it.
    if payload is not None:
        normalized_payload = _normalize_evidence_text(payload)
        error_path = session_path if session_path is not None else path
        try:
            for idx, item in enumerate(data.get("items", [])):
                if not isinstance(item, dict):
                    return False
                _validate_item_evidence_quote(
                    item,
                    idx=idx,
                    normalized_payload=normalized_payload,
                    session_path=error_path,
                )
                _validate_speaker_attribution(
                    item,
                    payload,
                    idx=idx,
                    dialogue_events=(
                        data.get("dialogue_events")
                        if legacy_slices
                        else dialogue_events or data.get("dialogue_events")
                    ),
                    require_structured_attribution=(
                        mode == "turn"
                    ),
                )
        except ValueError:
            return False
    return True


def _source_only_writer() -> bool:
    """Return whether this process must not write shared capture state.

    An explicit role wins so the same code and tests behave consistently on
    either Mac.  The SJM account name remains the fail-safe when no role has
    been supplied by a wrapper or caller.
    """
    role = os.environ.get("K2B_CAPTURE_WRITER_ROLE")
    if role is not None:
        # Only the exact home role can enable a shared write. Typos and future
        # unknown values fail closed on either machine.
        return role != "home"
    # The known home Mac account is the only implicit writer. SJM currently
    # uses keithcheung, and every unknown/migrated account fails closed until
    # its wrapper supplies the explicit role.
    return Path.home().name != "keithmbpm2"


def _capture_artifact_name(
    run_date: str,
    session_path: Path,
    *,
    completed_cursor: str | None = None,
    review_revision: str | None = None,
) -> str:
    stem = f"{run_date}_{_safe_session_id(session_path)}"
    if completed_cursor:
        cursor_token = hashlib.sha256(completed_cursor.encode("utf-8")).hexdigest()[:16]
        stem += f"__cursor-{cursor_token}"
    if review_revision:
        stem += f"__review-{review_revision[:16]}"
    return stem + ".json"


def _clear_stale_session_diagnostics(
    vault_path: Path,
    session_path: Path,
    *,
    run_date: str,
    completed_cursor: str | None = None,
) -> None:
    """Remove terminal markers only for the exact successfully staged prefix."""
    artifact_name = _capture_artifact_name(
        run_date, session_path, completed_cursor=completed_cursor
    )
    for directory in (
        "extraction-failures",
        "eod-quarantine",
        "extraction-skips",
    ):
        path = vault_path / ".staging" / directory / artifact_name
        try:
            path.unlink()
        except FileNotFoundError:
            continue
        _fsync_dir(path.parent)


def _reconcile_lock_path(vault_path: Path) -> Path:
    return vault_path / ".staging" / "eod-reconcile.lock"


def _remove_staged_extraction(vault_path: Path, out_path: Path) -> None:
    """Remove an extraction while excluding the global reconciler."""
    with _file_lock(_reconcile_lock_path(vault_path)):
        try:
            out_path.unlink()
        except FileNotFoundError:
            return
        _fsync_dir(out_path.parent)


def run_job_a(
    session_paths: Iterable[Path],
    *,
    vault_path: Path,
    run_date: str,
    extract_func: ExtractFunc | None = None,
    codex_root: Path | None = None,
    completed_cursor: str | None = None,
    review_revision: str | None = None,
) -> list[Path]:
    """Strip each session, run extraction, and stage JSON files."""
    if _source_only_writer():
        raise PermissionError("shared extraction staging requires the home writer")
    extract = extract_func or call_kimi_extractor
    codex_root = _resolve_codex_root(codex_root)
    out_dir = vault_path / ".staging" / "extractions"
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for session_path in session_paths:
        session_id = _safe_session_id(session_path)
        out_path = out_dir / _capture_artifact_name(run_date, session_path)
        lock_path = vault_path / ".staging" / "extraction-locks" / f"{run_date}_{session_id}.lock"
        with _file_lock(lock_path):
                raw_source_sha256: str | None = None
                transcript_sha256: str | None = None
                selected_cursor: str | None = None
                attempt_cursor: str | None = None
                try:
                    source_stat = session_path.stat()
                    raw_source_sha256 = _sha256_file(session_path)
                    parsed = parse_completed_dialogue(session_path, source_host="home")
                    prefix = _select_completed_prefix(
                        parsed, run_date=run_date, completed_cursor=completed_cursor
                    )
                    payload = str(prefix.get("transcript", "")) if prefix else ""
                    selected_cursor = str(prefix.get("cursor", "")) if prefix else None
                    modern_prefix = bool(prefix and prefix.get("mode") == "turn")
                    cursor_bound_prefix = bool(
                        prefix and (modern_prefix or parsed.get("explicit_turn_markers"))
                    )
                    attempt_cursor = selected_cursor if cursor_bound_prefix else None
                    out_path = out_dir / _capture_artifact_name(
                        run_date,
                        session_path,
                        completed_cursor=selected_cursor if cursor_bound_prefix else None,
                        review_revision=review_revision,
                    )
                    if not cursor_bound_prefix and _source_changed_since(
                        session_path, source_stat, raw_source_sha256
                    ):
                        _remove_staged_extraction(vault_path, out_path)
                        _write_extraction_skip(
                            vault_path,
                            session_path,
                            run_date=run_date,
                            reason="source_changed_during_read",
                            error=RuntimeError("source changed while transcript was read"),
                            raw_source_sha256=raw_source_sha256,
                            codex_root=codex_root,
                            completed_cursor=attempt_cursor,
                        )
                        continue
                    if not payload:
                        _write_extraction_skip(
                            vault_path,
                            session_path,
                            run_date=run_date,
                            reason="empty_stripped_transcript",
                            error=ValueError("empty stripped transcript"),
                            raw_source_sha256=raw_source_sha256,
                            codex_root=codex_root,
                            completed_cursor=attempt_cursor,
                        )
                        continue
                    transcript_sha256 = hashlib.sha256(payload.encode("utf-8")).hexdigest()
                    cache_candidates = [out_path]
                    if cursor_bound_prefix:
                        base = f"{run_date}_{_safe_session_id(session_path)}"
                        cache_candidates = sorted(out_dir.glob(f"{base}*.json"))
                    if any(_existing_valid_extraction(
                        candidate,
                        transcript_sha256=transcript_sha256,
                        raw_source_sha256=raw_source_sha256,
                        completed_cursor=(
                            selected_cursor if cursor_bound_prefix else None
                        ),
                        review_revision=review_revision,
                        payload=payload,
                        session_path=session_path,
                        dialogue_events=prefix.get("events") if prefix else None,
                    ) for candidate in cache_candidates):
                        _clear_stale_session_diagnostics(
                            vault_path,
                            session_path,
                            run_date=run_date,
                            completed_cursor=(
                                selected_cursor if cursor_bound_prefix else None
                            ),
                        )
                        continue
                    _clear_stale_session_diagnostics(
                        vault_path,
                        session_path,
                        run_date=run_date,
                        completed_cursor=(
                            selected_cursor if cursor_bound_prefix else None
                        ),
                    )
                    _remove_staged_extraction(vault_path, out_path)
                    if extract is call_kimi_extractor:
                        data = call_kimi_extractor(
                            payload,
                            session_path,
                            vault_path=vault_path,
                            run_date=run_date,
                            transcript_sha256=transcript_sha256,
                            raw_source_sha256=raw_source_sha256,
                            codex_root=codex_root,
                            completed_cursor=attempt_cursor,
                        )
                    else:
                        data = extract(payload, session_path)
                    if data is None:
                        continue
                    current_prefixes = []
                    if cursor_bound_prefix and selected_cursor:
                        current = parse_completed_dialogue(
                            session_path, source_host="home"
                        )
                        current_prefixes = [
                            str(candidate.get("cursor", ""))
                            for candidate in current.get("completed_prefixes", [])
                        ]
                    source_invalidated = (
                        selected_cursor not in current_prefixes
                        if cursor_bound_prefix and selected_cursor
                        else _source_changed_since(
                            session_path, source_stat, raw_source_sha256
                        )
                    )
                    if source_invalidated:
                        _remove_staged_extraction(vault_path, out_path)
                        _write_extraction_skip(
                            vault_path,
                            session_path,
                            run_date=run_date,
                            reason="source_changed_during_extraction",
                            error=RuntimeError(
                                "source changed while extraction was running"
                            ),
                            transcript_sha256=transcript_sha256,
                            raw_source_sha256=raw_source_sha256,
                            codex_root=codex_root,
                            completed_cursor=attempt_cursor,
                        )
                        continue
                    data.setdefault("schema_version", EXTRACTION_SCHEMA_VERSION)
                    if data.get("schema_version") != EXTRACTION_SCHEMA_VERSION:
                        error = ValueError(
                            "unsupported extraction schema_version: "
                            f"{data.get('schema_version')}"
                        )
                        _write_extraction_quarantine(
                            vault_path,
                            session_path,
                            run_date=run_date,
                            reason="schema_invalid_extraction",
                            error=error,
                            transcript_sha256=transcript_sha256,
                            raw_source_sha256=raw_source_sha256,
                            extractor_output=data,
                            codex_root=codex_root,
                            completed_cursor=attempt_cursor,
                        )
                        print(
                            f"eod-capture: extraction quarantined for {session_path}: "
                            f"{_sanitize_log_value(error)}",
                            file=sys.stderr,
                        )
                        continue
                    data.setdefault("session_path", str(session_path))
                    data.setdefault(
                        "source_app", detect_source_app(session_path, codex_root=codex_root)
                    )
                    data.setdefault("items", [])
                    data["transcript_sha256"] = transcript_sha256
                    data["raw_source_sha256"] = raw_source_sha256
                    data["run_date"] = run_date
                    if review_revision is not None:
                        data["review_revision"] = review_revision
                    if prefix:
                        data["source_host"] = parsed["source_host"]
                        data["host_id"] = parsed["host_id"]
                        data["session_id"] = parsed["session_id"]
                        data["completed_cursor"] = prefix["cursor"]
                        data["completed_prefix_sha256"] = prefix["prefix_sha256"]
                        data["completed_at"] = prefix["completed_at"]
                        data["completed_date"] = prefix["completed_date"]
                        data["completed_prefix_mode"] = (
                            str(prefix.get("mode") or "legacy_whole_source")
                        )
                        data["completed_turn_id"] = prefix["turn_id"]
                        data["dialogue_events"] = prefix["events"]
                    try:
                        data, rejections = _filter_extraction_items(
                            data,
                            payload,
                            session_path,
                            dialogue_events=prefix.get("events") if prefix else None,
                            require_structured_attribution=modern_prefix,
                        )
                    except ValueError as exc:
                        _write_extraction_quarantine(
                            vault_path,
                            session_path,
                            run_date=run_date,
                            reason="schema_invalid_extraction",
                            error=exc,
                            transcript_sha256=transcript_sha256,
                            raw_source_sha256=raw_source_sha256,
                            extractor_output=data,
                            codex_root=codex_root,
                            completed_cursor=attempt_cursor,
                        )
                        print(
                            f"eod-capture: extraction quarantined for {session_path}: "
                            f"{_sanitize_log_value(exc)}",
                            file=sys.stderr,
                        )
                        continue
                    for rejection in rejections:
                        _write_extraction_rejection(
                            vault_path,
                            session_path,
                            run_date=run_date,
                            rejection=rejection,
                            transcript_sha256=transcript_sha256,
                            raw_source_sha256=raw_source_sha256,
                            codex_root=codex_root,
                        )
                    if rejections and not data.get("items"):
                        message = f"all extractor items rejected for {session_path}"
                        if any(
                            rejection.get("rejection_class") == "schema"
                            for rejection in rejections
                        ):
                            error = ValueError(message)
                            _write_extraction_quarantine(
                                vault_path,
                                session_path,
                                run_date=run_date,
                                reason="schema_invalid_extraction",
                                error=error,
                                transcript_sha256=transcript_sha256,
                                raw_source_sha256=raw_source_sha256,
                                extractor_output=data,
                                rejections=rejections,
                                codex_root=codex_root,
                                completed_cursor=attempt_cursor,
                            )
                            print(
                                f"eod-capture: extraction quarantined for {session_path}: "
                                f"{_sanitize_log_value(error)}",
                                file=sys.stderr,
                            )
                            continue
                        if _all_rejections_are_content_class(rejections):
                            # Extractor produced well-formed items but couldn't
                            # ground them in the transcript (hallucination /
                            # bad canonical_home). Skip the session; the cron
                            # pipeline continues so the rest of the day's
                            # successful extractions still reach job-b.
                            raise AllItemsRejectedError(message)
                        error = ValueError(message)
                        _write_extraction_quarantine(
                            vault_path,
                            session_path,
                            run_date=run_date,
                            reason="schema_invalid_extraction",
                            error=error,
                            transcript_sha256=transcript_sha256,
                            raw_source_sha256=raw_source_sha256,
                            extractor_output=data,
                            rejections=rejections,
                            codex_root=codex_root,
                            completed_cursor=attempt_cursor,
                        )
                        print(
                            f"eod-capture: extraction quarantined for {session_path}: "
                            f"{_sanitize_log_value(error)}",
                            file=sys.stderr,
                        )
                        continue
                    if any(
                        rejection.get("rejection_class") == "schema"
                        for rejection in rejections
                    ):
                        error = ValueError(
                            f"extractor produced schema-invalid item(s) for {session_path}"
                        )
                        _write_extraction_quarantine(
                            vault_path,
                            session_path,
                            run_date=run_date,
                            reason="schema_invalid_extraction",
                            error=error,
                            transcript_sha256=transcript_sha256,
                            raw_source_sha256=raw_source_sha256,
                            extractor_output=data,
                            rejections=rejections,
                            codex_root=codex_root,
                            completed_cursor=attempt_cursor,
                        )
                        print(
                            f"eod-capture: extraction quarantined for {session_path}: "
                            f"{_sanitize_log_value(error)}",
                            file=sys.stderr,
                        )
                    with _file_lock(_reconcile_lock_path(vault_path)):
                        _atomic_write_json(out_path, data)
                    written.append(out_path)
                except AllItemsRejectedError as exc:
                    print(
                        f"eod-capture: extraction skipped for {session_path}: "
                        f"{_sanitize_log_value(exc)}",
                        file=sys.stderr,
                    )
                    _write_extraction_skip(
                        vault_path,
                        session_path,
                        run_date=run_date,
                        reason=ALL_ITEMS_REJECTED_SKIP_REASON,
                        error=exc,
                        transcript_sha256=transcript_sha256,
                        raw_source_sha256=raw_source_sha256,
                        codex_root=codex_root,
                        completed_cursor=attempt_cursor,
                    )
                except (
                    OSError,
                    RuntimeError,
                    json.JSONDecodeError,
                    ValueError,
                    subprocess.SubprocessError,
                ) as exc:
                    print(
                        f"eod-capture: extraction failed for {session_path}: "
                        f"{_sanitize_log_value(exc)}",
                        file=sys.stderr,
                    )
                    _write_extraction_failure(
                        vault_path,
                        session_path,
                        run_date=run_date,
                        error=exc,
                        transcript_sha256=transcript_sha256,
                        raw_source_sha256=raw_source_sha256,
                        codex_root=codex_root,
                        completed_cursor=attempt_cursor,
                    )
    return written


def _validated_reviewed_source_bundle(
    bundle: object,
    reviewed: object,
    *,
    run_date: str,
    require_automatic_source: bool = False,
) -> dict:
    """Bind reviewed items to one validated immutable completed-source prefix.

    The returned extraction is the only boundary allowed to assert that review
    applies to this exact source.  Caller-supplied review flags are ignored.
    """
    required = {
        "bundle_schema_version", "source_host", "source_app", "session_path",
        "run_date", "raw_source_sha256", "transcript", "transcript_sha256",
    }
    if not isinstance(bundle, dict) or not required.issubset(bundle):
        raise ValueError("source bundle is missing required provenance fields")
    if bundle["bundle_schema_version"] != 1 or bundle["source_host"] not in {"home", "sjm"}:
        raise ValueError("unsupported source bundle identity")
    if bundle["source_app"] != "codex_desktop" or bundle["run_date"] != run_date:
        raise ValueError("source bundle app or date does not match this capture")
    if not isinstance(bundle["session_path"], str) or not Path(bundle["session_path"]).is_absolute():
        raise ValueError("source bundle session_path must be absolute")
    for field in ("raw_source_sha256", "transcript_sha256"):
        if not isinstance(bundle[field], str) or not re.fullmatch(r"[0-9a-f]{64}", bundle[field]):
            raise ValueError(f"source bundle has invalid {field}")
    transcript = bundle["transcript"]
    if not isinstance(transcript, str) or not transcript.strip():
        raise ValueError("source bundle transcript is empty")
    if hashlib.sha256(transcript.encode("utf-8")).hexdigest() != bundle["transcript_sha256"]:
        raise ValueError("source bundle transcript hash does not match")
    mode = _completed_prefix_mode(
        bundle, label="source bundle", require_explicit=True
    )
    modern = mode == "turn"
    provenance_fields = (
        "source_host",
        "host_id",
        "session_id",
        "completed_cursor",
        "completed_prefix_sha256",
        "completed_at",
        "completed_date",
        "completed_prefix_mode",
        "review_revision",
        "completed_turn_id",
        "run_date",
    )
    if modern:
        _validate_modern_artifact_binding(
            bundle, label="source bundle", require_chunks=True
        )
        rendered = "\n\n".join(
            f"[{event['role']}]\n{event['text']}"
            for event in bundle["dialogue_events"]
        ).strip()
        if rendered != transcript:
            raise ValueError("source bundle dialogue_events do not match transcript")
    if require_automatic_source:
        if not modern:
            raise ValueError("automatic memory requires a completed-turn source")
        if bundle.get("provenance_status") != "known_user":
            raise ValueError("automatic memory source provenance is not a known user session")
        thread_source = bundle.get("thread_source")
        if (
            not isinstance(thread_source, str)
            or thread_source not in INTERACTIVE_THREAD_SOURCES
        ):
            raise ValueError("automatic memory source is worker, imported, or unknown")
    if not isinstance(reviewed, dict) or not isinstance(reviewed.get("items"), list):
        raise ValueError("reviewed extraction requires an explicit items list")
    if not reviewed["items"] and reviewed.get("reviewed_empty") is not True:
        raise ValueError(
            "empty reviewed extraction requires explicit reviewed_empty=true"
        )
    for field in ("raw_source_sha256", "transcript_sha256"):
        if reviewed.get(field) != bundle[field]:
            raise ValueError(f"reviewed extraction {field} does not match source bundle")
    for field in provenance_fields:
        if field in reviewed and reviewed[field] != bundle.get(field):
            raise ValueError(f"reviewed extraction {field} does not match source bundle")

    source_path = Path(bundle["session_path"])
    data = deepcopy(reviewed)
    data.setdefault("schema_version", EXTRACTION_SCHEMA_VERSION)
    if data["schema_version"] != EXTRACTION_SCHEMA_VERSION:
        raise ValueError("unsupported reviewed extraction schema_version")
    data["session_path"] = str(source_path)
    data["source_app"] = "codex_desktop"
    data["source_host"] = bundle["source_host"]
    data["run_date"] = run_date
    for item in data["items"]:
        if isinstance(item, dict):
            item["session_path"] = str(source_path)
            item["source_app"] = "codex_desktop"
    filtered, rejections = _filter_extraction_items(
        data,
        transcript,
        source_path,
        dialogue_events=bundle.get("dialogue_events"),
        require_structured_attribution=modern,
    )
    if rejections:
        raise ValueError("reviewed extraction contains invalid or ungrounded items")
    if require_automatic_source:
        events_by_id = {
            event["event_id"]: event for event in bundle["dialogue_events"]
        }
        completed_turn_id = bundle["completed_turn_id"]
        for idx, item in enumerate(filtered["items"]):
            evidence_event = events_by_id[item["evidence_event_id"]]
            if evidence_event["turn_id"] != completed_turn_id:
                raise ValueError(
                    f"automatic memory item {idx} evidence is not from the "
                    "work item's completed turn"
                )
    for field in (
        "raw_source_sha256",
        "transcript_sha256",
        "host_id",
        "session_id",
        "completed_cursor",
        "completed_prefix_sha256",
        "completed_at",
        "completed_date",
        "completed_prefix_mode",
        "completed_turn_id",
        "provenance_status",
        "reviewed_empty",
        "run_date",
        "source_app",
        "source_host",
        "session_path",
        "thread_source",
    ):
        if field in bundle:
            filtered[field] = bundle[field]
        elif field in data:
            filtered[field] = data[field]

    review_revision = hashlib.sha256(
        json.dumps(
            {
                "items": filtered.get("items", []),
                "reviewed_empty": filtered.get("reviewed_empty") is True,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    filtered["review_revision"] = review_revision
    return filtered


def stage_reviewed_bundle(
    bundle: dict, reviewed: dict, *, vault_path: Path, run_date: str
) -> Path:
    """Stage one interactively reviewed remote source without a provider call."""
    if _source_only_writer():
        raise PermissionError("reviewed bundle staging requires the home writer")
    filtered = _validated_reviewed_source_bundle(
        bundle, reviewed, run_date=run_date
    )
    source_path = Path(filtered["session_path"])
    modern = filtered.get("completed_prefix_mode") == "turn"
    review_revision = filtered["review_revision"]
    out_path = vault_path / ".staging" / "extractions" / _capture_artifact_name(
        run_date,
        source_path,
        completed_cursor=(
            str(filtered.get("completed_cursor") or "") if modern else None
        ),
        review_revision=review_revision,
    )
    lock_path = vault_path / ".staging" / "extraction-locks" / f"{run_date}_{_safe_session_id(source_path)}.lock"
    with _file_lock(lock_path):
        with _file_lock(_reconcile_lock_path(vault_path)):
            if out_path.exists():
                existing = _read_memory_json(
                    out_path, "staged reviewed extraction"
                )
                replay_identity = {
                    key: value
                    for key, value in existing.items()
                    if key != "exported_at"
                }
                filtered_identity = {
                    key: value
                    for key, value in filtered.items()
                    if key != "exported_at"
                }
                if replay_identity != filtered_identity:
                    raise RuntimeError(
                        "staged reviewed extraction conflicts with immutable artifact"
                    )
            else:
                _atomic_write_json(out_path, filtered)
            _clear_stale_session_diagnostics(
                vault_path,
                source_path,
                run_date=run_date,
                completed_cursor=(
                    str(filtered.get("completed_cursor") or "") if modern else None
                ),
            )
    return out_path


def _memory_writer_role(writer_role: str) -> str:
    if writer_role not in {"home", "sjm-source-only"}:
        raise PermissionError("automatic memory requires a known Home or SJM writer role")
    return writer_role


def _automatic_memory_key(item: dict) -> str:
    seed = item.get("dedupe_key") or ":".join(
        str(item.get(field, "")) for field in ("kind", "subject", "predicate")
    )
    normalized = re.sub(r"[^a-z0-9_.-]+", ".", str(seed).lower()).strip(".-_")
    if not normalized or not normalized[0].isalpha():
        normalized = f"memory.{normalized}" if normalized else "memory.item"
    digest = hashlib.sha256(str(seed).encode("utf-8")).hexdigest()[:12]
    return f"{normalized[:110].rstrip('.-_')}.{digest}"


def _reviewed_memory_bundle_result(
    bundle: object, reviewed: object, *, run_date: str
) -> dict:
    filtered = _validated_reviewed_source_bundle(
        bundle,
        reviewed,
        run_date=run_date,
        require_automatic_source=True,
    )
    grouped: dict[str, list[dict]] = {}
    filter_reasons: dict[str, int] = {}
    for item in filtered["items"]:
        reason = None
        if item["kind"] not in automatic_memory.ALLOWED_KINDS:
            reason = "unsupported_memory_kind"
        elif item["confidence"] != "high":
            reason = "confidence_not_high"
        if reason is not None:
            filter_reasons[reason] = filter_reasons.get(reason, 0) + 1
            continue
        grouped.setdefault(item["scope"], []).append(
            {
                "key": _automatic_memory_key(item),
                "kind": item["kind"],
                "value": item["object"],
                "speaker_source": item["speaker_source"],
                "evidence_event_id": item["evidence_event_id"],
                "evidence_quote": item["evidence_quote"],
            }
        )

    memory_bundles: list[dict] = []
    for scope in sorted(grouped):
        candidate = {
            "schema_version": automatic_memory.BUNDLE_SCHEMA_VERSION,
            "scope": scope,
            # Review state is established here, after source/hash/quote/speaker
            # binding above; it is never accepted from the caller as proof.
            "review_state": "reviewed",
            "redaction": {"status": "redacted", "raw_dialogue_included": False},
            "source": {
                "host_id": filtered["host_id"],
                "session_id": filtered["session_id"],
                "completed_cursor": filtered["completed_cursor"],
                "completed_at": _completed_at_iso(filtered["completed_at"]),
                "source_hash": filtered["completed_prefix_sha256"],
                "transcript_hash": filtered["transcript_sha256"],
                "origin": "interactive",
                "source_kind": "codex_session",
                "thread_source": filtered["thread_source"],
            },
            "items": grouped[scope],
        }
        memory_bundles.append(automatic_memory.validate_bundle(candidate))
    queued_items = sum(len(candidate["items"]) for candidate in memory_bundles)
    return {
        "bundles": memory_bundles,
        "input_items": len(filtered["items"]),
        "queued_items": queued_items,
        "filtered_items": len(filtered["items"]) - queued_items,
        "filter_reasons": filter_reasons,
        "reviewed_empty": filtered.get("reviewed_empty") is True,
    }


def build_reviewed_memory_bundles(
    bundle: object, reviewed: object, *, run_date: str
) -> list[dict]:
    """Adapt evidence-validated completed sources to the durable memory contract."""
    return _reviewed_memory_bundle_result(
        bundle, reviewed, run_date=run_date
    )["bundles"]


def queue_reviewed_memory(
    bundle: object,
    reviewed: object,
    *,
    state_root: Path,
    run_date: str,
    writer_role: str,
    max_attempts: int = 5,
) -> dict:
    """Queue evidence-validated memory locally; never transport it."""
    role = _memory_writer_role(writer_role)
    build_result = _reviewed_memory_bundle_result(
        bundle, reviewed, run_date=run_date
    )
    if (
        role == "sjm-source-only"
        and isinstance(bundle, dict)
        and bundle.get("source_host") != "sjm"
    ):
        raise PermissionError("SJM may queue only its own source bundles")
    deliveries = []
    for candidate in build_result["bundles"]:
        envelope = automatic_memory.enqueue_bundle(
            state_root, candidate, max_attempts=max_attempts
        )
        deliveries.append(
            {
                "delivery_id": envelope["delivery_id"],
                "content_id": envelope["content_id"],
                "status": envelope["status"],
                "path": str(
                    state_root / "outbox" / f"{envelope['delivery_id']}.json"
                ),
                "keys": sorted(item["key"] for item in candidate["items"]),
            }
        )
    if build_result["input_items"] == 0 and build_result["reviewed_empty"]:
        status = "reviewed_empty"
    elif build_result["queued_items"] == 0:
        status = "not_queued"
    elif build_result["filtered_items"]:
        status = "queued_with_filtered_items"
    else:
        status = "queued"
    return {
        "status": status,
        "input_items": build_result["input_items"],
        "queued_items": build_result["queued_items"],
        "filtered_items": build_result["filtered_items"],
        "filter_reasons": build_result["filter_reasons"],
        "deliveries": deliveries,
    }


def _canonical_memory_json(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _memory_work_id(bundle: dict) -> str:
    identity = {
        "host_id": bundle["host_id"],
        "session_id": bundle["session_id"],
        "completed_cursor": bundle["completed_cursor"],
        "completed_prefix_sha256": bundle["completed_prefix_sha256"],
        "transcript_sha256": bundle["transcript_sha256"],
    }
    return "work:" + hashlib.sha256(_canonical_memory_json(identity)).hexdigest()


def _memory_source_identity(bundle: dict) -> dict:
    return {
        field: bundle.get(field)
        for field in (
            "source_host",
            "host_id",
            "session_id",
            "completed_cursor",
            "completed_at",
            "completed_prefix_sha256",
            "transcript_sha256",
            "completed_turn_id",
            "run_date",
        )
    }


def _memory_work_path(state_root: Path, work_id: str, directory: str) -> Path:
    if not isinstance(work_id, str) or MEMORY_WORK_ID_RE.fullmatch(work_id) is None:
        raise ValueError("automatic memory work_id is invalid")
    return state_root / directory / f"{work_id}.json"


def _read_memory_json(path: Path, label: str) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"{label} is unreadable or malformed") from exc
    if not isinstance(value, dict):
        raise RuntimeError(f"{label} must be an object")
    return value


def _read_memory_json_list(path: Path, label: str) -> list:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"{label} is unreadable or malformed") from exc
    if not isinstance(value, list):
        raise RuntimeError(f"{label} must be an array")
    return value


def _memory_instant(now: str | None, label: str) -> datetime:
    raw = now or datetime.now(timezone.utc).isoformat()
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError(f"{label} must be an ISO timestamp with timezone")
    try:
        parsed = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{label} must be an ISO timestamp with timezone") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{label} must be an ISO timestamp with timezone")
    return parsed.astimezone(timezone.utc)


def _load_memory_extraction_diagnostic(
    state_root: Path, work_id: str, bundle: dict
) -> dict:
    path = _memory_work_path(state_root, work_id, "extraction-diagnostics")
    diagnostic = _read_memory_json(path, "memory extraction diagnostic")
    expected_fields = {
        "schema_version",
        "work_id",
        "completed_prefix_sha256",
        "status",
        "reason",
        "attempt_count",
        "recorded_at",
        "retry_after_seconds",
        "next_retry_at",
        "queue_result",
    }
    if set(diagnostic) != expected_fields or diagnostic.get("schema_version") != 1:
        raise RuntimeError("memory extraction diagnostic shape is invalid")
    if (
        diagnostic.get("work_id") != work_id
        or diagnostic.get("completed_prefix_sha256")
        != bundle.get("completed_prefix_sha256")
        or diagnostic.get("reason")
        not in {"invalid_reviewed_extraction", "all_items_filtered"}
        or diagnostic.get("status") not in {"retryable", "needs_attention"}
        or type(diagnostic.get("attempt_count")) is not int
        or not 1 <= diagnostic["attempt_count"] <= MAX_MEMORY_EXTRACTION_ATTEMPTS
        or diagnostic.get("queue_result") is not None
        and not isinstance(diagnostic["queue_result"], dict)
    ):
        raise RuntimeError("memory extraction diagnostic identity is invalid")
    recorded_at = _memory_instant(
        diagnostic.get("recorded_at"), "memory extraction diagnostic recorded_at"
    )
    if diagnostic["status"] == "retryable":
        retry = diagnostic.get("retry_after_seconds")
        if (
            type(retry) is not int
            or retry <= 0
            or diagnostic["attempt_count"] >= MAX_MEMORY_EXTRACTION_ATTEMPTS
        ):
            raise RuntimeError("memory extraction retry diagnostic is invalid")
        next_retry = _memory_instant(
            diagnostic.get("next_retry_at"),
            "memory extraction diagnostic next_retry_at",
        )
        if next_retry != recorded_at + timedelta(seconds=retry):
            raise RuntimeError("memory extraction retry diagnostic is invalid")
    elif (
        diagnostic["attempt_count"] != MAX_MEMORY_EXTRACTION_ATTEMPTS
        or diagnostic.get("retry_after_seconds") is not None
        or diagnostic.get("next_retry_at") is not None
    ):
        raise RuntimeError("memory extraction terminal diagnostic is invalid")
    return diagnostic


def _memory_extraction_diagnostic_result(diagnostic: dict) -> dict:
    result = {
        "status": diagnostic["status"],
        "reason": diagnostic["reason"],
        "work_id": diagnostic["work_id"],
        "attempt_count": diagnostic["attempt_count"],
        "retry_after_seconds": diagnostic["retry_after_seconds"],
        "next_retry_at": diagnostic["next_retry_at"],
        "duplicate": False,
    }
    if diagnostic["queue_result"] is not None:
        result["queue_result"] = diagnostic["queue_result"]
    return result


def _record_memory_extraction_diagnostic(
    state_root: Path,
    work_id: str,
    bundle: dict,
    *,
    reason: str,
    attempted_at: datetime,
    queue_result: dict | None = None,
) -> dict:
    path = _memory_work_path(state_root, work_id, "extraction-diagnostics")
    attempts = 1
    if path.exists():
        previous = _load_memory_extraction_diagnostic(
            state_root, work_id, bundle
        )
        if previous["status"] == "needs_attention":
            return _memory_extraction_diagnostic_result(previous)
        attempts = previous["attempt_count"] + 1
    terminal = attempts >= MAX_MEMORY_EXTRACTION_ATTEMPTS
    retry_after = None if terminal else min(3600, 60 * (2 ** (attempts - 1)))
    recorded_at = attempted_at.isoformat(timespec="microseconds")
    diagnostic = {
        "schema_version": 1,
        "work_id": work_id,
        "completed_prefix_sha256": bundle["completed_prefix_sha256"],
        "status": "needs_attention" if terminal else "retryable",
        "reason": reason,
        "attempt_count": attempts,
        "recorded_at": recorded_at,
        "retry_after_seconds": retry_after,
        "next_retry_at": (
            None
            if retry_after is None
            else (attempted_at + timedelta(seconds=retry_after)).isoformat(
                timespec="microseconds"
            )
        ),
        "queue_result": queue_result,
    }
    _atomic_write_json(path, diagnostic)
    _load_memory_extraction_diagnostic(state_root, work_id, bundle)
    return _memory_extraction_diagnostic_result(diagnostic)


def _validate_memory_extraction_receipt(
    state_root: Path,
    work_id: str,
    bundle: dict,
    *,
    reviewed_sha256: str | None = None,
) -> dict:
    receipt_path = _memory_work_path(
        state_root, work_id, "extraction-receipts"
    )
    receipt = _read_memory_json(receipt_path, "memory extraction receipt")
    expected_fields = {
        "schema_version",
        "work_id",
        "completed_prefix_sha256",
        "reviewed_sha256",
        "extraction_artifact_relpath",
        "extraction_artifact_sha256",
        "recorded_at",
        "result",
    }
    if set(receipt) != expected_fields or receipt.get("schema_version") != 1:
        raise RuntimeError("memory extraction receipt shape is invalid")
    if (
        receipt.get("work_id") != work_id
        or receipt.get("completed_prefix_sha256")
        != bundle.get("completed_prefix_sha256")
        or not isinstance(receipt.get("reviewed_sha256"), str)
        or re.fullmatch(r"[0-9a-f]{64}", receipt["reviewed_sha256"]) is None
        or reviewed_sha256 is not None
        and receipt["reviewed_sha256"] != reviewed_sha256
        or not isinstance(receipt.get("extraction_artifact_sha256"), str)
        or re.fullmatch(r"[0-9a-f]{64}", receipt["extraction_artifact_sha256"])
        is None
    ):
        raise RuntimeError("memory extraction receipt identity is invalid")
    try:
        recorded_at = datetime.fromisoformat(receipt["recorded_at"])
    except (TypeError, ValueError) as exc:
        raise RuntimeError("memory extraction receipt timestamp is invalid") from exc
    if recorded_at.tzinfo is None or recorded_at.utcoffset() is None:
        raise RuntimeError("memory extraction receipt timestamp is invalid")

    artifact_relpath = receipt.get("extraction_artifact_relpath")
    if not isinstance(artifact_relpath, str):
        raise RuntimeError("memory extraction receipt artifact path is invalid")
    artifact_path = state_root / artifact_relpath
    expected_artifact_dir = state_root.resolve(strict=False) / "extraction-artifacts"
    if artifact_path.resolve(strict=False).parent != expected_artifact_dir:
        raise RuntimeError("memory extraction receipt artifact path escapes state")
    try:
        artifact_hash = hashlib.sha256(artifact_path.read_bytes()).hexdigest()
    except OSError as exc:
        raise RuntimeError("memory extraction artifact is missing") from exc
    if artifact_hash != receipt["extraction_artifact_sha256"]:
        raise RuntimeError("memory extraction artifact hash conflicts with receipt")
    artifact = _read_memory_json(artifact_path, "memory extraction artifact")
    if (
        hashlib.sha256(_canonical_memory_json(artifact)).hexdigest()
        != receipt["reviewed_sha256"]
    ):
        raise RuntimeError(
            "memory extraction artifact conflicts with reviewed hash"
        )
    try:
        expected_result = _reviewed_memory_bundle_result(
            bundle,
            artifact,
            run_date=bundle["run_date"],
        )
    except (KeyError, ValueError) as exc:
        raise RuntimeError(
            "memory extraction artifact no longer validates against its source"
        ) from exc
    expected_deliveries = sorted(
        (
            expected["delivery_id"],
            expected["content_id"],
        )
        for expected in (
            automatic_memory.make_envelope(candidate)
            for candidate in expected_result["bundles"]
        )
    )

    result = receipt.get("result")
    if not isinstance(result, dict) or set(result) != {
        "status",
        "work_id",
        "deliveries",
        "queue_result",
        "receipt_path",
    }:
        raise RuntimeError("memory extraction receipt result is invalid")
    if (
        result.get("work_id") != work_id
        or result.get("status") not in {"queued", "reviewed_empty"}
        or result.get("receipt_path") != str(receipt_path)
        or not isinstance(result.get("deliveries"), list)
        or not isinstance(result.get("queue_result"), dict)
        or result["queue_result"].get("deliveries") != result["deliveries"]
    ):
        raise RuntimeError("memory extraction receipt result is invalid")
    if result["status"] == "queued" and not result["deliveries"]:
        raise RuntimeError("memory extraction receipt has no durable delivery")
    if result["status"] == "reviewed_empty" and result["deliveries"]:
        raise RuntimeError("reviewed-empty receipt cannot bind a delivery")
    actual_deliveries: list[tuple[str, str]] = []
    for delivery in result["deliveries"]:
        if (
            not isinstance(delivery, dict)
            or not isinstance(delivery.get("delivery_id"), str)
            or not isinstance(delivery.get("content_id"), str)
        ):
            raise RuntimeError("memory extraction receipt delivery is invalid")
        try:
            envelope = automatic_memory.load_outbox_envelope(
                state_root, delivery["delivery_id"]
            )
        except (OSError, ValueError, RuntimeError) as exc:
            raise RuntimeError(
                "memory extraction receipt durable delivery is invalid"
            ) from exc
        if envelope["content_id"] != delivery["content_id"]:
            raise RuntimeError("memory extraction receipt delivery identity conflicts")
        actual_deliveries.append(
            (delivery["delivery_id"], delivery["content_id"])
        )
    if sorted(actual_deliveries) != expected_deliveries:
        raise RuntimeError(
            "memory extraction receipt source-derived delivery identity conflicts"
        )
    return receipt


def build_memory_worklist(
    *,
    state_root: Path,
    codex_root: Path,
    since: str,
    through: str,
    writer_role: str,
    limit: int = 10,
    now: str | None = None,
) -> dict:
    """Persist a bounded list of eligible immutable completed-turn sources."""

    role = _memory_writer_role(writer_role)
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("automatic memory worklist limit must be from 1 through 100")
    selection_at = _memory_instant(now, "automatic memory worklist timestamp")
    source_host = "home" if role == "home" else "sjm"
    discovered = discover_session_paths_between(
        since=since, through=through, codex_root=codex_root
    )
    requested_dates = set(_date_range(since, through))
    eligible: list[dict] = []
    excluded = 0
    parse_exception_counts: dict[str, int] = {}
    source_paths = sorted(
        {source_path for paths in discovered.values() for source_path in paths}
    )
    for source_path in source_paths:
        try:
            parsed = parse_completed_dialogue(
                source_path, source_host=source_host
            )
        except OSError:
            excluded += 1
            parse_exception_counts["source_unavailable"] = (
                parse_exception_counts.get("source_unavailable", 0) + 1
            )
            continue
        except ValueError:
            excluded += 1
            continue
        if not parsed.get("eligible"):
            excluded += 1
            continue
        for diagnostic in parsed.get("parse_diagnostics", []):
            reason = str(diagnostic.get("reason") or "unknown_parse_diagnostic")
            parse_exception_counts[reason] = parse_exception_counts.get(reason, 0) + 1
        prefixes = [
            prefix
            for prefix in parsed.get("completed_prefixes", [])
            if prefix.get("mode") == "turn"
            and prefix.get("completed_date") in requested_dates
        ]
        for prefix in prefixes:
            try:
                bundle = build_source_bundle(
                    source_path,
                    source_host=source_host,
                    codex_root=codex_root,
                    run_date=prefix["completed_date"],
                    completed_cursor=prefix["cursor"],
                )
            except OSError:
                excluded += 1
                parse_exception_counts["source_unavailable"] = (
                    parse_exception_counts.get("source_unavailable", 0) + 1
                )
                break
            except ValueError:
                excluded += 1
                continue
            if (
                bundle.get("completed_prefix_mode") != "turn"
                or bundle.get("provenance_status") != "known_user"
                or bundle.get("thread_source") not in INTERACTIVE_THREAD_SOURCES
            ):
                excluded += 1
                continue
            eligible.append(bundle)
    eligible.sort(
        key=lambda row: (
            datetime.fromisoformat(_completed_at_iso(row["completed_at"]).replace("Z", "+00:00")),
            row["host_id"],
            row["session_id"],
            row["completed_cursor"],
        )
    )

    selected: list[dict] = []
    skipped_receipted = 0
    exception_counts = {"needs_attention": 0, "retry_backoff": 0}
    lock_path = state_root / ".memory-worklist.lock"
    with _file_lock(lock_path):
        for current_bundle in eligible:
            work_id = _memory_work_id(current_bundle)
            receipt_path = _memory_work_path(
                state_root, work_id, "extraction-receipts"
            )
            bundle_path = _memory_work_path(state_root, work_id, "source-bundles")
            record_path = _memory_work_path(state_root, work_id, "worklist")
            if bundle_path.exists():
                bundle = _read_memory_json(bundle_path, "memory source bundle")
                if (
                    _memory_work_id(bundle) != work_id
                    or _memory_source_identity(bundle)
                    != _memory_source_identity(current_bundle)
                ):
                    raise RuntimeError("memory source bundle identity conflicts")
            else:
                bundle = current_bundle
                _atomic_write_json(bundle_path, bundle)
            record = {
                "schema_version": 1,
                "work_id": work_id,
                "writer_role": role,
                "source_host": source_host,
                "session_path": bundle["session_path"],
                "run_date": bundle["run_date"],
                "host_id": bundle["host_id"],
                "session_id": bundle["session_id"],
                "completed_cursor": bundle["completed_cursor"],
                "completed_at": bundle["completed_at"],
                "completed_prefix_sha256": bundle["completed_prefix_sha256"],
                "transcript_sha256": bundle["transcript_sha256"],
                "source_bundle_relpath": str(bundle_path.relative_to(state_root)),
                "status": "pending_extraction",
            }
            if record_path.exists():
                if _read_memory_json(record_path, "memory work record") != record:
                    raise RuntimeError("memory work record identity conflicts")
            else:
                _atomic_write_json(record_path, record)
            if receipt_path.exists():
                _validate_memory_extraction_receipt(
                    state_root, work_id, bundle
                )
                skipped_receipted += 1
                continue
            diagnostic_path = _memory_work_path(
                state_root, work_id, "extraction-diagnostics"
            )
            if diagnostic_path.exists():
                diagnostic = _load_memory_extraction_diagnostic(
                    state_root, work_id, bundle
                )
                if diagnostic["status"] == "needs_attention":
                    exception_counts["needs_attention"] += 1
                    continue
                retry_at = _memory_instant(
                    diagnostic["next_retry_at"],
                    "memory extraction diagnostic next_retry_at",
                )
                if selection_at < retry_at:
                    exception_counts["retry_backoff"] += 1
                    continue
            selected.append(
                {
                    "work_id": work_id,
                    "session_path": bundle["session_path"],
                    "completed_at": bundle["completed_at"],
                    "completed_cursor": bundle["completed_cursor"],
                    "source_bundle_path": str(bundle_path),
                    "work_record_path": str(record_path),
                }
            )
            if len(selected) == limit:
                break
    has_exceptions = any(exception_counts.values()) or any(
        parse_exception_counts.values()
    )
    if selected:
        status = "ready_with_exceptions" if has_exceptions else "ready"
    elif exception_counts["needs_attention"] or parse_exception_counts:
        status = "needs_attention"
    elif exception_counts["retry_backoff"]:
        status = "waiting_retry"
    else:
        status = "idle"
    return {
        "status": status,
        "writer_role": role,
        "selected": len(selected),
        "excluded": excluded,
        "skipped_receipted": skipped_receipted,
        "exception_counts": exception_counts,
        "parse_exception_counts": parse_exception_counts,
        "items": selected,
    }


def prepare_memory_extraction_input(
    *, state_root: Path, work_id: str, writer_role: str,
) -> dict:
    """Project one complete turn plus bounded context; keep source/receipts intact."""
    role = _memory_writer_role(writer_role)
    record = _read_memory_json(
        _memory_work_path(state_root, work_id, "worklist"), "memory work record"
    )
    if (record.get("work_id") != work_id or record.get("writer_role") != role
            or record.get("schema_version") != 1
            or record.get("status") != "pending_extraction"):
        raise ValueError("memory work record is invalid or belongs to another role")
    source = _memory_work_path(state_root, work_id, "source-bundles")
    if record.get("source_bundle_relpath") != str(source.relative_to(state_root)):
        raise ValueError("memory source bundle path does not match work record")
    bundle = _read_memory_json(source, "memory source bundle")
    _validate_modern_artifact_binding(bundle, label="memory source", require_chunks=True)
    if (_memory_work_id(bundle) != work_id
            or bundle.get("completed_prefix_sha256") != record.get("completed_prefix_sha256")
            or hashlib.sha256(bundle["transcript"].encode()).hexdigest() != bundle["transcript_sha256"]):
        raise ValueError("memory source bundle does not match work record")
    events = bundle["dialogue_events"]
    current = [e for e in events if e["turn_id"] == bundle["completed_turn_id"]]
    if not current:
        raise ValueError("memory source has no completed-turn dialogue")
    # This is an input view, never a replacement source bundle. All current-turn
    # evidence is retained verbatim; prior turns remain separately eligible work.
    view = {k: bundle[k] for k in (
        "raw_source_sha256", "transcript_sha256", "completed_cursor",
        "completed_prefix_sha256", "completed_prefix_mode", "completed_date",
        "completed_at", "completed_turn_id", "host_id", "session_id",
    )}
    prior = events[:events.index(current[0])]
    view.update(input_schema_version=1, work_id=work_id, dialogue_events=current,
                context_events=[], context_omitted_events=len(prior))

    def byte_size(value: dict) -> int:
        return len((json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode())

    size = byte_size(view)
    if size > 48000:
        return {"status": "input_budget_exceeded", "work_id": work_id,
                "input_bytes": size, "limit_bytes": 48000}
    # Add a contiguous suffix of whole prior events, never fragments or a
    # misleading selection with holes. Context gets at most 16 KB of the cap.
    context_bytes = 0
    for event in reversed(prior):
        event_bytes = len(_canonical_memory_json(event))
        if context_bytes + event_bytes > 16000:
            break
        view["context_events"].insert(0, event)
        view["context_omitted_events"] -= 1
        if byte_size(view) > 48000:
            view["context_events"].pop(0)
            view["context_omitted_events"] += 1
            break
        context_bytes += event_bytes
    target = _memory_work_path(state_root, work_id, "extraction-inputs")
    _atomic_write_json(target, view)
    return {"status": "ready", "work_id": work_id, "input_path": str(target),
            "input_bytes": byte_size(view), "limit_bytes": 48000,
            "context_omitted_events": view["context_omitted_events"]}


def record_memory_extraction(
    *,
    state_root: Path,
    work_id: str,
    reviewed: object,
    writer_role: str,
    now: str | None = None,
) -> dict:
    """Validate a native extraction and receipt its durable queue outcome."""

    with _file_lock(state_root / ".memory-extraction.lock"):
        return _record_memory_extraction_locked(
            state_root=state_root,
            work_id=work_id,
            reviewed=reviewed,
            writer_role=writer_role,
            now=now,
        )


def _record_memory_extraction_locked(
    *,
    state_root: Path,
    work_id: str,
    reviewed: object,
    writer_role: str,
    now: str | None = None,
) -> dict:

    role = _memory_writer_role(writer_role)
    attempted_at = _memory_instant(now, "automatic memory extraction timestamp")
    record_path = _memory_work_path(state_root, work_id, "worklist")
    record = _read_memory_json(record_path, "memory work record")
    if (
        record.get("schema_version") != 1
        or record.get("work_id") != work_id
        or record.get("writer_role") != role
        or record.get("status") != "pending_extraction"
    ):
        raise RuntimeError("memory work record is invalid or belongs to another role")
    bundle_path = state_root / str(record.get("source_bundle_relpath", ""))
    expected_root = state_root.resolve(strict=False)
    if bundle_path.resolve(strict=False).parent != (expected_root / "source-bundles"):
        raise RuntimeError("memory source bundle path escapes its state root")
    bundle = _read_memory_json(bundle_path, "memory source bundle")
    if (
        bundle.get("completed_prefix_sha256")
        != record.get("completed_prefix_sha256")
        or _memory_work_id(bundle) != work_id
    ):
        raise RuntimeError("memory source bundle does not match work record")
    caller_reviewed_sha256 = hashlib.sha256(
        _canonical_memory_json(reviewed)
    ).hexdigest()
    receipt_path = _memory_work_path(
        state_root, work_id, "extraction-receipts"
    )
    if receipt_path.exists():
        receipt = _validate_memory_extraction_receipt(
            state_root,
            work_id,
            bundle,
        )
        if receipt["reviewed_sha256"] != caller_reviewed_sha256:
            raise ValueError(
                "completed memory extraction conflicts with existing receipt: "
                f"{receipt_path}"
            )
        return {**receipt["result"], "duplicate": True}

    diagnostic_path = _memory_work_path(
        state_root, work_id, "extraction-diagnostics"
    )
    if diagnostic_path.exists():
        diagnostic = _load_memory_extraction_diagnostic(
            state_root, work_id, bundle
        )
        if diagnostic["status"] == "needs_attention":
            return _memory_extraction_diagnostic_result(diagnostic)
        retry_at = _memory_instant(
            diagnostic["next_retry_at"],
            "memory extraction diagnostic next_retry_at",
        )
        if attempted_at < retry_at:
            waiting = _memory_extraction_diagnostic_result(diagnostic)
            waiting["status"] = "retry_backoff"
            return waiting

    artifact_path = _memory_work_path(
        state_root, work_id, "extraction-artifacts"
    )
    if artifact_path.exists():
        effective_reviewed: object = _read_memory_json(
            artifact_path, "memory extraction artifact"
        )
    else:
        effective_reviewed = reviewed
    try:
        build_result = _reviewed_memory_bundle_result(
            bundle,
            effective_reviewed,
            run_date=record["run_date"],
        )
    except ValueError:
        return _record_memory_extraction_diagnostic(
            state_root,
            work_id,
            bundle,
            reason="invalid_reviewed_extraction",
            attempted_at=attempted_at,
        )
    if (
        not artifact_path.exists()
        and build_result["input_items"] > 0
        and build_result["queued_items"] == 0
    ):
        queue_result = {
            "status": "not_queued",
            "input_items": build_result["input_items"],
            "queued_items": 0,
            "filtered_items": build_result["filtered_items"],
            "filter_reasons": build_result["filter_reasons"],
            "deliveries": [],
        }
        return _record_memory_extraction_diagnostic(
            state_root,
            work_id,
            bundle,
            reason="all_items_filtered",
            attempted_at=attempted_at,
            queue_result=queue_result,
        )
    if not artifact_path.exists():
        _atomic_write_json(artifact_path, effective_reviewed)
    effective_reviewed = _read_memory_json(
        artifact_path, "memory extraction artifact"
    )
    reviewed_sha256 = hashlib.sha256(
        _canonical_memory_json(effective_reviewed)
    ).hexdigest()
    queue_result = queue_reviewed_memory(
        bundle,
        effective_reviewed,
        state_root=state_root,
        run_date=record["run_date"],
        writer_role=role,
    )

    if queue_result["status"] == "not_queued":
        return _record_memory_extraction_diagnostic(
            state_root,
            work_id,
            bundle,
            reason="all_items_filtered",
            attempted_at=attempted_at,
            queue_result=queue_result,
        )

    public_result = {
        "status": (
            "reviewed_empty"
            if queue_result["status"] == "reviewed_empty"
            else "queued"
        ),
        "work_id": work_id,
        "deliveries": queue_result["deliveries"],
        "queue_result": queue_result,
        "receipt_path": str(receipt_path),
        "duplicate": False,
    }
    artifact_sha256 = hashlib.sha256(artifact_path.read_bytes()).hexdigest()
    receipt = {
        "schema_version": 1,
        "work_id": work_id,
        "completed_prefix_sha256": record["completed_prefix_sha256"],
        "reviewed_sha256": reviewed_sha256,
        "extraction_artifact_relpath": str(artifact_path.relative_to(state_root)),
        "extraction_artifact_sha256": artifact_sha256,
        "recorded_at": attempted_at.isoformat(timespec="microseconds"),
        "result": {key: value for key, value in public_result.items() if key != "duplicate"},
    }
    _atomic_write_json(receipt_path, receipt)
    _validate_memory_extraction_receipt(
        state_root,
        work_id,
        bundle,
        reviewed_sha256=reviewed_sha256,
    )
    if diagnostic_path.exists():
        diagnostic_path.unlink()
        _fsync_dir(diagnostic_path.parent)
    return public_result


def deliver_memory_outbox(
    state_root: Path,
    accept_func: Callable[[dict], dict],
    *,
    limit: int = 100,
    now: str | None = None,
) -> dict:
    """Attempt bounded delivery through an injected, explicitly authorized boundary."""
    summary = {
        "accepted": 0,
        "failed": 0,
        "offline": 0,
        "exhausted": 0,
        "local_ack_failed": 0,
        "local_state_failed": 0,
    }

    def count_terminal_or_local_failure(
        delivery_id: str, *, local_failure: str
    ) -> None:
        try:
            current = automatic_memory.load_outbox_envelope(
                state_root, delivery_id
            )
        except (OSError, ValueError, RuntimeError):
            summary[local_failure] += 1
            return
        if current["status"] in {"accepted", "exhausted"}:
            summary[current["status"]] += 1
        else:
            summary[local_failure] += 1

    def record_failure(delivery_id: str, *, outcome: str, error_code: str) -> None:
        try:
            updated = automatic_memory.record_delivery_attempt(
                state_root,
                delivery_id,
                outcome=outcome,
                error_code=error_code,
                now=now,
            )
        except (OSError, ValueError, RuntimeError):
            count_terminal_or_local_failure(
                delivery_id, local_failure="local_state_failed"
            )
            return
        summary[updated["status"]] += 1

    for envelope in automatic_memory.list_deliverable(
        state_root, limit=limit, now=now
    ):
        delivery_id = envelope["delivery_id"]
        try:
            receipt = accept_func(envelope)
            if (
                not isinstance(receipt, dict)
                or set(receipt) != {
                    "status",
                    "delivery_id",
                    "content_id",
                    "duplicate",
                }
                or receipt.get("status") != "accepted_not_reconciled"
                or receipt.get("delivery_id") != delivery_id
                or receipt.get("content_id") != envelope["content_id"]
                or type(receipt.get("duplicate")) is not bool
            ):
                raise automatic_memory.ValidationError(
                    "Home acceptance boundary returned an invalid receipt"
                )
        except (ConnectionError, TimeoutError, OSError):
            record_failure(
                delivery_id,
                outcome="offline",
                error_code="home_unreachable",
            )
        except (automatic_memory.ValidationError, automatic_memory.StateCorruptionError, ValueError, RuntimeError):
            record_failure(
                delivery_id,
                outcome="failed",
                error_code="home_acceptance_failed",
            )
        else:
            try:
                updated = automatic_memory.record_delivery_attempt(
                    state_root, delivery_id, outcome="accepted", now=now
                )
            except (OSError, ValueError, RuntimeError):
                count_terminal_or_local_failure(
                    delivery_id, local_failure="local_ack_failed"
                )
            else:
                summary[updated["status"]] += 1
    return summary


def _validate_memory_acceptance_ack(envelope: dict, receipt: object) -> dict:
    if (
        not isinstance(receipt, dict)
        or set(receipt)
        != {"status", "delivery_id", "content_id", "duplicate"}
        or receipt.get("status") != "accepted_not_reconciled"
        or receipt.get("delivery_id") != envelope["delivery_id"]
        or receipt.get("content_id") != envelope["content_id"]
        or type(receipt.get("duplicate")) is not bool
    ):
        raise automatic_memory.ValidationError(
            "Home acceptance boundary returned an invalid receipt"
        )
    return dict(receipt)


def list_memory_deliverables(
    state_root: Path,
    *,
    writer_role: str,
    limit: int = 100,
    now: str | None = None,
) -> dict:
    if _memory_writer_role(writer_role) != "sjm-source-only":
        raise PermissionError("delivery export requires the SJM source-only role")
    envelopes = automatic_memory.list_deliverable(
        state_root, limit=limit, now=now
    )
    return {
        "status": "ready" if envelopes else "idle",
        "deliveries": [
            {
                "delivery_id": envelope["delivery_id"],
                "content_id": envelope["content_id"],
                "status": envelope["status"],
                "attempt_count": envelope["attempt_count"],
                "last_attempt_at": envelope["last_attempt_at"],
                "retry_after_seconds": envelope["retry_after_seconds"],
            }
            for envelope in envelopes
        ],
    }


def export_memory_envelope(
    state_root: Path,
    delivery_id: str,
    *,
    writer_role: str,
    now: str | None = None,
) -> dict:
    if _memory_writer_role(writer_role) != "sjm-source-only":
        raise PermissionError("delivery export requires the SJM source-only role")
    for envelope in automatic_memory.list_deliverable(
        state_root, limit=1000, now=now
    ):
        if envelope["delivery_id"] == delivery_id:
            return envelope
    raise ValueError("delivery is not currently eligible for export")


def record_memory_acknowledgement(
    state_root: Path,
    acknowledgement: object,
    *,
    writer_role: str,
    now: str | None = None,
) -> dict:
    if _memory_writer_role(writer_role) != "sjm-source-only":
        raise PermissionError("delivery acknowledgement requires the SJM source-only role")
    if not isinstance(acknowledgement, dict):
        raise automatic_memory.ValidationError(
            "Home acceptance boundary returned an invalid receipt"
        )
    delivery_id = acknowledgement.get("delivery_id")
    if not isinstance(delivery_id, str):
        raise automatic_memory.ValidationError(
            "Home acceptance boundary returned an invalid receipt"
        )
    envelope = automatic_memory.load_outbox_envelope(state_root, delivery_id)
    _validate_memory_acceptance_ack(envelope, acknowledgement)
    updated = automatic_memory.record_delivery_attempt(
        state_root, delivery_id, outcome="accepted", now=now
    )
    return {
        "status": updated["status"],
        "delivery_id": updated["delivery_id"],
        "content_id": updated["content_id"],
        "attempt_count": updated["attempt_count"],
    }


MemoryTransport = Callable[[list[str], object | None], object]


def _default_sjm_memory_transport(
    args: list[str], input_payload: object | None = None
) -> object:
    if not args or not all(isinstance(arg, str) and "\x00" not in arg for arg in args):
        raise ValueError("SJM memory transport arguments are invalid")
    remote_command = (
        f'exec "$HOME/{SJM_MEMORY_REMOTE_PYTHON_REL}" '
        f'"$HOME/{SJM_MEMORY_REMOTE_COMMAND_REL}" {shlex.join(args)}'
    )
    command = [
        "/usr/bin/ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=10",
        SJM_MEMORY_SSH_ALIAS,
        remote_command,
    ]
    input_text = (
        None
        if input_payload is None
        else json.dumps(input_payload, ensure_ascii=False, sort_keys=True) + "\n"
    )
    try:
        completed = subprocess.run(
            command,
            input=input_text,
            text=True,
            capture_output=True,
            timeout=DEFAULT_MEMORY_TRANSPORT_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ConnectionError("SJM memory transport is unavailable") from exc
    if completed.returncode != 0:
        raise ConnectionError("SJM memory transport returned a nonzero status")
    if len(completed.stdout.encode("utf-8")) > MAX_MEMORY_TRANSPORT_STDOUT_BYTES:
        raise automatic_memory.ValidationError(
            "SJM memory transport exceeded its output limit"
        )
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise automatic_memory.ValidationError(
            "SJM memory transport returned malformed JSON"
        ) from exc


def pull_sjm_memory(
    home_root: Path,
    *,
    writer_role: str,
    transport_func: MemoryTransport | None = None,
    limit: int = 100,
    now: str | None = None,
) -> dict:
    if _memory_writer_role(writer_role) != "home":
        raise PermissionError("SJM memory pull requires the Home writer")
    transport = transport_func or _default_sjm_memory_transport
    summary = {
        "accepted": 0,
        "duplicate_acceptances": 0,
        "ack_failed": 0,
        "failed": 0,
        "offline": 0,
        "delivery_ids": [],
    }
    list_args = [
        "memory-list-deliverable",
        "--writer-role",
        "sjm-source-only",
        "--limit",
        str(limit),
    ]
    if now is not None:
        list_args.extend(["--now", now])
    try:
        listing = transport(list_args, None)
    except (ConnectionError, TimeoutError, OSError):
        summary["offline"] = 1
        return summary
    if (
        not isinstance(listing, dict)
        or listing.get("status") not in {"ready", "idle"}
        or not isinstance(listing.get("deliveries"), list)
    ):
        raise automatic_memory.ValidationError(
            "SJM delivery listing is malformed"
        )
    if len(listing["deliveries"]) > limit:
        raise automatic_memory.ValidationError(
            "SJM delivery listing exceeds requested limit"
        )
    for item in listing["deliveries"]:
        if (
            not isinstance(item, dict)
            or not isinstance(item.get("delivery_id"), str)
            or not isinstance(item.get("content_id"), str)
        ):
            raise automatic_memory.ValidationError(
                "SJM delivery listing item is malformed"
            )
        delivery_id = item["delivery_id"]
        export_args = [
            "memory-export-envelope",
            "--writer-role",
            "sjm-source-only",
            "--delivery-id",
            delivery_id,
        ]
        if now is not None:
            export_args.extend(["--now", now])
        try:
            envelope = transport(export_args, None)
            if (
                not isinstance(envelope, dict)
                or envelope.get("delivery_id") != delivery_id
                or envelope.get("content_id") != item["content_id"]
            ):
                raise automatic_memory.ValidationError(
                    "SJM exported envelope identity does not match listing"
                )
            acknowledgement = accept_memory_envelope(
                home_root, envelope, writer_role="home"
            )
        except (ConnectionError, TimeoutError, OSError):
            summary["offline"] += 1
            continue
        except (ValueError, RuntimeError):
            summary["failed"] += 1
            continue
        ack_args = [
            "memory-record-ack",
            "--writer-role",
            "sjm-source-only",
            "--ack-stdin",
        ]
        if now is not None:
            ack_args.extend(["--now", now])
        try:
            recorded = transport(ack_args, acknowledgement)
        except (ConnectionError, TimeoutError, OSError):
            summary["ack_failed"] += 1
            continue
        if (
            not isinstance(recorded, dict)
            or recorded.get("status") != "accepted"
            or recorded.get("delivery_id") != delivery_id
            or recorded.get("content_id") != item["content_id"]
        ):
            summary["ack_failed"] += 1
            continue
        summary["accepted"] += 1
        summary["duplicate_acceptances"] += int(
            acknowledgement["duplicate"] is True
        )
        summary["delivery_ids"].append(delivery_id)
    return summary


def drain_home_memory(
    state_root: Path,
    *,
    writer_role: str,
    pull_sjm: bool = False,
    transport_func: MemoryTransport | None = None,
    limit: int = 100,
    now: str | None = None,
    memory_path: Path | None = None,
    publish: bool = False,
    vault_root: Path | None = None,
    preflight: Callable[[Path], dict] | None = None,
    publisher_func: Callable[..., dict] | None = None,
) -> dict:
    """Resume bounded acceptance/reconciliation without scheduling or extraction."""

    if _memory_writer_role(writer_role) != "home":
        raise PermissionError("memory drain requires the Home writer")
    if type(pull_sjm) is not bool or type(publish) is not bool:
        raise ValueError("memory drain flags must be booleans")
    if type(limit) is not int or not 1 <= limit <= 1000:
        raise ValueError("memory drain limit must be from 1 through 1000")

    local_delivery = deliver_memory_outbox(
        state_root,
        lambda envelope: accept_memory_envelope(
            state_root, envelope, writer_role="home"
        ),
        limit=limit,
        now=now,
    )
    sjm_delivery = (
        pull_sjm_memory(
            state_root,
            writer_role="home",
            transport_func=transport_func,
            limit=limit,
            now=now,
        )
        if pull_sjm
        else {
            "accepted": 0,
            "duplicate_acceptances": 0,
            "ack_failed": 0,
            "failed": 0,
            "offline": 0,
            "delivery_ids": [],
        }
    )

    reconciled = 0
    replayed = 0
    acceptance_paths = sorted((state_root / "acceptance").glob("delivery:*.json"))
    pending_paths = [
        path
        for path in acceptance_paths
        if not (state_root / "reconciliation" / path.name).exists()
    ]
    replay_paths = [path for path in acceptance_paths if path not in pending_paths]
    selected_paths = [*pending_paths, *replay_paths][:limit]
    for acceptance_path in selected_paths:
        result = reconcile_memory_delivery(
            state_root,
            acceptance_path.stem,
            writer_role="home",
            memory_path=memory_path,
        )
        if result["duplicate"]:
            replayed += 1
        else:
            reconciled += 1

    publication: object = "disabled"
    if publish:
        if vault_root is None:
            raise ValueError("memory publication requires an explicit vault root")
        publication = publish_memory_state(
            memory_state_path=memory_path or state_root / "memory.json",
            state_root=state_root,
            vault_root=vault_root,
            writer_role="home",
            preflight=preflight,
            publisher_func=publisher_func,
        )
    return {
        "status": "reconciled" if reconciled or replayed else "idle",
        "local_delivery": local_delivery,
        "sjm_delivery": sjm_delivery,
        "reconciled": reconciled,
        "replayed": replayed,
        "publication": publication,
    }


def publish_memory_state(
    memory_state_path: Path,
    state_root: Path,
    vault_root: Path,
    writer_role: str,
    *,
    preflight: Callable[[Path], dict] | None = None,
    publisher_func: Callable[..., dict] | None = None,
) -> dict:
    """Lazy boundary to the separately reviewed Home publication adapter."""

    if _memory_writer_role(writer_role) != "home":
        raise PermissionError("memory publication requires the Home writer")
    if publisher_func is None:
        import automatic_memory_publisher

        publisher_func = automatic_memory_publisher.publish
    return publisher_func(
        memory_state_path=memory_state_path,
        state_root=state_root,
        vault_root=vault_root,
        writer_role="home",
        preflight=preflight,
    )


def accept_memory_envelope(
    home_root: Path, envelope: object, *, writer_role: str
) -> dict:
    if _memory_writer_role(writer_role) != "home":
        raise PermissionError("memory acceptance requires the home writer")
    return automatic_memory.accept_home_envelope(home_root, envelope)


def reconcile_memory_delivery(
    home_root: Path,
    delivery_id: str,
    *,
    writer_role: str,
    memory_path: Path | None = None,
) -> dict:
    """Apply one accepted envelope and durably receipt replay-safe reconciliation."""
    if _memory_writer_role(writer_role) != "home":
        raise PermissionError("memory reconciliation requires the home writer")
    target = memory_path or home_root / "memory.json"
    target_identity = hashlib.sha256(
        str(target.expanduser().resolve(strict=False)).encode("utf-8")
    ).hexdigest()
    receipt_path = home_root / "reconciliation" / f"{delivery_id}.json"
    with _file_lock(home_root / ".reconciliation.lock"):
        envelope = automatic_memory.load_home_envelope(home_root, delivery_id)
        if receipt_path.exists():
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            expected_keys = sorted(
                {item["key"] for item in envelope["bundle"]["items"]}
            )
            if (
                not isinstance(receipt, dict)
                or set(receipt) != {
                    "schema_version",
                    "delivery_id",
                    "content_id",
                    "memory_target_sha256",
                    "status",
                    "applied",
                    "duplicates",
                    "keys",
                }
                or type(receipt.get("schema_version")) is not int
                or receipt.get("schema_version") != 1
                or receipt.get("delivery_id") != delivery_id
                or receipt.get("content_id") != envelope["content_id"]
                or not isinstance(receipt.get("memory_target_sha256"), str)
                or re.fullmatch(r"[0-9a-f]{64}", receipt["memory_target_sha256"])
                is None
                or receipt.get("status") != "reconciled"
                or type(receipt.get("applied")) is not int
                or receipt["applied"] < 0
                or type(receipt.get("duplicates")) is not int
                or receipt["duplicates"] < 0
                or receipt["applied"] + receipt["duplicates"]
                != len(envelope["bundle"]["items"])
                or receipt.get("keys") != expected_keys
            ):
                raise automatic_memory.StateCorruptionError(
                    "Home reconciliation receipt is malformed"
                )
            if receipt["memory_target_sha256"] != target_identity:
                raise ValueError(
                    "reconciliation receipt belongs to a different memory target"
                )
            verification = automatic_memory.apply_reviewed_envelope(target, envelope)
            return {
                **receipt,
                "duplicate": True,
                "repair_applied": verification["applied"],
            }
        result = automatic_memory.apply_reviewed_envelope(target, envelope)
        receipt = {
            "schema_version": 1,
            "delivery_id": delivery_id,
            "content_id": envelope["content_id"],
            "memory_target_sha256": target_identity,
            "status": "reconciled",
            **result,
        }
        _atomic_write_json(receipt_path, receipt)
    return {**receipt, "duplicate": False}


def recall_memory(
    state_root: Path,
    key: str,
    *,
    writer_role: str,
    provisional_root: Path | None = None,
    memory_path: Path | None = None,
) -> dict:
    role = _memory_writer_role(writer_role)
    if role == "sjm-source-only" and provisional_root is None:
        provisional_root = state_root
    return automatic_memory.recall(
        memory_path or state_root / "memory.json",
        key,
        provisional_outbox=provisional_root,
        read_only=role == "sjm-source-only",
    )


MEMORY_RUN_MAX_TURNS = 12
MEMORY_RUN_MAX_INPUT_BYTES = 240000
MEMORY_RUN_MAX_SECONDS = 1200
MEMORY_RUN_PER_TURN_BYTES = 48000
MEMORY_RUN_DEFER_REASONS = ("byte_budget", "time_budget", "turn_budget")
# The prompt stops selecting new turns after 18 minutes, so a time_budget
# deferral is only honest once that selection cutoff has actually elapsed.
MEMORY_RUN_SELECTION_CUTOFF_SECONDS = 1080
# Fixed, sanitized reason codes allowed into a canonical receipt. Any other
# reason text is normalized to "redacted_reason" so free text is never stored.
CANONICAL_EXCEPTION_REASONS = {
    "quota_exhausted", "retry_backoff", "reviewed_empty", "per_turn_failure",
}


class _MemoryRunError(Exception):
    """One failed native run step, identified by a stable diagnostic token.

    The token is chosen at the raise site and never carries transcript,
    credential, path or free-text error content, so it is safe to persist as
    the durable diagnostic of a failed run receipt.
    """

    def __init__(self, diagnostic: str) -> None:
        super().__init__(diagnostic)
        self.diagnostic = diagnostic


def _memory_diagnostic_token(value: object) -> str:
    token = re.sub(r"[^a-z0-9]+", "_", str(value).strip().lower()).strip("_")
    return (token or "unspecified")[:80]


def _memory_run_receipt_path(state_root: Path, finished: datetime) -> Path:
    directory = state_root / "native-run-receipts"
    base = finished.strftime("%Y%m%dT%H%M%S%fZ")
    for suffix in ("", *(f"-{index}" for index in range(1, 100))):
        candidate = directory / f"{base}{suffix}.json"
        if not candidate.exists():
            return candidate
    raise RuntimeError("native run receipt directory is saturated")


def record_failed_native_memory_run(
    *, state_root: Path, writer_role: str, started_at: str,
    diagnostic: str = "run_evidence_invalid", work_ids: list[str] | None = None,
) -> dict:
    """Write durable failed run evidence once the role and start are valid.

    Used for receipt validation failures and for canonical command JSON the
    caller cannot read. It never fabricates success and never persists
    transcript, credential or free-text error content, only a stable token.
    """
    role = _memory_writer_role(writer_role)
    started = _memory_instant(started_at, "run start")
    finished = datetime.now(timezone.utc)
    if started > finished:
        raise ValueError("run start is in the future")
    selected = list(dict.fromkeys(
        item for item in work_ids
        if isinstance(item, str) and MEMORY_WORK_ID_RE.fullmatch(item)
    ))[:100] if isinstance(work_ids, list) else []
    token = _memory_diagnostic_token(diagnostic)
    data = {
        "schema_version": 1, "automation_id": native_job_status.JOB_IDS[role],
        "writer_role": role, "started_at": started.isoformat(),
        "finished_at": finished.isoformat(),
        "commands": {
            "memory_worklist": {"status": "idle", "selected": 0},
            # Dedicated run-level failure evidence. A bookkeeping or
            # command-JSON failure can happen after a genuinely completed
            # extraction, so this receipt carries no per-turn extraction
            # command and never claims an invented failure count.
            "native_run_bookkeeping": {"status": "failed"},
        },
        "selected_work_ids": selected, "exceptions": [], "diagnostic": token,
    }
    _, _, outcome, _ = native_job_status._classify_receipt(
        data, job_id=data["automation_id"], writer_role=role)
    if outcome != "failed":
        raise RuntimeError("failed native run evidence violates the receipt contract")
    path = _memory_run_receipt_path(state_root, finished)
    _atomic_write_json(path, data)
    return {"status": "failed", "outcome": "failed", "diagnostic": token,
            "completed": None, "failed": None, "receipt_path": str(path)}


def _normalize_run_exception(item: dict) -> dict:
    """Copy one exception into a receipt-safe canonical shape.

    Free-text channels are closed here: a retry reason outside the fixed code
    set becomes "redacted_reason", and a hold work_id must match the real
    worklist syntax. Historical readers keep their existing compatibility.
    """
    if "reason" in item:
        reason = item["reason"]
        return {**item, "reason": reason if reason in CANONICAL_EXCEPTION_REASONS
                else "redacted_reason"}
    if item.get("type") == "input_budget_exceeded":
        work_id = item.get("work_id")
        if not isinstance(work_id, str) or MEMORY_WORK_ID_RE.fullmatch(work_id) is None:
            raise _MemoryRunError("exception_work_id_invalid")
    return dict(item)


def _validated_run_exceptions(exceptions: object) -> list:
    """Accept only structured, schema-v1 exception or hold entries.

    Free-text strings are rejected: they could carry transcript or credential
    content, and the receipt must stay sanitized. Any malformed entry fails
    the run rather than being dropped.
    """
    if exceptions is None:
        return []
    if not isinstance(exceptions, list) or len(exceptions) > native_job_status.MAX_EXCEPTION_ITEMS:
        raise _MemoryRunError("exceptions_malformed")
    validated = []
    for item in exceptions:
        if not isinstance(item, dict) or native_job_status._classify_exception(item) is None:
            raise _MemoryRunError("exceptions_malformed")
        validated.append(_normalize_run_exception(item))
    return validated


def _build_native_run_receipt(
    *, state_root: Path, role: str, started: datetime, finished: datetime,
    work_ids: object, deferred_work_ids: object, deferred_reason: str,
    exceptions: object, drain_result: dict | None, publication_result: dict | None,
) -> tuple[dict, dict]:
    """Assemble and validate one canonical run receipt."""
    if deferred_reason not in MEMORY_RUN_DEFER_REASONS:
        raise _MemoryRunError("deferred_reason_invalid")
    if not isinstance(work_ids, list) or not all(isinstance(item, str) for item in work_ids):
        raise _MemoryRunError("selected_work_ids_malformed")
    # Duplicate IDs are idempotent: a repeated --work-id never aborts the receipt.
    selected_ids = list(dict.fromkeys(work_ids))
    if len(selected_ids) > 100:
        raise _MemoryRunError("selected_work_ids_over_limit")
    if deferred_work_ids is None:
        deferred_ids: list[str] = []
    elif isinstance(deferred_work_ids, list) and all(
            isinstance(item, str) for item in deferred_work_ids):
        deferred_ids = list(dict.fromkeys(deferred_work_ids))
    else:
        raise _MemoryRunError("deferred_work_ids_malformed")
    if len(deferred_ids) > 100:
        raise _MemoryRunError("deferred_work_ids_over_limit")
    validated_exceptions = _validated_run_exceptions(exceptions)

    source_host = "home" if role == "home" else "sjm"
    prepared: dict[str, Path] = {}
    for path in sorted((state_root / "extraction-inputs").glob("work:*.json")):
        try:
            stat = path.stat()
        except OSError:
            raise _MemoryRunError("prepared_view_unreadable")
        if not (started.timestamp() <= stat.st_mtime <= finished.timestamp()):
            continue
        if path.is_symlink() or not path.is_file():
            continue
        prepared[path.stem] = path
    # Input preparation is persisted before dialogue is shown. Include every
    # view touched during this run even if the model forgets to pass its ID.
    # A view written by another writer role in this state root is not this
    # run's evidence; never count a concurrent producer's turn.
    for work_id in list(prepared):
        try:
            record = _read_memory_json(
                _memory_work_path(state_root, work_id, "worklist"), "memory work record")
        except (OSError, ValueError, RuntimeError):
            continue
        if record.get("writer_role") != role:
            prepared.pop(work_id)

    deferred_pending: list[str] = []
    for work_id in deferred_ids:
        if work_id in selected_ids:
            raise _MemoryRunError("deferred_work_also_selected")
        try:
            record = _read_memory_json(
                _memory_work_path(state_root, work_id, "worklist"), "memory work record")
        except (OSError, ValueError, RuntimeError) as exc:
            raise _MemoryRunError("deferred_view_not_in_current_run") from exc
        if record.get("writer_role") != role:
            raise _MemoryRunError("deferred_view_wrong_role")
        view = prepared.get(work_id)
        # A stale view from an earlier run, or another producer's view, must
        # never be subtracted from this run's byte accounting.
        if view is None or view.is_symlink() or not view.is_file():
            raise _MemoryRunError("deferred_view_not_in_current_run")
        receipt_path = _memory_work_path(state_root, work_id, "extraction-receipts")
        if receipt_path.exists() or receipt_path.is_symlink():
            raise _MemoryRunError("deferred_work_already_extracted")
        deferred_pending.append(work_id)

    attempted = sorted(set(selected_ids) | set(prepared))
    input_bytes = 0
    for work_id in attempted:
        view = prepared.get(work_id) or _memory_work_path(
            state_root, work_id, "extraction-inputs")
        if view.is_file():
            input_bytes += view.stat().st_size
    if deferred_reason == "byte_budget" and deferred_pending and input_bytes <= MEMORY_RUN_MAX_INPUT_BYTES:
        # A byte-budget deferral must be the view that crossed the budget.
        raise _MemoryRunError("deferred_view_within_byte_budget")
    # A time/turn deferral must name a boundary that was actually reached,
    # otherwise excluding fresh unread work could report a false completion.
    if deferred_pending and deferred_reason == "time_budget":
        if (finished - started).total_seconds() < MEMORY_RUN_SELECTION_CUTOFF_SECONDS:
            raise _MemoryRunError("deferred_time_boundary_not_reached")
    if deferred_pending and deferred_reason == "turn_budget":
        attempted_non_deferred = len(set(selected_ids) | (set(prepared) - set(deferred_pending)))
        if attempted_non_deferred < MEMORY_RUN_MAX_TURNS:
            raise _MemoryRunError("deferred_turn_boundary_not_reached")
    for work_id in deferred_pending:
        input_bytes -= prepared[work_id].stat().st_size
        prepared.pop(work_id)
    attempted = sorted((set(selected_ids) | set(prepared)) - set(deferred_pending))

    completed = failed = queued = 0
    outcomes: list[dict] = []
    unattributed: list[str] = []
    for work_id in attempted:
        try:
            bundle = _read_memory_json(
                _memory_work_path(state_root, work_id, "source-bundles"), "source bundle")
            if bundle.get("source_host") != source_host:
                raise _MemoryRunError("wrong_host_run_evidence")
            if _memory_work_id(bundle) != work_id:
                raise _MemoryRunError("run_source_identity_conflict")
            view = prepared.get(work_id) or _memory_work_path(
                state_root, work_id, "extraction-inputs")
            if not view.is_file() or view.stat().st_size > MEMORY_RUN_PER_TURN_BYTES:
                raise _MemoryRunError("run_input_evidence_missing")
            receipt = _validate_memory_extraction_receipt(state_root, work_id, bundle)
            completed += 1
            queued += receipt["result"]["status"] == "queued"
            outcomes.append({"work_id": work_id, "outcome": receipt["result"]["status"]})
        except _MemoryRunError as exc:
            if work_id in selected_ids:
                # The caller asserted this turn was processed; missing
                # evidence is a genuine extraction failure.
                failed += 1
                outcomes.append({"work_id": work_id, "outcome": "failed",
                                 "diagnostic": exc.diagnostic})
            else:
                unattributed.append(work_id)
        except (OSError, ValueError, RuntimeError, KeyError, TypeError):
            if work_id in selected_ids:
                failed += 1
                outcomes.append({"work_id": work_id, "outcome": "failed"})
            else:
                unattributed.append(work_id)

    elapsed = (finished - started).total_seconds()
    exceeded = (len(attempted) > MEMORY_RUN_MAX_TURNS
                or input_bytes > MEMORY_RUN_MAX_INPUT_BYTES
                or elapsed > MEMORY_RUN_MAX_SECONDS)
    commands = {
        "memory_worklist": {"status": "ready" if attempted else "idle",
                            "selected": len(attempted)},
        "memory_record_extraction": {
            "status": "failed" if failed else ("queued" if queued else "reviewed_empty"),
            "completed": completed, "failed": failed,
            "deferred": len(deferred_pending), "unattributed_pending": len(unattributed),
        },
        "batch_budget": {"status": "exceeded" if exceeded else "bounded"},
    }
    if role == "home":
        if not isinstance(drain_result, dict) or not isinstance(publication_result, dict):
            raise _MemoryRunError("home_command_json_required")
        delivery_failures = 0
        for key in ("local_delivery", "sjm_delivery"):
            delivery = drain_result.get(key)
            if not isinstance(delivery, dict):
                raise _MemoryRunError("home_drain_delivery_missing")
            for name, count in delivery.items():
                if name.endswith("failed") or name in {"offline", "exhausted"}:
                    if type(count) is not int or count < 0:
                        raise _MemoryRunError("home_drain_failure_count_malformed")
                    delivery_failures += count
        commands["memory_home_drain"] = {
            "status": drain_result.get("status"), "failed": delivery_failures,
        }
        commands["memory_publish"] = {"status": publication_result.get("status")}
    elif drain_result is not None or publication_result is not None:
        raise PermissionError("SJM cannot record Home publication")
    data = {
        "schema_version": 1, "automation_id": native_job_status.JOB_IDS[role],
        "writer_role": role, "started_at": started.isoformat(),
        "finished_at": finished.isoformat(), "commands": commands,
        "selected_work_ids": selected_ids, "attempted_work_ids": attempted,
        "exceptions": validated_exceptions,
        "hold_created": any(native_job_status._classify_exception(item) == "action"
                            for item in validated_exceptions),
        "work_outcomes": outcomes, "deferred_work_ids": deferred_pending,
        "pending_prepared_work_ids": unattributed,
        "budget": {"input_bytes": input_bytes, "elapsed_seconds": elapsed,
                   "exceeded": exceeded, "deferred_reason": deferred_reason},
    }
    return data, {"completed": completed, "failed": failed,
                  "deferred": len(deferred_pending), "unattributed": len(unattributed)}


def record_native_memory_run(
    *, state_root: Path, writer_role: str, started_at: str,
    work_ids: list[str], drain_result: dict | None = None,
    publication_result: dict | None = None,
    deferred_work_ids: list[str] | None = None,
    deferred_reason: str = "byte_budget",
    exceptions: list | None = None,
) -> dict:
    """Write a canonical run receipt from validated per-turn durable evidence.

    Pass every selected work ID, including interrupted/failed items. A
    selected turn without a valid extraction receipt records a failed run. A
    prepared-but-unread turn declared with deferred_work_ids is a normal
    batch-boundary stop: it stays pending and never becomes a failure. Any
    other prepared-but-unaccounted turn makes the outcome ambiguous instead
    of failed. Validation failures after a valid start still leave durable
    failed run evidence. Command outputs are compacted here, not summarized
    by the native model.
    """
    role = _memory_writer_role(writer_role)
    started = _memory_instant(started_at, "run start")
    finished = datetime.now(timezone.utc)
    if started > finished:
        raise ValueError("run start is in the future")

    def failed_evidence(diagnostic: str) -> dict:
        return record_failed_native_memory_run(
            state_root=state_root, writer_role=role, started_at=started.isoformat(),
            diagnostic=diagnostic,
            work_ids=work_ids if isinstance(work_ids, list) else None)

    try:
        data, counts = _build_native_run_receipt(
            state_root=state_root, role=role, started=started, finished=finished,
            work_ids=work_ids, deferred_work_ids=deferred_work_ids,
            deferred_reason=deferred_reason, exceptions=exceptions,
            drain_result=drain_result, publication_result=publication_result)
    except _MemoryRunError as exc:
        return failed_evidence(exc.diagnostic)
    except PermissionError:
        raise
    except (OSError, ValueError, RuntimeError, KeyError, TypeError):
        return failed_evidence("run_evidence_invalid")
    _, _, outcome, diagnostic = native_job_status._classify_receipt(
        data, job_id=data["automation_id"], writer_role=role)
    if outcome is None:
        return failed_evidence("run_receipt_contract_unsatisfied")
    path = _memory_run_receipt_path(state_root, finished)
    _atomic_write_json(path, data)
    return {"status": "recorded", "outcome": outcome, "diagnostic": diagnostic,
            "completed": counts["completed"], "failed": counts["failed"],
            "deferred": counts["deferred"], "pending_prepared": counts["unattributed"],
            "receipt_path": str(path)}


def memory_backlog(
    *, state_root: Path, codex_root: Path, since: str, through: str,
    writer_role: str, max_scan_seconds: float = 60,
) -> dict:
    """Read source coverage without creating worklists or success receipts.

    An extraction is counted only after its persisted artifact and delivery
    binding validate, using the same validator as worklist replay. The
    deadline is enforced inside single-file parsing too, so one very large
    session file yields an honest partial result instead of an overrun.
    """
    if (isinstance(max_scan_seconds, bool)
            or not isinstance(max_scan_seconds, (int, float))
            or not math.isfinite(max_scan_seconds) or max_scan_seconds < 0):
        raise ValueError("max_scan_seconds must be a finite nonnegative number")
    role = _memory_writer_role(writer_role)
    dates = set(_date_range(since, through))
    root = _resolve_codex_root(codex_root)
    if not root.is_dir():
        raise ValueError("Codex source root is unavailable")
    source_host = "home" if role == "home" else "sjm"
    deadline = time.monotonic() + max_scan_seconds
    complete = True
    timed_out = 0
    pending, extracted = [], []
    seen: set[str] = set()
    invalid_receipts = source_errors = parse_exceptions = 0
    for path in sorted(root.rglob("*.jsonl")):
        if time.monotonic() >= deadline:
            complete = False
            break
        if path.is_symlink() or not path.resolve().is_relative_to(root):
            source_errors += 1
            continue
        if not _is_k2b_scope(path):
            continue
        try:
            before = path.stat()
            parsed = parse_completed_dialogue(
                path, source_host=source_host, deadline=deadline)
            after = path.stat()
            if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                source_errors += 1
                continue
        except DialogueParseTimeout:
            # Honest partial: the scan budget, not the backlog, ran out.
            timed_out += 1
            complete = False
            break
        except (OSError, ValueError):
            source_errors += 1
            continue
        if (not parsed.get("eligible") or parsed.get("provenance_status") != "known_user"
            or parsed.get("thread_source") not in INTERACTIVE_THREAD_SOURCES):
            continue
        parse_exceptions += sum(d.get("completed_date") in dates
                                for d in parsed.get("parse_diagnostics", []))
        for prefix in parsed.get("completed_prefixes", []):
            if (prefix.get("mode") != "turn" or prefix.get("completed_date") not in dates
                or not prefix.get("transcript")):
                continue
            identity = dict(host_id=parsed["host_id"], session_id=parsed["session_id"],
                completed_cursor=prefix["cursor"], completed_prefix_sha256=prefix["prefix_sha256"],
                transcript_sha256=prefix["prefix_sha256"])
            work_id = _memory_work_id(identity)
            if work_id in seen:
                continue
            seen.add(work_id)
            try:
                completed_at = datetime.fromisoformat(
                    _completed_at_iso(prefix["completed_at"]).replace("Z", "+00:00")
                ).astimezone(timezone.utc).isoformat()
            except (ValueError, KeyError, TypeError, OverflowError):
                parse_exceptions += 1
                continue
            receipt_path = _memory_work_path(state_root, work_id, "extraction-receipts")
            if receipt_path.exists() or receipt_path.is_symlink():
                try:
                    bundle_path = _memory_work_path(state_root, work_id, "source-bundles")
                    if receipt_path.is_symlink() or bundle_path.is_symlink():
                        raise RuntimeError("unsafe extraction evidence")
                    bundle = _read_memory_json(bundle_path, "source bundle")
                    if _memory_work_id(bundle) != work_id:
                        raise RuntimeError("source bundle identity conflicts")
                    _validate_memory_extraction_receipt(state_root, work_id, bundle)
                except (OSError, ValueError, RuntimeError, KeyError, TypeError):
                    invalid_receipts += 1
                else:
                    extracted.append(completed_at)
                    continue
            pending.append(completed_at)
    return {
        "status": "needs_attention" if invalid_receipts or source_errors or parse_exceptions or not complete else "ok",
        "complete": complete,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "writer_role": role, "since": since, "through": through,
        "eligible_turns": len(seen), "extracted_turns": len(extracted),
        "pending_turns": len(pending), "invalid_receipts": invalid_receipts,
        "source_errors": source_errors, "parse_exceptions": parse_exceptions,
        "timed_out_sources": timed_out,
        "oldest_pending_at": min(pending, default=None),
        "latest_extracted_source_at": max(extracted, default=None),
    }


def memory_status(
    state_root: Path,
    *,
    writer_role: str,
    memory_path: Path | None = None,
    automation_root: Path | None = None,
) -> dict:
    role = _memory_writer_role(writer_role)
    try:
        native = native_job_status.read_native_job_status(
            writer_role=role,
            state_root=state_root,
            automation_root=automation_root,
        )
    except PermissionError:
        raise
    except Exception as exc:  # unavailable evidence is unknown, not disabled
        native = {
            "job_id": native_job_status.JOB_IDS.get(role),
            "registration_state": "unknown",
            "configured_model": None,
            "configured_reasoning": None,
            "schedule": None,
            "last_completed_at": None,
            "last_run_outcome": "unknown",
            "extraction_hold": "unknown",
            "diagnostic": f"native status unavailable: {exc.__class__.__name__}",
        }
    result = {
        "writer_role": role,
        # Legacy keys kept for additive compatibility: both now carry the
        # truthful observed registration label, never extraction success.
        "automatic_runner": native["registration_state"],
        "production_activation": native["registration_state"],
        "native_job_id": native["job_id"],
        "native_registration_state": native["registration_state"],
        "native_configured_model": native["configured_model"],
        "native_configured_reasoning": native["configured_reasoning"],
        "native_schedule": native["schedule"],
        "native_last_completed_at": native["last_completed_at"],
        "native_run_outcome": native["last_run_outcome"],
        "native_extraction_hold": native["extraction_hold"],
        "native_diagnostic": native["diagnostic"],
        "outbox": automatic_memory.outbox_status(state_root),
        "worklist_files": len(list((state_root / "worklist").glob("*.json"))),
        "extraction_receipt_files": len(
            list((state_root / "extraction-receipts").glob("*.json"))
        ),
        "extraction_diagnostic_files": len(
            list((state_root / "extraction-diagnostics").glob("*.json"))
        ),
        "publication_receipt_files": len(
            list((state_root / "publication").glob("*.json"))
        ),
    }
    if role == "home":
        target = memory_path or state_root / "memory.json"
        snapshot = automatic_memory.memory_snapshot(target)
        result["memory_records"] = len(snapshot["records"])
        for name in ("inbox", "acceptance", "reconciliation"):
            result[f"{name}_files"] = len(list((state_root / name).glob("*.json")))
    else:
        result["home_delivery"] = "pending_or_external_to_this_host"
    return result


def publish_shared_recall(
    state_root: Path,
    *,
    writer_role: str,
    write_func: Callable[[str, str], None],
    memory_path: Path | None = None,
) -> dict:
    """Render through an injected Home writer after its own sync/lock preflight."""
    if _memory_writer_role(writer_role) != "home":
        raise PermissionError("shared recall publication requires the home writer")
    target = memory_path or state_root / "memory.json"
    snapshot = automatic_memory.memory_snapshot(target)
    recalls = [
        automatic_memory.recall_snapshot(snapshot, key)
        for key in sorted(snapshot["records"])
    ]
    # Publish the validated redacted state, not a second lossy projection, so
    # the same recall entry point works against the Syncthing-delivered copy on
    # SJM while retaining source history and citations.
    json_content = json.dumps(
        snapshot, ensure_ascii=False, indent=2, sort_keys=True
    ) + "\n"
    lines = [
        "---",
        "tags: [context, memory, automatic]",
        "status: generated",
        "---",
        "",
        "# Automatic Memory Recall",
        "",
        "Generated from evidence-bound reviewed sources. Current values are ordered by source time.",
        "",
    ]
    for recalled in recalls:
        lines.extend(
            [
                f"## {recalled['key']}",
                "",
                f"- Status: {recalled['status']}",
                f"- Value: {recalled['value'] if recalled['value'] is not None else 'unresolved'}",
                f"- Citation: {json.dumps(recalled.get('citation'), ensure_ascii=False, sort_keys=True)}",
                "",
            ]
        )
    write_func("System/memory/automatic-memory-current.json", json_content)
    write_func("wiki/context/context_automatic-memory-recall.md", "\n".join(lines))
    return {"status": "published_by_injected_home_writer", "records": len(recalls)}


def _atomic_write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(
        dir=str(path.parent), prefix=f".tmp_{path.name}_", suffix=".json"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        target_fd = os.open(str(path), os.O_RDONLY)
        try:
            os.fsync(target_fd)
        finally:
            os.close(target_fd)
        _fsync_dir(path.parent)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        finally:
            _fsync_dir(path.parent)
        raise


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(
        dir=str(path.parent),
        prefix=f".tmp_{path.name}_",
        suffix=path.suffix or ".tmp",
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        target_fd = os.open(str(path), os.O_RDONLY)
        try:
            os.fsync(target_fd)
        finally:
            os.close(target_fd)
        _fsync_dir(path.parent)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        finally:
            _fsync_dir(path.parent)
        raise


def _append_log(vault_path: Path, action: str, summary: str) -> None:
    log_path = vault_path / "wiki" / "log.md"
    if not log_path.exists():
        warning = f"wiki log missing: {log_path}"
    else:
        env = os.environ.copy()
        env["K2B_WIKI_LOG"] = str(log_path)
        try:
            proc = subprocess.run(
                [
                    str(WIKI_LOG_APPEND),
                    "/eod-capture",
                    _sanitize_log_value(action),
                    _sanitize_log_value(summary),
                ],
                text=True,
                capture_output=True,
                env=env,
                check=False,
                timeout=10,
            )
            warning = (
                _sanitize_log_value(proc.stderr.strip()) if proc.returncode != 0 else ""
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            warning = _sanitize_log_value(exc)
    if warning:
        print(f"eod-capture: warning: {warning}", file=sys.stderr)
        try:
            fallback_dir = vault_path / ".staging" / "eod-log-failures"
            fallback_dir.mkdir(parents=True, exist_ok=True)
            fallback = fallback_dir / f"{_safe_log_filename(action)}.jsonl"
            lock_path = fallback_dir / ".eod-log-failures.lock"
            line = (
                json.dumps(
                    {
                        "action": _sanitize_log_value(action),
                        "summary": _sanitize_log_value(summary),
                        "error": warning,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
                + "\n"
            )
            with _file_lock(lock_path):
                with fallback.open("a", encoding="utf-8") as f:
                    f.write(line)
                    f.flush()
                    os.fsync(f.fileno())
                _fsync_dir(fallback_dir)
        except OSError as exc:
            print(f"eod-capture: warning: fallback log failed: {exc}", file=sys.stderr)


def _sanitize_log_value(value: object) -> str:
    text = str(value)
    text = "".join(ch if ch >= " " and ch != "\x7f" else " " for ch in text)
    text = CREDENTIAL_URL_RE.sub(r"\1[REDACTED]@", text)
    text = SECRET_ASSIGNMENT_RE.sub(r"\1[REDACTED]", text)
    text = SECRET_OPTION_RE.sub(r"\1[REDACTED]", text)
    text = AUTH_VALUE_RE.sub(r"\1[REDACTED]", text)
    text = SECRET_TOKEN_RE.sub("[REDACTED]", text)
    return " ".join(text.split())[:500]


def _safe_log_filename(value: object) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(value)).strip("-._")
    return safe[:80] or "log"


def _diagnostic_prefix_binding(
    session_path: Path,
    *,
    run_date: str,
    completed_cursor: str | None,
) -> tuple[str | None, str | None]:
    """Resolve an exact diagnostic cursor without relabeling legacy prefixes."""
    try:
        parsed = parse_completed_dialogue(session_path, source_host="home")
        if completed_cursor:
            prefix = next(
                (
                    candidate
                    for candidate in parsed.get("completed_prefixes", [])
                    if candidate.get("cursor") == completed_cursor
                ),
                None,
            )
        else:
            prefix = _select_completed_prefix(parsed, run_date=run_date)
            if not prefix or prefix.get("mode") != "turn":
                return None, None
        if prefix:
            mode = prefix.get("mode")
            if mode in {"turn", "legacy_whole_source"}:
                return str(prefix.get("cursor") or "") or None, str(mode)
    except (OSError, ValueError):
        pass
    return completed_cursor, None


def _write_extraction_failure(
    vault_path: Path,
    session_path: Path,
    *,
    run_date: str,
    error: Exception,
    transcript_sha256: str | None = None,
    raw_source_sha256: str | None = None,
    codex_root: Path | None = None,
    completed_cursor: str | None = None,
) -> Path:
    failure_dir = vault_path / ".staging" / "extraction-failures"
    failure = {
        "failed_at": datetime.now().isoformat(timespec="microseconds"),
        "run_date": run_date,
        "session_path": str(session_path),
        "source_app": detect_source_app(session_path, codex_root=codex_root),
        "error": _sanitize_log_value(f"{type(error).__name__}: {error}"),
    }
    if transcript_sha256:
        failure["transcript_sha256"] = transcript_sha256
    if raw_source_sha256:
        failure["raw_source_sha256"] = raw_source_sha256
    completed_cursor, completed_prefix_mode = _diagnostic_prefix_binding(
        session_path,
        run_date=run_date,
        completed_cursor=completed_cursor,
    )
    if completed_cursor:
        failure["completed_cursor"] = completed_cursor
        if completed_prefix_mode:
            failure["completed_prefix_mode"] = completed_prefix_mode
    path = failure_dir / _capture_artifact_name(
        run_date, session_path, completed_cursor=completed_cursor
    )
    _atomic_write_json(path, failure)
    return path


def _write_extraction_skip(
    vault_path: Path,
    session_path: Path,
    *,
    run_date: str,
    reason: str,
    error: Exception,
    transcript_sha256: str | None = None,
    raw_source_sha256: str | None = None,
    codex_root: Path | None = None,
    completed_cursor: str | None = None,
) -> Path:
    skip_dir = vault_path / ".staging" / "extraction-skips"
    skip = {
        "skipped_at": datetime.now().isoformat(timespec="microseconds"),
        "run_date": run_date,
        "session_path": str(session_path),
        "source_app": detect_source_app(session_path, codex_root=codex_root),
        "reason": reason,
        "error": _sanitize_log_value(f"{type(error).__name__}: {error}"),
    }
    if transcript_sha256:
        skip["transcript_sha256"] = transcript_sha256
    if raw_source_sha256:
        skip["raw_source_sha256"] = raw_source_sha256
    completed_cursor, completed_prefix_mode = _diagnostic_prefix_binding(
        session_path,
        run_date=run_date,
        completed_cursor=completed_cursor,
    )
    if completed_cursor:
        skip["completed_cursor"] = completed_cursor
        if completed_prefix_mode:
            skip["completed_prefix_mode"] = completed_prefix_mode
    path = skip_dir / _capture_artifact_name(
        run_date, session_path, completed_cursor=completed_cursor
    )
    _atomic_write_json(path, skip)
    return path


def _write_extraction_quarantine(
    vault_path: Path,
    session_path: Path,
    *,
    run_date: str,
    reason: str,
    error: Exception | str,
    transcript_sha256: str | None = None,
    raw_source_sha256: str | None = None,
    extractor_output: object | None = None,
    rejections: list[dict] | None = None,
    codex_root: Path | None = None,
    completed_cursor: str | None = None,
) -> Path:
    quarantine_dir = vault_path / ".staging" / "eod-quarantine"
    quarantine = {
        "quarantined_at": datetime.now().isoformat(timespec="microseconds"),
        "run_date": run_date,
        "session_path": str(session_path),
        "source_app": detect_source_app(session_path, codex_root=codex_root),
        "reason": reason,
        "error": _sanitize_log_value(
            f"{type(error).__name__}: {error}"
            if isinstance(error, Exception)
            else str(error)
        ),
    }
    if transcript_sha256:
        quarantine["transcript_sha256"] = transcript_sha256
    if raw_source_sha256:
        quarantine["raw_source_sha256"] = raw_source_sha256
    if extractor_output is not None:
        quarantine["extractor_output"] = _sanitize_durable_value(extractor_output)
    if rejections is not None:
        quarantine["rejections"] = _sanitize_durable_value(rejections)
        quarantine["rejection_count"] = len(rejections)
        quarantine["schema_rejection_count"] = sum(
            1 for rejection in rejections if rejection.get("rejection_class") == "schema"
        )
        quarantine["content_rejection_count"] = sum(
            1 for rejection in rejections if rejection.get("rejection_class") == "content"
        )
    completed_cursor, completed_prefix_mode = _diagnostic_prefix_binding(
        session_path,
        run_date=run_date,
        completed_cursor=completed_cursor,
    )
    if completed_cursor:
        quarantine["completed_cursor"] = completed_cursor
        if completed_prefix_mode:
            quarantine["completed_prefix_mode"] = completed_prefix_mode
    path = quarantine_dir / _capture_artifact_name(
        run_date, session_path, completed_cursor=completed_cursor
    )
    _atomic_write_json(path, quarantine)
    return path


def _write_extraction_rejection(
    vault_path: Path,
    session_path: Path,
    *,
    run_date: str,
    rejection: dict,
    transcript_sha256: str | None = None,
    raw_source_sha256: str | None = None,
    codex_root: Path | None = None,
) -> Path:
    rejection_dir = vault_path / ".staging" / "extraction-rejections"
    item_index = int(rejection.get("item_index", 0))
    digest = hashlib.sha256(
        json.dumps(rejection, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()[:16]
    payload = {
        "rejected_at": datetime.now().isoformat(timespec="microseconds"),
        "run_date": run_date,
        "session_path": str(session_path),
        "source_app": detect_source_app(session_path, codex_root=codex_root),
        "item_index": item_index,
        "error": _sanitize_log_value(rejection.get("error", "")),
        "rejection_class": rejection.get("rejection_class", "schema"),
        "item": _sanitize_durable_value(rejection.get("item")),
    }
    if transcript_sha256:
        payload["transcript_sha256"] = transcript_sha256
    if raw_source_sha256:
        payload["raw_source_sha256"] = raw_source_sha256
    path = (
        rejection_dir
        / f"{run_date}_{_safe_session_id(session_path)}_item-{item_index}_{digest}.json"
    )
    _atomic_write_json(path, payload)
    return path


def _normalize(s: object) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(s).lower())


def _split_pipe_fields(line: str) -> list[str]:
    parts: list[str] = []
    current: list[str] = []
    escaped = False
    for ch in line:
        if ch == "|" and not escaped:
            parts.append("".join(current))
            current = []
        else:
            current.append(ch)
        if ch == "\\" and not escaped:
            escaped = True
        else:
            escaped = False
    parts.append("".join(current))
    return parts


def _extract_attr(line: str, key: str) -> str | None:
    patterns = [key]
    if key == "phone":
        patterns.extend(["tel", "telephone"])
    for k in patterns:
        found = _extract_pipe_field(line, k)
        if found is not None:
            return found
    return None


def _extract_pipe_field(line: str, key: str) -> str | None:
    target_key = key.strip().lower()
    for part in _split_pipe_fields(line):
        if ":" not in part:
            continue
        raw_key, raw_value = part.split(":", 1)
        if raw_key.strip().lower() == target_key:
            return raw_value.strip()
    return None


def _find_existing_fact(semantic_path: Path, item: dict) -> tuple[str, int, str] | None:
    if not semantic_path.exists():
        return None
    dedupe_key = str(item.get("dedupe_key", "")).strip()
    if not dedupe_key:
        return None
    dedupe_key_aliases = _dedupe_key_aliases(dedupe_key)
    raw_predicate = str(item.get("predicate", "")).strip() or "value"
    predicate = _normalize_predicate_value(raw_predicate) or "value"
    compatible_predicates = _predicate_key_aliases(raw_predicate) | _predicate_key_aliases(predicate)
    matches: list[tuple[str, int, str]] = []
    for lineno, line in enumerate(
        semantic_path.read_text(encoding="utf-8").splitlines(), 1
    ):
        if not line.startswith("- "):
            continue
        line_dedupe_key = _extract_pipe_field(line, "dedupe_key")
        row_predicate = _extract_pipe_field(line, "predicate")
        row_predicate_normalized = _normalize_predicate_value(row_predicate or "")
        exact_dedupe_match = line_dedupe_key == dedupe_key
        compatible_alias_match = (
            line_dedupe_key in dedupe_key_aliases
            and row_predicate_normalized in compatible_predicates
        )
        if line_dedupe_key and (exact_dedupe_match or compatible_alias_match):
            existing = _extract_attr(line, predicate)
            if existing is None and raw_predicate != predicate:
                existing = _extract_attr(line, raw_predicate)
            if existing is None:
                if row_predicate:
                    existing = _extract_attr(line, row_predicate)
            existing = existing or ""
            matches.append((line, lineno, existing))
    if len(matches) > 1:
        raise RuntimeError(f"duplicate dedupe_key in semantic.md: {dedupe_key}")
    return matches[0] if matches else None


def _count_semantic_dedupe(semantic_path: Path, dedupe_key: str) -> int:
    if not semantic_path.exists():
        return 0
    count = 0
    for line in semantic_path.read_text(encoding="utf-8").splitlines():
        if line.startswith("- ") and _extract_pipe_field(line, "dedupe_key") == dedupe_key:
            count += 1
    return count


def _same_value(a: str, b: str) -> bool:
    return str(a).strip() == str(b).strip()


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "unknown"


def _write_semantic_item(vault_path: Path, item: dict, *, run_date: str) -> bool:
    item = dict(item)
    _validate_extraction_item(item, idx=0, source=Path("semantic-write"))
    _normalize_item_predicate(item, log=True)
    dedupe_key = str(item.get("dedupe_key", "")).strip()
    semantic_path = vault_path / "wiki" / "context" / "shelves" / "semantic.md"
    attrs = [
        f"subject:{item.get('subject', '')}",
        f"predicate:{item.get('predicate', '')}",
        f"{item.get('predicate', 'value')}:{item.get('object', '')}",
    ]
    for key in (
        "dedupe_key",
        "scope",
        "speaker_source",
        "source_app",
        "session_path",
    ):
        if item.get(key):
            attrs.append(f"{key}:{item[key]}")
    if item.get("evidence_quote"):
        attrs.append(f"evidence_quote:{item['evidence_quote']}")
    cmd = [
        str(SHELF_WRITER),
        "--shelf",
        "semantic",
        "--date",
        run_date,
        "--type",
        str(item.get("subtype") or item.get("kind") or "fact"),
        "--slug",
        _slugify(str(item.get("subject") or item.get("dedupe_key") or "item")),
    ]
    for attr in attrs:
        cmd.extend(["--attr", attr])
    env = os.environ.copy()
    env["K2B_VAULT"] = str(vault_path)
    env["K2B_WIKI_LOG"] = str(vault_path / "wiki" / "log.md")
    timeout = int(
        os.environ.get(
            "K2B_EOD_SHELF_WRITER_TIMEOUT",
            str(DEFAULT_SHELF_WRITER_TIMEOUT_SECONDS),
        )
    )
    # shelf-writer owns the shelf lock. loop_lib.accept_conflict uses the same
    # lock name for semantic.md rewrites so appends cannot be overwritten.
    proc = subprocess.run(
        cmd,
        text=True,
        capture_output=True,
        env=env,
        check=False,
        timeout=timeout,
    )
    if proc.returncode == 3 and "duplicate dedupe_key" in proc.stderr:
        with _shell_compatible_lock(_shelf_lock_path("semantic")):
            row = _find_existing_fact(semantic_path, item)
            if row is not None and _same_value(row[2], str(item.get("object", ""))):
                return False
        raise RuntimeError(proc.stderr.strip() or "shelf-writer duplicate dedupe_key")
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "shelf-writer failed")
    if proc.stderr:
        sys.stderr.write(proc.stderr)
        if not proc.stderr.endswith("\n"):
            sys.stderr.write("\n")
        sys.stderr.flush()
    _fsync_dir(vault_path / "wiki" / "context" / "shelves")
    return True


def _allocate_learning_id(path: Path, run_date: str) -> str:
    max_n = 0
    if path.exists():
        for m in re.finditer(
            rf"^### L-{re.escape(run_date)}-(\d{{3}})\b",
            path.read_text(encoding="utf-8"),
            re.M,
        ):
            max_n = max(max_n, int(m.group(1)))
    return f"L-{run_date}-{max_n + 1:03d}"


def _learning_exists(path: Path, item: dict) -> bool:
    if not path.exists():
        return False
    text = path.read_text(encoding="utf-8")
    dedupe_key = str(item.get("dedupe_key", "")).strip()
    if not dedupe_key:
        return False
    for line in text.splitlines():
        m = re.match(r"^\s*-\s+\*\*Dedupe key:\*\*\s*(?P<key>\S+)\s*$", line)
        if m and m.group("key") == dedupe_key:
            return True
    return False


def _append_learning_item(vault_path: Path, item: dict, *, run_date: str) -> bool:
    path = vault_path / "System" / "memory" / "self_improve_learnings.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    with _file_lock(path.with_name(f".{path.name}.lock")):
        existing = path.read_text(encoding="utf-8") if path.exists() else ""
        if _learning_exists(path, item):
            return False
        lid = _allocate_learning_id(path, run_date)
        rule = str(item.get("object") or item.get("subject") or "Captured learning")
        block = (
            f"\n### {lid}\n"
            f"distilled-rule: \"{rule}\"\n"
            "- **Area:** workflow\n"
            f"- **Dedupe key:** {item.get('dedupe_key', '')}\n"
            f"- **Distilled rule:** {rule}\n"
            f"- **Learning:** {rule}\n"
            "- **Context:** End-of-day capture from "
            f"{item.get('session_path', 'unknown session')}. "
            f"Evidence: {item.get('evidence_quote', '')}\n"
            "- **Reinforced:** 1\n"
            f"- **Confidence:** {item.get('confidence', 'high')}\n"
            f"- **Date:** {run_date}\n"
        )
        _atomic_write_text(path, existing.rstrip() + "\n" + block.lstrip())
    return True


def _write_low_confidence(vault_path: Path, item: dict, *, run_date: str) -> None:
    review_dir = vault_path / "review"
    review_dir.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(
        json.dumps(item, sort_keys=True).encode("utf-8")
    ).hexdigest()[:10]
    path = review_dir / f"eod-low-confidence_{run_date}_{digest}.md"
    body = (
        "---\n"
        "tags: [review, eod-capture]\n"
        "type: eod-low-confidence\n"
        "review-action: pending\n"
        "review-notes: \"\"\n"
        "---\n\n"
        "# EOD low-confidence extraction\n\n"
        "```json\n"
        f"{json.dumps(item, ensure_ascii=False, indent=2)}\n"
        "```\n"
    )
    _atomic_write_text(path, body)


def _write_error_review(
    vault_path: Path,
    *,
    run_date: str,
    source_file: Path,
    reason: str,
    payload: object,
) -> None:
    safe_reason = _sanitize_log_value(reason)
    safe_payload = _sanitize_durable_value(payload)
    raw_discriminator = hashlib.sha256(
        json.dumps(
            {"reason": reason, "payload": payload},
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        ).encode("utf-8")
    ).hexdigest()
    review_dir = vault_path / "review"
    review_dir.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(
        f"{source_file}|{raw_discriminator}".encode("utf-8")
    ).hexdigest()[:10]
    path = review_dir / f"eod-error_{run_date}_{digest}.md"
    body = (
        "---\n"
        "tags: [review, eod-capture, error]\n"
        "type: eod-error\n"
        "review-action: pending\n"
        "review-notes: \"\"\n"
        "---\n\n"
        "# EOD capture error\n\n"
        f"- **Reason:** {safe_reason}\n"
        f"- **Source file:** {source_file}\n\n"
        "```json\n"
        f"{json.dumps(safe_payload, ensure_ascii=False, indent=2, default=str)}\n"
        "```\n"
    )
    _atomic_write_text(path, body)


def _write_conflict(
    vault_path: Path,
    item: dict,
    *,
    existing_value: str,
    line_no: int,
    run_date: str,
) -> Path | None:
    conflict_dir = vault_path / ".staging" / "pending-conflicts"
    with _file_lock(_pending_conflicts_lock_path(vault_path)):
        return _write_conflict_locked(
            conflict_dir,
            item,
            existing_value=existing_value,
            line_no=line_no,
            run_date=run_date,
        )


def _pending_conflicts_lock_path(vault_path: Path) -> Path:
    return vault_path / ".staging" / "pending-conflicts" / ".pending-conflicts.lock"


def _write_conflict_locked(
    conflict_dir: Path,
    item: dict,
    *,
    existing_value: str,
    line_no: int,
    run_date: str,
) -> Path | None:
    conflict_payload = (
        f"{run_date}|{item.get('dedupe_key')}|{item.get('object')}|"
        f"{item.get('session_path')}"
    )
    conflict_id = hashlib.sha256(conflict_payload.encode("utf-8")).hexdigest()[:12]
    conflict = {
        "conflict_id": conflict_id,
        "set_at": run_date,
        "source_session_path": item.get("session_path", ""),
        "source_app": item.get("source_app", ""),
        "subject": item.get("subject", ""),
        "predicate": item.get("predicate", ""),
        "existing_value": existing_value,
        "existing_source": f"wiki/context/shelves/semantic.md:{line_no}",
        "new_value": item.get("object", ""),
        "new_evidence_quote": item.get("evidence_quote", ""),
        "dedupe_key": item.get("dedupe_key", ""),
        "surfaced_count": 0,
    }
    existing_line = str(item.get("_existing_line") or "").rstrip("\r\n")
    if existing_line:
        conflict["existing_line_hash"] = hashlib.sha256(
            existing_line.encode("utf-8")
        ).hexdigest()
    path = conflict_dir / f"{run_date}_{conflict_id}.json"
    for existing_path in sorted(conflict_dir.glob(f"{run_date}_*.json")):
        try:
            existing = json.loads(existing_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if (
            isinstance(existing, dict)
            and str(existing.get("dedupe_key") or "") == str(item.get("dedupe_key") or "")
            and str(existing.get("set_at") or "") == run_date
        ):
            return None
    _atomic_write_json(path, conflict)
    try:
        written = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"conflict read-back failed: {path}") from exc
    if not isinstance(written, dict) or written.get("conflict_id") != conflict_id:
        raise RuntimeError(f"conflict read-back mismatch: {path}")
    return path


def _iter_extraction_files(vault_path: Path, run_date: str) -> list[Path]:
    return sorted((vault_path / ".staging" / "extractions").glob(f"{run_date}_*.json"))


def _reconciliation_receipt_path(vault_path: Path, extraction_path: Path) -> Path:
    return vault_path / ".staging" / "reconciled" / extraction_path.name


def _write_reconciliation_receipt(
    vault_path: Path,
    extraction_path: Path,
    extraction: dict,
    *,
    run_date: str,
    extraction_sha256: str,
) -> Path:
    receipt = {
        "status": (
            "reviewed_empty" if extraction.get("reviewed_empty") is True else "reconciled"
        ),
        "run_date": run_date,
        "reconciled_at": datetime.now(timezone.utc).isoformat(timespec="microseconds"),
        "session_path": str(extraction.get("session_path", "")),
        "source_app": str(extraction.get("source_app", "")),
        "source_host": str(extraction.get("source_host", "home")),
        "raw_source_sha256": str(extraction.get("raw_source_sha256", "")),
        "transcript_sha256": str(extraction.get("transcript_sha256", "")),
        # Bind the receipt to the exact bytes already parsed and reconciled,
        # not a second file read that could race with interactive restaging.
        "extraction_sha256": extraction_sha256,
    }
    for field in (
        "host_id",
        "session_id",
        "completed_cursor",
        "completed_prefix_sha256",
        "completed_at",
        "completed_date",
        "completed_prefix_mode",
        "review_revision",
    ):
        if extraction.get(field) is not None:
            receipt[field] = extraction[field]
    path = _reconciliation_receipt_path(vault_path, extraction_path)
    _atomic_write_json(path, receipt)
    return path


def _run_date_quarantine_files(vault_path: Path, run_date: str) -> list[Path]:
    quarantine_dir = vault_path / ".staging" / "eod-quarantine"
    if not quarantine_dir.is_dir():
        return []
    return sorted(
        path
        for path in quarantine_dir.glob(f"{run_date}_*.json")
        if path.is_file() and not path.name.startswith(".tmp")
    )


def _artifact_session_key(path: Path) -> str:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return str(path)
    if isinstance(data, dict):
        session_path = str(data.get("session_path", "")).strip()
        if session_path:
            return session_path
    return str(path)


def _artifact_candidate_count(vault_path: Path, run_date: str) -> int:
    failure_dir = vault_path / ".staging" / "extraction-failures"
    failures = (
        sorted(failure_dir.glob(f"{run_date}_*.json")) if failure_dir.is_dir() else []
    )
    paths = (
        _iter_extraction_files(vault_path, run_date)
        + _run_date_quarantine_files(vault_path, run_date)
        + _run_date_extraction_skips(vault_path, run_date)
        + failures
    )
    return len({_artifact_session_key(path) for path in paths})


def _known_candidate_count(
    vault_path: Path,
    run_date: str,
    *,
    processed_files: int,
    quarantined: int,
    slow_skipped: int,
    failures: int,
) -> int:
    artifact_count = _artifact_candidate_count(vault_path, run_date)
    if artifact_count == 0:
        artifact_count = processed_files + quarantined + slow_skipped + failures
    try:
        discovered_count = len(discover_session_paths(run_date=run_date))
    except (OSError, ValueError):
        discovered_count = 0
    return max(artifact_count, discovered_count)


def reconcile_extractions(vault_path: Path, *, run_date: str) -> dict:
    """Reconcile staged extraction files into vault memory."""
    if _source_only_writer():
        raise PermissionError("semantic reconciliation requires the home writer")
    with _file_lock(_reconcile_lock_path(vault_path)):
        return _reconcile_extractions_locked(vault_path, run_date=run_date)


def _quarantine_corrupt_extraction(
    vault_path: Path,
    path: Path,
    raw_bytes: bytes,
    *,
    run_date: str,
    error: UnicodeDecodeError,
) -> Path:
    """Move an undecodable extraction aside once and leave JSON metadata."""
    payload_dir = vault_path / ".staging" / "eod-quarantine-payloads"
    payload_dir.mkdir(parents=True, exist_ok=True)
    payload_sha256 = hashlib.sha256(raw_bytes).hexdigest()
    payload_path = payload_dir / f"{path.name}.{payload_sha256}.invalid"
    metadata_path = vault_path / ".staging" / "eod-quarantine" / path.name
    metadata = {
        "quarantined_at": datetime.now(timezone.utc).isoformat(
            timespec="microseconds"
        ),
        "run_date": run_date,
        "reason": "invalid_utf8_extraction",
        "error": _sanitize_log_value(error),
        "source_file": str(path),
        "payload_path": str(payload_path),
        "payload_sha256": payload_sha256,
        "payload_move": "pending",
    }
    # Publish the recovery pointer first. If the move fails, the original
    # extraction remains visible and the metadata explains the intended path.
    _atomic_write_json(metadata_path, metadata)
    if payload_path.exists():
        if _sha256_file(payload_path) != payload_sha256:
            raise OSError(f"quarantine payload collision: {payload_path}")
        path.unlink()
    else:
        os.replace(path, payload_path)
    _fsync_dir(path.parent)
    _fsync_dir(payload_dir)
    metadata["payload_move"] = "complete"
    _atomic_write_json(metadata_path, metadata)
    return metadata_path


def _reconcile_extractions_locked(vault_path: Path, *, run_date: str) -> dict:
    semantic_path = vault_path / "wiki" / "context" / "shelves" / "semantic.md"
    summary = {
        "processed_files": 0,
        "auto_written": 0,
        "low_confidence": 0,
        "conflicts": 0,
        "deduped": 0,
        "preferences_seen": 0,
        "skipped_preferences": 0,
        "errors": 0,
        "reconciled_files": 0,
    }
    for path in _iter_extraction_files(vault_path, run_date):
        summary["processed_files"] += 1
        file_errors_before = summary["errors"]
        try:
            raw_bytes = path.read_bytes()
        except FileNotFoundError:
            summary["errors"] += 1
            continue
        try:
            raw_text = raw_bytes.decode("utf-8")
        except UnicodeDecodeError as exc:
            summary["errors"] += 1
            try:
                _quarantine_corrupt_extraction(
                    vault_path, path, raw_bytes, run_date=run_date, error=exc
                )
            except OSError as quarantine_exc:
                print(
                    "eod-capture: corrupt extraction quarantine incomplete: "
                    f"{_sanitize_log_value(quarantine_exc)}",
                    file=sys.stderr,
                )
            continue
        try:
            data = json.loads(raw_text)
        except json.JSONDecodeError as exc:
            summary["errors"] += 1
            _write_error_review(
                vault_path,
                run_date=run_date,
                source_file=path,
                reason=f"malformed extraction JSON: {exc}",
                payload=raw_text,
            )
            continue
        try:
            mode = _completed_prefix_mode(data, label="staged extraction")
            if mode == "turn":
                _validate_modern_artifact_binding(data)
            elif mode == "legacy_whole_source":
                _validate_legacy_evidence_slices(
                    data, label="staged extraction"
                )
        except ValueError as exc:
            summary["errors"] += 1
            _write_error_review(
                vault_path,
                run_date=run_date,
                source_file=path,
                reason=f"invalid extraction provenance: {exc}",
                payload=data,
            )
            continue
        structured_payload = ""
        normalized_structured_payload = ""
        structured_attribution = bool(
            mode in {"turn", "legacy_whole_source"}
            and isinstance(data.get("dialogue_events"), list)
            and data.get("dialogue_events")
        )
        if structured_attribution:
            structured_payload = "\n\n".join(
                f"[{event['role']}]\n{event['text']}"
                for event in data["dialogue_events"]
            ).strip()
            normalized_structured_payload = _normalize_evidence_text(
                structured_payload
            )
        receipt_path = _reconciliation_receipt_path(vault_path, path)
        raw_source_sha256 = str(data.get("raw_source_sha256") or "")
        if _receipt_matches_source_and_extraction(
            receipt_path,
            path,
            raw_source_sha256,
            completed_cursor=(
                str(data.get("completed_cursor") or "") or None
                if mode == "turn"
                else None
            ),
            review_revision=(
                str(data.get("review_revision"))
                if data.get("review_revision") is not None
                else None
            ),
        ):
            # A receipt proves the exact extraction bytes were applied once.
            # Out-of-band rollback of a sink requires an explicit repair run;
            # normal replay skips the file to preserve idempotency.
            summary["reconciled_files"] += 1
            continue
        for idx, raw_item in enumerate(data.get("items", [])):
            if not isinstance(raw_item, dict):
                summary["errors"] += 1
                _write_error_review(
                    vault_path,
                    run_date=run_date,
                    source_file=path,
                    reason="malformed extraction item",
                    payload=raw_item,
                )
                continue
            item = dict(raw_item)
            item.setdefault("session_path", data.get("session_path", ""))
            item.setdefault("source_app", data.get("source_app", ""))
            try:
                kind = str(item.get("kind", "")).lower()
                if kind == "preference":
                    if structured_attribution:
                        _validate_item_evidence_quote(
                            item,
                            idx=idx,
                            normalized_payload=normalized_structured_payload,
                            session_path=path,
                        )
                        _validate_speaker_attribution(
                            item,
                            structured_payload,
                            idx=idx,
                            dialogue_events=data.get("dialogue_events"),
                            require_structured_attribution=True,
                        )
                    summary["preferences_seen"] += 1
                    summary["skipped_preferences"] += 1
                    continue
                _validate_extraction_item(item, idx=idx, source=path)
                if structured_attribution:
                    _validate_item_evidence_quote(
                        item,
                        idx=idx,
                        normalized_payload=normalized_structured_payload,
                        session_path=path,
                    )
                    _validate_speaker_attribution(
                        item,
                        structured_payload,
                        idx=idx,
                        dialogue_events=data.get("dialogue_events"),
                        require_structured_attribution=True,
                    )
                elif (
                    mode == "legacy_whole_source"
                    and not data.get("dialogue_events")
                ):
                    raise SpeakerAttributionError(
                        "legacy extraction lacks structured attribution evidence"
                    )
                _normalize_item_predicate(item, log=True)
                confidence = str(item.get("confidence", "")).lower()
                if confidence != "high":
                    _write_low_confidence(vault_path, item, run_date=run_date)
                    summary["low_confidence"] += 1
                    continue
                if kind == "learning":
                    learnings_path = (
                        vault_path / "System" / "memory" / "self_improve_learnings.md"
                    )
                    if _learning_exists(learnings_path, item):
                        summary["deduped"] += 1
                        continue
                    if _append_learning_item(vault_path, item, run_date=run_date):
                        summary["auto_written"] += 1
                    else:
                        summary["deduped"] += 1
                    continue
                conflict_checked = False
                with _file_lock(_pending_conflicts_lock_path(vault_path)):
                    with _shell_compatible_lock(_shelf_lock_path("semantic")):
                        existing = _find_existing_fact(semantic_path, item)
                        if existing is not None:
                            conflict_checked = True
                            _line, line_no, existing_value = existing
                            if _same_value(existing_value, str(item.get("object", ""))):
                                summary["deduped"] += 1
                                continue
                            conflict_path = _write_conflict_locked(
                                vault_path / ".staging" / "pending-conflicts",
                                {**item, "_existing_line": _line},
                                existing_value=existing_value,
                                line_no=line_no,
                                run_date=run_date,
                            )
                            if conflict_path is None:
                                summary["deduped"] += 1
                            else:
                                summary["conflicts"] += 1
                            continue
                if conflict_checked:
                    continue
                if _write_semantic_item(vault_path, item, run_date=run_date):
                    summary["auto_written"] += 1
                else:
                    summary["deduped"] += 1
            except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
                summary["errors"] += 1
                _write_error_review(
                    vault_path,
                    run_date=run_date,
                    source_file=path,
                    reason=f"item reconciliation failed: {exc}",
                    payload=item,
                )
                continue

        if summary["errors"] == file_errors_before:
            # Every sink above is keyed deterministically (semantic dedupe key,
            # learning dedupe key, review digest, or conflict identity). If the
            # receipt write fails after applying data, the next run safely
            # deduplicates those writes before retrying the receipt.
            try:
                _write_reconciliation_receipt(
                    vault_path,
                    path,
                    data,
                    run_date=run_date,
                    extraction_sha256=hashlib.sha256(raw_bytes).hexdigest(),
                )
                summary["reconciled_files"] += 1
            except (OSError, ValueError) as exc:
                summary["errors"] += 1
                try:
                    _write_error_review(
                        vault_path,
                        run_date=run_date,
                        source_file=path,
                        reason=f"reconciliation receipt failed: {exc}",
                        payload=data,
                    )
                except OSError as review_exc:
                    print(
                        "eod-capture: receipt and error-review write failed: "
                        f"{_sanitize_log_value(review_exc)}",
                        file=sys.stderr,
                    )

    slow_skips = _run_date_extraction_skips(
        vault_path, run_date, reason="slow_extraction"
    )
    failures = _failure_file_fingerprints(
        vault_path / ".staging" / "extraction-failures", run_date
    )
    summary["quarantined"] = len(_run_date_quarantine_files(vault_path, run_date))
    summary["slow_skipped"] = len(slow_skips)
    summary["candidate_files"] = _known_candidate_count(
        vault_path,
        run_date,
        processed_files=int(summary.get("processed_files", 0)),
        quarantined=int(summary.get("quarantined", 0)),
        slow_skipped=int(summary.get("slow_skipped", 0)),
        failures=len(failures),
    )
    summary_path = vault_path / ".staging" / f"eod-capture-summary-{run_date}.json"
    _atomic_write_json(summary_path, summary)
    _append_log(
        vault_path,
        run_date,
        f"processed={summary['processed_files']} "
        f"candidates={summary['candidate_files']} "
        f"quarantined={summary['quarantined']} "
        f"slow_skipped={summary['slow_skipped']} "
        f"auto_written={summary['auto_written']} "
        f"conflicts={summary['conflicts']} "
        f"low_confidence={summary['low_confidence']} "
        f"deduped={summary['deduped']} "
        f"preferences_seen={summary['preferences_seen']} "
        f"skipped_preferences={summary['skipped_preferences']}",
    )
    return summary


def build_digest_message(
    vault_path: Path, *, run_date: str, summary: dict | None = None
) -> str:
    if summary is None:
        lock_path = vault_path / ".staging" / "eod-reconcile.lock"
        with _file_lock(lock_path):
            return _build_digest_message_unlocked(
                vault_path, run_date=run_date, summary=summary
            )
    return _build_digest_message_unlocked(vault_path, run_date=run_date, summary=summary)


def _build_digest_message_unlocked(
    vault_path: Path, *, run_date: str, summary: dict | None = None
) -> str:
    summary_warning = ""
    if summary is None:
        summary_path = vault_path / ".staging" / f"eod-capture-summary-{run_date}.json"
        if summary_path.exists():
            try:
                summary = json.loads(summary_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                print(
                    f"eod-capture: warning: cannot read summary {summary_path}: {exc}",
                    file=sys.stderr,
                )
                summary = {"errors": 1}
                summary_warning = "summary warning: could not read summary file; counts may be incomplete"
        else:
            summary = {"errors": 1, "incomplete": True}
            summary_warning = "summary warning: missing summary file; counts may be incomplete"
    conflict_dir = vault_path / ".staging" / "pending-conflicts"
    conflicts = (
        sorted(
            p
            for p in conflict_dir.glob(f"{run_date}_*.json")
            if p.is_file() and not p.name.startswith(".tmp")
        )
        if conflict_dir.is_dir()
        else []
    )
    quarantine_files = _run_date_quarantine_files(vault_path, run_date)
    failure_dir = vault_path / ".staging" / "extraction-failures"
    failures = (
        sorted(failure_dir.glob(f"{run_date}_*.json")) if failure_dir.is_dir() else []
    )
    slow_skips = _run_date_extraction_skips(
        vault_path, run_date, reason="slow_extraction"
    )
    processed_files = int(summary.get("processed_files", 0) or 0)
    summary = {
        **summary,
        "conflicts": len(conflicts),
        "quarantined": len(quarantine_files),
        "slow_skipped": len(slow_skips),
        "candidate_files": _known_candidate_count(
            vault_path,
            run_date,
            processed_files=processed_files,
            quarantined=len(quarantine_files),
            slow_skipped=len(slow_skips),
            failures=len(failures),
        ),
    }
    lines = [f"End-of-Day Capture {run_date}"]
    if summary_warning:
        lines.append(summary_warning)
    lines.append(
        "capture accounting: "
        f"{summary.get('processed_files', 0)} of {summary.get('candidate_files', 0)} "
        f"processed, {summary.get('quarantined', 0)} quarantined, "
        f"{summary.get('slow_skipped', 0)} slow-skipped"
    )
    lines.append(f"sessions processed: {summary.get('processed_files', 0)}")
    lines.append(f"auto-written: {summary.get('auto_written', 0)}")
    lines.append(f"low-confidence review: {summary.get('low_confidence', 0)}")
    lines.append(f"conflicts: {summary.get('conflicts', 0)}")
    lines.append(f"deduped: {summary.get('deduped', 0)}")
    lines.append(f"preferences seen: {summary.get('preferences_seen', 0)}")
    lines.append(f"preferences skipped: {summary.get('skipped_preferences', 0)}")
    lines.append(f"errors: {summary.get('errors', 0)}")
    if failures:
        lines.append(f"extraction failures: {len(failures)}")
        for path in failures[:3]:
            try:
                failure = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                lines.append(f"- {path.name}")
                continue
            lines.append(
                f"- {Path(str(failure.get('session_path', path.name))).name}: "
                f"{failure.get('error', 'unknown error')}"
            )
    if quarantine_files:
        lines.append(f"quarantined extractions: {len(quarantine_files)}")
        for path in quarantine_files[:3]:
            try:
                quarantine = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                lines.append(f"- {path.name}: quarantined")
                continue
            lines.append(
                f"- {Path(str(quarantine.get('session_path', path.name))).name}: "
                f"{quarantine.get('reason', 'quarantined')} "
                f"{quarantine.get('error', 'unknown error')} "
                f"({path})"
            )
    if slow_skips:
        lines.append(f"slow extraction skips: {len(slow_skips)}")
        for path in slow_skips[:3]:
            try:
                skip = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                lines.append(f"- {path.name}: slow_extraction")
                continue
            lines.append(
                f"- {Path(str(skip.get('session_path', path.name))).name}: "
                f"{skip.get('reason', 'slow_extraction')} "
                f"{skip.get('error', 'unknown error')}"
            )
    all_rejected_skips = _run_date_extraction_skips(
        vault_path, run_date, reason=ALL_ITEMS_REJECTED_SKIP_REASON
    )
    if all_rejected_skips:
        lines.append(f"all-items-rejected skips: {len(all_rejected_skips)}")
        for path in all_rejected_skips[:3]:
            try:
                skip = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                lines.append(f"- {path.name}: {ALL_ITEMS_REJECTED_SKIP_REASON}")
                continue
            lines.append(
                f"- {Path(str(skip.get('session_path', path.name))).name}: "
                f"{skip.get('reason', ALL_ITEMS_REJECTED_SKIP_REASON)} "
                f"{skip.get('error', 'unknown error')}"
            )
    log_failure_dir = vault_path / ".staging" / "eod-log-failures"
    log_failures = sorted(log_failure_dir.glob("*.jsonl")) if log_failure_dir.is_dir() else []
    if log_failures:
        lines.append(f"log append failures: {len(log_failures)}")
    if conflicts:
        lines.append("")
        lines.append("Pending conflicts:")
        unreadable_conflicts = 0
        for path in conflicts[:5]:
            try:
                c = json.loads(path.read_text(encoding="utf-8"))
            except FileNotFoundError:
                unreadable_conflicts += 1
                continue
            except OSError as exc:
                unreadable_conflicts += 1
                print(f"eod-capture: warning: cannot read conflict {path}: {exc}", file=sys.stderr)
                continue
            except json.JSONDecodeError as exc:
                unreadable_conflicts += 1
                print(
                    f"eod-capture: warning: malformed conflict JSON {path}: {exc}",
                    file=sys.stderr,
                )
                continue
            lines.append(
                f"- {c.get('subject', '?')} {c.get('predicate', '?')} "
                f"(existing: {c.get('existing_value', '?')}, new: {c.get('new_value', '?')})"
            )
        if unreadable_conflicts:
            lines.append(f"unreadable conflicts: {unreadable_conflicts}")
        hidden_conflicts = len(conflicts) - 5
        if hidden_conflicts > 0:
            lines.append(f"... and {hidden_conflicts} more pending conflict(s) not shown")
    return "\n".join(lines)


def _default_vault() -> Path:
    return Path(
        os.environ.get("K2B_VAULT_PATH", str(Path.home() / "Projects" / "K2B-Vault"))
    ).expanduser()


def _default_capture_status_file() -> Path:
    return Path(
        os.environ.get(
            "K2B_CAPTURE_STATUS_FILE",
            str(Path.home() / ".local" / "state" / "k2b" / "capture-status.json"),
        )
    ).expanduser()


def _default_automatic_memory_state_root() -> Path:
    return Path(
        os.environ.get(
            "K2B_AUTOMATIC_MEMORY_STATE_ROOT",
            str(Path.home() / ".local" / "state" / "k2b" / "automatic-memory"),
        )
    ).expanduser()


@contextmanager
def _capture_status_lock(status_file: Path):
    """Serialize local status writers, failing loudly instead of hanging."""
    raw_timeout = os.environ.get("K2B_CAPTURE_STATUS_LOCK_TIMEOUT_SECONDS", "30")
    try:
        timeout = float(raw_timeout)
        if not math.isfinite(timeout):
            raise ValueError
        timeout = min(max(timeout, 0.1), 300.0)
    except ValueError:
        timeout = 30.0
    deadline = time.monotonic() + timeout
    status_file.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (status_file.parent / "discovery.lock").open("a") as lock:
        while True:
            try:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise TimeoutError(
                        f"capture status lock remained busy for {timeout:g}s"
                    )
                time.sleep(0.1)
        try:
            yield
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def _default_eod_date_hkt() -> str:
    # K2B convention (context_timezone-convention.md Rule 1): EOD CLI subcommands
    # process "the day that just ended." Default to yesterday HKT, not today, so
    # direct invocations without --date match the cron wrapper's behavior.
    return (datetime.now(HKT) - timedelta(days=1)).date().isoformat()


# Deprecated alias kept for back-compat with any external caller. Removed once
# git log confirms no consumers remain.
_today = _default_eod_date_hkt


def _failure_file_fingerprints(failure_dir: Path, run_date: str) -> dict[Path, str]:
    if not failure_dir.is_dir():
        return {}
    out: dict[Path, str] = {}
    for path in failure_dir.glob(f"{run_date}_*.json"):
        try:
            out[path] = _sha256_file(path)
        except OSError:
            out[path] = "<unreadable>"
    return out


def _run_date_extraction_skips(
    vault_path: Path, run_date: str, *, reason: str | None = None
) -> list[Path]:
    skip_dir = vault_path / ".staging" / "extraction-skips"
    if not skip_dir.is_dir():
        return []
    paths = sorted(skip_dir.glob(f"{run_date}_*.json"))
    if reason is None:
        return paths
    matched: list[Path] = []
    for path in paths:
        try:
            skip = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if skip.get("reason") == reason:
            matched.append(path)
    return matched


def _digest_health_issues(vault_path: Path, run_date: str) -> list[str]:
    issues: list[str] = []
    summary_path = vault_path / ".staging" / f"eod-capture-summary-{run_date}.json"
    if not summary_path.exists():
        issues.append("missing reconciliation summary")
    else:
        try:
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            issues.append(f"unreadable reconciliation summary: {exc}")
        else:
            try:
                error_count = int(summary.get("errors", 0))
            except (TypeError, ValueError):
                error_count = 1
            if error_count > 0:
                issues.append(f"reconciliation errors: {error_count}")
            try:
                processed_files = int(summary.get("processed_files", 0))
            except (TypeError, ValueError):
                processed_files = -1
            if processed_files >= 0:
                try:
                    candidate_sessions = discover_session_paths(run_date=run_date)
                except (OSError, ValueError):
                    candidate_sessions = []
                candidate_count = len(candidate_sessions)
                if processed_files == 0 and candidate_sessions:
                    issues.append(
                        f"zero-floor breach: 0 sessions processed but "
                        f"{candidate_count} JSONL(s) match {run_date} "
                        "(likely cron/data-day TZ mismatch; see E-2026-05-20-001)"
                    )
                elif processed_files > 0 and candidate_count > processed_files:
                    issues.append(
                        f"partial-miss suspected: {processed_files} processed, "
                        f"{candidate_count} candidates "
                        "(likely wrong run_date filter or extraction skip pattern; "
                        "see E-2026-05-20-001 follow-up)"
                    )
    failure_dir = vault_path / ".staging" / "extraction-failures"
    failures = (
        sorted(failure_dir.glob(f"{run_date}_*.json"))
        if failure_dir.is_dir()
        else []
    )
    if failures:
        issues.append(f"extraction failures: {len(failures)}")
    quarantines = _run_date_quarantine_files(vault_path, run_date)
    if quarantines:
        issues.append(
            "quarantined extractions: "
            f"{len(quarantines)} "
            "(manual recovery required; see .staging/eod-quarantine/)"
        )
    slow_skips = _run_date_extraction_skips(
        vault_path, run_date, reason="slow_extraction"
    )
    if slow_skips:
        issues.append(f"slow extraction skips: {len(slow_skips)}")
    all_rejected_skips = _run_date_extraction_skips(
        vault_path, run_date, reason=ALL_ITEMS_REJECTED_SKIP_REASON
    )
    if all_rejected_skips:
        issues.append(f"all-items-rejected skips: {len(all_rejected_skips)}")
    log_failure_dir = vault_path / ".staging" / "eod-log-failures"
    log_failures = (
        sorted(log_failure_dir.glob("*.jsonl"))
        if log_failure_dir.is_dir()
        else []
    )
    if log_failures:
        issues.append(f"log append failures: {len(log_failures)}")
    return issues


def _read_json_dict(path: Path) -> dict | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _sha256_file(path: Path, *, chunk_size: int = 1024 * 1024) -> str:
    """Hash a file without loading an entire Codex transcript into memory."""
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_file_with_retry(path: Path, *, attempts: int = 3) -> str:
    """Retry short-lived source read failures before reporting them."""
    last_error: OSError | None = None
    for attempt in range(attempts):
        try:
            return _sha256_file(path)
        except OSError as exc:
            last_error = exc
            if attempt + 1 < attempts:
                time.sleep(0.05)
    assert last_error is not None
    raise last_error


def _artifact_matches_source(
    path: Path, raw_source_sha256: str, *, completed_cursor: str | None = None
) -> bool:
    artifact = _read_json_dict(path)
    if completed_cursor:
        return bool(artifact and artifact.get("completed_cursor") == completed_cursor)
    return bool(
        artifact
        and raw_source_sha256
        and artifact.get("raw_source_sha256") == raw_source_sha256
    )


def _receipt_matches_source_and_extraction(
    receipt_path: Path,
    extraction_path: Path,
    raw_source_sha256: str,
    *,
    completed_cursor: str | None = None,
    review_revision: str | None = None,
) -> bool:
    receipt = _read_json_dict(receipt_path)
    if not receipt:
        return False
    if completed_cursor:
        if receipt.get("completed_cursor") != completed_cursor:
            return False
    elif not (
        raw_source_sha256 and receipt.get("raw_source_sha256") == raw_source_sha256
    ):
        return False
    if review_revision is not None and receipt.get("review_revision") != review_revision:
        return False
    if not extraction_path.exists():
        return False
    expected = str(receipt.get("extraction_sha256") or "")
    if not expected:
        return False
    try:
        return _sha256_file(extraction_path) == expected
    except OSError:
        return False


def _legacy_extraction_matches_completed_prefix(
    extraction_path: Path,
    *,
    prefix: dict,
    session_path: Path,
) -> bool:
    """Validate a pre-cursor extraction against the preserved legacy prefix."""
    extraction = _read_json_dict(extraction_path)
    if not extraction:
        return False
    if extraction.get("session_path") != str(session_path):
        return False
    extraction_cursor = extraction.get("completed_cursor")
    mode = extraction.get("completed_prefix_mode")
    if extraction_cursor:
        if mode != "legacy_whole_source" or extraction_cursor != prefix.get("cursor"):
            return False
    elif mode not in {None, "legacy_whole_source"}:
        return False
    prefix_hash = str(prefix.get("prefix_sha256") or "")
    raw_hash = str(extraction.get("raw_source_sha256") or "")
    if not prefix_hash or extraction.get("transcript_sha256") != prefix_hash:
        return False
    if not _existing_valid_extraction(
        extraction_path,
        transcript_sha256=prefix_hash,
        raw_source_sha256=raw_hash,
        completed_cursor=(str(extraction_cursor) if extraction_cursor else None),
        payload=str(prefix.get("transcript") or ""),
        session_path=session_path,
        dialogue_events=prefix.get("events"),
    ):
        return False
    return True


def _legacy_receipt_matches_completed_prefix(
    receipt_path: Path,
    extraction_path: Path,
    *,
    prefix: dict,
    session_path: Path,
) -> bool:
    """Recognize an exact pre-cursor extraction/receipt pair after source growth."""
    receipt = _read_json_dict(receipt_path)
    if not receipt:
        return False
    if not _legacy_extraction_matches_completed_prefix(
        extraction_path, prefix=prefix, session_path=session_path
    ):
        return False
    extraction = _read_json_dict(extraction_path)
    assert extraction is not None
    for field in (
        "session_path",
        "run_date",
        "raw_source_sha256",
        "transcript_sha256",
    ):
        if receipt.get(field) != extraction.get(field):
            return False
    if bool(receipt.get("completed_cursor")) != bool(
        extraction.get("completed_cursor")
    ):
        return False
    raw_hash = str(extraction.get("raw_source_sha256") or "")
    completed_cursor = (
        str(extraction.get("completed_cursor"))
        if extraction.get("completed_cursor")
        else None
    )
    return _receipt_matches_source_and_extraction(
        receipt_path,
        extraction_path,
        raw_hash,
        completed_cursor=completed_cursor,
        review_revision=(
            str(extraction.get("review_revision"))
            if extraction.get("review_revision") is not None
            else None
        ),
    )


def _session_artifact_paths(
    vault_path: Path, directory: str, run_date: str, session_path: Path
) -> list[Path]:
    base = f"{run_date}_{_safe_session_id(session_path)}"
    root = vault_path / ".staging" / directory
    return sorted(path for path in root.glob(f"{base}*.json") if path.is_file())


def capture_status(
    vault_path: Path,
    *,
    since: str,
    through: str,
    codex_root: Path | None = None,
    writer_role: str = "home",
) -> dict:
    """Return source-backed capture state without treating stale failures as current."""
    if writer_role not in {"home", "sjm-source-only"}:
        raise ValueError(f"unsupported capture writer role: {writer_role}")
    discovered = discover_session_paths_between(
        since=since,
        through=through,
        codex_root=codex_root,
    )
    sessions: list[dict] = []
    for run_date, paths in discovered.items():
        for session_path in paths:
            try:
                raw_source_sha256 = _sha256_file_with_retry(session_path)
                parsed = parse_completed_dialogue(session_path, source_host="home")
                prefix = _select_completed_prefix(parsed, run_date=run_date)
                prefix_mode = prefix.get("mode") if prefix else None
                completed_cursor = (
                    str(prefix.get("cursor", ""))
                    if prefix is not None and prefix_mode == "turn"
                    else None
                )
                legacy_transition = bool(
                    prefix
                    and prefix_mode == "legacy_whole_source"
                    and parsed.get("explicit_turn_markers")
                )
            except OSError:
                sessions.append(
                    {
                        "run_date": run_date,
                        "session_path": str(session_path),
                        "source_app": "codex_desktop",
                        "raw_source_sha256": None,
                        "status": "failed",
                        "extracted": False,
                        "retryable": True,
                        "error": "source_unreadable",
                    }
                )
                continue
            except ValueError:
                sessions.append(
                    {
                        "run_date": run_date,
                        "session_path": str(session_path),
                        "source_app": "codex_desktop",
                        "raw_source_sha256": raw_source_sha256,
                        "status": "failed",
                        "extracted": False,
                        "retryable": True,
                        "error": "source_parse_error",
                    }
                )
                continue
            extractions = _session_artifact_paths(
                vault_path, "extractions", run_date, session_path
            )
            failures = _session_artifact_paths(
                vault_path, "extraction-failures", run_date, session_path
            )
            quarantines = _session_artifact_paths(
                vault_path, "eod-quarantine", run_date, session_path
            )
            skips = _session_artifact_paths(
                vault_path, "extraction-skips", run_date, session_path
            )
            extraction_matches_paths = [
                path for path in extractions
                if _artifact_matches_source(
                    path, raw_source_sha256, completed_cursor=completed_cursor
                )
                or (
                    legacy_transition
                    and prefix is not None
                    and _legacy_extraction_matches_completed_prefix(
                        path, prefix=prefix, session_path=session_path
                    )
                )
            ]
            matching_receipts: list[tuple[Path, dict | None]] = []
            for extraction in extraction_matches_paths:
                receipt = _reconciliation_receipt_path(vault_path, extraction)
                extraction_data = _read_json_dict(extraction)
                review_revision = (
                    str(extraction_data.get("review_revision"))
                    if extraction_data
                    and extraction_data.get("review_revision") is not None
                    else None
                )
                ordinary_match = _receipt_matches_source_and_extraction(
                    receipt,
                    extraction,
                    raw_source_sha256,
                    completed_cursor=completed_cursor,
                    review_revision=review_revision,
                )
                legacy_match = bool(
                    legacy_transition
                    and prefix is not None
                    and _legacy_receipt_matches_completed_prefix(
                        receipt,
                        extraction,
                        prefix=prefix,
                        session_path=session_path,
                    )
                )
                if ordinary_match or legacy_match:
                    matching_receipts.append((receipt, _read_json_dict(receipt)))
            receipt_matches = bool(extraction_matches_paths) and (
                len(matching_receipts) == len(extraction_matches_paths)
            )
            receipt_data = matching_receipts[-1][1] if matching_receipts else None
            extraction_matches = bool(extraction_matches_paths)
            # Successful current artifacts clear matching diagnostics when
            # written. Defensive precedence still favors terminal diagnostics
            # if a cleanup I/O failure left both behind.
            if receipt_matches:
                state = (
                    "skipped"
                    if receipt_data and receipt_data.get("status") == "reviewed_empty"
                    else "reconciled"
                )
                retryable = False
            elif any(_artifact_matches_source(
                path, raw_source_sha256, completed_cursor=completed_cursor
            ) for path in failures):
                state = "failed"
                retryable = True
            elif any(
                _artifact_matches_source(
                    path, raw_source_sha256, completed_cursor=completed_cursor
                )
                or (completed_cursor is None and _read_json_dict(path) is not None)
                for path in quarantines
            ):
                state = "failed"
                retryable = True
            elif any(_artifact_matches_source(
                path, raw_source_sha256, completed_cursor=completed_cursor
            ) for path in skips):
                state = "skipped"
                retryable = True
            elif extraction_matches:
                state = "waiting"
                retryable = True
            else:
                state = "waiting"
                retryable = True
            sessions.append(
                {
                    "run_date": run_date,
                    "session_path": str(session_path),
                    "source_app": "codex_desktop",
                    "raw_source_sha256": raw_source_sha256,
                    "status": state,
                    "extracted": receipt_matches or extraction_matches,
                    "retryable": retryable,
                    "provenance_status": parsed.get("provenance_status", "unknown"),
                }
            )

    counts = {
        "discovered": len(sessions),
        "waiting": sum(item["status"] == "waiting" for item in sessions),
        "failed": sum(item["status"] == "failed" for item in sessions),
        "skipped": sum(item["status"] == "skipped" for item in sessions),
        "reconciled": sum(item["status"] == "reconciled" for item in sessions),
    }

    def latest(statuses: set[str]) -> str | None:
        dates = [item["run_date"] for item in sessions if item["status"] in statuses]
        return max(dates) if dates else None

    return {
        "since": since,
        "through": through,
        "writer_role": writer_role,
        "run_status": "complete",
        "counts_valid": True,
        "checked_at": datetime.now(timezone.utc).isoformat(timespec="microseconds"),
        "reconciliation": (
            "enabled_home_writer"
            if writer_role == "home"
            else "disabled_on_source_only_host"
        ),
        "counts": counts,
        "last_discovered": max((item["run_date"] for item in sessions), default=None),
        "last_extracted": max(
            (item["run_date"] for item in sessions if item["extracted"]),
            default=None,
        ),
        "last_reconciled": latest({"reconciled"}),
        "sessions": sessions,
    }


def run_catch_up(
    vault_path: Path,
    *,
    since: str,
    through: str,
    codex_root: Path | None = None,
    extract_func: ExtractFunc | None = None,
    reconcile: bool = True,
    status_file: Path | None = None,
    writer_role: str | None = None,
    _status_lock_held: bool = False,
) -> dict:
    """Process every missed date, with shared-vault writes restricted to home."""
    role = writer_role or ("home" if reconcile else "sjm-source-only")
    if status_file is not None and role == "sjm-source-only":
        resolved_status = status_file.resolve()
        resolved_vault = vault_path.resolve()
        if resolved_status == resolved_vault or resolved_status.is_relative_to(
            resolved_vault
        ):
            raise ValueError("source-only status file must stay outside the shared vault")
    if status_file is not None and not _status_lock_held:
        with _capture_status_lock(status_file):
            try:
                return run_catch_up(
                    vault_path,
                    since=since,
                    through=through,
                    codex_root=codex_root,
                    extract_func=extract_func,
                    reconcile=reconcile,
                    status_file=status_file,
                    writer_role=role,
                    _status_lock_held=True,
                )
            except (OSError, RuntimeError, ValueError) as exc:
                failure_status = {
                    "since": since,
                    "through": through,
                    "writer_role": role,
                    "run_status": "failed",
                    "counts_valid": False,
                    "counts": None,
                    "sessions": [],
                    "error": _sanitize_log_value(exc),
                    "checked_at": datetime.now(timezone.utc).isoformat(
                        timespec="microseconds"
                    ),
                }
                try:
                    _atomic_write_json(status_file, failure_status)
                    status_file.chmod(0o600)
                except OSError:
                    pass
                raise
    root = _resolve_codex_root(codex_root)
    if reconcile and role != "home":
        raise ValueError("reconciliation requires the home writer role")
    if not reconcile and role != "sjm-source-only":
        raise ValueError("source-only discovery requires the sjm-source-only role")
    discovered = discover_session_paths_between(
        since=since,
        through=through,
        codex_root=root,
    )
    result: dict = {"since": since, "through": through, "dates": {}}
    for run_date, sessions in discovered.items():
        if reconcile:
            run_job_a(
                sessions,
                vault_path=vault_path,
                run_date=run_date,
                extract_func=extract_func,
                codex_root=root,
            )
            staged = len(_iter_extraction_files(vault_path, run_date))
        else:
            # SJM never calls the provider or writes shared-vault staging. Its
            # durable local status file is the pending-operation evidence until
            # the raw source reaches the home writer through an approved route.
            staged = 0
        day_result: dict = {
            "discovered": len(sessions),
            "staged": staged,
            "pending_local": 0 if reconcile else len(sessions),
            "reconciliation_attempted": False,
            "reconciled": False,
        }
        if reconcile and staged:
            summary = reconcile_extractions(vault_path, run_date=run_date)
            day_result["summary"] = summary
            day_result["reconciliation_attempted"] = True
            day_result["reconciled"] = bool(
                int(summary.get("errors", 0)) == 0
                and int(summary.get("candidate_files", 0)) > 0
                and int(summary.get("reconciled_files", 0))
                == int(summary.get("candidate_files", 0))
            )
        result["dates"][run_date] = day_result
    if status_file is not None:
        status = capture_status(
            vault_path,
            since=since,
            through=through,
            codex_root=root,
            writer_role=role,
        )
        reconciliation_failures = [
            {
                "run_date": run_date,
                "errors": int(day.get("summary", {}).get("errors", 0) or 0),
                "quarantined": int(
                    day.get("summary", {}).get("quarantined", 0) or 0
                ),
                "reconciled": bool(day.get("reconciled", False)),
            }
            for run_date, day in result["dates"].items()
            if day.get("reconciliation_attempted")
            and (
                not day.get("reconciled")
                or int(day.get("summary", {}).get("errors", 0) or 0) > 0
                or int(day.get("summary", {}).get("quarantined", 0) or 0) > 0
            )
        ]
        status["reconciliation_failures"] = reconciliation_failures
        if reconciliation_failures:
            status["run_status"] = "failed"
        _atomic_write_json(status_file, status)
        result["status"] = status
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="K2B end-of-day capture")
    sub = parser.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("job-a")
    a.add_argument("--date", default=_default_eod_date_hkt())
    a.add_argument("--vault", type=Path, default=_default_vault())
    a.add_argument("--session", action="append", type=Path, default=[])
    a.add_argument("--codex-root", type=Path, default=None)
    b = sub.add_parser("job-b")
    b.add_argument("--date", default=_default_eod_date_hkt())
    b.add_argument("--vault", type=Path, default=_default_vault())
    d = sub.add_parser("digest")
    d.add_argument("--date", default=_default_eod_date_hkt())
    d.add_argument("--vault", type=Path, default=_default_vault())
    catch_up = sub.add_parser("catch-up")
    catch_up.add_argument("--since", required=True)
    catch_up.add_argument("--through", default=_default_eod_date_hkt())
    catch_up.add_argument("--vault", type=Path, default=_default_vault())
    catch_up.add_argument("--codex-root", type=Path, default=None)
    catch_up.add_argument("--source-only", action="store_true")
    status_parser = sub.add_parser("status")
    status_parser.add_argument("--since", required=True)
    status_parser.add_argument("--through", default=_default_eod_date_hkt())
    status_parser.add_argument("--vault", type=Path, default=_default_vault())
    status_parser.add_argument("--codex-root", type=Path, default=None)
    status_parser.add_argument(
        "--writer-role",
        choices=("home", "sjm-source-only"),
        default=os.environ.get("K2B_CAPTURE_WRITER_ROLE", "home"),
    )
    discover = sub.add_parser("discover", help="local queue/status only; never calls a provider or writes the vault")
    discover.add_argument("--since", default="2026-07-26")
    discover.add_argument("--through", default=datetime.now(HKT).date().isoformat())
    discover.add_argument("--vault", type=Path, default=_default_vault())
    discover.add_argument("--codex-root", type=Path, default=None)
    discover.add_argument("--writer-role", choices=("home", "sjm-source-only"), required=True)
    reviewed = sub.add_parser("stage-reviewed", help="stage one interactive extraction without any provider call")
    reviewed.add_argument("--date", required=True)
    reviewed.add_argument("--vault", type=Path, default=_default_vault())
    reviewed_source = reviewed.add_mutually_exclusive_group(required=True)
    reviewed_source.add_argument("--session", action="append", type=Path)
    reviewed_source.add_argument("--source-bundle", type=Path)
    reviewed.add_argument("--codex-root", type=Path, default=None)
    reviewed.add_argument("--reviewed-json", type=Path, required=True)
    export_source = sub.add_parser("export-source", help="write one private redacted source bundle for interactive transfer")
    export_source.add_argument("--source-host", choices=("home", "sjm"), required=True)
    export_source.add_argument("--session", type=Path, required=True)
    export_source.add_argument("--codex-root", type=Path, default=None)
    export_source.add_argument("--output", type=Path, required=True)
    memory_queue = sub.add_parser(
        "memory-queue-reviewed",
        help="validate reviewed source evidence and queue durable memory locally",
    )
    memory_queue.add_argument("--date", required=True)
    memory_queue.add_argument("--source-bundle", type=Path, required=True)
    memory_queue.add_argument("--reviewed-json", type=Path, required=True)
    memory_queue.add_argument(
        "--state-root", type=Path, default=_default_automatic_memory_state_root()
    )
    memory_queue.add_argument(
        "--writer-role", choices=("home", "sjm-source-only"), required=True
    )
    memory_worklist = sub.add_parser(
        "memory-worklist",
        help="prepare bounded eligible completed-turn sources for native extraction",
    )
    memory_worklist.add_argument("--since", required=True)
    memory_worklist.add_argument("--through", required=True)
    memory_worklist.add_argument("--codex-root", type=Path, required=True)
    memory_worklist.add_argument(
        "--state-root", type=Path, default=_default_automatic_memory_state_root()
    )
    memory_worklist.add_argument("--limit", type=int, default=10)
    memory_worklist.add_argument("--now")
    memory_worklist.add_argument(
        "--writer-role", choices=("home", "sjm-source-only"), required=True
    )
    memory_input = sub.add_parser(
        "memory-extraction-input",
        help="prepare complete current-turn native input with bounded prior context",
    )
    memory_input.add_argument("--work-id", required=True)
    memory_input.add_argument("--state-root", type=Path, default=_default_automatic_memory_state_root())
    memory_input.add_argument("--writer-role", choices=("home", "sjm-source-only"), required=True)
    memory_record = sub.add_parser(
        "memory-record-extraction",
        help="validate and durably queue one native reviewed extraction",
    )
    memory_record.add_argument("--work-id", required=True)
    memory_record.add_argument("--reviewed-json", type=Path, required=True)
    memory_record.add_argument("--now")
    memory_record.add_argument(
        "--state-root", type=Path, default=_default_automatic_memory_state_root()
    )
    memory_record.add_argument(
        "--writer-role", choices=("home", "sjm-source-only"), required=True
    )
    memory_list = sub.add_parser(
        "memory-list-deliverable",
        help="list bounded retry-eligible SJM memory deliveries",
    )
    memory_list.add_argument(
        "--state-root", type=Path, default=_default_automatic_memory_state_root()
    )
    memory_list.add_argument("--limit", type=int, default=100)
    memory_list.add_argument("--now")
    memory_list.add_argument(
        "--writer-role", choices=("home", "sjm-source-only"), required=True
    )
    memory_export = sub.add_parser(
        "memory-export-envelope",
        help="export one currently retry-eligible SJM memory envelope",
    )
    memory_export.add_argument("--delivery-id", required=True)
    memory_export.add_argument(
        "--state-root", type=Path, default=_default_automatic_memory_state_root()
    )
    memory_export.add_argument("--now")
    memory_export.add_argument(
        "--writer-role", choices=("home", "sjm-source-only"), required=True
    )
    memory_ack = sub.add_parser(
        "memory-record-ack",
        help="record one exact Home acceptance acknowledgement on SJM",
    )
    memory_ack_source = memory_ack.add_mutually_exclusive_group(required=True)
    memory_ack_source.add_argument("--ack-json", type=Path)
    memory_ack_source.add_argument("--ack-stdin", action="store_true")
    memory_ack.add_argument(
        "--state-root", type=Path, default=_default_automatic_memory_state_root()
    )
    memory_ack.add_argument("--now")
    memory_ack.add_argument(
        "--writer-role", choices=("home", "sjm-source-only"), required=True
    )
    memory_accept = sub.add_parser(
        "memory-accept", help="accept one validated delivery into the Home durable inbox"
    )
    memory_accept.add_argument("--envelope", type=Path, required=True)
    memory_accept.add_argument(
        "--state-root", type=Path, default=_default_automatic_memory_state_root()
    )
    memory_accept.add_argument(
        "--writer-role", choices=("home", "sjm-source-only"), required=True
    )
    memory_reconcile = sub.add_parser(
        "memory-reconcile", help="reconcile one accepted Home delivery idempotently"
    )
    memory_reconcile.add_argument("--delivery-id", required=True)
    memory_reconcile.add_argument(
        "--state-root", type=Path, default=_default_automatic_memory_state_root()
    )
    memory_reconcile.add_argument("--memory-path", type=Path)
    memory_reconcile.add_argument(
        "--writer-role", choices=("home", "sjm-source-only"), required=True
    )
    memory_recall = sub.add_parser(
        "memory-recall", help="recall one source-backed durable or provisional key"
    )
    memory_recall.add_argument("--key", required=True)
    memory_recall.add_argument(
        "--state-root", type=Path, default=_default_automatic_memory_state_root()
    )
    memory_recall.add_argument("--memory-path", type=Path)
    memory_recall.add_argument(
        "--provisional-root",
        type=Path,
        help=(
            "explicitly include pending local outbox items; Home recall is "
            "durable-only by default"
        ),
    )
    memory_recall.add_argument(
        "--writer-role", choices=("home", "sjm-source-only"), required=True
    )
    memory_status_parser = sub.add_parser(
        "memory-status", help="report durable memory state without implying runner health"
    )
    memory_status_parser.add_argument(
        "--state-root", type=Path, default=_default_automatic_memory_state_root()
    )
    memory_status_parser.add_argument("--memory-path", type=Path)
    memory_status_parser.add_argument(
        "--automation-root",
        type=Path,
        help="observe native job registration under this automations root",
    )
    memory_status_parser.add_argument(
        "--writer-role", choices=("home", "sjm-source-only"), required=True
    )
    memory_backlog_parser = sub.add_parser(
        "memory-backlog", help="read eligible source coverage and validated extraction lag")
    memory_backlog_parser.add_argument("--since", required=True)
    memory_backlog_parser.add_argument("--through", required=True)
    memory_backlog_parser.add_argument("--codex-root", type=Path, required=True)
    memory_backlog_parser.add_argument("--state-root", type=Path,
                                       default=_default_automatic_memory_state_root())
    memory_backlog_parser.add_argument("--writer-role", choices=("home", "sjm-source-only"), required=True)
    memory_backlog_parser.add_argument("--max-scan-seconds", type=float, default=60)
    memory_run_parser = sub.add_parser("memory-record-run", help="record canonical native run evidence")
    memory_run_parser.add_argument("--state-root", type=Path, default=_default_automatic_memory_state_root())
    memory_run_parser.add_argument("--writer-role", choices=("home", "sjm-source-only"), required=True)
    memory_run_parser.add_argument("--started-at", required=True)
    memory_run_parser.add_argument("--work-id", action="append", default=[])
    memory_run_parser.add_argument("--deferred-work-id", action="append", default=[])
    memory_run_parser.add_argument("--deferred-reason",
                                   choices=MEMORY_RUN_DEFER_REASONS, default="byte_budget")
    memory_run_parser.add_argument("--exceptions-json", type=Path)
    memory_run_parser.add_argument("--drain-json", type=Path)
    memory_run_parser.add_argument("--publication-json", type=Path)
    memory_drain = sub.add_parser(
        "memory-home-drain",
        help="durably accept and reconcile bounded Home and optional SJM deliveries",
    )
    memory_drain.add_argument(
        "--state-root", type=Path, default=_default_automatic_memory_state_root()
    )
    memory_drain.add_argument("--memory-path", type=Path)
    memory_drain.add_argument("--limit", type=int, default=100)
    memory_drain.add_argument("--now")
    memory_drain.add_argument("--pull-sjm", action="store_true")
    memory_drain.add_argument("--publish", action="store_true")
    memory_drain.add_argument("--vault-root", type=Path)
    memory_drain.add_argument(
        "--writer-role", choices=("home", "sjm-source-only"), required=True
    )
    memory_publish = sub.add_parser(
        "memory-publish",
        help="publish reconciled memory through the gated Home writer adapter",
    )
    memory_publish.add_argument(
        "--state-root", type=Path, default=_default_automatic_memory_state_root()
    )
    memory_publish.add_argument("--memory-path", type=Path)
    memory_publish.add_argument("--vault-root", type=Path, required=True)
    memory_publish.add_argument(
        "--writer-role", choices=("home", "sjm-source-only"), required=True
    )
    args = parser.parse_args(argv)

    if args.cmd == "memory-worklist":
        try:
            result = build_memory_worklist(
                state_root=args.state_root,
                codex_root=args.codex_root,
                since=args.since,
                through=args.through,
                writer_role=args.writer_role,
                limit=args.limit,
                now=args.now,
            )
        except (OSError, ValueError, RuntimeError, PermissionError) as exc:
            print(f"eod-capture: memory worklist failed: {_sanitize_log_value(exc)}", file=sys.stderr)
            return 2
        print(json.dumps(result, sort_keys=True))
        return 3 if result["status"] == "needs_attention" else 0

    if args.cmd == "memory-extraction-input":
        try:
            result = prepare_memory_extraction_input(
                state_root=args.state_root, work_id=args.work_id, writer_role=args.writer_role
            )
        except (OSError, ValueError, RuntimeError, KeyError, TypeError) as exc:
            print(f"eod-capture: memory input failed: {_sanitize_log_value(exc)}", file=sys.stderr)
            return 2
        print(json.dumps(result, sort_keys=True))
        return 0 if result["status"] == "ready" else 3

    if args.cmd == "memory-record-extraction":
        try:
            reviewed_data = json.loads(args.reviewed_json.read_text(encoding="utf-8"))
            result = record_memory_extraction(
                state_root=args.state_root,
                work_id=args.work_id,
                reviewed=reviewed_data,
                writer_role=args.writer_role,
                now=args.now,
            )
        except (OSError, ValueError, RuntimeError, PermissionError, json.JSONDecodeError) as exc:
            print(f"eod-capture: memory extraction record failed: {_sanitize_log_value(exc)}", file=sys.stderr)
            return 2
        print(json.dumps(result, sort_keys=True))
        return (
            3
            if result["status"]
            in {"retryable", "retry_backoff", "needs_attention"}
            else 0
        )

    if args.cmd == "memory-list-deliverable":
        try:
            result = list_memory_deliverables(
                args.state_root,
                writer_role=args.writer_role,
                limit=args.limit,
                now=args.now,
            )
        except (OSError, ValueError, RuntimeError, PermissionError) as exc:
            print(f"eod-capture: memory delivery list failed: {_sanitize_log_value(exc)}", file=sys.stderr)
            return 2
        print(json.dumps(result, sort_keys=True))
        return 0

    if args.cmd == "memory-export-envelope":
        try:
            result = export_memory_envelope(
                args.state_root,
                args.delivery_id,
                writer_role=args.writer_role,
                now=args.now,
            )
        except (OSError, ValueError, RuntimeError, PermissionError) as exc:
            print(f"eod-capture: memory envelope export failed: {_sanitize_log_value(exc)}", file=sys.stderr)
            return 2
        print(json.dumps(result, sort_keys=True))
        return 0

    if args.cmd == "memory-record-ack":
        try:
            ack_text = (
                sys.stdin.read()
                if args.ack_stdin
                else args.ack_json.read_text(encoding="utf-8")
            )
            acknowledgement = json.loads(ack_text)
            result = record_memory_acknowledgement(
                args.state_root,
                acknowledgement,
                writer_role=args.writer_role,
                now=args.now,
            )
        except (OSError, ValueError, RuntimeError, PermissionError, json.JSONDecodeError) as exc:
            print(f"eod-capture: memory acknowledgement failed: {_sanitize_log_value(exc)}", file=sys.stderr)
            return 2
        print(json.dumps(result, sort_keys=True))
        return 0

    if args.cmd == "memory-home-drain":
        try:
            result = drain_home_memory(
                args.state_root,
                writer_role=args.writer_role,
                pull_sjm=args.pull_sjm,
                limit=args.limit,
                now=args.now,
                memory_path=args.memory_path,
                publish=args.publish,
                vault_root=args.vault_root,
            )
        except (OSError, ValueError, RuntimeError, PermissionError, json.JSONDecodeError) as exc:
            print(f"eod-capture: memory drain failed: {_sanitize_log_value(exc)}", file=sys.stderr)
            return 2
        print(json.dumps(result, sort_keys=True))
        return 0

    if args.cmd == "memory-publish":
        try:
            result = publish_memory_state(
                memory_state_path=args.memory_path or args.state_root / "memory.json",
                state_root=args.state_root,
                vault_root=args.vault_root,
                writer_role=args.writer_role,
            )
        except (OSError, ValueError, RuntimeError, PermissionError, json.JSONDecodeError) as exc:
            print(f"eod-capture: memory publication failed: {_sanitize_log_value(exc)}", file=sys.stderr)
            return 2
        print(json.dumps(result, sort_keys=True))
        return 0

    if args.cmd == "memory-queue-reviewed":
        try:
            bundle = json.loads(args.source_bundle.read_text(encoding="utf-8"))
            reviewed_data = json.loads(args.reviewed_json.read_text(encoding="utf-8"))
            result = queue_reviewed_memory(
                bundle,
                reviewed_data,
                state_root=args.state_root,
                run_date=args.date,
                writer_role=args.writer_role,
            )
        except (OSError, ValueError, RuntimeError, PermissionError, json.JSONDecodeError) as exc:
            print(f"eod-capture: memory queue failed: {_sanitize_log_value(exc)}", file=sys.stderr)
            return 2
        print(json.dumps(result, sort_keys=True))
        return 3 if result["status"] == "not_queued" else 0

    if args.cmd == "memory-accept":
        try:
            envelope = json.loads(args.envelope.read_text(encoding="utf-8"))
            result = accept_memory_envelope(
                args.state_root, envelope, writer_role=args.writer_role
            )
        except (OSError, ValueError, RuntimeError, PermissionError, json.JSONDecodeError) as exc:
            print(f"eod-capture: memory acceptance failed: {_sanitize_log_value(exc)}", file=sys.stderr)
            return 2
        print(json.dumps(result, sort_keys=True))
        return 0

    if args.cmd == "memory-reconcile":
        try:
            result = reconcile_memory_delivery(
                args.state_root,
                args.delivery_id,
                writer_role=args.writer_role,
                memory_path=args.memory_path,
            )
        except (OSError, ValueError, RuntimeError, PermissionError, json.JSONDecodeError) as exc:
            print(f"eod-capture: memory reconciliation failed: {_sanitize_log_value(exc)}", file=sys.stderr)
            return 2
        print(json.dumps(result, sort_keys=True))
        return 0

    if args.cmd == "memory-recall":
        try:
            result = recall_memory(
                args.state_root,
                args.key,
                writer_role=args.writer_role,
                provisional_root=args.provisional_root,
                memory_path=args.memory_path,
            )
        except (OSError, ValueError, RuntimeError, PermissionError) as exc:
            print(f"eod-capture: memory recall failed: {_sanitize_log_value(exc)}", file=sys.stderr)
            return 2
        print(json.dumps(result, sort_keys=True))
        return 0

    if args.cmd == "memory-record-run":
        try:
            exceptions = (_read_memory_json_list(args.exceptions_json, "exceptions JSON")
                          if args.exceptions_json else None)
            drain_result = (_read_memory_json(args.drain_json, "drain JSON")
                            if args.drain_json else None)
            publication_result = (_read_memory_json(args.publication_json, "publication JSON")
                                  if args.publication_json else None)
        except (OSError, ValueError, RuntimeError) as exc:
            # A valid start/role must still leave durable failed run evidence,
            # never an evidence gap the status reader cannot distinguish from
            # "the job never ran".
            try:
                failed = record_failed_native_memory_run(
                    state_root=args.state_root, writer_role=args.writer_role,
                    started_at=args.started_at, diagnostic="command_json_unreadable",
                    work_ids=args.work_id)
            except (OSError, ValueError, RuntimeError, TypeError):
                failed = None
            print(f"eod-capture: run command JSON failed: {_sanitize_log_value(exc)}",
                  file=sys.stderr)
            if failed is not None:
                print(json.dumps(failed, sort_keys=True))
            return 2
        try:
            result = record_native_memory_run(state_root=args.state_root,
                writer_role=args.writer_role, started_at=args.started_at, work_ids=args.work_id,
                deferred_work_ids=args.deferred_work_id, deferred_reason=args.deferred_reason,
                exceptions=exceptions, drain_result=drain_result,
                publication_result=publication_result)
        except (OSError, ValueError, RuntimeError, TypeError) as exc:
            print(f"eod-capture: run receipt failed: {_sanitize_log_value(exc)}", file=sys.stderr)
            return 2
        print(json.dumps(result, sort_keys=True))
        if result.get("outcome") == "completed":
            return 0
        return 2 if result.get("status") == "failed" else 3

    if args.cmd == "memory-backlog":
        try:
            result = memory_backlog(state_root=args.state_root, codex_root=args.codex_root,
                since=args.since, through=args.through, writer_role=args.writer_role,
                max_scan_seconds=args.max_scan_seconds)
        except (OSError, ValueError, RuntimeError) as exc:
            print(f"eod-capture: memory backlog failed: {_sanitize_log_value(exc)}", file=sys.stderr)
            return 2
        print(json.dumps(result, sort_keys=True))
        return 3 if result["status"] == "needs_attention" else 0

    if args.cmd == "memory-status":
        try:
            result = memory_status(
                args.state_root,
                writer_role=args.writer_role,
                memory_path=args.memory_path,
                automation_root=args.automation_root,
            )
        except (OSError, ValueError, RuntimeError, PermissionError) as exc:
            print(f"eod-capture: memory status failed: {_sanitize_log_value(exc)}", file=sys.stderr)
            return 2
        print(json.dumps(result, sort_keys=True))
        return 0

    if args.cmd == "discover":
        status_file = _default_capture_status_file()
        try:
            # The same stable local lock also covers explicit catch-up status
            # writes, so an interactive run and scheduled discovery cannot
            # replace capture-status.json concurrently.
            with _capture_status_lock(status_file):
                status = capture_status(args.vault, since=args.since, through=args.through,
                                        codex_root=args.codex_root, writer_role=args.writer_role)
                status.update(capture_mode="interactive", automatic_extraction="disabled",
                              checked_at=datetime.now(timezone.utc).isoformat())
                discovery_run_id = os.environ.get("K2B_DISCOVERY_RUN_ID", "").strip()
                if discovery_run_id:
                    status["discovery_run_id"] = discovery_run_id
                _atomic_write_json(status_file, status)
                status_file.chmod(0o600)
            print(json.dumps({"counts": status["counts"], "capture_mode": "interactive"}))
            return 1 if status["counts"]["failed"] else 0
        except (OSError, ValueError) as exc:
            print(f"eod-capture: discovery failed: {_sanitize_log_value(exc)}", file=sys.stderr)
            return 1

    if args.cmd == "export-source":
        try:
            bundle = build_source_bundle(
                args.session, source_host=args.source_host, codex_root=args.codex_root
            )
            _atomic_write_json(args.output, bundle)
            args.output.chmod(0o600)
            print(json.dumps({
                "output": str(args.output),
                "source_host": bundle["source_host"],
                "run_date": bundle["run_date"],
                "raw_source_sha256": bundle["raw_source_sha256"],
            }, sort_keys=True))
            return 0
        except (OSError, ValueError) as exc:
            print(f"eod-capture: source export failed: {_sanitize_log_value(exc)}", file=sys.stderr)
            return 2

    if args.cmd in {"job-a", "stage-reviewed"}:
        if args.cmd == "stage-reviewed" and args.source_bundle:
            try:
                bundle = json.loads(args.source_bundle.read_text(encoding="utf-8"))
                reviewed_data = json.loads(args.reviewed_json.read_text(encoding="utf-8"))
                out_path = stage_reviewed_bundle(
                    bundle, reviewed_data, vault_path=args.vault, run_date=args.date
                )
                print(out_path)
                return 0
            except (OSError, ValueError, PermissionError, json.JSONDecodeError) as exc:
                print(f"eod-capture: {_sanitize_log_value(exc)}", file=sys.stderr)
                return 2
        # Resolve the Codex root only from the explicit CLI flag, the env var, or
        # the canonical default. Never infer it from a candidate session path.
        codex_root = _resolve_codex_root(args.codex_root)
        sessions = getattr(args, "session", None) or discover_session_paths(
            run_date=args.date,
            codex_root=codex_root,
        )
        if args.session:
            for sp in sessions:
                resolved_sp = sp.resolve()
                if not resolved_sp.is_relative_to(codex_root):
                    print(
                        f"eod-capture: rejected session outside Codex root: {sp}",
                        file=sys.stderr,
                    )
                    return 2
                source_app = detect_source_app(resolved_sp, codex_root=codex_root)
                if source_app == "unknown":
                    print(
                        f"eod-capture: rejected unknown source: {sp}",
                        file=sys.stderr,
                    )
                    return 2
                if not _is_k2b_scope(resolved_sp):
                    print(
                        f"eod-capture: rejected session outside exact K2B/K2Bi scope: {sp}",
                        file=sys.stderr,
                    )
                    return 2
                session_dates = _session_relevant_dates(resolved_sp)
                if not session_dates:
                    try:
                        rel_parts = resolved_sp.relative_to(codex_root).parts
                    except ValueError:
                        rel_parts = ()
                    if len(rel_parts) >= 4:
                        candidate_date = "-".join(rel_parts[:3])
                        try:
                            session_dates = {
                                date.fromisoformat(candidate_date).isoformat()
                            }
                        except ValueError:
                            session_dates = set()
                if args.date not in session_dates:
                    detail = ",".join(sorted(session_dates)) or "unknown"
                    print(
                        f"eod-capture: rejected session date {detail}; "
                        f"expected {args.date}: {sp}",
                        file=sys.stderr,
                    )
                    return 2
        extract_func = None
        reviewed_cursor: str | None = None
        review_revision: str | None = None
        if args.cmd == "stage-reviewed":
            if _source_only_writer():
                print("eod-capture: reviewed staging requires the home writer", file=sys.stderr)
                return 2
            try:
                if len(sessions) != 1:
                    raise ValueError("reviewed staging requires exactly one source")
                reviewed_data = json.loads(args.reviewed_json.read_text(encoding="utf-8"))
                if not isinstance(reviewed_data, dict):
                    raise ValueError("reviewed extraction must be an object")
                if not isinstance(reviewed_data.get("items"), list):
                    raise ValueError("reviewed extraction requires an explicit items list")
                if (
                    not reviewed_data["items"]
                    and reviewed_data.get("reviewed_empty") is not True
                ):
                    raise ValueError(
                        "empty reviewed extraction requires explicit reviewed_empty=true"
                    )
                expected_hash = reviewed_data.get("raw_source_sha256")
                modern_review = reviewed_data.get("completed_prefix_mode") == "turn"
                parsed_review = parse_completed_dialogue(
                    sessions[0], source_host="home"
                )
                if modern_review:
                    reviewed_cursor = str(reviewed_data.get("completed_cursor") or "")
                    if not reviewed_cursor:
                        raise ValueError("reviewed extraction missing completed_cursor")
                    reviewed_prefix = _select_completed_prefix(
                        parsed_review,
                        run_date=args.date,
                        completed_cursor=reviewed_cursor,
                    )
                    if reviewed_prefix is None:
                        raise ValueError(
                            "reviewed extraction completed_cursor does not match requested date"
                        )
                    expected_identity = {
                        "host_id": parsed_review["host_id"],
                        "session_id": parsed_review["session_id"],
                        "completed_prefix_sha256": reviewed_prefix["prefix_sha256"],
                        "completed_date": reviewed_prefix["completed_date"],
                    }
                    for field, expected in expected_identity.items():
                        if reviewed_data.get(field) != expected:
                            raise ValueError(
                                f"reviewed extraction {field} does not match completed prefix"
                            )
                    actual_transcript = str(reviewed_prefix["transcript"])
                else:
                    if expected_hash != _sha256_file(sessions[0]):
                        raise ValueError(
                            "reviewed extraction source hash does not match current source"
                        )
                    reviewed_prefix = _select_completed_prefix(
                        parsed_review, run_date=args.date
                    )
                    actual_transcript = (
                        str(reviewed_prefix.get("transcript", ""))
                        if reviewed_prefix
                        else ""
                    )
                expected_transcript_hash = reviewed_data.get("transcript_sha256")
                actual_transcript_hash = hashlib.sha256(
                    actual_transcript.encode("utf-8")
                ).hexdigest()
                if expected_transcript_hash != actual_transcript_hash:
                    raise ValueError("reviewed extraction transcript hash does not match current source")
                validate_extraction_shape(reviewed_data, sessions[0])
                review_revision = hashlib.sha256(
                    json.dumps(
                        {
                            "items": reviewed_data.get("items", []),
                            "reviewed_empty": reviewed_data.get("reviewed_empty") is True,
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode("utf-8")
                ).hexdigest()
            except (OSError, ValueError) as exc:
                print(f"eod-capture: {_sanitize_log_value(exc)}", file=sys.stderr)
                return 2

            def extract_func(_payload: str, source: Path) -> dict:
                if reviewed_cursor:
                    current = parse_completed_dialogue(source, source_host="home")
                    if _select_completed_prefix(
                        current,
                        run_date=args.date,
                        completed_cursor=reviewed_cursor,
                    ) is None:
                        raise ValueError("reviewed completed prefix changed after review")
                elif _sha256_file(source) != expected_hash:
                    raise ValueError("source changed after interactive review")
                return reviewed_data

        failure_dir = args.vault / ".staging" / "extraction-failures"
        before_failures = _failure_file_fingerprints(failure_dir, args.date)
        written = run_job_a(
            sessions, vault_path=args.vault, run_date=args.date, codex_root=codex_root,
            extract_func=extract_func,
            completed_cursor=reviewed_cursor,
            review_revision=review_revision,
        )
        for p in written:
            print(p)
        after_failures = _failure_file_fingerprints(failure_dir, args.date)
        new_failures = sorted(
            p
            for p, fingerprint in after_failures.items()
            if before_failures.get(p) != fingerprint
        )
        quarantines = _run_date_quarantine_files(args.vault, args.date)
        if new_failures:
            print(
                f"eod-capture: {len(new_failures)} extraction failure(s) written under {failure_dir}",
                file=sys.stderr,
            )
        if (
            sessions
            and (new_failures or quarantines)
            and not _iter_extraction_files(args.vault, args.date)
        ):
            print(
                "eod-capture: extraction breakage left zero staged output for "
                f"{len(sessions)} candidate session(s); returning rc=1",
                file=sys.stderr,
            )
            return 1
        if new_failures:
            print(
                "eod-capture: continuing despite per-session extraction failure(s); "
                "at least one extraction is staged for this run",
                file=sys.stderr,
            )
        return 0
    if args.cmd == "job-b":
        print(
            json.dumps(
                reconcile_extractions(args.vault, run_date=args.date),
                sort_keys=True,
            )
        )
        return 0
    if args.cmd == "digest":
        message = build_digest_message(args.vault, run_date=args.date)
        health_issues = _digest_health_issues(args.vault, args.date)
        if health_issues:
            message = (
                message
                + "\n\n[!] digest health issues:\n- "
                + "\n- ".join(health_issues)
            )
        print(message)
        if health_issues:
            return 1
        return 0
    if args.cmd == "catch-up":
        try:
            result = run_catch_up(
                args.vault,
                since=args.since,
                through=args.through,
                codex_root=args.codex_root,
                reconcile=not args.source_only,
                status_file=_default_capture_status_file(),
                writer_role="sjm-source-only" if args.source_only else "home",
            )
        except ValueError as exc:
            print(f"eod-capture: {exc}", file=sys.stderr)
            return 2
        except (OSError, RuntimeError, TimeoutError) as exc:
            print(f"eod-capture: catch-up failed: {_sanitize_log_value(exc)}", file=sys.stderr)
            return 1
        print(json.dumps(result, sort_keys=True))
        status = result["status"]
        return 1 if status["counts"]["failed"] or status["run_status"] == "failed" else 0
    if args.cmd == "status":
        try:
            result = capture_status(
                args.vault,
                since=args.since,
                through=args.through,
                codex_root=args.codex_root,
                writer_role=args.writer_role,
            )
        except ValueError as exc:
            print(f"eod-capture: {exc}", file=sys.stderr)
            return 2
        print(json.dumps(result, indent=2, sort_keys=True))
        return 1 if result["counts"]["failed"] else 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
