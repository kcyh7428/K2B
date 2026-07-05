#!/usr/bin/env python3
"""List raw K2B vault notes that still need /compile digestion."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

import yaml


FRONTMATTER_READ_LIMIT_BYTES = 256 * 1024
FRONTMATTER_RE = re.compile(r"^---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|$)", re.DOTALL)
NEEDS_COMPILE_VALUES = {"", "false", "no", "0", "null", "none"}


def vault_root() -> Path:
    root = os.environ.get("K2B_VAULT_ROOT") or os.environ.get("K2B_VAULT_PATH")
    if root:
        return Path(root).expanduser().resolve()
    return Path("~/Projects/K2B-Vault").expanduser().resolve()


def compiled_value(path: Path) -> str | None:
    try:
        data = path.open("rb").read(FRONTMATTER_READ_LIMIT_BYTES)
    except OSError:
        return None
    text = data.decode("utf-8", errors="replace").lstrip("\ufeff \t\r\n")
    match = FRONTMATTER_RE.match(text)
    if not match:
        return None
    try:
        frontmatter = yaml.safe_load(match.group(1))
    except yaml.YAMLError:
        return None
    if not isinstance(frontmatter, dict) or "compiled" not in frontmatter:
        return None
    raw_value = frontmatter.get("compiled")
    if raw_value is None:
        return ""
    value = str(raw_value).strip()
    return value.lower()


def needs_compile_value(value: str | None) -> bool:
    return value is None or value in NEEDS_COMPILE_VALUES


def iter_raw_notes(root: Path, min_age_hours: float) -> list[dict[str, object]]:
    raw = root / "raw"
    if not raw.is_dir():
        return []

    cutoff = time.time() - (min_age_hours * 3600)
    items: list[dict[str, object]] = []
    for dirpath, dirnames, filenames in os.walk(raw, followlinks=False):
        dir_path = Path(dirpath)
        dirnames[:] = [
            dirname
            for dirname in dirnames
            if not dirname.startswith(".") and not (dir_path / dirname).is_symlink()
        ]
        for filename in filenames:
            if filename.startswith(".") or not filename.endswith(".md"):
                continue
            path = dir_path / filename
            if path.is_symlink():
                continue
            if ".sync-conflict-" in path.name:
                continue
            if path.name == "index.md":
                continue
            try:
                stat = path.stat()
            except OSError:
                continue
            if stat.st_mtime > cutoff:
                continue
            value = compiled_value(path)
            if not needs_compile_value(value):
                continue
            rel = path.relative_to(root).as_posix()
            age_hours = round((time.time() - stat.st_mtime) / 3600, 1)
            items.append(
                {
                    "path": rel,
                    "compiled": "missing" if value is None else value,
                    "age_hours": age_hours,
                    "action": f"/compile {rel}",
                }
            )
    items.sort(key=lambda item: (-float(item["age_hours"]), str(item["path"])))
    return items


def render_markdown(items: list[dict[str, object]]) -> str:
    lines = []
    for item in items:
        lines.append(
            f"- `{item['path']}` -- compiled: {item['compiled']} -- "
            f"`{item['action']}` or mark skipped"
        )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--format", choices=("markdown", "json"), default="markdown")
    parser.add_argument("--max", type=int, default=20)
    parser.add_argument("--min-age-hours", type=float, default=24.0)
    args = parser.parse_args()

    if args.max < 0:
        print("raw-needs-compile: --max must be >= 0", file=sys.stderr)
        return 2
    if args.min_age_hours < 0:
        print("raw-needs-compile: --min-age-hours must be >= 0", file=sys.stderr)
        return 2

    root = vault_root()
    if not root.is_dir():
        print(f"raw-needs-compile: vault root not found: {root}", file=sys.stderr)
        return 2

    items = iter_raw_notes(root, args.min_age_hours)[: args.max]
    if args.format == "json":
        print(json.dumps(items, indent=2, sort_keys=True))
    else:
        output = render_markdown(items)
        if output:
            print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
