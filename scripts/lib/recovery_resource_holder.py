"""Purpose-bound cooperative resource holder for a one-time K2B recovery op.

Acquires and holds a caller-supplied finite set of known cooperative resources
(atomic mkdir or fcntl.flock on an exact path).  It never infers paths, never
asserts global idleness, and is not a freeze or background service.  Import
performs no I/O; all effects happen inside ResourceHolder enter/exit.
"""

from __future__ import annotations

import errno
import fcntl
import copy
import os
import re
import socket
import stat
import subprocess
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Protocol

__all__ = [
    "KNOWN_NAMES",
    "MKDIR",
    "FLOCK",
    "ResourceSpec",
    "ResourceError",
    "ResourceHolder",
    'inspect_busy',
    'ParentAnchor',
    'open_parent',
]

MKDIR = "mkdir"
FLOCK = "flock"

KNOWN_NAMES = frozenset({
    "note_write",
    "compile_index",
    "wiki_log",
    "vault_publication",
    "private_publication",
    "reconciliation",
    "memory_state",
    "memory_extraction",
    "memory_worklist",
    "outbox",
    "inbox",
    "discovery_status",
    "integration",
    "append_target",
    "eod_reconcile",
})

_FLOCK_FLAGS = os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC | os.O_RDWR
_DIR_FLAGS = os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
_FILE_MODE = 0o600
_DIR_MODE = 0o700


class ResourceError(Exception):
    """Static-reason error; never carries names, contents or errno strings."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class ParentAnchor:
    path: Path
    fd: int
    identity: dict
    scope_receipt_sha256: str

    def close(self):
        if self.fd >= 0:
            fd = self.fd
            object.__setattr__(self, 'fd', -1)
            try:
                os.close(fd)
            except OSError:
                raise ResourceError('anchor_close_failed') from None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


@dataclass(frozen=True)
class ResourceSpec:
    name: str
    path: Path
    protocol: str
    parent_anchor: Optional[ParentAnchor] = None


class _CheckWindow(Protocol):
    def check(self) -> None:
        ...


@dataclass
class _Handle:
    spec: ResourceSpec
    fd: int
    dev: int
    ino: int
    uid: int
    sid: int
    is_dir: bool


def _reject() -> None:
    raise ResourceError("invalid_spec")


def _validate_spec(spec: object) -> None:
    if not isinstance(spec, ResourceSpec):
        _reject()
    if not isinstance(spec.name, str) or not re.fullmatch(r'[a-z_]+(?::[a-z0-9_-]{1,64})?', spec.name):
        _reject()
    role = spec.name.split(':', 1)[0]
    if role not in KNOWN_NAMES:
        _reject()
    if spec.protocol not in (MKDIR, FLOCK):
        _reject()
    if role in {'note_write', 'compile_index'} and spec.protocol != MKDIR:
        _reject()
    if role not in {'note_write', 'compile_index', 'wiki_log'} and spec.protocol != FLOCK:
        _reject()
    path = spec.path
    if not isinstance(path, Path) or not path.is_absolute():
        _reject()
    text = str(path)
    if text in ("", "/") or path != Path(os.path.normpath(text)):
        _reject()
    if any(part in ("..", ".", "") for part in path.parts[1:]):
        _reject()
    if spec.parent_anchor is not None and (not isinstance(spec.parent_anchor, ParentAnchor) or spec.parent_anchor.path != path.parent):
        _reject()


def _validate_specs(specs: object) -> tuple[ResourceSpec, ...]:
    if not isinstance(specs, (list, tuple)) or not specs:
        _reject()
    out: list[ResourceSpec] = []
    names: set[str] = set()
    paths: set[str] = set()
    for spec in specs:
        _validate_spec(spec)
        key = unicodedata.normalize('NFD', str(spec.path)).casefold()
        if spec.name in names or key in paths:
            _reject()
        names.add(spec.name)
        paths.add(key)
        out.append(spec)
    return tuple(out)


def _check_no_symlink(path: Path) -> None:
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current = current / part
        try:
            st = os.lstat(current)
        except FileNotFoundError:
            continue
        except OSError:
            raise ResourceError("stat_failed") from None
        if stat.S_ISLNK(st.st_mode):
            raise ResourceError("symlink_path")


def _fstat(fd: int) -> os.stat_result:
    try:
        return os.fstat(fd)
    except OSError:
        raise ResourceError("fstat_failed") from None


def _lstat(path: Path) -> os.stat_result:
    try:
        return os.lstat(path)
    except OSError:
        raise ResourceError("stat_failed") from None


def _same_identity(st: os.stat_result, handle: _Handle) -> bool:
    return st.st_dev == handle.dev and st.st_ino == handle.ino


def _parent_identity(st):
    return {'dev': st.st_dev, 'ino': st.st_ino, 'uid': st.st_uid, 'mode': stat.S_IMODE(st.st_mode)}


def open_parent(path, expected_identity, scope_receipt_sha256):
    """Physically bind a parent already admitted by actual private scope evidence."""
    if not isinstance(path, Path) or not path.is_absolute() or '..' in path.parts:
        _reject()
    if not isinstance(expected_identity, dict) or set(expected_identity) != {'dev', 'ino', 'uid', 'mode'}:
        _reject()
    if any(type(v) is not int or v < 0 for v in expected_identity.values()):
        _reject()
    if not isinstance(scope_receipt_sha256, str) or not re.fullmatch('[0-9a-f]{64}', scope_receipt_sha256):
        _reject()
    _check_no_symlink(path)
    try:
        fd = os.open(path, _DIR_FLAGS)
    except OSError:
        raise ResourceError('anchor_open_failed') from None
    anchor = ParentAnchor(path, fd, dict(expected_identity), scope_receipt_sha256)
    try:
        _check_anchor(anchor)
        return anchor
    except BaseException:
        anchor.close()
        raise


def _check_anchor(anchor):
    if not isinstance(anchor, ParentAnchor) or anchor.fd < 0:
        raise ResourceError('creation_parent_required')
    _check_no_symlink(anchor.path)
    held, named = _fstat(anchor.fd), _lstat(anchor.path)
    if not stat.S_ISDIR(held.st_mode) or not stat.S_ISDIR(named.st_mode):
        raise ResourceError('anchor_not_directory')
    if _parent_identity(held) != anchor.identity or _parent_identity(named) != anchor.identity:
        raise ResourceError('anchor_identity_changed')
    return anchor.fd


def _fd_file_ok(handle: _Handle) -> int:
    st = _fstat(handle.fd)
    if not stat.S_ISREG(st.st_mode):
        raise ResourceError("not_regular")
    if st.st_uid != os.getuid():
        raise ResourceError("wrong_owner")
    if st.st_nlink != 1:
        raise ResourceError("bad_link_count")
    if st.st_mode & 0o022:
        raise ResourceError("group_or_world_write")
    if st.st_mode & 0o7000:
        raise ResourceError("special_mode_bits")
    if not _same_identity(st, handle):
        raise ResourceError("identity_changed")
    return stat.S_IMODE(st.st_mode)


def _named_file_ok(handle: _Handle) -> os.stat_result:
    st = _lstat(handle.spec.path)
    if not stat.S_ISREG(st.st_mode) or stat.S_ISLNK(st.st_mode):
        raise ResourceError("not_regular")
    if st.st_uid != os.getuid() or st.st_nlink != 1:
        raise ResourceError("wrong_owner")
    if st.st_mode & 0o022 or st.st_mode & 0o7000:
        raise ResourceError("bad_mode")
    if not _same_identity(st, handle):
        raise ResourceError("identity_changed")
    return st


def _fd_dir_ok(handle: _Handle) -> int:
    st = _fstat(handle.fd)
    if not stat.S_ISDIR(st.st_mode):
        raise ResourceError("not_directory")
    if st.st_uid != os.getuid():
        raise ResourceError("wrong_owner")
    if not _same_identity(st, handle):
        raise ResourceError("identity_changed")
    return stat.S_IMODE(st.st_mode)


def _named_dir_ok(handle: _Handle) -> os.stat_result:
    st = _lstat(handle.spec.path)
    if not stat.S_ISDIR(st.st_mode) or stat.S_ISLNK(st.st_mode):
        raise ResourceError("not_directory")
    if st.st_uid != os.getuid() or stat.S_IMODE(st.st_mode) != _DIR_MODE:
        raise ResourceError("wrong_dir_mode")
    if not _same_identity(st, handle):
        raise ResourceError("identity_changed")
    return st


class ResourceHolder:
    """All-or-nothing NB acquisition of confirmed cooperative resources."""

    def __init__(self, specs: object, window: object) -> None:
        self._specs = _validate_specs(specs)
        if not callable(getattr(window, "check", None)):
            raise ResourceError("invalid_window")
        self._window: _CheckWindow = window  # type: ignore[assignment]
        self._handles: list[_Handle] = []
        self._entered = False
        self._released = False
        self._pid = os.getpid()
        try:
            result = subprocess.run(['/bin/ps', '-p', str(self._pid), '-o', 'lstart='],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                env={'PATH': '/usr/bin:/bin', 'LC_ALL': 'C'}, timeout=2, check=True)
            self._process_start = result.stdout.decode('ascii').strip()
            if not self._process_start or len(self._process_start) > 128 or '\n' in self._process_start:
                raise ValueError()
        except Exception:
            raise ResourceError('process_start_unavailable') from None
        self.cleanup_errors: list[ResourceError] = []
        self.failure_reason = None
        self._attempt_records = []

    def __enter__(self) -> "ResourceHolder":
        if self._entered or os.getpid() != self._pid:
            raise ResourceError("already_entered")
        self._entered = True
        try:
            for spec in self._specs:
                self._check_window()
                handle = self._acquire(spec)
                self._handles.append(handle)
                self._retain(handle, 'acquired', None)
        except BaseException as original:
            self.failure_reason = original.reason if isinstance(original, ResourceError) else 'acquisition_failed'
            try:
                self._release_all("rollback_ambiguous")
            except ResourceError:
                raise ResourceError("rollback_ambiguous") from None
            if self.cleanup_errors:
                raise ResourceError("rollback_ambiguous") from None
            raise
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if exc is not None:
            self.failure_reason = exc.reason if isinstance(exc, ResourceError) else 'body_failed'
        try:
            self.release()
        except ResourceError as cleanup_error:
            raise ResourceError("cleanup_ambiguous") from None
        return None

    def _check_window(self) -> None:
        try:
            self._window.check()
        except ResourceError:
            raise
        except Exception:
            raise ResourceError("window_check_failed") from None

    def _same_process(self) -> None:
        if os.getpid() != self._pid:
            raise ResourceError("fork_refused")

    def _assert_live(self) -> None:
        self._same_process()
        if not self._entered or self._released:
            raise ResourceError("not_held")

    def _acquire(self, spec: ResourceSpec) -> _Handle:
        _check_no_symlink(spec.path)
        if spec.protocol == MKDIR:
            return self._acquire_mkdir(spec)
        return self._acquire_flock(spec)

    def _acquire_mkdir(self, spec: ResourceSpec) -> _Handle:
        parent_fd = _check_anchor(spec.parent_anchor)
        try:
            os.mkdir(spec.path.name, _DIR_MODE, dir_fd=parent_fd)
        except FileExistsError:
            raise ResourceError("mkdir_busy") from None
        except OSError:
            raise ResourceError("mkdir_failed") from None
        captured: Optional[_Handle] = None
        fd: Optional[int] = None
        try:
            st = _lstat(spec.path)
            self._attempt_records.append({'event': 'post_mkdir_lstat',
                'name': spec.name, 'path': str(spec.path), 'protocol': spec.protocol,
                'signature': _signature(st), 'fd_held_at_observation': False,
                'observed_monotonic_ns': time.monotonic_ns()})
            if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.getuid():
                raise ResourceError("not_directory")
            if stat.S_IMODE(st.st_mode) != _DIR_MODE:
                raise ResourceError("wrong_dir_mode")
            try:
                fd = os.open(spec.path.name, _DIR_FLAGS, dir_fd=parent_fd)
            except OSError:
                raise ResourceError("open_failed") from None
            handle = _Handle(spec, fd, st.st_dev, st.st_ino, st.st_uid,
                             0, True)
            captured = handle
            if not _same_identity(_fstat(fd), handle):
                raise ResourceError("identity_changed")
            _fd_dir_ok(handle)
            _named_dir_ok(handle)
            _require_empty_dir(handle)
            return handle
        except BaseException:
            ambiguous = self._cleanup_dir(captured, fd)
            if ambiguous:
                self.cleanup_errors.append(ResourceError("dir_ambiguous"))
            raise

    def _cleanup_dir(self, handle: Optional[_Handle], fd: Optional[int]) -> bool:
        """Close fd; rmdir only a positively identified own empty dir."""
        ambiguous = False
        try:
            if handle is not None and fd is not None:
                try:
                    _fd_dir_ok(handle)
                    _named_dir_ok(handle)
                    if os.listdir(fd):
                        ambiguous = True
                    else:
                        _rmdir_named(handle)
                except ResourceError:
                    ambiguous = True
                except OSError:
                    ambiguous = True
            elif fd is None:
                ambiguous = True
        finally:
            if fd is not None:
                if handle is not None:
                    self._retain(handle, 'failed_acquire_before_close', 'ambiguous' if ambiguous else 'clean')
                try:
                    os.close(fd)
                except OSError:
                    ambiguous = True
            if handle is not None:
                self._forget(handle)
        return ambiguous

    def _acquire_flock(self, spec: ResourceSpec) -> _Handle:
        existed = True
        try:
            os.lstat(spec.path)
        except FileNotFoundError:
            existed = False
        except OSError:
            raise ResourceError("stat_failed") from None
        if spec.name.split(':', 1)[0] == "append_target" and not existed:
            raise ResourceError("append_target_missing")
        flags = _FLOCK_FLAGS | (0 if existed else os.O_CREAT)
        parent_fd = None
        if spec.parent_anchor is not None:
            parent_fd = _check_anchor(spec.parent_anchor)
        elif not existed:
            raise ResourceError('creation_parent_required')
        try:
            fd = os.open(spec.path.name if parent_fd is not None else spec.path,
                         flags, _FILE_MODE, dir_fd=parent_fd)
        except OSError:
            raise ResourceError("open_failed") from None
        handle = _Handle(spec, fd, 0, 0, os.getuid(), 0, False)
        try:
            st = _fstat(fd)
            handle.dev, handle.ino = st.st_dev, st.st_ino
            _fd_file_ok(handle)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                if exc.errno in (errno.EAGAIN, errno.EACCES, errno.EWOULDBLOCK):
                    raise ResourceError("flock_busy") from None
                raise ResourceError("flock_failed") from None
            _fd_file_ok(handle)
            named = _named_file_ok(handle)
            _same_metadata(named, _fstat(fd))
            return handle
        except BaseException:
            try:
                os.close(fd)
            except OSError:
                self.cleanup_errors.append(ResourceError("close_failed"))
            raise

    def _forget(self, handle: _Handle) -> None:
        try:
            self._handles.remove(handle)
        except ValueError:
            pass

    def _verify_handle(self, handle: _Handle) -> int:
        if handle.spec.parent_anchor is not None:
            _check_anchor(handle.spec.parent_anchor)
        if handle.is_dir:
            mode = _fd_dir_ok(handle)
            if mode != _DIR_MODE:
                raise ResourceError("wrong_dir_mode")
            _named_dir_ok(handle)
            _require_empty_dir(handle)
            return mode
        mode = _fd_file_ok(handle)
        named = _named_file_ok(handle)
        _same_metadata(named, _fstat(handle.fd))
        return mode

    def evidence(self) -> dict:
        self._assert_live()
        records = []
        for handle in self._handles:
            self._check_window()
            mode = self._verify_handle(handle)
            st = _fstat(handle.fd)
            record = {
                "name": handle.spec.name,
                "protocol": handle.spec.protocol,
                "path": str(handle.spec.path),
                "dev": int(handle.dev),
                "ino": int(handle.ino),
                "fd": int(handle.fd),
                "mode": int(mode),
                'uid': int(st.st_uid),
                'observed_monotonic_ns': time.monotonic_ns(),
            }
            record.update(_signature(st))
            if handle.spec.parent_anchor is not None:
                record['creation_parent'] = dict(handle.spec.parent_anchor.identity)
                record['scope_receipt_sha256'] = handle.spec.parent_anchor.scope_receipt_sha256
            self._verify_handle(handle)
            if _signature(_fstat(handle.fd)) != _signature(st):
                raise ResourceError('evidence_changed')
            records.append(record)
        return {
            "pid": int(os.getpid()),
            'process_start': self._process_start,
            "uid": int(os.getuid()),
            "host": socket.gethostname(),
            "resources": records,
        }

    def release(self) -> None:
        self._same_process()
        if not self._entered:
            raise ResourceError('not_held')
        if self._released:
            if self.cleanup_errors:
                raise ResourceError('cleanup_ambiguous')
            return
        self._release_all("cleanup_ambiguous")

    @property
    def terminal_state(self):
        if not self._entered:
            return 'not_entered'
        if not self._released:
            return 'holding'
        return 'released_ambiguous' if self.cleanup_errors else 'released_clean'

    def _retain(self, handle, event, reason):
        record = {'event': event, 'name': handle.spec.name, 'path': str(handle.spec.path),
                  'protocol': handle.spec.protocol, 'dev': handle.dev, 'ino': handle.ino,
                  'fd_held_at_observation': True, 'reason': reason,
                  'observed_monotonic_ns': time.monotonic_ns()}
        try:
            record['signature'] = _signature(_fstat(handle.fd))
        except ResourceError:
            record['signature'] = None
        if handle.spec.parent_anchor is not None:
            record['creation_parent'] = dict(handle.spec.parent_anchor.identity)
            record['scope_receipt_sha256'] = handle.spec.parent_anchor.scope_receipt_sha256
        self._attempt_records.append(record)

    def postmortem(self):
        """Private operator evidence, including observations made before close."""
        self._same_process()
        return {'terminal_state': self.terminal_state, 'failure_reason': self.failure_reason,
                'pid': self._pid, 'process_start': self._process_start, 'uid': os.getuid(),
                'cleanup_reasons': [e.reason for e in self.cleanup_errors],
                'observations': copy.deepcopy(self._attempt_records)}

    def _release_all(self, reason: str) -> None:
        self._released = True
        errors: list[ResourceError] = []
        while self._handles:
            handle = self._handles[-1]
            previous_errors = len(errors)
            try:
                if handle.is_dir:
                    try:
                        self._verify_handle(handle)
                        _rmdir_named(handle)
                    except ResourceError as exc:
                        errors.append(exc)
                else:
                    try:
                        self._verify_handle(handle)
                    except ResourceError as exc:
                        errors.append(exc)
            finally:
                reasons = [e.reason for e in errors[previous_errors:]]
                self._retain(handle, 'before_close', reasons)
                try:
                    os.close(handle.fd)
                except OSError:
                    errors.append(ResourceError("close_failed"))
                    self._attempt_records.append({'event': 'close_failed',
                        'name': handle.spec.name, 'path': str(handle.spec.path),
                        'dev': handle.dev, 'ino': handle.ino})
                self._forget(handle)
        if errors:
            self.cleanup_errors.extend(errors)
            raise ResourceError(reason) from None


def _same_metadata(named: os.stat_result, held: os.stat_result) -> None:
    fields = ("st_mode", "st_uid", "st_nlink", "st_size", "st_mtime_ns",
              "st_ctime_ns")
    if any(getattr(named, f) != getattr(held, f) for f in fields):
        raise ResourceError("named_mismatch")


def _signature(st):
    return {'dev': st.st_dev, 'ino': st.st_ino, 'size': st.st_size,
            'mode': stat.S_IMODE(st.st_mode), 'nlink': st.st_nlink,
            'mtime_ns': st.st_mtime_ns, 'ctime_ns': st.st_ctime_ns}


def inspect_busy(spec):
    """Bounded acquisition-free observation; ownership always remains unknown."""
    _validate_spec(spec)
    _check_no_symlink(spec.path.parent)
    st = _lstat(spec.path)
    result = {'name': spec.name, 'path': str(spec.path), 'protocol': spec.protocol,
              'signature': _signature(st), 'owner_uid': st.st_uid,
              'ownership': 'unknown', 'entries': [], 'entries_truncated': False}
    if stat.S_ISDIR(st.st_mode):
        try:
            fd = os.open(spec.path, _DIR_FLAGS)
        except OSError:
            raise ResourceError('inspect_failed') from None
        try:
            if (os.fstat(fd).st_dev, os.fstat(fd).st_ino) != (st.st_dev, st.st_ino):
                raise ResourceError('identity_changed')
            with os.scandir(fd) as entries:
                for item in entries:
                    if len(result['entries']) == 128:
                        result['entries_truncated'] = True
                        break
                    result['entries'].append(item.name)
            if _signature(_fstat(fd)) != _signature(_lstat(spec.path)):
                raise ResourceError('identity_changed')
        except OSError:
            raise ResourceError('inspect_failed') from None
        finally:
            os.close(fd)
    return result


def _require_empty_dir(handle: _Handle) -> None:
    try:
        if os.listdir(handle.fd):
            raise ResourceError("mkdir_not_empty")
    except OSError:
        raise ResourceError("listdir_failed") from None


def _rmdir_named(handle: _Handle) -> None:
    """Remove only after re-verifying identity; never touch a replacement."""
    try:
        st = os.lstat(handle.spec.path)
        if not stat.S_ISDIR(st.st_mode) or not _same_identity(st, handle):
            raise ResourceError("identity_changed")
        if os.listdir(handle.fd):
            raise ResourceError("mkdir_not_empty")
        if handle.spec.parent_anchor is None:
            raise ResourceError('creation_parent_required')
        parent_fd = _check_anchor(handle.spec.parent_anchor)
        os.rmdir(handle.spec.path.name, dir_fd=parent_fd)
    except ResourceError:
        raise
    except OSError:
        raise ResourceError("rmdir_failed") from None
