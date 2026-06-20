#!/usr/bin/env python3
"""pytest coverage for orchestrator A5 deploy-to-engine gate."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

TARGET_SHA = "a" * 40
REMOTE_BASELINE_SHA = "b" * 40


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
    monkeypatch.delenv("K2B_ORCH_ALLOW_DEPLOY_TO_VPS", raising=False)
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


def _init_k2bi_repo(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "K2B Test")
    (repo / "scripts").mkdir(parents=True, exist_ok=True)
    deploy = repo / "scripts" / "deploy-to-vps.sh"
    deploy.write_text("#!/usr/bin/env bash\nset -euo pipefail\n", encoding="utf-8")
    deploy.chmod(0o755)
    (repo / "README.md").write_text("# repo\n", encoding="utf-8")
    _git(repo, "add", "README.md", "scripts/deploy-to-vps.sh")
    _git(repo, "commit", "-m", "initial")


def _manifest(path: Path, *, target_sha=TARGET_SHA, remote_sha=REMOTE_BASELINE_SHA) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "target_sha": target_sha,
                "remote_baseline_sha": remote_sha,
                "categories": ["execution", "scripts"],
                "restart_services": ["k2bi-engine.service"],
                "live_effect": "sync approved K2Bi engine code and restart k2bi-engine.service",
            },
            sort_keys=True,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return path


def _a5_parent(
    store,
    *,
    entity="CDNS",
    target_sha=TARGET_SHA,
    source_parent="2026-06-19-001",
    source_status="terminal_shipped",
    payload_updates=None,
):
    payload = {
        "chain_kind": "deploy",
        "deploy_source_parent": source_parent,
        "deploy_source_status": source_status,
        "deploy_target_sha": target_sha,
    }
    if payload_updates:
        payload.update(payload_updates)
    return store.add_task(
        assignee_profile="k2b",
        command_key="k2b-a5-deploy",
        success_criteria="A5 deploy chain parked",
        permissions="agent-native",
        entity_key=entity,
        status="needs_human",
        payload=payload,
    )


def _force_payload(store, tid: str, updates: dict) -> None:
    with store._acquire_lock():
        conn = store.connect()
        store.init_db(conn)
        row = conn.execute("SELECT * FROM tasks WHERE id=?", (tid,)).fetchone()
        payload = json.loads(dict(row)["payload"])
        payload.update(updates)
        store._update_payload_locked(conn, tid, payload, status=dict(row)["status"])
        conn.commit()
        conn.close()


def _preflight_task(payload: dict, *, entity="CDNS") -> dict:
    return {
        "assignee_profile": "k2bi",
        "command_key": "k2bi-deploy-to-vps",
        "entity_key": entity,
        "payload": json.dumps(payload),
    }


class TestA5DeployGate:
    def test_a5_status_is_terminal_deployed_and_releases_entity_lock(self, store):
        parent = _a5_parent(store)
        store.transition(parent, "terminal_deployed")

        second = store.add_task(
            assignee_profile="k2b",
            command_key="k2b-a1-chain",
            success_criteria="new flight can start after deploy terminalizes",
            permissions="agent-native",
            entity_key="CDNS",
            status="needs_human",
            payload={},
        )
        assert second != parent
        assert store.a1_resume_action(parent) == "terminal_deployed"
        with pytest.raises(ValueError, match="terminal status is irreversible"):
            store.transition(parent, "done")

    def test_a5_resume_ladder_from_preview_to_terminal_deployed(self, store, tmp_path, monkeypatch):
        manifest = _manifest(tmp_path / "preview.json")
        parent = _a5_parent(store)
        assert store.a1_resume_action(parent) == "preview_deploy"

        ok, reason = store.a5_record_deploy_preview(parent, str(manifest))
        assert ok, reason
        assert store.a1_resume_action(parent) == "await_deploy_approval"

        ok, reason = store.a5_authorize_deploy(parent)
        assert ok, reason
        assert store.a1_resume_action(parent) == "dispatch_deploy"

        ok, dispatch = store.a5_mark_deploy_dispatch_started(parent)
        assert ok, dispatch
        assert store.a1_resume_action(parent) == "verify_deploy"

        monkeypatch.setattr(
            store,
            "_a5_inspect_deploy_state_from_payload",
            lambda _task_id, payload: _clean_deploy_inspect(payload),
        )
        ok, reason = store.a5_verify_deploy(parent)
        assert ok, reason
        task = store.get_task(parent)
        assert task["status"] == "terminal_deployed"
        assert store.a1_resume_action(parent) == "terminal_deployed"

    def test_a5_defer_writes_pending_deploy_marker_without_dispatch(self, store, tmp_path):
        manifest = _manifest(tmp_path / "preview.json")
        parent = _a5_parent(store)
        ok, reason = store.a5_record_deploy_preview(parent, str(manifest))
        assert ok, reason

        ok, marker_path = store.a5_defer_deploy(parent, reason="operator_deferred")
        assert ok, marker_path
        marker = Path(marker_path)
        assert marker.exists()
        data = json.loads(marker.read_text(encoding="utf-8"))
        assert data["type"] == "k2bi-pending-deploy"
        assert data["source_parent"] == "2026-06-19-001"
        assert data["source_status"] == "terminal_shipped"
        assert data["target_sha"] == TARGET_SHA
        assert data["remote_baseline_sha"] == REMOTE_BASELINE_SHA
        assert data["preview_manifest_sha256"] == _sha256(manifest)
        assert data["reason"] == "operator_deferred"
        assert store.a1_resume_action(parent) == "deploy_deferred"

    def test_a5_authorize_after_defer_requires_fresh_preview(self, store, tmp_path):
        repo = tmp_path / "k2bi"
        _init_k2bi_repo(repo)
        head = _git(repo, "rev-parse", "HEAD").stdout.strip()
        manifest = _manifest(tmp_path / "preview.json", target_sha=head)
        parent = _a5_parent(store, target_sha=head)
        ok, reason = store.a5_record_deploy_preview(parent, str(manifest))
        assert ok, reason
        _force_payload(store, parent, {"deploy_verification_scope": "category_scoped"})
        ok, marker_path = store.a5_defer_deploy(parent, reason="operator_deferred")
        assert ok, marker_path
        deferred_payload = json.loads(store.get_task(parent)["payload"])
        assert "deploy_verification_scope" not in deferred_payload

        ok, reason = store.a5_authorize_deploy(parent)
        assert not ok
        assert "deploy_deferred" in reason

        ok, reason = store.a5_resume_deferred_deploy(parent)
        assert ok, reason
        assert store.a1_resume_action(parent) == "preview_deploy"
        resumed_payload = json.loads(store.get_task(parent)["payload"])
        assert "deploy_verification_scope" not in resumed_payload

        ok, reason = store.a5_record_deploy_preview(parent, str(manifest))
        assert ok, reason
        ok, reason = store.a5_authorize_deploy(parent)
        assert ok, reason

    def test_a5_resume_deferred_refuses_advanced_k2bi_head(self, store, tmp_path):
        repo = tmp_path / "k2bi"
        _init_k2bi_repo(repo)
        first_head = _git(repo, "rev-parse", "HEAD").stdout.strip()
        manifest = _manifest(tmp_path / "preview.json", target_sha=first_head)
        parent = _a5_parent(store, target_sha=first_head)
        ok, reason = store.a5_record_deploy_preview(parent, str(manifest))
        assert ok, reason
        ok, marker_path = store.a5_defer_deploy(parent, reason="operator_deferred")
        assert ok, marker_path

        (repo / "README.md").write_text("# repo\n\nadvanced\n", encoding="utf-8")
        _git(repo, "add", "README.md")
        _git(repo, "commit", "-m", "advance head")

        ok, reason = store.a5_resume_deferred_deploy(parent)
        assert not ok
        assert "advanced while deferred" in reason

    def test_a5_authorize_deploy_token_binds_target_sha_remote_baseline_and_manifest(
        self, store, tmp_path
    ):
        manifest = _manifest(tmp_path / "preview.json")
        parent = _a5_parent(store)
        ok, reason = store.a5_record_deploy_preview(parent, str(manifest))
        assert ok, reason

        ok, reason = store.a5_authorize_deploy(parent)
        assert ok, reason
        payload = json.loads(store.get_task(parent)["payload"])
        expected = (
            f"APPROVE_DEPLOY:{TARGET_SHA}:{REMOTE_BASELINE_SHA}:{_sha256(manifest)}:"
            f"{payload['deploy_approved_at']}:{payload['deploy_lease_id']}"
        )
        assert payload["deploy_approval_token"] == expected
        assert payload["deploy_authorized"] is True

    def test_a5_record_preview_refuses_overwrite_after_authorize(self, store, tmp_path):
        manifest = _manifest(tmp_path / "preview.json")
        parent = _a5_parent(store)
        ok, reason = store.a5_record_deploy_preview(parent, str(manifest))
        assert ok, reason
        ok, reason = store.a5_authorize_deploy(parent)
        assert ok, reason

        ok, reason = store.a5_record_deploy_preview(parent, str(manifest))
        assert not ok
        assert "already authorized" in reason

    def test_a5_preflight_refuses_stale_manifest_hash(self, tmp_path, monkeypatch):
        from scripts.lib import orchestrator_profiles as profiles

        repo = tmp_path / "k2bi"
        _init_k2bi_repo(repo)
        monkeypatch.setenv("K2B_ORCH_K2BI_WORKSPACE", str(repo))
        monkeypatch.setenv("K2B_ORCH_ALLOW_DEPLOY_TO_VPS", "1")
        baseline = tmp_path / "remote-baseline.txt"
        baseline.write_text(REMOTE_BASELINE_SHA + "\n", encoding="utf-8")
        manifest = _manifest(tmp_path / "preview.json")
        payload = _deploy_payload(manifest, manifest_sha="0" * 64, baseline_path=baseline)

        ok, reason = profiles.preflight_k2bi(_preflight_task(payload))
        assert not ok
        assert "manifest" in reason.lower()

    def test_a5_preflight_refuses_changed_remote_baseline(self, tmp_path, monkeypatch):
        from scripts.lib import orchestrator_profiles as profiles

        repo = tmp_path / "k2bi"
        _init_k2bi_repo(repo)
        monkeypatch.setenv("K2B_ORCH_K2BI_WORKSPACE", str(repo))
        monkeypatch.setenv("K2B_ORCH_ALLOW_DEPLOY_TO_VPS", "1")
        baseline = tmp_path / "remote-baseline.txt"
        baseline.write_text("c" * 40 + "\n", encoding="utf-8")
        manifest = _manifest(tmp_path / "preview.json")
        payload = _deploy_payload(manifest, baseline_path=baseline)

        ok, reason = profiles.preflight_k2bi(_preflight_task(payload))
        assert not ok
        assert "remote baseline" in reason.lower()

    def test_a5_preflight_refuses_manifest_realpath_drift(self, tmp_path, monkeypatch):
        from scripts.lib import orchestrator_profiles as profiles

        repo = tmp_path / "k2bi"
        _init_k2bi_repo(repo)
        monkeypatch.setenv("K2B_ORCH_K2BI_WORKSPACE", str(repo))
        monkeypatch.setenv("K2B_ORCH_ALLOW_DEPLOY_TO_VPS", "1")
        baseline = tmp_path / "remote-baseline.txt"
        baseline.write_text(REMOTE_BASELINE_SHA + "\n", encoding="utf-8")
        manifest = _manifest(tmp_path / "preview.json")
        payload = _deploy_payload(
            manifest,
            baseline_path=baseline,
            manifest_realpath=str(tmp_path / "other-preview.json"),
        )

        ok, reason = profiles.preflight_k2bi(_preflight_task(payload))
        assert not ok
        assert "realpath" in reason.lower()

    def test_a5_preflight_refuses_symlink_remote_baseline(self, tmp_path, monkeypatch):
        from scripts.lib import orchestrator_profiles as profiles

        repo = tmp_path / "k2bi"
        _init_k2bi_repo(repo)
        monkeypatch.setenv("K2B_ORCH_K2BI_WORKSPACE", str(repo))
        monkeypatch.setenv("K2B_ORCH_ALLOW_DEPLOY_TO_VPS", "1")
        baseline = tmp_path / "remote-baseline.txt"
        baseline.write_text(REMOTE_BASELINE_SHA + "\n", encoding="utf-8")
        symlink_baseline = tmp_path / "remote-baseline-link.txt"
        symlink_baseline.symlink_to(baseline)
        manifest = _manifest(tmp_path / "preview.json")
        payload = _deploy_payload(manifest, baseline_path=symlink_baseline)

        ok, reason = profiles.preflight_k2bi(_preflight_task(payload))
        assert not ok
        assert "symlink" in reason.lower()

    def test_a5_preflight_refuses_local_head_not_target_sha(self, tmp_path, monkeypatch):
        from scripts.lib import orchestrator_profiles as profiles

        repo = tmp_path / "k2bi"
        _init_k2bi_repo(repo)
        monkeypatch.setenv("K2B_ORCH_K2BI_WORKSPACE", str(repo))
        monkeypatch.setenv("K2B_ORCH_ALLOW_DEPLOY_TO_VPS", "1")
        baseline = tmp_path / "remote-baseline.txt"
        baseline.write_text(REMOTE_BASELINE_SHA + "\n", encoding="utf-8")
        manifest = _manifest(tmp_path / "preview.json")
        payload = _deploy_payload(manifest, baseline_path=baseline)

        ok, reason = profiles.preflight_k2bi(_preflight_task(payload))
        assert not ok
        assert "target sha" in reason.lower()

    def test_a5_preflight_refuses_existing_worker_lock(self, tmp_path, monkeypatch):
        from scripts.lib import orchestrator_profiles as profiles

        repo = tmp_path / "k2bi"
        _init_k2bi_repo(repo)
        head = _git(repo, "rev-parse", "HEAD").stdout.strip()
        monkeypatch.setenv("K2B_ORCH_K2BI_WORKSPACE", str(repo))
        monkeypatch.setenv("K2B_ORCH_ALLOW_DEPLOY_TO_VPS", "1")
        baseline = tmp_path / "remote-baseline.txt"
        baseline.write_text(REMOTE_BASELINE_SHA + "\n", encoding="utf-8")
        manifest = _manifest(tmp_path / "preview.json", target_sha=head)
        payload = _deploy_payload(manifest, target_sha=head, baseline_path=baseline)
        lock_path = tmp_path / "worker.lock"
        lock_path.write_text(str(os.getpid()), encoding="utf-8")
        profile = dict(profiles.get_profile("k2bi"))
        profile["worker_lock"] = str(lock_path)
        monkeypatch.setattr(
            profiles,
            "get_profile",
            lambda name: profile if name == "k2bi" else None,
        )

        ok, reason = profiles.preflight_k2bi(_preflight_task(payload))
        assert not ok
        assert "worker lock" in reason.lower()

    def test_a5_preflight_refuses_missing_permission_scope(self, tmp_path, monkeypatch):
        from scripts.lib import orchestrator_profiles as profiles

        repo = tmp_path / "k2bi"
        _init_k2bi_repo(repo)
        monkeypatch.setenv("K2B_ORCH_K2BI_WORKSPACE", str(repo))
        baseline = tmp_path / "remote-baseline.txt"
        baseline.write_text(REMOTE_BASELINE_SHA + "\n", encoding="utf-8")
        manifest = _manifest(tmp_path / "preview.json")
        payload = _deploy_payload(manifest, baseline_path=baseline)

        ok, reason = profiles.preflight_k2bi(_preflight_task(payload))
        assert not ok
        assert "permission" in reason.lower()

    def test_a5_resolve_command_requires_permission_scope(self, tmp_path, monkeypatch):
        from scripts.lib import orchestrator_profiles as profiles

        repo = tmp_path / "k2bi"
        _init_k2bi_repo(repo)
        head = _git(repo, "rev-parse", "HEAD").stdout.strip()
        monkeypatch.setenv("K2B_ORCH_K2BI_WORKSPACE", str(repo))
        baseline = tmp_path / "remote-baseline.txt"
        baseline.write_text(REMOTE_BASELINE_SHA + "\n", encoding="utf-8")
        manifest = _manifest(tmp_path / "preview.json", target_sha=head)
        payload = _deploy_payload(manifest, target_sha=head, baseline_path=baseline)

        assert profiles.resolve_command("k2bi", "k2bi-deploy-to-vps", payload) is None
        monkeypatch.setenv("K2B_ORCH_ALLOW_DEPLOY_TO_VPS", "1")
        assert profiles.resolve_command("k2bi", "k2bi-deploy-to-vps", payload) == [
            "bash",
            str(Path(os.environ["K2B_ORCH_K2BI_WORKSPACE"]) / "scripts" / "deploy-to-vps.sh"),
            "auto",
        ]

    def test_a5_public_deploy_payload_validator_wraps_permission_shape(
        self, tmp_path, monkeypatch
    ):
        from scripts.lib import orchestrator_profiles as profiles

        baseline = tmp_path / "remote-baseline.txt"
        baseline.write_text(REMOTE_BASELINE_SHA + "\n", encoding="utf-8")
        manifest = _manifest(tmp_path / "preview.json")
        payload = _deploy_payload(manifest, baseline_path=baseline)

        ok, reason = profiles.validate_a5_deploy_payload(payload)
        assert not ok
        assert "permission" in reason.lower()
        monkeypatch.setenv("K2B_ORCH_ALLOW_DEPLOY_TO_VPS", "1")
        ok, reason = profiles.validate_a5_deploy_payload(payload)
        assert ok, reason

    def test_a5_current_remote_baseline_requires_no_follow_open(
        self, tmp_path, monkeypatch
    ):
        from scripts.lib import orchestrator_profiles as profiles

        baseline = tmp_path / "remote-baseline.txt"
        baseline.write_text(REMOTE_BASELINE_SHA + "\n", encoding="utf-8")
        manifest = _manifest(tmp_path / "preview.json")
        payload = _deploy_payload(manifest, baseline_path=baseline)
        monkeypatch.delattr(profiles.os, "O_NOFOLLOW", raising=False)

        current, reason = profiles._a5_current_remote_baseline(payload)
        assert current is None
        assert "O_NOFOLLOW" in reason

    def test_a5_manifest_loader_requires_no_follow_open(self, tmp_path, monkeypatch):
        from scripts.lib import orchestrator_profiles as profiles

        manifest = _manifest(tmp_path / "preview.json")
        monkeypatch.delattr(profiles.os, "O_NOFOLLOW", raising=False)

        data, reason = profiles._a5_load_deploy_manifest(manifest)
        assert data is None
        assert "O_NOFOLLOW" in reason

    def test_a5_deploy_uses_capital_timeout_env(self, store, tmp_path, monkeypatch):
        from scripts.lib import orchestrator_worker as worker

        baseline = tmp_path / "remote-baseline.txt"
        baseline.write_text(REMOTE_BASELINE_SHA + "\n", encoding="utf-8")
        manifest = _manifest(tmp_path / "preview.json")
        tid = store.add_task(
            assignee_profile="k2bi",
            command_key="k2bi-deploy-to-vps",
            success_criteria="deploy to engine",
            permissions="analyst-command",
            payload=_deploy_payload(manifest, baseline_path=baseline),
        )
        store.mark_running(tid)
        monkeypatch.setenv("K2B_ORCH_ALLOW_DEPLOY_TO_VPS", "1")
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
        monkeypatch.setattr(
            worker.profiles,
            "validate_a5_deploy_script_ready",
            lambda: (True, ""),
        )
        monkeypatch.setattr(worker.subprocess, "run", fake_run)
        worker.main(tid)
        assert captured["timeout"] == 1200

    def test_a5_worker_rechecks_permission_scope_before_command(self, store, tmp_path, monkeypatch):
        from scripts.lib import orchestrator_worker as worker

        baseline = tmp_path / "remote-baseline.txt"
        baseline.write_text(REMOTE_BASELINE_SHA + "\n", encoding="utf-8")
        manifest = _manifest(tmp_path / "preview.json")
        tid = store.add_task(
            assignee_profile="k2bi",
            command_key="k2bi-deploy-to-vps",
            success_criteria="deploy to engine",
            permissions="analyst-command",
            payload=_deploy_payload(manifest, baseline_path=baseline),
        )
        store.mark_running(tid)
        monkeypatch.setattr(store, "notify", lambda *a, **k: None)
        monkeypatch.setattr(
            worker.profiles,
            "resolve_command",
            lambda *_a, **_k: [sys.executable, "-c", "print('should-not-run')"],
        )

        def fail_if_called(*_args, **_kwargs):
            raise AssertionError("deploy subprocess should not run without A5 permission scope")

        monkeypatch.setattr(worker.subprocess, "run", fail_if_called)
        assert worker.main(tid) == 1
        task = store.get_task(tid)
        assert task["status"] == "failed"
        assert "worker token recheck failed" in (task["blocker_reason"] or "")

    def test_a5_worker_rechecks_deploy_script_before_subprocess(
        self, store, tmp_path, monkeypatch
    ):
        from scripts.lib import orchestrator_worker as worker

        baseline = tmp_path / "remote-baseline.txt"
        baseline.write_text(REMOTE_BASELINE_SHA + "\n", encoding="utf-8")
        manifest = _manifest(tmp_path / "preview.json")
        tid = store.add_task(
            assignee_profile="k2bi",
            command_key="k2bi-deploy-to-vps",
            success_criteria="deploy to engine",
            permissions="analyst-command",
            payload=_deploy_payload(manifest, baseline_path=baseline),
        )
        store.mark_running(tid)
        monkeypatch.setenv("K2B_ORCH_ALLOW_DEPLOY_TO_VPS", "1")
        monkeypatch.setattr(store, "notify", lambda *a, **k: None)
        monkeypatch.setattr(
            worker.profiles,
            "resolve_command",
            lambda *_a, **_k: [sys.executable, "-c", "print('should-not-run')"],
        )
        monkeypatch.setattr(
            worker.profiles,
            "validate_a5_deploy_script_ready",
            lambda: (False, "deploy script drifted before worker run"),
        )

        def fail_if_called(*_args, **_kwargs):
            raise AssertionError("deploy subprocess should not run after script recheck failure")

        monkeypatch.setattr(worker.subprocess, "run", fail_if_called)
        assert worker.main(tid) == 1
        task = store.get_task(tid)
        assert task["status"] == "failed"
        assert "deploy script drifted" in (task["blocker_reason"] or "")

    def test_a5_verify_requires_independent_inspector_clean_state(self, store, tmp_path, monkeypatch):
        manifest = _manifest(tmp_path / "preview.json")
        parent = _authorized_dispatched_a5(store, manifest)
        monkeypatch.setattr(
            store,
            "_a5_inspect_deploy_state_from_payload",
            lambda _task_id, _payload: {
                "state": "worker_reported",
                "remote_head": None,
                "sync_state_sha": None,
                "service_active": None,
                "recovery_state_mismatch_count": None,
            },
        )

        ok, reason = store.a5_verify_deploy(parent)
        assert not ok
        assert "deploy verification refused" in reason
        assert store.get_task(parent)["status"] != "terminal_deployed"

    def test_a5_verify_refuses_untrusted_inspector_path(self, store, tmp_path):
        manifest = _manifest(tmp_path / "preview.json")
        parent = _authorized_dispatched_a5(store, manifest)
        payload = json.loads(store.get_task(parent)["payload"])
        result_dir = Path(store.K2B_VAULT) / "System" / "orchestrator" / "deploy-results"
        result_dir.mkdir(parents=True, exist_ok=True)
        result = tmp_path / "clean-result.json"
        result.write_text(json.dumps(_clean_deploy_inspect(payload)), encoding="utf-8")
        _force_payload(store, parent, {"deploy_verify_result_path": str(result)})

        ok, reason = store.a5_verify_deploy(parent)
        assert not ok
        assert "under" in reason
        assert store.get_task(parent)["status"] != "terminal_deployed"

    def test_a5_verify_refuses_stale_trusted_inspector_result(self, store, tmp_path):
        manifest = _manifest(tmp_path / "preview.json")
        parent = _authorized_dispatched_a5(store, manifest)
        payload = json.loads(store.get_task(parent)["payload"])
        result_dir = Path(store.K2B_VAULT) / "System" / "orchestrator" / "deploy-results"
        result_dir.mkdir(parents=True, exist_ok=True)
        result = result_dir / "clean-result.json"
        result.write_text(json.dumps(_clean_deploy_inspect(payload)), encoding="utf-8")
        os.utime(result, (0, 0))
        _force_payload(store, parent, {"deploy_verify_result_path": str(result)})

        ok, reason = store.a5_verify_deploy(parent)
        assert not ok
        assert "older than dispatch" in reason
        assert store.get_task(parent)["status"] != "terminal_deployed"

    def test_a5_verify_accepts_fresh_trusted_inspector_result(self, store, tmp_path):
        manifest = _manifest(tmp_path / "preview.json")
        parent = _authorized_dispatched_a5(store, manifest)
        payload = json.loads(store.get_task(parent)["payload"])
        result_dir = Path(store.K2B_VAULT) / "System" / "orchestrator" / "deploy-results"
        result_dir.mkdir(parents=True, exist_ok=True)
        result = result_dir / "clean-result.json"
        result.write_text(json.dumps(_clean_deploy_inspect(payload)), encoding="utf-8")
        _force_payload(store, parent, {"deploy_verify_result_path": str(result)})

        ok, reason = store.a5_verify_deploy(parent)
        assert ok, reason
        assert store.get_task(parent)["status"] == "terminal_deployed"

    def test_a5_verify_accepts_category_scoped_evidence_with_stale_remote_head(
        self, store, tmp_path
    ):
        manifest = _manifest(tmp_path / "preview.json")
        parent = _authorized_dispatched_a5(store, manifest)
        payload = json.loads(store.get_task(parent)["payload"])
        result_dir = Path(store.K2B_VAULT) / "System" / "orchestrator" / "deploy-results"
        result_dir.mkdir(parents=True, exist_ok=True)
        inspect = _clean_deploy_inspect(payload)
        inspect["remote_head"] = REMOTE_BASELINE_SHA
        inspect["verification_scope"] = "category_scoped"
        inspect["deployed_categories"] = ["scripts", "execution"]
        inspect["category_results"] = {
            "execution": {
                "matched_target": True,
                "path_count": 3,
                "missing_paths": [],
                "mismatched_paths": [],
                "extra_paths": [],
            },
            "scripts": {
                "matched_target": True,
                "path_count": 2,
                "missing_paths": [],
                "mismatched_paths": [],
                "extra_paths": [],
            },
        }
        result = result_dir / "category-scoped-result.json"
        result.write_text(json.dumps(inspect), encoding="utf-8")
        _force_payload(store, parent, {"deploy_verify_result_path": str(result)})

        ok, reason = store.a5_verify_deploy(parent)
        assert ok, reason
        task = store.get_task(parent)
        assert task["status"] == "terminal_deployed"
        terminal_payload = json.loads(task["payload"])
        assert terminal_payload["deploy_verification_scope"] == "category_scoped"

    def test_a5_verify_refuses_category_scoped_evidence_missing_expected_category(
        self, store, tmp_path
    ):
        manifest = _manifest(tmp_path / "preview.json")
        parent = _authorized_dispatched_a5(store, manifest)
        payload = json.loads(store.get_task(parent)["payload"])
        result_dir = Path(store.K2B_VAULT) / "System" / "orchestrator" / "deploy-results"
        result_dir.mkdir(parents=True, exist_ok=True)
        inspect = _clean_deploy_inspect(payload)
        inspect["remote_head"] = REMOTE_BASELINE_SHA
        inspect["verification_scope"] = "category_scoped"
        inspect["deployed_categories"] = ["scripts"]
        inspect["category_results"] = {
            "scripts": {
                "matched_target": True,
                "path_count": 2,
                "missing_paths": [],
                "mismatched_paths": [],
                "extra_paths": [],
            },
        }
        result = result_dir / "category-missing-result.json"
        result.write_text(json.dumps(inspect), encoding="utf-8")
        _force_payload(store, parent, {"deploy_verify_result_path": str(result)})

        ok, reason = store.a5_verify_deploy(parent)
        assert not ok
        assert "deploy verification categories do not match preview categories" in reason
        assert store.get_task(parent)["status"] != "terminal_deployed"

    def test_a5_verify_refuses_category_scoped_extra_category_result(self, store, tmp_path):
        manifest = _manifest(tmp_path / "preview.json")
        parent = _authorized_dispatched_a5(store, manifest)
        payload = json.loads(store.get_task(parent)["payload"])
        result_dir = Path(store.K2B_VAULT) / "System" / "orchestrator" / "deploy-results"
        result_dir.mkdir(parents=True, exist_ok=True)
        inspect = _clean_deploy_inspect(payload)
        inspect["remote_head"] = REMOTE_BASELINE_SHA
        inspect["verification_scope"] = "category_scoped"
        inspect["deployed_categories"] = ["execution", "scripts"]
        inspect["category_results"] = {
            "execution": {
                "matched_target": True,
                "path_count": 3,
                "missing_paths": [],
                "mismatched_paths": [],
                "extra_paths": [],
            },
            "scripts": {
                "matched_target": True,
                "path_count": 2,
                "missing_paths": [],
                "mismatched_paths": [],
                "extra_paths": [],
            },
            "skills": {
                "matched_target": True,
                "path_count": 1,
                "missing_paths": [],
                "mismatched_paths": [],
                "extra_paths": [],
            },
        }
        result = result_dir / "category-extra-result.json"
        result.write_text(json.dumps(inspect), encoding="utf-8")
        _force_payload(store, parent, {"deploy_verify_result_path": str(result)})

        ok, reason = store.a5_verify_deploy(parent)
        assert not ok
        assert "deploy verification category_results do not match preview categories" in reason

    def test_a5_verify_refuses_category_scoped_result_missing_required_path_keys(
        self, store, tmp_path
    ):
        manifest = _manifest(tmp_path / "preview.json")
        parent = _authorized_dispatched_a5(store, manifest)
        payload = json.loads(store.get_task(parent)["payload"])
        result_dir = Path(store.K2B_VAULT) / "System" / "orchestrator" / "deploy-results"
        result_dir.mkdir(parents=True, exist_ok=True)
        inspect = _clean_deploy_inspect(payload)
        inspect["remote_head"] = REMOTE_BASELINE_SHA
        inspect["verification_scope"] = "category_scoped"
        inspect["deployed_categories"] = ["execution", "scripts"]
        inspect["category_results"] = {
            "execution": {
                "matched_target": True,
                "path_count": 3,
                "missing_paths": [],
                "mismatched_paths": [],
                "extra_paths": [],
            },
            "scripts": {
                "matched_target": True,
                "path_count": 2,
                "missing_paths": [],
                "mismatched_paths": [],
            },
        }
        result = result_dir / "category-missing-key-result.json"
        result.write_text(json.dumps(inspect), encoding="utf-8")
        _force_payload(store, parent, {"deploy_verify_result_path": str(result)})

        ok, reason = store.a5_verify_deploy(parent)
        assert not ok
        assert "deploy verification category extra_paths missing" in reason

    def test_a5_verify_refuses_category_scoped_non_list_path_results(self, store, tmp_path):
        manifest = _manifest(tmp_path / "preview.json")
        parent = _authorized_dispatched_a5(store, manifest)
        payload = json.loads(store.get_task(parent)["payload"])
        result_dir = Path(store.K2B_VAULT) / "System" / "orchestrator" / "deploy-results"
        result_dir.mkdir(parents=True, exist_ok=True)
        inspect = _clean_deploy_inspect(payload)
        inspect["remote_head"] = REMOTE_BASELINE_SHA
        inspect["verification_scope"] = "category_scoped"
        inspect["deployed_categories"] = ["execution", "scripts"]
        inspect["category_results"] = {
            "execution": {
                "matched_target": True,
                "path_count": 3,
                "missing_paths": [],
                "mismatched_paths": [],
                "extra_paths": [],
            },
            "scripts": {
                "matched_target": True,
                "path_count": 2,
                "missing_paths": "[]",
                "mismatched_paths": [],
                "extra_paths": [],
            },
        }
        result = result_dir / "category-non-list-result.json"
        result.write_text(json.dumps(inspect), encoding="utf-8")
        _force_payload(store, parent, {"deploy_verify_result_path": str(result)})

        ok, reason = store.a5_verify_deploy(parent)
        assert not ok
        assert "deploy verification category missing_paths must be a list" in reason

    def test_a5_verify_refuses_unknown_verification_scope(self, store, tmp_path):
        manifest = _manifest(tmp_path / "preview.json")
        parent = _authorized_dispatched_a5(store, manifest)
        payload = json.loads(store.get_task(parent)["payload"])
        result_dir = Path(store.K2B_VAULT) / "System" / "orchestrator" / "deploy-results"
        result_dir.mkdir(parents=True, exist_ok=True)
        inspect = _clean_deploy_inspect(payload)
        inspect["remote_head"] = REMOTE_BASELINE_SHA
        inspect["verification_scope"] = "category-scoped"
        result = result_dir / "unknown-scope-result.json"
        result.write_text(json.dumps(inspect), encoding="utf-8")
        _force_payload(store, parent, {"deploy_verify_result_path": str(result)})

        ok, reason = store.a5_verify_deploy(parent)
        assert not ok
        assert "unknown verification_scope" in reason

    @pytest.mark.parametrize(
        ("path_count", "expected_reason"),
        [
            (0, "deploy verification category has no matched paths"),
            (-1, "deploy verification category has no matched paths"),
            ("2", "deploy verification category path_count invalid"),
            (1.0, "deploy verification category path_count invalid"),
            (True, "deploy verification category path_count invalid"),
        ],
    )
    def test_a5_verify_refuses_category_scoped_invalid_path_count(
        self, store, tmp_path, path_count, expected_reason
    ):
        manifest = _manifest(tmp_path / "preview.json")
        parent = _authorized_dispatched_a5(store, manifest)
        payload = json.loads(store.get_task(parent)["payload"])
        result_dir = Path(store.K2B_VAULT) / "System" / "orchestrator" / "deploy-results"
        result_dir.mkdir(parents=True, exist_ok=True)
        inspect = _clean_deploy_inspect(payload)
        inspect["remote_head"] = REMOTE_BASELINE_SHA
        inspect["verification_scope"] = "category_scoped"
        inspect["deployed_categories"] = ["execution", "scripts"]
        inspect["category_results"] = {
            "execution": {
                "matched_target": True,
                "path_count": 3,
                "missing_paths": [],
                "mismatched_paths": [],
                "extra_paths": [],
            },
            "scripts": {
                "matched_target": True,
                "path_count": path_count,
                "missing_paths": [],
                "mismatched_paths": [],
                "extra_paths": [],
            },
        }
        result = result_dir / "category-bad-path-count-result.json"
        result.write_text(json.dumps(inspect), encoding="utf-8")
        _force_payload(store, parent, {"deploy_verify_result_path": str(result)})

        ok, reason = store.a5_verify_deploy(parent)
        assert not ok
        assert expected_reason in reason

    def test_a5_verify_refuses_category_scoped_baseline_tampered_after_approval(
        self, store, tmp_path
    ):
        manifest = _manifest(tmp_path / "preview.json")
        parent = _authorized_dispatched_a5(store, manifest)
        payload = json.loads(store.get_task(parent)["payload"])
        tampered_baseline = "c" * 40
        result_dir = Path(store.K2B_VAULT) / "System" / "orchestrator" / "deploy-results"
        result_dir.mkdir(parents=True, exist_ok=True)
        inspect = _clean_deploy_inspect(payload)
        inspect["remote_head"] = tampered_baseline
        inspect["verification_scope"] = "category_scoped"
        inspect["deployed_categories"] = ["execution", "scripts"]
        inspect["category_results"] = {
            "execution": {
                "matched_target": True,
                "path_count": 3,
                "missing_paths": [],
                "mismatched_paths": [],
                "extra_paths": [],
            },
            "scripts": {
                "matched_target": True,
                "path_count": 2,
                "missing_paths": [],
                "mismatched_paths": [],
                "extra_paths": [],
            },
        }
        result = result_dir / "category-tampered-baseline-result.json"
        result.write_text(json.dumps(inspect), encoding="utf-8")
        _force_payload(
            store,
            parent,
            {
                "deploy_remote_baseline_sha": tampered_baseline,
                "deploy_verify_result_path": str(result),
            },
        )

        ok, reason = store.a5_verify_deploy(parent)
        assert not ok
        assert "deploy_remote_baseline_sha does not match deploy approval token" in reason

    def test_a5_verify_refuses_category_scoped_unexpected_stale_remote_head(
        self, store, tmp_path
    ):
        manifest = _manifest(tmp_path / "preview.json")
        parent = _authorized_dispatched_a5(store, manifest)
        payload = json.loads(store.get_task(parent)["payload"])
        result_dir = Path(store.K2B_VAULT) / "System" / "orchestrator" / "deploy-results"
        result_dir.mkdir(parents=True, exist_ok=True)
        inspect = _clean_deploy_inspect(payload)
        inspect["remote_head"] = "c" * 40
        inspect["verification_scope"] = "category_scoped"
        inspect["deployed_categories"] = ["execution", "scripts"]
        inspect["category_results"] = {
            "execution": {
                "matched_target": True,
                "path_count": 3,
                "missing_paths": [],
                "mismatched_paths": [],
                "extra_paths": [],
            },
            "scripts": {
                "matched_target": True,
                "path_count": 2,
                "missing_paths": [],
                "mismatched_paths": [],
                "extra_paths": [],
            },
        }
        result = result_dir / "category-wrong-head-result.json"
        result.write_text(json.dumps(inspect), encoding="utf-8")
        _force_payload(store, parent, {"deploy_verify_result_path": str(result)})

        ok, reason = store.a5_verify_deploy(parent)
        assert not ok
        assert "category-scoped remote_head does not match deploy baseline sha" in reason

    @pytest.mark.parametrize(
        ("baseline_sha", "expected_reason"),
        [
            ("", "deploy_remote_baseline_sha missing from payload"),
            ("not-a-sha", "deploy_remote_baseline_sha malformed in payload"),
        ],
    )
    def test_a5_verify_refuses_category_scoped_missing_or_malformed_baseline(
        self, store, tmp_path, baseline_sha, expected_reason
    ):
        manifest = _manifest(tmp_path / "preview.json")
        parent = _authorized_dispatched_a5(store, manifest)
        payload = json.loads(store.get_task(parent)["payload"])
        result_dir = Path(store.K2B_VAULT) / "System" / "orchestrator" / "deploy-results"
        result_dir.mkdir(parents=True, exist_ok=True)
        inspect = _clean_deploy_inspect(payload)
        inspect["remote_head"] = REMOTE_BASELINE_SHA
        inspect["verification_scope"] = "category_scoped"
        inspect["deployed_categories"] = ["execution", "scripts"]
        inspect["category_results"] = {
            "execution": {
                "matched_target": True,
                "path_count": 3,
                "missing_paths": [],
                "mismatched_paths": [],
                "extra_paths": [],
            },
            "scripts": {
                "matched_target": True,
                "path_count": 2,
                "missing_paths": [],
                "mismatched_paths": [],
                "extra_paths": [],
            },
        }
        result = result_dir / "category-bad-baseline-result.json"
        result.write_text(json.dumps(inspect), encoding="utf-8")
        _force_payload(
            store,
            parent,
            {
                "deploy_remote_baseline_sha": baseline_sha,
                "deploy_verify_result_path": str(result),
            },
        )

        ok, reason = store.a5_verify_deploy(parent)
        assert not ok
        assert expected_reason in reason

    def test_a5_verify_refuses_empty_preview_category_in_category_scoped_evidence(
        self, store, tmp_path
    ):
        manifest = _manifest(tmp_path / "preview.json")
        parent = _authorized_dispatched_a5(store, manifest)
        payload = json.loads(store.get_task(parent)["payload"])
        result_dir = Path(store.K2B_VAULT) / "System" / "orchestrator" / "deploy-results"
        result_dir.mkdir(parents=True, exist_ok=True)
        inspect = _clean_deploy_inspect(payload)
        inspect["remote_head"] = REMOTE_BASELINE_SHA
        inspect["verification_scope"] = "category_scoped"
        inspect["deployed_categories"] = ["execution", "scripts"]
        inspect["category_results"] = {
            "execution": {
                "matched_target": True,
                "path_count": 3,
                "missing_paths": [],
                "mismatched_paths": [],
                "extra_paths": [],
            },
            "scripts": {
                "matched_target": True,
                "path_count": 2,
                "missing_paths": [],
                "mismatched_paths": [],
                "extra_paths": [],
            },
        }
        result = result_dir / "category-empty-preview-category-result.json"
        result.write_text(json.dumps(inspect), encoding="utf-8")
        _force_payload(
            store,
            parent,
            {
                "deploy_categories": ["execution", "", "scripts"],
                "deploy_verify_result_path": str(result),
            },
        )

        ok, reason = store.a5_verify_deploy(parent)
        assert not ok
        assert "deploy preview categories contains empty category" in reason

    def test_a5_verify_refuses_category_scoped_sync_state_mismatch(self, store, tmp_path):
        manifest = _manifest(tmp_path / "preview.json")
        parent = _authorized_dispatched_a5(store, manifest)
        payload = json.loads(store.get_task(parent)["payload"])
        result_dir = Path(store.K2B_VAULT) / "System" / "orchestrator" / "deploy-results"
        result_dir.mkdir(parents=True, exist_ok=True)
        inspect = _clean_deploy_inspect(payload)
        inspect["remote_head"] = REMOTE_BASELINE_SHA
        inspect["sync_state_sha"] = "c" * 40
        inspect["verification_scope"] = "category_scoped"
        inspect["deployed_categories"] = ["execution", "scripts"]
        inspect["category_results"] = {
            "execution": {
                "matched_target": True,
                "path_count": 3,
                "missing_paths": [],
                "mismatched_paths": [],
                "extra_paths": [],
            },
            "scripts": {
                "matched_target": True,
                "path_count": 2,
                "missing_paths": [],
                "mismatched_paths": [],
                "extra_paths": [],
            },
        }
        result = result_dir / "category-wrong-sync-state-result.json"
        result.write_text(json.dumps(inspect), encoding="utf-8")
        _force_payload(store, parent, {"deploy_verify_result_path": str(result)})

        ok, reason = store.a5_verify_deploy(parent)
        assert not ok
        assert "sync_state_sha does not match deploy target sha" in reason

    def test_a5_verify_refuses_category_scope_when_remote_head_is_target(self, store, tmp_path):
        manifest = _manifest(tmp_path / "preview.json")
        parent = _authorized_dispatched_a5(store, manifest)
        payload = json.loads(store.get_task(parent)["payload"])
        result_dir = Path(store.K2B_VAULT) / "System" / "orchestrator" / "deploy-results"
        result_dir.mkdir(parents=True, exist_ok=True)
        inspect = _clean_deploy_inspect(payload)
        inspect["verification_scope"] = "category_scoped"
        inspect["deployed_categories"] = ["execution", "scripts"]
        inspect["category_results"] = {
            "execution": {
                "matched_target": True,
                "path_count": 3,
                "missing_paths": [],
                "mismatched_paths": [],
                "extra_paths": [],
            },
            "scripts": {
                "matched_target": True,
                "path_count": 2,
                "missing_paths": [],
                "mismatched_paths": [],
                "extra_paths": [],
            },
        }
        result = result_dir / "category-scope-full-head-result.json"
        result.write_text(json.dumps(inspect), encoding="utf-8")
        _force_payload(store, parent, {"deploy_verify_result_path": str(result)})

        ok, reason = store.a5_verify_deploy(parent)
        assert not ok
        assert "category-scoped remote_head does not match deploy baseline sha" in reason

    def test_a5_verify_refuses_category_scoped_noop_baseline(self, store, tmp_path):
        manifest = _manifest(
            tmp_path / "preview.json",
            target_sha=REMOTE_BASELINE_SHA,
            remote_sha=REMOTE_BASELINE_SHA,
        )
        parent = _authorized_dispatched_a5(store, manifest, target_sha=REMOTE_BASELINE_SHA)
        payload = json.loads(store.get_task(parent)["payload"])
        result_dir = Path(store.K2B_VAULT) / "System" / "orchestrator" / "deploy-results"
        result_dir.mkdir(parents=True, exist_ok=True)
        inspect = _clean_deploy_inspect(payload)
        inspect["remote_head"] = REMOTE_BASELINE_SHA
        inspect["sync_state_sha"] = REMOTE_BASELINE_SHA
        inspect["verification_scope"] = "category_scoped"
        inspect["deployed_categories"] = ["execution", "scripts"]
        inspect["category_results"] = {
            "execution": {
                "matched_target": True,
                "path_count": 3,
                "missing_paths": [],
                "mismatched_paths": [],
                "extra_paths": [],
            },
            "scripts": {
                "matched_target": True,
                "path_count": 2,
                "missing_paths": [],
                "mismatched_paths": [],
                "extra_paths": [],
            },
        }
        result = result_dir / "category-scope-noop-baseline-result.json"
        result.write_text(json.dumps(inspect), encoding="utf-8")
        _force_payload(store, parent, {"deploy_verify_result_path": str(result)})

        ok, reason = store.a5_verify_deploy(parent)
        assert not ok
        assert "category-scoped sync_state_sha did not advance from deploy baseline sha" in reason

    def test_a5_verify_refuses_empty_manifest_sha_bound_to_approval_token(
        self, store, tmp_path
    ):
        manifest = _manifest(tmp_path / "preview.json")
        parent = _authorized_dispatched_a5(store, manifest)
        payload = json.loads(store.get_task(parent)["payload"])
        result_dir = Path(store.K2B_VAULT) / "System" / "orchestrator" / "deploy-results"
        result_dir.mkdir(parents=True, exist_ok=True)
        result = result_dir / "empty-manifest-sha-result.json"
        result.write_text(json.dumps(_clean_deploy_inspect(payload)), encoding="utf-8")
        _force_payload(
            store,
            parent,
            {
                "deploy_preview_manifest_sha256": "",
                "deploy_verify_result_path": str(result),
            },
        )

        ok, reason = store.a5_verify_deploy(parent)
        assert not ok
        assert "deploy_preview_manifest_sha256 missing from deploy approval token payload" in reason

    def test_a5_verify_refuses_manifest_sha_mismatch_bound_to_approval_token(
        self, store, tmp_path
    ):
        manifest = _manifest(tmp_path / "preview.json")
        parent = _authorized_dispatched_a5(store, manifest)
        payload = json.loads(store.get_task(parent)["payload"])
        result_dir = Path(store.K2B_VAULT) / "System" / "orchestrator" / "deploy-results"
        result_dir.mkdir(parents=True, exist_ok=True)
        result = result_dir / "wrong-manifest-sha-result.json"
        result.write_text(json.dumps(_clean_deploy_inspect(payload)), encoding="utf-8")
        _force_payload(
            store,
            parent,
            {
                "deploy_preview_manifest_sha256": "c" * 64,
                "deploy_verify_result_path": str(result),
            },
        )

        ok, reason = store.a5_verify_deploy(parent)
        assert not ok
        assert "deploy_preview_manifest_sha256 does not match deploy approval token" in reason

    def test_a5_category_scoped_helper_requires_explicit_scope(self, store, tmp_path):
        manifest = _manifest(tmp_path / "preview.json")
        parent = _authorized_dispatched_a5(store, manifest)
        payload = json.loads(store.get_task(parent)["payload"])
        inspect = _clean_deploy_inspect(payload)
        inspect["remote_head"] = REMOTE_BASELINE_SHA
        inspect["deployed_categories"] = ["execution", "scripts"]
        inspect["category_results"] = {
            "execution": {
                "matched_target": True,
                "path_count": 3,
                "missing_paths": [],
                "mismatched_paths": [],
                "extra_paths": [],
            },
            "scripts": {
                "matched_target": True,
                "path_count": 2,
                "missing_paths": [],
                "mismatched_paths": [],
                "extra_paths": [],
            },
        }

        ok, reason = store._a5_category_scoped_deploy_inspect_clean(payload, inspect)
        assert not ok
        assert "verification_scope must be category_scoped" in reason

    def test_a5_verify_refuses_empty_dispatch_nonce_even_if_evidence_matches(
        self, store, tmp_path
    ):
        manifest = _manifest(tmp_path / "preview.json")
        parent = _authorized_dispatched_a5(store, manifest)
        payload = json.loads(store.get_task(parent)["payload"])
        result_dir = Path(store.K2B_VAULT) / "System" / "orchestrator" / "deploy-results"
        result_dir.mkdir(parents=True, exist_ok=True)
        inspect = _clean_deploy_inspect(payload)
        inspect["dispatch_nonce"] = ""
        result = result_dir / "empty-nonce-result.json"
        result.write_text(json.dumps(inspect), encoding="utf-8")
        _force_payload(store, parent, {
            "deploy_dispatch_nonce": "",
            "deploy_verify_result_path": str(result),
        })

        ok, reason = store.a5_verify_deploy(parent)
        assert not ok
        assert "deploy dispatch nonce missing from current dispatch" in reason

    def test_a5_verify_refuses_missing_dispatch_nonce_from_evidence(self, store, tmp_path):
        manifest = _manifest(tmp_path / "preview.json")
        parent = _authorized_dispatched_a5(store, manifest)
        payload = json.loads(store.get_task(parent)["payload"])
        result_dir = Path(store.K2B_VAULT) / "System" / "orchestrator" / "deploy-results"
        result_dir.mkdir(parents=True, exist_ok=True)
        inspect = _clean_deploy_inspect(payload)
        inspect.pop("dispatch_nonce")
        result = result_dir / "missing-nonce-result.json"
        result.write_text(json.dumps(inspect), encoding="utf-8")
        _force_payload(store, parent, {"deploy_verify_result_path": str(result)})

        ok, reason = store.a5_verify_deploy(parent)
        assert not ok
        assert "deploy verification dispatch_nonce missing" in reason

    def test_a5_verify_refuses_ambiguous_dual_inspector_paths(self, store, tmp_path):
        manifest = _manifest(tmp_path / "preview.json")
        parent = _authorized_dispatched_a5(store, manifest)
        payload = json.loads(store.get_task(parent)["payload"])
        result_dir = Path(store.K2B_VAULT) / "System" / "orchestrator" / "deploy-results"
        result_dir.mkdir(parents=True, exist_ok=True)
        first = result_dir / "first-result.json"
        second = result_dir / "second-result.json"
        first.write_text(json.dumps(_clean_deploy_inspect(payload)), encoding="utf-8")
        second.write_text(json.dumps(_clean_deploy_inspect(payload)), encoding="utf-8")
        _force_payload(
            store,
            parent,
            {
                "deploy_verify_result_path": str(first),
                "deploy_inspect_path": str(second),
            },
        )

        ok, reason = store.a5_verify_deploy(parent)
        assert not ok
        assert "ambiguous" in reason.lower()

    def test_a5_verify_refuses_dispatch_nonce_mismatch(self, store, tmp_path):
        manifest = _manifest(tmp_path / "preview.json")
        parent = _authorized_dispatched_a5(store, manifest)
        payload = json.loads(store.get_task(parent)["payload"])
        result_dir = Path(store.K2B_VAULT) / "System" / "orchestrator" / "deploy-results"
        result_dir.mkdir(parents=True, exist_ok=True)
        inspect = _clean_deploy_inspect(payload)
        inspect["dispatch_nonce"] = "wrong"
        result = result_dir / "wrong-nonce-result.json"
        result.write_text(json.dumps(inspect), encoding="utf-8")
        _force_payload(store, parent, {"deploy_verify_result_path": str(result)})

        ok, reason = store.a5_verify_deploy(parent)
        assert not ok
        assert "dispatch_nonce" in reason

    def test_a5_verify_requires_expected_named_services(self, store, tmp_path):
        manifest = _manifest(tmp_path / "preview.json")
        parent = _authorized_dispatched_a5(store, manifest)
        payload = json.loads(store.get_task(parent)["payload"])
        result_dir = Path(store.K2B_VAULT) / "System" / "orchestrator" / "deploy-results"
        result_dir.mkdir(parents=True, exist_ok=True)
        inspect = _clean_deploy_inspect(payload)
        inspect["services"] = {"k2bi-engine.service": False, "other.service": True}
        result = result_dir / "wrong-service-result.json"
        result.write_text(json.dumps(inspect), encoding="utf-8")
        _force_payload(store, parent, {"deploy_verify_result_path": str(result)})

        ok, reason = store.a5_verify_deploy(parent)
        assert not ok
        assert "k2bi-engine.service" in reason

    def test_a5_verify_refuses_symlink_trusted_deploy_results_root(self, store, tmp_path):
        manifest = _manifest(tmp_path / "preview.json")
        parent = _authorized_dispatched_a5(store, manifest)
        payload = json.loads(store.get_task(parent)["payload"])
        real_results = tmp_path / "real-results"
        real_results.mkdir()
        result = real_results / "clean-result.json"
        result.write_text(json.dumps(_clean_deploy_inspect(payload)), encoding="utf-8")
        result_root = Path(store.K2B_VAULT) / "System" / "orchestrator" / "deploy-results"
        result_root.parent.mkdir(parents=True, exist_ok=True)
        result_root.symlink_to(real_results, target_is_directory=True)
        _force_payload(store, parent, {"deploy_verify_result_path": str(result_root / result.name)})

        ok, reason = store.a5_verify_deploy(parent)
        assert not ok
        assert "symlink" in reason.lower()
        assert store.get_task(parent)["status"] != "terminal_deployed"

    def test_a5_retry_deploy_after_clean_rollback_reopens_dispatch(self, store, tmp_path):
        repo = tmp_path / "k2bi"
        _init_k2bi_repo(repo)
        head = _git(repo, "rev-parse", "HEAD").stdout.strip()
        manifest = _manifest(tmp_path / "preview.json", target_sha=head)
        parent = _authorized_dispatched_a5(store, manifest, target_sha=head)
        result_dir = Path(store.K2B_VAULT) / "System" / "orchestrator" / "deploy-results"
        result_dir.mkdir(parents=True, exist_ok=True)
        result = result_dir / "rollback-result.json"
        result.write_text(json.dumps(_clean_rollback_deploy_inspect()), encoding="utf-8")
        _force_payload(store, parent, {"deploy_verify_result_path": str(result)})

        ok, reason = store.a5_record_deploy_failed(parent, reason="network rollback")
        assert ok, reason
        ok, reason = store.a5_retry_deploy_after_rollback(parent)
        assert ok, reason
        assert store.a1_resume_action(parent) == "dispatch_deploy"

    def test_a5_record_deploy_failed_clears_stale_verification_scope(self, store, tmp_path):
        manifest = _manifest(tmp_path / "preview.json")
        parent = _authorized_dispatched_a5(store, manifest)
        _force_payload(store, parent, {"deploy_verification_scope": "category_scoped"})

        ok, reason = store.a5_record_deploy_failed(parent, reason="worker failed")
        assert ok, reason
        payload = json.loads(store.get_task(parent)["payload"])
        assert "deploy_verification_scope" not in payload

    def test_a5_retry_refuses_manifest_drift_before_reopen(self, store, tmp_path):
        manifest = _manifest(tmp_path / "preview.json")
        parent = _authorized_dispatched_a5(store, manifest)
        result_dir = Path(store.K2B_VAULT) / "System" / "orchestrator" / "deploy-results"
        result_dir.mkdir(parents=True, exist_ok=True)
        result = result_dir / "rollback-result.json"
        result.write_text(json.dumps(_clean_rollback_deploy_inspect()), encoding="utf-8")
        _force_payload(store, parent, {"deploy_verify_result_path": str(result)})

        ok, reason = store.a5_record_deploy_failed(parent, reason="network rollback")
        assert ok, reason
        _manifest(manifest, remote_sha="c" * 40)

        ok, reason = store.a5_retry_deploy_after_rollback(parent)
        assert not ok
        assert "preview manifest changed" in reason

    def test_a5_retry_refuses_advanced_k2bi_head(self, store, tmp_path):
        repo = tmp_path / "k2bi"
        _init_k2bi_repo(repo)
        first_head = _git(repo, "rev-parse", "HEAD").stdout.strip()
        manifest = _manifest(tmp_path / "preview.json", target_sha=first_head)
        parent = _authorized_dispatched_a5(store, manifest, target_sha=first_head)
        result_dir = Path(store.K2B_VAULT) / "System" / "orchestrator" / "deploy-results"
        result_dir.mkdir(parents=True, exist_ok=True)
        result = result_dir / "rollback-result.json"
        result.write_text(json.dumps(_clean_rollback_deploy_inspect()), encoding="utf-8")
        _force_payload(store, parent, {"deploy_verify_result_path": str(result)})

        ok, reason = store.a5_record_deploy_failed(parent, reason="network rollback")
        assert ok, reason
        (repo / "README.md").write_text("# repo\n\nadvanced\n", encoding="utf-8")
        _git(repo, "add", "README.md")
        _git(repo, "commit", "-m", "advance head")

        ok, reason = store.a5_retry_deploy_after_rollback(parent)
        assert not ok
        assert "advanced" in reason

    def test_a5_verify_refuses_recovery_mismatch(self, store, tmp_path, monkeypatch):
        manifest = _manifest(tmp_path / "preview.json")
        parent = _authorized_dispatched_a5(store, manifest)
        monkeypatch.setattr(
            store,
            "_a5_inspect_deploy_state_from_payload",
            lambda _task_id, _payload: {
                "state": "recovery_mismatch",
                "remote_head": TARGET_SHA,
                "sync_state_sha": TARGET_SHA,
                "service_active": True,
                "recovery_state_mismatch_count": 1,
            },
        )

        ok, reason = store.a5_verify_deploy(parent)
        assert not ok
        assert "recovery_mismatch" in reason
        assert store.a1_resume_action(parent) == "verify_deploy"

    def test_a5_attempt_limit_parks_needs_human_terminal(self, store, tmp_path):
        manifest = _manifest(tmp_path / "preview.json")
        parent = _a5_parent(
            store,
            payload_updates={"deploy_attempt_count": store.A5_MAX_DEPLOY_ATTEMPTS},
        )
        ok, reason = store.a5_record_deploy_preview(parent, str(manifest))
        assert ok, reason
        ok, reason = store.a5_authorize_deploy(parent)
        assert ok, reason

        ok, reason = store.a5_mark_deploy_dispatch_started(parent)
        assert not ok
        assert "deploy attempt limit exceeded" in reason
        assert store.a1_resume_action(parent) == "needs_human_terminal"


def _deploy_payload(
    manifest: Path,
    *,
    target_sha: str = TARGET_SHA,
    manifest_sha: str | None = None,
    baseline_path: Path,
    manifest_realpath: str | None = None,
    approved_at: str = "2026-06-19T00:00:00+00:00",
    lease_id: str = "cdns-deploy-a1-20260619T000000Z",
) -> dict:
    manifest_hash = manifest_sha or _sha256(manifest)
    token = f"APPROVE_DEPLOY:{target_sha}:{REMOTE_BASELINE_SHA}:{manifest_hash}:{approved_at}:{lease_id}"
    return {
        "target_sha": target_sha,
        "remote_baseline_sha": REMOTE_BASELINE_SHA,
        "remote_baseline_path": str(baseline_path),
        "manifest_path": str(manifest),
        "manifest_realpath": manifest_realpath or str(manifest.resolve(strict=False)),
        "manifest_sha256": manifest_hash,
        "categories": ["execution", "scripts"],
        "restart_services": ["k2bi-engine.service"],
        "approval": {
            "final_approval_token": token,
            "approved_by": "keith",
            "approved_at": approved_at,
            "deploy_lease_id": lease_id,
        },
    }


def _clean_deploy_inspect(payload: dict | None = None) -> dict:
    result = {
        "state": "deployed",
        "remote_head": TARGET_SHA,
        "sync_state_sha": TARGET_SHA,
        "service_active": True,
        "recovery_state_mismatch_count": 0,
    }
    if payload is not None:
        result["approval_token"] = payload.get("deploy_approval_token")
        result["dispatch_started_at"] = payload.get("deploy_dispatch_started_at")
        result["dispatch_nonce"] = payload.get("deploy_dispatch_nonce")
        result["services"] = {
            service: True for service in payload.get("deploy_restart_services", [])
        }
    return result


def _clean_rollback_deploy_inspect() -> dict:
    return {
        "state": "clean_rollback",
        "remote_head": REMOTE_BASELINE_SHA,
        "sync_state_sha": REMOTE_BASELINE_SHA,
        "service_active": True,
        "recovery_state_mismatch_count": 0,
    }


def _authorized_dispatched_a5(store, manifest: Path, *, target_sha: str = TARGET_SHA) -> str:
    parent = _a5_parent(store, target_sha=target_sha)
    ok, reason = store.a5_record_deploy_preview(parent, str(manifest))
    assert ok, reason
    ok, reason = store.a5_authorize_deploy(parent)
    assert ok, reason
    ok, dispatch = store.a5_mark_deploy_dispatch_started(parent)
    assert ok, dispatch
    return parent
