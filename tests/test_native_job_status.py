from __future__ import annotations

import json
import os
import stat
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "lib"))

import native_job_status  # noqa: E402

_UNSET: object = object()


@pytest.mark.parametrize("schema,exception", [
    (1, {"type": "task_complete_turn_id_mismatch", "count": 3,
         "state": "unchanged_from_prior_run", "actionable": False}),
    ("1.0", {"stage": "memory_worklist", "status": "non_blocking_parse_exceptions",
             "details": {"task_complete_turn_id_mismatch": 2}}),
])
def test_deployed_native_receipt_formats_preserve_actual_completion(tmp_path, schema, exception):
    state = tmp_path / "state"
    path = _home_receipt(state, "run.json", exceptions=[exception])
    data = json.loads(path.read_text())
    data["schema_version"] = schema
    path.write_text(json.dumps(data))
    result = native_job_status.read_native_job_status(
        writer_role="home", state_root=state, automation_root=tmp_path / "jobs")
    assert result["last_run_outcome"] == "completed"
    assert result["last_completed_at"] == "2026-09-13T22:36:57+00:00"


@pytest.mark.parametrize("field,value", [("count", -1), ("count", True), ("actionable", "false")])
def test_deployed_parse_exception_rejects_malformed_evidence(tmp_path, field, value):
    exception = {"type": "task_complete_turn_id_mismatch", "count": 3,
                 "state": "unchanged_from_prior_run", "actionable": False}
    exception[field] = value
    state = tmp_path / "state"
    _home_receipt(state, "run.json", exceptions=[exception])
    result = native_job_status.read_native_job_status(
        writer_role="home", state_root=state, automation_root=tmp_path / "jobs")
    assert result["last_run_outcome"] == "unknown"


def test_batch_cannot_claim_completion_with_only_one_extracted_turn(tmp_path):
    state = tmp_path / "state"
    path = _home_receipt(state, "run.json")
    data = json.loads(path.read_text())
    data["commands"]["memory_worklist"]["selected"] = 12
    data["commands"]["memory_record_extraction"]["completed"] = 1
    path.write_text(json.dumps(data))
    result = native_job_status.read_native_job_status(
        writer_role="home", state_root=state, automation_root=tmp_path / "jobs")
    assert result["last_run_outcome"] == "unknown"


def _home_status(state, tmp_path):
    return native_job_status.read_native_job_status(
        writer_role="home", state_root=state, automation_root=tmp_path / "jobs")


@pytest.mark.parametrize("status,expected", [("bounded", "completed"), ("exceeded", "unknown")])
def test_batch_budget_boundary_is_validated_without_failure(tmp_path, status, expected):
    state = tmp_path / "state"
    path = _home_receipt(state, "run.json")
    data = json.loads(path.read_text())
    data["commands"]["memory_record_extraction"]["completed"] = 1
    data["commands"]["batch_budget"] = {"status": status}
    path.write_text(json.dumps(data))
    assert _home_status(state, tmp_path)["last_run_outcome"] == expected


@pytest.mark.parametrize("budget", [{"status": "bogus"}, {"status": 1}, [], "bounded", None])
def test_malformed_batch_budget_never_completes(tmp_path, budget):
    state = tmp_path / "state"
    path = _home_receipt(state, "run.json")
    data = json.loads(path.read_text())
    data["commands"]["memory_record_extraction"]["completed"] = 1
    data["commands"]["batch_budget"] = budget
    path.write_text(json.dumps(data))
    assert _home_status(state, tmp_path)["last_run_outcome"] == "unknown"


def test_missing_batch_budget_key_is_still_readable(tmp_path):
    state = tmp_path / "state"
    path = _home_receipt(state, "run.json")
    data = json.loads(path.read_text())
    data["commands"]["memory_record_extraction"]["completed"] = 1
    path.write_text(json.dumps(data))
    assert _home_status(state, tmp_path)["last_run_outcome"] == "completed"


@pytest.mark.parametrize("field,value", [
    ("deferred", -1), ("deferred", True), ("deferred", None),
    ("unattributed_pending", "1"), ("unattributed_pending", 2 ** 70),
])
def test_malformed_batch_counters_never_complete(tmp_path, field, value):
    state = tmp_path / "state"
    path = _home_receipt(state, "run.json")
    data = json.loads(path.read_text())
    extraction = data["commands"]["memory_record_extraction"]
    extraction["completed"] = 1
    extraction[field] = value
    path.write_text(json.dumps(data))
    assert _home_status(state, tmp_path)["last_run_outcome"] == "unknown"


def test_unattributed_prepared_work_is_ambiguous_not_failed(tmp_path):
    state = tmp_path / "state"
    path = _home_receipt(state, "run.json")
    data = json.loads(path.read_text())
    extraction = data["commands"]["memory_record_extraction"]
    extraction.update(completed=1, deferred=0, unattributed_pending=1)
    path.write_text(json.dumps(data))
    assert _home_status(state, tmp_path)["last_run_outcome"] == "unknown"


@pytest.mark.parametrize("exception,expected", [
    ({"type": "input_budget_exceeded", "work_id": "work:example"}, "completed"),
    ({"reason": "quota", "status": "resolved_with_reviewed_empty"}, "completed"),
    ({"type": "input_budget_exceeded"}, "unknown"),
    ({"type": "input_budget_exceeded", "work_id": 3}, "unknown"),
    ({"type": "input_budget_exceeded", "work_id": "work:example",
      "operator_action_required": "yes"}, "unknown"),
    ("free text must not be classified", None),
])
def test_receipt_exception_channel(tmp_path, exception, expected):
    state = tmp_path / "state"
    _home_receipt(state, "run.json", exceptions=[exception])
    result = _home_status(state, tmp_path)
    if expected is None:
        # Plain free-text strings are read as informational, never as failure.
        assert result["last_run_outcome"] == "completed"
        return
    assert result["last_run_outcome"] == expected


def test_hold_exception_is_visible_in_run_diagnostics(tmp_path):
    state = tmp_path / "state"
    _home_receipt(state, "run.json",
                  exceptions=[{"type": "input_budget_exceeded", "work_id": "work:example"}])
    result = _home_status(state, tmp_path)
    assert result["last_run_outcome"] == "completed"
    assert "operator action" in (result["diagnostic"] or "")


def _job_dir(root: Path, job_id: str = "k2b-automatic-memory-home") -> Path:
    job = root / job_id
    job.mkdir(parents=True, exist_ok=True)
    return job


def _write_toml(job: Path, text: str) -> None:
    (job / "automation.toml").write_text(text, encoding="utf-8")


def _receipt_dir(state: Path) -> Path:
    receipt_dir = state / "native-run-receipts"
    receipt_dir.mkdir(parents=True, exist_ok=True)
    return receipt_dir


def _home_receipt(
    state: Path,
    name: str,
    *,
    task_key: str = "task_id",
    writer_role: str = "home",
    job_id: str = "k2b-automatic-memory-home",
    started_at: str = "2026-09-13T22:34:18Z",
    finished_at: str = "2026-09-13T22:36:57Z",
    commands: object | None = None,
    exceptions: object | None = None,
    user_action_required: object = False,
    publication: object = _UNSET,
) -> Path:
    if commands is None:
        commands = {
            "memory_worklist": {"status": "ready", "selected": 1},
            "memory_record_extraction": {"status": "reviewed_empty", "items": 0},
            "memory_home_drain": {
                "status": "reconciled",
                "reconciled": 0,
                "local_failed": 0,
                "sjm_failed": 0,
            },
            "memory_publish": {
                "status": "not_attempted",
                "reason": "no_new_reconciliation_or_pending_publication",
            },
        }
    data: dict = {
        "schema_version": 1,
        task_key: job_id,
        "writer_role": writer_role,
        "started_at": started_at,
        "finished_at": finished_at,
        "commands": commands,
        "exceptions": exceptions if exceptions is not None else [],
        "user_action_required": user_action_required,
    }
    if publication is not _UNSET:
        data["publication"] = publication
    path = _receipt_dir(state) / name
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _sjm_receipt(
    state: Path,
    name: str,
    *,
    worklist_status: str = "idle",
    exception_counts: dict | None = None,
    started_at: str = "2026-09-13T22:30:22Z",
    finished_at: str = "2026-09-13T22:30:30Z",
) -> Path:
    data = {
        "schema_version": 1,
        "automation_id": "k2b-automatic-memory-sjm",
        "writer_role": "sjm-source-only",
        "started_at": started_at,
        "finished_at": finished_at,
        "hold_check": "absent",
        "command_outcomes": {
            "memory_worklist": {
                "status": worklist_status,
                "selected": 0,
                "exception_counts": exception_counts
                if exception_counts is not None
                else {"needs_attention": 0, "retry_backoff": 0},
            }
        },
        "exceptions": [],
        "hold_created": False,
        "reconciliation": "not_run_sjm",
        "publication": "not_run_sjm",
    }
    path = _receipt_dir(state) / name
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_active_is_not_completion(tmp_path: Path) -> None:
    root = tmp_path / "automations"
    job = root / "k2b-automatic-memory-home"
    job.mkdir(parents=True)
    (job / "automation.toml").write_text(
        'id="k2b-automatic-memory-home"\nstatus="ACTIVE"\n')
    result = native_job_status.read_native_job_status(writer_role="home", state_root=tmp_path / "state", automation_root=root)
    assert result["registration_state"] == "active"
    assert result["last_run_outcome"] == "unknown"
    assert result["last_completed_at"] is None
    assert result["extraction_hold"] == "absent"


def test_registration_uses_real_toml_keys(tmp_path: Path) -> None:
    root = tmp_path / "automations"
    job = _job_dir(root)
    _write_toml(
        job,
        'id="k2b-automatic-memory-home"\n'
        'status="PAUSED"\n'
        'model="gpt-5.6-sol"\n'
        'reasoning_effort="low"\n'
        'rrule="FREQ=HOURLY;INTERVAL=1;BYMINUTE=33;BYSECOND=0"\n',
    )
    result = native_job_status.read_native_job_status(
        writer_role="home", state_root=tmp_path / "state", automation_root=root
    )
    assert result["registration_state"] == "paused"
    assert result["configured_model"] == "gpt-5.6-sol"
    assert result["configured_reasoning"] == "low"
    assert result["schedule"] == "FREQ=HOURLY;INTERVAL=1;BYMINUTE=33;BYSECOND=0"


def test_registration_config_wrong_types_become_none(tmp_path: Path) -> None:
    root = tmp_path / "automations"
    job = _job_dir(root)
    _write_toml(
        job,
        'id="k2b-automatic-memory-home"\n'
        'status="ACTIVE"\n'
        'model=["gpt-5.6-sol"]\n'
        'reasoning_effort=3\n'
        'rrule=true\n',
    )
    result = native_job_status.read_native_job_status(
        writer_role="home", state_root=tmp_path / "state", automation_root=root
    )
    assert result["registration_state"] == "active"
    assert result["configured_model"] is None
    assert result["configured_reasoning"] is None
    assert result["schedule"] is None


def test_missing_registration(tmp_path: Path) -> None:
    result = native_job_status.read_native_job_status(
        writer_role="home",
        state_root=tmp_path / "state",
        automation_root=tmp_path / "automations",
    )
    assert result["registration_state"] == "missing"
    assert result["job_id"] == "k2b-automatic-memory-home"
    assert result["diagnostic"] is None


def test_malformed_toml_is_unknown_not_disabled(tmp_path: Path) -> None:
    root = tmp_path / "automations"
    job = _job_dir(root)
    _write_toml(job, "id = \nstatus = 'ACTIVE'\n[broken\n")
    result = native_job_status.read_native_job_status(
        writer_role="home", state_root=tmp_path / "state", automation_root=root
    )
    assert result["registration_state"] == "unknown"
    assert result["diagnostic"] is not None


def test_wrong_job_id_is_unknown(tmp_path: Path) -> None:
    root = tmp_path / "automations"
    job = _job_dir(root)
    _write_toml(job, 'id="some-other-job"\nstatus="ACTIVE"\n')
    result = native_job_status.read_native_job_status(
        writer_role="home", state_root=tmp_path / "state", automation_root=root
    )
    assert result["registration_state"] == "unknown"
    assert "id" in (result["diagnostic"] or "")


def test_invalid_status_value_is_unknown(tmp_path: Path) -> None:
    root = tmp_path / "automations"
    job = _job_dir(root)
    _write_toml(job, 'id="k2b-automatic-memory-home"\nstatus="RUNNING_HARD"\n')
    result = native_job_status.read_native_job_status(
        writer_role="home", state_root=tmp_path / "state", automation_root=root
    )
    assert result["registration_state"] == "unknown"
    assert "status" in (result["diagnostic"] or "")


def test_oversize_toml_is_unknown_not_truncated(tmp_path: Path) -> None:
    root = tmp_path / "automations"
    job = _job_dir(root)
    padding = "x" * (native_job_status.MAX_READ_BYTES + 1)
    _write_toml(
        job,
        f'id="k2b-automatic-memory-home"\nstatus="ACTIVE"\n# {padding}\n',
    )
    result = native_job_status.read_native_job_status(
        writer_role="home", state_root=tmp_path / "state", automation_root=root
    )
    assert result["registration_state"] == "unknown"
    assert "size" in (result["diagnostic"] or "")


@pytest.mark.parametrize("link_target_kind", ["inside", "outside", "dangling"])
def test_registration_symlinks_rejected(tmp_path: Path, link_target_kind: str) -> None:
    root = tmp_path / "automations"
    job = _job_dir(root)
    if link_target_kind == "inside":
        target = job / "real.toml"
        target.write_text(
            'id="k2b-automatic-memory-home"\nstatus="ACTIVE"\n', encoding="utf-8"
        )
        os.symlink(target, job / "automation.toml")
    elif link_target_kind == "outside":
        outside = tmp_path / "outside.toml"
        outside.write_text(
            'id="k2b-automatic-memory-home"\nstatus="ACTIVE"\n', encoding="utf-8"
        )
        os.symlink(outside, job / "automation.toml")
    else:
        os.symlink(tmp_path / "missing-parent" / "x.toml", job / "automation.toml")
    result = native_job_status.read_native_job_status(
        writer_role="home", state_root=tmp_path / "state", automation_root=root
    )
    assert result["registration_state"] == "unknown"
    assert "symlink" in (result["diagnostic"] or "")


def test_registration_parent_dir_symlink_rejected(tmp_path: Path) -> None:
    root = tmp_path / "automations"
    root.mkdir(parents=True)
    real_dir = tmp_path / "real-job"
    real_dir.mkdir()
    (real_dir / "automation.toml").write_text(
        'id="k2b-automatic-memory-home"\nstatus="ACTIVE"\n', encoding="utf-8"
    )
    os.symlink(real_dir, root / "k2b-automatic-memory-home")
    result = native_job_status.read_native_job_status(
        writer_role="home", state_root=tmp_path / "state", automation_root=root
    )
    assert result["registration_state"] == "unknown"
    assert "symlink" in (result["diagnostic"] or "")


def test_unknown_role_must_not_select_home(tmp_path: Path) -> None:
    with pytest.raises(PermissionError):
        native_job_status.read_native_job_status(
            writer_role="admin",
            state_root=tmp_path / "state",
            automation_root=tmp_path / "automations",
        )


def test_sjm_role_maps_only_sjm_job(tmp_path: Path) -> None:
    root = tmp_path / "automations"
    job = root / "k2b-automatic-memory-sjm"
    job.mkdir(parents=True)
    (job / "automation.toml").write_text(
        'id="k2b-automatic-memory-sjm"\nstatus="ACTIVE"\n', encoding="utf-8"
    )
    home_job = root / "k2b-automatic-memory-home"
    home_job.mkdir(parents=True)
    (home_job / "automation.toml").write_text(
        'id="k2b-automatic-memory-home"\nstatus="ACTIVE"\n', encoding="utf-8"
    )
    result = native_job_status.read_native_job_status(
        writer_role="sjm-source-only",
        state_root=tmp_path / "state",
        automation_root=root,
    )
    assert result["job_id"] == "k2b-automatic-memory-sjm"
    assert result["registration_state"] == "active"


def test_home_completed_empty_run_receipt(tmp_path: Path) -> None:
    root = tmp_path / "automations"
    _write_toml(
        _job_dir(root),
        'id="k2b-automatic-memory-home"\nstatus="ACTIVE"\n',
    )
    state = tmp_path / "state"
    _home_receipt(state, "run.json")
    result = native_job_status.read_native_job_status(
        writer_role="home", state_root=state, automation_root=root
    )
    assert result["last_run_outcome"] == "completed"
    assert result["last_completed_at"] == "2026-09-13T22:36:57+00:00"


def test_home_earlier_shape_with_native_task_id_and_exit_codes(tmp_path: Path) -> None:
    root = tmp_path / "automations"
    _write_toml(_job_dir(root), 'id="k2b-automatic-memory-home"\nstatus="ACTIVE"\n')
    state = tmp_path / "state"
    _home_receipt(
        state,
        "run.json",
        task_key="native_task_id",
        commands={
            "memory_worklist": {"status": "ready", "selected": 1, "exit_code": 0},
            "memory_record_extraction": {"status": "queued", "exit_code": 0},
            "memory_home_drain": {"status": "reconciled", "exit_code": 0},
            "memory_publish": {"status": "published", "exit_code": 0},
        },
    )
    result = native_job_status.read_native_job_status(
        writer_role="home", state_root=state, automation_root=root
    )
    assert result["last_run_outcome"] == "completed"


def test_sjm_completed_empty_run_receipt(tmp_path: Path) -> None:
    root = tmp_path / "automations"
    job = root / "k2b-automatic-memory-sjm"
    job.mkdir(parents=True)
    _write_toml(job, 'id="k2b-automatic-memory-sjm"\nstatus="ACTIVE"\n')
    state = tmp_path / "state"
    _sjm_receipt(state, "run.json")
    result = native_job_status.read_native_job_status(
        writer_role="sjm-source-only", state_root=state, automation_root=root
    )
    assert result["last_run_outcome"] == "completed"
    assert result["last_completed_at"] == "2026-09-13T22:30:30+00:00"


def test_explicit_command_failure_is_failed(tmp_path: Path) -> None:
    root = tmp_path / "automations"
    _write_toml(_job_dir(root), 'id="k2b-automatic-memory-home"\nstatus="ACTIVE"\n')
    state = tmp_path / "state"
    _home_receipt(
        state,
        "run-failed.json",
        commands={
            "memory_worklist": {"status": "ready", "selected": 1},
            "memory_record_extraction": {"status": "failed"},
        },
    )
    result = native_job_status.read_native_job_status(
        writer_role="home", state_root=state, automation_root=root
    )
    assert result["last_run_outcome"] == "failed"
    assert result["last_completed_at"] is None


def test_nonzero_failure_counts_are_failed(tmp_path: Path) -> None:
    root = tmp_path / "automations"
    _write_toml(_job_dir(root), 'id="k2b-automatic-memory-home"\nstatus="ACTIVE"\n')
    state = tmp_path / "state"
    _home_receipt(
        state,
        "run.json",
        commands={
            "memory_worklist": {"status": "ready", "selected": 1},
            "memory_record_extraction": {"status": "queued"},
            "memory_home_drain": {"status": "reconciled", "local_failed": 1},
        },
    )
    result = native_job_status.read_native_job_status(
        writer_role="home", state_root=state, automation_root=root
    )
    assert result["last_run_outcome"] == "failed"


def test_nonzero_exit_code_is_failed(tmp_path: Path) -> None:
    root = tmp_path / "automations"
    _write_toml(_job_dir(root), 'id="k2b-automatic-memory-home"\nstatus="ACTIVE"\n')
    state = tmp_path / "state"
    _home_receipt(
        state,
        "run.json",
        commands={
            "memory_worklist": {"status": "ready", "exit_code": 1},
        },
    )
    result = native_job_status.read_native_job_status(
        writer_role="home", state_root=state, automation_root=root
    )
    assert result["last_run_outcome"] == "failed"


def test_sjm_exception_counts_nonzero_are_failed(tmp_path: Path) -> None:
    root = tmp_path / "automations"
    job = root / "k2b-automatic-memory-sjm"
    job.mkdir(parents=True)
    _write_toml(job, 'id="k2b-automatic-memory-sjm"\nstatus="ACTIVE"\n')
    state = tmp_path / "state"
    _sjm_receipt(
        state,
        "run.json",
        exception_counts={"needs_attention": 1, "retry_backoff": 0},
    )
    result = native_job_status.read_native_job_status(
        writer_role="sjm-source-only", state_root=state, automation_root=root
    )
    assert result["last_run_outcome"] == "failed"


def test_unknown_command_status_is_unknown_not_success(tmp_path: Path) -> None:
    root = tmp_path / "automations"
    _write_toml(_job_dir(root), 'id="k2b-automatic-memory-home"\nstatus="ACTIVE"\n')
    state = tmp_path / "state"
    _home_receipt(
        state,
        "run.json",
        commands={"memory_worklist": {"status": "exploded"}},
    )
    result = native_job_status.read_native_job_status(
        writer_role="home", state_root=state, automation_root=root
    )
    assert result["last_run_outcome"] == "unknown"
    assert result["last_completed_at"] is None


def test_empty_command_map_never_proves_completion(tmp_path: Path) -> None:
    root = tmp_path / "automations"
    _write_toml(_job_dir(root), 'id="k2b-automatic-memory-home"\nstatus="ACTIVE"\n')
    state = tmp_path / "state"
    _home_receipt(state, "run.json", commands={})
    result = native_job_status.read_native_job_status(
        writer_role="home", state_root=state, automation_root=root
    )
    assert result["last_run_outcome"] == "unknown"


def test_started_at_alone_is_not_completion(tmp_path: Path) -> None:
    root = tmp_path / "automations"
    _write_toml(_job_dir(root), 'id="k2b-automatic-memory-home"\nstatus="ACTIVE"\n')
    state = tmp_path / "state"
    _home_receipt(state, "unfinished.json", finished_at="not-a-time")
    result = native_job_status.read_native_job_status(
        writer_role="home", state_root=state, automation_root=root
    )
    assert result["last_run_outcome"] == "unknown"
    assert result["last_completed_at"] is None


def test_failed_latest_does_not_surface_older_success_time(tmp_path: Path) -> None:
    root = tmp_path / "automations"
    _write_toml(_job_dir(root), 'id="k2b-automatic-memory-home"\nstatus="ACTIVE"\n')
    state = tmp_path / "state"
    older = _home_receipt(state, "older.json")
    newer = _home_receipt(
        state,
        "newer.json",
        started_at="2026-09-14T22:34:18Z",
        finished_at="2026-09-14T22:35:00Z",
        commands={"memory_worklist": {"status": "failed"}},
    )
    old_time = 1_700_000_000
    os.utime(older, (old_time, old_time))
    recent = newer.stat().st_mtime + 100
    os.utime(newer, (recent, recent))
    result = native_job_status.read_native_job_status(
        writer_role="home", state_root=state, automation_root=root
    )
    assert result["last_run_outcome"] == "failed"
    assert result["last_completed_at"] is None


def test_unfinished_latest_does_not_surface_older_success_time(tmp_path: Path) -> None:
    root = tmp_path / "automations"
    _write_toml(_job_dir(root), 'id="k2b-automatic-memory-home"\nstatus="ACTIVE"\n')
    state = tmp_path / "state"
    older = _home_receipt(state, "older.json")
    newer = _home_receipt(
        state,
        "newer.json",
        started_at="2026-09-14T22:34:18Z",
        finished_at="garbage",
    )
    old_time = 1_700_000_000
    os.utime(older, (old_time, old_time))
    recent = newer.stat().st_mtime + 100
    os.utime(newer, (recent, recent))
    result = native_job_status.read_native_job_status(
        writer_role="home", state_root=state, automation_root=root
    )
    assert result["last_run_outcome"] == "unknown"
    assert result["last_completed_at"] is None


def test_finished_before_started_is_invalid(tmp_path: Path) -> None:
    root = tmp_path / "automations"
    _write_toml(_job_dir(root), 'id="k2b-automatic-memory-home"\nstatus="ACTIVE"\n')
    state = tmp_path / "state"
    _home_receipt(
        state,
        "run.json",
        started_at="2026-09-13T22:40:00Z",
        finished_at="2026-09-13T22:36:57Z",
    )
    result = native_job_status.read_native_job_status(
        writer_role="home", state_root=state, automation_root=root
    )
    assert result["last_run_outcome"] == "unknown"
    assert "timestamp" in (result["diagnostic"] or "")


def test_newer_malformed_receipt_is_not_ignored(tmp_path: Path) -> None:
    root = tmp_path / "automations"
    _write_toml(_job_dir(root), 'id="k2b-automatic-memory-home"\nstatus="ACTIVE"\n')
    state = tmp_path / "state"
    older = _home_receipt(state, "aaa-valid.json")
    receipt_dir = state / "native-run-receipts"
    bad = receipt_dir / "zzz-malformed.json"
    bad.write_text("{ not json", encoding="utf-8")
    old_time = 1_700_000_000
    os.utime(older, (old_time, old_time))
    recent = bad.stat().st_mtime + 100
    os.utime(bad, (recent, recent))
    result = native_job_status.read_native_job_status(
        writer_role="home", state_root=state, automation_root=root
    )
    assert result["last_run_outcome"] == "unknown"
    assert result["diagnostic"] is not None
    assert result["last_completed_at"] is None


def test_wrong_role_receipt_does_not_fabricate_success(tmp_path: Path) -> None:
    root = tmp_path / "automations"
    _write_toml(_job_dir(root), 'id="k2b-automatic-memory-home"\nstatus="ACTIVE"\n')
    state = tmp_path / "state"
    _home_receipt(
        state,
        "run.json",
        writer_role="sjm-source-only",
        job_id="k2b-automatic-memory-sjm",
    )
    result = native_job_status.read_native_job_status(
        writer_role="home", state_root=state, automation_root=root
    )
    assert result["last_run_outcome"] == "unknown"


def test_recovered_exceptions_are_sanitized_diagnostic_not_failure(tmp_path: Path) -> None:
    root = tmp_path / "automations"
    _write_toml(_job_dir(root), 'id="k2b-automatic-memory-home"\nstatus="ACTIVE"\n')
    state = tmp_path / "state"
    _home_receipt(
        state,
        "run.json",
        exceptions=["Recovered initial rejection; source then reviewed-empty"],
    )
    result = native_job_status.read_native_job_status(
        writer_role="home", state_root=state, automation_root=root
    )
    assert result["last_run_outcome"] == "completed"
    assert result["diagnostic"] is not None
    assert "Recovered initial rejection" not in result["diagnostic"]


@pytest.mark.parametrize(
    "commands",
    [
        ["memory_worklist"],
        {"memory_worklist": ["status", "ready"]},
        {"memory_worklist": {"status": ["ready"]}},
        {"memory_worklist": {"status": "ready", "exit_code": "0"}},
    ],
)
def test_receipt_type_validation_never_raises(tmp_path: Path, commands: object) -> None:
    root = tmp_path / "automations"
    _write_toml(_job_dir(root), 'id="k2b-automatic-memory-home"\nstatus="ACTIVE"\n')
    state = tmp_path / "state"
    _home_receipt(state, "run.json", commands=commands)
    result = native_job_status.read_native_job_status(
        writer_role="home", state_root=state, automation_root=root
    )
    assert result["last_run_outcome"] == "unknown"


def test_receipt_wrong_exception_type_is_invalid(tmp_path: Path) -> None:
    root = tmp_path / "automations"
    _write_toml(_job_dir(root), 'id="k2b-automatic-memory-home"\nstatus="ACTIVE"\n')
    state = tmp_path / "state"
    _home_receipt(state, "run.json", exceptions="boom")
    result = native_job_status.read_native_job_status(
        writer_role="home", state_root=state, automation_root=root
    )
    assert result["last_run_outcome"] == "unknown"


def test_receipt_wrong_user_action_type_is_invalid(tmp_path: Path) -> None:
    root = tmp_path / "automations"
    _write_toml(_job_dir(root), 'id="k2b-automatic-memory-home"\nstatus="ACTIVE"\n')
    state = tmp_path / "state"
    _home_receipt(state, "run.json", user_action_required="yes")
    result = native_job_status.read_native_job_status(
        writer_role="home", state_root=state, automation_root=root
    )
    assert result["last_run_outcome"] == "unknown"


def test_user_action_required_flag_is_diagnostic(tmp_path: Path) -> None:
    root = tmp_path / "automations"
    _write_toml(_job_dir(root), 'id="k2b-automatic-memory-home"\nstatus="ACTIVE"\n')
    state = tmp_path / "state"
    _home_receipt(state, "run.json", user_action_required=True)
    result = native_job_status.read_native_job_status(
        writer_role="home", state_root=state, automation_root=root
    )
    assert result["last_run_outcome"] == "completed"
    assert "user action" in (result["diagnostic"] or "")


def test_receipt_selection_uses_mtime_not_lexical_name(tmp_path: Path) -> None:
    root = tmp_path / "automations"
    _write_toml(_job_dir(root), 'id="k2b-automatic-memory-home"\nstatus="ACTIVE"\n')
    state = tmp_path / "state"
    newest = _home_receipt(
        state,
        "aaa-newest.json",
        started_at="2026-09-14T10:00:00Z",
        finished_at="2026-09-14T10:01:00Z",
        commands={
            "memory_worklist": {"status": "ready", "selected": 1},
            "memory_record_extraction": {"status": "reviewed_empty"},
        },
    )
    older = _home_receipt(
        state,
        "zzz-older.json",
        commands={"memory_worklist": {"status": "failed"}},
    )
    old_time = 1_700_000_000
    os.utime(older, (old_time, old_time))
    recent = newest.stat().st_mtime + 100
    os.utime(newest, (recent, recent))
    result = native_job_status.read_native_job_status(
        writer_role="home", state_root=state, automation_root=root
    )
    assert result["last_run_outcome"] == "completed"
    assert result["last_completed_at"] == "2026-09-14T10:01:00+00:00"


# --- newest-run selection by validated receipt timestamps ---


@pytest.mark.parametrize("newest_mtime,older_mtime", [(100, 200), (200, 100)])
def test_newest_run_selected_by_receipt_timestamp_not_mtime(
    tmp_path: Path, newest_mtime: int, older_mtime: int
) -> None:
    root = tmp_path / "automations"
    _write_toml(_job_dir(root), 'id="k2b-automatic-memory-home"\nstatus="ACTIVE"\n')
    state = tmp_path / "state"
    older = _home_receipt(
        state,
        "aaa-older.json",
        started_at="2026-09-13T09:00:00Z",
        finished_at="2026-09-13T10:01:00Z",
        commands={"memory_worklist": {"status": "idle"}},
    )
    newest = _home_receipt(
        state,
        "zzz-newest.json",
        started_at="2026-09-13T10:00:00Z",
        finished_at="2026-09-13T11:01:00Z",
        commands={"memory_worklist": {"status": "idle"}},
    )
    os.utime(older, (older_mtime, older_mtime))
    os.utime(newest, (newest_mtime, newest_mtime))
    result = native_job_status.read_native_job_status(
        writer_role="home", state_root=state, automation_root=root
    )
    assert result["last_run_outcome"] == "completed"
    assert result["last_completed_at"] == "2026-09-13T11:01:00+00:00"


def test_timestamp_tie_resolves_to_worst_outcome(tmp_path: Path) -> None:
    root = tmp_path / "automations"
    _write_toml(_job_dir(root), 'id="k2b-automatic-memory-home"\nstatus="ACTIVE"\n')
    state = tmp_path / "state"
    _home_receipt(
        state,
        "a-completed.json",
        started_at="2026-09-13T10:00:00Z",
        finished_at="2026-09-13T10:01:00Z",
        commands={"memory_worklist": {"status": "idle"}},
    )
    _home_receipt(
        state,
        "b-failed.json",
        started_at="2026-09-13T10:00:00Z",
        finished_at="2026-09-13T10:01:00Z",
        commands={"memory_worklist": {"status": "failed"}},
    )
    result = native_job_status.read_native_job_status(
        writer_role="home", state_root=state, automation_root=root
    )
    assert result["last_run_outcome"] == "failed"
    assert result["last_completed_at"] is None


def test_incomplete_newer_attempt_stays_unknown_despite_older_mtime(
    tmp_path: Path,
) -> None:
    root = tmp_path / "automations"
    _write_toml(_job_dir(root), 'id="k2b-automatic-memory-home"\nstatus="ACTIVE"\n')
    state = tmp_path / "state"
    older = _home_receipt(
        state,
        "aaa-older.json",
        started_at="2026-09-13T09:00:00Z",
        finished_at="2026-09-13T10:01:00Z",
        commands={"memory_worklist": {"status": "idle"}},
    )
    newer = _home_receipt(
        state,
        "zzz-unfinished.json",
        started_at="2026-09-13T10:05:00Z",
        finished_at="garbage",
    )
    os.utime(older, (200, 200))
    os.utime(newer, (100, 100))
    result = native_job_status.read_native_job_status(
        writer_role="home", state_root=state, automation_root=root
    )
    assert result["last_run_outcome"] == "unknown"
    assert result["last_completed_at"] is None


def test_newer_invalid_receipt_by_content_blocks_older_success(
    tmp_path: Path,
) -> None:
    root = tmp_path / "automations"
    _write_toml(_job_dir(root), 'id="k2b-automatic-memory-home"\nstatus="ACTIVE"\n')
    state = tmp_path / "state"
    _home_receipt(
        state,
        "aaa-valid.json",
        started_at="2026-09-13T09:00:00Z",
        finished_at="2026-09-13T10:01:00Z",
        commands={"memory_worklist": {"status": "idle"}},
    )
    bad = state / "native-run-receipts" / "zzz-invalid.json"
    bad.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "task_id": "k2b-automatic-memory-home",
                "writer_role": "home",
                "started_at": "2026-09-13T11:00:00Z",
                "finished_at": "2026-09-13T11:01:00Z",
            }
        ),
        encoding="utf-8",
    )
    old_time = 1_700_000_000
    os.utime(state / "native-run-receipts" / "aaa-valid.json", (old_time, old_time))
    recent = bad.stat().st_mtime + 100
    os.utime(bad, (recent, recent))
    result = native_job_status.read_native_job_status(
        writer_role="home", state_root=state, automation_root=root
    )
    assert result["last_run_outcome"] == "unknown"
    assert result["last_completed_at"] is None
    assert result["diagnostic"] is not None


# --- role-specific publication metadata ---


def _realistic_home_receipt(state: Path, name: str, publication: object) -> Path:
    data = {
        "schema_version": 1,
        "native_task_id": "k2b-automatic-memory-home",
        "writer_role": "home",
        "started_at": "2026-09-13T10:00:00Z",
        "finished_at": "2026-09-13T10:01:00Z",
        "commands": {
            "memory_worklist": {"status": "ready", "selected": 1},
            "memory_record_extraction": {"status": "queued"},
            "memory_home_drain": {"status": "reconciled", "local_failed": 0},
            "memory_publish": {"status": "published"},
        },
        "exceptions": [],
        "hold": {"status": "absent"},
        "publication": publication,
    }
    path = _receipt_dir(state) / name
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_home_publication_object_is_accepted(tmp_path: Path) -> None:
    root = tmp_path / "automations"
    _write_toml(_job_dir(root), 'id="k2b-automatic-memory-home"\nstatus="ACTIVE"\n')
    state = tmp_path / "state"
    _realistic_home_receipt(
        state,
        "run.json",
        {"status": "published", "published_at": "2026-09-13T10:01:00Z"},
    )
    result = native_job_status.read_native_job_status(
        writer_role="home", state_root=state, automation_root=root
    )
    assert result["last_run_outcome"] == "completed"
    assert result["last_completed_at"] == "2026-09-13T10:01:00+00:00"


def test_home_publication_wrong_shape_is_malformed(tmp_path: Path) -> None:
    root = tmp_path / "automations"
    _write_toml(_job_dir(root), 'id="k2b-automatic-memory-home"\nstatus="ACTIVE"\n')
    state = tmp_path / "state"
    _realistic_home_receipt(state, "run.json", "published")
    result = native_job_status.read_native_job_status(
        writer_role="home", state_root=state, automation_root=root
    )
    assert result["last_run_outcome"] == "unknown"
    assert "publication" in (result["diagnostic"] or "")


def test_home_publication_bare_published_status_is_completed(tmp_path: Path) -> None:
    root = tmp_path / "automations"
    _write_toml(_job_dir(root), 'id="k2b-automatic-memory-home"\nstatus="ACTIVE"\n')
    state = tmp_path / "state"
    _realistic_home_receipt(state, "run.json", {"status": "published"})
    result = native_job_status.read_native_job_status(
        writer_role="home", state_root=state, automation_root=root
    )
    assert result["last_run_outcome"] == "completed"
    assert result["last_completed_at"] == "2026-09-13T10:01:00+00:00"


def test_home_publication_snapshot_sha256_without_published_at_is_completed(
    tmp_path: Path,
) -> None:
    # The actual Home shape: snapshot hash plus the top-level finished_at is
    # success; a redundant published_at must not be required.
    root = tmp_path / "automations"
    _write_toml(_job_dir(root), 'id="k2b-automatic-memory-home"\nstatus="ACTIVE"\n')
    state = tmp_path / "state"
    _realistic_home_receipt(
        state,
        "run.json",
        {"status": "published", "snapshot_sha256": "ab" * 32},
    )
    result = native_job_status.read_native_job_status(
        writer_role="home", state_root=state, automation_root=root
    )
    assert result["last_run_outcome"] == "completed"
    assert result["last_completed_at"] == "2026-09-13T10:01:00+00:00"


def test_home_publication_not_attempted_object_is_completed(tmp_path: Path) -> None:
    root = tmp_path / "automations"
    _write_toml(_job_dir(root), 'id="k2b-automatic-memory-home"\nstatus="ACTIVE"\n')
    state = tmp_path / "state"
    _home_receipt(
        state,
        "run.json",
        commands={
            "memory_worklist": {"status": "idle"},
            "memory_publish": {"status": "not_attempted"},
        },
        publication={"status": "not_attempted"},
    )
    result = native_job_status.read_native_job_status(
        writer_role="home", state_root=state, automation_root=root
    )
    assert result["last_run_outcome"] == "completed"


def test_home_publication_failed_never_reports_completed(tmp_path: Path) -> None:
    root = tmp_path / "automations"
    _write_toml(_job_dir(root), 'id="k2b-automatic-memory-home"\nstatus="ACTIVE"\n')
    state = tmp_path / "state"
    _home_receipt(
        state,
        "run.json",
        started_at="2026-09-13T17:34:53Z",
        finished_at="2026-09-13T17:35:57Z",
        commands={
            "memory_worklist": {"status": "idle"},
            "memory_publish": {"status": "not_attempted"},
        },
        publication={"status": "failed"},
    )
    result = native_job_status.read_native_job_status(
        writer_role="home", state_root=state, automation_root=root
    )
    assert result["last_run_outcome"] == "failed"
    assert result["last_completed_at"] is None
    assert "publication" in (result["diagnostic"] or "")


@pytest.mark.parametrize(
    "publication",
    [
        {"status": "pending"},
        {"status": ""},
        None,
        "published",
        {"status": "published", "published_at": "garbage"},
        {"status": "published", "snapshot_sha256": ["ab"]},
        {"status": ["published"]},
    ],
    ids=[
        "unsupported-status",
        "empty-status",
        "null",
        "wrong-type-string",
        "malformed-published_at",
        "wrong-type-snapshot",
        "list-status",
    ],
)
def test_home_publication_unusable_metadata_is_malformed(
    tmp_path: Path, publication: object
) -> None:
    root = tmp_path / "automations"
    _write_toml(_job_dir(root), 'id="k2b-automatic-memory-home"\nstatus="ACTIVE"\n')
    state = tmp_path / "state"
    _home_receipt(state, "run.json", publication=publication)
    result = native_job_status.read_native_job_status(
        writer_role="home", state_root=state, automation_root=root
    )
    assert result["last_run_outcome"] == "unknown"
    assert result["last_completed_at"] is None
    assert "publication" in (result["diagnostic"] or "")


def test_home_publication_missing_key_remains_compatible(tmp_path: Path) -> None:
    root = tmp_path / "automations"
    _write_toml(_job_dir(root), 'id="k2b-automatic-memory-home"\nstatus="ACTIVE"\n')
    state = tmp_path / "state"
    _home_receipt(state, "run.json")
    result = native_job_status.read_native_job_status(
        writer_role="home", state_root=state, automation_root=root
    )
    assert result["last_run_outcome"] == "completed"


def test_sjm_publication_unknown_string_is_malformed(tmp_path: Path) -> None:
    root = tmp_path / "automations"
    job = root / "k2b-automatic-memory-sjm"
    job.mkdir(parents=True)
    _write_toml(job, 'id="k2b-automatic-memory-sjm"\nstatus="ACTIVE"\n')
    state = tmp_path / "state"
    path = _sjm_receipt(state, "run.json")
    data = json.loads(path.read_text(encoding="utf-8"))
    data["publication"] = "published"
    path.write_text(json.dumps(data), encoding="utf-8")
    result = native_job_status.read_native_job_status(
        writer_role="sjm-source-only", state_root=state, automation_root=root
    )
    assert result["last_run_outcome"] == "unknown"
    assert "publication" in (result["diagnostic"] or "")


def test_unknown_command_dict_status_does_not_raise(tmp_path: Path) -> None:
    root = tmp_path / "automations"
    _write_toml(_job_dir(root), 'id="k2b-automatic-memory-home"\nstatus="ACTIVE"\n')
    state = tmp_path / "state"
    _home_receipt(
        state,
        "run.json",
        commands={
            "memory_worklist": {"status": "idle"},
            "python_version": {"status": {"weird": True}},
        },
    )
    result = native_job_status.read_native_job_status(
        writer_role="home", state_root=state, automation_root=root
    )
    assert result["last_run_outcome"] == "completed"


def test_sjm_publication_object_is_malformed(tmp_path: Path) -> None:
    root = tmp_path / "automations"
    job = root / "k2b-automatic-memory-sjm"
    job.mkdir(parents=True)
    _write_toml(job, 'id="k2b-automatic-memory-sjm"\nstatus="ACTIVE"\n')
    state = tmp_path / "state"
    path = _sjm_receipt(state, "run.json")
    data = json.loads(path.read_text(encoding="utf-8"))
    data["publication"] = {"status": "published", "published_at": "2026-09-13T10:01:00Z"}
    path.write_text(json.dumps(data), encoding="utf-8")
    result = native_job_status.read_native_job_status(
        writer_role="sjm-source-only", state_root=state, automation_root=root
    )
    assert result["last_run_outcome"] == "unknown"
    assert "publication" in (result["diagnostic"] or "")


def test_home_publication_conflicting_with_commands_is_unknown(tmp_path: Path) -> None:
    root = tmp_path / "automations"
    _write_toml(_job_dir(root), 'id="k2b-automatic-memory-home"\nstatus="ACTIVE"\n')
    state = tmp_path / "state"
    path = _realistic_home_receipt(
        state,
        "run.json",
        {"status": "published", "published_at": "2026-09-13T10:01:00Z"},
    )
    data = json.loads(path.read_text(encoding="utf-8"))
    data["commands"]["memory_publish"] = {"status": "not_attempted"}
    path.write_text(json.dumps(data), encoding="utf-8")
    result = native_job_status.read_native_job_status(
        writer_role="home", state_root=state, automation_root=root
    )
    assert result["last_run_outcome"] == "unknown"
    assert result["last_completed_at"] is None
    assert "conflict" in (result["diagnostic"] or "")


# --- failure evidence outside the success whitelist ---


def test_extraction_input_failure_is_failed_not_completed(tmp_path: Path) -> None:
    root = tmp_path / "automations"
    _write_toml(_job_dir(root), 'id="k2b-automatic-memory-home"\nstatus="ACTIVE"\n')
    state = tmp_path / "state"
    _home_receipt(
        state,
        "run.json",
        started_at="2026-09-13T10:00:00Z",
        finished_at="2026-09-13T10:01:00Z",
        commands={
            "memory_worklist": {"status": "ready"},
            "memory_extraction_input": {"status": "failed", "exit_code": 1},
        },
    )
    result = native_job_status.read_native_job_status(
        writer_role="home", state_root=state, automation_root=root
    )
    assert result["last_run_outcome"] == "failed"
    assert result["last_completed_at"] is None


def test_unknown_command_nonzero_exit_is_failed(tmp_path: Path) -> None:
    root = tmp_path / "automations"
    _write_toml(_job_dir(root), 'id="k2b-automatic-memory-home"\nstatus="ACTIVE"\n')
    state = tmp_path / "state"
    _home_receipt(
        state,
        "run.json",
        commands={
            "memory_worklist": {"status": "idle"},
            "python_version": {"exit_code": 1},
        },
    )
    result = native_job_status.read_native_job_status(
        writer_role="home", state_root=state, automation_root=root
    )
    assert result["last_run_outcome"] == "failed"


def test_unknown_command_failure_count_is_failed(tmp_path: Path) -> None:
    root = tmp_path / "automations"
    _write_toml(_job_dir(root), 'id="k2b-automatic-memory-home"\nstatus="ACTIVE"\n')
    state = tmp_path / "state"
    _home_receipt(
        state,
        "run.json",
        commands={
            "memory_worklist": {"status": "idle"},
            "memory_extraction_input": {"status": "completed", "failed": 1},
        },
    )
    result = native_job_status.read_native_job_status(
        writer_role="home", state_root=state, automation_root=root
    )
    assert result["last_run_outcome"] == "failed"


def test_benign_unknown_command_never_blocks_completion(tmp_path: Path) -> None:
    root = tmp_path / "automations"
    _write_toml(_job_dir(root), 'id="k2b-automatic-memory-home"\nstatus="ACTIVE"\n')
    state = tmp_path / "state"
    _home_receipt(
        state,
        "run.json",
        commands={
            "memory_worklist": {"status": "idle"},
            "python_version": {"status": "checked", "version": "3.12.0"},
        },
    )
    result = native_job_status.read_native_job_status(
        writer_role="home", state_root=state, automation_root=root
    )
    assert result["last_run_outcome"] == "completed"


@pytest.mark.parametrize("extra", [None, "3.12.0", {"status": "weird"}])
def test_unknown_command_null_scalar_unknown_status_do_not_fail(
    tmp_path: Path, extra: object
) -> None:
    root = tmp_path / "automations"
    _write_toml(_job_dir(root), 'id="k2b-automatic-memory-home"\nstatus="ACTIVE"\n')
    state = tmp_path / "state"
    _home_receipt(
        state,
        "run.json",
        commands={
            "memory_worklist": {"status": "idle"},
            "python_version": extra,
        },
    )
    result = native_job_status.read_native_job_status(
        writer_role="home", state_root=state, automation_root=root
    )
    assert result["last_run_outcome"] == "completed"


def test_ready_worklist_alone_is_not_completion(tmp_path: Path) -> None:
    root = tmp_path / "automations"
    _write_toml(_job_dir(root), 'id="k2b-automatic-memory-home"\nstatus="ACTIVE"\n')
    state = tmp_path / "state"
    _home_receipt(
        state,
        "run.json",
        commands={"memory_worklist": {"status": "ready", "selected": 1}},
    )
    result = native_job_status.read_native_job_status(
        writer_role="home", state_root=state, automation_root=root
    )
    assert result["last_run_outcome"] == "unknown"
    assert result["last_completed_at"] is None


def test_receipt_symlink_rejected_even_same_root(tmp_path: Path) -> None:
    root = tmp_path / "automations"
    _write_toml(_job_dir(root), 'id="k2b-automatic-memory-home"\nstatus="ACTIVE"\n')
    state = tmp_path / "state"
    real = _home_receipt(state, "real.json")
    os.symlink(real, state / "native-run-receipts" / "linked.json")
    result = native_job_status.read_native_job_status(
        writer_role="home", state_root=state, automation_root=root
    )
    assert result["last_run_outcome"] == "unknown"


def test_receipt_fifo_rejected_without_blocking(tmp_path: Path) -> None:
    root = tmp_path / "automations"
    _write_toml(_job_dir(root), 'id="k2b-automatic-memory-home"\nstatus="ACTIVE"\n')
    state = tmp_path / "state"
    receipt_dir = _receipt_dir(state)
    _home_receipt(state, "valid.json")
    fifo = receipt_dir / "fifo.json"
    os.mkfifo(fifo)
    result = native_job_status.read_native_job_status(
        writer_role="home", state_root=state, automation_root=root
    )
    # The fifo is either rejected safely or newer-by-mtime and invalid; in
    # both cases no completion may be fabricated.
    assert result["last_run_outcome"] != "completed"


def test_oversize_receipt_rejected_not_truncated(tmp_path: Path) -> None:
    root = tmp_path / "automations"
    _write_toml(_job_dir(root), 'id="k2b-automatic-memory-home"\nstatus="ACTIVE"\n')
    state = tmp_path / "state"
    receipt_dir = _receipt_dir(state)
    padding = "x" * (native_job_status.MAX_READ_BYTES + 1)
    path = receipt_dir / "big.json"
    path.write_text(
        '{"schema_version": 1, "task_id": "k2b-automatic-memory-home", '
        f'"writer_role": "home", "pad": "{padding}"}}',
        encoding="utf-8",
    )
    result = native_job_status.read_native_job_status(
        writer_role="home", state_root=state, automation_root=root
    )
    assert result["last_run_outcome"] == "unknown"


@pytest.mark.parametrize("count", [257, 1000])
def test_many_receipts_scan_finds_newest_by_validated_timestamp(
    tmp_path: Path, count: int
) -> None:
    root = tmp_path / "automations"
    _write_toml(_job_dir(root), 'id="k2b-automatic-memory-home"\nstatus="ACTIVE"\n')
    state = tmp_path / "state"
    receipt_dir = _receipt_dir(state)
    base = datetime(2026, 9, 13, 0, 0, 0, tzinfo=timezone.utc)
    for index in range(count):
        started = base + timedelta(minutes=2 * index)
        finished = base + timedelta(minutes=2 * index + 1)
        _home_receipt(
            state,
            f"run-{index:04d}.json",
            started_at=started.strftime("%Y-%m-%dT%H:%M:%SZ"),
            finished_at=finished.strftime("%Y-%m-%dT%H:%M:%SZ"),
            commands={"memory_worklist": {"status": "idle"}},
        )
    # The true newest evidence sorts before the others by name, so name order
    # cannot be what selects it; only its validated receipt timestamp can.
    newest_finished = base + timedelta(minutes=2 * count + 1)
    newest_started = base + timedelta(minutes=2 * count)
    _home_receipt(
        state,
        "aaa-newest.json",
        started_at=newest_started.strftime("%Y-%m-%dT%H:%M:%SZ"),
        finished_at=newest_finished.strftime("%Y-%m-%dT%H:%M:%SZ"),
        commands={"memory_worklist": {"status": "idle"}},
    )
    # Unrelated directory entries must not change the answer.
    (receipt_dir / "notes.txt").write_text("not a receipt", encoding="utf-8")
    (receipt_dir / ".DS_Store").write_text("junk", encoding="utf-8")
    (receipt_dir / "run-0000.json.bak").write_text("{}", encoding="utf-8")
    (receipt_dir / "archive").mkdir()
    old_time = 1_700_000_000
    for path in receipt_dir.iterdir():
        if path.is_file():
            os.utime(path, (old_time, old_time))
    result = native_job_status.read_native_job_status(
        writer_role="home", state_root=state, automation_root=root
    )
    assert result["last_run_outcome"] == "completed"
    assert result["last_completed_at"] == newest_finished.isoformat()


def test_receipt_scan_directory_error_is_unknown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "automations"
    _write_toml(_job_dir(root), 'id="k2b-automatic-memory-home"\nstatus="ACTIVE"\n')
    state = tmp_path / "state"
    _home_receipt(state, "run.json")

    def broken_scandir(path: object) -> object:
        raise OSError(2, "directory vanished")

    monkeypatch.setattr(native_job_status.os, "scandir", broken_scandir)
    result = native_job_status.read_native_job_status(
        writer_role="home", state_root=state, automation_root=root
    )
    assert result["last_run_outcome"] == "unknown"
    assert "not readable" in (result["diagnostic"] or "")


def test_newest_malformed_receipt_still_blocks_at_scale(tmp_path: Path) -> None:
    root = tmp_path / "automations"
    _write_toml(_job_dir(root), 'id="k2b-automatic-memory-home"\nstatus="ACTIVE"\n')
    state = tmp_path / "state"
    receipt_dir = _receipt_dir(state)
    for index in range(300):
        _home_receipt(
            state,
            f"run-{index:04d}.json",
            commands={"memory_worklist": {"status": "idle"}},
        )
    old_time = 1_700_000_000
    for path in receipt_dir.iterdir():
        os.utime(path, (old_time, old_time))
    bad = receipt_dir / "zzz-malformed.json"
    bad.write_text("{ not json", encoding="utf-8")
    # 1_800_000_000 (~2027) is newer than any validated receipt timestamp
    # (2026-09-13), so the malformed file must block the older success.
    os.utime(bad, (1_800_000_000, 1_800_000_000))
    result = native_job_status.read_native_job_status(
        writer_role="home", state_root=state, automation_root=root
    )
    assert result["last_run_outcome"] == "unknown"
    assert result["last_completed_at"] is None
    assert result["diagnostic"] is not None


def test_disappearing_receipt_does_not_crash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "automations"
    _write_toml(_job_dir(root), 'id="k2b-automatic-memory-home"\nstatus="ACTIVE"\n')
    state = tmp_path / "state"
    _home_receipt(state, "run.json")
    real_lstat = os.lstat

    def flaky_lstat(path: object) -> os.stat_result:
        result = real_lstat(path)
        if str(path).endswith("run.json"):
            raise FileNotFoundError(2, "gone", str(path))
        return result

    monkeypatch.setattr(native_job_status.os, "lstat", flaky_lstat)
    result = native_job_status.read_native_job_status(
        writer_role="home", state_root=state, automation_root=root
    )
    assert result["last_run_outcome"] == "unknown"


def test_active_hold_is_present_not_failure(tmp_path: Path) -> None:
    root = tmp_path / "automations"
    _write_toml(_job_dir(root), 'id="k2b-automatic-memory-home"\nstatus="ACTIVE"\n')
    state = tmp_path / "state"
    state.mkdir(parents=True)
    (state / "extraction-hold.json").write_text(
        json.dumps(
            {
                "created_at": "2026-09-13T14:00:00+00:00",
                "reason": "quota exhausted",
                "work_ids": ["work:abc"],
            }
        ),
        encoding="utf-8",
    )
    result = native_job_status.read_native_job_status(
        writer_role="home", state_root=state, automation_root=root
    )
    assert result["extraction_hold"] == "present"
    assert result["last_run_outcome"] == "unknown"


def test_malformed_hold_is_unknown(tmp_path: Path) -> None:
    root = tmp_path / "automations"
    _write_toml(_job_dir(root), 'id="k2b-automatic-memory-home"\nstatus="ACTIVE"\n')
    state = tmp_path / "state"
    state.mkdir(parents=True)
    (state / "extraction-hold.json").write_text("{ broken", encoding="utf-8")
    result = native_job_status.read_native_job_status(
        writer_role="home", state_root=state, automation_root=root
    )
    assert result["extraction_hold"] == "unknown"
    assert result["diagnostic"] is not None


def test_hold_symlink_rejected(tmp_path: Path) -> None:
    root = tmp_path / "automations"
    _write_toml(_job_dir(root), 'id="k2b-automatic-memory-home"\nstatus="ACTIVE"\n')
    state = tmp_path / "state"
    state.mkdir(parents=True)
    real = tmp_path / "real-hold.json"
    real.write_text(
        json.dumps({"created_at": "2026-09-13T14:00:00+00:00"}), encoding="utf-8"
    )
    os.symlink(real, state / "extraction-hold.json")
    result = native_job_status.read_native_job_status(
        writer_role="home", state_root=state, automation_root=root
    )
    assert result["extraction_hold"] == "unknown"


def test_inputs_unchanged_byte_for_byte(tmp_path: Path) -> None:
    root = tmp_path / "automations"
    job = _job_dir(root)
    toml = 'id="k2b-automatic-memory-home"\nstatus="ACTIVE"\nmodel="gpt-5.6-sol"\n'
    _write_toml(job, toml)
    state = tmp_path / "state"
    _home_receipt(state, "run.json")
    (state / "extraction-hold.json").write_text(
        json.dumps({"created_at": "2026-09-13T14:00:00+00:00"}), encoding="utf-8"
    )
    paths = [
        job / "automation.toml",
        state / "native-run-receipts" / "run.json",
        state / "extraction-hold.json",
    ]
    before = {p: p.read_bytes() for p in paths}
    native_job_status.read_native_job_status(
        writer_role="home", state_root=state, automation_root=root
    )
    for p in paths:
        assert p.read_bytes() == before[p]


def test_never_returns_prompts_or_transcripts(tmp_path: Path) -> None:
    root = tmp_path / "automations"
    job = _job_dir(root)
    _write_toml(
        job,
        'id="k2b-automatic-memory-home"\n'
        'status="ACTIVE"\n'
        'model="gpt-5.6-sol"\n'
        'reasoning_effort="low"\n'
        'prompt="SECRET PROMPT"\n'
        'api_key="SECRET KEY"\n',
    )
    result = native_job_status.read_native_job_status(
        writer_role="home", state_root=tmp_path / "state", automation_root=root
    )
    assert result["configured_model"] == "gpt-5.6-sol"
    assert result["configured_reasoning"] == "low"
    blob = json.dumps(result)
    assert "SECRET" not in blob


def test_default_automation_root_is_real_codex_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake_home = tmp_path / "home"
    (fake_home / ".codex" / "automations" / "k2b-automatic-memory-home").mkdir(
        parents=True
    )
    (fake_home / ".codex" / "automations" / "k2b-automatic-memory-home" / "automation.toml").write_text(
        'id="k2b-automatic-memory-home"\nstatus="ACTIVE"\n', encoding="utf-8"
    )
    monkeypatch.setenv("HOME", str(fake_home))
    result = native_job_status.read_native_job_status(
        writer_role="home", state_root=tmp_path / "state"
    )
    assert result["registration_state"] == "active"


# --- deployed memory_worklist ready_with_exceptions shape ---


def _ready_with_exceptions_commands(
    *,
    exception_counts: object | None = None,
    parse_exception_counts: object | None = None,
) -> dict:
    worklist: dict = {"status": "ready_with_exceptions", "selected": 1}
    worklist["exception_counts"] = (
        exception_counts
        if exception_counts is not None
        else {"needs_attention": 0, "retry_backoff": 0}
    )
    if parse_exception_counts is not None:
        worklist["parse_exception_counts"] = parse_exception_counts
    return {
        "memory_worklist": worklist,
        "memory_record_extraction": {"status": "queued"},
        "memory_home_drain": {
            "status": "reconciled",
            "local_failed": 0,
            "sjm_failed": 0,
        },
        "memory_publish": {"status": "published"},
    }


def test_ready_with_exceptions_deployed_shape_is_completed(tmp_path: Path) -> None:
    root = tmp_path / "automations"
    _write_toml(_job_dir(root), 'id="k2b-automatic-memory-home"\nstatus="ACTIVE"\n')
    state = tmp_path / "state"
    _home_receipt(
        state,
        "run.json",
        commands=_ready_with_exceptions_commands(
            parse_exception_counts={"task_complete_turn_id_mismatch": 1},
        ),
    )
    result = native_job_status.read_native_job_status(
        writer_role="home", state_root=state, automation_root=root
    )
    # An informational parse exception is not extraction failure.
    assert result["last_run_outcome"] == "completed"
    assert result["last_completed_at"] == "2026-09-13T22:36:57+00:00"


@pytest.mark.parametrize(
    "exception_counts",
    [
        {"needs_attention": 1, "retry_backoff": 0},
        {"needs_attention": 0, "retry_backoff": 2},
        {"exhausted": 1},
        {"needs_attention": -1},
    ],
)
def test_ready_with_exceptions_positive_counts_remain_failed(
    tmp_path: Path, exception_counts: dict
) -> None:
    root = tmp_path / "automations"
    _write_toml(_job_dir(root), 'id="k2b-automatic-memory-home"\nstatus="ACTIVE"\n')
    state = tmp_path / "state"
    _home_receipt(
        state,
        "run.json",
        commands=_ready_with_exceptions_commands(exception_counts=exception_counts),
    )
    result = native_job_status.read_native_job_status(
        writer_role="home", state_root=state, automation_root=root
    )
    # The accepted status must never mask positive failure/action counts.
    assert result["last_run_outcome"] == "failed"
    assert result["last_completed_at"] is None


@pytest.mark.parametrize(
    "exception_counts",
    [
        "needs_attention:0",
        {"needs_attention": "1"},
        {"needs_attention": True},
        ["needs_attention"],
        3,
    ],
)
def test_ready_with_exceptions_malformed_counts_are_invalid(
    tmp_path: Path, exception_counts: object
) -> None:
    root = tmp_path / "automations"
    _write_toml(_job_dir(root), 'id="k2b-automatic-memory-home"\nstatus="ACTIVE"\n')
    state = tmp_path / "state"
    _home_receipt(
        state,
        "run.json",
        commands=_ready_with_exceptions_commands(exception_counts=exception_counts),
    )
    result = native_job_status.read_native_job_status(
        writer_role="home", state_root=state, automation_root=root
    )
    assert result["last_run_outcome"] == "unknown"


@pytest.mark.parametrize(
    "parse_exception_counts",
    [
        {"task_complete_turn_id_mismatch": 1},
        {"task_complete_turn_id_mismatch": 0},
        {"task_complete_turn_id_mismatch": 5, "other_parse_issue": 2},
    ],
)
def test_parse_exception_counts_are_informational_never_failure(
    tmp_path: Path, parse_exception_counts: dict
) -> None:
    root = tmp_path / "automations"
    _write_toml(_job_dir(root), 'id="k2b-automatic-memory-home"\nstatus="ACTIVE"\n')
    state = tmp_path / "state"
    _home_receipt(
        state,
        "run.json",
        commands=_ready_with_exceptions_commands(
            parse_exception_counts=parse_exception_counts
        ),
    )
    result = native_job_status.read_native_job_status(
        writer_role="home", state_root=state, automation_root=root
    )
    assert result["last_run_outcome"] == "completed"


@pytest.mark.parametrize(
    "parse_exception_counts",
    [
        "task_complete_turn_id_mismatch:1",
        {"task_complete_turn_id_mismatch": "1"},
        ["task_complete_turn_id_mismatch"],
    ],
)
def test_parse_exception_counts_malformed_are_invalid(
    tmp_path: Path, parse_exception_counts: object
) -> None:
    root = tmp_path / "automations"
    _write_toml(_job_dir(root), 'id="k2b-automatic-memory-home"\nstatus="ACTIVE"\n')
    state = tmp_path / "state"
    _home_receipt(
        state,
        "run.json",
        commands=_ready_with_exceptions_commands(
            parse_exception_counts=parse_exception_counts
        ),
    )
    result = native_job_status.read_native_job_status(
        writer_role="home", state_root=state, automation_root=root
    )
    assert result["last_run_outcome"] == "unknown"


def test_ready_with_exceptions_on_other_commands_is_not_success(tmp_path: Path) -> None:
    root = tmp_path / "automations"
    _write_toml(_job_dir(root), 'id="k2b-automatic-memory-home"\nstatus="ACTIVE"\n')
    state = tmp_path / "state"
    _home_receipt(
        state,
        "run.json",
        commands={"memory_publish": {"status": "ready_with_exceptions"}},
    )
    result = native_job_status.read_native_job_status(
        writer_role="home", state_root=state, automation_root=root
    )
    # The status is only meaningful for memory_worklist; elsewhere it is an
    # unknown status and can never prove completion.
    assert result["last_run_outcome"] == "unknown"


# --- bounded schema-v1 exception object shapes ---


def test_exceptions_resolved_object_after_retry_is_completed(tmp_path: Path) -> None:
    root = tmp_path / "automations"
    _write_toml(_job_dir(root), 'id="k2b-automatic-memory-home"\nstatus="ACTIVE"\n')
    state = tmp_path / "state"
    _home_receipt(
        state,
        "run.json",
        exceptions=[
            {
                "reason": "invalid_reviewed_extraction",
                "status": "resolved_with_reviewed_empty",
                "operator_action_required": False,
            }
        ],
    )
    result = native_job_status.read_native_job_status(
        writer_role="home", state_root=state, automation_root=root
    )
    assert result["last_run_outcome"] == "completed"
    assert "resolved" in (result["diagnostic"] or "")
    # Free text is validated for shape but never returned.
    assert "invalid_reviewed_extraction" not in json.dumps(result)


def test_exceptions_held_object_distinguishes_operator_action(tmp_path: Path) -> None:
    root = tmp_path / "automations"
    _write_toml(_job_dir(root), 'id="k2b-automatic-memory-home"\nstatus="ACTIVE"\n')
    state = tmp_path / "state"
    _home_receipt(
        state,
        "run.json",
        exceptions=[
            {
                "type": "input_budget_exceeded",
                "work_id": "work:fixture",
                "operator_action_required": True,
            }
        ],
    )
    result = native_job_status.read_native_job_status(
        writer_role="home", state_root=state, automation_root=root
    )
    assert result["last_run_outcome"] == "completed"
    assert "operator action" in (result["diagnostic"] or "")
    assert "input_budget_exceeded" not in json.dumps(result)
    assert "work:fixture" not in json.dumps(result)


def test_exceptions_resolve_then_hold_distinguish_both_diagnostic_paths(
    tmp_path: Path,
) -> None:
    root = tmp_path / "automations"
    _write_toml(_job_dir(root), 'id="k2b-automatic-memory-home"\nstatus="ACTIVE"\n')
    state = tmp_path / "state"
    _home_receipt(
        state,
        "run.json",
        exceptions=[
            {
                "reason": "invalid_reviewed_extraction",
                "status": "resolved_with_reviewed_empty",
                "operator_action_required": False,
            },
            {
                "type": "input_budget_exceeded",
                "work_id": "work:fixture",
                "operator_action_required": True,
            },
        ],
    )
    result = native_job_status.read_native_job_status(
        writer_role="home", state_root=state, automation_root=root
    )
    assert result["last_run_outcome"] == "completed"
    assert "resolved" in (result["diagnostic"] or "")
    assert "operator action" in (result["diagnostic"] or "")


@pytest.mark.parametrize(
    "exception",
    [
        {"reason": "invalid_reviewed_extraction", "status": "bogus"},
        {"reason": "invalid_reviewed_extraction"},
        {"status": "resolved_with_reviewed_empty"},
        {"reason": "", "status": "resolved_with_reviewed_empty"},
        {"reason": "x", "status": "resolved_with_reviewed_empty", "extra": 1},
        {
            "reason": "x",
            "status": "resolved_with_reviewed_empty",
            "operator_action_required": "yes",
        },
        {"type": "bogus_type", "work_id": "work:fixture"},
        {"type": "input_budget_exceeded"},
        {"type": "input_budget_exceeded", "work_id": ""},
        {"type": "input_budget_exceeded", "work_id": "work:fixture", "extra": 1},
        {"type": "input_budget_exceeded", "work_id": "work:fixture", "note": "x"},
        {},
        None,
        3,
        ["resolved_with_reviewed_empty"],
    ],
)
def test_exceptions_invalid_object_shapes_are_malformed(
    tmp_path: Path, exception: object
) -> None:
    root = tmp_path / "automations"
    _write_toml(_job_dir(root), 'id="k2b-automatic-memory-home"\nstatus="ACTIVE"\n')
    state = tmp_path / "state"
    _home_receipt(state, "run.json", exceptions=[exception])
    result = native_job_status.read_native_job_status(
        writer_role="home", state_root=state, automation_root=root
    )
    assert result["last_run_outcome"] == "unknown"
    assert "exceptions" in (result["diagnostic"] or "")


def test_exceptions_list_beyond_bound_is_malformed(tmp_path: Path) -> None:
    root = tmp_path / "automations"
    _write_toml(_job_dir(root), 'id="k2b-automatic-memory-home"\nstatus="ACTIVE"\n')
    state = tmp_path / "state"
    _home_receipt(
        state,
        "run.json",
        exceptions=["info"] * (native_job_status.MAX_EXCEPTION_ITEMS + 1),
    )
    result = native_job_status.read_native_job_status(
        writer_role="home", state_root=state, automation_root=root
    )
    assert result["last_run_outcome"] == "unknown"


def test_exceptions_empty_list_and_absence_still_complete(tmp_path: Path) -> None:
    root = tmp_path / "automations"
    _write_toml(_job_dir(root), 'id="k2b-automatic-memory-home"\nstatus="ACTIVE"\n')
    state = tmp_path / "state"
    path = _home_receipt(state, "run.json", exceptions=[])
    result = native_job_status.read_native_job_status(
        writer_role="home", state_root=state, automation_root=root
    )
    assert result["last_run_outcome"] == "completed"
    data = json.loads(path.read_text(encoding="utf-8"))
    del data["exceptions"]
    path.write_text(json.dumps(data), encoding="utf-8")
    result = native_job_status.read_native_job_status(
        writer_role="home", state_root=state, automation_root=root
    )
    assert result["last_run_outcome"] == "completed"


# --- reconciliation object form ---


def _receipt_with_reconciliation(state: Path, reconciliation: object) -> Path:
    data = {
        "schema_version": 1,
        "task_id": "k2b-automatic-memory-home",
        "writer_role": "home",
        "started_at": "2026-09-13T10:00:00Z",
        "finished_at": "2026-09-13T10:01:00Z",
        "commands": {"memory_worklist": {"status": "idle"}},
        "exceptions": [],
        "user_action_required": False,
        "reconciliation": reconciliation,
    }
    path = _receipt_dir(state) / "run.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_reconciliation_object_reconciled_is_completed(tmp_path: Path) -> None:
    root = tmp_path / "automations"
    _write_toml(_job_dir(root), 'id="k2b-automatic-memory-home"\nstatus="ACTIVE"\n')
    state = tmp_path / "state"
    _receipt_with_reconciliation(
        state, {"status": "reconciled", "new": 1, "replayed": 1}
    )
    result = native_job_status.read_native_job_status(
        writer_role="home", state_root=state, automation_root=root
    )
    assert result["last_run_outcome"] == "completed"


def test_reconciliation_object_failed_never_reports_completed(tmp_path: Path) -> None:
    root = tmp_path / "automations"
    _write_toml(_job_dir(root), 'id="k2b-automatic-memory-home"\nstatus="ACTIVE"\n')
    state = tmp_path / "state"
    _receipt_with_reconciliation(state, {"status": "failed", "new": 0, "replayed": 1})
    result = native_job_status.read_native_job_status(
        writer_role="home", state_root=state, automation_root=root
    )
    assert result["last_run_outcome"] == "failed"
    assert result["last_completed_at"] is None
    assert "reconciliation" in (result["diagnostic"] or "")


@pytest.mark.parametrize(
    "reconciliation",
    [
        {"status": "pending"},
        {"status": "reconciled", "new": "1"},
        {"status": "reconciled", "replayed": None},
        {"status": ["reconciled"]},
        {"status": "reconciled", "surprise": 1},
        {"status": "reconciled", "new": True},
        None,
        3,
        ["reconciled"],
    ],
)
def test_reconciliation_malformed_shapes_are_invalid(
    tmp_path: Path, reconciliation: object
) -> None:
    root = tmp_path / "automations"
    _write_toml(_job_dir(root), 'id="k2b-automatic-memory-home"\nstatus="ACTIVE"\n')
    state = tmp_path / "state"
    _receipt_with_reconciliation(state, reconciliation)
    result = native_job_status.read_native_job_status(
        writer_role="home", state_root=state, automation_root=root
    )
    assert result["last_run_outcome"] == "unknown"
    assert "reconciliation" in (result["diagnostic"] or "")


def test_reconciliation_wrong_role_string_is_unknown(tmp_path: Path) -> None:
    root = tmp_path / "automations"
    _write_toml(_job_dir(root), 'id="k2b-automatic-memory-home"\nstatus="ACTIVE"\n')
    state = tmp_path / "state"
    _receipt_with_reconciliation(state, "not_run_sjm")
    result = native_job_status.read_native_job_status(
        writer_role="home", state_root=state, automation_root=root
    )
    assert result["last_run_outcome"] == "unknown"


@pytest.mark.parametrize("value,want", [("failed", "failed"), ("bogus", "unknown"), ("", "unknown"), (None, "unknown"), ("reconciled", "completed")])
def test_direct_reconciliation_strings(tmp_path, value, want):
    _receipt_with_reconciliation(tmp_path, value)
    result = native_job_status.read_native_job_status(writer_role="home", state_root=tmp_path, automation_root=tmp_path / "jobs")
    assert result["last_run_outcome"] == want


@pytest.mark.parametrize("status,selected", [("ready", 1), ("ready_with_exceptions", 1), ("idle", 1), ("ready", None), ("ready", True), ("ready", -1), ("ready", "1")])
def test_direct_selected_work_requires_extraction(tmp_path, status, selected):
    _home_receipt(tmp_path, "run.json", commands={
        "memory_worklist": {"status": status, "selected": selected},
        "memory_home_drain": {"status": "reconciled"},
        "memory_publish": {"status": "not_attempted"},
    })
    result = native_job_status.read_native_job_status(writer_role="home", state_root=tmp_path, automation_root=tmp_path / "jobs")
    assert result["last_run_outcome"] == "unknown"


@pytest.mark.parametrize("timestamp", ["2026-09-13", "2026-09-13T22:36:57"])
@pytest.mark.parametrize("field", ["finished_at", "started_at"])
def test_direct_naive_timestamp_not_evidence(tmp_path, timestamp, field):
    _home_receipt(tmp_path, "run.json", **{field: timestamp})
    (tmp_path / "extraction-hold.json").write_text(json.dumps({"created_at": timestamp}))
    result = native_job_status.read_native_job_status(writer_role="home", state_root=tmp_path, automation_root=tmp_path / "jobs")
    assert result["last_run_outcome"] == "unknown"
    assert result["extraction_hold"] == "unknown"


@pytest.mark.parametrize("blocked", ["k2b-automatic-memory-home", "automation.toml", "extraction-hold.json", "native-run-receipts"])
@pytest.mark.parametrize("error", [PermissionError, OSError])
def test_direct_unreadable_is_not_absent(tmp_path, monkeypatch, blocked, error):
    root = tmp_path / "jobs"
    _write_toml(_job_dir(root), 'id="k2b-automatic-memory-home"\nstatus="ACTIVE"\n')
    real = os.lstat
    def denied(path, *args, **kwargs):
        if Path(path).name == blocked:
            raise error("injected unreadable path")
        return real(path, *args, **kwargs)
    monkeypatch.setattr(os, "lstat", denied)
    result = native_job_status.read_native_job_status(writer_role="home", state_root=tmp_path, automation_root=root)
    key = "extraction_hold" if blocked == "extraction-hold.json" else "last_run_outcome" if blocked == "native-run-receipts" else "registration_state"
    assert result[key] == "unknown"
    assert result["diagnostic"]


# --- lstat-first existence checks: unsafe evidence is unknown ---


@pytest.mark.parametrize("target_kind", ["valid", "dangling"])
def test_registration_job_dir_symlink_is_unknown_not_missing(
    tmp_path: Path, target_kind: str
) -> None:
    root = tmp_path / "automations"
    root.mkdir(parents=True)
    if target_kind == "valid":
        real_dir = tmp_path / "real-job-dir"
        real_dir.mkdir()
        (real_dir / "automation.toml").write_text(
            'id="k2b-automatic-memory-home"\nstatus="ACTIVE"\n', encoding="utf-8"
        )
        os.symlink(real_dir, root / "k2b-automatic-memory-home")
    else:
        os.symlink(tmp_path / "missing-parent" / "job", root / "k2b-automatic-memory-home")
    result = native_job_status.read_native_job_status(
        writer_role="home", state_root=tmp_path / "state", automation_root=root
    )
    assert result["registration_state"] == "unknown"
    assert "symlink" in (result["diagnostic"] or "")


def test_registration_job_dir_regular_file_is_unknown_not_missing(tmp_path: Path) -> None:
    root = tmp_path / "automations"
    root.mkdir(parents=True)
    (root / "k2b-automatic-memory-home").write_text("not a directory", encoding="utf-8")
    result = native_job_status.read_native_job_status(
        writer_role="home", state_root=tmp_path / "state", automation_root=root
    )
    assert result["registration_state"] == "unknown"
    assert "directory" in (result["diagnostic"] or "")


def test_registration_job_dir_missing_is_missing(tmp_path: Path) -> None:
    root = tmp_path / "automations"
    root.mkdir(parents=True)
    result = native_job_status.read_native_job_status(
        writer_role="home", state_root=tmp_path / "state", automation_root=root
    )
    assert result["registration_state"] == "missing"
    assert result["diagnostic"] is None


@pytest.mark.parametrize("target_kind", ["valid", "dangling"])
def test_hold_symlink_is_unknown_not_absent(tmp_path: Path, target_kind: str) -> None:
    root = tmp_path / "automations"
    _write_toml(_job_dir(root), 'id="k2b-automatic-memory-home"\nstatus="ACTIVE"\n')
    state = tmp_path / "state"
    state.mkdir(parents=True)
    if target_kind == "valid":
        real = tmp_path / "real-hold.json"
        real.write_text(
            json.dumps({"created_at": "2026-09-13T14:00:00+00:00"}), encoding="utf-8"
        )
        os.symlink(real, state / "extraction-hold.json")
    else:
        os.symlink(
            tmp_path / "missing-parent" / "hold.json", state / "extraction-hold.json"
        )
    result = native_job_status.read_native_job_status(
        writer_role="home", state_root=state, automation_root=root
    )
    assert result["extraction_hold"] == "unknown"


def test_hold_directory_is_unknown_not_absent(tmp_path: Path) -> None:
    root = tmp_path / "automations"
    _write_toml(_job_dir(root), 'id="k2b-automatic-memory-home"\nstatus="ACTIVE"\n')
    state = tmp_path / "state"
    (state / "extraction-hold.json").mkdir(parents=True)
    result = native_job_status.read_native_job_status(
        writer_role="home", state_root=state, automation_root=root
    )
    assert result["extraction_hold"] == "unknown"


@pytest.mark.parametrize("target_kind", ["valid", "dangling"])
def test_receipt_dir_symlink_is_unknown_not_missing(
    tmp_path: Path, target_kind: str
) -> None:
    root = tmp_path / "automations"
    _write_toml(_job_dir(root), 'id="k2b-automatic-memory-home"\nstatus="ACTIVE"\n')
    state = tmp_path / "state"
    state.mkdir(parents=True)
    if target_kind == "valid":
        real_dir = tmp_path / "real-receipts"
        real_dir.mkdir()
        (real_dir / "run.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "task_id": "k2b-automatic-memory-home",
                    "writer_role": "home",
                    "started_at": "2026-09-13T10:00:00Z",
                    "finished_at": "2026-09-13T10:01:00Z",
                    "commands": {"memory_worklist": {"status": "idle"}},
                    "exceptions": [],
                    "user_action_required": False,
                }
            ),
            encoding="utf-8",
        )
        os.symlink(real_dir, state / "native-run-receipts")
    else:
        os.symlink(
            tmp_path / "missing-parent" / "receipts", state / "native-run-receipts"
        )
    result = native_job_status.read_native_job_status(
        writer_role="home", state_root=state, automation_root=root
    )
    assert result["last_run_outcome"] == "unknown"
    assert "symlink" in (result["diagnostic"] or "")
    assert result["last_completed_at"] is None


def test_receipt_dir_regular_file_is_unknown_not_missing(tmp_path: Path) -> None:
    root = tmp_path / "automations"
    _write_toml(_job_dir(root), 'id="k2b-automatic-memory-home"\nstatus="ACTIVE"\n')
    state = tmp_path / "state"
    state.mkdir(parents=True)
    (state / "native-run-receipts").write_text("not a directory", encoding="utf-8")
    result = native_job_status.read_native_job_status(
        writer_role="home", state_root=state, automation_root=root
    )
    assert result["last_run_outcome"] == "unknown"
    assert result["diagnostic"] is not None
    assert result["last_completed_at"] is None
