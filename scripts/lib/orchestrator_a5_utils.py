#!/usr/bin/env python3
"""Shared A5 filesystem safety helpers for deploy manifests and evidence."""

from __future__ import annotations

import os
import stat
from pathlib import Path


def path_has_symlink_component(path: Path) -> bool:
    expanded = path.expanduser()
    for candidate in (expanded, *expanded.parents):
        try:
            if candidate.is_symlink():
                return True
        except OSError:
            return True
    return False


def safe_read_text(path: Path, label: str) -> tuple[str | None, str]:
    if path_has_symlink_component(path):
        return (None, f"{label} must not be a symlink: {path}")
    if not hasattr(os, "O_NOFOLLOW"):
        return (None, f"{label} requires O_NOFOLLOW for symlink-safe reads")
    try:
        before_stat = os.stat(path, follow_symlinks=False)
    except OSError as exc:
        return (None, f"{label} unreadable: {exc}")
    if not stat.S_ISREG(before_stat.st_mode):
        return (None, f"{label} must be a regular file: {path}")
    flags = os.O_RDONLY | os.O_NOFOLLOW
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        return (None, f"{label} unreadable: {exc}")
    try:
        fd_stat = os.fstat(fd)
        if (fd_stat.st_dev, fd_stat.st_ino) != (before_stat.st_dev, before_stat.st_ino):
            return (None, f"{label} changed while opening: {path}")
        with os.fdopen(fd, "r", encoding="utf-8") as fh:
            fd = None
            text = fh.read()
    except OSError as exc:
        return (None, f"{label} unreadable: {exc}")
    finally:
        if fd is not None:
            os.close(fd)
    if not text:
        return (None, f"{label} is empty: {path}")
    return (text, "")
