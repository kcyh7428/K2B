"""Read-only native SQL history adapter for explicitly selected recovery turns.

Retained SQL row snapshots are new evidence artifacts, never original JSONL.
No model calls, extraction receipts, shared writes or schedule changes occur here.
"""
from contextlib import contextmanager
import base64
from datetime import date, datetime, time as day_time, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import tempfile
import time
from urllib.parse import quote

import eod_capture as capture

MAX_ROWS = 5000
MAX_BYTES = 8_000_000
MAX_THREADS = 10000
TURN_COLUMNS = 'thread_id,turn_id,rollout_ordinal,status,completed_at'
ITEM_COLUMNS = 'thread_id,turn_id,item_id,rollout_ordinal,created_at_ms,item_json,item_type,updated_at_ordinal'
THREAD_COLUMNS = 'id,rollout_path,source,cwd,history_mode,thread_source,has_user_event'
HKT = timezone(timedelta(hours=8))
MAX_IMAGE_BYTES = 4_000_000


class SQLScanBudgetExceeded(RuntimeError):
    """Incomplete audit, never evidence of an empty or complete backlog."""


def _image_bytes(part):
    if part.get('type') == 'image':
        value = part.get('url', '')
        if not isinstance(value, str) or not value.startswith(('data:image/png;base64,', 'data:image/jpeg;base64,', 'data:image/webp;base64,')):
            raise ValueError('unsupported SQL image encoding')
        if len(value) > MAX_IMAGE_BYTES * 2:
            raise ValueError('SQL image exceeds bounded input limit')
        raw = base64.b64decode(value.split(',', 1)[1], validate=True)
    elif part.get('type') == 'localImage':
        path = Path(part['path'])
        if not path.is_file():
            return None
        resolved = path.resolve()
        if path.is_symlink() or not any(resolved.is_relative_to(root.resolve()) for root in [Path(tempfile.gettempdir()), Path('/tmp'), Path.home() / '.codex']):
            raise ValueError('unsupported SQL local image location')
        if path.stat().st_size > MAX_IMAGE_BYTES:
            raise ValueError('SQL image exceeds bounded input limit')
        with path.open('rb') as handle:
            before = capture.os.fstat(handle.fileno())
            raw = handle.read(MAX_IMAGE_BYTES + 1)
            after = capture.os.fstat(handle.fileno())
        current = path.stat()
        signature = lambda stat: (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns)
        if signature(before) != signature(after) or signature(after) != signature(current):
            raise RuntimeError('SQL attachment source changed during capture')
    else:
        raise ValueError('unsupported SQL image type')
    if len(raw) > MAX_IMAGE_BYTES:
        raise ValueError('SQL image exceeds bounded input limit')
    if raw.startswith(b'\x89PNG\r\n\x1a\n'):
        return raw, '.png'
    if raw.startswith(b'\xff\xd8\xff'):
        return raw, '.jpg'
    if raw.startswith(b'RIFF') and raw[8:12] == b'WEBP':
        return raw, '.webp'
    raise ValueError('unsupported SQL image signature')


def _capture_attachments(items, source_host, target_turn, state):
    attachments, blobs = [], []
    total_bytes = 0
    for row in items:
        if row['item_type'] != 'userMessage':
            continue
        content = json.loads(row['item_json']).get('content')
        if not isinstance(content, list):
            raise ValueError('unsupported SQL user content')
        for index, part in enumerate(content):
            if not isinstance(part, dict):
                raise ValueError('unsupported SQL content part')
            if part.get('type') == 'text':
                continue
            if len(attachments) >= 8:
                raise ValueError('SQL attachment batch exceeds bounded input limit')
            image = _image_bytes(part)
            descriptor = {'event_id': f"event:{source_host}:{row['thread_id']}:{row['item_id']}",
                          'content_index': index, 'kind': part['type']}
            if image is None:
                if row['turn_id'] == target_turn:
                    raise ValueError('native SQL attachment unavailable; preserve source and recover image')
                attachments.append(dict(descriptor, status='unavailable'))
                continue
            raw, extension = image
            total_bytes += len(raw)
            if len(attachments) >= 8 or total_bytes > MAX_BYTES:
                raise ValueError('SQL attachment batch exceeds bounded input limit')
            digest = hashlib.sha256(raw).hexdigest()
            path = state / 'sql-attachments' / (digest + extension)
            attachments.append(dict(descriptor, status='available', sha256=digest, path=str(path), bytes=len(raw)))
            blobs.append((path, raw))
    return attachments, blobs


def input_attachments(bundle, event_ids):
    snapshot = validate_source(bundle)
    return [item for item in snapshot['attachments'] if item['event_id'] in event_ids]


def _bytes(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + '\n').encode()


def _hash(value):
    return hashlib.sha256(_bytes(value)).hexdigest()


def _window(since, through):
    first, last = date.fromisoformat(since), date.fromisoformat(through)
    if first < date(2026, 9, 13) or last < first or last > datetime.now(HKT).date():
        raise ValueError('SQL recovery date window is not approved')
    return (int(datetime.combine(first, day_time(), HKT).timestamp()),
            int(datetime.combine(last + timedelta(days=1), day_time(), HKT).timestamp()))


def _roots(codex_root, state_root, writer_role):
    role = capture._memory_writer_role(writer_role)
    root, state = Path(codex_root), Path(state_root)
    if root.is_symlink() or state.is_symlink():
        raise ValueError('SQL recovery roots must not be symlinks')
    root, state = root.resolve(), state.resolve()
    vault = Path(capture.os.environ.get('K2B_VAULT_PATH') or Path.home() / 'Projects/K2B-Vault').expanduser().resolve()
    if state.is_relative_to(vault) or state.is_relative_to(root):
        raise ValueError('SQL evidence must remain private outside the vault and native history')
    production = (Path.home() / '.local/state/k2b/automatic-memory').resolve()
    if state == production and Path.home().name != ('keithmbpm2' if role == 'home' else 'keithcheung'):
        raise PermissionError('SQL recovery role does not match this host')
    return root, state, role


@contextmanager
def _database(root, name, deadline=None):
    path = root / name
    if path.is_symlink() or not path.is_file():
        raise ValueError('native SQL database is missing or unsafe')
    db = sqlite3.connect('file:' + quote(str(path), safe='/') + '?mode=ro', uri=True, timeout=2)
    db.row_factory = sqlite3.Row
    deadline = time.monotonic() + 20 if deadline is None else deadline
    interrupted = False
    def progress():
        nonlocal interrupted
        interrupted = time.monotonic() > deadline
        return int(interrupted)
    db.set_progress_handler(progress, 10000)
    try:
        db.execute('PRAGMA query_only=ON')
        db.execute('BEGIN')
        yield db
    except sqlite3.OperationalError as exc:
        if interrupted and getattr(exc, 'sqlite_errorcode', None) == sqlite3.SQLITE_INTERRUPT:
            raise SQLScanBudgetExceeded('SQL scan budget exceeded; increase the explicit bounded scan budget') from None
        raise
    finally:
        db.close()


def _eligible(thread):
    return (thread['source'] in capture.INTERACTIVE_THREAD_SOURCES
            and (thread['thread_source'] == 'user' or thread['thread_source'] is None and thread['has_user_event'] == 1)
            and thread['history_mode'] == 'paginated'
            and isinstance(thread['cwd'], str) and bool(capture.PROJECT_SCOPE_RE.search(thread['cwd'])))


def _rows(db, session_id, turn_id):
    size, count = db.execute("SELECT coalesce(sum(length(cast(item_json AS BLOB))),0),count(*) FROM thread_items WHERE thread_id=? AND turn_id=? AND item_type IN ('userMessage','agentMessage')", (session_id, turn_id)).fetchone()
    if size > MAX_BYTES or count > MAX_ROWS:
        raise ValueError('SQL dialogue exceeds bounded snapshot limits')
    return [dict(r) for r in db.execute(f"SELECT {ITEM_COLUMNS} FROM thread_items WHERE thread_id=? AND turn_id=? AND item_type IN ('userMessage','agentMessage') ORDER BY rollout_ordinal,item_id", (session_id, turn_id))]


def _turn_hash(turn, rows):
    return _hash({'turn': turn, 'items': rows})


def _revision(thread, turn, rows):
    return _hash({'thread': thread, 'turn': turn, 'items': rows})


def _bindings(state, deadline=None):
    result = {}
    for path in sorted((state / 'extraction-receipts').glob('*.json')):
        if deadline is not None and time.monotonic() > deadline:
            raise SQLScanBudgetExceeded('SQL scan budget exceeded while validating receipts')
        bundle_path = state / 'source-bundles' / path.name
        if path.is_symlink() or bundle_path.is_symlink():
            raise RuntimeError('unsafe extraction evidence in SQL coverage audit')
        bundle = capture._read_memory_json(bundle_path, 'existing SQL coverage source')
        capture._validate_memory_extraction_receipt(state, path.stem, bundle, read_only=True)
        result.setdefault((bundle['session_id'], bundle['completed_turn_id']), []).append(bundle)
    return result


def scan(*, codex_root, state_root, writer_role, since, through, max_scan_seconds=60):
    root, state, role = _roots(codex_root, state_root, writer_role)
    start, end = _window(since, through)
    if since != '2026-09-13' or through != datetime.now(HKT).date().isoformat():
        raise ValueError('SQL coverage audit requires the full approved date window')
    if (isinstance(max_scan_seconds, bool) or not isinstance(max_scan_seconds, (int, float))
            or not math.isfinite(max_scan_seconds) or not 0 < max_scan_seconds <= 300):
        raise ValueError('SQL scan budget must be positive and at most 300 seconds')
    deadline = time.monotonic() + max_scan_seconds
    bound = _bindings(state, deadline=deadline)
    if time.monotonic() > deadline:
        raise SQLScanBudgetExceeded('SQL scan budget exceeded while validating receipts')
    bound_sql = {key for key, bundles in bound.items()
                 if any('sql_binding' in b and start <= datetime.fromisoformat(capture._completed_at_iso(b['completed_at'])).timestamp() < end for b in bundles)}
    observed_sql = set()
    pending, extracted, changed, file_pending = [], 0, 0, 0
    with _database(root, 'state_5.sqlite', deadline) as meta, _database(root, 'thread_history_1.sqlite', deadline) as history:
        threads = list(meta.execute(f'SELECT {THREAD_COLUMNS} FROM threads LIMIT ?', (MAX_THREADS + 1,)))
        if len(threads) > MAX_THREADS:
            raise ValueError('SQL thread metadata limit exceeded')
        for entry in threads:
            if time.monotonic() > deadline:
                raise SQLScanBudgetExceeded('SQL scan budget exceeded during metadata audit')
            thread = dict(entry)
            if not _eligible(thread):
                continue
            turns = history.execute(f"SELECT {TURN_COLUMNS} FROM thread_turns t WHERE thread_id=? AND status='completed' AND completed_at>=? AND completed_at<? AND EXISTS (SELECT 1 FROM thread_items i WHERE i.thread_id=t.thread_id AND i.turn_id=t.turn_id AND item_type='userMessage') AND EXISTS (SELECT 1 FROM thread_items i WHERE i.thread_id=t.thread_id AND i.turn_id=t.turn_id AND item_type='agentMessage') ORDER BY rollout_ordinal LIMIT ?", (thread['id'], start, end, MAX_ROWS + 1)).fetchall()
            if len(turns) > MAX_ROWS:
                raise ValueError('SQL completed turn metadata limit exceeded')
            for entry in turns:
                if time.monotonic() > deadline:
                    raise SQLScanBudgetExceeded('SQL scan budget exceeded during metadata audit')
                turn = dict(entry)
                prior = bound.get((thread['id'], turn['turn_id']), [])
                sql_prior = [b for b in prior if 'sql_binding' in b]
                if sql_prior:
                    observed_sql.add((thread['id'], turn['turn_id']))
                if sql_prior and any(b['sql_binding']['turn_rows_sha256'] != _turn_hash(turn, _rows(history, thread['id'], turn['turn_id'])) for b in sql_prior):
                    changed += 1
                elif prior:
                    extracted += 1
                elif Path(thread['rollout_path']).is_file():
                    file_pending += 1
                else:
                    pending.append({'session_id': thread['id'], 'turn_id': turn['turn_id'],
                                    'completed_at': turn['completed_at'], 'source_storage': 'codex_sql_snapshot',
                                    'source_revision': _revision(thread, turn, _rows(history, thread['id'], turn['turn_id']))})
    changed += len(bound_sql - observed_sql)
    pending.sort(key=lambda r: (r['completed_at'], r['session_id'], r['turn_id']))
    return {'status': 'needs_attention' if changed else 'ok', 'complete': True,
            'writer_role': role, 'since': since, 'through': through,
            'pending_turns': len(pending), 'extracted_turns': extracted,
            'changed_sources': changed, 'file_backed_pending_turns': file_pending,
            'oldest_pending_at': capture._completed_at_iso(pending[0]['completed_at']) if pending else None,
            'candidates': pending}


def _projection(snapshot):
    if snapshot.get('source_kind') != 'codex_sql_snapshot' or snapshot.get('schema_version') != 1:
        raise ValueError('invalid SQL snapshot schema')
    thread, target = snapshot['thread'], snapshot['turn']
    if not _eligible(thread) or target['thread_id'] != thread['id'] or target['status'] != 'completed':
        raise ValueError('SQL snapshot is not eligible completed user history')
    source_host = snapshot['source_host']
    if source_host not in {'home', 'sjm'}:
        raise ValueError('invalid SQL snapshot host')
    start, end = _window(snapshot['since'], snapshot['through'])
    if type(target['completed_at']) is not int or not start <= target['completed_at'] < end:
        raise ValueError('SQL completion is outside the approved window')
    turns = {t['turn_id']: t for t in [*snapshot['context_turns'], target]}
    events = []
    for row in snapshot['items']:
        if row['thread_id'] != thread['id'] or row['turn_id'] not in turns:
            raise ValueError('SQL item does not belong to retained turns')
        item = json.loads(row['item_json'])
        if item.get('id') != row['item_id'] or item.get('type') != row['item_type']:
            raise ValueError('SQL native item identity mismatch')
        if row['item_type'] == 'userMessage':
            content = item.get('content')
            if not isinstance(content, list) or any(not isinstance(c, dict) or c.get('type') not in {'text', 'image', 'localImage'} or c.get('type') == 'text' and not isinstance(c.get('text'), str) for c in content):
                raise ValueError('unsupported SQL user content requires native operator review')
            text = '\n'.join(c['text'] if c['type'] == 'text' else '[native image attachment]' for c in content)
            role = 'user'
            if capture._is_codex_bootstrap_text(text):
                continue
        elif row['item_type'] == 'agentMessage':
            if item.get('phase') not in {None, 'commentary', 'final', 'final_answer'} or not isinstance(item.get('text'), str):
                raise ValueError('unsupported SQL assistant content')
            role, text = 'assistant', item['text']
        else:
            raise ValueError('unsupported SQL item type')
        text = capture._sanitize_transport_text(text)
        if not text.strip():
            continue
        events.append({'event_id': f"event:{source_host}:{thread['id']}:{row['item_id']}",
                       'turn_id': row['turn_id'], 'role': role, 'text': text,
                       'ordinal': row['rollout_ordinal']})
    current = [e for e in events if e['turn_id'] == target['turn_id']]
    if not any(e['role'] == 'user' for e in current) or not any(e['role'] == 'assistant' for e in current):
        raise ValueError('SQL completed turn has no eligible user and assistant dialogue')
    transcript = '\n\n'.join(f"[{e['role']}]\n{e['text']}" for e in events).strip()
    digest = hashlib.sha256(transcript.encode()).hexdigest()
    host = 'host:' + source_host
    seed = json.dumps([host, thread['id'], target['turn_id'], [e['event_id'] for e in events], digest], ensure_ascii=False, separators=(',', ':'))
    cursor = 'cursor:' + hashlib.sha256(seed.encode()).hexdigest()
    day = datetime.fromtimestamp(target['completed_at'], HKT).date().isoformat()
    return dict(source_host=source_host, host_id=host, session_id=thread['id'],
                completed_turn_id=target['turn_id'], completed_at=target['completed_at'],
                completed_date=day, run_date=day, completed_prefix_mode='turn',
                completed_cursor=cursor, completed_prefix_sha256=digest,
                transcript_sha256=digest, transcript=transcript, dialogue_events=events,
                chunks=capture._bounded_dialogue_chunks(transcript, cursor=cursor, max_chunk_chars=capture.DEFAULT_DIALOGUE_CHUNK_CHARS))


def validate_source(bundle):
    """Recheck the immutable SQL snapshot whenever native evidence is consumed."""
    binding = bundle.get('sql_binding')
    path = Path(bundle['session_path'])
    if (not isinstance(binding, dict) or set(binding) != {'schema_version', 'snapshot_sha256', 'turn_rows_sha256'}
            or binding['schema_version'] != 1 or path.is_symlink() or path.resolve() != path.absolute() or path.parent.name != 'sql-sources'
            or path.suffix != '.json' or path.stat().st_size > MAX_BYTES):
        raise ValueError('SQL snapshot binding is invalid')
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != bundle['raw_source_sha256'] or digest != binding['snapshot_sha256'] or path.stem != digest:
        raise ValueError('SQL snapshot hash mismatch')
    snapshot = json.loads(raw)
    expected_images = {}
    for row in snapshot['items']:
        if row['item_type'] == 'userMessage':
            for index, part in enumerate(json.loads(row['item_json']).get('content', [])):
                if part.get('type') != 'text':
                    expected_images[(f"event:{snapshot['source_host']}:{row['thread_id']}:{row['item_id']}", index)] = (part, row['turn_id'])
    actual_images = {}
    for image in snapshot['attachments']:
        identity = image['event_id'], image['content_index']
        if identity not in expected_images or identity in actual_images:
            raise ValueError('SQL attachment identity mismatch')
        part, turn_id = expected_images[identity]
        if image['kind'] != part['type']:
            raise ValueError('SQL attachment kind mismatch')
        if image['status'] == 'available':
            image_path = Path(image['path'])
            expected_parent = path.parent.parent / 'sql-attachments'
            if image_path.parent != expected_parent or image_path.resolve() != image_path.absolute() or image_path.is_symlink() or image_path.stat().st_size > MAX_IMAGE_BYTES:
                raise ValueError('unsafe SQL attachment artifact')
            content = image_path.read_bytes()
            if len(content) != image['bytes'] or hashlib.sha256(content).hexdigest() != image['sha256'] or image_path.stem != image['sha256']:
                raise ValueError('SQL attachment hash mismatch')
            # Original temporary localImage files may legitimately disappear
            # after capture. The immutable retained blob is the replay source;
            # requiring the old temporary file would invalidate good receipts.
            if part['type'] == 'image' and _image_bytes(part)[0] != content:
                raise ValueError('SQL embedded image projection mismatch')
        elif image['status'] != 'unavailable' or turn_id == snapshot['turn']['turn_id']:
            raise ValueError('SQL current attachment unavailable')
        actual_images[identity] = image
    if set(actual_images) != set(expected_images):
        raise ValueError('SQL snapshot attachment manifest is incomplete')
    current = [r for r in snapshot['items'] if r['turn_id'] == snapshot['turn']['turn_id']]
    if _turn_hash(snapshot['turn'], current) != binding['turn_rows_sha256']:
        raise ValueError('SQL turn row binding mismatch')
    projected = _projection(snapshot)
    if any(bundle.get(k) != v for k, v in projected.items()):
        raise ValueError('SQL projection does not match retained native rows')
    return snapshot


def _native_snapshot(root, session_id, turn_id, since, through, role, deadline=None):
    with _database(root, 'state_5.sqlite', deadline) as meta, _database(root, 'thread_history_1.sqlite', deadline) as history:
        thread = dict(meta.execute(f'SELECT {THREAD_COLUMNS} FROM threads WHERE id=?', (session_id,)).fetchone())
        target = dict(history.execute(f'SELECT {TURN_COLUMNS} FROM thread_turns WHERE thread_id=? AND turn_id=?', (session_id, turn_id)).fetchone())
        context = [dict(r) for r in history.execute(f"SELECT {TURN_COLUMNS} FROM thread_turns WHERE thread_id=? AND rollout_ordinal<? AND status='completed' ORDER BY rollout_ordinal DESC LIMIT 3", (session_id, target['rollout_ordinal']))][::-1]
        items = []
        for turn in [*context, target]:
            items.extend(_rows(history, session_id, turn['turn_id']))
        if len(items) > MAX_ROWS:
            raise ValueError('SQL context row limit exceeded')
        items.sort(key=lambda r: (r['rollout_ordinal'], r['item_id']))
        snapshot = dict(schema_version=1, source_kind='codex_sql_snapshot',
                        source_host='home' if role == 'home' else 'sjm',
                        state_database=str(root / 'state_5.sqlite'), history_database=str(root / 'thread_history_1.sqlite'),
                        since=since, through=through, thread=thread, turn=target, context_turns=context, items=items)
    return snapshot


def stage(*, codex_root, state_root, writer_role, since, through, session_id, turn_id, max_scan_seconds=60):
    root, state, role = _roots(codex_root, state_root, writer_role)
    audit = scan(codex_root=root, state_root=state, writer_role=role, since=since, through=through, max_scan_seconds=max_scan_seconds)
    if audit['changed_sources']:
        raise RuntimeError('completed SQL history changed after extraction; operator review required')
    approved = next((r for r in audit['candidates'] if r['session_id'] == session_id and r['turn_id'] == turn_id), None)
    if approved is None:
        raise ValueError('SQL turn is not an unextracted eligible candidate')
    if (state / 'extraction-hold.json').exists() or (state / 'extraction-hold.json').is_symlink():
        raise PermissionError('SQL extraction is on operator hold')
    snapshot = _native_snapshot(root, session_id, turn_id, since, through, role)
    native_hash = _hash(snapshot)
    target, items = snapshot['turn'], snapshot['items']
    current = [r for r in items if r['turn_id'] == turn_id]
    if _revision(snapshot['thread'], target, current) != approved['source_revision']:
        raise RuntimeError('SQL source changed after discovery; rescan before staging')
    attachments, blobs = _capture_attachments(items, snapshot['source_host'], turn_id, state)
    snapshot['attachments'] = attachments
    projected = _projection(snapshot)
    raw = _bytes(snapshot)
    if len(raw) > MAX_BYTES:
        raise ValueError('SQL snapshot byte limit exceeded')
    digest = hashlib.sha256(raw).hexdigest()
    source = state / 'sql-sources' / (digest + '.json')
    current = [r for r in items if r['turn_id'] == turn_id]
    bundle = dict(projected, bundle_schema_version=1, source_app='codex_desktop',
                  session_path=str(source), raw_source_sha256=digest, provenance_status='known_user',
                  thread_source=snapshot['thread']['thread_source'] or snapshot['thread']['source'],
                  sql_binding={'schema_version': 1, 'snapshot_sha256': digest, 'turn_rows_sha256': _turn_hash(target, current)},
                  exported_at=datetime.now(timezone.utc).isoformat())
    work_id = capture._memory_work_id(bundle)
    bundle_path = capture._memory_work_path(state, work_id, 'source-bundles')
    record_path = capture._memory_work_path(state, work_id, 'worklist')
    record = {k: bundle[k] for k in ['source_host', 'session_path', 'run_date', 'host_id', 'session_id', 'completed_cursor', 'completed_at', 'completed_prefix_sha256', 'transcript_sha256']}
    record.update(schema_version=1, work_id=work_id, writer_role=role,
                  source_bundle_relpath=str(bundle_path.relative_to(state)), status='pending_extraction')
    with capture._file_lock(state / '.memory-extraction.lock'), capture._file_lock(state / '.memory-worklist.lock'):
        # A larger operator audit budget must not extend shared writer holds.
        # Re-audit and final native read share the smaller locked deadline.
        locked_budget = min(max_scan_seconds, 20)
        locked_deadline = time.monotonic() + locked_budget
        try:
            # Reserve one fifth for the mandatory final read and image capture.
            fresh = scan(codex_root=root, state_root=state, writer_role=role, since=since, through=through, max_scan_seconds=locked_budget * 0.8)
            fresh_hash = _hash(_native_snapshot(root, session_id, turn_id, since, through, role, deadline=locked_deadline))
        except SQLScanBudgetExceeded:
            raise SQLScanBudgetExceeded('SQL locked preparation budget exceeded; no source staged; operator review of audit cost required') from None
        if fresh['changed_sources'] or fresh_hash != native_hash:
            raise RuntimeError('SQL source changed during preparation; preserve existing evidence')
        if (state / 'extraction-hold.json').exists() or (state / 'extraction-hold.json').is_symlink():
            raise PermissionError('SQL extraction is on operator hold')
        if not any(r['session_id'] == session_id and r['turn_id'] == turn_id for r in fresh['candidates']):
            raise RuntimeError('SQL turn is no longer pending after re-audit; rescan before continuing')
        fresh_attachments, fresh_blobs = _capture_attachments(items, snapshot['source_host'], turn_id, state)
        if fresh_attachments != attachments or fresh_blobs != blobs:
            raise RuntimeError('SQL attachment source changed during preparation; preserve existing evidence')
        if time.monotonic() > locked_deadline:
            raise SQLScanBudgetExceeded('SQL locked preparation budget exceeded; no source staged')
        for folder in ['sql-sources', 'sql-attachments', 'source-bundles', 'worklist']:
            if (state / folder).is_symlink():
                raise ValueError('unsafe SQL evidence destination')
        for image_path, image_bytes in blobs:
            if image_path.exists():
                if image_path.is_symlink() or image_path.read_bytes() != image_bytes:
                    raise RuntimeError('SQL image conflicts with retained attachment')
            else:
                image_path.parent.mkdir(parents=True, exist_ok=True)
                fd, temporary = tempfile.mkstemp(dir=image_path.parent, prefix='.image-')
                try:
                    with capture.os.fdopen(fd, 'wb') as handle:
                        handle.write(image_bytes)
                        handle.flush()
                        capture.os.fsync(handle.fileno())
                    capture.os.replace(temporary, image_path)
                    capture._fsync_dir(image_path.parent)
                finally:
                    if capture.os.path.exists(temporary):
                        capture.os.unlink(temporary)
        if source.exists():
            if source.is_symlink() or source.read_bytes() != raw:
                raise RuntimeError('SQL snapshot conflicts with existing evidence')
        else:
            capture._atomic_write_text(source, raw.decode())
        if bundle_path.exists():
            existing = capture._read_memory_json(bundle_path, 'SQL source bundle')
            validate_source(existing)
            if capture._memory_source_identity(existing) != capture._memory_source_identity(bundle):
                raise RuntimeError('SQL source bundle identity conflict')
        else:
            capture._atomic_write_json(bundle_path, bundle)
        if record_path.exists():
            if capture._read_memory_json(record_path, 'SQL work record') != record:
                raise RuntimeError('SQL work record identity conflict')
        else:
            capture._atomic_write_json(record_path, record)
    return {'status': 'ready', 'work_id': work_id, 'source_bundle_path': str(bundle_path),
            'work_record_path': str(record_path), 'sql_snapshot_path': str(source)}
