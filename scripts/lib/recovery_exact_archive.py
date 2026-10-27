import hashlib
import os
import stat
import tarfile
from pathlib import Path
import unicodedata

import recovery_exact_extract as extractutils
import recovery_nas_transport as transport


class ArchiveError(Exception):
    pass


def _sig(st):
    return {
        'dev': st.st_dev,
        'ino': st.st_ino,
        'size': st.st_size,
        'mode': stat.S_IMODE(st.st_mode),
        'nlink': st.st_nlink,
        'mtime_ns': st.st_mtime_ns,
        'ctime_ns': st.st_ctime_ns,
    }


class _Reader:
    def __init__(self, sourcefd, size, window):
        self._fd = sourcefd
        self._size = size
        self._window = window
        self._remaining = size
        self.total = 0
        self.hasher = hashlib.sha256()

    def read(self, size=-1):
        if size is None or size < 0:
            raise ArchiveError('unbounded_read')
        size = min(size, self._remaining)
        if size <= 0:
            return b''
        pieces = []
        remaining = size
        while remaining:
            self._window.check()
            piece = os.read(self._fd, remaining)
            if not piece:
                raise ArchiveError('short_read')
            pieces.append(piece)
            remaining -= len(piece)
        data = b''.join(pieces)
        self._remaining -= len(data)
        self.total += len(data)
        self.hasher.update(data)
        return data


def emit_tar(selection, rootfd, outputfd, window):
    files = selection['files']
    sha256 = selection['sha256']
    parent_modes = selection['parent_modes']

    dirs = sorted(parent_modes.keys(), key=lambda r: (0 if r == '.' else r.count('/') + 1, r))

    os.lseek(outputfd, 0, os.SEEK_SET)
    writer = os.fdopen(os.dup(outputfd), 'wb')
    count = 0
    try:
        tf = tarfile.open(fileobj=writer, mode='w', format=tarfile.PAX_FORMAT, encoding='utf-8')
        try:
            for rel in dirs:
                path = '' if rel == '.' else rel
                dfd = extractutils._open_relative_dir(rootfd, path)
                try:
                    st = os.fstat(dfd)
                    if not stat.S_ISDIR(st.st_mode):
                        raise ArchiveError('source_not_directory')
                    if stat.S_IMODE(st.st_mode) != parent_modes[rel]:
                        raise ArchiveError('source_directory_mode')
                finally:
                    os.close(dfd)
                window.check()
                ti = tarfile.TarInfo(name=rel)
                ti.type = tarfile.DIRTYPE
                ti.mode = parent_modes[rel]
                ti.size = 0
                ti.mtime = 0
                ti.uid = 0
                ti.gid = 0
                ti.uname = ''
                ti.gname = ''
                tf.addfile(ti)
                count += 1

            for entry in files:
                rel = entry['rel']
                sig = entry['signature']
                if sig['size'] < 0:
                    raise ArchiveError('source_size')
                parent_rel, _, leaf = rel.rpartition('/')
                if not leaf:
                    raise ArchiveError('source_path')
                parentfd = extractutils._open_relative_dir(rootfd, parent_rel)
                sourcefd = None
                reader = None
                try:
                    window.check()
                    sourcefd = os.open(
                        leaf,
                        os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                        dir_fd=parentfd,
                    )
                    st = os.fstat(sourcefd)
                    if not stat.S_ISREG(st.st_mode):
                        raise ArchiveError('source_not_regular')
                    if _sig(st) != sig:
                        raise ArchiveError('source_signature')
                    reader = _Reader(sourcefd, sig['size'], window)
                    window.check()
                    ti = tarfile.TarInfo(name=rel)
                    ti.type = tarfile.REGTYPE
                    ti.mode = sig['mode']
                    ti.size = sig['size']
                    secs, ns = divmod(abs(sig['mtime_ns']), 1000000000)
                    sign = '-' if sig['mtime_ns'] < 0 else ''
                    ti.mtime = sig['mtime_ns'] // 1000000000
                    ti.pax_headers = {'mtime': '%s%d.%09d' % (sign, secs, ns)}
                    ti.uid = 0
                    ti.gid = 0
                    ti.uname = ''
                    ti.gname = ''
                    tf.addfile(ti, reader)
                    if reader.total != sig['size']:
                        raise ArchiveError('source_size')
                    if reader.hasher.hexdigest() != sha256[rel]:
                        raise ArchiveError('source_digest')
                    st = os.fstat(sourcefd)
                    if not stat.S_ISREG(st.st_mode) or _sig(st) != sig:
                        raise ArchiveError('source_signature')
                    st = os.stat(leaf, dir_fd=parentfd, follow_symlinks=False)
                    if not stat.S_ISREG(st.st_mode) or _sig(st) != sig:
                        raise ArchiveError('source_signature')
                    count += 1
                finally:
                    try:
                        if sourcefd is not None:
                            os.close(sourcefd)
                    finally:
                        os.close(parentfd)
        finally:
            tf.close()
    finally:
        writer.close()
    return count


def _identity(st):
    return st.st_dev, st.st_ino, stat.S_IMODE(st.st_mode)


def _disjoint(output, root):
    source = unicodedata.normalize('NFD', os.path.realpath(root)).casefold().rstrip('/')
    for path in (output.path, output.path.parent):
        target = unicodedata.normalize('NFD', os.path.realpath(path)).casefold().rstrip('/')
        if source == target or source.startswith(target + '/') or target.startswith(source + '/'):
            raise ArchiveError('source_output_overlap')


def _create_exact(component, contract, selection, output, window):
    """Create only admitted PAX fields. R2 and restored comparison remain required."""
    rootfd = None
    try:
        contract._verify_selection(selection)
        if not selection['sha256']:
            raise ArchiveError('selection_unbound')
        window.check()
        if transport._verify_output(output) != output.initial_signature or output.initial_signature['size'] != 0:
            raise ArchiveError('output_not_fresh')
        root = Path(selection['root'])
        _disjoint(output, root)
        extractutils._check_anon_chain(root)
        capacity = os.fstatvfs(output.fd)
        if capacity.f_bavail * capacity.f_frsize < 8 * selection['profile']['total_bytes'] + 1024**3:
            raise ArchiveError('capacity_insufficient')
        rootfd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        expected_root = (selection['root_dev'], selection['root_ino'], selection['root_mode'])
        if _identity(os.fstat(rootfd)) != expected_root or _identity(os.lstat(root)) != expected_root:
            raise ArchiveError('source_root_identity')
        count = emit_tar(selection, rootfd, output.fd, window)
        extractutils._check_anon_chain(root)
        if _identity(os.fstat(rootfd)) != expected_root or _identity(os.lstat(root)) != expected_root:
            raise ArchiveError('source_root_identity')
        os.fsync(output.fd)
        pre = transport._verify_output(output)
        digest, size = transport._hash_fd(output.fd, window)
        if size > contract.MAX_ARCHIVE_BYTES or transport._verify_output(output) != pre:
            raise ArchiveError('archive_changed_or_oversized')
        report = contract.inspect_archive(component, output.path, selection, digest, window)
        if (report['archive_dev'], report['archive_ino'], report['archive_size']) != (pre['dev'], pre['ino'], pre['size']):
            raise ArchiveError('archive_identity')
        if report['archive_digest'] != digest or report['selection_digest'] != selection['selection_digest']:
            raise ArchiveError('archive_binding')
        if report['member_count'] != count or count != selection['profile']['member_count']:
            raise ArchiveError('archive_member_count')
        if transport._verify_output(output) != pre:
            raise ArchiveError('archive_changed')
        return {'exact_name_producer': 'stdlib-pax-v1', 'archive_digest': digest,
                'archive_size': size, 'archive_signature': pre,
                'selection_digest': selection['selection_digest'], 'member_count': count,
                'comparison': 'required_next_phase', 'R2': 'required_next_phase'}
    except ArchiveError:
        raise
    except (OSError, tarfile.TarError, extractutils.ExtractError, transport.TransportError):
        raise ArchiveError('archive_io_or_path') from None
    finally:
        if rootfd is not None:
            os.close(rootfd)


def create_exact(component, contract, selection, output, window):
    try:
        return _create_exact(component, contract, selection, output, window)
    except ArchiveError:
        raise
    except (OSError, tarfile.TarError, extractutils.ExtractError, transport.TransportError):
        raise ArchiveError('archive_io_or_path') from None
