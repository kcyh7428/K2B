#!/usr/bin/env python3
"""pytest unit tests for orchestrator narrative preflight and dispatch."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def temp_env(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    vault.mkdir()
    db = tmp_path / "orch.sqlite"
    monkeypatch.setenv("K2B_VAULT_PATH", str(vault))
    monkeypatch.setenv("K2BI_VAULT_PATH", str(vault))
    monkeypatch.setenv("K2B_ORCH_DB", str(db))
    monkeypatch.setenv("K2B_ORCH_SKIP_PROVIDER_PING", "1")
    ws = tmp_path / "k2bi"
    ws.mkdir()
    monkeypatch.setenv("K2B_ORCH_K2BI_WORKSPACE", str(ws))
    # These tests exercise the LEGACY dispatched k2bi-narrative preflight (P0-P5),
    # which is retired by default as of the agent-native Chat-2 change. Enable the
    # explicit legacy override so the legacy-path tests still exercise P0-P5.
    monkeypatch.setenv("K2B_ORCH_ALLOW_LEGACY_NARRATIVE", "1")
    # Remove any cached orchestrator modules
    to_remove = [k for k in sys.modules if "orchestrator" in k]
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


def _make_task(store, payload_dict, entity_key="ai-capex"):
    return store.add_task(
        assignee_profile="k2bi",
        command_key="k2bi-narrative",
        success_criteria="ok",
        permissions="analyst-command",
        entity_key=entity_key,
        payload=payload_dict,
    )


def _setup_workspace_for_p0(ws):
    """Create a fake Python package so P0 passes."""
    (ws / "scripts").mkdir()
    (ws / "scripts" / "__init__.py").write_text("")
    (ws / "scripts" / "lib").mkdir()
    (ws / "scripts" / "lib" / "__init__.py").write_text("")
    (ws / "scripts" / "lib" / "invest_narrative_pipeline.py").write_text("")


def _setup_vault_for_p1(vault):
    """Create macro-themes dir so P1 passes."""
    (vault / "wiki" / "macro-themes").mkdir(parents=True)


def _setup_registry_for_p2(vault, malformed=False, missing_aapl_name=False):
    """Create canonical registry so P2 passes (or tailored to fail)."""
    tickers_dir = vault / "wiki" / "tickers"
    tickers_dir.mkdir(parents=True)
    registry_path = tickers_dir / "canonical-registry.json"
    if malformed:
        registry_path.write_text("not json")
    elif missing_aapl_name:
        registry_path.write_text(json.dumps({"AAPL": {}}))
    else:
        registry_path.write_text(json.dumps({"AAPL": {"name": "Apple Inc."}}))


def _setup_env_for_p3(monkeypatch):
    """Set KIMI_API_KEY so P3 passes."""
    monkeypatch.setenv("KIMI_API_KEY", "fake-key")


class TestNarrativePreflightP0:
    def test_p0_module_not_importable(self, store, tmp_path):
        from scripts.lib import orchestrator_profiles as profiles

        ws = tmp_path / "k2bi"
        # No fake module -> P0 fails
        tid = _make_task(store, {"narrative": "AI capex is booming across all sectors globally now"})
        ok, reason = profiles.preflight_k2bi(store.get_task(tid))
        assert not ok
        assert "narrative pipeline module not importable" in reason


class TestNarrativePreflightP1:
    def test_p1_macro_themes_missing(self, store, tmp_path, monkeypatch):
        from scripts.lib import orchestrator_profiles as profiles

        ws = tmp_path / "k2bi"
        _setup_workspace_for_p0(ws)
        tid = _make_task(store, {"narrative": "AI capex is booming across all sectors globally now"})
        ok, reason = profiles.preflight_k2bi(store.get_task(tid))
        assert not ok
        assert "macro-themes output dir missing or not writable" in reason

    def test_p1_macro_themes_not_writable(self, store, tmp_path, monkeypatch):
        from scripts.lib import orchestrator_profiles as profiles

        ws = tmp_path / "k2bi"
        _setup_workspace_for_p0(ws)
        macro_themes = tmp_path / "vault" / "wiki" / "macro-themes"
        macro_themes.mkdir(parents=True)
        macro_themes.chmod(0o555)
        try:
            tid = _make_task(store, {"narrative": "AI capex is booming across all sectors globally now"})
            ok, reason = profiles.preflight_k2bi(store.get_task(tid))
            assert not ok
            assert "macro-themes output dir missing or not writable" in reason
        finally:
            macro_themes.chmod(0o755)


class TestNarrativePreflightP2:
    def test_p2_registry_missing(self, store, tmp_path, monkeypatch):
        from scripts.lib import orchestrator_profiles as profiles

        ws = tmp_path / "k2bi"
        _setup_workspace_for_p0(ws)
        _setup_vault_for_p1(tmp_path / "vault")
        tid = _make_task(store, {"narrative": "AI capex is booming across all sectors globally now"})
        ok, reason = profiles.preflight_k2bi(store.get_task(tid))
        assert not ok
        assert "canonical ticker registry missing/empty/malformed" in reason

    def test_p2_registry_malformed(self, store, tmp_path, monkeypatch):
        from scripts.lib import orchestrator_profiles as profiles

        ws = tmp_path / "k2bi"
        _setup_workspace_for_p0(ws)
        _setup_vault_for_p1(tmp_path / "vault")
        _setup_registry_for_p2(tmp_path / "vault", malformed=True)
        tid = _make_task(store, {"narrative": "AI capex is booming across all sectors globally now"})
        ok, reason = profiles.preflight_k2bi(store.get_task(tid))
        assert not ok
        assert "canonical ticker registry missing/empty/malformed" in reason

    def test_p2_registry_empty_dict(self, store, tmp_path, monkeypatch):
        from scripts.lib import orchestrator_profiles as profiles

        ws = tmp_path / "k2bi"
        _setup_workspace_for_p0(ws)
        _setup_vault_for_p1(tmp_path / "vault")
        registry_path = tmp_path / "vault" / "wiki" / "tickers" / "canonical-registry.json"
        registry_path.parent.mkdir(parents=True)
        registry_path.write_text("{}")
        tid = _make_task(store, {"narrative": "AI capex is booming across all sectors globally now"})
        ok, reason = profiles.preflight_k2bi(store.get_task(tid))
        assert not ok
        assert "canonical ticker registry missing/empty/malformed" in reason

    def test_p2_registry_missing_aapl_name(self, store, tmp_path, monkeypatch):
        from scripts.lib import orchestrator_profiles as profiles

        ws = tmp_path / "k2bi"
        _setup_workspace_for_p0(ws)
        _setup_vault_for_p1(tmp_path / "vault")
        _setup_registry_for_p2(tmp_path / "vault", missing_aapl_name=True)
        tid = _make_task(store, {"narrative": "AI capex is booming across all sectors globally now"})
        ok, reason = profiles.preflight_k2bi(store.get_task(tid))
        assert not ok
        assert "canonical ticker registry missing/empty/malformed" in reason


class TestNarrativePreflightP3:
    def test_p3_kimi_key_missing(self, store, tmp_path, monkeypatch):
        from scripts.lib import orchestrator_profiles as profiles

        ws = tmp_path / "k2bi"
        _setup_workspace_for_p0(ws)
        _setup_vault_for_p1(tmp_path / "vault")
        _setup_registry_for_p2(tmp_path / "vault")
        monkeypatch.delenv("KIMI_API_KEY", raising=False)
        monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
        # Neutralize the ~/.zshrc fallback so "missing" means missing everywhere.
        monkeypatch.setattr(profiles.Path, "home", lambda: tmp_path / "nohome")
        tid = _make_task(store, {"narrative": "AI capex is booming across all sectors globally now"})
        ok, reason = profiles.preflight_k2bi(store.get_task(tid))
        assert not ok
        assert "LLM API key not configured (KIMI_API_KEY)" in reason

    def test_p3_minimax_key_missing_when_provider_minimax(self, store, tmp_path, monkeypatch):
        from scripts.lib import orchestrator_profiles as profiles

        ws = tmp_path / "k2bi"
        _setup_workspace_for_p0(ws)
        _setup_vault_for_p1(tmp_path / "vault")
        _setup_registry_for_p2(tmp_path / "vault")
        monkeypatch.setenv("K2B_LLM_PROVIDER", "minimax")
        monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
        # Neutralize the ~/.zshrc fallback so "missing" means missing everywhere.
        monkeypatch.setattr(profiles.Path, "home", lambda: tmp_path / "nohome")
        tid = _make_task(store, {"narrative": "AI capex is booming across all sectors globally now"})
        ok, reason = profiles.preflight_k2bi(store.get_task(tid))
        assert not ok
        assert "LLM API key not configured (MINIMAX_API_KEY)" in reason


class TestNarrativePreflightP4:
    def test_p4_narrative_empty(self, store, tmp_path, monkeypatch):
        from scripts.lib import orchestrator_profiles as profiles

        ws = tmp_path / "k2bi"
        _setup_workspace_for_p0(ws)
        _setup_vault_for_p1(tmp_path / "vault")
        _setup_registry_for_p2(tmp_path / "vault")
        _setup_env_for_p3(monkeypatch)
        tid = _make_task(store, {"narrative": ""})
        ok, reason = profiles.preflight_k2bi(store.get_task(tid))
        assert not ok
        assert "narrative seed empty" in reason

    def test_p4_narrative_too_short(self, store, tmp_path, monkeypatch):
        from scripts.lib import orchestrator_profiles as profiles

        ws = tmp_path / "k2bi"
        _setup_workspace_for_p0(ws)
        _setup_vault_for_p1(tmp_path / "vault")
        _setup_registry_for_p2(tmp_path / "vault")
        _setup_env_for_p3(monkeypatch)
        tid = _make_task(store, {"narrative": "short"})
        ok, reason = profiles.preflight_k2bi(store.get_task(tid))
        assert not ok
        assert "narrative seed empty" in reason

    def test_p4_narrative_too_long(self, store, tmp_path, monkeypatch):
        from scripts.lib import orchestrator_profiles as profiles

        ws = tmp_path / "k2bi"
        _setup_workspace_for_p0(ws)
        _setup_vault_for_p1(tmp_path / "vault")
        _setup_registry_for_p2(tmp_path / "vault")
        _setup_env_for_p3(monkeypatch)
        tid = _make_task(store, {"narrative": "x" * 600})
        ok, reason = profiles.preflight_k2bi(store.get_task(tid))
        assert not ok
        assert "narrative seed too long -- distill to 1-3 sentences" in reason


class TestNarrativePreflightDirtyRepo:
    def test_dirty_repo_does_not_block_narrative(self, store, tmp_path, monkeypatch):
        from scripts.lib import orchestrator_profiles as profiles

        ws = tmp_path / "k2bi"
        _setup_workspace_for_p0(ws)
        _setup_vault_for_p1(tmp_path / "vault")
        _setup_registry_for_p2(tmp_path / "vault")
        _setup_env_for_p3(monkeypatch)

        # Make repo dirty
        subprocess.run(["git", "-C", str(ws), "init"], capture_output=True)
        (ws / "dirty.txt").write_text("dirty")

        tid = _make_task(store, {"narrative": "AI capex is booming across all sectors globally now"})
        ok, reason = profiles.preflight_k2bi(store.get_task(tid))
        assert ok, f"expected pass, got: {reason}"

    def test_dirty_repo_still_blocks_non_narrative(self, store, tmp_path, monkeypatch):
        from scripts.lib import orchestrator_profiles as profiles

        ws = tmp_path / "k2bi"
        # No fake module needed for smoke-enrich
        subprocess.run(["git", "-C", str(ws), "init"], capture_output=True)
        (ws / "dirty.txt").write_text("dirty")

        tid = store.add_task(
            assignee_profile="k2bi",
            command_key="k2bi-smoke-enrich-lrcx",
            success_criteria="ok",
            permissions="analyst-command",
        )
        ok, reason = profiles.preflight_k2bi(store.get_task(tid))
        assert not ok
        assert "dirty" in reason.lower()


class TestNarrativePreflightDirtyVault:
    def test_dirty_vault_does_not_block_narrative(self, store, tmp_path, monkeypatch):
        from scripts.lib import orchestrator_profiles as profiles

        ws = tmp_path / "k2bi"
        _setup_workspace_for_p0(ws)
        _setup_vault_for_p1(tmp_path / "vault")
        _setup_registry_for_p2(tmp_path / "vault")
        _setup_env_for_p3(monkeypatch)

        vault = tmp_path / "vault"
        # Create syncthing temp files
        (vault / ".syncthing.tmp").write_text("tmp")
        (vault / "note.sync-conflict-20260101-120000.txt").write_text("conflict")
        # Create a git repo in vault and make it dirty
        subprocess.run(["git", "-C", str(vault), "init"], capture_output=True)
        (vault / "uncommitted.md").write_text("x")

        tid = _make_task(store, {"narrative": "AI capex is booming across all sectors globally now"})
        ok, reason = profiles.preflight_k2bi(store.get_task(tid))
        assert ok, f"expected pass, got: {reason}"


class TestNarrativeOneFlightLock:
    def test_narrative_flight_lock_blocks_duplicate_entity(self, store, tmp_path, monkeypatch):
        from scripts.lib import orchestrator_store as store

        ws = tmp_path / "k2bi"
        _setup_workspace_for_p0(ws)
        _setup_vault_for_p1(tmp_path / "vault")
        _setup_registry_for_p2(tmp_path / "vault")
        _setup_env_for_p3(monkeypatch)

        store.add_task(
            assignee_profile="k2bi",
            command_key="k2bi-narrative",
            success_criteria="ok",
            permissions="analyst-command",
            entity_key="ai-capex",
            status="waiting_for_kimi_output",
            payload={"narrative": "AI capex is booming across all sectors globally now"},
        )
        with pytest.raises(store.FlightLockError) as exc_info:
            store.add_task(
                assignee_profile="k2bi",
                command_key="k2bi-narrative",
                success_criteria="ok",
                permissions="analyst-command",
                entity_key="AI-Capex",
                payload={"narrative": "AI capex is booming across all sectors globally now"},
            )
        assert "flight already active for 'AI-Capex'" in str(exc_info.value)


class TestProviderProbeTarget:
    """P5 must probe the host the child will actually call, not a hard-coded one."""

    def test_default_kimi_host(self, monkeypatch):
        from scripts.lib import orchestrator_profiles as profiles
        monkeypatch.delenv("K2B_LLM_PROVIDER", raising=False)
        monkeypatch.delenv("KIMI_API_HOST", raising=False)
        host, port = profiles._provider_probe_target()
        assert host == "api.kimi.com"
        assert port == 443

    def test_minimax_host(self, monkeypatch):
        from scripts.lib import orchestrator_profiles as profiles
        monkeypatch.setenv("K2B_LLM_PROVIDER", "minimax")
        monkeypatch.delenv("MINIMAX_API_HOST", raising=False)
        host, port = profiles._provider_probe_target()
        assert host == "api.minimaxi.com"
        assert port == 443

    def test_custom_kimi_host_override(self, monkeypatch):
        from scripts.lib import orchestrator_profiles as profiles
        monkeypatch.setenv("K2B_LLM_PROVIDER", "kimi")
        monkeypatch.setenv("KIMI_API_HOST", "https://proxy.internal:8443/coding")
        host, port = profiles._provider_probe_target()
        assert host == "proxy.internal"
        assert port == 8443

    def test_never_probes_moonshot(self, monkeypatch):
        """Regression: the old hard-coded api.moonshot.cn host must be gone."""
        from scripts.lib import orchestrator_profiles as profiles
        monkeypatch.delenv("K2B_LLM_PROVIDER", raising=False)
        monkeypatch.delenv("KIMI_API_HOST", raising=False)
        host, _ = profiles._provider_probe_target()
        assert host != "api.moonshot.cn"


class TestProviderKeyAvailable:
    """P3 must resolve the key the same way the K2Bi provider does: env OR a
    quoted ~/.zshrc export. The live MVP test (flight 2026-05-31-001) blocked
    because the key was in ~/.zshrc but not exported to the dispatch shell."""

    def test_key_in_env(self, tmp_path, monkeypatch):
        from scripts.lib import orchestrator_profiles as profiles
        monkeypatch.setenv("KIMI_API_KEY", "env-key")
        monkeypatch.setattr(profiles.Path, "home", lambda: tmp_path / "nohome")
        assert profiles._provider_key_available("kimi") is True

    def test_key_absent_everywhere(self, tmp_path, monkeypatch):
        from scripts.lib import orchestrator_profiles as profiles
        monkeypatch.delenv("KIMI_API_KEY", raising=False)
        monkeypatch.setattr(profiles.Path, "home", lambda: tmp_path / "nohome")
        assert profiles._provider_key_available("kimi") is False

    def test_key_in_zshrc_not_env_resolves(self, tmp_path, monkeypatch):
        """THE regression: key only in ~/.zshrc (quoted export), not env -> available."""
        from scripts.lib import orchestrator_profiles as profiles
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        (fake_home / ".zshrc").write_text('export KIMI_API_KEY="from-zshrc-12345"\n')
        monkeypatch.delenv("KIMI_API_KEY", raising=False)
        monkeypatch.setattr(profiles.Path, "home", lambda: fake_home)
        assert profiles._provider_key_available("kimi") is True

    def test_minimax_key_in_zshrc_not_env_resolves(self, tmp_path, monkeypatch):
        from scripts.lib import orchestrator_profiles as profiles
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        (fake_home / ".zshrc").write_text('export MINIMAX_API_KEY="mm-zshrc-key"\n')
        monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
        monkeypatch.setattr(profiles.Path, "home", lambda: fake_home)
        assert profiles._provider_key_available("minimax") is True
