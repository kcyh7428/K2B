"""Invented-data P1 integration for the separately prepared PAX producer."""
import hashlib
import json
import os
import stat
import sys
import tarfile
import tempfile
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts/lib'))
import manual_recovery_contract as contract
import recovery_exact_archive as producer
import recovery_exact_extract as extractor
import recovery_nas_transport as transport
from test_recovery_exact_extract import _component, _setup, _hashes, P1_CONTRACT_SHA256
import test_recovery_standard_tools as standard


def bind(src, rels, dirs, component, window):
    return contract.bind_hashes(component, contract.prepare_selection(component, src, rels, dirs, window),
                                _hashes(src, rels), window)


def private(path):
    path.mkdir(mode=0o700)
    path.chmod(0o700)
    return path


def roundtrip(tmp_path, mtime=None):
    assert hashlib.sha256(Path(contract.__file__).read_bytes()).hexdigest() == P1_CONTRACT_SHA256
    tmp_path.chmod(0o700)
    src, _, rels, dirs = _setup(tmp_path)
    (src / 'zero').write_bytes(b'')
    (src / 'zero').chmod(0o640)
    rels.append('zero')
    if mtime is not None:
        for rel in rels:
            os.utime(src / rel, ns=(mtime, mtime))
    stage = private(tmp_path / 'stage')
    component, window = _component(), contract.Window(60)
    bound = bind(src, rels, dirs, component, window)
    before = {rel: contract._signature((src / rel).stat()) for rel in rels}
    with transport.prepare_fetch_output(stage.resolve() / 'archive.tar') as output:
        receipt = producer.create_exact(component, contract, bound, output, window)
        assert receipt['R2'] == 'required_next_phase'
        assert receipt['archive_signature'] == transport._verify_output(output)
        with tarfile.open(output.path, 'r:') as archive:
            for member in archive:
                assert set(member.pax_headers) <= {'path', 'mtime'}
        restored = private(stage / 'restored')
        extractor.extract_exact(component, contract, output.path, bound, receipt['archive_digest'], restored, window)
        assert contract.compare_restore(component, bound, restored, _hashes(restored, rels), window)['verified']
    assert contract.check_source_unchanged(component, bound, _hashes(src, rels), window)['unchanged']
    assert {rel: contract._signature((src / rel).stat()) for rel in rels} == before
    assert receipt['member_count'] == bound['profile']['member_count']


@pytest.mark.parametrize('mask', [0o022, 0o077])
def test_unicode_empty_file_and_directory_roundtrip(tmp_path, mask):
    previous = os.umask(mask)
    try:
        roundtrip(tmp_path)
    finally:
        os.umask(previous)


def test_negative_nanosecond_mtime_roundtrip(tmp_path):
    roundtrip(tmp_path, -123456789)


def test_original_bsd_creationtime_refusal_and_new_route(tmp_path):
    tmp_path.chmod(0o700)
    source = private(tmp_path / 'source')
    directory = private(source / 'directory')
    file = directory / 'evidence'
    file.write_bytes(b'invented evidence')
    file.chmod(0o600)
    future = time.time_ns() + 2_000_000_000
    os.utime(directory, ns=(future, future))
    component, window = _component(), contract.Window(60)
    bound = bind(source, ['directory/evidence'], [], component, window)
    stage = private(tmp_path / 'stage')
    entries = stage / 'entries.nul'
    entries.write_bytes(b'.\0directory\0directory/evidence\0')
    bsd = stage / 'bsd.tar'
    component.tar_create(bsd, source, entries)
    with tarfile.open(bsd, 'r:') as reader:
        assert any('LIBARCHIVE.creationtime' in member.pax_headers for member in reader)
    with pytest.raises(contract.ContractError, match='archive_pax_unsupported'):
        contract.inspect_archive(component, bsd, bound, hashlib.sha256(bsd.read_bytes()).hexdigest(), window)
    with transport.prepare_fetch_output(stage.resolve() / 'exact.tar') as output:
        result = producer.create_exact(component, contract, bound, output, window)
        assert result['archive_digest'] == hashlib.sha256(output.path.read_bytes()).hexdigest()


def test_source_ctime_mutation_is_not_hidden_by_same_bytes(tmp_path, monkeypatch):
    src, _, rels, dirs = _setup(tmp_path)
    stage = private(tmp_path / 'stage')
    component, window = _component(), contract.Window(60)
    bound = bind(src, rels, dirs, component, window)
    target = src / bound['files'][0]['rel']
    original = target.read_bytes()
    expected_mtime = target.stat().st_mtime_ns
    actual = producer._Reader.read
    changed = False
    def change_after_read(self, size=-1):
        nonlocal changed
        data = actual(self, size)
        if not changed:
            changed = True
            target.write_bytes(original)
            os.utime(target, ns=(expected_mtime, expected_mtime))
        return data
    monkeypatch.setattr(producer._Reader, 'read', change_after_read)
    with transport.prepare_fetch_output(stage.resolve() / 'partial.tar') as output:
        with pytest.raises(producer.ArchiveError, match='source_signature'):
            producer.create_exact(component, contract, bound, output, window)
    assert changed


def test_output_must_remain_fresh(tmp_path):
    src, _, rels, dirs = _setup(tmp_path)
    stage = private(tmp_path / 'stage')
    component, window = _component(), contract.Window(60)
    bound = bind(src, rels, dirs, component, window)
    with transport.prepare_fetch_output(stage.resolve() / 'partial.tar') as output:
        os.write(output.fd, b'occupied')
        with pytest.raises(producer.ArchiveError, match='output_not_fresh'):
            producer.create_exact(component, contract, bound, output, window)
        assert output.path.read_bytes() == b'occupied'


@pytest.mark.skipif(os.environ.get('K2B_RUN_UPPER_ENVELOPE') != '1',
                    reason='explicit upper-envelope fixture release required')
def test_upper_envelope_exact_producer_and_restore(tmp_path):
    """Same full admitted P2 envelope, new producer and actual P1 comparison."""
    standard._require_envelope_space(tmp_path)
    with tempfile.TemporaryDirectory(dir=tmp_path) as temporary:
        root = Path(temporary).resolve()
        source, stage = private(root / 'source'), private(root / 'stage')
        rels, _ = standard._envelope_paths()
        standard._write_envelope_fixture(source, rels, standard._ordered_envelope_sizes(), standard._envelope_blocks())
        component, window = _component(), contract.Window(600)
        bound = bind(source, rels, [], component, window)
        started = time.monotonic()
        with transport.prepare_fetch_output(stage / 'archive.tar') as output:
            result = producer.create_exact(component, contract, bound, output, window)
            measured = time.monotonic() - started
            assert result['member_count'] == standard.ENVELOPE_MEMBER_COUNT
            assert bound['profile']['total_bytes'] == standard.ENVELOPE_TOTAL_BYTES
            restored = private(stage / 'restored')
            extractor.extract_exact(component, contract, output.path, bound, result['archive_digest'], restored, window)
            assert contract.compare_restore(component, bound, restored, _hashes(restored, rels), window)['verified']
            assert contract.check_source_unchanged(component, bound, _hashes(source, rels), window)['unchanged']
        assert measured < 60
