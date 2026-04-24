"""CLI entry point for loop routing. Invoked by scripts/loop/loop-apply.sh.

Reads --accept/--reject/--defer N actions from argv. Indices map into a
unified space: observer candidates 1..O, review items O+1..O+R. Ship 2
wires review items + defer counter + auto-archive at 3 defers.

Env vars:
  K2B_LOOP_CANDIDATES       observer-candidates.md
  K2B_LOOP_LEARNINGS        self_improve_learnings.md
  K2B_LOOP_ARCHIVE_DIR      observations.archive/
  K2B_LOOP_DEFERS           observer-defers.jsonl (Ship 2)
  K2B_LOOP_REVIEW_DIR       review/ (Ship 2)
  K2B_LOOP_REVIEW_READY_DIR review/Ready/ (Ship 2)
  K2B_LOOP_REVIEW_ARCHIVE_ROOT  Archive/review-archive/ (Ship 2)
  K2B_LOOP_DATE             today's date string
  K2B_LOOP_ACTOR            "keith" or similar
  K2B_LOOP_OBSERVER_RUN     observer timestamp
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from loop import loop_lib  # noqa: E402

_DEFER_ARCHIVE_THRESHOLD = 3


def _read_review_type(path: Path) -> str:
    """Return the `type:` frontmatter value (lowercase) for a review file.

    Empty string if the file has no frontmatter or no type field. Used by
    accept_review to surface follow-up hints for items whose loop-grammar
    routing is transport-only (crosslink-digest, content-idea, etc.).
    """
    try:
        head = path.read_text(encoding="utf-8").splitlines()[:40]
    except OSError:
        return ""
    in_fm = False
    for i, line in enumerate(head):
        if i == 0 and line.strip() == "---":
            in_fm = True
            continue
        if in_fm and line.strip() == "---":
            break
        if in_fm and line.startswith("type:"):
            return line.split(":", 1)[1].strip().lower()
    return ""


def _env(name: str) -> str:
    v = os.environ.get(name)
    if not v:
        sys.stderr.write(f"loop-apply: missing env var {name}\n")
        sys.exit(2)
    return v


def _env_optional(name: str) -> str:
    return os.environ.get(name, "")


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

    defers_path_str = _env_optional("K2B_LOOP_DEFERS")
    review_dir_str = _env_optional("K2B_LOOP_REVIEW_DIR")
    review_ready_str = _env_optional("K2B_LOOP_REVIEW_READY_DIR")
    review_archive_str = _env_optional("K2B_LOOP_REVIEW_ARCHIVE_ROOT")

    defers_path = Path(defers_path_str) if defers_path_str else None
    review_dir = Path(review_dir_str) if review_dir_str else None
    review_ready = Path(review_ready_str) if review_ready_str else None
    review_archive = Path(review_archive_str) if review_archive_str else None

    items = loop_lib.parse_candidates(candidates_path)
    reviews = loop_lib.list_reviews(review_dir) if review_dir else []

    if not items and not reviews:
        print("loop-apply: no candidates or reviews to route", file=sys.stderr)
        return 0

    total = len(items) + len(reviews)

    # Dedupe within each action and reject cross-action conflicts -- routing
    # the same index twice would produce duplicate writes because the remove
    # step runs once at the end.
    accept_idx = list(dict.fromkeys(args.accept))
    reject_idx = list(dict.fromkeys(args.reject))
    defer_idx = list(dict.fromkeys(args.defer))

    seen: dict[int, str] = {}
    for idx, action in (
        [(i, "accept") for i in accept_idx]
        + [(i, "reject") for i in reject_idx]
        + [(i, "defer") for i in defer_idx]
    ):
        if idx < 1 or idx > total:
            sys.stderr.write(
                f"loop-apply: index {idx} out of range 1..{total}\n"
            )
            sys.exit(2)
        prior = seen.get(idx)
        if prior and prior != action:
            sys.stderr.write(
                f"loop-apply: index {idx} cannot be both {prior} and {action}\n"
            )
            sys.exit(2)
        seen[idx] = action

    resolved_accepts: list[tuple[str, object]] = [
        loop_lib.resolve_index(i, items, reviews) for i in accept_idx
    ]
    resolved_rejects: list[tuple[str, object]] = [
        loop_lib.resolve_index(i, items, reviews) for i in reject_idx
    ]
    resolved_defers: list[tuple[str, object]] = [
        loop_lib.resolve_index(i, items, reviews) for i in defer_idx
    ]

    # --- Accept ---------------------------------------------------------
    observer_accept_ids: set[str] = set()
    review_accept_ids: set[str] = set()
    for kind, obj in resolved_accepts:
        if kind == "observer":
            cand = obj  # type: ignore[assignment]
            lid = loop_lib.append_learning(
                learnings_path, cand, date_str=date_str, observer_run=observer_run
            )
            print(f"accepted observer {cand.item_id} -> {lid}")
            observer_accept_ids.add(cand.item_id)
        elif kind == "review":
            review = obj  # type: ignore[assignment]
            if review_ready is None:
                sys.stderr.write(
                    "loop-apply: K2B_LOOP_REVIEW_READY_DIR required to accept review items\n"
                )
                sys.exit(2)
            # Peek at frontmatter `type:` before moving so crosslink-digest
            # items don't silently skip the weave apply step (Codex HIGH-2).
            # Loop grammar is transport-only in Ship 2; semantic application
            # stays with the specialized follow-up skill.
            kind_tag = _read_review_type(review.path)
            new_path = loop_lib.accept_review(
                review, date_str=date_str, ready_dir=review_ready
            )
            print(f"accepted review {review.filename} -> {new_path}")
            if kind_tag == "crosslink-digest":
                print(
                    "  follow-up: run scripts/k2b-weave.sh apply "
                    f"{new_path} to apply the per-pair Decisions"
                )
            review_accept_ids.add(review.item_id)

    # --- Reject ---------------------------------------------------------
    observer_reject_ids: set[str] = set()
    review_reject_ids: set[str] = set()
    for kind, obj in resolved_rejects:
        if kind == "observer":
            cand = obj  # type: ignore[assignment]
            loop_lib.archive_reject(
                archive_dir, cand, date_str=date_str, actor=actor
            )
            print(f"rejected observer {cand.item_id}")
            observer_reject_ids.add(cand.item_id)
        elif kind == "review":
            review = obj  # type: ignore[assignment]
            if review_archive is None:
                sys.stderr.write(
                    "loop-apply: K2B_LOOP_REVIEW_ARCHIVE_ROOT required to reject review items\n"
                )
                sys.exit(2)
            new_path = loop_lib.reject_review(
                review, date_str=date_str, archive_root=review_archive
            )
            print(f"rejected review {review.filename} -> {new_path}")
            review_reject_ids.add(review.item_id)

    # --- Defer (increment counter, auto-archive on 3) ------------------
    auto_archived_observer_ids: set[str] = set()
    for kind, obj in resolved_defers:
        if defers_path is None:
            sys.stderr.write(
                "loop-apply: K2B_LOOP_DEFERS required to defer items\n"
            )
            sys.exit(2)
        if kind == "observer":
            cand = obj  # type: ignore[assignment]
            new_count = loop_lib.increment_defer(
                defers_path,
                item_id=cand.item_id,
                kind="observer",
                date_str=date_str,
            )
            print(f"deferred observer {cand.item_id} -> {new_count}x")
            if new_count >= _DEFER_ARCHIVE_THRESHOLD:
                loop_lib.archive_observer_auto_deferred(
                    archive_dir,
                    cand,
                    date_str=date_str,
                    defer_count=new_count,
                )
                loop_lib.reset_defers(
                    defers_path, item_id=cand.item_id, kind="observer"
                )
                auto_archived_observer_ids.add(cand.item_id)
                print(
                    f"auto-archived observer {cand.item_id} (deferred {new_count}x)"
                )
        elif kind == "review":
            review = obj  # type: ignore[assignment]
            new_count = loop_lib.increment_defer(
                defers_path,
                item_id=review.item_id,
                kind="review",
                date_str=date_str,
            )
            print(f"deferred review {review.filename} -> {new_count}x")
            if new_count >= _DEFER_ARCHIVE_THRESHOLD:
                if review_archive is None:
                    sys.stderr.write(
                        "loop-apply: K2B_LOOP_REVIEW_ARCHIVE_ROOT required for review auto-archive\n"
                    )
                    sys.exit(2)
                loop_lib.archive_review_auto_deferred(
                    review,
                    date_str=date_str,
                    archive_root=review_archive,
                    defer_count=new_count,
                )
                loop_lib.reset_defers(
                    defers_path, item_id=review.item_id, kind="review"
                )
                print(
                    f"auto-archived review {review.filename} (deferred {new_count}x)"
                )

    # Remove consumed observer candidates from the source file in one rewrite.
    remove_ids = observer_accept_ids | observer_reject_ids | auto_archived_observer_ids
    if remove_ids:
        loop_lib.rewrite_candidates_without(candidates_path, remove_ids)

    # Clear defer counters for consumed observer + review items (Codex MEDIUM-4).
    # Orphaned entries would count against a regenerated identical payload.
    if defers_path is not None:
        for cand_id in observer_accept_ids | observer_reject_ids:
            loop_lib.reset_defers(defers_path, item_id=cand_id, kind="observer")
        for rev_id in review_accept_ids | review_reject_ids:
            loop_lib.reset_defers(defers_path, item_id=rev_id, kind="review")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
