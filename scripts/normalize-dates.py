#!/usr/bin/env python3
"""normalize-dates.py

Rewrite relative date expressions in text to ISO YYYY-MM-DD form, anchored
to an explicit reference date supplied as argv[1].

Called from the k2b-feedback skill's /learn flow to canonicalize phrases like
"yesterday", "3 days ago", "last Monday", "last week" before Keith's
description is written to self_improve_learnings.md.

The anchor is passed explicitly so callers can pin it to the session's start
date rather than the system's current date at invocation time.

Usage:
    normalize-dates.py <anchor-iso-date>

Reads text from stdin, writes normalized text to stdout. Exits 2 on bad args.
"""

from __future__ import annotations

import re
import sys
from calendar import monthrange
from datetime import date, timedelta

WEEKDAY_INDEX = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}

WORD_NUMBERS = {
    "a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
}


def iso(d: date) -> str:
    return d.strftime("%Y-%m-%d")


def word_to_int(token: str) -> int:
    token = token.lower()
    if token.isdigit():
        return int(token)
    return WORD_NUMBERS.get(token, 1)


def subtract_months(anchor: date, n: int) -> date:
    m = anchor.month - n
    y = anchor.year
    while m <= 0:
        m += 12
        y -= 1
    last = monthrange(y, m)[1]
    return date(y, m, min(anchor.day, last))


def subtract_years(anchor: date, n: int) -> date:
    y = anchor.year - n
    try:
        return date(y, anchor.month, anchor.day)
    except ValueError:
        last = monthrange(y, anchor.month)[1]
        return date(y, anchor.month, last)


def normalize(text: str, anchor: date) -> str:
    def replace_n_units_ago(match: re.Match) -> str:
        n = word_to_int(match.group(1))
        unit = match.group(2).lower().rstrip("s")
        if unit == "day":
            return iso(anchor - timedelta(days=n))
        if unit == "week":
            return iso(anchor - timedelta(days=n * 7))
        if unit == "month":
            return iso(subtract_months(anchor, n))
        if unit == "year":
            return iso(subtract_years(anchor, n))
        return match.group(0)

    text = re.sub(
        r"\b(a|an|one|two|three|four|five|six|seven|eight|nine|ten|\d+)"
        r"\s+(day|days|week|weeks|month|months|year|years)\s+ago\b",
        replace_n_units_ago,
        text,
        flags=re.IGNORECASE,
    )

    def replace_last_weekday(match: re.Match) -> str:
        target = WEEKDAY_INDEX[match.group(1).lower()]
        diff = (anchor.weekday() - target) % 7
        if diff == 0:
            diff = 7
        return iso(anchor - timedelta(days=diff))

    text = re.sub(
        r"\blast\s+(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",
        replace_last_weekday,
        text,
        flags=re.IGNORECASE,
    )

    text = re.sub(
        r"\blast\s+week\b",
        iso(anchor - timedelta(days=7)),
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"\blast\s+month\b",
        iso(subtract_months(anchor, 1)),
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"\blast\s+year\b",
        iso(subtract_years(anchor, 1)),
        text,
        flags=re.IGNORECASE,
    )

    text = re.sub(
        r"\byesterday\b",
        iso(anchor - timedelta(days=1)),
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"\btoday\b",
        iso(anchor),
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"\btomorrow\b",
        iso(anchor + timedelta(days=1)),
        text,
        flags=re.IGNORECASE,
    )

    return text


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("Usage: normalize-dates.py <anchor-iso-date>", file=sys.stderr)
        return 2
    try:
        anchor = date.fromisoformat(argv[1])
    except ValueError as exc:
        print(f"Invalid anchor date: {exc}", file=sys.stderr)
        return 2
    sys.stdout.write(normalize(sys.stdin.read(), anchor))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
