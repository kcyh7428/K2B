"""Exercise worklist evidence through the Home native CLI and run recorder."""

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path

import pytest

from test_eod_capture_status import _modern_session, _reviewed_memory_item, eod_capture


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
    waiting = _record(state, started, work_ids=[work_id], worklist_result=worklist,
                      publication_result={"status": "not_attempted",
                                          "reason": "publication_mount_unavailable"})
    assert waiting["outcome"] == "waiting"
    assert waiting["completed"] == 1
    assert "publication waiting for the approved SMB binding" in waiting["diagnostic"]
    waiting_receipt = json.loads(Path(waiting["receipt_path"]).read_bytes())
    assert waiting_receipt["commands"]["memory_home_drain"]["status"] == "reconciled"
    assert eod_capture.native_job_status._classify_receipt(
        waiting_receipt, job_id="k2b-automatic-memory-home", writer_role="home", state_root=state,
    )[1] is None
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
