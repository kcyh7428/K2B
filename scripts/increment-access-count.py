#!/usr/bin/env python3
"""increment-access-count.py

Bump the citation count for one or more L-IDs in the K2B access-counts TSV.

Called from `/ship` step 13.5 after writing the session-summary file. Each
L-ID argument becomes +1 on the corresponding row's count; argument duplicates
per invocation count as a single bump (not N). Previously-unseen L-IDs are
inserted with count=1.

Atomic rewrite: temp file + os.replace. No partial writes.

This script is the SOLE writer of access_counts.tsv. /learn does not touch
this file; it only touches self_improve_learnings.md. This split preserves the
K2B single-writer discipline for the main learnings file (P1 #4 in the
Codex plan review for feature_importance-weighted-rule-promotion).

Usage:
    increment-access-count.py <L-ID> [<L-ID> ...]

Env:
    K2B_ACCESS_COUNTS_TSV  path to the TSV (default: canonical K2B-Vault path)

Exit codes:
    0 - bumped successfully (unknown L-IDs inserted at count=1)
    1 - usage error (no L-IDs provided)
    2 - IO error on write
"""

from __future__ import annotations

import os
import sys
import tempfile
from datetime import date
from pathlib import Path

DEFAULT_TSV = (
    Path.home() / "Projects" / "K2B-Vault" / "System" / "memory" / "access_counts.tsv"
)
HEADER_COMMENT = "# access_counts.tsv -- citation counts per L-ID, single writer: scripts/increment-access-count.py"
HEADER_ROW = "learn_id\tcount\tlast_accessed"


def _tsv_path() -> Path:
    raw = os.environ.get("K2B_ACCESS_COUNTS_TSV")
    return Path(raw) if raw else DEFAULT_TSV


def _read_rows(path: Path) -> tuple[list[str], dict[str, tuple[int, str]]]:
    """Return (comment_lines, rows_by_id). rows_by_id is {lid: (count, date)}."""
    comments: list[str] = []
    rows: dict[str, tuple[int, str]] = {}
    if not path.exists():
        return comments, rows
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            stripped = line.rstrip("\n\r")
            if not stripped:
                continue
            if stripped.startswith("#"):
                comments.append(stripped)
                continue
            parts = stripped.split("\t")
            if len(parts) < 2:
                continue
            lid = parts[0].strip()
            if lid == "learn_id":
                continue  # header row
            raw_count = parts[1].strip()
            raw_date = parts[2].strip() if len(parts) >= 3 else ""
            try:
                rows[lid] = (int(raw_count), raw_date)
            except ValueError:
                continue
    return comments, rows


def _write_atomic(path: Path, comments: list[str], rows: dict[str, tuple[int, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    if comments:
        lines.extend(comments)
    else:
        lines.append(HEADER_COMMENT)
    lines.append(HEADER_ROW)
    for lid in sorted(rows):
        count, accessed = rows[lid]
        lines.append(f"{lid}\t{count}\t{accessed}")
    body = "\n".join(lines) + "\n"

    fd, tmp = tempfile.mkstemp(prefix=".tmp_access_counts_", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(body)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
        # fsync the parent directory so the rename is durable on filesystems
        # where a crash after os.replace could otherwise lose the new dirent.
        # Best-effort: platforms without O_DIRECTORY support (e.g. Windows)
        # simply skip this step.
        try:
            dfd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(dfd)
            finally:
                os.close(dfd)
        except OSError:
            pass
    except Exception:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("Usage: increment-access-count.py <L-ID> [<L-ID> ...]", file=sys.stderr)
        return 1

    unique_ids: list[str] = []
    seen: set[str] = set()
    for arg in argv[1:]:
        lid = arg.strip()
        if not lid or lid in seen:
            continue
        seen.add(lid)
        unique_ids.append(lid)

    if not unique_ids:
        print("Usage: increment-access-count.py <L-ID> [<L-ID> ...]", file=sys.stderr)
        return 1

    path = _tsv_path()
    try:
        comments, rows = _read_rows(path)
    except OSError as exc:
        print(f"read failed: {exc}", file=sys.stderr)
        return 2

    today = date.today().isoformat()
    for lid in unique_ids:
        if lid in rows:
            current, _ = rows[lid]
            rows[lid] = (current + 1, today)
        else:
            rows[lid] = (1, today)

    try:
        _write_atomic(path, comments, rows)
    except OSError as exc:
        print(f"write failed: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
