#!/usr/bin/env python3
"""pytest coverage for orchestrator Phase A2 (strategy half, Stages 9-10)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
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
    to_remove = [k for k in sys.modules if k.startswith("scripts.lib.orchestrator")]
    for k in to_remove:
        del sys.modules[k]


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


def _a1_flight(store, *, payload=None, entity="CDNS"):
    return store.add_task(
        assignee_profile="k2b",
        command_key="k2b-a1-chain",
        success_criteria="A1 chain parked",
        permissions="agent-native",
        entity_key=entity,
        status="needs_human",
        payload=payload or {},
    )


def _approved_thesis_payload(thesis_path: Path, *, thesis_approved=True) -> dict:
    payload = {
        "promote_done": True,
        "screen_done": True,
        "screen_approved_by_operator": True,
        "thesis_written": True,
        "thesis_artifact_verified": True,
        "thesis_path": str(thesis_path),
        "thesis_artifact_sha256": _sha256(thesis_path),
        "bear_done": True,
        "bear_verdict": "PROCEED",
        "bear_conviction": 60,
    }
    if thesis_approved:
        payload["thesis_approved"] = True
    return payload


def _sha256(path: Path) -> str:
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest()


def _strategy_file(path: Path, *, slug: str = "cdns", ticker: str = "CDNS", body: str = "v1") -> Path:
    """Write a strategy file whose frontmatter ticker binds to the parent entity
    (record-strategy-done now validates order.ticker == entity_key)."""
    path.write_text(
        "---\n"
        f"name: {slug}\n"
        f"ticker: {ticker}\n"
        "order:\n"
        f"  ticker: {ticker}\n"
        "---\n"
        f"# Strategy {slug}\n{body}\n",
        encoding="utf-8",
    )
    return path


def _backtest_capture(path: Path, *, slug: str, look_ahead_check: str) -> Path:
    """Write a realistic K2Bi backtest capture (the gate now parses its frontmatter
    for strategy_slug + backtest.look_ahead_check)."""
    path.write_text(
        "---\n"
        f"strategy_slug: {slug}\n"
        "type: backtest\n"
        "backtest:\n"
        f"  look_ahead_check: {look_ahead_check}\n"
        "---\n"
        "result body\n",
        encoding="utf-8",
    )
    return path


def _strategy_decision_dict(**overrides) -> dict:
    """A full 20-field StrategySpecDecision payload (strict coercer ignores
    dataclass defaults, so every field must be present)."""
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


# --------------------------------------------------------------------------- #
# BLOCKER: thesis/bear shared-file sha drift + re-anchor (latent A1 finding #5)
# --------------------------------------------------------------------------- #


class TestThesisArtifactReanchor:
    def test_bear_append_drift_blocks_resume(self, store, tmp_path):
        thesis = tmp_path / "CDNS.md"
        thesis.write_text("# CDNS thesis\n", encoding="utf-8")
        payload = _approved_thesis_payload(thesis, thesis_approved=False)
        tid = _a1_flight(store, payload=payload)
        # Bear appends to the SAME file AFTER the sha was recorded.
        with thesis.open("a", encoding="utf-8") as f:
            f.write("\n## Bear Case (2026-06-07)\nPROCEED conviction 60\n")
        assert store.a1_resume_action(tid) == "thesis_artifact_invalid"

    def test_reanchor_recovers_drifted_flight(self, store, tmp_path):
        thesis = tmp_path / "CDNS.md"
        thesis.write_text("# CDNS thesis\n", encoding="utf-8")
        tid = _a1_flight(
            store, payload=_approved_thesis_payload(thesis, thesis_approved=False)
        )
        with thesis.open("a", encoding="utf-8") as f:
            f.write("\n## Bear Case\nPROCEED\n")
        assert store.a1_resume_action(tid) == "thesis_artifact_invalid"
        ok, reason = store.a1_reanchor_thesis_artifact(
            tid, checked_log=True, reason="bear legitimately appended"
        )
        assert ok, reason
        # Drift cleared; flight is back at the (un-approved) thesis gate.
        assert store.a1_resume_action(tid) == "thesis_approval_gate"
        # And approving now advances into A2.
        ok, reason = store.a1_approve_thesis(tid)
        assert ok, reason
        assert store.a1_resume_action(tid) == "dispatch_strategy"

    def test_reanchor_requires_checked_log(self, store, tmp_path):
        thesis = tmp_path / "CDNS.md"
        thesis.write_text("# CDNS thesis\n", encoding="utf-8")
        tid = _a1_flight(
            store, payload=_approved_thesis_payload(thesis, thesis_approved=False)
        )
        ok, reason = store.a1_reanchor_thesis_artifact(tid, checked_log=False)
        assert not ok
        assert "i-checked-the-log" in reason

    def test_reanchor_requires_bear_done(self, store, tmp_path):
        thesis = tmp_path / "CDNS.md"
        thesis.write_text("# CDNS thesis\n", encoding="utf-8")
        payload = _approved_thesis_payload(thesis, thesis_approved=False)
        payload["bear_done"] = False
        payload.pop("bear_verdict", None)
        tid = _a1_flight(store, payload=payload)
        ok, reason = store.a1_reanchor_thesis_artifact(tid, checked_log=True)
        assert not ok
        assert "clear-thesis-artifact" in reason

    def test_forward_reanchor_on_bear_proceed(self, store, tmp_path):
        # The forward fix: a1_mark_bear_verdict re-anchors the sha at the moment the
        # verdict is recorded, so a future resume does not flag the bear append.
        thesis = tmp_path / "CDNS.md"
        thesis.write_text("# CDNS thesis\n", encoding="utf-8")
        payload = {
            "promote_done": True,
            "screen_done": True,
            "screen_approved_by_operator": True,
            "thesis_written": True,
            "thesis_artifact_verified": True,
            "thesis_path": str(thesis),
            "thesis_artifact_sha256": _sha256(thesis),
            "bear_done": False,
        }
        tid = _a1_flight(store, payload=payload)
        # Bear writes its section, THEN the conductor records the verdict.
        with thesis.open("a", encoding="utf-8") as f:
            f.write("\n## Bear Case\nPROCEED\n")
        assert store.a1_mark_bear_verdict(tid, "PROCEED", conviction=60) is True
        # No drift: the recorded sha now matches the post-bear file.
        assert store.a1_resume_action(tid) == "thesis_approval_gate"


# --------------------------------------------------------------------------- #
# A2 resume oracle + helpers
# --------------------------------------------------------------------------- #


class TestA2Oracle:
    def test_bear_done_without_approval_stays_at_thesis_gate(self, store, tmp_path):
        # Backward-compat: A1 flights (no thesis_approved) still park at the gate.
        thesis = tmp_path / "CDNS.md"
        thesis.write_text("# CDNS thesis\n", encoding="utf-8")
        tid = _a1_flight(
            store, payload=_approved_thesis_payload(thesis, thesis_approved=False)
        )
        assert store.a1_resume_action(tid) == "thesis_approval_gate"

    def test_approve_thesis_guards_not_at_gate(self, store, tmp_path):
        thesis = tmp_path / "CDNS.md"
        thesis.write_text("# CDNS thesis\n", encoding="utf-8")
        payload = _approved_thesis_payload(thesis, thesis_approved=False)
        payload["bear_done"] = False
        payload.pop("bear_verdict", None)
        tid = _a1_flight(store, payload=payload)
        ok, reason = store.a1_approve_thesis(tid)
        assert not ok
        # No bear verdict yet -> the PROCEED guard refuses before the gate check.
        assert "PROCEED" in reason

    def test_full_a2_sequence(self, store, tmp_path):
        thesis = tmp_path / "CDNS.md"
        thesis.write_text("# CDNS thesis\n", encoding="utf-8")
        tid = _a1_flight(store, payload=_approved_thesis_payload(thesis))
        assert store.a1_resume_action(tid) == "dispatch_strategy"

        strat = _strategy_file(tmp_path / "strategy_cdns.md")
        ok, reason = store.a1_record_strategy_done(tid, str(strat))
        assert ok, reason
        assert store.a1_resume_action(tid) == "verify_strategy_artifact"

        ok, reason = store.a1_verify_strategy_artifact(tid)
        assert ok, reason
        assert store.a1_resume_action(tid) == "dispatch_backtest"

        bt = _backtest_capture(tmp_path / "2026-06-07_cdns_backtest.md", slug="cdns", look_ahead_check="passed")
        ok, reason = store.a1_record_backtest_done(tid, str(bt), look_ahead_check="passed")
        assert ok, reason
        assert store.a1_resume_action(tid) == "strategy_approval_gate"

        ok, reason = store.a1_approve_strategy(tid)
        assert ok, reason
        assert store.a1_resume_action(tid) == "strategy_approved_await_ship"

    def test_record_strategy_requires_thesis_approval(self, store, tmp_path):
        thesis = tmp_path / "CDNS.md"
        thesis.write_text("# CDNS thesis\n", encoding="utf-8")
        tid = _a1_flight(
            store, payload=_approved_thesis_payload(thesis, thesis_approved=False)
        )
        strat = _strategy_file(tmp_path / "strategy_cdns.md")
        ok, reason = store.a1_record_strategy_done(tid, str(strat))
        assert not ok
        assert "thesis is approved" in reason

    def test_record_strategy_rejects_missing_artifact(self, store, tmp_path):
        thesis = tmp_path / "CDNS.md"
        thesis.write_text("# CDNS thesis\n", encoding="utf-8")
        tid = _a1_flight(store, payload=_approved_thesis_payload(thesis))
        ok, reason = store.a1_record_strategy_done(tid, str(tmp_path / "nope.md"))
        assert not ok
        assert "missing" in reason

    def test_backtest_requires_verified_strategy(self, store, tmp_path):
        thesis = tmp_path / "CDNS.md"
        thesis.write_text("# CDNS thesis\n", encoding="utf-8")
        tid = _a1_flight(store, payload=_approved_thesis_payload(thesis))
        strat = _strategy_file(tmp_path / "strategy_cdns.md")
        store.a1_record_strategy_done(tid, str(strat))
        bt = tmp_path / "bt.md"
        bt.write_text("x\n", encoding="utf-8")
        ok, reason = store.a1_record_backtest_done(tid, str(bt))
        assert not ok
        assert "strategy artifact is verified" in reason

    def test_approve_strategy_requires_backtest(self, store, tmp_path):
        thesis = tmp_path / "CDNS.md"
        thesis.write_text("# CDNS thesis\n", encoding="utf-8")
        tid = _a1_flight(store, payload=_approved_thesis_payload(thesis))
        ok, reason = store.a1_approve_strategy(tid)
        assert not ok
        assert "backtest" in reason

    def test_strategy_artifact_drift_flagged(self, store, tmp_path):
        thesis = tmp_path / "CDNS.md"
        thesis.write_text("# CDNS thesis\n", encoding="utf-8")
        tid = _a1_flight(store, payload=_approved_thesis_payload(thesis))
        strat = _strategy_file(tmp_path / "strategy_cdns.md")
        store.a1_record_strategy_done(tid, str(strat))
        store.a1_verify_strategy_artifact(tid)
        assert store.a1_resume_action(tid) == "dispatch_backtest"
        # Mutate the strategy file -> integrity check fails -> invalid.
        _strategy_file(strat, body="tampered")
        assert store.a1_resume_action(tid) == "strategy_artifact_invalid"


class TestA2Checkpoint2Hardening:
    """Regression coverage for the 6 Codex Checkpoint-2 findings."""

    def test_oracle_requires_proceed_not_just_non_veto(self, store, tmp_path):
        # #1: bear_done=True but verdict missing -> fail closed to dispatch_bear_case.
        thesis = tmp_path / "CDNS.md"
        thesis.write_text("# CDNS thesis\n", encoding="utf-8")
        payload = _approved_thesis_payload(thesis, thesis_approved=True)
        payload.pop("bear_verdict", None)  # malformed/partial recovery
        tid = _a1_flight(store, payload=payload)
        assert store.a1_resume_action(tid) == "dispatch_bear_case"

    def test_approve_thesis_requires_proceed(self, store, tmp_path):
        # #1: approve must reject a missing verdict, not just VETO.
        thesis = tmp_path / "CDNS.md"
        thesis.write_text("# CDNS thesis\n", encoding="utf-8")
        payload = _approved_thesis_payload(thesis, thesis_approved=False)
        payload["bear_verdict"] = "MAYBE"
        tid = _a1_flight(store, payload=payload)
        ok, reason = store.a1_approve_thesis(tid)
        assert not ok and "PROCEED" in reason

    def test_forward_reanchor_skips_file_without_bear_section(self, store, tmp_path):
        # #2: a1_mark_bear_verdict must NOT re-anchor a file lacking the bear marker
        # (corruption guard) -- it leaves the recorded sha so drift is surfaced.
        thesis = tmp_path / "CDNS.md"
        thesis.write_text("# CDNS thesis\n", encoding="utf-8")
        original_sha = _sha256(thesis)
        payload = {
            "promote_done": True,
            "screen_done": True,
            "screen_approved_by_operator": True,
            "thesis_written": True,
            "thesis_artifact_verified": True,
            "thesis_path": str(thesis),
            "thesis_artifact_sha256": original_sha,
            "bear_done": False,
        }
        tid = _a1_flight(store, payload=payload)
        # File mutated WITHOUT a bear section (corruption-like).
        thesis.write_text("# CDNS thesis\nCORRUPTED no bear here\n", encoding="utf-8")
        store.a1_mark_bear_verdict(tid, "PROCEED", conviction=60)
        task = store.get_task(tid)
        p = json.loads(task["payload"])
        assert p["thesis_artifact_sha256"] == original_sha  # NOT re-anchored
        # The drifted (now-mismatched) sha is correctly surfaced as invalid.
        assert store.a1_resume_action(tid) == "thesis_artifact_invalid"

    def test_recovery_reanchor_refuses_file_without_bear_section(self, store, tmp_path):
        # #2: the operator-attested recovery must also require the bear marker.
        thesis = tmp_path / "CDNS.md"
        thesis.write_text("# CDNS thesis no bear\n", encoding="utf-8")
        tid = _a1_flight(
            store, payload=_approved_thesis_payload(thesis, thesis_approved=False)
        )
        ok, reason = store.a1_reanchor_thesis_artifact(tid, checked_log=True)
        assert not ok and "Bear Case" in reason

    def test_record_strategy_clears_stale_backtest_and_approval(self, store, tmp_path):
        # #3: re-recording a strategy must drop downstream backtest/approval flags.
        thesis = tmp_path / "CDNS.md"
        thesis.write_text("# CDNS thesis\n", encoding="utf-8")
        tid = _a1_flight(store, payload=_approved_thesis_payload(thesis))
        strat = _strategy_file(tmp_path / "strategy_cdns.md")
        store.a1_record_strategy_done(tid, str(strat))
        store.a1_verify_strategy_artifact(tid)
        bt = _backtest_capture(tmp_path / "2026-06-07_cdns_backtest.md", slug="cdns", look_ahead_check="passed")
        store.a1_record_backtest_done(tid, str(bt), look_ahead_check="passed")
        store.a1_approve_strategy(tid)
        # Re-record a fresh strategy (e.g. a redispatch).
        _strategy_file(strat, body="v2-fresh")
        store.a1_record_strategy_done(tid, str(strat))
        p = json.loads(store.get_task(tid)["payload"])
        assert p["backtest_done"] is False
        assert "strategy_approved" not in p
        assert "backtest_look_ahead_check" not in p
        # Resume must re-verify then re-backtest, not skip to the ship gate.
        assert store.a1_resume_action(tid) == "verify_strategy_artifact"

    def test_backtest_verdict_is_artifact_derived(self, store, tmp_path):
        # #4 + round-3: the verdict comes from the capture, and a CLI value that
        # disagrees with the artifact is rejected (typo catch).
        thesis = tmp_path / "CDNS.md"
        thesis.write_text("# CDNS thesis\n", encoding="utf-8")
        tid = _a1_flight(store, payload=_approved_thesis_payload(thesis))
        strat = _strategy_file(tmp_path / "strategy_cdns.md")
        store.a1_record_strategy_done(tid, str(strat))
        store.a1_verify_strategy_artifact(tid)
        # Capture says passed, but the operator claims suspicious -> rejected.
        good = _backtest_capture(tmp_path / "2026-06-07_cdns_backtest.md", slug="cdns", look_ahead_check="passed")
        ok, reason = store.a1_record_backtest_done(tid, str(good), look_ahead_check="suspicious")
        assert not ok and "does not match the backtest capture" in reason
        # A capture whose own verdict is garbage is rejected regardless of CLI arg.
        bad = _backtest_capture(tmp_path / "2026-06-07b_cdns_backtest.md", slug="cdns", look_ahead_check="garbage")
        ok, reason = store.a1_record_backtest_done(tid, str(bad))
        assert not ok and "look_ahead_check" in reason
        # Capture passed + no CLI claim -> derives the verdict from the artifact.
        ok, reason = store.a1_record_backtest_done(tid, str(good))
        assert ok, reason
        p = json.loads(store.get_task(tid)["payload"])
        assert p["backtest_look_ahead_check"] == "passed"

    def test_backtest_capture_must_match_strategy_slug(self, store, tmp_path):
        # round-2 #1: a capture for a different strategy slug is refused.
        thesis = tmp_path / "CDNS.md"
        thesis.write_text("# CDNS thesis\n", encoding="utf-8")
        tid = _a1_flight(store, payload=_approved_thesis_payload(thesis))
        strat = _strategy_file(tmp_path / "strategy_cdns.md")
        store.a1_record_strategy_done(tid, str(strat))
        store.a1_verify_strategy_artifact(tid)
        # Capture frontmatter says a DIFFERENT strategy_slug than the recorded one.
        wrong = _backtest_capture(tmp_path / "2026-06-07_spy_backtest.md", slug="spy", look_ahead_check="passed")
        ok, reason = store.a1_record_backtest_done(tid, str(wrong), look_ahead_check="passed")
        assert not ok and "strategy_slug" in reason

    def test_approve_strategy_fails_closed_on_invalid_backtest_verdict(self, store, tmp_path):
        # round-2 #3: the invariant lives at the gate, not only the setter.
        thesis = tmp_path / "CDNS.md"
        thesis.write_text("# CDNS thesis\n", encoding="utf-8")
        payload = _approved_thesis_payload(thesis)
        strat = _strategy_file(tmp_path / "strategy_cdns.md")
        payload.update(
            {
                "strategy_spec_written": True,
                "strategy_path": str(strat),
                "strategy_artifact_verified": True,
                "strategy_artifact_sha256": _sha256(strat),
                "backtest_done": True,  # but NO valid look_ahead_check (pre-fix/hand-crafted)
            }
        )
        tid = _a1_flight(store, payload=payload)
        ok, reason = store.a1_approve_strategy(tid)
        assert not ok and "look_ahead_check" in reason
        # The oracle also fails closed -> re-run backtest, not the gate.
        assert store.a1_resume_action(tid) == "dispatch_backtest"

    def test_backtest_gate_fails_closed_on_edited_or_deleted_capture(self, store, tmp_path):
        # round-4: the gate re-opens + sha-checks the capture; a tampered/deleted
        # capture must fail closed (not leave the chain approvable).
        thesis = tmp_path / "CDNS.md"
        thesis.write_text("# CDNS thesis\n", encoding="utf-8")
        tid = _a1_flight(store, payload=_approved_thesis_payload(thesis))
        strat = _strategy_file(tmp_path / "strategy_cdns.md")
        store.a1_record_strategy_done(tid, str(strat))
        store.a1_verify_strategy_artifact(tid)
        bt = _backtest_capture(tmp_path / "2026-06-07_cdns_backtest.md", slug="cdns", look_ahead_check="passed")
        store.a1_record_backtest_done(tid, str(bt), look_ahead_check="passed")
        assert store.a1_resume_action(tid) == "strategy_approval_gate"
        # Edit the capture -> recorded sha mismatch -> fail closed.
        bt.write_text(bt.read_text() + "\ntampered\n", encoding="utf-8")
        assert store.a1_resume_action(tid) == "dispatch_backtest"
        ok, reason = store.a1_approve_strategy(tid)
        assert not ok and "sha256" in reason
        # Delete the capture -> also fail closed.
        bt.unlink()
        assert store.a1_resume_action(tid) == "dispatch_backtest"

    def test_backtest_gate_reparse_fallback_for_presha_flight(self, store, tmp_path):
        # round-4: a flight recorded before backtest_artifact_sha256 existed still
        # re-validates via reparse -- intact capture resolves, edited verdict caught.
        thesis = tmp_path / "CDNS.md"
        thesis.write_text("# CDNS thesis\n", encoding="utf-8")
        bt = _backtest_capture(tmp_path / "2026-06-07_cdns_backtest.md", slug="cdns", look_ahead_check="passed")
        strat = _strategy_file(tmp_path / "strategy_cdns.md")
        payload = _approved_thesis_payload(thesis)
        payload.update(
            {
                "strategy_spec_written": True,
                "strategy_path": str(strat),
                "strategy_artifact_verified": True,
                "strategy_artifact_sha256": _sha256(strat),
                "backtest_done": True,
                "backtest_artifact_path": str(bt),
                "backtest_look_ahead_check": "passed",  # NO backtest_artifact_sha256
            }
        )
        tid = _a1_flight(store, payload=payload)
        assert store.a1_resume_action(tid) == "strategy_approval_gate"
        # Change the capture's own verdict -> reparse catches the drift.
        _backtest_capture(bt, slug="cdns", look_ahead_check="suspicious")
        assert store.a1_resume_action(tid) == "dispatch_backtest"

    def test_runner_single_root_refuses_default_when_profile_overridden(self, tmp_path, monkeypatch):
        # round-2 #2: with K2BI_VAULT_PATH set, the default ~/Projects/K2Bi-Vault
        # must NOT be an allowed root. The function reads env live (no reload).
        from scripts.lib import orchestrator_k2bi_adapter as adapter

        profile_vault = Path(os.path.realpath(str(tmp_path))) / "profile-vault"
        profile_vault.mkdir()
        monkeypatch.setenv("K2BI_VAULT_PATH", str(profile_vault))
        roots = adapter._allowed_k2bi_vault_roots()
        assert roots == [profile_vault.resolve()]
        default = Path("~/Projects/K2Bi-Vault").expanduser().resolve()
        assert profile_vault.resolve() != default
        assert default not in roots

    def test_poll_once_cancels_out_of_order_a2_child(self, store, tmp_path, monkeypatch):
        # #6: a backtest child queued while the parent is still at dispatch_strategy
        # must be cancelled at claim time, not run.
        monkeypatch.setattr(store, "notify", lambda *a, **k: None)
        _write_registry(tmp_path / "k2bi-vault", "CDNS")
        thesis = tmp_path / "CDNS.md"
        thesis.write_text("# CDNS thesis\n", encoding="utf-8")
        parent = _a1_flight(store, payload=_approved_thesis_payload(thesis))
        # Parent is at dispatch_strategy. Queue a BACKTEST child (expects dispatch_backtest).
        child = store.add_task(
            assignee_profile="k2bi",
            command_key="k2bi-run-backtest",
            success_criteria="out of order",
            permissions="analyst-command",
            flight_id=parent,
            parent_task=parent,
            entity_key="CDNS",
            status="ready",
            payload={"symbol": "CDNS", "vault_root": str(tmp_path / "k2bi-vault"), "slug": "cdns"},
        )
        store.poll_once()
        assert store.get_task(child)["status"] == "cancelled"


class TestA2StrategyRevision:
    def test_strategy_revision_resets_to_dispatch(self, store, tmp_path):
        thesis = tmp_path / "CDNS.md"
        thesis.write_text("# CDNS thesis\n", encoding="utf-8")
        tid = _a1_flight(store, payload=_approved_thesis_payload(thesis))
        strat = _strategy_file(tmp_path / "strategy_cdns.md")
        store.a1_record_strategy_done(tid, str(strat))
        store.a1_verify_strategy_artifact(tid)
        assert store.a1_register_strategy_revision(tid) is True
        assert store.a1_resume_action(tid) == "dispatch_strategy"

    def test_fourth_strategy_revision_terminalizes(self, store, tmp_path):
        thesis = tmp_path / "CDNS.md"
        thesis.write_text("# CDNS thesis\n", encoding="utf-8")
        tid = _a1_flight(store, payload=_approved_thesis_payload(thesis))
        assert store.a1_register_strategy_revision(tid) is True
        assert store.a1_register_strategy_revision(tid) is True
        assert store.a1_register_strategy_revision(tid) is True
        assert store.a1_register_strategy_revision(tid) is False
        assert store.a1_resume_action(tid) == "needs_human_terminal"
        task = store.get_task(tid)
        payload = json.loads(task["payload"])
        assert payload["terminal_reason"] == "strategy_revision_limit_exceeded"

    def test_strategy_revision_does_not_touch_thesis_counter(self, store, tmp_path):
        thesis = tmp_path / "CDNS.md"
        thesis.write_text("# CDNS thesis\n", encoding="utf-8")
        tid = _a1_flight(store, payload=_approved_thesis_payload(thesis))
        store.a1_register_strategy_revision(tid)
        task = store.get_task(tid)
        payload = json.loads(task["payload"])
        assert payload["strategy_revision_count"] == 1
        assert "revision_count" not in payload or payload.get("revision_count") in (None, 0)


# --------------------------------------------------------------------------- #
# Profiles: allowlist + preflight
# --------------------------------------------------------------------------- #


class TestA2Profiles:
    def test_a2_commands_allowlisted_with_carrier(self, store):
        from scripts.lib import orchestrator_profiles as profiles

        for key in ("k2bi-write-strategy-spec", "k2bi-run-backtest"):
            argv = profiles.resolve_command(
                "k2bi", key, {"payload_path": "/tmp/k2b-orchestrator/p.json"}
            )
            assert argv is not None
            assert "--payload-path" in argv
            # No payload carrier -> refuse.
            assert profiles.resolve_command("k2bi", key, {}) is None

    def test_strategy_repo_root_must_match_profile(self, store, tmp_path):
        from scripts.lib import orchestrator_profiles as profiles

        _write_registry(tmp_path / "k2bi-vault", "CDNS")
        task = {
            "assignee_profile": "k2bi",
            "command_key": "k2bi-write-strategy-spec",
            "entity_key": "CDNS",
            "payload": json.dumps(
                {
                    "symbol": "CDNS",
                    "repo_root": str(tmp_path / "vault"),  # K2B vault, NOT K2Bi
                    "payload_json": json.dumps(
                        {"decision": {"slug": "cdns", "symbol": "CDNS", "order": {"ticker": "CDNS"}}}
                    ),
                }
            ),
        }
        ok, reason = profiles.preflight_k2bi(task)
        assert not ok
        assert "repo_root" in reason

    def test_strategy_payload_shape_gate(self, store, tmp_path):
        from scripts.lib import orchestrator_profiles as profiles

        _write_registry(tmp_path / "k2bi-vault", "CDNS")
        task = {
            "assignee_profile": "k2bi",
            "command_key": "k2bi-write-strategy-spec",
            "entity_key": "CDNS",
            "payload": json.dumps(
                {
                    "symbol": "CDNS",
                    "repo_root": str(tmp_path / "k2bi-vault"),
                    "payload_json": json.dumps({"decision": {"symbol": "CDNS"}}),
                }
            ),
        }
        ok, reason = profiles.preflight_k2bi(task)
        assert not ok
        assert "decision.slug" in reason

    def test_backtest_payload_shape_gate(self, store, tmp_path):
        from scripts.lib import orchestrator_profiles as profiles

        _write_registry(tmp_path / "k2bi-vault", "CDNS")
        task = {
            "assignee_profile": "k2bi",
            "command_key": "k2bi-run-backtest",
            "entity_key": "CDNS",
            "payload": json.dumps(
                {
                    "symbol": "CDNS",
                    "vault_root": str(tmp_path / "k2bi-vault"),
                    "payload_json": json.dumps({"slug": ""}),
                }
            ),
        }
        ok, reason = profiles.preflight_k2bi(task)
        assert not ok
        assert "slug" in reason

    def test_a2_symbol_must_match_entity(self, store, tmp_path):
        from scripts.lib import orchestrator_profiles as profiles

        _write_registry(tmp_path / "k2bi-vault", "CDNS", "SPY")
        task = {
            "assignee_profile": "k2bi",
            "command_key": "k2bi-run-backtest",
            "entity_key": "CDNS",
            "payload": json.dumps(
                {
                    "symbol": "SPY",
                    "vault_root": str(tmp_path / "k2bi-vault"),
                    "payload_path": "/tmp/k2b-orchestrator/p.json",
                }
            ),
        }
        ok, reason = profiles.preflight_k2bi(task)
        assert not ok
        assert "does not match entity_key" in reason


def _write_registry(vault: Path, *symbols: str) -> Path:
    path = vault / "wiki" / "tickers" / "canonical-registry.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({symbol: {"name": f"{symbol} Corp"} for symbol in symbols}),
        encoding="utf-8",
    )
    return path


# --------------------------------------------------------------------------- #
# Adapter runner (subprocess, realistic fake K2Bi workspace)
# --------------------------------------------------------------------------- #


def _write_a2_workspace(ws: Path) -> None:
    lib = ws / "scripts" / "lib"
    lib.mkdir(parents=True)
    (ws / "scripts" / "__init__.py").write_text("", encoding="utf-8")
    (lib / "__init__.py").write_text("", encoding="utf-8")
    (lib / "invest_orchestrator_adapters.py").write_text(
        "import hashlib\n"
        "from dataclasses import dataclass\n"
        "from pathlib import Path\n"
        "from typing import Any\n"
        "class OrchestratorGateError(ValueError): pass\n"
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
        "def write_complete_strategy_spec(decision, *, repo_root):\n"
        "    if not decision.how_this_works.strip():\n"
        "        raise OrchestratorGateError('How This Works must be a non-empty string')\n"
        "    path = Path(repo_root) / 'wiki' / 'strategies' / f'strategy_{decision.slug}.md'\n"
        "    path.parent.mkdir(parents=True, exist_ok=True)\n"
        "    body = f'---\\nname: {decision.slug}\\nticker: {decision.symbol}\\norder:\\n  ticker: {decision.order[\"ticker\"]}\\n---\\n# Strategy\\n## How This Works\\n{decision.how_this_works}\\n'\n"
        "    content = body.encode('utf-8')\n"
        "    path.write_bytes(content)\n"
        "    return StrategySpecWriteResult(path, {'name': decision.slug, 'ticker': decision.symbol}, hashlib.sha256(content).hexdigest())\n",
        encoding="utf-8",
    )
    (lib / "invest_backtest.py").write_text(
        "import datetime as _dt\n"
        "from dataclasses import dataclass\n"
        "from pathlib import Path\n"
        "from typing import Optional\n"
        "@dataclass(frozen=True)\n"
        "class BacktestWindow:\n"
        "    start: _dt.date\n"
        "    end: _dt.date\n"
        "@dataclass(frozen=True)\n"
        "class BacktestMetrics:\n"
        "    sharpe: float\n"
        "    max_dd_pct: float\n"
        "    win_rate_pct: float\n"
        "@dataclass(frozen=True)\n"
        "class BacktestResult:\n"
        "    path: Path\n"
        "    slug: str\n"
        "    symbol: str\n"
        "    window: BacktestWindow\n"
        "    metrics: BacktestMetrics\n"
        "    look_ahead_check: str\n"
        "    last_run: _dt.datetime\n"
        "def run_backtest(slug, *, vault_root):\n"
        "    strat = Path(vault_root) / 'wiki' / 'strategies' / f'strategy_{slug}.md'\n"
        "    assert strat.exists(), f'strategy missing: {strat}'\n"
        "    cap = Path(vault_root) / 'raw' / 'backtests' / f'2026-06-07_{slug}_backtest.md'\n"
        "    cap.parent.mkdir(parents=True, exist_ok=True)\n"
        "    cap.write_text('---\\ntype: backtest\\n---\\nok\\n', encoding='utf-8')\n"
        "    return BacktestResult(cap, slug, 'CDNS', BacktestWindow(_dt.date(2024,6,7), _dt.date(2026,6,7)), "
        "BacktestMetrics(1.2, -8.0, 54.0), 'passed', _dt.datetime(2026,6,7,12,0,0))\n",
        encoding="utf-8",
    )


class TestA2AdapterRunner:
    def _run(self, cmd, *, workspace, payload, vault, paydir):
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
                "K2BI_VAULT_PATH": str(vault),
                "K2B_ORCH_ADAPTER_PAYLOAD_DIR": str(paydir),
            },
            capture_output=True,
            text=True,
            timeout=30,
        )

    def _setup(self, tmp_path):
        real = lambda p: Path(os.path.realpath(str(p)))
        ws = real(tmp_path) / "workspace"
        _write_a2_workspace(ws)
        vault = real(tmp_path) / "k2bi-vault"
        vault.mkdir(exist_ok=True)
        paydir = real(tmp_path) / "payloads"
        paydir.mkdir()
        return ws, vault, paydir

    def test_runner_writes_strategy(self, tmp_path):
        ws, vault, paydir = self._setup(tmp_path)
        payload = {
            "symbol": "CDNS",
            "repo_root": str(vault),
            "decision": _strategy_decision_dict(),
        }
        r = self._run("write-strategy-spec", workspace=ws, payload=payload, vault=vault, paydir=paydir)
        assert r.returncode == 0, r.stdout + r.stderr
        out = json.loads(r.stdout)
        assert out["status"] == "ok"
        assert (vault / "wiki" / "strategies" / "strategy_cdns.md").is_file()

    def test_runner_rejects_ticker_mismatch(self, tmp_path):
        ws, vault, paydir = self._setup(tmp_path)
        decision = _strategy_decision_dict()
        decision["order"]["ticker"] = "SPY"  # diverges from symbol CDNS
        payload = {"symbol": "CDNS", "repo_root": str(vault), "decision": decision}
        r = self._run("write-strategy-spec", workspace=ws, payload=payload, vault=vault, paydir=paydir)
        assert r.returncode == 2, r.stdout + r.stderr
        err = json.loads(r.stdout)
        assert err["status"] == "error"
        assert "symbol mismatch" in err["message"]
        assert not (vault / "wiki" / "strategies" / "strategy_cdns.md").exists()

    def test_runner_rejects_string_sits_inside_guide(self, tmp_path):
        ws, vault, paydir = self._setup(tmp_path)
        decision = _strategy_decision_dict()
        decision["forward_guidance_metrics"][0]["sits_inside_guide"] = "false"
        payload = {"symbol": "CDNS", "repo_root": str(vault), "decision": decision}
        r = self._run("write-strategy-spec", workspace=ws, payload=payload, vault=vault, paydir=paydir)
        assert r.returncode == 2, r.stdout + r.stderr
        err = json.loads(r.stdout)
        assert "sits_inside_guide must be a JSON boolean" in err["message"]

    def test_runner_rejects_repo_root_outside_k2bi_vault(self, tmp_path):
        ws, vault, paydir = self._setup(tmp_path)
        k2b_vault = Path(os.path.realpath(str(tmp_path))) / "k2b-vault"
        k2b_vault.mkdir()
        payload = {
            "symbol": "CDNS",
            "repo_root": str(k2b_vault),  # NOT the K2Bi vault
            "decision": _strategy_decision_dict(),
        }
        # Even if K2B_VAULT_PATH points here, the K2Bi-only allowlist must refuse it.
        env_extra = {"K2B_VAULT_PATH": str(k2b_vault)}
        payload_file = paydir / "p.json"
        payload_file.write_text(json.dumps(payload), encoding="utf-8")
        r = subprocess.run(
            [
                sys.executable,
                str(REPO_ROOT / "scripts" / "lib" / "orchestrator_k2bi_adapter.py"),
                "write-strategy-spec",
                "--workspace",
                str(ws),
                "--payload-path",
                str(payload_file),
            ],
            env={
                **os.environ,
                "K2BI_VAULT_PATH": str(vault),
                "K2B_ORCH_ADAPTER_PAYLOAD_DIR": str(paydir),
                **env_extra,
            },
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert r.returncode == 2, r.stdout + r.stderr
        err = json.loads(r.stdout)
        assert "outside allowed K2Bi vault root" in err["message"]

    def test_runner_strategy_writer_refusal_negative_path(self, tmp_path):
        ws, vault, paydir = self._setup(tmp_path)
        decision = _strategy_decision_dict(how_this_works="   ")
        payload = {"symbol": "CDNS", "repo_root": str(vault), "decision": decision}
        r = self._run("write-strategy-spec", workspace=ws, payload=payload, vault=vault, paydir=paydir)
        assert r.returncode == 2, r.stdout + r.stderr
        err = json.loads(r.stdout)
        assert err["status"] == "error"
        assert "How This Works" in err["message"]

    def test_runner_backtest_rejects_symbol_mismatch(self, tmp_path):
        # round-2 #1: the backtest's fetched ticker (result.symbol) must equal the
        # dispatched symbol; the fake returns CDNS, so a SPY dispatch is refused.
        ws, vault, paydir = self._setup(tmp_path)
        strat = vault / "wiki" / "strategies" / "strategy_cdns.md"
        strat.parent.mkdir(parents=True, exist_ok=True)
        strat.write_text("---\nname: cdns\n---\n# s\n", encoding="utf-8")
        payload = {"symbol": "SPY", "vault_root": str(vault), "slug": "cdns"}
        r = self._run("run-backtest", workspace=ws, payload=payload, vault=vault, paydir=paydir)
        assert r.returncode == 2, r.stdout + r.stderr
        err = json.loads(r.stdout)
        assert "does not match dispatched symbol" in err["message"]

    def test_runner_backtest_serializes_dates(self, tmp_path):
        ws, vault, paydir = self._setup(tmp_path)
        # Pre-write a strategy file the fake backtest reads.
        strat = vault / "wiki" / "strategies" / "strategy_cdns.md"
        strat.parent.mkdir(parents=True, exist_ok=True)
        strat.write_text("---\nname: cdns\n---\n# s\n", encoding="utf-8")
        payload = {"symbol": "CDNS", "vault_root": str(vault), "slug": "cdns"}
        r = self._run("run-backtest", workspace=ws, payload=payload, vault=vault, paydir=paydir)
        assert r.returncode == 0, r.stdout + r.stderr
        out = json.loads(r.stdout)
        assert out["status"] == "ok"
        res = out["result"]
        assert res["window"]["start"] == "2024-06-07"
        assert res["last_run"].startswith("2026-06-07T")
        assert res["look_ahead_check"] == "passed"


# --------------------------------------------------------------------------- #
# Live de-risk: the REAL K2Bi StrategySpecDecision accepts the live payload shape
# --------------------------------------------------------------------------- #


class TestRealK2BiDecisionShape:
    """Run the REAL adapter runner against the REAL K2Bi checkout -- exactly the
    production path -- so the live `decision` payload shape is proven before the
    live dispatch. Skipped where the K2Bi checkout is absent (e.g. CI)."""

    @pytest.mark.skipif(
        not (K2BI_REPO / "scripts" / "lib" / "invest_orchestrator_adapters.py").exists(),
        reason="real K2Bi checkout not available",
    )
    def test_real_writer_accepts_live_payload_shape(self, tmp_path):
        repo = Path(os.path.realpath(str(tmp_path))) / "k2bi-strategy-vault"
        repo.mkdir()
        paydir = Path(os.path.realpath(str(tmp_path))) / "payloads"
        paydir.mkdir()
        payload = {
            "symbol": "CDNS",
            "repo_root": str(repo),
            "decision": _strategy_decision_dict(),
        }
        payload_file = paydir / "p.json"
        payload_file.write_text(json.dumps(payload), encoding="utf-8")
        r = subprocess.run(
            [
                sys.executable,
                str(REPO_ROOT / "scripts" / "lib" / "orchestrator_k2bi_adapter.py"),
                "write-strategy-spec",
                "--workspace",
                str(K2BI_REPO),
                "--payload-path",
                str(payload_file),
            ],
            env={
                **os.environ,
                # The tmp repo IS the profile K2Bi vault for this isolated write.
                "K2BI_VAULT_PATH": str(repo),
                "K2B_ORCH_ADAPTER_PAYLOAD_DIR": str(paydir),
            },
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert r.returncode == 0, r.stdout + r.stderr
        out = json.loads(r.stdout)
        assert out["status"] == "ok"
        written = repo / "wiki" / "strategies" / "strategy_cdns.md"
        assert written.is_file()
        # The real writer ran the real StrategySpecDecision validators +
        # build_canonical_strategy_frontmatter + _validate_strategy_shape; the file
        # carries order.ticker == CDNS so the live backtest fetches the right bars.
        content = written.read_text(encoding="utf-8")
        assert "ticker: CDNS" in content
