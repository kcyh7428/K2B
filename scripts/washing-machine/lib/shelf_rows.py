#!/usr/bin/env python3
"""Shelf row parser / serialiser.

Row format (after the leading "- " bullet):
    <YYYY-MM-DD> | <type> | <slug> | <key>:<value> | <key>:<value> | ...

Pipes inside values are escaped as ``\\|``. Keys never contain ``|`` or ``:``.
Values may contain ``:``; partition is on the first ``:`` only.

Plan: plans/2026-04-21_washing-machine-ship-1.md Commit 1.
Consumed by shelf-writer.sh (serialize), embed-index.py (parse, Commit 2),
retrieve.py (parse, Commit 2).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from typing import Dict, List

ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
SLUG_RE = re.compile(r"^[\w\-./]+$", re.UNICODE)
TYPE_RE = re.compile(r"^[a-z][a-z0-9_\-]*$")
KEY_RE = re.compile(r"^[a-z][a-z0-9_\-]*$")


@dataclass
class Row:
    date: str
    type: str
    slug: str
    attrs: Dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "date": self.date,
            "type": self.type,
            "slug": self.slug,
            "attrs": dict(self.attrs),
        }


def _escape_value(v: str) -> str:
    return v.replace("\\", "\\\\").replace("|", "\\|")


def _split_unescaped_pipe(s: str) -> List[str]:
    parts: List[str] = []
    buf: List[str] = []
    i = 0
    n = len(s)
    while i < n:
        c = s[i]
        if c == "\\" and i + 1 < n:
            nxt = s[i + 1]
            if nxt == "|":
                buf.append("|")
                i += 2
                continue
            if nxt == "\\":
                buf.append("\\")
                i += 2
                continue
            buf.append(c)
            i += 1
            continue
        if c == "|":
            parts.append("".join(buf))
            buf = []
            i += 1
            continue
        buf.append(c)
        i += 1
    parts.append("".join(buf))
    return parts


def validate_date(date: str) -> None:
    if not ISO_DATE_RE.match(date):
        raise ValueError(f"date must be YYYY-MM-DD, got {date!r}")


def validate_type(type_: str) -> None:
    if not TYPE_RE.match(type_):
        raise ValueError(f"type must match {TYPE_RE.pattern}, got {type_!r}")


def validate_slug(slug: str) -> None:
    # Slugs may contain unicode letters (for person_羅克強-醫生 style), digits,
    # underscores, hyphens, dots, slashes. No pipes, no colons, no whitespace.
    if not slug or any(ch in slug for ch in "|: \t\n\r"):
        raise ValueError(f"slug contains forbidden character, got {slug!r}")


def validate_key(key: str) -> None:
    if not KEY_RE.match(key):
        raise ValueError(f"attr key must match {KEY_RE.pattern}, got {key!r}")


def validate_value(value: str) -> None:
    if "\n" in value or "\r" in value:
        raise ValueError("attr value must not contain newlines")


def serialize(date: str, type_: str, slug: str, attrs: Dict[str, str]) -> str:
    validate_date(date)
    validate_type(type_)
    validate_slug(slug)
    parts = [date, type_, slug]
    for k, v in attrs.items():
        validate_key(k)
        validate_value(v)
        parts.append(f"{k}:{_escape_value(v)}")
    return " | ".join(parts)


def parse(row_text: str) -> Row:
    text = row_text.strip()
    if text.startswith("- "):
        text = text[2:].strip()
    if not text:
        raise ValueError("empty row")
    segments = [seg.strip() for seg in _split_unescaped_pipe(text)]
    if len(segments) < 3:
        raise ValueError(f"row has fewer than 3 segments: {row_text!r}")
    date, type_, slug, *attr_segs = segments
    validate_date(date)
    validate_type(type_)
    validate_slug(slug)
    attrs: Dict[str, str] = {}
    for seg in attr_segs:
        if not seg:
            continue
        if ":" not in seg:
            raise ValueError(f"attr segment missing ':' separator: {seg!r}")
        key, _, value = seg.partition(":")
        key = key.strip()
        validate_key(key)
        validate_value(value)
        attrs[key] = value
    return Row(date=date, type=type_, slug=slug, attrs=attrs)


def _cli_serialize(args: argparse.Namespace) -> int:
    attrs: Dict[str, str] = {}
    for item in args.attr or []:
        if ":" not in item:
            print(
                f"shelf_rows: --attr {item!r} missing ':' separator", file=sys.stderr
            )
            return 64
        k, _, v = item.partition(":")
        attrs[k] = v
    try:
        print(serialize(args.date, args.type, args.slug, attrs))
    except ValueError as e:
        print(f"shelf_rows: {e}", file=sys.stderr)
        return 65
    return 0


def _cli_parse(args: argparse.Namespace) -> int:
    row_text = sys.stdin.read()
    try:
        row = parse(row_text)
    except ValueError as e:
        print(f"shelf_rows: {e}", file=sys.stderr)
        return 65
    json.dump(row.to_dict(), sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


def main(argv: List[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="shelf_rows")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("serialize", help="Emit a row line for given fields")
    s.add_argument("--date", required=True)
    s.add_argument("--type", required=True)
    s.add_argument("--slug", required=True)
    s.add_argument("--attr", action="append", metavar="KEY:VALUE")
    s.set_defaults(func=_cli_serialize)

    pa = sub.add_parser("parse", help="Read a row line from stdin, emit JSON")
    pa.set_defaults(func=_cli_parse)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
