from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts/lib'))
import automatic_memory as memory
import eod_capture as capture
from test_automatic_memory import _bundle
from test_automatic_memory_publisher import setup, ready, publisher


def collision(state):
    fact = memory.enqueue_bundle(state, _bundle(key='cohort.review', kind='fact'))
    capture.accept_memory_envelope(state, fact, writer_role='home')
    capture.reconcile_memory_delivery(state, fact['delivery_id'], writer_role='home')
    decision = memory.enqueue_bundle(state, _bundle(key='cohort.review', kind='decision',
        value='Review everyone below 30.', quote='Review everyone below 30.', cursor='turn:0002'))
    capture.accept_memory_envelope(state, decision, writer_role='home')
    return fact, decision


def test_explicit_resolution_preserves_sources_and_is_receipted_and_publishable(tmp_path):
    import automatic_memory_key_resolution as resolution
    vault, state = setup(tmp_path)
    fact, decision = collision(state)
    original = (state / 'inbox' / (decision['delivery_id'] + '.json')).read_bytes()
    prior = (state / 'reconciliation' / (fact['delivery_id'] + '.json')).read_bytes()
    with pytest.raises(ValueError, match='cannot change kind'):
        capture.reconcile_memory_delivery(state, decision['delivery_id'], writer_role='home')
    plan = resolution.plan(state, decision['delivery_id'], 'cohort.review', 'Separate the review decision from the cohort calculation.')
    digest = hashlib.sha256(memory._canonical_json(plan)).hexdigest()
    resolution.record(state, plan, expected_sha256=digest, writer_role='home')
    result = capture.reconcile_memory_delivery(state, decision['delivery_id'], writer_role='home')
    assert result['key_resolution_sha256']
    assert memory.recall(state / 'memory.json', 'cohort.review')['kind'] == 'fact'
    recalled = memory.recall(state / 'memory.json', plan['new_key'])
    assert recalled['kind'] == 'decision'
    assert recalled['value'] == 'Review everyone below 30.'
    assert recalled['citation']['event_id'] == decision['bundle']['items'][0]['evidence_event_id']
    assert capture.reconcile_memory_delivery(state, decision['delivery_id'], writer_role='home')['duplicate']
    assert (state / 'inbox' / (decision['delivery_id'] + '.json')).read_bytes() == original
    assert (state / 'reconciliation' / (fact['delivery_id'] + '.json')).read_bytes() == prior
    result = publisher().publish(state / 'memory.json', state, vault, 'home', preflight=ready)
    assert result['status'] == 'published'
    shared = vault / 'System/memory/automatic-memory-current.json'
    assert memory.recall(shared, plan['new_key'], read_only=True)['kind'] == 'decision'
    # Receipt binds the exact resolution, and removal cannot bypass the gate.
    path = state / 'key-resolutions' / (decision['delivery_id'] + '.json')
    path.unlink()
    with pytest.raises(ValueError, match='resolution'):
        publisher().publish(state / 'memory.json', state, vault, 'home', preflight=ready)


@pytest.mark.parametrize('mutation', ['new_key', 'content_id', 'item_id', 'existing_kind', 'reason'])
def test_resolution_rejects_stale_or_tampered_plan(tmp_path, mutation):
    import automatic_memory_key_resolution as resolution
    state = tmp_path / 'state'
    _, decision = collision(state)
    plan = resolution.plan(state, decision['delivery_id'], 'cohort.review', 'Separate concepts.')
    digest = hashlib.sha256(memory._canonical_json(plan)).hexdigest()
    bad = deepcopy(plan)
    bad[mutation] = 'tampered'
    with pytest.raises(ValueError):
        resolution.record(state, bad, expected_sha256=digest, writer_role='home')
    assert not (state / 'key-resolutions').exists()


def test_resolution_cannot_be_recorded_by_sjm_or_after_receipt(tmp_path):
    import automatic_memory_key_resolution as resolution
    state = tmp_path / 'state'
    _, decision = collision(state)
    plan = resolution.plan(state, decision['delivery_id'], 'cohort.review', 'Separate concepts.')
    digest = hashlib.sha256(memory._canonical_json(plan)).hexdigest()
    with pytest.raises(PermissionError):
        resolution.record(state, plan, expected_sha256=digest, writer_role='sjm-source-only')
    resolution.record(state, plan, expected_sha256=digest, writer_role='home')
    capture.reconcile_memory_delivery(state, decision['delivery_id'], writer_role='home')
    with pytest.raises(ValueError, match='reconciled'):
        resolution.record(state, plan, expected_sha256=digest, writer_role='home')


def test_resolution_survives_later_evidence_on_both_keys(tmp_path):
    import automatic_memory_key_resolution as resolution
    state = tmp_path / 'state'
    _, decision = collision(state)
    plan = resolution.plan(state, decision['delivery_id'], 'cohort.review', 'Separate concepts.')
    resolution.record(state, plan, expected_sha256=hashlib.sha256(memory._canonical_json(plan)).hexdigest(), writer_role='home')
    capture.reconcile_memory_delivery(state, decision['delivery_id'], writer_role='home')
    for key, kind in [('cohort.review', 'fact'), (plan['new_key'], 'decision')]:
        new = memory.enqueue_bundle(state, _bundle(key=key, kind=kind, cursor='turn:0003',
            value='Later evidence.', quote='Later evidence.'))
        capture.accept_memory_envelope(state, new, writer_role='home')
        capture.reconcile_memory_delivery(state, new['delivery_id'], writer_role='home')
    assert capture.reconcile_memory_delivery(state, decision['delivery_id'], writer_role='home')['duplicate']


@pytest.mark.parametrize('command,method', [('memory-home-drain','drain_home_memory'), ('memory-publish','publish_memory_state')])
def test_failed_command_still_emits_machine_readable_evidence(tmp_path, monkeypatch, capsys, command, method):
    monkeypatch.setattr(capture, method, lambda *a, **k: (_ for _ in ()).throw(ValueError('collision')))
    argv = [command, '--writer-role', 'home', '--state-root', str(tmp_path)]
    if command == 'memory-publish':
        argv += ['--vault-root', str(tmp_path / 'vault')]
    assert capture.main(argv) == 2
    result = json.loads(capsys.readouterr().out)
    assert result['status'] == 'failed'
    assert result['diagnostic']


def test_resolution_supports_an_explicit_memory_target_and_rejects_wrong_target(tmp_path):
    import automatic_memory_key_resolution as resolution
    state = tmp_path / 'state'
    _, decision = collision(state)
    target = state / 'explicit-memory.json'
    (state / 'memory.json').rename(target)
    plan = resolution.plan(state, decision['delivery_id'], 'cohort.review', 'Separate concepts.', memory_path=target)
    resolution.record(state, plan, expected_sha256=hashlib.sha256(memory._canonical_json(plan)).hexdigest(), writer_role='home', memory_path=target)
    assert capture.reconcile_memory_delivery(state, decision['delivery_id'], writer_role='home', memory_path=target)['status'] == 'reconciled'
    with pytest.raises(ValueError):
        capture.reconcile_memory_delivery(state, decision['delivery_id'], writer_role='home')


def test_operator_restores_only_the_exact_receipted_resolution(tmp_path):
    import automatic_memory_key_resolution as resolution
    state = tmp_path / 'state'
    _, decision = collision(state)
    plan = resolution.plan(state, decision['delivery_id'], 'cohort.review', 'Separate concepts.')
    resolution.record(state, plan, expected_sha256=hashlib.sha256(memory._canonical_json(plan)).hexdigest(), writer_role='home')
    result = capture.reconcile_memory_delivery(state, decision['delivery_id'], writer_role='home')
    path = state / 'key-resolutions' / (decision['delivery_id'] + '.json')
    original = path.read_bytes()
    path.write_text('corrupt but preserved')
    resolution.restore(state, decision['delivery_id'], writer_role='home')
    assert path.read_bytes() == original
    assert capture.reconcile_memory_delivery(state, decision['delivery_id'], writer_role='home')['duplicate']
    backup = state / 'key-resolution-recovery' / (result['key_resolution_sha256'] + '.json')
    path.unlink()
    backup.write_text('{}')
    with pytest.raises(ValueError, match='hash'):
        resolution.restore(state, decision['delivery_id'], writer_role='home')
    assert not path.exists()


def test_canonical_run_retains_a_structured_command_failure(tmp_path):
    from datetime import datetime, timezone
    result = capture.record_native_memory_run(state_root=tmp_path / 'state', writer_role='home',
        started_at=datetime.now(timezone.utc).isoformat(), work_ids=[],
        drain_result={'status': 'failed', 'diagnostic': 'memory_drain_failed',
            'error_type': 'ValueError', 'message': 'kind collision'},
        publication_result={'status': 'failed', 'diagnostic': 'memory_publication_failed',
            'error_type': 'ValueError', 'message': 'missing reconciliation'})
    data = json.loads(Path(result['receipt_path']).read_text())
    assert result['outcome'] == 'failed'
    assert data['commands']['memory_home_drain']['diagnostic'] == 'memory_drain_failed'
    assert data['commands']['memory_home_drain']['message'] == 'kind collision'
    assert data['commands']['memory_publish']['message'] == 'missing reconciliation'
    assert 'native_run_bookkeeping' not in data['commands']


def test_recurrent_collision_has_an_explicit_operator_key_path(tmp_path):
    import automatic_memory_key_resolution as resolution
    state = tmp_path / 'state'
    _, decision = collision(state)
    first = resolution.plan(state, decision['delivery_id'], 'cohort.review', 'Separate concepts.')
    resolution.record(state, first, expected_sha256=hashlib.sha256(memory._canonical_json(first)).hexdigest(), writer_role='home')
    capture.reconcile_memory_delivery(state, decision['delivery_id'], writer_role='home')
    later = memory.enqueue_bundle(state, _bundle(key='cohort.review', kind='decision', cursor='turn:0009',
        value='Review another cohort.', quote='Review another cohort.'))
    capture.accept_memory_envelope(state, later, writer_role='home')
    second = resolution.plan(state, later['delivery_id'], 'cohort.review', 'Distinct cohort review.', new_key='cohort.review.other-decision')
    resolution.record(state, second, expected_sha256=hashlib.sha256(memory._canonical_json(second)).hexdigest(), writer_role='home')
    capture.reconcile_memory_delivery(state, later['delivery_id'], writer_role='home')
    assert memory.recall(state / 'memory.json', second['new_key'])['value'] == 'Review another cohort.'


def test_restore_rejects_a_non_object_receipt_without_modification(tmp_path):
    import automatic_memory_key_resolution as resolution
    state = tmp_path / 'state'
    _, decision = collision(state)
    path = state / 'reconciliation' / (decision['delivery_id'] + '.json')
    path.write_text('[]')
    with pytest.raises(ValueError, match='malformed'):
        resolution.restore(state, decision['delivery_id'], writer_role='home')
    assert path.read_text() == '[]'


@pytest.mark.parametrize('which', ['drain', 'publication'])
def test_structured_failures_require_detail_and_tolerate_additive_metadata(tmp_path, which):
    from datetime import datetime, timezone
    drain = {'status': 'failed', 'diagnostic': 'memory_drain_failed', 'error_type': 'ValueError', 'message': 'collision', 'retryable': True}
    publication = {'status': 'failed', 'diagnostic': 'memory_publication_failed', 'error_type': 'ValueError', 'message': 'missing receipt', 'retryable': True}
    result = capture.record_native_memory_run(state_root=tmp_path / 'state', writer_role='home',
        started_at=datetime.now(timezone.utc).isoformat(), work_ids=[], drain_result=drain, publication_result=publication)
    data = json.loads(Path(result['receipt_path']).read_text())
    assert data['commands']['memory_home_drain']['message'] == 'collision'
    assert data['commands']['memory_publish']['message'] == 'missing receipt'
    del (drain if which == 'drain' else publication)['error_type']
    result = capture.record_native_memory_run(state_root=tmp_path / 'malformed-state', writer_role='home',
        started_at=datetime.now(timezone.utc).isoformat(), work_ids=[], drain_result=drain, publication_result=publication)
    assert result['outcome'] == 'failed'
    assert result['diagnostic'] == ('home_drain_failure_malformed' if which == 'drain' else 'home_publication_failure_malformed')
    persisted = json.loads(Path(result['receipt_path']).read_text())
    assert persisted['diagnostic'] == result['diagnostic']
    assert persisted['commands']['native_run_bookkeeping']['status'] == 'failed'


@pytest.mark.parametrize('bad', [{'status': 'failed'}, {'status': 'failed', 'diagnostic': 'other'},
    {'status': 'failed', 'diagnostic': 'memory_publication_failed', 'error_type': 'ValueError', 'message': ' '}])
def test_noncanonical_publication_failure_is_durable_failed_evidence(tmp_path, bad):
    from datetime import datetime, timezone
    result = capture.record_native_memory_run(state_root=tmp_path / 'state', writer_role='home',
        started_at=datetime.now(timezone.utc).isoformat(), work_ids=[],
        drain_result={'status': 'reconciled', 'local_delivery': {}, 'sjm_delivery': {}}, publication_result=bad)
    assert result['outcome'] == 'failed'
    persisted = json.loads(Path(result['receipt_path']).read_text())
    assert persisted['diagnostic'] == 'home_publication_failure_malformed'
