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
