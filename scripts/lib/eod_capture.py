"""End-of-day transcript capture for K2B.

Job A stages structured extraction JSON from Claude Code / Codex Desktop
session transcripts. Job B reconciles staged items into vault memory.
"""
from __future__ import annotations

import argparse
import errno
import fcntl
import hashlib
import json
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Callable, Iterable


REPO_ROOT = Path(__file__).resolve().parents[2]
PROMPT_PATH = REPO_ROOT / "scripts" / "prompts" / "eod-capture-extract.md"
MINIMAX_JSON_JOB = REPO_ROOT / "scripts" / "minimax-json-job.sh"
WIKI_LOG_APPEND = REPO_ROOT / "scripts" / "wiki-log-append.sh"
SHELF_WRITER = REPO_ROOT / "scripts" / "washing-machine" / "shelf-writer.sh"
EXTRACTION_SCHEMA_VERSION = "1.0"
MAX_EXTRACTOR_STDOUT_BYTES = 1_000_000
MAX_EXTRACTOR_STDERR_BYTES = 200_000
MAX_TELEGRAM_DIGEST_BYTES = 3900
DEFAULT_SHELF_WRITER_TIMEOUT_SECONDS = 30
MIN_EVIDENCE_QUOTE_CHARS = 10
PROJECT_SCOPE_RE = re.compile(r"(^|/)Projects/(K2B|K2Bi)(?:$|/)")
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

ExtractFunc = Callable[[str, Path], dict]


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


def detect_source_app(session_path: Path) -> str:
    s = str(session_path)
    if "/.codex/sessions/" in s or "rollout-" in session_path.name:
        return "codex_desktop"
    return "claude_code"


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
    return bool(
        PROJECT_SCOPE_RE.search(s)
        or "Projects-K2B" in s
        or "Projects-K2Bi" in s
    )


def _is_k2b_scope(session_path: Path) -> bool:
    cwd = session_cwd(session_path)
    if cwd:
        return bool(PROJECT_SCOPE_RE.search(cwd))
    return _path_implies_project_scope(session_path)


def _mtime_date(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime).date().isoformat()


def _parse_event_date(value: object) -> str:
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value).date().isoformat()
        except (OSError, OverflowError, ValueError):
            return ""
    if not isinstance(value, str) or not value.strip():
        return ""
    text = value.strip()
    if re.match(r"^\d{4}-\d{2}-\d{2}$", text):
        return text
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        return ""


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
    claude_root: Path | None = None,
    codex_root: Path | None = None,
) -> list[Path]:
    """Discover today's K2B/K2Bi Claude Code and Codex Desktop sessions."""
    claude_root = (
        claude_root
        or Path(
            os.environ.get("K2B_CLAUDE_SESSIONS_ROOT", "~/.claude/projects")
        ).expanduser()
    )
    codex_root = (
        codex_root
        or Path(
            os.environ.get("K2B_CODEX_SESSIONS_ROOT", "~/.codex/sessions")
        ).expanduser()
    )

    found: list[Path] = []
    if claude_root.is_dir():
        for path in sorted(claude_root.rglob("*.jsonl")):
            try:
                session_date = _session_content_date(path) or _mtime_date(path)
                if session_date != run_date:
                    continue
            except OSError:
                continue
            if _is_k2b_scope(path):
                found.append(path)

    try:
        year, month, day = run_date.split("-", 2)
    except ValueError:
        year = month = day = ""
    codex_day = codex_root / year / month / day if year else codex_root
    if codex_day.is_dir():
        codex_candidates = sorted(codex_day.rglob("*.jsonl"))
    else:
        codex_candidates = []
    for path in codex_candidates:
        try:
            session_date = _session_content_date(path) or _mtime_date(path)
        except OSError:
            continue
        if session_date != run_date:
            continue
        if _is_k2b_scope(path):
            found.append(path)

    return found


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


def call_kimi_extractor(payload: str, session_path: Path) -> dict:
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
                    timeout=180,
                )
        except (OSError, subprocess.TimeoutExpired) as exc:
            last_error = RuntimeError(f"eod extractor failed for {session_path}: {exc}")
            if attempt < 2:
                time.sleep(_extractor_retry_delay(attempt))
                continue
            raise last_error from exc
        if proc.returncode == 0:
            if not proc.stdout.strip():
                stderr_hint = proc.stderr.strip()
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
            f"eod extractor failed for {session_path}: {proc.stderr.strip()}"
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
    quote = str(item.get("evidence_quote") or "").strip()
    _validate_evidence_quote_controls(quote, idx=idx)
    normalized_quote = _normalize_evidence_text(quote)
    if normalized_quote and len(normalized_quote) < MIN_EVIDENCE_QUOTE_CHARS:
        raise ValueError(
            f"evidence_quote for item {idx} in {session_path} is too short"
        )
    if normalized_quote and normalized_quote not in normalized_payload:
        raise ValueError(
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
            _validate_extraction_item(item, idx=idx, source=session_path)
            _validate_item_evidence_quote(
                item,
                idx=idx,
                normalized_payload=normalized_payload,
                session_path=session_path,
            )
        except ValueError as exc:
            rejections.append(
                {
                    "item_index": idx,
                    "error": f"{type(exc).__name__}: {exc}",
                    "item": item,
                }
            )
            continue
        valid_items.append(item)
    filtered = dict(shaped)
    filtered["items"] = valid_items
    return filtered, rejections


def _validate_extraction_item(item: object, *, idx: int, source: Path) -> None:
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
    canonical_home = str(item.get("canonical_home", "")).strip()
    if canonical_home and canonical_home != "wiki/context/shelves/semantic.md":
        raise ValueError(
            f"extractor item {idx} has unsupported canonical_home: {canonical_home}"
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


def _existing_valid_extraction(path: Path, *, transcript_sha256: str | None = None) -> bool:
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
    return True


def run_job_a(
    session_paths: Iterable[Path],
    *,
    vault_path: Path,
    run_date: str,
    extract_func: ExtractFunc | None = None,
) -> list[Path]:
    """Strip each session, run extraction, and stage JSON files."""
    extract = extract_func or call_kimi_extractor
    out_dir = vault_path / ".staging" / "extractions"
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for session_path in session_paths:
        session_id = _safe_session_id(session_path)
        out_path = out_dir / f"{run_date}_{session_id}.json"
        lock_path = vault_path / ".staging" / "extraction-locks" / f"{run_date}_{session_id}.lock"
        try:
            with _file_lock(lock_path):
                transcript_sha256: str | None = None
                try:
                    payload = strip_transcript(session_path)
                    if not payload:
                        _write_extraction_failure(
                            vault_path,
                            session_path,
                            run_date=run_date,
                            error=ValueError("empty stripped transcript"),
                        )
                        continue
                    transcript_sha256 = hashlib.sha256(payload.encode("utf-8")).hexdigest()
                    if _existing_valid_extraction(
                        out_path, transcript_sha256=transcript_sha256
                    ):
                        continue
                    data = extract(payload, session_path)
                    data.setdefault("schema_version", EXTRACTION_SCHEMA_VERSION)
                    if data.get("schema_version") != EXTRACTION_SCHEMA_VERSION:
                        raise ValueError(
                            f"unsupported extraction schema_version: {data.get('schema_version')}"
                        )
                    data.setdefault("session_path", str(session_path))
                    data.setdefault("source_app", detect_source_app(session_path))
                    data.setdefault("items", [])
                    data["transcript_sha256"] = transcript_sha256
                    data, rejections = _filter_extraction_items(
                        data, payload, session_path
                    )
                    for rejection in rejections:
                        _write_extraction_rejection(
                            vault_path,
                            session_path,
                            run_date=run_date,
                            rejection=rejection,
                            transcript_sha256=transcript_sha256,
                        )
                    if rejections and not data.get("items"):
                        raise ValueError(
                            f"all extractor items rejected for {session_path}"
                        )
                    _atomic_write_json(out_path, data)
                    written.append(out_path)
                except (
                    OSError,
                    RuntimeError,
                    json.JSONDecodeError,
                    ValueError,
                    subprocess.SubprocessError,
                ) as exc:
                    print(
                        f"eod-capture: extraction failed for {session_path}: {exc}",
                        file=sys.stderr,
                    )
                    _write_extraction_failure(
                        vault_path,
                        session_path,
                        run_date=run_date,
                        error=exc,
                        transcript_sha256=transcript_sha256,
                    )
        finally:
            try:
                lock_path.unlink(missing_ok=True)
            except OSError:
                pass
            finally:
                _fsync_dir(lock_path.parent)
    return written


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
            warning = proc.stderr.strip() if proc.returncode != 0 else ""
        except (OSError, subprocess.TimeoutExpired) as exc:
            warning = str(exc)
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
) -> Path:
    failure_dir = vault_path / ".staging" / "extraction-failures"
    failure = {
        "failed_at": datetime.now().isoformat(timespec="microseconds"),
        "run_date": run_date,
        "session_path": str(session_path),
        "source_app": detect_source_app(session_path),
        "error": f"{type(error).__name__}: {error}",
    }
    if transcript_sha256:
        failure["transcript_sha256"] = transcript_sha256
    path = failure_dir / f"{run_date}_{_safe_session_id(session_path)}.json"
    _atomic_write_json(path, failure)
    return path


def _write_extraction_rejection(
    vault_path: Path,
    session_path: Path,
    *,
    run_date: str,
    rejection: dict,
    transcript_sha256: str | None = None,
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
        "source_app": detect_source_app(session_path),
        "item_index": item_index,
        "error": str(rejection.get("error", "")),
        "item": rejection.get("item"),
    }
    if transcript_sha256:
        payload["transcript_sha256"] = transcript_sha256
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
    predicate = str(item.get("predicate", "")).strip() or "value"
    matches: list[tuple[str, int, str]] = []
    for lineno, line in enumerate(
        semantic_path.read_text(encoding="utf-8").splitlines(), 1
    ):
        if not line.startswith("- "):
            continue
        if dedupe_key and _extract_pipe_field(line, "dedupe_key") == dedupe_key:
            existing = _extract_attr(line, predicate) or ""
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
    _validate_extraction_item(item, idx=0, source=Path("semantic-write"))
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
    review_dir = vault_path / "review"
    review_dir.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(
        f"{source_file}|{reason}|{repr(payload)}".encode("utf-8")
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
        f"- **Reason:** {reason}\n"
        f"- **Source file:** {source_file}\n\n"
        "```json\n"
        f"{json.dumps(payload, ensure_ascii=False, indent=2, default=str)}\n"
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


def reconcile_extractions(vault_path: Path, *, run_date: str) -> dict:
    """Reconcile staged extraction files into vault memory."""
    lock_path = vault_path / ".staging" / "eod-reconcile.lock"
    with _file_lock(lock_path):
        return _reconcile_extractions_locked(vault_path, run_date=run_date)


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
    }
    for path in _iter_extraction_files(vault_path, run_date):
        summary["processed_files"] += 1
        try:
            raw_text = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            summary["errors"] += 1
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

    summary_path = vault_path / ".staging" / f"eod-capture-summary-{run_date}.json"
    _atomic_write_json(summary_path, summary)
    _append_log(
        vault_path,
        run_date,
        f"processed={summary['processed_files']} "
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
    summary = {**summary, "conflicts": len(conflicts)}
    lines = [f"End-of-Day Capture {run_date}"]
    if summary_warning:
        lines.append(summary_warning)
    lines.append(f"sessions processed: {summary.get('processed_files', 0)}")
    lines.append(f"auto-written: {summary.get('auto_written', 0)}")
    lines.append(f"low-confidence review: {summary.get('low_confidence', 0)}")
    lines.append(f"conflicts: {summary.get('conflicts', 0)}")
    lines.append(f"deduped: {summary.get('deduped', 0)}")
    lines.append(f"preferences seen: {summary.get('preferences_seen', 0)}")
    lines.append(f"preferences skipped: {summary.get('skipped_preferences', 0)}")
    lines.append(f"errors: {summary.get('errors', 0)}")
    failure_dir = vault_path / ".staging" / "extraction-failures"
    failures = (
        sorted(failure_dir.glob(f"{run_date}_*.json")) if failure_dir.is_dir() else []
    )
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


def _today() -> str:
    return date.today().isoformat()


def _failure_file_fingerprints(failure_dir: Path, run_date: str) -> dict[Path, str]:
    if not failure_dir.is_dir():
        return {}
    out: dict[Path, str] = {}
    for path in failure_dir.glob(f"{run_date}_*.json"):
        try:
            out[path] = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            out[path] = "<unreadable>"
    return out


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
    failure_dir = vault_path / ".staging" / "extraction-failures"
    failures = (
        sorted(failure_dir.glob(f"{run_date}_*.json"))
        if failure_dir.is_dir()
        else []
    )
    if failures:
        issues.append(f"extraction failures: {len(failures)}")
    log_failure_dir = vault_path / ".staging" / "eod-log-failures"
    log_failures = (
        sorted(log_failure_dir.glob("*.jsonl"))
        if log_failure_dir.is_dir()
        else []
    )
    if log_failures:
        issues.append(f"log append failures: {len(log_failures)}")
    return issues


def _digest_failure_path(vault_path: Path, run_date: str) -> Path:
    return vault_path / ".staging" / "eod-digest-failures" / f"{run_date}_digest.txt"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="K2B end-of-day capture")
    sub = parser.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("job-a")
    a.add_argument("--date", default=_today())
    a.add_argument("--vault", type=Path, default=_default_vault())
    a.add_argument("--session", action="append", type=Path, default=[])
    a.add_argument("--claude-root", type=Path, default=None)
    a.add_argument("--codex-root", type=Path, default=None)
    b = sub.add_parser("job-b")
    b.add_argument("--date", default=_today())
    b.add_argument("--vault", type=Path, default=_default_vault())
    d = sub.add_parser("digest")
    d.add_argument("--date", default=_today())
    d.add_argument("--vault", type=Path, default=_default_vault())
    d.add_argument("--send", action="store_true")
    args = parser.parse_args(argv)

    if args.cmd == "job-a":
        sessions = args.session or discover_session_paths(
            run_date=args.date,
            claude_root=args.claude_root,
            codex_root=args.codex_root,
        )
        failure_dir = args.vault / ".staging" / "extraction-failures"
        before_failures = _failure_file_fingerprints(failure_dir, args.date)
        written = run_job_a(sessions, vault_path=args.vault, run_date=args.date)
        for p in written:
            print(p)
        after_failures = _failure_file_fingerprints(failure_dir, args.date)
        new_failures = sorted(
            p
            for p, fingerprint in after_failures.items()
            if before_failures.get(p) != fingerprint
        )
        if new_failures:
            print(
                f"eod-capture: {len(new_failures)} extraction failure(s) written under {failure_dir}",
                file=sys.stderr,
            )
            return 1
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
        if args.send:
            failure_path = _digest_failure_path(args.vault, args.date)
            _atomic_write_text(failure_path, message + "\n")
            message_size = len(message.encode("utf-8"))
            if message_size > MAX_TELEGRAM_DIGEST_BYTES:
                print(
                    f"eod-capture: digest too large for Telegram send: {message_size} bytes",
                    file=sys.stderr,
                )
                return 1
            with tempfile.TemporaryDirectory(prefix="eod-capture-") as tmp_dir:
                tmp = Path(tmp_dir) / "digest.txt"
                with tmp.open("w", encoding="utf-8") as f:
                    f.write(message)
                    f.flush()
                    os.fsync(f.fileno())
                last_send_error: Exception | None = None
                for attempt in range(3):
                    try:
                        subprocess.run(
                            [
                                str(REPO_ROOT / "scripts" / "send-telegram.sh"),
                                "--file",
                                str(tmp),
                            ],
                            check=True,
                            timeout=60,
                        )
                        last_send_error = None
                        break
                    except subprocess.TimeoutExpired as exc:
                        last_send_error = exc
                    except subprocess.CalledProcessError as exc:
                        last_send_error = exc
                    except OSError as exc:
                        last_send_error = exc
                    if attempt < 2:
                        time.sleep(2 ** attempt)
                if last_send_error is not None:
                    print(
                        f"eod-capture: digest send failed after 3 attempts: {last_send_error}",
                        file=sys.stderr,
                    )
                    if isinstance(last_send_error, subprocess.CalledProcessError):
                        return last_send_error.returncode or 1
                    return 1
            if health_issues:
                print(
                    "eod-capture: digest sent with health issue(s): "
                    + "; ".join(health_issues),
                    file=sys.stderr,
                )
                return 1
            unlink_failed = False
            unlink_error: OSError | None = None
            try:
                failure_path.unlink(missing_ok=True)
            except OSError as exc:
                unlink_failed = True
                unlink_error = exc
                print(
                    f"eod-capture: warning: could not clear digest failure copy "
                    f"{failure_path}: {exc}",
                    file=sys.stderr,
                )
            finally:
                _fsync_dir(failure_path.parent)
            if unlink_failed:
                warning_path = (
                    args.vault
                    / ".staging"
                    / "eod-digest-cleanup-warnings"
                    / f"{args.date}_cleanup.json"
                )
                try:
                    warning_path.parent.mkdir(parents=True, exist_ok=True)
                    _atomic_write_json(
                        warning_path,
                        {
                            "date": args.date,
                            "errno": getattr(unlink_error, "errno", None),
                            "error_message": str(unlink_error),
                            "error_type": type(unlink_error).__name__,
                            "failure_path": str(failure_path),
                            "send_succeeded": True,
                            "warning": "digest failure copy could not be removed after successful send",
                        },
                    )
                except OSError as exc:
                    print(
                        f"eod-capture: warning: could not write digest cleanup warning "
                        f"{warning_path}: {exc}",
                        file=sys.stderr,
                    )
                if warning_path.exists():
                    print(
                        "eod-capture: digest send succeeded; cleanup warning recorded",
                        file=sys.stderr,
                    )
                    return 3
                print(
                    "eod-capture: digest send succeeded; cleanup warning could not be recorded",
                    file=sys.stderr,
                )
                return 4
        else:
            print(message)
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
