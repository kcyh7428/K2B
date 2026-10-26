"""Attempt-bound normalized Kimi review result (protocol_version 1).

The runner creates a private attempt directory and passes three protected
environment values to the reviewer child. The reviewer writes one durable
JSON result atomically after its existing checks; the runner reads only that
unique expected path with the exact attempt and scope binding. Logs remain
diagnostic evidence only and cannot approve.
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

RESULT_PATH_ENV = "K2B_REVIEW_RESULT_PATH"
ATTEMPT_ID_ENV = "K2B_REVIEW_ATTEMPT_ID"
SCOPE_SHA256_ENV = "K2B_REVIEW_SCOPE_SHA256"
PROTOCOL_VERSION = 1
MAX_RESULT_BYTES = 1024 * 1024
MAX_INPUT_BYTES = 16 * 1024 * 1024
INPUT_NAME = "review_input.txt"
_ACCEPTED_FINISH_REASONS = {"end_turn", "stop"}
_VERDICTS = {"approve", "needs-attention"}


def _env_binding() -> tuple[str, str, str] | None:
    values = [os.environ.get(key) for key in (
        RESULT_PATH_ENV, ATTEMPT_ID_ENV, SCOPE_SHA256_ENV,
    )]
    present = [v is not None and v != "" for v in values]
    if not any(present):
        return None
    if not all(present):
        raise ValueError("partial review result binding")
    path, attempt_id, scope = values
    return path, attempt_id, scope


def _is_hex64(value: object) -> bool:
    if not isinstance(value, str) or len(value) != 64:
        return False
    return all(c in "0123456789abcdef" for c in value)


def _sha256_file_streaming(path: Path, limit: int) -> str:
    import hashlib

    digest = hashlib.sha256()
    total = 0
    fd = os.open(
        str(path), os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        with os.fdopen(fd, "rb") as handle:
            opened = os.fstat(handle.fileno())
            if not stat.S_ISREG(opened.st_mode):
                raise ValueError("review input is not a regular file")
            if opened.st_uid != os.getuid() or stat.S_IMODE(opened.st_mode) & 0o077:
                raise ValueError("review input ownership or permissions invalid")
            while True:
                chunk = handle.read(65536)
                if not chunk:
                    break
                total += len(chunk)
                if total > limit:
                    raise ValueError("review input oversized")
                digest.update(chunk)
    finally:
        pass
    return digest.hexdigest()


def _atomic_exclusive_write(final_path: Path, data: bytes) -> None:
    """Publish data at final_path with mode0600, no overwrite, no symlink."""
    parent = final_path.parent
    if final_path.is_symlink():
        raise ValueError("review path is a symlink")
    if final_path.exists():
        raise ValueError("review path already exists")
    if not parent.is_dir() or parent.is_symlink():
        raise ValueError("review path parent is not a real directory")
    temp_path = parent / f".{final_path.name}.{os.getpid()}.{id(data):x}.tmp"
    fd = os.open(
        str(temp_path),
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(str(temp_path), str(final_path), follow_symlinks=False)
        except FileExistsError as exc:
            raise ValueError("review path already exists") from exc
        directory_fd = os.open(parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        try:
            os.unlink(str(temp_path))
        except OSError:
            pass


def write_review_input(prompt: str) -> str | None:
    """Publish exact reviewed prompt bytes next to the bound result path.

    Returns the SHA256 hex digest of the written bytes, or None when no
    result binding is present (direct wrapper invocation). Partial binding
    is an error. Uses the same bounded atomic exclusive publication as the
    result. Prompt is already redacted by build_prompt.
    """
    binding = _env_binding()
    if binding is None:
        return None
    path, _attempt_id, _scope_sha256 = binding
    if not isinstance(prompt, str):
        raise ValueError("invalid review input")
    data = prompt.encode("utf-8")
    if len(data) > MAX_INPUT_BYTES:
        raise ValueError("review input oversized")
    final_path = Path(path).parent / INPUT_NAME
    _atomic_exclusive_write(final_path, data)
    import hashlib

    return hashlib.sha256(data).hexdigest()


def write_attempt_result(
    review: object,
    *,
    finish_reason: object,
    model: object,
    content_sha256: object = None,
) -> Path | None:
    """Write the normalized result when the child is attempt-bound.

    Returns the written path, or None when no binding is present (direct
    wrapper invocation keeps normal behavior). Partial binding is an error.
    content_sha256 must equal the digest of the retained review_input.txt
    sibling. Refuses overwrite and symlink targets. Publishes atomically and
    exclusively via a mode-0600 temp file and exclusive hard link.
    """
    binding = _env_binding()
    if binding is None:
        return None
    path, attempt_id, scope_sha256 = binding
    if finish_reason not in _ACCEPTED_FINISH_REASONS:
        raise ValueError("unsupported finish_reason")
    if not isinstance(model, str) or not model:
        raise ValueError("invalid model")
    if not isinstance(review, dict):
        raise ValueError("invalid review object")
    if review.get("verdict") not in _VERDICTS:
        raise ValueError("invalid review verdict")
    if not _is_hex64(content_sha256):
        raise ValueError("invalid content_sha256")

    final_path = Path(path)
    input_path = final_path.parent / INPUT_NAME
    input_digest = _sha256_file_streaming(input_path, MAX_INPUT_BYTES)
    if input_digest != content_sha256:
        raise ValueError("review input digest mismatch")

    payload = {
        "protocol_version": PROTOCOL_VERSION,
        "attempt_id": attempt_id,
        "scope_sha256": scope_sha256,
        "content_sha256": content_sha256,
        "finish_reason": finish_reason,
        "model": model,
        "review": review,
    }
    data = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    _atomic_exclusive_write(final_path, data)
    return final_path


def read_attempt_result(
    expected_path: Path,
    *,
    attempt_id: str,
    scope_sha256: str,
    expected_content_sha256: str | None = None,
    metadata: dict | None = None,
) -> str:
    """Return lowercase approve or needs-attention after full validation.

    Accepts only the runner's expected unique attempt path with matching
    attempt/scope binding, valid protocol, success finish reason and a review
    object that fully satisfies the bundled review schema. Also verifies the
    content_sha256 binding against the retained sibling review_input.txt and,
    when supplied, expected_content_sha256.
    """
    from minimax_review import SCHEMA_PATH, validate_review_output

    try:
        st = os.lstat(str(expected_path))
    except OSError as exc:
        raise ValueError("review result missing") from exc
    if not stat.S_ISREG(st.st_mode):
        raise ValueError("review result is not a regular file")
    if st.st_size > MAX_RESULT_BYTES:
        raise ValueError("review result oversized")
    fd = os.open(
        str(expected_path), os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        with os.fdopen(fd, "rb") as handle:
            opened = os.fstat(handle.fileno())
            if not stat.S_ISREG(opened.st_mode) or opened.st_uid != os.getuid():
                raise ValueError("review result ownership or type invalid")
            if stat.S_IMODE(opened.st_mode) & 0o077:
                raise ValueError("review result permissions invalid")
            raw = handle.read(MAX_RESULT_BYTES + 1)
    finally:
        pass
    if len(raw) > MAX_RESULT_BYTES:
        raise ValueError("review result oversized")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except Exception as exc:
        raise ValueError("review result malformed") from exc
    if not isinstance(payload, dict):
        raise ValueError("review result malformed")
    if type(payload.get("protocol_version")) is not int or payload.get("protocol_version") != PROTOCOL_VERSION:
        raise ValueError("review result bad protocol_version")
    if payload.get("attempt_id") != attempt_id:
        raise ValueError("review result attempt mismatch")
    if payload.get("scope_sha256") != scope_sha256:
        raise ValueError("review result scope mismatch")
    if payload.get("finish_reason") not in _ACCEPTED_FINISH_REASONS:
        raise ValueError("review result non-success finish")
    if not isinstance(payload.get("model"), str) or not payload.get("model"):
        raise ValueError("review result missing model")
    content_sha256 = payload.get("content_sha256")
    if not _is_hex64(content_sha256):
        raise ValueError("review result invalid content_sha256")
    if expected_content_sha256 is not None and content_sha256 != expected_content_sha256:
        raise ValueError("review result content digest mismatch")
    input_path = expected_path.parent / INPUT_NAME
    try:
        input_stat = os.lstat(str(input_path))
    except OSError as exc:
        raise ValueError("review input missing") from exc
    if not stat.S_ISREG(input_stat.st_mode):
        raise ValueError("review input is not a regular file")
    if input_stat.st_uid != os.getuid():
        raise ValueError("review input ownership invalid")
    if stat.S_IMODE(input_stat.st_mode) & 0o077:
        raise ValueError("review input permissions invalid")
    input_digest = _sha256_file_streaming(input_path, MAX_INPUT_BYTES)
    if input_digest != content_sha256:
        raise ValueError("review result content digest mismatch")
    review = payload.get("review")
    if not isinstance(review, dict):
        raise ValueError("review result invalid review")
    try:
        schema = json.loads(SCHEMA_PATH.read_text())
    except Exception as exc:
        raise ValueError("review schema unavailable") from exc
    schema_error = validate_review_output(review, schema)
    if schema_error is not None:
        raise ValueError("review result invalid normalized review")
    verdict = review.get("verdict")
    if verdict not in _VERDICTS:
        raise ValueError("review result invalid verdict")
    if metadata is not None:
        metadata.update(content_sha256=content_sha256, model=payload["model"])
    return verdict
