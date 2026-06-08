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
import datetime as _dt
import errno
import fcntl
import io
import json
import math
import os
import re
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


def _allowed_k2bi_vault_roots() -> list[Path]:
    """K2Bi-vault-only allowlist for A2 strategy/backtest roots (Checkpoint-1 C2 +
    Checkpoint-2 #5).

    Resolves ONLY to the profile K2Bi vault: `K2BI_VAULT_PATH` (the SAME env the
    profile's k2bi_vault() trusts, so the runner's resolution is identical to the
    trusted preflight's profile check) plus the `~/Projects/K2Bi-Vault` default.
    Resolves to EXACTLY ONE root -- the profile vault: `K2BI_VAULT_PATH` if set, else
    the `~/Projects/K2Bi-Vault` default. NOT both (Checkpoint-2 round-2 #2): allowing
    both would let an env-overridden profile validate preflight against K2BI_VAULT_PATH
    while a payload_path-carried `repo_root=~/Projects/K2Bi-Vault` still passes the
    runner, leaving a residual wrong-vault write under profile/env skew. Deliberately
    EXCLUDES `K2B_VAULT_PATH` and the generic `K2B_ORCH_ADAPTER_VAULT_ROOT` override.
    """
    k2bi_vault = os.environ.get("K2BI_VAULT_PATH")
    only = Path(k2bi_vault).expanduser() if k2bi_vault else Path("~/Projects/K2Bi-Vault").expanduser()
    return [only.resolve(strict=False)]


def _allowed_k2bi_repo_roots() -> list[Path]:
    """K2Bi-repo-only allowlist for A3 author/ship roots.

    Resolves to exactly one root: K2B_ORCH_K2BI_WORKSPACE when set, otherwise
    ~/Projects/K2Bi. Deliberately excludes K2Bi-Vault, K2B_VAULT_PATH, and the
    generic adapter vault override; A3's capital path writes only through the
    git-tracked K2Bi repo that the engine consumes after sync.
    """
    workspace = os.environ.get("K2B_ORCH_K2BI_WORKSPACE")
    only = Path(workspace).expanduser() if workspace else Path("~/Projects/K2Bi").expanduser()
    return [only.resolve(strict=False)]


def _resolve_allowed_k2bi_root(path_value: Any, field_name: str) -> Path:
    """Resolve a strategy `repo_root` / backtest `vault_root` to the K2Bi vault ONLY."""
    if not path_value:
        raise ValueError(f"payload missing required field {field_name!r}")
    root = Path(os.path.realpath(os.path.expanduser(str(path_value))))
    for allowed_root in _allowed_k2bi_vault_roots():
        if root == Path(os.path.realpath(str(allowed_root))):
            return root
    raise ValueError(f"{field_name} {root} is outside allowed K2Bi vault root")


def _resolve_allowed_k2bi_repo(path_value: Any, field_name: str) -> Path:
    """Resolve an A3 repo root/path to the K2Bi code repo ONLY."""
    if not path_value:
        raise ValueError(f"payload missing required field {field_name!r}")
    root = Path(os.path.realpath(os.path.expanduser(str(path_value))))
    for allowed_root in _allowed_k2bi_repo_roots():
        if root == Path(os.path.realpath(str(allowed_root))):
            return root
    raise ValueError(f"{field_name} {root} is outside allowed K2Bi repo root")


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
    # datetime is a subclass of date; .isoformat() covers both. BacktestResult
    # carries date/datetime (window.start/end, last_run, trade dates) that json
    # cannot serialize -- map them to ISO strings (additive; thesis/bear results
    # carry none).
    if isinstance(value, (_dt.date, _dt.datetime)):
        return value.isoformat()
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
    # Carrier-agnostic non-empty gate: the orchestrator's inline shape check only
    # sees payload_json, so a payload_path-carried thesis would otherwise reach
    # here with an empty decision set and defeat the fail-fast T7 contract. Enforce
    # it in the runner so BOTH carriers gate it identically (Codex Checkpoint-2
    # MEDIUM, 2026-06-07). The K2Bi adapter also rejects empty downstream; this is
    # the earlier, carrier-uniform stop.
    if not raw_claim_decisions:
        raise ValueError("claim_decisions must not be empty")
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


def _validate_strategy_decision_payload(decision: Any, payload_symbol: str) -> None:
    """Runner-side fail-fast gates the strict dataclass coercion cannot do
    (Checkpoint-1 C1 + C4), enforced for BOTH the payload_json and payload_path
    carriers (the inline preflight shape check only sees payload_json)."""
    if not isinstance(decision, dict):
        raise ValueError("decision must be a JSON object")
    decision_symbol = str(decision.get("symbol", "")).strip().upper()
    if not decision_symbol:
        raise ValueError("decision missing symbol")
    order = decision.get("order")
    if not isinstance(order, dict):
        raise ValueError("decision.order must be a JSON object")
    order_ticker = str(order.get("ticker", "")).strip().upper()
    if not order_ticker:
        raise ValueError("decision.order.ticker missing")
    # C1: K2Bi derives the strategy file's top-level `ticker` from decision.symbol
    # but preserves decision.order.ticker verbatim, and run_backtest fetches bars for
    # order.ticker. If they diverge, a CDNS chain could write strategy_cdns.md yet
    # backtest a DIFFERENT ticker -> false gate evidence. Require all three equal.
    if not (payload_symbol == decision_symbol == order_ticker):
        raise ValueError(
            "symbol mismatch: payload symbol "
            f"{payload_symbol!r}, decision.symbol {decision_symbol!r}, and "
            f"decision.order.ticker {order_ticker!r} must all be the same ticker"
        )
    # C4: forward_guidance_metrics is typed list[dict[str, Any]], so the strict
    # coercer treats nested values as Any. K2Bi's writer does
    # bool(m["sits_inside_guide"]), so a string "false" would silently flip to True.
    # Enforce required keys + a REAL boolean here.
    metrics = decision.get("forward_guidance_metrics")
    if not isinstance(metrics, list) or not metrics:
        raise ValueError("decision.forward_guidance_metrics must be a non-empty list")
    required_metric_keys = (
        "metric",
        "locked_threshold_text",
        "guide_source_text",
        "guide_range_text",
        "sits_inside_guide",
    )
    for i, metric in enumerate(metrics):
        if not isinstance(metric, dict):
            raise ValueError(f"forward_guidance_metrics[{i}] must be a JSON object")
        for key in required_metric_keys:
            if key not in metric:
                raise ValueError(f"forward_guidance_metrics[{i}] missing {key!r}")
        if not isinstance(metric["sits_inside_guide"], bool):
            raise ValueError(
                f"forward_guidance_metrics[{i}].sits_inside_guide must be a JSON boolean, "
                f"got {type(metric['sits_inside_guide']).__name__}"
            )


def _write_strategy_spec(payload: dict[str, Any]) -> dict[str, Any]:
    """A2 Stage 9: author the proposed strategy file via the merged K2Bi adapter.

    repo_root is the K2Bi VAULT (where wiki/strategies/ lives). No capital path,
    no engine touch -- this writes a `status: proposed` strategy file only.
    """
    repo_root = _resolve_allowed_k2bi_root(payload.get("repo_root"), "repo_root")
    from scripts.lib import invest_orchestrator_adapters as ioa

    payload_symbol = str(payload.get("symbol", "")).strip().upper()
    if not payload_symbol:
        raise ValueError("payload missing required field 'symbol'")
    decision = _load_json_value(payload, "decision")
    _validate_strategy_decision_payload(decision, payload_symbol)
    decision_obj = _dataclass_from_dict(ioa.StrategySpecDecision, decision)
    result = ioa.write_complete_strategy_spec(decision_obj, repo_root=repo_root)
    return {"status": "ok", "result": _result_to_jsonable(result)}


def _run_backtest(payload: dict[str, Any]) -> dict[str, Any]:
    """A2 Stage 10: run the yfinance sanity-check backtest for a written strategy.

    Reads vault_root/wiki/strategies/strategy_<slug>.md (order.ticker), pulls live
    bars, writes an immutable capture under raw/backtests/, and NEVER touches the
    strategy file. The sanity gate's look_ahead_check is the overfit rejector.
    """
    vault_root = _resolve_allowed_k2bi_root(payload.get("vault_root"), "vault_root")
    from scripts.lib import invest_backtest as ibt

    slug = payload.get("slug")
    if not isinstance(slug, str) or not slug.strip():
        raise ValueError("payload missing required field 'slug'")
    payload_symbol = str(payload.get("symbol", "")).strip().upper()
    if not payload_symbol:
        raise ValueError("payload missing required field 'symbol'")
    result = ibt.run_backtest(slug.strip(), vault_root=vault_root)
    # Bind the backtest to the dispatched ticker (Checkpoint-2 round-2 #1): the
    # capture's symbol is the strategy file's order.ticker, so a mismatch means the
    # backtest fetched bars for the wrong ticker -- refuse rather than record it.
    result_symbol = str(getattr(result, "symbol", "")).strip().upper()
    if result_symbol != payload_symbol:
        raise ValueError(
            f"backtest symbol {result_symbol!r} does not match dispatched symbol "
            f"{payload_symbol!r} (slug {slug!r} resolves to the wrong ticker)"
        )
    return {"status": "ok", "result": _result_to_jsonable(result)}


def _author_strategy_to_repo(payload: dict[str, Any]) -> dict[str, Any]:
    """A3 Stage 11a: author the approved proposed strategy into the K2Bi repo.

    This is identical to A2's strategy writer except for the root resolver:
    repo_root is the git-tracked K2Bi repo, not the K2Bi vault.
    """
    repo_root = _resolve_allowed_k2bi_repo(payload.get("repo_root"), "repo_root")
    from scripts.lib import invest_orchestrator_adapters as ioa

    payload_symbol = str(payload.get("symbol", "")).strip().upper()
    if not payload_symbol:
        raise ValueError("payload missing required field 'symbol'")
    decision = _load_json_value(payload, "decision")
    _validate_strategy_decision_payload(decision, payload_symbol)
    decision_obj = _dataclass_from_dict(ioa.StrategySpecDecision, decision)
    result = ioa.write_complete_strategy_spec(decision_obj, repo_root=repo_root)
    return {"status": "ok", "result": _result_to_jsonable(result)}


def _resolve_allowed_strategy_repo_path(path_value: Any) -> Path:
    if not path_value:
        raise ValueError("payload missing required field 'strategy_path'")
    raw = os.path.expanduser(str(path_value))
    strategy_path = Path(os.path.realpath(raw))
    repo_root = _allowed_k2bi_repo_roots()[0]
    strategies_dir = repo_root / "wiki" / "strategies"
    try:
        under = os.path.commonpath([str(strategy_path), str(strategies_dir)]) == str(strategies_dir)
    except ValueError:
        under = False
    if not under:
        raise ValueError(
            f"strategy_path {strategy_path} is outside K2Bi repo strategies dir {strategies_dir}"
        )
    name = strategy_path.name
    if not (name.startswith("strategy_") and name.endswith(".md")):
        raise ValueError(f"strategy_path must be wiki/strategies/strategy_<slug>.md: {strategy_path}")
    return strategy_path


def _limits_slug_from_proposal_path(path: Path) -> str | None:
    match = re.match(r"^\d{4}-\d{2}-\d{2}_limits-proposal_(.+)$", path.stem)
    slug = match.group(1) if match else path.stem
    return slug or None


def _resolve_allowed_limits_proposal_path(path_value: Any) -> Path:
    if not path_value:
        raise ValueError("payload missing required field 'proposal_path'")
    raw = os.path.expanduser(str(path_value))
    proposal_path = Path(os.path.realpath(raw))
    repo_root = _allowed_k2bi_repo_roots()[0]
    approvals_dir = repo_root / "review" / "strategy-approvals"
    try:
        under = os.path.commonpath([str(proposal_path), str(approvals_dir)]) == str(approvals_dir)
    except ValueError:
        under = False
    if not under:
        raise ValueError(
            f"proposal_path {proposal_path} is outside K2Bi repo strategy-approvals dir {approvals_dir}"
        )
    if proposal_path.suffix != ".md" or "_limits-proposal_" not in proposal_path.stem:
        raise ValueError(
            f"proposal_path must be review/strategy-approvals/*_limits-proposal_*.md: {proposal_path}"
        )
    if _limits_slug_from_proposal_path(proposal_path) is None:
        raise ValueError(f"proposal_path has no derivable limits slug: {proposal_path}")
    return proposal_path


def _approval_from_payload(raw_approval: Any) -> Any:
    if not isinstance(raw_approval, dict):
        raise ValueError("payload approval must be a JSON object")
    required = ("final_approval_token", "approved_by", "approved_at", "ship_lease_id")
    for key in required:
        value = raw_approval.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"payload approval.{key} must be a non-empty string")
    from scripts.lib import invest_orchestrator_adapters as ioa

    return ioa.FullShipApproval(
        final_approval_token=raw_approval["final_approval_token"],
        approved_by=raw_approval["approved_by"],
        approved_at=raw_approval["approved_at"],
        ship_lease_id=raw_approval["ship_lease_id"],
    )


def _limits_approval_from_payload(raw_approval: Any) -> Any:
    if not isinstance(raw_approval, dict):
        raise ValueError("payload approval must be a JSON object")
    required = ("final_approval_token", "approved_by", "approved_at", "apply_lease_id")
    for key in required:
        value = raw_approval.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"payload approval.{key} must be a non-empty string")
    from scripts.lib import invest_orchestrator_adapters as ioa

    return ioa.LimitsApproval(
        final_approval_token=raw_approval["final_approval_token"],
        approved_by=raw_approval["approved_by"],
        approved_at=raw_approval["approved_at"],
        apply_lease_id=raw_approval["apply_lease_id"],
    )


def _run_full_ship(payload: dict[str, Any]) -> dict[str, Any]:
    """A3 Stage 11b: dispatch K2Bi's merged full-ship adapter.

    K2B only builds the typed approval and validates root containment. K2Bi's
    run_full_ship owns reviews, the real strategy approval gate, commit, and
    rollback.
    """
    strategy_path = _resolve_allowed_strategy_repo_path(payload.get("strategy_path"))
    vault_root = _resolve_allowed_k2bi_root(payload.get("vault_root"), "vault_root")
    from scripts.lib import invest_orchestrator_adapters as ioa

    payload_symbol = str(payload.get("symbol", "")).strip().upper()
    if not payload_symbol:
        raise ValueError("payload missing required field 'symbol'")
    approval = _approval_from_payload(payload.get("approval"))
    required_primary = str(payload.get("required_primary", "minimax")).strip() or "minimax"
    result = ioa.run_full_ship(
        strategy_path,
        approval=approval,
        vault_root=vault_root,
        required_primary=required_primary,
    )
    return {"status": "ok", "result": _result_to_jsonable(result)}


A4_ALLOWED_REVIEW_PRIMARIES = frozenset({"minimax", "kimi"})


def _apply_approved_limits(payload: dict[str, Any]) -> dict[str, Any]:
    """A4: dispatch K2Bi's merged approved-limits adapter."""
    proposal_path = _resolve_allowed_limits_proposal_path(payload.get("proposal_path"))
    from scripts.lib import invest_orchestrator_adapters as ioa

    approval = _limits_approval_from_payload(payload.get("approval"))
    required_primary = str(payload.get("required_primary", "minimax")).strip() or "minimax"
    # Fail fast on the capital path: an unknown review provider must not reach the
    # K2Bi review late (CP2 r1 fix G).
    if required_primary not in A4_ALLOWED_REVIEW_PRIMARIES:
        raise ValueError(
            f"required_primary must be one of {sorted(A4_ALLOWED_REVIEW_PRIMARIES)}, "
            f"got {required_primary!r}"
        )
    # Bind config_path explicitly to the SAME validator file whose sha the preflight
    # dual-sha token bound, instead of relying on the K2Bi default (CP2 r1 fix E).
    config_path = _allowed_k2bi_repo_roots()[0] / "execution" / "validators" / "config.yaml"
    result = ioa.apply_approved_limits(
        proposal_path,
        approval=approval,
        config_path=config_path,
        required_primary=required_primary,
    )
    return {"status": "ok", "result": _result_to_jsonable(result)}


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
    envelope = {
        "status": "error",
        "category": category,
        "retryable": retryable,
        "exit_code": exit_code,
        "exception_type": type(exc).__name__,
        "message": str(exc),
    }
    rollback_result = getattr(exc, "rollback_result", None)
    if rollback_result is not None:
        envelope["rollback_result"] = _result_to_jsonable(rollback_result)
    return envelope


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
    for name in (
        "verify-and-generate-thesis",
        "run-bear-case",
        "write-strategy-spec",
        "run-backtest",
        "author-strategy-to-repo",
        "run-full-ship",
        "apply-limits",
    ):
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
        elif args.cmd == "write-strategy-spec":
            output = _write_strategy_spec(payload)
        elif args.cmd == "run-backtest":
            output = _run_backtest(payload)
        elif args.cmd == "author-strategy-to-repo":
            output = _author_strategy_to_repo(payload)
        elif args.cmd == "run-full-ship":
            output = _run_full_ship(payload)
        elif args.cmd == "apply-limits":
            output = _apply_approved_limits(payload)
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
