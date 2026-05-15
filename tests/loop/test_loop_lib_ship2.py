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


def test_increment_defer_fsyncs_parent_dir(tmp_path, monkeypatch):
    p = tmp_path / "defers.jsonl"
    synced: list[Path] = []
    monkeypatch.setattr(loop_lib, "_fsync_dir", lambda path: synced.append(path))

    loop_lib.increment_defer(p, item_id="abc12345", kind="observer", date_str="2026-04-24")

    assert synced == [tmp_path]


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


def test_list_conflicts_sorted_and_stable_ids(tmp_path):
    conflict_dir = tmp_path / "pending-conflicts"
    conflict_dir.mkdir()
    (conflict_dir / "2026-05-14_b.json").write_text(
        json.dumps(
            {
                "conflict_id": "b",
                "subject": "Bravo",
                "predicate": "phone",
                "existing_value": "1111",
                "new_value": "2222",
                "source_session_path": "/tmp/b.jsonl",
            }
        ),
        encoding="utf-8",
    )
    (conflict_dir / "2026-05-14_a.json").write_text(
        json.dumps(
            {
                "conflict_id": "a",
                "subject": "Alpha",
                "predicate": "phone",
                "existing_value": "3333",
                "new_value": "4444",
                "source_session_path": "/tmp/a.jsonl",
            }
        ),
        encoding="utf-8",
    )

    conflicts = loop_lib.list_conflicts(conflict_dir)

    assert [c.conflict_id for c in conflicts] == ["a", "b"]
    assert all(c.item_id and len(c.item_id) == 8 for c in conflicts)


def test_resolve_index_conflict_range_after_observer_and_review(tmp_path):
    items = loop_lib.parse_candidates(FIXTURE_DIR / "observer-candidates.md")
    src_dir = tmp_path / "review"
    src_dir.mkdir()
    (src_dir / "alpha.md").write_text(
        "---\nreview-action: pending\n---\n", encoding="utf-8"
    )
    conflict_dir = tmp_path / "pending-conflicts"
    conflict_dir.mkdir()
    (conflict_dir / "2026-05-14_c.json").write_text(
        json.dumps(
            {
                "conflict_id": "c",
                "subject": "Dr. Lo Hak Keung",
                "predicate": "phone",
                "existing_value": "2840 3709",
                "new_value": "2830 3709",
                "source_session_path": "/tmp/s1.jsonl",
            }
        ),
        encoding="utf-8",
    )
    reviews = loop_lib.list_reviews(src_dir)
    conflicts = loop_lib.list_conflicts(conflict_dir)

    kind, obj = loop_lib.resolve_index(5, items, reviews, conflicts)

    assert kind == "conflict"
    assert obj.conflict_id == "c"


def test_defer_conflict_increments_then_auto_archives(tmp_path):
    conflict_dir = tmp_path / "pending-conflicts"
    archive_dir = tmp_path / "conflicts.archive"
    conflict_dir.mkdir()
    path = conflict_dir / "2026-05-14_c.json"
    path.write_text(
        json.dumps(
            {
                "conflict_id": "c",
                "subject": "Dr. Lo Hak Keung",
                "predicate": "phone",
                "existing_value": "2840 3709",
                "new_value": "2830 3709",
                "source_session_path": "/tmp/s1.jsonl",
                "surfaced_count": 2,
            }
        ),
        encoding="utf-8",
    )
    conflict = loop_lib.list_conflicts(conflict_dir)[0]

    new_count, archived_path = loop_lib.defer_conflict(
        conflict, archive_dir=archive_dir, date_str="2026-05-14", threshold=3
    )

    assert new_count == 3
    assert archived_path is not None
    assert archived_path.exists()
    assert not path.exists()
    record = json.loads(archived_path.read_text(encoding="utf-8"))
    assert record["archive_reason"] == "deferred_threshold"
    assert record["auto_archived_at"] == "2026-05-14"


def test_defer_conflict_archives_existing_threshold_count_without_increment(tmp_path):
    conflict_dir = tmp_path / "pending-conflicts"
    archive_dir = tmp_path / "conflicts.archive"
    conflict_dir.mkdir()
    path = conflict_dir / "2026-05-14_c.json"
    path.write_text(
        json.dumps(
            {
                "conflict_id": "c",
                "subject": "Dr. Lo Hak Keung",
                "predicate": "phone",
                "existing_value": "2840 3709",
                "new_value": "2830 3709",
                "source_session_path": "/tmp/s1.jsonl",
                "surfaced_count": 3,
            }
        ),
        encoding="utf-8",
    )
    conflict = loop_lib.list_conflicts(conflict_dir)[0]

    new_count, archived_path = loop_lib.defer_conflict(
        conflict, archive_dir=archive_dir, date_str="2026-05-14", threshold=3
    )

    assert new_count == 3
    assert archived_path is not None
    assert not path.exists()
    record = json.loads(archived_path.read_text(encoding="utf-8"))
    assert record["surfaced_count"] == 3
    assert record["archive_reason"] == "deferred_threshold"


def test_accept_conflict_replaces_existing_value_and_deletes_conflict(tmp_path):
    vault = tmp_path / "vault"
    semantic = vault / "wiki" / "context" / "shelves" / "semantic.md"
    semantic.parent.mkdir(parents=True)
    row = "- 2026-05-14 | contact | dr-lo-hak-keung | name:Dr. Lo Hak Keung | note:old number 2840 3709 | phone:2840 3709 | dedupe_key:person:dr-lo-hak-keung:phone"
    semantic.write_text("## Rows\n" + row + "\n", encoding="utf-8")
    conflict_dir = vault / ".staging" / "pending-conflicts"
    conflict_dir.mkdir(parents=True)
    path = conflict_dir / "2026-05-14_c.json"
    path.write_text(
        json.dumps(
            {
                "conflict_id": "c",
                "subject": "Dr. Lo Hak Keung",
                "predicate": "phone",
                "existing_value": "2840 3709",
                "existing_source": "wiki/context/shelves/semantic.md:2",
                "new_value": "2830 3709",
                "dedupe_key": "person:dr-lo-hak-keung:phone",
                "source_session_path": "/tmp/s1.jsonl",
            }
        ),
        encoding="utf-8",
    )
    conflict = loop_lib.list_conflicts(conflict_dir)[0]

    changed = loop_lib.accept_conflict(conflict, vault_root=vault)

    assert changed == semantic
    text = semantic.read_text(encoding="utf-8")
    assert "phone:2830 3709" in text
    assert "phone:2840 3709" not in text
    assert "note:old number 2840 3709" in text
    assert not path.exists()


def test_accept_conflict_rejects_post_write_file_corruption(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    semantic = vault / "wiki" / "context" / "shelves" / "semantic.md"
    semantic.parent.mkdir(parents=True)
    semantic.write_text(
        "## Rows\n"
        "- 2026-05-14 | contact | other | name:Other | phone:1111 1111 | dedupe_key:person:other:phone\n"
        "- 2026-05-14 | contact | dr-lo-hak-keung | name:Dr. Lo Hak Keung | phone:2840 3709 | dedupe_key:person:dr-lo-hak-keung:phone\n",
        encoding="utf-8",
    )
    conflict_dir = vault / ".staging" / "pending-conflicts"
    conflict_dir.mkdir(parents=True)
    path = conflict_dir / "2026-05-14_c.json"
    path.write_text(
        json.dumps(
            {
                "conflict_id": "c",
                "subject": "Dr. Lo Hak Keung",
                "predicate": "phone",
                "existing_value": "2840 3709",
                "existing_source": "wiki/context/shelves/semantic.md:3",
                "new_value": "2830 3709",
                "dedupe_key": "person:dr-lo-hak-keung:phone",
                "source_session_path": "/tmp/s1.jsonl",
            }
        ),
        encoding="utf-8",
    )
    real_atomic_write = loop_lib._atomic_write

    def corrupt_atomic_write(target, payload):
        real_atomic_write(target, payload.replace("name:Other", "name:Corrupt"))

    monkeypatch.setattr(loop_lib, "_atomic_write", corrupt_atomic_write)
    conflict = loop_lib.list_conflicts(conflict_dir)[0]

    with pytest.raises(RuntimeError, match="target read-back failed"):
        loop_lib.accept_conflict(conflict, vault_root=vault)

    assert path.exists()


def test_accept_conflict_allows_unrelated_row_edit_when_value_still_matches(tmp_path):
    vault = tmp_path / "vault"
    semantic = vault / "wiki" / "context" / "shelves" / "semantic.md"
    semantic.parent.mkdir(parents=True)
    changed_row = "- 2026-05-14 | contact | dr-lo-hak-keung | name:Dr. Lo Hak Keung | note:edited after conflict | phone:2840 3709 | dedupe_key:person:dr-lo-hak-keung:phone"
    semantic.write_text("## Rows\n" + changed_row + "\n", encoding="utf-8")
    conflict_dir = vault / ".staging" / "pending-conflicts"
    conflict_dir.mkdir(parents=True)
    path = conflict_dir / "2026-05-14_c.json"
    path.write_text(
        json.dumps(
            {
                "conflict_id": "c",
                "subject": "Dr. Lo Hak Keung",
                "predicate": "phone",
                "existing_value": "2840 3709",
                "existing_source": "wiki/context/shelves/semantic.md:2",
                "new_value": "2830 3709",
                "dedupe_key": "person:dr-lo-hak-keung:phone",
                "source_session_path": "/tmp/s1.jsonl",
            }
        ),
        encoding="utf-8",
    )
    conflict = loop_lib.list_conflicts(conflict_dir)[0]

    loop_lib.accept_conflict(conflict, vault_root=vault)

    text = semantic.read_text(encoding="utf-8")
    assert not path.exists()
    assert "note:edited after conflict" in text
    assert "phone:2830 3709" in text


def test_accept_conflict_rejects_target_row_hash_mismatch(tmp_path):
    vault = tmp_path / "vault"
    semantic = vault / "wiki" / "context" / "shelves" / "semantic.md"
    semantic.parent.mkdir(parents=True)
    original_row = "- 2026-05-14 | contact | dr-lo-hak-keung | name:Dr. Lo Hak Keung | note:old number | phone:2840 3709 | dedupe_key:person:dr-lo-hak-keung:phone"
    changed_row = "- 2026-05-14 | contact | dr-lo-hak-keung | name:Dr. Lo Hak Keung | note:manual edit | phone:2840 3709 | dedupe_key:person:dr-lo-hak-keung:phone"
    semantic.write_text("## Rows\n" + changed_row + "\n", encoding="utf-8")
    conflict_dir = vault / ".staging" / "pending-conflicts"
    conflict_dir.mkdir(parents=True)
    path = conflict_dir / "2026-05-14_c.json"
    path.write_text(
        json.dumps(
            {
                "conflict_id": "c",
                "subject": "Dr. Lo Hak Keung",
                "predicate": "phone",
                "existing_value": "2840 3709",
                "existing_source": "wiki/context/shelves/semantic.md:2",
                "existing_line_hash": loop_lib._line_hash(original_row),
                "new_value": "2830 3709",
                "dedupe_key": "person:dr-lo-hak-keung:phone",
                "source_session_path": "/tmp/s1.jsonl",
            }
        ),
        encoding="utf-8",
    )
    conflict = loop_lib.list_conflicts(conflict_dir)[0]

    with pytest.raises(ValueError, match="stale target row"):
        loop_lib.accept_conflict(conflict, vault_root=vault)

    text = semantic.read_text(encoding="utf-8")
    assert path.exists()
    assert "note:manual edit" in text
    assert "phone:2840 3709" in text


def test_accept_conflict_preserves_crlf_line_endings(tmp_path):
    vault = tmp_path / "vault"
    semantic = vault / "wiki" / "context" / "shelves" / "semantic.md"
    semantic.parent.mkdir(parents=True)
    semantic.write_text(
        "## Rows\r\n"
        "- 2026-05-14 | contact | dr-lo-hak-keung | name:Dr. Lo Hak Keung | phone:2840 3709 | dedupe_key:person:dr-lo-hak-keung:phone\r\n",
        encoding="utf-8",
        newline="",
    )
    conflict_dir = vault / ".staging" / "pending-conflicts"
    conflict_dir.mkdir(parents=True)
    path = conflict_dir / "2026-05-14_c.json"
    path.write_text(
        json.dumps(
            {
                "conflict_id": "c",
                "subject": "Dr. Lo Hak Keung",
                "predicate": "phone",
                "existing_value": "2840 3709",
                "existing_source": "wiki/context/shelves/semantic.md:2",
                "new_value": "2830 3709",
                "dedupe_key": "person:dr-lo-hak-keung:phone",
                "source_session_path": "/tmp/s1.jsonl",
            }
        ),
        encoding="utf-8",
    )
    conflict = loop_lib.list_conflicts(conflict_dir)[0]

    loop_lib.accept_conflict(conflict, vault_root=vault)

    raw = semantic.read_bytes()
    assert b"\r\n" in raw
    assert b"\n##" not in raw
    assert b"phone:2830 3709" in raw


def test_accept_conflict_uses_dedupe_key_anchor_when_multiple_values_match(tmp_path):
    vault = tmp_path / "vault"
    semantic = vault / "wiki" / "context" / "shelves" / "semantic.md"
    semantic.parent.mkdir(parents=True)
    semantic.write_text(
        "## Rows\n"
        "- 2026-05-14 | contact | other | name:Other | phone:2840 3709 | dedupe_key:person:other:phone\n"
        "- 2026-05-14 | contact | dr-lo-hak-keung | name:Dr. Lo Hak Keung | phone:2840 3709 | dedupe_key:person:dr-lo-hak-keung:phone\n",
        encoding="utf-8",
    )
    conflict_dir = vault / ".staging" / "pending-conflicts"
    conflict_dir.mkdir(parents=True)
    path = conflict_dir / "2026-05-14_c.json"
    path.write_text(
        json.dumps(
            {
                "conflict_id": "c",
                "subject": "Dr. Lo Hak Keung",
                "predicate": "phone",
                "existing_value": "2840 3709",
                "existing_source": "wiki/context/shelves/semantic.md:3",
                "new_value": "2830 3709",
                "dedupe_key": "person:dr-lo-hak-keung:phone",
                "source_session_path": "/tmp/s1.jsonl",
            }
        ),
        encoding="utf-8",
    )
    conflict = loop_lib.list_conflicts(conflict_dir)[0]

    loop_lib.accept_conflict(conflict, vault_root=vault)

    lines = semantic.read_text(encoding="utf-8").splitlines()
    assert "other | name:Other | phone:2840 3709" in lines[1]
    assert "dr-lo-hak-keung | name:Dr. Lo Hak Keung | phone:2830 3709" in lines[2]


def test_accept_conflict_deletes_pending_file_if_already_applied(tmp_path):
    vault = tmp_path / "vault"
    semantic = vault / "wiki" / "context" / "shelves" / "semantic.md"
    semantic.parent.mkdir(parents=True)
    semantic.write_text(
        "## Rows\n"
        "- 2026-05-14 | contact | dr-lo-hak-keung | name:Dr. Lo Hak Keung | phone:2830 3709 | dedupe_key:person:dr-lo-hak-keung:phone\n",
        encoding="utf-8",
    )
    conflict_dir = vault / ".staging" / "pending-conflicts"
    conflict_dir.mkdir(parents=True)
    path = conflict_dir / "2026-05-14_c.json"
    path.write_text(
        json.dumps(
            {
                "conflict_id": "c",
                "subject": "Dr. Lo Hak Keung",
                "predicate": "phone",
                "existing_value": "2840 3709",
                "existing_source": "wiki/context/shelves/semantic.md:2",
                "new_value": "2830 3709",
                "dedupe_key": "person:dr-lo-hak-keung:phone",
                "source_session_path": "/tmp/s1.jsonl",
            }
        ),
        encoding="utf-8",
    )
    conflict = loop_lib.list_conflicts(conflict_dir)[0]

    changed = loop_lib.accept_conflict(conflict, vault_root=vault)

    assert changed == semantic
    assert not path.exists()
    assert "phone:2840 3709" not in semantic.read_text(encoding="utf-8")


def test_accept_conflict_tolerates_surfaced_count_change_after_render(tmp_path):
    vault = tmp_path / "vault"
    semantic = vault / "wiki" / "context" / "shelves" / "semantic.md"
    semantic.parent.mkdir(parents=True)
    semantic.write_text(
        "## Rows\n"
        "- 2026-05-14 | contact | dr-lo-hak-keung | name:Dr. Lo Hak Keung | phone:2840 3709 | dedupe_key:person:dr-lo-hak-keung:phone\n",
        encoding="utf-8",
    )
    conflict_dir = vault / ".staging" / "pending-conflicts"
    conflict_dir.mkdir(parents=True)
    path = conflict_dir / "2026-05-14_c.json"
    payload = {
        "conflict_id": "c",
        "subject": "Dr. Lo Hak Keung",
        "predicate": "phone",
        "existing_value": "2840 3709",
        "existing_source": "wiki/context/shelves/semantic.md:2",
        "new_value": "2830 3709",
        "dedupe_key": "person:dr-lo-hak-keung:phone",
        "source_session_path": "/tmp/s1.jsonl",
        "surfaced_count": 0,
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    conflict = loop_lib.list_conflicts(conflict_dir)[0]
    path.write_text(json.dumps({**payload, "surfaced_count": 1}), encoding="utf-8")

    loop_lib.accept_conflict(conflict, vault_root=vault)

    assert not path.exists()
    assert "phone:2830 3709" in semantic.read_text(encoding="utf-8")


def test_accept_conflict_requires_dedupe_key_anchor(tmp_path):
    vault = tmp_path / "vault"
    semantic = vault / "wiki" / "context" / "shelves" / "semantic.md"
    semantic.parent.mkdir(parents=True)
    semantic.write_text(
        "## Rows\n"
        "- 2026-05-14 | contact | dr-lo-hak-keung | name:Dr. Lo Hak Keung | phone:2840 3709\n",
        encoding="utf-8",
    )
    conflict_dir = vault / ".staging" / "pending-conflicts"
    conflict_dir.mkdir(parents=True)
    path = conflict_dir / "2026-05-14_c.json"
    path.write_text(
        json.dumps(
            {
                "conflict_id": "c",
                "subject": "Dr. Lo Hak Keung",
                "predicate": "phone",
                "existing_value": "2840 3709",
                "existing_source": "wiki/context/shelves/semantic.md:2",
                "new_value": "2830 3709",
                "source_session_path": "/tmp/s1.jsonl",
            }
        ),
        encoding="utf-8",
    )
    conflict = loop_lib.list_conflicts(conflict_dir)[0]

    with pytest.raises(ValueError):
        loop_lib.accept_conflict(conflict, vault_root=vault)

    assert path.exists()
    assert "phone:2840 3709" in semantic.read_text(encoding="utf-8")


def test_accept_conflict_rejects_control_whitespace_in_new_value(tmp_path):
    line = "- 2026-05-14 | contact | dr-lo | phone:2840 3709 | dedupe_key:person:dr-lo:phone"

    with pytest.raises(ValueError, match="control whitespace"):
        loop_lib._replace_pipe_field_value(
            line,
            predicate="phone",
            existing_value="2840 3709",
            new_value="2830\n3709",
        )


def test_pipe_field_parser_preserves_escaped_pipes():
    line = "- 2026-05-14 | note | thing | evidence_quote:alpha\\|beta\\|gamma | phone:2840 3709 | dedupe_key:fact:thing:note"

    assert loop_lib._extract_pipe_field(line, "evidence_quote") == "alpha\\|beta\\|gamma"
    assert loop_lib._extract_pipe_field(line, "phone") == "2840 3709"
    new_line, status = loop_lib._replace_pipe_field_value(
        line,
        predicate="phone",
        existing_value="2840 3709",
        new_value="2830 3709",
    )
    assert status == "replaced"
    assert "evidence_quote:alpha\\|beta\\|gamma" in new_line
    assert "phone:2830 3709" in new_line


def test_accept_conflict_rejects_existing_source_outside_shelves(tmp_path):
    vault = tmp_path / "vault"
    semantic = vault / "wiki" / "context" / "shelves" / "semantic.md"
    semantic.parent.mkdir(parents=True)
    semantic.write_text(
        "## Rows\n"
        "- 2026-05-14 | contact | dr-lo-hak-keung | phone:2840 3709 | dedupe_key:person:dr-lo-hak-keung:phone\n",
        encoding="utf-8",
    )
    conflict_dir = vault / ".staging" / "pending-conflicts"
    conflict_dir.mkdir(parents=True)
    path = conflict_dir / "2026-05-14_c.json"
    path.write_text(
        json.dumps(
            {
                "conflict_id": "c",
                "subject": "Dr. Lo Hak Keung",
                "predicate": "phone",
                "existing_value": "2840 3709",
                "existing_source": "../outside.md:1",
                "new_value": "2830 3709",
                "dedupe_key": "person:dr-lo-hak-keung:phone",
                "source_session_path": "/tmp/s1.jsonl",
            }
        ),
        encoding="utf-8",
    )
    conflict = loop_lib.list_conflicts(conflict_dir)[0]

    with pytest.raises(ValueError, match="invalid existing_source"):
        loop_lib.accept_conflict(conflict, vault_root=vault)

    assert path.exists()
    assert "phone:2840 3709" in semantic.read_text(encoding="utf-8")


def test_accept_conflict_rejects_existing_source_symlink_escape(tmp_path):
    vault = tmp_path / "vault"
    shelves = vault / "wiki" / "context" / "shelves"
    shelves.mkdir(parents=True)
    outside = tmp_path / "outside.md"
    outside.write_text(
        "- 2026-05-14 | contact | dr-lo-hak-keung | phone:2840 3709 | dedupe_key:person:dr-lo-hak-keung:phone\n",
        encoding="utf-8",
    )
    (shelves / "semantic.md").symlink_to(outside)
    conflict_dir = vault / ".staging" / "pending-conflicts"
    conflict_dir.mkdir(parents=True)
    path = conflict_dir / "2026-05-14_c.json"
    path.write_text(
        json.dumps(
            {
                "conflict_id": "c",
                "subject": "Dr. Lo Hak Keung",
                "predicate": "phone",
                "existing_value": "2840 3709",
                "existing_source": "wiki/context/shelves/semantic.md:1",
                "new_value": "2830 3709",
                "dedupe_key": "person:dr-lo-hak-keung:phone",
                "source_session_path": "/tmp/s1.jsonl",
            }
        ),
        encoding="utf-8",
    )
    conflict = loop_lib.list_conflicts(conflict_dir)[0]

    with pytest.raises(ValueError, match="outside shelves"):
        loop_lib.accept_conflict(conflict, vault_root=vault)

    assert path.exists()
    assert "phone:2840 3709" in outside.read_text(encoding="utf-8")


def test_accept_conflict_rejects_symlinked_shelves_root(tmp_path):
    vault = tmp_path / "vault"
    context = vault / "wiki" / "context"
    context.mkdir(parents=True)
    outside_shelves = tmp_path / "outside-shelves"
    outside_shelves.mkdir()
    outside = outside_shelves / "semantic.md"
    outside.write_text(
        "- 2026-05-14 | contact | dr-lo-hak-keung | phone:2840 3709 | dedupe_key:person:dr-lo-hak-keung:phone\n",
        encoding="utf-8",
    )
    (context / "shelves").symlink_to(outside_shelves, target_is_directory=True)
    conflict_dir = vault / ".staging" / "pending-conflicts"
    conflict_dir.mkdir(parents=True)
    path = conflict_dir / "2026-05-14_c.json"
    path.write_text(
        json.dumps(
            {
                "conflict_id": "c",
                "subject": "Dr. Lo Hak Keung",
                "predicate": "phone",
                "existing_value": "2840 3709",
                "existing_source": "wiki/context/shelves/semantic.md:1",
                "new_value": "2830 3709",
                "dedupe_key": "person:dr-lo-hak-keung:phone",
                "source_session_path": "/tmp/s1.jsonl",
            }
        ),
        encoding="utf-8",
    )
    conflict = loop_lib.list_conflicts(conflict_dir)[0]

    with pytest.raises(ValueError, match="symlinked shelves path"):
        loop_lib.accept_conflict(conflict, vault_root=vault)

    assert path.exists()
    assert "phone:2840 3709" in outside.read_text(encoding="utf-8")


def test_accept_conflict_rejects_existing_source_line_mismatch(tmp_path):
    vault = tmp_path / "vault"
    semantic = vault / "wiki" / "context" / "shelves" / "semantic.md"
    semantic.parent.mkdir(parents=True)
    semantic.write_text(
        "## Rows\n"
        "- 2026-05-14 | contact | other | name:Other | phone:2840 3709 | dedupe_key:person:other:phone\n"
        "- 2026-05-14 | contact | dr-lo-hak-keung | name:Dr. Lo Hak Keung | phone:2840 3709 | dedupe_key:person:dr-lo-hak-keung:phone\n",
        encoding="utf-8",
    )
    conflict_dir = vault / ".staging" / "pending-conflicts"
    conflict_dir.mkdir(parents=True)
    path = conflict_dir / "2026-05-14_c.json"
    path.write_text(
        json.dumps(
            {
                "conflict_id": "c",
                "subject": "Dr. Lo Hak Keung",
                "predicate": "phone",
                "existing_value": "2840 3709",
                "existing_source": "wiki/context/shelves/semantic.md:2",
                "new_value": "2830 3709",
                "dedupe_key": "person:dr-lo-hak-keung:phone",
                "source_session_path": "/tmp/s1.jsonl",
            }
        ),
        encoding="utf-8",
    )
    conflict = loop_lib.list_conflicts(conflict_dir)[0]

    with pytest.raises(ValueError, match="existing_source line"):
        loop_lib.accept_conflict(conflict, vault_root=vault)

    assert path.exists()
    assert "person:dr-lo-hak-keung:phone" in semantic.read_text(encoding="utf-8")


def test_accept_conflict_keeps_pending_file_if_neither_value_matches(tmp_path):
    vault = tmp_path / "vault"
    semantic = vault / "wiki" / "context" / "shelves" / "semantic.md"
    semantic.parent.mkdir(parents=True)
    semantic.write_text(
        "## Rows\n"
        "- 2026-05-14 | contact | dr-lo-hak-keung | name:Dr. Lo Hak Keung | phone:9999 9999\n",
        encoding="utf-8",
    )
    conflict_dir = vault / ".staging" / "pending-conflicts"
    conflict_dir.mkdir(parents=True)
    path = conflict_dir / "2026-05-14_c.json"
    path.write_text(
        json.dumps(
            {
                "conflict_id": "c",
                "subject": "Dr. Lo Hak Keung",
                "predicate": "phone",
                "existing_value": "2840 3709",
                "existing_source": "wiki/context/shelves/semantic.md:2",
                "new_value": "2830 3709",
                "source_session_path": "/tmp/s1.jsonl",
            }
        ),
        encoding="utf-8",
    )
    conflict = loop_lib.list_conflicts(conflict_dir)[0]

    with pytest.raises(ValueError):
        loop_lib.accept_conflict(conflict, vault_root=vault)

    assert path.exists()
    assert "phone:9999 9999" in semantic.read_text(encoding="utf-8")


def test_reject_conflict_archives_before_delete(tmp_path):
    conflict_dir = tmp_path / "pending-conflicts"
    archive_dir = tmp_path / "conflicts.archive"
    conflict_dir.mkdir()
    path = conflict_dir / "2026-05-14_c.json"
    path.write_text(
        json.dumps(
            {
                "conflict_id": "c",
                "subject": "Dr. Lo Hak Keung",
                "predicate": "phone",
                "existing_value": "2840 3709",
                "new_value": "2830 3709",
                "source_session_path": "/tmp/s1.jsonl",
            }
        ),
        encoding="utf-8",
    )
    conflict = loop_lib.list_conflicts(conflict_dir)[0]

    archived = loop_lib.archive_conflict_reject(
        conflict, archive_dir=archive_dir, date_str="2026-05-14", actor="keith"
    )
    loop_lib.reject_conflict(conflict)

    assert archived.exists()
    assert not path.exists()
    record = json.loads(archived.read_text(encoding="utf-8"))
    assert record["rejected_by"] == "keith"


def test_defer_conflict_recovers_if_archive_exists_before_pending_delete(tmp_path):
    conflict_dir = tmp_path / "pending-conflicts"
    archive_dir = tmp_path / "conflicts.archive"
    conflict_dir.mkdir()
    archived_dir = archive_dir / "2026-05-14"
    archived_dir.mkdir(parents=True)
    path = conflict_dir / "2026-05-14_c.json"
    payload = {
        "conflict_id": "c",
        "subject": "Dr. Lo Hak Keung",
        "predicate": "phone",
        "existing_value": "2840 3709",
        "new_value": "2830 3709",
        "source_session_path": "/tmp/s1.jsonl",
        "surfaced_count": 2,
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    archived = archived_dir / path.name
    archived.write_text(
        json.dumps(
            {
                **payload,
                "archive_reason": "deferred_threshold",
                "auto_archived_at": "2026-05-14",
                "surfaced_count": 3,
            }
        ),
        encoding="utf-8",
    )
    conflict = loop_lib.list_conflicts(conflict_dir)[0]

    count, recovered = loop_lib.defer_conflict(
        conflict, archive_dir=archive_dir, date_str="2026-05-14", threshold=3
    )

    assert count == 3
    assert recovered == archived
    assert not path.exists()


def test_defer_conflict_rejects_stale_archive_count(tmp_path):
    conflict_dir = tmp_path / "pending-conflicts"
    archive_dir = tmp_path / "conflicts.archive"
    conflict_dir.mkdir()
    archived_dir = archive_dir / "2026-05-14"
    archived_dir.mkdir(parents=True)
    path = conflict_dir / "2026-05-14_c.json"
    payload = {
        "conflict_id": "c",
        "subject": "Dr. Lo Hak Keung",
        "predicate": "phone",
        "existing_value": "2840 3709",
        "new_value": "2830 3709",
        "source_session_path": "/tmp/s1.jsonl",
        "surfaced_count": 4,
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    archived = archived_dir / path.name
    archived.write_text(
        json.dumps(
            {
                **payload,
                "archive_reason": "deferred_threshold",
                "auto_archived_at": "2026-05-14",
                "surfaced_count": 3,
            }
        ),
        encoding="utf-8",
    )
    conflict = loop_lib.list_conflicts(conflict_dir)[0]

    with pytest.raises(ValueError, match="stale archive count"):
        loop_lib.defer_conflict(
            conflict, archive_dir=archive_dir, date_str="2026-05-14", threshold=3
        )

    assert path.exists()


def test_defer_conflict_rejects_archived_count_below_threshold(tmp_path):
    conflict_dir = tmp_path / "pending-conflicts"
    archive_dir = tmp_path / "conflicts.archive"
    conflict_dir.mkdir()
    archived_dir = archive_dir / "2026-05-14"
    archived_dir.mkdir(parents=True)
    path = conflict_dir / "2026-05-14_c.json"
    payload = {
        "conflict_id": "c",
        "subject": "Dr. Lo Hak Keung",
        "predicate": "phone",
        "existing_value": "2840 3709",
        "new_value": "2830 3709",
        "source_session_path": "/tmp/s1.jsonl",
        "surfaced_count": 2,
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    archived = archived_dir / path.name
    archived.write_text(
        json.dumps(
            {
                **payload,
                "archive_reason": "deferred_threshold",
                "auto_archived_at": "2026-05-14",
            }
        ),
        encoding="utf-8",
    )
    conflict = loop_lib.list_conflicts(conflict_dir)[0]

    with pytest.raises(ValueError, match="archive count below threshold"):
        loop_lib.defer_conflict(
            conflict, archive_dir=archive_dir, date_str="2026-05-14", threshold=3
        )


def test_defer_conflict_rejects_premature_archive(tmp_path):
    conflict_dir = tmp_path / "pending-conflicts"
    archive_dir = tmp_path / "conflicts.archive"
    conflict_dir.mkdir()
    archived_dir = archive_dir / "2026-05-14"
    archived_dir.mkdir(parents=True)
    path = conflict_dir / "2026-05-14_c.json"
    payload = {
        "conflict_id": "c",
        "subject": "Dr. Lo Hak Keung",
        "predicate": "phone",
        "existing_value": "2840 3709",
        "new_value": "2830 3709",
        "source_session_path": "/tmp/s1.jsonl",
        "surfaced_count": 1,
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    archived = archived_dir / path.name
    archived.write_text(
        json.dumps(
            {
                **payload,
                "archive_reason": "deferred_threshold",
                "auto_archived_at": "2026-05-14",
                "surfaced_count": 3,
            }
        ),
        encoding="utf-8",
    )
    conflict = loop_lib.list_conflicts(conflict_dir)[0]

    with pytest.raises(ValueError, match="premature archive"):
        loop_lib.defer_conflict(
            conflict, archive_dir=archive_dir, date_str="2026-05-14", threshold=3
        )


def test_defer_conflict_rejects_archive_conflict_id_mismatch(tmp_path):
    conflict_dir = tmp_path / "pending-conflicts"
    archive_dir = tmp_path / "conflicts.archive"
    conflict_dir.mkdir()
    archived_dir = archive_dir / "2026-05-14"
    archived_dir.mkdir(parents=True)
    path = conflict_dir / "2026-05-14_c.json"
    payload = {
        "conflict_id": "c",
        "subject": "Dr. Lo Hak Keung",
        "predicate": "phone",
        "existing_value": "2840 3709",
        "new_value": "2830 3709",
        "source_session_path": "/tmp/s1.jsonl",
        "surfaced_count": 3,
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    archived = archived_dir / path.name
    archived.write_text(
        json.dumps({**payload, "conflict_id": "other", "archive_reason": "deferred_threshold"}),
        encoding="utf-8",
    )
    conflict = loop_lib.list_conflicts(conflict_dir)[0]

    with pytest.raises(ValueError, match="archive conflict_id mismatch"):
        loop_lib.defer_conflict(
            conflict, archive_dir=archive_dir, date_str="2026-05-14", threshold=3
        )


def test_defer_conflict_rejects_archive_identity_mismatch(tmp_path):
    conflict_dir = tmp_path / "pending-conflicts"
    archive_dir = tmp_path / "conflicts.archive"
    conflict_dir.mkdir()
    target = archive_dir / "2026-05-14" / "2026-05-14_c.json"
    target.parent.mkdir(parents=True)
    path = conflict_dir / "2026-05-14_c.json"
    payload = {
        "conflict_id": "c",
        "subject": "Dr. Lo Hak Keung",
        "predicate": "phone",
        "existing_value": "2840 3709",
        "new_value": "2830 3709",
        "dedupe_key": "person:dr-lo-hak-keung:phone",
        "source_session_path": "/tmp/session.jsonl",
        "surfaced_count": 3,
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    target.write_text(
        json.dumps(
            {
                **payload,
                "subject": "Different Person",
                "archive_reason": "deferred_threshold",
            }
        ),
        encoding="utf-8",
    )
    conflict = loop_lib.list_conflicts(conflict_dir)[0]

    with pytest.raises(ValueError, match="archive identity mismatch"):
        loop_lib.defer_conflict(
            conflict, archive_dir=archive_dir, date_str="2026-05-14", threshold=3
        )

    assert path.exists()


def test_resolve_index_out_of_range_raises(tmp_path):
    items = loop_lib.parse_candidates(FIXTURE_DIR / "observer-candidates.md")
    reviews: list = []
    with pytest.raises(IndexError):
        loop_lib.resolve_index(999, items, reviews, [])
    with pytest.raises(IndexError):
        loop_lib.resolve_index(0, items, reviews, [])
