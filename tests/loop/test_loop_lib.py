"""Unit tests for scripts/loop/loop_lib.py."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from loop import loop_lib  # noqa: E402


FIXTURE_DIR = ROOT / "tests" / "fixtures" / "loop-mvp"


def test_parse_candidates_returns_five_items():
    items = loop_lib.parse_candidates(FIXTURE_DIR / "observer-candidates.md")
    assert len(items) == 5
    assert items[0].severity == "high"
    assert items[0].area == "workflow"
    assert "parse errors" in items[0].rule.lower()
    assert items[0].evidence  # non-empty


def test_parse_candidates_assigns_stable_ids():
    items = loop_lib.parse_candidates(FIXTURE_DIR / "observer-candidates.md")
    ids = [it.item_id for it in items]
    assert len(set(ids)) == 5  # all unique
    assert all(len(i) == 8 and all(c in "0123456789abcdef" for c in i) for i in ids)

    # Re-parsing yields same IDs (deterministic hash)
    items2 = loop_lib.parse_candidates(FIXTURE_DIR / "observer-candidates.md")
    assert [it.item_id for it in items2] == ids


def test_allocate_next_lid_empty_for_date(tmp_path):
    learnings = tmp_path / "learnings.md"
    learnings.write_text("# empty\n\n### L-2026-04-22-007\n", encoding="utf-8")
    assert loop_lib.allocate_next_lid(learnings, "2026-04-23") == "L-2026-04-23-001"


def test_allocate_next_lid_skips_existing(tmp_path):
    learnings = tmp_path / "learnings.md"
    learnings.write_text(
        "### L-2026-04-22-000\n\n### L-2026-04-22-002\n", encoding="utf-8"
    )
    assert loop_lib.allocate_next_lid(learnings, "2026-04-22") == "L-2026-04-22-003"


def test_allocate_next_lid_handles_missing_file(tmp_path):
    assert loop_lib.allocate_next_lid(tmp_path / "nope.md", "2026-04-22") == "L-2026-04-22-001"


def test_rewrite_candidates_removes_specified_ids(tmp_path):
    src = FIXTURE_DIR / "observer-candidates.md"
    dst = tmp_path / "observer-candidates.md"
    dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")

    before = loop_lib.parse_candidates(dst)
    assert len(before) == 5
    remove_ids = {before[0].item_id, before[2].item_id}

    loop_lib.rewrite_candidates_without(dst, remove_ids)

    after = loop_lib.parse_candidates(dst)
    assert len(after) == 3
    assert {c.item_id for c in after}.isdisjoint(remove_ids)
    # Kept items preserve original order
    kept_rules = [c.rule for c in after]
    assert kept_rules == [before[1].rule, before[3].rule, before[4].rule]


def test_rewrite_candidates_noop_when_nothing_to_remove(tmp_path):
    src = FIXTURE_DIR / "observer-candidates.md"
    dst = tmp_path / "observer-candidates.md"
    original = src.read_text(encoding="utf-8")
    dst.write_text(original, encoding="utf-8")
    loop_lib.rewrite_candidates_without(dst, set())
    assert dst.read_text(encoding="utf-8") == original


def test_rewrite_candidates_empties_section_when_all_removed(tmp_path):
    src = FIXTURE_DIR / "observer-candidates.md"
    dst = tmp_path / "observer-candidates.md"
    dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")

    items = loop_lib.parse_candidates(dst)
    loop_lib.rewrite_candidates_without(dst, {c.item_id for c in items})
    assert loop_lib.parse_candidates(dst) == []
    remaining = dst.read_text(encoding="utf-8")
    assert "## Candidate Learnings" in remaining
    assert "## Summary" in remaining
