#!/usr/bin/env python3
"""pytest coverage for orchestrator Phase A4 (operator-approved limits apply)."""

from __future__ import annotations

import hashlib
import json
import os
import re
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


def _init_git_repo(repo: Path, *, symbols=("SPY",)) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "K2B Test")
    (repo / "README.md").write_text("# repo\n", encoding="utf-8")
    (repo / "execution" / "validators").mkdir(parents=True, exist_ok=True)
    _write_config(repo / "execution" / "validators" / "config.yaml", symbols)
    _git(repo, "add", "README.md", "execution/validators/config.yaml")
    _git(repo, "commit", "-m", "initial")


def _write_config(path: Path, symbols) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "validators:\n  enabled: true\ninstrument_whitelist:\n  symbols:\n"
    body += "".join(f"  - {str(symbol).upper()}\n" for symbol in symbols)
    path.write_text(body, encoding="utf-8")
    return path


def _limits_proposal(
    path: Path,
    *,
    slug="instrument_whitelist-add-CDNS",
    status="proposed",
    before=("SPY",),
    after=("SPY", "CDNS"),
    rule="instrument_whitelist",
    change_type="add",
) -> Path:
    if path.suffix != ".md":
        path = path / f"2026-06-09_limits-proposal_{slug}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    before_inline = "[" + ", ".join(before) + "]"
    after_inline = "[" + ", ".join(after) + "]"
    before_yaml = "".join(f"    - {symbol}\n" for symbol in before)
    after_yaml = "".join(f"    - {symbol}\n" for symbol in after)
    path.write_text(
        "---\n"
        "type: limits-proposal\n"
        f"status: {status}\n"
        "applies-to: execution/validators/config.yaml\n"
        "---\n\n"
        "# Limits Proposal\n\n"
        "## Change\n\n"
        "```yaml\n"
        f"rule: {rule}\n"
        f"change_type: {change_type}\n"
        "ticker: CDNS\n"
        "field: symbols\n"
        f"before: {before_inline}\n"
        f"after: {after_inline}\n"
        "```\n\n"
        "## YAML Patch\n\n"
        "before:\n\n"
        "```yaml\n"
        "  symbols:\n"
        f"{before_yaml}"
        "```\n\n"
        "after:\n\n"
        "```yaml\n"
        "  symbols:\n"
        f"{after_yaml}"
        "```\n",
        encoding="utf-8",
    )
    return path


def _approve_proposal(path: Path, parent_sha: str) -> None:
    text = path.read_text(encoding="utf-8")
    text = text.replace("status: proposed", "status: approved")
    text = text.replace(
        "---\n\n# Limits Proposal",
        "---\napproved_at: '2026-06-09T00:00:00+00:00'\n"
        f"approved_commit_sha: {parent_sha}\n\n# Limits Proposal",
    )
    path.write_text(text, encoding="utf-8")


def _limits_token(slug: str, proposal_sha: str, config_sha: str, approved_at: str, lease: str) -> str:
    return f"APPROVE_LIMITS:{slug}:{proposal_sha}:{config_sha}:{approved_at}:{lease}"


def _limits_payload(proposal: Path, *, config: Path | None = None, slug="instrument_whitelist-add-CDNS") -> dict:
    approved_at = "2026-06-09T00:00:00+00:00"
    lease = f"{slug}-limits-a1-20260609T000000Z"
    config = config or proposal.parents[2] / "execution" / "validators" / "config.yaml"
    return {
        "payload_path": "/tmp/k2b-orchestrator/limits.json",
        "proposal_path": str(proposal),
        "approval": {
            "final_approval_token": _limits_token(slug, _sha256(proposal), _sha256(config), approved_at, lease),
            "approved_by": "keith",
            "approved_at": approved_at,
            "apply_lease_id": lease,
        },
        "required_primary": "minimax",
    }


def _a4_parent(store, *, entity="instrument_whitelist-add-CDNS", payload_updates=None):
    payload = {"chain_kind": "limits"}
    if payload_updates:
        payload.update(payload_updates)
    return store.add_task(
        assignee_profile="k2b",
        command_key="k2b-a4-limits",
        success_criteria="A4 limits chain parked",
        permissions="agent-native",
        entity_key=entity,
        status="needs_human",
        payload=payload,
    )


def _recorded_authorized_parent(store, repo: Path, proposal: Path):
    tid = _a4_parent(store)
    ok, reason = store.a4_record_limits_proposal(tid, str(proposal))
    assert ok, reason
    assert store.a1_resume_action(tid) == "await_limits_approval"
    ok, reason = store.a4_authorize_limits(tid)
    assert ok, reason
    return tid


def _k2bi_task(command_key: str, payload: dict, *, entity="instrument_whitelist-add-CDNS") -> dict:
    return {
        "assignee_profile": "k2bi",
        "command_key": command_key,
        "entity_key": entity,
        "payload": json.dumps(payload),
    }


class TestA4OracleAndGates:
    def test_limits_ladder_and_dual_sha_dispatch_token(self, store, tmp_path, monkeypatch):
        repo = tmp_path / "k2bi"
        _init_git_repo(repo)
        monkeypatch.setenv("K2B_ORCH_K2BI_WORKSPACE", str(repo))
        proposal = _limits_proposal(repo / "review" / "strategy-approvals")
        tid = _a4_parent(store)
        assert store.a1_resume_action(tid) == "author_limits_proposal"

        ok, reason = store.a4_record_limits_proposal(tid, str(proposal))
        assert ok, reason
        assert store.a1_resume_action(tid) == "await_limits_approval"
        payload = json.loads(store.get_task(tid)["payload"])
        assert payload["limits_proposal_slug"] == "instrument_whitelist-add-CDNS"
        assert payload["limits_expected_after_symbols"] == ["CDNS", "SPY"]

        ok, reason = store.a4_authorize_limits(tid)
        assert ok, reason
        assert store.a1_resume_action(tid) == "dispatch_limits"

        ok, dispatch = store.a4_mark_limits_dispatch_started(tid)
        assert ok, dispatch
        assert store.a1_resume_action(tid) == "verify_limits"
        assert dispatch["proposal_sha"] == _sha256(proposal)
        assert dispatch["config_sha"] == _sha256(repo / "execution" / "validators" / "config.yaml")
        assert re.match(r"^[A-Za-z0-9][A-Za-z0-9_.-]{2,127}$", dispatch["apply_lease_id"])
        expected = _limits_token(
            "instrument_whitelist-add-CDNS",
            dispatch["proposal_sha"],
            dispatch["config_sha"],
            dispatch["approved_at"],
            dispatch["apply_lease_id"],
        )
        assert dispatch["approval_token"] == expected

    def test_record_limits_proposal_refuses_non_whitelist_scope(self, store, tmp_path, monkeypatch):
        repo = tmp_path / "k2bi"
        _init_git_repo(repo)
        monkeypatch.setenv("K2B_ORCH_K2BI_WORKSPACE", str(repo))
        proposal = _limits_proposal(
            repo / "review" / "strategy-approvals",
            slug="position-size-widen-CDNS",
            rule="position_size",
            change_type="widen",
        )
        tid = _a4_parent(store, entity="position-size-widen-CDNS")
        ok, reason = store.a4_record_limits_proposal(tid, str(proposal))
        assert not ok
        assert "instrument_whitelist/add" in reason

    def test_mark_limits_dispatch_attempt_limit_terminalizes(self, store, tmp_path, monkeypatch):
        repo = tmp_path / "k2bi"
        _init_git_repo(repo)
        monkeypatch.setenv("K2B_ORCH_K2BI_WORKSPACE", str(repo))
        proposal = _limits_proposal(repo / "review" / "strategy-approvals")
        tid = _recorded_authorized_parent(store, repo, proposal)
        task = store.get_task(tid)
        payload = json.loads(task["payload"])
        payload["limits_attempt_count"] = store.A4_MAX_APPLY_ATTEMPTS
        conn = store.connect()
        store._update_payload_locked(conn, tid, payload, status="needs_human")
        conn.commit()
        conn.close()

        ok, reason = store.a4_mark_limits_dispatch_started(tid)
        assert not ok
        assert "limits apply attempt limit exceeded" in reason
        assert store.a1_resume_action(tid) == "needs_human_terminal"

    def test_non_limits_chain_still_uses_existing_ladder(self, store, tmp_path):
        tid = store.add_task(
            assignee_profile="k2b",
            command_key="k2b-a1-chain",
            success_criteria="A1",
            permissions="agent-native",
            entity_key="CDNS",
            status="needs_human",
            payload={},
        )
        assert store.a1_resume_action(tid) == "await_promote"

    def test_oracle_does_not_trust_stale_limits_verified_on_nonterminal_row(self, store):
        # CP2 r1 fix C: a non-terminal row carrying limits_verified=True is
        # corruption; the oracle must NOT claim terminal -- it falls through to
        # verify_limits (which re-inspects and fail-closes), never terminal.
        tid = _a4_parent(
            store,
            payload_updates={
                "limits_proposal_recorded": True,
                "limits_proposal_path": "/tmp/x_limits-proposal_instrument_whitelist-add-CDNS.md",
                "limits_proposal_sha256": "a" * 64,
                "limits_authorized": True,
                "limits_dispatch_started_at": "2026-06-09T00:00:00+00:00",
                "limits_approved_at": "2026-06-09T00:00:00+00:00",
                "limits_verified": True,  # corrupted: set without a terminal status
            },
        )
        assert store.get_task(tid)["status"] == "needs_human"
        assert store.a1_resume_action(tid) == "verify_limits"

    def test_authorize_refuses_proposal_sha_drift(self, store, tmp_path, monkeypatch):
        # CP2 r1 fix D: a proposal mutated after record must be refused at the gate
        # (clearer early error than a late mark-dispatch refusal).
        repo = tmp_path / "k2bi"
        _init_git_repo(repo)
        monkeypatch.setenv("K2B_ORCH_K2BI_WORKSPACE", str(repo))
        proposal = _limits_proposal(repo / "review" / "strategy-approvals")
        tid = _a4_parent(store)
        ok, reason = store.a4_record_limits_proposal(tid, str(proposal))
        assert ok, reason
        proposal.write_text(proposal.read_text(encoding="utf-8") + "\nmutated\n", encoding="utf-8")
        ok, reason = store.a4_authorize_limits(tid)
        assert not ok
        assert "proposal changed since record" in reason

    def test_mark_dispatch_refuses_dirty_config_without_minting(self, store, tmp_path, monkeypatch):
        # Codex CP2 final F1: a dirty validator config must refuse at mark-dispatch
        # BEFORE a capital token is minted or an attempt is burned (the child preflight
        # would refuse anyway, but minting/burning over a refusable state wastes the
        # bounded attempts and leaves stale token state).
        repo = tmp_path / "k2bi"
        _init_git_repo(repo)
        monkeypatch.setenv("K2B_ORCH_K2BI_WORKSPACE", str(repo))
        proposal = _limits_proposal(repo / "review" / "strategy-approvals")
        tid = _recorded_authorized_parent(store, repo, proposal)
        # Dirty the tracked validator config (unstaged change).
        cfg = repo / "execution" / "validators" / "config.yaml"
        cfg.write_text(cfg.read_text(encoding="utf-8") + "\n# dirty\n", encoding="utf-8")
        ok, result = store.a4_mark_limits_dispatch_started(tid)
        assert not ok
        assert "dirty" in str(result).lower() or "config" in str(result).lower()
        payload = json.loads(store.get_task(tid)["payload"])
        assert int(payload.get("limits_attempt_count") or 0) == 0
        assert "limits_approval_token" not in payload
        assert "limits_dispatch_started_at" not in payload

    def test_record_limits_proposal_refuses_and_preserves_partial_state(self, store, tmp_path, monkeypatch):
        # Codex CP2 final F3: re-recording must NOT erase a partial_approved_uncommitted
        # recovery state -- the durable "human recovery required" evidence is preserved.
        repo = tmp_path / "k2bi"
        _init_git_repo(repo)
        monkeypatch.setenv("K2B_ORCH_K2BI_WORKSPACE", str(repo))
        proposal = _limits_proposal(repo / "review" / "strategy-approvals")
        tid = _a4_parent(
            store,
            payload_updates={
                "limits_partial_detected_at": "2026-06-09T00:00:00+00:00",
                "limits_partial_reason": "apply approved but not committed",
            },
        )
        ok, reason = store.a4_record_limits_proposal(tid, str(proposal))
        assert not ok
        assert "partial" in reason.lower()
        payload = json.loads(store.get_task(tid)["payload"])
        assert payload.get("limits_partial_detected_at") == "2026-06-09T00:00:00+00:00"
        assert not payload.get("limits_proposal_recorded")


class TestA4Inspector:
    def test_inspector_classifies_committed_exact_whitelist_and_verify_terminalizes(
        self, store, tmp_path, monkeypatch
    ):
        repo = tmp_path / "k2bi"
        _init_git_repo(repo)
        monkeypatch.setenv("K2B_ORCH_K2BI_WORKSPACE", str(repo))
        proposal = _limits_proposal(repo / "review" / "strategy-approvals")
        tid = _recorded_authorized_parent(store, repo, proposal)
        ok, dispatch = store.a4_mark_limits_dispatch_started(tid)
        assert ok, dispatch
        head_before = _git(repo, "rev-parse", "HEAD").stdout.strip()
        _approve_proposal(proposal, head_before)
        _write_config(repo / "execution" / "validators" / "config.yaml", ("SPY", "CDNS"))
        _git(repo, "add", "review/strategy-approvals", "execution/validators/config.yaml")
        # The real K2Bi commit message carries this dispatch's approved_at verbatim
        # ("Approval-Captured-At: <approved_at>"); the inspector's provenance gate
        # requires it before classifying committed.
        _git(repo, "commit", "-m", f"Approve limits\n\nApproval-Captured-At: {dispatch['approved_at']}")

        state = store.a4_inspect_limits_state(tid)
        assert state["state"] == "committed"
        ok, reason = store.a4_verify_limits(tid)
        assert ok, reason
        assert store.get_task(tid)["status"] == "terminal_limits_applied"
        assert store.a1_resume_action(tid) == "terminal_limits_applied"

    def test_inspector_rejects_extra_whitelist_symbol_as_partial(self, store, tmp_path, monkeypatch):
        repo = tmp_path / "k2bi"
        _init_git_repo(repo)
        monkeypatch.setenv("K2B_ORCH_K2BI_WORKSPACE", str(repo))
        proposal = _limits_proposal(repo / "review" / "strategy-approvals")
        tid = _recorded_authorized_parent(store, repo, proposal)
        ok, dispatch = store.a4_mark_limits_dispatch_started(tid)
        assert ok
        head_before = _git(repo, "rev-parse", "HEAD").stdout.strip()
        _approve_proposal(proposal, head_before)
        _write_config(repo / "execution" / "validators" / "config.yaml", ("SPY", "CDNS", "BAD"))
        _git(repo, "add", "review/strategy-approvals", "execution/validators/config.yaml")
        # Provenance present, so the ONLY failing condition is the extra symbol.
        _git(repo, "commit", "-m", f"Approve limits with extra\n\nApproval-Captured-At: {dispatch['approved_at']}")

        state = store.a4_inspect_limits_state(tid)
        assert state["state"] == "partial_approved_uncommitted"
        assert "recorded operator-approved after list" in (state.get("reason") or "")

    def test_inspector_rejects_commit_without_approval_provenance(self, store, tmp_path, monkeypatch):
        # CP2 r1 fix A: a tree-correct commit whose message does NOT carry this
        # dispatch's approved_at is NOT this dispatch's commit -> not committed.
        repo = tmp_path / "k2bi"
        _init_git_repo(repo)
        monkeypatch.setenv("K2B_ORCH_K2BI_WORKSPACE", str(repo))
        proposal = _limits_proposal(repo / "review" / "strategy-approvals")
        tid = _recorded_authorized_parent(store, repo, proposal)
        ok, _dispatch = store.a4_mark_limits_dispatch_started(tid)
        assert ok
        head_before = _git(repo, "rev-parse", "HEAD").stdout.strip()
        _approve_proposal(proposal, head_before)
        _write_config(repo / "execution" / "validators" / "config.yaml", ("SPY", "CDNS"))
        _git(repo, "add", "review/strategy-approvals", "execution/validators/config.yaml")
        _git(repo, "commit", "-m", "Approve limits (no provenance trailer)")

        state = store.a4_inspect_limits_state(tid)
        assert state["state"] == "partial_approved_uncommitted"
        assert "provenance" in (state.get("reason") or "")
        ok, reason = store.a4_verify_limits(tid)
        assert not ok
        assert store.get_task(tid)["status"] != "terminal_limits_applied"

    def test_inspector_rejects_config_not_matching_recorded_snapshot(self, store, tmp_path, monkeypatch):
        # Codex CP2 final F2: a tampered/amended commit changes BOTH the HEAD proposal's
        # ## Change.after AND config.yaml to a DIFFERENT whitelist than approved (plus the
        # trailer). The committed whitelist must match the RECORDED operator-approved
        # snapshot, not the mutable HEAD proposal -- so this is NOT committed.
        repo = tmp_path / "k2bi"
        _init_git_repo(repo)
        monkeypatch.setenv("K2B_ORCH_K2BI_WORKSPACE", str(repo))
        proposal = _limits_proposal(repo / "review" / "strategy-approvals")  # approved after = (SPY, CDNS)
        tid = _recorded_authorized_parent(store, repo, proposal)
        ok, dispatch = store.a4_mark_limits_dispatch_started(tid)
        assert ok, dispatch
        head_before = _git(repo, "rev-parse", "HEAD").stdout.strip()
        # Tamper at "apply" time: rewrite the proposal's after-list to add EVIL and make
        # config agree, so a HEAD-proposal-based check would (wrongly) pass.
        _limits_proposal(repo / "review" / "strategy-approvals", after=("SPY", "CDNS", "EVIL"))
        _approve_proposal(proposal, head_before)
        _write_config(repo / "execution" / "validators" / "config.yaml", ("SPY", "CDNS", "EVIL"))
        _git(repo, "add", "review/strategy-approvals", "execution/validators/config.yaml")
        _git(repo, "commit", "-m", f"Tampered approve\n\nApproval-Captured-At: {dispatch['approved_at']}")

        state = store.a4_inspect_limits_state(tid)
        assert state["state"] == "partial_approved_uncommitted"
        assert "recorded operator-approved after list" in (state.get("reason") or "")

    def test_inspector_rejects_commit_parent_not_baseline(self, store, tmp_path, monkeypatch):
        # Codex CP2 final F2: a legitimate apply is ONE commit on top of the recorded
        # dispatch baseline. An extra commit between dispatch and the apply means HEAD's
        # parent != limits_repo_head_before -> NOT committed (rejects amended/squashed/
        # unrelated HEAD that merely advanced past the baseline).
        repo = tmp_path / "k2bi"
        _init_git_repo(repo)
        monkeypatch.setenv("K2B_ORCH_K2BI_WORKSPACE", str(repo))
        proposal = _limits_proposal(repo / "review" / "strategy-approvals")
        tid = _recorded_authorized_parent(store, repo, proposal)
        ok, dispatch = store.a4_mark_limits_dispatch_started(tid)
        assert ok, dispatch
        # Unrelated commit AFTER the dispatch baseline was recorded.
        (repo / "unrelated.txt").write_text("x\n", encoding="utf-8")
        _git(repo, "add", "unrelated.txt")
        _git(repo, "commit", "-m", "unrelated")
        head_before = _git(repo, "rev-parse", "HEAD").stdout.strip()  # NOT the recorded baseline
        _approve_proposal(proposal, head_before)
        _write_config(repo / "execution" / "validators" / "config.yaml", ("SPY", "CDNS"))
        _git(repo, "add", "review/strategy-approvals", "execution/validators/config.yaml")
        _git(repo, "commit", "-m", f"Approve limits\n\nApproval-Captured-At: {dispatch['approved_at']}")

        state = store.a4_inspect_limits_state(tid)
        assert state["state"] == "partial_approved_uncommitted"
        assert "baseline" in (state.get("reason") or "")

    def test_inspector_classifies_clean_rollback_and_marker(self, store, tmp_path, monkeypatch):
        repo = tmp_path / "k2bi"
        _init_git_repo(repo)
        monkeypatch.setenv("K2B_ORCH_K2BI_WORKSPACE", str(repo))
        proposal = _limits_proposal(repo / "review" / "strategy-approvals")
        tid = _recorded_authorized_parent(store, repo, proposal)
        ok, _dispatch = store.a4_mark_limits_dispatch_started(tid)
        assert ok
        assert store.a4_inspect_limits_state(tid)["state"] == "clean_rollback"

        marker = repo / ".k2bi-orchestrator" / "rollback" / "limits_instrument_whitelist-add-CDNS.json"
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text('{"phase":"started"}\n', encoding="utf-8")
        assert store.a4_inspect_limits_state(tid)["state"] == "incomplete_rollback_marker"

    def test_verify_limits_partial_parks_needs_human(self, store, tmp_path, monkeypatch):
        repo = tmp_path / "k2bi"
        _init_git_repo(repo)
        monkeypatch.setenv("K2B_ORCH_K2BI_WORKSPACE", str(repo))
        proposal = _limits_proposal(repo / "review" / "strategy-approvals")
        tid = _recorded_authorized_parent(store, repo, proposal)
        ok, _dispatch = store.a4_mark_limits_dispatch_started(tid)
        assert ok
        _approve_proposal(proposal, _git(repo, "rev-parse", "HEAD").stdout.strip())

        ok, reason = store.a4_verify_limits(tid)
        assert not ok
        assert "partial_approved_uncommitted" in reason
        assert store.get_task(tid)["status"] == "needs_human"
        assert store.a1_resume_action(tid) == "limits_partial"


class TestA4Profiles:
    def test_apply_limits_command_is_allowlisted_with_payload_carrier(self, store):
        from scripts.lib import orchestrator_profiles as profiles

        argv = profiles.resolve_command(
            "k2bi", "k2bi-apply-limits", {"payload_path": "/tmp/k2b-orchestrator/limits.json"}
        )
        assert argv is not None
        assert argv[-3:] == ["apply-limits", "--workspace", profiles.resolve_workspace("k2bi")] or "apply-limits" in argv
        assert "--payload-path" in argv
        assert profiles.resolve_command("k2bi", "k2bi-apply-limits", {}) is None

    def test_a4_preflight_accepts_untracked_proposal_clean_config(self, store, tmp_path, monkeypatch):
        from scripts.lib import orchestrator_profiles as profiles

        repo = tmp_path / "k2bi"
        _init_git_repo(repo)
        vault = tmp_path / "k2bi-vault"
        vault.mkdir(exist_ok=True)
        monkeypatch.setenv("K2B_ORCH_K2BI_WORKSPACE", str(repo))
        monkeypatch.setenv("K2BI_VAULT_PATH", str(vault))
        proposal = _limits_proposal(repo / "review" / "strategy-approvals")
        payload = _limits_payload(proposal)
        ok, reason = profiles.preflight_k2bi(_k2bi_task("k2bi-apply-limits", payload))
        assert ok, reason

    def test_a4_preflight_rejects_wrong_proposal_and_config_sha(self, store, tmp_path, monkeypatch):
        from scripts.lib import orchestrator_profiles as profiles

        repo = tmp_path / "k2bi"
        _init_git_repo(repo)
        vault = tmp_path / "k2bi-vault"
        vault.mkdir(exist_ok=True)
        monkeypatch.setenv("K2B_ORCH_K2BI_WORKSPACE", str(repo))
        monkeypatch.setenv("K2BI_VAULT_PATH", str(vault))
        proposal = _limits_proposal(repo / "review" / "strategy-approvals")
        payload = _limits_payload(proposal)
        payload["approval"]["final_approval_token"] = _limits_token(
            "instrument_whitelist-add-CDNS",
            "f" * 64,
            "e" * 64,
            payload["approval"]["approved_at"],
            payload["approval"]["apply_lease_id"],
        )
        ok, reason = profiles.preflight_k2bi(_k2bi_task("k2bi-apply-limits", payload))
        assert not ok
        assert "approval token" in reason or "token" in reason

    def test_a4_preflight_refuses_modified_or_staged_config_even_with_valid_token(
        self, store, tmp_path, monkeypatch
    ):
        from scripts.lib import orchestrator_profiles as profiles

        repo = tmp_path / "k2bi"
        _init_git_repo(repo)
        vault = tmp_path / "k2bi-vault"
        vault.mkdir(exist_ok=True)
        monkeypatch.setenv("K2B_ORCH_K2BI_WORKSPACE", str(repo))
        monkeypatch.setenv("K2BI_VAULT_PATH", str(vault))
        proposal = _limits_proposal(repo / "review" / "strategy-approvals")
        config = repo / "execution" / "validators" / "config.yaml"
        _write_config(config, ("SPY", "CDNS"))
        payload = _limits_payload(proposal, config=config)
        ok, reason = profiles.preflight_k2bi(_k2bi_task("k2bi-apply-limits", payload))
        assert not ok
        assert "config" in reason and "dirty" in reason.lower()

        _git(repo, "add", "execution/validators/config.yaml")
        payload = _limits_payload(proposal, config=config)
        ok, reason = profiles.preflight_k2bi(_k2bi_task("k2bi-apply-limits", payload))
        assert not ok
        assert "config" in reason and "dirty" in reason.lower()

    def test_a4_preflight_refuses_unrelated_dirty_file(self, store, tmp_path, monkeypatch):
        from scripts.lib import orchestrator_profiles as profiles

        repo = tmp_path / "k2bi"
        _init_git_repo(repo)
        vault = tmp_path / "k2bi-vault"
        vault.mkdir(exist_ok=True)
        monkeypatch.setenv("K2B_ORCH_K2BI_WORKSPACE", str(repo))
        monkeypatch.setenv("K2BI_VAULT_PATH", str(vault))
        proposal = _limits_proposal(repo / "review" / "strategy-approvals")
        (repo / "README.md").write_text("# dirty\n", encoding="utf-8")
        ok, reason = profiles.preflight_k2bi(_k2bi_task("k2bi-apply-limits", _limits_payload(proposal)))
        assert not ok
        assert "dirty" in reason.lower()


class TestA4AdapterRunner:
    def _setup(self, tmp_path):
        real = lambda p: Path(os.path.realpath(str(p)))
        ws = real(tmp_path) / "workspace"
        _write_a4_workspace(ws)
        repo = real(tmp_path) / "k2bi-repo"
        _init_git_repo(repo)
        vault = real(tmp_path) / "k2bi-vault"
        vault.mkdir(exist_ok=True)
        paydir = real(tmp_path) / "payloads"
        paydir.mkdir()
        return ws, repo, vault, paydir

    def _run(self, cmd, *, workspace, payload, repo, vault, paydir):
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
            },
            capture_output=True,
            text=True,
            timeout=30,
        )

    def test_runner_apply_limits_builds_approval_and_serializes_result(self, tmp_path):
        ws, repo, vault, paydir = self._setup(tmp_path)
        proposal = _limits_proposal(repo / "review" / "strategy-approvals")
        payload = _limits_payload(proposal)
        r = self._run("apply-limits", workspace=ws, payload=payload, repo=repo, vault=vault, paydir=paydir)
        assert r.returncode == 0, r.stdout + r.stderr
        out = json.loads(r.stdout)
        assert out["status"] == "ok"
        assert out["result"]["slug"] == "instrument_whitelist-add-CDNS"
        assert out["result"]["approval"]["apply_lease_id"] == payload["approval"]["apply_lease_id"]
        assert out["result"]["required_primary"] == "minimax"

    def test_runner_apply_limits_surfaces_rollback_result_on_gate_error(self, tmp_path):
        ws, repo, vault, paydir = self._setup(tmp_path)
        proposal = _limits_proposal(repo / "review" / "strategy-approvals", slug="rollback-CDNS")
        payload = _limits_payload(proposal, slug="rollback-CDNS")
        r = self._run("apply-limits", workspace=ws, payload=payload, repo=repo, vault=vault, paydir=paydir)
        assert r.returncode == 2, r.stdout + r.stderr
        err = json.loads(r.stdout)
        assert err["status"] == "error"
        assert err["rollback_result"]["marker_cleared"] is True
        assert err["rollback_result"]["working_tree_restored"] is True

    def test_runner_apply_limits_rejects_proposal_outside_strategy_approvals(self, tmp_path):
        ws, repo, vault, paydir = self._setup(tmp_path)
        proposal = _limits_proposal(repo / "outside.md")
        payload = _limits_payload(proposal, config=repo / "execution" / "validators" / "config.yaml")
        r = self._run("apply-limits", workspace=ws, payload=payload, repo=repo, vault=vault, paydir=paydir)
        assert r.returncode == 2, r.stdout + r.stderr
        err = json.loads(r.stdout)
        assert "strategy-approvals" in err["message"]

    def test_runner_apply_limits_rejects_unknown_required_primary(self, tmp_path):
        # CP2 r1 fix G: an unknown review provider must fail fast on the capital
        # path, not late inside the K2Bi review.
        ws, repo, vault, paydir = self._setup(tmp_path)
        proposal = _limits_proposal(repo / "review" / "strategy-approvals")
        payload = _limits_payload(proposal)
        payload["required_primary"] = "bogus"
        r = self._run("apply-limits", workspace=ws, payload=payload, repo=repo, vault=vault, paydir=paydir)
        assert r.returncode == 2, r.stdout + r.stderr
        err = json.loads(r.stdout)
        assert "required_primary" in err["message"]


class TestA4PollAndWorker:
    def test_poll_once_cancels_out_of_order_apply_child(self, store, tmp_path, monkeypatch):
        monkeypatch.setattr(store, "notify", lambda *a, **k: None)
        repo = tmp_path / "k2bi"
        _init_git_repo(repo)
        parent = _a4_parent(store)
        assert store.a1_resume_action(parent) == "author_limits_proposal"
        child = store.add_task(
            assignee_profile="k2bi",
            command_key="k2bi-apply-limits",
            success_criteria="out of order",
            permissions="analyst-command",
            flight_id=parent,
            parent_task=parent,
            entity_key="instrument_whitelist-add-CDNS",
            status="ready",
            payload=_limits_payload(_limits_proposal(repo / "review" / "strategy-approvals")),
        )
        store.poll_once()
        assert store.get_task(child)["status"] == "cancelled"

    def test_poll_once_admits_apply_child_at_verify_limits(self, store, tmp_path, monkeypatch):
        monkeypatch.setattr(store, "notify", lambda *a, **k: None)
        repo = tmp_path / "k2bi"
        _init_git_repo(repo)
        vault = tmp_path / "k2bi-vault"
        vault.mkdir(exist_ok=True)
        monkeypatch.setenv("K2B_ORCH_K2BI_WORKSPACE", str(repo))
        monkeypatch.setenv("K2BI_VAULT_PATH", str(vault))
        proposal = _limits_proposal(repo / "review" / "strategy-approvals")
        parent = _recorded_authorized_parent(store, repo, proposal)
        ok, _dispatch = store.a4_mark_limits_dispatch_started(parent)
        assert ok
        assert store.a1_resume_action(parent) == "verify_limits"
        child = store.add_task(
            assignee_profile="k2bi",
            command_key="k2bi-apply-limits",
            success_criteria="in-flight apply",
            permissions="analyst-command",
            flight_id=parent,
            parent_task=parent,
            entity_key="instrument_whitelist-add-CDNS",
            status="ready",
            payload=_limits_payload(proposal),
        )
        store.poll_once()
        t = store.get_task(child)
        assert not (
            t["status"] == "cancelled" and "out of order" in (t.get("blocker_reason") or "")
        )

    def test_apply_limits_uses_capital_timeout_env(self, store, tmp_path, monkeypatch):
        from scripts.lib import orchestrator_worker as worker

        tid = store.add_task(
            assignee_profile="k2bi",
            command_key="k2bi-apply-limits",
            success_criteria="apply limits",
            permissions="analyst-command",
            payload={"payload_path": "/tmp/k2b-orchestrator/limits.json"},
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


def _write_a4_workspace(ws: Path) -> None:
    lib = ws / "scripts" / "lib"
    lib.mkdir(parents=True)
    (ws / "scripts" / "__init__.py").write_text("", encoding="utf-8")
    (lib / "__init__.py").write_text("", encoding="utf-8")
    (lib / "invest_orchestrator_adapters.py").write_text(
        "from dataclasses import dataclass\n"
        "from pathlib import Path\n"
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
        "class LimitsApproval:\n"
        "    final_approval_token: str\n"
        "    approved_by: str\n"
        "    approved_at: str\n"
        "    apply_lease_id: str\n"
        "@dataclass(frozen=True)\n"
        "class LimitsApplyResult:\n"
        "    slug: str\n"
        "    approval: LimitsApproval\n"
        "    required_primary: str\n"
        "def apply_approved_limits(proposal_path, *, approval, required_primary='minimax', config_path=None, now_utc=None):\n"
        "    proposal_path = Path(proposal_path)\n"
        "    slug = proposal_path.stem.split('limits-proposal_', 1)[-1]\n"
        "    if 'rollback' in proposal_path.read_text(encoding='utf-8') or 'rollback' in slug:\n"
        "        rb = RollbackResult(str(proposal_path), 'abc', 'marker', True, True, True, ({'event':'rollback'},))\n"
        "        raise OrchestratorGateError('limits refused', rollback_result=rb)\n"
        "    if now_utc is not None:\n"
        "        raise AssertionError('K2B runner must leave now_utc to the adapter default')\n"
        "    if config_path is None:\n"
        "        raise AssertionError('K2B runner must bind config_path explicitly')\n"
        "    expected_config = (proposal_path.parents[2] / 'execution' / 'validators' / 'config.yaml').resolve()\n"
        "    if Path(config_path).resolve() != expected_config:\n"
        "        raise AssertionError(f'config_path must be the canonical validator file, got {config_path}')\n"
        "    return LimitsApplyResult(slug, approval, required_primary)\n",
        encoding="utf-8",
    )


class TestRealK2BiLimitsShape:
    @pytest.mark.skipif(
        not (K2BI_REPO / "scripts" / "lib" / "invest_orchestrator_adapters.py").exists(),
        reason="real K2Bi checkout not available",
    )
    def test_real_apply_limits_signature_and_approval_token_shape(self):
        script = f"""
import datetime as dt
import inspect
import sys
sys.path.insert(0, {str(K2BI_REPO)!r})
from scripts.lib import invest_orchestrator_adapters as ioa

sig = inspect.signature(ioa.apply_approved_limits)
assert "proposal_path" in sig.parameters
assert "approval" in sig.parameters
assert "config_path" in sig.parameters
assert "now_utc" in sig.parameters
assert "now" not in sig.parameters
assert set(ioa.LimitsApproval.__dataclass_fields__) == {{
    "final_approval_token",
    "approved_by",
    "approved_at",
    "apply_lease_id",
}}
approved_at = "2026-06-09T00:00:00+00:00"
now = dt.datetime.fromisoformat(approved_at)
proposal_sha = "a" * 64
config_sha = "b" * 64
lease = "instrument_whitelist-add-CDNS-limits-a1-20260609T000000Z"
approval = ioa.LimitsApproval(
    final_approval_token=(
        f"APPROVE_LIMITS:instrument_whitelist-add-CDNS:{{proposal_sha}}:"
        f"{{config_sha}}:{{approved_at}}:{{lease}}"
    ),
    approved_by="keith",
    approved_at=approved_at,
    apply_lease_id=lease,
)
ioa._validate_limits_approval(
    approval,
    "instrument_whitelist-add-CDNS",
    proposal_sha,
    config_sha,
    now_utc=now,
)
bad = ioa.LimitsApproval(
    final_approval_token=(
        f"APPROVE_LIMITS:instrument_whitelist-add-CDNS:{{proposal_sha}}:"
        f"{{'c' * 64}}:{{approved_at}}:{{lease}}"
    ),
    approved_by="keith",
    approved_at=approved_at,
    apply_lease_id=lease,
)
try:
    ioa._validate_limits_approval(
        bad,
        "instrument_whitelist-add-CDNS",
        proposal_sha,
        config_sha,
        now_utc=now,
    )
except ioa.OrchestratorGateError as exc:
    assert "config hash" in str(exc)
else:
    raise AssertionError("wrong config sha unexpectedly passed")
"""
        proc = subprocess.run(
            [sys.executable, "-c", script],
            cwd=str(K2BI_REPO),
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr
