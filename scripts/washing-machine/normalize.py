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


# --- Ship 1B: date-contradiction detection ---------------------------------
#
# When an attachment pipeline (VLM OCR on a business card) supplies an
# OCR-detected date alongside the message metadata timestamp, we flag the
# extraction for confirmation before writing to the semantic shelf if the
# two disagree by more than 6 months. This is how the 2026-04-01 business
# card mis-dated bug gets killed: the old path silently wrote a Daily note
# dated 2025-04-11, driven by an OCR-sourced date on the card itself.

_SIX_MONTHS_DAYS = 183
_LOW_CONFIDENCE_THRESHOLD = 0.7


def _parse_iso_prefix(value: str) -> date | None:
    """Parse a date out of either an ISO prefix (YYYY-MM-DD) or a raw
    epoch-milliseconds integer string. Returns None for unparseable
    input so the caller can log a distinct reason instead of crashing.

    Supporting both shapes matters because the attachment ingest path
    passes Telegram's message timestamp as raw epoch-ms (e.g.
    1711987200000), while the text path feeds ISO strings. Without the
    epoch-ms branch, ship-1b date-contradiction detection was silently
    returning date_parse_error on every real attachment (caught by
    review round 1 on Commit 2, finding #1).
    """
    if not value:
        return None
    parsed: date | None = None
    # Epoch-ms path: all-digits, >= 10 chars (10 digits is seconds, 13 is ms).
    if value.isdigit() and len(value) >= 10:
        try:
            from datetime import datetime
            ts = int(value)
            if ts > 10**12:  # looks like ms, not seconds
                ts = ts // 1000
            parsed = datetime.fromtimestamp(ts).date()
        except (ValueError, OSError, OverflowError):
            return None
    else:
        # ISO path: may be date (YYYY-MM-DD) or full timestamp; slice first 10.
        try:
            parsed = date.fromisoformat(value[:10])
        except (ValueError, TypeError):
            return None

    # Plausibility window: reject dates outside 2000-2100. Guards against
    # phone numbers / order IDs that happened to be >= 10 digits and parsed
    # cleanly as epoch timestamps. Keith is unlikely to capture a business
    # card from 1970 or 2200 for the foreseeable future.
    if parsed is None or parsed.year < 2000 or parsed.year > 2100:
        return None
    return parsed


def detect_date_contradiction(ocr_date: str | None, message_ts: str | None) -> list[str]:
    """Return list of needs_confirmation_reason codes.

    Codes:
      date_mismatch     OCR date and message timestamp disagree by > 6 months
      date_parse_error  one of the dates wouldn't parse (could be ambiguous
                        OCR like "4/11" -> 2025 vs 2026)

    An empty list means the two dates agree and the write is safe.
    """
    if not ocr_date or not message_ts:
        # Either missing → no contradiction possible; text-only path lands here.
        return []
    ocr_parsed = _parse_iso_prefix(ocr_date)
    msg_parsed = _parse_iso_prefix(message_ts)
    if ocr_parsed is None or msg_parsed is None:
        return ["date_parse_error"]
    delta = abs((ocr_parsed - msg_parsed).days)
    if delta > _SIX_MONTHS_DAYS:
        return ["date_mismatch"]
    return []


def assess_confidence(date_confidence: float | None) -> list[str]:
    """Low-confidence flagging. Date confidence < 0.7 triggers the same
    pending-confirmation UX as a 6-month mismatch."""
    if date_confidence is None:
        return []
    if date_confidence < _LOW_CONFIDENCE_THRESHOLD:
        return ["low_confidence"]
    return []


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="normalize.py")
    parser.add_argument("--anchor", required=True)
    parser.add_argument("--json", action="store_true")
    parser.add_argument(
        "--ocr-date",
        default=None,
        help="(Ship 1B) ISO date detected by OCR on an attachment, if any",
    )
    parser.add_argument(
        "--message-ts",
        default=None,
        help="(Ship 1B) ISO timestamp of the message metadata, if any",
    )
    parser.add_argument(
        "--date-confidence",
        default=None,
        type=float,
        help="(Ship 1B) classifier-reported date confidence in [0, 1]",
    )
    args = parser.parse_args(argv[1:])

    try:
        anchor = date.fromisoformat(args.anchor)
    except ValueError as exc:
        print(f"normalize: invalid anchor date: {exc}", file=sys.stderr)
        return 2

    text = sys.stdin.read()
    rewritten, subs = normalize_all(text, anchor)

    needs_confirmation: list[str] = []
    needs_confirmation.extend(detect_date_contradiction(args.ocr_date, args.message_ts))
    needs_confirmation.extend(assess_confidence(args.date_confidence))

    if args.json:
        json.dump(
            {
                "rewritten_text": rewritten,
                "substitutions": subs,
                "needs_confirmation_reason": needs_confirmation,
            },
            sys.stdout,
            ensure_ascii=False,
        )
        sys.stdout.write("\n")
    else:
        sys.stdout.write(rewritten)

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
