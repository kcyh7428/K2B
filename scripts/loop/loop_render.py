"""Render the session-start loop dashboard.

Ship 2 merges observer candidates and review items into one routable index
space. Observer entries carry their severity + area. Review entries show the
filename. Both participate in `a N / r N / d N` keystrokes. Research-without-
delivery-link stays informational in Ship 2.

Reads:
  $K2B_LOOP_CANDIDATES          observer-candidates.md
  $K2B_LOOP_REVIEW_DIR          review/ directory
  $K2B_LOOP_RESEARCH_DIR        raw/research/ directory
  $K2B_LOOP_DEFERS              observer-defers.jsonl (Ship 2)

Emits a compact dashboard to stdout. Empty surfaces collapse to silence.
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


def main() -> int:
    candidates_path = Path(os.environ["K2B_LOOP_CANDIDATES"])
    review_dir = Path(os.environ.get("K2B_LOOP_REVIEW_DIR", ""))
    research_dir = Path(os.environ.get("K2B_LOOP_RESEARCH_DIR", ""))
    defers_path_str = os.environ.get("K2B_LOOP_DEFERS", "")
    today = date.today()

    candidates = (
        loop_lib.parse_candidates(candidates_path) if candidates_path.exists() else []
    )
    reviews = (
        loop_lib.list_reviews(review_dir) if str(review_dir) else []
    )
    researches = (
        _find_research_without_delivery(research_dir, today)
        if str(research_dir)
        else []
    )
    defers = (
        loop_lib.read_defers(Path(defers_path_str)) if defers_path_str else {}
    )

    if not candidates and not reviews and not researches:
        return 0

    lines: list[str] = []
    lines.append(f"## K2B LOOP DASHBOARD -- {today.isoformat()}")
    lines.append("")
    lines.append("Routing grammar (a N / r N / d N) -- observer candidates + review queue routable:")
    lines.append("  a N = ACCEPT item N (observer: learning; review: move to Ready/)")
    lines.append("  r N = REJECT item N (observer: archive; review: move to Archive/review-archive)")
    lines.append("  d N = DEFER item N (increments counter; auto-archive on 3rd defer)")
    lines.append(
        "Claude will call scripts/loop/loop-apply.sh with your choices before the next prompt."
    )
    lines.append("")

    index = 0
    if candidates:
        lines.append(f"### Observer candidates ({len(candidates)}) -- ROUTABLE")
        for cand in candidates:
            index += 1
            defer_count = defers.get((cand.item_id, "observer"), 0)
            badge = f" (deferred {defer_count}x)" if defer_count else ""
            lines.append(
                f"  [{index}] [{cand.severity}] {cand.item_id} · {cand.area} · {cand.rule}{badge}"
            )
        lines.append("")

    if reviews:
        lines.append(f"### Review queue ({len(reviews)}) -- ROUTABLE")
        for review in reviews:
            index += 1
            defer_count = defers.get((review.item_id, "review"), 0)
            badge = f" (deferred {defer_count}x)" if defer_count else ""
            lines.append(
                f"  [{index}] review · {review.filename}{badge}"
            )
        lines.append("")

    if researches:
        lines.append(
            f"### Research without delivery link ({len(researches)}) -- edit frontmatter or process with /lint"
        )
        for p, age in researches:
            lines.append(f"  - {p.name} (age {age} days)")
        lines.append("")

    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
