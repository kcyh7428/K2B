#!/usr/bin/env python3
"""normalize.py -- Washing Machine date pre-normaliser.

Wraps the backward-looking ``scripts/normalize-dates.py`` shipped 2026-04-19
and adds forward-looking relatives (``next Friday``, ``next week``, etc.).
Called by ``washingMachine.ts`` before the classifier runs so the LLM sees
resolved ISO dates, not the calendar math.

Usage
-----
    normalize.py --anchor YYYY-MM-DD [--json] < input.txt

Default output: stdin text with relative dates rewritten to YYYY-MM-DD.
With ``--json``: an object ``{rewritten_text, substitutions}`` where each
substitution is ``{original, iso, kind}``. kind is one of:
``yesterday``, ``today``, ``tomorrow``, ``last_weekday``, ``next_weekday``,
``last_week``, ``next_week``, ``last_month``, ``last_year``, ``n_units_ago``.

Why a wrapper (and not a second copy): the backward pass is shared with the
``/learn`` flow and has test coverage there. Duplicating it would be the
exact "two homes per fact" failure that K2B's memory layer ownership rule
exists to prevent.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
from datetime import date, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
_BACKWARD_PATH = REPO_ROOT / "scripts" / "normalize-dates.py"


def _load_backward():
    if not _BACKWARD_PATH.exists():
        raise RuntimeError(
            f"normalize.py requires {_BACKWARD_PATH} (shipped 2026-04-19); "
            f"file is missing. Restore from git or reinstall the K2B repo."
        )
    spec = importlib.util.spec_from_file_location("k2b_normalize_dates", _BACKWARD_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot build importlib spec for {_BACKWARD_PATH}")
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as exc:  # noqa: BLE001 -- surfacing the real error
        raise RuntimeError(
            f"normalize.py failed to load {_BACKWARD_PATH}: {exc}. "
            f"Every Washing Machine gate invocation would fail until fixed."
        ) from exc
    for symbol in ("normalize", "WORD_NUMBERS", "subtract_months", "subtract_years"):
        if not hasattr(module, symbol):
            raise RuntimeError(
                f"normalize-dates.py is missing required symbol '{symbol}'; "
                f"contract between the two files has drifted."
            )
    return module


_BACKWARD = _load_backward()


WEEKDAY_INDEX = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}


def iso(d: date) -> str:
    return d.strftime("%Y-%m-%d")


def next_weekday(anchor: date, target_name: str) -> date:
    target = WEEKDAY_INDEX[target_name.lower()]
    diff = (target - anchor.weekday()) % 7
    if diff == 0:
        diff = 7
    return anchor + timedelta(days=diff)


def _rewrite_with_hook(text: str, pattern: str, replacer) -> tuple[str, list[dict]]:
    subs: list[dict] = []

    def callback(match: re.Match) -> str:
        original = match.group(0)
        replacement, kind, resolved = replacer(match)
        subs.append({"original": original, "iso": iso(resolved), "kind": kind})
        return replacement

    rewritten = re.sub(pattern, callback, text, flags=re.IGNORECASE)
    return rewritten, subs


def forward_pass(text: str, anchor: date) -> tuple[str, list[dict]]:
    subs: list[dict] = []

    def _next_weekday(match: re.Match):
        resolved = next_weekday(anchor, match.group(1))
        return iso(resolved), "next_weekday", resolved

    text, s = _rewrite_with_hook(
        text,
        r"\bnext\s+(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",
        _next_weekday,
    )
    subs.extend(s)

    def _next_week(match: re.Match):
        resolved = anchor + timedelta(days=7)
        return iso(resolved), "next_week", resolved

    text, s = _rewrite_with_hook(text, r"\bnext\s+week\b", _next_week)
    subs.extend(s)

    return text, subs


def backward_pass(text: str, anchor: date) -> tuple[str, list[dict]]:
    """Diff before/after to infer which substitutions fired. The backward
    module rewrites in place without reporting substitutions; we run it and
    compare tokens to populate the metadata list.
    """
    before = text
    after = _BACKWARD.normalize(text, anchor)
    if before == after:
        return after, []

    subs: list[dict] = []

    patterns = [
        (r"\byesterday\b", "yesterday", anchor - timedelta(days=1)),
        (r"\btoday\b", "today", anchor),
        (r"\btomorrow\b", "tomorrow", anchor + timedelta(days=1)),
        (r"\blast\s+week\b", "last_week", anchor - timedelta(days=7)),
    ]
    for pat, kind, resolved in patterns:
        for match in re.finditer(pat, before, flags=re.IGNORECASE):
            subs.append({"original": match.group(0), "iso": iso(resolved), "kind": kind})

    for match in re.finditer(
        r"\blast\s+(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",
        before,
        flags=re.IGNORECASE,
    ):
        target = WEEKDAY_INDEX[match.group(1).lower()]
        diff = (anchor.weekday() - target) % 7
        if diff == 0:
            diff = 7
        resolved = anchor - timedelta(days=diff)
        subs.append({"original": match.group(0), "iso": iso(resolved), "kind": "last_weekday"})

    for match in re.finditer(
        r"\b(a|an|one|two|three|four|five|six|seven|eight|nine|ten|\d+)"
        r"\s+(day|days|week|weeks|month|months|year|years)\s+ago\b",
        before,
        flags=re.IGNORECASE,
    ):
        n_word = match.group(1).lower()
        n = int(n_word) if n_word.isdigit() else _BACKWARD.WORD_NUMBERS.get(n_word, 1)
        unit = match.group(2).lower().rstrip("s")
        if unit == "day":
            resolved = anchor - timedelta(days=n)
        elif unit == "week":
            resolved = anchor - timedelta(days=n * 7)
        elif unit == "month":
            resolved = _BACKWARD.subtract_months(anchor, n)
        elif unit == "year":
            resolved = _BACKWARD.subtract_years(anchor, n)
        else:
            continue
        subs.append({"original": match.group(0), "iso": iso(resolved), "kind": "n_units_ago"})

    return after, subs


def normalize_all(text: str, anchor: date) -> tuple[str, list[dict]]:
    rewritten, back_subs = backward_pass(text, anchor)
    rewritten, fwd_subs = forward_pass(rewritten, anchor)
    return rewritten, back_subs + fwd_subs


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="normalize.py")
    parser.add_argument("--anchor", required=True)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv[1:])

    try:
        anchor = date.fromisoformat(args.anchor)
    except ValueError as exc:
        print(f"normalize: invalid anchor date: {exc}", file=sys.stderr)
        return 2

    text = sys.stdin.read()
    rewritten, subs = normalize_all(text, anchor)

    if args.json:
        json.dump({"rewritten_text": rewritten, "substitutions": subs}, sys.stdout, ensure_ascii=False)
        sys.stdout.write("\n")
    else:
        sys.stdout.write(rewritten)

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
