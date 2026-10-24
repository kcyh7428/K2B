import hashlib
import base64
import importlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts/lib'))
import eod_capture as capture

SESSION = '01234567-1234-1234-1234-123456789abc'
TURN = '01234567-1234-1234-1234-123456789def'
TODAY = datetime.now(timezone(timedelta(hours=8))).date().isoformat()
IMAGE = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII='


def adapter():
    assert (ROOT / 'scripts/lib/codex_sql_memory.py').is_file(), 'SQL completed turns have no source adapter'
    return importlib.import_module('codex_sql_memory')


def database(tmp_path):
    root = tmp_path / 'native'
    root.mkdir()
    with sqlite3.connect(root / 'state_5.sqlite') as db:
        db.execute('CREATE TABLE threads (id TEXT PRIMARY KEY, rollout_path TEXT, source TEXT, cwd TEXT, history_mode TEXT, thread_source TEXT, has_user_event INTEGER)')
        db.execute('INSERT INTO threads VALUES (?,?,?,?,?,?,?)', (SESSION, str(root / 'archived_sessions/missing.jsonl'), 'codex_app', '/Users/test/Projects/K2B', 'paginated', 'user', 1))
    with sqlite3.connect(root / 'thread_history_1.sqlite') as db:
        db.execute('CREATE TABLE thread_turns (thread_id TEXT, turn_id TEXT, rollout_ordinal INTEGER, status TEXT, completed_at INTEGER, PRIMARY KEY(thread_id,turn_id))')
        db.execute('CREATE TABLE thread_items (thread_id TEXT, turn_id TEXT, item_id TEXT, rollout_ordinal INTEGER, created_at_ms INTEGER, item_json TEXT, item_type TEXT, updated_at_ordinal INTEGER, PRIMARY KEY(thread_id,turn_id,item_id))')
        db.execute('INSERT INTO thread_turns VALUES (?,?,?,?,?)', (SESSION, TURN, 1, 'completed', 1789743108))
        for ordinal, kind, item in [
            (2, 'userMessage', {'type': 'userMessage', 'id': 'user-native', 'clientId': None, 'content': [{'type': 'text', 'text': 'Keep the phone session signed in.'}]}),
            (3, 'agentMessage', {'type': 'agentMessage', 'id': 'agent-native', 'text': 'The session is preserved.', 'phase': 'final', 'memoryCitation': None, 'delivery': None, 'questions': []}),
        ]:
            db.execute('INSERT INTO thread_items VALUES (?,?,?,?,?,?,?,?)', (SESSION, TURN, item['id'], ordinal, 1789743108000, json.dumps(item), kind, ordinal))
    return root, tmp_path / 'private-state'


def args(root, state):
    return dict(codex_root=root, state_root=state, writer_role='home', since='2026-09-13', through=TODAY)


def reviewed(state, work_id):
    result = capture.prepare_memory_extraction_input(state_root=state, work_id=work_id, writer_role='home')
    view = json.loads(Path(result['input_path']).read_text())
    value = {k: view[k] for k in ['raw_source_sha256', 'transcript_sha256', 'completed_cursor', 'completed_prefix_sha256', 'completed_prefix_mode', 'completed_date', 'host_id', 'session_id']}
    value.update(schema_version='1.0', review_state='reviewed', items=[dict(kind='preference', subtype='other', subject='session', predicate='phone_session_requirement', object='Keep phone signed in.', scope='K2B', confidence='high', evidence_quote='Keep the phone session signed in.', speaker_source='keith', evidence_event_id=f'event:home:{SESSION}:user-native', dedupe_key='session:phone:requirement', canonical_home='wiki/context/shelves/semantic.md')])
    return value


def test_sql_completed_turn_has_real_source_binding_and_native_receipt(tmp_path):
    sql = adapter()
    root, state = database(tmp_path)
    before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in root.glob('*.sqlite')}
    audit = sql.scan(**args(root, state))
    assert audit['complete'] and audit['pending_turns'] == 1
    assert not state.exists()
    staged = sql.stage(**args(root, state), session_id=SESSION, turn_id=TURN)
    assert sql.scan(**args(root, state))['pending_turns'] == 1
    source = json.loads(Path(staged['source_bundle_path']).read_text())
    snapshot = Path(source['session_path'])
    assert snapshot.suffix == '.json'
    assert snapshot.parent == state / 'sql-sources'
    assert hashlib.sha256(snapshot.read_bytes()).hexdigest() == source['raw_source_sha256']
    raw = json.loads(snapshot.read_text())
    assert raw['source_kind'] == 'codex_sql_snapshot'
    assert raw['history_database'] == str(root / 'thread_history_1.sqlite')
    assert raw['items'][-1]['item_id'] == 'agent-native'
    assert (snapshot.stat().st_mode & 0o777) == 0o600
    extraction = reviewed(state, staged['work_id'])
    result = capture.record_memory_extraction(state_root=state, work_id=staged['work_id'], reviewed=extraction, writer_role='home')
    assert result['status'] == 'queued'
    capture._validate_memory_extraction_receipt(state, staged['work_id'], source, read_only=True)
    audit = sql.scan(**args(root, state))
    assert audit['pending_turns'] == 0 and audit['extracted_turns'] == 1
    assert len(list((state / 'extraction-receipts').glob('*.json'))) == 1
    assert not list(root.rglob('*.jsonl'))
    assert {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in root.glob('*.sqlite')} == before


@pytest.mark.parametrize('change', ['snapshot', 'projection'])
def test_tampered_sql_evidence_cannot_create_extraction_receipt(tmp_path, change):
    sql = adapter()
    root, state = database(tmp_path)
    staged = sql.stage(**args(root, state), session_id=SESSION, turn_id=TURN)
    bundle_path = Path(staged['source_bundle_path'])
    bundle = json.loads(bundle_path.read_text())
    if change == 'snapshot':
        raw = Path(bundle['session_path'])
        raw.write_bytes(raw.read_bytes() + b' ')
    else:
        bundle['dialogue_events'][0]['text'] = 'Invented preference.'
        bundle_path.write_text(json.dumps(bundle))
    with pytest.raises((ValueError, RuntimeError)):
        capture.prepare_memory_extraction_input(state_root=state, work_id=staged['work_id'], writer_role='home')
    assert not list((state / 'extraction-receipts').glob('*.json'))
    assert not list((state / 'outbox').glob('*.json'))


@pytest.mark.parametrize('column,value', [('status', 'interrupted'), ('completed_at', 1789120000)])
def test_incomplete_or_outside_window_sql_turn_never_stages(tmp_path, column, value):
    sql = adapter()
    root, state = database(tmp_path)
    with sqlite3.connect(root / 'thread_history_1.sqlite') as db:
        db.execute(f'UPDATE thread_turns SET {column}=?', (value,))
    assert sql.scan(**args(root, state))['pending_turns'] == 0
    with pytest.raises(ValueError):
        sql.stage(**args(root, state), session_id=SESSION, turn_id=TURN)
    assert not state.exists()


def test_worker_or_foreign_project_sql_history_is_not_eligible(tmp_path):
    sql = adapter()
    root, state = database(tmp_path)
    with sqlite3.connect(root / 'state_5.sqlite') as db:
        db.execute('UPDATE threads SET thread_source=?', ('automation',))
    assert sql.scan(**args(root, state))['pending_turns'] == 0
    with sqlite3.connect(root / 'state_5.sqlite') as db:
        db.execute('UPDATE threads SET thread_source=?,cwd=?', ('user', '/Users/test/Projects/unrelated'))
    assert sql.scan(**args(root, state))['pending_turns'] == 0


def test_unrelated_database_growth_is_idempotent_but_amended_completed_rows_are_flagged(tmp_path):
    sql = adapter()
    root, state = database(tmp_path)
    staged = sql.stage(**args(root, state), session_id=SESSION, turn_id=TURN)
    capture.record_memory_extraction(state_root=state, work_id=staged['work_id'], reviewed=reviewed(state, staged['work_id']), writer_role='home')
    with sqlite3.connect(root / 'thread_history_1.sqlite') as db:
        db.execute('INSERT INTO thread_turns VALUES (?,?,?,?,?)', ('unrelated', 'other', 100, 'started', None))
    assert sql.scan(**args(root, state))['extracted_turns'] == 1
    with sqlite3.connect(root / 'thread_history_1.sqlite') as db:
        item = json.loads(db.execute("SELECT item_json FROM thread_items WHERE item_type='userMessage'").fetchone()[0])
        item['content'][0]['text'] = 'Changed completed dialogue.'
        db.execute("UPDATE thread_items SET item_json=? WHERE item_type='userMessage'", (json.dumps(item),))
    audit = sql.scan(**args(root, state))
    assert audit['status'] == 'needs_attention'
    assert audit['changed_sources'] == 1
    with pytest.raises(RuntimeError):
        sql.stage(**args(root, state), session_id=SESSION, turn_id=TURN)


def test_unsupported_current_user_content_fails_without_partial_work(tmp_path):
    sql = adapter()
    root, state = database(tmp_path)
    with sqlite3.connect(root / 'thread_history_1.sqlite') as db:
        item = json.loads(db.execute("SELECT item_json FROM thread_items WHERE item_type='userMessage'").fetchone()[0])
        item['content'].append({'type': 'image', 'url': 'private:image'})
        db.execute("UPDATE thread_items SET item_json=? WHERE item_type='userMessage'", (json.dumps(item),))
    with pytest.raises(ValueError, match='unsupported'):
        sql.stage(**args(root, state), session_id=SESSION, turn_id=TURN)
    assert not list((state / 'worklist').glob('*.json'))


def test_approved_date_window_and_database_symlinks_fail_closed(tmp_path):
    sql = adapter()
    root, state = database(tmp_path)
    with pytest.raises(ValueError):
        sql.scan(**{**args(root, state), 'since': '2026-09-12'})
    actual = root / 'thread_history_1.sqlite'
    actual.rename(root / 'actual.sqlite')
    actual.symlink_to(root / 'actual.sqlite')
    with pytest.raises(ValueError):
        sql.scan(**args(root, state))


def test_cli_metadata_scan_and_stage_keep_dialogue_private(tmp_path):
    root, state = database(tmp_path)
    command = [sys.executable, str(ROOT / 'scripts/memory-sql-source.py'), 'scan',
               '--codex-root', str(root), '--state-root', str(state), '--writer-role', 'home',
               '--since', '2026-09-13', '--through', TODAY]
    result = subprocess.run(command, capture_output=True, text=True)
    assert result.returncode == 0
    assert json.loads(result.stdout)['pending_turns'] == 1
    assert 'Keep the phone' not in result.stdout
    command[2] = 'stage'
    result = subprocess.run([*command, '--session-id', SESSION, '--turn-id', TURN], capture_output=True, text=True)
    assert result.returncode == 0
    assert json.loads(result.stdout)['status'] == 'ready'
    assert 'Keep the phone' not in result.stdout


def test_stage_refuses_existing_operator_hold(tmp_path):
    sql = adapter()
    root, state = database(tmp_path)
    state.mkdir()
    (state / 'extraction-hold.json').write_text('{malformed hold')
    with pytest.raises(PermissionError):
        sql.stage(**args(root, state), session_id=SESSION, turn_id=TURN)
    assert not list((state / 'worklist').glob('*.json'))


def test_explicit_user_origin_with_stale_cached_user_flag_still_uses_real_turn_rows(tmp_path):
    sql = adapter()
    root, state = database(tmp_path)
    with sqlite3.connect(root / 'state_5.sqlite') as db:
        db.execute('UPDATE threads SET has_user_event=0')
    assert sql.scan(**args(root, state))['pending_turns'] == 1
    with sqlite3.connect(root / 'state_5.sqlite') as db:
        db.execute('UPDATE threads SET thread_source=NULL')
    assert sql.scan(**args(root, state))['pending_turns'] == 0


def test_native_legacy_final_answer_phase_is_retained_verbatim(tmp_path):
    sql = adapter()
    root, state = database(tmp_path)
    with sqlite3.connect(root / 'thread_history_1.sqlite') as db:
        item = json.loads(db.execute("SELECT item_json FROM thread_items WHERE item_type='agentMessage'").fetchone()[0])
        item['phase'] = 'final_answer'
        db.execute("UPDATE thread_items SET item_json=? WHERE item_type='agentMessage'", (json.dumps(item),))
    result = sql.stage(**args(root, state), session_id=SESSION, turn_id=TURN)
    bundle = json.loads(Path(result['source_bundle_path']).read_text())
    assert bundle['dialogue_events'][-1]['text'] == 'The session is preserved.'


def test_embedded_native_image_is_preserved_and_exposed_in_bounded_input(tmp_path):
    sql = adapter()
    root, state = database(tmp_path)
    encoded = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII='
    with sqlite3.connect(root / 'thread_history_1.sqlite') as db:
        item = json.loads(db.execute("SELECT item_json FROM thread_items WHERE item_type='userMessage'").fetchone()[0])
        item['content'].append({'type': 'image', 'url': 'data:image/png;base64,' + encoded, 'detail': None})
        db.execute("UPDATE thread_items SET item_json=? WHERE item_type='userMessage'", (json.dumps(item),))
    result = sql.stage(**args(root, state), session_id=SESSION, turn_id=TURN)
    view_result = capture.prepare_memory_extraction_input(state_root=state, work_id=result['work_id'], writer_role='home')
    view = json.loads(Path(view_result['input_path']).read_text())
    image = view['attachments'][0]
    assert Path(image['path']).read_bytes() == base64.b64decode(encoded)
    assert image['event_id'] == f'event:home:{SESSION}:user-native'
    assert image['sha256'] == hashlib.sha256(base64.b64decode(encoded)).hexdigest()
    assert (Path(image['path']).stat().st_mode & 0o777) == 0o600
    Path(image['path']).write_bytes(b'changed')
    with pytest.raises((ValueError, RuntimeError)):
        capture.prepare_memory_extraction_input(state_root=state, work_id=result['work_id'], writer_role='home')


def test_missing_native_local_image_blocks_stage_without_fabricating_a_receipt(tmp_path):
    sql = adapter()
    root, state = database(tmp_path)
    with sqlite3.connect(root / 'thread_history_1.sqlite') as db:
        item = json.loads(db.execute("SELECT item_json FROM thread_items WHERE item_type='userMessage'").fetchone()[0])
        item['content'].append({'type': 'localImage', 'path': str(tmp_path / 'missing.png'), 'detail': None})
        db.execute("UPDATE thread_items SET item_json=? WHERE item_type='userMessage'", (json.dumps(item),))
    with pytest.raises(ValueError, match='attachment unavailable'):
        sql.stage(**args(root, state), session_id=SESSION, turn_id=TURN)
    assert not list((state / 'extraction-receipts').glob('*.json'))
    assert not list((state / 'worklist').glob('*.json'))


@pytest.mark.parametrize('withdrawal', ['status', 'date', 'scope', 'deleted_turn'])
def test_previously_extracted_sql_turn_cannot_vanish_from_coverage_audit(tmp_path, withdrawal):
    sql = adapter()
    root, state = database(tmp_path)
    staged = sql.stage(**args(root, state), session_id=SESSION, turn_id=TURN)
    capture.record_memory_extraction(state_root=state, work_id=staged['work_id'], reviewed=reviewed(state, staged['work_id']), writer_role='home')
    if withdrawal == 'scope':
        with sqlite3.connect(root / 'state_5.sqlite') as db:
            db.execute("UPDATE threads SET cwd='/Users/test/Projects/unrelated'")
    else:
        with sqlite3.connect(root / 'thread_history_1.sqlite') as db:
            if withdrawal == 'status':
                db.execute("UPDATE thread_turns SET status='interrupted'")
            elif withdrawal == 'date':
                db.execute('UPDATE thread_turns SET completed_at=1789120000')
            else:
                db.execute('DELETE FROM thread_turns')
    result = sql.scan(**args(root, state))
    assert result['status'] == 'needs_attention' and result['changed_sources'] == 1


def test_native_mutation_after_discovery_rejects_staging_instead_of_binding_new_content(tmp_path, monkeypatch):
    sql = adapter()
    root, state = database(tmp_path)
    original = sql.scan
    def mutate_after_scan(**kwargs):
        result = original(**kwargs)
        with sqlite3.connect(root / 'thread_history_1.sqlite') as db:
            item = json.loads(db.execute("SELECT item_json FROM thread_items WHERE item_type='userMessage'").fetchone()[0])
            item['content'][0]['text'] = 'A concurrent completed-row amendment.'
            db.execute("UPDATE thread_items SET item_json=? WHERE item_type='userMessage'", (json.dumps(item),))
        return result
    monkeypatch.setattr(sql, 'scan', mutate_after_scan)
    with pytest.raises(RuntimeError, match='changed'):
        sql.stage(**args(root, state), session_id=SESSION, turn_id=TURN)
    assert not list((state / 'worklist').glob('*.json'))


def local_image(root, tmp_path):
    image = tmp_path / 'clipboard.png'
    image.write_bytes(base64.b64decode(IMAGE))
    with sqlite3.connect(root / 'thread_history_1.sqlite') as db:
        item = json.loads(db.execute("SELECT item_json FROM thread_items WHERE item_type='userMessage'").fetchone()[0])
        item['content'].append({'type': 'localImage', 'path': str(image), 'detail': None})
        db.execute("UPDATE thread_items SET item_json=? WHERE item_type='userMessage'", (json.dumps(item),))
    return image


def test_local_image_drift_before_locked_persist_rejects_staging(tmp_path, monkeypatch):
    sql = adapter()
    root, state = database(tmp_path)
    image = local_image(root, tmp_path)
    original = sql._capture_attachments
    first = True
    def capture_then_change(*args, **kwargs):
        nonlocal first
        result = original(*args, **kwargs)
        if first:
            first = False
            image.write_bytes(image.read_bytes() + b'concurrent change')
        return result
    monkeypatch.setattr(sql, '_capture_attachments', capture_then_change)
    with pytest.raises(RuntimeError, match='attachment.*changed'):
        sql.stage(**args(root, state), session_id=SESSION, turn_id=TURN)
    assert not list((state / 'worklist').glob('*.json'))


def test_retained_image_receipt_survives_original_temporary_file_cleanup(tmp_path):
    sql = adapter()
    root, state = database(tmp_path)
    image = local_image(root, tmp_path)
    staged = sql.stage(**args(root, state), session_id=SESSION, turn_id=TURN)
    capture.record_memory_extraction(state_root=state, work_id=staged['work_id'], reviewed=reviewed(state, staged['work_id']), writer_role='home')
    bundle = json.loads(Path(staged['source_bundle_path']).read_text())
    snapshot = sql.validate_source(bundle)
    image.unlink()
    capture._validate_memory_extraction_receipt(state, staged['work_id'], bundle, read_only=True)
    assert sql.scan(**args(root, state))['extracted_turns'] == 1
    Path(snapshot['attachments'][0]['path']).write_bytes(b'changed retained image')
    with pytest.raises((ValueError, RuntimeError)):
        capture._validate_memory_extraction_receipt(state, staged['work_id'], bundle, read_only=True)


def test_live_coverage_scan_refuses_silently_narrowed_window(tmp_path):
    sql = adapter()
    root, state = database(tmp_path)
    with pytest.raises(ValueError, match='full approved'):
        sql.scan(**{**args(root, state), 'since': '2026-09-14'})


def test_scan_budget_exhaustion_is_distinct_from_complete_empty_coverage(tmp_path, monkeypatch):
    sql = adapter()
    root, state = database(tmp_path)
    original = sql._bindings
    def delayed(*a, **kw):
        value = original(*a, **kw)
        time.sleep(0.02)
        return value
    monkeypatch.setattr(sql, '_bindings', delayed)
    with pytest.raises(RuntimeError, match='budget'):
        sql.scan(**args(root, state), max_scan_seconds=0.001)
    assert not state.exists()


def test_locked_reaudit_does_not_inherit_large_operator_scan_budget(tmp_path, monkeypatch):
    sql = adapter()
    root, state = database(tmp_path)
    original = sql.scan
    budgets = []
    def observed(**kwargs):
        budgets.append(kwargs['max_scan_seconds'])
        return original(**kwargs)
    monkeypatch.setattr(sql, 'scan', observed)
    sql.stage(**args(root, state), session_id=SESSION, turn_id=TURN, max_scan_seconds=300)
    assert budgets[0] == 300 and budgets[1] <= 16


def test_database_contention_after_deadline_keeps_original_diagnostic(tmp_path):
    sql = adapter()
    root, state = database(tmp_path)
    with pytest.raises(sqlite3.OperationalError, match='database is locked'):
        with sql._database(root, 'state_5.sqlite', time.monotonic() - 1):
            raise sqlite3.OperationalError('database is locked')


def test_locked_stage_timeout_has_accurate_remediation_and_no_staged_source(tmp_path, monkeypatch):
    sql = adapter()
    root, state = database(tmp_path)
    original = sql.scan
    calls = 0
    def timeout(**kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise sql.SQLScanBudgetExceeded('increase the explicit bounded scan budget')
        return original(**kwargs)
    monkeypatch.setattr(sql, 'scan', timeout)
    with pytest.raises(sql.SQLScanBudgetExceeded, match='locked preparation.*no source staged'):
        sql.stage(**args(root, state), session_id=SESSION, turn_id=TURN)
    assert not list((state / 'worklist').glob('*.json'))


def test_receipt_publication_observes_already_durable_source_bundle(tmp_path, monkeypatch):
    sql = adapter()
    root, state = database(tmp_path)
    original = capture._atomic_write_json
    observed = []
    def writing(path, value):
        if path.parent.name == 'extraction-receipts':
            bundle = state / 'source-bundles' / path.name
            assert bundle.is_file()
            sql.validate_source(json.loads(bundle.read_text()))
            observed.append(path)
        return original(path, value)
    monkeypatch.setattr(capture, '_atomic_write_json', writing)
    staged = sql.stage(**args(root, state), session_id=SESSION, turn_id=TURN)
    capture.record_memory_extraction(state_root=state, work_id=staged['work_id'], reviewed=reviewed(state, staged['work_id']), writer_role='home')
    assert len(observed) == 1
