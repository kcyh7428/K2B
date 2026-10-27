"""Harmless fixture tools test FD/argv protocol, not GPG or private entry."""
import hashlib
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts/lib'))
import recovery_foreground_crypto as crypto
import recovery_nas_transport as transport
from manual_recovery_contract import Window
from test_recovery_nas_transport import simulation


@pytest.fixture
def tools(tmp_path):
    gpg = tmp_path.resolve() / 'fixture-gpg'
    gpg.write_text('#!' + sys.executable + '\n' + '''import sys
from pathlib import Path
args = sys.argv[1:]
assert '--no-options' in args and '--no-symkey-cache' in args
assert args[args.index('--pinentry-mode') + 1] == 'ask'
assert not any(arg.startswith('--passphrase') for arg in args)
home = Path(args[args.index('--homedir') + 1])
(home / 'fixture-agent').write_text('invented')
data = sys.stdin.buffer.read()
if '--decrypt' in args:
    assert data.startswith(b'fixture:')
    data = data[len(b'fixture:'):]
else:
    assert '--symmetric' in args and 'AES256' in args
    data = b'fixture:' + data
sys.stdout.buffer.write(data)
''')
    conf = tmp_path.resolve() / 'fixture-gpgconf'
    conf.write_text('#!' + sys.executable + '\n' + '''import sys
from pathlib import Path
assert sys.argv[1] == '--homedir' and sys.argv[3:] == ['--kill', 'gpg-agent']
(Path(sys.argv[2]) / 'cleanup-observed').write_text('invented')
''')
    for path in [gpg, conf]:
        path.chmod(0o700)
    return gpg, hashlib.sha256(gpg.read_bytes()).hexdigest(), conf, hashlib.sha256(conf.read_bytes()).hexdigest()


def plaintext(tmp_path):
    path = tmp_path / 'plain'
    path.write_bytes(b'invented archive bytes')
    path.chmod(0o600)
    return path, hashlib.sha256(path.read_bytes()).hexdigest()


def home(tmp_path, name):
    path = tmp_path.resolve() / name
    path.mkdir(mode=0o700)
    return path


def test_encrypt_protocol_parent_owned_descriptors(tmp_path, tools):
    tmp_path.chmod(0o700)
    source, digest = plaintext(tmp_path)
    run_home = home(tmp_path, 'encrypt-home')
    with source.open('rb') as stream, transport.prepare_fetch_output(tmp_path.resolve() / 'cipher') as output:
        receipt = crypto.crypt('encrypt', stream.fileno(), output, run_home, *tools, digest, None, Window(60))
        assert receipt['source_sha256'] == digest
        assert receipt['output_sha256'] == hashlib.sha256(b'fixture:' + source.read_bytes()).hexdigest()
        assert (run_home / 'cleanup-observed').exists()
        assert receipt['gpg_exit'] == receipt['cleanup_exit'] == 0
        assert receipt['output_signature'] == transport._verify_output(output)


@pytest.mark.parametrize('case', ['correct', 'wrong_plain_hash', 'wrong_context'])
def test_decrypt_consumes_actual_fetched_descriptor(simulation, tmp_path, tools, case):
    args, _ = simulation
    source, digest = plaintext(tmp_path)
    encrypted_home = home(tmp_path, 'encrypt-home')
    with source.open('rb') as original, transport.prepare_fetch_output(tmp_path.resolve() / 'cipher') as cipher:
        encrypted = crypto.crypt('encrypt', original.fileno(), cipher, encrypted_home, *tools, digest, None, Window(60))
        created = transport.create(*args, Window(60))
        uploaded = transport.upload(*args, created, cipher.fd, Window(60))
        assert uploaded['local']['sha256'] == encrypted['output_sha256']
    with transport.prepare_fetch_output(tmp_path.resolve() / 'fetched') as fetched:
        fetched_receipt = transport.fetch(*args, created, uploaded, fetched, Window(60), expected_context_sha256=transport.request_context(*args, artifact='archive'))
        transport.accept_fetched(fetched, fetched_receipt, uploaded, Window(60), expected_context_sha256=transport.request_context(*args, artifact='archive'))
        with transport.prepare_fetch_output(tmp_path.resolve() / 'recovered') as recovered:
            if case == 'wrong_context':
                different = (*args[:3], 'manual-other', args[4])
                with pytest.raises(crypto.CryptoError, match='source_fetch_context'):
                    crypto.crypt('decrypt', fetched.fd, recovered, home(tmp_path, 'decrypt-home'),
                        *tools, uploaded['local']['sha256'], digest, Window(60), source_fetch_receipt=fetched.receipt,
                        expected_fetch_context_sha256=transport.request_context(*different))
                assert os.fstat(recovered.fd).st_size == 0
                return
            if case == 'wrong_plain_hash':
                with pytest.raises(crypto.CryptoError, match='output_hash_mismatch'):
                    crypto.crypt('decrypt', fetched.fd, recovered, home(tmp_path, 'decrypt-home'),
                        *tools, uploaded['local']['sha256'], '0' * 64, Window(60), source_fetch_receipt=fetched.receipt, expected_fetch_context_sha256=transport.request_context(*args))
                return
            result = crypto.crypt('decrypt', fetched.fd, recovered, home(tmp_path, 'decrypt-home'),
                *tools, uploaded['local']['sha256'], digest, Window(60), source_fetch_receipt=fetched.receipt, expected_fetch_context_sha256=transport.request_context(*args))
            assert result['output_sha256'] == digest
            assert os.pread(recovered.fd, source.stat().st_size, 0) == source.read_bytes()


@pytest.mark.parametrize('kind', ['nonempty_home', 'bad_tool_pin', 'wrong_source_hash', 'missing_fetch_receipt'])
def test_refusals_never_accept_output(tmp_path, tools, kind):
    tmp_path.chmod(0o700)
    source, digest = plaintext(tmp_path)
    run_home = home(tmp_path, 'home')
    chosen = list(tools)
    direction, expected = 'encrypt', None
    if kind == 'nonempty_home':
        (run_home / 'existing').write_text('preserve')
    elif kind == 'bad_tool_pin':
        chosen[1] = '0' * 64
    elif kind == 'wrong_source_hash':
        digest = '0' * 64
    else:
        direction, expected = 'decrypt', '0' * 64
    with source.open('rb') as stream, transport.prepare_fetch_output(tmp_path.resolve() / 'out') as output:
        with pytest.raises(crypto.CryptoError):
            crypto.crypt(direction, stream.fileno(), output, run_home, *chosen, digest, expected, Window(60))
    if kind == 'nonempty_home':
        assert (run_home / 'existing').read_text() == 'preserve'
        assert not (run_home / 'cleanup-observed').exists()


def test_agent_cleanup_failure_never_accepts_cipher(tmp_path, tools):
    tmp_path.chmod(0o700)
    source, digest = plaintext(tmp_path)
    chosen = list(tools)
    chosen[2].write_text('#!' + sys.executable + '\nraise SystemExit(7)\n')
    chosen[3] = hashlib.sha256(chosen[2].read_bytes()).hexdigest()
    with source.open('rb') as stream, transport.prepare_fetch_output(tmp_path.resolve() / 'cipher') as output:
        with pytest.raises(crypto.CryptoError, match='cleanup_failed'):
            crypto.crypt('encrypt', stream.fileno(), output, home(tmp_path, 'home'),
                         *chosen, digest, None, Window(60))
        assert os.fstat(output.fd).st_size > 0


def test_fresh_home_cannot_be_reused(tmp_path, tools):
    tmp_path.chmod(0o700)
    source, digest = plaintext(tmp_path)
    run_home = home(tmp_path, 'home')
    for number in range(2):
        with source.open('rb') as stream, transport.prepare_fetch_output(tmp_path.resolve() / str(number)) as output:
            if number == 0:
                crypto.crypt('encrypt', stream.fileno(), output, run_home, *tools, digest, None, Window(60))
            else:
                with pytest.raises(crypto.CryptoError, match='home_not_empty'):
                    crypto.crypt('encrypt', stream.fileno(), output, run_home, *tools, digest, None, Window(60))


def test_crypto_and_cleanup_failure_preserve_both_reasons(tmp_path, tools):
    tmp_path.chmod(0o700)
    source, digest = plaintext(tmp_path)
    chosen = list(tools)
    for index in (0, 2):
        chosen[index].write_text('#!' + sys.executable + '\nraise SystemExit(7)\n')
        chosen[index + 1] = hashlib.sha256(chosen[index].read_bytes()).hexdigest()
    with source.open('rb') as stream, transport.prepare_fetch_output(tmp_path.resolve() / 'cipher') as output:
        with pytest.raises(crypto.CryptoError, match='cleanup_failed') as failure:
            crypto.crypt('encrypt', stream.fileno(), output, home(tmp_path, 'home'), *chosen, digest, None, Window(60))
        assert failure.value.phase_reason == 'gpg_exit_nonzero'
        assert failure.value.cleanup_reason == 'agent_cleanup_nonzero'
