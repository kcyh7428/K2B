"""Exercise the remote helper against local invented paths, never SSH or NAS."""
import hashlib
import json
import os
import pwd
import socket
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts/lib'))
import recovery_nas_remote as remote


def identity(path):
    st = path.stat()
    return {'dev': st.st_dev, 'ino': st.st_ino, 'uid': st.st_uid, 'mode': st.st_mode & 0o7777}


@pytest.fixture
def config(tmp_path, monkeypatch):
    root = tmp_path.resolve()
    root.chmod(0o700)
    user = pwd.getpwuid(os.getuid()).pw_name
    parent = root / user
    parent.mkdir(mode=0o700)
    monkeypatch.setattr(remote, 'HOME_PREFIX', str(root) + '/')
    return {'action': 'create', 'parent': str(parent), 'run_id': 'manual-invented',
            'binding': {'hostname': socket.gethostname(), 'username': user,
                        'uid': os.getuid(), 'parent': identity(parent), 'namespace': None}}


def upload_config(config, payload=b'invented ciphertext'):
    created = remote.run(config)
    return dict(config, action='upload', created=created, size=len(payload),
                sha256=hashlib.sha256(payload).hexdigest()), payload


def upload(tmp_path, cfg, payload):
    path = tmp_path / 'input'
    path.write_bytes(payload)
    with path.open('rb') as stream:
        return remote.run(cfg, input_fd=stream.fileno())


def test_real_descriptor_upload_readback_fetch(config, tmp_path):
    cfg, payload = upload_config(config)
    receipt = upload(tmp_path, cfg, payload)
    assert receipt['sha256'] == hashlib.sha256(payload).hexdigest()
    assert receipt['signature']['size'] == len(payload)
    fetched = tmp_path / 'fetched'
    with fetched.open('wb') as stream:
        result = remote.run(dict(cfg, action='fetch', file_signature=receipt['signature']), output_fd=stream.fileno())
    assert fetched.read_bytes() == payload
    assert result['before'] == result['after'] == receipt['signature']
    assert result['sha256'] == hashlib.sha256(fetched.read_bytes()).hexdigest()


def test_unknown_namespace_and_existing_run_refused(config):
    namespace = Path(config['parent']) / remote.NAMESPACE
    namespace.mkdir(mode=0o700)
    with pytest.raises(remote.RemoteError, match='namespace_exists'):
        remote.run(config)
    config['binding']['namespace'] = identity(namespace)
    assert remote.run(config)['action'] == 'create'
    with pytest.raises(remote.RemoteError, match='run_exists'):
        remote.run(config)


@pytest.mark.parametrize('kind', ['short', 'hash'])
def test_bad_upload_retains_unaccepted_partial(config, tmp_path, kind):
    cfg, payload = upload_config(config)
    if kind == 'short':
        payload = payload[:-1]
    else:
        payload = b'X' + payload[1:]
    with pytest.raises(remote.RemoteError):
        upload(tmp_path, cfg, payload)
    path = Path(cfg['parent']) / remote.NAMESPACE / cfg['run_id'] / remote.SLOT
    assert path.exists()
    with pytest.raises(remote.RemoteError, match='slot_exists'):
        upload(tmp_path, cfg, payload)


def test_remote_mutation_after_upload_refuses_fetch(config, tmp_path):
    cfg, payload = upload_config(config)
    receipt = upload(tmp_path, cfg, payload)
    path = Path(cfg['parent']) / remote.NAMESPACE / cfg['run_id'] / remote.SLOT
    path.write_bytes(b'X' + payload[1:])
    dest = tmp_path / 'fetched'
    with dest.open('wb') as stream:
        with pytest.raises(remote.RemoteError, match='file_signature_mismatch'):
            remote.run(dict(cfg, action='fetch', file_signature=receipt['signature']), output_fd=stream.fileno())
    assert dest.stat().st_size == 0


@pytest.mark.parametrize('field', ['hostname', 'uid', 'parent'])
def test_wrong_actual_binding_refused(config, field):
    if field == 'parent':
        config['binding']['parent']['ino'] += 1
    elif field == 'uid':
        config['binding']['uid'] += 1
    else:
        config['binding']['hostname'] = 'invented-wrong-host'
    with pytest.raises(remote.RemoteError):
        remote.run(config)
    assert not (Path(config['parent']) / remote.NAMESPACE).exists()


def test_named_run_replacement_after_transfer_refused(config, tmp_path, monkeypatch):
    cfg, payload = upload_config(config)
    actual = remote._upload
    def replace_after_upload(*args):
        result = actual(*args)
        path = Path(cfg['parent']) / remote.NAMESPACE / cfg['run_id']
        path.rename(path.with_name('old'))
        path.mkdir(mode=0o700)
        return result
    monkeypatch.setattr(remote, '_upload', replace_after_upload)
    with pytest.raises(remote.RemoteError, match='identity_mismatch'):
        upload(tmp_path, cfg, payload)


@pytest.mark.parametrize('kind', ['namespace', 'slot'])
def test_symlink_refused(config, tmp_path, kind):
    if kind == 'namespace':
        (Path(config['parent']) / remote.NAMESPACE).symlink_to(tmp_path)
        with pytest.raises(remote.RemoteError, match='namespace_exists'):
            remote.run(config)
    else:
        cfg, payload = upload_config(config)
        slot = Path(cfg['parent']) / remote.NAMESPACE / cfg['run_id'] / remote.SLOT
        slot.symlink_to(tmp_path / 'elsewhere')
        with pytest.raises(remote.RemoteError, match='slot_exists'):
            upload(tmp_path, cfg, payload)


@pytest.mark.parametrize('text', ['{"x":1,"x":2}', '{"x":NaN}', '{"x":1.5}'])
def test_strict_config_refuses_ambiguous_values(text):
    with pytest.raises(remote.RemoteError):
        remote._load_config(text.encode())


def test_capacity_refusal_precedes_slot_creation(config, tmp_path, monkeypatch):
    cfg, payload = upload_config(config)
    class Full:
        f_bavail = 0
        f_frsize = 4096
    monkeypatch.setattr(remote.os, 'fstatvfs', lambda fd: Full())
    with pytest.raises(remote.RemoteError, match='capacity_insufficient'):
        upload(tmp_path, cfg, payload)
    assert not (Path(cfg['parent']) / remote.NAMESPACE / cfg['run_id'] / remote.SLOT).exists()


def test_inspect_does_not_follow_namespace_link_or_read_payload(config, tmp_path, monkeypatch):
    outside = tmp_path / 'outside'
    outside.mkdir()
    (outside / 'private').write_bytes(b'invented do not read')
    (Path(config['parent']) / remote.NAMESPACE).symlink_to(outside)
    def forbidden(*args):
        pytest.fail('inspection hashed payload')
    monkeypatch.setattr(remote, '_hash_fd', forbidden)
    result = remote.run(dict(config, action='inspect'))
    assert result['namespace']['type'] == 'symlink'
    assert result['run'] is None and all(v is None for v in result['artifacts'].values())
    assert result['ownership'] == 'unresolved'


@pytest.mark.parametrize('username', ['a', 'User_42', '1source', '_worker', 'A' * 64, 'bad.name'])
def test_username_grammar_matches_actual_p1(username):
    import recovery_orchestration as p1
    assert bool(remote.USER_RE.fullmatch(username)) == bool(p1._SAFE_USERNAME_RE.fullmatch(username))


@pytest.mark.parametrize('run_id', ['manual-x', 'manual-X-42', 'manual-' + 'a' * 73, 'manual-bad_name'])
def test_run_grammar_matches_actual_p1(run_id):
    import recovery_orchestration as p1
    assert bool(remote.RUN_RE.fullmatch(run_id)) == bool(p1._RUN_ID_RE.fullmatch(run_id))
