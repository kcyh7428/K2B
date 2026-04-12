#!/usr/bin/env python3
"""Add a slug to a wiki page's `related:` frontmatter array.

Contract:
  - Idempotent: adding the same slug twice is a no-op
  - Atomic: writes via tmp + rename
  - Optimistic concurrency: re-reads file immediately before writing; if mtime
    changed since initial read, exits 2 (caller requeues the proposal)
  - Preserves rest of frontmatter and body verbatim
  - Uses inline list format: related: ["[[a]]", "[[b]]"]
  - If `related:` exists in block list format, converts to inline list

Usage: k2b-weave-add-related.py <page-path> <to-slug>

Exit codes:
  0  slug added (or already present -- idempotent success)
  1  hard error (file missing, no frontmatter, write failed)
  2  optimistic concurrency retry (mtime changed during read-modify-write)
"""

import os
import re
import sys
import tempfile
from typing import List, Tuple


def split_frontmatter(content: str) -> Tuple[str, str, str]:
    """Split content into (pre_fm, frontmatter_body, post_fm).
    pre_fm is everything before the opening ---, frontmatter_body is between the
    two ---, post_fm is everything after the closing ---. Raises ValueError if
    there's no valid frontmatter block.
    """
    if not content.startswith("---\n") and not content.startswith("---\r\n"):
        raise ValueError("no opening frontmatter delimiter")
    # Find the closing ---
    # Skip the opening line
    lines = content.split("\n")
    if lines[0].rstrip() != "---":
        raise ValueError("opening line is not ---")
    close_idx = None
    for i in range(1, len(lines)):
        if lines[i].rstrip() == "---":
            close_idx = i
            break
    if close_idx is None:
        raise ValueError("no closing frontmatter delimiter")
    fm_lines = lines[1:close_idx]
    body_lines = lines[close_idx + 1:]
    return "---\n", "\n".join(fm_lines), "\n---\n" + "\n".join(body_lines)


def normalize_wikilink(slug: str) -> str:
    """Given a slug or wikilink-formatted string, return a canonical `[[slug]]`."""
    slug = slug.strip()
    # Strip surrounding quotes
    slug = slug.strip('"').strip("'")
    # Strip wikilink brackets
    if slug.startswith("[[") and slug.endswith("]]"):
        slug = slug[2:-2]
    # Strip alias portion
    if "|" in slug:
        slug = slug.split("|", 1)[0]
    # Strip section anchor
    if "#" in slug:
        slug = slug.split("#", 1)[0]
    return f"[[{slug.strip()}]]"


def parse_related_field(fm_body: str) -> Tuple[List[str], int, int]:
    """Parse the `related:` field if present. Returns (links, start_line, end_line).
    start_line and end_line are 0-indexed line indices within fm_body; (-1, -1) if absent.
    Supports both inline `related: [...]` and block list form.
    """
    lines = fm_body.split("\n")
    for i, line in enumerate(lines):
        if re.match(r"^related\s*:", line):
            # Inline form: related: [...]
            m = re.match(r"^related\s*:\s*\[(.*)\]\s*$", line)
            if m:
                inner = m.group(1).strip()
                if not inner:
                    return [], i, i
                # Split on commas, respecting quoted strings (simple case)
                parts = [p.strip() for p in inner.split(",") if p.strip()]
                return [normalize_wikilink(p) for p in parts], i, i
            # Block form: related:\n  - "[[a]]"\n  - "[[b]]"
            links = []
            end = i
            for j in range(i + 1, len(lines)):
                if re.match(r"^\s*-\s+(.+)$", lines[j]):
                    item = re.match(r"^\s*-\s+(.+)$", lines[j]).group(1)
                    links.append(normalize_wikilink(item))
                    end = j
                elif lines[j].strip() == "" or re.match(r"^[a-zA-Z_]", lines[j]):
                    # End of block list (next key or blank line followed by key)
                    break
                else:
                    break
            return links, i, end
    return [], -1, -1


def build_inline_related(links: List[str]) -> str:
    """Build the inline `related: ["[[a]]", "[[b]]"]` line."""
    if not links:
        return 'related: []'
    quoted = ", ".join(f'"{link}"' for link in links)
    return f"related: [{quoted}]"


def add_related(fm_body: str, to_slug: str) -> Tuple[str, bool]:
    """Return (new_fm_body, changed). changed is False if slug already present."""
    target_link = normalize_wikilink(to_slug)
    links, start, end = parse_related_field(fm_body)
    if target_link in links:
        return fm_body, False
    new_links = links + [target_link]
    new_line = build_inline_related(new_links)
    lines = fm_body.split("\n")
    if start == -1:
        # No related: field -- append before end of frontmatter body
        # Prefer to add right before `up:` if present, else at end
        up_idx = next((i for i, ln in enumerate(lines) if re.match(r"^up\s*:", ln)), None)
        if up_idx is not None:
            lines = lines[:up_idx] + [new_line] + lines[up_idx:]
        else:
            lines = lines + [new_line]
    else:
        lines = lines[:start] + [new_line] + lines[end + 1:]
    return "\n".join(lines), True


def atomic_write(path: str, content: str) -> None:
    """Write content to path via fsync(temp) + rename + fsync(dir). POSIX atomic + durable."""
    dirname = os.path.dirname(path) or "."
    fd, tmp = tempfile.mkstemp(prefix=".weave.", dir=dirname)
    try:
        with os.fdopen(fd, "w") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.rename(tmp, path)
        # fsync parent directory to ensure rename is durable
        dir_fd = os.open(dirname, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def main(argv: List[str]) -> int:
    if len(argv) != 3:
        sys.stderr.write("Usage: k2b-weave-add-related.py <page-path> <to-slug>\n")
        return 1
    page_path = argv[1]
    to_slug = argv[2]

    if not os.path.isfile(page_path):
        sys.stderr.write(f"ERROR: file not found: {page_path}\n")
        return 1

    # Initial read + capture mtime for optimistic concurrency check
    initial_mtime = os.stat(page_path).st_mtime_ns
    with open(page_path, "r") as f:
        content = f.read()

    try:
        pre, fm_body, post = split_frontmatter(content)
    except ValueError as e:
        sys.stderr.write(f"ERROR: {page_path}: {e}\n")
        return 1

    new_fm_body, changed = add_related(fm_body, to_slug)
    if not changed:
        # Already present -- idempotent success
        return 0

    new_content = pre + new_fm_body + post

    # Optimistic concurrency check: re-read mtime just before writing
    current_mtime = os.stat(page_path).st_mtime_ns
    if current_mtime != initial_mtime:
        sys.stderr.write(f"CONCURRENCY: {page_path} changed during read-modify-write\n")
        return 2

    atomic_write(page_path, new_content)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
