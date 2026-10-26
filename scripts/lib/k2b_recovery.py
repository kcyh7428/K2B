"""P2-A synthetic recovery preparation primitives (read-only).

manifest(root, approved_paths) and verify_restore(manifest, restored_root)
produce and check a manifest of explicitly approved regular files. Nothing in
this module copies, restores, or writes files. Standard library only.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import unicodedata
from pathlib import Path

SCHEMA_VERSION = 1
MAX_FILE_BYTES = 16 * 1024 * 1024
CHUNK_BYTES = 65536

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_PRIVATE_COMPONENTS = frozenset(
    {
        ".",
        "..",
        "credentials",
        "secrets",
        "secret",
        "sessions",
        "accounts",
        "auth",
        "private",
        "outbox",
        "receipts",
        "node_modules",
    }
)
_PRIVATE_SUFFIXES = frozenset(
    {
        ".env",
        ".pem",
        ".key",
        ".p12",
        ".pfx",
        ".db",
        ".sqlite",
        ".sqlite3",
    }
)
_PRIVATE_NAMES = frozenset(
    {
        ".env",
        "id_rsa",
        "id_dsa",
        "id_ecdsa",
        "id_ed25519",
    }
)
_MANIFEST_KEYS = frozenset(
    {"schema_version", "source_root", "files"}
)
_FILE_KEYS = frozenset({"path", "size", "sha256", "mode", "mtime_ns"})


class RecoveryError(ValueError):
    """Raised for invalid input or failed verification. Fail closed."""


# ---- path and name validation -------------------------------------------


def _is_private_component(component: str) -> bool:
    if not component or component in {".", ".."}:
        return True
    if component.startswith("."):
        return True
    folded = component.casefold()
    if folded in _PRIVATE_COMPONENTS:
        return True
    if component in _PRIVATE_NAMES or folded in _PRIVATE_NAMES:
        return True
    lowered = component.lower()
    for suffix in _PRIVATE_SUFFIXES:
        if lowered.endswith(suffix):
            return True
    return False


def _validate_approved_path(raw) -> str:
    if not isinstance(raw, str):
        raise RecoveryError("approved path must be a string")
    if not raw:
        raise RecoveryError("approved path must not be empty")
    if any(ord(char) < 32 or ord(char) == 127 for char in raw):
        raise RecoveryError("approved path contains a control character")
    if "\\" in raw:
        raise RecoveryError(f"approved path must not contain backslash: {raw!r}")
    if raw.startswith("/"):
        raise RecoveryError(f"approved path must be relative: {raw!r}")
    if raw != unicodedata.normalize("NFC", raw):
        raise RecoveryError(f"approved path must be NFC-normalized: {raw!r}")
    parts = raw.split("/")
    if not (parts[0] in {"wiki", "raw", "Archive"} or parts[:2] == ["System", "memory"]):
        raise RecoveryError("approved path is outside shared knowledge scope")
    for part in parts:
        if part in {"", ".", ".."}:
            raise RecoveryError(f"approved path has invalid component: {raw!r}")
        if unicodedata.normalize("NFC", part) != part:
            raise RecoveryError(f"approved path component is not NFC: {raw!r}")
        if _is_private_component(part):
            raise RecoveryError(
                f"approved path references private or sensitive state: {raw!r}"
            )
    return raw


def _validate_approved_paths(approved_paths) -> list[str]:
    if not isinstance(approved_paths, (list, tuple)):
        raise RecoveryError("approved_paths must be a list")
    if not approved_paths:
        raise RecoveryError("approved_paths must not be empty")
    seen_exact = set()
    seen_fold = {}
    validated = []
    for raw in approved_paths:
        path = _validate_approved_path(raw)
        if path in seen_exact:
            raise RecoveryError(f"duplicate approved path: {path!r}")
        seen_exact.add(path)
        folded = path.casefold()
        if folded in seen_fold:
            raise RecoveryError(
                f"case-insensitive duplicate approved path: {path!r} vs {seen_fold[folded]!r}"
            )
        seen_fold[folded] = path
        validated.append(path)
    return validated


# ---- root validation ----------------------------------------------------


def _check_absolute_existing_dir(root, label: str) -> Path:
    if not isinstance(root, Path):
        raise RecoveryError(f"{label} must be a pathlib.Path")
    if not root.is_absolute():
        raise RecoveryError(f"{label} must be absolute")
    # Ancestor chain must contain no symlinks, ending at an existing regular dir.
    current = Path(root.anchor)
    for part in root.parts[1:]:
        current = current / part
        if not current.exists() and not current.is_symlink():
            raise RecoveryError(f"{label} does not exist: {root}")
        if current.is_symlink():
            raise RecoveryError(f"{label} has symlink component: {current}")
    if not root.is_dir():
        raise RecoveryError(f"{label} is not a directory: {root}")
    return root


def _disjoint(source: Path, restored: Path) -> None:
    try:
        s = source.resolve(strict=False)
        r = restored.resolve(strict=True)
    except OSError as exc:
        raise RecoveryError(f"cannot resolve roots: {exc}") from exc
    if s == r:
        raise RecoveryError("restored root must differ from source root")
    if s in r.parents or r in s.parents:
        raise RecoveryError("restored root must be disjoint from source root")


def _stat_signature(st: os.stat_result) -> tuple:
    return (
        st.st_dev,
        st.st_ino,
        st.st_size,
        st.st_mtime_ns,
        st.st_ctime_ns,
        st.st_mode,
    )


def _lstat(path: Path) -> os.stat_result:
    try:
        return os.lstat(path)
    except OSError as exc:
        raise RecoveryError(f"cannot stat {path}: {exc}") from exc


def _sha256_bounded(path: Path, limit: int) -> tuple[str, int]:
    digest = hashlib.sha256()
    total = 0
    try:
        # Refuse a final-component symlink even if it was swapped after lstat.
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as handle:
            before = os.fstat(handle.fileno())
            if not stat.S_ISREG(before.st_mode):
                raise RecoveryError(f"not a regular file: {path}")
            while True:
                chunk = handle.read(CHUNK_BYTES)
                if not chunk:
                    break
                total += len(chunk)
                if total > limit:
                    raise RecoveryError(f"file exceeds size ceiling: {path}")
                digest.update(chunk)
            if _stat_signature(before) != _stat_signature(os.fstat(handle.fileno())):
                raise RecoveryError(f"opened file changed while hashing: {path}")
    except RecoveryError:
        raise
    except OSError as exc:
        raise RecoveryError(f"cannot read {path}: {exc}") from exc
    return digest.hexdigest(), total


def _read_approved(root: Path, rel_paths: list[str]) -> list[dict]:
    # Full validation of members before any payload read.
    resolved = []
    for rel in rel_paths:
        full = root / rel
        current = root
        for part in rel.split("/"):
            current = current / part
            st = _lstat(current)
            if stat.S_ISLNK(st.st_mode):
                raise RecoveryError(f"symlink component in approved path: {rel!r}")
        st = _lstat(full)
        if stat.S_ISLNK(st.st_mode):
            raise RecoveryError(f"approved path is symlink: {rel!r}")
        if not stat.S_ISREG(st.st_mode):
            raise RecoveryError(f"approved path is not a regular file: {rel!r}")
        if st.st_size > MAX_FILE_BYTES:
            raise RecoveryError(f"approved file exceeds size ceiling: {rel!r}")
        resolved.append((rel, full, st))

    records = []
    for rel, full, before in resolved:
        digest, total = _sha256_bounded(full, MAX_FILE_BYTES)
        after = _lstat(full)
        if _stat_signature(before) != _stat_signature(after) or total != after.st_size:
            raise RecoveryError(f"file changed while hashing: {rel!r}")
        records.append(
            {
                "path": rel,
                "size": after.st_size,
                "sha256": digest,
                "mode": stat.S_IMODE(after.st_mode),
                "mtime_ns": after.st_mtime_ns,
            }
        )

    for rel, full, before in resolved:
        after = _lstat(full)
        if _stat_signature(before) != _stat_signature(after):
            raise RecoveryError(f"file changed during manifest pass: {rel!r}")

    records.sort(key=lambda item: item["path"])
    return records


def manifest(root: Path, approved_paths: list[str]) -> dict:
    """Build a read-only manifest for explicit approved files under root."""
    root = _check_absolute_existing_dir(root, "root")
    rel_paths = _validate_approved_paths(approved_paths)
    files = _read_approved(root, rel_paths)
    if not files:
        raise RecoveryError("manifest must not be empty")
    return {
        "schema_version": SCHEMA_VERSION,
        "source_root": str(root),
        "files": files,
    }


# ---- manifest validation ------------------------------------------------


def _require_int(value, field: str, *, minimum: int | None = None, maximum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise RecoveryError(f"manifest field {field} must be an integer")
    if minimum is not None and value < minimum:
        raise RecoveryError(f"manifest field {field} below minimum")
    if maximum is not None and value > maximum:
        raise RecoveryError(f"manifest field {field} above maximum")
    return value


def _validate_manifest(data) -> tuple[Path, list[dict]]:
    if not isinstance(data, dict):
        raise RecoveryError("manifest must be an object")
    if set(data.keys()) != _MANIFEST_KEYS:
        raise RecoveryError("manifest has unexpected or missing keys")
    _require_int(data["schema_version"], "schema_version")
    if data["schema_version"] != SCHEMA_VERSION:
        raise RecoveryError(f"unsupported schema_version: {data['schema_version']!r}")
    source_root = data["source_root"]
    if not isinstance(source_root, str):
        raise RecoveryError("source_root must be a string")
    source_path = Path(source_root)
    if not source_path.is_absolute():
        raise RecoveryError("source_root must be absolute")
    files = data["files"]
    if not isinstance(files, list) or not files:
        raise RecoveryError("manifest files must be a nonempty list")
    seen_exact = set()
    seen_fold = {}
    records = []
    for entry in files:
        if not isinstance(entry, dict):
            raise RecoveryError("manifest file entry must be an object")
        if set(entry.keys()) != _FILE_KEYS:
            raise RecoveryError("manifest file entry has unexpected or missing keys")
        rel = _validate_approved_path(entry["path"])
        if rel in seen_exact:
            raise RecoveryError(f"duplicate manifest path: {rel!r}")
        seen_exact.add(rel)
        folded = rel.casefold()
        if folded in seen_fold:
            raise RecoveryError(
                f"case-insensitive duplicate manifest path: {rel!r} vs {seen_fold[folded]!r}"
            )
        seen_fold[folded] = rel
        size = _require_int(entry["size"], "size", minimum=0, maximum=MAX_FILE_BYTES)
        _require_int(entry["mtime_ns"], "mtime_ns")
        mode = _require_int(entry["mode"], "mode", minimum=0, maximum=0o7777)
        digest = entry["sha256"]
        if not isinstance(digest, str) or not _SHA256_RE.fullmatch(digest):
            raise RecoveryError(f"invalid sha256 for {rel!r}")
        records.append(
            {"path": rel, "size": size, "sha256": digest, "mode": mode}
        )
    records.sort(key=lambda item: item["path"])
    return source_path, records


# ---- tree walking -------------------------------------------------------


def _walk_tree(root: Path) -> tuple[dict, set]:
    """Map NFC relative names to (stat, native path), refusing collisions."""
    files = {}
    empty_dirs = set()
    seen = set()

    def canonical(full):
        rel = unicodedata.normalize("NFC", full.relative_to(root).as_posix())
        folded = rel.casefold()
        if folded in seen:
            raise RecoveryError(f"restored path collision: {rel!r}")
        seen.add(folded)
        return rel
    def walk_error(error):
        raise RecoveryError(f"cannot walk restored tree: {error}") from error

    for dirpath, dirnames, filenames in os.walk(root, followlinks=False, onerror=walk_error):
        base = Path(dirpath)
        kept_dirs = []
        for name in sorted(dirnames):
            full = base / name
            canonical(full)
            st = _lstat(full)
            if stat.S_ISLNK(st.st_mode):
                raise RecoveryError(f"symlink directory in restored tree: {full}")
            if not stat.S_ISDIR(st.st_mode):
                raise RecoveryError(f"special directory entry in restored tree: {full}")
            kept_dirs.append(name)
        dirnames[:] = kept_dirs
        for name in sorted(filenames):
            full = base / name
            st = _lstat(full)
            rel = canonical(full)
            if stat.S_ISLNK(st.st_mode):
                raise RecoveryError(f"symlink file in restored tree: {rel!r}")
            if not stat.S_ISREG(st.st_mode):
                raise RecoveryError(f"special file in restored tree: {rel!r}")
            files[rel] = (st, full)
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False, onerror=walk_error):
        if not dirnames and not filenames:
            rel = unicodedata.normalize("NFC", Path(dirpath).relative_to(root).as_posix())
            if rel != ".":
                empty_dirs.add(rel)
    return files, empty_dirs


def verify_restore(manifest: dict, restored_root: Path) -> dict:
    """Verify a restored tree against a manifest. Read-only; rehearsal only."""
    source_path, records = _validate_manifest(manifest)
    if not isinstance(restored_root, Path):
        raise RecoveryError("restored_root must be a pathlib.Path")
    restored_root = _check_absolute_existing_dir(restored_root, "restored_root")
    _disjoint(source_path, restored_root)

    expected = {r["path"]: r for r in records}

    before_files, before_empty = _walk_tree(restored_root)
    unexpected = set(before_files) - set(expected)
    if unexpected:
        raise RecoveryError(f"unexpected files in restored tree: {sorted(unexpected)}")
    missing = set(expected) - set(before_files)
    if missing:
        raise RecoveryError(f"missing files in restored tree: {sorted(missing)}")
    if before_empty:
        raise RecoveryError(f"unexpected empty directories: {sorted(before_empty)}")

    total_bytes = 0
    for rel in sorted(expected):
        record = expected[rel]
        before, full = before_files[rel]
        if stat.S_IMODE(before.st_mode) != record["mode"]:
            raise RecoveryError(f"mode mismatch for {rel!r}")
        if before.st_size != record["size"]:
            raise RecoveryError(f"size mismatch for {rel!r}")
        digest, total = _sha256_bounded(full, MAX_FILE_BYTES)
        if total != record["size"]:
            raise RecoveryError(f"size changed while hashing {rel!r}")
        if digest != record["sha256"]:
            raise RecoveryError(f"content mismatch for {rel!r}")
        total_bytes += total

    after_files, after_empty = _walk_tree(restored_root)
    if set(after_files) != set(before_files):
        raise RecoveryError("restored tree membership changed during verification")
    if after_empty != before_empty:
        raise RecoveryError("restored tree directories changed during verification")
    for rel, (before, native_path) in before_files.items():
        after, after_path = after_files[rel]
        if after_path != native_path:
            raise RecoveryError(f"restored native path changed: {rel!r}")
        if _stat_signature(before) != _stat_signature(after):
            raise RecoveryError(f"restored file changed during verification: {rel!r}")

    return {
        "status": "verified",
        "file_count": len(expected),
        "total_bytes": total_bytes,
        "rehearsal_only": True,
        "source_freshness": "not_checked",
        "mtime_checked": False,
        "independence_checked": False,
        "manifest_sha256": hashlib.sha256(
            json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
    }
