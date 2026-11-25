"""Exercise worklist evidence through the Home native CLI and run recorder."""

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from test_eod_capture_status import _modern_session, _reviewed_memory_item, eod_capture
from test_automatic_memory_publisher import namespace_migration


@pytest.fixture
def native_clock(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    fixed = datetime(2026, 9, 14, 4, tzinfo=timezone.utc)

    class FixedClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed.astimezone(tz) if tz is not None else fixed.replace(tzinfo=None)

    monkeypatch.setattr(eod_capture, "datetime", FixedClock)
    return fixed


def _stamp(path, clock):
    os.utime(path, (clock.timestamp(), clock.timestamp()))


def _select(state, sources, started, clock, capsys, timestamp_format="%Y-%m-%dT%H:%M:%SZ", *, expected_exit=0):
    assert eod_capture.main([
        "memory-worklist", "--state-root", str(state), "--codex-root", str(sources),
        "--writer-role", "home", "--since", "2026-09-13",
        "--through", "2026-09-14",
        "--limit", "1", "--run-started-at", started.strftime(timestamp_format),
    ]) == expected_exit
    path = eod_capture._home_worklist_evidence_path(state, started)
    evidence = json.loads(path.read_bytes())
    assert evidence["recorded_at"] == clock.isoformat()
    assert evidence["started_at"] == started.isoformat()
    return json.loads(capsys.readouterr().out)


def _record(state, started, **options):
    return eod_capture.record_native_memory_run(
        state_root=state, writer_role="home", started_at=started.isoformat(),
        drain_result=eod_capture.drain_home_memory(state, writer_role="home"),
        **options,
    )


QUIET = {"status": "not_attempted", "reason": "no_new_reconciliation_or_pending_publication"}


@pytest.mark.parametrize("supplied", [
    None, {"writer_role": "home", "status": "idle", "selected": 0, "items": []},
])
def test_missing_or_caller_supplied_idle_worklist_cannot_report_completed(tmp_path, supplied):
    started = datetime.now(timezone.utc)
    run = _record(tmp_path, started, work_ids=[], publication_result=QUIET,
                  worklist_result=supplied)
    assert run["outcome"] == "needs_attention"
    assert "worklist evidence was not verified" in run["diagnostic"]
    receipt = json.loads(Path(run["receipt_path"]).read_bytes())
    worklist = receipt["commands"]["memory_worklist"]
    assert worklist["evidence_problem"] == "missing"
    assert worklist["evidence_verified"] is False
    assert worklist["observed_status"] == "not_recorded"


def test_only_matching_current_producer_evidence_can_report_idle_completion(tmp_path, capsys, native_clock):
    started = native_clock - timedelta(seconds=1)
    sources = tmp_path / ".codex" / "sessions"
    sources.mkdir(parents=True)
    worklist = _select(tmp_path, sources, started, native_clock, capsys)
    assert worklist["status"] == "idle"
    assert _record(tmp_path, started, work_ids=[], publication_result=QUIET)["outcome"] == "completed"
    # The producer keys evidence by exact run start, independent of test speed.
    stale = _record(tmp_path, started - timedelta(minutes=5), work_ids=[],
                    publication_result=QUIET)
    assert stale["outcome"] == "needs_attention"
    assert "producer worklist evidence is missing" in stale["diagnostic"]
    reported_only = "work:" + "a" * 64
    mismatch = _record(tmp_path, started, work_ids=[], publication_result=QUIET,
                       worklist_result={**worklist, "status": "ready", "selected": 1,
                                        "items": [{"work_id": reported_only}]})
    assert mismatch["outcome"] == "needs_attention"
    assert "differs from the retained result" in mismatch["diagnostic"]
    observed = json.loads(Path(mismatch["receipt_path"]).read_bytes())
    comparison = observed["commands"]["memory_worklist"]
    assert comparison["reported_sha256"] != comparison["observed_sha256"]
    assert comparison["differing_work_ids"] == [reported_only]
    path = eod_capture._home_worklist_evidence_path(tmp_path, started)
    _stamp(path, started - timedelta(seconds=1))
    assert _record(tmp_path, started, work_ids=[], publication_result=QUIET)["outcome"] == "completed"


@pytest.mark.parametrize("field, value", [
    ("evidence_verified", None), ("observed_status", None),
    ("observed_status", "unknown"), ("observed_selected", 1),
])
def test_canonical_home_batch_missing_verified_worklist_cannot_report_success(
    tmp_path, capsys, native_clock, field, value,
):
    started = native_clock - timedelta(seconds=1)
    sources = tmp_path / ".codex" / "sessions"
    sources.mkdir(parents=True)
    _select(tmp_path, sources, started, native_clock, capsys)
    run = _record(tmp_path, started, work_ids=[], publication_result=QUIET)
    receipt = json.loads(Path(run["receipt_path"]).read_bytes())
    worklist = receipt["commands"]["memory_worklist"]
    assert receipt["schema_version"] == 2
    if value is None:
        del worklist[field]
    else:
        worklist[field] = value
    _, _, outcome, diagnostic = eod_capture.native_job_status._classify_receipt(
        receipt, job_id="k2b-automatic-memory-home", writer_role="home", state_root=tmp_path,
    )
    assert outcome == "needs_attention"
    assert "worklist evidence was not verified" in diagnostic


def test_reader_keeps_legacy_success_but_rejects_an_explicitly_unobserved_legacy_run(
    tmp_path, capsys, native_clock,
):
    started = native_clock - timedelta(seconds=1)
    sources = tmp_path / ".codex" / "sessions"
    sources.mkdir(parents=True)
    _select(tmp_path, sources, started, native_clock, capsys)
    receipt = json.loads(Path(_record(tmp_path, started, work_ids=[],
                                    publication_result=QUIET)["receipt_path"]).read_bytes())
    receipt["schema_version"] = 1
    worklist = receipt["commands"]["memory_worklist"]
    del worklist["evidence_verified"]
    classify = eod_capture.native_job_status._classify_receipt
    identity = {"job_id": "k2b-automatic-memory-home", "writer_role": "home", "state_root": tmp_path}
    assert classify(receipt, **identity)[2] == "completed"
    # The version-1 format did not define this field. Only the recorded
    # pre-cutover outcome is retained, without version-2 proof.
    worklist["evidence_verified"] = "unknown historic field"
    assert classify(receipt, **identity)[2] == "completed"
    worklist["observed_status"] = "not_recorded"
    assert classify(receipt, **identity)[2] == "needs_attention"
    worklist["observed_status"] = "idle"
    receipt["finished_at"] = eod_capture.native_job_status.HOME_WORKLIST_RECEIPT_REQUIRED_FROM.isoformat()
    assert classify(receipt, **identity)[2] == "completed"
    del receipt["commands"]["memory_worklist"]
    assert classify(receipt, **identity)[2] == "needs_attention"
    receipt["commands"]["memory_worklist"] = worklist
    receipt["started_at"] = receipt["finished_at"]
    assert classify(receipt, **identity)[2] == "needs_attention"


def test_a_later_idle_selection_cannot_hide_an_earlier_unprocessed_turn(
    tmp_path, capsys, native_clock,
):
    state = tmp_path / "state"
    sources = tmp_path / ".codex" / "sessions"
    source = _modern_session(sources, "2026-09-13", "selected-but-unread")
    started = native_clock - timedelta(seconds=1)
    selection = _select(state, sources, started, native_clock, capsys)
    work_id = selection["items"][0]["work_id"]
    source.rename(tmp_path / "selected-but-unread.jsonl")
    idle = _select(state, sources, started, native_clock, capsys)
    assert idle["status"] == "idle"
    run = _record(state, started, work_ids=[], worklist_result=idle,
                  publication_result=QUIET)
    assert run["outcome"] == "needs_attention"
    assert run["failed"] == 0
    receipt = json.loads(Path(run["receipt_path"]).read_bytes())
    assert receipt["commands"]["memory_worklist"]["unaccounted_work_ids"] == [work_id]


@pytest.mark.parametrize("defer_last", [False, True])
def test_verified_multi_turn_run_accepts_final_idle_or_a_valid_deferral(
    tmp_path, capsys, native_clock, monkeypatch, defer_last,
):
    state = tmp_path / "state"
    sources = tmp_path / ".codex" / "sessions"
    _modern_session(sources, "2026-09-13", "first-turn")
    _modern_session(sources, "2026-09-13", "second-turn")
    started = native_clock - timedelta(seconds=1)
    completed = []
    options = {}
    for index in range(2):
        selection = _select(state, sources, started, native_clock, capsys)
        work = selection["items"][0]
        work_id = work["work_id"]
        prepared = eod_capture.prepare_memory_extraction_input(
            state_root=state, work_id=work_id, writer_role="home",
        )
        input_path = Path(prepared["input_path"])
        _stamp(input_path, native_clock)
        if index == 0:
            first_bytes = input_path.stat().st_size
        if index == 1 and defer_last:
            monkeypatch.setattr(eod_capture, "MEMORY_RUN_MAX_INPUT_BYTES", first_bytes + 1)
            options = {"deferred_work_ids": [work_id], "deferred_reason": "byte_budget"}
            break
        bundle = json.loads(Path(work["source_bundle_path"]).read_bytes())
        reviewed = _reviewed_memory_item(bundle, value="Checked")
        reviewed.update(items=[], reviewed_empty=True)
        assert eod_capture.record_memory_extraction(
            state_root=state, work_id=work_id, writer_role="home", reviewed=reviewed,
        )["status"] == "reviewed_empty"
        completed.append(work_id)
    if not defer_last:
        selection = _select(state, sources, started, native_clock, capsys)
        assert selection["status"] == "idle"
    run = _record(state, started, work_ids=completed, worklist_result=selection,
                  publication_result=QUIET, **options)
    assert run["outcome"] == "completed"
    assert run["completed"] == 2 - int(defer_last)
    assert run["deferred"] == int(defer_last)


@pytest.mark.parametrize("producer_format", ["%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%S.%f+00:00"])
@pytest.mark.parametrize("recorder_format", ["%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%S.%f+00:00"])
def test_registered_utc_start_is_accepted_by_both_cli_commands(
    tmp_path, capsys, native_clock, producer_format, recorder_format,
):
    started = native_clock - timedelta(microseconds=123456)
    sources = tmp_path / ".codex" / "sessions"
    sources.mkdir(parents=True)
    worklist = _select(tmp_path, sources, started, native_clock, capsys, producer_format)
    args = [
        "memory-record-run", "--state-root", str(tmp_path), "--writer-role", "home",
        "--started-at", started.strftime(recorder_format),
    ]
    for name, data in (
        ("worklist", worklist), ("publication", QUIET),
        ("drain", eod_capture.drain_home_memory(tmp_path, writer_role="home")),
    ):
        artifact = tmp_path / f"{name}.json"
        artifact.write_text(json.dumps(data))
        args.extend((f"--{name}-json", str(artifact)))
    assert eod_capture.main(args) == 0
    assert json.loads(capsys.readouterr().out)["outcome"] == "completed"


def test_sjm_cli_keeps_its_existing_worklist_and_receipt_contract(tmp_path, capsys, native_clock):
    sources = tmp_path / ".codex" / "sessions"
    sources.mkdir(parents=True)
    state = tmp_path / "state"
    common = ["--state-root", str(state), "--writer-role", "sjm-source-only"]
    assert eod_capture.main([
        "memory-worklist", *common, "--codex-root", str(sources),
        "--since", "2026-09-13", "--through", "2026-09-14", "--limit", "1",
    ]) == 0
    result = tmp_path / "worklist.json"
    result.write_text(capsys.readouterr().out)
    assert eod_capture.main([
        "memory-record-run", *common, "--started-at", native_clock.isoformat(),
        "--worklist-json", str(result),
    ]) == 0
    run = json.loads(capsys.readouterr().out)
    receipt = json.loads(Path(run["receipt_path"]).read_bytes())
    assert receipt["schema_version"] == 1
    assert "evidence_verified" not in receipt["commands"]["memory_worklist"]
    assert not list(state.glob("home-worklist-evidence-*.json"))


def test_held_home_batch_does_not_accept_unverified_caller_selection(tmp_path, native_clock):
    started = native_clock
    (tmp_path / "extraction-hold.json").write_text(json.dumps({"created_at": started.isoformat()}))
    fake = {"writer_role": "home", "status": "ready", "selected": 1,
            "items": [{"work_id": "work:" + "a" * 64}]}
    run = _record(tmp_path, started, work_ids=[], worklist_result=fake, publication_result=QUIET)
    assert run["outcome"] == "blocked"
    assert "evidence was not recorded during the hold" in run["diagnostic"]
    receipt = json.loads(Path(run["receipt_path"]).read_bytes())
    assert receipt["commands"]["memory_worklist"]["observed_status"] == "not_recorded"


def test_hold_appearing_after_selection_stays_visible_in_the_receipt(tmp_path, capsys, native_clock):
    state = tmp_path / "state"
    sources = tmp_path / ".codex" / "sessions"
    _modern_session(sources, "2026-09-13", "held-after-selection")
    started = native_clock - timedelta(seconds=1)
    selection = _select(state, sources, started, native_clock, capsys)
    assert selection["status"] == "ready"
    (state / "extraction-hold.json").write_text(json.dumps({"created_at": native_clock.isoformat()}))
    work_id = selection["items"][0]["work_id"]
    assert eod_capture.main([
        "memory-extraction-input", "--state-root", str(state), "--writer-role", "home",
        "--work-id", work_id,
    ]) == 3
    assert json.loads(capsys.readouterr().out)["status"] == "blocked"
    assert not list((state / "extraction-inputs").glob("*.json"))
    stopped = {"writer_role": "home", "status": "blocked", "selected": 0, "items": []}
    run = _record(state, started, work_ids=[], worklist_result=stopped, publication_result=QUIET)
    assert run["outcome"] == "blocked"
    assert "extraction held" in run["diagnostic"]
    assert "run outcome is ambiguous" not in run["diagnostic"]
    receipt = json.loads(Path(run["receipt_path"]).read_bytes())
    assert receipt["commands"]["memory_worklist"]["evidence_problem"] == "hold_conflict"


@pytest.mark.parametrize("corrupt", [b"{incomplete", b"{}", b'{"started_at": "invalid"}'])
def test_corrupt_worklist_evidence_is_preserved_and_does_not_block_next_batch(
    tmp_path, capsys, native_clock, corrupt,
):
    started = native_clock - timedelta(seconds=1)
    sources = tmp_path / ".codex" / "sessions"
    sources.mkdir(parents=True)
    path = eod_capture._home_worklist_evidence_path(tmp_path, started)
    path.write_bytes(corrupt)
    _select(tmp_path, sources, started, native_clock, capsys)
    run = _record(tmp_path, started, work_ids=[], publication_result=QUIET)
    assert run["outcome"] == "needs_attention"
    preserved = list(tmp_path.glob(f"{path.stem}.corrupt-*.json"))
    assert len(preserved) == 1
    assert preserved[0].read_bytes() == corrupt
    _select(tmp_path, sources, native_clock, native_clock, capsys)
    run = _record(tmp_path, native_clock, work_ids=[], publication_result=QUIET)
    assert run["outcome"] == "completed"


def test_an_older_run_cannot_rename_a_newer_runs_worklist(tmp_path, capsys, native_clock):
    sources = tmp_path / ".codex" / "sessions"
    sources.mkdir(parents=True)
    selection = _select(tmp_path, sources, native_clock, native_clock, capsys)
    path = eod_capture._home_worklist_evidence_path(tmp_path, native_clock)
    original = path.read_bytes()
    older_start = native_clock - timedelta(seconds=1)
    older_path = eod_capture._home_worklist_evidence_path(tmp_path, older_start)
    older_path.write_bytes(original)
    assert eod_capture.main([
        "memory-worklist", "--state-root", str(tmp_path), "--codex-root", str(sources),
        "--writer-role", "home", "--since", "2026-09-13", "--through", "2026-09-14",
        "--limit", "1", "--run-started-at", older_start.isoformat(),
    ]) == 2
    assert "overlapping Home run" in capsys.readouterr().err
    run = _record(tmp_path, older_start, work_ids=[], publication_result=QUIET,
                  worklist_result=selection)
    assert run["outcome"] == "needs_attention"
    assert "overlapping run" in run["diagnostic"]
    assert path.read_bytes() == original
    assert older_path.read_bytes() == original


def test_later_worklist_keeps_the_earlier_runs_evidence(tmp_path, capsys, native_clock):
    sources = tmp_path / ".codex" / "sessions"
    sources.mkdir(parents=True)
    started = native_clock - timedelta(seconds=1)
    first = _select(tmp_path, sources, started, native_clock, capsys)
    _select(tmp_path, sources, native_clock, native_clock, capsys)
    run = _record(tmp_path, started, work_ids=[], worklist_result=first, publication_result=QUIET)
    assert run["outcome"] == "completed"


def test_a_backwards_clock_change_does_not_replace_current_run_evidence(
    tmp_path, capsys, native_clock,
):
    sources = tmp_path / ".codex" / "sessions"
    sources.mkdir(parents=True)
    started = native_clock - timedelta(seconds=1)
    selection = _select(tmp_path, sources, started, native_clock, capsys)
    path = eod_capture._home_worklist_evidence_path(tmp_path, started)
    future = json.loads(path.read_bytes())
    future["recorded_at"] = (native_clock + timedelta(microseconds=1)).isoformat()
    path.write_text(json.dumps(future))
    original = path.read_bytes()
    with pytest.raises(ValueError, match="ahead of the local clock"):
        eod_capture._retain_home_run_worklist(tmp_path, started, selection)
    assert path.read_bytes() == original
    run = _record(tmp_path, started, work_ids=[], publication_result=QUIET)
    assert run["outcome"] == "needs_attention"
    assert "timestamp is ahead of the local receipt clock" in run["diagnostic"]


def test_previous_valid_worklist_cannot_complete_after_the_original_run_expires(
    tmp_path, capsys, native_clock, monkeypatch,
):
    sources = tmp_path / ".codex" / "sessions"
    sources.mkdir(parents=True)
    earlier = native_clock - timedelta(minutes=21)

    class EarlierClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return earlier.astimezone(tz) if tz is not None else earlier.replace(tzinfo=None)

    with monkeypatch.context() as clock:
        clock.setattr(eod_capture, "datetime", EarlierClock)
        selection = _select(tmp_path, sources, earlier, earlier, capsys)
    run = _record(tmp_path, earlier, work_ids=[], worklist_result=selection,
                  publication_result=QUIET)
    assert run["outcome"] == "unknown"
    assert "batch budget was exceeded" in run["diagnostic"]
    receipt = json.loads(Path(run["receipt_path"]).read_bytes())
    assert receipt["commands"]["batch_budget"]["status"] == "exceeded"


@pytest.mark.parametrize("missing", [False, True])
def test_status_requires_actual_producer_file_for_completed_home_receipt(
    tmp_path, capsys, native_clock, missing,
):
    sources = tmp_path / ".codex" / "sessions"
    sources.mkdir(parents=True)
    _select(tmp_path, sources, native_clock, native_clock, capsys)
    run = _record(tmp_path, native_clock, work_ids=[], publication_result=QUIET)
    assert run["outcome"] == "completed"
    read = eod_capture.native_job_status.read_native_job_status
    assert read(writer_role="home", state_root=tmp_path)["last_run_outcome"] == "completed"
    path = eod_capture._home_worklist_evidence_path(tmp_path, native_clock)
    if missing:
        path.unlink()
    else:
        altered = json.loads(path.read_bytes())
        altered["result"]["excluded"] = 2
        path.write_text(json.dumps(altered))
    status = read(writer_role="home", state_root=tmp_path)
    assert status["last_run_outcome"] == "needs_attention"
    assert status["last_successful_at"] is None
    expected = "producer worklist file is missing" if missing else "producer worklist file does not match"
    assert expected in status["diagnostic"]


def test_completed_home_receipt_requires_the_producer_start(tmp_path, capsys, native_clock):
    sources = tmp_path / ".codex" / "sessions"
    sources.mkdir(parents=True)
    _select(tmp_path, sources, native_clock, native_clock, capsys)
    run = _record(tmp_path, native_clock, work_ids=[], publication_result=QUIET)
    assert run["outcome"] == "completed"
    receipt_path = Path(run["receipt_path"])
    data = json.loads(receipt_path.read_bytes())
    del data["started_at"]
    receipt_path.write_text(json.dumps(data))
    status = eod_capture.native_job_status.read_native_job_status(
        writer_role="home", state_root=tmp_path,
    )
    assert status["last_run_outcome"] == "needs_attention"
    assert status["last_successful_at"] is None


def test_native_worklist_retains_the_existing_hold_as_an_empty_selection(tmp_path, capsys, native_clock):
    sources = tmp_path / ".codex" / "sessions"
    _modern_session(sources, "2026-09-13", "held")
    (tmp_path / "extraction-hold.json").write_text(json.dumps({"created_at": native_clock.isoformat()}))
    worklist = _select(tmp_path, sources, native_clock, native_clock, capsys)
    assert worklist["status"] == "blocked" and worklist["items"] == []
    run = _record(tmp_path, native_clock, work_ids=[], worklist_result=worklist,
                  publication_result=QUIET)
    assert run["outcome"] == "blocked"
    assert "extraction held" in run["diagnostic"]


def test_native_worklist_reports_an_unverifiable_hold(tmp_path, capsys, native_clock):
    sources = tmp_path / ".codex" / "sessions"
    sources.mkdir(parents=True)
    (tmp_path / "extraction-hold.json").write_text("bad hold JSON")
    worklist = _select(tmp_path, sources, native_clock, native_clock, capsys, expected_exit=3)
    assert worklist["status"] == "needs_attention" and worklist["hold_diagnostic"]
    run = _record(tmp_path, native_clock, work_ids=[], worklist_result=worklist,
                  publication_result=QUIET)
    assert run["outcome"] == "needs_attention"
    assert "extraction hold could not be verified" in run["diagnostic"]


def test_worklist_cli_failure_leaves_failed_run_evidence(tmp_path, capsys, native_clock):
    common = ["--state-root", str(tmp_path), "--writer-role", "home"]
    started = native_clock.isoformat()
    assert eod_capture.main([
        "memory-worklist", *common, "--codex-root", str(tmp_path),
        "--since", "2026-09-13", "--through", "2026-09-14", "--limit", "1",
        "--run-started-at", started, "--max-scan-seconds", "0",
    ]) == 2
    assert "scan budget must be positive" in capsys.readouterr().err
    assert eod_capture.main([
        "memory-record-run", *common, "--started-at", started,
        "--worklist-json", str(tmp_path / "worklist-output.json"),
    ]) == 2
    failed = json.loads(capsys.readouterr().out)
    assert failed["outcome"] == "failed"
    assert failed["diagnostic"] == "command_json_unreadable"
    assert Path(failed["receipt_path"]).is_file()


def test_home_receipt_records_command_lock_timeout(tmp_path, native_clock, monkeypatch):
    monkeypatch.setenv("K2B_EOD_LOCK_TIMEOUT_SECONDS", "0")
    with eod_capture._file_lock(tmp_path / eod_capture.HOME_NATIVE_COMMAND_LOCK):
        run = _record(tmp_path, native_clock, work_ids=[], publication_result=QUIET)
    assert run["outcome"] == "failed"
    assert run["diagnostic"] == "home_native_command_lock_unavailable"
    assert Path(run["receipt_path"]).is_file()


def test_registered_entry_uses_home_run_clock_roles_and_capture_window():
    entry = Path(eod_capture.REPO_ROOT) / "scripts/prompts/automatic-memory-home-entry.md"
    native = entry.with_name("automatic-memory-native.md").read_text()
    _, shell, _ = entry.read_text().split("```")
    assert "run_start=$(/usr/local/bin/python3.12 -c '" in shell.splitlines()
    assert 'datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")' in shell
    assert "through_date=$(TZ=Asia/Hong_Kong date +%F)" in shell.splitlines()
    assert 'scripts/eod-capture.py memory-worklist' in shell
    assert '--state-root "$HOME/.local/state/k2b/automatic-memory"' in shell
    assert '--codex-root "$HOME/.codex/sessions"' in shell
    assert f"--writer-role home --since {eod_capture.HOME_NATIVE_CAPTURE_SINCE}" in shell
    assert '--through "$through_date"' in shell
    assert '--limit 1 --run-started-at "$run_start"' in shell
    assert '`$HOME/.codex/sessions`' in native
    assert 'add `--native-home` and use the same registered sessions root' in native


def test_home_configured_source_root_excludes_older_archived_turn(tmp_path, capsys, native_clock):
    state = tmp_path / "state"
    codex = tmp_path / ".codex"
    sessions = codex / "sessions"
    active = _modern_session(sessions, "2026-09-14", "active")
    _modern_session(codex / "archived_sessions", "2026-09-13", "older-archived")
    common = ["--state-root", str(state), "--writer-role", "home",
              "--since", "2026-09-13", "--through", "2026-09-14"]
    assert eod_capture.main([
        "memory-worklist", *common, "--codex-root", str(codex),
        "--limit", "1", "--run-started-at", native_clock.isoformat(),
    ]) == 2
    assert "requires the registered Codex sessions directory" in capsys.readouterr().err
    assert not state.exists()
    worklist = _select(state, sessions, native_clock, native_clock, capsys)
    assert worklist["selected"] == 1
    assert Path(worklist["items"][0]["session_path"]) == active
    assert eod_capture.main([
        "memory-backlog", *common, "--codex-root", str(codex), "--native-home",
    ]) == 2
    assert "requires the registered Codex sessions directory" in capsys.readouterr().err
    assert eod_capture.main([
        "memory-backlog", *common, "--codex-root", str(sessions), "--native-home",
    ]) == 0
    backlog = json.loads(capsys.readouterr().out)
    assert backlog["complete"] and backlog["pending_turns"] == 1
    assert backlog["home_source_scope"] == "registered_sessions"
    assert eod_capture.main([
        "memory-backlog", *common, "--codex-root", str(codex),
    ]) == 0
    manual = json.loads(capsys.readouterr().out)
    assert manual["pending_turns"] == 2 and manual["home_source_scope"] == "operator_selected"
    assert manual["source_scope_diagnostic"]


def test_native_home_does_not_follow_a_sessions_symlink_to_archives(tmp_path, capsys, native_clock):
    codex = tmp_path / ".codex"
    archived = codex / "archived_sessions"
    _modern_session(archived, "2026-09-13", "archived")
    sessions = codex / "sessions"
    sessions.symlink_to(archived, target_is_directory=True)
    assert eod_capture.main([
        "memory-backlog", "--state-root", str(tmp_path / "state"), "--codex-root", str(sessions),
        "--writer-role", "home", "--native-home", "--since", "2026-09-13", "--through", "2026-09-14",
    ]) == 2
    assert "requires the registered Codex sessions directory" in capsys.readouterr().err


def test_native_home_does_not_reuse_an_out_of_scope_retained_binding(tmp_path, capsys, native_clock):
    state = tmp_path / "state"
    codex = tmp_path / ".codex"
    sessions = codex / "sessions"
    archived = _modern_session(codex / "archived_sessions", "2026-09-13", "older")
    broad = eod_capture.build_memory_worklist(
        state_root=state, codex_root=codex, writer_role="home",
        since="2026-09-13", through="2026-09-14", limit=1,
    )
    retained = Path(broad["items"][0]["source_bundle_path"])
    original = retained.read_bytes()
    duplicate = sessions / archived.relative_to(codex / "archived_sessions")
    duplicate.parent.mkdir(parents=True)
    duplicate.write_bytes(archived.read_bytes())
    active = _modern_session(sessions, "2026-09-14", "active")
    result = _select(state, sessions, native_clock, native_clock, capsys)
    assert result["status"] == "ready_with_exceptions"
    assert result["parse_exception_counts"] == {"retained_sources_outside_native_root": 1}
    assert result["retained_scope_work_ids"] == [broad["items"][0]["work_id"]]
    assert not result["retained_scope_unreadable_work_ids"]
    assert Path(result["items"][0]["session_path"]) == active
    assert retained.read_bytes() == original


def test_home_can_process_local_turn_with_no_nas_and_keep_its_receipt(
    tmp_path, capsys, native_clock, monkeypatch,
):
    state = tmp_path / "state"
    sources = tmp_path / ".codex" / "sessions"
    _modern_session(sources, "2026-09-13", "local-before-mount")
    started = native_clock - timedelta(seconds=1)
    discover = eod_capture.session_paths
    with monkeypatch.context() as scan:
        scan.setattr(eod_capture, "session_paths",
                     lambda root: [*discover(root), sources / "vanished.jsonl"])
        worklist = _select(state, sources, started, native_clock, capsys)
    assert worklist["selected"] == 1
    assert worklist["status"] == "ready_with_exceptions"
    assert worklist["parse_exception_counts"] == {"source_unavailable": 1}
    work = worklist["items"][0]
    work_id = work["work_id"]
    bundle = json.loads(Path(work["source_bundle_path"]).read_bytes())
    prepared = eod_capture.prepare_memory_extraction_input(
        state_root=state, work_id=work_id, writer_role="home",
    )
    assert prepared["status"] == "ready"
    _stamp(prepared["input_path"], native_clock)
    assert eod_capture.record_memory_extraction(
        state_root=state, work_id=work_id, writer_role="home",
        reviewed=_reviewed_memory_item(bundle, value="Local evidence while SMB is unavailable"),
    )["status"] == "queued"
    import automatic_memory_publisher as publisher

    drain_path = state / "actual-drain.json"
    drain_path.write_text(json.dumps(eod_capture.drain_home_memory(state, writer_role="home")))
    command = ["memory-record-run", "--finish-home", "--writer-role", "home",
               "--state-root", str(state), "--started-at", started.isoformat(), "--work-id", work_id,
               "--memory-path", str(state / "memory.json"),
               "--drain-json", str(drain_path), "--vault-root", str(tmp_path / "configured-vault")]

    def mount(args, **kwargs):
        assert args == ["/sbin/mount"]  # Never probe or write through the former mount.
        mount_point = publisher.SMB_STORAGE["mount_point"]
        return SimpleNamespace(stdout=f"{publisher.PRE_TAILSCALE_SOURCE} on {mount_point} (smbfs)\n")

    with monkeypatch.context() as scoped:
        scoped.setattr(publisher.subprocess, "run", mount)
        assert eod_capture.main(command) == 3
        waiting = json.loads(capsys.readouterr().out)
        for flags in ([], ["--check-only"]):
            assert eod_capture.main(["memory-publish", "--native-smb", "--writer-role", "home",
                "--state-root", str(state), "--vault-root", str(tmp_path / "vault"), *flags]) == 0
            assert json.loads(capsys.readouterr().out) == {
                "status": "not_attempted", "reason": "publication_mount_unavailable"}
        def timeout_probe(*args):
            raise publisher.SmbProbeTimeout("fixture probe timed out")

        scoped.setattr(publisher, "_smb_readiness", timeout_probe)
        assert eod_capture.main(command) == 3
        timed_out = json.loads(capsys.readouterr().out)
        assert timed_out["outcome"] == "waiting" and timed_out["completed"] == 1
        assert "SMB readiness probe timed out" in timed_out["diagnostic"]
    assert waiting["outcome"] == "waiting"
    assert waiting["completed"] == 1
    assert "publication waiting for the approved SMB binding" in waiting["diagnostic"]
    waiting_receipt = json.loads(Path(waiting["receipt_path"]).read_bytes())
    assert waiting_receipt["commands"]["memory_worklist"]["evidence_verified"] is True
    assert waiting_receipt["commands"]["memory_home_drain"]["status"] == "reconciled"
    assert eod_capture.native_job_status._classify_receipt(
        waiting_receipt, job_id="k2b-automatic-memory-home", writer_role="home", state_root=state,
    )[1] is None
    with monkeypatch.context() as scoped:
        def check(memory, root, vault, role, **options):
            assert vault == tmp_path / "configured-vault"
            assert options == {"check_only": True, "native_smb": True, "include_receipt": True}
            return {"publication_due": True}

        scoped.setattr(eod_capture, "publish_memory_state", check)
        assert eod_capture.main(command) == 3
        missing = json.loads(capsys.readouterr().out)
        assert missing["outcome"] == "failed" and missing["completed"] == 1
        record = json.loads(Path(missing["receipt_path"]).read_bytes())
        assert record["commands"]["memory_worklist"]["evidence_verified"] is True
        assert record["commands"]["memory_publish"]["error_type"] == "MissingRunEvidence"
    run = _record(state, started, work_ids=[work_id], worklist_result=worklist,
                  publication_result={"status": "failed", "diagnostic": "memory_publication_failed",
                                      "error_type": "PermissionError", "message": "SMB mount is absent"})
    assert run["outcome"] == "failed"
    assert run["completed"] == 1
    receipt = json.loads(Path(run["receipt_path"]).read_bytes())
    assert receipt["commands"]["memory_worklist"]["evidence_verified"] is True
    assert eod_capture._validate_memory_extraction_receipt(
        state, work_id, bundle, read_only=True,
    )["result"]["status"] == "queued"
    published = _record(state, started, work_ids=[work_id], worklist_result=worklist,
                        publication_result={"status": "published"})
    assert published["outcome"] == "completed"
    assert "selected a turn and reported other worklist exceptions" in published["diagnostic"]
    for recorded_start, evidence in (
        (started, {**worklist, "excluded": 7}),
        (started - timedelta(minutes=5), None),
        (started - timedelta(minutes=5), worklist),
    ):
        run = _record(state, recorded_start, work_ids=[work_id], worklist_result=evidence,
                      publication_result={"status": "published"})
        assert run["outcome"] == "needs_attention"
        assert run["completed"] == 1
        receipt = json.loads(Path(run["receipt_path"]).read_bytes())
        assert receipt["commands"]["memory_worklist"]["evidence_verified"] is False


@pytest.mark.parametrize("due", [True, False])
def test_home_finishing_rechecks_quiet_drain_for_earlier_publication(tmp_path, monkeypatch, due):
    vault = tmp_path / "vault"
    state_file = tmp_path / "custom-memory.json"

    def check(memory, state, target, role, **options):
        assert (memory, state, target, role) == (state_file, tmp_path, vault, "home")
        assert options == {"check_only": True, "native_smb": True}
        return {"publication_due": due}

    monkeypatch.setattr(eod_capture, "publish_memory_state", check)
    drain = {"status": "idle", "reconciled": 0, "unreconciled_remaining": 0, "reconciliation_conflicts": 0}
    result = eod_capture._native_home_publication_result(tmp_path, vault, state_file, drain, None)
    if due:
        assert result["error_type"] == "MissingRunEvidence"
    else:
        assert result == QUIET


@pytest.mark.parametrize("failure", [
    subprocess.CalledProcessError(2, "/sbin/mount"),
    PermissionError("binding is read-only"),
    RuntimeError("malformed mount table"),
])
def test_home_native_mount_probe_preserves_failure_classification(tmp_path, monkeypatch, failure):
    import automatic_memory_publisher as publisher

    def fail(*args):
        raise failure

    monkeypatch.setattr(publisher, "_smb_readiness", fail)
    result = eod_capture._home_native_mount_deferral(tmp_path)
    assert result["status"] == "failed"
    assert result["error_type"] == type(failure).__name__


def test_home_native_mount_distinguishes_the_approved_read_only_share(tmp_path, monkeypatch):
    import automatic_memory_publisher as publisher

    binding = publisher.SMB_STORAGE
    output = f'{binding["source"]} on {binding["mount_point"]} (smbfs, read-only)\n'
    monkeypatch.setattr(publisher.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(stdout=output))
    result = eod_capture._home_native_mount_deferral(tmp_path)
    assert result["status"] == "failed" and "read-only" in result["message"]


@pytest.mark.parametrize("timeout", [False, True])
def test_home_native_smb_child_distinguishes_missing_target_from_probe_timeout(tmp_path, monkeypatch, timeout):
    import automatic_memory_publisher as publisher

    binding = publisher.SMB_STORAGE

    def run(args, **kwargs):
        if args == ["/sbin/mount"]:
            return SimpleNamespace(stdout=f'{binding["source"]} on {binding["mount_point"]} (smbfs)\n')
        if timeout:
            raise subprocess.TimeoutExpired(args, 5)
        raise subprocess.CalledProcessError(3, args)

    monkeypatch.setattr(publisher.subprocess, "run", run)
    result = eod_capture._home_native_mount_deferral(tmp_path)
    if timeout:
        assert result == {"status": "not_attempted", "reason": "publication_mount_probe_timed_out"}
    else:
        assert result["status"] == "failed" and "missing target" in result["message"]


@pytest.mark.parametrize(("decision", "kind"), [
    ({}, "InvalidPublicationEvidence"),
    ({"status": "failed", "diagnostic": "memory_publication_failed",
      "error_type": "PublisherError", "message": "publisher refused"}, "PublisherError"),
    (KeyError("publisher field"), "KeyError"),
])
def test_home_finishing_preserves_publication_check_failures(tmp_path, monkeypatch, decision, kind):
    def check(*args, **kwargs):
        if isinstance(decision, Exception):
            raise decision
        return decision

    monkeypatch.setattr(eod_capture, "publish_memory_state", check)
    drain = {"status": "idle", "reconciled": 0, "unreconciled_remaining": 0, "reconciliation_conflicts": 0}
    result = eod_capture._native_home_publication_result(tmp_path, tmp_path, tmp_path / "memory.json", drain, None)
    assert result["status"] == "failed" and result["error_type"] == kind


def test_home_finishing_never_reports_publication_over_a_failed_drain(tmp_path):
    result = eod_capture._native_home_publication_result(
        tmp_path, tmp_path, tmp_path / "memory.json", {"status": "failed"}, {"status": "published"})
    assert result["status"] == "failed"
    assert result["error_type"] == "UnexpectedPublicationEvidence"


@pytest.mark.parametrize(("reconciled", "remaining"), [(1, 0), (0, 1)])
def test_home_finishing_rejects_quiet_claim_over_new_or_unreconciled_items(tmp_path, reconciled, remaining):
    drain = {"status": "reconciled", "reconciled": reconciled,
             "unreconciled_remaining": remaining, "reconciliation_conflicts": 0}
    result = eod_capture._native_home_publication_result(
        tmp_path, tmp_path, tmp_path / "memory.json", drain, QUIET)
    assert result["status"] == "failed"
    assert result["error_type"] == "InvalidPublicationEvidence"


def test_home_finishing_rejects_non_object_drain_with_a_receipt(tmp_path, capsys, native_clock):
    drain = tmp_path / "drain.json"
    drain.write_text("[]")
    started = native_clock - timedelta(seconds=1)
    assert eod_capture.main(["memory-record-run", "--finish-home", "--writer-role", "home",
        "--state-root", str(tmp_path), "--vault-root", str(tmp_path / "vault"), "--started-at",
        started.isoformat(), "--memory-path", str(tmp_path / "memory.json"), "--drain-json", str(drain)]) == 2
    result = json.loads(capsys.readouterr().out)
    assert result["diagnostic"] == "command_json_unreadable"
    assert Path(result["receipt_path"]).is_file()


def test_native_smb_flag_uses_the_real_publisher_when_ready(namespace_migration, capsys, monkeypatch):
    env = namespace_migration
    started = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    flags = ["memory-publish", "--native-smb", "--writer-role", "home",
             "--state-root", str(env.state), "--vault-root", str(env.vault)]
    original = {path: path.read_bytes() for path in env.vault.rglob("*") if path.is_file()}
    assert eod_capture.main([*flags, "--check-only"]) == 0
    assert json.loads(capsys.readouterr().out) == {"publication_due": True}
    assert {path: path.read_bytes() for path in env.vault.rglob("*") if path.is_file()} == original
    monkeypatch.setenv("K2B_COMPILE_INDEX_LOCK", str(env.vault / ".k2b-compile-index.lock.d"))
    assert eod_capture.main(flags) == 0
    publication = json.loads(capsys.readouterr().out)
    assert publication["status"] == "published"
    assert publication["binding"] == env.binding
    saved_receipt, = (env.state / "publication").glob("*.json")
    coarse_time = int(datetime.fromisoformat(started).timestamp())
    os.utime(saved_receipt, (coarse_time, coarse_time))
    drain = {"status": "reconciled", "reconciled": 1, "unreconciled_remaining": 0, "reconciliation_conflicts": 0}
    args = (env.state, env.vault, env.state / "memory.json", drain)
    assert eod_capture._native_home_publication_result(*args, None, started_at=started) == publication
    with monkeypatch.context() as scoped:
        scoped.setattr(eod_capture, "publish_memory_state",
                       lambda *args, **kwargs: pytest.fail("saved publication proof requires no new SMB operation"))
        assert eod_capture._native_home_publication_result(*args, publication, started_at=started) == publication
    rejected = eod_capture._native_home_publication_result(
        *args, {**publication, "snapshot_sha256": "0" * 64}, started_at=started)
    assert rejected["error_type"] == "InvalidPublicationEvidence"
    stale_start = (datetime.now(timezone.utc) + timedelta(seconds=1)).isoformat()
    rejected = eod_capture._native_home_publication_result(*args, publication, started_at=stale_start)
    assert rejected["error_type"] == "InvalidPublicationEvidence"
    duplicate = {**publication, "duplicate": True}
    assert eod_capture._native_home_publication_result(*args, duplicate, started_at=stale_start) == duplicate
    assert eod_capture._native_home_publication_result(*args, None, started_at=stale_start) == duplicate
    with monkeypatch.context() as scoped:
        scoped.setattr(eod_capture, "publish_memory_state", lambda *args, **kwargs: {"publication_due": False})
        bad_duplicate = {**duplicate, "snapshot_sha256": "0" * 64}
        assert eod_capture._native_home_publication_result(
            *args, bad_duplicate, started_at=stale_start)["error_type"] == "InvalidPublicationEvidence"
    monkeypatch.setattr(eod_capture, "publish_memory_state", lambda *args, **kwargs: {"publication_due": True})
    rejected = eod_capture._native_home_publication_result(*args, duplicate, started_at=stale_start)
    assert rejected["error_type"] == "MissingRunEvidence"


@pytest.mark.parametrize("error", [subprocess.CalledProcessError(2, "compile-helper"),
                                   PermissionError("publisher permission refused")])
def test_memory_publish_dependency_failure_has_structured_output(tmp_path, monkeypatch, capsys, error):
    def fail(*args, **kwargs):
        raise error

    monkeypatch.setattr(eod_capture, "publish_memory_state", fail)
    assert eod_capture.main(["memory-publish", "--native-smb", "--writer-role", "home",
        "--state-root", str(tmp_path), "--vault-root", str(tmp_path / "vault")]) == 2
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "failed" and result["error_type"] == type(error).__name__


def test_native_publish_waits_when_mount_disappears_after_readiness(tmp_path, monkeypatch, capsys):
    import automatic_memory_publisher as publisher

    def unavailable(**kwargs):
        raise publisher.HomeMountUnavailable("fixture mount disconnected during publication preflight")

    monkeypatch.setattr(eod_capture, "_home_native_mount_deferral", lambda vault: None)
    monkeypatch.setattr(publisher, "publish", unavailable)
    assert eod_capture.main(["memory-publish", "--native-smb", "--writer-role", "home",
        "--state-root", str(tmp_path), "--vault-root", str(tmp_path / "vault")]) == 0
    assert json.loads(capsys.readouterr().out) == {
        "status": "not_attempted", "reason": "publication_mount_unavailable"}


def test_memory_publish_rejects_non_object_output(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(eod_capture, "publish_memory_state", lambda **kwargs: [])
    assert eod_capture.main(["memory-publish", "--native-smb", "--writer-role", "home",
        "--state-root", str(tmp_path), "--vault-root", str(tmp_path / "vault")]) == 2
    assert json.loads(capsys.readouterr().out)["status"] == "failed"


def test_home_finishing_preserves_actual_drain_error(tmp_path, capsys, native_clock):
    drain = tmp_path / "drain.json"
    drain.write_text(json.dumps({"status": "failed", "diagnostic": "memory_drain_failed",
                                 "error_type": "OSError", "message": "fixture drain access failed"}))
    started = native_clock - timedelta(seconds=1)
    assert eod_capture.main(["memory-record-run", "--finish-home", "--writer-role", "home",
        "--state-root", str(tmp_path), "--vault-root", str(tmp_path / "vault"), "--started-at",
        started.isoformat(), "--memory-path", str(tmp_path / "memory.json"), "--drain-json", str(drain)]) == 3
    result = json.loads(capsys.readouterr().out)
    assert result["outcome"] == "failed"
    receipt = json.loads(Path(result["receipt_path"]).read_bytes())
    assert receipt["commands"]["memory_home_drain"]["message"] == "fixture drain access failed"


def test_home_finishing_records_unverified_eligibility_as_a_durable_failure(tmp_path, capsys, native_clock):
    drain = tmp_path / "drain.json"
    drain.write_text(json.dumps(eod_capture.drain_home_memory(tmp_path, writer_role="home")))
    publication = tmp_path / "publication.json"
    publication.write_text(json.dumps({"status": "not_attempted", "reason": "publication_eligibility_unverified"}))
    started = (native_clock - timedelta(seconds=1)).isoformat()
    assert eod_capture.main(["memory-record-run", "--finish-home", "--writer-role", "home",
        "--state-root", str(tmp_path), "--vault-root", str(tmp_path / "vault"), "--started-at", started,
        "--memory-path", str(tmp_path / "memory.json"), "--drain-json", str(drain),
        "--publication-json", str(publication)]) == 2
    result = json.loads(capsys.readouterr().out)
    assert result["diagnostic"] == "publication_eligibility_unverified"
    assert Path(result["receipt_path"]).is_file()
