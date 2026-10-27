"""Local subprocess simulation only. No SSH or actual NAS route is invoked."""
import hashlib
import copy
import json
import os
import pwd
import shlex
import socket
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts/lib'))
import recovery_nas_transport as client
import recovery_orchestration as orchestration
from manual_recovery_contract import Window, supervise_step


@pytest.fixture
def simulation(tmp_path, monkeypatch):
    root = tmp_path.resolve()
    root.chmod(0o700)
    user = pwd.getpwuid(os.getuid()).pw_name
    parent = root / user
    parent.mkdir(mode=0o700)
    st = parent.stat()
    binding = {'hostname': socket.gethostname(), 'username': user, 'uid': os.getuid(),
               'parent': {'dev': st.st_dev, 'ino': st.st_ino, 'uid': st.st_uid, 'mode': 0o700}, 'namespace': None}
    actual_popen = subprocess.Popen
    calls = []
    def local_fixed_program(argv, **kwargs):
        if argv[0] != 'ssh':
            assert Path(argv[0]).name in {'fixture-gpg', 'fixture-gpgconf'}
            return actual_popen(argv, **kwargs)
        calls.append(argv)
        assert argv[0] == 'ssh'
        assert kwargs.get('shell') is False
        assert 'start_new_session' not in kwargs
        command = shlex.split(argv[-1])
        assert command[:2] == ['/usr/bin/python3', '-c']
        source = command[2]
        assert hashlib.sha256(source.encode()).hexdigest() == client.REMOTE_PROGRAM_SHA256
        config = json.loads(command[3])
        # Only this explicit local simulation redirects the fixed NAS prefix.
        config['parent'] = str(parent)
        source = source.replace("HOME_PREFIX = '/volume1/homes/'", 'HOME_PREFIX = ' + repr(str(root) + '/'))
        return actual_popen([sys.executable, '-c', source, json.dumps(config)], **kwargs)
    monkeypatch.setattr(client.subprocess, 'Popen', local_fixed_program)
    args = (orchestration, user + '@192.168.1.250', '/volume1/homes/' + user, 'manual-synthetic', binding)
    return args, calls


def make_uploaded(tmp_path, args):
    created = client.create(*args, Window(60))
    cipher = tmp_path / 'cipher'
    cipher.write_bytes(b'invented encrypted bytes\x00' * 100)
    cipher.chmod(0o600)
    with cipher.open('rb') as stream:
        uploaded = client.upload(*args, created, stream.fileno(), Window(60))
    return created, uploaded, cipher


def fetch_artifact(args, created, uploaded, path, window):
    output = client.prepare_fetch_output(path)
    try:
        receipt = client.fetch(*args, created, uploaded, output, window, expected_context_sha256=client.request_context(*args, artifact='archive'))
        return client.accept_fetched(output, receipt, uploaded, window, expected_context_sha256=client.request_context(*args, artifact='archive'))
    except BaseException:
        output.close()
        raise


def test_actual_local_stream_chain(simulation, tmp_path):
    args, calls = simulation
    created, uploaded, cipher = make_uploaded(tmp_path, args)
    output = tmp_path.resolve() / 'fresh-fetch'
    with fetch_artifact(args, created, uploaded, output, Window(60)) as artifact:
        assert os.read(artifact.fd, output.stat().st_size) == cipher.read_bytes()
        assert artifact.receipt['remote']['before'] == uploaded['remote']['signature']
        assert artifact.receipt['remote']['after'] == uploaded['remote']['signature']
        assert artifact.receipt['local']['sha256'] == hashlib.sha256(cipher.read_bytes()).hexdigest()
        assert artifact.receipt['local']['signature']['ino'] != cipher.stat().st_ino
    assert artifact.fd == -1
    assert len(calls) == 3
    for receipt in (created, uploaded, artifact.receipt):
        assert receipt['exit_code'] == 0
        assert len(receipt['execution']) == 64
        assert receipt['remote_program_sha256'] == client.REMOTE_PROGRAM_SHA256


def test_cached_upload_cannot_replace_missing_remote_fetch(simulation, tmp_path):
    args, _ = simulation
    created, uploaded, cipher = make_uploaded(tmp_path, args)
    remote_slot = tmp_path / pwd.getpwuid(os.getuid()).pw_name / 'K2B-Recovery' / args[3] / 'knowledge.tar.gpg'
    remote_slot.unlink()
    with pytest.raises(client.TransportError, match='fetch_failed'):
        fetch_artifact(args, created, uploaded, tmp_path.resolve() / 'fresh', Window(60))
    assert cipher.exists()
    assert (tmp_path / 'fresh').stat().st_size == 0


@pytest.mark.parametrize('kind', ['occupied', 'symlink', 'parent_mode', 'receipt_context'])
def test_fetch_refuses_unbound_or_unsafe_output(simulation, tmp_path, kind):
    args, calls = simulation
    created, uploaded, _ = make_uploaded(tmp_path, args)
    dest = tmp_path.resolve() / 'fresh'
    if kind == 'occupied':
        dest.write_text('preserve')
    elif kind == 'symlink':
        dest.symlink_to(tmp_path / 'cipher')
    elif kind == 'parent_mode':
        tmp_path.chmod(0o755)
    else:
        uploaded['context_sha256'] = '0' * 64
    before = len(calls)
    with pytest.raises(client.TransportError):
        fetch_artifact(args, created, uploaded, dest, Window(60))
    assert len(calls) == before
    if kind == 'occupied':
        assert dest.read_text() == 'preserve'


def test_remote_tamper_refuses_fresh_fetch(simulation, tmp_path):
    args, _ = simulation
    created, uploaded, cipher = make_uploaded(tmp_path, args)
    remote_slot = tmp_path / pwd.getpwuid(os.getuid()).pw_name / 'K2B-Recovery' / args[3] / 'knowledge.tar.gpg'
    remote_slot.write_bytes(b'X' * cipher.stat().st_size)
    with pytest.raises(client.TransportError, match='fetch_failed'):
        fetch_artifact(args, created, uploaded, tmp_path.resolve() / 'fresh', Window(60))


def test_helper_pin_refuses_changed_program_before_spawn(simulation, monkeypatch):
    args, calls = simulation
    monkeypatch.setattr(client, 'REMOTE_PROGRAM_SHA256', '0' * 64)
    with pytest.raises(client.TransportError, match='helper_hash_mismatch'):
        client.create(*args, Window(60))
    assert not calls


@pytest.mark.parametrize('text', [b'{"x":1,"x":2}', b'{"x":NaN}', b'{"x":1.5}'])
def test_strict_receipt_json(text):
    with pytest.raises(client.TransportError, match='bad_json'):
        client._strict_json(text)


def test_actual_fetched_bytes_tampered_after_child_success(simulation, tmp_path, monkeypatch):
    args, _ = simulation
    created, uploaded, _ = make_uploaded(tmp_path, args)
    actual = client.subprocess.Popen
    class Proxy:
        def __init__(self, child, fd):
            self.child, self.fd = child, fd
        def __getattr__(self, name):
            return getattr(self.child, name)
        def communicate(self, **kwargs):
            result = self.child.communicate(**kwargs)
            os.pwrite(self.fd, b'X', 0)
            return result
    def tamper_after(argv, **kwargs):
        return Proxy(actual(argv, **kwargs), kwargs['stdout'])
    monkeypatch.setattr(client.subprocess, 'Popen', tamper_after)
    with pytest.raises(client.TransportError, match='hash_mismatch'):
        fetch_artifact(args, created, uploaded, tmp_path.resolve() / 'fresh', Window(60))


def test_upload_source_full_signature_checked_after_success(simulation, tmp_path, monkeypatch):
    args, _ = simulation
    created = client.create(*args, Window(60))
    cipher = tmp_path / 'cipher'
    cipher.write_bytes(b'invented ciphertext')
    cipher.chmod(0o600)
    actual = client.subprocess.Popen
    class Proxy:
        def __init__(self, child):
            self.child = child
        def __getattr__(self, name):
            return getattr(self.child, name)
        def communicate(self, **kwargs):
            result = self.child.communicate(**kwargs)
            cipher.write_bytes(b'X' + cipher.read_bytes()[1:])
            return result
    monkeypatch.setattr(client.subprocess, 'Popen', lambda argv, **kwargs: Proxy(actual(argv, **kwargs)))
    with cipher.open('rb') as stream:
        with pytest.raises(client.TransportError, match='fd_changed'):
            client.upload(*args, created, stream.fileno(), Window(60))


def test_real_p1_supervisor_keeps_parent_fetched_descriptor(simulation, tmp_path):
    args, _ = simulation
    created, uploaded, cipher = make_uploaded(tmp_path, args)
    with client.prepare_fetch_output(tmp_path.resolve() / 'calibration') as calibration_output:
        measured_receipt = client.fetch(*args, created, uploaded, calibration_output, Window(60), expected_context_sha256=client.request_context(*args, artifact='archive'))
    measured = measured_receipt['elapsed_seconds']
    calibration_evidence = json.dumps(measured_receipt, sort_keys=True, separators=(',', ':')).encode()
    calibration = {'version': 1, 'stages': {'nas-fetch': {
        'kind': 'hash', 'measured_seconds': measured,
        'receipt_sha256': hashlib.sha256(calibration_evidence).hexdigest()}}}
    raw = json.dumps(calibration, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
    window = Window(60, calibration=calibration, trusted_calibration_sha256=hashlib.sha256(raw).hexdigest())
    with client.prepare_fetch_output(tmp_path.resolve() / 'supervised') as output:
        parent_pid = os.getpid()
        before_fd = output.fd
        receipt = supervise_step('nas-fetch', lambda: client.fetch(*args, created, uploaded, output, window, expected_context_sha256=client.request_context(*args, artifact='archive')),
                                 window, 10, measured)
        assert os.getpid() == parent_pid
        assert output.fd == before_fd
        assert os.fstat(output.fd).st_size == cipher.stat().st_size
        client.accept_fetched(output, receipt, uploaded, window, expected_context_sha256=client.request_context(*args, artifact='archive'))
        assert os.read(output.fd, cipher.stat().st_size) == cipher.read_bytes()


def test_parent_recheck_refuses_post_supervisor_mutation(simulation, tmp_path):
    args, _ = simulation
    created, uploaded, _ = make_uploaded(tmp_path, args)
    with client.prepare_fetch_output(tmp_path.resolve() / 'fresh') as output:
        receipt = client.fetch(*args, created, uploaded, output, Window(60), expected_context_sha256=client.request_context(*args, artifact='archive'))
        os.pwrite(output.fd, b'X', 0)
        with pytest.raises(client.TransportError, match='fetch_changed'):
            client.accept_fetched(output, receipt, uploaded, Window(60), expected_context_sha256=client.request_context(*args, artifact='archive'))


def test_fetched_output_cannot_be_reused_for_second_attempt(simulation, tmp_path):
    args, calls = simulation
    created, uploaded, _ = make_uploaded(tmp_path, args)
    with client.prepare_fetch_output(tmp_path.resolve() / 'fresh') as output:
        client.fetch(*args, created, uploaded, output, Window(60), expected_context_sha256=client.request_context(*args, artifact='archive'))
        before = len(calls)
        with pytest.raises(client.TransportError, match='output_not_fresh'):
            client.fetch(*args, created, uploaded, output, Window(60), expected_context_sha256=client.request_context(*args, artifact='archive'))
        assert len(calls) == before


@pytest.mark.parametrize('kind', ['missing_remote', 'bad_local', 'bool_exit', 'bad_execution', 'missing_remote_stat'])
def test_parent_accept_malformed_receipts_raise_static_error(simulation, tmp_path, kind):
    args, _ = simulation
    created, uploaded, _ = make_uploaded(tmp_path, args)
    with client.prepare_fetch_output(tmp_path.resolve() / 'fresh') as output:
        receipt = client.fetch(*args, created, uploaded, output, Window(60), expected_context_sha256=client.request_context(*args, artifact='archive'))
        corrupted_upload, corrupted_fetch = copy.deepcopy(uploaded), copy.deepcopy(receipt)
        if kind == 'missing_remote':
            del corrupted_fetch['remote']
        elif kind == 'bad_local':
            corrupted_upload['local'] = None
        elif kind == 'bool_exit':
            corrupted_upload['exit_code'] = True
        elif kind == 'bad_execution':
            corrupted_upload['execution'] = ''
        else:
            del corrupted_fetch['remote']['before']['ctime_ns']
        with pytest.raises(client.TransportError):
            client.accept_fetched(output, corrupted_fetch, corrupted_upload, Window(60), expected_context_sha256=client.request_context(*args, artifact='archive'))


@pytest.mark.parametrize('operation', ['lseek', 'fsync'])
def test_fetch_io_failures_are_static(simulation, tmp_path, monkeypatch, operation):
    args, _ = simulation
    created, uploaded, _ = make_uploaded(tmp_path, args)
    with client.prepare_fetch_output(tmp_path.resolve() / 'fresh') as output:
        def fail(*args, **kwargs):
            raise OSError('invented private error')
        monkeypatch.setattr(client.os, operation, fail)
        with pytest.raises(client.TransportError, match='io_error'):
            client.fetch(*args, created, uploaded, output, Window(60), expected_context_sha256=client.request_context(*args, artifact='archive'))


def test_separate_fixed_encrypted_control_slot(simulation, tmp_path):
    args, _ = simulation
    created, archive_upload, _ = make_uploaded(tmp_path, args)
    source = tmp_path / 'control-cipher'
    source.write_bytes(b'invented encrypted control bytes')
    source.chmod(0o600)
    with source.open('rb') as stream:
        control_upload = client.upload(*args, created, stream.fileno(), Window(60), artifact='control')
    assert control_upload['remote']['artifact'] == 'control'
    assert control_upload['context_sha256'] != archive_upload['context_sha256']
    assert control_upload['remote']['signature']['ino'] != archive_upload['remote']['signature']['ino']
    with client.prepare_fetch_output(tmp_path.resolve() / 'control-fetched') as output:
        result = client.fetch(*args, created, control_upload, output, Window(60), artifact='control', expected_context_sha256=client.request_context(*args, artifact='control'))
        client.accept_fetched(output, result, control_upload, Window(60), expected_context_sha256=client.request_context(*args, artifact='control'))
        assert os.read(output.fd, source.stat().st_size) == source.read_bytes()


def test_archive_receipt_cannot_stand_in_for_control_fetch(simulation, tmp_path):
    args, calls = simulation
    created, uploaded, _ = make_uploaded(tmp_path, args)
    before = len(calls)
    with client.prepare_fetch_output(tmp_path.resolve() / 'control') as output:
        with pytest.raises(client.TransportError, match='artifact_mismatch'):
            client.fetch(*args, created, uploaded, output, Window(60), artifact='control', expected_context_sha256=client.request_context(*args, artifact='control'))
    assert len(calls) == before


def test_no_arbitrary_transport_slot(simulation, tmp_path):
    args, calls = simulation
    created, uploaded, _ = make_uploaded(tmp_path, args)
    before = len(calls)
    with client.prepare_fetch_output(tmp_path.resolve() / 'out') as output:
        with pytest.raises(client.TransportError, match='artifact_invalid'):
            client.fetch(*args, created, uploaded, output, Window(60), artifact='../other', expected_context_sha256=client.request_context(*args))
    assert len(calls) == before


def test_inspect_absent_run_has_no_mutation(simulation, tmp_path):
    args, _ = simulation
    result = client.inspect(*args, Window(60))
    assert result['remote']['namespace'] is None
    assert result['remote']['run'] is None
    assert result['remote']['ownership'] == 'unresolved'
    namespace = tmp_path / pwd.getpwuid(os.getuid()).pw_name / 'K2B-Recovery'
    assert not namespace.exists()


def test_inspect_partial_slot_cannot_be_adopted(simulation, tmp_path, monkeypatch):
    args, _ = simulation
    created = client.create(*args, Window(60))
    source = tmp_path / 'cipher'
    source.write_bytes(b'invented ciphertext')
    source.chmod(0o600)
    actual = client._run
    def short_upload(argv, stdin, stdout, stderr, window):
        config = json.loads(shlex.split(argv[-1])[-1])
        if config['action'] == 'upload':
            stdin = subprocess.DEVNULL
        return actual(argv, stdin, stdout, stderr, window)
    monkeypatch.setattr(client, '_run', short_upload)
    with source.open('rb') as stream:
        with pytest.raises(client.TransportError, match='upload_failed'):
            client.upload(*args, created, stream.fileno(), Window(60))
    observed = client.inspect(*args, Window(60))
    assert observed['remote']['artifacts']['archive']['size'] == 0
    assert observed['remote']['ownership'] == 'unresolved'
    assert observed['remote']['run'] is not None
    with source.open('rb') as stream:
        with pytest.raises(client.TransportError):
            client.upload(*args, observed, stream.fileno(), Window(60))


def test_replayed_consistent_pair_still_requires_original_context(simulation, tmp_path):
    args, _ = simulation
    created, _, _ = make_uploaded(tmp_path, args)
    original_context = client.request_context(*args)
    other_binding = dict(args[4], namespace=created['remote']['namespace'])
    other_args = (*args[:3], 'manual-other', other_binding)
    other_created, other_uploaded, _ = make_uploaded(tmp_path, other_args)
    with client.prepare_fetch_output(tmp_path.resolve() / 'other-fetched') as output:
        result = client.fetch(*other_args, other_created, other_uploaded, output, Window(60),
                              expected_context_sha256=client.request_context(*other_args))
        with pytest.raises(client.TransportError, match='expected_context_mismatch'):
            client.accept_fetched(output, result, other_uploaded, Window(60), expected_context_sha256=original_context)
