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


def test_append_learning_writes_expected_entry(tmp_path):
    learnings = tmp_path / "learnings.md"
    learnings.write_text("# K2B Learnings\n\n", encoding="utf-8")
    items = loop_lib.parse_candidates(FIXTURE_DIR / "observer-candidates.md")
    cand = items[0]
    lid = loop_lib.append_learning(
        learnings, cand, date_str="2026-04-23", observer_run="2026-04-22 21:44"
    )
    assert lid == "L-2026-04-23-001"
    text = learnings.read_text(encoding="utf-8")
    assert "### L-2026-04-23-001" in text
    assert f'distilled-rule: "{cand.rule}"' in text
    assert f"- **Area:** {cand.area}" in text
    assert f"- **Distilled rule:** {cand.rule}" in text
    assert f"- **Confidence:** {cand.severity}" in text
    assert "- **Reinforced:** 1" in text
    assert "- **Date:** 2026-04-23" in text
    assert "- **Source:** observer-candidates (auto-applied 2026-04-23 via session-start dashboard)" in text
    assert cand.evidence in text


def test_append_learning_increments_for_same_day(tmp_path):
    learnings = tmp_path / "learnings.md"
    learnings.write_text("# empty\n", encoding="utf-8")
    items = loop_lib.parse_candidates(FIXTURE_DIR / "observer-candidates.md")
    lid1 = loop_lib.append_learning(learnings, items[0], date_str="2026-04-23", observer_run="run")
    lid2 = loop_lib.append_learning(learnings, items[1], date_str="2026-04-23", observer_run="run")
    assert lid1 == "L-2026-04-23-001"
    assert lid2 == "L-2026-04-23-002"


def test_archive_reject_writes_jsonl_line(tmp_path):
    archive_dir = tmp_path / "observations.archive"
    archive_dir.mkdir()
    items = loop_lib.parse_candidates(FIXTURE_DIR / "observer-candidates.md")
    cand = items[3]
    loop_lib.archive_reject(archive_dir, cand, date_str="2026-04-23", actor="keith")
    target = archive_dir / "rejected-2026-04-23.jsonl"
    assert target.exists()
    line = target.read_text(encoding="utf-8").strip()
    record = json.loads(line)
    assert record["item_id"] == cand.item_id
    assert record["severity"] == cand.severity
    assert record["area"] == cand.area
    assert record["rule"] == cand.rule
    assert record["evidence"] == cand.evidence
    assert record["rejected"] == "keith 2026-04-23"


def test_archive_reject_appends_without_clobber(tmp_path):
    archive_dir = tmp_path / "observations.archive"
    archive_dir.mkdir()
    items = loop_lib.parse_candidates(FIXTURE_DIR / "observer-candidates.md")
    loop_lib.archive_reject(archive_dir, items[3], date_str="2026-04-23", actor="keith")
    loop_lib.archive_reject(archive_dir, items[4], date_str="2026-04-23", actor="keith")
    target = archive_dir / "rejected-2026-04-23.jsonl"
    lines = target.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
