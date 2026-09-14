#!/usr/bin/env python3
"""plate-memory-flags.py -- truthful Memory flags body for k2b-plate.

Read-only parser for the self-improve request/error logs. Emits the existing
Memory flags subsection body (without its heading) as Markdown.

Entry headings are `### R-YYYY-MM-DD-NNN` / `### E-YYYY-MM-DD-NNN`; legacy
`## ` headings are still accepted. A request counts as open only when its
latest explicit dated Status is "open"; missing or malformed status never
becomes open. Duplicate/addendum IDs resolve by the latest explicit dated
status, not by counting the same ID twice; recent errors collapse by ID the
same way before the top-3 limit is applied, so one error cannot occupy
several slots or appear with contradictory labels. Errors are limited to the
last 30 days (supplied HKT calendar date) and are labelled by their explicit
Status (resolved/open) or "unspecified".

Missing log files produce a truthful unavailable line; nothing here writes
to or rewrites the historical logs.
"""
from __future__ import annotations

import argparse
import re
import sys
from datetime import date, datetime
from pathlib import Path

ENTRY_RE = re.compile(r"^#{2,3}\s+((?:R|E)-(\d{4}-\d{2}-\d{2})-(\d{3}))\b")
ANY_HEADING_RE = re.compile(r"^#{1,6}\s")
STATUS_RE = re.compile(r"^-\s+\*\*Status:\*\*\s*(\S+)", re.IGNORECASE)
DATE_RE = re.compile(r"^-\s+\*\*Date:\*\*\s*(\S+)", re.IGNORECASE)
OPEN_LIMIT = 5
ERROR_LIMIT = 3
ERROR_WINDOW_DAYS = 30


def _parse_iso_day(value: str) -> date | None:
    try:
        return datetime.strptime(value.strip(), "%Y-%m-%d").date()
    except (ValueError, AttributeError):
        return None


def _normalize_status(value: str) -> str:
    # "open (waiting-for-data)" resolves to "open"; "closed-obsolete (...)"
    # resolves to "closed-obsolete" and is never treated as open.
    return value.strip().rstrip(".").split()[0].lower()


def _read_entries(path: Path) -> list[dict]:
    """Split a log file into dated entries; unreadable input yields none.

    Entry headings may carry a trailing title or addendum text. Any other
    heading (peer or ancestor level) terminates the active record so its
    Status/Date fields cannot overwrite the preceding entry.
    """
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    entries: list[dict] = []
    current: dict | None = None
    for line in text.splitlines():
        stripped = line.strip()
        match = ENTRY_RE.match(stripped)
        if match:
            current = {"entry_id": match.group(1), "status": None, "day": None}
            entries.append(current)
            continue
        if ANY_HEADING_RE.match(stripped):
            current = None
            continue
        if current is None:
            continue
        status_match = STATUS_RE.match(stripped)
        if status_match:
            current["status"] = _normalize_status(status_match.group(1))
            continue
        date_match = DATE_RE.match(stripped)
        if date_match:
            parsed_day = _parse_iso_day(date_match.group(1))
            if parsed_day is not None:
                current["day"] = parsed_day
    return entries


def _open_request_ids(requests_path: Path, today: date) -> list[tuple[str, date]]:
    """IDs whose latest valid dated explicit status is open, in file order.

    Only entries with both an explicit status and a valid date participate:
    undated or malformed entries never become open and never clobber a valid
    dated status for the same ID. Future-dated entries are not current
    evidence and never become open either.
    """
    by_id: dict[str, tuple[date, str]] = {}
    order: list[str] = []
    for entry in _read_entries(requests_path):
        if entry["status"] is None or entry["day"] is None:
            continue
        if entry["day"] > today:
            continue
        entry_id = entry["entry_id"]
        if entry_id not in by_id:
            order.append(entry_id)
        previous = by_id.get(entry_id)
        if previous is None or entry["day"] >= previous[0]:
            by_id[entry_id] = (entry["day"], entry["status"])
    return [
        (entry_id, by_id[entry_id][0])
        for entry_id in order
        if by_id[entry_id][1] == "open"
    ]


def _recent_errors(errors_path: Path, today: date) -> list[tuple[str, date, str]]:
    """Errors within the last 30 days labelled resolved/open/unspecified.

    Duplicate/addendum IDs collapse to their latest valid dated explicit
    status before the caller applies the top-3 limit: same-date entries
    resolve to the last occurrence, and malformed or future-dated addenda
    cannot override a valid dated status, so a single error can never occupy
    several slots or appear with contradictory labels.
    """
    by_id: dict[str, tuple[date, str]] = {}
    order: list[str] = []
    for entry in _read_entries(errors_path):
        day = entry["day"]
        if day is None:
            continue
        age = (today - day).days
        if age < 0 or age > ERROR_WINDOW_DAYS:
            continue
        status = entry["status"]
        label = status if status in {"resolved", "open"} else "unspecified"
        entry_id = entry["entry_id"]
        if entry_id not in by_id:
            order.append(entry_id)
        previous = by_id.get(entry_id)
        if previous is None or day >= previous[0]:
            by_id[entry_id] = (day, label)
    return [
        (entry_id, by_id[entry_id][0], by_id[entry_id][1])
        for entry_id in order
    ]


def build_body(requests_path: Path, errors_path: Path, today: date) -> str:
    lines: list[str] = []
    if requests_path.is_file():
        open_ids = _open_request_ids(requests_path, today)[:OPEN_LIMIT]
        if open_ids:
            lines.append("**Open R-IDs (top 5):**")
            for entry_id, day in open_ids:
                lines.append(f"- {entry_id} ({day.isoformat()})")
            lines.append("")
        else:
            lines.append("_(no open R-IDs)_")
            lines.append("")
    else:
        lines.append("_(self-improve requests log unavailable)_")
        lines.append("")

    if errors_path.is_file():
        recent = _recent_errors(errors_path, today)[:ERROR_LIMIT]
        if recent:
            lines.append("**Recent E-IDs (top 3, last 30 days):**")
            for entry_id, day, label in recent:
                lines.append(f"- {entry_id} ({day.isoformat()}, {label})")
            lines.append("")
        else:
            lines.append("_(no E-IDs in the last 30 days)_")
            lines.append("")
    else:
        lines.append("_(self-improve errors log unavailable)_")
        lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--requests", type=Path, required=True)
    parser.add_argument("--errors", type=Path, required=True)
    parser.add_argument("--today", required=True, help="HKT calendar date YYYY-MM-DD")
    args = parser.parse_args(argv)
    today = _parse_iso_day(args.today)
    if today is None:
        print(f"plate-memory-flags: invalid --today {args.today!r} (want YYYY-MM-DD)", file=sys.stderr)
        return 2
    sys.stdout.write(build_body(args.requests, args.errors, today))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
