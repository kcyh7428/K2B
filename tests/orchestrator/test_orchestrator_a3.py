#!/usr/bin/env python3
"""pytest coverage for orchestrator Phase A3 (ship-to-engine, Stage 11)."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
K2BI_REPO = Path("~/Projects/K2Bi").expanduser()


@pytest.fixture(autouse=True)
def temp_env(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    vault.mkdir()
    db = tmp_path / "orch.sqlite"
    k2bi_vault = tmp_path / "k2bi-vault"
    k2bi_vault.mkdir()
    ws = tmp_path / "k2bi"
    ws.mkdir()
    monkeypatch.setenv("K2B_VAULT_PATH", str(vault))
    monkeypatch.setenv("K2BI_VAULT_PATH", str(k2bi_vault))
    monkeypatch.setenv("K2B_ORCH_DB", str(db))
    monkeypatch.setenv("K2B_ORCH_K2BI_WORKSPACE", str(ws))
    for key in list(sys.modules):
        if key.startswith("scripts.lib.orchestrator"):
            del sys.modules[key]


@pytest.fixture
def store(temp_env, tmp_path):
    from scripts.lib import orchestrator_store as store

    db = tmp_path / "orch.sqlite"
    vault = tmp_path / "vault"
    store.DB_PATH = str(db)
    store.RESULTS_DIR = str(vault / "raw" / "orchestrator-results")
    store.BOARD_PATH = str(vault / "System" / "orchestrator" / "board.md")
    store.K2B_VAULT = str(vault)
    store.init_db(store.connect())
    os.makedirs(store.RESULTS_DIR, exist_ok=True)
    return store


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    )


def _init_git_repo(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "K2B Test")
    (repo / "README.md").write_text("# repo\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "initial")
    (repo / "execution" / "validators").mkdir(parents=True, exist_ok=True)
    (repo / "execution" / "validators" / "config.yaml").write_text(
        "validators:\n  enabled: true\n"
        "instrument_whitelist:\n  symbols:\n  - CDNS\n  - SPY\n  - G\n",
        encoding="utf-8",
    )
    _git(repo, "add", "execution/validators/config.yaml")
    _git(repo, "commit", "-m", "validators")


def _write_registry(vault: Path, *symbols: str) -> Path:
    path = vault / "wiki" / "tickers" / "canonical-registry.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({symbol: {"name": f"{symbol} Corp"} for symbol in symbols}),
        encoding="utf-8",
    )
    return path


def _strategy_file(
    path: Path,
    *,
    slug: str = "cdns",
    ticker: str = "CDNS",
    status: str = "proposed",
    approved_commit_sha: str | None = None,
    body: str = "v1",
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    approved = ""
    if approved_commit_sha is not None:
        approved = f"approved_commit_sha: {approved_commit_sha}\napproved_at: '2026-06-08T00:00:00+00:00'\n"
    path.write_text(
        "---\n"
        f"name: {slug}\n"
        f"ticker: {ticker}\n"
        f"status: {status}\n"
        f"{approved}"
        "order:\n"
        f"  ticker: {ticker}\n"
        "---\n"
        f"# Strategy {slug}\n{body}\n",
        encoding="utf-8",
    )
    return path


def _backtest_capture(path: Path, *, slug: str = "cdns", look_ahead_check: str = "passed") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "---\n"
        f"strategy_slug: {slug}\n"
        "backtest:\n"
        f"  look_ahead_check: {look_ahead_check}\n"
        "---\n"
        "ok\n",
        encoding="utf-8",
    )
    return path


def _approved_strategy_payload(strategy_path: Path, backtest_path: Path) -> dict:
    return {
        "promote_done": True,
        "screen_done": True,
        "screen_approved_by_operator": True,
        "thesis_written": True,
        "thesis_artifact_verified": True,
        "thesis_path": str(strategy_path.parent / "CDNS.md"),
        "thesis_artifact_sha256": "0" * 64,
        "bear_done": True,
        "bear_verdict": "PROCEED",
        "bear_conviction": 60,
        "thesis_approved": True,
        "strategy_spec_written": True,
        "strategy_path": str(strategy_path),
        "strategy_artifact_verified": True,
        "strategy_artifact_sha256": _sha256(strategy_path),
        "backtest_done": True,
        "backtest_artifact_path": str(backtest_path),
        "backtest_artifact_sha256": _sha256(backtest_path),
        "backtest_look_ahead_check": "passed",
        "strategy_approved": True,
    }


def _a3_parent(store, tmp_path, *, payload_updates=None, entity="CDNS"):
    strategy = _strategy_file(tmp_path / "strategy_cdns.md")
    thesis = strategy.parent / "CDNS.md"
    thesis.write_text("# CDNS thesis\n", encoding="utf-8")
    backtest = _backtest_capture(tmp_path / "2026-06-07_cdns_backtest.md")
    payload = _approved_strategy_payload(strategy, backtest)
    payload["thesis_artifact_sha256"] = _sha256(thesis)
    if payload_updates:
        payload.update(payload_updates)
    return store.add_task(
        assignee_profile="k2b",
        command_key="k2b-a1-chain",
        success_criteria="A3 chain parked",
        permissions="agent-native",
        entity_key=entity,
        status="needs_human",
        payload=payload,
    )


def _dispatched_ship(store, tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    _init_git_repo(repo)
    monkeypatch.setenv("K2B_ORCH_K2BI_WORKSPACE", str(repo))
    proposed = _strategy_file(repo / "wiki" / "strategies" / "strategy_cdns.md")
    tid = _a3_parent(
        store,
        tmp_path,
        payload_updates={
            "ship_authorized": True,
            "ship_repo_authored": True,
            "ship_strategy_repo_path": str(proposed),
            "ship_strategy_repo_sha256": _sha256(proposed),
            "strategy_artifact_sha256": _sha256(proposed),
        },
    )
    ok, dispatch = store.a1_mark_ship_dispatch_started(tid)
    assert ok, dispatch
    return repo, proposed, tid


def _patch_lock_to_mutate_once(store, monkeypatch, mutate):
    real_acquire_lock = store._acquire_lock
    mutated = False

    @contextmanager
    def mutating_lock():
        nonlocal mutated
        with real_acquire_lock():
            if not mutated:
                mutated = True
                mutate()
            yield

    monkeypatch.setattr(store, "_acquire_lock", mutating_lock)


def _ship_token(slug: str, sha: str, approved_at: str, lease: str) -> str:
    return f"APPROVE_STRATEGY:{slug}:{sha}:{approved_at}:{lease}"


def _strategy_decision_dict(**overrides) -> dict:
    data = {
        "slug": "cdns",
        "symbol": "CDNS",
        "sigid": "2026-06-07-cdns-signal",
        "risk_envelope_pct": "0.0025",
        "order": {
            "ticker": "CDNS",
            "side": "buy",
            "qty": 1,
            "order_type": "LMT",
            "limit_price": "300.00",
            "stop_loss": "270.00",
            "time_in_force": "DAY",
        },
        "forward_guidance_metrics": [
            {
                "metric": "none",
                "locked_threshold_text": "No thresholded metric",
                "guide_source_text": "operator-pasted: no guide applies",
                "guide_range_text": "no quantitative guide",
                "sits_inside_guide": False,
            }
        ],
        "forward_guidance_status": "pass",
        "how_this_works": "Buy CDNS only while the operator-approved thesis holds.",
        "bucket_rules": ["Bucket 4 exits when thesis-breaking news lands."],
        "entry_rules": ["Enter only after operator confirms the rule set."],
        "stop_rules": ["Stop at 270.00."],
        "target_rules": ["Review at 340.00 and 380.00."],
        "hold_rules": ["Maximum hold is 30 trading days."],
        "kill_rules": ["Kill if the thesis is invalidated."],
        "accepted_gaps": ["No regime filter for this first paper trade."],
        "forward_guidance_override_reason": None,
        "forward_guidance_waive_reason": None,
        "regime_filter": [],
        "date": "2026-06-07",
        "extra_frontmatter": None,
    }
    data.update(overrides)
    return data


class TestA3OracleAndHelpers:
    def test_backward_compat_no_ship_authorized_still_parks(self, store, tmp_path):
        tid = _a3_parent(store, tmp_path)
        assert store.a1_resume_action(tid) == "strategy_approved_await_ship"

    def test_a3_ladder_to_terminal_shipped(self, store, tmp_path, monkeypatch):
        repo = tmp_path / "repo"
        _init_git_repo(repo)
        monkeypatch.setenv("K2B_ORCH_K2BI_WORKSPACE", str(repo))
        proposed = _strategy_file(repo / "wiki" / "strategies" / "strategy_cdns.md")
        tid = _a3_parent(store, tmp_path, payload_updates={"strategy_artifact_sha256": _sha256(proposed)})

        ok, reason = store.a1_authorize_ship(tid)
        assert ok, reason
        assert store.a1_resume_action(tid) == "author_strategy_to_repo"

        ok, reason = store.a1_record_ship_repo_authored(tid, str(proposed))
        assert ok, reason
        assert store.a1_resume_action(tid) == "dispatch_ship"

        ok, dispatch = store.a1_mark_ship_dispatch_started(tid)
        assert ok, dispatch
        assert re.match(r"^cdns-ship-a1-\d{8}T\d{6}Z$", dispatch["lease_id"])
        assert dispatch["repo_sha"] == _sha256(proposed)
        assert store.a1_resume_action(tid) == "verify_ship"

        head_before = _git(repo, "rev-parse", "HEAD").stdout.strip()
        _strategy_file(proposed, status="approved", approved_commit_sha=head_before)
        _git(repo, "add", "wiki/strategies/strategy_cdns.md")
        _git(repo, "commit", "-m", "Approve strategy")
        ok, reason = store.a1_verify_ship(tid)
        assert ok, reason
        task = store.get_task(tid)
        payload = json.loads(task["payload"])
        assert task["status"] == "terminal_shipped"
        assert payload["ship_verified"] is True
        assert payload["ship_commit_sha"] == _git(repo, "rev-parse", "HEAD").stdout.strip()
        assert set(payload["ship_inspect"]) == {"state", "head", "file_sha256", "strategy_status"}
        assert store.a1_resume_action(tid) == "terminal_shipped"

    def test_authorize_ship_requires_strategy_gate(self, store, tmp_path):
        tid = _a3_parent(store, tmp_path, payload_updates={"strategy_approved": False})
        ok, reason = store.a1_authorize_ship(tid)
        assert not ok
        assert "strategy_approved_await_ship" in reason

    def test_record_ship_repo_authored_refuses_sha_mismatch(self, store, tmp_path):
        repo_strategy = _strategy_file(tmp_path / "repo" / "wiki" / "strategies" / "strategy_cdns.md")
        tid = _a3_parent(store, tmp_path, payload_updates={"ship_authorized": True})
        repo_strategy.write_text(repo_strategy.read_text(encoding="utf-8") + "\ndrift\n", encoding="utf-8")
        ok, reason = store.a1_record_ship_repo_authored(tid, str(repo_strategy))
        assert not ok
        assert "sha256" in reason

    def test_record_ship_repo_authored_accepts_completed_at_drift(self, store, tmp_path):
        # K2Bi's write_complete_strategy_spec stamps forward_guidance_check.completed_at at
        # write time, so a faithful re-author of the SAME approved decision into the repo
        # differs from the A2-approved vault file ONLY by that line (live-MVP finding,
        # 2026-06-08). The bind must accept it (normalized-equal, vault still approved).
        tmpl = (
            "---\nname: cdns\nticker: CDNS\nstatus: proposed\n"
            "forward_guidance_check:\n  status: pass\n  completed_at: {ts}\n"
            "order:\n  ticker: CDNS\n---\n# Strategy cdns\nbody\n"
        )
        vault = tmp_path / "vault_cdns.md"
        vault.write_text(tmpl.format(ts="'2026-06-07T23:09:01.053512'"), encoding="utf-8")
        repo = tmp_path / "repo" / "wiki" / "strategies" / "strategy_cdns.md"
        repo.parent.mkdir(parents=True)
        repo.write_text(tmpl.format(ts="'2026-06-08T16:21:04.629593'"), encoding="utf-8")
        assert _sha256(repo) != _sha256(vault)  # raw bytes differ (only the timestamp)
        tid = _a3_parent(store, tmp_path, payload_updates={
            "ship_authorized": True,
            "strategy_path": str(vault),
            "strategy_artifact_sha256": _sha256(vault),
        })
        ok, reason = store.a1_record_ship_repo_authored(tid, str(repo))
        assert ok, reason
        assert json.loads(store.get_task(tid)["payload"])["ship_repo_authored"] is True

    def test_record_ship_repo_authored_refuses_material_drift_modulo_timestamp(self, store, tmp_path):
        # A difference on a MATERIAL line (not the write timestamp) still refuses even
        # after completed_at is normalized away.
        tmpl = (
            "---\nname: cdns\nticker: CDNS\nstatus: proposed\n"
            "forward_guidance_check:\n  status: pass\n  completed_at: {ts}\n"
            "order:\n  ticker: CDNS\n---\n# Strategy cdns\n{body}\n"
        )
        vault = tmp_path / "vault_cdns.md"
        vault.write_text(tmpl.format(ts="'2026-06-07T23:09:01.053512'", body="body"), encoding="utf-8")
        repo = tmp_path / "repo" / "wiki" / "strategies" / "strategy_cdns.md"
        repo.parent.mkdir(parents=True)
        repo.write_text(tmpl.format(ts="'2026-06-08T16:21:04.629593'", body="TAMPERED"), encoding="utf-8")
        tid = _a3_parent(store, tmp_path, payload_updates={
            "ship_authorized": True,
            "strategy_path": str(vault),
            "strategy_artifact_sha256": _sha256(vault),
        })
        ok, reason = store.a1_record_ship_repo_authored(tid, str(repo))
        assert not ok
        assert "does not match" in reason

    def test_record_ship_repo_authored_refuses_wrong_ticker(self, store, tmp_path):
        repo_strategy = _strategy_file(
            tmp_path / "repo" / "wiki" / "strategies" / "strategy_spy.md",
            slug="spy",
            ticker="SPY",
        )
        tid = _a3_parent(
            store,
            tmp_path,
            payload_updates={
                "ship_authorized": True,
                "strategy_artifact_sha256": _sha256(repo_strategy),
            },
        )
        ok, reason = store.a1_record_ship_repo_authored(tid, str(repo_strategy))
        assert not ok
        assert "entity" in reason

    def test_mark_ship_dispatch_started_bounds_attempts(self, store, tmp_path):
        repo_strategy = _strategy_file(tmp_path / "repo" / "wiki" / "strategies" / "strategy_cdns.md")
        tid = _a3_parent(
            store,
            tmp_path,
            payload_updates={
                "ship_authorized": True,
                "ship_repo_authored": True,
                "ship_strategy_repo_path": str(repo_strategy),
                "ship_strategy_repo_sha256": _sha256(repo_strategy),
                "ship_attempt_count": 3,
            },
        )
        ok, reason = store.a1_mark_ship_dispatch_started(tid)
        assert not ok
        assert "attempt limit" in reason
        payload = json.loads(store.get_task(tid)["payload"])
        assert payload["terminal_reason"] == "ship_attempt_limit_exceeded"
        assert store.a1_resume_action(tid) == "needs_human_terminal"

    def test_mark_ship_dispatch_started_refuses_missing_recorded_repo_sha(self, store, tmp_path):
        repo_strategy = _strategy_file(tmp_path / "repo" / "wiki" / "strategies" / "strategy_cdns.md")
        tid = _a3_parent(
            store,
            tmp_path,
            payload_updates={
                "ship_authorized": True,
                "ship_repo_authored": True,
                "ship_strategy_repo_path": str(repo_strategy),
                "ship_strategy_repo_sha256": "",
                "strategy_artifact_sha256": _sha256(repo_strategy),
            },
        )
        ok, reason = store.a1_mark_ship_dispatch_started(tid)
        assert not ok
        assert "ship_strategy_repo_sha256" in reason
        payload = json.loads(store.get_task(tid)["payload"])
        assert "ship_dispatch_started_at" not in payload

    def test_mark_ship_dispatch_started_refuses_missing_git_repo_baseline(self, store, tmp_path):
        repo_strategy = _strategy_file(tmp_path / "repo" / "wiki" / "strategies" / "strategy_cdns.md")
        tid = _a3_parent(
            store,
            tmp_path,
            payload_updates={
                "ship_authorized": True,
                "ship_repo_authored": True,
                "ship_strategy_repo_path": str(repo_strategy),
                "ship_strategy_repo_sha256": _sha256(repo_strategy),
                "strategy_artifact_sha256": _sha256(repo_strategy),
            },
        )
        ok, reason = store.a1_mark_ship_dispatch_started(tid)
        assert not ok
        assert "cannot establish ship baseline" in reason
        payload = json.loads(store.get_task(tid)["payload"])
        assert "ship_attempt_count" not in payload
        assert "ship_dispatch_started_at" not in payload
        assert "ship_repo_head_before" not in payload
        assert "ship_lease_id" not in payload
        assert "ship_approval_token" not in payload
        assert store.a1_resume_action(tid) == "dispatch_ship"

    def test_mark_ship_dispatch_started_refuses_unavailable_git_head_baseline(
        self, store, tmp_path, monkeypatch
    ):
        repo = tmp_path / "repo"
        _init_git_repo(repo)
        repo_strategy = _strategy_file(repo / "wiki" / "strategies" / "strategy_cdns.md")
        tid = _a3_parent(
            store,
            tmp_path,
            payload_updates={
                "ship_authorized": True,
                "ship_repo_authored": True,
                "ship_strategy_repo_path": str(repo_strategy),
                "ship_strategy_repo_sha256": _sha256(repo_strategy),
                "strategy_artifact_sha256": _sha256(repo_strategy),
            },
        )
        monkeypatch.setattr(store, "_a3_git_head", lambda _repo: None)

        ok, reason = store.a1_mark_ship_dispatch_started(tid)
        assert not ok
        assert "cannot establish ship baseline" in reason
        payload = json.loads(store.get_task(tid)["payload"])
        assert "ship_attempt_count" not in payload
        assert "ship_dispatch_started_at" not in payload
        assert "ship_repo_head_before" not in payload
        assert "ship_lease_id" not in payload
        assert "ship_approval_token" not in payload
        assert store.a1_resume_action(tid) == "dispatch_ship"


class TestA3ShipInspector:
    def test_inspector_classifies_clean_rollback_without_worker_rollback_result(self, store, tmp_path, monkeypatch):
        repo = tmp_path / "repo"
        _init_git_repo(repo)
        monkeypatch.setenv("K2B_ORCH_K2BI_WORKSPACE", str(repo))
        proposed = _strategy_file(repo / "wiki" / "strategies" / "strategy_cdns.md")
        tid = _a3_parent(
            store,
            tmp_path,
            payload_updates={
                "ship_authorized": True,
                "ship_repo_authored": True,
                "ship_strategy_repo_path": str(proposed),
                "ship_strategy_repo_sha256": _sha256(proposed),
                "ship_dispatch_started_at": "2026-06-08T00:00:00+00:00",
            },
        )
        state = store.a1_inspect_ship_state(tid)
        assert state["state"] == "clean_rollback"
        ok, reason = store.a1_record_ship_failed(tid, reason="iss.ValidationError: bear stale")
        assert ok, reason
        payload = json.loads(store.get_task(tid)["payload"])
        assert payload["ship_rollback_clean"] is True
        assert store.a1_resume_action(tid) == "ship_rolled_back"

    def test_inspector_classifies_partial_approved_uncommitted(self, store, tmp_path, monkeypatch):
        repo = tmp_path / "repo"
        _init_git_repo(repo)
        monkeypatch.setenv("K2B_ORCH_K2BI_WORKSPACE", str(repo))
        proposed = _strategy_file(repo / "wiki" / "strategies" / "strategy_cdns.md")
        proposed_sha = _sha256(proposed)
        tid = _a3_parent(
            store,
            tmp_path,
            payload_updates={
                "ship_authorized": True,
                "ship_repo_authored": True,
                "ship_strategy_repo_path": str(proposed),
                "ship_strategy_repo_sha256": proposed_sha,
                "ship_dispatch_started_at": "2026-06-08T00:00:00+00:00",
            },
        )
        _strategy_file(proposed, status="approved", approved_commit_sha=_git(repo, "rev-parse", "HEAD").stdout.strip())
        state = store.a1_inspect_ship_state(tid)
        assert state["state"] == "partial_approved_uncommitted"
        ok, reason = store.a1_verify_ship(tid)
        assert not ok
        assert "partial_approved_uncommitted" in reason
        assert store.get_task(tid)["status"] == "needs_human"
        assert store.a1_resume_action(tid) == "ship_partial"

    def test_inspector_classifies_incomplete_rollback_marker(self, store, tmp_path, monkeypatch):
        repo = tmp_path / "repo"
        _init_git_repo(repo)
        monkeypatch.setenv("K2B_ORCH_K2BI_WORKSPACE", str(repo))
        proposed = _strategy_file(repo / "wiki" / "strategies" / "strategy_cdns.md")
        marker = repo / ".k2bi-orchestrator" / "rollback" / "cdns.json"
        marker.parent.mkdir(parents=True)
        marker.write_text('{"phase":"started"}\n', encoding="utf-8")
        tid = _a3_parent(
            store,
            tmp_path,
            payload_updates={
                "ship_authorized": True,
                "ship_repo_authored": True,
                "ship_strategy_repo_path": str(proposed),
                "ship_strategy_repo_sha256": _sha256(proposed),
            },
        )
        assert store.a1_inspect_ship_state(tid)["state"] == "incomplete_rollback_marker"

    def test_retry_ship_requires_live_clean_rollback(self, store, tmp_path, monkeypatch):
        repo = tmp_path / "repo"
        _init_git_repo(repo)
        monkeypatch.setenv("K2B_ORCH_K2BI_WORKSPACE", str(repo))
        proposed = _strategy_file(repo / "wiki" / "strategies" / "strategy_cdns.md")
        tid = _a3_parent(
            store,
            tmp_path,
            payload_updates={
                "ship_authorized": True,
                "ship_repo_authored": True,
                "ship_strategy_repo_path": str(proposed),
                "ship_strategy_repo_sha256": _sha256(proposed),
                "ship_dispatch_started_at": "2026-06-08T00:00:00+00:00",
                "ship_rolled_back_at": "2026-06-08T00:01:00+00:00",
                "ship_rollback_reason": "gate refused",
                "ship_attempt_count": 1,
            },
        )
        ok, reason = store.a1_retry_ship_after_rollback(tid)
        assert ok, reason
        payload = json.loads(store.get_task(tid)["payload"])
        assert "ship_rolled_back_at" not in payload
        assert "ship_dispatch_started_at" not in payload
        assert store.a1_resume_action(tid) == "dispatch_ship"

    def test_inspector_does_not_classify_clean_approved_file_without_new_head_as_committed(
        self, store, tmp_path, monkeypatch
    ):
        repo = tmp_path / "repo"
        _init_git_repo(repo)
        monkeypatch.setenv("K2B_ORCH_K2BI_WORKSPACE", str(repo))
        strategy = _strategy_file(
            repo / "wiki" / "strategies" / "strategy_cdns.md",
            status="approved",
        )
        _git(repo, "add", "wiki/strategies/strategy_cdns.md")
        _git(repo, "commit", "-m", "Preexisting approved strategy")
        tid = _a3_parent(
            store,
            tmp_path,
            payload_updates={
                "ship_authorized": True,
                "ship_repo_authored": True,
                "ship_strategy_repo_path": str(strategy),
                "ship_strategy_repo_sha256": _sha256(strategy),
                "strategy_path": str(strategy),
                "strategy_artifact_sha256": _sha256(strategy),
            },
        )
        ok, dispatch = store.a1_mark_ship_dispatch_started(tid)
        assert ok, dispatch

        inspect = store.a1_inspect_ship_state(tid)
        assert inspect["state"] != "committed"
        ok, reason = store.a1_verify_ship(tid)
        assert not ok
        assert "committed" not in store.get_task(tid)["status"]

    def test_verify_ship_reinspects_after_acquiring_lock(self, store, tmp_path, monkeypatch):
        repo, strategy, tid = _dispatched_ship(store, tmp_path, monkeypatch)
        head_before = _git(repo, "rev-parse", "HEAD").stdout.strip()
        _strategy_file(strategy, status="approved", approved_commit_sha=head_before)
        _git(repo, "add", "wiki/strategies/strategy_cdns.md")
        _git(repo, "commit", "-m", "Approve strategy")

        def mutate():
            _strategy_file(strategy, status="approved", approved_commit_sha=head_before, body="dirty")

        _patch_lock_to_mutate_once(store, monkeypatch, mutate)
        ok, reason = store.a1_verify_ship(tid)
        assert not ok
        assert "partial_approved_uncommitted" in reason
        task = store.get_task(tid)
        assert task["status"] == "needs_human"
        payload = json.loads(task["payload"])
        assert payload["ship_inspect_state"] == "partial_approved_uncommitted"

    def test_record_ship_failed_reinspects_after_acquiring_lock(self, store, tmp_path, monkeypatch):
        _, strategy, tid = _dispatched_ship(store, tmp_path, monkeypatch)

        def mutate():
            _strategy_file(strategy, status="approved", approved_commit_sha="pre-lock", body="dirty")

        _patch_lock_to_mutate_once(store, monkeypatch, mutate)
        ok, reason = store.a1_record_ship_failed(tid, reason="worker failed")
        assert ok, reason
        payload = json.loads(store.get_task(tid)["payload"])
        assert payload["ship_rollback_clean"] is False
        assert payload["ship_inspect_state"] == "partial_approved_uncommitted"
        assert "ship_partial_detected_at" in payload

    def test_retry_ship_after_rollback_reinspects_after_acquiring_lock(self, store, tmp_path, monkeypatch):
        _, strategy, tid = _dispatched_ship(store, tmp_path, monkeypatch)
        task = store.get_task(tid)
        payload = json.loads(task["payload"])
        payload["ship_rolled_back_at"] = "2026-06-08T00:01:00+00:00"
        payload["ship_rollback_reason"] = "first failure"
        conn = store.connect()
        store._update_payload_locked(conn, tid, payload, status="needs_human")
        conn.commit()
        conn.close()

        def mutate():
            _strategy_file(strategy, status="approved", approved_commit_sha="pre-lock", body="dirty")

        _patch_lock_to_mutate_once(store, monkeypatch, mutate)
        ok, reason = store.a1_retry_ship_after_rollback(tid)
        assert not ok
        assert "partial_approved_uncommitted" in reason
        payload = json.loads(store.get_task(tid)["payload"])
        assert payload["ship_rolled_back_at"] == "2026-06-08T00:01:00+00:00"

    def test_record_ship_failed_preserves_first_failure_audit(self, store, tmp_path, monkeypatch):
        _, _, tid = _dispatched_ship(store, tmp_path, monkeypatch)
        ok, reason = store.a1_record_ship_failed(tid, reason="first failure")
        assert ok, reason
        first_payload = json.loads(store.get_task(tid)["payload"])

        ok, reason = store.a1_record_ship_failed(tid, reason="second failure")
        assert ok, reason
        payload = json.loads(store.get_task(tid)["payload"])
        assert payload["ship_failed_at"] == first_payload["ship_failed_at"]
        assert payload["ship_failure_reason"] == "first failure"


class TestA3TerminalLockingAndPoll:
    def test_terminal_shipped_releases_entity_lock(self, store, tmp_path):
        parent = _a3_parent(store, tmp_path)
        store.transition(parent, "terminal_shipped")
        fresh = store.add_task(
            assignee_profile="k2b",
            command_key="k2b-a1-chain",
            success_criteria="fresh chain",
            permissions="agent-native",
            entity_key="CDNS",
            status="needs_human",
        )
        assert fresh != parent

    def test_poll_once_cancels_out_of_order_a3_child(self, store, tmp_path, monkeypatch):
        monkeypatch.setattr(store, "notify", lambda *a, **k: None)
        from scripts.lib import orchestrator_profiles as profiles

        _write_registry(tmp_path / "k2bi-vault", "CDNS")
        parent = _a3_parent(store, tmp_path)
        assert store.a1_resume_action(parent) == "strategy_approved_await_ship"
        child = store.add_task(
            assignee_profile="k2bi",
            command_key="k2bi-run-full-ship",
            success_criteria="out of order",
            permissions="analyst-command",
            flight_id=parent,
            parent_task=parent,
            entity_key="CDNS",
            status="ready",
            payload={
                "symbol": "CDNS",
                "payload_path": "/tmp/k2b-orchestrator/ship.json",
                "strategy_path": str(tmp_path / "repo" / "wiki" / "strategies" / "strategy_cdns.md"),
                "approval": {
                    "final_approval_token": "x",
                    "approved_by": "keith",
                    "approved_at": "2026-06-08T00:00:00+00:00",
                    "ship_lease_id": "cdns-ship-a1-20260608T000000Z",
                },
                "vault_root": str(tmp_path / "k2bi-vault"),
            },
        )
        store.poll_once()
        assert store.get_task(child)["status"] == "cancelled"

    def test_poll_once_admits_in_flight_ship_child_at_verify_ship(self, store, tmp_path, monkeypatch):
        # After mark-ship-dispatch-started records the dispatch intent + mints the token,
        # the oracle is at verify_ship, and the in-flight ship child legitimately runs
        # THEN. The stage guard must NOT cancel it as "out of order" (live-MVP fix
        # 2026-06-08; before the fix it expected dispatch_ship and killed the real ship).
        monkeypatch.setattr(store, "notify", lambda *a, **k: None)
        repo, proposed, parent = _dispatched_ship(store, tmp_path, monkeypatch)
        assert store.a1_resume_action(parent) == "verify_ship"
        child = store.add_task(
            assignee_profile="k2bi",
            command_key="k2bi-run-full-ship",
            success_criteria="in-flight ship",
            permissions="analyst-command",
            flight_id=parent,
            parent_task=parent,
            entity_key="CDNS",
            status="ready",
            payload={
                "symbol": "CDNS",
                "payload_path": "/tmp/k2b-orchestrator/ship.json",
                "strategy_path": str(proposed),
                "approval": {
                    "final_approval_token": "x",
                    "approved_by": "keith",
                    "approved_at": "2026-06-08T00:00:00+00:00",
                    "ship_lease_id": "cdns-ship-a1-20260608T000000Z",
                },
            },
        )
        store.poll_once()
        t = store.get_task(child)
        # NOT stage-cancelled (it may be blocked by the real capital preflight, which is fine).
        assert not (
            t["status"] == "cancelled" and "out of order" in (t.get("blocker_reason") or "")
        )


class TestA3Profiles:
    def test_a3_commands_allowlisted_with_carrier(self, store):
        from scripts.lib import orchestrator_profiles as profiles

        for key in ("k2bi-author-strategy-to-repo", "k2bi-run-full-ship"):
            argv = profiles.resolve_command(
                "k2bi", key, {"payload_path": "/tmp/k2b-orchestrator/p.json"}
            )
            assert argv is not None
            assert "--payload-path" in argv
            assert profiles.resolve_command("k2bi", key, {}) is None

    def test_author_strategy_repo_root_must_match_repo(self, store, tmp_path):
        from scripts.lib import orchestrator_profiles as profiles

        _write_registry(tmp_path / "k2bi-vault", "CDNS")
        task = {
            "assignee_profile": "k2bi",
            "command_key": "k2bi-author-strategy-to-repo",
            "entity_key": "CDNS",
            "payload": json.dumps(
                {
                    "symbol": "CDNS",
                    "repo_root": str(tmp_path / "k2bi-vault"),
                    "payload_json": json.dumps(
                        {"decision": {"slug": "cdns", "symbol": "CDNS", "order": {"ticker": "CDNS"}}}
                    ),
                }
            ),
        }
        ok, reason = profiles.preflight_k2bi(task)
        assert not ok
        assert "repo_root" in reason

    def test_capital_preflight_refuses_kill_switch_without_writing_it(self, store, tmp_path, monkeypatch):
        from scripts.lib import orchestrator_profiles as profiles

        repo = tmp_path / "k2bi"
        _init_git_repo(repo)
        vault = tmp_path / "k2bi-vault"
        (vault / "System").mkdir(parents=True, exist_ok=True)
        kill = vault / "System" / ".killed"
        kill.write_text("operator\n", encoding="utf-8")
        monkeypatch.setenv("K2B_ORCH_K2BI_WORKSPACE", str(repo))
        monkeypatch.setenv("K2BI_VAULT_PATH", str(vault))
        _write_registry(vault, "CDNS")
        strategy = _strategy_file(repo / "wiki" / "strategies" / "strategy_cdns.md")
        payload = _ship_payload(strategy, vault)
        ok, reason = profiles.preflight_k2bi(_k2bi_task("k2bi-run-full-ship", payload))
        assert not ok
        assert "kill-switch" in reason
        assert kill.read_text(encoding="utf-8") == "operator\n"

    def test_capital_preflight_refuses_missing_validators_config(self, store, tmp_path, monkeypatch):
        from scripts.lib import orchestrator_profiles as profiles

        repo = tmp_path / "k2bi"
        _init_git_repo(repo)
        (repo / "execution" / "validators" / "config.yaml").unlink()
        vault = tmp_path / "k2bi-vault"
        vault.mkdir(exist_ok=True)
        monkeypatch.setenv("K2B_ORCH_K2BI_WORKSPACE", str(repo))
        monkeypatch.setenv("K2BI_VAULT_PATH", str(vault))
        _write_registry(vault, "CDNS")
        strategy = _strategy_file(repo / "wiki" / "strategies" / "strategy_cdns.md")
        payload = _ship_payload(strategy, vault)
        ok, reason = profiles.preflight_k2bi(_k2bi_task("k2bi-run-full-ship", payload))
        assert not ok
        assert "validators" in reason

    def test_capital_preflight_refuses_stale_sha_token(self, store, tmp_path, monkeypatch):
        from scripts.lib import orchestrator_profiles as profiles

        repo = tmp_path / "k2bi"
        _init_git_repo(repo)
        vault = tmp_path / "k2bi-vault"
        vault.mkdir(exist_ok=True)
        monkeypatch.setenv("K2B_ORCH_K2BI_WORKSPACE", str(repo))
        monkeypatch.setenv("K2BI_VAULT_PATH", str(vault))
        _write_registry(vault, "CDNS")
        strategy = _strategy_file(repo / "wiki" / "strategies" / "strategy_cdns.md")
        payload = _ship_payload(strategy, vault)
        payload["approval"]["final_approval_token"] = _ship_token(
            "cdns", "f" * 64, payload["approval"]["approved_at"], payload["approval"]["ship_lease_id"]
        )
        ok, reason = profiles.preflight_k2bi(_k2bi_task("k2bi-run-full-ship", payload))
        assert not ok
        assert "approval token" in reason

    def test_capital_preflight_refuses_unrelated_dirty_repo_path(self, store, tmp_path, monkeypatch):
        from scripts.lib import orchestrator_profiles as profiles

        repo = tmp_path / "k2bi"
        _init_git_repo(repo)
        vault = tmp_path / "k2bi-vault"
        vault.mkdir(exist_ok=True)
        monkeypatch.setenv("K2B_ORCH_K2BI_WORKSPACE", str(repo))
        monkeypatch.setenv("K2BI_VAULT_PATH", str(vault))
        _write_registry(vault, "CDNS")
        strategy = _strategy_file(repo / "wiki" / "strategies" / "strategy_cdns.md")
        (repo / "README.md").write_text("# dirty\n", encoding="utf-8")
        payload = _ship_payload(strategy, vault)
        ok, reason = profiles.preflight_k2bi(_k2bi_task("k2bi-run-full-ship", payload))
        assert not ok
        assert "dirty" in reason

    def test_capital_preflight_allows_clean_tree_except_target(self, store, tmp_path, monkeypatch):
        from scripts.lib import orchestrator_profiles as profiles

        repo = tmp_path / "k2bi"
        _init_git_repo(repo)
        vault = tmp_path / "k2bi-vault"
        vault.mkdir(exist_ok=True)
        monkeypatch.setenv("K2B_ORCH_K2BI_WORKSPACE", str(repo))
        monkeypatch.setenv("K2BI_VAULT_PATH", str(vault))
        _write_registry(vault, "CDNS")
        strategy = _strategy_file(repo / "wiki" / "strategies" / "strategy_cdns.md")
        payload = _ship_payload(strategy, vault)
        ok, reason = profiles.preflight_k2bi(_k2bi_task("k2bi-run-full-ship", payload))
        assert ok, reason

    def test_capital_preflight_refuses_non_whitelisted_ticker(self, store, tmp_path, monkeypatch):
        # Keith's workflow finding (2026-06-08): the ship gate must catch a
        # non-whitelisted ticker UPFRONT here, not as a last-step run_full_ship rollback.
        from scripts.lib import orchestrator_profiles as profiles

        repo = tmp_path / "k2bi"
        _init_git_repo(repo)
        # Rewrite the allowed-list WITHOUT CDNS (only SPY/G), then commit so the tree is clean.
        (repo / "execution" / "validators" / "config.yaml").write_text(
            "validators:\n  enabled: true\n"
            "instrument_whitelist:\n  symbols:\n  - SPY\n  - G\n",
            encoding="utf-8",
        )
        _git(repo, "add", "execution/validators/config.yaml")
        _git(repo, "commit", "-m", "drop CDNS from whitelist")
        vault = tmp_path / "k2bi-vault"
        vault.mkdir(exist_ok=True)
        monkeypatch.setenv("K2B_ORCH_K2BI_WORKSPACE", str(repo))
        monkeypatch.setenv("K2BI_VAULT_PATH", str(vault))
        _write_registry(vault, "CDNS")
        strategy = _strategy_file(repo / "wiki" / "strategies" / "strategy_cdns.md")
        payload = _ship_payload(strategy, vault)
        ok, reason = profiles.preflight_k2bi(_k2bi_task("k2bi-run-full-ship", payload))
        assert not ok
        assert "allowed-list" in reason and "/invest-propose-limits" in reason

    def test_ticker_whitelisted_helper(self, store, tmp_path, monkeypatch):
        from scripts.lib import orchestrator_profiles as profiles

        repo = tmp_path / "k2bi"
        _init_git_repo(repo)  # whitelist [CDNS, SPY, G]
        monkeypatch.setenv("K2B_ORCH_K2BI_WORKSPACE", str(repo))
        assert profiles.ticker_whitelisted("CDNS")[0] is True
        assert profiles.ticker_whitelisted("spy")[0] is True  # case-insensitive
        ok, reason = profiles.ticker_whitelisted("ZZZZ")
        assert ok is False and "allowed-list" in reason


def _ship_payload(strategy: Path, vault: Path) -> dict:
    approved_at = "2026-06-08T00:00:00+00:00"
    lease = "cdns-ship-a1-20260608T000000Z"
    sha = _sha256(strategy)
    return {
        "symbol": "CDNS",
        "payload_path": "/tmp/k2b-orchestrator/ship.json",
        "strategy_path": str(strategy),
        "vault_root": str(vault),
        "approval": {
            "final_approval_token": _ship_token("cdns", sha, approved_at, lease),
            "approved_by": "keith",
            "approved_at": approved_at,
            "ship_lease_id": lease,
        },
        "required_primary": "minimax",
    }


def _k2bi_task(command_key: str, payload: dict) -> dict:
    return {
        "assignee_profile": "k2bi",
        "command_key": command_key,
        "entity_key": "CDNS",
        "payload": json.dumps(payload),
    }


class TestA3AdapterRunner:
    def _setup(self, tmp_path):
        real = lambda p: Path(os.path.realpath(str(p)))
        ws = real(tmp_path) / "workspace"
        _write_a3_workspace(ws)
        repo = real(tmp_path) / "k2bi-repo"
        repo.mkdir()
        vault = real(tmp_path) / "k2bi-vault"
        vault.mkdir(exist_ok=True)
        paydir = real(tmp_path) / "payloads"
        paydir.mkdir()
        return ws, repo, vault, paydir

    def _run(self, cmd, *, workspace, payload, repo, vault, paydir, extra_env=None):
        payload_file = paydir / "p.json"
        payload_file.write_text(json.dumps(payload), encoding="utf-8")
        return subprocess.run(
            [
                sys.executable,
                str(REPO_ROOT / "scripts" / "lib" / "orchestrator_k2bi_adapter.py"),
                cmd,
                "--workspace",
                str(workspace),
                "--payload-path",
                str(payload_file),
            ],
            env={
                **os.environ,
                "K2B_ORCH_K2BI_WORKSPACE": str(repo),
                "K2BI_VAULT_PATH": str(vault),
                "K2B_ORCH_ADAPTER_PAYLOAD_DIR": str(paydir),
                **(extra_env or {}),
            },
            capture_output=True,
            text=True,
            timeout=30,
        )

    def test_runner_authors_strategy_to_repo_only(self, tmp_path):
        ws, repo, vault, paydir = self._setup(tmp_path)
        payload = {"symbol": "CDNS", "repo_root": str(repo), "decision": _strategy_decision_dict()}
        r = self._run("author-strategy-to-repo", workspace=ws, payload=payload, repo=repo, vault=vault, paydir=paydir)
        assert r.returncode == 0, r.stdout + r.stderr
        out = json.loads(r.stdout)
        assert out["status"] == "ok"
        assert (repo / "wiki" / "strategies" / "strategy_cdns.md").is_file()

    def test_runner_refuses_authoring_to_vault_root(self, tmp_path):
        ws, repo, vault, paydir = self._setup(tmp_path)
        payload = {"symbol": "CDNS", "repo_root": str(vault), "decision": _strategy_decision_dict()}
        r = self._run("author-strategy-to-repo", workspace=ws, payload=payload, repo=repo, vault=vault, paydir=paydir)
        assert r.returncode == 2, r.stdout + r.stderr
        err = json.loads(r.stdout)
        assert "outside allowed K2Bi repo root" in err["message"]

    def test_runner_full_ship_builds_approval_and_serializes_result(self, tmp_path):
        ws, repo, vault, paydir = self._setup(tmp_path)
        strategy = _strategy_file(repo / "wiki" / "strategies" / "strategy_cdns.md")
        payload = _ship_payload(strategy, vault)
        r = self._run("run-full-ship", workspace=ws, payload=payload, repo=repo, vault=vault, paydir=paydir)
        assert r.returncode == 0, r.stdout + r.stderr
        out = json.loads(r.stdout)
        assert out["status"] == "ok"
        assert out["result"]["slug"] == "cdns"
        assert out["result"]["approval"]["ship_lease_id"] == payload["approval"]["ship_lease_id"]

    def test_runner_full_ship_surfaces_rollback_result_on_gate_error(self, tmp_path):
        ws, repo, vault, paydir = self._setup(tmp_path)
        strategy = _strategy_file(repo / "wiki" / "strategies" / "strategy_cdns.md", body="rollback")
        payload = _ship_payload(strategy, vault)
        payload["force_rollback"] = True
        r = self._run("run-full-ship", workspace=ws, payload=payload, repo=repo, vault=vault, paydir=paydir)
        assert r.returncode == 2, r.stdout + r.stderr
        err = json.loads(r.stdout)
        assert err["status"] == "error"
        assert err["rollback_result"]["marker_cleared"] is True
        assert err["rollback_result"]["working_tree_restored"] is True


def _write_a3_workspace(ws: Path) -> None:
    lib = ws / "scripts" / "lib"
    lib.mkdir(parents=True)
    (ws / "scripts" / "__init__.py").write_text("", encoding="utf-8")
    (lib / "__init__.py").write_text("", encoding="utf-8")
    (lib / "invest_orchestrator_adapters.py").write_text(
        "import hashlib\n"
        "from dataclasses import dataclass\n"
        "from pathlib import Path\n"
        "from typing import Any\n"
        "class OrchestratorGateError(ValueError):\n"
        "    def __init__(self, message, *, rollback_result=None):\n"
        "        super().__init__(message)\n"
        "        self.rollback_result = rollback_result\n"
        "@dataclass(frozen=True)\n"
        "class RollbackResult:\n"
        "    strategy_path: str\n"
        "    original_sha256: str\n"
        "    marker_path: str\n"
        "    index_restored: bool\n"
        "    working_tree_restored: bool\n"
        "    marker_cleared: bool\n"
        "    events: tuple\n"
        "@dataclass(frozen=True)\n"
        "class StrategySpecDecision:\n"
        "    slug: str\n"
        "    symbol: str\n"
        "    sigid: str\n"
        "    risk_envelope_pct: Any\n"
        "    order: dict[str, Any]\n"
        "    forward_guidance_metrics: list[dict[str, Any]]\n"
        "    forward_guidance_status: str\n"
        "    how_this_works: str\n"
        "    bucket_rules: list[str]\n"
        "    entry_rules: list[str]\n"
        "    stop_rules: list[str]\n"
        "    target_rules: list[str]\n"
        "    hold_rules: list[str]\n"
        "    kill_rules: list[str]\n"
        "    accepted_gaps: list[str]\n"
        "    forward_guidance_override_reason: str | None\n"
        "    forward_guidance_waive_reason: str | None\n"
        "    regime_filter: list[Any] | None\n"
        "    date: str | None\n"
        "    extra_frontmatter: dict[str, Any] | None\n"
        "@dataclass(frozen=True)\n"
        "class StrategySpecWriteResult:\n"
        "    path: Path\n"
        "    frontmatter: dict[str, Any]\n"
        "    content_sha256: str\n"
        "@dataclass(frozen=True)\n"
        "class FullShipApproval:\n"
        "    final_approval_token: str\n"
        "    approved_by: str\n"
        "    approved_at: str\n"
        "    ship_lease_id: str\n"
        "@dataclass(frozen=True)\n"
        "class FullShipResult:\n"
        "    slug: str\n"
        "    approval: FullShipApproval\n"
        "    events: list[dict[str, Any]]\n"
        "def write_complete_strategy_spec(decision, *, repo_root):\n"
        "    path = Path(repo_root) / 'wiki' / 'strategies' / f'strategy_{decision.slug}.md'\n"
        "    path.parent.mkdir(parents=True, exist_ok=True)\n"
        "    body = f'---\\nname: {decision.slug}\\nticker: {decision.symbol}\\nstatus: proposed\\norder:\\n  ticker: {decision.order[\"ticker\"]}\\n---\\n# Strategy\\n{decision.how_this_works}\\n'\n"
        "    content = body.encode('utf-8')\n"
        "    path.write_bytes(content)\n"
        "    return StrategySpecWriteResult(path, {'name': decision.slug, 'ticker': decision.symbol}, hashlib.sha256(content).hexdigest())\n"
        "def run_full_ship(strategy_path, *, approval, vault_root=None, required_primary='minimax'):\n"
        "    if strategy_path.read_text().find('rollback') >= 0:\n"
        "        rb = RollbackResult(str(strategy_path), 'abc', 'marker', True, True, True, ({'event':'rollback'},))\n"
        "        raise OrchestratorGateError('ship refused', rollback_result=rb)\n"
        "    return FullShipResult(strategy_path.stem[len('strategy_'):], approval, [{'event':'ok'}])\n",
        encoding="utf-8",
    )


class TestA3Worker:
    def test_ship_timeout_branch_uses_module_ship_key_constant(self, store, tmp_path, monkeypatch):
        from scripts.lib import orchestrator_worker as worker

        monkeypatch.setattr(worker, "SHIP_COMMAND_KEY", "renamed-ship-command")
        tid = store.add_task(
            assignee_profile="k2bi",
            command_key="renamed-ship-command",
            success_criteria="ship",
            permissions="analyst-command",
            payload={"payload_path": "/tmp/k2b-orchestrator/ship.json"},
        )
        store.mark_running(tid)
        monkeypatch.setenv("K2B_ORCH_CMD_TIMEOUT", "11")
        monkeypatch.setenv("K2B_ORCH_SHIP_CMD_TIMEOUT", "1200")
        monkeypatch.setattr(store, "notify", lambda *a, **k: None)
        captured = {}

        def fake_resolve_command(profile, key, payload=None):
            return [sys.executable, "-c", "print('ok')"]

        def fake_run(*args, **kwargs):
            captured["timeout"] = kwargs.get("timeout")

            class R:
                returncode = 0
                stdout = "ok"
                stderr = ""

            return R()

        monkeypatch.setattr(worker.profiles, "resolve_command", fake_resolve_command)
        monkeypatch.setattr(worker.subprocess, "run", fake_run)
        worker.main(tid)
        assert captured["timeout"] == 1200

    def test_run_full_ship_uses_ship_timeout_env(self, store, tmp_path, monkeypatch):
        from scripts.lib import orchestrator_worker as worker

        tid = store.add_task(
            assignee_profile="k2bi",
            command_key="k2bi-run-full-ship",
            success_criteria="ship",
            permissions="analyst-command",
            payload={"payload_path": "/tmp/k2b-orchestrator/ship.json"},
        )
        store.mark_running(tid)
        monkeypatch.setenv("K2B_ORCH_CMD_TIMEOUT", "11")
        monkeypatch.setenv("K2B_ORCH_SHIP_CMD_TIMEOUT", "1200")
        monkeypatch.setattr(store, "notify", lambda *a, **k: None)
        captured = {}

        def fake_resolve_command(profile, key, payload=None):
            return [sys.executable, "-c", "print('ok')"]

        def fake_run(*args, **kwargs):
            captured["timeout"] = kwargs.get("timeout")

            class R:
                returncode = 0
                stdout = "ok"
                stderr = ""

            return R()

        monkeypatch.setattr(worker.profiles, "resolve_command", fake_resolve_command)
        monkeypatch.setattr(worker.subprocess, "run", fake_run)
        worker.main(tid)
        assert captured["timeout"] == 1200

    def test_run_full_ship_malformed_ship_timeout_falls_back_to_ship_default(
        self, store, tmp_path, monkeypatch, capsys
    ):
        from scripts.lib import orchestrator_worker as worker

        tid = store.add_task(
            assignee_profile="k2bi",
            command_key="k2bi-run-full-ship",
            success_criteria="ship",
            permissions="analyst-command",
            payload={"payload_path": "/tmp/k2b-orchestrator/ship.json"},
        )
        store.mark_running(tid)
        monkeypatch.setenv("K2B_ORCH_CMD_TIMEOUT", "11")
        monkeypatch.setenv("K2B_ORCH_SHIP_CMD_TIMEOUT", "not-an-int")
        monkeypatch.setattr(store, "notify", lambda *a, **k: None)
        captured = {}

        def fake_resolve_command(profile, key, payload=None):
            return [sys.executable, "-c", "print('ok')"]

        def fake_run(*args, **kwargs):
            captured["timeout"] = kwargs.get("timeout")

            class R:
                returncode = 0
                stdout = "ok"
                stderr = ""

            return R()

        monkeypatch.setattr(worker.profiles, "resolve_command", fake_resolve_command)
        monkeypatch.setattr(worker.subprocess, "run", fake_run)
        worker.main(tid)

        err = capsys.readouterr().err
        assert captured["timeout"] == 1200
        assert "K2B_ORCH_SHIP_CMD_TIMEOUT" in err
        assert "1200" in err


class TestRealK2BiShipShape:
    @pytest.mark.skipif(
        not (K2BI_REPO / "scripts" / "lib" / "invest_orchestrator_adapters.py").exists(),
        reason="real K2Bi checkout not available",
    )
    def test_real_full_ship_pre_review_gates(self, tmp_path):
        repo = tmp_path / "real-k2bi-repo"
        vault = tmp_path / "real-k2bi-vault"
        vault.mkdir()
        _init_git_repo(repo)
        script = f"""
import datetime as dt
import json
import subprocess
from pathlib import Path
import sys
sys.path.insert(0, {str(K2BI_REPO)!r})
from scripts.lib import invest_orchestrator_adapters as ioa

repo = Path({str(repo)!r})
vault = Path({str(vault)!r})
decision = ioa.StrategySpecDecision(**{_strategy_decision_dict()!r})
result = ioa.write_complete_strategy_spec(decision, repo_root=repo)
strategy = Path(result.path)
sha = result.content_sha256
approved_at = "2026-06-08T00:00:00+00:00"
lease = "cdns-ship-a1-20260608T000000Z"
approval = ioa.FullShipApproval(
    final_approval_token=f"APPROVE_STRATEGY:cdns:{{sha}}:{{approved_at}}:{{lease}}",
    approved_by="keith",
    approved_at=approved_at,
    ship_lease_id=lease,
)
ioa._validate_full_ship_approval(approval, "cdns", sha)
head_before = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()

bad = ioa.FullShipApproval(
    final_approval_token=f"APPROVE_STRATEGY:cdns:{{'f' * 64}}:{{approved_at}}:{{lease}}",
    approved_by="keith",
    approved_at=approved_at,
    ship_lease_id=lease,
)
try:
    ioa.run_full_ship(strategy, approval=bad, vault_root=vault)
except ioa.OrchestratorGateError as exc:
    assert "final approval token" in str(exc)
else:
    raise AssertionError("stale sha token unexpectedly passed")
assert subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip() == head_before

tracked = repo / "tracked.txt"
tracked.write_text("v1\\n", encoding="utf-8")
subprocess.check_call(["git", "-C", str(repo), "add", "tracked.txt"])
subprocess.check_call(["git", "-C", str(repo), "commit", "-m", "tracked"])
tracked.write_text("dirty\\n", encoding="utf-8")
try:
    ioa.run_full_ship(strategy, approval=approval, vault_root=vault)
except ioa.OrchestratorGateError as exc:
    assert "clean-tree preflight" in str(exc)
else:
    raise AssertionError("dirty tree unexpectedly passed")
subprocess.check_call(["git", "-C", str(repo), "restore", "tracked.txt"])

marker = repo / ".k2bi-orchestrator" / "rollback" / "cdns.json"
marker.parent.mkdir(parents=True, exist_ok=True)
marker.write_text(json.dumps({{"phase":"started"}}), encoding="utf-8")
try:
    ioa.run_full_ship(strategy, approval=approval, vault_root=vault)
except ioa.OrchestratorGateError as exc:
    assert "rollback" in str(exc).lower()
else:
    raise AssertionError("incomplete rollback marker unexpectedly passed")
"""
        proc = subprocess.run(
            [sys.executable, "-c", script],
            cwd=str(K2BI_REPO),
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr
