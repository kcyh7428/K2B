"""Render the session-start loop dashboard.

Reads:
  - $K2B_LOOP_CANDIDATES      observer-candidates.md
  - $K2B_LOOP_REVIEW_DIR      review/ directory (flat .md files)
  - $K2B_LOOP_RESEARCH_DIR    raw/research/ directory

Emits a compact dashboard to stdout. Empty sections collapse to silence.
"""
from __future__ import annotations

import os
import re
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from loop import loop_lib  # noqa: E402


def _find_research_without_delivery(research_dir: Path, today: date):
    if not research_dir.is_dir():
        return []
    out = []
    for p in sorted(research_dir.glob("*.md")):
        m = re.match(r"(\d{4}-\d{2}-\d{2})_", p.name)
        if not m:
            continue
        try:
            when = datetime.strptime(m.group(1), "%Y-%m-%d").date()
        except ValueError:
            continue
        age_days = (today - when).days
        if age_days <= 30:
            continue
        try:
            head = p.read_text(encoding="utf-8").splitlines()[:40]
        except OSError:
            continue
        in_fm = False
        fm_closed = False
        has_delivery = False
        for i, line in enumerate(head):
            if i == 0 and line.strip() == "---":
                in_fm = True
                continue
            if in_fm and line.strip() == "---":
                fm_closed = True
                break
            if in_fm and re.match(r"^follow-up-delivery:\s*\S", line):
                if "null" not in line.lower():
                    has_delivery = True
        if not fm_closed:
            continue
        if has_delivery:
            continue
        out.append((p, age_days))
    out.sort(key=lambda t: -t[1])
    return out[:3]


def _list_review_items(review_dir: Path):
    if not review_dir.is_dir():
        return []
    return sorted(
        p for p in review_dir.glob("*.md") if p.name not in {"index.md"}
    )


def main() -> int:
    candidates_path = Path(os.environ["K2B_LOOP_CANDIDATES"])
    review_dir = Path(os.environ.get("K2B_LOOP_REVIEW_DIR", ""))
    research_dir = Path(os.environ.get("K2B_LOOP_RESEARCH_DIR", ""))
    today = date.today()

    candidates = (
        loop_lib.parse_candidates(candidates_path) if candidates_path.exists() else []
    )
    reviews = _list_review_items(review_dir) if str(review_dir) else []
    researches = (
        _find_research_without_delivery(research_dir, today) if str(research_dir) else []
    )

    if not candidates and not reviews and not researches:
        return 0

    lines: list[str] = []
    lines.append(f"## K2B LOOP DASHBOARD -- {today.isoformat()}")
    lines.append("")
    lines.append("Routing grammar (a N / r N / d N):")
    lines.append("  a N = ACCEPT item N (apply routing)")
    lines.append("  r N = REJECT item N (archive)")
    lines.append("  d N = DEFER item N (leave for next session)")
    lines.append(
        "Claude will call scripts/loop/loop-apply.sh with your choices before the next prompt."
    )
    lines.append("")

    idx = 0
    if candidates:
        lines.append(f"### Observer candidates ({len(candidates)})")
        for cand in candidates:
            idx += 1
            lines.append(
                f"  [{idx}] [{cand.severity}] {cand.item_id} · {cand.area} · {cand.rule}"
            )
        lines.append("")

    if reviews:
        lines.append(f"### Review queue ({len(reviews)})")
        for p in reviews:
            idx += 1
            lines.append(f"  [{idx}] {p.name}")
        lines.append("")

    if researches:
        lines.append(f"### Research without delivery link ({len(researches)})")
        for p, age in researches:
            idx += 1
            lines.append(f"  [{idx}] {p.name} (age {age} days)")
        lines.append("")

    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
