import hashlib
import io
import os
import resource
import stat
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'scripts' / 'lib'))

import recovery_exact_extract as adapter  # noqa: E402
import manual_recovery_contract as contract
from manual_recovery_contract import (  # noqa: E402
    ContractError,
    Window,
    bind_hashes,
    compare_restore,
    inspect_archive,
    load_component,
    prepare_selection,
)

COMP = REPO / 'tests' / 'test_recovery_standard_tools.py'
PLAN = REPO / 'plans' / '2026-10-27_k2b-standard-tool-recovery-component.md'
MTIME = 1700000000123456789
P1_CONTRACT_SHA256 = '7942016c89560444b822befb4b2f770578ee49b4a5e8d00913a5dadadee6cb44'
NFC_PARENT = '\u4e2d\u6587'
NFD_PARENT = '\u4e2d\u6587\u0301'
EMOJI_PARENT = 'emoji'
SPACE_PARENT = 'spaces'
NFD_NAME = 'cafe\u0301.txt'
NFC_NAME = 'nfc/caf\u00e9.txt'
EMPTY_DIR = SPACE_PARENT + '/emptydir'


@pytest.fixture(autouse=True)
def frozen_p1_binding():
    assert hashlib.sha256(Path(contract.__file__).read_bytes()).hexdigest() == P1_CONTRACT_SHA256


def _write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    os.chmod(path, 0o640)
    os.utime(path, ns=(MTIME, MTIME))


def _make_source(root):
    os.chmod(root, 0o700)
    files = {}
    files[NFD_PARENT + '/' + NFD_NAME] = b'nfd data'
    files[NFC_NAME] = b'nfc data'
    files[EMOJI_PARENT + '/x\U0001f3c3.txt'] = b'run'
    files[SPACE_PARENT + '/a b.txt'] = b'space'
    for rel, data in files.items():
        parts = rel.split('/')
        cur = root
        for part in parts[:-1]:
            cur = cur / part
            if not os.path.lexists(cur):
                cur.mkdir()
                os.chmod(cur, 0o750)
        _write(root / rel, data)
    (root / EMPTY_DIR).mkdir()
    os.chmod(root / EMPTY_DIR, 0o750)
    return files


def _snapshot(root):
    snap = {}
    for cur, dirs, names in os.walk(root):
        for name in dirs + names:
            p = Path(cur) / name
            st = p.lstat()
            snap[str(p)] = (st.st_dev, st.st_ino, st.st_mode, st.st_size,
                            st.st_mtime_ns, st.st_ctime_ns, st.st_nlink)
    return snap


def _pax(seconds, nanos):
    return ('%d' % seconds) + '.' + ('%09d' % nanos)


def _tarinfo(name, kind, mode, size=0):
    ti = tarfile.TarInfo(name)
    ti.type = kind
    ti.mode = mode
    ti.size = size
    ti.uid = 1234
    ti.gid = 1234
    ti.uname = ''
    ti.gname = ''
    ti.pax_headers = {'mtime': _pax(MTIME // 10**9, MTIME % 10**9)}
    return ti


def _build_archive(source, entries, files_first=False, decorate=False):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode='w', format=tarfile.PAX_FORMAT) as tf:
        tf.addfile(_tarinfo('.', tarfile.DIRTYPE, 0o700))
        seen = set()
        directories, regulars = [], []
        for rel in entries:
            parts = rel.split('/')
            for i in range(1, len(parts)):
                parent = '/'.join(parts[:i])
                if parent not in seen:
                    seen.add(parent)
                    directories.append(parent)
            src = source / rel
            if src.is_dir():
                seen.add(rel)
                directories.append(rel)
            else:
                data = src.read_bytes()
                regulars.append((rel, data))
        def add_dirs():
            for rel in directories:
                name = './' + rel + '/' if decorate else rel
                tf.addfile(_tarinfo(name, tarfile.DIRTYPE, 0o750))
        def add_files():
            for rel, data in regulars:
                name = './' + rel if decorate else rel
                tf.addfile(_tarinfo(name, tarfile.REGTYPE, 0o640, len(data)), io.BytesIO(data))
        for operation in ((add_files, add_dirs) if files_first else (add_dirs, add_files)):
            operation()
    return buf.getvalue()


def _hashes(root, rels):
    rows = []
    for rel in rels:
        p = root / rel
        if p.is_file():
            h = hashlib.sha256(p.read_bytes()).hexdigest()
            rows.append(h + '  ' + str(p) + '\n')
    return ''.join(sorted(rows))


def _ledger(paths):
    rows = []
    for p in paths:
        h = hashlib.sha256(Path(p).read_bytes()).hexdigest()
        rows.append(h + '  ' + str(p) + '\n')
    return ''.join(sorted(rows))


def _listing(root):
    found = []
    for cur, dirs, names in os.walk(root):
        for name in sorted(dirs + names):
            full = Path(cur) / name
            if full.is_file():
                found.append(str(full.relative_to(root)))
    return sorted(found)


def _setup(tmp_path):
    os.chmod(tmp_path, 0o700)
    src = tmp_path / 'src'
    src.mkdir()
    files = _make_source(src)
    rels = _listing(src)
    dirs = [EMPTY_DIR]
    return src, files, rels, dirs


def _component():
    return load_component(COMP, PLAN)


def _select(comp, source, files, dirs):
    return prepare_selection(comp, source, files, dirs, Window(60))


def _roundtrip(tmp_path, files_first=False, decorate=False, root_mode=0o700):
    src, files, rels, dirs = _setup(tmp_path)
    src.chmod(root_mode)
    before = _snapshot(src)
    entries = rels + [EMPTY_DIR]
    arch = tmp_path / 'a.tar'
    archive_bytes = _build_archive(src, entries, files_first, decorate)
    if root_mode != 0o700:
        with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode='r:') as reader:
            records = [(entry, reader.extractfile(entry).read() if entry.isreg() else None) for entry in reader]
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode='w', format=tarfile.PAX_FORMAT) as writer:
            for entry, data in records:
                if entry.name == '.':
                    entry.mode = root_mode
                writer.addfile(entry, io.BytesIO(data) if data is not None else None)
        archive_bytes = buffer.getvalue()
    arch.write_bytes(archive_bytes)
    os.chmod(arch, 0o600)
    sha = hashlib.sha256(arch.read_bytes()).hexdigest()
    comp = _component()
    sel = _select(comp, src, rels, dirs)
    bound = bind_hashes(comp, sel, _hashes(src, rels), Window(60))
    ins = inspect_archive(comp, arch, bound, sha, Window(60))
    dest = tmp_path / 'dest'
    dest.mkdir(mode=0o700)
    out = adapter.extract_exact(comp, contract, arch, bound, sha, dest, Window(60))
    assert out['comparison'] == 'required_next_phase'
    ledger = _ledger(dest / r for r in rels)
    res = compare_restore(comp, bound, dest, ledger, Window(60))
    assert _snapshot(src) == before
    return src, dest, files, rels, ins, res


def test_roundtrip_p1(tmp_path):
    src, dest, files, rels, ins, res = _roundtrip(tmp_path)
    for rel in rels:
        assert (dest / rel).read_bytes() == files[rel]
        st = (dest / rel).stat()
        assert stat.S_IMODE(st.st_mode) == 0o640
        assert st.st_uid == os.getuid()
        assert (st.st_dev, st.st_ino) != ((src / rel).stat().st_dev, (src / rel).stat().st_ino)
        assert st.st_mtime_ns == MTIME
    assert (dest / EMPTY_DIR).is_dir()
    assert stat.S_IMODE((dest / EMPTY_DIR).stat().st_mode) == 0o750
    assert _listing(src) == _listing(dest)
    assert res is not None


def test_observed_names(tmp_path):
    src, dest, files, rels, ins, res = _roundtrip(tmp_path)
    for name in os.listdir(src / NFD_PARENT):
        assert (dest / NFD_PARENT / name).exists()
    for name in os.listdir(src):
        assert (dest / name).exists()
    assert len({str((dest / r).resolve()) for r in rels}) == len(rels)


def test_file_first(tmp_path):
    src, dest, files, rels, ins, res = _roundtrip(tmp_path, True)
    for rel in rels:
        assert (dest / rel).read_bytes() == files[rel]
    assert res is not None


def _neg(tmp_path, kind):
    src, files, rels, dirs = _setup(tmp_path)
    data = _build_archive(src, rels + [EMPTY_DIR])
    sha = hashlib.sha256(data).hexdigest()
    arch = tmp_path / 'a.tar'
    arch.write_bytes(data)
    comp = _component()
    sel = _select(comp, src, rels, dirs)
    bound = bind_hashes(comp, sel, _hashes(src, rels), Window(60))
    if kind == 'tampered':
        tampered = bytearray(data)
        idx = tampered.find(b'nfc data')
        assert idx >= 0
        tampered[idx] = ord('X')
        arch.write_bytes(bytes(tampered))
        sha = hashlib.sha256(arch.read_bytes()).hexdigest()
    if kind == 'symlink_ancestor':
        real = tmp_path / 'real'
        real.mkdir()
        link = tmp_path / 'link'
        link.symlink_to(real, target_is_directory=True)
        arch.rename(real / 'a.tar')
        dest = tmp_path / 'dest'
        dest.mkdir(mode=0o700)
        with pytest.raises((adapter.ExtractError, ContractError)):
            adapter.extract_exact(comp, contract, link / 'a.tar', bound, sha, dest, Window(60))
        assert not list(dest.iterdir())
        return
    if kind == 'changed_sha':
        sha = '0' * 64
    dest = tmp_path / 'dest'
    if kind != 'symlink_dest':
        dest.mkdir(mode=0o700)
    if kind == 'occupied':
        (dest / 'f').write_bytes(b'x')
    if kind == 'symlink_dest':
        real = tmp_path / 'real'
        real.mkdir()
        dest.symlink_to(real, target_is_directory=True)
    with pytest.raises((adapter.ExtractError, ContractError)):
        adapter.extract_exact(comp, contract, arch, bound, sha, dest, Window(60))
    if kind in ('tampered', 'changed_sha'):
        assert not list(dest.iterdir())


@pytest.mark.parametrize(
    'kind', ['tampered', 'occupied', 'symlink_ancestor', 'symlink_dest', 'changed_sha']
)
def test_negative(tmp_path, kind):
    _neg(tmp_path, kind)


@pytest.mark.parametrize('breadth,depth', [(300, 1), (1, 70)])
def test_constant_descriptor_budget(tmp_path, breadth, depth):
    os.chmod(tmp_path, 0o700)
    src = tmp_path / 'src'
    src.mkdir(mode=0o700)
    rels = ['/'.join([f'd{i}'] * depth) + '/f.txt' for i in range(breadth)]
    for rel in rels:
        _write(src / rel, rel.encode())
        os.chmod((src / rel).parent, 0o750)
    for path in src.rglob('*'):
        if path.is_dir():
            path.chmod(0o750)
    comp = _component()
    bound = bind_hashes(comp, _select(comp, src, rels, []), _hashes(src, rels), Window(60))
    arc = tmp_path / 'a.tar'
    arc.write_bytes(_build_archive(src, rels, True))
    dest = tmp_path / 'dest'
    dest.mkdir(mode=0o700)
    limits = resource.getrlimit(resource.RLIMIT_NOFILE)
    try:
        resource.setrlimit(resource.RLIMIT_NOFILE, (min(64, limits[0]), limits[1]))
        adapter.extract_exact(comp, contract, arc, bound, hashlib.sha256(arc.read_bytes()).hexdigest(), dest, Window(60))
        assert compare_restore(comp, bound, dest, _hashes(dest, rels), Window(60))
    finally:
        resource.setrlimit(resource.RLIMIT_NOFILE, limits)


def test_archive_replacement_after_real_inspection(tmp_path, monkeypatch):
    src, _, rels, dirs = _setup(tmp_path)
    comp = _component()
    bound = bind_hashes(comp, _select(comp, src, rels, dirs), _hashes(src, rels), Window(60))
    arc = tmp_path / 'a.tar'
    arc.write_bytes(_build_archive(src, rels + [EMPTY_DIR]))
    replacement = tmp_path / 'replacement.tar'
    replacement.write_bytes(arc.read_bytes())
    dest = tmp_path / 'dest'
    dest.mkdir(mode=0o700)
    actual = contract.inspect_archive
    def inspect_then_replace(*args):
        report = actual(*args)
        replacement.replace(arc)
        return report
    monkeypatch.setattr(contract, 'inspect_archive', inspect_then_replace)
    with pytest.raises(adapter.ExtractError, match='archive_identity_changed'):
        adapter.extract_exact(comp, contract, arc, bound, hashlib.sha256(arc.read_bytes()).hexdigest(), dest, Window(60))
    assert not list(dest.iterdir())


def test_bsd_name_environment_witness(tmp_path):
    """Environment evidence only, separate from adapter coverage."""
    src, _, rels, dirs = _setup(tmp_path)
    comp = _component()
    bound = bind_hashes(comp, _select(comp, src, rels, dirs), _hashes(src, rels), Window(60))
    arc = tmp_path / 'a.tar'
    arc.write_bytes(_build_archive(src, rels + [EMPTY_DIR]))
    dest = tmp_path / 'bsd'
    dest.mkdir(mode=0o700)
    subprocess.run(['/usr/bin/tar', '-xf', str(arc), '-C', str(dest)],
                   check=True, capture_output=True, timeout=30)
    source_names = os.listdir(src / 'nfc')
    output_names = os.listdir(dest / 'nfc')
    if source_names != output_names:
        with pytest.raises(ContractError):
            compare_restore(comp, bound, dest, _hashes(dest, _listing(dest)), Window(60))
    else:
        assert compare_restore(comp, bound, dest, _hashes(dest, rels), Window(60))


def test_decorated_member_parity(tmp_path):
    _roundtrip(tmp_path, decorate=True)


def test_restored_root_mode_inside_private_parent(tmp_path):
    _, dest, *_ = _roundtrip(tmp_path, root_mode=0o750)
    assert stat.S_IMODE(dest.stat().st_mode) == 0o750
    assert stat.S_IMODE(dest.parent.stat().st_mode) == 0o700


@pytest.mark.parametrize('pair', [('A.txt', 'a.txt'), ('caf\u00e9.txt', 'cafe\u0301.txt')])
def test_actual_p1_refuses_fold_collision_before_extraction(tmp_path, pair):
    tmp_path.chmod(0o700)
    src = tmp_path / 'src'
    src.mkdir(mode=0o700)
    _write(src / pair[0], b'invented')
    dest = tmp_path / 'dest'
    dest.mkdir(mode=0o700)
    with pytest.raises(ContractError, match='selection_duplicate'):
        _select(_component(), src, list(pair), [])
    assert not list(dest.iterdir())


def test_actual_p1_member_limit_refuses_before_extraction(tmp_path):
    tmp_path.chmod(0o700)
    src = tmp_path / 'src'
    src.mkdir(mode=0o700)
    with (src / 'large').open('wb') as stream:
        stream.truncate(contract.MAX_FILE_BYTES + 1)
    with pytest.raises(ContractError):
        _select(_component(), src, ['large'], [])


def test_declared_alias_uses_separate_real_p1_phase(tmp_path):
    tmp_path.chmod(0o700)
    src = tmp_path / 'src'
    src.mkdir(mode=0o700)
    for rel in ['System', 'wiki']:
        (src / rel).mkdir(mode=0o750)
        (src / rel).chmod(0o750)
    _write(src / 'wiki/log.md', b'invented log')
    (src / 'System/log.md').symlink_to('../wiki/log.md')
    comp = _component()
    sel = prepare_selection(comp, src, ['wiki/log.md'], [], Window(60),
                            alias={'path': 'System/log.md', 'target': '../wiki/log.md'})
    bound = bind_hashes(comp, sel, _hashes(src, ['wiki/log.md']), Window(60))
    control = contract.alias_control(bound)
    trusted = hashlib.sha256(control).hexdigest()
    arc = tmp_path / 'a.tar'
    arc.write_bytes(_build_archive(src, ['wiki/log.md', 'System']))
    dest = tmp_path / 'dest'
    dest.mkdir(mode=0o700)
    adapter.extract_exact(comp, contract, arc, bound, hashlib.sha256(arc.read_bytes()).hexdigest(), dest, Window(60))
    assert not (dest / 'System/log.md').exists()
    assert compare_restore(comp, bound, dest, _hashes(dest, ['wiki/log.md']), Window(60))
    assert contract.restore_declared_alias(comp, bound, dest, control, trusted, Window(60))['restored']
    assert os.readlink(dest / 'System/log.md') == '../wiki/log.md'


@pytest.mark.parametrize('kind', ['literal', 'case', 'normalization'])
def test_source_overlap_refused_before_any_output(tmp_path, kind):
    src, _, rels, dirs = _setup(tmp_path)
    if kind == 'normalization':
        renamed = tmp_path / 'caf\u00e9'
        src.rename(renamed)
        src = renamed
        parent = tmp_path / 'cafe\u0301'
    else:
        parent = src if kind == 'literal' else tmp_path / 'SRC'
    if not parent.exists():
        parent.mkdir(mode=0o700)
    dest = parent / 'dest'
    dest.mkdir(mode=0o700)
    comp = _component()
    bound = bind_hashes(comp, _select(comp, src, rels, dirs), _hashes(src, rels), Window(60))
    arc = tmp_path / 'a.tar'
    arc.write_bytes(_build_archive(src, rels + [EMPTY_DIR]))
    with pytest.raises(adapter.ExtractError, match='destination_overlaps_source'):
        adapter.extract_exact(comp, contract, arc, bound, hashlib.sha256(arc.read_bytes()).hexdigest(), dest, Window(60))
    assert not list(dest.iterdir())


def test_capacity_refusal_before_output_entries(tmp_path, monkeypatch):
    src, _, rels, dirs = _setup(tmp_path)
    comp = _component()
    bound = bind_hashes(comp, _select(comp, src, rels, dirs), _hashes(src, rels), Window(60))
    arc = tmp_path / 'a.tar'
    arc.write_bytes(_build_archive(src, rels + [EMPTY_DIR]))
    dest = tmp_path / 'dest'
    dest.mkdir(mode=0o700)
    class Full:
        f_bavail = 0
        f_frsize = 4096
    monkeypatch.setattr(adapter.os, 'statvfs', lambda path: Full())
    with pytest.raises(adapter.ExtractError, match='destination_capacity_insufficient'):
        adapter.extract_exact(comp, contract, arc, bound, hashlib.sha256(arc.read_bytes()).hexdigest(), dest, Window(60))
    assert not list(dest.iterdir())
