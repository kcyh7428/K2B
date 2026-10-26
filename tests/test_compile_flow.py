from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from test_knowledge_integration import (home, proposal, record, save_targets, sha,
    snapshot, tree, vault_env, original_vault_env)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts/lib"))
from raw_source_status import classify


def load(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


context = load("compile_context_test", "scripts/compile-context.py")
raw = load("raw_queue_test", "scripts/raw-needs-compile.py")
WORK = "wiki/work/work_kingdee-hris-recovery.md"


@pytest.mark.parametrize("path,metadata,expected", [
    ("raw/orchestrator-results/a.md", "", "technical_record"),
    ("raw/orchestrator-inbox/a.md", "", "technical_record"),
    ("raw/tldrs/a.md", "type: orchestrator-result\n", "technical_record"),
    ("raw/sessions/handoff_orchestrator.md", "type: handoff\n", "human_capture"),
    ("raw/research/orchestrator.md", "type: research\ntags: [orchestrator-of-agents]\n", "human_capture"),
])
def test_classification_uses_ownership_not_topic_words(path, metadata, expected):
    text = "---\n" + metadata + "compiled: false\n---\nSource.\n" if metadata else "Source."
    assert classify(path, text)["classification"] == expected


@pytest.mark.parametrize("text,state,compiled", [
    ("\ufeff\r\n---\r\ncompiled: true\r\n---\r\nBody", "parsed", "true"),
    ("No metadata", "missing", None),
    ("---\ncompiled: [\n---\n", "unreadable", None),
    ("---\ncompiled: [false]\n---\n", "unreadable", None),
    ("---\n- scalar\n---\n", "unreadable", None),
    ("---\ncompiled: false\n", "unreadable", None),
])
def test_metadata_states(text, state, compiled):
    result = classify("raw/tldrs/a.md", text)
    assert (result["metadata_state"], result["compiled"]) == (state, compiled)
    assert result["legacy_completed"] == (compiled == "true")


def write_raw(env, text="Exact synthetic quotation.", rel="raw/research/source.md", metadata="type: research\ncompiled: false\n"):
    path = env["vault"] / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("---\n" + metadata + "---\n" + text)
    return path


@pytest.mark.parametrize("leaf", [False, True])
def test_raw_receipt_queue_and_replay_preserve_source(tmp_path, vault_env, home, leaf):
    env, data = vault_env, snapshot()
    path = write_raw(env)
    before = path.read_bytes()
    item = proposal(env, data, leaf=leaf)
    item["sources"] = [{"kind": "raw_capture", "raw_path": path.relative_to(env["vault"]).as_posix(),
        "raw_sha256": sha(before), "classification": "human_capture", "speaker": "keith",
        "evidence_quote": "Exact synthetic quotation."}]
    outputs = [] if leaf else save_targets(tmp_path, env, data, item)
    recorded = record(env, data, item, outputs)
    receipts = raw.read_receipts(env["state"] / "digestion")
    assert len(receipts) == 1
    assert raw.iter_raw_notes(env["vault"], 0, receipts=receipts) == []
    rows = raw.iter_raw_notes(env["vault"], 0, receipts=receipts, include_all=True)
    assert rows[0]["state"] == ("retained_leaf" if leaf else "integrated")
    assert record(env, data, item, outputs)["status"] == "already-recorded"
    assert path.read_bytes() == before
    path.write_bytes(before + b"\nChanged synthetic source.")
    assert raw.iter_raw_notes(env["vault"], 0, receipts=receipts)[0]["state"] == "needs_review"
    Path(recorded["receipt_path"]).write_text("{}")
    with pytest.raises(RuntimeError, match="binding"):
        raw.read_receipts(env["state"] / "digestion")


def test_raw_technical_legacy_and_bad_metadata_are_explicit(vault_env):
    env = vault_env
    write_raw(env, rel="raw/orchestrator-results/a.md")
    for flag in ("skipped", "deferred", "true", "false"):
        write_raw(env, rel="raw/tldrs/" + flag + ".md", metadata="compiled: " + flag + "\n")
    write_raw(env, rel="raw/tldrs/bad.md", metadata="compiled: [false]\n")
    before = tree(env["vault"])
    rows = raw.iter_raw_notes(env["vault"], 0)
    assert {Path(row["path"]).stem for row in rows} == {"false", "bad"}
    all_rows = raw.iter_raw_notes(env["vault"], 0, include_all=True)
    assert len(all_rows) == 6
    assert all(row["state"] == "blocked" for row in all_rows if not row["path"].endswith("/false.md"))
    assert all(row["action"] == "manual review" for row in all_rows if row["state"] == "blocked")
    assert all(not row["state"] == "integrated" for row in all_rows)
    assert before == tree(env["vault"])


def test_oversized_raw_is_blocked_without_unusable_compile_action(vault_env, monkeypatch):
    path = write_raw(vault_env)
    before = path.read_bytes()
    monkeypatch.setattr(raw, "MAX_RAW_BYTES", 10)
    row = raw.iter_raw_notes(vault_env["vault"], 0)[0]
    assert row["state"] == "blocked" and row["action"] == "manual review"
    assert row["source_sha256"] is None
    assert before == path.read_bytes()


def test_human_orchestrator_research_validates(vault_env):
    env, data = vault_env, snapshot()
    path = write_raw(env, rel="raw/research/orchestrator-framework.md",
        metadata="type: research\ntags: [orchestrator-of-agents]\n")
    item = proposal(env, data, leaf=True)
    item["sources"] = [{"kind": "raw_capture", "raw_path": path.relative_to(env["vault"]).as_posix(),
        "raw_sha256": sha(path.read_bytes()), "classification": "human_capture", "speaker": "keith",
        "evidence_quote": "Exact synthetic quotation."}]
    assert raw.integration.validate_proposal(item, vault_root=env["vault"], snapshot=data)


def test_context_reads_current_body_and_hashes_without_writes(vault_env):
    env = vault_env
    path = write_raw(env, metadata='type: research\nrelated: ["[[work_kingdee-hris-recovery]]"]\n')
    before = tree(env["vault"]), tree(env["state"])
    packet = context.collect(env["vault"], str(path), [])
    assert not packet["candidate_only"]
    assert packet["source"]["sha256"] == sha(path.read_bytes())
    assert packet["source"]["text"] == path.read_text()
    note = packet["current_notes"][0]
    assert note["path"] == WORK
    assert note["expected_sha256"] == sha((env["vault"] / WORK).read_bytes())
    assert "Kingdee" in note["excerpt"]
    assert before == (tree(env["vault"]), tree(env["state"]))


@pytest.mark.parametrize("target", ["../escape", "wiki/context/private", "missing-note", "wiki/work/index.md"])
def test_context_refuses_unsafe_or_missing_targets(vault_env, target):
    path = write_raw(vault_env)
    with pytest.raises(ValueError):
        context.collect(vault_env["vault"], str(path), [target])


def test_context_target_symlink_and_ambiguity_refused(vault_env):
    env = vault_env
    path = write_raw(env)
    duplicate = env["vault"] / "wiki/people/work_kingdee-hris-recovery.md"
    duplicate.write_bytes((env["vault"] / WORK).read_bytes())
    with pytest.raises(ValueError):
        context.collect(env["vault"], str(path), ["work_kingdee-hris-recovery"])
    duplicate.unlink()
    duplicate.symlink_to(env["vault"] / WORK)
    with pytest.raises(ValueError):
        context.collect(env["vault"], str(path), [duplicate.relative_to(env["vault"]).as_posix()])


def test_context_never_reads_a_symlinked_index_leaf(vault_env, tmp_path):
    env = vault_env
    path = write_raw(env)
    index = env["vault"] / "wiki/index.md"
    index.unlink()
    outside = tmp_path / "outside.txt"
    outside.write_text("Synthetic out-of-vault data must not enter context")
    index.symlink_to(outside)
    with pytest.raises(ValueError, match="symlink"):
        context.collect(env["vault"], str(path), [])


def test_context_budgets_and_discovery_only(vault_env, monkeypatch):
    env = vault_env
    path = write_raw(env)
    packet = context.collect(env["vault"], str(path), [])
    assert packet["candidate_only"] and packet["current_notes"] == []
    monkeypatch.setattr(context, "MAX_NOTE_EXCERPT_BYTES", 20)
    packet = context.collect(env["vault"], str(path), [WORK])
    assert packet["current_notes"][0]["truncated"]
    assert packet["current_notes"][0]["expected_sha256"] == sha((env["vault"] / WORK).read_bytes())
    with pytest.raises(ValueError, match="three"):
        context.collect(env["vault"], str(path), [WORK] * 4)
    monkeypatch.setattr(context, "MAX_CONTEXT_BYTES", 10)
    with pytest.raises(ValueError, match="combined"):
        context.collect(env["vault"], str(path), [])
    path.write_bytes(b"a" * (context.MAX_RAW_BYTES + 1))
    with pytest.raises(ValueError, match="byte budget"):
        context.collect(env["vault"], str(path), [])


def test_context_only_wrapper_has_no_provider_requirement(vault_env):
    env = vault_env
    path = write_raw(env)
    result = subprocess.run(["bash", str(ROOT / "scripts/minimax-compile.sh"), "--context-only", str(path), WORK],
        env={**env["env"], "K2B_LLM_PROVIDER": "disabled", "K2B_PYTHON": sys.executable, "K2B_VAULT_PATH": str(env["vault"])},
        capture_output=True)
    assert result.returncode == 0, result.stderr.decode()
    assert json.loads(result.stdout)["current_notes"][0]["path"] == WORK


@pytest.mark.parametrize("mode,accepted", [("valid", True), ("new-path", False), ("wrong-hash", False),
    ("create", False), ("candidate-update", False), ("empty", False)])
def test_worker_rejects_out_of_context_proposals_without_live_api(tmp_path, mode, accepted):
    wrapper = tmp_path / "minimax-compile.sh"
    wrapper.write_bytes((ROOT / "scripts/minimax-compile.sh").read_bytes())
    (tmp_path / "compile-context.py").write_text('import os\nprint(os.environ["FIXTURE_CONTEXT"])\n')
    (tmp_path / "minimax-common.sh").write_text('K2B_LLM_MODEL=synthetic\nmm_api() { printf "%s" "$FIXTURE_RESPONSE"; }\n')
    packet = {"candidate_only": mode == "candidate-update", "current_notes": [{"path": WORK, "expected_sha256": "a" * 64}]}
    update = {"path": WORK, "expected_sha256": "a" * 64}
    if mode == "new-path":
        update["path"] = "wiki/work/invented.md"
    if mode == "wrong-hash":
        update["expected_sha256"] = "b" * 64
    proposal = {"pages_to_update": [update], "pages_to_create": [{}] if mode == "create" else []}
    content = "" if mode == "empty" else json.dumps(proposal)
    response = {"choices": [{"message": {"content": content}}]}
    result = subprocess.run(["bash", str(wrapper), "synthetic.md"], capture_output=True,
        env={**os.environ, "K2B_PYTHON": sys.executable, "FIXTURE_CONTEXT": json.dumps(packet), "FIXTURE_RESPONSE": json.dumps(response)})
    assert (result.returncode == 0) == accepted
    if not accepted:
        assert result.stdout == b""
