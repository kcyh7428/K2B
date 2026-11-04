"""Bounded streaming reads of native logs, preserving compressed originals."""
from contextlib import contextmanager
import io
import os
from pathlib import Path
import select
import shutil
import subprocess
import time

MAX_DECODED_BYTES = 1024 * 1024 * 1024


class SourceReaderUnavailable(ValueError):
    """A required host-local source decoder is unavailable."""


def session_paths(root):
    plain = set(root.rglob("*.jsonl"))
    compressed = {p for p in root.rglob("*.jsonl.zst")
                  if p.with_suffix("") not in plain}
    return sorted(plain | compressed)


class _DecodedSource(io.RawIOBase):
    def __init__(self, process, deadline, limit=MAX_DECODED_BYTES):
        super().__init__()
        self.process, self.deadline, self.total = process, deadline, 0
        self.limit = limit

    def readable(self):
        return True

    def readinto(self, target):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0 or not select.select([self.process.stdout], [], [], remaining)[0]:
            raise TimeoutError("compressed source scan deadline exceeded")
        data = os.read(self.process.stdout.fileno(), min(len(target), 65536, self.limit - self.total + 1))
        self.total += len(data)
        if self.total > self.limit:
            raise ValueError("compressed source exceeds decoded byte limit")
        if not data:
            try:
                code = self.process.wait(timeout=max(0.001, self.deadline - time.monotonic()))
            except subprocess.TimeoutExpired as exc:
                raise TimeoutError("compressed source scan deadline exceeded") from exc
            if code:
                raise ValueError("compressed Codex source failed decompression")
        target[:len(data)] = data
        return len(data)


@contextmanager
def open_session(path, mode="rb", *, deadline=None, max_decoded_bytes=None):
    if not path.name.endswith(".jsonl.zst"):
        with path.open(mode, **({"encoding": "utf-8"} if "b" not in mode else {})) as handle:
            yield handle
        return
    executable = shutil.which("zstd") or next(
        (str(p) for p in (Path("/opt/homebrew/bin/zstd"), Path("/usr/local/bin/zstd")) if p.is_file()), None)
    if not executable:
        raise SourceReaderUnavailable("compressed Codex source requires installed zstd; restore this host's approved decoder installation")
    bound = deadline if deadline is not None else time.monotonic() + 120
    try:
        process = subprocess.Popen([executable, "-d", "-c", "--", str(path)],
                                   stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    except OSError as exc:
        raise SourceReaderUnavailable(
            f"compressed Codex decoder could not start ({type(exc).__name__}); verify this host's zstd installation"
        ) from exc
    try:
        limit = MAX_DECODED_BYTES if max_decoded_bytes is None else min(max_decoded_bytes, MAX_DECODED_BYTES)
        binary = io.BufferedReader(_DecodedSource(process, bound, limit))
        with binary if "b" in mode else io.TextIOWrapper(binary, encoding="utf-8") as handle:
            yield handle
    except TimeoutError as exc:
        raise TimeoutError(f"{path}: {exc}") from exc
    except ValueError as exc:
        raise ValueError(f"{path}: {exc}") from exc
    finally:
        if process.poll() is None:
            process.kill()
        process.wait()
        process.stdout.close()
