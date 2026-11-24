"""Exercise the Home hold authority with private synthetic work and receipts."""

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from test_eod_capture_status import _modern_session, _reviewed_memory_item, eod_capture


@pytest.fixture
def selected(tmp_path):
    started = datetime.now(timezone.utc) - timedelta(seconds=1)
    source = tmp_path / "sessions"
    state = tmp_path / "state"
    _modern_session(source, "2026-09-13", "new-hold-test")
    worklist = eod_capture.build_memory_worklist(
        state_root=state, codex_root=source, writer_role="home",
        since="2026-09-13", through="2026-09-13", limit=1,
    )
    assert worklist["status"] == "ready"
    current = state / "current-worklist.json"
    current.write_text(json.dumps(worklist))
    work = worklist["items"][0]
    return state, started, current, work


def _record(selected, reason="persistent_failure", **options):
    state, started, worklist, work = selected
    return eod_capture.record_home_extraction_hold(
        state_root=state, started_at=started.isoformat(),
        work_id=work["work_id"], worklist_path=worklist, reason=reason, **options,
    )


def _terminal_diagnostic(selected, stamp):
    state, _, _, work = selected
    bundle = json.loads(Path(work["source_bundle_path"]).read_text())
    for _ in range(eod_capture.MAX_MEMORY_EXTRACTION_ATTEMPTS):
        result = eod_capture._record_memory_extraction_diagnostic(
            state, work["work_id"], bundle, reason="invalid_reviewed_extraction", attempted_at=stamp,
        )
    assert result["status"] == "needs_attention"


def test_previous_publication_failure_cannot_create_a_home_extraction_hold(selected, capsys):
    state, started, worklist, work = selected
    eod_capture.record_failed_native_memory_run(
        state_root=state, writer_role="home",
        started_at=(started - timedelta(hours=6)).isoformat(),
        diagnostic="publication_eligibility_unverified",
    )
    args = ["memory-record-home-hold", "--state-root", str(state), "--started-at",
            started.isoformat(), "--work-id", work["work_id"], "--worklist-json",
            str(worklist), "--reason"]
    assert eod_capture.main([*args, "persistent_failure"]) == 2
    assert "hold refused" in capsys.readouterr().err
    assert not (state / "extraction-hold.json").exists()
    assert eod_capture.prepare_memory_extraction_input(
        state_root=state, writer_role="home", work_id=work["work_id"],
    )["status"] == "ready"


@pytest.mark.parametrize("stale", ["diagnostic", "selection"])
def test_stale_home_hold_evidence_is_refused(selected, stale):
    state, started, worklist, _ = selected
    old = started - timedelta(hours=1)
    _terminal_diagnostic(selected, old if stale == "diagnostic" else started)
    if stale == "selection":
        os.utime(worklist, (old.timestamp(), old.timestamp()))
    with pytest.raises(ValueError, match="current"):
        _record(selected)
    assert not (state / "extraction-hold.json").exists()


def test_fresh_terminal_extraction_creates_hold_once(selected):
    state, _, _, work = selected
    _terminal_diagnostic(selected, datetime.now(timezone.utc))
    hold = state / "extraction-hold.json"
    assert _record(selected) == {
        "status": "created", "work_id": work["work_id"], "reason": "persistent_failure",
    }
    before = hold.read_bytes()
    assert _record(selected, "repeated_selection") == {"status": "already_present"}
    assert hold.read_bytes() == before
    assert eod_capture.native_job_status._read_hold(state)[0] == "present"


def test_mismatched_selection_and_existing_operator_hold(selected):
    state, started, worklist, work = selected
    forged = json.loads(worklist.read_text())
    forged["items"][0]["source_bundle_path"] = str(state / "different-work")
    worklist.write_text(json.dumps(forged))
    with pytest.raises(ValueError, match="selection paths"):
        _record(selected)
    hold = state / "extraction-hold.json"
    hold.write_text(json.dumps({"created_at": started.isoformat(), "reason": "operator_hold"}))
    before = hold.read_bytes()
    assert _record(selected) == {"status": "already_present"}
    assert hold.read_bytes() == before
    assert eod_capture.build_memory_worklist(
        state_root=state, codex_root=Path(work["session_path"]).parent,
        writer_role="home", since="2026-09-13", through="2026-09-13", limit=1,
    )["status"] == "blocked"


def test_input_budget_claim_is_recomputed_without_writing_a_view(selected):
    state, _, _, _ = selected
    with pytest.raises(ValueError, match="fits the extraction input budget"):
        _record(selected, "input_budget_exceeded")
    assert not (state / "extraction-inputs").exists()
    assert not (state / "extraction-hold.json").exists()


def test_item_limit_requires_more_than_twenty_distinct_source_bound_items(selected):
    state, _, _, work = selected
    bundle = json.loads(Path(work["source_bundle_path"]).read_text())
    eod_capture.prepare_memory_extraction_input(
        state_root=state, writer_role="home", work_id=work["work_id"],
    )
    reviewed = _reviewed_memory_item(bundle, value="Synthetic fact")
    template = reviewed["items"][0]
    path = state / "reviewed.json"
    reviewed["items"] = [template] * 21
    path.write_text(json.dumps(reviewed))
    with pytest.raises(ValueError, match="item-count limit"):
        _record(selected, "item_count_exceeded", reviewed_path=path)
    reviewed["items"] = [
        {**template, "dedupe_key": f"fact:k2b-memory:office-{i}"}
        for i in range(21)
    ]
    path.write_text(json.dumps(reviewed))
    assert _record(selected, "item_count_exceeded", reviewed_path=path)["status"] == "created"
