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
  K2B_LOOP_CONFLICTS_DIR    .staging/pending-conflicts/ (EOD capture)
  K2B_LOOP_CONFLICT_ARCHIVE_DIR .staging/conflicts.archive/ (EOD capture)
  K2B_VAULT_PATH            vault root for conflict canonical-home edits
  K2B_LOOP_DATE             today's date string
  K2B_LOOP_ACTOR            "keith" or similar
  K2B_LOOP_OBSERVER_RUN     observer timestamp
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from loop import loop_lib  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
WIKI_LOG_APPEND = REPO_ROOT / "scripts" / "wiki-log-append.sh"
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


def _sanitize_log_value(value: object) -> str:
    text = str(value)
    text = "".join(ch if ch >= " " and ch != "\x7f" else " " for ch in text)
    return " ".join(text.split())[:500]


def _safe_log_filename(value: object) -> str:
    safe = "".join(ch if ch.isalnum() or ch in "._-" else "-" for ch in str(value))
    safe = safe.strip("-._")
    return safe[:80] or "log"


def _append_wiki_log(vault_root: Path, action: str, summary: str) -> None:
    log_path = Path(os.environ.get("K2B_WIKI_LOG", str(vault_root / "wiki" / "log.md")))
    if not log_path.exists():
        warning = f"wiki log missing: {log_path}"
    else:
        env = os.environ.copy()
        env["K2B_WIKI_LOG"] = str(log_path)
        try:
            proc = subprocess.run(
                [
                    str(WIKI_LOG_APPEND),
                    "/eod-capture",
                    _sanitize_log_value(action),
                    _sanitize_log_value(summary),
                ],
                text=True,
                capture_output=True,
                env=env,
                check=False,
                timeout=10,
            )
            warning = proc.stderr.strip() if proc.returncode != 0 else ""
        except (OSError, subprocess.TimeoutExpired) as exc:
            warning = str(exc)
    if warning:
        print(f"loop-apply: warning: {warning}", file=sys.stderr)
        try:
            fallback_dir = vault_root / ".staging" / "eod-log-failures"
            fallback_dir.mkdir(parents=True, exist_ok=True)
            fallback = fallback_dir / f"{_safe_log_filename(action)}.jsonl"
            lock_path = fallback_dir / ".eod-log-failures.lock"
            line = (
                json.dumps(
                    {
                        "action": _sanitize_log_value(action),
                        "summary": _sanitize_log_value(summary),
                        "error": warning,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
                + "\n"
            )
            with loop_lib._file_lock(lock_path):
                with fallback.open("a", encoding="utf-8") as f:
                    f.write(line)
                    f.flush()
                    os.fsync(f.fileno())
                loop_lib._fsync_dir(fallback_dir)
        except OSError as exc:
            print(f"loop-apply: warning: fallback log failed: {exc}", file=sys.stderr)


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
    conflicts_dir_str = _env_optional("K2B_LOOP_CONFLICTS_DIR")
    conflict_archive_str = _env_optional("K2B_LOOP_CONFLICT_ARCHIVE_DIR")
    vault_root_str = _env_optional("K2B_VAULT_PATH")

    defers_path = Path(defers_path_str) if defers_path_str else None
    review_dir = Path(review_dir_str) if review_dir_str else None
    review_ready = Path(review_ready_str) if review_ready_str else None
    review_archive = Path(review_archive_str) if review_archive_str else None
    conflicts_dir = Path(conflicts_dir_str) if conflicts_dir_str else None
    conflict_archive = Path(conflict_archive_str) if conflict_archive_str else None

    items = loop_lib.parse_candidates(candidates_path)
    reviews = loop_lib.list_reviews(review_dir) if review_dir else []
    conflict_files_present = (
        conflicts_dir is not None
        and conflicts_dir.is_dir()
        and any(
            p.is_file() and not p.name.startswith(".tmp")
            for p in conflicts_dir.glob("*.json")
        )
    )
    if conflict_files_present and not vault_root_str:
        sys.stderr.write("loop-apply: K2B_VAULT_PATH required when conflicts are present\n")
        sys.exit(2)
    vault_root = Path(vault_root_str) if vault_root_str else None
    if conflict_files_present and vault_root is not None and not vault_root.is_dir():
        sys.stderr.write(f"loop-apply: vault root not found: {vault_root}\n")
        sys.exit(2)
    if conflict_files_present and vault_root is not None and not (
        (vault_root / "wiki").is_dir() and (vault_root / "System" / "memory").is_dir()
    ):
        sys.stderr.write(f"loop-apply: K2B_VAULT_PATH is not a K2B vault: {vault_root}\n")
        sys.exit(2)
    if conflict_files_present:
        if vault_root is None:
            sys.stderr.write("loop-apply: K2B_VAULT_PATH required when conflicts are present\n")
            sys.exit(2)
        actual_conflicts_dir = conflicts_dir.resolve(strict=False)  # type: ignore[union-attr]
        expected_conflicts_dir = (
            vault_root / ".staging" / "pending-conflicts"
        ).resolve(strict=False)
        if actual_conflicts_dir != expected_conflicts_dir:
            sys.stderr.write(
                "loop-apply: K2B_LOOP_CONFLICTS_DIR must be "
                f"{expected_conflicts_dir}, got {actual_conflicts_dir}\n"
            )
            sys.exit(2)
    conflicts = loop_lib.list_conflicts(conflicts_dir) if conflicts_dir else []

    if not items and not reviews and not conflicts:
        print("loop-apply: no candidates, reviews, or conflicts to route", file=sys.stderr)
        return 0

    total = len(items) + len(reviews) + len(conflicts)

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

    try:
        resolved_accepts: list[tuple[str, object]] = [
            loop_lib.resolve_index(i, items, reviews, conflicts) for i in accept_idx
        ]
        resolved_rejects: list[tuple[str, object]] = [
            loop_lib.resolve_index(i, items, reviews, conflicts) for i in reject_idx
        ]
        resolved_defers: list[tuple[str, object]] = [
            loop_lib.resolve_index(i, items, reviews, conflicts) for i in defer_idx
        ]
    except IndexError as exc:
        sys.stderr.write(f"loop-apply: {exc}\n")
        sys.exit(2)

    # --- Accept ---------------------------------------------------------
    observer_accept_ids: set[str] = set()
    review_accept_ids: set[str] = set()
    conflict_accept_ids: set[str] = set()
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
        elif kind == "conflict":
            conflict = obj  # type: ignore[assignment]
            if vault_root is None:
                sys.stderr.write("loop-apply: K2B_VAULT_PATH required to accept conflicts\n")
                sys.exit(2)
            changed_path = loop_lib.accept_conflict(conflict, vault_root=vault_root)
            _append_wiki_log(
                vault_root,
                "conflict-accepted",
                f"{conflict.subject} {conflict.predicate}: {conflict.existing_value} -> {conflict.new_value}",
            )
            print(f"accepted conflict {conflict.conflict_id} -> {changed_path}")
            conflict_accept_ids.add(conflict.item_id)

    # --- Reject ---------------------------------------------------------
    observer_reject_ids: set[str] = set()
    review_reject_ids: set[str] = set()
    conflict_reject_ids: set[str] = set()
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
        elif kind == "conflict":
            conflict = obj  # type: ignore[assignment]
            if vault_root is None:
                sys.stderr.write("loop-apply: K2B_VAULT_PATH required to reject conflicts\n")
                sys.exit(2)
            if conflict_archive is None:
                sys.stderr.write(
                    "loop-apply: K2B_LOOP_CONFLICT_ARCHIVE_DIR required to reject conflicts\n"
                )
                sys.exit(2)
            loop_lib.reject_conflict_to_archive(
                conflict,
                archive_dir=conflict_archive,
                date_str=date_str,
                actor=actor,
            )
            _append_wiki_log(
                vault_root,
                "conflict-rejected",
                f"{conflict.subject} {conflict.predicate}: kept {conflict.existing_value}, rejected {conflict.new_value}",
            )
            print(f"rejected conflict {conflict.conflict_id}")
            conflict_reject_ids.add(conflict.item_id)

    # --- Defer (increment counter, auto-archive on 3) ------------------
    auto_archived_observer_ids: set[str] = set()
    conflict_defer_ids: set[str] = set()
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
        elif kind == "conflict":
            conflict = obj  # type: ignore[assignment]
            if vault_root is None:
                sys.stderr.write("loop-apply: K2B_VAULT_PATH required to defer conflicts\n")
                sys.exit(2)
            if conflict_archive is None:
                sys.stderr.write(
                    "loop-apply: K2B_LOOP_CONFLICT_ARCHIVE_DIR required to defer conflicts\n"
                )
                sys.exit(2)
            new_count, archived_path = loop_lib.defer_conflict(
                conflict,
                archive_dir=conflict_archive,
                date_str=date_str,
                threshold=_DEFER_ARCHIVE_THRESHOLD,
            )
            print(f"deferred conflict {conflict.conflict_id} -> {new_count}x")
            _append_wiki_log(
                vault_root,
                "conflict-deferred",
                f"{conflict.subject} {conflict.predicate}: defer {new_count}x",
            )
            conflict_defer_ids.add(conflict.item_id)
            if archived_path is not None:
                _append_wiki_log(
                    vault_root,
                    "conflict-auto-archived",
                    f"{conflict.subject} {conflict.predicate}: deferred {new_count}x",
                )
                print(
                    f"auto-archived conflict {conflict.conflict_id} (deferred {new_count}x)"
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
        # Conflicts track live defers in surfaced_count, but clearing any legacy
        # shared-counter entry prevents stale state if an older loop wrote one.
        for conflict_id in conflict_accept_ids | conflict_reject_ids | conflict_defer_ids:
            loop_lib.reset_defers(defers_path, item_id=conflict_id, kind="conflict")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
