from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from importlib import util as importlib_util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "lib"))

import knowledge_status  # noqa: E402

NOW = "2026-10-25T03:00:00+00:00"


def _base_observations(**overrides):
    data = {
        "source_window": {"since": "2026-09-13", "through": "2026-10-25"},
        "scope": {"projects": ["K2B", "K2Bi"]},
        "source_coverage": {
            "observed_at": "2026-10-25T02:30:00+00:00",
            "complete": True,
            "file_backed": {
                "status": "ok",
                "pending_turns": 3,
                "extracted_turns": 40,
                "oldest_pending_at": "2026-10-22T08:00:00+08:00",
                "complete": True,
            },
            "sql_backed": {
                "status": "ok",
                "pending_turns": 1,
                "extracted_turns": 5,
                "oldest_pending_at": "2026-10-23T09:00:00+08:00",
                "complete": True,
                "changed_sources": 0,
            },
            "union": "unmeasured",
        },
        "delivery": {
            "status": "observed",
            "observed_at": "2026-10-25T02:45:00+00:00",
            "outbox": "ok",
            "pending_envelopes": 4,
            "oldest_pending_at": "2026-10-24T01:00:00+00:00",
        },
        "publication": {
            "status": "published",
            "observed_at": "2026-10-25T02:40:00+00:00",
            "published_at": "2026-10-25T01:30:00+00:00",
            "snapshot_sha256": "a" * 64,
            "watermark_size": 7,
        },
        "integration": {},
        "replication": {
            "status": "idle",
            "observed_at": "2026-10-25T02:50:00+00:00",
            "peers": [
                {"name": "sjm", "state": "idle",
                 "last_seen_at": "2026-10-25T02:49:00+00:00"},
            ],
        },
        "native_job": {
            "registration_state": "active",
            "native_schedule": "RRULE:FREQ=HOURLY",
            "native_job_id": "k2b-automatic-memory-home",
            "observed_dispatch": "unknown",
            "last_run_outcome": "completed",
            "last_completed_at": "2026-10-25T02:20:00+00:00",
            "extraction_hold": "absent",
            "observed_at": "2026-10-25T02:50:00+00:00",
        },
    }
    data.update(overrides)
    return data


def _cli_module():
    spec = importlib_util.spec_from_file_location(
        "k2b_knowledge_status_cli", ROOT / "scripts" / "k2b-knowledge-status.py"
    )
    module = importlib_util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_receipt_and_replication_reads_are_bounded(tmp_path, monkeypatch):
    import io
    cli = _cli_module()
    path = tmp_path / "receipt.json"
    path.write_text("{}")
    sizes = []
    class GrowingFile(io.BytesIO):
        def read(self, size=-1):
            assert size > 0, "unbounded read"
            sizes.append(size)
            return b"x" * size
    original_open = Path.open
    def growing_open(self, *args, **kwargs):
        if self == path:
            return GrowingFile()
        return original_open(self, *args, **kwargs)
    monkeypatch.setattr(Path, "open", growing_open)
    assert cli._read_receipt(path, tmp_path) == (None, cli.MAX_RECEIPT_BYTES + 1)
    assert cli._read_optional_json(path) is None
    assert sizes == [cli.MAX_RECEIPT_BYTES + 1] * 2


# --- summarize contract -------------------------------------------------


def test_overlapping_file_and_sql_inventories_are_not_summed():
    report = knowledge_status.summarize(_base_observations(), now=NOW)
    coverage = report["source_coverage"]
    assert coverage["file_backed"]["pending_turns"] == 3
    assert coverage["sql_backed"]["pending_turns"] == 1
    assert coverage.get("pending_turns") is None
    assert coverage["union"] == "unmeasured"
    assert any("no exact union" in line for line in report["limitations"])


def test_forged_exact_union_label_is_rejected():
    observations = _base_observations()
    observations["source_coverage"]["union"] = "exact"
    observations["source_coverage"]["pending_turns"] = 4
    report = knowledge_status.summarize(observations, now=NOW)
    coverage = report["source_coverage"]
    # A bare caller label must never be promoted to an exact union.
    assert coverage["union"] == "unmeasured"
    assert coverage.get("pending_turns") is None
    assert any("union label was not accepted" in line
               for line in report["limitations"])


def test_excluded_projects_and_metadata_only_and_sql_selection_reported():
    observations = _base_observations(scope={
        "projects": ["K2B"],
        "excluded_projects": ["K2Bi"],
    })
    report = knowledge_status.summarize(observations, now=NOW)
    scope = report["scope"]
    assert scope["projects"] == ["K2B"]
    assert scope["excluded_projects"] == ["K2Bi"]
    assert scope["metadata_only"] is True
    assert scope["sql_selection"] == "operator_only"
    assert report["source_window"]["since"] == "2026-09-13"
    assert report["source_window"]["through"] == "2026-10-25"


def test_out_of_scope_project_is_excluded_and_flagged():
    report = knowledge_status.summarize(
        _base_observations(scope={"projects": ["K2B", "Other"]}), now=NOW)
    assert report["scope"]["projects"] == ["K2B"]
    assert "Other" in report["scope"]["excluded_projects"]
    assert any("outside the approved scope" in line for line in report["limitations"])


def test_outbox_normalizer_uses_shared_deliverable_states():
    observations = _base_observations(delivery={
        "status": "observed",
        "observed_at": "2026-10-25T02:45:00+00:00",
        "outbox": "observed",
        "counts": {"pending": 1, "offline": 1, "failed": 1,
                   "exhausted": 1, "accepted": 5},
        "inspected": 9,
        "total_files": 9,
        "truncated": False,
    })
    report = knowledge_status.summarize(observations, now=NOW)
    assert report["delivery"]["pending_envelopes"] == 4
    # A missing deliverable state keeps the backlog unknown, never partial sums.
    observations["delivery"]["counts"] = {"pending": 1, "accepted": 5}
    report = knowledge_status.summarize(observations, now=NOW)
    assert report["delivery"]["pending_envelopes"] is None


def test_sql_only_pending_turn_surfaces_without_file_counts():
    observations = _base_observations()
    observations["source_coverage"]["file_backed"] = {
        "status": "ok", "pending_turns": 0, "extracted_turns": 40,
        "oldest_pending_at": None, "complete": True,
    }
    report = knowledge_status.summarize(observations, now=NOW)
    sql = report["source_coverage"]["sql_backed"]
    assert sql["pending_turns"] == 1
    assert sql["oldest_pending_at"] == "2026-10-23T09:00:00+08:00"
    assert report["source_coverage"]["file_backed"]["oldest_pending_at"] is None


def test_unavailable_attachments_reported_as_unavailable_turns():
    observations = _base_observations()
    observations["source_coverage"]["file_backed"]["unavailable_turns"] = 2
    report = knowledge_status.summarize(observations, now=NOW)
    assert report["source_coverage"]["file_backed"]["unavailable_turns"] == 2


def test_changed_sql_revision_adds_limitation():
    observations = _base_observations()
    observations["source_coverage"]["sql_backed"]["changed_sources"] = 2
    report = knowledge_status.summarize(observations, now=NOW)
    sql = report["source_coverage"]["sql_backed"]
    assert sql["changed_sources"] == 2
    assert any("changed_sources=2" in line for line in report["limitations"])
    assert report["source_coverage"]["status"] != "complete"


def test_incomplete_scans_are_partial_not_zero():
    observations = _base_observations()
    observations["source_coverage"]["complete"] = False
    observations["source_coverage"]["file_backed"]["complete"] = False
    observations["source_coverage"]["file_backed"]["timed_out_sources"] = 1
    report = knowledge_status.summarize(observations, now=NOW)
    assert report["source_coverage"]["status"] == "partial"
    assert report["source_coverage"]["file_backed"]["pending_turns"] == 3
    assert any("partial" in line for line in report["limitations"])


def test_missing_source_coverage_is_unavailable_not_zero():
    observations = _base_observations()
    del observations["source_coverage"]
    report = knowledge_status.summarize(observations, now=NOW)
    coverage = report["source_coverage"]
    assert coverage["status"] == "unavailable"
    assert coverage.get("pending_turns") is None
    assert coverage.get("file_backed") == "unavailable"
    assert coverage.get("sql_backed") == "unavailable"
    assert any("unknown or unavailable" in line for line in report["limitations"])


def test_unavailable_sql_bucket_blocks_complete():
    observations = _base_observations()
    observations["source_coverage"]["sql_backed"] = {
        "status": "unavailable", "complete": False, "pending_turns": None,
        "diagnostic": "SQL audit unavailable: ValueError",
    }
    report = knowledge_status.summarize(observations, now=NOW)
    assert report["source_coverage"]["status"] in ("partial", "unknown")
    assert report["source_coverage"]["complete"] is False
    assert any("sql_backed observation" in line
               for line in report["limitations"])


def test_stale_observation_is_flagged_but_binding_preserved():
    observations = _base_observations()
    observations["source_coverage"]["observed_at"] = "2026-10-20T00:00:00+00:00"
    report = knowledge_status.summarize(observations, now=NOW)
    assert report["source_coverage"]["stale"] is True
    assert report["source_coverage"]["observed_at"] == "2026-10-20T00:00:00+00:00"
    assert report["observed_at"] == NOW
    assert any("staleness hint" in line for line in report["limitations"])


def test_future_observation_is_clock_skew_not_freshness():
    observations = _base_observations()
    observations["source_coverage"]["observed_at"] = "2026-10-26T00:00:00+00:00"
    report = knowledge_status.summarize(observations, now=NOW)
    assert report["source_coverage"]["stale"] is True
    assert report["source_coverage"]["observed_at"] == "2026-10-26T00:00:00+00:00"
    assert any("clock skew" in line for line in report["limitations"])


def test_file_side_failures_block_complete():
    for key in ("invalid_receipts", "source_errors", "parse_exceptions", "timed_out_sources"):
        observations = _base_observations()
        observations["source_coverage"]["file_backed"][key] = 1
        report = knowledge_status.summarize(observations, now=NOW)
        assert report["source_coverage"]["complete"] is False, key
        assert report["source_coverage"]["status"] != "complete", key
        assert any(f"file_backed audit reported {key}=1" in line
                   for line in report["limitations"]), key


def test_forged_artifact_verification_is_not_promoted():
    observations = _base_observations()
    observations["publication"]["artifact_verified"] = "verified"
    report = knowledge_status.summarize(observations, now=NOW)
    pub = report["publication"]
    assert pub["artifact_verified"] == "unmeasured"
    assert pub["caller_artifact_verification_claim"] == "verified"
    assert any("artifact verification is unmeasured" in line
               for line in report["limitations"])


def test_retained_widened_window_keeps_caller_bounds():
    observations = _base_observations()
    observations["source_window"] = {"since": "2026-09-01", "through": "2026-10-25"}
    report = knowledge_status.summarize(observations, now=NOW)
    window = report["source_window"]
    assert window["since"] == "2026-09-01"
    assert window["through"] == "2026-10-25"
    assert window["scope_state"]["widened"] is True


def test_retained_future_window_keeps_caller_bounds():
    observations = _base_observations()
    observations["source_window"] = {"since": "2026-09-13", "through": "2026-12-31"}
    report = knowledge_status.summarize(observations, now=NOW)
    window = report["source_window"]
    assert window["since"] == "2026-09-13"
    assert window["through"] == "2026-12-31"
    assert window["scope_state"]["future"] is True


def test_offline_peer_reported_and_idle_does_not_claim_integration():
    observations = _base_observations()
    observations["replication"] = {
        "status": "syncing",
        "observed_at": "2026-10-25T02:55:00+00:00",
        "peers": [
            {"name": "sjm", "state": "offline", "last_seen_at": "2026-10-24T00:00:00+00:00"},
        ],
    }
    report = knowledge_status.summarize(observations, now=NOW)
    assert report["replication"]["status"] == "syncing"
    assert report["replication"]["peers"][0]["state"] == "offline"
    assert report["replication"]["observed_at"] == "2026-10-25T02:55:00+00:00"
    assert any("offline" in line for line in report["limitations"])
    assert report["integration"]["status"] == "unmeasured"


def test_future_publication_receipt_lag_unknown_and_flagged():
    observations = _base_observations()
    observations["publication"] = {
        "status": "published",
        "observed_at": "2026-10-25T02:59:00+00:00",
        # Publication event 30 minutes after the report clock: skew.
        "published_at": "2026-10-25T03:30:00+00:00",
        "snapshot_sha256": "f" * 64,
        "watermark_size": 3,
    }
    report = knowledge_status.summarize(observations, now=NOW)
    publication = report["publication"]
    # Receipt binding and published status are preserved.
    assert publication["status"] == "published"
    assert publication["published_at"] == "2026-10-25T03:30:00+00:00"
    assert publication["snapshot_sha256"] == "f" * 64
    assert publication["future_receipt"] is True
    # Lag must not be a fabricated negative offset.
    assert publication["lag_seconds"] is None
    assert any("clock skew" in line and "publication" in line
               for line in report["limitations"])
    assert any("lag is unknown" in line for line in report["limitations"])


def test_publication_lag_and_watermark_reported():
    observations = _base_observations()
    observations["publication"] = {
        "status": "published",
        "observed_at": "2026-10-25T02:59:00+00:00",
        "published_at": "2026-10-24T03:00:00+00:00",
        "snapshot_sha256": "b" * 64,
        "watermark_size": 5,
        "artifact_verified": "unmeasured",
    }
    report = knowledge_status.summarize(observations, now=NOW)
    publication = report["publication"]
    assert publication["status"] == "published"
    assert publication["snapshot_sha256"] == "b" * 64
    assert publication["watermark_size"] == 5
    assert publication["lag_seconds"] == 24 * 3600
    assert publication["artifact_verified"] == "unmeasured"
    assert any("artifact verification is unmeasured" in line
               for line in report["limitations"])


def test_publication_missing_is_explicit_unknown():
    observations = _base_observations()
    observations["publication"] = None
    report = knowledge_status.summarize(observations, now=NOW)
    assert report["publication"]["status"] == "unavailable"
    assert report["publication"]["snapshot_sha256"] is None


def test_publication_bad_hash_not_claimed():
    observations = _base_observations()
    observations["publication"]["snapshot_sha256"] = "not-a-hash"
    report = knowledge_status.summarize(observations, now=NOW)
    assert report["publication"]["snapshot_sha256"] is None


def test_publication_latest_attempt_outcome_always_unmeasured():
    observations = _base_observations()
    # A staged recovery journal has no timestamp or outcome; a caller may
    # still assert anything. Neither may be promoted to a measured outcome.
    observations["publication"]["latest_attempt_outcome"] = "failed"
    report = knowledge_status.summarize(observations, now=NOW)
    pub = report["publication"]
    assert pub["latest_attempt_outcome"] == "unmeasured"
    assert pub["status"] == "published"
    assert pub["snapshot_sha256"] == "a" * 64
    assert any("latest publication attempt outcome is unmeasured" in line
               for line in report["limitations"])
    # Missing publication observation keeps the same explicit unknown.
    observations = _base_observations(publication=None)
    report = knowledge_status.summarize(observations, now=NOW)
    assert report["publication"]["latest_attempt_outcome"] == "unmeasured"


def test_unmeasured_integration_never_inferred():
    observations = _base_observations()
    observations["integration"] = {
        "status": "measured",
        "evidence": {"note": "wiki page mtime"},
    }
    report = knowledge_status.summarize(observations, now=NOW)
    assert report["integration"]["status"] == "unmeasured"
    assert report["integration"]["evidence"] is None
    assert any("integration is unmeasured" in line for line in report["limitations"])


def test_registered_schedule_is_not_observed_dispatch():
    observations = _base_observations()
    observations["native_job"]["registration_state"] = "active"
    observations["native_job"]["observed_dispatch"] = "unknown"
    report = knowledge_status.summarize(observations, now=NOW)
    native = report["native_job"]
    assert native["registration_state"] == "active"
    assert native["native_schedule"] == "RRULE:FREQ=HOURLY"
    assert native["observed_dispatch"] == "unknown"
    assert any("dispatch was not observed" in line for line in report["limitations"])


def test_caller_only_dispatch_declaration_not_verified():
    observations = _base_observations()
    observations["native_job"]["observed_dispatch"] = "observed"
    observations["native_job"]["last_run_outcome"] = "unknown"
    report = knowledge_status.summarize(observations, now=NOW)
    native = report["native_job"]
    assert native["observed_dispatch"] == "unknown"
    assert native["dispatch_evidence"] == "unverified"
    assert native["caller_dispatch_claim"] == "observed"
    assert native["last_run_outcome"] == "unknown"
    assert any("not verified" in line for line in report["limitations"])
    assert any("run outcome is unknown" in line for line in report["limitations"])


def test_forged_verified_dispatch_declaration_still_unverified():
    observations = _base_observations()
    observations["native_job"]["observed_dispatch"] = "observed"
    observations["native_job"]["dispatch_evidence"] = "verified"
    report = knowledge_status.summarize(observations, now=NOW)
    native = report["native_job"]
    # No current owning reader can verify native dispatch; a caller string
    # must never be promoted to verified dispatch.
    assert native["observed_dispatch"] == "unknown"
    assert native["dispatch_evidence"] == "unverified"
    assert native["caller_dispatch_claim"] == "observed"
    assert native["caller_dispatch_evidence_claim"] == "verified"
    assert any("not verified" in line for line in report["limitations"])


def test_failed_run_outcome_is_reported():
    observations = _base_observations()
    observations["native_job"]["last_run_outcome"] = "failed"
    report = knowledge_status.summarize(observations, now=NOW)
    assert report["native_job"]["last_run_outcome"] == "failed"


def test_invalid_now_raises():
    with pytest.raises(ValueError):
        knowledge_status.summarize(_base_observations(), now="2026-10-25T03:00:00")


def test_naive_observed_at_downgrades_to_partial():
    observations = _base_observations()
    observations["source_coverage"]["observed_at"] = "2026-10-25T03:00:00"
    report = knowledge_status.summarize(observations, now=NOW)
    coverage = report["source_coverage"]
    assert coverage["status"] == "partial"
    assert coverage["complete"] is False
    assert coverage["observed_at"] is None
    assert coverage["observed_at_valid"] is False


def test_widened_window_flagged():
    observations = _base_observations()
    observations["source_window"] = {"since": "2026-09-01", "through": "2026-10-25"}
    report = knowledge_status.summarize(observations, now=NOW)
    assert report["source_window"]["scope_state"]["widened"] is True
    assert any("widened" in line for line in report["limitations"])


def test_future_window_flagged():
    observations = _base_observations()
    observations["source_window"] = {"since": "2026-09-13", "through": "2026-12-31"}
    report = knowledge_status.summarize(observations, now=NOW)
    assert report["source_window"]["scope_state"]["future"] is True
    assert any("future" in line for line in report["limitations"])


def test_default_through_uses_hkt_calendar():
    # 2026-10-24T23:30:00+00:00 is 2026-10-25T07:30 in UTC+8.
    report = knowledge_status.summarize(
        _base_observations(source_window={}),
        now="2026-10-24T23:30:00+00:00",
    )
    assert report["source_window"]["through"] == "2026-10-25"


def test_negative_scan_budget_rejected():
    cli = _cli_module()
    with pytest.raises(ValueError):
        cli._validate_scan_seconds(-1)
    with pytest.raises(ValueError):
        cli._validate_scan_seconds(0)
    with pytest.raises(ValueError):
        cli._validate_scan_seconds(float("nan"))
    with pytest.raises(ValueError):
        cli._validate_scan_seconds(float("inf"))
    with pytest.raises(ValueError):
        cli._validate_scan_seconds(True)


# --- CLI collectors -----------------------------------------------------


def test_cli_empty_state_tree_unchanged(tmp_path: Path, capsys, monkeypatch):
    monkeypatch.setenv("K2B_CODEX_SESSIONS_ROOT", str(tmp_path / "missing"))
    codex_root = tmp_path / "empty-codex"
    codex_root.mkdir()
    state_root = tmp_path / "state"
    state_root.mkdir()
    automation_root = tmp_path / "automations"
    automation_root.mkdir()
    before = sorted((p.name, p.stat().st_size if p.is_file() else 0)
                    for p in state_root.iterdir())
    module = _cli_module()
    rc = module.main([
        "report",
        "--codex-root", str(codex_root),
        "--state-root", str(state_root),
        "--automation-root", str(automation_root),
        "--max-scan-seconds", "0.5",
        "--now", NOW,
    ])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["integration"]["status"] == "unmeasured"
    after = sorted((p.name, p.stat().st_size if p.is_file() else 0)
                   for p in state_root.iterdir())
    assert before == after, "CLI must not create lock or state files"


def test_cli_tolerates_missing_paths(tmp_path: Path, capsys, monkeypatch):
    monkeypatch.setenv("K2B_CODEX_SESSIONS_ROOT", str(tmp_path / "missing"))
    codex_root = tmp_path / "empty-codex"
    codex_root.mkdir()
    state_root = tmp_path / "state"
    state_root.mkdir()
    automation_root = tmp_path / "automations"
    automation_root.mkdir()
    module = _cli_module()
    rc = module.main([
        "report",
        "--codex-root", str(codex_root),
        "--state-root", str(state_root),
        "--automation-root", str(automation_root),
        "--max-scan-seconds", "0.5",
        "--now", NOW,
    ])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["source_coverage"]["status"] in ("partial", "unknown", "unavailable")
    assert out["publication"]["status"] in ("no_receipts", "unavailable", "unknown")
    assert out["integration"]["status"] == "unmeasured"
    # No lock or state created beyond the provided roots.
    assert sorted(p.name for p in state_root.iterdir()) == []


def test_cli_outbox_validation_uses_owner_helper(tmp_path: Path, capsys, monkeypatch):
    monkeypatch.setenv("K2B_CODEX_SESSIONS_ROOT", str(tmp_path / "missing"))
    codex_root = tmp_path / "empty-codex"
    codex_root.mkdir()
    state_root = tmp_path / "state"
    outbox = state_root / "outbox"
    outbox.mkdir(parents=True)
    # Malformed envelope: status is bogus; validator must reject it.
    (outbox / "bad.json").write_text(json.dumps({"status": "bogus"}))
    automation_root = tmp_path / "automations"
    automation_root.mkdir()
    module = _cli_module()
    # Spy on the real owner validator to prove it was called for the bad file.
    calls: list = []
    original_validate = module.automatic_memory._validate_envelope
    def spying_validate(envelope, durable=False):
        calls.append(envelope)
        return original_validate(envelope, durable=durable)
    monkeypatch.setattr(module.automatic_memory, "_validate_envelope", spying_validate)
    rc = module.main([
        "report",
        "--codex-root", str(codex_root),
        "--state-root", str(state_root),
        "--automation-root", str(automation_root),
        "--max-scan-seconds", "0.5",
        "--now", NOW,
    ])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    delivery = out["delivery"]
    assert calls, "owner validator must be invoked"
    assert any(isinstance(c, dict) and c.get("status") == "bogus" for c in calls)
    # A validation failure is honest partial with unknown counts, not zero.
    assert delivery["status"] == "partial"
    assert delivery["pending_envelopes"] is None
    assert any("failed validation" in line for line in out["limitations"])


def test_file_owner_unavailable_turns_preserved_from_owner(tmp_path: Path, monkeypatch):
    app_root = tmp_path / "app"
    app_root.mkdir()
    sessions = app_root / "sessions"
    sessions.mkdir()
    state_root = tmp_path / "state"
    state_root.mkdir()
    monkeypatch.delenv("K2B_CODEX_SESSIONS_ROOT", raising=False)
    monkeypatch.delenv("CODEX_HOME", raising=False)
    cli = _cli_module()

    def fake_file_scan(**kwargs):
        return {"status": "ok", "complete": True, "pending_turns": 0,
                "extracted_turns": 0, "oldest_pending_at": None,
                "unverified_turns": 7, "unavailable_turns": 3}

    def fake_sql_scan(**kwargs):
        return {"status": "ok", "complete": True, "pending_turns": 0,
                "extracted_turns": 0, "changed_sources": 0,
                "oldest_pending_at": None}

    monkeypatch.setattr(cli.eod_capture, "memory_backlog", fake_file_scan)
    import codex_sql_memory as csm
    monkeypatch.setattr(csm, "scan", fake_sql_scan)

    coverage = cli._collect_source_coverage(
        app_root=app_root, sessions_root=sessions, state_root=state_root,
        writer_role="home", since=knowledge_status.APPROVED_WINDOW_START,
        through="2026-10-25", max_scan_seconds=0.5,
    )
    fb = coverage["file_backed"]
    assert fb["unavailable_turns"] == 3
    assert fb["unverified_turns"] == 7


def test_file_owner_omits_unavailable_turns_stays_unknown(tmp_path: Path, monkeypatch):
    app_root = tmp_path / "app"
    app_root.mkdir()
    sessions = app_root / "sessions"
    sessions.mkdir()
    state_root = tmp_path / "state"
    state_root.mkdir()
    monkeypatch.delenv("K2B_CODEX_SESSIONS_ROOT", raising=False)
    monkeypatch.delenv("CODEX_HOME", raising=False)
    cli = _cli_module()

    def fake_file_scan(**kwargs):
        return {"status": "ok", "complete": True, "pending_turns": 0,
                "extracted_turns": 0, "oldest_pending_at": None,
                "unverified_turns": 5}

    def fake_sql_scan(**kwargs):
        return {"status": "ok", "complete": True, "pending_turns": 0,
                "extracted_turns": 0, "changed_sources": 0,
                "oldest_pending_at": None}

    monkeypatch.setattr(cli.eod_capture, "memory_backlog", fake_file_scan)
    import codex_sql_memory as csm
    monkeypatch.setattr(csm, "scan", fake_sql_scan)

    coverage = cli._collect_source_coverage(
        app_root=app_root, sessions_root=sessions, state_root=state_root,
        writer_role="home", since=knowledge_status.APPROVED_WINDOW_START,
        through="2026-10-25", max_scan_seconds=0.5,
    )
    fb = coverage["file_backed"]
    assert "unavailable_turns" not in fb
    assert fb["unverified_turns"] == 5


def test_collector_routes_owner_roots(tmp_path: Path, monkeypatch):
    """File owner receives APP_ROOT/sessions; SQL owner receives APP_ROOT."""
    app_root = tmp_path / "app"
    app_root.mkdir()
    sessions = app_root / "sessions"
    sessions.mkdir()
    state_root = tmp_path / "state"
    state_root.mkdir()
    monkeypatch.delenv("K2B_CODEX_SESSIONS_ROOT", raising=False)
    monkeypatch.delenv("CODEX_HOME", raising=False)
    cli = _cli_module()
    seen: dict = {}

    def fake_file_scan(**kwargs):
        seen["file"] = kwargs
        return {"status": "ok", "complete": True, "pending_turns": 0,
                "extracted_turns": 0, "oldest_pending_at": None}

    def fake_sql_scan(**kwargs):
        seen["sql"] = kwargs
        return {"status": "ok", "complete": True, "pending_turns": 0,
                "extracted_turns": 0, "changed_sources": 0,
                "oldest_pending_at": None}

    monkeypatch.setattr(cli.eod_capture, "memory_backlog", fake_file_scan)
    import codex_sql_memory as csm
    monkeypatch.setattr(csm, "scan", fake_sql_scan)

    coverage = cli._collect_source_coverage(
        app_root=app_root, sessions_root=sessions, state_root=state_root,
        writer_role="home", since=knowledge_status.APPROVED_WINDOW_START,
        through="2026-10-25", max_scan_seconds=0.5,
    )
    assert Path(seen["file"]["codex_root"]) == sessions
    assert Path(seen["sql"]["codex_root"]) == app_root
    assert coverage["file_backed"]["pending_turns"] == 0


def test_cli_sessions_root_override_is_passed_to_file_owner(
    tmp_path: Path, capsys, monkeypatch
):
    app_root = tmp_path / "app"
    app_root.mkdir()
    sessions = tmp_path / "alt-sessions"
    sessions.mkdir()
    state_root = tmp_path / "state"
    state_root.mkdir()
    automation_root = tmp_path / "automations"
    automation_root.mkdir()
    cli = _cli_module()
    seen: dict = {}

    def fake_file_scan(**kwargs):
        seen["file"] = kwargs
        return {"status": "ok", "complete": True, "pending_turns": 0,
                "extracted_turns": 0, "oldest_pending_at": None}

    def fake_sql_scan(**kwargs):
        seen["sql"] = kwargs
        return {"status": "ok", "complete": True, "pending_turns": 0,
                "extracted_turns": 0, "changed_sources": 0,
                "oldest_pending_at": None}

    monkeypatch.setattr(cli.eod_capture, "memory_backlog", fake_file_scan)
    import codex_sql_memory as csm
    monkeypatch.setattr(csm, "scan", fake_sql_scan)
    rc = cli.main([
        "report", "--codex-root", str(app_root),
        "--sessions-root", str(sessions),
        "--state-root", str(state_root),
        "--automation-root", str(automation_root),
        "--max-scan-seconds", "0.5", "--now", NOW,
    ])
    assert rc == 0
    capsys.readouterr()
    assert Path(seen["file"]["codex_root"]) == sessions
    assert Path(seen["sql"]["codex_root"]) == app_root


def test_cli_out_of_scope_window_rejected_before_collection(
    tmp_path: Path, capsys, monkeypatch
):
    app_root = tmp_path / "app"
    app_root.mkdir()
    state_root = tmp_path / "state"
    state_root.mkdir()
    automation_root = tmp_path / "automations"
    automation_root.mkdir()
    cli = _cli_module()
    calls = {"file": 0, "sql": 0}

    def fake_file_scan(**kwargs):
        calls["file"] += 1
        return {}

    def fake_sql_scan(**kwargs):
        calls["sql"] += 1
        return {}

    monkeypatch.setattr(cli.eod_capture, "memory_backlog", fake_file_scan)
    import codex_sql_memory as csm
    monkeypatch.setattr(csm, "scan", fake_sql_scan)
    with pytest.raises(SystemExit):
        cli.main([
            "report", "--codex-root", str(app_root),
            "--state-root", str(state_root),
            "--automation-root", str(automation_root),
            "--since", "2026-09-01", "--through", "2026-10-25",
            "--max-scan-seconds", "0.5", "--now", NOW,
        ])
    assert calls == {"file": 0, "sql": 0}
    capsys.readouterr()


def test_cli_missing_outbox_is_unknown(tmp_path: Path, capsys, monkeypatch):
    missing_sessions = tmp_path / "missing"
    monkeypatch.setenv("K2B_CODEX_SESSIONS_ROOT", str(missing_sessions))
    codex_root = tmp_path / "empty-codex"
    codex_root.mkdir()
    state_root = tmp_path / "state"
    state_root.mkdir()
    automation_root = tmp_path / "automations"
    automation_root.mkdir()
    module = _cli_module()
    rc = module.main([
        "report",
        "--codex-root", str(codex_root),
        "--state-root", str(state_root),
        "--automation-root", str(automation_root),
        "--max-scan-seconds", "0.5",
        "--now", NOW,
    ])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    # No source sessions directory and no outbox directory: neither may be
    # fabricated as a complete zero scan.
    assert out["source_coverage"]["file_backed"] in ("unavailable",)
    assert out["source_coverage"]["status"] != "complete"
    assert out["source_coverage"]["complete"] is False
    delivery = out["delivery"]
    assert delivery["pending_envelopes"] is None
    assert delivery["outbox"] in ("unknown", "partial")


def test_cli_non_json_files_bounded_by_entry_limit(tmp_path: Path, capsys, monkeypatch):
    monkeypatch.setenv("K2B_CODEX_SESSIONS_ROOT", str(tmp_path / "missing"))
    codex_root = tmp_path / "empty-codex"
    codex_root.mkdir()
    state_root = tmp_path / "state"
    pub = state_root / "publication"
    pub.mkdir(parents=True)
    for i in range(5):
        (pub / f"note{i}.txt").write_text("not a receipt")
    automation_root = tmp_path / "automations"
    automation_root.mkdir()
    cli = _cli_module()
    cli.MAX_RECEIPT_SCAN_ENTRIES = 2
    rc = cli.main([
        "report",
        "--codex-root", str(codex_root),
        "--state-root", str(state_root),
        "--automation-root", str(automation_root),
        "--max-scan-seconds", "0.5",
        "--now", NOW,
    ])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    # A directory full of non-JSON files must still be bounded and marked
    # partial rather than silently exhausting the scan.
    assert out["publication"]["status"] == "no_receipts"
    assert out["publication"]["scan_partial"] is True
    assert any("latest receipt may be missed" in line for line in out["limitations"])


def test_cli_publication_oversize_and_malformed_are_ignored(tmp_path: Path, capsys, monkeypatch):
    monkeypatch.setenv("K2B_CODEX_SESSIONS_ROOT", str(tmp_path / "missing"))
    codex_root = tmp_path / "empty-codex"
    codex_root.mkdir()
    state_root = tmp_path / "state"
    pub = state_root / "publication"
    pub.mkdir(parents=True)
    (pub / "oversize.json").write_text("x" * (300 * 1024))
    (pub / "malformed.json").write_text("{not json")
    automation_root = tmp_path / "automations"
    automation_root.mkdir()
    module = _cli_module()
    rc = module.main([
        "report",
        "--codex-root", str(codex_root),
        "--state-root", str(state_root),
        "--automation-root", str(automation_root),
        "--max-scan-seconds", "0.5",
        "--now", NOW,
    ])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["publication"]["status"] == "no_receipts"
    assert out["publication"]["artifact_verified"] == "unmeasured"


def test_cli_publication_valid_receipt_is_reported(tmp_path: Path, capsys, monkeypatch):
    monkeypatch.setenv("K2B_CODEX_SESSIONS_ROOT", str(tmp_path / "missing"))
    codex_root = tmp_path / "empty-codex"
    codex_root.mkdir()
    state_root = tmp_path / "state"
    pub = state_root / "publication"
    pub.mkdir(parents=True)
    receipt = {
        "schema_version": 1,
        "status": "published",
        "writer_role": "home",
        "vault_root": str(tmp_path / "vault"),
        "snapshot_sha256": "c" * 64,
        "artifact_sha256": {},
        "reconciliation_watermark": {"delivery:" + "d" * 64: "e" * 64},
        "published_at": "2026-10-25T02:50:00+00:00",
    }
    (pub / "abc.json").write_text(json.dumps(receipt))
    automation_root = tmp_path / "automations"
    automation_root.mkdir()
    module = _cli_module()
    rc = module.main([
        "report",
        "--codex-root", str(codex_root),
        "--state-root", str(state_root),
        "--automation-root", str(automation_root),
        "--max-scan-seconds", "0.5",
        "--now", NOW,
    ])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    publication = out["publication"]
    assert publication["status"] == "published"
    assert publication["snapshot_sha256"] == "c" * 64
    assert publication["watermark_size"] == 1
    assert publication["artifact_verified"] == "unmeasured"
    assert any("artifact verification is unmeasured" in line
               for line in out["limitations"])


def _emit_publication(tmp_path, state_root, receipts, *extra):
    pub = state_root / "publication"
    pub.mkdir(parents=True, exist_ok=True)
    for name, payload in receipts.items():
        (pub / name).write_text(json.dumps(payload) if not isinstance(payload, str) else payload)
    return pub


def test_cli_publication_rejected_only_is_partial_with_diagnostic(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("K2B_CODEX_SESSIONS_ROOT", str(tmp_path / "missing"))
    codex_root = tmp_path / "empty-codex"
    codex_root.mkdir()
    state_root = tmp_path / "state"
    state_root.mkdir()
    # Business rejection: schema_version is unsupported.
    _emit_publication(tmp_path, state_root, {
        "r1.json": {"schema_version": 2, "status": "published",
                    "snapshot_sha256": "a" * 64,
                    "published_at": "2026-10-25T01:00:00+00:00"},
        # Wrong status.
        "r2.json": {"schema_version": 1, "status": "draft",
                    "snapshot_sha256": "b" * 64,
                    "published_at": "2026-10-25T01:00:00+00:00"},
    })
    automation_root = tmp_path / "automations"
    automation_root.mkdir()
    module = _cli_module()
    rc = module.main([
        "report", "--codex-root", str(codex_root),
        "--state-root", str(state_root),
        "--automation-root", str(automation_root),
        "--max-scan-seconds", "0.5", "--now", NOW,
    ])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    pub = out["publication"]
    assert pub["scan_partial"] is True
    assert pub["rejected_receipts"] == 2
    assert pub["status"] == "unknown"
    assert any("rejected" in line and "publication" in line
               for line in out["limitations"])


def test_cli_publication_mixed_valid_keeps_best_success(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("K2B_CODEX_SESSIONS_ROOT", str(tmp_path / "missing"))
    codex_root = tmp_path / "empty-codex"
    codex_root.mkdir()
    state_root = tmp_path / "state"
    state_root.mkdir()
    good_new = {"schema_version": 1, "status": "published",
                "writer_role": "home",
                "vault_root": str(tmp_path / "vault"),
                "snapshot_sha256": "c" * 64,
                "artifact_sha256": {},
                "reconciliation_watermark": {"delivery:" + "d" * 64: "e" * 64},
                "published_at": "2026-10-25T02:50:00+00:00"}
    good_old = dict(good_new, snapshot_sha256="e" * 64,
                    published_at="2026-10-25T01:00:00+00:00")
    rejected = {"schema_version": 1, "status": "published",
                "snapshot_sha256": "not-a-hash",
                "published_at": "2026-10-25T02:00:00+00:00"}
    _emit_publication(tmp_path, state_root, {
        "old.json": good_old, "new.json": good_new, "bad.json": rejected,
    })
    automation_root = tmp_path / "automations"
    automation_root.mkdir()
    module = _cli_module()
    rc = module.main([
        "report", "--codex-root", str(codex_root),
        "--state-root", str(state_root),
        "--automation-root", str(automation_root),
        "--max-scan-seconds", "0.5", "--now", NOW,
    ])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    pub = out["publication"]
    assert pub["status"] == "published"
    assert pub["snapshot_sha256"] == "c" * 64
    assert pub["scan_partial"] is True
    assert pub["rejected_receipts"] == 1
    assert any("rejected" in line for line in out["limitations"])


def test_cli_publication_empty_folder_is_distinct_from_rejected(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("K2B_CODEX_SESSIONS_ROOT", str(tmp_path / "missing"))
    codex_root = tmp_path / "empty-codex"
    codex_root.mkdir()
    state_root = tmp_path / "state"
    state_root.mkdir()
    _emit_publication(tmp_path, state_root, {})
    automation_root = tmp_path / "automations"
    automation_root.mkdir()
    module = _cli_module()
    rc = module.main([
        "report", "--codex-root", str(codex_root),
        "--state-root", str(state_root),
        "--automation-root", str(automation_root),
        "--max-scan-seconds", "0.5", "--now", NOW,
    ])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    pub = out["publication"]
    assert pub["status"] == "no_receipts"
    assert pub.get("rejected_receipts") in (None, 0)
    assert pub["scan_partial"] is not True


def test_cli_publication_deeply_nested_json_does_not_crash(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("K2B_CODEX_SESSIONS_ROOT", str(tmp_path / "missing"))
    codex_root = tmp_path / "empty-codex"
    codex_root.mkdir()
    state_root = tmp_path / "state"
    state_root.mkdir()
    # Synthetic deep nesting within MAX_RECEIPT_BYTES triggers RecursionError
    # in the stdlib JSON decoder.
    depth = 50000
    nested = "[" * depth + "0" + "]" * depth
    assert len(nested) < 256 * 1024
    _emit_publication(tmp_path, state_root, {"deep.json": nested})
    automation_root = tmp_path / "automations"
    automation_root.mkdir()
    module = _cli_module()
    rc = module.main([
        "report", "--codex-root", str(codex_root),
        "--state-root", str(state_root),
        "--automation-root", str(automation_root),
        "--max-scan-seconds", "0.5", "--now", NOW,
    ])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["publication"]["status"] == "no_receipts"
    assert out["publication"]["scan_partial"] is True
    assert out["publication"]["snapshot_sha256"] is None


def test_cli_replication_deeply_nested_json_does_not_crash(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("K2B_CODEX_SESSIONS_ROOT", str(tmp_path / "missing"))
    codex_root = tmp_path / "empty-codex"
    codex_root.mkdir()
    state_root = tmp_path / "state"
    state_root.mkdir()
    automation_root = tmp_path / "automations"
    automation_root.mkdir()
    depth = 50000
    nested = "[" * depth + "0" + "]" * depth
    assert len(nested) < 256 * 1024
    repl = tmp_path / "replication.json"
    repl.write_text(nested)
    module = _cli_module()
    rc = module.main([
        "report", "--codex-root", str(codex_root),
        "--state-root", str(state_root),
        "--automation-root", str(automation_root),
        "--replication-json", str(repl),
        "--max-scan-seconds", "0.5", "--now", NOW,
    ])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["replication"]["status"] == "unmeasured"


def test_cli_delivery_oldest_pending_at_from_envelope_created_at(tmp_path):
    import automatic_memory
    from test_automatic_memory import _bundle
    state_root = tmp_path / "state"
    for state, created in (
        ("accepted", "2026-10-20T00:00:00+00:00"),
        ("pending", "2026-10-24T10:00:00+00:00"),
        ("failed", "2026-10-24T05:00:00+00:00"),
    ):
        envelope = automatic_memory.enqueue_bundle(
            state_root, _bundle(session_id="synthetic-" + state), now=created)
        if state != "pending":
            kwargs = {} if state == "accepted" else {"error_code": "offline_test"}
            automatic_memory.record_delivery_attempt(
                state_root, envelope["delivery_id"], outcome=state,
                now=created, **kwargs)
    cli = _cli_module()
    delivery = cli._collect_delivery(state_root=state_root)
    assert delivery["status"] == "observed"
    assert delivery["pending_envelopes"] == 2
    assert delivery["oldest_pending_at"] == "2026-10-24T05:00:00+00:00"
    (state_root / "outbox" / "corrupt.json").write_text("{}")
    partial = cli._collect_delivery(state_root=state_root)
    assert partial["status"] == "partial"
    assert partial["pending_envelopes"] is None
    report = knowledge_status.summarize(_base_observations(delivery=partial), now=NOW)
    assert any("age covers inspected envelopes only" in line for line in report["limitations"])


def test_cli_publication_bounded_scan_marks_partial(tmp_path: Path, capsys, monkeypatch):
    monkeypatch.setenv("K2B_CODEX_SESSIONS_ROOT", str(tmp_path / "missing"))
    codex_root = tmp_path / "empty-codex"
    codex_root.mkdir()
    state_root = tmp_path / "state"
    pub = state_root / "publication"
    pub.mkdir(parents=True)
    cli = _cli_module()
    cli.MAX_RECEIPT_SCAN_ENTRIES = 2
    for i in range(4):
        receipt = {
            "schema_version": 1, "status": "published", "writer_role": "home",
            "vault_root": str(tmp_path / "vault"), "snapshot_sha256": str(i) * 64,
            "artifact_sha256": {},
            "reconciliation_watermark": {},
            "published_at": f"2026-10-2{i}T00:00:00+00:00",
        }
        (pub / f"r{i}.json").write_text(json.dumps(receipt))
    automation_root = tmp_path / "automations"
    automation_root.mkdir()
    rc = cli.main([
        "report",
        "--codex-root", str(codex_root),
        "--state-root", str(state_root),
        "--automation-root", str(automation_root),
        "--max-scan-seconds", "0.5",
        "--now", NOW,
    ])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["publication"]["status"] == "published"
    assert out["publication"]["scan_partial"] is True
    assert any("latest receipt may be missed" in line for line in out["limitations"])


def test_cli_outbox_oversize_envelope_not_read(tmp_path: Path, capsys, monkeypatch):
    monkeypatch.setenv("K2B_CODEX_SESSIONS_ROOT", str(tmp_path / "missing"))
    codex_root = tmp_path / "empty-codex"
    codex_root.mkdir()
    state_root = tmp_path / "state"
    outbox = state_root / "outbox"
    outbox.mkdir(parents=True)
    # Oversize envelope content: status 'pending' would fabricate backlog if read;
    # it must instead be uninspected with unknown total counts.
    payload = json.dumps({"status": "pending", "padding": "x" * (300 * 1024)})
    (outbox / "big.json").write_text(payload)
    automation_root = tmp_path / "automations"
    automation_root.mkdir()
    module = _cli_module()
    read_paths: list = []
    original_read = Path.read_text
    def tracking_read(self, *args, **kwargs):
        read_paths.append(str(self))
        return original_read(self, *args, **kwargs)
    monkeypatch.setattr(Path, "read_text", tracking_read)
    validator_calls: list = []
    original_validate = module.automatic_memory._validate_envelope
    def spying_validate(envelope, durable=False):
        validator_calls.append(envelope)
        return original_validate(envelope, durable=durable)
    monkeypatch.setattr(module.automatic_memory, "_validate_envelope", spying_validate)
    rc = module.main([
        "report",
        "--codex-root", str(codex_root),
        "--state-root", str(state_root),
        "--automation-root", str(automation_root),
        "--max-scan-seconds", "0.5",
        "--now", NOW,
    ])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    delivery = out["delivery"]
    big = str(outbox / "big.json")
    # Both the file read and the validator must be bypassed for oversize.
    assert big not in read_paths
    assert not any("padding" in json.dumps(c) for c in validator_calls)
    assert delivery["status"] == "partial"
    assert delivery["pending_envelopes"] is None
    assert any("exceeded the read byte limit" in line
               for line in out["limitations"])
    # No lock or state directory was created.
    assert sorted(p.name for p in state_root.iterdir()) == ["outbox"]


def test_cli_role_policy_denial_is_nonzero_before_collection(tmp_path: Path, capsys, monkeypatch):
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    production_state = fake_home / ".local" / "state" / "k2b" / "automatic-memory"
    production_state.mkdir(parents=True)
    monkeypatch.setattr("getpass.getuser", lambda: "not-a-role")
    cli = _cli_module()
    calls = {"file": 0, "sql": 0}
    def fake_file_scan(**kwargs):
        calls["file"] += 1
        return {}
    def fake_sql_scan(**kwargs):
        calls["sql"] += 1
        return {}
    monkeypatch.setattr(cli.eod_capture, "memory_backlog", fake_file_scan)
    import codex_sql_memory as csm
    monkeypatch.setattr(csm, "scan", fake_sql_scan)
    codex_root = tmp_path / "empty-codex"
    codex_root.mkdir()
    (codex_root / "sessions").mkdir()
    monkeypatch.delenv("K2B_CODEX_SESSIONS_ROOT", raising=False)
    automation_root = tmp_path / "automations"
    automation_root.mkdir()
    cli.MAX_RECEIPT_SCAN_ENTRIES = 1
    rc = cli.main([
        "report", "--codex-root", str(codex_root),
        "--state-root", str(production_state),
        "--automation-root", str(automation_root),
        "--now", NOW, "--max-scan-seconds", "0.5",
    ])
    captured = capsys.readouterr()
    assert rc != 0
    assert rc == 3
    assert captured.out == ""
    assert "Traceback" not in captured.err
    assert "production home state belongs to host role" in captured.err
    assert calls == {"file": 0, "sql": 0}
    monkeypatch.setattr("getpass.getuser", lambda: "keithmbpm2")
    assert cli.main([
        "report", "--codex-root", str(codex_root),
        "--state-root", str(production_state),
        "--automation-root", str(automation_root),
        "--now", NOW, "--max-scan-seconds", "0.5",
    ]) == 0
    assert calls == {"file": 1, "sql": 1}


def test_enforce_role_binding_helper_still_raises(tmp_path: Path, monkeypatch):
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    production_state = fake_home / ".local" / "state" / "k2b" / "automatic-memory"
    production_state.mkdir(parents=True)
    monkeypatch.setattr("getpass.getuser", lambda: "not-a-role")
    cli = _cli_module()
    with pytest.raises(PermissionError):
        cli._enforce_role_binding(
            state_root=production_state, writer_role="home")
    # Synthetic overrides are not bound to a host role.
    cli._enforce_role_binding(state_root=tmp_path / "synthetic", writer_role="home")


def test_cli_delivery_uses_shared_deliverable_states(tmp_path: Path, monkeypatch):
    state_root = tmp_path / "state"
    state_root.mkdir()
    cli = _cli_module()
    counts = {state: 0 for state in
              ("accepted", "pending", "offline", "failed", "exhausted")}
    counts.update({"pending": 2, "failed": 1, "accepted": 9})
    monkeypatch.setattr(cli, "_read_outbox_read_only", lambda root: {
        "status": "observed", "counts": counts, "inspected": 12,
        "total_files": 12, "truncated": False, "diagnostic": None,
    })
    delivery = cli._collect_delivery(state_root=state_root)
    assert delivery["pending_envelopes"] == 3
    assert cli.knowledge_status.DELIVERABLE_STATES is knowledge_status.DELIVERABLE_STATES
    assert cli.knowledge_status.DELIVERABLE_STATES == (
        "pending", "offline", "failed", "exhausted")


def test_cli_authorized_reader_filesystem_denial_degrades(tmp_path: Path, monkeypatch):
    app_root = tmp_path / "app"
    app_root.mkdir()
    sessions = app_root / "sessions"
    sessions.mkdir()
    state_root = tmp_path / "state"
    state_root.mkdir()
    monkeypatch.delenv("K2B_CODEX_SESSIONS_ROOT", raising=False)
    monkeypatch.delenv("CODEX_HOME", raising=False)
    cli = _cli_module()
    def denied_file_scan(**kwargs):
        raise PermissionError("denied")
    def denied_sql_scan(**kwargs):
        raise PermissionError("denied")
    monkeypatch.setattr(cli.eod_capture, "memory_backlog", denied_file_scan)
    import codex_sql_memory as csm
    monkeypatch.setattr(csm, "scan", denied_sql_scan)
    monkeypatch.setattr(
        cli.eod_capture.native_job_status, "read_native_job_status",
        lambda **kwargs: (_ for _ in ()).throw(PermissionError("denied")),
    )
    coverage = cli._collect_source_coverage(
        app_root=app_root, sessions_root=sessions, state_root=state_root,
        writer_role="home", since=knowledge_status.APPROVED_WINDOW_START,
        through="2026-10-25", max_scan_seconds=0.5,
    )
    assert coverage["file_backed"]["status"] == "unavailable"
    assert "permission denied" in coverage["file_backed"]["diagnostic"]
    assert coverage["sql_backed"]["status"] == "unavailable"
    native = cli._collect_native_job(
        state_root=state_root, automation_root=tmp_path / "automations",
        writer_role="home",
    )
    assert "permission denied" in native["diagnostic"]


def test_cli_sql_gate_uses_resolved_report_date(tmp_path: Path, capsys, monkeypatch):
    monkeypatch.setenv("K2B_CODEX_SESSIONS_ROOT", str(tmp_path / "missing"))
    codex_root = tmp_path / "empty-codex"
    codex_root.mkdir()
    state_root = tmp_path / "state"
    state_root.mkdir()
    automation_root = tmp_path / "automations"
    automation_root.mkdir()
    cli = _cli_module()
    calls: list = []
    def fake_file_scan(**kwargs):
        return {"status": "ok", "complete": True, "pending_turns": 0,
                "extracted_turns": 0, "oldest_pending_at": None}
    def fake_sql_scan(**kwargs):
        calls.append(kwargs)
        return {"status": "ok", "complete": True, "pending_turns": 0,
                "extracted_turns": 0, "changed_sources": 0,
                "oldest_pending_at": None}
    monkeypatch.setattr(cli.eod_capture, "memory_backlog", fake_file_scan)
    import codex_sql_memory as csm
    monkeypatch.setattr(csm, "scan", fake_sql_scan)
    # Make a second wall-clock reading disagree with the resolved report date.
    monkeypatch.setattr(cli, "_current_hkt_date", lambda: "2099-01-01")
    rc = cli.main([
        "report", "--codex-root", str(codex_root),
        "--state-root", str(state_root),
        "--automation-root", str(automation_root),
        "--max-scan-seconds", "0.5", "--now", NOW,
    ])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    # Gate passed with the resolved date (2026-10-25), not the mocked one.
    assert calls, "SQL scan should have been attempted with the resolved date"
    assert calls[0]["through"] == "2026-10-25"
    assert out["source_window"]["through"] == "2026-10-25"


def test_cli_live_now_follows_observation_clock(tmp_path: Path, capsys, monkeypatch):
    monkeypatch.setenv("K2B_CODEX_SESSIONS_ROOT", str(tmp_path / "missing"))
    codex_root = tmp_path / "empty-codex"
    codex_root.mkdir()
    state_root = tmp_path / "state"
    state_root.mkdir()
    automation_root = tmp_path / "automations"
    automation_root.mkdir()
    cli = _cli_module()

    start = datetime(2026, 10, 25, 3, 0, 0, tzinfo=timezone.utc)
    observed = (start + timedelta(seconds=4)).isoformat()
    ticks = {"n": 0}

    def fake_now(tz=None):
        ticks["n"] += 1
        if ticks["n"] == 1:
            return start if tz is None else start.astimezone(tz)
        later = start + timedelta(seconds=4)
        return later if tz is None else later.astimezone(tz)

    class FakeDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fake_now(tz)

    monkeypatch.setattr(cli, "datetime", FakeDatetime)

    def fake_source_coverage(**kwargs):
        return {
            "observed_at": observed,
            "file_backed": {
                "status": "ok", "complete": True, "pending_turns": 0,
                "extracted_turns": 1, "oldest_pending_at": None,
            },
            "sql_backed": {
                "status": "ok", "complete": True, "pending_turns": 0,
                "extracted_turns": 0, "changed_sources": 0,
                "oldest_pending_at": None,
            },
            "union": "unmeasured",
        }

    monkeypatch.setattr(cli, "_collect_source_coverage", fake_source_coverage)
    rc = cli.main([
        "report", "--codex-root", str(codex_root),
        "--state-root", str(state_root),
        "--automation-root", str(automation_root),
        "--max-scan-seconds", "0.5",
    ])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    # Report time follows the clock after collection, not the pre-collection
    # reading, so the fresh observation is not future skew.
    assert out["observed_at"] == observed
    assert out["observed_at"] > start.isoformat()
    assert out["source_coverage"]["observed_at"] == observed
    assert out["source_coverage"].get("stale") is not True
    assert out["source_coverage"].get("observed_at_valid") is not False
    assert not any("clock skew" in line for line in out["limitations"])
    assert not any("staleness hint" in line for line in out["limitations"])


def test_cli_explicit_now_override_is_preserved(tmp_path: Path, capsys, monkeypatch):
    monkeypatch.setenv("K2B_CODEX_SESSIONS_ROOT", str(tmp_path / "missing"))
    codex_root = tmp_path / "empty-codex"
    codex_root.mkdir()
    state_root = tmp_path / "state"
    state_root.mkdir()
    automation_root = tmp_path / "automations"
    automation_root.mkdir()
    cli = _cli_module()

    def exploding_now(tz=None):
        raise AssertionError("explicit report time must not re-read the clock")

    class FakeDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return exploding_now(tz)

    monkeypatch.setattr(cli, "datetime", FakeDatetime)

    def fake_source_coverage(**kwargs):
        return {
            "observed_at": "2026-10-25T02:30:00+00:00",
            "file_backed": {
                "status": "ok", "complete": True, "pending_turns": 0,
                "extracted_turns": 1, "oldest_pending_at": None,
            },
            "sql_backed": {
                "status": "ok", "complete": True, "pending_turns": 0,
                "extracted_turns": 0, "changed_sources": 0,
                "oldest_pending_at": None,
            },
            "union": "unmeasured",
        }

    monkeypatch.setattr(cli, "_collect_source_coverage", fake_source_coverage)
    # Isolate main's report clock from collectors' observation clocks.
    monkeypatch.setattr(cli, "_collect_delivery", lambda **kwargs: {})
    monkeypatch.setattr(cli, "_collect_publication", lambda **kwargs: {})
    monkeypatch.setattr(cli, "_collect_native_job", lambda **kwargs: {})
    rc = cli.main([
        "report", "--codex-root", str(codex_root),
        "--state-root", str(state_root),
        "--automation-root", str(automation_root),
        "--max-scan-seconds", "0.5", "--now", NOW,
    ])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["observed_at"] == NOW
    assert out["source_window"]["through"] == "2026-10-25"


def test_cli_iso_datetime_window_canonicalizes_for_readers(
    tmp_path: Path, capsys, monkeypatch
):
    app_root = tmp_path / "app"
    app_root.mkdir()
    (app_root / "sessions").mkdir()
    monkeypatch.delenv("K2B_CODEX_SESSIONS_ROOT", raising=False)
    state_root = tmp_path / "state"
    state_root.mkdir()
    automation_root = tmp_path / "automations"
    automation_root.mkdir()
    cli = _cli_module()
    seen: dict = {}

    def fake_file_scan(**kwargs):
        seen["file"] = kwargs
        return {"status": "ok", "complete": True, "pending_turns": 0,
                "extracted_turns": 0, "oldest_pending_at": None}

    def fake_sql_scan(**kwargs):
        seen["sql"] = kwargs
        return {"status": "ok", "complete": True, "pending_turns": 0,
                "extracted_turns": 0, "changed_sources": 0,
                "oldest_pending_at": None}

    monkeypatch.setattr(cli.eod_capture, "memory_backlog", fake_file_scan)
    import codex_sql_memory as csm
    monkeypatch.setattr(csm, "scan", fake_sql_scan)
    rc = cli.main([
        "report", "--codex-root", str(app_root),
        "--state-root", str(state_root),
        "--automation-root", str(automation_root),
        "--since", "2026-09-13T00:00:00+08:00",
        "--through", "2026-10-25T23:59:59+08:00",
        "--max-scan-seconds", "0.5", "--now", NOW,
    ])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert seen["file"]["since"] == "2026-09-13"
    assert seen["file"]["through"] == "2026-10-25"
    assert "sql" in seen, "SQL reader must be attempted for full-window ISO input"
    assert seen["sql"]["since"] == "2026-09-13"
    assert seen["sql"]["through"] == "2026-10-25"
    assert out["source_window"]["since"] == "2026-09-13"
    assert out["source_window"]["through"] == "2026-10-25"


def test_cli_iso_datetime_widened_window_rejected_before_collection(
    tmp_path: Path, capsys, monkeypatch
):
    app_root = tmp_path / "app"
    app_root.mkdir()
    state_root = tmp_path / "state"
    state_root.mkdir()
    automation_root = tmp_path / "automations"
    automation_root.mkdir()
    cli = _cli_module()
    calls = {"file": 0, "sql": 0}

    def fake_file_scan(**kwargs):
        calls["file"] += 1
        return {}

    def fake_sql_scan(**kwargs):
        calls["sql"] += 1
        return {}

    monkeypatch.setattr(cli.eod_capture, "memory_backlog", fake_file_scan)
    import codex_sql_memory as csm
    monkeypatch.setattr(csm, "scan", fake_sql_scan)
    for since, through in (
        ("2026-09-01T00:00:00+08:00", "2026-10-25"),
        ("2026-09-13", "2026-12-31T23:59:59+08:00"),
    ):
        with pytest.raises(SystemExit):
            cli.main([
                "report", "--codex-root", str(app_root),
                "--state-root", str(state_root),
                "--automation-root", str(automation_root),
                "--since", since, "--through", through,
                "--max-scan-seconds", "0.5", "--now", NOW,
            ])
    assert calls == {"file": 0, "sql": 0}
    capsys.readouterr()


def test_cli_automation_root_derived_from_codex_home(
    tmp_path: Path, capsys, monkeypatch
):
    relocated = tmp_path / "relocated-codex"
    (relocated / "sessions").mkdir(parents=True)
    state_root = tmp_path / "state"
    state_root.mkdir()
    monkeypatch.setenv("CODEX_HOME", str(relocated))
    monkeypatch.delenv("K2B_CODEX_SESSIONS_ROOT", raising=False)
    cli = _cli_module()
    seen: dict = {}

    def fake_native(**kwargs):
        seen["native"] = kwargs
        return {"registration_state": "active"}

    def fake_file_scan(**kwargs):
        seen["file"] = kwargs
        return {"status": "ok", "complete": True, "pending_turns": 0,
                "extracted_turns": 0, "oldest_pending_at": None}

    def fake_sql_scan(**kwargs):
        seen["sql"] = kwargs
        return {"status": "ok", "complete": True, "pending_turns": 0,
                "extracted_turns": 0, "changed_sources": 0,
                "oldest_pending_at": None}

    monkeypatch.setattr(
        cli.eod_capture.native_job_status, "read_native_job_status", fake_native)
    monkeypatch.setattr(cli.eod_capture, "memory_backlog", fake_file_scan)
    import codex_sql_memory as csm
    monkeypatch.setattr(csm, "scan", fake_sql_scan)
    rc = cli.main([
        "report", "--state-root", str(state_root),
        "--max-scan-seconds", "0.5", "--now", NOW,
    ])
    assert rc == 0
    capsys.readouterr()
    assert Path(seen["native"]["automation_root"]) == relocated / "automations"
    assert Path(seen["sql"]["codex_root"]) == relocated
    assert Path(seen["file"]["codex_root"]) == relocated / "sessions"


def test_cli_automation_root_derived_from_explicit_app_root(
    tmp_path: Path, capsys, monkeypatch
):
    app_root = tmp_path / "explicit-codex"
    (app_root / "sessions").mkdir(parents=True)
    other_home = tmp_path / "other-codex"
    other_home.mkdir()
    state_root = tmp_path / "state"
    state_root.mkdir()
    automation_root = tmp_path / "automations"
    automation_root.mkdir()
    monkeypatch.setenv("CODEX_HOME", str(other_home))
    monkeypatch.delenv("K2B_CODEX_SESSIONS_ROOT", raising=False)
    cli = _cli_module()
    seen: dict = {}

    def fake_native(**kwargs):
        seen["native"] = kwargs
        return {"registration_state": "active"}

    def fake_file_scan(**kwargs):
        return {"status": "ok", "complete": True, "pending_turns": 0,
                "extracted_turns": 0, "oldest_pending_at": None}

    def fake_sql_scan(**kwargs):
        return {"status": "ok", "complete": True, "pending_turns": 0,
                "extracted_turns": 0, "changed_sources": 0,
                "oldest_pending_at": None}

    monkeypatch.setattr(
        cli.eod_capture.native_job_status, "read_native_job_status", fake_native)
    monkeypatch.setattr(cli.eod_capture, "memory_backlog", fake_file_scan)
    import codex_sql_memory as csm
    monkeypatch.setattr(csm, "scan", fake_sql_scan)
    for args, expected in (
        (["--codex-root", str(app_root)], app_root / "automations"),
        (["--codex-root", str(app_root), "--automation-root", str(automation_root)],
         automation_root),
    ):
        rc = cli.main([
            "report", *args, "--state-root", str(state_root),
            "--max-scan-seconds", "0.5", "--now", NOW,
        ])
        assert rc == 0
        capsys.readouterr()
        assert Path(seen["native"]["automation_root"]) == expected


def test_cli_sql_reader_unavailable_is_partial(tmp_path: Path, capsys, monkeypatch):
    monkeypatch.setenv("K2B_CODEX_SESSIONS_ROOT", str(tmp_path / "missing"))
    codex_root = tmp_path / "empty-codex"
    codex_root.mkdir()
    state_root = tmp_path / "state"
    state_root.mkdir()
    automation_root = tmp_path / "automations"
    automation_root.mkdir()
    cli = _cli_module()
    import codex_sql_memory as csm  # noqa: F401
    original = csm.scan
    def failing_scan(**kwargs):
        raise ValueError("SQL not approved")
    monkeypatch.setattr(csm, "scan", failing_scan)
    rc = cli.main([
        "report",
        "--codex-root", str(codex_root),
        "--state-root", str(state_root),
        "--automation-root", str(automation_root),
        "--max-scan-seconds", "0.5",
        "--now", NOW,
    ])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    sql = out["source_coverage"]["sql_backed"]
    if isinstance(sql, dict):
        assert sql["status"] in ("unavailable", "partial", "not_attempted")
        if sql["status"] == "unavailable":
            assert sql["pending_turns"] is None
    else:
        assert sql == "unavailable"
    # complete is never claimed
    assert out["source_coverage"]["status"] != "complete"
    assert out["source_coverage"]["complete"] is False
