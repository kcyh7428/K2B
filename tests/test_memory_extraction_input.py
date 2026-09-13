"""Native input must be bounded by completed turn, not accumulated history."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

from test_eod_capture_status import ROOT, _modern_session, eod_capture


def prepared(tmp_path, text="Use the Azure meeting room.", completed_at=None,
             prior_text="Old context. " * 20000):
    root = tmp_path / "sessions"
    source = _modern_session(root, "2026-09-12", "long-input")
    rows = [json.loads(line) for line in source.read_text().splitlines()]
    rows[2]["payload"]["item"]["content"][0]["text"] = prior_text
    rows += [
        {"type": "event_msg", "payload": {"type": "task_started", "turn_id": "turn-2"}},
        {"type": "response_item", "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": text}]}},
        {"type": "response_item", "payload": {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "Understood; no other choice was approved."}]}},
        {"type": "event_msg", "timestamp": "2026-09-13T09:00:00+08:00", "payload": {"type": "task_complete", "turn_id": "turn-2"}},
    ]
    if completed_at is not None:
        rows[-1]["payload"]["completed_at"] = completed_at
    source.write_text("".join(json.dumps(row) + "\n" for row in rows))
    state = tmp_path / "state"
    work = eod_capture.build_memory_worklist(state_root=state, codex_root=root,
        since="2026-09-13", through="2026-09-13", writer_role="home", limit=1)["items"][0]
    return state, work


def input_command(state, work, role="home"):
    return subprocess.run([sys.executable, str(ROOT / "scripts/eod-capture.py"),
        "memory-extraction-input", "--state-root", str(state), "--work-id", work["work_id"],
        "--writer-role", role], capture_output=True, text=True)


def test_long_history_produces_complete_current_turn_without_rewriting_source(tmp_path):
    state, work = prepared(tmp_path)
    source = Path(work["source_bundle_path"])
    before = source.read_bytes()
    assert len(before) > 48000
    result = input_command(state, work)
    assert result.returncode == 0, result.stderr
    ready = json.loads(result.stdout)
    assert ready["status"] == "ready"
    view_path = Path(ready["input_path"])
    view = json.loads(view_path.read_text())
    assert view_path.stat().st_size <= 48000
    assert view_path.stat().st_mode & 0o077 == 0
    assert [e["text"] for e in view["dialogue_events"]] == [
        "Use the Azure meeting room.", "Understood; no other choice was approved."]
    assert all(e["turn_id"] == "turn-2" for e in view["dialogue_events"])
    assert view["context_omitted_events"] == 1
    assert view["context_events"] == []
    assert "chunks" not in view and "transcript" not in view
    full = json.loads(before)
    for key in ("raw_source_sha256", "transcript_sha256", "completed_prefix_sha256", "completed_cursor"):
        assert view[key] == full[key]
    assert source.read_bytes() == before
    again = input_command(state, work)
    assert again.returncode == 0
    assert view_path.read_bytes() == json.dumps(view, ensure_ascii=False, indent=2, sort_keys=True).encode() + b"\n"
    assert not (state / "extraction-receipts").exists()


def test_real_oversized_turn_is_not_truncated_or_receipted(tmp_path):
    state, work = prepared(tmp_path, "漢" * 20000)
    result = input_command(state, work)
    assert result.returncode == 3, result.stderr
    outcome = json.loads(result.stdout)
    assert outcome["status"] == "input_budget_exceeded"
    assert "input_path" not in outcome
    assert not (state / "extraction-inputs").exists()
    assert not (state / "extraction-receipts").exists()


def test_input_rejects_wrong_host_role(tmp_path):
    state, work = prepared(tmp_path)
    result = input_command(state, work, "sjm-source-only")
    assert result.returncode == 2
    assert "another role" in result.stderr
    assert not (state / "extraction-inputs").exists()


def test_input_metadata_can_record_empty_and_replay_original_work(tmp_path):
    state, work = prepared(tmp_path)
    result = input_command(state, work)
    assert result.returncode == 0, result.stderr
    view = json.loads(Path(json.loads(result.stdout)["input_path"]).read_text())
    reviewed = {k: view[k] for k in ("raw_source_sha256", "transcript_sha256",
        "completed_prefix_sha256", "completed_cursor", "completed_prefix_mode",
        "completed_date", "host_id", "session_id")}
    reviewed.update(schema_version="1.0", review_state="reviewed", items=[], reviewed_empty=True)
    recorded = eod_capture.record_memory_extraction(state_root=state, work_id=work["work_id"],
        reviewed=reviewed, writer_role="home")
    assert recorded["status"] == "reviewed_empty"
    replay = eod_capture.record_memory_extraction(state_root=state, work_id=work["work_id"],
        reviewed=reviewed, writer_role="home")
    assert replay["duplicate"] is True


@pytest.mark.parametrize("completed_at", [1789261200, 1789261200.0])
def test_native_epoch_timestamp_survives_input_and_actual_queue(tmp_path, completed_at):
    state, work = prepared(tmp_path, completed_at=completed_at)
    result = input_command(state, work)
    assert result.returncode == 0, result.stderr
    view = json.loads(Path(json.loads(result.stdout)["input_path"]).read_text())
    reviewed = {k: view[k] for k in ("raw_source_sha256", "transcript_sha256")}
    reviewed.update(schema_version="1.0", items=[{
        "kind": "decision", "subtype": "other", "subject": "Meeting room",
        "predicate": "selection", "object": "Azure", "scope": "K2B", "confidence": "high",
        "speaker_source": "keith", "evidence_event_id": view["dialogue_events"][0]["event_id"],
        "evidence_quote": "Use the Azure meeting room.", "dedupe_key": "meeting:room:selection",
        "canonical_home": "wiki/context/shelves/semantic.md",
    }])
    outcome = eod_capture.record_memory_extraction(state_root=state, work_id=work["work_id"],
        reviewed=reviewed, writer_role="home")
    assert outcome["status"] == "queued"
    envelope = json.loads(Path(outcome["deliveries"][0]["path"]).read_text())
    assert envelope["bundle"]["source"]["completed_at"] == "2026-09-13T01:00:00+00:00"
    assert json.loads(Path(work["source_bundle_path"]).read_text())["completed_at"] == completed_at


def test_mixed_native_epoch_and_iso_worklist_orders_by_actual_time(tmp_path):
    state, _ = prepared(tmp_path, completed_at=1789261200)
    _modern_session(tmp_path / "sessions", "2026-09-13", "later-iso")
    result = eod_capture.build_memory_worklist(state_root=state, codex_root=tmp_path / "sessions",
        since="2026-09-13", through="2026-09-13", writer_role="home", limit=2)
    assert [Path(i["session_path"]).name for i in result["items"]] == [
        "rollout-long-input.jsonl", "rollout-later-iso.jsonl"]


def test_recent_context_is_preserved_but_not_current_evidence(tmp_path):
    state, work = prepared(tmp_path, prior_text="I previously chose the Green room.")
    result = input_command(state, work)
    assert result.returncode == 0, result.stderr
    view = json.loads(Path(json.loads(result.stdout)["input_path"]).read_text())
    assert view["context_omitted_events"] == 0
    assert [e["text"] for e in view["context_events"]] == ["I previously chose the Green room."]
    assert view["context_events"][0]["turn_id"] == "turn-1"
    assert view["dialogue_events"][0]["text"] == "Use the Azure meeting room."


def test_corrupted_source_cannot_produce_native_input(tmp_path):
    state, work = prepared(tmp_path)
    path = Path(work["source_bundle_path"])
    bundle = json.loads(path.read_text())
    bundle["dialogue_events"][-1]["text"] = "Forged source text"
    path.write_text(json.dumps(bundle))
    result = input_command(state, work)
    assert result.returncode == 2
    assert "hash does not match" in result.stderr
    assert not (state / "extraction-inputs").exists()


@pytest.mark.parametrize("bad", [True, float("nan"), float("inf"), -float("inf")])
def test_invalid_native_timestamp_is_not_accepted(tmp_path, bad):
    state, work = prepared(tmp_path)
    path = Path(work["source_bundle_path"])
    bundle = json.loads(path.read_text())
    bundle["completed_at"] = bad
    path.write_text(json.dumps(bundle))
    result = input_command(state, work)
    assert result.returncode == 2
    assert "completed_at" in result.stderr
    assert not (state / "extraction-inputs").exists()
