"""Invented resources only. These tests do not acquire any live K2B lock."""
import fcntl
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts/lib'))
from recovery_resource_holder import ResourceError, ResourceHolder, ResourceSpec, inspect_busy, open_parent

_anchors = []


@pytest.fixture(autouse=True)
def close_invented_anchors():
    yield
    while _anchors:
        _anchors.pop().close()


def anchor(path):
    path = path.resolve()
    st = path.stat()
    identity = {'dev': st.st_dev, 'ino': st.st_ino, 'uid': st.st_uid, 'mode': st.st_mode & 0o7777}
    # Synthetic identity receipt, never real synchronization/indexing proof.
    evidence = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    value = open_parent(path, identity, evidence)
    _anchors.append(value)
    return value


class Window:
    def check(self):
        pass


def spec(tmp_path, name='note_write', protocol='mkdir'):
    return ResourceSpec(name, tmp_path.resolve() / name, protocol, anchor(tmp_path))


def test_held_handles_and_real_flock_exclusion(tmp_path):
    first = spec(tmp_path)
    second = spec(tmp_path, 'reconciliation', 'flock')
    with ResourceHolder([first, second], Window()) as holder:
        evidence = holder.evidence()
        assert evidence['pid'] == os.getpid()
        assert evidence['uid'] == os.getuid()
        assert evidence['process_start']
        assert len(evidence['resources']) == 2
        for record in evidence['resources']:
            st = os.fstat(record['fd'])
            assert (st.st_dev, st.st_ino) == (record['dev'], record['ino'])
            assert st.st_ino == Path(record['path']).stat().st_ino
            for key in ['size', 'nlink', 'mtime_ns', 'ctime_ns']:
                assert key in record
        child = subprocess.run([sys.executable, '-c',
            'import fcntl,sys; f=open(sys.argv[1],"r+"); '
            'fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)', str(second.path)],
            capture_output=True, timeout=10)
        assert child.returncode != 0
        assert b'BlockingIOError' in child.stderr
    assert not first.path.exists()
    assert second.path.is_file()
    with second.path.open('r+') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
    with pytest.raises(ResourceError, match='not_held'):
        holder.evidence()
    holder.release()


@pytest.mark.parametrize('busy', ['mkdir', 'flock'])
def test_busy_resource_rolls_back_only_owned_handles(tmp_path, busy):
    first = spec(tmp_path)
    second = spec(tmp_path, 'wiki_log', busy)
    if busy == 'mkdir':
        second.path.mkdir(mode=0o700)
        stream = None
    else:
        stream = second.path.open('w+')
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
    original = second.path.stat()
    try:
        with pytest.raises(ResourceError, match=busy + '_busy'):
            with ResourceHolder([first, second], Window()):
                pytest.fail('busy resource was adopted')
        assert not first.path.exists()
        assert second.path.stat().st_ino == original.st_ino
    finally:
        if stream is not None:
            stream.close()


def test_body_failure_releases_resources(tmp_path):
    resource = spec(tmp_path)
    with pytest.raises(ValueError, match='body'):
        with ResourceHolder([resource], Window()):
            raise ValueError('body')
    assert not resource.path.exists()


@pytest.mark.parametrize('kind', ['replacement', 'nonempty', 'chmod'])
def test_changed_mkdir_is_retained_and_cleanup_fails(tmp_path, kind):
    resource = spec(tmp_path)
    holder = ResourceHolder([resource], Window())
    holder.__enter__()
    if kind == 'replacement':
        resource.path.rename(tmp_path / 'old')
        resource.path.mkdir(mode=0o700)
    elif kind == 'nonempty':
        (resource.path / 'unexpected').write_text('invented')
    else:
        resource.path.chmod(0o755)
    with pytest.raises(ResourceError):
        holder.evidence()
    with pytest.raises(ResourceError, match='cleanup_ambiguous'):
        holder.release()
    assert holder.cleanup_errors
    assert holder.terminal_state == 'released_ambiguous'
    with pytest.raises(ResourceError, match='cleanup_ambiguous'):
        holder.release()
    assert resource.path.exists()


def test_flock_replacement_is_never_unlinked(tmp_path):
    resource = spec(tmp_path, 'outbox', 'flock')
    holder = ResourceHolder([resource], Window())
    holder.__enter__()
    resource.path.rename(tmp_path / 'old')
    resource.path.write_text('replacement')
    with pytest.raises(ResourceError, match='identity_changed'):
        holder.evidence()
    with pytest.raises(ResourceError, match='cleanup_ambiguous'):
        holder.release()
    assert resource.path.read_text() == 'replacement'


@pytest.mark.parametrize('kind', ['symlink', 'hardlink', 'writable', 'special'])
def test_unsafe_flock_refused(tmp_path, kind):
    resource = spec(tmp_path, 'inbox', 'flock')
    original = tmp_path / 'original'
    original.write_text('invented')
    if kind == 'symlink':
        resource.path.symlink_to(original)
    elif kind == 'hardlink':
        os.link(original, resource.path)
    else:
        resource.path.write_text('invented')
        resource.path.chmod(0o666 if kind == 'writable' else 0o4600)
    with pytest.raises(ResourceError):
        with ResourceHolder([resource], Window()):
            pytest.fail('unsafe resource adopted')
    assert original.read_text() == 'invented'
    assert os.path.lexists(resource.path)


def test_append_target_requires_existing_exact_file(tmp_path):
    resource = spec(tmp_path, 'append_target', 'flock')
    with pytest.raises(ResourceError, match='append_target_missing'):
        with ResourceHolder([resource], Window()):
            pytest.fail('missing append target created')
    assert not resource.path.exists()
    resource.path.write_text('unchanged')
    before = resource.path.stat()
    with ResourceHolder([resource], Window()) as holder:
        assert holder.evidence()['resources'][0]['ino'] == before.st_ino
    assert resource.path.read_text() == 'unchanged'


def test_expired_window_still_releases(tmp_path):
    class Expiring:
        expired = False
        def check(self):
            if self.expired:
                raise TimeoutError()
    window = Expiring()
    resource = spec(tmp_path)
    with ResourceHolder([resource], window) as holder:
        window.expired = True
        with pytest.raises(ResourceError, match='window_check_failed'):
            holder.evidence()
    assert not resource.path.exists()


def test_open_failure_retains_uncaptured_dir_and_reports_ambiguity(tmp_path, monkeypatch):
    resource = spec(tmp_path)
    actual = os.open
    def fail_directory(path, flags, *args, **kwargs):
        if flags & os.O_DIRECTORY:
            raise OSError('invented')
        return actual(path, flags, *args, **kwargs)
    monkeypatch.setattr(os, 'open', fail_directory)
    holder = ResourceHolder([resource], Window())
    with pytest.raises(ResourceError, match='rollback_ambiguous'):
        holder.__enter__()
    assert resource.path.is_dir()
    assert holder.cleanup_errors
    records = holder.postmortem()['observations']
    observation = next(r for r in records if r['event'] == 'post_mkdir_lstat')
    assert observation['signature']['ino'] == resource.path.stat().st_ino
    assert observation['fd_held_at_observation'] is False


def test_rmdir_failure_never_reports_clean_release(tmp_path, monkeypatch):
    holder = ResourceHolder([spec(tmp_path)], Window())
    holder.__enter__()
    def fail(*args, **kwargs):
        raise OSError('invented')
    monkeypatch.setattr(os, 'rmdir', fail)
    with pytest.raises(ResourceError, match='cleanup_ambiguous'):
        holder.release()
    assert holder.cleanup_errors


def test_duplicate_and_wrong_protocol_refused(tmp_path):
    item = spec(tmp_path)
    for items in ([item, item], [spec(tmp_path, 'note_write', 'flock')],
                  [spec(tmp_path, 'append_target', 'mkdir')],
                  [ResourceSpec('outbox', item.path, 'flock'), item]):
        with pytest.raises(ResourceError, match='invalid_spec'):
            ResourceHolder(items, Window())


def test_evidence_before_entry_refused(tmp_path):
    with pytest.raises(ResourceError, match='not_held'):
        ResourceHolder([spec(tmp_path)], Window()).evidence()


def test_fork_cannot_claim_or_release_parent_ownership(tmp_path):
    resource = spec(tmp_path)
    with ResourceHolder([resource], Window()) as holder:
        reader, writer = os.pipe()
        child = os.fork()
        if child == 0:
            os.close(reader)
            try:
                refused = 0
                for action in (holder.evidence, holder.release, holder.__enter__):
                    try:
                        action()
                    except ResourceError:
                        refused += 1
                os.write(writer, str(refused).encode())
            finally:
                os._exit(0)
        os.close(writer)
        try:
            assert os.read(reader, 10) == b'3'
        finally:
            os.close(reader)
            _, status = os.waitpid(child, 0)
        assert status == 0
        assert holder.evidence()['resources'][0]['ino'] == resource.path.stat().st_ino
    assert not resource.path.exists()


def test_multiple_exact_append_targets_are_separately_held(tmp_path):
    specs = []
    for label in ['usage', 'preferences']:
        path = tmp_path.resolve() / label
        path.write_text('invented')
        specs.append(ResourceSpec('append_target:' + label, path, 'flock'))
    with ResourceHolder(specs, Window()) as holder:
        assert len(holder.evidence()['resources']) == 2


def test_release_before_entry_refused(tmp_path):
    holder = ResourceHolder([spec(tmp_path)], Window())
    assert holder.terminal_state == 'not_entered'
    with pytest.raises(ResourceError, match='not_held'):
        holder.release()


def test_symlink_ancestor_refused(tmp_path):
    actual = tmp_path / 'actual'
    actual.mkdir()
    linked = tmp_path / 'linked'
    linked.symlink_to(actual, target_is_directory=True)
    resource = ResourceSpec('outbox', tmp_path.resolve() / 'linked/outbox', 'flock')
    with pytest.raises(ResourceError, match='symlink_path'):
        with ResourceHolder([resource], Window()):
            pytest.fail('ancestor symlink adopted')
    assert not (actual / 'outbox').exists()


def test_window_expiry_mid_acquisition_rolls_back(tmp_path):
    class Expires:
        calls = 0
        def check(self):
            self.calls += 1
            if self.calls == 2:
                raise TimeoutError()
    first = spec(tmp_path)
    second = spec(tmp_path, 'compile_index')
    holder = ResourceHolder([first, second], Expires())
    with pytest.raises(ResourceError, match='window_check_failed'):
        holder.__enter__()
    assert holder.failure_reason == 'window_check_failed'
    assert holder.terminal_state == 'released_clean'
    assert not first.path.exists() and not second.path.exists()


def test_ambiguous_release_retains_original_identity(tmp_path):
    resource = spec(tmp_path)
    holder = ResourceHolder([resource], Window())
    holder.__enter__()
    original = resource.path.stat()
    resource.path.rename(tmp_path / 'old')
    resource.path.mkdir(mode=0o700)
    with pytest.raises(ResourceError, match='cleanup_ambiguous'):
        holder.release()
    report = holder.postmortem()
    assert report['terminal_state'] == 'released_ambiguous'
    owned = next(r for r in report['observations'] if r['event'] == 'acquired')
    assert (owned['dev'], owned['ino']) == (original.st_dev, original.st_ino)
    assert owned['ino'] != resource.path.stat().st_ino
    report['observations'].clear()
    assert holder.postmortem()['observations']


@pytest.mark.parametrize('names', [('Lock', 'lock'), ('caf\u00e9', 'cafe\u0301')])
def test_folded_path_duplicates_refused_without_artifact(tmp_path, names):
    specs = [ResourceSpec(role, tmp_path.resolve() / name, 'mkdir')
             for role, name in zip(['note_write', 'compile_index'], names)]
    with pytest.raises(ResourceError, match='invalid_spec'):
        ResourceHolder(specs, Window())
    assert not list(tmp_path.iterdir())


def test_busy_inspection_never_claims_ownership_or_removes(tmp_path):
    resource = spec(tmp_path)
    resource.path.mkdir(mode=0o700)
    for number in range(130):
        (resource.path / str(number)).write_bytes(b'invented')
    before = resource.path.stat()
    observation = inspect_busy(resource)
    assert observation['ownership'] == 'unknown'
    assert observation['signature']['ino'] == before.st_ino
    assert len(observation['entries']) == 128
    assert observation['entries_truncated'] is True
    assert len(list(resource.path.iterdir())) == 130


@pytest.mark.parametrize('protocol,role', [('mkdir', 'note_write'), ('flock', 'outbox')])
def test_creation_without_physical_anchor_refused(tmp_path, protocol, role):
    resource = ResourceSpec(role, tmp_path.resolve() / role, protocol)
    with pytest.raises(ResourceError, match='creation_parent_required'):
        with ResourceHolder([resource], Window()):
            pytest.fail('unanchored creation accepted')
    assert not resource.path.exists()


def test_parent_anchor_replacement_cannot_redirect_creation(tmp_path):
    parent = tmp_path / 'parent'
    parent.mkdir(mode=0o700)
    bound = anchor(parent)
    resource = ResourceSpec('outbox', parent.resolve() / 'lock', 'flock', bound)
    parent.rename(tmp_path / 'old')
    parent.mkdir(mode=0o700)
    with pytest.raises(ResourceError, match='anchor_identity_changed'):
        with ResourceHolder([resource], Window()):
            pytest.fail('replacement anchor accepted')
    assert not (parent / 'lock').exists()
    assert not (tmp_path / 'old/lock').exists()


def test_parent_anchor_wrong_evidence_identity_refused(tmp_path):
    st = tmp_path.stat()
    wrong = {'dev': st.st_dev, 'ino': st.st_ino + 1, 'uid': st.st_uid, 'mode': st.st_mode & 0o7777}
    with pytest.raises(ResourceError, match='anchor_identity_changed'):
        open_parent(tmp_path.resolve(), wrong, 'a' * 64)
