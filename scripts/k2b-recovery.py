#!/usr/bin/env python3
"""P2-B synthetic recovery preparation CLI.

Read-only. Builds or verifies a manifest of explicitly approved files.
Does not copy, restore, or write payloads. JSON operational output/errors;
help output is human readable. Selected POSIX file modes and, for new
manifests, exact ancestor directory modes are recorded and checked. No ACL,
owner, xattr, timestamp, ancestor-outside-root, or independent-device
verification is claimed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))

from k2b_recovery import RecoveryError, manifest, verify_restore, serialize_manifest, _canonical_manifest_sha256, _stat_signature, MAX_MANIFEST_BYTES  # noqa: E402


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise RecoveryError(message)


def _reject_duplicate_keys(pairs):
    seen = set()
    for key, _value in pairs:
        if key in seen:
            raise RecoveryError(f"duplicate JSON key: {key!r}")
        seen.add(key)
    return dict(pairs)


def _reject_constant(value):
    raise RecoveryError(f"nonstandard JSON constant: {value}")


def _emit(payload: dict, stream) -> None:
    json.dump(payload, stream, sort_keys=True, indent=2)
    stream.write("\n")


def _cmd_manifest(args) -> dict:
    result = manifest(Path(args.root), list(args.approved_paths))
    sys.stdout.write(serialize_manifest(result))
    return {"status": "ok"}


def _cmd_verify(args, evidence=None) -> dict:
    evidence = {} if evidence is None else evidence
    manifest_path = Path(args.manifest)
    if not manifest_path.is_absolute():
        raise RecoveryError("manifest path must be absolute")
    if manifest_path.is_symlink():
        raise RecoveryError("manifest path must not be a symlink")
    if not manifest_path.is_file():
        raise RecoveryError("manifest path must be an existing regular file")
    try:
        manifest_resolved = str(manifest_path.resolve(strict=True))
        before = manifest_path.lstat()
        fd = os.open(manifest_path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as handle:
            opened = os.fstat(handle.fileno())
            if not stat.S_ISREG(opened.st_mode) or _stat_signature(before) != _stat_signature(opened):
                raise RecoveryError("manifest changed before opening")
            if opened.st_size > MAX_MANIFEST_BYTES:
                raise RecoveryError("manifest exceeds 64 MiB ceiling")
            raw_bytes = handle.read(MAX_MANIFEST_BYTES + 1)
            if len(raw_bytes) > MAX_MANIFEST_BYTES:
                raise RecoveryError("manifest exceeds 64 MiB ceiling")
            evidence["observed_bytes_sha256"] = hashlib.sha256(raw_bytes).hexdigest()
            if (_stat_signature(opened) != _stat_signature(os.fstat(handle.fileno()))
                    or _stat_signature(opened) != _stat_signature(manifest_path.lstat())):
                raise RecoveryError("manifest changed while reading")
    except (OSError, UnicodeError) as exc:
        raise RecoveryError(f"cannot read manifest: {exc}") from exc
    evidence["manifest_artifact_sha256"] = evidence.pop("observed_bytes_sha256")
    try:
        text = raw_bytes.decode("utf-8-sig", errors="strict")
    except UnicodeDecodeError as exc:
        raise RecoveryError(f"manifest is not valid UTF-8: {exc}") from exc
    try:
        data = json.loads(text, object_pairs_hook=_reject_duplicate_keys, parse_constant=_reject_constant)
    except RecoveryError:
        raise
    except (ValueError, TypeError) as exc:
        raise RecoveryError(f"invalid manifest JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise RecoveryError("manifest JSON must be an object")
    evidence["canonical_manifest_sha256"] = _canonical_manifest_sha256(data)
    if "directory_modes" not in data and not getattr(args, "allow_legacy_no_directory_modes", False):
        raise RecoveryError("legacy manifest requires explicit --allow-legacy-no-directory-modes; directory permission coverage is absent")
    result = verify_restore(data, Path(args.restored_root), allow_legacy_no_directory_modes=getattr(args, "allow_legacy_no_directory_modes", False))
    try:
        final_signature = _stat_signature(manifest_path.lstat())
        final_path = str(manifest_path.resolve(strict=True))
    except OSError as exc:
        raise RecoveryError(f"manifest unreadable during final identity check: {exc}") from exc
    if (_stat_signature(opened) != final_signature or final_path != manifest_resolved):
        raise RecoveryError("manifest identity changed before receipt")
    result["manifest_artifact_sha256"] = hashlib.sha256(raw_bytes).hexdigest()
    result["manifest_artifact_checked"] = True
    result["manifest_path"] = manifest_resolved
    _emit(result, sys.stdout)
    return {"status": "ok"}


def _build_parser() -> argparse.ArgumentParser:
    parser = Parser(
        prog="k2b-recovery",
        description="Read-only P2-B rehearsal: 16 MiB per file; restored tree must contain exactly approved files and their ancestors, with no empty directories. Checks selected POSIX file and directory mode bits only.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_manifest = sub.add_parser("manifest", help="build a manifest from approved files")
    p_manifest.add_argument("--root", required=True, help="absolute existing source root")
    p_manifest.add_argument(
        "--approved-path",
        dest="approved_paths",
        action="append",
        required=True,
        help="repeated explicit relative file path",
    )

    p_verify = sub.add_parser("verify", help="verify a restored tree against a manifest")
    p_verify.add_argument("--manifest", required=True, help="absolute manifest JSON path")
    p_verify.add_argument("--restored-root", required=True, help="absolute restored directory")
    p_verify.add_argument("--allow-legacy-no-directory-modes", action="store_true", help="explicitly allow byte/file-mode checks without directory-mode coverage")

    return parser


def main(argv=None) -> int:
    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return int(exc.code or 0)
    except RecoveryError as exc:
        _emit({"status": "error", "error": str(exc)}, sys.stderr)
        return 2
    evidence = {}
    try:
        if args.command == "manifest":
            _cmd_manifest(args)
        elif args.command == "verify":
            _cmd_verify(args, evidence)
        else:
            raise RecoveryError(f"unknown command: {args.command!r}")
    except RecoveryError as exc:
        _emit({"status": "error", "error": str(exc), **evidence}, sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 - fail closed with structured JSON
        _emit({"status": "error", "error": f"{type(exc).__name__}: {exc}", **evidence}, sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
