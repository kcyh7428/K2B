"""Bounded one-run consumer contract for a reviewed standard-tool package.

This module supplements an already-reviewed observation/path/command package.
It provides: a hash-verified import of the frozen P2 component, a single
monotonic Window, process-group supervision, metadata-only selection
preparation/destination profile checks, hash binding, archive inspection,
restore comparison, source re-check and a declared post-verification alias
reconstruction.

Nothing here authorizes a real run. All reasons are static sanitized codes;
source paths and failed field details live only in private ``_OperatorRecord``
objects that callers must not forward to provider output.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import select
import signal
import stat
import struct
import subprocess
import sys
import threading
import time
import types
import unicodedata
import importlib.util
from pathlib import Path

P2_TEST_SHA256 = "89108b15119e9ffc3cf2d440bb6b047b3ee5c1c42013c285d891c43f1f617474"
P2_METHOD_SHA256 = "50361f62e1fd678260c89e635eebfe0d033e04ae9391acc246dccf6761aaaa3d"

P2_TEST_REL = "tests/test_recovery_standard_tools.py"
P2_METHOD_REL = "plans/2026-10-27_k2b-standard-tool-recovery-component.md"

COMPONENT_FILE_BOUND = 256 * 1024

MAX_WINDOW_SECONDS = 600.0
MAX_FILES = 16384
MAX_MEMBERS = 32768
MAX_FILE_BYTES = 64 * 1024 * 1024
MAX_TOTAL_BYTES = 1024 * 1024 * 1024
MAX_COMPONENT_BYTES = 240
MAX_FULL_PATH_BYTES = 900
MAX_CUMULATIVE_PATH_BYTES = 16 * 1024 * 1024
MAX_ARCHIVE_BYTES = MAX_TOTAL_BYTES + 128 * 1024 * 1024
MAX_METADATA_READ = 1024 * 1024
IPC_RESULT_LIMIT = 32 * 1024 * 1024
MIN_START_HEADROOM = 5.0
DEFAULT_PHASE_ALLOWANCE = 60.0
REAL_WALL_CAP = 300.0

_HEX = frozenset("0123456789abcdef")
_REPO_FIELD_MAX_BYTES = 900


class ContractError(Exception):
    """Static sanitized reason code. Never carries paths or field values."""

    def __init__(self, reason: str):
        if not isinstance(reason, str) or not reason:
            reason = "contract_error"
        self.reason = reason
        super().__init__(reason)


class _OperatorRecord(Exception):
    """Private operator detail (may include paths/fields). Never surfaced."""

    def __init__(self, reason: str, detail=None):
        self.reason = reason
        self.detail = detail
        super().__init__(reason)


# ---------------------------------------------------------------------------
# Window
# ---------------------------------------------------------------------------


class Window:
    """Single monotonic deadline; phases are nonrenewable."""

    def __init__(self, seconds: float = 600.0, *, clock=time.monotonic,
                 calibration=None, trusted_calibration_sha256=None):
        if isinstance(seconds, bool) or not isinstance(seconds, (int, float)):
            raise ContractError("window_bounds")
        seconds = float(seconds)
        if not (seconds > 0.0) or seconds != seconds or seconds == float("inf"):
            raise ContractError("window_bounds")
        if seconds > MAX_WINDOW_SECONDS:
            raise ContractError("window_bounds")
        if not callable(clock):
            raise ContractError("window_clock")
        self._clock = clock
        self._last_clock = self._now()
        self._deadline = self._last_clock + seconds
        self._seconds = seconds
        self._phases = {}
        self._calibration = {}
        if calibration is not None:
            try:
                if set(calibration) != {"version", "stages"} or type(calibration["version"]) is not int or calibration["version"] != 1:
                    raise ValueError
                if not isinstance(calibration["stages"], dict) or not 1 <= len(calibration["stages"]) <= 128:
                    raise ValueError
                for name, entry in calibration["stages"].items():
                    if not isinstance(name, str) or not name or len(name) > 128:
                        raise ValueError
                    if set(entry) != {"kind", "measured_seconds", "receipt_sha256"}:
                        raise ValueError
                    maximum = {"archive": 60.0, "hash": 120.0, "metadata": 60.0, "sqlite": 30.0}[entry["kind"]]
                    measured = entry["measured_seconds"]
                    digest = entry["receipt_sha256"]
                    if isinstance(measured, bool) or not isinstance(measured, (int, float)) or not math.isfinite(measured) or not 0 < measured < maximum / 2:
                        raise ValueError
                    if not isinstance(digest, str) or len(digest) != 64 or any(c not in _HEX for c in digest):
                        raise ValueError
                    self._calibration[name] = (maximum, float(measured), digest)
                raw = json.dumps(calibration, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
                if len(raw) > 65536 or hashlib.sha256(raw).hexdigest() != trusted_calibration_sha256:
                    raise ValueError
            except (TypeError, ValueError, KeyError, AttributeError):
                raise ContractError("calibration_invalid") from None
        elif trusted_calibration_sha256 is not None:
            raise ContractError("calibration_invalid")

    def _now(self):
        try:
            value = self._clock()
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError
            value = float(value)
            if not math.isfinite(value) or value < getattr(self, "_last_clock", value):
                raise ValueError
        except Exception:
            raise ContractError("window_clock") from None
        self._last_clock = value
        return value

    def check(self):
        if self._now() >= self._deadline:
            raise ContractError("window_expired")

    def remaining(self) -> float:
        return max(0.0, self._deadline - self._now())

    def whole_deadline(self) -> float:
        return self._deadline

    def begin_phase(self, name, allowance: float, measured: float) -> float:
        if not isinstance(name, str) or not name:
            raise ContractError("phase_name")
        if name in self._phases:
            raise ContractError("phase_reentry")
        if name not in self._calibration:
            raise ContractError("calibration_missing")
        maximum, bound_measured, _receipt = self._calibration[name]
        for value in (allowance, measured):
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ContractError("phase_bounds")
            if not math.isfinite(value) or value < 0.0:
                raise ContractError("phase_bounds")
        if measured != bound_measured or allowance > maximum or allowance <= 2 * bound_measured:
            raise ContractError("calibration_mismatch")
        self.check()
        remaining_whole = self.remaining()
        if remaining_whole < 2.0 * float(measured) + MIN_START_HEADROOM:
            raise ContractError("insufficient_headroom")
        if float(allowance) > remaining_whole:
            raise ContractError("insufficient_headroom")
        phase_end = self._now() + float(allowance)
        if phase_end > self._deadline:
            phase_end = self._deadline
        self._phases[name] = phase_end
        effective = self.effective_timeout(name)
        if effective <= 0.0:
            self._phases.pop(name, None)
            raise ContractError("phase_expired")
        return effective

    def effective_timeout(self, name) -> float:
        try:
            phase_end = self._phases[name]
        except KeyError:
            raise ContractError("phase_unknown")
        now = self._now()
        whole = self._deadline - now
        phase = phase_end - now
        return max(0.0, min(whole, phase))

    def assume(self, name) -> None:
        self.check()
        if name not in self._phases:
            raise ContractError("phase_unknown")
        effective = self.effective_timeout(name)
        if effective <= 0.0:
            raise ContractError("phase_expired")


# ---------------------------------------------------------------------------
# Component loading
# ---------------------------------------------------------------------------


def _sha256_file_no_follow(path: Path, bound: int) -> str:
    try:
        fd = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        raise ContractError("component_missing")
    except OSError:
        raise ContractError("component_open")
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise ContractError("component_not_regular")
        h = hashlib.sha256()
        total = 0
        while True:
            try:
                chunk = os.read(fd, 1024 * 1024)
            except OSError:
                raise ContractError("component_open")
            if not chunk:
                break
            total += len(chunk)
            if total > bound:
                raise ContractError("component_too_large")
            h.update(chunk)
        return h.hexdigest()
    finally:
        os.close(fd)


def _read_verified_bytes(path: Path, bound: int) -> bytes:
    try:
        fd = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        raise ContractError("component_missing")
    except OSError:
        raise ContractError("component_open")
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise ContractError("component_not_regular")
        chunks = []
        total = 0
        while True:
            try:
                chunk = os.read(fd, 1024 * 1024)
            except OSError:
                raise ContractError("component_open")
            if not chunk:
                break
            total += len(chunk)
            if total > bound:
                raise ContractError("component_too_large")
            chunks.append(chunk)
        end = os.fstat(fd)
        named = os.lstat(path)
        signature = lambda item: (item.st_dev, item.st_ino, item.st_size,
                                  item.st_mtime_ns, item.st_ctime_ns,
                                  item.st_mode, item.st_nlink)
        if signature(st) != signature(end) or signature(end) != signature(named):
            raise ContractError("component_identity_changed")
        _check_component_path_chain(path)
        return b"".join(chunks)
    finally:
        os.close(fd)


def _check_relative_no_symlink(base: Path, rel: str) -> Path:
    parts = rel.split("/")
    if any(p in ("", ".", "..") for p in parts):
        raise ContractError("component_path")
    cur = Path(base)
    for idx, part in enumerate(parts):
        cur = cur / part
        try:
            st = os.lstat(str(cur))
        except FileNotFoundError:
            raise ContractError("component_missing")
        except OSError:
            raise ContractError("component_open")
        if stat.S_ISLNK(st.st_mode):
            raise ContractError("component_symlink")
        if idx == len(parts) - 1:
            if not stat.S_ISREG(st.st_mode):
                raise ContractError("component_not_regular")
        else:
            if not stat.S_ISDIR(st.st_mode):
                raise ContractError("component_ancestor")
    return cur


def _valid_commit(commit) -> bool:
    return (
        isinstance(commit, str)
        and len(commit) == 40
        and all(c in _HEX for c in commit)
    )


def _valid_repo(repo) -> bool:
    if not isinstance(repo, str) or not repo:
        return False
    try:
        encoded = repo.encode("utf-8")
    except UnicodeError:
        return False
    return (
        0 < len(encoded) <= _REPO_FIELD_MAX_BYTES
        and repo.startswith("/")
        and "\x00" not in repo
    )


def _git_tree_bytes(repo: str, commit: str, rel: str) -> bytes:
    """Exact Git blob bytes via bounded local ``git show commit:rel``.

    Requires a real 40-lowerhex commit and a non-symlink absolute repo
    directory that contains a ``.git`` entry. No network. The returned
    value is the SHA-256 hex digest (ASCII bytes) of the committed blob,
    derived from the exact bytes emitted by git, never from the working tree.
    """
    if not _valid_commit(commit):
        raise ContractError("delivery_commit")
    if not _valid_repo(repo):
        raise ContractError("delivery_repo")
    parts = rel.split("/")
    if not parts or any(p in ("", ".", "..") for p in parts) or "\\" in rel:
        raise ContractError("component_path")
    base = Path(repo)
    try:
        st = os.lstat(str(base))
    except FileNotFoundError:
        raise ContractError("delivered_repo")
    except OSError:
        raise ContractError("delivered_repo")
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
        raise ContractError("delivered_repo")
    git_entry = base / ".git"
    try:
        gst = os.lstat(str(git_entry))
    except (FileNotFoundError, OSError):
        raise ContractError("delivered_repo")
    if stat.S_ISLNK(gst.st_mode) or not (stat.S_ISDIR(gst.st_mode) or stat.S_ISREG(gst.st_mode)):
        raise ContractError("delivered_repo")
    env = {
        "PATH": "/usr/bin:/bin",
        "LC_ALL": "C",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
    }
    proc = None
    try:
        proc = subprocess.Popen(
            ["/usr/bin/git", "-C", repo, "--no-pager", "cat-file", "blob", "%s:%s" % (commit, rel)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=env,
            close_fds=True,
        )
        deadline = time.monotonic() + 30
        blob = bytearray()
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ContractError("delivered_tree_unavailable")
            readable, _, _ = select.select([proc.stdout], [], [], remaining)
            if not readable:
                raise ContractError("delivered_tree_unavailable")
            chunk = os.read(proc.stdout.fileno(), min(65536, COMPONENT_FILE_BOUND + 1 - len(blob)))
            if not chunk:
                break
            blob.extend(chunk)
            if len(blob) > COMPONENT_FILE_BOUND:
                raise ContractError("delivered_tree_mismatch")
        proc.wait(timeout=max(0.001, deadline - time.monotonic()))
    except (OSError, subprocess.SubprocessError):
        raise ContractError("delivered_tree_unavailable") from None
    finally:
        if proc is not None:
            if proc.poll() is None:
                proc.kill()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                raise ContractError("delivery_cleanup_uncertain") from None
            proc.stdout.close()
    if proc.returncode != 0:
        raise ContractError("delivered_tree_unavailable")
    return hashlib.sha256(blob).hexdigest().encode("ascii")


def _check_component_path_chain(path: Path) -> None:
    if not isinstance(path, Path) or not path.is_absolute():
        raise ContractError("component_path")
    cur = Path(path.parts[0])
    if not str(path.parts[0]):
        raise ContractError("component_path")
    try:
        st = os.lstat(str(cur))
    except OSError:
        raise ContractError("component_missing")
    if stat.S_ISLNK(st.st_mode):
        raise ContractError("component_symlink")
    for part in path.parts[1:]:
        cur = cur / part
        try:
            st = os.lstat(str(cur))
        except FileNotFoundError:
            raise ContractError("component_missing")
        except OSError:
            raise ContractError("component_open")
        if stat.S_ISLNK(st.st_mode):
            raise ContractError("component_symlink")


def load_component(
    test_file: Path,
    plan_file: Path,
    *,
    execution: bool = False,
    delivered_repo=None,
    delivered_commit=None,
):
    if not isinstance(test_file, (str, os.PathLike)) or not isinstance(
            plan_file, (str, os.PathLike)):
        raise ContractError("component_path")
    test_path = Path(test_file)
    plan_path = Path(plan_file)

    if execution:
        if not _valid_commit(delivered_commit):
            raise ContractError("delivery_commit")
        if not _valid_repo(delivered_repo):
            raise ContractError("delivery_repo")
        for fixed_rel, fixed_digest in (
            (P2_TEST_REL, P2_TEST_SHA256),
            (P2_METHOD_REL, P2_METHOD_SHA256),
        ):
            tree_digest = _git_tree_bytes(delivered_repo, delivered_commit, fixed_rel)
            if tree_digest.decode("ascii") != fixed_digest:
                raise ContractError("delivered_tree_mismatch")

    _check_component_path_chain(test_path)
    _check_component_path_chain(plan_path)

    test_bytes = _read_verified_bytes(test_path, COMPONENT_FILE_BOUND)
    plan_bytes = _read_verified_bytes(plan_path, COMPONENT_FILE_BOUND)

    test_digest = hashlib.sha256(test_bytes).hexdigest()
    plan_digest = hashlib.sha256(plan_bytes).hexdigest()
    if test_digest != P2_TEST_SHA256 or plan_digest != P2_METHOD_SHA256:
        raise ContractError("component_hash_mismatch")

    module = _exec_component_bytes(test_bytes)

    for name in (
        "source_signature",
        "admit_regular",
        "admit_mode",
        "tool_env",
        "parse_ledger",
        "compare_ledgers",
    ):
        if not callable(getattr(module, name, None)):
            raise ContractError("component_missing_symbol")
    return module


_IMPORT_LOCK = threading.Lock()


def _exec_component_bytes(source: bytes):
    """Compile and exec the already-verified exact bytes as a fresh module.

    This avoids a second filesystem read (importlib may reread the file and
    hit TOCTOU). The verified ``source`` bytes are the only source. No
    ``__pycache__`` write occurs; ``sys.dont_write_bytecode`` is set for the
    duration.
    """
    name = "_k2b_p2_frozen_component"
    if not isinstance(source, (bytes, bytearray)):
        raise ContractError("component_import")
    with _IMPORT_LOCK:
        module = types.ModuleType(name)
        module.__file__ = "<k2b-verified-component>"
        module.__loader__ = None
        try:
            code = compile(bytes(source), module.__file__, "exec", dont_inherit=True)
        except (SyntaxError, ValueError):
            raise ContractError("component_import")
        prev = sys.dont_write_bytecode
        old = sys.modules.get(name)
        sys.modules[name] = module
        sys.dont_write_bytecode = True
        try:
            exec(code, module.__dict__)
        except ContractError:
            _restore_module(name, old)
            raise
        except SystemExit:
            _restore_module(name, old)
            raise ContractError("component_import")
        except BaseException:
            _restore_module(name, old)
            raise ContractError("component_import")
        finally:
            sys.dont_write_bytecode = prev
        return module


def _restore_module(name: str, old) -> None:
    if old is None:
        sys.modules.pop(name, None)
    else:
        sys.modules[name] = old


# ---------------------------------------------------------------------------
# Process supervision
# ---------------------------------------------------------------------------


def _serialize_result(obj) -> bytes:
    try:
        data = json.dumps(obj, allow_nan=False).encode("utf-8")
    except Exception:
        raise _OperatorRecord("result_not_json")
    if len(data) > IPC_RESULT_LIMIT:
        raise _OperatorRecord("result_too_large")
    return data


def _write_all(fd: int, data: bytes) -> bool:
    view = memoryview(data)
    while view:
        try:
            n = os.write(fd, view)
        except OSError:
            return False
        if n <= 0:
            return False
        view = view[n:]
    return True


def _child_main(func, wfd: int, startfd: int):
    try:
        os.setsid()
    except OSError:
        try:
            os.close(wfd)
        except OSError:
            pass
        os._exit(70)
    try:
        if not _write_all(wfd, b"R"):
            os._exit(71)
        if os.read(startfd, 1) != b"G":
            os._exit(71)
        os.close(startfd)
        try:
            result = func()
            data = _serialize_result({"status": "ok", "result": result})
        except BaseException as exc:
            reason = "step_result_invalid" if isinstance(exc, _OperatorRecord) else "step_failed"
            data = _serialize_result({"status": "failed", "reason": reason})
        header = struct.pack("!I", len(data))
        if not _write_all(wfd, header + data):
            os._exit(73)
        try:
            os.close(wfd)
        except OSError:
            pass
        # Remain the live, owned process-group leader until the parent
        # terminates the group. A zombie leader can make killpg ambiguous
        # on Darwin. Only a complete bounded result authorizes success.
        while True:
            signal.pause()
    except BaseException:
        try:
            os.close(wfd)
        except OSError:
            pass
        os._exit(74)


def supervise_step(stage, function, window, allowance, measured):
    if not callable(function):
        raise ContractError("step_callable")
    if not isinstance(stage, str) or not stage:
        raise ContractError("step_stage")
    if threading.active_count() != 1:
        raise ContractError("multithreaded_caller")
    effective = window.begin_phase(stage, allowance, measured)
    if effective <= 0.0:
        raise ContractError("phase_expired")
    rfd = wfd = startfd = gofd = None
    try:
        rfd, wfd = os.pipe()
        startfd, gofd = os.pipe()
    except OSError:
        for fd in [rfd, wfd, startfd, gofd]:
            if fd is not None:
                os.close(fd)
        raise ContractError("pipe_failed")
    try:
        pid = os.fork()
    except OSError:
        os.close(rfd)
        os.close(wfd)
        os.close(startfd)
        os.close(gofd)
        raise ContractError("fork_failed")
    if pid == 0:
        try:
            os.close(rfd)
            os.close(gofd)
        except OSError:
            pass
        _child_main(function, wfd, startfd)

    os.close(wfd)
    os.close(startfd)
    return _parent_oversee(pid, rfd, gofd, window, stage, effective)


def _kill_group(pid: int, ready: bool) -> bool:
    try:
        if ready:
            os.killpg(pid, signal.SIGKILL)
        else:
            os.kill(pid, signal.SIGKILL)
        return True
    except ProcessLookupError:
        return True
    except OSError:
        return False


def _reap(pid: int, hard_cap: float = 5.0) -> bool:
    deadline = time.monotonic() + hard_cap
    while True:
        try:
            done, _ = os.waitpid(pid, os.WNOHANG)
        except ChildProcessError:
            return True
        except OSError:
            return False
        if done == pid:
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.01)


def _parse_stream(buffer: bytearray, ready: bool, expected):
    """Advance parser state over ``buffer``; return (ready, header_needed, expected, complete_len)."""
    if not ready:
        if not buffer:
            return False, True, None, None
        if buffer[:1] != b"R":
            raise ContractError("step_control")
        del buffer[:1]
        ready = True
    if expected is None:
        if len(buffer) < 4:
            return ready, True, None, None
        expected = struct.unpack("!I", bytes(buffer[:4]))[0]
        del buffer[:4]
        if expected > IPC_RESULT_LIMIT:
            raise ContractError("step_result_too_large")
    if len(buffer) >= expected:
        return ready, False, expected, expected
    return ready, False, expected, None


def _parent_oversee(pid, rfd, gofd, window, stage, effective):
    ready = False
    buffer = bytearray()
    expected = None
    done = False
    timed_out = False
    uncertain = False
    payload = None
    acknowledged = False
    early_eof = False
    read_deadline = time.monotonic() + float(effective)
    hardwall = time.monotonic() + float(REAL_WALL_CAP)
    try:
        while True:
            now_real = time.monotonic()
            try:
                    clock_now = window._now()
            except Exception:
                    uncertain = True
                    break
            if now_real >= read_deadline or now_real >= hardwall:
                timed_out = True
                break
            if clock_now is not None and clock_now >= window.whole_deadline():
                timed_out = True
                break
            wait = min(0.02, read_deadline - now_real, hardwall - now_real)
            if wait < 0:
                wait = 0.0
            try:
                r, _, _ = select.select([rfd], [], [], wait)
            except InterruptedError:
                r = []
            except OSError:
                uncertain = True
                break
            if r:
                try:
                    chunk = os.read(rfd, 65536)
                except OSError:
                    chunk = b""
                if not chunk:
                    early_eof = True
                    break
                buffer.extend(chunk)
                if len(buffer) > IPC_RESULT_LIMIT + 8:
                    uncertain = True
                    break
            if buffer or expected is not None:
                try:
                    ready, _hdr, expected, complete = _parse_stream(
                        buffer, ready, expected)
                except ContractError:
                    uncertain = True
                    break
                if ready and not acknowledged:
                    # The child cannot invoke the step before this verified
                    # ownership handshake. Before it, killing only the leader
                    # cannot leave step-spawned descendants behind.
                    try:
                        if os.getpgid(pid) != pid or os.getsid(pid) != pid:
                            raise OSError
                        if not _write_all(gofd, b"G"):
                            raise OSError
                    except OSError:
                        uncertain = True
                        break
                    acknowledged = True
                if complete is not None:
                    payload = bytes(buffer[:complete])
                    # Keep reading until EOF or short grace for remaining
                    # body; the child has already written full body.
                    done = True
                    break
        # Always drain bounded IPC before join.
        drain_deadline = time.monotonic() + 1.0
        while time.monotonic() < drain_deadline:
            try:
                r, _, _ = select.select([rfd], [], [], 0.02)
            except (OSError, InterruptedError):
                break
            if not r:
                if done:
                    break
                continue
            try:
                chunk = os.read(rfd, 65536)
            except OSError:
                break
            if not chunk:
                break
            if len(buffer) + len(chunk) > IPC_RESULT_LIMIT + 8:
                uncertain = True
                break
            buffer.extend(chunk)
    finally:
        killed = _kill_group(pid, ready)
        reaped = _reap(pid)
        if not killed or not reaped:
            uncertain = True
        try:
            os.close(rfd)
            os.close(gofd)
        except OSError:
            pass

    if uncertain:
        raise ContractError("cleanup_uncertain")
    if timed_out or not done:
        raise ContractError("step_failed" if early_eof and not timed_out else "step_timeout")
    try:
        frame = json.loads(payload.decode("utf-8"))
    except Exception:
        raise ContractError("step_result_malformed")
    if isinstance(frame, dict) and set(frame) == {"status", "result"} and frame["status"] == "ok":
        return frame["result"]
    if isinstance(frame, dict) and set(frame) == {"status", "reason"} and frame["status"] == "failed" and frame["reason"] in {"step_failed", "step_result_invalid"}:
        raise ContractError(frame["reason"])
    raise ContractError("step_result_malformed")


# ---------------------------------------------------------------------------
# Selection preparation
# ---------------------------------------------------------------------------


def _check_root(root: Path, window) -> Path:
    if not isinstance(root, Path):
        raise ContractError("root_not_absolute")
    if not root.is_absolute():
        raise ContractError("root_not_absolute")
    window.check()
    parts = root.parts
    cur = Path(parts[0])
    try:
        os.lstat(str(cur))
    except OSError:
        raise ContractError("root_missing")
    for part in parts[1:]:
        try:
            if len(part.encode("utf-8")) > MAX_COMPONENT_BYTES:
                raise ContractError("root_component_too_long")
        except UnicodeError:
            raise ContractError("root_component_invalid") from None
        cur = cur / part
        try:
            st = os.lstat(str(cur))
        except FileNotFoundError:
            raise ContractError("root_missing")
        except OSError:
            raise ContractError("root_unreadable")
        if stat.S_ISLNK(st.st_mode):
            raise ContractError("root_symlink_ancestor")
    try:
        st = os.lstat(str(root))
    except OSError:
        raise ContractError("root_missing")
    if not stat.S_ISDIR(st.st_mode):
        raise ContractError("root_not_dir")
    return root


def _validate_relative_path(rel: str, window) -> str:
    if not isinstance(rel, str) or not rel:
        raise ContractError("relative_empty")
    try:
        raw = rel.encode("utf-8")
        raw.decode("utf-8")
    except UnicodeError:
        raise ContractError("relative_invalid_utf8")
    if rel.startswith("/"):
        raise ContractError("relative_absolute")
    if "\\" in rel:
        raise ContractError("relative_backslash")
    for ch in rel:
        code = ord(ch)
        if code < 0x20 or code == 0x7F:
            raise ContractError("relative_control")
    parts = rel.split("/")
    for part in parts:
        if part in ("", ".", ".."):
            raise ContractError("relative_traversal")
        comp_len = len(part.encode("utf-8"))
        if comp_len > MAX_COMPONENT_BYTES:
            raise ContractError("component_too_long")
    window.check()
    return rel


def _identity_key(rel: str) -> str:
    return unicodedata.normalize("NFD", rel).casefold()


def _lstat_chain(root: Path, rel: str, window) -> Path:
    parts = rel.split("/")
    cur = root
    for part in parts[:-1]:
        cur = cur / part
        try:
            st = os.lstat(str(cur))
        except FileNotFoundError:
            raise ContractError("path_missing")
        except OSError:
            raise ContractError("path_unreadable")
        if stat.S_ISLNK(st.st_mode):
            raise ContractError("path_symlink")
        if not stat.S_ISDIR(st.st_mode):
            raise ContractError("path_not_dir")
    cur = cur / parts[-1]
    window.check()
    return cur


def _signature(st) -> dict:
    return {
        "dev": int(st.st_dev),
        "ino": int(st.st_ino),
        "size": int(st.st_size),
        "mtime_ns": int(st.st_mtime_ns),
        "ctime_ns": int(st.st_ctime_ns),
        "mode": int(st.st_mode & 0o7777),
        "nlink": int(st.st_nlink),
    }


def _pathconf_limits(dirpath: Path) -> tuple:
    try:
        name_max = os.pathconf(str(dirpath), "PC_NAME_MAX")
        path_max = os.pathconf(str(dirpath), "PC_PATH_MAX")
    except (OSError, ValueError):
        raise ContractError("pathconf_unknown")
    if name_max is None or path_max is None:
        raise ContractError("pathconf_unknown")
    if name_max <= 0 or path_max <= 0:
        raise ContractError("pathconf_unknown")
    return int(name_max), int(path_max)


def _parent_modes(root: Path, rels: list, window) -> dict:
    modes = {}
    seen = set()
    for rel in rels:
        parts = rel.split("/")
        for i in range(1, len(parts)):
            prefix = "/".join(parts[:i])
            if prefix in seen:
                continue
            seen.add(prefix)
            window.check()
            p = root / prefix
            try:
                st = os.lstat(str(p))
            except OSError:
                raise ContractError("parent_not_dir")
            if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
                raise ContractError("parent_not_dir")
            if stat.S_IMODE(st.st_mode) & 0o7000:
                raise ContractError("parent_special_bits")
            modes[prefix] = st.st_mode & 0o7777
    try:
        rst = os.lstat(str(root))
    except OSError:
        raise ContractError("root_not_dir")
    if not stat.S_ISDIR(rst.st_mode):
        raise ContractError("root_not_dir")
    root_mode = rst.st_mode & 0o7777
    if root_mode & 0o7000:
        raise ContractError("root_special_bits")
    modes["."] = root_mode
    return modes


def _membership(root: Path, rel_dirs: list, rels: list, window) -> dict:
    result = {".": _listdir_sorted(root, window)}
    count = len(result["."])
    total = sum(len(item.encode("utf-8")) for item in result["."])
    all_rels = set()
    for rel in rels:
        parts = rel.split("/")
        for i in range(1, len(parts)):
            all_rels.add("/".join(parts[:i]))
    all_rels.update(rel_dirs)
    all_rels.discard(".")
    for prefix in sorted(all_rels):
        result[prefix] = _listdir_sorted(root / prefix, window)
        count += len(result[prefix])
        total += sum(len(item.encode("utf-8")) for item in result[prefix])
        if count > MAX_MEMBERS:
            raise ContractError("membership_bound")
        if total > MAX_CUMULATIVE_PATH_BYTES:
            raise ContractError("membership_bound")
    return result


def _listdir_sorted(path: Path, window) -> list:
    window.check()
    try:
        entries = os.scandir(str(path))
    except OSError:
        raise ContractError("membership_unreadable")
    out = []
    total = 0
    with entries:
        for entry in entries:
            window.check()
            name = entry.name
            try:
                total += len(name.encode("utf-8"))
            except UnicodeError:
                raise ContractError("membership_unreadable")
            out.append(name)
            if len(out) > MAX_MEMBERS or total > MAX_CUMULATIVE_PATH_BYTES:
                raise ContractError("membership_bound")
    out.sort()
    return out


def _alias_capture(root: Path, alias: dict, window) -> dict:
    if not isinstance(alias, dict) or set(alias) != {"path", "target"}:
        raise ContractError("alias_shape")
    rel, target = alias["path"], alias["target"]
    if not isinstance(rel, str) or not rel:
        raise ContractError("alias_shape")
    _validate_relative_path(rel, window)
    if not isinstance(target, str) or not target:
        raise ContractError("alias_target")
    try:
        target.encode("utf-8").decode("utf-8")
    except UnicodeError:
        raise ContractError("alias_target_utf8")
    if target.startswith("/"):
        raise ContractError("alias_target_absolute")
    if "\\" in target or "\x00" in target:
        raise ContractError("alias_target_unsafe")
    link_path = _lstat_chain(root, rel, window)
    try:
        st = os.lstat(str(link_path))
    except FileNotFoundError:
        raise ContractError("alias_missing")
    except OSError:
        raise ContractError("alias_unreadable")
    if not stat.S_ISLNK(st.st_mode):
        raise ContractError("alias_not_symlink")
    abs_link = os.path.abspath(str(link_path))
    resolved = os.path.normpath(os.path.join(os.path.dirname(abs_link), target))
    root_str = os.path.abspath(str(root))
    if not (resolved == root_str or resolved.startswith(root_str + os.sep)):
        raise ContractError("alias_escapes_root")
    try:
        readlink = os.readlink(str(link_path))
    except OSError:
        raise ContractError("alias_readlink")
    if readlink != target:
        raise ContractError("alias_readlink")
    target_rel = os.path.relpath(resolved, root_str).replace(os.sep, "/")
    if target_rel.startswith("..") or target_rel == ".":
        raise ContractError("alias_escapes_root")
    tparts = target_rel.split("/")
    cur = root
    for part in tparts[:-1]:
        cur = cur / part
        try:
            sst = os.lstat(str(cur))
        except OSError:
            raise ContractError("alias_target_ancestor")
        if stat.S_ISLNK(sst.st_mode) or not stat.S_ISDIR(sst.st_mode):
            raise ContractError("alias_target_ancestor")
    tfinal = root / target_rel
    try:
        tst = os.lstat(str(tfinal))
    except OSError:
        raise ContractError("alias_target_not_regular")
    if not stat.S_ISREG(tst.st_mode):
        raise ContractError("alias_target_not_regular")
    return {
        "path": rel,
        "target": target,
        "target_rel": target_rel,
        "signature": _signature(st),
        "target_signature": _signature(tst),
    }


def _sha256_raw(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def prepare_selection(component, root: Path, files: list, empty_dirs: list,
                      window, *, alias=None) -> dict:
    root = _check_root(root, window)
    if not isinstance(files, list) or not isinstance(empty_dirs, list):
        raise ContractError("selection_shape")

    if len(files) < 1:
        raise ContractError("selection_empty")
    if len(files) > MAX_FILES:
        raise ContractError("selection_too_many_files")

    # Refuse duplicates/normalization collisions before reading any entry.
    seen_keys = set()
    rels = []
    for rel in files:
        _validate_relative_path(rel, window)
        key = _identity_key(rel)
        if key in seen_keys:
            raise ContractError("selection_duplicate")
        seen_keys.add(key)
        rels.append(rel)

    dir_rels = []
    dir_seen = set()
    for rel in empty_dirs:
        _validate_relative_path(rel, window)
        key = _identity_key(rel)
        if key in dir_seen or key in seen_keys:
            raise ContractError("selection_duplicate")
        dir_seen.add(key)
        dir_rels.append(rel)

    try:
        name_max, path_max = _pathconf_limits(root)
    except ContractError:
        raise
    except Exception:
        raise ContractError("pathconf_unknown")

    file_entries = []
    dev_ino = set()
    total_bytes = 0
    cumulative = 0
    max_file = 0
    max_comp = 0
    max_full = 0

    for rel in rels:
        window.check()
        abs_path = _lstat_chain(root, rel, window)
        try:
            st = component.admit_regular(str(abs_path))
        except ContractError:
            raise
        except Exception:
            raise ContractError("file_not_admissible")
        try:
            component.admit_mode(str(abs_path))
        except ContractError:
            raise
        except Exception:
            raise ContractError("file_special_bits")
        ident = (st.st_dev, st.st_ino)
        if ident in dev_ino:
            raise ContractError("file_identity_collision")
        dev_ino.add(ident)
        if st.st_size > MAX_FILE_BYTES:
            raise ContractError("file_too_large")
        total_bytes += st.st_size
        if total_bytes > MAX_TOTAL_BYTES:
            raise ContractError("selection_total_bytes")
        full_bytes = len(str(abs_path).encode("utf-8"))
        if full_bytes > MAX_FULL_PATH_BYTES or full_bytes >= path_max:
            raise ContractError("path_too_long")
        for part in rel.split("/"):
            comp_len = len(part.encode("utf-8"))
            if comp_len > name_max:
                raise ContractError("component_name_too_long")
            max_comp = max(max_comp, comp_len)
        max_full = max(max_full, full_bytes)
        cumulative += full_bytes
        if cumulative > MAX_CUMULATIVE_PATH_BYTES:
            raise ContractError("selection_cumulative_paths")
        max_file = max(max_file, st.st_size)
        file_entries.append({
            "rel": rel,
            "signature": _signature(st),
        })

    for rel in dir_rels:
        window.check()
        abs_path = _lstat_chain(root, rel, window)
        try:
            st = os.lstat(str(abs_path))
        except OSError:
            raise ContractError("empty_dir_not_dir")
        if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
            raise ContractError("empty_dir_not_dir")
        if stat.S_IMODE(st.st_mode) & 0o7000:
            raise ContractError("empty_dir_special_bits")
        try:
            if os.listdir(str(abs_path)):
                raise ContractError("empty_dir_not_empty")
        except ContractError:
            raise
        except OSError:
            raise ContractError("empty_dir_unreadable")

    alias_capture = None
    alias_parents = set()
    if alias is not None:
        alias_capture = _alias_capture(root, alias, window)
        aparts = alias_capture["path"].split("/")
        for i in range(1, len(aparts)):
            alias_parents.add("/".join(aparts[:i]))
        selected = {e["rel"] for e in file_entries}
        if alias_capture["target_rel"] not in selected:
            raise ContractError("alias_target_not_selected")

    empty_set = set(dir_rels)
    member_rels = set()
    for entry in file_entries:
        parts = entry["rel"].split("/")
        for i in range(1, len(parts)):
            member_rels.add("/".join(parts[:i]))
    member_rels.update(empty_set)
    for rel in empty_set:
        parts = rel.split("/")
        member_rels.update("/".join(parts[:i]) for i in range(1, len(parts)))
    member_rels.update(alias_parents)
    member_rels.discard(".")
    member_count = len(member_rels) + len(file_entries) + 1
    if member_count > MAX_MEMBERS:
        raise ContractError("selection_too_many_members")

    parent_modes = _parent_modes(root, rels, window)
    for rel in member_rels:
        parent_st = os.lstat(_lstat_chain(root, rel, window))
        if not stat.S_ISDIR(parent_st.st_mode) or parent_st.st_mode & 0o7000:
            raise ContractError("parent_not_dir")
        parent_modes[rel] = parent_st.st_mode & 0o7777
    for rel in empty_set:
        try:
            st = os.lstat(str(root / rel))
        except OSError:
            raise ContractError("empty_dir_not_dir")
        parent_modes[rel] = st.st_mode & 0o7777
    membership = _membership(root, sorted(member_rels), rels, window)

    try:
        root_st = os.lstat(str(root))
    except OSError:
        raise ContractError("root_missing")
    all_paths = set(rels) | member_rels | {"."}
    if alias_capture is not None:
        all_paths.add(alias_capture["path"])
    total_path_bytes = 0
    for rel in all_paths:
        window.check()
        full_size = len(str(root if rel == "." else root / rel).encode("utf-8"))
        if full_size > MAX_FULL_PATH_BYTES or full_size >= path_max:
            raise ContractError("path_too_long")
        total_path_bytes += full_size
        max_full = max(max_full, full_size)
        for part in rel.split("/"):
            length = len(part.encode("utf-8"))
            if length > min(name_max, MAX_COMPONENT_BYTES):
                raise ContractError("component_name_too_long")
            max_comp = max(max_comp, length)
    if total_path_bytes > MAX_CUMULATIVE_PATH_BYTES:
        raise ContractError("selection_cumulative_paths")
    profile = {
        "file_count": len(file_entries),
        "member_count": member_count,
        "total_bytes": total_bytes,
        "size_per_file": max_file,
        "component_bytes": max_comp,
        "full_path_bytes": max_full,
        "cumulative_path_bytes": total_path_bytes,
        "name_max": name_max,
        "path_max": path_max,
    }

    selection = {
        "root": str(root),
        "root_mode": int(root_st.st_mode & 0o7777),
        "root_dev": int(root_st.st_dev),
        "root_ino": int(root_st.st_ino),
        "files": file_entries,
        "empty_dirs": sorted(empty_set),
        "parent_modes": parent_modes,
        "membership": membership,
        "alias": alias_capture,
        "profile": profile,
        "sha256": {},
        "selection_digest": "",
    }
    selection["selection_digest"] = _selection_digest(selection)
    return selection


def _selection_schema_keys() -> set:
    return {
        "root", "root_mode", "root_dev", "root_ino", "files", "empty_dirs",
        "parent_modes", "membership", "alias", "profile", "sha256",
        "selection_digest",
    }


def _canonical_digest_body(selection: dict) -> dict:
    return {key: value for key, value in selection.items() if key != "selection_digest"}


def _selection_digest(selection: dict) -> str:
    body = _canonical_digest_body(selection)
    try:
        data = json.dumps(body, sort_keys=True, separators=(",", ":"),
                          allow_nan=False).encode("utf-8")
    except (TypeError, ValueError):
        raise ContractError("selection_corrupt")
    return hashlib.sha256(data).hexdigest()


def _is_nonneg_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _verify_selection(selection: dict) -> None:
    def require(condition):
        if not condition:
            raise ContractError("selection_corrupt")

    def relative(value, root=False):
        require(isinstance(value, str))
        if root and value == ".":
            return
        require(0 < len(value.encode("utf-8")) <= MAX_FULL_PATH_BYTES)
        require("\\" not in value and not any(ord(c) < 32 or ord(c) == 127 for c in value))
        require(all(part not in ("", ".", "..") and len(part.encode("utf-8")) <= MAX_COMPONENT_BYTES
                    for part in value.split("/")))

    def signature(value, link=False):
        fields = {"dev", "ino", "size", "mtime_ns", "ctime_ns", "mode", "nlink"}
        require(isinstance(value, dict) and set(value) == fields)
        for key in fields:
            require(type(value[key]) is int)
            if key not in {"mtime_ns", "ctime_ns"}:
                require(value[key] >= 0)
        require(0 <= value["mode"] <= 0o777 and value["nlink"] == 1)
        require(link or value["size"] <= MAX_FILE_BYTES)

    try:
        if not isinstance(selection, dict):
            raise ContractError("selection_corrupt")
        if set(selection.keys()) != _selection_schema_keys():
            raise ContractError("selection_corrupt")
        stored = selection["selection_digest"]
        if not (isinstance(stored, str) and len(stored) == 64
                and all(c in _HEX for c in stored)):
            raise ContractError("selection_corrupt")
        if not isinstance(selection["files"], list):
            raise ContractError("selection_corrupt")
        if not isinstance(selection["membership"], dict):
            raise ContractError("selection_corrupt")
        require(isinstance(selection["root"], str) and selection["root"].startswith("/"))
        require(str(Path(selection["root"])) == selection["root"])
        require(len(selection["root"].encode("utf-8")) <= MAX_FULL_PATH_BYTES)
        require(not any(part in (".", "..") for part in selection["root"].split("/")))
        for key in ["root_mode", "root_dev", "root_ino"]:
            require(_is_nonneg_int(selection[key]))
        require(selection["root_mode"] <= 0o777)
        require(1 <= len(selection["files"]) <= MAX_FILES)
        names = set()
        for entry in selection["files"]:
            if not isinstance(entry, dict):
                raise ContractError("selection_corrupt")
            sig = entry["signature"]
            require(set(entry) == {"rel", "signature"})
            relative(entry["rel"])
            require(_identity_key(entry["rel"]) not in names)
            names.add(_identity_key(entry["rel"]))
            signature(sig)
            if not _is_nonneg_int(sig["size"]):
                raise ContractError("selection_corrupt")
            if sig["nlink"] != 1:
                raise ContractError("selection_corrupt")
            mode = sig["mode"]
            if not _is_nonneg_int(mode):
                raise ContractError("selection_corrupt")
            if mode & 0o7000:
                raise ContractError("selection_corrupt")
        require(isinstance(selection["empty_dirs"], list))
        for rel in selection["empty_dirs"]:
            relative(rel)
        require(selection["empty_dirs"] == sorted(set(selection["empty_dirs"])))
        require(isinstance(selection["parent_modes"], dict) and selection["parent_modes"])
        for rel, mode in selection["parent_modes"].items():
            relative(rel, root=True)
            require(type(mode) is int and 0 <= mode <= 0o777)
        require(set(selection["membership"]) == set(selection["parent_modes"]) | {"."})
        for rel, members in selection["membership"].items():
            relative(rel, root=True)
            require(isinstance(members, list) and members == sorted(set(members)))
            observed_name_max = selection["profile"]["name_max"]
            require(type(observed_name_max) is int and observed_name_max > 0)
            for name in members:
                # These names include unselected siblings. They are retained
                # observations, never archive members or traversal inputs.
                require(isinstance(name, str) and name not in ("", ".", ".."))
                require("/" not in name and "\x00" not in name)
                require(len(name.encode("utf-8")) <= observed_name_max)
        profile = selection["profile"]
        require(isinstance(profile, dict) and set(profile) == {
            "file_count", "member_count", "total_bytes", "size_per_file", "component_bytes",
            "full_path_bytes", "cumulative_path_bytes", "name_max", "path_max"})
        require(all(_is_nonneg_int(value) for value in profile.values()))
        require(profile["file_count"] == len(selection["files"]))
        require(profile["member_count"] == len(selection["files"]) + len(set(selection["parent_modes"]) | {"."}))
        require(profile["member_count"] <= MAX_MEMBERS)
        require(profile["total_bytes"] == sum(row["signature"]["size"] for row in selection["files"]))
        require(profile["total_bytes"] <= MAX_TOTAL_BYTES)
        require(profile["size_per_file"] == max(row["signature"]["size"] for row in selection["files"]))
        for key, bound in [("component_bytes", MAX_COMPONENT_BYTES), ("full_path_bytes", MAX_FULL_PATH_BYTES),
                           ("cumulative_path_bytes", MAX_CUMULATIVE_PATH_BYTES)]:
            require(profile[key] <= bound)
        require(profile["name_max"] > 0 and profile["path_max"] > 0)
        require(isinstance(selection["sha256"], dict))
        selected = {row["rel"] for row in selection["files"]}
        require(not selection["sha256"] or set(selection["sha256"]) == selected)
        alias = selection["alias"]
        if alias is not None:
            require(isinstance(alias, dict) and set(alias) == {
                "path", "target", "target_rel", "signature", "target_signature"})
            relative(alias["path"])
            relative(alias["target_rel"])
            require(alias["target_rel"] in selected and alias["path"] not in selected)
            require(isinstance(alias["target"], str) and alias["target"] and not alias["target"].startswith("/"))
            signature(alias["signature"], link=True)
            signature(alias["target_signature"])
        for rel, digest in selection.get("sha256", {}).items():
            if not (isinstance(digest, str) and len(digest) == 64
                    and all(c in _HEX for c in digest)):
                raise ContractError("selection_corrupt")
    except ContractError:
        raise
    except (KeyError, TypeError, AttributeError, ValueError, UnicodeError):
        raise ContractError("selection_corrupt")
    expected = _selection_digest(selection)
    if stored != expected:
        raise ContractError("selection_corrupt")


def _safe_lstat_path_chain(parent: Path, tag: str) -> None:
    if not parent.is_absolute():
        return
    cur = Path(parent.parts[0])
    try:
        st = os.lstat(str(cur))
    except OSError:
        raise ContractError(tag)
    if stat.S_ISLNK(st.st_mode):
        raise ContractError(tag)
    for part in parent.parts[1:]:
        cur = cur / part
        try:
            st = os.lstat(str(cur))
        except FileNotFoundError:
            raise ContractError(tag)
        except OSError:
            raise ContractError(tag)
        if stat.S_ISLNK(st.st_mode):
            raise ContractError(tag)


def validate_destination_profile(selection: dict, existing_target_parent: Path,
                                 proposed_relative_prefix: str, window) -> dict:
    _verify_selection(selection)
    window.check()
    try:
        parent = Path(existing_target_parent)
    except TypeError:
        raise ContractError("destination_parent_absolute")
    if not parent.is_absolute():
        raise ContractError("destination_parent_absolute")
    _safe_lstat_path_chain(parent, "destination_symlink_ancestor")
    try:
        pst = os.lstat(str(parent))
    except OSError:
        raise ContractError("destination_parent_not_dir")
    if not stat.S_ISDIR(pst.st_mode):
        raise ContractError("destination_parent_not_dir")

    if not isinstance(proposed_relative_prefix, str) or not proposed_relative_prefix:
        raise ContractError("prefix_empty")
    prel = _validate_relative_path(proposed_relative_prefix, window)
    if not prel:
        raise ContractError("prefix_empty")
    pbytes = prel.encode("utf-8")
    full_prefix = str(parent) + "/" + prel
    name_max, path_max = _pathconf_limits(parent)
    for part in prel.split("/"):
        if not part or part in (".", ".."):
            raise ContractError("prefix_traversal")
        if len(part.encode("utf-8")) > name_max:
            raise ContractError("prefix_component_too_long")
    if len(pbytes) > MAX_FULL_PATH_BYTES or len(full_prefix.encode("utf-8")) >= path_max:
        raise ContractError("prefix_too_long")

    active_prefix = parent
    for part in prel.split("/"):
        active_prefix = active_prefix / part
        if os.path.lexists(str(active_prefix)):
            raise ContractError("prefix_occupied")
    source_key = _identity_key(selection["root"]).rstrip("/")
    target_key = _identity_key(str(active_prefix)).rstrip("/")
    if source_key == target_key or source_key.startswith(target_key + "/") or target_key.startswith(source_key + "/"):
        raise ContractError("destination_source_overlap")
    members = {row["rel"] for row in selection["files"]} | set(selection["parent_modes"])
    if selection["alias"] is not None:
        members.add(selection["alias"]["path"])
    cumulative = 0
    full_max = 0
    for rel in members:
        window.check()
        full = full_prefix if rel == "." else full_prefix + "/" + rel
        full_size = len(full.encode("utf-8"))
        if full_size > MAX_FULL_PATH_BYTES or full_size >= path_max:
            raise ContractError("destination_member_path")
        if any(len(part.encode("utf-8")) > min(name_max, MAX_COMPONENT_BYTES) for part in full.split("/")):
            raise ContractError("destination_member_component")
        cumulative += full_size
        full_max = max(full_max, full_size)
        if cumulative > MAX_CUMULATIVE_PATH_BYTES:
            raise ContractError("destination_cumulative_paths")

    try:
        st_vfs = os.statvfs(str(parent))
    except OSError:
        raise ContractError("statvfs_unavailable")
    avail = st_vfs.f_bavail * st_vfs.f_frsize
    selected_bytes = selection["profile"]["total_bytes"]
    required = 8 * selected_bytes + 1024 * 1024 * 1024
    if avail < required:
        raise ContractError("capacity_insufficient")
    return {
        "existing_target_parent": str(parent),
        "prefix": prel,
        "prefix_bytes": len(pbytes),
        "full_prefix": full_prefix,
        "maximum_member_path_bytes": full_max,
        "cumulative_member_path_bytes": cumulative,
        "dev": pst.st_dev,
        "available_bytes": avail,
        "required_bytes": required,
        "name_max": name_max,
        "path_max": path_max,
    }


# ---------------------------------------------------------------------------
# Hash binding
# ---------------------------------------------------------------------------


def _scan_sha256(path: Path, window) -> str:
    window.check()
    h = hashlib.sha256()
    try:
        fd = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW)
    except OSError:
        raise ContractError("scan_open")
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise ContractError("scan_not_regular")
        if before.st_size > MAX_FILE_BYTES:
            raise ContractError("file_too_large")
        total = 0
        while True:
            window.check()
            try:
                chunk = os.read(fd, 1024 * 1024)
            except OSError:
                raise ContractError("scan_open")
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_FILE_BYTES:
                raise ContractError("file_too_large")
            h.update(chunk)
        if total != before.st_size or _signature(before) != _signature(os.fstat(fd)) or _signature(before) != _signature(os.lstat(path)):
            raise ContractError("scan_identity_changed")
    finally:
        os.close(fd)
    return h.hexdigest()


def _expected_abs_paths(selection: dict) -> list:
    root = Path(selection["root"])
    return [str(root / entry["rel"]) for entry in selection["files"]]


def _parse_bound_digests(component, selection: dict, source_ledger: str,
                         window) -> dict:
    abs_paths = _expected_abs_paths(selection)
    window.check()
    try:
        parsed = component.parse_ledger(source_ledger, abs_paths)
    except ContractError:
        raise
    except Exception:
        raise ContractError("ledger_invalid")
    if not isinstance(parsed, dict) or len(parsed) != len(selection["files"]):
        raise ContractError("ledger_count")
    digests = {}
    for entry in selection["files"]:
        window.check()
        key = os.fsencode(str(Path(selection["root"]) / entry["rel"]))
        digest = parsed.get(key)
        if not isinstance(digest, str) or len(digest) != 64 or any(
                c not in _HEX for c in digest):
            raise ContractError("ledger_invalid")
        digests[entry["rel"]] = digest
    return digests


def _remeasure(selection: dict, window) -> None:
    root = Path(selection["root"])
    try:
        root_st = os.lstat(str(root))
    except OSError:
        raise ContractError("source_root_changed")
    if not stat.S_ISDIR(root_st.st_mode):
        raise ContractError("source_root_changed")
    if (root_st.st_dev, root_st.st_ino) != (selection["root_dev"], selection["root_ino"]):
        raise ContractError("source_root_changed")
    if (root_st.st_mode & 0o7777) != selection["root_mode"]:
        raise ContractError("source_root_changed")

    dev_ino = set()
    for entry in selection["files"]:
        window.check()
        abs_path = _lstat_chain(root, entry["rel"], window)
        try:
            st = os.lstat(str(abs_path))
        except OSError:
            raise ContractError("source_drift")
        if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
            raise ContractError("source_drift")
        if _signature(st) != entry["signature"]:
            raise ContractError("source_drift")
        ident = (st.st_dev, st.st_ino)
        if ident in dev_ino:
            raise ContractError("source_identity_collision")
        dev_ino.add(ident)

    for rel, expected_mode in selection["parent_modes"].items():
        window.check()
        p = root if rel == "." else root / rel
        try:
            st = os.lstat(str(p))
        except OSError:
            raise ContractError("source_drift")
        if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
            raise ContractError("source_drift")
        if (st.st_mode & 0o7777) != expected_mode:
            raise ContractError("source_drift")

    # Root membership is part of the selection and must be re-derived here too.
    for rel, entries in selection["membership"].items():
        window.check()
        p = root if rel == "." else root / rel
        try:
            current = _listdir_sorted(p, window)
        except OSError:
            raise ContractError("source_drift")
        if current != list(entries):
            raise ContractError("source_drift")

    alias = selection.get("alias")
    if alias is not None:
        link_path = root / alias["path"]
        try:
            st = os.lstat(str(link_path))
        except OSError:
            raise ContractError("alias_drift")
        if not stat.S_ISLNK(st.st_mode):
            raise ContractError("alias_drift")
        if _signature(st) != alias["signature"]:
            raise ContractError("alias_drift")
        try:
            rl = os.readlink(str(link_path))
        except OSError:
            raise ContractError("alias_drift")
        if rl != alias["target"]:
            raise ContractError("alias_drift")
        tgt = root / alias["target_rel"]
        try:
            tst = os.lstat(str(tgt))
        except OSError:
            raise ContractError("alias_drift")
        if not stat.S_ISREG(tst.st_mode) or _signature(tst) != alias["target_signature"]:
            raise ContractError("alias_drift")


def bind_hashes(component, selection: dict, source_ledger: str, window) -> dict:
    _verify_selection(selection)
    digests = _parse_bound_digests(component, selection, source_ledger, window)
    _remeasure(selection, window)
    new_selection = json.loads(json.dumps(selection, allow_nan=False))
    new_selection["sha256"] = digests
    new_selection["selection_digest"] = _selection_digest(new_selection)
    return new_selection


def check_source_unchanged(component, selection: dict, source_ledger: str, window) -> dict:
    _verify_selection(selection)
    if not selection.get("sha256"):
        raise ContractError("selection_unbound")
    digests = _parse_bound_digests(component, selection, source_ledger, window)
    if digests != selection["sha256"]:
        raise ContractError("source_digest_drift")
    _remeasure(selection, window)
    return {"selection_digest": selection["selection_digest"], "unchanged": True}


# ---------------------------------------------------------------------------
# Archive inspection
# ---------------------------------------------------------------------------


_TAR_BLOCK = 512


def _tar_number(raw: bytes) -> int:
    if raw and raw[0] & 0x80:
        n = int.from_bytes(raw, "big")
        return n & ((1 << (len(raw) * 8 - 1)) - 1)
    text = raw.split(b"\x00", 1)[0].strip()
    if not text:
        return 0
    try:
        return int(text, 8)
    except ValueError:
        raise ContractError("archive_bad_header")


def _tar_checksum(header: bytes) -> int:
    return sum(header[:148]) + 8 * 32 + sum(header[156:])


def _parse_pax(payload: bytes) -> dict:
    result = {}
    pos = 0
    total = len(payload)
    while pos < total:
        sp = payload.find(b" ", pos)
        if sp < 0:
            raise ContractError("archive_bad_pax")
        try:
            length = int(payload[pos:sp])
        except ValueError:
            raise ContractError("archive_bad_pax")
        if length <= 0 or pos + length > total:
            raise ContractError("archive_bad_pax")
        record = payload[sp + 1: pos + length]
        if not record.endswith(b"\n"):
            raise ContractError("archive_bad_pax")
        record = record[:-1]
        eq = record.find(b"=")
        if eq < 0:
            raise ContractError("archive_bad_pax")
        key_raw = record[:eq]
        value = record[eq + 1:]
        try:
            key = key_raw.decode("utf-8")
        except UnicodeError:
            raise ContractError("archive_bad_pax")
        if key in result:
            raise ContractError("archive_duplicate_pax")
        result[key] = value
        pos += length
    return result


def _safe_member_name(raw: bytes) -> str:
    try:
        name = raw.decode("utf-8")
    except UnicodeError:
        raise ContractError("archive_member_invalid_utf8")
    if "\\" in name:
        raise ContractError("archive_member_backslash")
    for ch in name:
        code = ord(ch)
        if code < 0x20 or code == 0x7F:
            raise ContractError("archive_member_control")
    if name.startswith("/"):
        raise ContractError("archive_member_absolute")
    return name


def _iter_tar(fd: int, limit: int, window):
    position = 0
    long_name = None
    pax = {}
    while True:
        window.check()
        try:
            header = os.pread(fd, _TAR_BLOCK, position)
        except OSError:
            raise ContractError("archive_io")
        if len(header) < _TAR_BLOCK:
            raise ContractError("archive_truncated")
        position += _TAR_BLOCK
        if header == b"\x00" * _TAR_BLOCK:
            break
        stored = _tar_number(header[148:156])
        if _tar_checksum(header) != stored:
            raise ContractError("archive_bad_header")
        typeflag = header[156:157]
        try:
            size = _tar_number(header[124:136])
            mode = _tar_number(header[100:108]) & 0o7777
            mtimesec = _tar_number(header[136:148])
        except ContractError:
            raise
        if size < 0:
            raise ContractError("archive_bad_header")
        name_field = header[0:100]
        prefix = header[345:500].split(b"\x00", 1)[0]
        if prefix:
            name_raw = prefix + b"/" + name_field.split(b"\x00", 1)[0]
        else:
            name_raw = name_field.split(b"\x00", 1)[0]
        if typeflag in (b"L", b"K", b"x", b"g"):
            if size > MAX_METADATA_READ:
                raise ContractError("archive_meta_too_large")
            try:
                data = os.pread(fd, size, position)
            except OSError:
                raise ContractError("archive_io")
            if len(data) < size:
                raise ContractError("archive_truncated")
            position += size + ((_TAR_BLOCK - size % _TAR_BLOCK) % _TAR_BLOCK)
            if typeflag == b"g":
                raise ContractError("archive_global_pax")
            if typeflag == b"L":
                raise ContractError("archive_gnu_long_name")
            elif typeflag == b"K":
                raise ContractError("archive_gnu_link_name")
            elif typeflag == b"x":
                extra = _parse_pax(data)
                for key in extra:
                    if key in pax:
                        raise ContractError("archive_duplicate_pax")
                pax.update(extra)
            continue
        if "path" in pax:
            name_raw = pax["path"]
        if set(pax) - {"path", "mtime", "atime", "ctime", "uid", "gid", "uname", "gname", "size"}:
            raise ContractError("archive_pax_unsupported")
        if "size" in pax and pax["size"] != str(size).encode("ascii"):
            raise ContractError("archive_pax_size")
        if header[257:265] != b"ustar\x0000":
            raise ContractError("archive_format")
        padded_end = position + size + (-size % _TAR_BLOCK)
        if padded_end > limit:
            raise ContractError("archive_truncated")
        if typeflag == b"5" and size != 0:
            raise ContractError("archive_dir_payload")
        if long_name is not None:
            name_raw = long_name
        yield {
            "name": name_raw,
            "typeflag": typeflag,
            "size": size,
            "mode": mode,
            "mtimesec": mtimesec,
            "pax": dict(pax),
            "data_offset": position,
        }
        position += size + ((_TAR_BLOCK - size % _TAR_BLOCK) % _TAR_BLOCK)
        long_name = None
        pax = {}
    if pax or long_name is not None or position + _TAR_BLOCK > limit:
        raise ContractError("archive_truncated")
    if os.pread(fd, _TAR_BLOCK, position) != b"\x00" * _TAR_BLOCK:
        raise ContractError("archive_trailing_data")
    while True:
        window.check()
        try:
            chunk = os.pread(fd, _TAR_BLOCK, position)
        except OSError:
            raise ContractError("archive_io")
        if not chunk:
            return
        if chunk.strip(b"\x00"):
            raise ContractError("archive_trailing_data")
        position += len(chunk)


def _hash_raw(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _pread_into_sha(fd, offset, size, window, h):
    remaining = size
    pos = offset
    while remaining > 0:
        window.check()
        want = min(remaining, 1024 * 1024)
        try:
            data = os.pread(fd, want, pos)
        except OSError:
            raise ContractError("archive_io")
        if not data:
            raise ContractError("archive_truncated")
        h.update(data)
        pos += len(data)
        remaining -= len(data)


def _member_mtime_ns(member: dict) -> int:
    pax = member["pax"]
    if "mtime" in pax:
        raw = pax["mtime"]
        try:
            text = raw.decode("ascii")
        except UnicodeError:
            raise ContractError("archive_bad_pax_mtime")
        if "." in text:
            whole, frac = text.split(".", 1)
            if len(frac) > 9 and any(ch != "0" for ch in frac[9:]):
                raise ContractError("archive_mtime_precision")
            frac = (frac + "000000000")[:9]
            if not whole.lstrip("-").isdigit() or not frac.isdigit():
                raise ContractError("archive_bad_pax_mtime")
            sign = -1 if whole.startswith("-") else 1
            return sign * (abs(int(whole)) * 1_000_000_000 + int(frac))
        if not text.lstrip("-").isdigit():
            raise ContractError("archive_bad_pax_mtime")
        return int(text) * 1_000_000_000
    return int(member.get("mtimesec", 0)) * 1_000_000_000


def inspect_archive(component, archive: Path, selection: dict,
                    expected_archive_sha256: str, window) -> dict:
    _verify_selection(selection)
    if not selection.get("sha256"):
        raise ContractError("selection_unbound")
    if not (isinstance(expected_archive_sha256, str)
            and len(expected_archive_sha256) == 64
            and all(c in _HEX for c in expected_archive_sha256)):
        raise ContractError("archive_digest_argument")
    window.check()
    try:
        archive = Path(archive)
    except TypeError:
        raise ContractError("archive_missing")
    try:
        pre = os.lstat(str(archive))
    except FileNotFoundError:
        raise ContractError("archive_missing")
    except OSError:
        raise ContractError("archive_missing")
    if stat.S_ISLNK(pre.st_mode):
        raise ContractError("archive_symlink")
    if not stat.S_ISREG(pre.st_mode):
        raise ContractError("archive_not_regular")
    if pre.st_nlink != 1:
        raise ContractError("archive_hardlink")
    if pre.st_size > MAX_ARCHIVE_BYTES:
        raise ContractError("archive_too_large")

    try:
        fd = os.open(str(archive), os.O_RDONLY | os.O_NOFOLLOW)
    except OSError:
        raise ContractError("archive_open")
    try:
        fst = os.fstat(fd)
        if (fst.st_dev, fst.st_ino) != (pre.st_dev, pre.st_ino):
            raise ContractError("archive_identity_changed")
        limit = fst.st_size
        h = hashlib.sha256()
        pos = 0
        while pos < limit:
            window.check()
            try:
                chunk = os.pread(fd, min(1024 * 1024, limit - pos), pos)
            except OSError:
                raise ContractError("archive_io")
            if not chunk:
                break
            h.update(chunk)
            pos += len(chunk)
        if pos != limit:
            raise ContractError("archive_identity_changed")
        actual_digest = h.hexdigest()
        if actual_digest != expected_archive_sha256:
            raise ContractError("archive_digest_mismatch")

        expected_files = {entry["rel"]: entry for entry in selection["files"]}
        expected_dirs = {".": selection["root_mode"]}
        for rel in selection["empty_dirs"]:
            expected_dirs[rel] = selection["parent_modes"][rel]
        for rel, m in selection["parent_modes"].items():
            if rel == ".":
                continue
            expected_dirs.setdefault(rel, m)
        alias = selection.get("alias")
        if alias is not None:
            parts = alias["path"].split("/")
            for i in range(1, len(parts)):
                p = "/".join(parts[:i])
                if p not in expected_dirs:
                    raise ContractError("selection_corrupt")

        seen = set()
        keys = set()
        observed = {"files": {}, "dirs": {}}
        for member in _iter_tar(fd, limit, window):
            try:
                name = _safe_member_name(member["name"])
            except ContractError:
                raise
            norm = name[2:] if name.startswith("./") else name
            if member["typeflag"] == b"5" and norm.endswith("/"):
                norm = norm[:-1]
            if norm not in ("", ".") and any(part in ("", ".", "..") for part in norm.split("/")):
                raise ContractError("archive_member_path")
            if norm in ("", "."):
                norm = "."
            typeflag = member["typeflag"]
            if typeflag not in (b"0", b"\x00", b"5"):
                raise ContractError("archive_member_type")
            pax = member["pax"]
            for bad_key in ("GNU.sparse.name", "GNU.sparse.major",
                            "GNU.sparse.minor", "GNU.sparse.realsize"):
                if bad_key in pax:
                    raise ContractError("archive_member_sparse")
            if "linkpath" in pax or "SCHILY.realsize" in pax:
                raise ContractError("archive_member_type")
            key = _identity_key(norm)
            if key in keys:
                raise ContractError("archive_member_duplicate")
            keys.add(key)
            if norm in seen:
                raise ContractError("archive_member_duplicate")
            seen.add(norm)
            if typeflag == b"5":
                if norm not in expected_dirs:
                    raise ContractError("archive_member_unexpected")
                if member["mode"] != expected_dirs[norm]:
                    raise ContractError("archive_dir_mode")
                if member["size"] != 0:
                    raise ContractError("archive_bad_header")
                observed["dirs"][norm] = member["mode"]
            else:
                if norm not in expected_files:
                    raise ContractError("archive_member_unexpected")
                entry = expected_files[norm]
                if member["size"] != entry["signature"]["size"]:
                    raise ContractError("archive_file_size")
                if member["mode"] != entry["signature"]["mode"]:
                    raise ContractError("archive_file_mode")
                mtime = _member_mtime_ns(member)
                if mtime != entry["signature"]["mtime_ns"]:
                    raise ContractError("archive_file_mtime")
                fh = hashlib.sha256()
                _pread_into_sha(fd, member["data_offset"], member["size"], window, fh)
                if fh.hexdigest() != selection["sha256"][norm]:
                    raise ContractError("archive_file_digest")
                observed["files"][norm] = {
                    "sha256": fh.hexdigest(),
                    "size": member["size"],
                    "mode": member["mode"],
                    "mtime_ns": mtime,
                }

        if seen != set(expected_files) | set(expected_dirs):
            raise ContractError("archive_member_missing")
        if observed["dirs"].get(".") != selection["root_mode"]:
            raise ContractError("archive_root_mode")

        try:
            post_named = os.lstat(str(archive))
        except OSError:
            raise ContractError("archive_identity_changed")
        post_fd = os.fstat(fd)
        if _signature(post_named) != _signature(pre):
            raise ContractError("archive_identity_changed")
        if _signature(post_fd) != _signature(pre):
            raise ContractError("archive_identity_changed")

        return {
            "archive_digest": actual_digest,
            "selection_digest": selection["selection_digest"],
            "archive_size": pre.st_size,
            "archive_dev": pre.st_dev,
            "archive_ino": pre.st_ino,
            "members": observed,
            "member_count": len(seen),
            "alias_declared": alias is not None,
        }
    finally:
        os.close(fd)


# ---------------------------------------------------------------------------
# Restore comparison
# ---------------------------------------------------------------------------


def _enumerate_tree(root: Path, window):
    files = []
    dirs = set()
    stack = [("", root)]
    while stack:
        window.check()
        rel, path = stack.pop()
        try:
            entries = _listdir_sorted(path, window)
        except OSError:
            raise ContractError("restore_unreadable")
        for name in entries:
            window.check()
            child_rel = name if rel == "" else rel + "/" + name
            child = path / name
            try:
                st = os.lstat(str(child))
            except OSError:
                raise ContractError("restore_unreadable")
            if stat.S_ISLNK(st.st_mode):
                raise ContractError("restore_link")
            if stat.S_ISDIR(st.st_mode):
                if stat.S_IMODE(st.st_mode) & 0o7000:
                    raise ContractError("restore_special_bits")
                dirs.add(child_rel)
                stack.append((child_rel, child))
            elif stat.S_ISREG(st.st_mode):
                files.append((child_rel, child, st))
            else:
                raise ContractError("restore_special")
            if len(files) + len(dirs) + 1 > MAX_MEMBERS:
                raise ContractError("restore_member_bound")
    return files, dirs


def compare_restore(component, selection: dict, restored_root: Path,
                    restored_ledger: str, window) -> dict:
    _verify_selection(selection)
    if not selection.get("sha256"):
        raise ContractError("selection_unbound")
    window.check()
    try:
        restored_root = Path(restored_root)
    except TypeError:
        raise ContractError("restore_not_dir")
    if not restored_root.is_absolute():
        raise ContractError("restore_not_dir")
    _safe_lstat_path_chain(restored_root, "restore_symlink_ancestor")
    try:
        rst = os.lstat(str(restored_root))
    except OSError:
        raise ContractError("restore_not_dir")
    if not stat.S_ISDIR(rst.st_mode):
        raise ContractError("restore_not_dir")
    if stat.S_IMODE(rst.st_mode) & 0o7000:
        raise ContractError("restore_special_bits")

    files, dirs = _enumerate_tree(restored_root, window)

    expected_files = {entry["rel"]: entry for entry in selection["files"]}
    expected_dirs = set(selection["parent_modes"]) - {"."}
    for rel in expected_files:
        parts = rel.split("/")
        for i in range(1, len(parts)):
            expected_dirs.add("/".join(parts[:i]))
    if set(expected_files) != {rel for rel, _, _ in files}:
        raise ContractError("restore_member_set")
    if dirs != expected_dirs:
        raise ContractError("restore_member_set")

    uid = os.getuid()
    if rst.st_uid != uid:
        raise ContractError("restore_owner")
    source_inodes = {(row["signature"]["dev"], row["signature"]["ino"]) for row in selection["files"]}
    output_inodes = set()
    abs_out = [str(path) for _, path, _ in files]
    window.check()
    try:
        parsed = component.parse_ledger(restored_ledger, abs_out)
    except ContractError:
        raise
    except Exception:
        raise ContractError("ledger_invalid")

    for rel, path, st in files:
        window.check()
        entry = expected_files[rel]
        if st.st_size != entry["signature"]["size"]:
            raise ContractError("restore_size")
        if (st.st_mode & 0o7777) != entry["signature"]["mode"]:
            raise ContractError("restore_mode")
        if st.st_mtime_ns != entry["signature"]["mtime_ns"]:
            raise ContractError("restore_mtime")
        if st.st_uid != uid:
            raise ContractError("restore_owner")
        identity = (st.st_dev, st.st_ino)
        if st.st_nlink != 1 or identity in source_inodes or identity in output_inodes:
            raise ContractError("restore_inode_alias")
        output_inodes.add(identity)
        key = os.fsencode(str(path))
        if parsed.get(key) != selection["sha256"][rel]:
            raise ContractError("restore_digest")

    if (rst.st_mode & 0o7777) != selection["root_mode"]:
        raise ContractError("restore_root_mode")
    for rel, expected_mode in selection["parent_modes"].items():
        window.check()
        if rel == ".":
            continue
        if rel not in expected_dirs and rel not in selection["empty_dirs"]:
            continue
        try:
            st = os.lstat(str(restored_root / rel))
        except OSError:
            raise ContractError("restore_member_set")
        if (st.st_mode & 0o7777) != expected_mode:
            raise ContractError("restore_dir_mode")
        if st.st_uid != uid:
            raise ContractError("restore_owner")

    return {
        "selection_digest": selection["selection_digest"],
        "restored_root": str(restored_root),
        "file_count": len(files),
        "dir_count": len(dirs),
        "verified": True,
    }


# ---------------------------------------------------------------------------
# Declared alias reconstruction
# ---------------------------------------------------------------------------


def alias_control(selection: dict) -> bytes:
    _verify_selection(selection)
    alias = selection.get("alias")
    if alias is None:
        raise ContractError("alias_undeclared")
    if not selection.get("sha256"):
        raise ContractError("selection_unbound")
    target_rel = alias["target_rel"]
    target_digest = selection["sha256"].get(target_rel)
    if target_digest is None:
        raise ContractError("alias_target_unbound")
    body = {
        "version": 1,
        "selection_digest": selection["selection_digest"],
        "path": alias["path"],
        "target": alias["target"],
        "target_rel": target_rel,
        "target_sha256": target_digest,
        "link_signature": alias["signature"],
    }
    try:
        return json.dumps(body, sort_keys=True, separators=(",", ":"),
                          allow_nan=False).encode("utf-8")
    except (TypeError, ValueError):
        raise ContractError("control_malformed")


def restore_declared_alias(component, selection: dict, inactive_root: Path,
                           control_bytes: bytes, trusted_control_sha256: str,
                           window) -> dict:
    _verify_selection(selection)
    if not selection.get("sha256"):
        raise ContractError("selection_unbound")
    if not (isinstance(trusted_control_sha256, str)
            and len(trusted_control_sha256) == 64
            and all(c in _HEX for c in trusted_control_sha256)):
        raise ContractError("control_digest_argument")
    if not isinstance(control_bytes, (bytes, bytearray)):
        raise ContractError("control_bytes")
    if len(control_bytes) > 16384:
        raise ContractError("control_bytes")
    try:
        actual_control_digest = _hash_raw(bytes(control_bytes))
    except Exception:
        raise ContractError("control_bytes")
    if actual_control_digest != trusted_control_sha256:
        raise ContractError("control_digest_mismatch")
    if bytes(control_bytes) != alias_control(selection):
        raise ContractError("control_declaration")
    try:
        body = json.loads(bytes(control_bytes).decode("utf-8"))
    except Exception:
        raise ContractError("control_malformed")
    if not isinstance(body, dict):
        raise ContractError("control_malformed")
    alias = selection.get("alias")
    if alias is None:
        raise ContractError("alias_undeclared")
    if body.get("selection_digest") != selection["selection_digest"]:
        raise ContractError("control_stale")
    if body.get("path") != alias["path"] or body.get("target") != alias["target"]:
        raise ContractError("control_declaration")
    if body.get("target_rel") != alias["target_rel"]:
        raise ContractError("control_declaration")
    if body.get("link_signature") != alias["signature"]:
        raise ContractError("control_declaration")
    target_sha = body.get("target_sha256")
    if target_sha != selection["sha256"].get(alias["target_rel"]):
        raise ContractError("control_target_digest")

    window.check()
    try:
        inactive_root = Path(inactive_root)
    except TypeError:
        raise ContractError("inactive_not_dir")
    if not inactive_root.is_absolute():
        raise ContractError("inactive_not_dir")
    _safe_lstat_path_chain(inactive_root, "inactive_symlink_ancestor")
    try:
        root_st = os.lstat(str(inactive_root))
    except OSError:
        raise ContractError("inactive_not_dir")
    if not stat.S_ISDIR(root_st.st_mode):
        raise ContractError("inactive_not_dir")

    link_rel = alias["path"]
    link_parts = link_rel.split("/")
    parent = inactive_root
    for part in link_parts[:-1]:
        parent = parent / part
        try:
            st = os.lstat(str(parent))
        except OSError:
            raise ContractError("alias_parent_missing")
        if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
            raise ContractError("alias_parent_missing")
    link_path = parent / link_parts[-1]
    if os.path.lexists(str(link_path)):
        raise ContractError("alias_path_occupied")
    if stat.S_IMODE(root_st.st_mode) != selection["root_mode"] or root_st.st_uid != os.getuid():
        raise ContractError("alias_root_metadata")

    target_rel = alias["target_rel"]
    target_path = inactive_root
    tparts = target_rel.split("/")
    for part in tparts[:-1]:
        target_path = target_path / part
        try:
            st = os.lstat(str(target_path))
        except OSError:
            raise ContractError("alias_target_ancestor")
        if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
            raise ContractError("alias_target_ancestor")
    target_path = target_path / tparts[-1]
    try:
        tst = os.lstat(str(target_path))
    except OSError:
        raise ContractError("alias_target_not_regular")
    if not stat.S_ISREG(tst.st_mode):
        raise ContractError("alias_target_not_regular")
    if tst.st_nlink != 1:
        raise ContractError("alias_target_hardlink")
    expected_metadata = alias["target_signature"]
    if any(actual != expected_metadata[key] for key, actual in {
            "size": tst.st_size, "mode": stat.S_IMODE(tst.st_mode),
            "mtime_ns": tst.st_mtime_ns, "nlink": tst.st_nlink}.items()) or tst.st_uid != os.getuid():
        raise ContractError("alias_target_metadata")

    resolved = os.path.normpath(os.path.join(str(parent), alias["target"]))
    root_str = os.path.abspath(str(inactive_root))
    if not (resolved == root_str or resolved.startswith(root_str + os.sep)):
        raise ContractError("alias_escapes_root")
    if os.path.relpath(resolved, str(parent)).replace(os.sep, "/") != alias["target"]:
        raise ContractError("alias_declaration")

    actual_target_digest = _scan_sha256(target_path, window)
    if actual_target_digest != selection["sha256"][target_rel]:
        raise ContractError("alias_target_digest")

    try:
        os.symlink(alias["target"], str(link_path))
    except OSError:
        raise ContractError("alias_create_failed")

    window.check()
    try:
        lst = os.lstat(str(link_path))
    except OSError:
        raise ContractError("alias_post_lstat")
    if not stat.S_ISLNK(lst.st_mode):
        raise ContractError("alias_post_lstat")
    try:
        rl = os.readlink(str(link_path))
    except OSError:
        raise ContractError("alias_post_readlink")
    if rl != alias["target"]:
        raise ContractError("alias_post_readlink")
    post_target = _scan_sha256(target_path, window)
    if post_target != selection["sha256"][target_rel]:
        raise ContractError("alias_post_target_digest")
    post_stat = os.lstat(target_path)
    if _signature(post_stat) != _signature(tst) or (os.lstat(inactive_root).st_dev, os.lstat(inactive_root).st_ino) != (root_st.st_dev, root_st.st_ino):
        raise ContractError("alias_post_metadata")

    return {
        "path": alias["path"],
        "target": alias["target"],
        "restored": True,
        "control_digest": actual_control_digest,
    }
