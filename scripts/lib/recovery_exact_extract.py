"""Purpose-bound stdlib adapter for exact-name extraction (preparation only).

Writes selected archive members beneath an already-existing, private, empty
destination directory, binding a second stdlib reader to an existing P1
inspection verdict.  It performs no admission, parsing policy, restore
comparison, or deployment action.  The cooperative same-user assumption
applies: own newly created directories are not defended against a malicious
same-user process.  A returned receipt is not evidence of a completed
recovery; the owning controller must run ``contract.compare_restore`` with an
independently produced output ledger before acceptance.
"""

import hashlib
import os
import stat
import tarfile
import unicodedata
from pathlib import Path

_READ_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
_CHUNK = 1024 * 1024


class ExtractError(Exception):
    """Generic static extraction failure; carries no payload or names."""


def _fail(reason):
    raise ExtractError(reason) from None


def _sig(st):
    return (st.st_dev, st.st_ino, st.st_size, st.st_mode, st.st_nlink,
            st.st_mtime_ns, st.st_ctime_ns, st.st_uid)


def _root_sig(st):
    return (st.st_dev, st.st_ino, st.st_uid, stat.S_ISDIR(st.st_mode))


def _hash_fd(fd, limit, window):
    h = hashlib.sha256()
    pos = 0
    while pos < limit:
        window.check()
        try:
            chunk = os.pread(fd, min(_CHUNK, limit - pos), pos)
        except OSError:
            _fail("archive_io")
        if not chunk:
            break
        h.update(chunk)
        pos += len(chunk)
    if pos != limit:
        _fail("archive_identity_changed")
    return h.hexdigest()


def _check_archive(fd, archive, window):
    try:
        pre = os.lstat(str(archive))
    except OSError:
        _fail("archive_identity_changed")
    if not stat.S_ISREG(pre.st_mode) or pre.st_nlink != 1:
        _fail("archive_identity_changed")
    if pre.st_uid != os.getuid():
        _fail("archive_owner")
    try:
        fst = os.fstat(fd)
    except OSError:
        _fail("archive_io")
    if _sig(pre) != _sig(fst):
        _fail("archive_identity_changed")
    return pre


def _first_component(path):
    if not isinstance(path, Path) or not path.is_absolute():
        _fail("path_invalid")
    if len(path.parts) < 2 or ".." in path.parts or str(path) != os.path.normpath(str(path)):
        _fail("path_invalid")
    return path.parts[1]


def _check_anon_chain(top):
    parts = []
    cur = top
    while True:
        parts.append(cur)
        parent = cur.parent
        if parent == cur:
            break
        cur = parent
    for p in parts:
        try:
            st = os.lstat(str(p))
        except OSError:
            _fail("destination_path")
        if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
            _fail("destination_ancestor")


def _check_destination(destination):
    _check_anon_chain(destination)
    try:
        st = os.lstat(str(destination))
    except OSError:
        _fail("destination_not_dir")
    if not stat.S_ISDIR(st.st_mode) or stat.S_ISLNK(st.st_mode):
        _fail("destination_not_dir")
    if stat.S_IMODE(st.st_mode) != 0o700:
        _fail("destination_mode")
    if st.st_uid != os.getuid():
        _fail("destination_owner")
    if stat.S_IMODE(st.st_mode) & 0o7000:
        _fail("destination_special_bits")
    parent = destination.parent
    if parent != destination:
        try:
            pst = os.lstat(str(parent))
        except OSError:
            _fail("destination_path")
        if pst.st_uid != os.getuid() or stat.S_IMODE(pst.st_mode) != 0o700:
            _fail("destination_parent")
    try:
        entries = os.listdir(str(destination))
    except OSError:
        _fail("destination_io")
    if entries:
        _fail("destination_not_empty")
    try:
        fd = os.open(str(destination), _DIR_FLAGS)
    except OSError:
        _fail("destination_open")
    try:
        fst = os.fstat(fd)
    except OSError:
        os.close(fd)
        _fail("destination_io")
    if _sig(fst) != _sig(st):
        os.close(fd)
        _fail("destination_identity_changed")
    return fd, st


def _check_disjoint(archive, destination, selection):
    root = selection.get("root") if isinstance(selection, dict) else None
    if root is None:
        _fail("selection_unbound")
    try:
        rp = Path(os.path.realpath(str(root)))
        dp = Path(os.path.realpath(str(destination)))
        ap = Path(os.path.realpath(str(archive)))
    except OSError:
        _fail("path_invalid")
    # Match P1 destination-profile identity semantics, including on a
    # case-sensitive filesystem where this intentionally refuses conservatively.
    rp, dp, ap = (unicodedata.normalize('NFD', str(p)).casefold()
                  for p in (rp, dp, ap))
    for a, b in ((dp, rp), (rp, dp), (ap, dp), (dp, ap)):
        if a == b or str(a).startswith(str(b) + os.sep):
            _fail("destination_overlaps_source")


def _check_capacity(destination, selection):
    # Same headroom as frozen P1 validate_destination_profile. P1's pre-create
    # destination admission remains an independent required operator phase.
    try:
        available = os.statvfs(destination)
    except OSError:
        _fail('destination_capacity_unavailable')
    required = 8 * selection['profile']['total_bytes'] + 1024 * 1024 * 1024
    if available.f_bavail * available.f_frsize < required:
        _fail('destination_capacity_insufficient')


def _open_relative_dir(root_fd, relative):
    fd = os.dup(root_fd)
    if not relative:
        return fd
    for part in relative.split("/"):
        if part in ("", ".", ".."):
            os.close(fd)
            _fail("archive_member_path")
        try:
            nfd = os.open(part, _DIR_FLAGS, dir_fd=fd)
        except OSError:
            os.close(fd)
            _fail("destination_open")
        os.close(fd)
        fd = nfd
    return fd


def _precreate_dirs(root_fd, exp_dirs, window):
    created = set()
    for key in sorted((k for k in exp_dirs if k != "."),
                      key=lambda k: k.count("/")):
        window.check()
        parent, _, name = key.rpartition("/")
        if name in ("", ".", ".."):
            _fail("archive_member_path")
        fd = _open_relative_dir(root_fd, parent)
        try:
            os.mkdir(name, 0o700, dir_fd=fd)
        except OSError:
            _fail("destination_exists")
        finally:
            os.close(fd)
        created.add(key)
    return created


def _write_file(root_fd, rel, src, expected, window):
    parent, _, name = rel.rpartition("/")
    if name in ("", ".", ".."):
        _fail("archive_member_path")
    dfd = _open_relative_dir(root_fd, parent)
    try:
        try:
            out = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL
                          | os.O_NOFOLLOW, 0o600, dir_fd=dfd)
        except OSError:
            _fail("destination_exists")
        try:
            h = hashlib.sha256()
            total = 0
            while True:
                window.check()
                chunk = src.read(_CHUNK)
                if not chunk:
                    break
                total += len(chunk)
                if total > expected['size']:
                    _fail("archive_file_size")
                h.update(chunk)
                view = memoryview(chunk)
                while view:
                    window.check()
                    try:
                        n = os.write(out, view)
                    except OSError:
                        _fail("destination_io")
                    if n <= 0:
                        _fail("destination_io")
                    view = view[n:]
            if total != expected['size']:
                _fail("archive_file_size")
            if h.hexdigest() != expected['sha256']:
                _fail("archive_file_digest")
            try:
                os.fchmod(out, expected['mode'])
                os.utime(out, ns=(expected['mtime_ns'], expected['mtime_ns']))
                os.fsync(out)
            except OSError:
                _fail("destination_io")
        finally:
            try:
                os.close(out)
            except OSError:
                pass
    finally:
        os.close(dfd)


def _member_key(name):
    key = name[2:] if name.startswith("./") else name
    if key.endswith("/") and key not in ("", "."):
        key = key[:-1]
    return key


def extract_exact(component, contract, archive, selection, expected_sha256,
                  destination, window):
    """Prepare an exact-name extraction bound to an existing P1 verdict."""
    if not isinstance(selection, dict):
        _fail("selection_unbound")
    if not selection.get("sha256") or selection.get("root") is None:
        _fail("selection_unbound")
    if not isinstance(expected_sha256, str) or len(expected_sha256) != 64:
        _fail("archive_digest_argument")
    try:
        archive = Path(archive)
        destination = Path(destination)
    except TypeError:
        _fail("path_invalid")
    _first_component(archive)
    _first_component(destination)
    window.check()

    root_fd = None
    fd = None
    try:
        root_fd, root_st = _check_destination(destination)
        _check_disjoint(archive, destination, selection)
        _check_anon_chain(archive.parent)
        try:
            fd = os.open(str(archive), _READ_FLAGS)
        except OSError:
            _fail("archive_open")
        pre = _check_archive(fd, archive, window)
        limit = pre.st_size
        if limit > contract.MAX_ARCHIVE_BYTES:
            _fail("archive_size")
        pre_digest = _hash_fd(fd, limit, window)
        if pre_digest != expected_sha256:
            _fail("archive_digest_mismatch")

        report = contract.inspect_archive(component, archive, selection,
                                          expected_sha256, window)
        if not isinstance(report, dict):
            _fail("inspection_invalid")
        if report.get("archive_digest") != pre_digest:
            _fail("archive_identity_changed")
        if report.get("selection_digest") != selection.get("selection_digest"):
            _fail("selection_digest_mismatch")
        if (report.get("archive_size"), report.get("archive_dev"),
                report.get("archive_ino")) != (pre.st_size, pre.st_dev, pre.st_ino):
            _fail("archive_identity_changed")
        try:
            if _sig(os.fstat(fd)) != _sig(pre):
                _fail("archive_identity_changed")
        except OSError:
            _fail("archive_io")
        if _sig(_check_archive(fd, archive, window)) != _sig(pre):
            _fail("archive_identity_changed")
        members = report.get("members")
        if not isinstance(members, dict):
            _fail("inspection_invalid")
        exp_files = members.get("files") or {}
        exp_dirs = members.get("dirs") or {}
        if not isinstance(exp_files, dict) or not isinstance(exp_dirs, dict):
            _fail("inspection_invalid")
        expected = exp_dirs
        if "." not in expected:
            _fail("inspection_invalid")
        if report.get('member_count') != len(exp_files) + len(exp_dirs):
            _fail('inspection_member_count')
        # P1 excludes the separately declared alias from tar members. Its own
        # restore_declared_alias phase follows successful regular-tree comparison.
        if report.get('alias_declared') is not (selection.get('alias') is not None):
            _fail('inspection_alias_declaration')
        if any(ent['size'] > contract.MAX_FILE_BYTES for ent in exp_files.values()):
            _fail('archive_file_size')
        _check_capacity(destination, selection)

        created = _precreate_dirs(root_fd, exp_dirs, window)
        seen_files = set()
        seen_dirs = set()
        try:
            tfd = os.dup(fd)
        except OSError:
            _fail("archive_io")
        try:
            os.lseek(tfd, 0, os.SEEK_SET)
        except OSError:
            os.close(tfd)
            _fail("archive_io")
        try:
            with os.fdopen(tfd, "rb") as raw:
                try:
                    tf = tarfile.open(fileobj=raw, mode="r:")
                except (OSError, tarfile.TarError):
                    _fail("archive_read")
                try:
                    for ti in tf:
                        window.check()
                        key = _member_key(ti.name)
                        if key in ("", "."):
                            key = "."
                        if ti.isdir():
                            if key not in exp_dirs or key in seen_dirs:
                                _fail("archive_member_unexpected")
                            seen_dirs.add(key)
                        elif ti.isreg():
                            if key not in exp_files or key in seen_files:
                                _fail("archive_member_unexpected")
                            ent = exp_files[key]
                            if ti.size != ent['size'] or ti.mode != ent['mode']:
                                _fail("archive_file_mode")
                            try:
                                src = tf.extractfile(ti)
                            except (OSError, tarfile.TarError):
                                _fail("archive_read")
                            if src is None:
                                _fail("archive_member_type")
                            with src:
                                _write_file(root_fd, key, src, ent, window)
                            seen_files.add(key)
                        else:
                            _fail("archive_member_type")
                except ExtractError:
                    raise
                except (OSError, tarfile.TarError):
                    _fail("archive_read")
                finally:
                    tf.close()
        except ExtractError:
            raise
        except OSError:
            _fail("archive_read")

        if seen_files != set(exp_files) or seen_dirs != set(exp_dirs):
            _fail("archive_member_missing")
        if any(k not in created for k in exp_dirs if k != ".") or (
                set(created) != set(exp_dirs) - {"."}):
            _fail("archive_member_unexpected")

        for key in sorted(created, key=lambda k: k.count("/"), reverse=True):
            window.check()
            dfd = _open_relative_dir(root_fd, key)
            try:
                try:
                    os.fchmod(dfd, exp_dirs[key])
                    os.fsync(dfd)
                except OSError:
                    _fail("destination_io")
            finally:
                os.close(dfd)
        try:
            os.fchmod(root_fd, expected["."])
            os.fsync(root_fd)
        except OSError:
            _fail("destination_io")

        post = _check_archive(fd, archive, window)
        if _sig(post) != _sig(pre):
            _fail("archive_identity_changed")
        if _hash_fd(fd, limit, window) != pre_digest:
            _fail("archive_identity_changed")
        try:
            rst = os.lstat(str(destination))
            rfd = os.fstat(root_fd)
        except OSError:
            _fail("destination_identity_changed")
        if _root_sig(rst) != _root_sig(root_st) or _root_sig(rfd) != _root_sig(root_st):
            _fail("destination_identity_changed")
        if stat.S_IMODE(rfd.st_mode) != expected["."]:
            _fail("destination_mode")

        return {
            "extracted": True,
            "comparison": "required_next_phase",
            "exact_name_route": "stdlib-open-v1",
            "archive_digest": pre_digest,
            "selection_digest": report.get("selection_digest"),
            "member_count": len(seen_files) + len(seen_dirs),
        }
    finally:
        if root_fd is not None:
            try:
                os.close(root_fd)
            except OSError:
                pass
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
