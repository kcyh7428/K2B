#!/usr/bin/env python3
"""Fixed remote recovery helper, preparation only; no import effects."""
import contextlib
import hashlib
import json
import os
import pwd
import re
import socket
import stat
import sys

HOME_PREFIX = '/volume1/homes/'
NAMESPACE = 'K2B-Recovery'
SLOT = 'knowledge.tar.gpg'
ARTIFACTS = {'archive': SLOT, 'control': 'control.json.gpg'}
MAX_FILE = 2 * 1024 * 1024 * 1024
MAX_CONFIG = 16 * 1024
CHUNK = 1024 * 1024
USER_RE = re.compile(r'[A-Za-z0-9_][A-Za-z0-9_-]{0,63}\Z')
RUN_RE = re.compile(r'manual-[A-Za-z0-9-]{1,73}\Z')
SHA_RE = re.compile(r'[0-9a-f]{64}\Z')
O_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW


class RemoteError(Exception):
    pass


def _err(msg):
    raise RemoteError(msg)


def _identity(name):
    d = {}
    d['dev'] = name['dev']
    d['ino'] = name['ino']
    d['uid'] = name['uid']
    d['mode'] = name['mode']
    return d


def _dir_identity(fd):
    st = os.fstat(fd)
    if not stat.S_ISDIR(st.st_mode):
        _err('not_a_directory')
    return {'dev': st.st_dev, 'ino': st.st_ino, 'uid': st.st_uid, 'mode': stat.S_IMODE(st.st_mode)}


def _sig(fd):
    st = os.fstat(fd)
    return {'dev': st.st_dev, 'ino': st.st_ino, 'size': st.st_size, 'mode': stat.S_IMODE(st.st_mode), 'nlink': st.st_nlink, 'mtime_ns': st.st_mtime_ns, 'ctime_ns': st.st_ctime_ns, 'uid': st.st_uid}


def _open_dir(parent_fd, name):
    return os.open(name, O_DIR_FLAGS, dir_fd=parent_fd)


def _verify_dirfd_named(parent_fd, name, fd, mode, uid):
    ident = _dir_identity(fd)
    st = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    if not stat.S_ISDIR(st.st_mode):
        _err('named_not_directory')
    named = {'dev': st.st_dev, 'ino': st.st_ino, 'uid': st.st_uid, 'mode': stat.S_IMODE(st.st_mode)}
    if named != ident:
        _err('identity_mismatch')
    if ident['mode'] != mode or ident['uid'] != uid:
        _err('mode_uid_mismatch')
    return ident


def _hash_fd(fd, size):
    h = hashlib.sha256()
    off = 0
    while off < size:
        n = min(CHUNK, size - off)
        chunk = os.pread(fd, n, off)
        if not chunk:
            _err('short_read')
        h.update(chunk)
        off += len(chunk)
    return h.hexdigest()


def _require_dict(value):
    if not isinstance(value, dict):
        _err('bad_config')
    return value


def _require_int(value, minimum):
    if type(value) is not int or value < minimum:
        _err('bad_config')
    return value


def _require_str(value, regex=None):
    if not isinstance(value, str):
        _err('bad_config')
    if regex is not None and not regex.match(value):
        _err('bad_config')
    return value


def _check_binding(binding):
    _require_dict(binding)
    host = _require_str(binding.get('hostname'))
    user = _require_str(binding.get('username'), USER_RE)
    uid = _require_int(binding.get('uid'), 0)
    act_user = pwd.getpwuid(os.getuid()).pw_name
    if socket.gethostname() != host or act_user != user or os.getuid() != uid:
        _err('binding_mismatch')
    parent = _require_dict(binding.get('parent'))
    for key in ('dev', 'ino', 'uid', 'mode'):
        _require_int(parent.get(key), 0)
    ns = binding.get('namespace')
    if ns is not None:
        _require_dict(ns)
        for key in ('dev', 'ino', 'uid', 'mode'):
            _require_int(ns.get(key), 0)
    return {'username': user, 'uid': uid, 'parent': parent, 'namespace': ns}


def _open_parent(bt, stack):
    home = HOME_PREFIX + bt['username']
    parts = [p for p in home.split('/') if p]
    fd = os.open('/', O_DIR_FLAGS)
    stack.callback(os.close, fd)
    chain = []
    for part in parts:
        nfd = _open_dir(fd, part)
        stack.callback(os.close, nfd)
        chain.append((fd, part, nfd))
        fd = nfd
    return fd, chain


def _check_parent_chain(chain, bt):
    expected = bt['parent']
    for parent, name, fd in chain:
        actual = _dir_identity(fd)
        _verify_dirfd_named(parent, name, fd, actual['mode'], actual['uid'])
    ident = _dir_identity(chain[-1][2])
    if ident['dev'] != expected['dev'] or ident['ino'] != expected['ino'] or ident['uid'] != expected['uid'] or ident['mode'] != expected['mode']:
        _err('parent_mismatch')
    if ident['uid'] != bt['uid']:
        _err('parent_owner_mismatch')
    return ident


def _ensure_namespace(pfd, bt, stack, create):
    ns = bt['namespace']
    uid = bt['uid']
    if ns is None:
        if not create:
            _err('namespace_unbound')
        try:
            os.mkdir(NAMESPACE, 0o700, dir_fd=pfd)
        except FileExistsError:
            _err('namespace_exists')
        os.fsync(pfd)
    nfd = _open_dir(pfd, NAMESPACE)
    stack.callback(os.close, nfd)
    ident = _verify_dirfd_named(pfd, NAMESPACE, nfd, 0o700, uid)
    if ns is not None:
        if ident['dev'] != ns['dev'] or ident['ino'] != ns['ino'] or ident['uid'] != ns['uid'] or ident['mode'] != ns['mode']:
            _err('namespace_mismatch')
    return nfd, ident


def _open_run(nfd, run_id, bt, stack):
    try:
        rfd = _open_dir(nfd, run_id)
        stack.callback(os.close, rfd)
        ident = _verify_dirfd_named(nfd, run_id, rfd, 0o700, bt['uid'])
    except FileNotFoundError:
        _err('run_missing')
    return rfd, ident


def _created_check(created, bt):
    _require_dict(created)
    if created.get('schema') != 1 or created.get('action') != 'create':
        _err('bad_created')
    for key in ('namespace', 'run'):
        d = _require_dict(created.get(key))
        for f in ('dev', 'ino', 'uid', 'mode'):
            _require_int(d.get(f), 0)
    return created


def _dirs_match(a, b):
    return a['dev'] == b['dev'] and a['ino'] == b['ino'] and a['uid'] == b['uid'] and a['mode'] == b['mode']


def _run(config, input_fd=0, output_fd=1):
    cfg = _require_dict(config)
    action = _require_str(cfg.get('action'))
    if action not in ('create', 'upload', 'fetch', 'inspect'):
        _err('bad_action')
    run_id = _require_str(cfg.get('run_id'), RUN_RE)
    parent = _require_str(cfg.get('parent'))
    bt = _check_binding(_require_dict(cfg.get('binding')))
    if parent != HOME_PREFIX + bt['username']:
        _err('bad_parent_path')
    with contextlib.ExitStack() as stk:
        pfd, chain = _open_parent(bt, stk)
        _check_parent_chain(chain, bt)
        if action == 'inspect':
            result = _inspect(pfd, run_id, stk)
            _check_parent_chain(chain, bt)
            return result
        created = None
        if action != 'create':
            created = _created_check(cfg.get('created'), bt)
            if bt['namespace'] is None:
                bt['namespace'] = created['namespace']
        nfd, ns_ident = _ensure_namespace(pfd, bt, stk, action == 'create')
        if action == 'create':
            try:
                os.mkdir(run_id, 0o700, dir_fd=nfd)
            except FileExistsError:
                _err('run_exists')
            rfd = _open_dir(nfd, run_id)
            stk.callback(os.close, rfd)
            run_ident = _verify_dirfd_named(nfd, run_id, rfd, 0o700, bt['uid'])
            os.fsync(nfd)
            out = {'schema': 1, 'action': 'create', 'namespace': ns_ident, 'run': run_ident}
        else:
            if not _dirs_match(created['namespace'], ns_ident):
                _err('namespace_mismatch')
            rfd, run_ident = _open_run(nfd, run_id, bt, stk)
            if not _dirs_match(created['run'], run_ident):
                _err('run_mismatch')
            out = _transfer(action, cfg, bt, rfd, ns_ident, run_ident, input_fd, output_fd)
        _check_parent_chain(chain, bt)
        if _verify_dirfd_named(pfd, NAMESPACE, nfd, 0o700, bt['uid']) != ns_ident:
            _err('namespace_changed')
        if _verify_dirfd_named(nfd, run_id, rfd, 0o700, bt['uid']) != run_ident:
            _err('run_changed')
        return out


def run(config, input_fd=0, output_fd=1):
    try:
        return _run(config, input_fd, output_fd)
    except RemoteError:
        raise
    except (OSError, KeyError, TypeError, ValueError):
        _err('remote_state_invalid')


def _observe(parentfd, name):
    try:
        st = os.stat(name, dir_fd=parentfd, follow_symlinks=False)
    except FileNotFoundError:
        return None
    kind = ('directory' if stat.S_ISDIR(st.st_mode) else 'regular' if stat.S_ISREG(st.st_mode)
            else 'symlink' if stat.S_ISLNK(st.st_mode) else 'other')
    return {'type': kind, 'dev': st.st_dev, 'ino': st.st_ino, 'size': st.st_size,
            'uid': st.st_uid, 'mode': stat.S_IMODE(st.st_mode), 'nlink': st.st_nlink,
            'mtime_ns': st.st_mtime_ns, 'ctime_ns': st.st_ctime_ns}


def _inspect(pfd, run_id, stack):
    """Observe only the fixed names. Never create, adopt, hash or read payloads."""
    result = {'schema': 1, 'action': 'inspect', 'namespace': _observe(pfd, NAMESPACE),
              'run': None, 'artifacts': {name: None for name in ARTIFACTS},
              'ownership': 'unresolved', 'consistency': 'metadata_observation_only'}
    if result['namespace'] is None or result['namespace']['type'] != 'directory':
        return result
    nfd = _open_dir(pfd, NAMESPACE)
    stack.callback(os.close, nfd)
    ns = result['namespace']
    if _dir_identity(nfd) != {k: ns[k] for k in ('dev', 'ino', 'uid', 'mode')}:
        _err('inspection_identity_changed')
    _verify_dirfd_named(pfd, NAMESPACE, nfd, ns['mode'], ns['uid'])
    result['run'] = _observe(nfd, run_id)
    if result['run'] is not None and result['run']['type'] == 'directory':
        rfd = _open_dir(nfd, run_id)
        stack.callback(os.close, rfd)
        observed = result['run']
        if _dir_identity(rfd) != {k: observed[k] for k in ('dev', 'ino', 'uid', 'mode')}:
            _err('inspection_identity_changed')
        _verify_dirfd_named(nfd, run_id, rfd, observed['mode'], observed['uid'])
        result['artifacts'] = {kind: _observe(rfd, slot) for kind, slot in ARTIFACTS.items()}
        _verify_dirfd_named(nfd, run_id, rfd, observed['mode'], observed['uid'])
    _verify_dirfd_named(pfd, NAMESPACE, nfd, ns['mode'], ns['uid'])
    return result


def _transfer(action, cfg, bt, rfd, ns_ident, run_ident, input_fd, output_fd):
    artifact = cfg.get('artifact', 'archive')
    if artifact not in ARTIFACTS:
        _err('artifact_invalid')
    slot = ARTIFACTS[artifact]
    size = _require_int(cfg.get('size'), 1)
    if size > MAX_FILE:
        _err('too_large')
    sha = _require_str(cfg.get('sha256'))
    if not SHA_RE.match(sha):
        _err('bad_sha256')
    base = {'schema': 1, 'action': action, 'namespace': ns_ident, 'run': run_ident, 'artifact': artifact}
    if action == 'upload':
        return _upload(base, bt, rfd, size, sha, input_fd, slot)
    fsig = _require_dict(cfg.get('file_signature'))
    return _fetch(base, bt, rfd, size, sha, fsig, output_fd, slot)


def _named_signature(rfd, slot):
    st = os.stat(slot, dir_fd=rfd, follow_symlinks=False)
    if not stat.S_ISREG(st.st_mode):
        _err('slot_not_regular')
    return {'dev': st.st_dev, 'ino': st.st_ino, 'size': st.st_size,
            'mode': stat.S_IMODE(st.st_mode), 'nlink': st.st_nlink,
            'mtime_ns': st.st_mtime_ns, 'ctime_ns': st.st_ctime_ns, 'uid': st.st_uid}


def _upload(base, bt, rfd, size, sha, input_fd, slot):
    space = os.fstatvfs(rfd)
    if space.f_bavail * space.f_frsize < size + 1024 * 1024 * 1024:
        _err('capacity_insufficient')
    try:
        wfd = os.open(slot, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=rfd)
    except FileExistsError:
        _err('slot_exists')
    try:
        pre = _sig(wfd)
        if pre['size'] != 0 or pre['nlink'] != 1 or pre['mode'] != 0o600 or pre['uid'] != bt['uid']:
            _err('slot_bad')
        h = hashlib.sha256()
        remaining = size
        while remaining > 0:
            chunk = os.read(input_fd, min(CHUNK, remaining))
            if not chunk:
                _err('short_input')
            h.update(chunk)
            off = 0
            while off < len(chunk):
                n = os.pwrite(wfd, chunk[off:], (size - remaining) + off)
                if n <= 0:
                    _err('write_failed')
                off += n
            remaining -= len(chunk)
        os.fsync(wfd)
        written = _sig(wfd)
        if written['size'] != size or h.hexdigest() != sha:
            _err('upload_mismatch')
        vfd = os.open(slot, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=rfd)
        try:
            if _sig(vfd) != written:
                _err('file_changed')
            actual_hash = _hash_fd(vfd, size)
            if actual_hash != sha:
                _err('hash_mismatch')
            if _sig(vfd) != written or _sig(wfd) != written or _named_signature(rfd, slot) != written:
                _err('file_changed')
        finally:
            os.close(vfd)
        os.fsync(rfd)
    finally:
        os.close(wfd)
    base['size'] = size
    base['sha256'] = actual_hash
    base['signature'] = written
    return base


def _fetch(base, bt, rfd, size, sha, fsig, output_fd, slot):
    for k in ('dev', 'ino', 'size', 'mode', 'nlink', 'mtime_ns', 'ctime_ns', 'uid'):
        _require_int(fsig.get(k), 0)
    vfd = os.open(slot, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=rfd)
    try:
        before = _sig(vfd)
        if before != fsig or before['uid'] != bt['uid'] or before['size'] != size:
            _err('file_signature_mismatch')
        if before['nlink'] != 1 or before['mode'] != 0o600 or not stat.S_ISREG(os.fstat(vfd).st_mode):
            _err('slot_bad')
        if _hash_fd(vfd, size) != sha:
            _err('hash_mismatch')
        h = hashlib.sha256()
        off = 0
        while off < size:
            chunk = os.pread(vfd, min(CHUNK, size - off), off)
            if not chunk:
                _err('short_read')
            h.update(chunk)
            woff = 0
            while woff < len(chunk):
                n = os.write(output_fd, chunk[woff:])
                if n <= 0:
                    _err('write_failed')
                woff += n
            off += len(chunk)
        if h.hexdigest() != sha:
            _err('stream_hash_mismatch')
        after = _sig(vfd)
        if after != before or _named_signature(rfd, slot) != before:
            _err('file_changed')
    finally:
        os.close(vfd)
    base['size'] = size
    base['sha256'] = sha
    base['before'] = before
    base['after'] = after
    return base


def _strict_pairs(pairs):
    seen = {}
    for k, v in pairs:
        if k in seen:
            _err('duplicate_key')
        seen[k] = v
    return seen


def _bad_float(text):
    _err('bad_number')


def _load_config(data):
    if len(data) > MAX_CONFIG:
        _err('config_too_large')
    text = data.decode('utf-8')
    cfg = json.loads(text, object_pairs_hook=_strict_pairs, parse_float=_bad_float, parse_constant=_bad_float)
    return _require_dict(cfg)


def main(argv=None):
    args = sys.argv if argv is None else argv
    try:
        if len(args) != 2:
            _err('usage')
        cfg = _load_config(args[1].encode('utf-8'))
        receipt = run(cfg)
        stream = sys.stderr if cfg['action'] == 'fetch' else sys.stdout
        stream.write(json.dumps(receipt, separators=(',', ':')))
        stream.flush()
        return 0
    except Exception:
        sys.stderr.write('transport_failure')
        sys.stderr.flush()
        return 65


if __name__ == '__main__':
    sys.exit(main())
