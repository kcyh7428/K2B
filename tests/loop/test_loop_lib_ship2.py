"""Ship 2 unit tests for scripts/loop/loop_lib.py.

Ship 2 adds: defer counter, auto-archive at 3, review-item routing (accept,
reject, defer), unified numbering across observer + review surfaces.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from loop import loop_lib  # noqa: E402


FIXTURE_DIR = ROOT / "tests" / "fixtures" / "loop-mvp-ship2"


# --- Defer counter primitives ---


def test_read_defers_missing_file_returns_empty(tmp_path):
    assert loop_lib.read_defers(tmp_path / "missing.jsonl") == {}


def test_read_defers_empty_file_returns_empty(tmp_path):
    p = tmp_path / "defers.jsonl"
    p.write_text("", encoding="utf-8")
    assert loop_lib.read_defers(p) == {}


def test_increment_defer_first_time_returns_one(tmp_path):
    p = tmp_path / "defers.jsonl"
    count = loop_lib.increment_defer(p, item_id="abc12345", kind="observer", date_str="2026-04-24")
    assert count == 1


def test_increment_defer_second_time_returns_two(tmp_path):
    p = tmp_path / "defers.jsonl"
    loop_lib.increment_defer(p, item_id="abc12345", kind="observer", date_str="2026-04-24")
    count2 = loop_lib.increment_defer(p, item_id="abc12345", kind="observer", date_str="2026-04-24")
    assert count2 == 2


def test_read_defers_returns_counts_after_increments(tmp_path):
    p = tmp_path / "defers.jsonl"
    loop_lib.increment_defer(p, item_id="abc12345", kind="observer", date_str="2026-04-24")
    loop_lib.increment_defer(p, item_id="abc12345", kind="observer", date_str="2026-04-24")
    loop_lib.increment_defer(p, item_id="def67890", kind="observer", date_str="2026-04-24")
    defers = loop_lib.read_defers(p)
    assert defers.get(("abc12345", "observer")) == 2
    assert defers.get(("def67890", "observer")) == 1


def test_increment_defer_different_kinds_tracked_separately(tmp_path):
    p = tmp_path / "defers.jsonl"
    c1 = loop_lib.increment_defer(p, item_id="same_id", kind="observer", date_str="2026-04-24")
    c2 = loop_lib.increment_defer(p, item_id="same_id", kind="review", date_str="2026-04-24")
    assert c1 == 1
    assert c2 == 1
    defers = loop_lib.read_defers(p)
    assert defers.get(("same_id", "observer")) == 1
    assert defers.get(("same_id", "review")) == 1


def test_reset_defers_removes_matching_entry(tmp_path):
    p = tmp_path / "defers.jsonl"
    loop_lib.increment_defer(p, item_id="abc12345", kind="observer", date_str="2026-04-24")
    loop_lib.increment_defer(p, item_id="abc12345", kind="observer", date_str="2026-04-24")
    loop_lib.increment_defer(p, item_id="other", kind="observer", date_str="2026-04-24")
    loop_lib.reset_defers(p, item_id="abc12345", kind="observer")
    defers = loop_lib.read_defers(p)
    assert ("abc12345", "observer") not in defers
    assert defers.get(("other", "observer")) == 1


def test_reset_defers_preserves_malformed_lines(tmp_path):
    """Codex LOW-5: malformed lines must survive reset_defers rewrite."""
    p = tmp_path / "defers.jsonl"
    p.write_text(
        '{"item_id": "abc", "kind": "observer", "deferred_at": "2026-04-24"}\n'
        "not json here\n"
        '{"item_id": "abc", "kind": "observer", "deferred_at": "2026-04-24"}\n'
        '{"item_id": "xyz", "kind": "observer", "deferred_at": "2026-04-24"}\n',
        encoding="utf-8",
    )
    loop_lib.reset_defers(p, item_id="abc", kind="observer")
    text = p.read_text(encoding="utf-8")
    assert "not json here" in text
    assert '"item_id": "abc"' not in text
    assert '"item_id": "xyz"' in text


def test_read_defers_ignores_malformed_lines(tmp_path):
    """A malformed line must not blow up the whole file -- skip it and keep counting."""
    p = tmp_path / "defers.jsonl"
    p.write_text(
        '{"item_id": "abc", "kind": "observer", "deferred_at": "2026-04-24"}\n'
        "not json here\n"
        '{"item_id": "abc", "kind": "observer", "deferred_at": "2026-04-24"}\n',
        encoding="utf-8",
    )
    defers = loop_lib.read_defers(p)
    assert defers.get(("abc", "observer")) == 2


# --- Auto-archive observer candidate at 3 defers ---


def test_archive_observer_auto_deferred_writes_jsonl(tmp_path):
    archive_dir = tmp_path / "observations.archive"
    archive_dir.mkdir()
    items = loop_lib.parse_candidates(FIXTURE_DIR / "observer-candidates.md")
    cand = items[0]
    loop_lib.archive_observer_auto_deferred(
        archive_dir, cand, date_str="2026-04-24", defer_count=3
    )
    target = archive_dir / "auto-archived-deferred-2026-04-24.jsonl"
    assert target.exists()
    line = target.read_text(encoding="utf-8").strip()
    record = json.loads(line)
    assert record["item_id"] == cand.item_id
    assert record["severity"] == cand.severity
    assert record["area"] == cand.area
    assert record["rule"] == cand.rule
    assert record["defer_count"] == 3
    assert record["auto_archived"] == "2026-04-24"


def test_archive_observer_auto_deferred_appends_multiple(tmp_path):
    archive_dir = tmp_path / "observations.archive"
    archive_dir.mkdir()
    items = loop_lib.parse_candidates(FIXTURE_DIR / "observer-candidates.md")
    loop_lib.archive_observer_auto_deferred(
        archive_dir, items[0], date_str="2026-04-24", defer_count=3
    )
    loop_lib.archive_observer_auto_deferred(
        archive_dir, items[1], date_str="2026-04-24", defer_count=3
    )
    target = archive_dir / "auto-archived-deferred-2026-04-24.jsonl"
    lines = target.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2


# --- Review item surface ---


def test_list_reviews_sorted_excluding_index(tmp_path):
    (tmp_path / "index.md").write_text("---\nname: index\n---\n", encoding="utf-8")
    (tmp_path / "beta.md").write_text("---\nreview-action: pending\n---\n", encoding="utf-8")
    (tmp_path / "alpha.md").write_text("---\nreview-action: pending\n---\n", encoding="utf-8")
    reviews = loop_lib.list_reviews(tmp_path)
    names = [r.filename for r in reviews]
    assert names == ["alpha.md", "beta.md"]
    assert all(r.item_id and len(r.item_id) == 8 for r in reviews)


def test_list_reviews_item_id_is_stable(tmp_path):
    (tmp_path / "sample.md").write_text("---\nreview-action: pending\n---\n", encoding="utf-8")
    r1 = loop_lib.list_reviews(tmp_path)
    r2 = loop_lib.list_reviews(tmp_path)
    assert r1[0].item_id == r2[0].item_id


def test_accept_review_moves_to_ready_and_flips_action(tmp_path):
    src_dir = tmp_path / "review"
    src_dir.mkdir()
    ready_dir = src_dir / "Ready"
    original = FIXTURE_DIR / "review" / "content_ship2-sample.md"
    src = src_dir / original.name
    src.write_text(original.read_text(encoding="utf-8"), encoding="utf-8")

    reviews = loop_lib.list_reviews(src_dir)
    assert len(reviews) == 1
    new_path = loop_lib.accept_review(reviews[0], date_str="2026-04-24", ready_dir=ready_dir)

    assert new_path.exists()
    assert not src.exists()
    assert new_path.parent == ready_dir
    text = new_path.read_text(encoding="utf-8")
    assert "review-action: accepted" in text
    assert "review-action: pending" not in text


def test_reject_review_moves_to_archive_and_flips_action(tmp_path):
    src_dir = tmp_path / "review"
    src_dir.mkdir()
    archive_root = tmp_path / "Archive" / "review-archive"
    original = FIXTURE_DIR / "review" / "crosslinks_ship2-sample.md"
    src = src_dir / original.name
    src.write_text(original.read_text(encoding="utf-8"), encoding="utf-8")

    reviews = loop_lib.list_reviews(src_dir)
    new_path = loop_lib.reject_review(
        reviews[0], date_str="2026-04-24", archive_root=archive_root
    )

    assert new_path.exists()
    assert not src.exists()
    assert new_path.parent == archive_root / "2026-04-24"
    text = new_path.read_text(encoding="utf-8")
    assert "review-action: rejected" in text
    assert "review-action: pending" not in text


def test_accept_review_idempotent_action_already_set(tmp_path):
    """Accepting an item that already has review-action: accepted keeps the field once."""
    src_dir = tmp_path / "review"
    src_dir.mkdir()
    ready_dir = src_dir / "Ready"
    src = src_dir / "already.md"
    src.write_text(
        "---\nreview-action: accepted\nreview-notes: \"\"\n---\n\n# already accepted\n",
        encoding="utf-8",
    )
    reviews = loop_lib.list_reviews(src_dir)
    new_path = loop_lib.accept_review(reviews[0], date_str="2026-04-24", ready_dir=ready_dir)
    text = new_path.read_text(encoding="utf-8")
    assert text.count("review-action:") == 1
    assert "review-action: accepted" in text


# --- Review auto-archive on 3 defers ---


def test_archive_review_auto_deferred_moves_file(tmp_path):
    src_dir = tmp_path / "review"
    src_dir.mkdir()
    archive_root = tmp_path / "Archive" / "review-archive"
    src = src_dir / "stale.md"
    src.write_text(
        "---\nreview-action: pending\n---\n\n# stale review\n", encoding="utf-8"
    )
    reviews = loop_lib.list_reviews(src_dir)
    new_path = loop_lib.archive_review_auto_deferred(
        reviews[0], date_str="2026-04-24", archive_root=archive_root, defer_count=3
    )
    assert new_path.exists()
    assert not src.exists()
    assert new_path.parent == archive_root / "2026-04-24"
    text = new_path.read_text(encoding="utf-8")
    assert "review-action: auto-archived-deferred" in text


# --- Unified numbering / index resolution (used by loop_apply.py) ---


def test_resolve_index_observer_range(tmp_path):
    items = loop_lib.parse_candidates(FIXTURE_DIR / "observer-candidates.md")
    reviews: list = []
    kind, obj = loop_lib.resolve_index(1, items, reviews)
    assert kind == "observer"
    assert obj.item_id == items[0].item_id


def test_resolve_index_review_range(tmp_path):
    items = loop_lib.parse_candidates(FIXTURE_DIR / "observer-candidates.md")
    src_dir = tmp_path / "review"
    src_dir.mkdir()
    (src_dir / "alpha.md").write_text(
        "---\nreview-action: pending\n---\n", encoding="utf-8"
    )
    reviews = loop_lib.list_reviews(src_dir)
    # Observer has 3, review is index 4
    kind, obj = loop_lib.resolve_index(4, items, reviews)
    assert kind == "review"
    assert obj.filename == "alpha.md"


def test_resolve_index_out_of_range_raises(tmp_path):
    items = loop_lib.parse_candidates(FIXTURE_DIR / "observer-candidates.md")
    reviews: list = []
    with pytest.raises(IndexError):
        loop_lib.resolve_index(999, items, reviews)
    with pytest.raises(IndexError):
        loop_lib.resolve_index(0, items, reviews)
