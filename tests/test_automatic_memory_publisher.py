"""Exercise the real publisher with synthetic vaults; no live host mutations."""
from __future__ import annotations

import fcntl
import hashlib
import importlib
import json
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "lib"))
import automatic_memory
import eod_capture
from test_automatic_memory import _bundle


def publisher():
    assert importlib.util.find_spec("automatic_memory_publisher"), "safe publisher is missing"
    return importlib.import_module("automatic_memory_publisher")


def setup(tmp_path):
    vault, state = tmp_path / "vault", tmp_path / "state"
    (vault / "wiki/context").mkdir(parents=True)
    (vault / "wiki/context/index.md").write_text(
        "# Context\nLast updated: 2026-09-01 | Entries: 0\n\n## K2B System\n\n"
        "| Page | Summary | Updated |\n|------|---------|---------|\n")
    (vault / "wiki/index.md").write_text(
        "# Wiki\n| Folder | Purpose | Entries |\n|---|---|---|\n"
        "| [context/](context/index.md) | Context | 0 |\n\n**Total wiki pages: 0**\n")
    (vault / "wiki/log.md").write_text("# Log\n")
    (vault / "wiki/context/policy-ledger.jsonl").write_text(
        '{"type":"guard","scope":"*","action":"read_state","rule":"Poll before acting."}\n')
    envelope = automatic_memory.enqueue_bundle(state, _bundle())
    eod_capture.accept_memory_envelope(state, envelope, writer_role="home")
    eod_capture.reconcile_memory_delivery(state, envelope["delivery_id"], writer_role="home")
    return vault, state


def ready(vault):
    return {"policy_ledger_sha256": hashlib.sha256(
        (vault / "wiki/context/policy-ledger.jsonl").read_bytes()).hexdigest(),
        "syncthing": "idle"}


def test_publication_produces_indexed_cited_artifacts_and_idempotent_receipt(tmp_path):
    vault, state = setup(tmp_path)
    p = publisher()
    result = p.publish(state / "memory.json", state, vault, "home", preflight=ready)
    assert result["status"] == "published"
    shared = vault / "System/memory/automatic-memory-current.json"
    assert automatic_memory.recall(shared, "fact.office", read_only=True)["value"] == "The SJM office is in Macau."
    note = (vault / "wiki/context/context_automatic-memory-recall.md").read_text()
    assert "event-user-1" in note and "The SJM office is in Macau." in note
    index = (vault / "wiki/context/index.md").read_text()
    assert "[[context_automatic-memory-recall]]" in index and "Entries: 1" in index
    assert "| Context | 1 |" in (vault / "wiki/index.md").read_text()
    before = {str(f): f.read_bytes() for f in vault.rglob("*") if f.is_file()}
    repeat = p.publish(state / "memory.json", state, vault, "home", preflight=ready)
    assert repeat["duplicate"] is True
    assert before == {str(f): f.read_bytes() for f in vault.rglob("*") if f.is_file()}
    assert result["snapshot_sha256"] == hashlib.sha256(shared.read_bytes()).hexdigest()


@pytest.mark.parametrize("role", ["sjm-source-only", "unknown"])
def test_sjm_cannot_publish_even_with_injected_healthy_preflight(tmp_path, role):
    vault, state = setup(tmp_path)
    with pytest.raises(PermissionError):
        publisher().publish(state / "memory.json", state, vault, role, preflight=ready)
    assert not (vault / "System").exists()


def test_live_publication_without_activation_authority_is_blocked(tmp_path):
    vault, state = setup(tmp_path)
    with pytest.raises(PermissionError):
        publisher().publish(state / "memory.json", state, vault, "home")
    assert not (vault / "System").exists()


def test_unhealthy_preflight_and_concurrent_owner_cannot_publish(tmp_path):
    vault, state = setup(tmp_path)
    p = publisher()
    with pytest.raises(PermissionError):
        p.publish(state / "memory.json", state, vault, "home", preflight=lambda v: {**ready(v), "syncthing": "syncing"})
    with (state / ".publication.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(BlockingIOError):
            p.publish(state / "memory.json", state, vault, "home", preflight=ready)
    assert not (vault / "System").exists()


def test_unreconciled_acceptance_and_tampered_receipt_block_publication(tmp_path):
    vault, state = setup(tmp_path)
    p = publisher()
    receipt = next((state / "reconciliation").glob("*.json"))
    original = receipt.read_bytes()
    receipt.unlink()
    with pytest.raises(ValueError, match="reconcil"):
        p.publish(state / "memory.json", state, vault, "home", preflight=ready)
    receipt.write_bytes(original)
    body = json.loads(original)
    body["content_id"] = "bad"
    receipt.write_text(json.dumps(body))
    with pytest.raises(ValueError, match="reconcil"):
        p.publish(state / "memory.json", state, vault, "home", preflight=ready)
    assert not (vault / "System").exists()


def test_log_failure_has_no_success_receipt_and_retry_does_not_duplicate(tmp_path, monkeypatch):
    vault, state = setup(tmp_path)
    p = publisher()
    log = p._append_log
    monkeypatch.setattr(p, "_append_log", lambda *a: (_ for _ in ()).throw(OSError("disk failure")))
    with pytest.raises(OSError):
        p.publish(state / "memory.json", state, vault, "home", preflight=ready)
    assert not list((state / "publication").glob("*.json"))
    monkeypatch.setattr(p, "_append_log", log)
    result = p.publish(state / "memory.json", state, vault, "home", preflight=ready)
    assert result["status"] == "published"
    assert (vault / "wiki/log.md").read_text().count(result["snapshot_sha256"]) == 1


def test_receipt_tampering_and_symlink_escape_fail_closed(tmp_path):
    vault, state = setup(tmp_path)
    p = publisher()
    p.publish(state / "memory.json", state, vault, "home", preflight=ready)
    receipt = next((state / "publication").glob("*.json"))
    receipt.write_text('{}')
    with pytest.raises(ValueError, match="receipt"):
        p.publish(state / "memory.json", state, vault, "home", preflight=ready)
    fresh_vault, fresh_state = setup(tmp_path / "fresh")
    escape = tmp_path / "escape"
    escape.mkdir()
    (fresh_vault / "System").symlink_to(escape, target_is_directory=True)
    with pytest.raises(PermissionError):
        p.publish(fresh_state / "memory.json", fresh_state, fresh_vault, "home", preflight=ready)
    assert list(escape.iterdir()) == []


def test_publisher_preserves_existing_user_edits_and_refuses_shared_local_state(tmp_path):
    vault, state = setup(tmp_path)
    p = publisher()
    p.publish(state / "memory.json", state, vault, "home", preflight=ready)
    note = vault / "wiki/context/context_automatic-memory-recall.md"
    note.write_text("Keith's manual edits must survive.\n")
    with pytest.raises(PermissionError, match="modified"):
        p.publish(state / "memory.json", state, vault, "home", preflight=ready)
    assert note.read_text() == "Keith's manual edits must survive.\n"
    with pytest.raises(PermissionError, match="local"):
        p.publish(state / "memory.json", vault / "System/state", vault, "home", preflight=ready)
    assert not (vault / "System/state").exists()


def test_note_has_context_frontmatter_and_snapshot_identity(tmp_path):
    vault, state = setup(tmp_path)
    result = publisher().publish(state / "memory.json", state, vault, "home", preflight=ready)
    note = (vault / "wiki/context/context_automatic-memory-recall.md").read_text()
    for field in ('type: context', 'origin: k2b-extract', 'date: 2026-09-09', 'up: "[[index]]"'):
        assert field in note
    assert result["snapshot_sha256"] in note


def test_failure_between_artifacts_leaves_no_receipt_and_replay_repairs(tmp_path, monkeypatch):
    vault, state = setup(tmp_path)
    p = publisher()
    original = eod_capture._atomic_write_text
    def fail_note(path, text):
        if path.name == "context_automatic-memory-recall.md":
            raise OSError("write interrupted")
        return original(path, text)
    monkeypatch.setattr(eod_capture, "_atomic_write_text", fail_note)
    with pytest.raises(OSError):
        p.publish(state / "memory.json", state, vault, "home", preflight=ready)
    assert (vault / "System/memory/automatic-memory-current.json").is_file()
    assert not list((state / "publication").glob("*.json"))
    monkeypatch.setattr(eod_capture, "_atomic_write_text", original)
    assert p.publish(state / "memory.json", state, vault, "home", preflight=ready)["status"] == "published"


def test_canonical_vault_ownership_excludes_a_second_state_root(tmp_path, monkeypatch):
    vault, state = setup(tmp_path)
    unused_vault, second_state = setup(tmp_path / "second")
    p = publisher()
    original = eod_capture._atomic_write_text
    outcomes = []
    def overlap(path, text):
        if path == vault / "System/memory/automatic-memory-current.json" and not outcomes:
            outcomes.append("started")
            try:
                p.publish(second_state / "memory.json", second_state, vault, "home", preflight=ready)
            except BlockingIOError:
                outcomes.append("blocked")
            else:
                outcomes.append("overlapped")
        original(path, text)
    monkeypatch.setattr(eod_capture, "_atomic_write_text", overlap)
    assert p.publish(state / "memory.json", state, vault, "home", preflight=ready)["status"] == "published"
    assert outcomes == ["started", "blocked"]


def test_interrupted_publication_recovers_after_newer_memory_arrives(tmp_path, monkeypatch):
    vault, state = setup(tmp_path)
    p = publisher()
    original = eod_capture._atomic_write_text
    def fail_note(path, text):
        if path.name == "context_automatic-memory-recall.md":
            raise OSError("interrupted before note")
        original(path, text)
    monkeypatch.setattr(eod_capture, "_atomic_write_text", fail_note)
    with pytest.raises(OSError):
        p.publish(state / "memory.json", state, vault, "home", preflight=ready)
    monkeypatch.setattr(eod_capture, "_atomic_write_text", original)
    newer = automatic_memory.enqueue_bundle(state, _bundle(
        completed_at="2026-09-10T10:00:00+08:00", cursor="turn:0002",
        value="The office is now Green.", quote="The office is now Green."))
    eod_capture.accept_memory_envelope(state, newer, writer_role="home")
    eod_capture.reconcile_memory_delivery(state, newer["delivery_id"], writer_role="home")
    result = p.publish(state / "memory.json", state, vault, "home", preflight=ready)
    assert result["status"] == "published"
    assert "The office is now Green." in (vault / "wiki/context/context_automatic-memory-recall.md").read_text()


def test_additional_duplicate_provenance_advances_watermark_without_failure(tmp_path):
    vault, state = setup(tmp_path)
    p = publisher()
    first = p.publish(state / "memory.json", state, vault, "home", preflight=ready)
    duplicate = automatic_memory.enqueue_bundle(state, _bundle(thread_source="desktop"))
    eod_capture.accept_memory_envelope(state, duplicate, writer_role="home")
    assert eod_capture.reconcile_memory_delivery(state, duplicate["delivery_id"], writer_role="home")["duplicates"] == 1
    second = p.publish(state / "memory.json", state, vault, "home", preflight=ready)
    assert first["snapshot_sha256"] == second["snapshot_sha256"]
    assert len(second["reconciliation_watermark"]) == 2
    assert p.publish(state / "memory.json", state, vault, "home", preflight=ready)["duplicate"] is True
    assert (vault / "wiki/log.md").read_text().count(second["snapshot_sha256"]) == 1


def test_vault_lock_does_not_depend_on_process_temp_directory(tmp_path, monkeypatch):
    vault, state = setup(tmp_path)
    _, second_state = setup(tmp_path / "second")
    p = publisher()
    original = eod_capture._atomic_write_text
    outcomes = []
    def overlap(path, text):
        if path == vault / "System/memory/automatic-memory-current.json" and not outcomes:
            outcomes.append("started")
            monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path / "other-temp"))
            try:
                p.publish(second_state / "memory.json", second_state, vault, "home", preflight=ready)
            except BlockingIOError:
                outcomes.append("blocked")
            else:
                outcomes.append("overlapped")
        original(path, text)
    monkeypatch.setattr(eod_capture, "_atomic_write_text", overlap)
    p.publish(state / "memory.json", state, vault, "home", preflight=ready)
    assert outcomes == ["started", "blocked"]


def test_renderer_header_drift_cannot_receive_publication_success(tmp_path, monkeypatch):
    vault, state = setup(tmp_path)
    original = eod_capture.publish_shared_recall
    def drift(*args, **kwargs):
        writer = kwargs['write_func']
        kwargs['write_func'] = lambda rel, text: writer(rel, text.replace('status: generated\n', 'status: changed\n'))
        return original(*args, **kwargs)
    monkeypatch.setattr(eod_capture, 'publish_shared_recall', drift)
    with pytest.raises(ValueError, match='frontmatter'):
        publisher().publish(state/'memory.json', state, vault, 'home', preflight=ready)
    assert not (vault/'System').exists()


def test_index_helper_exit_is_a_catchable_failure_without_success(tmp_path):
    vault, state = setup(tmp_path)
    (vault/'wiki/context/index.md').write_text('# unrecognized index\n')
    with pytest.raises(OSError, match='index'):
        publisher().publish(state/'memory.json', state, vault, 'home', preflight=ready)
    assert not list((state/'publication').glob('*.json'))


def test_completed_pending_journal_is_retired_but_receipt_remains(tmp_path):
    vault, state = setup(tmp_path)
    publisher().publish(state/'memory.json', state, vault, 'home', preflight=ready)
    assert not list((state/'publication-pending').glob('*.json'))
    assert len(list((state/'publication').glob('*.json'))) == 1


def test_malformed_pending_journal_names_exact_failure_without_quarantine(tmp_path):
    vault, state = setup(tmp_path)
    pending = state/'publication-pending'/'bad.json'
    pending.parent.mkdir()
    pending.write_text(json.dumps({'status':'staged','publication':'x'}))
    with pytest.raises(ValueError, match='bad.json'):
        publisher().publish(state/'memory.json', state, vault, 'home', preflight=ready)
    assert pending.exists()
    assert not (vault/'System').exists()


def test_missing_log_is_detected_before_any_shared_artifact_write(tmp_path):
    vault, state = setup(tmp_path)
    (vault/'wiki/log.md').unlink()
    with pytest.raises(OSError, match='log'):
        publisher().publish(state/'memory.json', state, vault, 'home', preflight=ready)
    assert not (vault/'System').exists()


def test_shared_hub_edits_during_staging_are_merged_not_reverted(tmp_path):
    vault, state = setup(tmp_path)
    calls = 0
    def edit_during_staging(v):
        nonlocal calls
        calls += 1
        if calls == 2:
            index = v/'wiki/context/index.md'
            index.write_text(index.read_text()+'\nManual context detail to retain.\n')
            master = v/'wiki/index.md'
            master.write_text(master.read_text()+'\nManual master detail to retain.\n')
            log = v/'wiki/log.md'
            log.write_text(log.read_text()+'Manual log entry to retain.\n')
        return ready(v)
    publisher().publish(state/'memory.json', state, vault, 'home', preflight=edit_during_staging)
    assert 'Manual context detail to retain.' in (vault/'wiki/context/index.md').read_text()
    assert 'Manual master detail to retain.' in (vault/'wiki/index.md').read_text()
    assert 'Manual log entry to retain.' in (vault/'wiki/log.md').read_text()


def test_invalid_policy_bytes_are_an_explicit_gate_refusal(monkeypatch):
    p = publisher()
    # Fake only the read boundaries; this test never reads/writes live files.
    home = Path('/Users/keithmbpm2')
    vault = home/'Projects/K2B-Vault'
    monkeypatch.setattr(Path, 'home', staticmethod(lambda: home))
    monkeypatch.setattr(p.getpass, 'getuser', lambda: 'keithmbpm2')
    authority = {'schema_version':1, 'enabled':True, 'vault_root':str(vault),
                 'exclusive_paths':[p.JSON_REL,p.NOTE_REL], 'policy_ledger_sha256':'a'*64}
    def read_text(path, *args, **kwargs):
        assert path.name == 'publication-authority.json'
        return json.dumps(authority)
    def read_bytes(path):
        assert path.name == 'policy-ledger.jsonl'
        return b'not valid JSON'
    monkeypatch.setattr(Path, 'read_text', read_text)
    monkeypatch.setattr(Path, 'read_bytes', read_bytes)
    with pytest.raises(PermissionError, match='policy ledger is malformed'):
        p.live_preflight(vault)
