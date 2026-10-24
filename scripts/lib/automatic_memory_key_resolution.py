"""Explicit Home operator disambiguation of a reviewed semantic-key collision.

Original envelopes, item evidence and historical receipts are immutable. A
separate evidence-bound resolution describes a different storage key for one
assertion. Publication must verify that resolution and the resulting version.
No automatic kind conversion or success receipt is provided here.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import automatic_memory as memory


def _path(state: Path, delivery_id: str) -> Path:
    if not isinstance(delivery_id, str) or memory.DELIVERY_ID_RE.fullmatch(delivery_id) is None:
        raise ValueError('invalid key resolution delivery')
    path = state / 'key-resolutions' / (delivery_id + '.json')
    if path.parent.is_symlink() or path.is_symlink():
        raise ValueError('unsafe key resolution path')
    return path


def _new_key(key: str, kind: str) -> str:
    suffix = '.' + kind
    if len(key + suffix) <= 127:
        return key + suffix
    digest = hashlib.sha256(key.encode()).hexdigest()[:12]
    return key[:100].rstrip('.-_') + '.' + digest + suffix


def plan(state: Path, delivery_id: str, key: str, reason: str, *, memory_path: Path | None = None,
         new_key: str | None = None) -> dict:
    envelope = memory.load_home_envelope(state, delivery_id)
    items = [item for item in envelope['bundle']['items'] if item['key'] == key]
    if len(items) != 1 or not isinstance(reason, str) or not reason.strip() or len(reason) > 2000:
        raise ValueError('key resolution requires one assertion and an operator reason')
    item = items[0]
    target_path = memory_path or state / 'memory.json'
    snapshot = memory.memory_snapshot(target_path)
    existing = snapshot['records'].get(key)
    if existing is None or existing['kind'] == item['kind']:
        raise ValueError('key resolution requires an existing kind collision')
    new_key = _new_key(key, item['kind']) if new_key is None else new_key
    if not isinstance(new_key, str) or memory.SAFE_KEY_RE.fullmatch(new_key) is None or new_key == key:
        raise ValueError('key resolution requires a distinct valid destination key')
    # An operator may not merge this assertion into an unrelated existing key.
    # An identical resolution's own previously applied version is allowed on replay.
    effective_item = {**item, 'key': new_key}
    expected = memory._version(envelope['bundle'], effective_item)
    target = snapshot['records'].get(new_key)
    if target is not None and (target['kind'] != item['kind'] or expected not in target['versions']):
        raise ValueError('key resolution target is already occupied')
    return {'schema_version': 1, 'delivery_id': delivery_id,
        'content_id': envelope['content_id'], 'original_key': key, 'new_key': new_key,
        'memory_target_sha256': hashlib.sha256(str(target_path.expanduser().resolve()).encode()).hexdigest(),
        'item_id': memory._version(envelope['bundle'], item)['item_id'],
        'existing_kind': existing['kind'],
        'anchor_item_id': min(v['item_id'] for v in existing['versions']),
        'reason': reason}


def effective(state: Path, envelope: dict, *, memory_path: Path | None = None) -> tuple[dict, str | None]:
    path = _path(state, envelope['delivery_id'])
    if not path.exists():
        return envelope, None
    return _project(state, envelope, path.read_bytes(), memory_path=memory_path)


def _project(state: Path, envelope: dict, raw: bytes, *, memory_path: Path | None = None) -> tuple[dict, str]:
    try:
        resolution = json.loads(raw)
        expected = plan(state, envelope['delivery_id'], resolution['original_key'], resolution['reason'], memory_path=memory_path,
            new_key=resolution['new_key'])
        snapshot = memory.memory_snapshot(memory_path or state / 'memory.json')
        anchors = {v['item_id'] for v in snapshot['records'][resolution['original_key']]['versions']}
        if resolution.get('anchor_item_id') not in anchors:
            raise ValueError('key resolution anchor is absent')
        expected['anchor_item_id'] = resolution['anchor_item_id']
    except (OSError, ValueError, TypeError, KeyError) as exc:
        raise ValueError('key resolution is invalid') from exc
    if resolution != expected:
        raise ValueError('key resolution evidence changed')
    bundle = deepcopy(envelope['bundle'])
    for item in bundle['items']:
        if item['key'] == resolution['original_key']:
            item['key'] = resolution['new_key']
    projected = memory.make_envelope(bundle, now=envelope['created_at'])
    return projected, hashlib.sha256(raw).hexdigest()


def _recovery_path(state: Path, digest: str) -> Path:
    if not isinstance(digest, str) or memory.HEX_64_RE.fullmatch(digest) is None:
        raise ValueError('invalid resolution recovery hash')
    path = state / 'key-resolution-recovery' / (digest + '.json')
    if path.parent.is_symlink() or path.is_symlink():
        raise ValueError('unsafe resolution recovery path')
    return path


def record(state: Path, resolution: dict, *, expected_sha256: str, writer_role: str,
           memory_path: Path | None = None) -> dict:
    if writer_role != 'home':
        raise PermissionError('key resolution requires the Home operator')
    import eod_capture as capture
    if not isinstance(resolution, dict):
        raise ValueError('key resolution plan must be an object')
    if hashlib.sha256(memory._canonical_json(resolution)).hexdigest() != expected_sha256:
        raise ValueError('key resolution review hash differs')
    with capture._file_lock(state / '.reconciliation.lock'):
        delivery_id = resolution.get('delivery_id')
        path = _path(state, delivery_id)
        if (state / 'reconciliation' / (delivery_id + '.json')).exists():
            raise ValueError('already reconciled delivery cannot be rekeyed')
        expected = plan(state, delivery_id, resolution.get('original_key'), resolution.get('reason'), memory_path=memory_path,
            new_key=resolution.get('new_key'))
        if resolution != expected:
            raise ValueError('key resolution review is stale')
        if path.exists():
            if json.loads(path.read_bytes()) != resolution:
                raise ValueError('key resolution is immutable')
        else:
            raw = (json.dumps(resolution, indent=2, sort_keys=True) + '\n').encode()
            recovery = _recovery_path(state, hashlib.sha256(raw).hexdigest())
            if recovery.exists() and recovery.read_bytes() != raw:
                raise ValueError('resolution recovery copy differs')
            if not recovery.exists():
                memory._atomic_write_bytes(recovery, raw)
            memory._atomic_write_bytes(path, raw)
    return {'status': 'key_resolution_recorded', 'path': str(path),
        'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}


def restore(state: Path, delivery_id: str, *, writer_role: str,
            memory_path: Path | None = None) -> dict:
    """Restore exact resolution bytes from private recovery evidence only.

    Never alter a reconciliation receipt or bless a different resolution.
    Missing original knowledge still blocks restoration and publication.
    """
    if writer_role != 'home':
        raise PermissionError('resolution restoration requires the Home operator')
    import eod_capture as capture
    with capture._file_lock(state / '.reconciliation.lock'):
        path = _path(state, delivery_id)
        receipt_path = state / 'reconciliation' / (delivery_id + '.json')
        if receipt_path.is_symlink():
            raise ValueError('unsafe reconciliation receipt')
        receipt = json.loads(receipt_path.read_bytes())
        if not isinstance(receipt, dict):
            raise ValueError('resolution recovery receipt malformed')
        envelope = memory.load_home_envelope(state, delivery_id)
        if (receipt.get('status') != 'reconciled' or receipt.get('delivery_id') != delivery_id
                or receipt.get('content_id') != envelope['content_id']):
            raise ValueError('resolution recovery receipt identity differs')
        digest = receipt.get('key_resolution_sha256')
        raw = _recovery_path(state, digest).read_bytes()
        if hashlib.sha256(raw).hexdigest() != digest:
            raise ValueError('resolution recovery hash differs')
        _project(state, envelope, raw, memory_path=memory_path)
        if receipt.get('memory_target_sha256') != json.loads(raw)['memory_target_sha256']:
            raise ValueError('resolution recovery target differs')
        if path.exists() and path.read_bytes() != raw:
            damaged = path.read_bytes()
            preserved = _recovery_path(state, hashlib.sha256(damaged).hexdigest())
            if preserved.exists() and preserved.read_bytes() != damaged:
                raise ValueError('damaged resolution recovery copy differs')
            if not preserved.exists():
                memory._atomic_write_bytes(preserved, damaged)
        memory._atomic_write_bytes(path, raw)
    return {'status': 'key_resolution_restored', 'path': str(path), 'sha256': digest}
