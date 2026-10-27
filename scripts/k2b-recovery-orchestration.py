#!/usr/bin/env python3
"""CLI for the bounded recovery observation and planning helper."""

from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path

_THIS_DIR = Path(__file__).resolve().parent
if str(_THIS_DIR / "lib") not in sys.path:
    sys.path.insert(0, str(_THIS_DIR / "lib"))

import recovery_orchestration as ro  # noqa: E402

_MAX_APPROVED_BYTES = 256 * 1024


def _emit(payload: dict) -> int:
    sys.stdout.write(json.dumps(payload, sort_keys=True) + "\n")
    return 0


def _error(reason: str) -> int:
    sys.stderr.write("recovery-orchestration: " + reason + "\n")
    return 2


def _parse_flags(args: list[str], allowed: tuple[str, ...]) -> dict[str, str]:
    values: dict[str, str] = {}
    i = 0
    while i < len(args):
        token = args[i]
        if not token.startswith("--"):
            raise ro.OrchestrationError("arguments_invalid")
        name = token[2:]
        if name not in allowed or name in values:
            raise ro.OrchestrationError("arguments_invalid")
        if i + 1 >= len(args):
            raise ro.OrchestrationError("arguments_invalid")
        values[name] = args[i + 1]
        i += 2
    return values


def _read_approved_bounded(path: Path) -> bytes:
    if not path.is_absolute():
        raise ro.OrchestrationError("approved_list_unavailable")
    try:
        ro._reject_symlink_ancestors(path, allow_missing_leaf=False)
    except ro.OrchestrationError:
        raise ro.OrchestrationError("approved_list_unavailable")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        fd = os.open(path, flags)
    except OSError:
        raise ro.OrchestrationError("approved_list_unavailable")
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise ro.OrchestrationError("approved_list_unavailable")
        if st.st_size > _MAX_APPROVED_BYTES:
            raise ro.OrchestrationError("approved_list_too_large")
        chunks: list[bytes] = []
        total = 0
        while total <= _MAX_APPROVED_BYTES:
            chunk = os.read(fd, min(65536, _MAX_APPROVED_BYTES + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
        if total > _MAX_APPROVED_BYTES:
            raise ro.OrchestrationError("approved_list_too_large")
        post = os.fstat(fd)
        named = os.lstat(path)
        signature = lambda item: (item.st_dev, item.st_ino, item.st_size,
                                  item.st_mtime_ns, item.st_ctime_ns)
        if signature(st) != signature(post) or signature(post) != signature(named):
            raise ro.OrchestrationError("approved_list_unavailable")
        return b"".join(chunks)
    except OSError:
        raise ro.OrchestrationError("approved_list_unavailable")
    finally:
        try:
            os.close(fd)
        except OSError:
            pass


def _expected_specs(flags: dict[str, str]):
    path = flags.get("expected-witness-specs")
    if path is None:
        return None, None
    raw = _read_approved_bounded(Path(path))
    expected = ro._loads_strict(raw, "expected_witness_specs_invalid")
    if not isinstance(expected, list):
        raise ro.OrchestrationError("expected_witness_specs_invalid")
    expected = ro._normalize_lock_specs(expected, Path("/"), Path("/"))
    return expected, ro.hashlib.sha256(raw).hexdigest()


def _witness_receipt(payload, expected_digest):
    payload["witness_binding"] = "expected_specs_matched" if expected_digest else "unbound_preflight"
    payload["expected_witness_specs_sha256"] = expected_digest
    return payload


def cmd_status(args: list[str]) -> int:
    flags = _parse_flags(args, ("actor", "expected-witness-specs"))
    actor = flags.get("actor")
    if actor not in ro.ACTORS:
        raise ro.OrchestrationError("actor_invalid")
    expected, digest = _expected_specs(flags)
    observation = ro.local_status(actor)
    received = ro.time.time_ns()
    observation = ro.validate_observation(observation, actor=actor, now_ns=received,
                                           expected_witness_specs=expected)
    return _emit(_witness_receipt({"observation": observation, "received_at_ns": received}, digest))


def cmd_counterpart(args: list[str]) -> int:
    flags = _parse_flags(args, ("payload-sha256", "expected-witness-specs"))
    digest = flags.get("payload-sha256")
    if digest is None or not ro._SHA256_HEX_RE.fullmatch(digest):
        raise ro.OrchestrationError("arguments_invalid")
    expected, expected_digest = _expected_specs(flags)
    result = ro.probe_sjm(expected_payload_sha256=digest, expected_witness_specs=expected)
    return _emit(_witness_receipt(result, expected_digest))


def cmd_archive_plan(args: list[str]) -> int:
    flags = _parse_flags(args, ("root", "approved-list"))
    root = flags.get("root")
    approved = flags.get("approved-list")
    if not root or not approved:
        raise ro.OrchestrationError("arguments_invalid")
    root_path = Path(root)
    if not root_path.is_absolute():
        raise ro.OrchestrationError("archive_root_not_absolute")
    raw = _read_approved_bounded(Path(approved))
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise ro.OrchestrationError("approved_list_malformed")
    if not isinstance(parsed, list) or not all(isinstance(p, str) for p in parsed):
        raise ro.OrchestrationError("approved_list_malformed")
    plan = ro.archive_plan(root_path, parsed)
    if _read_approved_bounded(Path(approved)) != raw:
        raise ro.OrchestrationError("approved_list_changed")
    plan["approved_list_sha256"] = ro.hashlib.sha256(raw).hexdigest()
    plan["plan_sha256"] = ro.plan_digest(plan)
    return _emit(plan)


_SUBCOMMANDS = {
    "status": cmd_status,
    "counterpart": cmd_counterpart,
    "archive-plan": cmd_archive_plan,
}


def main(argv: list[str]) -> int:
    if not argv:
        return _error("subcommand_required")
    handler = _SUBCOMMANDS.get(argv[0])
    if handler is None:
        return _error("subcommand_invalid")
    try:
        return handler(argv[1:])
    except ro.OrchestrationError as exc:
        return _error(exc.reason)
    except Exception:
        return _error("orchestration_failed")


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
