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


# ---------------------------------------------------------------------------
# Bounded one-run operator component loading (P2 fixed commit + manifests)
# ---------------------------------------------------------------------------

OPERATOR_COMMIT = "944f560ea07313923600cdf5caba5b9b3c1f4bd7"

OPERATOR_MANIFEST = {
    "plans/2026-10-27_exact-name-recovery-adapter.md": "a2342c9ee3dd6cb2a40090d9cee3e724138ed8866c27e7947336b5eea1c1cebd",
    "plans/2026-10-27_fixed-recovery-transport.md": "58e080c29bb26ccbe17c82260aedf840961b75e66532deea080d01b1181234d8",
    "plans/2026-10-27_recovery-operator-control-proposal.md": "d796cbfce83a1ffea92b687a371ee05521a364b1f751df548ceec10ebff05067",
    "scripts/lib/recovery_exact_archive.py": "8751b973e05a81d52aaa8994f1fd7330e4204327f1c257638ea3a8501f0d0fe0",
    "scripts/lib/recovery_exact_extract.py": "0a5e1ebc4095423104713b9135b2986fa2dc268cb0996e51d86f1cc6b5d3a975",
    "scripts/lib/recovery_foreground_crypto.py": "ea3285f8cbf81717706d1da1814ae48eadd276c69f31d79e5258cd3bde784a77",
    "scripts/lib/recovery_nas_remote.py": "514673d8e8baf6ecf7681805a2ffbf1883c16b64fe7db6c0fe3ed38a45e28a50",
    "scripts/lib/recovery_nas_transport.py": "6bad7c5ec23afd3d8af7cb5e67f74580211c1c23137a273e31f57f9d60162fbb",
    "scripts/lib/recovery_resource_holder.py": "a11e8ee65d5ad755760a834520626688ccc7f6281154d04ec473934fb4917fc2",
    "tests/test_recovery_exact_archive.py": "881cdb88befe99cc3b296fc75e7a584faf2f02f97e5517298bc781ce2d0ec9e4",
    "tests/test_recovery_exact_extract.py": "156d246421cce4dc37ce0dc0b88db2e4521961dfbd6d34dde802917ac4e3d1ab",
    "tests/test_recovery_foreground_crypto.py": "e57d6f7c541b842680b3fc6d87adf24ecfbc72407f5b560a2ec8d017c0ba0cda",
    "tests/test_recovery_nas_remote.py": "ba389d45860310a67ea31b26aa39cdbe13cf6fd650793aa3f1da8d5641fcd441",
    "tests/test_recovery_nas_transport.py": "a5770b977c54753b4420b730feaf9f7a785500921a9364a8334bf48ee497bb91",
    "tests/test_recovery_resource_holder.py": "85c7f2664774216a8140d23369865edffd14fc81450612caf445dc61ec2937ad",
}

STANDARD_MANIFEST = {
    "plans/2026-10-27_k2b-sqlite-recovery-component.md": "133d21c85f2fc1d4821c58111593c083380798e6afcf19422d3e17eb27293e75",
    "plans/2026-10-27_k2b-standard-tool-recovery-component.md": "50361f62e1fd678260c89e635eebfe0d033e04ae9391acc246dccf6761aaaa3d",
    "scripts/lib/k2b_sqlite_recovery.py": "7d973ca69ad1caadbb7ec32f418c94d75eb49455d508ede01df142c59f41008d",
    "tests/test_k2b_sqlite_recovery.py": "49ae53520231ebc7efd8e4e050900ff2faa30ffee7464c76dd80775f9934c0c5",
    "tests/test_recovery_standard_tools.py": "89108b15119e9ffc3cf2d440bb6b047b3ee5c1c42013c285d891c43f1f617474",
}

def _manifest_sha256(manifest: dict) -> str:
    raw = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def operator_manifest_sha256() -> str:
    """Canonical digest of the fixed 15-file operator manifest (public API)."""
    return _manifest_sha256(OPERATOR_MANIFEST)


def standard_manifest_sha256() -> str:
    return _manifest_sha256(STANDARD_MANIFEST)


def load_operator_components(repo: Path, commit=OPERATOR_COMMIT, *,
                             window=None, stage="operator-load", allowance=None,
                             measured=None):
    """Load exact delivered bytes without importing an unchecked sibling."""
    if commit != OPERATOR_COMMIT:
        raise ContractError("delivery_commit")
    try:
        root = Path(repo)
        if not root.is_absolute():
            raise ContractError("delivery_repo")
        if not isinstance(window, Window):
            raise ContractError("calibration_missing")
        if threading.active_count() != 1:
            raise ContractError("multithreaded_caller")
        if window._calibration.get(stage, (None,))[0] != 60.0:
            raise ContractError("calibration_kind")
        if isinstance(allowance, bool) or not isinstance(allowance, (int, float)) or allowance > 60:
            raise ContractError("phase_bounds")
        view = phase_view(window, stage)

        def verify():
            current = {}
            def git_type(object_name, expected_type, refusal):
                try:
                    view.check()
                    probe = subprocess.run(["/usr/bin/git", "-C", str(root),
                        "cat-file", "-t", object_name], stdin=subprocess.DEVNULL,
                        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                        env={"PATH": "/usr/bin:/bin", "LC_ALL": "C",
                             "GIT_TERMINAL_PROMPT": "0", "GIT_CONFIG_NOSYSTEM": "1",
                             "GIT_CONFIG_GLOBAL": os.devnull},
                        timeout=min(3.0, view.remaining()))
                except subprocess.TimeoutExpired:
                    raise ContractError("delivery_git_timeout") from None
                except OSError:
                    raise ContractError("delivery_git_unavailable") from None
                if probe.returncode != 0 or probe.stdout.strip() != expected_type:
                    raise ContractError(refusal)
            try:
                git_type(commit, b"commit", "delivery_commit_unavailable")
            except ContractError as exc:
                return {"error": exc.reason, "manifest_index": None}
            for index, (rel, digest) in enumerate({**OPERATOR_MANIFEST, **STANDARD_MANIFEST}.items()):
                try:
                    view.check()
                    git_type(commit + ":" + rel, b"blob", "delivery_path_unavailable")
                    if _git_tree_bytes(str(root), commit, rel).decode("ascii") != digest:
                        raise ContractError("delivered_tree_mismatch")
                    path = root / rel
                    _check_component_path_chain(path)
                    raw = _read_verified_bytes(path, COMPONENT_FILE_BOUND)
                    if hashlib.sha256(raw).hexdigest() != digest:
                        raise ContractError("component_hash_mismatch")
                    current[rel] = raw.decode("utf-8")
                except ContractError as exc:
                    return {"error": exc.reason, "manifest_index": index}
                except Exception:
                    return {"error": "operator_component_unavailable", "manifest_index": index}
            return {"current": current}

        verified = supervise_step(stage, verify, window, allowance, measured)
        if "error" in verified:
            raise ContractError(verified["error"]) from _OperatorRecord("operator_load_failure", verified)
        current = {rel: raw.encode("utf-8") for rel, raw in verified["current"].items()}
        view.check()
        if threading.active_count() != 1:
            raise ContractError("multithreaded_caller")
        order = ["recovery_nas_transport", "recovery_exact_extract",
                 "recovery_foreground_crypto", "recovery_exact_archive",
                 "recovery_resource_holder", "k2b_sqlite_recovery"]
        loaded = {}
        with _IMPORT_LOCK:
            originals = {name: sys.modules.get(name) for name in order}
            previous = sys.dont_write_bytecode
            try:
                sys.dont_write_bytecode = True
                for name in order:
                    view.check()
                    rel = "scripts/lib/" + name + ".py"
                    module = types.ModuleType(name)
                    module.__file__ = str(root / rel)
                    module.__package__ = ""
                    sys.modules[name] = module
                    exec(compile(current[rel], module.__file__, "exec", dont_inherit=True), module.__dict__)
                    loaded[name] = module
                view.check()
            finally:
                sys.dont_write_bytecode = previous
                for name, previous_module in originals.items():
                    if previous_module is None:
                        sys.modules.pop(name, None)
                    else:
                        sys.modules[name] = previous_module
        return types.SimpleNamespace(
            transport=loaded["recovery_nas_transport"],
            archive=loaded["recovery_exact_archive"],
            extract=loaded["recovery_exact_extract"],
            crypto=loaded["recovery_foreground_crypto"],
            resources=loaded["recovery_resource_holder"],
            sqlite=loaded["k2b_sqlite_recovery"],
            manifest=dict(OPERATOR_MANIFEST), operator_commit=commit,
            operator_manifest_sha256=operator_manifest_sha256(),
            standard_component_sha256=P2_TEST_SHA256,
            sqlite_component_sha256=STANDARD_MANIFEST["scripts/lib/k2b_sqlite_recovery.py"])
    except ContractError:
        raise
    except Exception:
        raise ContractError("operator_component_unavailable") from None


# ---------------------------------------------------------------------------
# Phase view: a single nonrenewable budget per stage
# ---------------------------------------------------------------------------


class PhaseView:
    """Adapter over an existing Window stage; never extends any deadline."""

    __slots__ = ("_window", "_stage")

    def __init__(self, window, stage):
        if not hasattr(window, "assume") or not hasattr(window, "effective_timeout"):
            raise ContractError("phase_window")
        if not isinstance(stage, str) or not stage:
            raise ContractError("phase_stage")
        self._window = window
        self._stage = stage

    def check(self):
        self._window.assume(self._stage)

    def remaining(self) -> float:
        return self._window.effective_timeout(self._stage)

    # Method names used by actual P2 components.
    def assume(self, _stage=None):
        self.check()

    def effective_timeout(self, _stage=None) -> float:
        return self.remaining()

    def whole_deadline(self) -> float:
        return self._window.whole_deadline()

    def __getattr__(self, name):
        raise ContractError("phase_view_attribute")


def phase_view(window, stage):
    return PhaseView(window, stage)


# ---------------------------------------------------------------------------
# Owned supervisor step bound to a live parent ResourceHolder
# ---------------------------------------------------------------------------


def _holder_evidence(holder):
    evidence = getattr(holder, "evidence", None)
    if not callable(evidence):
        raise ContractError("holder_invalid")
    try:
        value = evidence()
    except ContractError:
        raise
    except BaseException:
        raise ContractError("holder_evidence") from None
    if not isinstance(value, dict) or type(value.get("pid")) is not int:
        raise ContractError("holder_evidence")
    return value


def _component_result(operators, function):
    try:
        return {"owned_status": "ok", "value": function()}
    except ContractError as exc:
        return {"owned_status": "failed", "reason": exc.reason}
    except operators.transport.TransportError as exc:
        return {"owned_status": "failed", "reason": "transport_failed:" + exc.reason}
    except operators.crypto.CryptoError as exc:
        return {"owned_status": "failed", "reason": "crypto_failed:" + str(exc),
                "phase_reason": exc.phase_reason, "cleanup_reason": exc.cleanup_reason}
    except operators.sqlite.RecoveryError as exc:
        return {"owned_status": "failed", "reason": "sqlite_failed:" + exc.code}
    except operators.archive.ArchiveError as exc:
        return {"owned_status": "failed", "reason": "archive_failed:" + str(exc)}
    except operators.extract.ExtractError as exc:
        return {"owned_status": "failed", "reason": "extract_failed:" + str(exc)}


def _component_value(result):
    if result["owned_status"] == "failed":
        raise ContractError(result["reason"]) from _OperatorRecord("component_failure", result)
    return result["value"]


def owned_step(operators, holder, stage, function, window, allowance, measured):
    """Supervise a component step while verifying parent ResourceHolder.

    The callable runs only inside ``supervise_step``; the holder is never
    acquired or released in the child.
    """
    if not callable(function):
        raise ContractError("step_callable")
    if not isinstance(holder, operators.resources.ResourceHolder):
        raise ContractError("holder_invalid")
    if not isinstance(stage, str) or not stage:
        raise ContractError("step_stage")
    view = phase_view(window, stage)

    before = _holder_evidence(holder)
    parent_pid = os.getpid()
    if before.get("pid") != parent_pid:
        raise ContractError("holder_ownership")

    def bound():
        view.check()
        return _component_result(operators, lambda: function(view))

    try:
        result = supervise_step(stage, bound, window, allowance, measured)
    except ContractError as exc:
        failure = {"stage": stage, "ownership_before": before,
                   "ownership_after": None, "ownership_after_error": None}
        try:
            after = _holder_evidence(holder)
            if after.get("pid") != parent_pid:
                raise ContractError("holder_ownership")
            failure["ownership_after"] = after
        except ContractError as evidence_error:
            failure["ownership_after_error"] = evidence_error.reason
        # Private details stay outside the sanitized public exception fields.
        raise exc from _OperatorRecord("owned_step_failure", failure)

    try:
        after = _holder_evidence(holder)
        if after.get("pid") != parent_pid:
            raise ContractError("holder_ownership")
    except ContractError as exc:
        failure = {"stage": stage, "ownership_before": before,
                   "ownership_after": None, "ownership_after_error": exc.reason,
                   "component_reason": result.get("reason")}
        raise exc from _OperatorRecord("owned_step_failure", failure)
    if result["owned_status"] == "failed":
        failure = {"stage": stage, "ownership_before": before,
                   "ownership_after": after, "ownership_after_error": None,
                   "component_failure": result}
        raise ContractError(result["reason"]) from _OperatorRecord("owned_step_failure", failure)

    return {
        "result": result["value"],
        "ownership_before": before,
        "ownership_after": after,
    }


# ---------------------------------------------------------------------------
# Exact archive create / restore delegation
# ---------------------------------------------------------------------------


def create_exact_archive(operators, component, selection, output, window):
    try:
        return operators.archive.create_exact(component, sys.modules.get(
            __name__) or _contract_module(), selection, output, window)
    except ContractError:
        raise
    except operators.archive.ArchiveError as exc:
        raise ContractError("archive_create_failed:" + str(exc)) from None
    except BaseException:
        raise ContractError("archive_create_failed") from None


def _contract_module():
    module = sys.modules.get(__name__)
    if module is None:
        module = types.ModuleType(__name__)
        module.__dict__.update(globals())
    return module


def restore_exact_archive(operators, component, archive, selection,
                          expected_sha256, destination, window):
    try:
        return operators.extract.extract_exact(component, _contract_module(),
                                               archive, selection,
                                               expected_sha256, destination,
                                               window)
    except ContractError:
        raise
    except operators.extract.ExtractError as exc:
        raise ContractError("archive_extract_failed:" + str(exc)) from None
    except BaseException:
        raise ContractError("archive_extract_failed") from None


# ---------------------------------------------------------------------------
# Parent acceptance of a preheld fetched artifact
# ---------------------------------------------------------------------------

_OUTPUT_SIGNATURE_FIELDS = ("dev", "ino", "size", "mode", "nlink",
                            "mtime_ns", "ctime_ns", "uid")


def _verify_output(operators, output):
    try:
        return operators.transport._verify_output(output)
    except operators.transport.TransportError as exc:
        raise ContractError("output_verify:" + exc.reason) from None
    except BaseException:
        raise ContractError("output_verify") from None


def _hash_fd(operators, fd, window):
    try:
        return operators.transport._hash_fd(fd, window)
    except ContractError:
        raise
    except operators.transport.TransportError as exc:
        raise ContractError("output_hash:" + exc.reason) from None
    except BaseException:
        raise ContractError("output_hash") from None


def _require_successful_receipt(operators, receipt):
    if not isinstance(receipt, dict):
        raise ContractError("child_return") from None
    if receipt.get("remote_program_sha256") != operators.transport.REMOTE_PROGRAM_SHA256:
        raise ContractError("child_return") from None
    if type(receipt.get("exit_code")) is not int or receipt["exit_code"] != 0:
        raise ContractError("child_return") from None
    if not operators.transport._is_hex64(receipt.get("execution")):
        raise ContractError("child_return") from None
    elapsed = receipt.get("elapsed_seconds")
    if isinstance(elapsed, bool) or not isinstance(elapsed, (int, float)) or not math.isfinite(elapsed) or elapsed < 0:
        raise ContractError("child_return") from None
    if not operators.transport._is_hex64(receipt.get("context_sha256")):
        raise ContractError("child_return") from None


def _sig_fields(signature):
    if not isinstance(signature, dict) or set(signature) != set(_OUTPUT_SIGNATURE_FIELDS):
        raise ContractError("output_signature") from None
    for key in _OUTPUT_SIGNATURE_FIELDS:
        if type(signature[key]) is not int or signature[key] < 0:
            raise ContractError("output_signature") from None
    return {key: signature[key] for key in _OUTPUT_SIGNATURE_FIELDS}


def accept_local_output(operators, output, receipt, window, *, kind):
    """Foreground acceptance of the parent-held object, same phase budget."""
    if kind not in ("archive", "crypto"):
        raise ContractError("accept_kind")
    if not isinstance(output, operators.transport.FetchedArtifact) or output.fd < 0:
        raise ContractError("output_invalid")
    window.check()
    if not isinstance(receipt, dict):
        raise ContractError("child_return")

    if kind == "archive":
        digest = receipt.get("archive_digest")
        size = receipt.get("archive_size")
        signature = _sig_fields(receipt.get("archive_signature"))
    else:
        digest = receipt.get("output_sha256")
        size = receipt.get("output_size")
        signature = _sig_fields(receipt.get("output_signature"))
        if type(receipt.get("gpg_exit")) is not int or receipt["gpg_exit"] != 0:
            raise ContractError("child_return") from None
        cleanup = receipt.get("cleanup_exit")
        if type(cleanup) is not int or cleanup != 0:
            raise ContractError("child_return") from None

    if not operators.transport._is_hex64(digest):
        raise ContractError("output_signature") from None
    if type(size) is not int or size < 1 or size > (MAX_ARCHIVE_BYTES if kind == "archive" else 2 * 1024**3):
        raise ContractError("output_signature") from None
    if signature["size"] != size:
        raise ContractError("output_signature") from None

    initial = getattr(output, "initial_signature", None)
    if not isinstance(initial, dict):
        raise ContractError("output_invalid") from None
    if (initial.get("dev"), initial.get("ino")) != (signature["dev"], signature["ino"]):
        raise ContractError("output_identity") from None

    pre = _verify_output(operators, output)
    if _sig_fields(pre) != signature:
        raise ContractError("output_changed") from None

    actual_digest, actual_size = _hash_fd(operators, output.fd, window)
    if actual_digest != digest or actual_size != size:
        raise ContractError("output_hash") from None

    post = _verify_output(operators, output)
    if _sig_fields(post) != _sig_fields(pre):
        raise ContractError("output_changed") from None
    window.check()
    return output


# ---------------------------------------------------------------------------
# Transport fetch with independent context/cipher binding
# ---------------------------------------------------------------------------


def _request_context(operators, orchestration, target, parent, run_id, binding, artifact):
    try:
        return operators.transport.request_context(
            orchestration, target, parent, run_id, binding, artifact=artifact)
    except ContractError:
        raise
    except BaseException:
        raise ContractError("request_context") from None


def fetch_artifact(operators, orchestration, target, parent, run_id, binding,
                   create_receipt, upload_receipt, output, window, *,
                   artifact, expected_context_sha256,
                   expected_cipher_sha256, expected_cipher_size,
                   stage, allowance, measured):
    if artifact not in ("archive", "control"):
        raise ContractError("artifact_invalid")
    if not operators.transport._is_hex64(expected_cipher_sha256):
        raise ContractError("cipher_argument") from None
    if type(expected_cipher_size) is not int or expected_cipher_size < 1:
        raise ContractError("cipher_argument") from None
    if not operators.transport._is_hex64(expected_context_sha256):
        raise ContractError("context_argument") from None
    if not isinstance(output, operators.transport.FetchedArtifact):
        raise ContractError("output_invalid")

    local = upload_receipt.get("local") if isinstance(upload_receipt, dict) else None
    if not isinstance(local, dict):
        raise ContractError("upload_receipt") from None
    if local.get("sha256") != expected_cipher_sha256 or local.get("size") != expected_cipher_size:
        raise ContractError("cipher_mismatch") from None

    request = _request_context(operators, orchestration, target, parent, run_id,
                               binding, artifact)
    if request != expected_context_sha256:
        raise ContractError("context_mismatch") from None
    uploaded_context = upload_receipt.get("context_sha256") if isinstance(upload_receipt, dict) else None
    if uploaded_context != expected_context_sha256:
        raise ContractError("context_mismatch") from None

    view = phase_view(window, stage)

    def call():
        view.check()
        return operators.transport.fetch(
            orchestration, target, parent, run_id, binding,
            create_receipt, upload_receipt, output, view,
            artifact=artifact,
            expected_context_sha256=expected_context_sha256)

    receipt = _component_value(supervise_step(stage, lambda: _component_result(operators, call),
                                              window, allowance, measured))

    try:
        return operators.transport.accept_fetched(
            output, receipt, upload_receipt, view,
            expected_context_sha256=expected_context_sha256)
    except ContractError:
        raise
    except operators.transport.TransportError as exc:
        raise ContractError("accept_fetched:" + exc.reason) from None
    except BaseException:
        raise ContractError("accept_fetched") from None


# ---------------------------------------------------------------------------
# Decrypt via actual crypto.crypt under parent acceptance
# ---------------------------------------------------------------------------


def decrypt_artifact(operators, artifact_kind, fetched, output, fresh_home,
                     gpg, gpg_sha256, gpgconf, gpgconf_sha256,
                     original_plaintext_sha256,
                     expected_fetch_context_sha256, window, *,
                     stage, allowance, measured):
    if artifact_kind not in ("archive", "control"):
        raise ContractError("artifact_invalid")
    if not operators.transport._is_hex64(original_plaintext_sha256):
        raise ContractError("plaintext_argument") from None
    if not operators.transport._is_hex64(expected_fetch_context_sha256):
        raise ContractError("context_argument") from None
    if not isinstance(output, operators.transport.FetchedArtifact):
        raise ContractError("output_invalid")
    receipt = getattr(fetched, "receipt", None)
    if not isinstance(fetched, operators.transport.FetchedArtifact) or not receipt:
        raise ContractError("fetched_invalid") from None
    _require_successful_receipt(operators, receipt)
    if receipt.get("context_sha256") != expected_fetch_context_sha256:
        raise ContractError("context_mismatch") from None
    remote = receipt.get("remote") if isinstance(receipt, dict) else None
    if not isinstance(remote, dict) or remote.get("artifact") != artifact_kind:
        raise ContractError("artifact_mismatch") from None

    source_cipher_sha256 = None
    local = receipt.get("local") if isinstance(receipt, dict) else None
    if isinstance(local, dict):
        source_cipher_sha256 = local.get("sha256")
    if not operators.transport._is_hex64(source_cipher_sha256):
        raise ContractError("fetched_invalid") from None

    view = phase_view(window, stage)

    def call():
        view.check()
        return operators.crypto.crypt(
            "decrypt", fetched.fd, output, fresh_home, gpg, gpg_sha256,
            gpgconf, gpgconf_sha256, source_cipher_sha256,
            original_plaintext_sha256, view,
            source_fetch_receipt=receipt,
            expected_fetch_context_sha256=expected_fetch_context_sha256)

    crypto_receipt = _component_value(supervise_step(stage, lambda: _component_result(operators, call),
                                                     window, allowance, measured))
    return accept_local_output(operators, output, crypto_receipt, view, kind="crypto")


# ---------------------------------------------------------------------------
# Small independently-controlled delivery control metadata
# ---------------------------------------------------------------------------

CONTROL_MAX_BYTES = 32 * 1024 * 1024
CONTROL_CLAIM = "exact_admitted_collection_controlled_interval"

_CONTROL_ROOT_KEYS = {"version", "claim", "collections", "bindings",
                      "interval", "sqlite_bundles"}
_CONTROL_BINDING_KEYS = {
    "consumer_sha256",
    "operator_commit",
    "operator_manifest_sha256",
    "standard_component_sha256",
    "sqlite_component_sha256",
    "admission_sha256",
    "source_dispositions_sha256",
    "provenance_sha256",
    "selected_effects_sha256",
    "owner_launch_sha256",
    "workflow_interval_sha256",
    "placement_sha256",
    "metadata_policy_sha256",
    "calibration_sha256",
}
_CONTROL_COLLECTION_KEYS = {"id", "selection", "alias_control_sha256"}
_CONTROL_INTERVAL_KEYS = {"id", "started_at_ns", "finished_at_ns", "evidence_sha256"}
_CONTROL_SQLITE_KEYS = {"id", "profile", "receipt_sha256", "sqlite_version",
                        "interval_evidence_sha256", "relative_root"}
_CONTROL_MAX_COLLECTIONS = 1
_CONTROL_MAX_SQLITE = 32
_CONTROL_MAX_INTERVAL_NS = 600 * 1_000_000_000


def _is_dict(value) -> bool:
    return isinstance(value, dict)


def _is_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _hex64(value) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(c in _HEX for c in value)


def _safe_ident(value, bound=64) -> bool:
    if not isinstance(value, str) or not (1 <= len(value) <= bound):
        return False
    return value.isascii() and value[0].isalnum() and all(c.isalnum() or c in "_-." for c in value)


def _validate_collections(collections):
    if not isinstance(collections, list) or not 1 <= len(collections) <= _CONTROL_MAX_COLLECTIONS:
        raise ContractError("control_collections")
    ids = set()
    selections = []
    rows = []
    for entry in collections:
        if not _is_dict(entry) or set(entry) != _CONTROL_COLLECTION_KEYS:
            raise ContractError("control_collections")
        if not _safe_ident(entry["id"]):
            raise ContractError("control_collections")
        if entry["id"] in ids:
            raise ContractError("control_collections")
        ids.add(entry["id"])
        selection = entry["selection"]
        try:
            _verify_selection(selection)
        except ContractError:
            raise
        if not selection.get("sha256"):
            raise ContractError("selection_unbound")
        alias_digest = entry["alias_control_sha256"]
        if alias_digest is not None:
            if not _hex64(alias_digest):
                raise ContractError("control_collections")
            if selection.get("alias") is None:
                raise ContractError("control_alias")
            if hashlib.sha256(alias_control(selection)).hexdigest() != alias_digest:
                raise ContractError("control_alias")
        elif selection.get("alias") is not None:
            raise ContractError("control_alias")
        selections.append(selection)
        rows.append({"id": entry["id"], "selection_digest": selection["selection_digest"],
                    "alias_control_sha256": alias_digest})
    return selections, rows


def _validate_bindings(bindings):
    if not _is_dict(bindings) or set(bindings) != _CONTROL_BINDING_KEYS:
        raise ContractError("control_bindings")
    for key in _CONTROL_BINDING_KEYS:
        value = bindings[key]
        if key == "operator_commit":
            if value != OPERATOR_COMMIT:
                raise ContractError("control_bindings")
            continue
        if not _hex64(value):
            raise ContractError("control_bindings")
    if bindings["operator_manifest_sha256"] != operator_manifest_sha256():
        raise ContractError("control_bindings")
    if bindings["standard_component_sha256"] != P2_TEST_SHA256:
        raise ContractError("control_bindings")
    if bindings["sqlite_component_sha256"] != STANDARD_MANIFEST["scripts/lib/k2b_sqlite_recovery.py"]:
        raise ContractError("control_bindings")
    return dict(bindings)


def _validate_interval(interval):
    if not _is_dict(interval) or set(interval) != _CONTROL_INTERVAL_KEYS:
        raise ContractError("control_interval")
    if not _safe_ident(interval["id"]):
        raise ContractError("control_interval")
    start = interval["started_at_ns"]
    finish = interval["finished_at_ns"]
    if not _is_int(start) or not _is_int(finish) or start <= 0 or finish < start:
        raise ContractError("control_interval")
    if finish - start > _CONTROL_MAX_INTERVAL_NS:
        raise ContractError("control_interval")
    if not _hex64(interval["evidence_sha256"]):
        raise ContractError("control_interval")
    return {"id": interval["id"], "started_at_ns": start,
            "finished_at_ns": finish, "evidence_sha256": interval["evidence_sha256"]}


def _validate_sqlite(sqlite_bundles):
    if not isinstance(sqlite_bundles, list) or len(sqlite_bundles) > _CONTROL_MAX_SQLITE:
        raise ContractError("control_sqlite")
    ids = set()
    rows = []
    for entry in sqlite_bundles:
        if not _is_dict(entry) or set(entry) != _CONTROL_SQLITE_KEYS:
            raise ContractError("control_sqlite")
        if not _safe_ident(entry["id"]):
            raise ContractError("control_sqlite")
        if entry["id"] in ids:
            raise ContractError("control_sqlite")
        ids.add(entry["id"])
        if entry["profile"] not in ("memory", "meter"):
            raise ContractError("control_sqlite")
        if not _hex64(entry["receipt_sha256"]) or not _hex64(entry["interval_evidence_sha256"]):
            raise ContractError("control_sqlite")
        version = entry["sqlite_version"]
        if not isinstance(version, str) or not (1 <= len(version) <= 64):
            raise ContractError("control_sqlite")
        relative_root = entry["relative_root"]
        if relative_root != ".":
            # Pure bounded syntax validation, not a new operation deadline.
            _validate_relative_path(relative_root, types.SimpleNamespace(check=lambda: None))
        rows.append({"id": entry["id"], "profile": entry["profile"],
                     "receipt_sha256": entry["receipt_sha256"],
                     "sqlite_version": version,
                     "interval_evidence_sha256": entry["interval_evidence_sha256"],
                     "relative_root": relative_root})
    return rows


def _bind_sqlite_members(sqlite_rows, selection):
    seen = set()
    for entry in sqlite_rows:
        prefix = "" if entry["relative_root"] == "." else entry["relative_root"] + "/"
        key = _identity_key(entry["relative_root"])
        if key in seen:
            raise ContractError("control_sqlite_overlap")
        seen.add(key)
        required = {prefix + "receipt.json", prefix + "snapshot.sqlite3"}
        if entry["profile"] == "memory":
            required.add(prefix + "report.json")
        actual = {row["rel"] for row in selection["files"] if row["rel"].startswith(prefix)}
        if actual != required:
            raise ContractError("control_sqlite_members")
        root = entry["relative_root"]
        if root != ".":
            parts = root.split("/")
            for index in range(len(parts)):
                ancestor = "/".join(parts[:index + 1])
                parent = "/".join(parts[:index]) or "."
                if ancestor not in selection["parent_modes"] or parts[index] not in selection["membership"].get(parent, []):
                    raise ContractError("control_sqlite_members")
        expected_names = {name[len(prefix):] for name in required}
        if set(selection["membership"].get(root, [])) != expected_names:
            raise ContractError("control_sqlite_members")
        if any(rel == root or rel.startswith(prefix) for rel in selection["empty_dirs"]):
            raise ContractError("control_sqlite_members")
        alias = selection.get("alias")
        if alias is not None and alias["path"].startswith(prefix):
            raise ContractError("control_sqlite_members")
        if any(rel.startswith(prefix) and rel != entry["relative_root"] for rel in selection["parent_modes"] if rel != "."):
            raise ContractError("control_sqlite_members")
        if selection["sha256"].get(prefix + "receipt.json") != entry["receipt_sha256"]:
            raise ContractError("control_sqlite_receipt")


def _strict_loads(raw: bytes):
    def _bad(_x):
        raise ContractError("control_malformed")

    def _pairs(pairs):
        out = {}
        for key, value in pairs:
            if key in out:
                raise ContractError("control_malformed")
            out[key] = value
        return out

    try:
        text = raw.decode("utf-8")
        return json.loads(text, object_pairs_hook=_pairs,
                          parse_float=_bad, parse_constant=_bad)
    except ContractError:
        raise
    except Exception:
        raise ContractError("control_malformed") from None


def encode_control(collections, bindings, interval, sqlite_bundles) -> bytes:
    _selections, collection_rows = _validate_collections(collections)
    binding_rows = _validate_bindings(bindings)
    interval_row = _validate_interval(interval)
    sqlite_rows = _validate_sqlite(sqlite_bundles)
    _bind_sqlite_members(sqlite_rows, _selections[0])
    body = {
        "version": 1,
        "claim": CONTROL_CLAIM,
        "collections": [
            {"id": row["id"],
             "selection": collections[idx]["selection"],
             "alias_control_sha256": row["alias_control_sha256"]}
            for idx, row in enumerate(collection_rows)
        ],
        "bindings": binding_rows,
        "interval": interval_row,
        "sqlite_bundles": sqlite_rows,
    }


    try:
        raw = json.dumps(body, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError):
        raise ContractError("control_malformed") from None
    if len(raw) > CONTROL_MAX_BYTES:
        raise ContractError("control_too_large")
    return raw


FIXED_COLLECTION_COUNT = 4
MAX_COLLECTION_FILES = 18454


def decode_fixed_controls(raw_controls, original_digests, *, expected_ids,
                          expected_selection_digests, expected_bindings,
                          expected_interval, expected_dependencies,
                          expected_dependencies_sha256):
    """Check exactly four controls against independently bound private inputs.

    Dependency projections come from the existing admitted run record. Each
    link has two endpoints (index 0..3, selected relative file, original hash),
    one endpoint in each of two different controls. This checks selected
    membership/hash and independently retained projection digest, not
    application semantics or successful restore. No new on-disk control schema
    is created and no per-selection limit is relaxed.
    """
    if (not isinstance(expected_ids, (tuple, list))
            or len(expected_ids) != FIXED_COLLECTION_COUNT
            or any(not _safe_ident(value) for value in expected_ids)
            or len(set(expected_ids)) != FIXED_COLLECTION_COUNT
            or not isinstance(expected_selection_digests, (tuple, list))
            or len(expected_selection_digests) != FIXED_COLLECTION_COUNT
            or any(not _hex64(value) for value in expected_selection_digests)
            or not isinstance(raw_controls, (tuple, list))
            or len(raw_controls) != FIXED_COLLECTION_COUNT
            or not isinstance(original_digests, (tuple, list))
            or len(original_digests) != FIXED_COLLECTION_COUNT):
        raise ContractError("control_aggregate_expected")
    bindings = _validate_bindings(expected_bindings)
    interval = _validate_interval(expected_interval)
    controls = []
    for index in range(FIXED_COLLECTION_COUNT):
        control = decode_control(raw_controls[index], original_digests[index])
        controls.append(control)
    controls = tuple(controls)
    selections = []
    for index, control in enumerate(controls):
        if len(control["collections"]) != 1:
            raise ContractError("control_aggregate_binding")
        collection = control["collections"][0]
        if (control["bindings"] != bindings or control["interval"] != interval
                or collection["id"] != expected_ids[index]
                or collection["selection"]["selection_digest"]
                != expected_selection_digests[index]):
            raise ContractError("control_aggregate_binding")
        selections.append(collection["selection"])
    total_files = 0
    total_members = 0
    total_bytes = 0
    total_path_bytes = 0
    total_bundles = 0
    for control in controls:
        bundles = control["sqlite_bundles"]
        total_bundles += len(bundles)
        if total_bundles > _CONTROL_MAX_SQLITE:
            raise ContractError("control_aggregate")
        seen_bundle_ids = set()
        for bundle in bundles:
            bundle_id = bundle["id"]
            if bundle_id in seen_bundle_ids:
                raise ContractError("control_aggregate")
            seen_bundle_ids.add(bundle_id)
        for collection in control["collections"]:
            selection = collection["selection"]
            profile = selection["profile"]
            total_files += profile["file_count"]
            total_members += profile["member_count"]
            total_bytes += profile["total_bytes"]
            total_path_bytes += profile["cumulative_path_bytes"]
            if total_files > MAX_COLLECTION_FILES:
                raise ContractError("control_aggregate")
            if total_members > MAX_MEMBERS:
                raise ContractError("control_aggregate")
            if total_bytes > MAX_TOTAL_BYTES:
                raise ContractError("control_aggregate")
            if total_path_bytes > MAX_CUMULATIVE_PATH_BYTES:
                raise ContractError("control_aggregate")
    if not isinstance(expected_dependencies, list) or not expected_dependencies:
        raise ContractError("control_aggregate_dependencies")
    if len(expected_dependencies) > MAX_FILES:
        raise ContractError("control_aggregate_dependencies")
    if not _hex64(expected_dependencies_sha256):
        raise ContractError("control_aggregate_dependencies")
    try:
        projection_raw = json.dumps(expected_dependencies, sort_keys=True,
                                    separators=(",", ":"), ensure_ascii=False,
                                    allow_nan=False).encode("utf-8")
    except (TypeError, ValueError):
        raise ContractError("control_aggregate_dependencies") from None
    if hashlib.sha256(projection_raw).hexdigest() != expected_dependencies_sha256:
        raise ContractError("control_aggregate_dependencies")
    for link in expected_dependencies:
        if not isinstance(link, (tuple, list)) or len(link) != 2:
            raise ContractError("control_aggregate_dependencies")
        control_indices = set()
        for endpoint in link:
            if not isinstance(endpoint, (tuple, list)) or len(endpoint) != 3:
                raise ContractError("control_aggregate_dependencies")
            index, rel, digest = endpoint
            if (type(index) is not int
                    or index not in (0, 1, 2, 3)
                    or not isinstance(rel, str) or not _hex64(digest)
                    or selections[index]["sha256"].get(rel) != digest):
                raise ContractError("control_aggregate_dependencies")
            control_indices.add(index)
        if len(control_indices) != 2:
            raise ContractError("control_aggregate_dependencies")
    return controls


def decode_control(raw, expected_sha256):
    if not isinstance(raw, (bytes, bytearray)):
        raise ContractError("control_bytes")
    raw = bytes(raw)
    if len(raw) > CONTROL_MAX_BYTES:
        raise ContractError("control_too_large")
    if not _hex64(expected_sha256):
        raise ContractError("control_digest_argument")
    if hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise ContractError("control_digest_mismatch")
    body = _strict_loads(raw)
    if not _is_dict(body) or set(body) != _CONTROL_ROOT_KEYS:
        raise ContractError("control_schema")
    if type(body["version"]) is not int or body["version"] != 1:
        raise ContractError("control_schema")
    if body["claim"] != CONTROL_CLAIM:
        raise ContractError("control_claim")
    _selections, rows = _validate_collections(body["collections"])
    bindings = _validate_bindings(body["bindings"])
    interval = _validate_interval(body["interval"])
    sqlite_rows = _validate_sqlite(body["sqlite_bundles"])
    if encode_control(body["collections"], bindings, interval, sqlite_rows) != raw:
        raise ContractError("control_noncanonical")
    return {
        "version": 1,
        "claim": CONTROL_CLAIM,
        "collections": body["collections"],
        "bindings": bindings,
        "interval": interval,
        "sqlite_bundles": sqlite_rows,
    }
