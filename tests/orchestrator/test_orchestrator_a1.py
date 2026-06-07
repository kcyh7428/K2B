#!/usr/bin/env python3
"""pytest coverage for orchestrator Phase A1 chain state."""

from __future__ import annotations

import json
import errno
import os
import subprocess
import sys
import types
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


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


def _write_watchlist(vault: Path, symbol: str, status: str) -> Path:
    path = vault / "wiki" / "watchlist" / f"{symbol}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(
            [
                "---",
                f"symbol: {symbol}",
                f"status: {status}",
                "type: watchlist",
                "---",
                f"# Watchlist: {symbol}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return path


def _write_registry(vault: Path, *symbols: str) -> Path:
    path = vault / "wiki" / "tickers" / "canonical-registry.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps({symbol: {"name": f"{symbol} Corp"} for symbol in symbols}),
        encoding="utf-8",
    )
    return path


def _write_realistic_adapter_workspace(ws: Path) -> None:
    lib = ws / "scripts" / "lib"
    lib.mkdir(parents=True)
    (ws / "scripts" / "__init__.py").write_text("", encoding="utf-8")
    (lib / "__init__.py").write_text("", encoding="utf-8")
    (lib / "invest_thesis.py").write_text(
        "from dataclasses import dataclass\n"
        "from pathlib import Path\n"
        "@dataclass\n"
        "class ThesisInput:\n"
        "    symbol: str\n"
        "    title: str\n"
        "    base_sources: list[str]\n"
        "@dataclass\n"
        "class ThesisResult:\n"
        "    path: Path\n"
        "    written: bool\n"
        "    claim_count: int\n"
        "    refresh: bool\n",
        encoding="utf-8",
    )
    (lib / "invest_shared.py").write_text("VALUE = 'workspace-shared'\n", encoding="utf-8")
    (lib / "invest_orchestrator_adapters.py").write_text(
        "from dataclasses import dataclass\n"
        "from pathlib import Path\n"
        "from scripts.lib.invest_shared import VALUE\n"
        "from scripts.lib.invest_thesis import ThesisResult\n"
        "class OrchestratorGateError(ValueError): pass\n"
        "@dataclass(frozen=True)\n"
        "class ThesisClaimDecision:\n"
        "    claim_id: str\n"
        "    claim_text: str\n"
        "    claim_load_bearing: bool\n"
        "    source_url: str | None\n"
        "    source_excerpt: str\n"
        "    curated_framing: str\n"
        "    operator_mark: str\n"
        "    operator_note: str | None\n"
        "    source_vendor: str\n"
        "    spot_check_vendor: str | None\n"
        "def verify_and_generate_thesis(thesis_input, vault_root, *, claim_decisions, "
        "operator_override_reason, calx_override_acknowledged, "
        "vendor_warning_acknowledged, vendor_provenance, refresh, learning_stage):\n"
        "    assert thesis_input.symbol == 'CDNS'\n"
        "    assert thesis_input.base_sources == ['https://example.com/10q']\n"
        "    assert claim_decisions[0].operator_mark == 'verified'\n"
        "    assert claim_decisions[0].source_vendor == 'SEC'\n"
        "    assert operator_override_reason is None\n"
        "    assert calx_override_acknowledged is False\n"
        "    assert vendor_warning_acknowledged is False\n"
        "    assert vendor_provenance == {'primary': 'SEC'}\n"
        "    assert learning_stage == 'advanced'\n"
        "    assert VALUE == 'workspace-shared'\n"
        "    return ThesisResult(Path(vault_root) / 'wiki' / 'theses' / 'CDNS.md', True, "
        "len(claim_decisions), refresh)\n",
        encoding="utf-8",
    )
    (lib / "invest_bear_case.py").write_text(
        "from dataclasses import dataclass\n"
        "from pathlib import Path\n"
        "@dataclass\n"
        "class BearCaseInput:\n"
        "    bear_conviction: int\n"
        "    objections: list[str]\n"
        "@dataclass\n"
        "class BearCaseResult:\n"
        "    path: Path\n"
        "    written: bool\n"
        "    bear_verdict: str\n"
        "    bear_conviction: int\n"
        "    position_size_hkd: float | None\n"
        "def run_bear_case(symbol, bear_input, vault_root, *, refresh, learning_stage, "
        "position_size_hkd):\n"
        "    assert symbol == 'CDNS'\n"
        "    assert bear_input.objections == ['margin pressure']\n"
        "    assert refresh is True\n"
        "    assert learning_stage == 'advanced'\n"
        "    return BearCaseResult(Path(vault_root) / 'wiki' / 'bear-cases' / 'CDNS.md', "
        "True, 'PROCEED', bear_input.bear_conviction, position_size_hkd)\n",
        encoding="utf-8",
    )


class TestA1FlightState:
    def test_bear_veto_is_terminal_and_releases_entity_lock(self, store):
        tid = _a1_flight(
            store,
            payload={
                "promote_done": True,
                "screen_done": True,
                "thesis_written": True,
                "bear_done": False,
            },
        )

        assert store.a1_mark_bear_verdict(tid, "VETO", conviction=84) is True

        vetoed = store.get_task(tid)
        assert vetoed["status"] == "terminal_bear_veto"
        assert vetoed["finished_at"]
        payload = json.loads(vetoed["payload"])
        assert payload["bear_done"] is True
        assert payload["bear_verdict"] == "VETO"
        assert payload["bear_conviction"] == 84

        tid2 = _a1_flight(store, entity="CDNS")
        assert store.get_task(tid2)["status"] == "needs_human"
        assert store.a1_resume_action(tid) == "terminal_bear_veto"

    def test_bear_verdict_is_write_once(self, store):
        tid = _a1_flight(
            store,
            payload={
                "promote_done": True,
                "screen_done": True,
                "thesis_written": True,
                "bear_done": False,
            },
        )

        assert store.a1_mark_bear_verdict(tid, "PROCEED", conviction=20) is True
        assert store.a1_mark_bear_verdict(tid, "VETO", conviction=99) is False

        payload = json.loads(store.get_task(tid)["payload"])
        assert payload["bear_verdict"] == "PROCEED"
        assert payload["bear_conviction"] == 20

    def test_bear_verdict_missing_task_fails_loud(self, store):
        with pytest.raises(KeyError, match="task missing not found"):
            store.a1_mark_bear_verdict("missing", "VETO", conviction=84)

    def test_bear_verdict_on_terminal_task_fails_loud(self, store):
        tid = _a1_flight(store)
        store.transition(tid, "terminal_bear_veto")

        with pytest.raises(ValueError, match="already terminal"):
            store.a1_mark_bear_verdict(tid, "VETO", conviction=84)

    def test_resume_uses_completion_flags_not_stage_enum(self, store, tmp_path):
        thesis_path = tmp_path / "CDNS.md"
        thesis_path.write_text("# CDNS thesis\n", encoding="utf-8")
        tid = _a1_flight(
            store,
            payload={
                "stage": "screen",
                "promote_done": True,
                "screen_done": True,
                "screen_approved_by_operator": True,
                "thesis_written": True,
                "thesis_artifact_verified": True,
                "thesis_path": str(thesis_path),
                "bear_done": False,
            },
        )

        assert store.a1_resume_action(tid) == "dispatch_bear_case"

    def test_resume_verifies_thesis_artifact_before_bear_case(self, store):
        tid = _a1_flight(
            store,
            payload={
                "promote_done": True,
                "screen_done": True,
                "thesis_written": True,
                "bear_done": False,
            },
        )

        assert store.a1_resume_action(tid) == "verify_thesis_artifact"

    def test_locked_resume_oracle_matches_public_resume_action(self, store, tmp_path):
        thesis_path = tmp_path / "CDNS.md"
        thesis_path.write_text("# CDNS thesis\n", encoding="utf-8")
        tid = _a1_flight(
            store,
            payload={
                "promote_done": True,
                "screen_done": True,
                "screen_approved_by_operator": True,
                "thesis_written": True,
                "thesis_artifact_verified": True,
                "thesis_path": str(thesis_path),
                "bear_done": False,
            },
        )

        with store._acquire_lock():
            conn = store.connect()
            try:
                assert store.a1_resume_action_locked(conn, tid) == "dispatch_bear_case"
            finally:
                conn.close()
        assert store.a1_resume_action(tid) == "dispatch_bear_case"

    def test_resume_terminal_status_never_dispatches(self, store):
        tid = _a1_flight(
            store,
            payload={
                "promote_done": True,
                "screen_done": True,
                "thesis_written": False,
                "bear_done": False,
            },
        )
        store.transition(tid, "cancelled")

        assert store.a1_resume_action(tid) == "terminal_cancelled"

    def test_terminal_status_cannot_transition_to_done(self, store):
        tid = _a1_flight(store)
        store.transition(tid, "terminal_bear_veto")

        with pytest.raises(ValueError, match="terminal status is irreversible"):
            store.transition(tid, "done")

    def test_identical_thesis_redispatch_sets_refresh_true(self, store):
        claim_decisions = [
            {
                "claim_id": "c1",
                "claim_text": "Revenue grew 20 percent.",
                "claim_load_bearing": True,
                "source_url": "https://example.com/source",
                "source_excerpt": "Revenue grew 20 percent.",
                "curated_framing": "Curated source framed revenue growth.",
                "operator_mark": "verified",
                "operator_note": None,
                "source_vendor": "SEC filing",
                "spot_check_vendor": "Perplexity",
            }
        ]
        existing_hash = store.a1_claim_decisions_hash(claim_decisions)
        prepared = store.a1_prepare_thesis_dispatch_payload(
            {
                "symbol": "CDNS",
                "thesis_written": True,
                "thesis_artifact_verified": True,
                "claim_decisions_hash": store.a1_t7_context_hash({}, claim_decisions),
            },
            claim_decisions,
        )

        assert prepared["refresh"] is True
        assert prepared["claim_decisions"] == claim_decisions
        assert prepared["claim_decisions_hash"] == store.a1_t7_context_hash(
            prepared,
            claim_decisions,
        )
        assert prepared["thesis_dispatch_started_at"]

    def test_t7_context_hash_includes_override_context(self, store):
        claims = [{"claim_id": "c1"}]
        base = store.a1_t7_context_hash({}, claims)
        overridden = store.a1_t7_context_hash({"operator_override_reason": "Keith approved"}, claims)

        assert base != overridden

    def test_t7_context_hash_includes_thesis_input(self, store):
        claims = [{"claim_id": "c1"}]
        base = store.a1_t7_context_hash({"thesis_input": {"symbol": "CDNS"}}, claims)
        changed = store.a1_t7_context_hash({"thesis_input": {"symbol": "AAPL"}}, claims)

        assert base != changed

    def test_thesis_dispatch_refuses_refresh_before_artifact_verification(self, store):
        with pytest.raises(ValueError, match="before thesis_artifact_verified"):
            store.a1_prepare_thesis_dispatch_payload(
                {"thesis_written": True},
                [{"claim_id": "c1"}],
            )

    def test_thesis_dispatch_rejects_stale_claim_hash(self, store):
        with pytest.raises(ValueError, match="claim_decisions_hash mismatch"):
            store.a1_prepare_thesis_dispatch_payload(
                {"claim_decisions_hash": "stale"},
                [{"claim_id": "new"}],
            )

    def test_thesis_dispatch_rejects_changed_claims_without_revision(self, store):
        with pytest.raises(ValueError, match="without a registered revision"):
            store.a1_prepare_thesis_dispatch_payload(
                {"claim_decisions": [{"claim_id": "old"}]},
                [{"claim_id": "new"}],
            )

    def test_fourth_revision_escalates_to_needs_human_terminal(self, store):
        tid = _a1_flight(
            store,
            payload={
                "revision_count": 3,
                "promote_done": True,
                "screen_done": True,
                "thesis_written": True,
                "bear_done": True,
            },
        )

        assert store.a1_register_revision(tid) is False

        task = store.get_task(tid)
        assert task["status"] == "needs_human"
        payload = json.loads(task["payload"])
        assert payload["revision_count"] == 4
        assert payload["terminal_reason"] == "revision_limit_exceeded"
        assert payload["terminal_reason_at"]
        assert payload["promote_done"] is True
        assert payload["screen_done"] is True
        assert payload["thesis_written"] is False
        assert payload["bear_done"] is False
        assert "Cancel it before starting a fresh A1 chain" in task["blocker_reason"]
        assert store.a1_resume_action(tid) == "needs_human_terminal"

    def test_resume_action_uses_revision_count_defense(self, store):
        tid = _a1_flight(
            store,
            payload={
                "revision_count": 4,
                "promote_done": True,
                "screen_done": True,
                "thesis_written": False,
            },
        )

        assert store.a1_resume_action(tid) == "needs_human_terminal"

    def test_third_revision_in_progress_can_resume_thesis(self, store):
        tid = _a1_flight(
            store,
            payload={
                "revision_count": 3,
                "promote_done": True,
                "screen_done": True,
                "screen_approved_by_operator": True,
                "thesis_written": False,
            },
        )

        assert store.a1_resume_action(tid) == "dispatch_thesis"

    def test_terminal_revision_needs_human_uses_long_ttl(self, store, monkeypatch):
        monkeypatch.setenv("K2B_ORCH_TERMINAL_REASON_TTL_DAYS", "36500")
        tid = _a1_flight(store, payload={"terminal_reason": "revision_limit_exceeded"})
        conn = store.connect()
        old = "2026-01-01T00:00:00+00:00"
        conn.execute("UPDATE tasks SET updated_at=? WHERE id=?", (old, tid))
        conn.commit()
        conn.close()

        result = store.poll_once()

        assert tid not in result["ttl_expired"]
        assert store.get_task(tid)["status"] == "needs_human"

    def test_terminal_revision_needs_human_expires_after_terminal_ttl(self, store, monkeypatch):
        monkeypatch.setenv("K2B_ORCH_TERMINAL_REASON_TTL_DAYS", "1")
        tid = _a1_flight(store, payload={"terminal_reason": "revision_limit_exceeded"})
        conn = store.connect()
        old = "2026-01-01T00:00:00+00:00"
        conn.execute("UPDATE tasks SET updated_at=? WHERE id=?", (old, tid))
        conn.commit()
        conn.close()

        result = store.poll_once()

        assert tid in result["ttl_expired"]
        assert store.get_task(tid)["status"] == "cancelled"

    def test_future_terminal_reason_at_does_not_bypass_ttl(self, store, monkeypatch):
        monkeypatch.setenv("K2B_ORCH_TERMINAL_REASON_TTL_DAYS", "1")
        tid = _a1_flight(
            store,
            payload={
                "terminal_reason": "revision_limit_exceeded",
                "terminal_reason_at": "2999-01-01T00:00:00+00:00",
            },
        )
        conn = store.connect()
        old = "2026-01-01T00:00:00+00:00"
        conn.execute("UPDATE tasks SET updated_at=? WHERE id=?", (old, tid))
        conn.commit()
        conn.close()

        result = store.poll_once()

        assert tid in result["ttl_expired"]
        assert store.get_task(tid)["status"] == "cancelled"

    def test_missing_terminal_reason_at_uses_created_at_for_ttl(self, store, monkeypatch):
        monkeypatch.setenv("K2B_ORCH_TERMINAL_REASON_TTL_DAYS", "1")
        tid = _a1_flight(store, payload={"terminal_reason": "revision_limit_exceeded"})
        conn = store.connect()
        old = "2026-01-01T00:00:00+00:00"
        recent = store.now_iso()
        conn.execute(
            "UPDATE tasks SET created_at=?, updated_at=? WHERE id=?",
            (old, recent, tid),
        )
        conn.commit()
        conn.close()

        result = store.poll_once()

        assert tid in result["ttl_expired"]
        assert store.get_task(tid)["status"] == "cancelled"

    def test_epoch_created_at_does_not_underflow_terminal_ttl(self, store, monkeypatch):
        monkeypatch.setenv("K2B_ORCH_TERMINAL_REASON_TTL_DAYS", "1")
        tid = _a1_flight(store, payload={"terminal_reason": "revision_limit_exceeded"})
        conn = store.connect()
        conn.execute(
            "UPDATE tasks SET created_at=?, updated_at=? WHERE id=?",
            ("1970-01-01T00:00:00+00:00", store.now_iso(), tid),
        )
        conn.commit()
        conn.close()

        result = store.poll_once()

        assert tid not in result["ttl_expired"]
        assert store.get_task(tid)["status"] == "needs_human"

    def test_revision_limit_cannot_be_reparked_by_generic_transition(self, store):
        tid = _a1_flight(
            store,
            payload={"revision_count": 4, "terminal_reason": "revision_limit_exceeded"},
        )

        with pytest.raises(ValueError, match="revision limit exceeded"):
            store.transition(tid, "needs_human")

        with pytest.raises(ValueError, match="revision limit exceeded"):
            store.transition(tid, "blocked")

    def test_payload_update_cannot_resurrect_terminal_status(self, store):
        tid = _a1_flight(store)
        store.transition(tid, "terminal_bear_veto")
        conn = store.connect()
        try:
            with pytest.raises(ValueError, match="terminal status is irreversible"):
                store._update_payload_locked(conn, tid, {"ok": True}, status="done")
        finally:
            conn.close()

    def test_verify_thesis_artifact_records_resume_gate(self, store, tmp_path):
        thesis_path = tmp_path / "CDNS.md"
        thesis_path.write_text("# CDNS thesis\n", encoding="utf-8")
        tid = _a1_flight(
            store,
            payload={
                "promote_done": True,
                "screen_done": True,
                "thesis_written": True,
                "bear_done": False,
                "thesis_path": str(thesis_path),
            },
        )

        ok, reason = store.a1_verify_thesis_artifact(tid)

        assert ok, reason
        payload = json.loads(store.get_task(tid)["payload"])
        assert payload["thesis_artifact_verified"] is True
        assert payload["thesis_artifact_verified_at"]
        assert payload["thesis_artifact_sha256"]
        assert store.a1_resume_action(tid) == "dispatch_bear_case"
        os.utime(thesis_path, (1, 1))
        assert store.a1_resume_action(tid) == "dispatch_bear_case"

    def test_resume_marks_invalid_verified_thesis_as_drift(self, store, tmp_path):
        missing_path = tmp_path / "missing.md"
        tid = _a1_flight(
            store,
            payload={
                "promote_done": True,
                "screen_done": True,
                "screen_approved_by_operator": True,
                "thesis_written": True,
                "thesis_artifact_verified": True,
                "thesis_path": str(missing_path),
                "bear_done": False,
            },
        )

        assert store.a1_resume_action(tid) == "thesis_artifact_invalid"
        payload = json.loads(store.get_task(tid)["payload"])
        assert payload["thesis_written"] is True
        assert payload["thesis_artifact_verified"] is False
        assert payload["thesis_artifact_drift_detected_at"]
        assert "missing or not a file" in payload["thesis_artifact_drift_reason"]
        missing_path.write_text("# late artifact\n", encoding="utf-8")
        ok, reason = store.a1_verify_thesis_artifact(tid)
        assert not ok
        assert "clear-thesis-artifact" in reason

    def test_clear_thesis_artifact_is_explicit_recovery_action(self, store, tmp_path):
        missing_path = tmp_path / "missing.md"
        tid = _a1_flight(
            store,
            payload={
                "promote_done": True,
                "screen_done": True,
                "screen_approved_by_operator": True,
                "thesis_written": True,
                "thesis_artifact_verified": True,
                "thesis_artifact_verified_at": "2026-06-07T00:00:00+00:00",
                "thesis_artifact_sha256": "stale",
                "thesis_path": str(missing_path),
                "bear_done": True,
                "bear_verdict": "PROCEED",
            },
        )

        assert store.a1_resume_action(tid) == "thesis_artifact_invalid"
        ok, reason = store.a1_clear_thesis_artifact(tid, reason="missing artifact")

        assert ok, reason
        payload = json.loads(store.get_task(tid)["payload"])
        assert payload["thesis_written"] is False
        assert payload["bear_done"] is False
        assert "thesis_artifact_verified" not in payload
        assert "thesis_artifact_sha256" not in payload
        assert "thesis_artifact_drift_detected_at" not in payload
        assert payload["thesis_artifact_invalid_reason"] == "missing artifact"
        assert store.a1_resume_action(tid) == "dispatch_thesis"

    def test_verify_thesis_artifact_rejects_stale_artifact(self, store, tmp_path):
        thesis_path = tmp_path / "CDNS.md"
        thesis_path.write_text("# CDNS thesis\n", encoding="utf-8")
        os.utime(thesis_path, (1, 1))
        tid = _a1_flight(
            store,
            payload={
                "promote_done": True,
                "screen_done": True,
                "thesis_written": True,
                "bear_done": False,
                "thesis_path": str(thesis_path),
                "thesis_dispatch_started_at": "2026-06-07T00:00:00+00:00",
            },
        )

        ok, reason = store.a1_verify_thesis_artifact(tid)

        assert not ok
        assert "older than dispatch" in reason

    def test_force_verify_thesis_artifact_requires_log_ack(self, store, tmp_path):
        thesis_path = tmp_path / "CDNS.md"
        thesis_path.write_text("# CDNS thesis\n", encoding="utf-8")
        tid = _a1_flight(
            store,
            payload={
                "promote_done": True,
                "screen_done": True,
                "thesis_written": True,
                "bear_done": False,
                "thesis_path": str(thesis_path),
                "thesis_dispatch_started_at": "2026-06-07T00:00:00+00:00",
            },
        )

        ok, reason = store.a1_force_verify_thesis_artifact(
            tid,
            str(thesis_path),
            checked_log=False,
        )
        assert not ok
        assert "i-checked-the-log" in reason

        ok, reason = store.a1_force_verify_thesis_artifact(
            tid,
            str(thesis_path),
            checked_log=True,
        )

        assert ok, reason
        payload = json.loads(store.get_task(tid)["payload"])
        assert payload["thesis_artifact_verified"] is True
        assert payload["thesis_recovery_log_checked_at"]

    def test_record_screen_done_verifies_artifact(self, store, tmp_path):
        screen_path = tmp_path / "screen.json"
        screen_path.write_text('{"score": 82}\n', encoding="utf-8")
        tid = _a1_flight(
            store,
            payload={
                "promote_done": True,
                "screen_done": False,
            },
        )

        ok, reason = store.a1_record_screen_done(tid, str(screen_path))

        assert ok, reason
        payload = json.loads(store.get_task(tid)["payload"])
        assert payload["screen_done"] is True
        assert payload["screen_artifact_path"] == str(screen_path)
        assert payload["screen_done_at"]
        assert store.a1_resume_action(tid) == "await_screen_approval"

        ok, reason = store.a1_approve_screen(tid)

        assert ok, reason
        payload = json.loads(store.get_task(tid)["payload"])
        assert payload["screen_approved_by_operator"] is True
        assert payload["screen_approved_at"]
        assert store.a1_resume_action(tid) == "dispatch_thesis"

    def test_zombie_reclaim_clears_partial_a1_thesis_and_bear_flags(self, store):
        tid = _a1_flight(
            store,
            payload={
                "promote_done": True,
                "screen_done": True,
                "screen_approved_by_operator": True,
                "thesis_written": True,
                "thesis_artifact_verified": True,
                "thesis_artifact_sha256": "stale",
                "bear_done": True,
                "bear_verdict": "PROCEED",
            },
        )
        old = "2026-01-01T00:00:00+00:00"
        conn = store.connect()
        conn.execute(
            "UPDATE tasks SET status='running', started_at=?, heartbeat_at=?, worker_pid=NULL WHERE id=?",
            (old, old, tid),
        )
        conn.commit()
        conn.close()

        reclaimed = store.reclaim_zombies(timeout_s=1)

        assert tid in reclaimed
        task = store.get_task(tid)
        assert task["status"] == "ready"
        payload = json.loads(task["payload"])
        assert payload["thesis_written"] is False
        assert payload["bear_done"] is False
        assert "thesis_artifact_verified" not in payload
        assert "bear_verdict" not in payload
        assert "screen_approved_by_operator" not in payload
        assert "screen_approved_at" not in payload
        assert payload["worker_reclaim_reset_reason"].startswith("zombie reclaim")
        assert store.a1_resume_action(tid) == "await_screen_approval"

    def test_register_revision_cli_uses_locked_helper(self, store):
        tid = _a1_flight(store, payload={"revision_count": 0})

        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "scripts.lib.orchestrator_store",
                "register-revision",
                tid,
            ],
            env=os.environ.copy(),
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=15,
        )

        assert result.returncode == 0
        assert "revision registered: True" in result.stdout
        payload = json.loads(store.get_task(tid)["payload"])
        assert payload["revision_count"] == 1

    def test_register_revision_rejects_running_task(self, store):
        tid = _a1_flight(store)
        conn = store.connect()
        conn.execute("UPDATE tasks SET status='running' WHERE id=?", (tid,))
        conn.commit()
        conn.close()

        with pytest.raises(ValueError, match="cannot register revision while task is running"):
            store.a1_register_revision(tid)

    def test_complete_refuses_terminal_bear_veto_with_specific_message(self, store):
        tid = _a1_flight(store)
        store.transition(tid, "terminal_bear_veto")

        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "scripts.lib.orchestrator_store",
                "complete",
                tid,
            ],
            env=os.environ.copy(),
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=15,
        )

        assert result.returncode == 1
        assert "Cannot complete: task is terminal_bear_veto" in result.stderr

    def test_revision_limit_cancel_clears_blocker_reason(self, store):
        tid = _a1_flight(store, payload={"revision_count": 3})
        assert store.a1_register_revision(tid) is False
        assert store.get_task(tid)["blocker_reason"]

        store.transition(tid, "cancelled")

        task = store.get_task(tid)
        assert task["status"] == "cancelled"
        assert task["blocker_reason"] is None

    def test_revision_clears_stale_t7_evidence(self, store):
        tid = _a1_flight(
            store,
            payload={
                "revision_count": 1,
                "thesis_written": True,
                "bear_done": True,
                "bear_verdict": "PROCEED",
                "thesis_artifact_verified": True,
                "thesis_artifact_verified_at": "2026-06-07T00:00:00+00:00",
                "claim_decisions": [{"claim_id": "old"}],
                "claim_decisions_hash": "old-hash",
                "operator_override_reason": "old reason",
                "calx_override_acknowledged": True,
                "vendor_warning_acknowledged": True,
                "vendor_provenance": {"vendor": "old"},
            },
        )

        assert store.a1_register_revision(tid) is True

        payload = json.loads(store.get_task(tid)["payload"])
        assert payload["thesis_written"] is False
        assert payload["bear_done"] is False
        for key in (
            "bear_verdict",
            "thesis_artifact_verified",
            "thesis_artifact_verified_at",
            "claim_decisions",
            "claim_decisions_hash",
            "operator_override_reason",
            "calx_override_acknowledged",
            "vendor_warning_acknowledged",
            "vendor_provenance",
        ):
            assert key not in payload


class TestA1ProfilePreflight:
    def test_verify_thesis_accepts_promoted_or_screened_watchlist_status(self, store, tmp_path):
        from scripts.lib import orchestrator_profiles as profiles

        vault = tmp_path / "k2bi-vault"
        _write_registry(vault, "CDNS")
        # A1.1 fix (live MVP #1, 2026-06-07): thesis (Stage 5-7) runs AFTER
        # screen (Stage 4), which advances the watchlist promoted -> screened.
        # Both 'promoted' and 'screened' must satisfy the thesis precondition;
        # requiring exactly 'promoted' rejected every real (post-screen) ticker.
        for status in ("promoted", "screened"):
            _write_watchlist(vault, "CDNS", status)
            profiles.assert_a1_promoted_precondition("CDNS", str(vault))  # must not raise

        # Integration: the corrected status gate is honored through the FULL
        # preflight_k2bi path (payload -> symbol -> vault_root -> precondition).
        # A 'screened' ticker must no longer be rejected on the status gate
        # (it may still fail later on other preflight checks, but NOT on status).
        _write_watchlist(vault, "CDNS", "screened")
        tid = store.add_task(
            assignee_profile="k2bi",
            command_key="k2bi-verify-and-generate-thesis",
            success_criteria="verify thesis",
            permissions="analyst-command",
            entity_key="CDNS",
            payload={"symbol": "CDNS", "vault_root": str(vault), "payload_json": "{}"},
        )
        _ok, reason = profiles.preflight_k2bi(store.get_task(tid))
        assert "promoted' or 'screened" not in reason  # status gate no longer the blocker
        assert "got 'screened'" not in reason

        # A pre-promote / terminal status is still rejected.
        _write_watchlist(vault, "CDNS", "dropped")
        with pytest.raises(ValueError, match="promoted' or 'screened"):
            profiles.assert_a1_promoted_precondition("CDNS", str(vault))

    def test_promoted_precondition_rejects_non_string_or_duplicate_status(self, tmp_path):
        from scripts.lib import orchestrator_profiles as profiles

        vault = tmp_path / "k2bi-vault"
        path = _write_watchlist(vault, "CDNS", "promoted")
        path.write_text(
            "---\n"
            "symbol: CDNS\n"
            "status: promoted\n"
            "status: screened\n"
            "---\n",
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="duplicate status"):
            profiles.assert_a1_promoted_precondition("CDNS", str(vault))

        path.write_text(
            "---\n"
            "symbol: CDNS\n"
            "status: true\n"
            "---\n",
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="status must be a string"):
            profiles.assert_a1_promoted_precondition("CDNS", str(vault))

        path.write_text(
            "---\n"
            "symbol: CDNS\n"
            "aliases: &aliases [CDNS]\n"
            "status: promoted\n"
            "---\n",
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="YAML anchors or aliases are not allowed"):
            profiles.assert_a1_promoted_precondition("CDNS", str(vault))

        path.write_text(
            "---\n"
            "symbol: CDNS\n"
            "status: !!python/object/apply:os.system []\n"
            "---\n",
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="YAML tags are not allowed"):
            profiles.assert_a1_promoted_precondition("CDNS", str(vault))

    def test_bear_case_rejects_unpromoted_watchlist_status(self, store, tmp_path):
        from scripts.lib import orchestrator_profiles as profiles

        vault = tmp_path / "k2bi-vault"
        _write_registry(vault, "CDNS")
        # A1.1 fix (live MVP #1, 2026-06-07): bear-case (like thesis) accepts a
        # 'promoted' OR 'screened' ticker; only a pre-promote / terminal status
        # is rejected by the preflight. One task, watchlist flipped between
        # preflight calls (preflight_k2bi re-reads the watchlist each call), so
        # the one-flight entity lock is not tripped.
        _write_watchlist(vault, "CDNS", "screened")
        tid = store.add_task(
            assignee_profile="k2bi",
            command_key="k2bi-run-bear-case",
            success_criteria="bear case",
            permissions="analyst-command",
            entity_key="CDNS",
            payload={
                "symbol": "CDNS",
                "vault_root": str(vault),
                "payload_json": "{}",
            },
        )

        # Positive integration: a 'screened' bear task gets PAST the status gate
        # (it may fail later on bear_input, but NOT on the watchlist status).
        _ok, reason_ok = profiles.preflight_k2bi(store.get_task(tid))
        assert "promoted' or 'screened" not in reason_ok
        assert "got 'screened'" not in reason_ok

        # Negative: a pre-promote / terminal status IS rejected on the gate.
        _write_watchlist(vault, "CDNS", "dropped")
        ok, reason = profiles.preflight_k2bi(store.get_task(tid))
        assert not ok
        assert "promoted' or 'screened" in reason
        assert "got 'dropped'" in reason

    def test_a1_preflight_rejects_vault_root_split_brain(self, store, tmp_path):
        from scripts.lib import orchestrator_profiles as profiles

        trusted_vault = Path(os.environ["K2BI_VAULT_PATH"])
        _write_watchlist(trusted_vault, "CDNS", "promoted")
        _write_registry(trusted_vault, "CDNS")
        other_vault = tmp_path / "other-k2bi-vault"
        _write_watchlist(other_vault, "CDNS", "promoted")
        _write_registry(other_vault, "CDNS")
        tid = store.add_task(
            assignee_profile="k2bi",
            command_key="k2bi-verify-and-generate-thesis",
            success_criteria="thesis",
            permissions="analyst-command",
            entity_key="CDNS",
            payload={
                "symbol": "CDNS",
                "vault_root": str(other_vault),
                "payload_json": "{}",
            },
        )

        ok, reason = profiles.preflight_k2bi(store.get_task(tid))
        assert not ok
        assert "vault_root" in reason
        assert "profile K2Bi vault" in reason

    def test_a1_symbol_must_match_entity_key(self, store, tmp_path):
        from scripts.lib import orchestrator_profiles as profiles

        vault = tmp_path / "k2bi-vault"
        _write_watchlist(vault, "CDNS", "promoted")
        _write_registry(Path(os.environ["K2BI_VAULT_PATH"]), "CDNS")
        tid = store.add_task(
            assignee_profile="k2bi",
            command_key="k2bi-screen-enrich",
            success_criteria="screen",
            permissions="analyst-command",
            entity_key="AAPL",
            payload={"symbol": "CDNS", "vault_root": str(vault)},
        )

        ok, reason = profiles.preflight_k2bi(store.get_task(tid))
        assert not ok
        assert "payload symbol CDNS does not match entity_key AAPL" in reason

    def test_a1_preflight_requires_entity_key(self, store):
        from scripts.lib import orchestrator_profiles as profiles

        tid = store.add_task(
            assignee_profile="k2bi",
            command_key="k2bi-screen-enrich",
            success_criteria="screen",
            permissions="analyst-command",
            payload={"symbol": "CDNS"},
        )

        ok, reason = profiles.preflight_k2bi(store.get_task(tid))
        assert not ok
        assert "entity_key missing or invalid" in reason

    def test_legacy_smoke_bypasses_a1_symbol_gate(self):
        from scripts.lib import orchestrator_profiles as profiles

        ok, reason = profiles._preflight_a1_symbol_matches_entity(
            {"command_key": "k2bi-smoke-enrich-lrcx"},
            {},
        )
        assert ok
        assert reason == ""

    def test_screen_enrich_requires_canonical_registry_match_when_present(self, store):
        from scripts.lib import orchestrator_profiles as profiles

        vault = Path(os.environ["K2BI_VAULT_PATH"])
        registry_path = vault / "wiki" / "tickers" / "canonical-registry.json"
        registry_path.parent.mkdir(parents=True)
        registry_path.write_text(json.dumps({"AAPL": {"name": "Apple Inc."}}), encoding="utf-8")
        tid = store.add_task(
            assignee_profile="k2bi",
            command_key="k2bi-screen-enrich",
            success_criteria="screen",
            permissions="analyst-command",
            entity_key="CDNS",
            payload={"symbol": "CDNS"},
        )

        ok, reason = profiles.preflight_k2bi(store.get_task(tid))
        assert not ok
        assert "unknown canonical ticker CDNS" in reason

    def test_canonical_registry_errors_are_distinct(self, tmp_path, monkeypatch):
        from scripts.lib import orchestrator_profiles as profiles

        vault = tmp_path / "k2bi-vault"
        monkeypatch.setenv("K2BI_VAULT_PATH", str(vault))

        ok, reason = profiles._canonical_symbol_known("CDNS")
        assert not ok
        assert "missing or unreadable" in reason

        registry = vault / "wiki" / "tickers" / "canonical-registry.json"
        registry.parent.mkdir(parents=True)
        registry.write_text("", encoding="utf-8")
        ok, reason = profiles._canonical_symbol_known("CDNS")
        assert not ok
        assert "registry empty" in reason

        registry.write_text("{", encoding="utf-8")
        ok, reason = profiles._canonical_symbol_known("CDNS")
        assert not ok
        assert "malformed JSON" in reason

    def test_a1_preflight_validates_inline_adapter_payload_shape(self, store, tmp_path):
        from scripts.lib import orchestrator_profiles as profiles

        vault = Path(os.environ["K2BI_VAULT_PATH"])
        _write_watchlist(vault, "CDNS", "promoted")
        _write_registry(vault, "CDNS")
        tid = store.add_task(
            assignee_profile="k2bi",
            command_key="k2bi-verify-and-generate-thesis",
            success_criteria="thesis",
            permissions="analyst-command",
            entity_key="CDNS",
            payload={
                "symbol": "CDNS",
                "vault_root": str(vault),
                "payload_json": json.dumps({"claim_decisions": []}),
            },
        )

        ok, reason = profiles.preflight_k2bi(store.get_task(tid))
        assert not ok
        assert "missing thesis_input.symbol" in reason

    def test_a1_preflight_validates_claim_decision_items(self, store):
        from scripts.lib import orchestrator_profiles as profiles

        vault = Path(os.environ["K2BI_VAULT_PATH"])
        _write_watchlist(vault, "CDNS", "promoted")
        _write_registry(vault, "CDNS")
        tid = store.add_task(
            assignee_profile="k2bi",
            command_key="k2bi-verify-and-generate-thesis",
            success_criteria="thesis",
            permissions="analyst-command",
            entity_key="CDNS",
            payload={
                "symbol": "CDNS",
                "vault_root": str(vault),
                "payload_json": json.dumps(
                    {
                        "thesis_input": {
                            "symbol": "CDNS",
                            "title": "CDNS thesis",
                            "base_sources": ["https://example.com/source"],
                        },
                        "claim_decisions": [1],
                    }
                ),
            },
        )

        ok, reason = profiles.preflight_k2bi(store.get_task(tid))
        assert not ok
        assert "claim_decisions items must be objects" in reason

    def test_a1_preflight_validates_operator_mark_vocabulary(self, store):
        from scripts.lib import orchestrator_profiles as profiles

        vault = Path(os.environ["K2BI_VAULT_PATH"])
        _write_watchlist(vault, "CDNS", "promoted")
        _write_registry(vault, "CDNS")
        tid = store.add_task(
            assignee_profile="k2bi",
            command_key="k2bi-verify-and-generate-thesis",
            success_criteria="thesis",
            permissions="analyst-command",
            entity_key="CDNS",
            payload={
                "symbol": "CDNS",
                "vault_root": str(vault),
                "payload_json": json.dumps(
                    {
                        "thesis_input": {
                            "symbol": "CDNS",
                            "title": "CDNS thesis",
                            "base_sources": ["https://example.com/source"],
                        },
                        "claim_decisions": [
                            {
                                "claim_id": "c1",
                                "operator_mark": "approved",
                            }
                        ],
                    }
                ),
            },
        )

        ok, reason = profiles.preflight_k2bi(store.get_task(tid))
        assert not ok
        assert "invalid operator_mark" in reason
        assert "verified" in reason

    def test_a1_preflight_requires_non_empty_base_sources(self, store):
        from scripts.lib import orchestrator_profiles as profiles

        vault = Path(os.environ["K2BI_VAULT_PATH"])
        _write_watchlist(vault, "CDNS", "promoted")
        _write_registry(vault, "CDNS")
        tid = store.add_task(
            assignee_profile="k2bi",
            command_key="k2bi-verify-and-generate-thesis",
            success_criteria="thesis",
            permissions="analyst-command",
            entity_key="CDNS",
            payload={
                "symbol": "CDNS",
                "vault_root": str(vault),
                "payload_json": json.dumps(
                    {
                        "thesis_input": {
                            "symbol": "CDNS",
                            "title": "CDNS thesis",
                            "base_sources": [],
                        },
                        "claim_decisions": [],
                    }
                ),
            },
        )

        ok, reason = profiles.preflight_k2bi(store.get_task(tid))
        assert not ok
        assert "non-empty thesis_input.base_sources" in reason

    def test_bear_preflight_requires_verified_thesis_artifact(self, store):
        from scripts.lib import orchestrator_profiles as profiles

        vault = Path(os.environ["K2BI_VAULT_PATH"])
        _write_watchlist(vault, "CDNS", "promoted")
        _write_registry(vault, "CDNS")
        tid = store.add_task(
            assignee_profile="k2bi",
            command_key="k2bi-run-bear-case",
            success_criteria="bear",
            permissions="analyst-command",
            entity_key="CDNS",
            payload={
                "symbol": "CDNS",
                "vault_root": str(vault),
                "payload_json": json.dumps({"bear_input": {"bear_conviction": 40}}),
                "thesis_written": True,
                "thesis_artifact_verified": False,
            },
        )

        ok, reason = profiles.preflight_k2bi(store.get_task(tid))
        assert not ok
        assert "thesis_artifact_verified=true" in reason

    def test_bear_preflight_rechecks_verified_thesis_artifact_sha(self, store, tmp_path):
        from scripts.lib import orchestrator_profiles as profiles

        vault = Path(os.environ["K2BI_VAULT_PATH"])
        _write_watchlist(vault, "CDNS", "promoted")
        _write_registry(vault, "CDNS")
        thesis_path = tmp_path / "CDNS.md"
        thesis_path.write_text("# CDNS thesis\n", encoding="utf-8")
        tid = store.add_task(
            assignee_profile="k2bi",
            command_key="k2bi-run-bear-case",
            success_criteria="bear",
            permissions="analyst-command",
            entity_key="CDNS",
            payload={
                "symbol": "CDNS",
                "vault_root": str(vault),
                "payload_json": json.dumps({"bear_input": {"bear_conviction": 40}}),
                "thesis_written": True,
                "thesis_artifact_verified": True,
                "thesis_path": str(thesis_path),
                "thesis_artifact_sha256": "not-the-real-sha",
            },
        )

        ok, reason = profiles.preflight_k2bi(store.get_task(tid))
        assert not ok
        assert "sha256 mismatch" in reason

    def test_symbol_regex_requires_alpha_character(self):
        from scripts.lib import orchestrator_profiles as profiles

        assert profiles.SYMBOL_RE.match("CDNS")
        assert not profiles.SYMBOL_RE.match("1234")

    def test_verify_and_bear_require_canonical_registry_match(self, store, tmp_path):
        from scripts.lib import orchestrator_profiles as profiles

        vault = Path(os.environ["K2BI_VAULT_PATH"])
        _write_watchlist(vault, "CDNS", "promoted")
        _write_watchlist(vault, "MSFT", "promoted")
        _write_registry(vault, "AAPL")
        for command_key, symbol in (
            ("k2bi-verify-and-generate-thesis", "CDNS"),
            ("k2bi-run-bear-case", "MSFT"),
        ):
            tid = store.add_task(
                assignee_profile="k2bi",
                command_key=command_key,
                success_criteria="adapter",
                permissions="analyst-command",
                entity_key=symbol,
                payload={
                    "symbol": symbol,
                    "vault_root": str(vault),
                    "payload_json": "{}",
                },
            )

            ok, reason = profiles.preflight_k2bi(store.get_task(tid))
            assert not ok
            assert f"unknown canonical ticker {symbol}" in reason

    def test_a1_adapter_commands_are_allowlisted(self):
        from scripts.lib import orchestrator_profiles as profiles

        verify_payload = {"symbol": "CDNS", "payload_json": "{}"}
        verify_before = dict(verify_payload)
        screen_argv = profiles.resolve_command(
            "k2bi",
            "k2bi-screen-enrich",
            payload={"symbol": "CDNS"},
        )
        verify_argv = profiles.resolve_command(
            "k2bi",
            "k2bi-verify-and-generate-thesis",
            payload=verify_payload,
        )
        bear_argv = profiles.resolve_command(
            "k2bi",
            "k2bi-run-bear-case",
            payload={"symbol": "CDNS", "payload_json": "{}"},
        )

        assert screen_argv == [
            "python3",
            "-m",
            "scripts.lib.invest_screen",
            "--enrich",
            "CDNS",
        ]
        assert verify_argv is not None
        assert "orchestrator_k2bi_adapter.py" in " ".join(verify_argv)
        assert "--workspace" in verify_argv
        assert "verify-and-generate-thesis" in verify_argv
        assert verify_payload == verify_before
        assert bear_argv is not None
        assert "run-bear-case" in bear_argv

    def test_payload_json_must_be_string_and_bounded(self):
        from scripts.lib import orchestrator_profiles as profiles

        assert (
            profiles.resolve_command(
                "k2bi",
                "k2bi-verify-and-generate-thesis",
                payload={"symbol": "CDNS", "payload_json": {"not": "a string"}},
            )
            is None
        )
        assert (
            profiles.resolve_command(
                "k2bi",
                "k2bi-verify-and-generate-thesis",
                payload={"symbol": "CDNS", "payload_json": "x" * 1_000_001},
            )
            is None
        )
        assert (
            profiles.resolve_command(
                "k2bi",
                "k2bi-verify-and-generate-thesis",
                payload={"symbol": "CDNS", "vault_root": "/tmp/k2bi-vault"},
            )
            is None
        )
        assert (
            profiles.resolve_command(
                "k2bi",
                "k2bi-verify-and-generate-thesis",
                payload={"symbol": "CDNS", "payload_json": '{"note": "bad\tchar"}'},
            )
            is None
        )


class TestA1AdapterRunner:
    def test_adapter_rejects_vault_root_outside_allowlist(self, tmp_path):
        workspace = tmp_path / "workspace"
        (workspace / "scripts" / "lib").mkdir(parents=True)
        allowed_vault = tmp_path / "k2bi-vault"
        allowed_vault.mkdir(exist_ok=True)
        outside_vault = tmp_path / "other-vault"
        outside_vault.mkdir()
        payload = {
            "thesis_input": {"symbol": "CDNS"},
            "vault_root": str(outside_vault),
            "claim_decisions": [],
        }

        result = subprocess.run(
            [
                sys.executable,
                str(REPO_ROOT / "scripts" / "lib" / "orchestrator_k2bi_adapter.py"),
                "verify-and-generate-thesis",
                "--workspace",
                str(workspace),
                "--payload-json",
                json.dumps(payload),
            ],
            env={**os.environ, "K2BI_VAULT_PATH": str(allowed_vault)},
            capture_output=True,
            text=True,
            timeout=15,
        )

        assert result.returncode == 2
        assert "vault_root" in result.stderr
        assert "outside allowed vault root" in result.stderr
        error = json.loads(result.stdout)
        assert error["status"] == "error"
        assert error["category"] == "validation"
        assert error["retryable"] is False
        assert error["exit_code"] == 2

    def test_adapter_error_envelope_classifies_os_errors_by_errno(self):
        from scripts.lib import orchestrator_k2bi_adapter as adapter

        transient = adapter._adapter_error_envelope(OSError(errno.EAGAIN, "try again"))
        assert transient["category"] == "transient"
        assert transient["retryable"] is True
        assert transient["exit_code"] == 3

        missing = adapter._adapter_error_envelope(FileNotFoundError(errno.ENOENT, "missing"))
        assert missing["category"] == "transient"
        assert missing["retryable"] is True
        assert missing["exit_code"] == 3

        permanent = adapter._adapter_error_envelope(OSError(errno.ENOSPC, "no space"))
        assert permanent["category"] == "environment"
        assert permanent["retryable"] is False
        assert permanent["exit_code"] == 4

    def test_payload_json_carrier_checks_are_explicit(self):
        from scripts.lib import orchestrator_k2bi_adapter as adapter

        with pytest.raises(ValueError, match="must not be empty"):
            adapter._load_payload(types.SimpleNamespace(payload_json="", payload_path=None))
        with pytest.raises(ValueError, match="exactly one"):
            adapter._load_payload(types.SimpleNamespace(payload_json="", payload_path="/tmp/x"))

    def test_payload_file_is_read_through_fd_not_path_read_text(self, tmp_path, monkeypatch):
        from scripts.lib import orchestrator_k2bi_adapter as adapter

        allowed = tmp_path / "allowed"
        allowed.mkdir()
        payload_path = allowed / "payload.json"
        payload_path.write_text('{"ok": true}', encoding="utf-8")
        monkeypatch.setenv("K2B_ORCH_ADAPTER_PAYLOAD_DIR", str(allowed))
        monkeypatch.setattr(
            Path,
            "read_text",
            lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("Path.read_text used")),
        )

        assert adapter._read_allowed_json_file(payload_path) == '{"ok": true}'

    def test_payload_file_rejects_fd_opened_outside_allowed_root(self, tmp_path, monkeypatch):
        from scripts.lib import orchestrator_k2bi_adapter as adapter

        allowed = tmp_path / "allowed"
        outside = tmp_path / "outside"
        allowed.mkdir()
        outside.mkdir()
        payload_path = allowed / "payload.json"
        outside_path = outside / "payload.json"
        payload_path.write_text('{"ok": true}', encoding="utf-8")
        outside_path.write_text('{"outside": true}', encoding="utf-8")
        monkeypatch.setenv("K2B_ORCH_ADAPTER_PAYLOAD_DIR", str(allowed))
        real_open = os.open

        def fake_open(path, flags):
            return real_open(str(outside_path), flags)

        monkeypatch.setattr(adapter.os, "open", fake_open)
        with pytest.raises(ValueError, match="outside allowed payload directory"):
            adapter._read_allowed_json_file(payload_path)

    def test_payload_file_requires_fd_path_verification(self, tmp_path, monkeypatch):
        from scripts.lib import orchestrator_k2bi_adapter as adapter

        allowed = tmp_path / "allowed"
        allowed.mkdir()
        payload_path = allowed / "payload.json"
        payload_path.write_text('{"ok": true}', encoding="utf-8")
        monkeypatch.setenv("K2B_ORCH_ADAPTER_PAYLOAD_DIR", str(allowed))
        monkeypatch.setattr(adapter, "_fd_realpath", lambda fd: None)

        with pytest.raises(ValueError, match="fd path verification unavailable"):
            adapter._read_allowed_json_file(payload_path)

    def test_payload_file_fd_verification_has_no_env_bypass(self, tmp_path, monkeypatch):
        from scripts.lib import orchestrator_k2bi_adapter as adapter

        allowed = tmp_path / "allowed"
        allowed.mkdir()
        payload_path = allowed / "payload.json"
        payload_path.write_text('{"ok": true}', encoding="utf-8")
        monkeypatch.setenv("K2B_ORCH_ADAPTER_PAYLOAD_DIR", str(allowed))
        monkeypatch.setenv("K2B_ORCH_ALLOW_DEGRADED_FD_VERIFY", "1")
        monkeypatch.setattr(adapter, "_fd_realpath", lambda fd: None)

        with pytest.raises(ValueError, match="fd path verification unavailable"):
            adapter._read_allowed_json_file(payload_path)

    def test_payload_file_size_is_bounded(self, tmp_path, monkeypatch):
        from scripts.lib import orchestrator_k2bi_adapter as adapter

        allowed = tmp_path / "allowed"
        allowed.mkdir()
        payload_path = allowed / "payload.json"
        payload_path.write_text('{"ok": true}', encoding="utf-8")
        monkeypatch.setenv("K2B_ORCH_ADAPTER_PAYLOAD_DIR", str(allowed))
        monkeypatch.setenv("K2B_ORCH_ADAPTER_MAX_PAYLOAD_FILE_BYTES", "4")

        with pytest.raises(ValueError, match="exceeds maximum"):
            adapter._read_allowed_json_file(payload_path)

    def test_dataclass_coercion_requires_exact_fields(self):
        from dataclasses import make_dataclass
        from scripts.lib import orchestrator_k2bi_adapter as adapter

        StrictInput = make_dataclass(
            "StrictInput",
            [("symbol", str), ("required_count", int)],
        )

        with pytest.raises(ValueError, match="missing required field required_count"):
            adapter._dataclass_from_dict(StrictInput, {"symbol": "CDNS"})
        with pytest.raises(ValueError, match="unexpected field extra"):
            adapter._dataclass_from_dict(
                StrictInput,
                {"symbol": "CDNS", "required_count": 1, "extra": "ignored"},
            )

    def test_coerce_unknown_annotation_fails_closed(self):
        from dataclasses import make_dataclass
        from decimal import Decimal
        from scripts.lib import orchestrator_k2bi_adapter as adapter

        StrictInput = make_dataclass("StrictInput", [("price", Decimal)])

        with pytest.raises(ValueError, match="unsupported type annotation"):
            adapter._dataclass_from_dict(StrictInput, {"price": "1.23"})

    def test_coerce_input_depth_is_bounded(self, monkeypatch):
        from dataclasses import make_dataclass
        from scripts.lib import orchestrator_k2bi_adapter as adapter

        Inner = make_dataclass("Inner", [("value", int)])
        Outer = make_dataclass("Outer", [("child", Inner)])

        monkeypatch.setenv("K2B_ORCH_ADAPTER_MAX_INPUT_DEPTH", "0")
        with pytest.raises(ValueError, match="adapter input exceeds maximum depth"):
            adapter._dataclass_from_dict(Outer, {"child": {"value": 1}})

    def test_bear_case_position_size_is_validated_before_adapter_call(self, tmp_path):
        ws = tmp_path / "fake-k2bi"
        lib = ws / "scripts" / "lib"
        lib.mkdir(parents=True)
        (ws / "scripts" / "__init__.py").write_text("", encoding="utf-8")
        (lib / "__init__.py").write_text("", encoding="utf-8")
        (lib / "invest_thesis.py").write_text("", encoding="utf-8")
        (lib / "invest_bear_case.py").write_text(
            "from dataclasses import dataclass\n"
            "@dataclass\n"
            "class BearCaseInput:\n"
            "    bear_conviction: int\n"
            "def run_bear_case(*args, **kwargs):\n"
            "    raise AssertionError('should fail before adapter call')\n",
            encoding="utf-8",
        )
        vault = tmp_path / "vault"
        vault.mkdir(exist_ok=True)
        payload = {
            "symbol": "CDNS",
            "vault_root": str(vault),
            "bear_input": {"bear_conviction": 55},
            "position_size_hkd": -1,
        }

        result = subprocess.run(
            [
                sys.executable,
                str(REPO_ROOT / "scripts" / "lib" / "orchestrator_k2bi_adapter.py"),
                "run-bear-case",
                "--workspace",
                str(ws),
                "--payload-json",
                json.dumps(payload),
            ],
            env={**os.environ, "K2BI_VAULT_PATH": str(vault)},
            capture_output=True,
            text=True,
            timeout=15,
        )

        assert result.returncode == 2
        assert "position_size_hkd must be between 0 and" in result.stderr

    def test_bear_case_position_size_rejects_nan(self, tmp_path):
        from scripts.lib import orchestrator_k2bi_adapter as adapter

        with pytest.raises(ValueError, match="must be finite"):
            adapter._validate_position_size_hkd(float("nan"))

    def test_adapter_main_restores_sys_path_on_failure(self, tmp_path, monkeypatch):
        from scripts.lib import orchestrator_k2bi_adapter as adapter

        workspace = tmp_path / "workspace"
        (workspace / "scripts" / "lib").mkdir(parents=True)
        outside_vault = tmp_path / "outside-vault"
        outside_vault.mkdir()
        allowed_vault = tmp_path / "allowed-vault"
        allowed_vault.mkdir()
        monkeypatch.setenv("K2BI_VAULT_PATH", str(allowed_vault))
        before = list(sys.path)

        rc = adapter.main(
            [
                "verify-and-generate-thesis",
                "--workspace",
                str(workspace),
                "--payload-json",
                json.dumps(
                    {
                        "thesis_input": {"symbol": "CDNS"},
                        "vault_root": str(outside_vault),
                        "claim_decisions": [],
                    }
                ),
            ]
        )

        assert rc == 2
        assert sys.path == before

    def test_adapter_failure_stdout_stays_json_when_error_dump_fails(
        self,
        tmp_path,
        monkeypatch,
        capsys,
    ):
        from scripts.lib import orchestrator_k2bi_adapter as adapter

        workspace = tmp_path / "workspace"
        (workspace / "scripts" / "lib").mkdir(parents=True)

        def boom(_args):
            raise RuntimeError("boom")

        def broken_dumps(*_args, **_kwargs):
            print("partial junk")
            raise TypeError("json unavailable")

        monkeypatch.setattr(adapter, "_load_payload", boom)
        monkeypatch.setattr(adapter.json, "dumps", broken_dumps)

        rc = adapter.main(
            [
                "verify-and-generate-thesis",
                "--workspace",
                str(workspace),
                "--payload-json",
                "{}",
            ]
        )

        captured = capsys.readouterr()
        assert rc == 5
        body = json.loads(captured.out)
        assert body["status"] == "error"
        assert body["category"] == "unexpected"
        assert "partial junk" not in captured.out
        assert "boom" in captured.err

    def test_adapter_main_restores_preexisting_invest_modules_after_failure(
        self,
        tmp_path,
        monkeypatch,
    ):
        from scripts.lib import orchestrator_k2bi_adapter as adapter

        workspace = tmp_path / "workspace"
        (workspace / "scripts" / "lib").mkdir(parents=True)
        outside_vault = tmp_path / "outside-vault"
        outside_vault.mkdir()
        allowed_vault = tmp_path / "allowed-vault"
        allowed_vault.mkdir()
        monkeypatch.setenv("K2BI_VAULT_PATH", str(allowed_vault))
        original_thesis = types.ModuleType("scripts.lib.invest_thesis")
        original_sub_util = types.ModuleType("scripts.lib.invest_thesis.sub_util")
        original_custom = types.ModuleType("scripts.lib.invest_custom_tool")
        sys.modules["scripts.lib.invest_thesis"] = original_thesis
        sys.modules["scripts.lib.invest_thesis.sub_util"] = original_sub_util
        sys.modules["scripts.lib.invest_custom_tool"] = original_custom
        original_module_keys = set(sys.modules)

        try:
            rc = adapter.main(
                [
                    "verify-and-generate-thesis",
                    "--workspace",
                    str(workspace),
                    "--payload-json",
                    json.dumps(
                        {
                            "thesis_input": {"symbol": "CDNS"},
                            "vault_root": str(outside_vault),
                            "claim_decisions": [],
                        }
                    ),
                ]
            )

            assert rc == 2
            assert sys.modules["scripts.lib.invest_thesis"] is original_thesis
            assert sys.modules["scripts.lib.invest_thesis.sub_util"] is original_sub_util
            assert sys.modules["scripts.lib.invest_custom_tool"] is original_custom
            assert set(sys.modules) == original_module_keys
        finally:
            sys.modules.pop("scripts.lib.invest_thesis", None)
            sys.modules.pop("scripts.lib.invest_thesis.sub_util", None)
            sys.modules.pop("scripts.lib.invest_custom_tool", None)

    def test_adapter_cleanup_failure_preserves_success_observable_on_stderr(
        self,
        tmp_path,
        monkeypatch,
        capsys,
    ):
        from scripts.lib import orchestrator_k2bi_adapter as adapter

        ws = tmp_path / "fake-k2bi"
        _write_realistic_adapter_workspace(ws)
        vault = tmp_path / "vault"
        vault.mkdir(exist_ok=True)
        monkeypatch.setenv("K2BI_VAULT_PATH", str(vault))
        payload = {
            "symbol": "CDNS",
            "thesis_input": {
                "symbol": "CDNS",
                "title": "CDNS thesis",
                "base_sources": ["https://example.com/10q"],
            },
            "vault_root": str(vault),
            "claim_decisions": [
                {
                    "claim_id": "c1",
                    "claim_text": "Revenue grew.",
                    "claim_load_bearing": True,
                    "source_url": "https://example.com/10q",
                    "source_excerpt": "Revenue grew.",
                    "curated_framing": "SEC filing supports revenue growth.",
                    "operator_mark": "verified",
                    "operator_note": None,
                    "source_vendor": "SEC",
                    "spot_check_vendor": None,
                }
            ],
            "operator_override_reason": None,
            "calx_override_acknowledged": False,
            "vendor_warning_acknowledged": False,
            "vendor_provenance": {"primary": "SEC"},
            "refresh": True,
            "learning_stage": "advanced",
        }

        def fail_restore(_snapshot):
            raise RuntimeError("restore failed")

        monkeypatch.setattr(adapter, "_restore_module_snapshot", fail_restore)

        saved_packages = {
            name: sys.modules.pop(name)
            for name in ("scripts", "scripts.lib")
            if name in sys.modules
        }
        try:
            rc = adapter.main(
                [
                    "verify-and-generate-thesis",
                    "--workspace",
                    str(ws),
                    "--payload-json",
                    json.dumps(payload),
                ]
            )
        finally:
            for name in list(sys.modules):
                if name.startswith("scripts.lib.invest"):
                    sys.modules.pop(name, None)
            for name in ("scripts", "scripts.lib"):
                sys.modules.pop(name, None)
            sys.modules.update(saved_packages)

        captured = capsys.readouterr()
        # Checkpoint-2 fix (2026-06-07): an in-process cleanup failure must NOT
        # mask a successful result. The success envelope stays on stdout (rc 0);
        # the cleanup error is observable on stderr only, never replacing the
        # worker's success contract.
        assert rc == 0
        result = json.loads(captured.out)
        assert result["status"] == "ok"
        assert "restore failed" in captured.err

    def test_adapter_original_error_json_wins_over_cleanup_failure(
        self,
        tmp_path,
        monkeypatch,
        capsys,
    ):
        from scripts.lib import orchestrator_k2bi_adapter as adapter

        workspace = tmp_path / "workspace"
        (workspace / "scripts" / "lib").mkdir(parents=True)
        outside_vault = tmp_path / "outside-vault"
        outside_vault.mkdir()
        allowed_vault = tmp_path / "allowed-vault"
        allowed_vault.mkdir()
        monkeypatch.setenv("K2BI_VAULT_PATH", str(allowed_vault))

        def fail_restore(_snapshot):
            raise RuntimeError("restore failed")

        monkeypatch.setattr(adapter, "_restore_module_snapshot", fail_restore)

        rc = adapter.main(
            [
                "verify-and-generate-thesis",
                "--workspace",
                str(workspace),
                "--payload-json",
                json.dumps(
                    {
                        "thesis_input": {"symbol": "CDNS"},
                        "vault_root": str(outside_vault),
                        "claim_decisions": [],
                    }
                ),
            ]
        )

        captured = capsys.readouterr()
        assert rc == 2
        error = json.loads(captured.out)
        assert error["category"] == "validation"
        assert "outside allowed vault root" in error["message"]
        assert "restore failed" in captured.err


    def test_adapter_main_removes_invest_modules_after_success(
        self,
        tmp_path,
        monkeypatch,
        capsys,
    ):
        from scripts.lib import orchestrator_k2bi_adapter as adapter

        ws = tmp_path / "fake-k2bi"
        _write_realistic_adapter_workspace(ws)
        vault = tmp_path / "vault"
        vault.mkdir(exist_ok=True)
        monkeypatch.setenv("K2BI_VAULT_PATH", str(vault))
        payload = {
            "symbol": "CDNS",
            "thesis_input": {
                "symbol": "CDNS",
                "title": "CDNS thesis",
                "base_sources": ["https://example.com/10q"],
            },
            "vault_root": str(vault),
            "claim_decisions": [
                {
                    "claim_id": "c1",
                    "claim_text": "Revenue grew.",
                    "claim_load_bearing": True,
                    "source_url": "https://example.com/10q",
                    "source_excerpt": "Revenue grew.",
                    "curated_framing": "SEC filing supports revenue growth.",
                    "operator_mark": "verified",
                    "operator_note": None,
                    "source_vendor": "SEC",
                    "spot_check_vendor": None,
                }
            ],
            "operator_override_reason": None,
            "calx_override_acknowledged": False,
            "vendor_warning_acknowledged": False,
            "vendor_provenance": {"primary": "SEC"},
            "refresh": True,
            "learning_stage": "advanced",
        }

        saved_packages = {
            name: sys.modules.pop(name)
            for name in ("scripts", "scripts.lib")
            if name in sys.modules
        }
        try:
            rc = adapter.main(
                [
                    "verify-and-generate-thesis",
                    "--workspace",
                    str(ws),
                    "--payload-json",
                    json.dumps(payload),
                ]
            )

            captured = capsys.readouterr()
            assert rc == 0, captured.err
            assert json.loads(captured.out)["status"] == "ok"
            assert "scripts.lib.invest_thesis" not in sys.modules
            assert "scripts.lib.invest_orchestrator_adapters" not in sys.modules
            assert "scripts.lib.invest_bear_case" not in sys.modules
            assert "scripts.lib.invest_shared" not in sys.modules
        finally:
            for name in ("scripts", "scripts.lib"):
                sys.modules.pop(name, None)
            sys.modules.update(saved_packages)

    def test_fd_realpath_linux_fallback_does_not_resolve_target_symlinks(
        self,
        monkeypatch,
    ):
        from scripts.lib import orchestrator_k2bi_adapter as adapter

        monkeypatch.setattr(adapter.fcntl, "F_GETPATH", None, raising=False)
        monkeypatch.setattr(
            adapter.os,
            "readlink",
            lambda _path: "/allowed/link/payload.json",
        )

        def fail_realpath(_path):
            raise AssertionError("realpath should not be used for procfs fd target")

        monkeypatch.setattr(adapter.os.path, "realpath", fail_realpath)

        assert adapter._fd_realpath(3) == Path("/allowed/link/payload.json")

    def test_adapter_success_path_with_realistic_k2bi_dataclasses(self, tmp_path):
        ws = tmp_path / "fake-k2bi"
        _write_realistic_adapter_workspace(ws)
        vault = tmp_path / "vault"
        vault.mkdir(exist_ok=True)
        thesis_payload = {
            "symbol": "CDNS",
            "thesis_input": {
                "symbol": "CDNS",
                "title": "CDNS thesis",
                "base_sources": ["https://example.com/10q"],
            },
            "vault_root": str(vault),
            "claim_decisions": [
                {
                    "claim_id": "c1",
                    "claim_text": "Revenue grew.",
                    "claim_load_bearing": True,
                    "source_url": "https://example.com/10q",
                    "source_excerpt": "Revenue grew.",
                    "curated_framing": "SEC filing supports revenue growth.",
                    "operator_mark": "verified",
                    "operator_note": None,
                    "source_vendor": "SEC",
                    "spot_check_vendor": None,
                }
            ],
            "operator_override_reason": None,
            "calx_override_acknowledged": False,
            "vendor_warning_acknowledged": False,
            "vendor_provenance": {"primary": "SEC"},
            "refresh": True,
            "learning_stage": "advanced",
        }

        thesis = subprocess.run(
            [
                sys.executable,
                str(REPO_ROOT / "scripts" / "lib" / "orchestrator_k2bi_adapter.py"),
                "verify-and-generate-thesis",
                "--workspace",
                str(ws),
                "--payload-json",
                json.dumps(thesis_payload),
            ],
            cwd=str(ws),
            env={**os.environ, "K2BI_VAULT_PATH": str(vault)},
            capture_output=True,
            text=True,
            timeout=15,
        )

        assert thesis.returncode == 0, thesis.stderr
        thesis_output = json.loads(thesis.stdout)
        assert thesis_output["status"] == "ok"
        assert thesis_output["result"]["path"].endswith("wiki/theses/CDNS.md")
        assert thesis_output["result"]["written"] is True
        assert thesis_output["result"]["claim_count"] == 1
        assert thesis_output["result"]["refresh"] is True

        bear_payload = {
            "symbol": "CDNS",
            "vault_root": str(vault),
            "bear_input": {
                "bear_conviction": 37,
                "objections": ["margin pressure"],
            },
            "refresh": True,
            "learning_stage": "advanced",
            "position_size_hkd": 25000,
        }
        bear = subprocess.run(
            [
                sys.executable,
                str(REPO_ROOT / "scripts" / "lib" / "orchestrator_k2bi_adapter.py"),
                "run-bear-case",
                "--workspace",
                str(ws),
                "--payload-json",
                json.dumps(bear_payload),
            ],
            cwd=str(ws),
            env={**os.environ, "K2BI_VAULT_PATH": str(vault)},
            capture_output=True,
            text=True,
            timeout=15,
        )

        assert bear.returncode == 0, bear.stderr
        bear_output = json.loads(bear.stdout)
        assert bear_output["status"] == "ok"
        assert bear_output["result"]["path"].endswith("wiki/bear-cases/CDNS.md")
        assert bear_output["result"]["bear_verdict"] == "PROCEED"
        assert bear_output["result"]["bear_conviction"] == 37
        assert bear_output["result"]["position_size_hkd"] == 25000

    def test_adapter_output_is_bounded(self, tmp_path):
        ws = tmp_path / "fake-k2bi"
        lib = ws / "scripts" / "lib"
        lib.mkdir(parents=True)
        (ws / "scripts" / "__init__.py").write_text("", encoding="utf-8")
        (lib / "__init__.py").write_text("", encoding="utf-8")
        (lib / "invest_thesis.py").write_text(
            "from dataclasses import dataclass\n"
            "@dataclass\n"
            "class ThesisInput:\n"
            "    symbol: str\n",
            encoding="utf-8",
        )
        (lib / "invest_bear_case.py").write_text("", encoding="utf-8")
        (lib / "invest_orchestrator_adapters.py").write_text(
            "from dataclasses import dataclass\n"
            "class OrchestratorGateError(ValueError): pass\n"
            "@dataclass(frozen=True)\n"
            "class ThesisClaimDecision:\n"
            "    claim_id: str\n"
            "@dataclass\n"
            "class ThesisResult:\n"
            "    body: str\n"
            "def verify_and_generate_thesis(*args, **kwargs):\n"
            "    return ThesisResult('x' * 256)\n",
            encoding="utf-8",
        )
        vault = tmp_path / "vault"
        vault.mkdir(exist_ok=True)
        payload = {
            "symbol": "CDNS",
            "thesis_input": {"symbol": "CDNS"},
            "vault_root": str(vault),
            "claim_decisions": [{"claim_id": "c1"}],
        }

        result = subprocess.run(
            [
                sys.executable,
                str(REPO_ROOT / "scripts" / "lib" / "orchestrator_k2bi_adapter.py"),
                "verify-and-generate-thesis",
                "--workspace",
                str(ws),
                "--payload-json",
                json.dumps(payload),
            ],
            env={
                **os.environ,
                "K2BI_VAULT_PATH": str(vault),
                "K2B_ORCH_ADAPTER_MAX_OUTPUT_BYTES": "128",
            },
            capture_output=True,
            text=True,
            timeout=15,
        )

        assert result.returncode == 2
        assert "adapter output exceeds maximum" in result.stderr

    def test_refused_claim_without_override_refuses_without_coercion(self, tmp_path):
        ws = tmp_path / "fake-k2bi"
        lib = ws / "scripts" / "lib"
        lib.mkdir(parents=True)
        (ws / "scripts" / "__init__.py").write_text("", encoding="utf-8")
        (lib / "__init__.py").write_text("", encoding="utf-8")
        (lib / "invest_thesis.py").write_text(
            "from dataclasses import dataclass\n"
            "from pathlib import Path\n"
            "@dataclass\n"
            "class ThesisInput:\n"
            "    symbol: str\n"
            "@dataclass\n"
            "class ThesisResult:\n"
            "    path: Path\n"
            "    written: bool\n",
            encoding="utf-8",
        )
        (lib / "invest_bear_case.py").write_text(
            "from dataclasses import dataclass\n"
            "from pathlib import Path\n"
            "@dataclass\n"
            "class BearCaseInput:\n"
            "    bear_conviction: int\n"
            "@dataclass\n"
            "class BearCaseResult:\n"
            "    path: Path\n"
            "    written: bool\n"
            "    bear_verdict: str\n"
            "    bear_conviction: int\n",
            encoding="utf-8",
        )
        (lib / "invest_orchestrator_adapters.py").write_text(
            "from dataclasses import dataclass\n"
            "class OrchestratorGateError(ValueError): pass\n"
            "@dataclass(frozen=True)\n"
            "class ThesisClaimDecision:\n"
            "    claim_id: str\n"
            "    claim_text: str\n"
            "    claim_load_bearing: bool\n"
            "    source_url: str | None\n"
            "    source_excerpt: str\n"
            "    curated_framing: str\n"
            "    operator_mark: str\n"
            "    operator_note: str | None\n"
            "    source_vendor: str\n"
            "    spot_check_vendor: str | None = None\n"
            "def verify_and_generate_thesis(thesis_input, vault_root, *, claim_decisions, **kwargs):\n"
            "    claim = claim_decisions[0]\n"
            "    assert claim.source_excerpt == ''\n"
            "    assert claim.operator_mark == 'refused'\n"
            "    assert 'operator_override_reason' in kwargs\n"
            "    raise OrchestratorGateError('adapter refused load-bearing claim')\n"
            "def run_bear_case(*args, **kwargs):\n"
            "    raise AssertionError('not used')\n",
            encoding="utf-8",
        )
        payload = {
            "symbol": "CDNS",
            "thesis_input": {"symbol": "CDNS"},
            "vault_root": str(tmp_path / "vault"),
            "claim_decisions": [
                {
                    "claim_id": "c1",
                    "claim_text": "Unsupported claim.",
                    "claim_load_bearing": True,
                    "source_url": None,
                    "source_excerpt": "",
                    "curated_framing": "",
                    "operator_mark": "refused",
                    "operator_note": "source does not support this load bearing claim",
                    "source_vendor": "",
                    "spot_check_vendor": None,
                }
            ],
            "operator_override_reason": None,
            "calx_override_acknowledged": False,
            "vendor_warning_acknowledged": False,
            "vendor_provenance": None,
            "refresh": False,
        }

        result = subprocess.run(
            [
                sys.executable,
                str(REPO_ROOT / "scripts" / "lib" / "orchestrator_k2bi_adapter.py"),
                "verify-and-generate-thesis",
                "--workspace",
                str(ws),
                "--payload-json",
                json.dumps(payload),
            ],
            cwd=str(ws),
            env={**os.environ, "PYTHONPATH": str(ws)},
            capture_output=True,
            text=True,
            timeout=15,
        )

        assert result.returncode == 2
        assert "adapter refused load-bearing claim" in result.stderr

    def test_adapter_rejects_payload_symbol_mismatch(self, tmp_path):
        ws = tmp_path / "fake-k2bi"
        lib = ws / "scripts" / "lib"
        lib.mkdir(parents=True)
        (ws / "scripts" / "__init__.py").write_text("", encoding="utf-8")
        (lib / "__init__.py").write_text("", encoding="utf-8")
        (lib / "invest_thesis.py").write_text(
            "from dataclasses import dataclass\n"
            "@dataclass\n"
            "class ThesisInput:\n"
            "    symbol: str\n",
            encoding="utf-8",
        )
        (lib / "invest_bear_case.py").write_text("", encoding="utf-8")
        (lib / "invest_orchestrator_adapters.py").write_text(
            "from dataclasses import dataclass\n"
            "@dataclass(frozen=True)\n"
            "class ThesisClaimDecision:\n"
            "    claim_id: str\n"
            "def verify_and_generate_thesis(*args, **kwargs):\n"
            "    raise AssertionError('should fail before adapter call')\n",
            encoding="utf-8",
        )
        vault = tmp_path / "vault"
        vault.mkdir(exist_ok=True)
        payload = {
            "symbol": "AAPL",
            "thesis_input": {"symbol": "CDNS"},
            "vault_root": str(vault),
            "claim_decisions": [{"claim_id": "c1"}],
        }

        result = subprocess.run(
            [
                sys.executable,
                str(REPO_ROOT / "scripts" / "lib" / "orchestrator_k2bi_adapter.py"),
                "verify-and-generate-thesis",
                "--workspace",
                str(ws),
                "--payload-json",
                json.dumps(payload),
            ],
            env={**os.environ, "K2BI_VAULT_PATH": str(vault)},
            capture_output=True,
            text=True,
            timeout=15,
        )

        assert result.returncode == 2
        assert "does not match thesis_input.symbol" in result.stderr

    def test_payload_path_outside_allowed_dir_is_rejected(self, tmp_path):
        workspace = tmp_path / "workspace"
        (workspace / "scripts" / "lib").mkdir(parents=True)
        outside = tmp_path / "outside.json"
        outside.write_text("{}", encoding="utf-8")
        allowed = tmp_path / "allowed"
        allowed.mkdir()

        result = subprocess.run(
            [
                sys.executable,
                str(REPO_ROOT / "scripts" / "lib" / "orchestrator_k2bi_adapter.py"),
                "verify-and-generate-thesis",
                "--workspace",
                str(workspace),
                "--payload-path",
                str(outside),
            ],
            env={**os.environ, "K2B_ORCH_ADAPTER_PAYLOAD_DIR": str(allowed)},
            capture_output=True,
            text=True,
            timeout=15,
        )

        assert result.returncode == 2
        assert "outside allowed payload directory" in result.stderr

    def test_payload_path_missing_is_retryable_and_symlink_is_rejected(self, tmp_path):
        workspace = tmp_path / "workspace"
        (workspace / "scripts" / "lib").mkdir(parents=True)
        allowed = tmp_path / "allowed"
        allowed.mkdir()
        outside = tmp_path / "outside.json"
        outside.write_text("{}", encoding="utf-8")
        symlink = allowed / "payload.json"
        symlink.symlink_to(outside)

        missing = subprocess.run(
            [
                sys.executable,
                str(REPO_ROOT / "scripts" / "lib" / "orchestrator_k2bi_adapter.py"),
                "verify-and-generate-thesis",
                "--workspace",
                str(workspace),
                "--payload-path",
                str(allowed / "missing.json"),
            ],
            env={**os.environ, "K2B_ORCH_ADAPTER_PAYLOAD_DIR": str(allowed)},
            capture_output=True,
            text=True,
            timeout=15,
        )

        assert missing.returncode == 3
        assert "not yet available" in missing.stderr
        assert json.loads(missing.stdout)["retryable"] is True

        symlink_result = subprocess.run(
            [
                sys.executable,
                str(REPO_ROOT / "scripts" / "lib" / "orchestrator_k2bi_adapter.py"),
                "verify-and-generate-thesis",
                "--workspace",
                str(workspace),
                "--payload-path",
                str(symlink),
            ],
            env={**os.environ, "K2B_ORCH_ADAPTER_PAYLOAD_DIR": str(allowed)},
            capture_output=True,
            text=True,
            timeout=15,
        )

        assert symlink_result.returncode == 2
        assert "outside allowed payload directory" in symlink_result.stderr

    def test_payload_path_malformed_json_is_retryable(self, tmp_path):
        workspace = tmp_path / "workspace"
        (workspace / "scripts" / "lib").mkdir(parents=True)
        allowed = tmp_path / "allowed"
        allowed.mkdir()
        payload_path = allowed / "payload.json"
        payload_path.write_text("{", encoding="utf-8")

        result = subprocess.run(
            [
                sys.executable,
                str(REPO_ROOT / "scripts" / "lib" / "orchestrator_k2bi_adapter.py"),
                "verify-and-generate-thesis",
                "--workspace",
                str(workspace),
                "--payload-path",
                str(payload_path),
            ],
            env={**os.environ, "K2B_ORCH_ADAPTER_PAYLOAD_DIR": str(allowed)},
            capture_output=True,
            text=True,
            timeout=15,
        )

        assert result.returncode == 3
        error = json.loads(result.stdout)
        assert error["category"] == "transient"
        assert error["retryable"] is True

    def test_claim_decisions_limit_fails_before_adapter_call(self, tmp_path):
        ws = tmp_path / "fake-k2bi"
        lib = ws / "scripts" / "lib"
        lib.mkdir(parents=True)
        (ws / "scripts" / "__init__.py").write_text("", encoding="utf-8")
        (lib / "__init__.py").write_text("", encoding="utf-8")
        (lib / "invest_thesis.py").write_text(
            "from dataclasses import dataclass\n"
            "@dataclass\n"
            "class ThesisInput:\n"
            "    symbol: str\n",
            encoding="utf-8",
        )
        (lib / "invest_bear_case.py").write_text("", encoding="utf-8")
        (lib / "invest_orchestrator_adapters.py").write_text(
            "from dataclasses import dataclass\n"
            "class OrchestratorGateError(ValueError): pass\n"
            "@dataclass(frozen=True)\n"
            "class ThesisClaimDecision:\n"
            "    claim_id: str\n"
            "def verify_and_generate_thesis(*args, **kwargs):\n"
            "    raise AssertionError('should fail before adapter call')\n",
            encoding="utf-8",
        )
        payload = {
            "symbol": "CDNS",
            "thesis_input": {"symbol": "CDNS"},
            "vault_root": str(tmp_path / "vault"),
            "claim_decisions": [{"claim_id": str(i)} for i in range(501)],
        }

        result = subprocess.run(
            [
                sys.executable,
                str(REPO_ROOT / "scripts" / "lib" / "orchestrator_k2bi_adapter.py"),
                "verify-and-generate-thesis",
                "--workspace",
                str(ws),
                "--payload-json",
                json.dumps(payload),
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )

        assert result.returncode == 2
        assert "claim_decisions exceeds maximum" in result.stderr

    def test_claim_decision_items_must_be_objects(self, tmp_path):
        ws = tmp_path / "fake-k2bi"
        lib = ws / "scripts" / "lib"
        lib.mkdir(parents=True)
        (ws / "scripts" / "__init__.py").write_text("", encoding="utf-8")
        (lib / "__init__.py").write_text("", encoding="utf-8")
        (lib / "invest_thesis.py").write_text(
            "from dataclasses import dataclass\n"
            "@dataclass\n"
            "class ThesisInput:\n"
            "    symbol: str\n",
            encoding="utf-8",
        )
        (lib / "invest_bear_case.py").write_text("", encoding="utf-8")
        (lib / "invest_orchestrator_adapters.py").write_text(
            "from dataclasses import dataclass\n"
            "class OrchestratorGateError(ValueError): pass\n"
            "@dataclass(frozen=True)\n"
            "class ThesisClaimDecision:\n"
            "    claim_id: str\n"
            "def verify_and_generate_thesis(*args, **kwargs):\n"
            "    raise AssertionError('should fail before adapter call')\n",
            encoding="utf-8",
        )
        vault = tmp_path / "vault"
        vault.mkdir(exist_ok=True)
        payload = {
            "symbol": "CDNS",
            "thesis_input": {"symbol": "CDNS"},
            "vault_root": str(vault),
            "claim_decisions": ["bad-shape"],
        }

        result = subprocess.run(
            [
                sys.executable,
                str(REPO_ROOT / "scripts" / "lib" / "orchestrator_k2bi_adapter.py"),
                "verify-and-generate-thesis",
                "--workspace",
                str(ws),
                "--payload-json",
                json.dumps(payload),
            ],
            env={**os.environ, "K2BI_VAULT_PATH": str(vault)},
            capture_output=True,
            text=True,
            timeout=15,
        )

        assert result.returncode == 2
        assert "claim_decisions items must be JSON objects" in result.stderr

    def test_union_type_mismatch_fails_closed(self, tmp_path):
        ws = tmp_path / "fake-k2bi"
        lib = ws / "scripts" / "lib"
        lib.mkdir(parents=True)
        (ws / "scripts" / "__init__.py").write_text("", encoding="utf-8")
        (lib / "__init__.py").write_text("", encoding="utf-8")
        (lib / "invest_thesis.py").write_text(
            "from dataclasses import dataclass\n"
            "@dataclass\n"
            "class ThesisInput:\n"
            "    symbol: str | int\n",
            encoding="utf-8",
        )
        (lib / "invest_bear_case.py").write_text("", encoding="utf-8")
        (lib / "invest_orchestrator_adapters.py").write_text(
            "from dataclasses import dataclass\n"
            "class OrchestratorGateError(ValueError): pass\n"
            "@dataclass(frozen=True)\n"
            "class ThesisClaimDecision:\n"
            "    claim_id: str\n"
            "def verify_and_generate_thesis(*args, **kwargs):\n"
            "    raise AssertionError('should fail before adapter call')\n",
            encoding="utf-8",
        )
        payload = {
            "symbol": "CDNS",
            "thesis_input": {"symbol": {"bad": "shape"}},
            "vault_root": str(tmp_path / "vault"),
            "claim_decisions": [{"claim_id": "c1"}],
        }

        result = subprocess.run(
            [
                sys.executable,
                str(REPO_ROOT / "scripts" / "lib" / "orchestrator_k2bi_adapter.py"),
                "verify-and-generate-thesis",
                "--workspace",
                str(ws),
                "--payload-json",
                json.dumps(payload),
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )

        assert result.returncode == 2
        assert "does not match any allowed type" in result.stderr

    def test_union_coercion_increments_depth(self, monkeypatch):
        from typing import Union
        from scripts.lib import orchestrator_k2bi_adapter as adapter

        monkeypatch.setenv("K2B_ORCH_ADAPTER_MAX_INPUT_DEPTH", "0")
        with pytest.raises(ValueError, match="adapter input exceeds maximum depth"):
            adapter._coerce(Union[dict, str], {"nested": "value"})
