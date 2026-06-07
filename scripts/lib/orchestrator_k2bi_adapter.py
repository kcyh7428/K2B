#!/usr/bin/env python3
"""Thin K2B runner for callable K2Bi orchestrator adapters.

This process is executed by the K2B orchestrator worker from the trusted K2Bi
workspace. It performs only JSON -> K2Bi dataclass conversion and delegates all
T7/bear safety behavior to K2Bi's merged adapter/helper layer.
"""

from __future__ import annotations

import argparse
import contextlib
import dataclasses
import errno
import fcntl
import io
import json
import math
import os
import sys
import tempfile
import traceback
import types
from pathlib import Path
from typing import Any, Union, get_args, get_origin, get_type_hints


class AdapterTransientError(RuntimeError):
    """Retryable adapter failure, usually from producer/consumer file timing."""


FALLBACK_ADAPTER_ERROR_JSON = (
    '{"category":"unexpected","exit_code":5,'
    '"message":"adapter error envelope serialization failed",'
    '"retryable":false,"status":"error"}'
)


def _allowed_payload_roots() -> list[Path]:
    roots = []
    explicit = os.environ.get("K2B_ORCH_ADAPTER_PAYLOAD_DIR")
    if explicit:
        roots.append(Path(explicit).expanduser())
    vault = os.environ.get("K2B_VAULT_PATH")
    if vault:
        roots.append(Path(vault).expanduser() / "raw" / "orchestrator-results")
    roots.append(Path(tempfile.gettempdir()) / "k2b-orchestrator")
    return [root.resolve(strict=False) for root in roots]


def _max_payload_file_bytes() -> int:
    return int(os.environ.get("K2B_ORCH_ADAPTER_MAX_PAYLOAD_FILE_BYTES", "1000000"))


def _allowed_vault_roots() -> list[Path]:
    roots = []
    explicit = os.environ.get("K2B_ORCH_ADAPTER_VAULT_ROOT")
    if explicit:
        roots.append(Path(explicit).expanduser())
    k2bi_vault = os.environ.get("K2BI_VAULT_PATH")
    if k2bi_vault:
        roots.append(Path(k2bi_vault).expanduser())
    roots.append(Path("~/Projects/K2Bi-Vault").expanduser())
    k2b_vault = os.environ.get("K2B_VAULT_PATH")
    if k2b_vault:
        roots.append(Path(k2b_vault).expanduser())
    return [root.resolve(strict=False) for root in roots]


def _read_allowed_json_file(path_value: Any) -> str:
    raw_path = os.path.expanduser(str(path_value))
    lexical = Path(os.path.abspath(os.path.normpath(raw_path)))
    resolved = Path(os.path.realpath(raw_path))
    if lexical != resolved:
        raise ValueError(f"payload path {lexical} is outside allowed payload directory")
    try:
        lexical.resolve(strict=True)
    except FileNotFoundError as exc:
        raise AdapterTransientError(
            f"payload path {lexical} is not yet available; retry after producer completes atomic write"
        ) from exc
    except (OSError, RuntimeError) as exc:
        raise ValueError(f"payload path {lexical} is outside allowed payload directory") from exc
    allowed_roots = _allowed_payload_roots()
    for root in allowed_roots:
        root_path = Path(os.path.abspath(os.path.normpath(str(root))))
        try:
            under_root = os.path.commonpath([str(lexical), str(root_path)]) == str(root_path)
        except ValueError:
            under_root = False
        if not under_root:
            continue
        fd = None
        try:
            flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
            fd = os.open(str(lexical), flags)
            if os.fstat(fd).st_size > _max_payload_file_bytes():
                raise ValueError(
                    f"payload file {lexical} exceeds maximum of {_max_payload_file_bytes()} bytes"
            )
            fd_path = _fd_realpath(fd)
            if fd_path is None:
                raise ValueError("payload fd path verification unavailable")
            elif not _path_is_under_any_root(fd_path, allowed_roots):
                raise ValueError(f"payload path {fd_path} is outside allowed payload directory")
            with os.fdopen(fd, "r", encoding="utf-8") as fh:
                fd = None
                return fh.read()
        except OSError as exc:
            if getattr(exc, "errno", None) == errno.ENOENT:
                raise AdapterTransientError(
                    f"payload path {lexical} is not yet available; retry after producer completes atomic write"
                ) from exc
            raise ValueError(f"payload path {lexical} is outside allowed payload directory") from exc
        finally:
            if fd is not None:
                os.close(fd)
    raise ValueError(f"payload path {lexical} is outside allowed payload directory")


def _fd_realpath(fd: int) -> Path | None:
    getpath = getattr(fcntl, "F_GETPATH", None)
    if getpath is None:
        # macOS has F_GETPATH. Linux CI/dev hosts can still verify the opened
        # fd through procfs; if neither exists, callers retain the lexical
        # root + O_NOFOLLOW checks but lose this defense-in-depth check.
        try:
            target = os.readlink(f"/proc/self/fd/{fd}")
        except OSError:
            return None
        path = Path(target)
        if not path.is_absolute():
            return None
        return path
    try:
        raw = fcntl.fcntl(fd, getpath, b"\0" * 1024)
    except OSError:
        return None
    if not isinstance(raw, (bytes, bytearray)):
        return None
    path = bytes(raw).split(b"\0", 1)[0]
    if not path:
        return None
    return Path(path.decode("utf-8"))


def _path_is_under_any_root(path: Path, roots: list[Path]) -> bool:
    candidate = Path(os.path.abspath(os.path.normpath(str(path))))
    for root in roots:
        root_path = Path(os.path.abspath(os.path.normpath(str(root))))
        try:
            if os.path.commonpath([str(candidate), str(root_path)]) == str(root_path):
                return True
        except ValueError:
            continue
    return False


def _resolve_allowed_vault_root(path_value: Any) -> Path:
    if not path_value:
        raise ValueError("payload missing required field 'vault_root'")
    vault_root = Path(os.path.realpath(os.path.expanduser(str(path_value))))
    for allowed_root in _allowed_vault_roots():
        if vault_root == Path(os.path.realpath(str(allowed_root))):
            return vault_root
    raise ValueError(f"vault_root {vault_root} is outside allowed vault root")


def _load_payload(args: argparse.Namespace) -> dict[str, Any]:
    if sum(value is not None for value in (args.payload_json, args.payload_path)) != 1:
        raise ValueError("exactly one of --payload-json or --payload-path is required")
    if args.payload_path is not None:
        text = _read_allowed_json_file(args.payload_path)
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise AdapterTransientError(
                "payload_path JSON is not parseable; retry after producer completes atomic write"
            ) from exc
    else:
        if args.payload_json == "":
            raise ValueError("payload_json must not be empty")
        text = args.payload_json
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError("payload_json is not parseable JSON") from exc
    if not isinstance(data, dict):
        raise ValueError("payload must be a JSON object")
    return data


def _load_json_value(payload: dict[str, Any], key: str) -> Any:
    path_key = f"{key}_path"
    if path_key in payload:
        try:
            return json.loads(_read_allowed_json_file(payload[path_key]))
        except json.JSONDecodeError as exc:
            raise AdapterTransientError(
                f"{path_key} JSON is not parseable; retry after producer completes atomic write"
            ) from exc
    if key not in payload:
        raise ValueError(f"payload missing required field {key!r}")
    return payload[key]


def _max_input_depth() -> int:
    return int(os.environ.get("K2B_ORCH_ADAPTER_MAX_INPUT_DEPTH", "32"))


def _coerce(annotation: Any, value: Any, *, _depth: int = 0) -> Any:
    if _depth > _max_input_depth():
        raise ValueError("adapter input exceeds maximum depth")
    if annotation is Any:
        return value
    if value is None:
        return None
    origin = get_origin(annotation)
    args = get_args(annotation)
    if origin in (list, tuple):
        item_type = args[0] if args else Any
        if not isinstance(value, (list, tuple)):
            raise ValueError(f"expected list, got {type(value).__name__}")
        return [_coerce(item_type, item, _depth=_depth + 1) for item in value]
    if origin is dict:
        if not isinstance(value, dict):
            raise ValueError(f"expected dict, got {type(value).__name__}")
        if len(args) >= 2:
            key_type, value_type = args[0], args[1]
            return {
                _coerce(key_type, key, _depth=_depth + 1): _coerce(
                    value_type, item, _depth=_depth + 1
                )
                for key, item in value.items()
            }
        return dict(value)
    if origin in (Union, types.UnionType):
        errors = []
        for candidate in args:
            if candidate is type(None):
                continue
            try:
                return _coerce(candidate, value, _depth=_depth + 1)
            except Exception as exc:
                errors.append(f"{candidate}: {exc}")
        raise ValueError(
            f"value of type {type(value).__name__} does not match any allowed type "
            f"for {annotation}: {', '.join(errors)}"
        )
    if dataclasses.is_dataclass(annotation):
        return _dataclass_from_dict(annotation, value, _depth=_depth + 1)
    if annotation is Path:
        return Path(value)
    if annotation is str:
        if not isinstance(value, str):
            raise ValueError(f"expected str, got {type(value).__name__}")
        return value
    if annotation is bool:
        if not isinstance(value, bool):
            raise ValueError(f"expected bool, got {type(value).__name__}")
        return value
    if annotation is int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"expected int, got {type(value).__name__}")
        return value
    if annotation is float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"expected float, got {type(value).__name__}")
        return value
    raise ValueError(f"unsupported type annotation {annotation!r} for coercion")


def _dataclass_from_dict(cls: type, data: Any, *, _depth: int = 0) -> Any:
    if _depth > _max_input_depth():
        raise ValueError("adapter input exceeds maximum depth")
    if not isinstance(data, dict):
        raise ValueError(f"{cls.__name__} payload must be an object")
    type_hints = get_type_hints(cls)
    fields = {field.name: field for field in dataclasses.fields(cls)}
    extra_fields = sorted(set(data.keys()) - set(fields.keys()))
    if extra_fields:
        raise ValueError(f"{cls.__name__} payload has unexpected field {extra_fields[0]}")
    kwargs = {}
    for field in fields.values():
        if field.name in data:
            kwargs[field.name] = _coerce(
                type_hints.get(field.name, field.type),
                data[field.name],
                _depth=_depth + 1,
            )
        else:
            raise ValueError(f"{cls.__name__} payload missing required field {field.name}")
    return cls(**kwargs)


def _result_to_jsonable(value: Any, *, _depth: int = 0) -> Any:
    max_depth = int(os.environ.get("K2B_ORCH_ADAPTER_MAX_OUTPUT_DEPTH", "32"))
    if _depth > max_depth:
        raise ValueError("adapter output exceeds maximum depth")
    if dataclasses.is_dataclass(value):
        return {
            field.name: _result_to_jsonable(getattr(value, field.name), _depth=_depth + 1)
            for field in dataclasses.fields(value)
        }
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (list, tuple)):
        return [_result_to_jsonable(item, _depth=_depth + 1) for item in value]
    if isinstance(value, dict):
        return {str(k): _result_to_jsonable(v, _depth=_depth + 1) for k, v in value.items()}
    return value


def _dump_bounded_output(output: dict[str, Any]) -> str:
    max_bytes = int(os.environ.get("K2B_ORCH_ADAPTER_MAX_OUTPUT_BYTES", "10000000"))
    chunks = []
    total = 0
    encoder = json.JSONEncoder(ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    for chunk in encoder.iterencode(output):
        total += len(chunk.encode("utf-8"))
        if total > max_bytes:
            raise ValueError(f"adapter output exceeds maximum of {max_bytes} bytes")
        chunks.append(chunk)
    return "".join(chunks)


def _verify_and_generate_thesis(payload: dict[str, Any]) -> dict[str, Any]:
    vault_root = _resolve_allowed_vault_root(payload.get("vault_root"))
    from scripts.lib import invest_orchestrator_adapters as ioa
    from scripts.lib import invest_thesis as it

    payload_symbol = str(payload.get("symbol", "")).strip().upper()
    if not payload_symbol:
        raise ValueError("payload missing required field 'symbol'")
    raw_claim_decisions = _load_json_value(payload, "claim_decisions")
    if not isinstance(raw_claim_decisions, list):
        raise ValueError("claim_decisions must be a JSON array")
    if len(raw_claim_decisions) > 500:
        raise ValueError("claim_decisions exceeds maximum of 500")
    if not all(isinstance(item, dict) for item in raw_claim_decisions):
        raise ValueError("claim_decisions items must be JSON objects")
    thesis_input = _dataclass_from_dict(
        it.ThesisInput,
        _load_json_value(payload, "thesis_input"),
    )
    thesis_symbol = str(getattr(thesis_input, "symbol", "")).strip().upper()
    if thesis_symbol != payload_symbol:
        raise ValueError(
            f"payload symbol {payload_symbol} does not match thesis_input.symbol {thesis_symbol}"
        )
    claim_decisions = [
        _dataclass_from_dict(ioa.ThesisClaimDecision, item)
        for item in raw_claim_decisions
    ]
    result = ioa.verify_and_generate_thesis(
        thesis_input,
        vault_root,
        claim_decisions=claim_decisions,
        operator_override_reason=payload.get("operator_override_reason"),
        calx_override_acknowledged=bool(payload.get("calx_override_acknowledged", False)),
        vendor_warning_acknowledged=bool(payload.get("vendor_warning_acknowledged", False)),
        vendor_provenance=payload.get("vendor_provenance"),
        refresh=bool(payload.get("refresh", False)),
        learning_stage=str(payload.get("learning_stage", "advanced")),
    )
    return {"status": "ok", "result": _result_to_jsonable(result)}


def _run_bear_case(payload: dict[str, Any]) -> dict[str, Any]:
    vault_root = _resolve_allowed_vault_root(payload.get("vault_root"))
    from scripts.lib import invest_bear_case as ibc

    payload_symbol = str(payload.get("symbol", "")).strip().upper()
    if not payload_symbol:
        raise ValueError("payload missing required field 'symbol'")
    position_size_hkd = _validate_position_size_hkd(payload.get("position_size_hkd"))
    bear_input = _dataclass_from_dict(
        ibc.BearCaseInput,
        _load_json_value(payload, "bear_input"),
    )
    result = ibc.run_bear_case(
        payload_symbol,
        bear_input,
        vault_root,
        refresh=bool(payload.get("refresh", False)),
        learning_stage=str(payload.get("learning_stage", "advanced")),
        position_size_hkd=position_size_hkd,
    )
    return {"status": "ok", "result": _result_to_jsonable(result)}


def _validate_position_size_hkd(value: Any) -> int | float | None:
    if value is None:
        return None
    max_value = float(os.environ.get("K2B_ORCH_MAX_POSITION_SIZE_HKD", "1000000000000"))
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("position_size_hkd must be numeric")
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("position_size_hkd must be finite")
    if value < 0 or value > max_value:
        raise ValueError(f"position_size_hkd must be between 0 and {max_value:g}")
    return value


TRANSIENT_ERRNOS = {
    errno.EAGAIN,
    getattr(errno, "EWOULDBLOCK", errno.EAGAIN),
    errno.ENOENT,
    getattr(errno, "ESTALE", errno.ENOENT),
    errno.ETIMEDOUT,
    errno.ECONNRESET,
    errno.ECONNREFUSED,
}


def _adapter_error_envelope(exc: Exception) -> dict[str, Any]:
    """Machine-readable adapter failure contract for the orchestrator worker."""
    if isinstance(exc, AdapterTransientError):
        category = "transient"
        retryable = True
        exit_code = 3
    elif isinstance(exc, ValueError):
        category = "validation"
        retryable = False
        exit_code = 2
    elif isinstance(exc, (TimeoutError, ConnectionError)) or (
        isinstance(exc, OSError) and getattr(exc, "errno", None) in TRANSIENT_ERRNOS
    ):
        category = "transient"
        retryable = True
        exit_code = 3
    elif isinstance(exc, (PermissionError, ImportError, AttributeError)):
        category = "environment"
        retryable = False
        exit_code = 4
    elif isinstance(exc, OSError):
        category = "environment"
        retryable = False
        exit_code = 4
    else:
        category = "unexpected"
        retryable = False
        exit_code = 5
    return {
        "status": "error",
        "category": category,
        "retryable": retryable,
        "exit_code": exit_code,
        "exception_type": type(exc).__name__,
        "message": str(exc),
    }


def _safe_adapter_error_json(exc: Exception) -> tuple[dict[str, Any], str]:
    try:
        error = _adapter_error_envelope(exc)
    except Exception:
        error = {
            "status": "error",
            "category": "unexpected",
            "retryable": False,
            "exit_code": 5,
            "exception_type": "AdapterErrorEnvelopeFailure",
            "message": "adapter failed before building error envelope",
        }
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            encoded = json.dumps(
                error,
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=True,
            )
        return error, encoded
    except Exception:
        fallback = {
            "status": "error",
            "category": "unexpected",
            "retryable": False,
            "exit_code": 5,
            "exception_type": "AdapterErrorSerializationFailure",
            "message": "adapter error envelope serialization failed",
        }
        return fallback, FALLBACK_ADAPTER_ERROR_JSON


def _restore_module_snapshot(original_modules: dict[str, Any]) -> None:
    sys.modules.clear()
    sys.modules.update(original_modules)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run K2Bi adapter from K2B orchestrator")
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name in ("verify-and-generate-thesis", "run-bear-case"):
        p = sub.add_parser(name)
        p.add_argument("--workspace", required=True)
        p.add_argument("--payload-json")
        p.add_argument("--payload-path")
    args = parser.parse_args(argv)

    original_sys_path = list(sys.path)
    original_modules = dict(sys.modules)
    workspace = None
    encoded_output = None
    exit_code = 0
    cleanup_exc = None
    try:
        try:
            workspace = Path(args.workspace).expanduser().resolve(strict=True)
        except FileNotFoundError as exc:
            raise AdapterTransientError(f"workspace {args.workspace} is not available") from exc
        if not (workspace / "scripts" / "lib").is_dir():
            raise ValueError(f"workspace {workspace} is not a K2Bi checkout")
        # The command is launched with cwd=<K2Bi workspace>, but the trust anchor
        # is the explicit profile-resolved workspace argument.
        sys.path.insert(0, str(workspace))
        payload = _load_payload(args)
        if args.cmd == "verify-and-generate-thesis":
            output = _verify_and_generate_thesis(payload)
        elif args.cmd == "run-bear-case":
            output = _run_bear_case(payload)
        else:  # pragma: no cover - argparse enforces choices
            raise ValueError(f"unknown command {args.cmd!r}")
        encoded_output = _dump_bounded_output(output)
        if not isinstance(encoded_output, str):
            raise RuntimeError("adapter did not produce encoded JSON output")
    except Exception as exc:
        # The worker can parse stdout for retry policy; stderr stays readable for
        # Keith/operator logs and preserves the original adapter message.
        error, encoded_error = _safe_adapter_error_json(exc)
        encoded_output = encoded_error
        exit_code = int(error["exit_code"])
        traceback.print_exception(type(exc), exc, exc.__traceback__, file=sys.stderr)
        print(error["message"], file=sys.stderr)
    finally:
        try:
            # The adapter runs untrusted workspace imports in-process. Restoring
            # the exact module snapshot keeps repeated in-process tests isolated;
            # in production this process exits immediately after one command.
            _restore_module_snapshot(original_modules)
        except Exception as exc:
            cleanup_exc = exc
        finally:
            sys.path[:] = original_sys_path
    if cleanup_exc is not None:
        # Cleanup (in-process module-snapshot restore) only matters for test
        # isolation; in production the process exits immediately after one
        # command, so a cleanup failure is moot. It must NEVER replace the
        # original stdout envelope: masking a SUCCESSFUL thesis/bear result as
        # a failure would trigger a false worker retry on a task that actually
        # succeeded. Log to stderr only; the original success/error envelope
        # stays on stdout (original wins -- Checkpoint-2 finding, 2026-06-07).
        cleanup_error = RuntimeError(f"adapter module cleanup failed: {cleanup_exc}")
        traceback.print_exception(
            type(cleanup_error),
            cleanup_error,
            cleanup_error.__traceback__,
            file=sys.stderr,
        )
        print(str(cleanup_error), file=sys.stderr)
    if encoded_output is None:
        _error, encoded_output = _safe_adapter_error_json(
            RuntimeError("adapter did not emit structured output")
        )
        exit_code = 5
    print(encoded_output)
    return exit_code

if __name__ == "__main__":
    raise SystemExit(main())
