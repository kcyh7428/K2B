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
import shutil
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Callable, Iterable


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
MIN_EVIDENCE_QUOTE_CHARS = 10
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


def strip_transcript(session_path: Path) -> str:
    """Return compact text from a JSONL session transcript."""
    parts: list[str] = []
    with session_path.open(encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"invalid JSON in transcript {session_path} line {lineno}: {exc}"
                ) from exc
            if not isinstance(event, dict):
                continue
            text = _event_to_text(event)
            if text:
                parts.append(text)
    return "\n\n".join(parts).strip()


def strip_dialogue_for_transport(session_path: Path) -> str:
    """Return only redacted user/assistant dialogue for a scoped two-Mac handoff."""
    parts: list[str] = []
    with session_path.open(encoding="utf-8") as handle:
        for lineno, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                # A crashed or actively-appending Codex session can leave one
                # partial JSONL record. The raw-file hash still binds the exact
                # source, so omitting an unparseable record is safe and keeps
                # otherwise valid dialogue exportable.
                continue
            if not isinstance(event, dict):
                continue
            payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
            item = payload.get("item") if isinstance(payload, dict) else None
            if not isinstance(item, dict):
                item = payload if event.get("type") == "response_item" else event
            if item.get("type") != "message" or item.get("role") not in {"user", "assistant"}:
                continue
            role = str(item["role"])
            text = _content_to_text(item.get("content", ""))
            if not text or (role == "user" and _is_codex_bootstrap_text(text)):
                continue
            redacted = _sanitize_transport_text(
                _truncate(_sanitize_transport_text(text), head=4000, limit=8000)
            )
            if redacted:
                parts.append(f"[{role}]\n{redacted}")
    return "\n\n".join(parts).strip()


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
    session_path: Path, *, source_host: str, codex_root: Path | None = None
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
    transcript = strip_dialogue_for_transport(resolved)
    if not transcript:
        raise ValueError("source has no eligible user/assistant dialogue")
    if _source_changed_since(resolved, source_stat, raw_source_sha256):
        raise ValueError("source changed while the handoff bundle was created")
    run_date = _session_content_date(resolved) or _mtime_date(resolved)
    return {
        "bundle_schema_version": 1,
        "source_host": source_host,
        "source_app": "codex_desktop",
        "session_path": str(resolved),
        "run_date": run_date,
        "raw_source_sha256": raw_source_sha256,
        "transcript": transcript,
        "transcript_sha256": hashlib.sha256(transcript.encode("utf-8")).hexdigest(),
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
            session_date = _session_content_date(resolved) or _mtime_date(resolved)
        except OSError:
            continue
        if session_date != run_date:
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
            run_date = _session_content_date(resolved) or _mtime_date(resolved)
        except OSError:
            continue
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


def _filter_extraction_items(
    data: dict, payload: str, session_path: Path
) -> tuple[dict, list[dict]]:
    shaped = validate_extraction_shape(data, session_path)
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
    return filtered, rejections


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
    if kind not in {"fact", "decision", "learning", "preference"}:
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
    payload: str | None = None,
    session_path: Path | None = None,
) -> bool:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    try:
        validate_extraction(data, path)
    except ValueError:
        return False
    if data.get("schema_version") != EXTRACTION_SCHEMA_VERSION:
        return False
    if transcript_sha256 is not None and data.get("transcript_sha256") != transcript_sha256:
        return False
    if raw_source_sha256 is not None and data.get("raw_source_sha256") != raw_source_sha256:
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


def _clear_stale_session_diagnostics(
    vault_path: Path, session_path: Path, *, run_date: str
) -> None:
    """Remove older terminal markers after a current extraction succeeds."""
    artifact_name = f"{run_date}_{_safe_session_id(session_path)}.json"
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
        out_path = out_dir / f"{run_date}_{session_id}.json"
        lock_path = vault_path / ".staging" / "extraction-locks" / f"{run_date}_{session_id}.lock"
        with _file_lock(lock_path):
                raw_source_sha256: str | None = None
                transcript_sha256: str | None = None
                try:
                    source_stat = session_path.stat()
                    raw_source_sha256 = _sha256_file(session_path)
                    payload = strip_transcript(session_path)
                    if _source_changed_since(
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
                        )
                        continue
                    transcript_sha256 = hashlib.sha256(payload.encode("utf-8")).hexdigest()
                    if _existing_valid_extraction(
                        out_path,
                        transcript_sha256=transcript_sha256,
                        raw_source_sha256=raw_source_sha256,
                        payload=payload,
                        session_path=session_path,
                    ):
                        _clear_stale_session_diagnostics(
                            vault_path, session_path, run_date=run_date
                        )
                        continue
                    _clear_stale_session_diagnostics(
                        vault_path, session_path, run_date=run_date
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
                        )
                    else:
                        data = extract(payload, session_path)
                    if data is None:
                        continue
                    if _source_changed_since(
                        session_path, source_stat, raw_source_sha256
                    ):
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
                    try:
                        data, rejections = _filter_extraction_items(
                            data, payload, session_path
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
                    )
    return written


def stage_reviewed_bundle(
    bundle: dict, reviewed: dict, *, vault_path: Path, run_date: str
) -> Path:
    """Stage one interactively reviewed remote source without a provider call."""
    if _source_only_writer():
        raise PermissionError("reviewed bundle staging requires the home writer")
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
    if not isinstance(reviewed, dict) or not isinstance(reviewed.get("items"), list):
        raise ValueError("reviewed extraction requires an explicit items list")
    if not reviewed["items"] and reviewed.get("reviewed_empty") is not True:
        raise ValueError(
            "empty reviewed extraction requires explicit reviewed_empty=true"
        )
    for field in ("raw_source_sha256", "transcript_sha256"):
        if reviewed.get(field) != bundle[field]:
            raise ValueError(f"reviewed extraction {field} does not match source bundle")

    source_path = Path(bundle["session_path"])
    data = dict(reviewed)
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
    filtered, rejections = _filter_extraction_items(data, transcript, source_path)
    if rejections:
        raise ValueError("reviewed extraction contains invalid or ungrounded items")
    for field in (
        "raw_source_sha256",
        "transcript_sha256",
        "reviewed_empty",
        "run_date",
        "source_app",
        "source_host",
        "session_path",
    ):
        if field in data:
            filtered[field] = data[field]

    out_path = vault_path / ".staging" / "extractions" / f"{run_date}_{_safe_session_id(source_path)}.json"
    lock_path = vault_path / ".staging" / "extraction-locks" / f"{run_date}_{_safe_session_id(source_path)}.lock"
    with _file_lock(lock_path):
        # Interactive review is authoritative for this immutable source.
        # Always replace an older extraction so a corrected review cannot be
        # silently discarded merely because the source hashes are unchanged.
        with _file_lock(_reconcile_lock_path(vault_path)):
            _atomic_write_json(out_path, filtered)
            _clear_stale_session_diagnostics(
                vault_path, source_path, run_date=run_date
            )
    return out_path


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


def _write_extraction_failure(
    vault_path: Path,
    session_path: Path,
    *,
    run_date: str,
    error: Exception,
    transcript_sha256: str | None = None,
    raw_source_sha256: str | None = None,
    codex_root: Path | None = None,
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
    path = failure_dir / f"{run_date}_{_safe_session_id(session_path)}.json"
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
    path = skip_dir / f"{run_date}_{_safe_session_id(session_path)}.json"
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
    path = quarantine_dir / f"{run_date}_{_safe_session_id(session_path)}.json"
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
    for key in ("dedupe_key", "scope", "source_app", "session_path"):
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
        receipt_path = _reconciliation_receipt_path(vault_path, path)
        raw_source_sha256 = str(data.get("raw_source_sha256") or "")
        if _receipt_matches_source_and_extraction(
            receipt_path, path, raw_source_sha256
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
                    summary["preferences_seen"] += 1
                    summary["skipped_preferences"] += 1
                    continue
                _validate_extraction_item(item, idx=idx, source=path)
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


def _artifact_matches_source(path: Path, raw_source_sha256: str) -> bool:
    artifact = _read_json_dict(path)
    return bool(
        artifact
        and raw_source_sha256
        and artifact.get("raw_source_sha256") == raw_source_sha256
    )


def _receipt_matches_source_and_extraction(
    receipt_path: Path, extraction_path: Path, raw_source_sha256: str
) -> bool:
    receipt = _read_json_dict(receipt_path)
    if not (
        receipt
        and raw_source_sha256
        and receipt.get("raw_source_sha256") == raw_source_sha256
    ):
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
            artifact_name = f"{run_date}_{_safe_session_id(session_path)}.json"
            extraction = vault_path / ".staging" / "extractions" / artifact_name
            receipt = vault_path / ".staging" / "reconciled" / artifact_name
            failure = vault_path / ".staging" / "extraction-failures" / artifact_name
            quarantine = vault_path / ".staging" / "eod-quarantine" / artifact_name
            skip = vault_path / ".staging" / "extraction-skips" / artifact_name

            receipt_matches = _receipt_matches_source_and_extraction(
                receipt, extraction, raw_source_sha256
            )
            receipt_data = _read_json_dict(receipt) if receipt_matches else None
            quarantine_data = _read_json_dict(quarantine)
            extraction_matches = _artifact_matches_source(extraction, raw_source_sha256)
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
            elif _artifact_matches_source(failure, raw_source_sha256):
                state = "failed"
                retryable = True
            elif _artifact_matches_source(
                quarantine, raw_source_sha256
            ) or quarantine_data is not None:
                state = "failed"
                retryable = True
            elif _artifact_matches_source(skip, raw_source_sha256):
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
    args = parser.parse_args(argv)

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
                session_date = _session_content_date(resolved_sp)
                if not session_date:
                    try:
                        rel_parts = resolved_sp.relative_to(codex_root).parts
                    except ValueError:
                        rel_parts = ()
                    if len(rel_parts) >= 4:
                        candidate_date = "-".join(rel_parts[:3])
                        try:
                            session_date = date.fromisoformat(candidate_date).isoformat()
                        except ValueError:
                            session_date = ""
                if session_date != args.date:
                    detail = session_date or "unknown"
                    print(
                        f"eod-capture: rejected session date {detail}; "
                        f"expected {args.date}: {sp}",
                        file=sys.stderr,
                    )
                    return 2
        extract_func = None
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
                if expected_hash != _sha256_file(sessions[0]):
                    raise ValueError("reviewed extraction source hash does not match current source")
                expected_transcript_hash = reviewed_data.get("transcript_sha256")
                actual_transcript_hash = hashlib.sha256(
                    strip_transcript(sessions[0]).encode("utf-8")
                ).hexdigest()
                if expected_transcript_hash != actual_transcript_hash:
                    raise ValueError("reviewed extraction transcript hash does not match current source")
                validate_extraction_shape(reviewed_data, sessions[0])
            except (OSError, ValueError) as exc:
                print(f"eod-capture: {_sanitize_log_value(exc)}", file=sys.stderr)
                return 2

            def extract_func(_payload: str, source: Path) -> dict:
                if _sha256_file(source) != expected_hash:
                    raise ValueError("source changed after interactive review")
                return reviewed_data

        failure_dir = args.vault / ".staging" / "extraction-failures"
        before_failures = _failure_file_fingerprints(failure_dir, args.date)
        written = run_job_a(
            sessions, vault_path=args.vault, run_date=args.date, codex_root=codex_root,
            extract_func=extract_func,
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
