"""importance.py

Shared score helper for the importance-weighted rule promotion feature
(Item 1 of the 2026-04-19 memory-architecture plan).

One formula, two callers:
  - scripts/promote-learnings.py sorts candidates DESC by this score
  - scripts/select-lru-victim.py sorts rules ASC by this score (victim
    is the lowest-scoring rule -- least important, safest to demote)

Formula:
    score = (reinforcement_count * max(1, access_count)) / max(1, age_in_days)

Where:
  reinforcement_count  -- count from the `- **Reinforced:** N` bullet,
                          unchanged semantics.
  access_count         -- NEW. Count from the `- **Access count:** N`
                          bullet. Defaults to 1 on first capture.
                          Floored to 1 inside the formula so a zero
                          never produces score=0 by itself.
  last_reinforced_iso  -- `last-reinforced: YYYY-MM-DD` parenthetical
                          for rules, or `Date:` bullet for learnings.
                          Empty / malformed -> age clamped to 1.
  today_iso            -- reference date, so tests can pin the anchor.

Design decisions documented in
  plans/2026-04-19_importance-weighted-rule-promotion.md
  wiki/concepts/feature_importance-weighted-rule-promotion.md
"""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path

DEFAULT_ACCESS_COUNTS_TSV = (
    Path.home() / "Projects" / "K2B-Vault" / "System" / "memory" / "access_counts.tsv"
)


def load_access_counts(tsv_path: str | os.PathLike | None = None) -> dict[str, int]:
    """Read the TSV and return {L-ID: count}. Missing file -> {}.

    TSV format (tab-separated, header required):
        learn_id\tcount\tlast_accessed
        L-2026-04-01-001\t3\t2026-04-17

    Lines starting with '#' are comments; the header row is skipped.
    Malformed rows (non-int count, wrong column count) are skipped silently --
    this is a best-effort loader for ranking, not a validator.
    Duplicate L-IDs: last row wins.
    """
    path = Path(tsv_path) if tsv_path is not None else DEFAULT_ACCESS_COUNTS_TSV
    if not path.exists():
        return {}
    counts: dict[str, int] = {}
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                stripped = line.rstrip("\n\r")
                if not stripped or stripped.startswith("#"):
                    continue
                parts = stripped.split("\t")
                if len(parts) < 2:
                    continue
                lid, raw_count = parts[0].strip(), parts[1].strip()
                if lid == "learn_id":
                    continue  # header row
                try:
                    counts[lid] = int(raw_count)
                except ValueError:
                    continue
    except OSError:
        return {}
    return counts


def _parse_iso(iso_string: str) -> date | None:
    """Return a date from YYYY-MM-DD, or None if the string is empty /
    malformed / sentinel-zero. Callers treat None as "no signal"."""
    if not iso_string:
        return None
    s = iso_string.strip()
    if s in {"", "0000-00-00"}:
        return None
    try:
        return date.fromisoformat(s)
    except ValueError:
        return None


def importance_score(
    reinforcement_count: int,
    access_count: int,
    last_reinforced_iso: str,
    today_iso: str,
) -> float:
    """Return the importance score for sort ordering.

    Higher = more important. Tie at (r=0, anything) collapses to 0 so
    zero-reinforcement rules don't outrank anything regardless of access.

    Age is floored to 1 day to prevent divide-by-zero AND to prevent a
    just-reinforced rule from producing infinity.
    Access is floored to 1 for the multiplier so a zero never nukes the
    numerator on its own.
    Future `last_reinforced_iso` (clock skew) treats age as 1.
    """
    last = _parse_iso(last_reinforced_iso)
    today = _parse_iso(today_iso)
    if last is None or today is None:
        age_days = 1
    else:
        age_days = (today - last).days
        if age_days < 1:
            age_days = 1

    access = access_count if access_count > 1 else 1
    return (reinforcement_count * access) / age_days
