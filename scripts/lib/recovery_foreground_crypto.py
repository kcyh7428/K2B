"""One-time foreground GPG phase for archive encrypt/decrypt.

Preparation only; execution uses the existing pinned GPG tool.
Limitations:
- No real crypto/key/prompt test is claimed or performed automatically.
- Trust in source_fetch_receipt is outer operator IPC binding; JSON is not
  self-authenticating. This helper only cross-checks it against the held FD.
- Fresh home creation, custody, terminal/pinentry readiness, GPG entry
  readiness/version and outer phase P1 supervision are caller gates.
- On parent death or cleanup failure the private agent state may be unknown;
  callers must perform explicit manual inspection. No auto-retry.
- Cleanup is bounded to 5s and does not renew any outer P1 phase bound.
"""
import hashlib
import os
import stat
import subprocess
import time
from pathlib import Path

import recovery_nas_transport as transport

MAX_SOURCE = 2 * 1024 * 1024 * 1024
STDERR_CAP = 64 * 1024


class CryptoError(Exception):
    def __init__(self, reason, *, phase_reason=None, cleanup_reason=None):
        super().__init__(reason)
        self.phase_reason = phase_reason
        self.cleanup_reason = cleanup_reason


def _hex(data):
    return hashlib.sha256(data).hexdigest()


def _hash_tool(path, window):
    p = Path(path)
    try:
        if not p.is_absolute() or p.resolve() != p:
            raise CryptoError("tool_path_not_canonical")
        fd = os.open(str(p), os.O_RDONLY | os.O_NOFOLLOW)
    except OSError as exc:
        raise CryptoError("tool_open_failed") from None
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or not (st.st_mode & 0o111):
            raise CryptoError("tool_not_regular_executable")
        if st.st_uid not in (0, os.geteuid()):
            raise CryptoError("tool_untrusted_owner")
        if st.st_mode & 0o022:
            raise CryptoError('tool_writable_by_others')
        h = hashlib.sha256()
        while True:
            window.check()
            chunk = os.read(fd, 1 << 20)
            if not chunk:
                break
            h.update(chunk)
        if transport._stat_full(st) != transport._fstat_full(fd) or transport._stat_full(st) != transport._stat_full(os.lstat(p)):
            raise CryptoError('tool_changed')
        return h.hexdigest()
    except OSError as exc:
        raise CryptoError("tool_hash_failed") from None
    finally:
        os.close(fd)


def _check_home(home):
    p = Path(home)
    try:
        st = os.lstat(p)
    except OSError as exc:
        raise CryptoError("home_missing") from None
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
        raise CryptoError("home_not_dir")
    if not p.is_absolute() or '..' in p.parts or st.st_uid != os.getuid() or stat.S_IMODE(st.st_mode) != 0o700:
        raise CryptoError("home_mode")
    parent = os.lstat(p.parent)
    if not stat.S_ISDIR(parent.st_mode) or parent.st_uid != os.getuid() or stat.S_IMODE(parent.st_mode) != 0o700:
        raise CryptoError('home_parent')
    try:
        if not os.access(p, os.W_OK | os.X_OK):
            raise CryptoError("home_unwritable")
        with os.scandir(p) as it:
            if next(it, None) is not None:
                raise CryptoError("home_not_empty")
    except OSError as exc:
        raise CryptoError("home_scan_failed") from None
    try:
        transport._check_ancestors(p)
    except Exception as exc:
        raise CryptoError("home_ancestor_invalid") from None
    return st


def _fd_sig(fd):
    try:
        st = transport._fstat_full(fd)
    except Exception as exc:
        raise CryptoError("source_fstat_failed") from None
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        raise CryptoError("source_not_regular")
    if st.get("nlink") != 1:
        raise CryptoError("source_links")
    if st.get("mode", 0) & 0o777 != 0o600:
        raise CryptoError("source_mode")
    if st.get('uid') != os.getuid():
        raise CryptoError('source_owner')
    if st.get("size", -1) <= 0 or st["size"] > MAX_SOURCE:
        raise CryptoError("source_size")
    return st


def _sanitized_env():
    env = dict(os.environ)
    env.pop("GPG_AGENT_INFO", None)
    env.pop("GNUPGHOME", None)
    return env


def _check_receipt(receipt, sig, sha, size, expected_context):
    if not isinstance(receipt, dict):
        raise CryptoError("source_receipt_required")
    if not transport._is_hex64(expected_context) or receipt.get('context_sha256') != expected_context:
        raise CryptoError('source_fetch_context')
    try:
        loc = receipt["local"]
        rem = receipt["remote"]
        exe = receipt["execution"]
    except (KeyError, TypeError) as exc:
        raise CryptoError("source_receipt_schema") from None
    if loc.get("signature") != sig:
        raise CryptoError("source_receipt_signature")
    if loc.get("sha256") != sha or loc.get("size") != size:
        raise CryptoError("source_receipt_hash")
    if rem.get("sha256") != sha or rem.get("size") != size:
        raise CryptoError("source_remote_hash")
    if rem.get('action') != 'fetch' or type(rem.get('schema')) is not int or rem['schema'] != 1:
        raise CryptoError('source_remote_type')
    if not isinstance(rem.get('before'), dict) or rem.get("before") != rem.get("after"):
        raise CryptoError("source_remote_unstable")
    try:
        remote_sig = transport._validate_sig(rem['before'], rem['before']['uid'])
    except Exception:
        raise CryptoError('source_remote_signature') from None
    if remote_sig['size'] != size or not transport._is_hex64(receipt.get('context_sha256')):
        raise CryptoError('source_remote_signature')
    if not transport._is_hex64(exe) or type(receipt.get('exit_code')) is not int or receipt['exit_code'] != 0:
        raise CryptoError("source_remote_exit")
    if receipt.get("remote_program_sha256") != transport.REMOTE_PROGRAM_SHA256:
        raise CryptoError("source_remote_program")


def _wait_owned(proc, window):
    try:
        _, err = proc.communicate(timeout=window.remaining())
    except (subprocess.TimeoutExpired, KeyboardInterrupt, BaseException) as exc:
        try:
            proc.kill()
        except OSError:
            pass
        try:
            proc.wait(timeout=5)
        except Exception:
            pass
        raise CryptoError("gpg_partial_unknown") from None
    if len(err or b"") > STDERR_CAP:
        raise CryptoError("gpg_stderr_oversize")
    if proc.returncode != 0:
        raise CryptoError("gpg_exit_nonzero")
    return proc.returncode


def _crypt(direction, source_fd, output, fresh_home, gpg, gpg_sha256,
          gpgconf, gpgconf_sha256, expected_source_sha256,
          expected_output_sha256, window, *, source_fetch_receipt=None,
          expected_fetch_context_sha256=None):
    start = time.monotonic()
    if direction not in ("encrypt", "decrypt"):
        raise CryptoError("direction")
    if not (transport._is_hex64(expected_source_sha256)
            and transport._is_hex64(gpg_sha256)
            and transport._is_hex64(gpgconf_sha256)):
        raise CryptoError("hash_argument")
    if direction == "decrypt":
        if not transport._is_hex64(expected_fetch_context_sha256):
            raise CryptoError('expected_fetch_context_required')
        if not transport._is_hex64(expected_output_sha256):
            raise CryptoError("expected_output_required")
    else:
        if expected_output_sha256 is not None:
            raise CryptoError("expected_output_forbidden_encrypt")
        if source_fetch_receipt is not None:
            raise CryptoError("receipt_forbidden_encrypt")
        if expected_fetch_context_sha256 is not None:
            raise CryptoError('fetch_context_forbidden_encrypt')
    home = Path(fresh_home)
    checked_home = _check_home(home)
    gpg_h = _hash_tool(gpg, window)
    if gpg_h != gpg_sha256:
        raise CryptoError("gpg_hash_mismatch")
    gc_h = _hash_tool(gpgconf, window)
    if gc_h != gpgconf_sha256:
        raise CryptoError("gpgconf_hash_mismatch")
    st0 = _fd_sig(source_fd)
    src_sha, src_size = transport._hash_fd(source_fd, window)
    if src_sha != expected_source_sha256 or src_size != st0["size"]:
        raise CryptoError("source_hash")
    if _fd_sig(source_fd) != st0:
        raise CryptoError('source_changed')
    if direction == "decrypt":
        _check_receipt(source_fetch_receipt, st0, src_sha, src_size, expected_fetch_context_sha256)
    try:
        out_sig = transport._verify_output(output)
    except Exception as exc:
        raise CryptoError("output_verify") from None
    if out_sig.get("size") != 0 or out_sig != output.initial_signature:
        raise CryptoError("output_not_empty")
    if (out_sig['dev'], out_sig['ino']) == (st0['dev'], st0['ino']):
        raise CryptoError('output_alias')
    mfd = os.open(str(home), os.O_RDONLY | os.O_NOFOLLOW | os.O_DIRECTORY)
    home_id = os.fstat(mfd)
    if (home_id.st_dev, home_id.st_ino) != (checked_home.st_dev, checked_home.st_ino):
        os.close(mfd)
        raise CryptoError('home_changed')
    env = _sanitized_env()
    argv = [str(gpg), "--no-options", "--homedir", str(home),
            "--pinentry-mode", "ask", "--no-symkey-cache", "--output", "-"]
    if direction == "encrypt":
        argv += ["--cipher-algo", "AES256", "--symmetric"]
    else:
        argv += ["--decrypt"]
    argv_h = _hex("\0".join(argv).encode())
    proc = None
    gpg_rc = None
    cleanup_rc = None
    phase_reason = None
    try:
        os.lseek(source_fd, 0, os.SEEK_SET)
        os.lseek(output.fd, 0, os.SEEK_SET)
        try:
            proc = subprocess.Popen(
                argv, stdin=source_fd, stdout=output.fd,
                stderr=subprocess.PIPE, shell=False, close_fds=True, env=env)
        except OSError as exc:
            raise CryptoError("gpg_spawn_failed") from None
        gpg_rc = _wait_owned(proc, window)
    except BaseException as error:
        phase_reason = str(error) if isinstance(error, CryptoError) else 'gpg_phase_failed'
        raise
    finally:
        cleanup_ok = False
        cleanup_reason = 'home_identity_changed'
        try:
            cur = os.lstat(str(home))
            transport._check_ancestors(home)
            if (cur.st_dev, cur.st_ino, cur.st_uid, stat.S_IMODE(cur.st_mode)) == (home_id.st_dev, home_id.st_ino, os.getuid(), 0o700):
                try:
                    cc = subprocess.run(
                        [str(gpgconf), "--homedir", str(home),
                         "--kill", "gpg-agent"],
                        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                        stderr=subprocess.PIPE, shell=False, close_fds=True,
                        timeout=5, env=env)
                    cleanup_rc = cc.returncode
                    cleanup_ok = cleanup_rc == 0
                    cleanup_reason = 'agent_cleanup_nonzero' if not cleanup_ok else 'clean'
                except Exception:
                    cleanup_rc = None
                    cleanup_reason = 'agent_cleanup_unknown'
        except Exception:
            cleanup_reason = 'home_identity_unavailable'
        finally:
            try:
                os.close(mfd)
            except OSError:
                cleanup_ok = False
                cleanup_reason = 'home_descriptor_close_failed'
        if not cleanup_ok:
            raise CryptoError('cleanup_failed', phase_reason=phase_reason or 'gpg_completed',
                              cleanup_reason=cleanup_reason) from None
    if gpg_rc != 0:
        raise CryptoError("gpg_exit")
    try:
        os.fsync(output.fd)
    except OSError as exc:
        raise CryptoError("output_fsync") from None
    try:
        before_sig = transport._verify_output(output)
        out_sha, out_size = transport._hash_fd(output.fd, window)
        after_sig = transport._verify_output(output)
    except Exception as exc:
        raise CryptoError("output_postcheck") from None
    if before_sig != after_sig:
        raise CryptoError("output_unstable")
    if out_size <= 0:
        raise CryptoError("output_invalid")
    if direction == "decrypt" and out_sha != expected_output_sha256:
        raise CryptoError("output_hash_mismatch")
    try:
        st1 = _fd_sig(source_fd)
        src_sha2, src_size2 = transport._hash_fd(source_fd, window)
    except CryptoError:
        raise
    if st1 != st0 or (src_sha2, src_size2) != (src_sha, src_size):
        raise CryptoError("source_changed")
    try:
        home_now = os.stat(str(home), follow_symlinks=False)
    except OSError as exc:
        raise CryptoError("home_changed") from None
    if ((home_now.st_dev, home_now.st_ino, home_now.st_uid,
         home_now.st_mode & 0o777) !=
            (home_id.st_dev, home_id.st_ino, home_id.st_uid,
             home_id.st_mode & 0o777)):
        raise CryptoError("home_identity_changed")
    return {
        "direction": direction,
        "source_sha256": src_sha,
        "source_signature": st0,
        "output_sha256": out_sha,
        "output_size": out_size,
        "output_signature": after_sig,
        "argv_sha256": argv_h,
        "gpg_sha256": gpg_h,
        "gpgconf_sha256": gc_h,
        "gpg_exit": gpg_rc,
        "cleanup_exit": cleanup_rc,
        'home_identity': {'dev': home_id.st_dev, 'ino': home_id.st_ino,
                          'uid': home_id.st_uid, 'mode': stat.S_IMODE(home_id.st_mode)},
        'cleanup_argv_sha256': _hex('\0'.join([str(gpgconf), '--homedir', str(home), '--kill', 'gpg-agent']).encode()),
        "elapsed_seconds": time.monotonic() - start,
    }


def crypt(*args, **kwargs):
    """Run one fixed phase; return no payload-bearing underlying exception."""
    try:
        return _crypt(*args, **kwargs)
    except CryptoError:
        raise
    except Exception:
        raise CryptoError('crypto_failed') from None
