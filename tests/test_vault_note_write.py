#!/usr/bin/env python3
# tests/test_vault_note_write.py
# Focused tests for scripts/vault-note-write.py (simple two-Mac ordinary
# note writer). Every test runs against a synthetic vault under tmp_path via
# environment overrides; no real vault, state, provider, SSH or Syncthing
# surface is touched.

import hashlib
import importlib.util
import json
import os
import stat
import subprocess
import sys
import time

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(REPO_ROOT, "scripts", "vault-note-write.py")

MAX_NOTE_BYTES = 2 * 1024 * 1024


@pytest.fixture
def native_identity_case(tmp_path, monkeypatch):
    from types import SimpleNamespace
    mod = load_module()
    target = str(tmp_path / "note.md")
    (tmp_path / "note.md").write_text("synthetic", encoding="utf-8")
    expected = os.lstat(target)
    events = []
    control = {"opened": expected}
    monkeypatch.setattr(mod.sys, "platform", "darwin")
    monkeypatch.setattr(mod.fcntl, "F_GETPATH", 631, raising=False)
    monkeypatch.setattr(mod.os, "pathconf", lambda *args: 1024)

    def event(stage):
        events.append(stage)
        if control.get("error_stage") == stage:
            raise OSError(control["errno"], "synthetic native failure")

    def opened(path, flags):
        assert path == target
        assert flags & mod.os.O_NOFOLLOW and flags & mod.os.O_NONBLOCK
        assert flags & mod.os.O_ACCMODE == mod.os.O_RDONLY
        if mod.stat.S_ISDIR(control["opened"].st_mode):
            assert flags & mod.os.O_DIRECTORY
        event("open")
        return 901

    def fstat(fd):
        assert fd == 901
        event("fstat")
        return control["opened"]

    def getpath(fd, command, buffer):
        assert fd == 901 and command == 631
        assert isinstance(buffer, bytes) and buffer == bytes(1024)
        event("fcntl")
        return control.get("raw", (os.fsencode(target) + b"\0").ljust(1024, b"\0"))

    def close(fd):
        assert fd == 901
        event("close")

    monkeypatch.setattr(mod.os, "open", opened)
    monkeypatch.setattr(mod.os, "fstat", fstat)
    monkeypatch.setattr(mod.os, "close", close)
    monkeypatch.setattr(mod.os, "read", lambda *args: pytest.fail("payload read"))
    monkeypatch.setattr(mod.fcntl, "fcntl", getpath)
    return SimpleNamespace(mod=mod, target=target, expected=expected,
                           events=events, control=control)


@pytest.mark.parametrize("directory", [False, True])
def test_native_identity_bytes_result_and_close(native_identity_case, directory):
    from types import SimpleNamespace
    case = native_identity_case
    expected = case.expected
    if directory:
        expected = SimpleNamespace(st_mode=stat.S_IFDIR, st_dev=expected.st_dev, st_ino=expected.st_ino)
        case.control["opened"] = expected
    assert case.mod._native_path_identity(case.target, expected) == case.target
    assert case.events == ["open", "fstat", "fcntl", "close"]


@pytest.mark.skipif(sys.platform != "darwin", reason="Darwin native API integration")
@pytest.mark.parametrize("directory", [False, True])
def test_native_real_api_canonical_case_on_synthetic_volume(tmp_path, directory):
    mod = load_module()
    if not all((hasattr(mod.fcntl, "F_GETPATH"), hasattr(os, "O_NOFOLLOW"), hasattr(os, "O_NONBLOCK"))):
        pytest.skip("Native optimization unavailable on this runtime")
    canonical = tmp_path.resolve() / "Canonical-Leaf"
    if directory:
        canonical.mkdir()
    else:
        canonical.write_text("synthetic", encoding="utf-8")
    altered = canonical.with_name(canonical.name.swapcase())
    if not os.path.lexists(altered):
        pytest.skip("Synthetic volume is case-sensitive; altered-case lookup is absent")
    identity = mod._native_path_identity(str(altered), os.lstat(altered))
    assert identity == str(canonical)
    assert identity != str(altered)


@pytest.mark.parametrize("error", ["EACCES", "EPERM", "EIO", "ELOOP"])
def test_native_errors_have_clean_cli_refusal(vault_env, tmp_path, monkeypatch, capsys, error):
    mod = load_module()
    paths = [vault_env["vault"] / "wiki/work/index.md", vault_env["vault"] / "wiki/index.md"]
    before = [path.read_bytes() for path in paths]
    def failure(*args):
        raise OSError(getattr(mod.errno, error), "synthetic native refusal")
    monkeypatch.setattr(mod, "_native_path_identity", failure)
    assert inproc_write(mod, tmp_path, vault_env, "wiki/work/work_new-note.md", NOTE_NEW, "missing", "sum", "src") == 1
    output = capsys.readouterr()
    assert output.out == ""
    assert output.err.startswith("vault-note-write: input unavailable:")
    assert "synthetic native refusal" in output.err
    assert "Traceback" not in output.err
    assert not (vault_env["vault"] / "wiki/work/work_new-note.md").exists()
    assert [path.read_bytes() for path in paths] == before
    assert receipts(vault_env) == []


@pytest.mark.parametrize("unsupported", ["platform", "F_GETPATH", "O_NOFOLLOW", "O_NONBLOCK", "buffer"])
def test_native_identity_unavailable_without_open(native_identity_case, monkeypatch, unsupported):
    case = native_identity_case
    if unsupported == "platform":
        monkeypatch.setattr(case.mod.sys, "platform", "linux")
    elif unsupported == "buffer":
        monkeypatch.setattr(case.mod.os, "pathconf", lambda *args: 1025)
    else:
        owner = case.mod.fcntl if unsupported == "F_GETPATH" else case.mod.os
        monkeypatch.delattr(owner, unsupported, raising=False)
    assert case.mod._native_path_identity(case.target, case.expected) is None
    assert case.events == []


@pytest.mark.parametrize("stage", ["open", "fcntl"])
@pytest.mark.parametrize("error", ["ENOTSUP", "EOPNOTSUPP", "ENOSYS", "EINVAL", "ENOTTY"])
def test_native_identity_unsupported_requests_scan(native_identity_case, stage, error):
    case = native_identity_case
    case.control.update(error_stage=stage, errno=getattr(case.mod.errno, error))
    assert case.mod._native_path_identity(case.target, case.expected) is None
    assert case.events == (["open"] if stage == "open" else ["open", "fstat", "fcntl", "close"])


@pytest.mark.parametrize("stage", ["open", "fstat", "fcntl", "close"])
@pytest.mark.parametrize("error", ["EACCES", "EPERM", "EIO", "ELOOP"])
def test_native_identity_real_errors_fail_closed(native_identity_case, stage, error):
    case = native_identity_case
    number = getattr(case.mod.errno, error)
    case.control.update(error_stage=stage, errno=number)
    with pytest.raises(OSError) as caught:
        case.mod._native_path_identity(case.target, case.expected)
    assert caught.value.errno == number
    assert case.events == (["open"] if stage == "open" else
                           ["open", "fstat", "close"] if stage == "fstat" else
                           ["open", "fstat", "fcntl", "close"])


def test_native_identity_missing_open_requests_scan(native_identity_case):
    case = native_identity_case
    case.control.update(error_stage="open", errno=case.mod.errno.ENOENT)
    assert case.mod._native_path_identity(case.target, case.expected) is None
    assert case.events == ["open"]


@pytest.mark.parametrize("error", [ValueError, NotImplementedError])
def test_native_identity_path_limit_unavailable(native_identity_case, monkeypatch, error):
    case = native_identity_case
    def unavailable(*args):
        raise error("synthetic unavailable path limit")
    monkeypatch.setattr(case.mod.os, "pathconf", unavailable)
    assert case.mod._native_path_identity(case.target, case.expected) is None
    assert case.events == []


def test_native_identity_path_limit_io_error_fails_closed(native_identity_case, monkeypatch):
    case = native_identity_case
    def unavailable(*args):
        raise OSError(case.mod.errno.EIO, "synthetic I/O error")
    monkeypatch.setattr(case.mod.os, "pathconf", unavailable)
    with pytest.raises(OSError) as caught:
        case.mod._native_path_identity(case.target, case.expected)
    assert caught.value.errno == case.mod.errno.EIO
    assert case.events == []


@pytest.mark.parametrize("field", ["st_mode", "st_dev", "st_ino"])
def test_native_identity_changed_descriptor_refused(native_identity_case, field):
    from types import SimpleNamespace
    case = native_identity_case
    values = {key: getattr(case.expected, key) for key in ("st_mode", "st_dev", "st_ino")}
    values[field] = stat.S_IFIFO if field == "st_mode" else values[field] + 1
    case.control["opened"] = SimpleNamespace(**values)
    with pytest.raises(case.mod.WriteError, match="changed during native validation"):
        case.mod._native_path_identity(case.target, case.expected)
    assert case.events == ["open", "fstat", "close"]


@pytest.mark.parametrize("raw", [None, "not bytes", bytearray(1024), b"", b"/short\0",
    b"x" * 1024, bytes(1024), b"relative\0".ljust(1024, b"\0"),
    b"/x/../y\0".ljust(1024, b"\0"), b"/x\0junk".ljust(1024, b"\0")])
def test_native_identity_malformed_requests_scan_and_closes(native_identity_case, raw):
    case = native_identity_case
    case.control["raw"] = raw
    assert case.mod._native_path_identity(case.target, case.expected) is None
    assert case.events == ["open", "fstat", "fcntl", "close"]


def test_native_validator_exact_avoids_scan(tmp_path, monkeypatch):
    mod = load_module()
    (tmp_path / "wiki").mkdir()
    (tmp_path / "wiki/note.md").write_text("synthetic", encoding="utf-8")
    native = []
    def identity(path, metadata):
        native.append(path)
        return path
    monkeypatch.setattr(mod, "_native_path_identity", identity)
    monkeypatch.setattr(mod.os, "scandir", lambda *args: pytest.fail("native exact path scanned"))
    mod.check_target_chain(str(tmp_path), "wiki/note.md")
    assert native == [str(tmp_path / "wiki"), str(tmp_path / "wiki/note.md")]


@pytest.mark.parametrize("exact_first", [False, True, None])
def test_native_mismatch_scan_exact_wins_over_case_sibling(tmp_path, monkeypatch, exact_first):
    from types import SimpleNamespace
    mod = load_module()
    (tmp_path / "note.md").write_text("synthetic", encoding="utf-8")
    monkeypatch.setattr(mod, "_native_path_identity", lambda path, metadata: str(tmp_path / "NOTE.md"))
    closed = []
    class Scan:
        def __enter__(self):
            return self
        def __iter__(self):
            # Emulate case-sensitive sibling names on any host filesystem.
            names = ["NOTE.md"] if exact_first is None else ["note.md", "NOTE.md"] if exact_first else ["NOTE.md", "note.md"]
            return iter(SimpleNamespace(name=name) for name in names)
        def __exit__(self, *args):
            closed.append(True)
    monkeypatch.setattr(mod.os, "scandir", lambda *args: Scan())
    if exact_first is None:
        with pytest.raises(mod.WriteError, match="canonical on-disk case"):
            mod.check_target_chain(str(tmp_path), "note.md")
    else:
        mod.check_target_chain(str(tmp_path), "note.md")
    assert closed == [True]


def test_native_missing_tail_scans_only_existing_parent(tmp_path, monkeypatch):
    mod = load_module()
    (tmp_path / "wiki").mkdir()
    raw = os.scandir
    scans = []
    monkeypatch.setattr(mod, "_native_path_identity", lambda path, metadata: path)
    def scan(path):
        scans.append(path)
        return raw(path)
    monkeypatch.setattr(mod.os, "scandir", scan)
    mod.check_target_chain(str(tmp_path), "wiki/missing/note.md")
    assert scans == [str(tmp_path / "wiki")]


@pytest.mark.parametrize("existing", [False, True])
def test_native_fallback_rechecks_type_after_scan(tmp_path, monkeypatch, existing):
    mod = load_module()
    target = tmp_path / "note.md"
    other = tmp_path / "other.md"
    other.write_text("synthetic", encoding="utf-8")
    if existing:
        target.write_text("synthetic", encoding="utf-8")
    raw = os.scandir
    monkeypatch.setattr(mod, "_native_path_identity", lambda *args: None)
    class Scan:
        def __enter__(self):
            self.iterator = raw(tmp_path)
            return self.iterator
        def __exit__(self, *args):
            self.iterator.close()
            if existing:
                target.unlink()
            target.symlink_to(other)
    monkeypatch.setattr(mod.os, "scandir", lambda *args: Scan())
    with pytest.raises(mod.WriteError, match="refusing symlink"):
        mod.check_target_chain(str(tmp_path), "note.md")


@pytest.mark.parametrize("phase", ["initial", "after-scan"])
@pytest.mark.parametrize("error", ["EACCES", "EIO"])
def test_native_validator_metadata_errors_fail_closed(tmp_path, monkeypatch, phase, error):
    mod = load_module()
    target = tmp_path / "note.md"
    target.write_text("synthetic", encoding="utf-8")
    raw_stat, raw_scan = os.lstat, os.scandir
    after = []
    number = getattr(mod.errno, error)
    def metadata(path, *args, **kwargs):
        if str(path) == str(target) and (phase == "initial" or after):
            raise OSError(number, "synthetic metadata failure")
        return raw_stat(path, *args, **kwargs)
    class Scan:
        def __enter__(self):
            self.scan = raw_scan(tmp_path)
            return self.scan
        def __exit__(self, *args):
            self.scan.close()
            after.append(True)
    monkeypatch.setattr(mod.os, "lstat", metadata)
    monkeypatch.setattr(mod.os, "scandir", lambda *args: Scan())
    monkeypatch.setattr(mod, "_native_path_identity", lambda *args: None)
    with pytest.raises(OSError) as caught:
        mod.check_target_chain(str(tmp_path), "note.md")
    assert caught.value.errno == number
    assert after == ([] if phase == "initial" else [True])


@pytest.mark.parametrize("mode", [stat.S_IFIFO, stat.S_IFCHR, stat.S_IFBLK, stat.S_IFSOCK, stat.S_IFLNK, stat.S_IFDIR])
def test_native_validator_rejects_type_before_open(tmp_path, monkeypatch, mode):
    from types import SimpleNamespace
    mod = load_module()
    raw = os.lstat
    target = str(tmp_path / "note.md")
    def metadata(path, *args, **kwargs):
        return SimpleNamespace(st_mode=mode) if str(path) == target else raw(path, *args, **kwargs)
    monkeypatch.setattr(mod.os, "lstat", metadata)
    monkeypatch.setattr(mod, "_native_path_identity", lambda *args: pytest.fail("native opened rejected type"))
    with pytest.raises(mod.WriteError):
        mod.check_target_chain(str(tmp_path), "note.md")


@pytest.mark.parametrize("alias_first", [False, True])
def test_target_chain_stops_at_exact_name_and_closes_scan(tmp_path, monkeypatch, alias_first):
    from types import SimpleNamespace
    mod = load_module()
    monkeypatch.setattr(mod, "_native_path_identity", lambda *args: None)
    root = tmp_path / "vault"
    folder = root / "wiki/reference"
    folder.mkdir(parents=True)
    (folder / "note.md").write_text("synthetic", encoding="utf-8")
    expected = {str(root): "wiki", str(root / "wiki"): "reference", str(folder): "note.md"}
    active = set()
    closed = []
    yielded = []
    real_lstat = os.lstat

    class Scan:
        def __init__(self, path):
            self.path = str(path)

        def __enter__(self):
            active.add(self.path)
            return self

        def __iter__(self):
            name = expected[self.path]
            if alias_first:
                yielded.append(name.upper())
                yield SimpleNamespace(name=name.upper())
            yielded.append(name)
            yield SimpleNamespace(name=name)
            pytest.fail("enumerated unrelated names after exact match")

        def __exit__(self, *args):
            active.remove(self.path)
            closed.append(self.path)

    def checked_lstat(path, *args, **kwargs):
        assert not active, "directory scan must close before path metadata checks"
        return real_lstat(path, *args, **kwargs)

    monkeypatch.setattr(mod.os, "scandir", Scan)
    monkeypatch.setattr(mod.os, "lstat", checked_lstat)
    monkeypatch.setattr(mod.os, "listdir", lambda path: pytest.fail("full directory listing used"))
    mod.check_target_chain(str(root), "wiki/reference/note.md")
    assert closed == list(expected)
    assert not active
    assert len(yielded) == (6 if alias_first else 3)


@pytest.mark.parametrize("fault", ["alias", "missing", "iteration-error", "open-error"])
def test_target_chain_absent_exact_name_family(tmp_path, monkeypatch, fault):
    from types import SimpleNamespace
    mod = load_module()
    monkeypatch.setattr(mod, "_native_path_identity", lambda *args: None)
    root = tmp_path / "vault"
    root.mkdir()
    closed = []

    class Scan:
        def __enter__(self):
            return self

        def __iter__(self):
            yield SimpleNamespace(name="unrelated")
            if fault in ("alias", "iteration-error"):
                yield SimpleNamespace(name="NOTE.md")
            if fault == "iteration-error":
                raise PermissionError("synthetic iteration failure")

        def __exit__(self, *args):
            closed.append(True)

    def scan(path):
        if fault == "open-error":
            raise PermissionError("synthetic open failure")
        return Scan()

    monkeypatch.setattr(mod.os, "scandir", scan)
    if fault == "alias":
        with pytest.raises(mod.WriteError, match="canonical on-disk case"):
            mod.check_target_chain(str(root), "note.md")
    elif fault.endswith("error"):
        with pytest.raises(PermissionError, match="synthetic"):
            mod.check_target_chain(str(root), "note.md")
    else:
        mod.check_target_chain(str(root), "note.md")
    assert closed == ([] if fault == "open-error" else [True])
    assert list(root.iterdir()) == []


@pytest.mark.parametrize("kind", ["symlink-parent", "non-directory-parent", "directory-target"])
def test_target_chain_metadata_refusals_preserved(tmp_path, kind):
    mod = load_module()
    root = tmp_path / "vault"
    root.mkdir()
    if kind == "symlink-parent":
        outside = tmp_path / "outside"
        outside.mkdir()
        (root / "wiki").symlink_to(outside, target_is_directory=True)
        message = "refusing symlink"
    elif kind == "non-directory-parent":
        (root / "wiki").write_text("synthetic", encoding="utf-8")
        message = "non-directory"
    else:
        (root / "wiki/note.md").mkdir(parents=True)
        message = "not a regular file"
    with pytest.raises(mod.WriteError, match=message):
        mod.check_target_chain(str(root), "wiki/note.md")


def test_scoped_bookkeeping_unrelated_not_counted(vault_env, tmp_path, monkeypatch, capsys):
    mod = load_module()
    calls = []
    master_path = vault_env["vault"] / "wiki/index.md"
    master_path.write_text(master_path.read_text().replace("| Person pages | 1 |", "| Person pages | 17 |"))
    real = mod.count_pages_scoped
    people_dir = str((vault_env["vault"] / "wiki/people").resolve())
    work_dir = str((vault_env["vault"] / "wiki/work").resolve())

    def guarded(dir_abs):
        calls.append(os.path.realpath(dir_abs))
        if os.path.realpath(dir_abs).startswith(people_dir):
            raise AssertionError("unrelated scope counted: " + dir_abs)
        return real(dir_abs)

    monkeypatch.setattr(mod, "count_pages_scoped", guarded)
    real_scandir = os.scandir

    def guarded_scandir(path):
        if os.path.realpath(path).startswith(people_dir):
            raise AssertionError("unrelated directory enumerated")
        return real_scandir(path)

    monkeypatch.setattr(os, "scandir", guarded_scandir)
    rc = inproc_write(
        mod, tmp_path, vault_env,
        "wiki/work/work_new-note.md", NOTE_NEW, "missing", "sum", "src",
    )
    assert rc == 0, capsys.readouterr()
    assert calls == [work_dir]
    work_index = (vault_env["vault"] / "wiki/work/index.md").read_text(encoding="utf-8")
    assert "Entries: 3" in work_index
    master = (vault_env["vault"] / "wiki/index.md").read_text(encoding="utf-8")
    # The intentionally stale people snapshot remains 17; work becomes 3.
    assert "| [work/](work/index.md) | Work pages | 3 |" in master
    assert "| [people/](people/index.md) | Person pages | 17 |" in master
    assert "**Total wiki pages: 20**" in master

def test_scoped_bookkeeping_indexed_nested_ownership(vault_env, tmp_path, monkeypatch, capsys):
    mod = load_module()
    vault = vault_env["vault"]
    nested = vault / "wiki/work/deep"
    nested.mkdir(parents=True)
    (nested / "index.md").write_text(FOLDER_INDEX_4COL, encoding="utf-8")
    master = vault / "wiki/index.md"
    master.write_text(
        MASTER_INDEX.replace(
            "| [work/](work/index.md) | Work pages | 2 |",
            "| [work/](work/index.md) | Work pages | 2 |\n| [work/deep/](work/deep/index.md) | Deep | 2 |",
        ).replace("**Total wiki pages: 3**", "**Total wiki pages: 5**"),
        encoding="utf-8",
    )
    rc = inproc_write(
        mod, tmp_path, vault_env,
        "wiki/work/deep/work_new-note.md", NOTE_NEW, "missing", "sum", "src",
    )
    assert rc == 0, capsys.readouterr()
    mtext = master.read_text(encoding="utf-8")
    assert "| [work/deep/](work/deep/index.md) | Deep | 1 |" in mtext
    # Parent work row untouched (snapshot), total recomputed from rows.
    assert "| [work/](work/index.md) | Work pages | 2 |" in mtext
    assert "**Total wiki pages: 4**" in mtext

def test_scoped_bookkeeping_unindexed_nested_owned_by_ancestor(vault_env, tmp_path, monkeypatch, capsys):
    mod = load_module()
    vault = vault_env["vault"]
    nested = vault / "wiki/work/plain"
    nested.mkdir(parents=True)
    rc = inproc_write(
        mod, tmp_path, vault_env,
        "wiki/work/plain/work_new-note.md", NOTE_NEW, "missing", "sum", "src",
    )
    assert rc == 0, capsys.readouterr()
    master = (vault / "wiki/index.md").read_text(encoding="utf-8")
    assert "| [work/](work/index.md) | Work pages | 3 |" in master
    assert "**Total wiki pages: 4**" in master

def test_scoped_bookkeeping_missing_own_master_row_refused(vault_env, tmp_path, monkeypatch, capsys):
    mod = load_module()
    vault = vault_env["vault"]
    nested = vault / "wiki/work/deep"
    nested.mkdir(parents=True)
    (nested / "index.md").write_text(FOLDER_INDEX_4COL, encoding="utf-8")
    # No row for work/deep in the master index.
    master_before = (vault / "wiki/index.md").read_bytes()
    rc = inproc_write(
        mod, tmp_path, vault_env,
        "wiki/work/deep/work_new-note.md", NOTE_NEW, "missing", "sum", "src",
    )
    assert rc == 1, capsys.readouterr()
    assert (vault / "wiki/index.md").read_bytes() == master_before
    assert not (nested / "work_new-note.md").exists()
    assert receipts(vault_env) == []

def test_scoped_bookkeeping_malformed_master_before_mutation(vault_env, tmp_path, monkeypatch, capsys):
    mod = load_module()
    vault = vault_env["vault"]
    master = vault / "wiki/index.md"
    bad = MASTER_INDEX.replace(
        "| [work/](work/index.md) | Work pages | 2 |",
        "| [work/](work/index.md) | Work pages | lots |",
    )
    master.write_text(bad, encoding="utf-8")
    note_before = (vault / "wiki/work/work_kingdee-hris-recovery.md").read_bytes()
    rc = inproc_write(
        mod, tmp_path, vault_env,
        "wiki/work/work_new-note.md", NOTE_NEW, "missing", "sum", "src",
    )
    assert rc == 1, capsys.readouterr()
    assert master.read_text(encoding="utf-8") == bad
    assert (vault / "wiki/work/work_kingdee-hris-recovery.md").read_bytes() == note_before
    assert not (vault / "wiki/work/work_new-note.md").exists()
    assert receipts(vault_env) == []

def test_scoped_bookkeeping_partial_index_failure_repair(vault_env, tmp_path, monkeypatch, capsys):
    mod = load_module()
    real = inject_first_index_replace_failure(mod)
    try:
        rc = inproc_write(
            mod, tmp_path, vault_env,
            "wiki/work/work_new-note.md", NOTE_NEW, "missing", "sum", "src",
        )
    finally:
        mod.atomic_replace = real
    assert rc == 3, capsys.readouterr()
    partial = _receipt_payload(receipts(vault_env)[-1])
    assert partial["result"] == "partial"
    note = vault_env["vault"] / "wiki/work/work_new-note.md"
    assert note.exists()
    # Retry same operation repairs.
    rc2 = inproc_write(
        mod, tmp_path, vault_env,
        "wiki/work/work_new-note.md", NOTE_NEW, "missing", "sum", "src",
    )
    assert rc2 == 0, capsys.readouterr()
    final = _receipt_payload(receipts(vault_env)[-1])
    assert final["result"] in ("saved-local", "already-present")
    assert final["backups"]["note_after"] == partial["backups"]["note_after"]
    assert len(receipts(vault_env)) == 1
    master = (vault_env["vault"] / "wiki/index.md").read_text(encoding="utf-8")
    assert "| [work/](work/index.md) | Work pages | 3 |" in master
    assert "**Total wiki pages: 4**" in master

def test_scoped_bookkeeping_completed_replay_no_counts(vault_env, tmp_path, monkeypatch, capsys):
    mod = load_module()
    rc = inproc_write(
        mod, tmp_path, vault_env,
        "wiki/work/work_new-note.md", NOTE_NEW, "missing", "sum", "src",
    )
    assert rc == 0, capsys.readouterr()
    capsys.readouterr()
    receipt_before = receipts(vault_env)[0].read_bytes()
    calls = []
    real = mod.count_pages_scoped

    def guarded(dir_abs):
        calls.append(dir_abs)
        return real(dir_abs)

    monkeypatch.setattr(mod, "count_pages_scoped", guarded)
    master_before = (vault_env["vault"] / "wiki/index.md").read_bytes()
    rc2 = inproc_write(
        mod, tmp_path, vault_env,
        "wiki/work/work_new-note.md", NOTE_NEW, "missing", "sum", "src",
    )
    assert rc2 == 0, capsys.readouterr()
    out = json.loads(capsys.readouterr().out)
    assert out["result"] == "already-present"
    assert calls == []
    assert receipts(vault_env)[0].read_bytes() == receipt_before
    assert (vault_env["vault"] / "wiki/index.md").read_bytes() == master_before

@pytest.mark.parametrize("fault", [
    "count", "negative", "duplicate", "mismatch", "escape", "total", "missing-total", "duplicate-total", "folder",
])
def test_scoped_bookkeeping_invalid_indices_before_write(vault_env, tmp_path, monkeypatch, capsys, fault):
    mod = load_module()
    vault = vault_env["vault"]
    master = vault / "wiki/index.md"
    folder = vault / "wiki/work/index.md"
    row = "| [work/](work/index.md) | Work pages | 2 |"
    if fault == "folder":
        folder.write_text(folder.read_text().replace("Last updated:", "Broken header:"))
    else:
        replacements = {
            "count": row.replace("2 |", "many |"),
            "negative": row.replace("2 |", "-1 |"),
            "duplicate": row + "\n" + row,
            "mismatch": row.replace("(work/index.md)", "(work/other/index.md)"),
            "escape": row.replace("(work/index.md)", "(../work/index.md)"),
            "total": row,
            "missing-total": row,
            "duplicate-total": row,
        }
        text = master.read_text().replace(row, replacements[fault])
        if fault == "total":
            text = text.replace("**Total wiki pages: 3**", "**Total wiki pages: many**")
        elif fault == "missing-total":
            text = text.replace("**Total wiki pages: 3**", "")
        elif fault == "duplicate-total":
            text += "\n**Total wiki pages: 3**\n"
        master.write_text(text)
    before = (master.read_bytes(), folder.read_bytes())
    monkeypatch.setattr(mod, "count_pages_scoped", lambda path: pytest.fail("counted before validation"))
    assert inproc_write(mod, tmp_path, vault_env, "wiki/work/new.md", NOTE_NEW, "missing", "sum", "src") == 1
    capsys.readouterr()
    assert not (vault / "wiki/work/new.md").exists()
    assert receipts(vault_env) == []
    assert (master.read_bytes(), folder.read_bytes()) == before


def test_scoped_bookkeeping_fresh_master_failure_is_partial_and_repairable(vault_env, tmp_path, monkeypatch, capsys):
    mod = load_module()
    vault = vault_env["vault"]
    master = vault / "wiki/index.md"
    original = master.read_bytes()
    real_replace = mod.atomic_replace

    def concurrent_change(path, data, mode):
        result = real_replace(path, data, mode)
        if path.endswith("work_new-note.md"):
            master.write_text(master.read_text().replace("| Work pages | 2 |", "| Work pages | bad |"))
        return result

    monkeypatch.setattr(mod, "atomic_replace", concurrent_change)
    assert inproc_write(mod, tmp_path, vault_env, "wiki/work/work_new-note.md", NOTE_NEW, "missing", "sum", "src") == 3
    capsys.readouterr()
    assert "| Work pages | bad |" in master.read_text()
    partial = _receipt_payload(receipts(vault_env)[0])
    assert partial["result"] == "partial"
    assert (vault / "wiki/work/work_new-note.md").read_text() == NOTE_NEW
    master.write_bytes(original)
    monkeypatch.setattr(mod, "atomic_replace", real_replace)
    assert inproc_write(mod, tmp_path, vault_env, "wiki/work/work_new-note.md", NOTE_NEW, "missing", "sum", "src") == 0
    capsys.readouterr()
    final = _receipt_payload(receipts(vault_env)[0])
    assert final["backups"]["note_after"] == partial["backups"]["note_after"]
    assert "**Total wiki pages: 4**" in master.read_text()


@pytest.mark.parametrize("grow_after_note", [False, True])
def test_scoped_bookkeeping_master_size_bound(vault_env, tmp_path, monkeypatch, capsys, grow_after_note):
    mod = load_module()
    master = vault_env["vault"] / "wiki/index.md"
    original = master.read_bytes()
    monkeypatch.setattr(mod, "MAX_INDEX_BYTES", 4096)
    oversized = original + b"\n" + b"x" * 4096
    real_replace = mod.atomic_replace
    if grow_after_note:
        def grow(path, data, mode):
            result = real_replace(path, data, mode)
            if path.endswith("work_new-note.md"):
                master.write_bytes(oversized)
            return result
        monkeypatch.setattr(mod, "atomic_replace", grow)
    else:
        master.write_bytes(oversized)
    rc = inproc_write(mod, tmp_path, vault_env, "wiki/work/work_new-note.md", NOTE_NEW, "missing", "sum", "src")
    capsys.readouterr()
    assert rc == (3 if grow_after_note else 1)
    assert master.read_bytes() == oversized
    note = vault_env["vault"] / "wiki/work/work_new-note.md"
    assert note.exists() == grow_after_note
    if not grow_after_note:
        assert receipts(vault_env) == []
        return
    partial = _receipt_payload(receipts(vault_env)[0])
    assert partial["result"] == "partial"
    master.write_bytes(original)
    monkeypatch.setattr(mod, "atomic_replace", real_replace)
    assert inproc_write(mod, tmp_path, vault_env, "wiki/work/work_new-note.md", NOTE_NEW, "missing", "sum", "src") == 0
    capsys.readouterr()
    final = _receipt_payload(receipts(vault_env)[0])
    assert final["backups"]["note_after"] == partial["backups"]["note_after"]
    assert len(receipts(vault_env)) == 1
    assert "**Total wiki pages: 4**" in master.read_text()


@pytest.mark.parametrize("valid_target", [False, True])
def test_scoped_bookkeeping_child_index_symlink_policy(tmp_path, valid_target):
    mod = load_module()
    root = tmp_path / "scope"
    child = root / "child"
    child.mkdir(parents=True)
    (root / "root.md").write_text("root")
    (child / "child.md").write_text("child")
    target = tmp_path / "external-index.md"
    if valid_target:
        target.write_text("metadata ownership only")
    (child / "index.md").symlink_to(target)
    # Existing exists policy treats a dangling index as unindexed. Neither
    # the index target nor note contents are read by this metadata traversal.
    assert mod.count_pages_scoped(str(root)) == (1 if valid_target else 2)


def legacy_count_pages_scoped(dir_abs):
    """Designated compile count_pages semantics, minus .sync-conflict-*.md
    artifacts. Scoped to this writer: compile-index-update.py is not edited
    and its global semantics are unchanged."""
    if not os.path.isdir(dir_abs):
        return 0
    n = 0
    for entry in os.listdir(dir_abs):
        path = os.path.join(dir_abs, entry)
        if os.path.islink(path):
            continue
        if os.path.isdir(path):
            # A nested folder without its own index is owned by this index.
            if entry.casefold() != "shipped" and not os.path.exists(os.path.join(path, "index.md")):
                n += legacy_count_pages_scoped(path)
            continue
        if not entry.endswith(".md") or entry == "index.md":
            continue
        if ".sync-conflict-" in entry:
            continue
        if os.path.isfile(os.path.join(dir_abs, entry)):
            n += 1
    return n

def test_count_pages_scoped_real_fs_counts_and_no_child_probes(tmp_path, monkeypatch):
    mod = load_module()
    root = tmp_path / "vault"
    root.mkdir()
    (root / "a.md").write_text("x", encoding="utf-8")
    (root / "index.md").write_text("x", encoding="utf-8")
    (root / "b.MD").write_text("x", encoding="utf-8")
    (root / "note.sync-conflict-20200101.md").write_text("x", encoding="utf-8")
    plain = root / "plain"
    plain.mkdir()
    (plain / "c.md").write_text("x", encoding="utf-8")
    plain_nested = plain / "deeper"
    plain_nested.mkdir()
    (plain_nested / "d.md").write_text("x", encoding="utf-8")
    indexed = root / "indexed"
    indexed.mkdir()
    (indexed / "index.md").write_text("x", encoding="utf-8")
    (indexed / "e.md").write_text("x", encoding="utf-8")
    shipped = root / "ShIpPeD"
    shipped.mkdir()
    (shipped / "f.md").write_text("x", encoding="utf-8")
    link_file = root / "link.md"
    link_file.symlink_to(root / "a.md")
    link_dir = root / "linkdir"
    link_dir.symlink_to(plain)
    expected = legacy_count_pages_scoped(str(root))
    assert expected == 3
    visited = []
    original_lstat = os.lstat
    def counting_lstat(path, *args, **kwargs):
        visited.append(os.path.normpath(str(path)))
        return original_lstat(path, *args, **kwargs)
    monkeypatch.setattr(mod.os, "lstat", counting_lstat)
    probes = []
    original_islink = os.path.islink
    def counting_islink(path):
        probes.append(("islink", os.path.normpath(str(path))))
        return original_islink(path)
    monkeypatch.setattr(mod.os.path, "islink", counting_islink)
    original_isfile = os.path.isfile
    def counting_isfile(path):
        probes.append(("isfile", os.path.normpath(str(path))))
        return original_isfile(path)
    monkeypatch.setattr(mod.os.path, "isfile", counting_isfile)
    assert mod.count_pages_scoped(str(root)) == expected
    guard_visited = {os.path.normpath(str(root)), os.path.normpath(str(plain)), os.path.normpath(str(plain_nested))}
    assert set(visited) == guard_visited
    assert probes == []

def test_count_pages_scoped_missing_and_nondir_root(tmp_path):
    mod = load_module()
    missing = tmp_path / "nope"
    assert mod.count_pages_scoped(str(missing)) == legacy_count_pages_scoped(str(missing)) == 0
    file_root = tmp_path / "file.md"
    file_root.write_text("x", encoding="utf-8")
    assert mod.count_pages_scoped(str(file_root)) == legacy_count_pages_scoped(str(file_root)) == 0

def test_count_pages_scoped_fifo_excluded(tmp_path):
    mod = load_module()
    root = tmp_path / "vault"
    root.mkdir()
    (root / "real.md").write_text("x", encoding="utf-8")
    fifo_path = root / "pipe.md"
    try:
        os.mkfifo(str(fifo_path))
    except (AttributeError, OSError):
        pytest.skip("FIFO not supported on this platform")
    assert stat.S_ISFIFO(os.lstat(str(fifo_path)).st_mode)
    assert mod.count_pages_scoped(str(root)) == legacy_count_pages_scoped(str(root)) == 1

def test_count_pages_scoped_listing_error_propagates(tmp_path, monkeypatch):
    mod = load_module()
    root = tmp_path / "vault"
    root.mkdir()
    (root / "a.md").write_text("x", encoding="utf-8")
    real_listdir = os.listdir
    real_scandir = os.scandir
    target = os.path.normpath(str(root))
    def bad_listdir(path):
        if os.path.normpath(str(path)) == target:
            raise PermissionError("denied")
        return real_listdir(path)
    def bad_scandir(path):
        if os.path.normpath(str(path)) == target:
            raise PermissionError("denied")
        return real_scandir(path)
    monkeypatch.setattr(mod.os, "listdir", bad_listdir)
    monkeypatch.setattr(mod.os, "scandir", bad_scandir)
    with pytest.raises(PermissionError):
        mod.count_pages_scoped(str(root))

def test_count_pages_scoped_nested_permission_error_propagates(tmp_path, monkeypatch):
    mod = load_module()
    root = tmp_path / "vault"
    root.mkdir()
    (root / "a.md").write_text("x", encoding="utf-8")
    sub = root / "sub"
    sub.mkdir()
    (sub / "b.md").write_text("x", encoding="utf-8")
    real_scandir = os.scandir
    real_listdir = os.listdir
    target = os.path.normpath(str(sub))
    def bad_scandir(path):
        if os.path.normpath(str(path)) == target:
            raise PermissionError("denied")
        return real_scandir(path)
    def bad_listdir(path):
        if os.path.normpath(str(path)) == target:
            raise PermissionError("denied")
        return real_listdir(path)
    monkeypatch.setattr(mod.os, "scandir", bad_scandir)
    monkeypatch.setattr(mod.os, "listdir", bad_listdir)
    with pytest.raises(PermissionError):
        mod.count_pages_scoped(str(root))


@pytest.mark.parametrize("failure", ["all", "is_symlink", "is_dir", "is_file"])
def test_count_pages_scoped_entry_metadata_error_suppressed(tmp_path, monkeypatch, failure):
    mod = load_module()
    (tmp_path / "good.md").write_text("x", encoding="utf-8")
    (tmp_path / "bad.md").write_text("x", encoding="utf-8")
    real_scandir = os.scandir

    class Entry:
        def __init__(self, entry):
            self.entry = entry
            self.name = entry.name
            self.path = entry.path

        def is_symlink(self):
            if self.name == "bad.md" and failure in ("all", "is_symlink"):
                raise PermissionError("metadata denied")
            return self.entry.is_symlink()

        def is_dir(self, *, follow_symlinks):
            if self.name == "bad.md" and failure in ("all", "is_dir"):
                raise PermissionError("metadata denied")
            return self.entry.is_dir(follow_symlinks=follow_symlinks)

        def is_file(self, *, follow_symlinks):
            if self.name == "bad.md" and failure in ("all", "is_file"):
                raise PermissionError("metadata denied")
            return self.entry.is_file(follow_symlinks=follow_symlinks)

    class Scan:
        def __enter__(self):
            self.scan = real_scandir(tmp_path)
            return (Entry(entry) for entry in self.scan)

        def __exit__(self, *args):
            self.scan.close()

    # Exercise the actual old os.path helpers with underlying syscall failures.
    real_stat, real_lstat = os.stat, os.lstat
    bad_path = str(tmp_path / "bad.md")
    stat_calls = 0

    def failing_stat(path, *args, **kwargs):
        nonlocal stat_calls
        if os.fspath(path) == bad_path:
            stat_calls += 1
            if (failure == "all" or failure == "is_dir" and stat_calls == 1
                    or failure == "is_file" and stat_calls == 2):
                raise PermissionError("metadata denied")
        return real_stat(path, *args, **kwargs)

    def failing_lstat(path, *args, **kwargs):
        if os.fspath(path) == bad_path and failure in ("all", "is_symlink"):
            raise PermissionError("metadata denied")
        return real_lstat(path, *args, **kwargs)

    with monkeypatch.context() as legacy_patch:
        legacy_patch.setattr(os, "stat", failing_stat)
        legacy_patch.setattr(os, "lstat", failing_lstat)
        expected = legacy_count_pages_scoped(str(tmp_path))
    assert expected == (1 if failure in ("all", "is_file") else 2)
    monkeypatch.setattr(mod.os, "scandir", lambda path: Scan())
    assert mod.count_pages_scoped(str(tmp_path)) == expected


def test_count_pages_scoped_iteration_error_propagates(tmp_path, monkeypatch):
    mod = load_module()

    class Scan:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def __iter__(self):
            raise PermissionError("iteration denied")

    monkeypatch.setattr(mod.os, "scandir", lambda path: Scan())
    with pytest.raises(PermissionError):
        mod.count_pages_scoped(str(tmp_path))


def test_count_pages_scoped_failed_link_metadata_never_follows_targets(tmp_path, monkeypatch):
    mod = load_module()
    root = tmp_path / "root"
    root.mkdir()
    (root / "good.md").write_text("x", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "target.md").write_text("x", encoding="utf-8")
    (root / "file-link.md").symlink_to(outside / "target.md")
    (root / "dir-link").symlink_to(outside, target_is_directory=True)
    links = {str(root / "file-link.md"), str(root / "dir-link")}
    real_lstat, real_scandir = os.lstat, os.scandir

    def denied_link_stat(path, *args, **kwargs):
        if os.fspath(path) in links:
            raise PermissionError("link metadata denied")
        return real_lstat(path, *args, **kwargs)

    # The legacy false result can admit links through following path predicates.
    # The released contract explicitly excludes symlinks even under this error.
    with monkeypatch.context() as legacy_patch:
        legacy_patch.setattr(os, "lstat", denied_link_stat)
        assert legacy_count_pages_scoped(str(root)) == 3

    class Entry:
        def __init__(self, entry):
            self.entry = entry
            self.name = entry.name
            self.path = entry.path

        def is_symlink(self):
            if self.path in links:
                raise PermissionError("link metadata denied")
            return self.entry.is_symlink()

        def is_dir(self, *, follow_symlinks):
            assert follow_symlinks is False
            return self.entry.is_dir(follow_symlinks=follow_symlinks)

        def is_file(self, *, follow_symlinks):
            assert follow_symlinks is False
            return self.entry.is_file(follow_symlinks=follow_symlinks)

    class Scan:
        def __enter__(self):
            self.scan = real_scandir(root)
            return (Entry(entry) for entry in self.scan)

        def __exit__(self, *args):
            self.scan.close()

    monkeypatch.setattr(os, "scandir", lambda path: Scan())
    assert mod.count_pages_scoped(str(root)) == 1


def test_count_pages_scoped_closes_scans_before_recursion(tmp_path, monkeypatch):
    mod = load_module()
    leaf = tmp_path
    for _ in range(7):
        leaf = leaf / "nested"
        leaf.mkdir()
    (leaf / "note.md").write_text("x", encoding="utf-8")
    real_scandir = os.scandir
    active = 0

    class Scan:
        def __init__(self, path):
            self.path = path

        def __enter__(self):
            nonlocal active
            active += 1
            assert active == 1
            self.scan = real_scandir(self.path)
            return self.scan

        def __exit__(self, *args):
            nonlocal active
            self.scan.close()
            active -= 1

    monkeypatch.setattr(os, "scandir", Scan)
    assert mod.count_pages_scoped(str(tmp_path)) == 1
    assert active == 0


def test_count_pages_scoped_rejects_symlink_root_and_swapped_child(tmp_path, monkeypatch):
    mod = load_module()
    root = tmp_path / "root"
    root.mkdir()
    child = root / "child"
    child.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "external.md").write_text("x", encoding="utf-8")
    link = tmp_path / "root-link"
    link.symlink_to(outside, target_is_directory=True)
    assert mod.count_pages_scoped(str(link)) == 0
    real_scandir = os.scandir

    class Scan:
        def __enter__(self):
            self.scan = real_scandir(root)
            return self.scan

        def __exit__(self, *args):
            self.scan.close()
            child.rmdir()
            child.symlink_to(outside, target_is_directory=True)

    monkeypatch.setattr(os, "scandir", lambda path: Scan())
    assert mod.count_pages_scoped(str(root)) == 0

FOLDER_INDEX_4COL = """---
tags: [index, wiki]
date: 2026-04-08
type: index
origin: k2b-generate
---
# Wiki Work Index
Fixture work index.

Last updated: 2026-09-01 | Entries: 2

| Page | Status | Summary | Updated |
|------|--------|---------|---------|
| [[work_kingdee-hris-recovery]] | active | Kingdee HRIS recovery: vendor recovery through 2027 | 2026-09-01 |
| [[work_sjm-ai-implementation]] | active | SJM AI implementation hub | 2026-09-01 |
"""

FOLDER_INDEX_3COL = """---
tags: [index, wiki]
date: 2026-04-08
type: index
origin: k2b-generate
---
# Wiki People Index
Fixture people index.

Last updated: 2026-09-01 | Entries: 1

| Page | Summary | Updated |
|---------|---------|---------|
| [[person_Amy-Lin]] | SVP Treasury candidate | 2026-07-15 |
"""

MASTER_INDEX = """---
tags: [index, wiki, master]
date: 2026-04-15
type: index
origin: k2b-generate
---
# K2B Wiki -- Master Index
Fixture master index.

Last updated: 2026-09-01

## Subfolders

| Folder | Purpose | Entries |
|--------|---------|---------|
| [people/](people/index.md) | Person pages | 1 |
| [work/](work/index.md) | Work pages | 2 |

**Total wiki pages: 3**
"""

NOTE_KINGDEE_V1 = """---
tags: [work, sjm]
date: 2026-09-01
type: work
origin: keith
up: "[[MOC_SJM-Work]]"
---
# Kingdee HRIS Recovery

Vendor recovery running through 2027.
"""

NOTE_KINGDEE_V2 = """---
tags: [work, sjm]
date: 2026-09-14
type: work
origin: keith
up: "[[MOC_SJM-Work]]"
---
# Kingdee HRIS Recovery

Vendor recovery running through 2027. January 2027 payroll readiness confirmed.
"""

NOTE_NEW = """---
tags: [work, sjm]
date: 2026-09-14
type: work
origin: keith
up: "[[MOC_SJM-Work]]"
---
# Call Centre Consolidation

Consolidate the call centre off Macau.
"""


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    return sha256_bytes(text.encode("utf-8"))


@pytest.fixture()
def vault_env(tmp_path, monkeypatch):
    """Synthetic vault + local state + lock dirs, all under tmp_path."""
    vault = tmp_path / "vault"
    state = tmp_path / "state"
    lock_root = tmp_path / "locks"
    for d in (
        vault / "wiki" / "work",
        vault / "wiki" / "people",
        state,
        lock_root,
    ):
        d.mkdir(parents=True, exist_ok=True)
    (vault / "wiki" / "work" / "index.md").write_text(FOLDER_INDEX_4COL, encoding="utf-8")
    (vault / "wiki" / "people" / "index.md").write_text(FOLDER_INDEX_3COL, encoding="utf-8")
    (vault / "wiki" / "people" / "person_Amy-Lin.md").write_text(
        "---\ntags: [person]\ndate: 2026-07-15\ntype: person\norigin: keith\n"
        'up: "[[MOC_SJM-Work]]"\n---\n# Amy Lin\n\nTreasury candidate.\n',
        encoding="utf-8",
    )
    (vault / "wiki" / "index.md").write_text(MASTER_INDEX, encoding="utf-8")
    (vault / "wiki" / "work" / "work_kingdee-hris-recovery.md").write_text(
        NOTE_KINGDEE_V1, encoding="utf-8"
    )
    (vault / "wiki" / "work" / "work_sjm-ai-implementation.md").write_text(
        "---\ntags: [work]\ndate: 2026-09-01\ntype: work\norigin: keith\n"
        'up: "[[MOC_SJM-Work]]"\n---\n# SJM AI Implementation\n\nHub.\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("K2B_VAULT_PATH", str(vault))
    monkeypatch.setenv("K2B_VAULT_ROOT", str(vault))
    monkeypatch.setenv("K2B_LOCAL_STATE", str(state))
    monkeypatch.setenv("K2B_NOTE_WRITE_LOCK", str(lock_root / "note-write.lock.d"))
    monkeypatch.setenv("K2B_COMPILE_INDEX_LOCK", str(lock_root / "compile-index.lock.d"))
    return {
        "vault": vault,
        "state": state,
        "lock_root": lock_root,
        "env": dict(os.environ),
    }


def write_args(env, *extra):
    return [sys.executable, SCRIPT, *extra]


def run_cli(args, env, **kwargs):
    return subprocess.run(
        args,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=60,
        **kwargs,
    )


def draft(tmp_path, cli_env, content: str):
    """Private draft outside the vault."""
    draft_dir = tmp_path / "drafts"
    draft_dir.mkdir(exist_ok=True)
    f = draft_dir / ("draft-%d.md" % time.time_ns())
    f.write_text(content, encoding="utf-8")
    return f


def base_write_cmd(tmp_path, cli_env, note_rel, content, expected, summary, source_ref):
    content_file = draft(tmp_path, cli_env, content)
    cmd = write_args(
        cli_env,
        "write",
        "--path", note_rel,
        "--content-file", str(content_file),
        "--expected-sha256", expected,
        "--summary", summary,
        "--source-ref", source_ref,
    )
    return cmd


def kingdee_expected(env):
    target = env["vault"] / "wiki" / "work" / "work_kingdee-hris-recovery.md"
    return sha256_bytes(target.read_bytes())


def receipts(env):
    rdir = env["state"] / "note-write-receipts"
    if not rdir.is_dir():
        return []
    return sorted(rdir.glob("*.json"))


def result_of(proc):
    return json.loads(proc.stdout.decode("utf-8"))


# --- identity / offline save ---------------------------------------------

def load_module():
    spec = importlib.util.spec_from_file_location(
        "vault_note_write_under_test", SCRIPT
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.parametrize("note_rel", [
    "wiki/reference/minnie-family-memory",
    "wiki/reference/minnie-family-memory/approved-memories.md",
    "wiki/reference/minnie-family-memory/nested/other.md",
    "WIKI/REFERENCE/MINNIE-FAMILY-MEMORY/approved-memories.md",
    "wiki/reference/Minnie-Family-Memory/nested/other.md",
])
def test_minnie_generated_subtree_is_not_an_ordinary_note(note_rel):
    mod = load_module()
    with pytest.raises(mod.WriteError, match="Mini-owned generated memory"):
        mod.validate_note_rel(note_rel)


@pytest.mark.parametrize("note_rel", [
    "wiki/reference/minnie-family-memory.md",
    "wiki/reference/minnie-family-memory-other/note.md",
    "wiki/reference/family-discord-memory.md",
    "wiki/work/work_kingdee-hris-recovery.md",
])
def test_minnie_generated_guard_preserves_other_note_paths(note_rel):
    assert load_module().validate_note_rel(note_rel) == note_rel


@pytest.mark.parametrize("note_rel", [
    "wiki/reference/minnie-family-memory",
    "wiki/reference/minnie-family-memory/approved-memories.md",
    "WIKI/REFERENCE/MINNIE-FAMILY-MEMORY/approved-memories.md",
    "wiki/reference/minnie-family-memory/a/b/new.md",
])
def test_minnie_generated_save_refusal_preserves_existing_bytes(
    vault_env, tmp_path, note_rel,
):
    target = vault_env["vault"] / "wiki/reference/minnie-family-memory/approved-memories.md"
    target.parent.mkdir(parents=True)
    target.write_text(NOTE_KINGDEE_V1, encoding="utf-8")
    index = target.parent / "index.md"
    index.write_text(FOLDER_INDEX_4COL, encoding="utf-8")
    paths = [target, index, vault_env["vault"] / "wiki/index.md"]
    before = {p: p.read_bytes() for p in paths}
    (vault_env["state"] / "existing-state.json").write_text('{"preserve":true}\n')
    state_before = {
        p.relative_to(vault_env["state"]): p.read_bytes() if p.is_file() else None
        for p in vault_env["state"].rglob("*")
    }
    cmd = base_write_cmd(
        tmp_path, vault_env["env"], note_rel, NOTE_KINGDEE_V2,
        sha256_bytes(before[target]), "sum", "src",
    )
    proc = run_cli(cmd, vault_env["env"])
    assert proc.returncode == 1, (proc.stdout, proc.stderr)
    assert b"Mini-owned generated memory" in proc.stdout + proc.stderr
    assert {p: p.read_bytes() for p in paths} == before
    assert not (target.parent / "a").exists()
    assert {
        p.relative_to(vault_env["state"]): p.read_bytes() if p.is_file() else None
        for p in vault_env["state"].rglob("*")
    } == state_before


@pytest.mark.parametrize("directory_link", [False, True])
def test_minnie_generated_subtree_cannot_be_reached_through_symlink(
    vault_env, tmp_path, directory_link,
):
    target = vault_env["vault"] / "wiki/reference/minnie-family-memory/approved-memories.md"
    target.parent.mkdir(parents=True)
    target.write_text(NOTE_KINGDEE_V1, encoding="utf-8")
    if directory_link:
        link = target.parent.parent / "alternate-memory"
        link.symlink_to(target.parent, target_is_directory=True)
        note_rel = "wiki/reference/alternate-memory/approved-memories.md"
    else:
        link = target.parent.parent / "alternate-memory.md"
        link.symlink_to(target)
        note_rel = "wiki/reference/alternate-memory.md"
    cmd = base_write_cmd(
        tmp_path, vault_env["env"], note_rel, NOTE_KINGDEE_V2,
        sha256_text(NOTE_KINGDEE_V1), "sum", "src",
    )
    proc = run_cli(cmd, vault_env["env"])
    assert proc.returncode == 1, (proc.stdout, proc.stderr)
    assert b"symlink" in proc.stdout + proc.stderr
    assert link.is_symlink()
    assert target.read_bytes() == NOTE_KINGDEE_V1.encode()
    assert not receipts(vault_env)


@pytest.mark.parametrize("user", ["keithmbpm2", "keithcheung"])
def test_identity_fixtures_save_same_note_offline(vault_env, tmp_path, monkeypatch, user, capsys):
    mod = load_module()
    monkeypatch.setattr(mod, "current_user_name", lambda: user)
    content_file = draft(tmp_path, vault_env, NOTE_KINGDEE_V2)
    rc = mod.main(
        [
            "write",
            "--path", "wiki/work/work_kingdee-hris-recovery.md",
            "--content-file", str(content_file),
            "--expected-sha256", kingdee_expected(vault_env),
            "--summary", "Kingdee HRIS recovery: January 2027 payroll readiness confirmed",
            "--source-ref", "codex-session-2026-09-14-kingdee",
        ]
    )
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["result"] == "saved-local"
    assert "synchronization unverified" in out["note"]
    assert "synchronized" not in out["result"]
    target = vault_env["vault"] / "wiki" / "work" / "work_kingdee-hris-recovery.md"
    assert target.read_text(encoding="utf-8") == NOTE_KINGDEE_V2
    assert receipts(vault_env), "expected a local receipt"


# --- recovery copies ------------------------------------------------------

def test_before_after_copies_recover_mistaken_overwrite(vault_env, tmp_path):
    cli_env = dict(os.environ)
    original = (vault_env["vault"] / "wiki" / "work" / "work_kingdee-hris-recovery.md").read_bytes()
    cmd = base_write_cmd(
        tmp_path, cli_env,
        "wiki/work/work_kingdee-hris-recovery.md",
        NOTE_KINGDEE_V2, sha256_bytes(original),
        "updated summary", "src-1",
    )
    proc = run_cli(cmd, cli_env)
    assert proc.returncode == 0, proc.stderr.decode()
    backups = vault_env["state"] / "note-write-backups"
    before = list(backups.glob("before/*.md"))
    after = list(backups.glob("after/*.md"))
    assert before and after
    assert any(p.read_bytes() == original for p in before), "before copy must hold pre-write bytes"
    assert any(p.read_bytes() == NOTE_KINGDEE_V2.encode() for p in after)


# --- refusal cases leave target unchanged ---------------------------------

@pytest.mark.parametrize(
    "note_rel",
    [
        "wiki/work/work_kingdee-hris-recovery.md/../escape.md",
        "wiki/work/../context/policy-ledger.jsonl.md",
        "wiki/work/.hidden/note.md",
        "wiki/work/Shipped/note.md",
        "wiki/work/index.md",
        "wiki/context/context_note.md",
        "System/memory/active_rules.md",
        "wiki/work/note.txt",
    ],
)
def test_disallowed_paths_refused(vault_env, tmp_path, note_rel):
    cli_env = dict(os.environ)
    cmd = base_write_cmd(tmp_path, cli_env, note_rel, NOTE_NEW, "missing", "sum", "src")
    proc = run_cli(cmd, cli_env)
    assert proc.returncode == 1, (note_rel, proc.stdout, proc.stderr)
    if note_rel == "wiki/work/index.md":
        assert (vault_env["vault"] / note_rel).read_text(encoding="utf-8") == FOLDER_INDEX_4COL
    else:
        assert not (vault_env["vault"] / note_rel).exists()


def test_stale_expected_hash_leaves_target_unchanged(vault_env, tmp_path):
    cli_env = dict(os.environ)
    stale = "0" * 64
    cmd = base_write_cmd(
        tmp_path, cli_env,
        "wiki/work/work_kingdee-hris-recovery.md",
        NOTE_KINGDEE_V2, stale, "sum", "src",
    )
    proc = run_cli(cmd, cli_env)
    assert proc.returncode == 2, (proc.stdout, proc.stderr)
    target = vault_env["vault"] / "wiki" / "work" / "work_kingdee-hris-recovery.md"
    assert target.read_text(encoding="utf-8") == NOTE_KINGDEE_V1


def test_expected_missing_but_note_exists_refused(vault_env, tmp_path):
    cli_env = dict(os.environ)
    cmd = base_write_cmd(
        tmp_path, cli_env,
        "wiki/work/work_kingdee-hris-recovery.md",
        NOTE_KINGDEE_V2, "missing", "sum", "src",
    )
    proc = run_cli(cmd, cli_env)
    assert proc.returncode == 1
    target = vault_env["vault"] / "wiki" / "work" / "work_kingdee-hris-recovery.md"
    assert target.read_text(encoding="utf-8") == NOTE_KINGDEE_V1


def test_symlink_target_refused(vault_env, tmp_path):
    cli_env = dict(os.environ)
    link = vault_env["vault"] / "wiki" / "work" / "work_link.md"
    link.symlink_to(vault_env["vault"] / "wiki" / "work" / "work_kingdee-hris-recovery.md")
    cmd = base_write_cmd(tmp_path, cli_env, "wiki/work/work_link.md", NOTE_NEW, "missing", "sum", "src")
    proc = run_cli(cmd, cli_env)
    assert proc.returncode == 1
    assert link.is_symlink()


def test_fifo_target_refused(vault_env, tmp_path):
    cli_env = dict(os.environ)
    fifo = vault_env["vault"] / "wiki" / "work" / "work_fifo.md"
    os.mkfifo(fifo)
    cmd = base_write_cmd(tmp_path, cli_env, "wiki/work/work_fifo.md", NOTE_NEW, "missing", "sum", "src")
    proc = run_cli(cmd, cli_env)
    assert proc.returncode == 1
    assert stat.S_ISFIFO(fifo.stat().st_mode)


@pytest.mark.parametrize(
    "bad_content",
    [
        "",  # empty
        "\x00binary",  # NUL
        "no markdown structure at all",  # not markdown-looking
    ],
)
def test_malformed_content_refused(vault_env, tmp_path, bad_content):
    cli_env = dict(os.environ)
    cmd = base_write_cmd(tmp_path, cli_env, "wiki/work/work_new-note.md", bad_content, "missing", "sum", "src")
    proc = run_cli(cmd, cli_env)
    assert proc.returncode == 1
    assert not (vault_env["vault"] / "wiki" / "work" / "work_new-note.md").exists()


def test_oversized_content_refused(vault_env, tmp_path):
    cli_env = dict(os.environ)
    big = "# Big\n\n" + ("x" * MAX_NOTE_BYTES)
    cmd = base_write_cmd(tmp_path, cli_env, "wiki/work/work_big.md", big, "missing", "sum", "src")
    proc = run_cli(cmd, cli_env)
    assert proc.returncode == 1
    assert not (vault_env["vault"] / "wiki" / "work" / "work_big.md").exists()


def test_invalid_utf8_refused(vault_env, tmp_path):
    cli_env = dict(os.environ)
    draft_dir = tmp_path / "drafts"
    draft_dir.mkdir(exist_ok=True)
    f = draft_dir / "bad.md"
    f.write_bytes(b"# Note\n\n\xff\xfe invalid")
    cmd = write_args(
        cli_env, "write", "--path", "wiki/work/work_bad.md",
        "--content-file", str(f), "--expected-sha256", "missing",
        "--summary", "sum", "--source-ref", "src",
    )
    proc = run_cli(cmd, cli_env)
    assert proc.returncode == 1
    assert not (vault_env["vault"] / "wiki" / "work" / "work_bad.md").exists()


def test_content_file_inside_vault_refused(vault_env):
    cli_env = dict(os.environ)
    inside = vault_env["vault"] / "wiki" / "work" / "draft-inside.md"
    inside.write_text(NOTE_NEW, encoding="utf-8")
    cmd = write_args(
        cli_env, "write", "--path", "wiki/work/work_inside.md",
        "--content-file", str(inside), "--expected-sha256", "missing",
        "--summary", "sum", "--source-ref", "src",
    )
    proc = run_cli(cmd, cli_env)
    assert proc.returncode == 1
    assert not (vault_env["vault"] / "wiki" / "work" / "work_inside.md").exists()


def test_summary_cannot_inject_table_delimiter(vault_env, tmp_path):
    cli_env = dict(os.environ)
    cmd = base_write_cmd(
        tmp_path, cli_env,
        "wiki/work/work_kingdee-hris-recovery.md",
        NOTE_KINGDEE_V2, kingdee_expected(vault_env),
        "summary | forged row", "src",
    )
    proc = run_cli(cmd, cli_env)
    assert proc.returncode == 1
    target = vault_env["vault"] / "wiki" / "work" / "work_kingdee-hris-recovery.md"
    assert target.read_text(encoding="utf-8") == NOTE_KINGDEE_V1


def test_multiline_summary_refused(vault_env, tmp_path):
    cli_env = dict(os.environ)
    cmd = base_write_cmd(
        tmp_path, cli_env,
        "wiki/work/work_kingdee-hris-recovery.md",
        NOTE_KINGDEE_V2, kingdee_expected(vault_env),
        "line1\nline2", "src",
    )
    proc = run_cli(cmd, cli_env)
    assert proc.returncode == 1


# --- conflict copies ------------------------------------------------------

def make_conflict(vault_env, folder, stem):
    conflict = vault_env["vault"] / folder / ("%s.sync-conflict-20260914-120000.md" % stem)
    conflict.write_text("# conflict version\n", encoding="utf-8")
    return conflict


def test_conflict_copy_refused_and_both_versions_preserved(vault_env, tmp_path):
    cli_env = dict(os.environ)
    conflict = make_conflict(vault_env, "wiki/work", "work_kingdee-hris-recovery")
    target = vault_env["vault"] / "wiki" / "work" / "work_kingdee-hris-recovery.md"
    before_bytes = target.read_bytes()
    cmd = base_write_cmd(
        tmp_path, cli_env,
        "wiki/work/work_kingdee-hris-recovery.md",
        NOTE_KINGDEE_V2, sha256_bytes(before_bytes), "sum", "src",
    )
    proc = run_cli(cmd, cli_env)
    assert proc.returncode == 2, (proc.stdout, proc.stderr)
    assert target.read_bytes() == before_bytes
    assert conflict.read_text(encoding="utf-8") == "# conflict version\n"


def test_unrelated_conflict_does_not_block_different_note(vault_env, tmp_path):
    cli_env = dict(os.environ)
    make_conflict(vault_env, "wiki/work", "work_sjm-ai-implementation")
    cmd = base_write_cmd(tmp_path, cli_env, "wiki/work/work_new-note.md", NOTE_NEW, "missing", "sum", "src")
    proc = run_cli(cmd, cli_env)
    assert proc.returncode == 0, proc.stderr.decode()
    assert (vault_env["vault"] / "wiki" / "work" / "work_new-note.md").exists()


# --- lock exclusion -------------------------------------------------------

def test_writer_lock_exclusion(vault_env, tmp_path):
    cli_env = dict(os.environ)
    lock = vault_env["lock_root"] / "note-write.lock.d"
    lock.mkdir()
    cmd = base_write_cmd(tmp_path, cli_env, "wiki/work/work_new-note.md", NOTE_NEW, "missing", "sum", "src")
    proc = run_cli(cmd, cli_env)
    assert proc.returncode == 4
    assert not (vault_env["vault"] / "wiki" / "work" / "work_new-note.md").exists()


# --- idempotent replay ----------------------------------------------------

def test_idempotent_replay_creates_no_duplicate_rows_or_copies(vault_env, tmp_path):
    cli_env = dict(os.environ)
    cmd = base_write_cmd(
        tmp_path, cli_env,
        "wiki/work/work_kingdee-hris-recovery.md",
        NOTE_KINGDEE_V2, kingdee_expected(vault_env), "new summary", "src",
    )
    proc1 = run_cli(cmd, cli_env)
    assert proc1.returncode == 0, proc1.stderr.decode()
    backups_after_first = sorted(
        p.name for p in (vault_env["state"] / "note-write-backups").glob("*/*.md")
    )
    index_after_first = (vault_env["vault"] / "wiki" / "work" / "index.md").read_text(encoding="utf-8")
    receipts_after_first = len(receipts(vault_env))

    expected_now = kingdee_expected(vault_env)
    cmd2 = base_write_cmd(
        tmp_path, cli_env,
        "wiki/work/work_kingdee-hris-recovery.md",
        NOTE_KINGDEE_V2, expected_now, "new summary", "src",
    )
    proc2 = run_cli(cmd2, cli_env)
    assert proc2.returncode == 0, proc2.stderr.decode()
    out = result_of(proc2)
    assert out["result"] == "already-present"
    backups_after_second = sorted(
        p.name for p in (vault_env["state"] / "note-write-backups").glob("*/*.md")
    )
    assert backups_after_second == backups_after_first, "replay must not add history copies"
    index_after_second = (vault_env["vault"] / "wiki" / "work" / "index.md").read_text(encoding="utf-8")
    assert index_after_second.count("[[work_kingdee-hris-recovery]]") == 1
    assert index_after_second == index_after_first
    assert len(receipts(vault_env)) == receipts_after_first, "replay must not duplicate receipts"
    last_receipt = json.loads(receipts(vault_env)[-1].read_text(encoding="utf-8"))
    assert last_receipt["result"] == "saved-local", \
        "completed replay is a no-op and must not rewrite the finished receipt"
    assert last_receipt["synchronized"] == "unverified"


# --- new note, index integration ------------------------------------------

def test_new_note_updates_4col_index_counts_and_master(vault_env, tmp_path):
    cli_env = dict(os.environ)
    cmd = base_write_cmd(
        tmp_path, cli_env,
        "wiki/work/work_call-centre-consolidation.md",
        NOTE_NEW, "missing",
        "Consolidate the call centre off Macau", "src",
    )
    proc = run_cli(cmd, cli_env)
    assert proc.returncode == 0, proc.stderr.decode()
    index = (vault_env["vault"] / "wiki" / "work" / "index.md").read_text(encoding="utf-8")
    assert "[[work_call-centre-consolidation]]" in index
    assert "Consolidate the call centre off Macau" in index
    assert "| Entries: 3" in index
    master = (vault_env["vault"] / "wiki" / "index.md").read_text(encoding="utf-8")
    assert "| [work/](work/index.md) | Work pages | 3 |" in master
    assert "**Total wiki pages: 4**" in master


def test_existing_note_row_updated_and_other_rows_retained(vault_env, tmp_path):
    cli_env = dict(os.environ)
    cmd = base_write_cmd(
        tmp_path, cli_env,
        "wiki/work/work_kingdee-hris-recovery.md",
        NOTE_KINGDEE_V2, kingdee_expected(vault_env),
        "Kingdee HRIS recovery: payroll readiness confirmed", "src",
    )
    proc = run_cli(cmd, cli_env)
    assert proc.returncode == 0, proc.stderr.decode()
    index = (vault_env["vault"] / "wiki" / "work" / "index.md").read_text(encoding="utf-8")
    kingdee_rows = [l for l in index.splitlines() if "work_kingdee-hris-recovery" in l]
    assert len(kingdee_rows) == 1
    assert "Kingdee HRIS recovery: payroll readiness confirmed" in kingdee_rows[0]
    assert "[[work_sjm-ai-implementation]] | active | SJM AI implementation hub" in index
    assert "| Entries: 2" in index


def test_3col_index_gets_row_without_status(vault_env, tmp_path):
    cli_env = dict(os.environ)
    note = """---
tags: [person]
date: 2026-09-14
type: person
origin: keith
up: "[[MOC_SJM-Work]]"
---
# Person Bob Jones

SJM finance stakeholder.
"""
    cmd = base_write_cmd(
        tmp_path, cli_env,
        "wiki/people/person_Bob-Jones.md",
        note, "missing", "SJM finance stakeholder", "src",
    )
    proc = run_cli(cmd, cli_env)
    assert proc.returncode == 0, proc.stderr.decode()
    index = (vault_env["vault"] / "wiki" / "people" / "index.md").read_text(encoding="utf-8")
    rows = [l for l in index.splitlines() if "person_Bob-Jones" in l]
    assert len(rows) == 1
    assert rows[0].startswith("| [[person_Bob-Jones]] | SJM finance stakeholder |")
    assert rows[0].count("|") == 4  # 3-column shape


def test_4col_status_column_updated_when_supplied(vault_env, tmp_path):
    cli_env = dict(os.environ)
    content_file = draft(tmp_path, cli_env, NOTE_KINGDEE_V2)
    cmd = write_args(
        cli_env, "write",
        "--path", "wiki/work/work_kingdee-hris-recovery.md",
        "--content-file", str(content_file),
        "--expected-sha256", kingdee_expected(vault_env),
        "--summary", "Kingdee recovery status changed", "--source-ref", "src",
        "--status", "simmering",
    )
    proc = run_cli(cmd, cli_env)
    assert proc.returncode == 0, proc.stderr.decode()
    index = (vault_env["vault"] / "wiki" / "work" / "index.md").read_text(encoding="utf-8")
    rows = [l for l in index.splitlines() if "work_kingdee-hris-recovery" in l]
    assert len(rows) == 1
    assert "| simmering |" in rows[0]


def test_malformed_index_shape_fails_explicitly_without_note_write(vault_env, tmp_path):
    cli_env = dict(os.environ)
    bad_index = vault_env["vault"] / "wiki" / "work" / "index.md"
    bad_index.write_text("# no table here\n", encoding="utf-8")
    cmd = base_write_cmd(tmp_path, cli_env, "wiki/work/work_new-note.md", NOTE_NEW, "missing", "sum", "src")
    proc = run_cli(cmd, cli_env)
    assert proc.returncode == 1
    assert not (vault_env["vault"] / "wiki" / "work" / "work_new-note.md").exists()


def test_missing_index_blocks_save_explicitly(vault_env, tmp_path):
    cli_env = dict(os.environ)
    (vault_env["vault"] / "wiki" / "work" / "index.md").unlink()
    cmd = base_write_cmd(tmp_path, cli_env, "wiki/work/work_new-note.md", NOTE_NEW, "missing", "sum", "src")
    proc = run_cli(cmd, cli_env)
    assert proc.returncode == 1
    assert not (vault_env["vault"] / "wiki" / "work" / "work_new-note.md").exists()


def test_master_index_conflict_copy_refused_before_note_write(vault_env, tmp_path):
    cli_env = dict(os.environ)
    make_conflict(vault_env, "wiki", "index")
    cmd = base_write_cmd(tmp_path, cli_env, "wiki/work/work_new-note.md", NOTE_NEW, "missing", "sum", "src")
    proc = run_cli(cmd, cli_env)
    assert proc.returncode == 2
    assert not (vault_env["vault"] / "wiki" / "work" / "work_new-note.md").exists()


# --- partial save + retry repair ------------------------------------------

def test_index_write_failure_is_partial_and_retry_repairs(vault_env, tmp_path, monkeypatch, capsys):
    mod = load_module()
    real_replace = inject_first_index_replace_failure(mod)
    rc = inproc_write(
        mod, tmp_path, vault_env,
        "wiki/work/work_new-note.md", NOTE_NEW, "missing", "sum", "src",
    )
    assert rc == 3
    out = json.loads(capsys.readouterr().out)
    assert out["result"] == "partial"
    assert out["saved"] == "wiki/work/work_new-note.md"
    assert out["failed_step"] == "index"
    work_dir = vault_env["vault"] / "wiki/work"
    assert (work_dir / "work_new-note.md").read_text(encoding="utf-8") == NOTE_NEW

    # Retry the exact original command now that the fault is gone: the
    # note bytes equal this operation's saved-after hash and its receipt
    # is unfinished, so the indexes repair.
    mod.atomic_replace = real_replace
    expected = sha256_bytes((work_dir / "work_new-note.md").read_bytes())
    rc = inproc_write(
        mod, tmp_path, vault_env,
        "wiki/work/work_new-note.md", NOTE_NEW, expected, "sum", "src",
    )
    assert rc == 0
    assert "[[work_new-note]]" in (work_dir / "index.md").read_text(encoding="utf-8")


def test_backup_failure_blocks_write(vault_env, tmp_path):
    cli_env = dict(os.environ)
    backups = vault_env["state"] / "note-write-backups"
    backups.write_text("i am a file, not a dir", encoding="utf-8")
    cmd = base_write_cmd(tmp_path, cli_env, "wiki/work/work_new-note.md", NOTE_NEW, "missing", "sum", "src")
    proc = run_cli(cmd, cli_env)
    assert proc.returncode == 5, (proc.stdout, proc.stderr)
    assert not (vault_env["vault"] / "wiki" / "work" / "work_new-note.md").exists()


# --- conflicts subcommand (SessionStart support) --------------------------

def test_conflicts_subcommand_lists_bounded_paths(vault_env):
    cli_env = dict(os.environ)
    make_conflict(vault_env, "wiki/work", "work_kingdee-hris-recovery")
    proc = run_cli(write_args(cli_env, "conflicts"), cli_env)
    assert proc.returncode == 0
    out = proc.stdout.decode("utf-8")
    assert "work_kingdee-hris-recovery.sync-conflict-" in out


def test_conflicts_subcommand_silent_when_none(vault_env):
    cli_env = dict(os.environ)
    proc = run_cli(write_args(cli_env, "conflicts"), cli_env)
    assert proc.returncode == 0
    assert proc.stdout.decode("utf-8").strip() == ""


def test_conflicts_subcommand_unknown_on_missing_vault(vault_env, monkeypatch):
    monkeypatch.setenv("K2B_VAULT_PATH", "/nonexistent-vault-path-for-test")
    cli_env = dict(os.environ)
    proc = run_cli(write_args(cli_env, "conflicts"), cli_env)
    assert proc.returncode == 0
    out = proc.stdout.decode("utf-8")
    assert "unknown" in out


def test_conflicts_subcommand_performs_no_writes(vault_env, tmp_path):
    cli_env = dict(os.environ)
    make_conflict(vault_env, "wiki/work", "work_kingdee-hris-recovery")
    before = sorted(str(p.relative_to(vault_env["vault"])) for p in vault_env["vault"].rglob("*"))
    proc = run_cli(write_args(cli_env, "conflicts"), cli_env)
    assert proc.returncode == 0
    after = sorted(str(p.relative_to(vault_env["vault"])) for p in vault_env["vault"].rglob("*"))
    assert before == after, "conflicts must not create backup/state files in the vault"


# --- receipt hygiene ------------------------------------------------------

def test_private_state_files_are_0600(vault_env, tmp_path):
    cli_env = dict(os.environ)
    cmd = base_write_cmd(tmp_path, cli_env, "wiki/work/work_new-note.md", NOTE_NEW, "missing", "sum", "src")
    proc = run_cli(cmd, cli_env)
    assert proc.returncode == 0, proc.stderr.decode()
    for f in receipts(vault_env):
        assert stat.S_IMODE(f.stat().st_mode) == 0o600
    backups = vault_env["state"] / "note-write-backups"
    for f in list(backups.glob("before/*")) + list(backups.glob("after/*")):
        assert stat.S_IMODE(f.stat().st_mode) == 0o600


# --- correction gaps: identity, symlinks, state confinement -----------------

def test_unapproved_identity_refused(vault_env, tmp_path, monkeypatch, capsys):
    mod = load_module()
    monkeypatch.setattr(mod, "current_user_name", lambda: "mallory")
    content_file = draft(tmp_path, vault_env, NOTE_KINGDEE_V2)
    rc = mod.main(
        [
            "write",
            "--path", "wiki/work/work_kingdee-hris-recovery.md",
            "--content-file", str(content_file),
            "--expected-sha256", kingdee_expected(vault_env),
            "--summary", "s", "--source-ref", "src",
        ]
    )
    assert rc == 1
    target = vault_env["vault"] / "wiki" / "work" / "work_kingdee-hris-recovery.md"
    assert target.read_text(encoding="utf-8") == NOTE_KINGDEE_V1


def test_conflicts_subcommand_allowed_for_any_identity(vault_env, monkeypatch, capsys):
    mod = load_module()
    monkeypatch.setattr(mod, "current_user_name", lambda: "mallory")
    rc = mod.main(["conflicts"])
    assert rc == 0
    assert capsys.readouterr().out.strip() == ""


def test_draft_symlink_refused(vault_env, tmp_path):
    cli_env = dict(os.environ)
    real = draft(tmp_path, vault_env, NOTE_NEW)
    link = tmp_path / "drafts" / "linked-draft.md"
    link.symlink_to(real)
    cmd = write_args(
        cli_env, "write", "--path", "wiki/work/work_new-note.md",
        "--content-file", str(link), "--expected-sha256", "missing",
        "--summary", "sum", "--source-ref", "src",
    )
    proc = run_cli(cmd, cli_env)
    assert proc.returncode == 1
    assert not (vault_env["vault"] / "wiki" / "work" / "work_new-note.md").exists()


def test_folder_index_symlink_refused(vault_env, tmp_path):
    cli_env = dict(os.environ)
    index = vault_env["vault"] / "wiki" / "work" / "index.md"
    real = index.read_bytes()
    index.unlink()
    index.symlink_to(tmp_path / "elsewhere-index.md")
    (tmp_path / "elsewhere-index.md").write_bytes(real)
    cmd = base_write_cmd(tmp_path, cli_env, "wiki/work/work_new-note.md", NOTE_NEW, "missing", "sum", "src")
    proc = run_cli(cmd, cli_env)
    assert proc.returncode == 1
    assert not (vault_env["vault"] / "wiki" / "work" / "work_new-note.md").exists()


def test_master_index_symlink_refused(vault_env, tmp_path):
    cli_env = dict(os.environ)
    master = vault_env["vault"] / "wiki" / "index.md"
    real = master.read_bytes()
    master.unlink()
    master.symlink_to(tmp_path / "elsewhere-master.md")
    (tmp_path / "elsewhere-master.md").write_bytes(real)
    cmd = base_write_cmd(tmp_path, cli_env, "wiki/work/work_new-note.md", NOTE_NEW, "missing", "sum", "src")
    proc = run_cli(cmd, cli_env)
    assert proc.returncode == 1
    assert not (vault_env["vault"] / "wiki" / "work" / "work_new-note.md").exists()


def test_local_state_inside_vault_refused(vault_env, tmp_path, monkeypatch):
    monkeypatch.setenv("K2B_LOCAL_STATE", str(vault_env["vault"] / "state-inside"))
    cli_env = dict(os.environ)
    cmd = base_write_cmd(tmp_path, cli_env, "wiki/work/work_new-note.md", NOTE_NEW, "missing", "sum", "src")
    proc = run_cli(cmd, cli_env)
    assert proc.returncode == 1
    assert not (vault_env["vault"] / "wiki" / "work" / "work_new-note.md").exists()
    assert not (vault_env["vault"] / "state-inside").exists()


# --- correction gaps: frontmatter validation --------------------------------

FRONTMATTER_UNCLOSED = "---\ntags: [work]\ndate: 2026-09-14\n# never closed\n\nbody\n"
FRONTMATTER_YAML_LIST = "---\n- a\n- b\n---\n# Body\n\ntext\n"
FRONTMATTER_MISSING_UP = (
    "---\ntags: [work]\ndate: 2026-09-14\ntype: work\norigin: keith\n"
    "---\n# Body\n\ntext\n"
)
FRONTMATTER_EMPTY_BODY = (
    "---\ntags: [work]\ndate: 2026-09-14\ntype: work\norigin: keith\n"
    'up: "[[MOC_SJM-Work]]"\n---\n'


)


@pytest.mark.parametrize(
    "bad",
    [
        FRONTMATTER_UNCLOSED,
        FRONTMATTER_YAML_LIST,
        FRONTMATTER_MISSING_UP,
        FRONTMATTER_EMPTY_BODY,
    ],
)
def test_invalid_frontmatter_refused(vault_env, tmp_path, bad):
    cli_env = dict(os.environ)
    cmd = base_write_cmd(tmp_path, cli_env, "wiki/work/work_new-note.md", bad, "missing", "sum", "src")
    proc = run_cli(cmd, cli_env)
    assert proc.returncode == 1
    assert not (vault_env["vault"] / "wiki" / "work" / "work_new-note.md").exists()


# --- correction gaps: backup keys and receipts -------------------------------

def _receipt_payload(receipt_path):
    return json.loads(receipt_path.read_text(encoding="utf-8"))


def test_receipt_backup_keys_exist_on_disk(vault_env, tmp_path):
    cli_env = dict(os.environ)
    cmd = base_write_cmd(tmp_path, cli_env, "wiki/work/work_new-note.md", NOTE_NEW, "missing", "sum", "src")
    proc = run_cli(cmd, cli_env)
    assert proc.returncode == 0, proc.stderr.decode()
    receipt = _receipt_payload(receipts(vault_env)[-1])
    keys = [v for v in receipt.get("backups", {}).values() if v]
    assert keys, "receipt must record backup keys"
    for key in keys:
        assert (vault_env["state"] / key).exists(), "backup key missing: " + key


def test_partial_updates_same_receipt_and_retry_completes_it(vault_env, tmp_path, monkeypatch, capsys):
    mod = load_module()
    real_replace = inject_first_index_replace_failure(mod)
    work_dir = vault_env["vault"] / "wiki/work"
    rc = inproc_write(
        mod, tmp_path, vault_env,
        "wiki/work/work_new-note.md", NOTE_NEW, "missing", "sum", "src",
    )
    assert rc == 3
    capsys.readouterr()
    assert len(receipts(vault_env)) == 1, "partial must update the same operation receipt"
    receipt = _receipt_payload(receipts(vault_env)[0])
    assert receipt["result"] == "partial"
    assert receipt["saved"] == "wiki/work/work_new-note.md"

    mod.atomic_replace = real_replace
    expected = sha256_bytes((work_dir / "work_new-note.md").read_bytes())
    rc = inproc_write(
        mod, tmp_path, vault_env,
        "wiki/work/work_new-note.md", NOTE_NEW, expected, "sum", "src",
    )
    assert rc == 0
    assert len(receipts(vault_env)) == 1, "retry must finish the same operation receipt"
    receipt = _receipt_payload(receipts(vault_env)[0])
    assert receipt["result"] in ("saved-local", "already-present")
    assert "[[work_new-note]]" in (work_dir / "index.md").read_text(encoding="utf-8")


# --- correction gaps: conflicts read-failure honesty --------------------------

def test_conflicts_unknown_on_unreadable_dir(vault_env):
    cli_env = dict(os.environ)
    secret = vault_env["vault"] / "wiki" / "work" / "restricted"
    secret.mkdir()
    (secret / "note.md").write_text("x", encoding="utf-8")
    secret.chmod(0)
    try:
        proc = run_cli(write_args(cli_env, "conflicts"), cli_env)
        assert proc.returncode == 0
        assert "unknown" in proc.stdout.decode("utf-8")
    finally:
        secret.chmod(0o700)


def test_conflicts_deadline_reports_unknown(vault_env, monkeypatch, capsys):
    mod = load_module()
    ticks = iter(range(0, 10**12, 10**9))
    monkeypatch.setattr(time, "monotonic", lambda: next(ticks))
    rc = mod.main(["conflicts"])
    assert rc == 0
    assert "unknown" in capsys.readouterr().out


# --- correction pass: ten findings, regression-first -------------------------

import datetime as _dt


def _today():
    return _dt.date.today().isoformat()


NOTE_CONCEPT_V2 = """---
tags: [concept]
date: 2026-09-14
type: concept
origin: keith
up: "[[MOC_K2B-System]]"
---
# Agent Routing

Route tasks between agents. January rollout confirmed.
"""

NOTE_CONCEPT_NEW = """---
tags: [concept]
date: 2026-09-14
type: concept
origin: keith
up: "[[MOC_K2B-System]]"
---
# New Concept

Fresh concept note.
"""

NOTE_PERSON_CINDY_V2 = """---
tags: [person]
date: 2026-09-14
type: person
origin: keith
up: "[[MOC_TalentSignals]]"
---
# Cindy Zhao

Advisor v2.
"""

_PERSON_NOTE = """---
tags: [person]
date: 2026-06-01
type: person
origin: keith
up: "[[MOC_SJM-Work]]"
---
# {name}

Fixture person.
"""

PEOPLE_INDEX_MULTI = """---
tags: [index, wiki]
date: 2026-04-08
type: index
origin: k2b-generate
---
# Wiki People Index

Last updated: 2026-09-01 | Entries: 4

## SJM Resorts

| Page | Role/Context | Updated |
|------|--------------|---------|
| [[person_Amy-Lin]] | SVP Treasury candidate | 2026-07-15 |
| [[person_Gerard-Walker]] | CIO, group technology | 2026-06-01 |

## TalentSignals / Agency at Scale

| Page | Role/Context | Updated |
|------|--------------|---------|
| [[person_Cindy-Zhao]] | TalentSignals advisor | 2026-08-15 |

## Other

| Page | Role/Context | Updated |
|------|--------------|---------|
| [[person_Random-Dev]] | External engineer | 2026-05-01 |
"""

CONCEPTS_INDEX = """---
tags: [index, wiki]
date: 2026-04-08
type: index
origin: k2b-generate
---
# Wiki Concepts Index

Last updated: 2026-09-01 | Entries: 5

## In Progress

| Page | Ship / Phase | Priority | Effort | Updated |
|------|--------------|----------|--------|---------|
| [[concept_agent-routing]] | Phase 2 rollout | high | 5d | 2026-09-01 |

## Next Up

| Page | Ship / Phase | Priority | Effort | Updated |
|------|--------------|----------|--------|---------|
| [[concept_vector-memory]] | Not started | medium | 3d | 2026-08-20 |

## Backlog

| Page | Priority | Effort | Impact | Updated |
|------|----------|--------|--------|---------|
| [[concept_old-idea]] | low | 1d | small | 2026-08-01 |

## Architecture Patterns

| Page | Summary | Updated |
|------|---------|---------|
| [[concept_pattern-cache]] | Cache invalidation pattern | 2026-07-01 |

## Shipped

| Page | Shipped | Notes |
|------|---------|-------|
| [[concept_done-thing]] | 2026-06-01 | Retired after rollout |
"""

CONCEPT_STEMS = (
    "concept_agent-routing",
    "concept_vector-memory",
    "concept_old-idea",
    "concept_pattern-cache",
    "concept_done-thing",
)


def _person_note(name):
    return _PERSON_NOTE.format(name=name)


def install_people_multi(env):
    vault = env["vault"]
    (vault / "wiki/people/index.md").write_text(PEOPLE_INDEX_MULTI, encoding="utf-8")
    for stem, name in (
        ("person_Gerard-Walker", "Gerard Walker"),
        ("person_Cindy-Zhao", "Cindy Zhao"),
        ("person_Random-Dev", "Random Dev"),
    ):
        (vault / "wiki/people" / (stem + ".md")).write_text(
            _person_note(name), encoding="utf-8"
        )


def install_concepts(env):
    vault = env["vault"]
    folder = vault / "wiki/concepts"
    folder.mkdir(exist_ok=True)
    (folder / "index.md").write_text(CONCEPTS_INDEX, encoding="utf-8")
    for stem in CONCEPT_STEMS:
        (folder / (stem + ".md")).write_text(NOTE_CONCEPT_NEW, encoding="utf-8")
    master = vault / "wiki/index.md"
    text = master.read_text(encoding="utf-8")
    rows = [line for line in text.splitlines() if line.startswith("| [")]
    anchors = [line for line in rows if "(people/index.md)" in line]
    assert len(anchors) == 1
    assert "(concepts/index.md)" not in text
    existing_total = sum(int(line.split("|")[-2].strip()) for line in rows)
    total_lines = [line for line in text.splitlines()
                   if line.startswith("**Total wiki pages:")]
    assert total_lines == ["**Total wiki pages: %d**" % existing_total]
    count = len(CONCEPT_STEMS)
    row = "| [concepts/](concepts/index.md) | Concept pages | %d |" % count
    assert text.count(anchors[0]) == 1
    text = text.replace(anchors[0], row + "\n" + anchors[0], 1)
    text = text.replace(total_lines[0], "**Total wiki pages: %d**" % (existing_total + count), 1)
    assert text.count(row) == 1
    master.write_text(text, encoding="utf-8")


def inproc_write(mod, tmp_path, env, note_rel, content, expected, summary, src="src",
                 extra=None):
    assert mod.vault_root() == str(env["vault"].resolve()), "test vault override lost"
    assert mod.state_root() == str(env["state"]), "test state override lost"
    content_file = draft(tmp_path, env, content)
    argv = [
        "write", "--path", note_rel,
        "--content-file", str(content_file),
        "--expected-sha256", expected,
        "--summary", summary,
        "--source-ref", src,
    ]
    if extra:
        argv.extend(extra)
    return mod.main(argv)


def inject_first_index_replace_failure(mod):
    """Make the first index.md atomic_replace raise, producing a real
    partial save (folder index replace precedes the master replace).
    Returns the real implementation so the caller can restore it; uses
    direct attribute restore instead of monkeypatch.undo(), which would
    also undo the vault_env fixture's environment overrides."""
    real = mod.atomic_replace
    state = {"hit": False}

    def fake(path, data, mode):
        if os.path.basename(path) == "index.md" and not state["hit"]:
            state["hit"] = True
            raise OSError("injected folder index write failure")
        return real(path, data, mode)

    mod.atomic_replace = fake
    return real


@pytest.mark.parametrize("existing", [False, True])
def test_direct_exact_original_retry(vault_env, tmp_path, capsys, existing):
    mod = load_module()
    rel = "wiki/work/work_kingdee-hris-recovery.md" if existing else "wiki/work/work_new-note.md"
    target = vault_env["vault"] / rel
    expected = sha256_bytes(target.read_bytes()) if existing else "missing"
    real = inject_first_index_replace_failure(mod)
    assert inproc_write(mod, tmp_path, vault_env, rel, NOTE_NEW, expected, "sum") == 3
    capsys.readouterr()
    mod.atomic_replace = real
    assert inproc_write(mod, tmp_path, vault_env, rel, NOTE_NEW, expected, "sum") == 0
    assert target.read_text() == NOTE_NEW
    assert _receipt_payload(receipts(vault_env)[0])["result"] in ("saved-local", "already-present")


def test_direct_escaped_concept_table_and_phase_preserved(vault_env, tmp_path):
    install_concepts(vault_env)
    index = vault_env["vault"] / "wiki/concepts/index.md"
    index.write_text(index.read_text().replace("[[concept_done-thing]]", "[[Shipped/concept_done-thing\\|Done]]"))
    target = vault_env["vault"] / "wiki/concepts/concept_agent-routing.md"
    before = index.read_text()
    proc = run_cli(base_write_cmd(tmp_path, vault_env["env"], "wiki/concepts/concept_agent-routing.md", NOTE_CONCEPT_V2, sha256_bytes(target.read_bytes()), "New summary", "src"), vault_env["env"])
    assert proc.returncode == 0, proc.stderr.decode()
    original_phase = next(line for line in before.splitlines() if "[[concept_agent-routing]]" in line).split("|")[2].strip()
    assert "| [[concept_agent-routing]] | " + original_phase + " |" in index.read_text()
    assert "[[Shipped/concept_done-thing\\|Done]]" in index.read_text()


@pytest.mark.parametrize("kind", ["note", "folder", "master"])
def test_direct_conflict_arriving_during_intent_blocks_note(vault_env, tmp_path, monkeypatch, kind):
    mod = load_module()
    real = mod.write_receipt_file
    directory = vault_env["vault"] / ("wiki" if kind == "master" else "wiki/work")
    stem = "work_new-note" if kind == "note" else "index"
    def late_conflict(key, payload):
        result = real(key, payload)
        (directory / (stem + ".sync-conflict-late.md")).write_text("other version")
        return result
    monkeypatch.setattr(mod, "write_receipt_file", late_conflict)
    assert inproc_write(mod, tmp_path, vault_env, "wiki/work/work_new-note.md", NOTE_NEW, "missing", "sum") == 2
    assert not (vault_env["vault"] / "wiki/work/work_new-note.md").exists()


def test_direct_metadata_only_save_has_real_receipt(vault_env, tmp_path, capsys):
    mod = load_module()
    rel = "wiki/work/work_kingdee-hris-recovery.md"
    target = vault_env["vault"] / rel
    assert inproc_write(mod, tmp_path, vault_env, rel, target.read_text(), sha256_bytes(target.read_bytes()), "Revised summary") == 0
    result = json.loads(capsys.readouterr().out)
    assert (vault_env["state"] / result["receipt"]).is_file()
    assert "Revised summary" in (target.parent / "index.md").read_text()


def test_direct_effective_identity(vault_env, monkeypatch):
    mod = load_module()
    monkeypatch.setattr(mod.os, "getuid", lambda: 1)
    monkeypatch.setattr(mod.os, "geteuid", lambda: 2)
    from types import SimpleNamespace
    monkeypatch.setattr(mod.pwd, "getpwuid", lambda uid: SimpleNamespace(pw_name="keithcheung" if uid == 2 else "unauthorized"))
    assert mod.validate_operator() == "keithcheung"


def test_direct_import_preserves_path_environment(vault_env, monkeypatch):
    mod = load_module()
    monkeypatch.setenv("K2B_VAULT_ROOT", "/unused-sentinel")
    mod.load_compile_index()
    assert os.environ["K2B_VAULT_ROOT"] == "/unused-sentinel"


def test_direct_nested_note_uses_exact_relative_index_row(vault_env, tmp_path):
    folder = vault_env["vault"] / "wiki/work/nested"
    folder.mkdir()
    proc = run_cli(base_write_cmd(tmp_path, vault_env["env"], "wiki/work/nested/note.md", NOTE_NEW, "missing", "Nested note", "src"), vault_env["env"])
    assert proc.returncode == 0, proc.stderr.decode()
    assert "[[nested/note]]" in (folder.parent / "index.md").read_text()
    assert "[[note]]" not in (folder.parent / "index.md").read_text()
    assert "| Entries: 3" in (folder.parent / "index.md").read_text()


def test_residual_alias_row_updated_once(vault_env, tmp_path):
    index = vault_env["vault"] / "wiki/work/index.md"
    index.write_text(index.read_text().replace("[[work_kingdee-hris-recovery]]", "[[work_kingdee-hris-recovery\\|Kingdee]]"))
    target = index.parent / "work_kingdee-hris-recovery.md"
    proc = run_cli(base_write_cmd(tmp_path, vault_env["env"], "wiki/work/work_kingdee-hris-recovery.md", NOTE_NEW, sha256_bytes(target.read_bytes()), "Updated alias", "src"), vault_env["env"])
    assert proc.returncode == 0, proc.stderr.decode()
    assert index.read_text().count("work_kingdee-hris-recovery") == 1
    assert "[[work_kingdee-hris-recovery\\|Kingdee]]" in index.read_text()
    assert "Updated alias" in index.read_text()


def test_residual_original_retry_external_change(vault_env, tmp_path, capsys):
    mod = load_module()
    real = inject_first_index_replace_failure(mod)
    rel = "wiki/work/work_new-note.md"
    assert inproc_write(mod, tmp_path, vault_env, rel, NOTE_NEW, "missing", "sum") == 3
    mod.atomic_replace = real
    (vault_env["vault"] / rel).write_text("External change")
    capsys.readouterr()
    assert inproc_write(mod, tmp_path, vault_env, rel, NOTE_NEW, "missing", "sum") == 2
    assert "changed" in capsys.readouterr().err
    assert (vault_env["vault"] / rel).read_text() == "External change"


@pytest.mark.parametrize("after_folder", [False, True])
def test_residual_conflict_after_note_reports_manual_resolution(vault_env, tmp_path, monkeypatch, capsys, after_folder):
    mod = load_module()
    real = mod.atomic_replace
    def replace_and_conflict(path, data, mode):
        result = real(path, data, mode)
        trigger = "wiki/work/index.md" if after_folder else "wiki/work/work_new-note.md"
        if str(path).endswith(trigger):
            (vault_env["vault"] / "wiki/index.sync-conflict-late.md").write_text("Other version")
        return result
    monkeypatch.setattr(mod, "atomic_replace", replace_and_conflict)
    assert inproc_write(mod, tmp_path, vault_env, "wiki/work/work_new-note.md", NOTE_NEW, "missing", "sum") == 2
    out = json.loads(capsys.readouterr().out)
    assert out["failed_step"] == "conflict"
    assert "resolve" in out["note"]
    receipt = _receipt_payload(receipts(vault_env)[0])
    assert receipt["failed_step"] == "conflict"
    assert (vault_env["vault"] / "wiki/work/work_new-note.md").read_text() == NOTE_NEW


def test_direct_duplicate_section_is_ambiguous(vault_env, tmp_path):
    install_people_multi(vault_env)
    index = vault_env["vault"] / "wiki/people/index.md"
    index.write_text(index.read_text().replace("## Other", "## SJM Resorts"))
    proc = run_cli(base_write_cmd(tmp_path, vault_env["env"], "wiki/people/person_New.md", _person_note("New"), "missing", "Role", "src") + ["--section", "SJM Resorts"], vault_env["env"])
    assert proc.returncode == 1
    assert not (index.parent / "person_New.md").exists()


# --- finding 1: case-insensitive macOS aliases --------------------------------

@pytest.mark.parametrize(
    "alias",
    [
        "wiki/work/INDEX.md",
        "wiki/work/Index.md",
        "wiki/work/SHIPPED/note.md",
        "wiki/work/shipped/note.md",
        "wiki/work/ShIpPeD/note.md",
        "WIKI/WORK/note.md",
        "Wiki/Work/note.md",
    ],
)
def test_case_insensitive_aliases_refused(vault_env, tmp_path, alias):
    if "shipped" in alias.casefold():
        # Pre-create the aliased directory so a case-blind check would write.
        (vault_env["vault"] / os.path.dirname(alias)).mkdir(
            parents=True, exist_ok=True
        )
    cli_env = dict(os.environ)
    cmd = base_write_cmd(tmp_path, cli_env, alias, NOTE_NEW, "missing", "sum", "src")
    proc = run_cli(cmd, cli_env)
    assert proc.returncode == 1, (alias, proc.stdout, proc.stderr)
    assert (vault_env["vault"] / "wiki/work/index.md").read_text(
        encoding="utf-8"
    ) == FOLDER_INDEX_4COL, "index must be untouched for alias " + alias
    assert not (vault_env["vault"] / "wiki/work/note.md").exists()


# --- finding 2: owning-table selection over verified schemas ------------------

def test_ship_schema_existing_row_updates_only_owning_table(vault_env, tmp_path):
    install_concepts(vault_env)
    cli_env = dict(os.environ)
    target = vault_env["vault"] / "wiki/concepts/concept_agent-routing.md"
    cmd = base_write_cmd(
        tmp_path, cli_env,
        "wiki/concepts/concept_agent-routing.md",
        NOTE_CONCEPT_V2, sha256_bytes(target.read_bytes()),
        "Agent routing: phase 2 live", "src",
    )
    proc = run_cli(cmd, cli_env)
    assert proc.returncode == 0, proc.stderr.decode()
    index = (vault_env["vault"] / "wiki/concepts/index.md").read_text(encoding="utf-8")
    today = _today()
    row = [l for l in index.splitlines() if "concept_agent-routing" in l]
    assert row == [
        "| [[concept_agent-routing]] | Phase 2 rollout | high | 5d | %s |"
        % today
    ], "unrelated Ship/Phase, Priority and Effort metadata is preserved"
    # Unrelated tables and sections are byte-preserved.
    assert "| [[concept_vector-memory]] | Not started | medium | 3d | 2026-08-20 |" in index
    assert "| [[concept_old-idea]] | low | 1d | small | 2026-08-01 |" in index
    assert "| [[concept_pattern-cache]] | Cache invalidation pattern | 2026-07-01 |" in index
    assert "| [[concept_done-thing]] | 2026-06-01 | Retired after rollout |" in index
    for heading in ("## In Progress", "## Next Up", "## Backlog",
                    "## Architecture Patterns", "## Shipped"):
        assert heading in index


def test_new_note_multi_table_index_requires_section(vault_env, tmp_path):
    install_concepts(vault_env)
    cli_env = dict(os.environ)
    cmd = base_write_cmd(
        tmp_path, cli_env,
        "wiki/concepts/concept_new-thing.md", NOTE_CONCEPT_NEW, "missing",
        "Fresh concept", "src",
    )
    proc = run_cli(cmd, cli_env)
    assert proc.returncode == 1, (proc.stdout, proc.stderr)
    assert not (vault_env["vault"] / "wiki/concepts/concept_new-thing.md").exists()


def test_new_note_with_section_selects_owning_table(vault_env, tmp_path):
    install_concepts(vault_env)
    cli_env = dict(os.environ)
    content_file = draft(tmp_path, cli_env, NOTE_CONCEPT_NEW)
    cmd = write_args(
        cli_env, "write",
        "--path", "wiki/concepts/concept_new-thing.md",
        "--content-file", str(content_file),
        "--expected-sha256", "missing",
        "--summary", "Fresh concept",
        "--source-ref", "src",
        "--section", "Next Up",
    )
    proc = run_cli(cmd, cli_env)
    assert proc.returncode == 0, proc.stderr.decode()
    index = (vault_env["vault"] / "wiki/concepts/index.md").read_text(encoding="utf-8")
    today = _today()
    assert "| [[concept_new-thing]] | - | - | - | %s |" % today in index
    # Row landed under Next Up, nowhere else.
    sections = index.split("## ")
    next_up = [s for s in sections if s.startswith("Next Up")][0]
    in_progress = [s for s in sections if s.startswith("In Progress")][0]
    backlog = [s for s in sections if s.startswith("Backlog")][0]
    assert "concept_new-thing" in next_up
    assert "concept_new-thing" not in in_progress
    assert "concept_new-thing" not in backlog
    assert "## Shipped" in index
    assert "| [[concept_done-thing]] | 2026-06-01 | Retired after rollout |" in index


@pytest.mark.parametrize("section", ["Shipped", "No Such Section"])
def test_new_note_unknown_or_shipped_section_refused(vault_env, tmp_path, section):
    install_concepts(vault_env)
    cli_env = dict(os.environ)
    content_file = draft(tmp_path, cli_env, NOTE_CONCEPT_NEW)
    cmd = write_args(
        cli_env, "write",
        "--path", "wiki/concepts/concept_new-thing.md",
        "--content-file", str(content_file),
        "--expected-sha256", "missing",
        "--summary", "Fresh concept",
        "--source-ref", "src",
        "--section", section,
    )
    proc = run_cli(cmd, cli_env)
    assert proc.returncode == 1, (section, proc.stdout, proc.stderr)
    assert not (vault_env["vault"] / "wiki/concepts/concept_new-thing.md").exists()


def test_existing_row_in_shipped_table_is_not_saved(vault_env, tmp_path):
    install_concepts(vault_env)
    cli_env = dict(os.environ)
    target = vault_env["vault"] / "wiki/concepts/concept_done-thing.md"
    cmd = base_write_cmd(
        tmp_path, cli_env,
        "wiki/concepts/concept_done-thing.md",
        NOTE_CONCEPT_V2, sha256_bytes(target.read_bytes()),
        "attempted shipped update", "src",
    )
    proc = run_cli(cmd, cli_env)
    assert proc.returncode == 1, (proc.stdout, proc.stderr)
    assert target.read_text(encoding="utf-8") == NOTE_CONCEPT_NEW


def test_people_multi_section_existing_row_updates_only_owning_section(vault_env, tmp_path):
    install_people_multi(vault_env)
    cli_env = dict(os.environ)
    target = vault_env["vault"] / "wiki/people/person_Cindy-Zhao.md"
    cmd = base_write_cmd(
        tmp_path, cli_env,
        "wiki/people/person_Cindy-Zhao.md",
        NOTE_PERSON_CINDY_V2, sha256_bytes(target.read_bytes()),
        "TalentSignals advisor, expanded scope", "src",
    )
    proc = run_cli(cmd, cli_env)
    assert proc.returncode == 0, proc.stderr.decode()
    index = (vault_env["vault"] / "wiki/people/index.md").read_text(encoding="utf-8")
    today = _today()
    assert (
        "| [[person_Cindy-Zhao]] | TalentSignals advisor, expanded scope | %s |" % today
        in index
    )
    # Other sections byte-preserved.
    assert "| [[person_Amy-Lin]] | SVP Treasury candidate | 2026-07-15 |" in index
    assert "| [[person_Gerard-Walker]] | CIO, group technology | 2026-06-01 |" in index
    assert "| [[person_Random-Dev]] | External engineer | 2026-05-01 |" in index
    assert index.count("## ") == 3  # three section headings


def test_people_new_note_requires_section_and_section_selects(vault_env, tmp_path):
    install_people_multi(vault_env)
    cli_env = dict(os.environ)
    cmd = base_write_cmd(
        tmp_path, cli_env,
        "wiki/people/person_New-Person.md", _person_note("New Person"), "missing",
        "New joiner", "src",
    )
    proc = run_cli(cmd, cli_env)
    assert proc.returncode == 1, (proc.stdout, proc.stderr)

    content_file = draft(tmp_path, cli_env, _person_note("New Person"))
    cmd2 = write_args(
        cli_env, "write",
        "--path", "wiki/people/person_New-Person.md",
        "--content-file", str(content_file),
        "--expected-sha256", "missing",
        "--summary", "New joiner",
        "--source-ref", "src",
        "--section", "SJM Resorts",
    )
    proc2 = run_cli(cmd2, cli_env)
    assert proc2.returncode == 0, proc2.stderr.decode()
    index = (vault_env["vault"] / "wiki/people/index.md").read_text(encoding="utf-8")
    today = _today()
    assert "| [[person_New-Person]] | New joiner | %s |" % today in index
    sjm = [s for s in index.split("## ") if s.startswith("SJM Resorts")][0]
    talent = [s for s in index.split("## ") if s.startswith("TalentSignals")][0]
    assert "person_New-Person" in sjm
    assert "person_New-Person" not in talent


def test_status_rejected_for_non_status_schema(vault_env, tmp_path):
    install_people_multi(vault_env)
    cli_env = dict(os.environ)
    content_file = draft(tmp_path, cli_env, NOTE_PERSON_CINDY_V2)
    target = vault_env["vault"] / "wiki/people/person_Cindy-Zhao.md"
    before = target.read_bytes()
    cmd = write_args(
        cli_env, "write",
        "--path", "wiki/people/person_Cindy-Zhao.md",
        "--content-file", str(content_file),
        "--expected-sha256", sha256_bytes(before),
        "--summary", "s", "--source-ref", "src",
        "--status", "active",
    )
    proc = run_cli(cmd, cli_env)
    assert proc.returncode == 1, (proc.stdout, proc.stderr)
    assert target.read_bytes() == before
    assert (vault_env["vault"] / "wiki/people/index.md").read_text(
        encoding="utf-8"
    ) == PEOPLE_INDEX_MULTI


def test_first_compatible_table_is_not_owner_when_row_elsewhere(vault_env, tmp_path):
    # Two 4-column-compatible tables; the row lives in the second one.
    vault_env["vault"].joinpath("wiki/work/index.md").write_text(
        FOLDER_INDEX_4COL.replace(
            "| [[work_kingdee-hris-recovery]] | active | Kingdee HRIS recovery: vendor recovery through 2027 | 2026-09-01 |\n"
            "| [[work_sjm-ai-implementation]] | active | SJM AI implementation hub | 2026-09-01 |",
            "| [[work_sjm-ai-implementation]] | active | SJM AI implementation hub | 2026-09-01 |\n\n"
            "## On Hold\n\n"
            "| Page | Status | Summary | Updated |\n"
            "|------|--------|---------|---------|\n"
            "| [[work_kingdee-hris-recovery]] | active | Kingdee HRIS recovery: vendor recovery through 2027 | 2026-09-01 |",
        ),
        encoding="utf-8",
    )
    cli_env = dict(os.environ)
    target = vault_env["vault"] / "wiki/work/work_kingdee-hris-recovery.md"
    cmd = base_write_cmd(
        tmp_path, cli_env,
        "wiki/work/work_kingdee-hris-recovery.md",
        NOTE_KINGDEE_V2, sha256_bytes(target.read_bytes()),
        "updated summary", "src",
    )
    proc = run_cli(cmd, cli_env)
    assert proc.returncode == 0, proc.stderr.decode()
    index = (vault_env["vault"] / "wiki/work/index.md").read_text(encoding="utf-8")
    hold = [s for s in index.split("## ") if s.startswith("On Hold")][0]
    main_rows = index.split("## ")[0]
    assert "updated summary" in hold
    assert "work_kingdee-hris-recovery" not in main_rows


# --- finding 4: backups hold fresh bytes; expected-absence recheck ------------

def test_folder_index_backup_holds_fresh_bytes_not_stale_plan(vault_env, tmp_path, monkeypatch):
    mod = load_module()
    index_path = vault_env["vault"] / "wiki/work/index.md"
    original = index_path.read_bytes()
    external = original.replace(b"Entries: 2", b"Entries: 9")
    # A concurrent edit lands after planning read the index but before the
    # mutation phase re-reads it.
    real_receipt = mod.write_receipt_file

    def fake_receipt(op_key, payload):
        if payload.get("result") == "in-progress":
            index_path.write_bytes(external)
        return real_receipt(op_key, payload)

    monkeypatch.setattr(mod, "write_receipt_file", fake_receipt)
    target = vault_env["vault"] / "wiki/work/work_kingdee-hris-recovery.md"
    rc = inproc_write(
        mod, tmp_path, vault_env,
        "wiki/work/work_kingdee-hris-recovery.md",
        NOTE_KINGDEE_V2, sha256_bytes(target.read_bytes()),
        "fresh backup check", "src",
    )
    assert rc == 0
    receipt = _receipt_payload(receipts(vault_env)[-1])
    key = receipt["backups"]["folder_index_before"]
    assert (vault_env["state"] / key).read_bytes() == external, \
        "before copy must hold the fresh bytes being replaced, not the planning bytes"
    index_after = index_path.read_text(encoding="utf-8")
    assert "fresh backup check" in index_after
    assert "[[work_kingdee-hris-recovery]]" in index_after


def test_new_note_appearing_after_intent_is_refused(vault_env, tmp_path, monkeypatch, capsys):
    mod = load_module()
    target = vault_env["vault"] / "wiki/work/work_racer.md"
    real_receipt = mod.write_receipt_file

    def fake_receipt(op_key, payload):
        if payload.get("result") == "in-progress":
            target.write_text(NOTE_NEW, encoding="utf-8")
        return real_receipt(op_key, payload)

    monkeypatch.setattr(mod, "write_receipt_file", fake_receipt)
    rc = inproc_write(
        mod, tmp_path, vault_env,
        "wiki/work/work_racer.md", NOTE_NEW, "missing", "sum", "src",
    )
    assert rc == 2, capsys.readouterr()
    assert target.read_text(encoding="utf-8") == NOTE_NEW, "appeared file must be preserved"
    assert (vault_env["vault"] / "wiki/work/index.md").read_text(
        encoding="utf-8"
    ) == FOLDER_INDEX_4COL


# --- finding 5: post-note-mutation failure boundary ---------------------------

def test_partial_on_folder_index_decode_failure(vault_env, tmp_path, monkeypatch, capsys):
    mod = load_module()
    folder_index = vault_env["vault"] / "wiki/work/index.md"
    target = folder_index.parent / "work_new-note.md"
    real_read = mod._read_index_bytes

    def fake_read(path, label):
        if os.path.abspath(path) == str(folder_index) and target.exists():
            return b"\xff\xfe not utf-8"
        return real_read(path, label)

    monkeypatch.setattr(mod, "_read_index_bytes", fake_read)
    rc = inproc_write(
        mod, tmp_path, vault_env,
        "wiki/work/work_new-note.md", NOTE_NEW, "missing", "sum", "src",
    )
    assert rc == 3
    out = json.loads(capsys.readouterr().out)
    assert out["result"] == "partial"
    assert out["saved"] == "wiki/work/work_new-note.md"
    assert out["failed_step"] == "index"
    assert (vault_env["vault"] / "wiki/work/work_new-note.md").read_text(
        encoding="utf-8"
    ) == NOTE_NEW
    assert (vault_env["vault"] / "wiki/work/index.md").read_text(
        encoding="utf-8"
    ) == FOLDER_INDEX_4COL
    partial = _receipt_payload(receipts(vault_env)[0])
    note_before_retry = target.read_bytes()
    operation = mod.operation_key(
        "wiki/work/work_new-note.md", note_before_retry, "sum", None, "src", None
    )
    assert partial["operation"] == operation == receipts(vault_env)[0].stem
    assert partial["expected_sha256"] == "missing"
    assert partial["note_sha256"] == sha256_bytes(note_before_retry)
    assert partial["result"] == "partial"
    # The original missing expectation is accepted only as recorded_replay:
    # matching unfinished receipt, unchanged note bytes and same operation.
    monkeypatch.setattr(mod, "_read_index_bytes", real_read)
    assert inproc_write(
        mod, tmp_path, vault_env,
        "wiki/work/work_new-note.md", NOTE_NEW, "missing", "sum", "src",
    ) == 0
    capsys.readouterr()
    assert len(receipts(vault_env)) == 1
    final = _receipt_payload(receipts(vault_env)[0])
    assert final["backups"]["note_after"] == partial["backups"]["note_after"]
    assert final["operation"] == operation
    assert final["result"] == "already-present"
    for key in ("operation", "path", "note_sha256", "summary", "status",
                "section", "source_ref", "expected_sha256"):
        assert final[key] == partial[key]
    assert final["expected_sha256"] == "missing"
    assert target.read_bytes() == note_before_retry
    repaired = folder_index.read_text(encoding="utf-8")
    expected_folder = FOLDER_INDEX_4COL.replace(
        "Last updated: 2026-09-01 | Entries: 2",
        "Last updated: %s | Entries: 3" % _today(),
    ) + "| [[work_new-note]] | - | sum | %s |\n" % _today()
    assert repaired == expected_folder
    master = (vault_env["vault"] / "wiki/index.md").read_text(encoding="utf-8")
    expected_master = MASTER_INDEX.replace(
        "| [work/](work/index.md) | Work pages | 2 |",
        "| [work/](work/index.md) | Work pages | 3 |",
    ).replace("**Total wiki pages: 3**", "**Total wiki pages: 4**")
    assert master == expected_master
    receipt_bytes = receipts(vault_env)[0].read_bytes()
    assert inproc_write(
        mod, tmp_path, vault_env,
        "wiki/work/work_new-note.md", NOTE_NEW, "missing", "sum", "src",
    ) == 0
    capsys.readouterr()
    assert receipts(vault_env)[0].read_bytes() == receipt_bytes
    assert folder_index.read_text(encoding="utf-8") == expected_folder
    assert (vault_env["vault"] / "wiki/index.md").read_text(encoding="utf-8") == expected_master
    assert target.read_bytes() == note_before_retry


@pytest.mark.parametrize("fault", [
    "absent", "operation", "path", "note_sha256", "summary", "status",
    "section", "source_ref", "expected_sha256", "altered-note",
])
def test_missing_retry_requires_recorded_binding(vault_env, tmp_path, monkeypatch, capsys, fault):
    mod = load_module()
    rel = "wiki/work/work_new-note.md"
    real_replace = inject_first_index_replace_failure(mod)
    assert inproc_write(mod, tmp_path, vault_env, rel, NOTE_NEW, "missing", "sum", "src") == 3
    capsys.readouterr()
    monkeypatch.setattr(mod, "atomic_replace", real_replace)
    receipt_path = receipts(vault_env)[0]
    target = vault_env["vault"] / rel
    if fault == "absent":
        receipt_path.unlink()
    elif fault == "altered-note":
        target.write_text("External change", encoding="utf-8")
    else:
        payload = _receipt_payload(receipt_path)
        payload[fault] = "mismatched binding"
        receipt_path.write_text(json.dumps(payload), encoding="utf-8")
    watched = [target, target.parent / "index.md", vault_env["vault"] / "wiki/index.md"]
    before = [path.read_bytes() for path in watched]
    receipt_before = receipt_path.read_bytes() if receipt_path.exists() else None
    assert inproc_write(mod, tmp_path, vault_env, rel, NOTE_NEW, "missing", "sum", "src") == (2 if fault == "altered-note" else 1)
    capsys.readouterr()
    assert [path.read_bytes() for path in watched] == before
    assert (receipt_path.read_bytes() if receipt_path.exists() else None) == receipt_before
    assert len(receipts(vault_env)) == (0 if fault == "absent" else 1)


def test_partial_on_master_helper_systemexit(vault_env, tmp_path, monkeypatch, capsys):
    mod = load_module()
    real_helper = mod.update_master_index_text

    def flaky(text, index_rel, new_count, compile_mod):
        if new_count is not None:
            raise SystemExit(3)
        return real_helper(text, index_rel, new_count, compile_mod)

    monkeypatch.setattr(mod, "update_master_index_text", flaky)
    master_before = (vault_env["vault"] / "wiki/index.md").read_bytes()
    rc = inproc_write(
        mod, tmp_path, vault_env,
        "wiki/work/work_new-note.md", NOTE_NEW, "missing", "sum", "src",
    )
    assert rc == 3
    out = json.loads(capsys.readouterr().out)
    assert out["result"] == "partial"
    assert out["failed_step"] == "index"
    assert "3" in out["detail"]
    assert (vault_env["vault"] / "wiki/index.md").read_bytes() == master_before


def test_partial_on_after_copy_failure(vault_env, tmp_path, monkeypatch, capsys):
    mod = load_module()
    calls = {"n": 0}
    real_copy = mod.durable_private_copy

    def fake_copy(subdir, data):
        calls["n"] += 1
        # New note copies: note_after(1), folder_before(2), folder_after(3).
        if calls["n"] == 3:
            raise mod.BackupError("injected after-copy failure")
        return real_copy(subdir, data)

    monkeypatch.setattr(mod, "durable_private_copy", fake_copy)
    rc = inproc_write(
        mod, tmp_path, vault_env,
        "wiki/work/work_new-note.md", NOTE_NEW, "missing", "sum", "src",
    )
    assert rc == 3
    out = json.loads(capsys.readouterr().out)
    assert out["result"] == "partial"
    assert (vault_env["vault"] / "wiki/work/work_new-note.md").read_text(
        encoding="utf-8"
    ) == NOTE_NEW, "note save is not rolled back"
    assert (vault_env["vault"] / "wiki/work/index.md").read_text(
        encoding="utf-8"
    ) == FOLDER_INDEX_4COL, "index not replaced when its after-copy failed"


def test_partial_on_final_receipt_failure(vault_env, tmp_path, monkeypatch, capsys):
    mod = load_module()
    real_receipt = mod.write_receipt_file

    def fake_receipt(op_key, payload):
        if payload.get("result") == "saved-local":
            raise mod.BackupError("injected final receipt failure")
        return real_receipt(op_key, payload)

    monkeypatch.setattr(mod, "write_receipt_file", fake_receipt)
    rc = inproc_write(
        mod, tmp_path, vault_env,
        "wiki/work/work_new-note.md", NOTE_NEW, "missing", "sum", "src",
    )
    assert rc == 3
    out = json.loads(capsys.readouterr().out)
    assert out["result"] == "partial"
    assert out["failed_step"] == "receipt"
    assert out["saved"] == "wiki/work/work_new-note.md"
    receipt = _receipt_payload(receipts(vault_env)[-1])
    assert receipt["result"] == "partial"
    assert receipt["failed_step"] == "receipt"
    assert (vault_env["vault"] / "wiki/work/index.md").read_text(
        encoding="utf-8"
    ) != FOLDER_INDEX_4COL, "indexes were mutated even though the final receipt failed"


# --- finding 6: exact-command retry repair boundaries -------------------------

def test_interrupted_intent_receipt_retry_completes(vault_env, tmp_path, monkeypatch, capsys):
    mod = load_module()
    real_replace = mod.atomic_replace
    calls = {"n": 0}

    def fake_replace(path, data, mode):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("injected note write failure")
        return real_replace(path, data, mode)

    monkeypatch.setattr(mod, "atomic_replace", fake_replace)
    rc = inproc_write(
        mod, tmp_path, vault_env,
        "wiki/work/work_late.md", NOTE_NEW, "missing", "sum", "src",
    )
    assert rc == 1, "note-write failure is pre-mutation and distinct from partial"
    assert not (vault_env["vault"] / "wiki/work/work_late.md").exists()
    receipt = _receipt_payload(receipts(vault_env)[-1])
    assert receipt["result"] == "in-progress"

    cli_env = dict(os.environ)
    cmd = base_write_cmd(
        tmp_path, cli_env,
        "wiki/work/work_late.md", NOTE_NEW, "missing", "sum", "src",
    )
    proc = run_cli(cmd, cli_env)
    assert proc.returncode == 0, proc.stderr.decode()
    assert (vault_env["vault"] / "wiki/work/work_late.md").read_text(
        encoding="utf-8"
    ) == NOTE_NEW
    assert "[[work_late]]" in (vault_env["vault"] / "wiki/work/index.md").read_text(
        encoding="utf-8"
    )
    receipt = _receipt_payload(receipts(vault_env)[-1])
    assert receipt["result"] == "saved-local"


def test_partial_then_external_change_refused(vault_env, tmp_path, monkeypatch, capsys):
    mod = load_module()
    real_replace = inject_first_index_replace_failure(mod)
    work_dir = vault_env["vault"] / "wiki/work"
    rc = inproc_write(
        mod, tmp_path, vault_env,
        "wiki/work/work_new-note.md", NOTE_NEW, "missing", "sum", "src",
    )
    assert rc == 3
    capsys.readouterr()
    saved_hash = sha256_bytes((work_dir / "work_new-note.md").read_bytes())

    # External edit lands before the retry; the saved-after hash is stale.
    (work_dir / "work_new-note.md").write_text(NOTE_KINGDEE_V1, encoding="utf-8")
    cli_env = dict(os.environ)
    cmd3 = base_write_cmd(tmp_path, cli_env, "wiki/work/work_new-note.md", NOTE_NEW, saved_hash, "sum", "src")
    proc3 = run_cli(cmd3, cli_env)
    assert proc3.returncode == 2, (proc3.stdout, proc3.stderr)
    assert (work_dir / "work_new-note.md").read_text(encoding="utf-8") == NOTE_KINGDEE_V1
    receipt = _receipt_payload(receipts(vault_env)[-1])
    assert receipt["result"] == "partial"


def test_retry_preserves_original_backup_references(vault_env, tmp_path, monkeypatch, capsys):
    mod = load_module()
    real_replace = inject_first_index_replace_failure(mod)
    work_dir = vault_env["vault"] / "wiki/work"
    rc = inproc_write(
        mod, tmp_path, vault_env,
        "wiki/work/work_new-note.md", NOTE_NEW, "missing", "sum", "src",
    )
    assert rc == 3
    capsys.readouterr()
    partial_receipt = _receipt_payload(receipts(vault_env)[-1])
    assert partial_receipt["backups"].get("note_after"), \
        "partial receipt must keep the operation's durable copies"
    note_after_key = partial_receipt["backups"]["note_after"]
    assert (vault_env["state"] / note_after_key).exists()

    mod.atomic_replace = real_replace
    expected = sha256_bytes((work_dir / "work_new-note.md").read_bytes())
    rc = inproc_write(
        mod, tmp_path, vault_env,
        "wiki/work/work_new-note.md", NOTE_NEW, expected, "sum", "src",
    )
    assert rc == 0
    final_receipt = _receipt_payload(receipts(vault_env)[-1])
    assert final_receipt["backups"]["note_after"] == note_after_key, \
        "retry must preserve original backup references, not an empty replay map"


def test_different_operation_same_note_after_partial(vault_env, tmp_path, monkeypatch, capsys):
    mod = load_module()
    real_replace = inject_first_index_replace_failure(mod)
    work_dir = vault_env["vault"] / "wiki/work"
    rc = inproc_write(
        mod, tmp_path, vault_env,
        "wiki/work/work_new-note.md", NOTE_NEW, "missing", "first summary", "src",
    )
    assert rc == 3
    capsys.readouterr()
    receipt_files = receipts(vault_env)
    assert len(receipt_files) == 1
    first_key = receipt_files[0].name
    first_receipt = _receipt_payload(receipt_files[0])
    assert first_receipt["result"] == "partial"

    mod.atomic_replace = real_replace
    # Same bytes with different index metadata is a new operation. It must
    # update the index and issue its own real receipt, not skip the change.
    expected = sha256_bytes((work_dir / "work_new-note.md").read_bytes())
    rc = inproc_write(
        mod, tmp_path, vault_env,
        "wiki/work/work_new-note.md", NOTE_NEW, expected, "second summary", "src",
    )
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["result"] == "already-present"
    assert len(receipts(vault_env)) == 2
    assert (vault_env["state"] / out["receipt"]).is_file()
    assert "second summary" in (work_dir / "index.md").read_text()
    first_receipt = _receipt_payload(vault_env["state"] / "note-write-receipts" / first_key)
    assert first_receipt["result"] == "partial"

    # The unfinished original operation still repairs on its exact retry.
    rc = inproc_write(
        mod, tmp_path, vault_env,
        "wiki/work/work_new-note.md", NOTE_NEW, expected, "first summary", "src",
    )
    assert rc == 0
    index = (work_dir / "index.md").read_text(encoding="utf-8")
    assert "first summary" in index
    first_receipt = _receipt_payload(vault_env["state"] / "note-write-receipts" / first_key)
    assert first_receipt["result"] in ("saved-local", "already-present")


# --- finding 7: counts exclude unrelated conflict artifacts -------------------

def test_counts_exclude_unrelated_sync_conflict_artifacts(vault_env, tmp_path):
    cli_env = dict(os.environ)
    make_conflict(vault_env, "wiki/work", "work_sjm-ai-implementation")
    cmd = base_write_cmd(tmp_path, cli_env, "wiki/work/work_unrelated.md", NOTE_NEW, "missing", "sum", "src")
    proc = run_cli(cmd, cli_env)
    assert proc.returncode == 0, (proc.stdout, proc.stderr)
    index = (vault_env["vault"] / "wiki/work/index.md").read_text(encoding="utf-8")
    assert "| Entries: 3" in index, "folder count must exclude the unrelated .sync-conflict artifact"
    master = (vault_env["vault"] / "wiki/index.md").read_text(encoding="utf-8")
    assert "| [work/](work/index.md) | Work pages | 3 |" in master
    assert "**Total wiki pages: 4**" in master


# --- finding 8: effective UID resolution --------------------------------------

def test_env_user_spoofing_does_not_change_identity(vault_env, tmp_path):
    cli_env = dict(os.environ)
    cli_env["LOGNAME"] = "root"
    cli_env["USER"] = "mallory"
    cmd = base_write_cmd(
        tmp_path, cli_env,
        "wiki/work/work_kingdee-hris-recovery.md",
        NOTE_KINGDEE_V2, kingdee_expected(vault_env), "sum", "src",
    )
    proc = run_cli(cmd, cli_env)
    assert proc.returncode == 0, (
        "env LOGNAME/USER must not override the OS identity: " + proc.stderr.decode()
    )


# --- finding 9: conflict-list listdir failures are errors ----------------------

def test_missing_target_dir_conflict_scan_is_error(vault_env, tmp_path):
    cli_env = dict(os.environ)
    cmd = base_write_cmd(
        tmp_path, cli_env,
        "wiki/work/nested/note.md", NOTE_NEW, "missing", "sum", "src",
    )
    proc = run_cli(cmd, cli_env)
    assert proc.returncode == 1, (proc.stdout, proc.stderr)
    assert not (vault_env["vault"] / "wiki/work/nested").exists()
    assert (vault_env["vault"] / "wiki/work/index.md").read_text(
        encoding="utf-8"
    ) == FOLDER_INDEX_4COL


def test_conflict_listdir_failure_is_error_not_empty_list(vault_env, tmp_path, monkeypatch, capsys):
    mod = load_module()

    def fake_listdir(directory):
        raise OSError("injected listdir failure")

    monkeypatch.setattr(mod.os, "listdir", fake_listdir)
    rc = inproc_write(
        mod, tmp_path, vault_env,
        "wiki/work/work_new-note.md", NOTE_NEW, "missing", "sum", "src",
    )
    assert rc == 1
    err = capsys.readouterr().err
    assert "injected listdir failure" in err
    assert not (vault_env["vault"] / "wiki/work/work_new-note.md").exists()
    assert (vault_env["vault"] / "wiki/work/index.md").read_text(
        encoding="utf-8"
    ) == FOLDER_INDEX_4COL
    assert (vault_env["vault"] / "wiki/index.md").read_bytes() == MASTER_INDEX.encode()


# --- finding 10: completed replay is a true no-op across calendar days ---------

def test_completed_replay_on_later_day_is_noop(vault_env, tmp_path, monkeypatch, capsys):
    cli_env = dict(os.environ)
    cmd = base_write_cmd(tmp_path, cli_env, "wiki/work/work_new-note.md", NOTE_NEW, "missing", "sum", "src")
    proc = run_cli(cmd, cli_env)
    assert proc.returncode == 0, proc.stderr.decode()
    index_after_first = (vault_env["vault"] / "wiki/work/index.md").read_text(encoding="utf-8")
    receipt_path = receipts(vault_env)[-1]
    receipt_before = receipt_path.read_bytes()
    backups_before = sorted(
        p.name for p in (vault_env["state"] / "note-write-backups").glob("*/*.md")
    )

    mod = load_module()

    class FutureDate(_dt.date):
        @classmethod
        def today(cls):
            return _dt.date(2027, 1, 2)

    monkeypatch.setattr(mod, "date", FutureDate)
    expected_now = sha256_bytes(
        (vault_env["vault"] / "wiki/work/work_new-note.md").read_bytes()
    )
    rc = inproc_write(
        mod, tmp_path, vault_env,
        "wiki/work/work_new-note.md", NOTE_NEW, expected_now, "sum", "src",
    )
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["result"] == "already-present"
    assert (vault_env["vault"] / "wiki/work/index.md").read_text(
        encoding="utf-8"
    ) == index_after_first, "completed replay must not rewrite index dates"
    assert receipt_path.read_bytes() == receipt_before, "completed replay must not rewrite the receipt"
    assert sorted(
        p.name for p in (vault_env["state"] / "note-write-backups").glob("*/*.md")
    ) == backups_before, "completed replay must not add backups"


def test_replay_without_receipt_records_current_save(vault_env, tmp_path, monkeypatch, capsys):
    cli_env = dict(os.environ)
    cmd = base_write_cmd(tmp_path, cli_env, "wiki/work/work_new-note.md", NOTE_NEW, "missing", "sum", "src")
    proc = run_cli(cmd, cli_env)
    assert proc.returncode == 0, proc.stderr.decode()
    index_after_first = (vault_env["vault"] / "wiki/work/index.md").read_text(encoding="utf-8")
    for r in receipts(vault_env):
        r.unlink()

    mod = load_module()
    expected_now = sha256_bytes(
        (vault_env["vault"] / "wiki/work/work_new-note.md").read_bytes()
    )
    rc = inproc_write(
        mod, tmp_path, vault_env,
        "wiki/work/work_new-note.md", NOTE_NEW, expected_now, "sum", "src",
    )
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["result"] == "already-present"
    assert (vault_env["vault"] / "wiki/work/index.md").read_text(
        encoding="utf-8"
    ) == index_after_first
    assert len(receipts(vault_env)) == 1
    receipt = _receipt_payload(vault_env["state"] / out["receipt"])
    assert receipt["result"] == "already-present"
    assert (vault_env["state"] / receipt["backups"]["note_after"]).is_file()
