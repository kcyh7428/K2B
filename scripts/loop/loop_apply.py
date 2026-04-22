"""CLI entry point for loop routing. Invoked by scripts/loop/loop-apply.sh.

Reads actions from argv, mutates:
  - $K2B_LOOP_LEARNINGS     (append L-IDs on accept)
  - $K2B_LOOP_ARCHIVE_DIR/rejected-YYYY-MM-DD.jsonl (append on reject)
  - $K2B_LOOP_CANDIDATES    (remove both accepted and rejected)

--defer is a no-op at the routing level in Ship 1 (documented in the feature
spec Updates); the item stays in observer-candidates.md and re-surfaces next
session.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from loop import loop_lib  # noqa: E402


def _env(name: str) -> str:
    v = os.environ.get(name)
    if not v:
        sys.stderr.write(f"loop-apply: missing env var {name}\n")
        sys.exit(2)
    return v


def main() -> int:
    parser = argparse.ArgumentParser(description="K2B loop routing.")
    parser.add_argument("--accept", type=int, action="append", default=[])
    parser.add_argument("--reject", type=int, action="append", default=[])
    parser.add_argument("--defer", type=int, action="append", default=[])
    args = parser.parse_args()

    candidates_path = Path(_env("K2B_LOOP_CANDIDATES"))
    learnings_path = Path(_env("K2B_LOOP_LEARNINGS"))
    archive_dir = Path(_env("K2B_LOOP_ARCHIVE_DIR"))
    date_str = _env("K2B_LOOP_DATE")
    actor = _env("K2B_LOOP_ACTOR")
    observer_run = _env("K2B_LOOP_OBSERVER_RUN")

    items = loop_lib.parse_candidates(candidates_path)
    if not items:
        print("loop-apply: no candidates to route", file=sys.stderr)
        return 0

    total = len(items)

    def pick(idx: int) -> loop_lib.Candidate:
        if idx < 1 or idx > total:
            sys.stderr.write(f"loop-apply: index {idx} out of range 1..{total}\n")
            sys.exit(2)
        return items[idx - 1]

    # Dedupe within each action and reject cross-action conflicts -- routing
    # the same candidate twice would produce duplicate learnings / duplicate
    # archive lines because the remove step runs once at the end.
    accept_idx = list(dict.fromkeys(args.accept))  # preserves first-seen order
    reject_idx = list(dict.fromkeys(args.reject))
    defer_idx = list(dict.fromkeys(args.defer))

    seen: dict[int, str] = {}
    for idx, action in (
        [(i, "accept") for i in accept_idx]
        + [(i, "reject") for i in reject_idx]
        + [(i, "defer") for i in defer_idx]
    ):
        prior = seen.get(idx)
        if prior and prior != action:
            sys.stderr.write(
                f"loop-apply: index {idx} cannot be both {prior} and {action}\n"
            )
            sys.exit(2)
        seen[idx] = action

    accepted = [pick(i) for i in accept_idx]
    rejected = [pick(i) for i in reject_idx]

    for cand in accepted:
        lid = loop_lib.append_learning(
            learnings_path, cand, date_str=date_str, observer_run=observer_run
        )
        print(f"accepted {cand.item_id} -> {lid}")

    for cand in rejected:
        loop_lib.archive_reject(
            archive_dir, cand, date_str=date_str, actor=actor
        )
        print(f"rejected {cand.item_id}")

    remove_ids = {c.item_id for c in accepted} | {c.item_id for c in rejected}
    if remove_ids:
        loop_lib.rewrite_candidates_without(candidates_path, remove_ids)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
