#!/usr/bin/env python3
"""Read-only OKF readiness audit for a K2B markdown subtree."""

from __future__ import annotations

import argparse
import re
import sys
import unicodedata
from pathlib import Path

import yaml


FRONTMATTER_RE = re.compile(r"^---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|$)", re.DOTALL)
WIKILINK_RE = re.compile(r"\[\[([^\]]+)\]\]")
FENCED_CODE_RE = re.compile(r"(```|~~~).*?\1", re.DOTALL)
INLINE_CODE_RE = re.compile(r"`[^`\n]*`")


def rel(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def read_markdown(path: Path) -> tuple[str | None, str | None]:
    try:
        return path.read_text(encoding="utf-8").lstrip("\ufeff \t\r\n"), None
    except UnicodeDecodeError as exc:
        return None, f"UTF-8 decode failed: {exc}"
    except OSError as exc:
        return None, f"read failed: {exc}"


def parse_frontmatter(text: str) -> tuple[dict[str, object] | None, str | None, str]:
    match = FRONTMATTER_RE.match(text)
    if not match:
        return None, "missing parseable YAML frontmatter", text
    try:
        parsed = yaml.safe_load(match.group(1))
    except yaml.YAMLError as exc:
        return None, f"frontmatter YAML parse failed: {exc}", text[match.end():]
    if not isinstance(parsed, dict):
        return None, "frontmatter must be a YAML mapping", text[match.end():]
    return {str(k): v for k, v in parsed.items()}, None, text[match.end():]


def wikilink_target(raw: str) -> str:
    target = raw.split("|", 1)[0].split("#", 1)[0].strip()
    return target.rsplit("/", 1)[-1].removesuffix(".md")


def wikilink_path(raw: str) -> str:
    return raw.split("|", 1)[0].split("#", 1)[0].strip().removesuffix(".md")


def wikilink_portability_reason(raw: str) -> str | None:
    if "|" in raw:
        return "alias"
    if "#" in raw:
        return "section anchor"
    target = raw.split("|", 1)[0].split("#", 1)[0].strip()
    if "/" in target or target.endswith(".md"):
        return "path-specific target"
    return None


def strip_code(text: str) -> str:
    without_fences = FENCED_CODE_RE.sub("", text)
    without_indented = "\n".join(
        line
        for line in without_fences.splitlines()
        if not line.startswith("    ") and not line.startswith("\t")
    )
    return INLINE_CODE_RE.sub("", without_indented)


def norm_key(value: str) -> str:
    return unicodedata.normalize("NFC", value).casefold()


def target_exists(
    source_path: Path,
    target: str,
    link_root: Path,
    stems: set[str],
    rel_targets: set[str],
) -> bool:
    if not target:
        return False
    if "/" not in target and not target.startswith(".") and not target.endswith(".md"):
        return norm_key(target) in stems

    target_path = Path(target)
    if target_path.is_absolute():
        candidate = link_root / str(target_path).lstrip("/")
    elif target.startswith("."):
        candidate = source_path.parent / target_path
    else:
        candidate = link_root / target_path
    try:
        rel_target = candidate.resolve().relative_to(link_root.resolve())
    except ValueError:
        return False
    return norm_key(rel_target.with_suffix("").as_posix()) in rel_targets


def collect_markdown_files(root: Path) -> list[Path]:
    return sorted(
        path
        for path in root.rglob("*.md")
        if ".sync-conflict-" not in path.name
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("subtree", help="Vault subtree or OKF bundle directory to audit")
    args = parser.parse_args()

    root = Path(args.subtree).expanduser().resolve()
    if not root.exists():
        print(f"okf-audit: path not found: {root}", file=sys.stderr)
        return 2
    if root.is_file():
        files = [root]
        link_root = root.parent
    else:
        files = collect_markdown_files(root)
        link_root = root

    stems = {norm_key(path.stem) for path in files}
    rel_targets = {
        norm_key(path.relative_to(link_root).with_suffix("").as_posix())
        for path in files
    }
    single_file_mode = root.is_file()
    errors: list[str] = []
    warnings: list[str] = []

    for path in files:
        label = rel(path, link_root)
        text, read_error = read_markdown(path)
        if read_error:
            errors.append(f"{label}: {read_error}")
            continue
        assert text is not None
        frontmatter, error, body = parse_frontmatter(text)
        if error:
            errors.append(f"{label}: {error}")
            continue

        note_type = frontmatter.get("type")
        if not isinstance(note_type, str) or not note_type.strip():
            errors.append(f"{label}: missing required type")
        if not frontmatter.get("title"):
            warnings.append(f"{label}: missing recommended title")
        if not frontmatter.get("description"):
            warnings.append(f"{label}: missing recommended description")

        for match in WIKILINK_RE.finditer(strip_code(body)):
            raw = match.group(1)
            target = wikilink_target(raw)
            target_path = wikilink_path(raw)
            reason = wikilink_portability_reason(raw)
            if reason:
                warnings.append(f"{label}: non-portable wikilink ({reason}): [[{raw}]]")
            if single_file_mode:
                continue
            if target and not target_exists(path, target_path, link_root, stems, rel_targets):
                warnings.append(f"{label}: wikilink target not found: {target}")

    for warning in warnings:
        print(f"WARN: {warning}")
    for error in errors:
        print(f"ERROR: {error}", file=sys.stderr)

    if errors:
        print(
            f"okf-audit failed: {len(files)} files, {len(errors)} errors, "
            f"{len(warnings)} warnings",
            file=sys.stderr,
        )
        return 1

    print(f"okf-audit passed: {len(files)} files, {len(warnings)} warnings")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
