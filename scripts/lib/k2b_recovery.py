"""P2-B synthetic recovery preparation primitives (read-only).

manifest(root, approved_paths) and verify_restore(manifest, restored_root)
produce and check a manifest of explicitly approved regular files. Nothing in
this module copies, restores, or writes files. Standard library only.

Selected POSIX file modes and, for new manifests, exact ancestor directory
modes (root '.' plus each ancestor of an approved file) are recorded and
checked. No ACL, owner, xattr, timestamp, ancestor-outside-root, or
independent-device verification is claimed.
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
MAX_MANIFEST_BYTES = 64 * 1024 * 1024
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
    {"schema_version", "source_root", "files", "directory_modes"}
)
_LEGACY_MANIFEST_KEYS = frozenset({"schema_version", "source_root", "files"})
_FILE_KEYS = frozenset({"path", "size", "sha256", "mode", "mtime_ns"})
_DIRECTORY_MODE_MAX = 0o7777


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


def _expected_directory_keys(rel_paths) -> set[str]:
    """Return exact required directory key set: '.' plus every file ancestor."""
    keys = {"."}
    for rel in rel_paths:
        parts = rel.split("/")
        for index in range(1, len(parts)):
            keys.add("/".join(parts[:index]))
    return keys


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


def _read_approved(root: Path, rel_paths: list[str]) -> tuple[list[dict], dict[str, tuple]]:
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

    signatures = {}
    for rel, full, before in resolved:
        after = _lstat(full)
        if _stat_signature(before) != _stat_signature(after):
            raise RecoveryError(f"file changed during manifest pass: {rel!r}")
        signatures[rel] = _stat_signature(after)

    records.sort(key=lambda item: item["path"])
    return records, signatures


def _directory_mode_snapshot(root: Path, rel_paths: list[str]) -> dict[str, int]:
    """Capture exact mode bits for root '.' and every approved-file ancestor."""
    modes: dict[str, int] = {}
    keys = _expected_directory_keys(rel_paths)
    for key in sorted(keys):
        full = root if key == "." else root / key
        st = _lstat(full)
        if stat.S_ISLNK(st.st_mode):
            raise RecoveryError(f"symlink directory in source: {key!r}")
        if not stat.S_ISDIR(st.st_mode):
            raise RecoveryError(f"not a directory in source: {key!r}")
        modes[key] = stat.S_IMODE(st.st_mode)
    # Recheck that source directory modes stayed stable during capture.
    for key in sorted(keys):
        full = root if key == "." else root / key
        st = _lstat(full)
        if stat.S_IMODE(st.st_mode) != modes[key]:
            raise RecoveryError(f"source directory mode changed during manifest pass: {key!r}")
    return modes


def manifest(root: Path, approved_paths: list[str]) -> dict:
    """Build a read-only manifest for explicit approved files under root."""
    root = _check_absolute_existing_dir(root, "root")
    rel_paths = _validate_approved_paths(approved_paths)
    directory_modes = _directory_mode_snapshot(root, rel_paths)
    files, signatures = _read_approved(root, rel_paths)
    after_modes = _directory_mode_snapshot(root, rel_paths)
    if after_modes != directory_modes:
        raise RecoveryError("source directory mode changed while hashing")
    directory_modes = after_modes
    if not files:
        raise RecoveryError("manifest must not be empty")
    result = {
        "schema_version": SCHEMA_VERSION,
        "source_root": str(root),
        "files": files,
        "directory_modes": directory_modes,
    }
    serialize_manifest(result)
    if _directory_mode_snapshot(root, rel_paths) != directory_modes:
        raise RecoveryError("source directory mode changed during serialization")
    for rel in rel_paths:
        if _stat_signature(_lstat(root / rel)) != signatures[rel]:
            raise RecoveryError(f"source file changed during serialization: {rel!r}")
    return result


def serialize_manifest(result: dict) -> str:
    """Return the exact bounded JSON text emitted by the manifest CLI."""
    try:
        serialized = json.dumps(result, sort_keys=True, indent=2, allow_nan=False) + "\n"
    except (ValueError, TypeError) as exc:
        raise RecoveryError("nonstandard or out-of-range JSON value") from exc
    if len(serialized.encode("utf-8")) > MAX_MANIFEST_BYTES:
        raise RecoveryError("serialized manifest exceeds 64 MiB ceiling; use separately verified supported tooling")
    return serialized


# ---- manifest validation ------------------------------------------------


def _require_int(value, field: str, *, minimum: int | None = None, maximum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise RecoveryError(f"manifest field {field} must be an integer")
    if minimum is not None and value < minimum:
        raise RecoveryError(f"manifest field {field} below minimum")
    if maximum is not None and value > maximum:
        raise RecoveryError(f"manifest field {field} above maximum")
    return value


def _validate_directory_modes(directory_modes, required_keys: set[str]) -> dict[str, int]:
    if not isinstance(directory_modes, dict):
        raise RecoveryError("manifest field directory_modes must be an object")
    if not all(isinstance(key, str) for key in directory_modes):
        raise RecoveryError("directory_modes keys must be strings")
    actual = set(directory_modes.keys())
    missing = required_keys - actual
    extra = actual - required_keys
    if missing:
        raise RecoveryError(f"directory_modes missing keys: {sorted(missing)}")
    if extra:
        raise RecoveryError(f"directory_modes has unexpected keys: {sorted(extra)}")
    seen_fold: dict[str, str] = {}
    result: dict[str, int] = {}
    for key in sorted(actual):
        if not isinstance(key, str) or not key:
            raise RecoveryError("directory_modes keys must be nonempty strings")
        if key != ".":
            if any(ord(c) < 32 or ord(c) == 127 for c in key):
                raise RecoveryError(f"directory_modes key has control character: {key!r}")
            if "\\" in key or key.startswith("/"):
                raise RecoveryError(f"directory_modes key is unsafe: {key!r}")
            if key != unicodedata.normalize("NFC", key):
                raise RecoveryError(f"directory_modes key is not NFC: {key!r}")
            parts = key.split("/")
            for part in parts:
                if part in {"", ".", ".."}:
                    raise RecoveryError(f"directory_modes key has invalid component: {key!r}")
                if part != unicodedata.normalize("NFC", part):
                    raise RecoveryError(f"directory_modes key component is not NFC: {key!r}")
        elif key == ".":
            pass
        folded = key.casefold()
        if folded in seen_fold:
            raise RecoveryError(
                f"directory_modes case-insensitive key collision: {key!r} vs {seen_fold[folded]!r}"
            )
        seen_fold[folded] = key
        value = directory_modes[key]
        result[key] = _require_int(
            value, f"directory_modes[{key!r}]", minimum=0, maximum=_DIRECTORY_MODE_MAX
        )
    return result


def _validate_manifest(data) -> tuple[Path, list[dict], dict[str, int] | None]:
    if not isinstance(data, dict):
        raise RecoveryError("manifest must be an object")
    keys = set(data.keys())
    has_directory_modes = "directory_modes" in keys
    allowed = _MANIFEST_KEYS if has_directory_modes else _LEGACY_MANIFEST_KEYS
    if keys != allowed:
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

    directory_modes = None
    if has_directory_modes:
        expected_keys = _expected_directory_keys([r["path"] for r in records])
        directory_modes = _validate_directory_modes(data["directory_modes"], expected_keys)
    return source_path, records, directory_modes


# ---- tree walking -------------------------------------------------------


def _walk_tree(root: Path) -> tuple[dict, set, dict]:
    """Map NFC relative names to (stat, native path), refusing collisions.

    Also returns a map of NFC relative directory names to native Path objects.
    """
    files = {}
    directories = {".": root}
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
            rel = canonical(full)
            st = _lstat(full)
            if stat.S_ISLNK(st.st_mode):
                raise RecoveryError(f"symlink directory in restored tree: {full}")
            if not stat.S_ISDIR(st.st_mode):
                raise RecoveryError(f"special directory entry in restored tree: {full}")
            directories[rel] = full
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
    return files, empty_dirs, directories


def _check_directory_modes(
    restored_root: Path,
    expected: dict[str, int],
    directories: dict[str, Path],
) -> None:
    """Check root and every expected directory mode using native walked paths."""
    for key in sorted(expected):
        if key == ".":
            native = restored_root
        else:
            native = directories.get(key)
            if native is None:
                raise RecoveryError(f"missing directory in restored tree: {key!r}")
        st = _lstat(native)
        if stat.S_ISLNK(st.st_mode):
            raise RecoveryError(f"symlink directory in restored tree: {key!r}")
        if not stat.S_ISDIR(st.st_mode):
            raise RecoveryError(f"not a directory in restored tree: {key!r}")
        if stat.S_IMODE(st.st_mode) != expected[key]:
            raise RecoveryError(f"directory mode mismatch for {key!r}")


def _check_restored_directory_set(directories: dict[str, Path], expected_dirs: set[str]) -> None:
    actual = set(directories.keys())
    unexpected = actual - expected_dirs
    if unexpected:
        raise RecoveryError(f"unexpected directories in restored tree: {sorted(unexpected)}")
    missing = expected_dirs - actual
    if missing:
        raise RecoveryError(f"missing directories in restored tree: {sorted(missing)}")


def _canonical_manifest_sha256(manifest_obj: dict) -> str:
    try:
        encoded = json.dumps(manifest_obj, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (ValueError, TypeError) as exc:
        raise RecoveryError("nonstandard or out-of-range JSON value") from exc
    return hashlib.sha256(encoded).hexdigest()


def verify_restore(manifest: dict, restored_root: Path, *, allow_legacy_no_directory_modes: bool = False) -> dict:
    """Verify a restored tree against a manifest. Read-only; rehearsal only."""
    source_path, records, directory_modes = _validate_manifest(manifest)
    if type(allow_legacy_no_directory_modes) is not bool:
        raise RecoveryError("legacy opt-in must be a boolean")
    if directory_modes is None and not allow_legacy_no_directory_modes:
        raise RecoveryError("legacy manifest requires allow_legacy_no_directory_modes=True; directory coverage is absent")
    if not isinstance(restored_root, Path):
        raise RecoveryError("restored_root must be a pathlib.Path")
    restored_root = _check_absolute_existing_dir(restored_root, "restored_root")
    root_path = str(restored_root.resolve(strict=True))
    root_before = _lstat(restored_root)
    _disjoint(source_path, restored_root)

    expected = {r["path"]: r for r in records}
    expected_dirs = _expected_directory_keys(list(expected))
    modes_checked = directory_modes is not None

    before_files, before_empty, before_dirs = _walk_tree(restored_root)
    _check_restored_directory_set(before_dirs, expected_dirs)
    unexpected = set(before_files) - set(expected)
    if unexpected:
        raise RecoveryError(f"unexpected files in restored tree: {sorted(unexpected)}")
    missing = set(expected) - set(before_files)
    if missing:
        raise RecoveryError(f"missing files in restored tree: {sorted(missing)}")
    if before_empty:
        raise RecoveryError(f"unexpected empty directories: {sorted(before_empty)}")
    if modes_checked:
        _check_directory_modes(restored_root, directory_modes, before_dirs)

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

    after_files, after_empty, after_dirs = _walk_tree(restored_root)
    if set(after_files) != set(before_files):
        raise RecoveryError("restored tree membership changed during verification")
    if set(after_dirs) != set(before_dirs):
        raise RecoveryError("restored tree directories changed during verification")
    if after_empty != before_empty:
        raise RecoveryError("restored tree directories changed during verification")
    for rel, (before, native_path) in before_files.items():
        after, after_path = after_files[rel]
        if after_path != native_path:
            raise RecoveryError(f"restored native path changed: {rel!r}")
        if _stat_signature(before) != _stat_signature(after):
            raise RecoveryError(f"restored file changed during verification: {rel!r}")
    if modes_checked:
        _check_directory_modes(restored_root, directory_modes, after_dirs)
    if (_stat_signature(root_before) != _stat_signature(_lstat(restored_root))
            or str(restored_root.resolve(strict=True)) != root_path):
        raise RecoveryError("restored root identity changed during verification")

    canonical = _canonical_manifest_sha256(manifest)
    return {
        "status": "verified" if modes_checked else "verified_legacy_no_directory_modes",
        "directory_mode_gate": "passed" if modes_checked else "not_passed_legacy",
        "file_count": len(expected),
        "total_bytes": total_bytes,
        "restored_root": root_path,
        "restored_root_device": root_before.st_dev,
        "rehearsal_only": True,
        "source_freshness": "not_checked",
        "mtime_checked": False,
        "independence_checked": False,
        "file_modes_checked": True,
        "manifest_artifact_checked": False,
        "directory_modes_checked": modes_checked,
        "canonical_manifest_sha256": canonical,
        "manifest_sha256": canonical,
    }
