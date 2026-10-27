"""Compact recovery NAS transport client.

Client side only: create / upload / fetch over a fixed argv whose remote
program is the pinned helper ``recovery_nas_remote.py``. No I/O is
performed here until the caller invokes the public functions; tests replace
``subprocess.Popen`` with a local fixed program.
"""

from __future__ import annotations

import hashlib
import functools
import json
import math
import os
import shlex
import stat
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REMOTE_PROGRAM_SHA256 = (
    "514673d8e8baf6ecf7681805a2ffbf1883c16b64fe7db6c0fe3ed38a45e28a50"
)
_HELPER_NAME = "recovery_nas_remote.py"
_ARTIFACT_NAME = "knowledge.tar.gpg"
_CHUNK = 1 << 20
_MAX_OUTPUT = 64 * 1024
_MAX_SIZE = 2 * 1024 * 1024 * 1024
_SECURE_KEYS = {"dev", "ino", "uid", "mode"}
_RUN_KEYS = {"dev", "ino", "uid", "mode"}
_SIG_KEYS = {"dev", "ino", "size", "mode", "nlink", "mtime_ns", "ctime_ns", "uid"}
_CREATE_KEYS = {"schema", "action", "namespace", "run"}
_UPLOAD_KEYS = _CREATE_KEYS | {"size", "sha256", "signature", 'artifact'}
_FETCH_KEYS = _CREATE_KEYS | {"size", "sha256", "before", "after", 'artifact'}


class TransportError(Exception):
    """Static transport failure; carries no paths, payloads or raw errors."""

    def __init__(self, reason: str = "transport_error") -> None:
        super().__init__(reason)
        self.reason = reason


def _static_failures(function):
    @functools.wraps(function)
    def bounded(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except TransportError:
            raise
        except OSError:
            raise TransportError('io_error') from None
        except (KeyError, TypeError, AttributeError, ValueError):
            raise TransportError('bad_receipt_or_argument') from None
    return bounded


@dataclass(frozen=True)
class FetchedArtifact:
    fd: int
    path: Path
    receipt: dict[str, Any]
    parent_fd: int
    parent_identity: tuple
    initial_signature: dict

    def close(self) -> None:
        failed = False
        for field in ('fd', 'parent_fd'):
            fd = getattr(self, field)
            if fd >= 0:
                object.__setattr__(self, field, -1)
                try:
                    os.close(fd)
                except OSError:
                    failed = True
        if failed:
            raise TransportError("close_error") from None

    def __enter__(self) -> "FetchedArtifact":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _is_hex64(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(c in "0123456789abcdef" for c in value)
    )


def _int_exact(value: Any) -> bool:
    return type(value) is int


def _identity(obj: Any, keys: set[str]) -> dict[str, int]:
    if not isinstance(obj, dict) or set(obj) != keys:
        raise TransportError("bad_identity")
    out: dict[str, int] = {}
    for key in keys:
        value = obj[key]
        if not _int_exact(value) or value < 0:
            raise TransportError("bad_identity")
        out[key] = value
    return out


def _binding(binding: Any) -> dict[str, Any]:
    if not isinstance(binding, dict) or set(binding) != {
        "hostname",
        "username",
        "uid",
        "parent",
        "namespace",
    }:
        raise TransportError("bad_binding")
    if not isinstance(binding["hostname"], str):
        raise TransportError("bad_binding")
    if not isinstance(binding["username"], str):
        raise TransportError("bad_binding")
    uid = binding["uid"]
    if not _int_exact(uid) or uid < 0:
        raise TransportError("bad_binding")
    parent = _identity(binding["parent"], _SECURE_KEYS)
    ns = binding["namespace"]
    if ns is not None:
        ns = _identity(ns, _SECURE_KEYS)
    return {
        "hostname": binding["hostname"],
        "username": binding["username"],
        "uid": uid,
        "parent": parent,
        "namespace": ns,
    }


def _helper_source() -> str:
    try:
        raw = Path(__file__).with_name(_HELPER_NAME).read_bytes()
    except OSError:
        raise TransportError('helper_unavailable') from None
    if _sha256_bytes(raw) != REMOTE_PROGRAM_SHA256:
        raise TransportError("helper_hash_mismatch")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        raise TransportError("helper_hash_mismatch") from None


def _argv(orchestration, target, parent, run_id, config) -> list[str]:
    base = orchestration.nas_command(target, parent, "stat_archive", run_id=run_id)
    if not isinstance(base, list) or not base:
        raise TransportError("bad_argv")
    source = _helper_source()
    remote = shlex.join(
        [
            "/usr/bin/python3",
            "-c",
            source,
            json.dumps(config, separators=(",", ":"), allow_nan=False),
        ]
    )
    return list(base[:-1]) + [remote]


def _hash_argv(argv: list[str]) -> str:
    payload = json.dumps(argv, separators=(",", ":"), ensure_ascii=False).encode()
    return _sha256_bytes(payload)


def _remaining(window) -> float:
    try:
        window.check()
        value = window.remaining()
    except TransportError:
        raise
    except Exception:
        raise TransportError("deadline_error") from None
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value) or value <= 0:
        raise TransportError("deadline_error")
    return float(value)


def _strict_json(data: bytes) -> Any:
    def _bad(_x):
        raise TransportError("bad_json")

    def _pairs(pairs):
        obj = {}
        for key, value in pairs:
            if key in obj:
                raise TransportError("bad_json")
            obj[key] = value
        return obj

    try:
        return json.loads(
            data.decode("utf-8"),
            object_pairs_hook=_pairs,
            parse_float=_bad,
            parse_constant=_bad,
        )
    except TransportError:
        raise
    except Exception:
        raise TransportError("bad_json") from None


def _run(argv, stdin, stdout, stderr, window):
    started = time.monotonic()
    timeout = _remaining(window)
    try:
        proc = subprocess.Popen(
            argv,
            stdin=stdin,
            stdout=stdout,
            stderr=stderr,
            shell=False,
            close_fds=True,
        )
    except Exception:
        raise TransportError("spawn_error") from None
    try:
        out, err = proc.communicate(timeout=timeout)
    except BaseException:
        try:
            proc.kill()
            proc.wait(timeout=5)
        except Exception:
            pass
        raise TransportError('partial_unknown') from None
    elapsed = time.monotonic() - started
    return proc.returncode, out, err, elapsed


def _close_quiet(fd: int) -> None:
    try:
        os.close(fd)
    except OSError:
        pass


def _read_output(out: Any) -> bytes:
    if out is None:
        return b""
    if not isinstance(out, (bytes, bytearray)):
        raise TransportError("bad_output")
    if len(out) > _MAX_OUTPUT:
        raise TransportError("output_too_large")
    return bytes(out)


def _receipt(remote, argv, elapsed, code) -> dict[str, Any]:
    return {
        "remote": remote,
        "execution": _hash_argv(argv),
        "remote_program_sha256": REMOTE_PROGRAM_SHA256,
        "exit_code": code,
        "elapsed_seconds": elapsed,
    }


def _check_remote_identity(remote, run, binding, action) -> None:
    if not isinstance(remote, dict) or type(remote.get("schema")) is not int:
        raise TransportError("bad_remote")
    if remote["schema"] != 1 or remote.get("action") != action:
        raise TransportError("bad_remote")
    ns = remote["namespace"]
    exp_ns = binding["namespace"]
    ns = _identity(ns, _SECURE_KEYS)
    if ns['mode'] != 0o700 or ns['uid'] != binding['uid'] or ns['dev'] != binding['parent']['dev']:
        raise TransportError('bad_remote')
    if exp_ns is not None and ns != exp_ns:
        raise TransportError('bad_remote')
    ident = _identity(run, _RUN_KEYS)
    if ident["mode"] != 0o700 or ident["uid"] != binding["uid"]:
        raise TransportError("bad_remote")
    if ident["dev"] != binding["parent"]["dev"]:
        raise TransportError("bad_remote")


def _validate_create_body(obj: Any, binding) -> dict[str, Any]:
    if not isinstance(obj, dict) or set(obj) != _CREATE_KEYS:
        raise TransportError("bad_remote")
    _check_remote_identity(obj, obj["run"], binding, "create")
    return obj


def _validate_upload_body(obj: Any, binding, size, digest, artifact='archive') -> dict[str, Any]:
    if not isinstance(obj, dict) or set(obj) != _UPLOAD_KEYS:
        raise TransportError("bad_remote")
    _check_remote_identity(obj, obj["run"], binding, "upload")
    if obj['artifact'] != artifact:
        raise TransportError('artifact_mismatch')
    if type(obj['size']) is not int or not 1 <= obj['size'] <= _MAX_SIZE or not _is_hex64(obj['sha256']) or obj["size"] != size or obj["sha256"] != digest:
        raise TransportError("bad_remote")
    sig = _identity(obj["signature"], _SIG_KEYS)
    if sig["size"] != size or sig["mode"] != 0o600 or sig["nlink"] != 1:
        raise TransportError("bad_remote")
    if sig["uid"] != binding["uid"]:
        raise TransportError("bad_remote")
    return obj


def _validate_fetch_body(obj: Any, binding, size, digest, sig, artifact='archive') -> dict[str, Any]:
    if not isinstance(obj, dict) or set(obj) != _FETCH_KEYS:
        raise TransportError("bad_remote")
    _check_remote_identity(obj, obj["run"], binding, "fetch")
    if obj['artifact'] != artifact:
        raise TransportError('artifact_mismatch')
    if obj["size"] != size or obj["sha256"] != digest:
        raise TransportError("bad_remote")
    before = _identity(obj["before"], _SIG_KEYS)
    after = _identity(obj["after"], _SIG_KEYS)
    if before != after or before != sig:
        raise TransportError("bad_remote")
    return obj


def _stat_full(st) -> dict[str, int]:
    return {
        "dev": st.st_dev, "ino": st.st_ino, "size": st.st_size,
        "mode": stat.S_IMODE(st.st_mode), "nlink": st.st_nlink,
        "mtime_ns": st.st_mtime_ns, "ctime_ns": st.st_ctime_ns, "uid": st.st_uid,
    }


def _fstat_full(fd):
    return _stat_full(os.fstat(fd))


def _context(target, parent, run_id, binding, artifact=None):
    values = [target, parent, run_id, binding]
    if artifact is not None:
        values.append(_artifact(artifact))
    return _sha256_bytes(json.dumps(values, sort_keys=True,
                                   separators=(',', ':'), allow_nan=False).encode())


def _artifact(value):
    if value not in ('archive', 'control'):
        raise TransportError('artifact_invalid')
    return value


@_static_failures
def request_context(orchestration, target, parent, run_id, binding, *, artifact='archive'):
    """Mint from independently admitted request inputs, never saved receipts."""
    orchestration.nas_command(target, parent, 'stat_archive', run_id=run_id)
    return _context(target, parent, run_id, _binding(binding), _artifact(artifact))


def _same_run(body, created):
    if body['namespace'] != created['namespace'] or body['run'] != created['run']:
        raise TransportError('run_identity_mismatch')


def _hash_fd(fd: int, window) -> tuple[str, int]:
    h = hashlib.sha256()
    total = 0
    try:
        os.lseek(fd, 0, os.SEEK_SET)
    except OSError:
        raise TransportError("io_error") from None
    while True:
        _remaining(window)
        try:
            chunk = os.pread(fd, _CHUNK, total)
        except OSError:
            raise TransportError("io_error") from None
        if not chunk:
            break
        total += len(chunk)
        if total > _MAX_SIZE:
            raise TransportError("too_large")
        h.update(chunk)
    if total < 1:
        raise TransportError("empty")
    return h.hexdigest(), total


def _validate_sig(sig: Any, uid: int) -> dict[str, int]:
    out = _identity(sig, _SIG_KEYS)
    if out["mode"] != 0o600 or out["nlink"] != 1 or out["uid"] != uid:
        raise TransportError("bad_signature")
    if not (1 <= out["size"] <= _MAX_SIZE):
        raise TransportError("bad_signature")
    return out


def _validate_receipt(receipt: Any, action: str, binding, validator, context) -> dict[str, Any]:
    if not isinstance(receipt, dict) or receipt.get("remote_program_sha256") != REMOTE_PROGRAM_SHA256:
        raise TransportError("bad_receipt")
    if receipt.get("exit_code") != 0 or type(receipt.get("exit_code")) is not int:
        raise TransportError("bad_receipt")
    if not _is_hex64(receipt.get("execution")):
        raise TransportError("bad_receipt")
    if receipt.get('context_sha256') != context:
        raise TransportError('receipt_context')
    elapsed = receipt.get('elapsed_seconds')
    if isinstance(elapsed, bool) or not isinstance(elapsed, (int, float)) or not math.isfinite(elapsed) or elapsed < 0:
        raise TransportError("bad_receipt")
    body = receipt.get("remote")
    return validator(body, binding)


@_static_failures
def inspect(orchestration, target, parent, run_id, binding, window):
    """Read-only failure inspection, never an ownership/adoption return."""
    bind = _binding(binding)
    config = {'action': 'inspect', 'parent': parent, 'run_id': run_id, 'binding': bind}
    argv = _argv(orchestration, target, parent, run_id, config)
    code, out, err, elapsed = _run(argv, subprocess.DEVNULL, subprocess.PIPE, subprocess.PIPE, window)
    _remaining(window)
    if code != 0 or _read_output(err):
        raise TransportError('inspect_failed')
    result = _strict_json(_read_output(out))
    keys = {'schema', 'action', 'namespace', 'run', 'artifacts', 'ownership', 'consistency'}
    if not isinstance(result, dict) or set(result) != keys or type(result['schema']) is not int or result['schema'] != 1 or result['action'] != 'inspect':
        raise TransportError('bad_inspection')
    if result['ownership'] != 'unresolved' or result['consistency'] != 'metadata_observation_only':
        raise TransportError('bad_inspection')
    if not isinstance(result['artifacts'], dict) or set(result['artifacts']) != {'archive', 'control'}:
        raise TransportError('bad_inspection')
    for item in [result['namespace'], result['run'], *result['artifacts'].values()]:
        if item is None:
            continue
        if not isinstance(item, dict) or set(item) != _SIG_KEYS | {'type'} or item['type'] not in {'directory', 'regular', 'symlink', 'other'}:
            raise TransportError('bad_inspection')
        if any(type(item[k]) is not int or (k not in {'mtime_ns', 'ctime_ns'} and item[k] < 0) for k in _SIG_KEYS):
            raise TransportError('bad_inspection')
    receipt = _receipt(result, argv, elapsed, code)
    receipt['context_sha256'] = _context(target, parent, run_id, bind)
    return receipt


@_static_failures
def create(orchestration, target, parent, run_id, binding, window) -> dict[str, Any]:
    bind = _binding(binding)
    config = {
        "action": "create", "parent": parent, "run_id": run_id,
        "binding": binding,
    }
    argv = _argv(orchestration, target, parent, run_id, config)
    _remaining(window)
    code, out, err, elapsed = _run(argv, subprocess.DEVNULL, subprocess.PIPE,
                                  subprocess.PIPE, window)
    _remaining(window)
    body = _read_output(out)
    errb = _read_output(err)
    if code != 0 or errb != b"":
        raise TransportError("create_failed")
    remote = _validate_create_body(_strict_json(body), bind)
    result = _receipt(remote, argv, elapsed, code)
    result['context_sha256'] = _context(target, parent, run_id, bind)
    return result


@_static_failures
def upload(orchestration, target, parent, run_id, binding, create_receipt,
           cipher_fd, window, *, artifact='archive') -> dict[str, Any]:
    bind = _binding(binding)
    _artifact(artifact)
    if not _int_exact(cipher_fd) or cipher_fd < 0:
        raise TransportError("bad_fd")
    try:
        pre_st = os.fstat(cipher_fd)
    except OSError:
        raise TransportError("bad_fd") from None
    if not stat.S_ISREG(pre_st.st_mode):
        raise TransportError("bad_fd")
    if (pre_st.st_nlink != 1 or pre_st.st_uid != os.getuid()
            or stat.S_IMODE(pre_st.st_mode) != 0o600):
        raise TransportError("bad_fd")
    pre = _fstat_full(cipher_fd)
    digest, size = _hash_fd(cipher_fd, window)
    if _fstat_full(cipher_fd) != pre:
        raise TransportError('fd_changed')
    _remaining(window)
    _validate_receipt(create_receipt, "create", bind,
                      lambda b, bd: _validate_create_body(b, bd), _context(target, parent, run_id, bind))
    config = {
        "action": "upload", "parent": parent, "run_id": run_id,
        "binding": binding, "created": create_receipt["remote"],
        "size": size, "sha256": digest,
        'artifact': artifact,
    }
    argv = _argv(orchestration, target, parent, run_id, config)
    try:
        os.lseek(cipher_fd, 0, os.SEEK_SET)
    except OSError:
        raise TransportError("io_error") from None
    code, out, err, elapsed = _run(argv, cipher_fd, subprocess.PIPE,
                                  subprocess.PIPE, window)
    _remaining(window)
    body = _read_output(out)
    errb = _read_output(err)
    if code != 0 or errb != b"":
        raise TransportError("upload_failed")
    post = _fstat_full(cipher_fd)
    if post != pre:
        raise TransportError("fd_changed")
    remote = _validate_upload_body(_strict_json(body), bind, size, digest, artifact)
    _same_run(remote, create_receipt['remote'])
    return {
        "remote": remote,
        "local": {"sha256": digest, "size": size, "signature": pre},
        "execution": _hash_argv(argv),
        "remote_program_sha256": REMOTE_PROGRAM_SHA256,
        "exit_code": code,
        "elapsed_seconds": elapsed,
        'context_sha256': _context(target, parent, run_id, bind, artifact),
    }


def _check_ancestors(path: Path) -> None:
    for anc in path.parents:
        try:
            st = os.lstat(anc)
        except OSError:
            raise TransportError("bad_destination") from None
        if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
            raise TransportError("bad_destination")


def _recheck_ancestors(path: Path) -> None:
    _check_ancestors(path)


@_static_failures
def prepare_fetch_output(destination):
    """Foreground parent exclusively creates and holds the output before fork."""
    if not isinstance(destination, Path) or not destination.is_absolute() or '..' in destination.parts or destination == Path('/'):
        raise TransportError("bad_destination")
    _check_ancestors(destination)
    parent_dir = destination.parent
    try:
        pst = os.stat(parent_dir, follow_symlinks=False)
    except OSError:
        raise TransportError("bad_destination") from None
    if (not stat.S_ISDIR(pst.st_mode) or pst.st_uid != os.getuid()
            or stat.S_IMODE(pst.st_mode) != 0o700):
        raise TransportError("bad_destination")
    try:
        os.lstat(destination)
    except FileNotFoundError:
        pass
    except OSError:
        raise TransportError("bad_destination") from None
    else:
        raise TransportError("destination_exists")
    pfd = leaf = -1
    try:
        pfd = os.open(parent_dir, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        actual = os.fstat(pfd)
        if _parent_identity(actual) != _parent_identity(pst):
            raise TransportError('bad_destination')
        leaf = os.open(destination.name, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                       0o600, dir_fd=pfd)
        artifact = FetchedArtifact(leaf, destination, {}, pfd, _parent_identity(pst), _fstat_full(leaf))
        _verify_output(artifact)
        return artifact
    except BaseException:
        for fd in (leaf, pfd):
            if fd >= 0:
                _close_quiet(fd)
        raise


def _parent_identity(st):
    return st.st_dev, st.st_ino, st.st_uid, stat.S_IMODE(st.st_mode)


def _verify_output(output):
    if not isinstance(output, FetchedArtifact) or output.fd < 0 or output.parent_fd < 0:
        raise TransportError('bad_output')
    _check_ancestors(output.path)
    parent = os.fstat(output.parent_fd)
    named_parent = os.stat(output.path.parent, follow_symlinks=False)
    if _parent_identity(parent) != output.parent_identity or _parent_identity(named_parent) != output.parent_identity:
        raise TransportError('bad_destination')
    if parent.st_uid != os.getuid() or stat.S_IMODE(parent.st_mode) != 0o700:
        raise TransportError('bad_destination')
    held = os.fstat(output.fd)
    named = os.stat(output.path.name, dir_fd=output.parent_fd, follow_symlinks=False)
    if not stat.S_ISREG(held.st_mode) or not stat.S_ISREG(named.st_mode):
        raise TransportError('bad_fetch_file')
    if held.st_uid != os.getuid() or held.st_nlink != 1 or stat.S_IMODE(held.st_mode) != 0o600:
        raise TransportError('bad_fetch_file')
    if _stat_full(named) != _stat_full(held):
        raise TransportError('bad_fetch_file')
    if (held.st_dev, held.st_ino) != (output.initial_signature['dev'], output.initial_signature['ino']):
        raise TransportError('bad_fetch_file')
    return _stat_full(held)


def _bound_upload(target, parent, run_id, bind, created, upload_receipt, artifact):
    _validate_receipt(created, 'create', bind, _validate_create_body, _context(target, parent, run_id, bind))
    upload_remote = _validate_upload_body(
        upload_receipt.get("remote") if isinstance(upload_receipt, dict) else None,
        bind, upload_receipt.get("local", {}).get("size")
        if isinstance(upload_receipt, dict) else None,
        upload_receipt.get("local", {}).get("sha256")
        if isinstance(upload_receipt, dict) else None,
        artifact,
    )
    if not isinstance(upload_receipt, dict):
        raise TransportError("bad_receipt")
    if upload_receipt.get("remote_program_sha256") != REMOTE_PROGRAM_SHA256:
        raise TransportError("bad_receipt")
    if upload_receipt.get("exit_code") != 0:
        raise TransportError("bad_receipt")
    local = upload_receipt.get("local")
    if not isinstance(local, dict) or set(local) != {"sha256", "size", "signature"}:
        raise TransportError("bad_receipt")
    if not _is_hex64(local["sha256"]) or not _int_exact(local["size"]):
        raise TransportError("bad_receipt")
    up_sig = _validate_sig(local["signature"], os.getuid())
    if up_sig["size"] != local["size"]:
        raise TransportError("bad_receipt")
    _validate_receipt(upload_receipt, 'upload', bind,
                      lambda b, bd: _validate_upload_body(b, bd, local['size'], local['sha256'], artifact),
                      _context(target, parent, run_id, bind, artifact))
    _same_run(upload_remote, created['remote'])
    return local, up_sig, upload_remote


@_static_failures
def fetch(orchestration, target, parent, run_id, binding, create_receipt,
          upload_receipt, output, window, *, artifact='archive', expected_context_sha256):
    """Write into a parent-held fresh descriptor; return JSON-safe evidence."""
    bind = _binding(binding)
    _artifact(artifact)
    if not _is_hex64(expected_context_sha256) or expected_context_sha256 != _context(target, parent, run_id, bind, artifact):
        raise TransportError('expected_context_mismatch')
    local, up_sig, upload_remote = _bound_upload(target, parent, run_id, bind, create_receipt, upload_receipt, artifact)
    initial = _verify_output(output)
    if initial != output.initial_signature or initial['size'] != 0:
        raise TransportError('output_not_fresh')
    if (initial['dev'], initial['ino']) == (up_sig['dev'], up_sig['ino']):
        raise TransportError('inode_alias')
    config = {'action': 'fetch', 'parent': parent, 'run_id': run_id, 'binding': binding,
              'file_signature': upload_remote['signature'], 'created': create_receipt['remote'],
              'size': local['size'], 'sha256': local['sha256'], 'artifact': artifact}
    argv = _argv(orchestration, target, parent, run_id, config)
    os.lseek(output.fd, 0, os.SEEK_SET)
    code, out, err, elapsed = _run(argv, subprocess.DEVNULL, output.fd, subprocess.PIPE, window)
    _remaining(window)
    if out is not None or code != 0:
        raise TransportError('fetch_failed')
    os.fsync(output.fd)
    pre = _verify_output(output)
    digest, size = _hash_fd(output.fd, window)
    if _verify_output(output) != pre:
        raise TransportError('fetch_changed')
    if digest != local['sha256'] or size != local['size']:
        raise TransportError('hash_mismatch')
    remote = _validate_fetch_body(_strict_json(_read_output(err)), bind, size, digest, upload_remote['signature'], artifact)
    _same_run(remote, create_receipt['remote'])
    result = _receipt(remote, argv, elapsed, code)
    result.update(local={'sha256': digest, 'size': size, 'signature': pre},
                  context_sha256=_context(target, parent, run_id, bind, artifact))
    return result


def _saved_execution(receipt):
    if not isinstance(receipt, dict) or receipt.get('remote_program_sha256') != REMOTE_PROGRAM_SHA256:
        raise TransportError('bad_receipt')
    if type(receipt.get('exit_code')) is not int or receipt['exit_code'] != 0:
        raise TransportError('bad_receipt')
    if not _is_hex64(receipt.get('execution')) or not _is_hex64(receipt.get('context_sha256')):
        raise TransportError('bad_receipt')
    elapsed = receipt.get('elapsed_seconds')
    if isinstance(elapsed, bool) or not isinstance(elapsed, (int, float)) or not math.isfinite(elapsed) or elapsed < 0:
        raise TransportError('bad_receipt')


@_static_failures
def accept_fetched(output, receipt, upload_receipt, window, *, expected_context_sha256):
    """Parent rechecks the actual held output after a trusted supervisor return."""
    _saved_execution(upload_receipt)
    _saved_execution(receipt)
    if not _is_hex64(expected_context_sha256) or expected_context_sha256 != upload_receipt['context_sha256']:
        raise TransportError('expected_context_mismatch')
    if receipt['context_sha256'] != upload_receipt['context_sha256']:
        raise TransportError('bad_receipt')
    local = upload_receipt['local']
    if not isinstance(local, dict) or set(local) != {'sha256', 'size', 'signature'} or not _is_hex64(local['sha256']):
        raise TransportError('bad_receipt')
    original_sig = _validate_sig(local['signature'], os.getuid())
    if type(local['size']) is not int or local['size'] != original_sig['size']:
        raise TransportError('bad_receipt')
    uploaded = upload_receipt['remote']
    namespace = _identity(uploaded['namespace'], _SECURE_KEYS)
    bind = {'uid': namespace['uid'], 'namespace': namespace, 'parent': {'dev': namespace['dev']}}
    artifact = _artifact(uploaded['artifact'])
    _validate_upload_body(uploaded, bind, local['size'], local['sha256'], artifact)
    _validate_fetch_body(receipt['remote'], bind, local['size'], local['sha256'], uploaded['signature'], artifact)
    _same_run(receipt['remote'], uploaded)
    pre = _verify_output(output)
    digest, size = _hash_fd(output.fd, window)
    if _verify_output(output) != pre or receipt.get('local') != {'sha256': digest, 'size': size, 'signature': pre}:
        raise TransportError('fetch_changed')
    if digest != upload_receipt['local']['sha256'] or size != upload_receipt['local']['size']:
        raise TransportError('hash_mismatch')
    if receipt['remote']['before'] != upload_receipt['remote']['signature'] or receipt['remote']['after'] != upload_receipt['remote']['signature']:
        raise TransportError('bad_remote')
    object.__setattr__(output, 'receipt', receipt)
    os.lseek(output.fd, 0, os.SEEK_SET)
    return output


__all__ = ["TransportError", "FetchedArtifact", "create", "upload", "fetch", 'inspect',
           "prepare_fetch_output", "accept_fetched", 'request_context', "REMOTE_PROGRAM_SHA256"]
