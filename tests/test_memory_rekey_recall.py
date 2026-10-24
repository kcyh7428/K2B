from pathlib import Path
import hashlib
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts/lib'))
import automatic_memory as memory
import automatic_memory_key_resolution as resolution
import eod_capture as capture
from test_memory_key_resolution import collision
from test_automatic_memory_publisher import setup, ready, publisher
from test_automatic_memory import _bundle


def publish_pair(tmp_path, home):
    vault, _ = setup(tmp_path / 'publication')
    publisher().publish(home / 'memory.json', home, vault, 'home', preflight=ready)
    return vault


def recall_pair(tmp_path, home, sjm):
    return memory.recall(home / 'memory.json', 'cohort.review', provisional_outbox=sjm,
        read_only=True, resolution_note=tmp_path / 'publication/vault/wiki/context/context_automatic-memory-recall.md')


def setup_rekeyed_assertion(tmp_path):
    home = tmp_path / 'home'
    _, decision = collision(home)
    plan = resolution.plan(home, decision['delivery_id'], 'cohort.review', 'Separate cohort calculation and decision.')
    resolution.record(home, plan, expected_sha256=hashlib.sha256(memory._canonical_json(plan)).hexdigest(), writer_role='home')
    capture.reconcile_memory_delivery(home, decision['delivery_id'], writer_role='home')
    sjm = tmp_path / 'sjm'
    queued = memory.enqueue_bundle(sjm, decision['bundle'])
    memory.record_delivery_attempt(sjm, queued['delivery_id'], outcome='accepted')
    publish_pair(tmp_path, home)
    return home, sjm, decision


def test_sjm_overlay_recognizes_exact_published_assertion_after_key_resolution(tmp_path, monkeypatch):
    home, sjm, _ = setup_rekeyed_assertion(tmp_path)
    vault = tmp_path / 'publication/vault'
    monkeypatch.setenv('K2B_VAULT_PATH', str(vault))
    result = capture.recall_memory(sjm, 'cohort.review', writer_role='sjm-source-only', memory_path=vault / 'System/memory/automatic-memory-current.json')
    assert result['status'] == 'current'
    assert result['kind'] == 'fact'
    assert result['citation']['event_id']
    assert not result.get('pending_home')
    assert result['rekeyed_outbox_items'][0]['new_key'] == 'cohort.review.decision'
    assert result['rekeyed_outbox_items'][0]['original_item_id']
    assert result['rekeyed_outbox_items'][0]['published_item_id']


@pytest.mark.parametrize('change', ['value', 'evidence_quote', 'evidence_event_id'])
def test_different_assertion_still_remains_pending_after_key_resolution(tmp_path, change):
    from copy import deepcopy
    home, sjm, decision = setup_rekeyed_assertion(tmp_path)
    bundle = deepcopy(decision['bundle'])
    bundle['items'][0][change] += '-different'
    memory.enqueue_bundle(sjm, bundle)
    result = recall_pair(tmp_path, home, sjm)
    assert result['status'] == 'unresolved'
    assert result['pending_home']


def test_identical_assertion_under_unrelated_key_is_not_a_resolution(tmp_path):
    from copy import deepcopy
    home = tmp_path / 'home'
    fact = memory.enqueue_bundle(home, _bundle(key='cohort.review'))
    capture.accept_memory_envelope(home, fact, writer_role='home')
    capture.reconcile_memory_delivery(home, fact['delivery_id'], writer_role='home')
    decision = _bundle(key='cohort.review', kind='decision', cursor='turn:0002')
    unrelated = deepcopy(decision)
    unrelated['items'][0]['key'] = 'unrelated.review'
    queued = memory.enqueue_bundle(home, unrelated)
    capture.accept_memory_envelope(home, queued, writer_role='home')
    capture.reconcile_memory_delivery(home, queued['delivery_id'], writer_role='home')
    sjm = tmp_path / 'sjm'
    memory.enqueue_bundle(sjm, decision)
    publish_pair(tmp_path, home)
    result = recall_pair(tmp_path, home, sjm)
    assert result['status'] == 'unresolved'
    assert result['pending_home']


@pytest.mark.parametrize('damage', ['remove', 'snapshot', 'item', 'anchor'])
def test_unverified_shared_proof_never_suppresses_pending_item(tmp_path, damage):
    import automatic_memory_proofs as proofs
    import json
    home, sjm, _ = setup_rekeyed_assertion(tmp_path)
    note = tmp_path / 'publication/vault/wiki/context/context_automatic-memory-recall.md'
    text = note.read_text()
    header, encoded = text.split(proofs.START, 1)
    data = json.loads(encoded.split(proofs.END, 1)[0])
    if damage == 'remove':
        note.write_text(header)
    else:
        if damage == 'snapshot':
            data['snapshot_sha256'] = '0' * 64
        elif damage == 'item':
            data['resolutions'][0]['original_item_id'] = 'item:' + '0' * 64
        else:
            data['resolutions'][0]['anchor_item_id'] = 'item:' + '0' * 64
        note.write_text(header + proofs.START + json.dumps(data) + proofs.END)
    result = recall_pair(tmp_path, home, sjm)
    assert result['status'] == 'unresolved'
    assert result['pending_home']
    assert result['alternatives'][0]['key_resolution_proof_state'] != 'verified'


def test_published_home_recall_does_not_create_a_vault_lock(tmp_path, monkeypatch):
    home, sjm, _ = setup_rekeyed_assertion(tmp_path)
    vault = tmp_path / 'publication/vault'
    monkeypatch.setenv('K2B_VAULT_PATH', str(vault))
    target = vault / 'System/memory/automatic-memory-current.json'
    lock = target.with_name('.' + target.name + '.lock')
    assert not lock.exists()
    capture.recall_memory(sjm, 'cohort.review', writer_role='home', memory_path=target)
    assert not lock.exists()


def test_home_local_overlay_uses_published_proof_for_exact_snapshot(tmp_path, monkeypatch):
    home, sjm, _ = setup_rekeyed_assertion(tmp_path)
    monkeypatch.setenv('K2B_VAULT_PATH', str(tmp_path / 'publication/vault'))
    result = capture.recall_memory(home, 'cohort.review', writer_role='home',
                                  provisional_root=sjm)
    assert result['status'] == 'current'
    assert not result.get('pending_home')
    assert result['key_resolution_proof_state'] == 'verified'
    assert result['rekeyed_outbox_items'][0]['new_key'] == 'cohort.review.decision'


def test_home_local_overlay_keeps_pending_when_published_proof_is_stale(tmp_path, monkeypatch):
    home, sjm, _ = setup_rekeyed_assertion(tmp_path)
    monkeypatch.setenv('K2B_VAULT_PATH', str(tmp_path / 'publication/vault'))
    queued = memory.enqueue_bundle(home, _bundle(key='another.key', cursor='turn:0004'))
    capture.accept_memory_envelope(home, queued, writer_role='home')
    capture.reconcile_memory_delivery(home, queued['delivery_id'], writer_role='home')
    result = capture.recall_memory(home, 'cohort.review', writer_role='home',
                                  provisional_root=sjm)
    assert result['status'] == 'unresolved'
    assert result['pending_home']
    assert result['key_resolution_proof_state'] != 'verified'


def test_missing_original_anchor_is_explicit_and_remains_pending(tmp_path):
    import automatic_memory_proofs as proofs
    import json
    home, sjm, _ = setup_rekeyed_assertion(tmp_path)
    path = home / 'memory.json'
    data = json.loads(path.read_text())
    del data['records']['cohort.review']
    path.write_text(json.dumps(data))
    note = tmp_path / 'publication/vault/wiki/context/context_automatic-memory-recall.md'
    text = note.read_text()
    before, encoded = text.split(proofs.START, 1)
    metadata = json.loads(encoded.split(proofs.END, 1)[0])
    metadata['snapshot_sha256'] = proofs.snapshot_hash(memory.memory_snapshot(path))
    note.write_text(before + proofs.START + json.dumps(metadata) + proofs.END)
    result = recall_pair(tmp_path, home, sjm)
    assert result['status'] == 'pending_home'
    assert result['key_resolution_proof_state'] == 'anchor_missing'


def test_mixed_pending_result_keeps_resolved_proof_visible(tmp_path):
    home, sjm, _ = setup_rekeyed_assertion(tmp_path)
    memory.enqueue_bundle(sjm, _bundle(key='cohort.review', kind='decision', value='A later decision.',
        quote='A later decision.', event_id='event-user-new', cursor='turn:0003'))
    result = recall_pair(tmp_path, home, sjm)
    assert result['status'] == 'unresolved'
    assert result['pending_home']
    assert len(result['rekeyed_outbox_items']) == 1


def test_explicit_snapshot_outside_configured_vault_is_read_only_and_diagnosed(tmp_path, monkeypatch):
    home, sjm, _ = setup_rekeyed_assertion(tmp_path)
    original = tmp_path / 'publication/vault/System/memory/automatic-memory-current.json'
    target = tmp_path / 'other/automatic-memory-current.json'
    target.parent.mkdir()
    target.write_bytes(original.read_bytes())
    monkeypatch.setenv('K2B_VAULT_PATH', str(tmp_path / 'different-vault'))
    result = capture.recall_memory(sjm, 'cohort.review', writer_role='home', memory_path=target)
    assert result['key_resolution_proof_state'] == 'outside_configured_vault'
    assert not target.with_name('.' + target.name + '.lock').exists()


def test_publisher_rejects_proof_serialization_drift_before_shared_writes(tmp_path, monkeypatch):
    import automatic_memory_proofs as proofs
    vault, state = setup(tmp_path)
    monkeypatch.setattr(proofs, 'snapshot_hash', lambda state: '0' * 64)
    with pytest.raises(ValueError, match='serialization'):
        publisher().publish(state / 'memory.json', state, vault, 'home', preflight=ready)
    assert not (vault / 'System').exists()
