#!/usr/bin/env python3
"""Deterministic, redacted exporter for K2B/K2Bi Claude JSONL session history.

Reads JSONL session files from a source tree (default ~/.claude/projects),
redacts secret-bearing values, emits deterministic sorted artifacts, and writes
a schema-v1 manifest last (atomic replace). Identical --freeze reruns are
idempotent. A changed or malformed existing manifest fails closed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REDACTED = "[REDACTED]"

# Keys whose values are treated as secret material and redacted.
SENSITIVE_KEYS = frozenset(
    {
        "api_key",
        "apikey",
        "api_token",
        "auth",
        "authorization",
        "bot_token",
        "cookie",
        "credential",
        "credentials",
        "key",
        "oauth",
        "oauth_token",
        "password",
        "secret",
        "secret_key",
        "session",
        "session_cookie",
        "token",
    }
)

# Regexes for secret-bearing string values anywhere in the transcript.
SECRET_VALUE_RE = re.compile(
    r"(?:(?<![A-Za-z0-9])(?:"
    r"sk-[a-zA-Z0-9_\-]{8,}|"
    r"xox[bpoar]-[A-Za-z0-9\-]{10,}|"
    r"AIza[A-Za-z0-9_\-]{35}(?![A-Za-z0-9_\-]))|\b(?:"
    r"[a-zA-Z0-9_\-]{32,64}\.[a-zA-Z0-9_\-]{32,64}\.[a-zA-Z0-9_\-]{32,64}|"
    r"[A-Za-z0-9+/]{40,}={0,2}|"
    r"(?i:Bearer)\s+[A-Za-z0-9_\-\.]+|"
    r"ghp_[A-Za-z0-9]{36}|"
    r"glpat-[A-Za-z0-9\-]{20}|"
    r"AKIA[0-9A-Z]{16}))"
)

ENV_SECRET_RE = re.compile(
    r"^(K2B_|TELEGRAM_|OPENAI_|ANTHROPIC_|GH_|GITLAB_|AWS_)?"
    r"(.*(?:API_?KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL|AUTH|COOKIE|OAUTH))"
    r"\s*=\s*(.*)$",
    re.IGNORECASE,
)
URL_USERINFO_RE = re.compile(r"([A-Za-z][A-Za-z0-9+.-]*://)[^/@\s]+@")
HEADER_SECRET_RE = re.compile(
    r"(?i)(\b(?:authorization|authentication|x-api-key|api-key|cookie|set-cookie)"
    r"\s*:\s*).*$"
)
SEMANTIC_SECRET_RE = re.compile(
    r"(?i)((?:[\"']?)\b[A-Z0-9_-]*(?:API_?KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL|AUTH|"
    r"COOKIE|OAUTH)[A-Z0-9_-]*(?:[\"']?)\s*[:=]\s*)"
    r"(?:\"[^\"]*\"|'[^']*'|[^\s,;]+)"
)
CLI_SECRET_RE = re.compile(
    r"(?i)(--[A-Z0-9_-]*(?:api[-_]?key|token|secret|password|credential|auth|cookie|oauth)"
    r"[A-Z0-9_-]*"
    r"(?:=|\s+))(?:\"[^\"]*\"|'[^']*'|[^\s,;]+)"
)


def _redact_string(value: str) -> str:
    """Redact known secret patterns in a free-form string."""
    if not isinstance(value, str):
        return value
    lines = []
    for line in value.splitlines():
        line = URL_USERINFO_RE.sub(r"\1[REDACTED]@", line)
        line = HEADER_SECRET_RE.sub(rf"\1{REDACTED}", line)
        line = CLI_SECRET_RE.sub(rf"\1{REDACTED}", line)
        line = SEMANTIC_SECRET_RE.sub(rf"\1{REDACTED}", line)
        m = ENV_SECRET_RE.match(line)
        if m:
            key_text = f"{m.group(1) or ''}{m.group(2)}"
            key_text = SECRET_VALUE_RE.sub(REDACTED, key_text)
            lines.append(f"{key_text}={REDACTED}")
        else:
            lines.append(SECRET_VALUE_RE.sub(REDACTED, line))
    return "\n".join(lines)


def _is_sensitive_key(key: Any) -> bool:
    if not isinstance(key, str):
        return False
    lower = key.lower()
    return any(
        lower == sensitive
        or lower.endswith(f"_{sensitive}")
        or lower.startswith(f"{sensitive}_")
        for sensitive in SENSITIVE_KEYS
    )


def redact_value(value: Any, key: Any | None = None) -> Any:
    """Recursively redact secrets from a JSON value."""
    if isinstance(value, dict):
        return {
            k: (REDACTED if _is_sensitive_key(k) else redact_value(v, k))
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [redact_value(item, key) for item in value]
    if isinstance(value, str):
        if _is_sensitive_key(key):
            return REDACTED
        return _redact_string(value)
    return value


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(
        dir=str(path.parent), prefix=f".tmp_{path.name}_", suffix=".json"
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


def _fsync_dir(path: Path) -> None:
    try:
        fd = os.open(str(path), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _load_jsonl_bytes(raw: bytes, path: Path) -> list[dict]:
    """Parse one immutable byte snapshot of a JSONL session."""
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"malformed UTF-8 JSONL {path}: {exc}") from exc
    events: list[dict] = []
    for lineno, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"malformed JSONL {path} line {lineno}: {exc}") from exc
        events.append(event)
    return events


def _load_jsonl(path: Path) -> list[dict]:
    return _load_jsonl_bytes(path.read_bytes(), path)


def _session_cwd(event: dict) -> str:
    cwd = event.get("cwd")
    if isinstance(cwd, str) and cwd:
        return cwd
    payload = event.get("payload")
    if isinstance(payload, dict):
        payload_cwd = payload.get("cwd")
        if isinstance(payload_cwd, str) and payload_cwd:
            return payload_cwd
    return ""


def _project_scope(path: Path, project_names: set[str]) -> bool:
    """True if the session path or its first cwd implies one of the named projects."""
    names = "|".join(re.escape(name) for name in sorted(project_names))
    if not names:
        return False
    exact_path_re = re.compile(
        rf"(?:^|/)Projects/(?:{names})(?:/|$)|"
        rf"(?:^|[-/])Projects-(?:{names})(?:/|$)"
    )
    # Never convert parse damage into "out of scope": doing so would let a
    # freeze silently omit a candidate session and produce an incomplete archive.
    events = _load_jsonl(path)
    saw_cwd = False
    for event in events[:20]:
        if not isinstance(event, dict):
            continue
        cwd = _session_cwd(event)
        if cwd:
            saw_cwd = True
        if exact_path_re.search(cwd):
            return True
    if saw_cwd:
        return False
    return bool(exact_path_re.search(str(path)))


def _source_session_paths(source_dir: Path, project_names: set[str]) -> list[Path]:
    paths = sorted(p for p in source_dir.rglob("*.jsonl") if p.is_file())
    return [p for p in paths if _project_scope(p, project_names)]


def _artifact_rel_path(source_path: Path, source_dir: Path) -> Path:
    try:
        rel = source_path.relative_to(source_dir)
    except ValueError:
        rel = source_path.relative_to(Path.cwd())
    return Path("sessions") / rel


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _artifact_text(
    source_path: Path, source_dir: Path, *, redact: bool
) -> tuple[Path, str, str, str]:
    """Return relative path, artifact text/hash, and immutable raw-source hash."""
    rel = _artifact_rel_path(source_path, source_dir)
    raw = source_path.read_bytes()
    source_sha256 = hashlib.sha256(raw).hexdigest()
    events = _load_jsonl_bytes(raw, source_path)
    output_events = redact_value(events) if redact else events
    text = "".join(
        json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n"
        for event in output_events
    )
    return rel, text, _sha256_text(text), source_sha256


def _compute_manifest(
    source_dir: Path, output_dir: Path, project_names: set[str], *, redact: bool
) -> tuple[list[Path], dict]:
    """Compute source paths and the schema-v1 manifest without writing."""
    source_dir = source_dir.resolve()
    output_dir = output_dir.resolve()
    if not source_dir.is_dir():
        raise ValueError(f"source is not a directory: {source_dir}")

    source_paths = _source_session_paths(source_dir, project_names)
    artifacts: list[dict] = []
    for source_path in source_paths:
        rel, _text, sha256, source_sha256 = _artifact_text(
            source_path, source_dir, redact=redact
        )
        artifacts.append(
            {
                "relativePath": str(rel),
                "sha256": sha256,
                "sourceSha256": source_sha256,
            }
        )

    artifacts.sort(key=lambda a: a["relativePath"])

    manifest = {
        "schemaVersion": 1,
        "frozen": True,
        "createdAt": _now_utc(),
        "sourceSessionCount": len(source_paths),
        "artifacts": artifacts,
    }
    return source_paths, manifest


def _compute_candidate(
    source_dir: Path, output_dir: Path, project_names: set[str], *, redact: bool
) -> tuple[list[Path], dict, list[tuple[Path, str]]]:
    """Compute source paths, manifest, and in-memory artifact payloads without writing."""
    source_dir = source_dir.resolve()
    output_dir = output_dir.resolve()
    if not source_dir.is_dir():
        raise ValueError(f"source is not a directory: {source_dir}")

    source_paths = _source_session_paths(source_dir, project_names)
    artifact_payloads: list[tuple[Path, str]] = []
    artifacts: list[dict] = []
    for source_path in source_paths:
        rel, text, sha256, source_sha256 = _artifact_text(
            source_path, source_dir, redact=redact
        )
        artifact_payloads.append((rel, text))
        artifacts.append(
            {
                "relativePath": str(rel),
                "sha256": sha256,
                "sourceSha256": source_sha256,
            }
        )

    artifacts.sort(key=lambda a: a["relativePath"])
    artifact_payloads.sort(key=lambda t: str(t[0]))

    manifest = {
        "schemaVersion": 1,
        "frozen": True,
        "createdAt": _now_utc(),
        "sourceSessionCount": len(source_paths),
        "artifacts": artifacts,
    }
    return source_paths, manifest, artifact_payloads


def _validate_manifest_schema(manifest: dict, *, existing: bool = False) -> str | None:
    """Return an error message if the manifest does not match schema v1, or None."""
    if not isinstance(manifest, dict):
        return "manifest is not a JSON object"
    if manifest.get("schemaVersion") != 1:
        return "manifest schemaVersion mismatch"
    if manifest.get("frozen") is not True:
        return "manifest frozen flag missing or false"
    if not isinstance(manifest.get("sourceSessionCount"), int):
        return "manifest sourceSessionCount missing or not an integer"
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, list):
        return "manifest artifacts missing or not a list"
    seen: set[str] = set()
    for idx, entry in enumerate(artifacts):
        if not isinstance(entry, dict):
            return f"artifact entry {idx} is not an object"
        rel = entry.get("relativePath")
        if not isinstance(rel, str) or not rel:
            return f"artifact entry {idx} missing relativePath"
        if rel in seen:
            return f"artifact entry {idx} duplicate relativePath: {rel}"
        seen.add(rel)
        sha256 = entry.get("sha256")
        if not isinstance(sha256, str) or len(sha256) != 64:
            return f"artifact entry {idx} missing or invalid sha256"
        try:
            int(sha256, 16)
        except ValueError:
            return f"artifact entry {idx} sha256 is not hexadecimal"
        source_sha256 = entry.get("sourceSha256")
        if not isinstance(source_sha256, str) or len(source_sha256) != 64:
            return f"artifact entry {idx} missing or invalid sourceSha256"
        try:
            int(source_sha256, 16)
        except ValueError:
            return f"artifact entry {idx} sourceSha256 is not hexadecimal"
    if manifest.get("sourceSessionCount") != len(artifacts):
        return "manifest sourceSessionCount does not equal artifact count"
    return None


def _verify_artifacts(manifest_path: Path, manifest: dict) -> str | None:
    output_dir = manifest_path.parent
    for idx, entry in enumerate(manifest["artifacts"]):
        artifact_path = output_dir / entry["relativePath"]
        try:
            actual = hashlib.sha256(artifact_path.read_bytes()).hexdigest()
        except OSError as exc:
            return f"cannot read artifact {artifact_path}: {exc}"
        if actual != entry["sha256"]:
            return (
                f"hash mismatch for artifact {artifact_path}: "
                f"expected {entry['sha256']}, got {actual}"
            )
    return None


def do_preview(
    source_dir: Path, output_dir: Path, project_names: set[str], *, redact: bool
) -> int:
    try:
        source_paths, manifest = _compute_manifest(
            source_dir, output_dir, project_names, redact=redact
        )
    except ValueError as exc:
        print(f"export-claude-history: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True))
    print(f"export-claude-history: preview {len(source_paths)} session(s)")
    return 0


def do_freeze(
    source_dir: Path, output_dir: Path, project_names: set[str], *, redact: bool
) -> int:
    source_dir = source_dir.resolve()
    output_dir = output_dir.resolve()
    if not source_dir.is_dir():
        print(f"export-claude-history: source is not a directory: {source_dir}", file=sys.stderr)
        return 2

    manifest_path = output_dir / "manifest.json"
    existing_manifest: dict | None = None
    if manifest_path.exists():
        try:
            existing_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(
                f"export-claude-history: existing manifest malformed: {exc}",
                file=sys.stderr,
            )
            return 2
        schema_error = _validate_manifest_schema(existing_manifest, existing=True)
        if schema_error:
            print(
                f"export-claude-history: existing manifest invalid: {schema_error}",
                file=sys.stderr,
            )
            return 2

    # Compute the complete candidate manifest and artifact bytes in memory before
    # any write. This guarantees that a refusal leaves every existing archive byte
    # unchanged and that an identical rerun performs no writes at all.
    try:
        source_paths, new_manifest, artifact_payloads = _compute_candidate(
            source_dir, output_dir, project_names, redact=redact
        )
    except ValueError as exc:
        print(f"export-claude-history: {exc}", file=sys.stderr)
        return 2

    if existing_manifest is not None:
        # Preserve creation time for idempotency comparison.
        new_manifest["createdAt"] = existing_manifest.get("createdAt", new_manifest["createdAt"])
        comparable_existing = dict(existing_manifest)
        comparable_existing.pop("createdAt", None)
        comparable_new = dict(new_manifest)
        comparable_new.pop("createdAt", None)
        if comparable_existing != comparable_new:
            print(
                "export-claude-history: existing manifest differs from computed manifest; "
                "refusing to overwrite",
                file=sys.stderr,
            )
            return 2
        artifact_error = _verify_artifacts(manifest_path, existing_manifest)
        if artifact_error:
            print(
                f"export-claude-history: existing archive invalid: {artifact_error}",
                file=sys.stderr,
            )
            return 2
        # Identical manifest: do not rewrite artifacts or manifest.
        print(
            f"export-claude-history: frozen manifest identical; "
            f"no write needed for {len(source_paths)} session(s)"
        )
        return 0

    for rel, text in artifact_payloads:
        artifact_path = output_dir / rel
        _atomic_write_text(artifact_path, text)

    _atomic_write_text(
        manifest_path,
        json.dumps(new_manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )
    print(f"export-claude-history: frozen {len(source_paths)} session(s) to {output_dir}")
    return 0


def do_verify(manifest_path: Path) -> int:
    if not manifest_path.is_file():
        print(f"export-claude-history: manifest not found: {manifest_path}", file=sys.stderr)
        return 2
    output_dir = manifest_path.parent
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"export-claude-history: cannot read manifest: {exc}", file=sys.stderr)
        return 2
    schema_error = _validate_manifest_schema(manifest, existing=True)
    if schema_error:
        print(f"export-claude-history: manifest invalid: {schema_error}", file=sys.stderr)
        return 2
    artifact_error = _verify_artifacts(manifest_path, manifest)
    if artifact_error:
        print(f"export-claude-history: {artifact_error}", file=sys.stderr)
        return 2
    artifacts = manifest["artifacts"]
    print(f"export-claude-history: verified {len(artifacts)} artifact(s)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Deterministic redacted exporter for K2B/K2Bi Claude JSONL history"
    )
    parser.add_argument(
        "--source",
        type=Path,
        default=None,
        help="source directory of JSONL files",
    )
    parser.add_argument(
        "--destination",
        type=Path,
        default=None,
        help="output directory",
    )
    parser.add_argument(
        "--projects",
        type=str,
        default="K2B,K2Bi",
        help="comma-separated project names to include (default: K2B,K2Bi)",
    )
    parser.add_argument(
        "--redact",
        action="store_true",
        help="redact secret-bearing values",
    )
    parser.add_argument(
        "--freeze",
        action="store_true",
        help="write artifacts + manifest",
    )
    parser.add_argument(
        "--preview",
        action="store_true",
        help="print the manifest without writing artifacts or manifest",
    )
    parser.add_argument(
        "--verify-manifest",
        type=Path,
        default=None,
        help="verify counts/hashes in the manifest file",
    )
    args = parser.parse_args(argv)

    if args.verify_manifest:
        return do_verify(args.verify_manifest)

    if args.source is None or args.destination is None:
        print(
            "export-claude-history: --source and --destination are required",
            file=sys.stderr,
        )
        return 2

    if args.preview and args.freeze:
        print(
            "export-claude-history: --preview and --freeze are mutually exclusive",
            file=sys.stderr,
        )
        return 2

    if args.freeze and not args.redact:
        print(
            "export-claude-history: --freeze requires --redact",
            file=sys.stderr,
        )
        return 2

    if not args.freeze and not args.preview:
        print(
            "export-claude-history: one of --freeze, --preview, or --verify-manifest is required",
            file=sys.stderr,
        )
        return 2

    project_names = {name.strip() for name in args.projects.split(",") if name.strip()}
    if args.preview:
        return do_preview(args.source, args.destination, project_names, redact=args.redact)
    return do_freeze(args.source, args.destination, project_names, redact=args.redact)


if __name__ == "__main__":
    raise SystemExit(main())
