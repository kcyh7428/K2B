"""Snapshot-bound key resolution metadata in the existing Home-managed note.

The publisher emits this after validating acceptance, reconciliation and the
private operator resolution. Readers consume it as data, never instructions.
Original JSON memory schemas and assertion identities remain unchanged.
"""
import hashlib
import json
from pathlib import Path

import automatic_memory as memory

START = '<!-- k2b-key-resolution-proofs-v1\n'
END = '\n-->\n'
FIELDS = {'original_key', 'new_key', 'original_item_id', 'published_item_id',
          'anchor_item_id', 'resolution_sha256', 'delivery_id', 'content_id'}
JSON_REL = 'System/memory/automatic-memory-current.json'
NOTE_REL = 'wiki/context/context_automatic-memory-recall.md'


def snapshot_hash(state: dict) -> str:
    raw = json.dumps(memory._memory_state_to_json(state), ensure_ascii=False, indent=2, sort_keys=True) + '\n'
    return hashlib.sha256(raw.encode()).hexdigest()


def render(digest: str, proofs: list[dict]) -> str:
    return '\n' + START + json.dumps({'schema_version': 1, 'writer_role': 'home',
        'snapshot_sha256': digest, 'resolutions': proofs}, sort_keys=True) + END


def collect(state_root: Path, target: Path, snapshot: dict) -> list[dict]:
    import automatic_memory_key_resolution as resolution
    proofs = []
    for path in sorted((state_root / 'key-resolutions').glob('*.json')):
        original = memory.load_home_envelope(state_root, path.stem)
        effective, digest = resolution.effective(state_root, original, memory_path=target)
        data = json.loads(path.read_bytes())
        item = next((i for i in effective['bundle']['items'] if i['key'] == data['new_key']), None)
        if item is None:
            raise ValueError('key resolution changed during proof collection')
        version = memory._version(effective['bundle'], item)
        if version not in snapshot['records'].get(data['new_key'], {}).get('versions', []):
            raise ValueError('key resolution proof is not backed by published state')
        proofs.append({'original_key': data['original_key'], 'new_key': data['new_key'],
            'original_item_id': data['item_id'], 'published_item_id': version['item_id'],
            'anchor_item_id': data['anchor_item_id'], 'resolution_sha256': digest,
            'delivery_id': original['delivery_id'], 'content_id': original['content_id']})
    return proofs


def read(note_path: Path, state: dict) -> tuple[dict[str, dict], str]:
    """Return verified mappings, or an explicit failure state with no mappings.

    A temporary mismatch during replication must preserve provisional items.
    Anchor loss is invalid evidence, never a reason to guess a resolution.
    """
    try:
        if note_path.is_symlink():
            return {}, 'unsafe_note'
        text = note_path.read_text(encoding='utf-8')
        if START not in text:
            return {}, 'absent'
        if text.count(START) != 1:
            return {}, 'invalid'
        encoded, end = text.split(START, 1)[1].split(END, 1)
        if end.strip():
            return {}, 'invalid'
        data = json.loads(encoded)
        if (not isinstance(data, dict)
            or set(data) != {'schema_version', 'writer_role', 'snapshot_sha256', 'resolutions'}
            or type(data['schema_version']) is not int or data['schema_version'] != 1
            or data['writer_role'] != 'home' or not isinstance(data['resolutions'], list)):
            return {}, 'invalid'
        if data['snapshot_sha256'] != snapshot_hash(state):
            return {}, 'snapshot_mismatch'
        index = memory._item_index(state['records'])
        result = {}
        for proof in data['resolutions']:
            if not isinstance(proof, dict) or set(proof) != FIELDS:
                return {}, 'invalid'
            for key in ['original_key', 'new_key']:
                if not isinstance(proof[key], str) or not memory.SAFE_KEY_RE.fullmatch(proof[key]):
                    return {}, 'invalid'
            for key in ['original_item_id', 'published_item_id', 'anchor_item_id']:
                if not isinstance(proof[key], str) or not memory.ITEM_ID_RE.fullmatch(proof[key]):
                    return {}, 'invalid'
            if (not isinstance(proof['resolution_sha256'], str) or not memory.HEX_64_RE.fullmatch(proof['resolution_sha256'])
                or not isinstance(proof['delivery_id'], str) or not memory.DELIVERY_ID_RE.fullmatch(proof['delivery_id'])
                or not isinstance(proof['content_id'], str) or not proof['content_id'].startswith('bundle:')
                or not memory.HEX_64_RE.fullmatch(proof['content_id'][7:])):
                return {}, 'invalid'
            original = state['records'].get(proof['original_key'])
            if original is None or proof['original_key'] == proof['new_key']:
                return {}, 'anchor_missing'
            if proof['anchor_item_id'] not in {v['item_id'] for v in original['versions']}:
                return {}, 'anchor_missing'
            key, kind, version = index[proof['published_item_id']]
            if key != proof['new_key'] or original['kind'] == kind:
                return {}, 'invalid'
            expected = memory._item_identity(source_identity=version['source_identity'],
                key=proof['original_key'], kind=kind, value=version['value'],
                evidence_quote=version['evidence_quote'], speaker_source=version['speaker_source'])
            if expected != proof['original_item_id']:
                return {}, 'invalid'
            prior = result.get(expected)
            if prior is not None and prior != proof:
                return {}, 'ambiguous'
            result[expected] = proof
        return result, 'verified'
    except (OSError, ValueError, KeyError, TypeError, UnicodeError):
        return {}, 'unavailable_or_invalid'
