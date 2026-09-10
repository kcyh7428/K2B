#!/usr/bin/env python3
"""Append approved small records without letting SJM write the shared vault.

Home writes the canonical append-only hub. SJM and unknown hosts queue the
same validated record under private local state for later Home reconciliation.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import secrets
import stat
from datetime import date
from pathlib import Path


HOME_USERS = {"keithmbpm2"}
SOURCE_ONLY_USERS = {"keithcheung", "fastshower"}


def writer_role() -> str:
    role = os.environ.get("K2B_CAPTURE_WRITER_ROLE", "").strip().lower()
    if role:
        return role
    user = os.environ.get("USER", "").strip()
    if user in HOME_USERS:
        return "home"
    if user in SOURCE_ONLY_USERS:
        return "source-only"
    return "unknown"


def _reject_symlink_components(path: Path) -> None:
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        try:
            component = os.lstat(current)
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(component.st_mode):
            raise RuntimeError(f"append path contains a symlink: {current}")


def _safe_append(path: Path, line: str, *, private: bool) -> None:
    _reject_symlink_components(path.parent)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    parent_stat = path.parent.stat()
    if parent_stat.st_uid != os.getuid() or not stat.S_ISDIR(parent_stat.st_mode):
        raise RuntimeError(f"unsafe append parent: {path.parent}")
    flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(path, flags, 0o600)
    try:
        file_stat = os.fstat(fd)
        if not stat.S_ISREG(file_stat.st_mode) or file_stat.st_uid != os.getuid():
            raise RuntimeError(f"unsafe append target: {path}")
        if private and stat.S_IMODE(file_stat.st_mode) & 0o077:
            raise RuntimeError(f"append target must not be group/world accessible: {path}")
        fcntl.flock(fd, fcntl.LOCK_EX)
        os.write(fd, (line + "\n").encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)


def _target(hub: str) -> tuple[Path, bool]:
    home = writer_role() == "home"
    if home:
        vault = Path(
            os.environ.get("K2B_VAULT_PATH", "~/Projects/K2B-Vault")
        ).expanduser().resolve(strict=False)
        relative = {
            "usage": "wiki/context/skill-usage-log.tsv",
            "preference": "wiki/context/preference-signals.jsonl",
        }[hub]
        return vault / relative, True
    pending_root = Path(
        os.environ.get("K2B_LOCAL_PENDING_ROOT", "~/.local/state/k2b")
    ).expanduser().resolve(strict=False)
    return pending_root / f"pending-{hub}-records.jsonl", False


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="hub", required=True)
    usage = sub.add_parser("usage")
    usage.add_argument("--skill", required=True)
    usage.add_argument("--summary", required=True)
    preference = sub.add_parser("preference")
    preference.add_argument("--file", required=True)
    preference.add_argument("--source-skill", required=True)
    preference.add_argument("--type", required=True)
    preference.add_argument("--action", required=True)
    preference.add_argument("--days-in-inbox", required=True, type=int)
    preference.add_argument("--has-feedback", required=True)
    preference.add_argument("--feedback", default="")
    args = parser.parse_args()

    target, canonical = _target(args.hub)
    if args.hub == "usage":
        if not args.skill.startswith("k2b-"):
            parser.error("usage skill must start with k2b-")
        if any(ch in args.summary for ch in "\r\n\t"):
            parser.error("usage summary must be a single TSV field")
        if canonical:
            line = "\t".join(
                (date.today().isoformat(), args.skill, secrets.token_hex(4), args.summary)
            )
        else:
            line = json.dumps(
                {
                    "hub": "usage",
                    "date": date.today().isoformat(),
                    "skill": args.skill,
                    "run_id": secrets.token_hex(4),
                    "summary": args.summary,
                },
                separators=(",", ":"),
                sort_keys=True,
            )
    else:
        record = {
            "date": date.today().isoformat(),
            "file": args.file,
            "source_skill": args.source_skill,
            "type": args.type,
            "action": args.action,
            "days_in_inbox": args.days_in_inbox,
            "has_feedback": args.has_feedback,
            "feedback": args.feedback,
        }
        if any("\n" in str(value) or "\r" in str(value) for value in record.values()):
            parser.error("preference fields must be single-line")
        line = json.dumps(record, separators=(",", ":"), sort_keys=True)
        if not canonical:
            line = json.dumps(
                {"hub": "preference", "record": record},
                separators=(",", ":"),
                sort_keys=True,
            )

    _safe_append(target, line, private=not canonical)
    print(("appended " if canonical else "queued locally ") + str(target))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
