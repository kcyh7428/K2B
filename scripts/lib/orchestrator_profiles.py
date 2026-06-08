#!/usr/bin/env python3
"""Worker-profile registry, allowlist, and K2Bi preflight for K2B orchestrator."""

import json
import hashlib
import os
import re
import socket
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

REPO_ROOT = Path(__file__).resolve().parents[2]
A1_ADAPTER_RUNNER = REPO_ROOT / "scripts" / "lib" / "orchestrator_k2bi_adapter.py"
# Positive lookahead enforces at least one alpha character; callers uppercase first.
SYMBOL_RE = re.compile(r"^(?=.*[A-Z])[A-Z0-9]+(?:\.[A-Z0-9]+)?$")
SHIP_LEASE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{2,127}$")
MAX_ADAPTER_PAYLOAD_JSON_BYTES = 1_000_000
ALLOWED_A1_OPERATOR_MARKS = frozenset({"verified", "refused", "override", "advisory"})
CANONICAL_REGISTRY_MISSING_ERROR = (
    "canonical ticker registry missing or unreadable -- run: "
    "python3 -m scripts.build_canonical_registry"
)
CANONICAL_REGISTRY_EMPTY_ERROR = (
    "canonical ticker registry empty -- run: python3 -m scripts.build_canonical_registry"
)
CANONICAL_REGISTRY_MALFORMED_ERROR = (
    "canonical ticker registry malformed JSON -- inspect disk/sync state, then run: "
    "python3 -m scripts.build_canonical_registry"
)


def _expand(p):
    return str(Path(p).expanduser())


def k2bi_workspace():
    return os.environ.get("K2B_ORCH_K2BI_WORKSPACE") or _expand("~/Projects/K2Bi")


def k2bi_repo():
    return k2bi_workspace()


def k2bi_vault():
    return os.environ.get("K2BI_VAULT_PATH") or _expand("~/Projects/K2Bi-Vault")


def k2bi_allowed_commands():
    # NOTE: `k2bi-narrative` (the dispatched path that runs K2Bi's
    # invest_narrative_pipeline, which uses Kimi to generate candidates +
    # citations) is SUPERSEDED for the Chat-2 conductor by the agent-native path
    # (waiting_for_agent_theme parked state + `verify-theme`). The conductor now
    # generates candidates + validates/repairs citations in-session (no Kimi) and
    # writes the theme directly, then verify-theme gates it. This entry is
    # retained only so the Ship-1a/Increment-2 dispatch invariants + tests stay
    # green; the conductor does NOT create ready k2bi-narrative flights, so the
    # Kimi pipeline is never reached via the live Chat-2 path. Do not re-route
    # Chat-2 through this command.
    return {
        "k2bi-smoke-enrich-lrcx": [
            "python3",
            "-m",
            "scripts.lib.invest_screen",
            "--enrich",
            "LRCX",
        ],
        "k2bi-narrative": [
            "python3",
            "-m",
            "scripts.lib.invest_narrative_pipeline",
        ],
        "k2bi-screen-enrich": [
            "python3",
            "-m",
            "scripts.lib.invest_screen",
            "--enrich",
        ],
        "k2bi-verify-and-generate-thesis": [
            "python3",
            str(A1_ADAPTER_RUNNER),
            "verify-and-generate-thesis",
        ],
        "k2bi-run-bear-case": [
            "python3",
            str(A1_ADAPTER_RUNNER),
            "run-bear-case",
        ],
        "k2bi-write-strategy-spec": [
            "python3",
            str(A1_ADAPTER_RUNNER),
            "write-strategy-spec",
        ],
        "k2bi-run-backtest": [
            "python3",
            str(A1_ADAPTER_RUNNER),
            "run-backtest",
        ],
        "k2bi-author-strategy-to-repo": [
            "python3",
            str(A1_ADAPTER_RUNNER),
            "author-strategy-to-repo",
        ],
        "k2bi-run-full-ship": [
            "python3",
            str(A1_ADAPTER_RUNNER),
            "run-full-ship",
        ],
        "k2bi-apply-limits": [
            "python3",
            str(A1_ADAPTER_RUNNER),
            "apply-limits",
        ],
        "test-echo-readonly": ["/bin/echo", "orchestrator-smoke-ok"],
    }


def get_profile(name) -> dict | None:
    if name == "k2bi":
        return {
            "workspace_path": k2bi_workspace(),
            "vault_path": k2bi_vault(),
            "permission": "analyst-command",
            "worker_lock": "/tmp/k2b-orch-k2bi-worker.lock",
            "human_lock": None,
            "result_slug": "k2bi-smoke",
        }
    return None


def _valid_payload_json(payload_json) -> bool:
    if not isinstance(payload_json, str):
        return False
    if len(payload_json.encode("utf-8")) > MAX_ADAPTER_PAYLOAD_JSON_BYTES:
        return False
    return all(ord(ch) >= 32 for ch in payload_json)


def _adapter_payload_args(payload: dict | None) -> list[str] | None:
    args = ["--workspace", resolve_workspace("k2bi")]
    if not payload:
        return None
    payload_path = payload.get("payload_path")
    if payload_path:
        return args + ["--payload-path", str(payload_path)]
    payload_json = payload.get("payload_json")
    if payload_json is not None:
        if not _valid_payload_json(payload_json):
            return None
        return args + ["--payload-json", payload_json]
    return None


def _payload_symbol(payload: dict | None) -> str | None:
    if not payload:
        return None
    symbol = str(payload.get("symbol", "")).strip().upper()
    if not symbol or not SYMBOL_RE.match(symbol):
        return None
    return symbol


def _canonical_symbol_known(symbol: str) -> tuple[bool, str]:
    registry_path = Path(k2bi_vault()) / "wiki" / "tickers" / "canonical-registry.json"
    try:
        if not registry_path.exists():
            return (False, CANONICAL_REGISTRY_MISSING_ERROR)
        registry_text = registry_path.read_text(encoding="utf-8")
        if not registry_text:
            return (False, CANONICAL_REGISTRY_EMPTY_ERROR)
        registry = json.loads(registry_text)
    except json.JSONDecodeError:
        return (False, CANONICAL_REGISTRY_MALFORMED_ERROR)
    except OSError:
        return (False, CANONICAL_REGISTRY_MISSING_ERROR)
    if not isinstance(registry, dict) or not registry:
        return (False, CANONICAL_REGISTRY_EMPTY_ERROR)
    entry = registry.get(symbol)
    if not isinstance(entry, dict) or not entry.get("name"):
        return (False, f"unknown canonical ticker {symbol}")
    return (True, "")


def resolve_command(profile_name, command_key, payload=None) -> list[str] | None:
    if profile_name == "k2bi":
        cmds = k2bi_allowed_commands()
        if command_key in cmds:
            payload_view = dict(payload) if isinstance(payload, dict) else payload
            # k2bi-narrative (the Kimi-backed dispatch) is RETIRED -- the agent-native
            # Chat-2 path replaces it. Gate it at the EXECUTION boundary: resolve_command
            # is called by BOTH preflight AND orchestrator_worker, so refusing here means
            # a manually-claimed / 'running' task cannot reach the Kimi pipeline even when
            # preflight is bypassed -- unless an explicit operator override is set
            # (Codex Checkpoint-2 round-2 F1).
            if command_key == "k2bi-narrative" and os.environ.get(
                "K2B_ORCH_ALLOW_LEGACY_NARRATIVE"
            ) != "1":
                return None
            argv = list(cmds[command_key])  # copy
            if command_key == "k2bi-narrative":
                if payload_view and isinstance(payload_view, dict):
                    narrative = payload_view.get("narrative", "")
                    argv.append(f"--narrative={narrative}")
            if command_key == "k2bi-screen-enrich":
                symbol = _payload_symbol(payload_view if isinstance(payload_view, dict) else None)
                if symbol is None:
                    return None
                argv.append(symbol)
            if command_key in {
                "k2bi-verify-and-generate-thesis",
                "k2bi-run-bear-case",
                "k2bi-write-strategy-spec",
                "k2bi-run-backtest",
                "k2bi-author-strategy-to-repo",
                "k2bi-run-full-ship",
                "k2bi-apply-limits",
            }:
                payload_args = _adapter_payload_args(payload_view if isinstance(payload_view, dict) else None)
                if payload_args is None:
                    return None
                argv.extend(payload_args)
            return argv
    return None


def resolve_workspace(profile_name) -> str:
    if profile_name == "k2bi":
        return k2bi_workspace()
    raise ValueError(f"unknown profile: {profile_name}")


def preflight(task) -> tuple[bool, str]:
    profile = task.get("assignee_profile")
    if profile == "k2bi":
        return preflight_k2bi(task)
    return (False, f"preflight not implemented for profile: {profile}")


def _worker_lock_is_stale(path) -> bool:
    """A worker lock is stale if the PID it records is no longer alive.

    A worker SIGKILLed by zombie reclaim cannot run its `finally`, so it
    orphans /tmp/k2b-orch-k2bi-worker.lock. Without this check, one reclaimed
    worker would wedge ALL future k2bi dispatch as "worker lock present".
    """
    try:
        with open(path) as f:
            pid_txt = f.read().strip()
    except OSError:
        return False  # unreadable -> treat as live (safe default)
    if not pid_txt:
        return True  # empty -> orphaned
    try:
        pid = int(pid_txt)
    except ValueError:
        return True  # garbage contents -> orphaned
    try:
        os.kill(pid, 0)
        return False  # alive
    except ProcessLookupError:
        return True  # dead -> stale
    except (PermissionError, OSError):
        return False  # alive under another owner / unconfirmable -> not stale


def _provider_probe_target() -> tuple[str, int]:
    """Resolve the (host, port) to probe for P5 reachability.

    Derived from the SAME provider config the narrative child will use
    (scripts.lib.minimax_common): KIMI_API_HOST for provider kimi (the
    default), MINIMAX_API_HOST for minimax, honoring env overrides. A
    hard-coded host would let P5 pass while the real provider is down, or
    block a healthy configured provider -- defeating the unmet-prerequisite
    gate the narrative lane depends on.
    """
    provider = os.environ.get("K2B_LLM_PROVIDER", "kimi").strip() or "kimi"
    if provider == "kimi":
        api_host = os.environ.get("KIMI_API_HOST", "https://api.kimi.com/coding")
    else:
        api_host = os.environ.get("MINIMAX_API_HOST", "https://api.minimaxi.com")
    parsed = urlparse(api_host if "://" in api_host else f"https://{api_host}")
    return (parsed.hostname or api_host, parsed.port or 443)


def _provider_key_available(provider: str) -> bool:
    """True if the provider's API key is resolvable the SAME way the K2Bi
    pipeline resolves it: os.environ first, then a quoted ~/.zshrc export
    fallback (mirrors scripts.lib.minimax_common.load_kimi_api_key). Without
    the ~/.zshrc fallback, P3 false-blocks when the key lives in ~/.zshrc but
    is not exported to the dispatch shell, even though the pipeline WOULD find
    it via its own fallback (live MVP test 2026-05-31, flight 2026-05-31-001).
    """
    env_var = "KIMI_API_KEY" if provider == "kimi" else "MINIMAX_API_KEY"
    if os.environ.get(env_var, "").strip():
        return True
    zshrc = Path.home() / ".zshrc"
    if zshrc.exists():
        try:
            pattern = rf'^\s*export\s+{env_var}\s*=\s*"([^"]+)"'
            if re.search(pattern, zshrc.read_text(), re.MULTILINE):
                return True
        except OSError:
            return False
    return False


def _preflight_narrative(task) -> tuple[bool, str]:
    """Narrative-specific preflight checks P0-P5."""
    workspace = resolve_workspace("k2bi")
    vault = k2bi_vault()

    # Parse payload
    payload = {}
    raw_payload = task.get("payload")
    if raw_payload:
        try:
            parsed = json.loads(raw_payload)
            if isinstance(parsed, dict):
                payload = parsed
        except json.JSONDecodeError:
            pass

    # P0 -- module importable
    try:
        result = subprocess.run(
            ["python3", "-c", "import scripts.lib.invest_narrative_pipeline"],
            cwd=workspace,
            capture_output=True,
            text=True,
            timeout=15,
        )
        if result.returncode != 0:
            return (
                False,
                "narrative pipeline module not importable -- check K2Bi deploy state",
            )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return (
            False,
            "narrative pipeline module not importable -- check K2Bi deploy state",
        )

    # P1 -- macro-themes dir writable
    macro_themes = os.path.join(vault, "wiki", "macro-themes")
    if not os.path.isdir(macro_themes) or not os.access(macro_themes, os.W_OK):
        return (False, "macro-themes output dir missing or not writable")

    # P2 -- ticker registry sane
    registry_path = os.path.join(vault, "wiki", "tickers", "canonical-registry.json")
    try:
        if not os.path.exists(registry_path) or os.path.getsize(registry_path) == 0:
            return (
                False,
                "canonical ticker registry missing/empty/malformed -- run: python3 -m scripts.build_canonical_registry",
            )
        with open(registry_path) as f:
            registry = json.load(f)
        if not isinstance(registry, dict) or not registry:
            return (
                False,
                "canonical ticker registry missing/empty/malformed -- run: python3 -m scripts.build_canonical_registry",
            )
        aapl = registry.get("AAPL")
        if not isinstance(aapl, dict) or not aapl.get("name"):
            return (
                False,
                "canonical ticker registry missing/empty/malformed -- run: python3 -m scripts.build_canonical_registry",
            )
    except (json.JSONDecodeError, OSError):
        return (
            False,
            "canonical ticker registry missing/empty/malformed -- run: python3 -m scripts.build_canonical_registry",
        )

    # P3 -- LLM key resolvable (env OR ~/.zshrc, mirroring the K2Bi provider)
    provider = os.environ.get("K2B_LLM_PROVIDER", "kimi").strip() or "kimi"
    if not _provider_key_available(provider):
        env_var = "KIMI_API_KEY" if provider == "kimi" else "MINIMAX_API_KEY"
        return (False, f"LLM API key not configured ({env_var})")

    # P4 -- narrative seed length
    narrative = payload.get("narrative", "")
    if narrative is None:
        narrative = ""
    narrative = str(narrative).strip()
    if not narrative:
        return (False, "narrative seed empty")
    if len(narrative) < 40:
        return (False, "narrative seed empty")
    if len(narrative) > 500:
        return (False, "narrative seed too long -- distill to 1-3 sentences")

    # P5 -- provider reachability (LIGHT, best-effort). Probe the host the child
    # will actually call, not a hard-coded one (see _provider_probe_target).
    if os.environ.get("K2B_ORCH_SKIP_PROVIDER_PING") != "1":
        host, port = _provider_probe_target()
        try:
            sock = socket.create_connection((host, port), timeout=5)
            sock.close()
        except socket.gaierror:
            return (False, "LLM provider unreachable")
        except ConnectionRefusedError:
            return (False, "LLM provider unreachable")
        except (OSError, socket.timeout, TimeoutError):
            # timeout or uncertainty -> pass (do not false-block on a slow network)
            pass

    return (True, "")


def _task_payload(task) -> dict:
    raw_payload = task.get("payload")
    if not raw_payload:
        return {}
    try:
        parsed = json.loads(raw_payload)
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _read_frontmatter(path: Path) -> dict:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError(f"watchlist entry unreadable: {path}") from exc
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise ValueError(f"watchlist entry has no frontmatter: {path}")
    close_idx = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            close_idx = i
            break
    if close_idx is None:
        raise ValueError(f"watchlist entry frontmatter not closed: {path}")
    frontmatter_lines = lines[1:close_idx]
    if any(re.search(r"(^|[\s\[{,])[&*][A-Za-z0-9_-]+", line) for line in frontmatter_lines):
        raise ValueError(f"watchlist YAML anchors or aliases are not allowed: {path}")
    if any("!!" in line for line in frontmatter_lines):
        raise ValueError(f"watchlist YAML tags are not allowed: {path}")
    status_keys = [
        line for line in frontmatter_lines
        if re.match(r"^status\s*:", line)
    ]
    if len(status_keys) > 1:
        raise ValueError(f"watchlist frontmatter has duplicate status key: {path}")
    try:
        import yaml
    except ImportError as exc:
        raise ValueError("yaml unavailable -- cannot validate watchlist frontmatter") from exc
    data = yaml.safe_load("\n".join(frontmatter_lines))
    if not isinstance(data, dict):
        raise ValueError(f"watchlist frontmatter is not a mapping: {path}")
    if not all(isinstance(key, str) for key in data.keys()):
        raise ValueError(f"watchlist frontmatter keys must be strings: {path}")
    if "status" in data and not isinstance(data["status"], str):
        raise ValueError(f"watchlist status must be a string: {path}")
    return data


def assert_a1_promoted_precondition(symbol: str, vault_root: str | None = None) -> None:
    clean_symbol = str(symbol or "").strip().upper()
    if not clean_symbol:
        raise ValueError("A1 thesis dispatch missing symbol for promoted precondition")
    root = Path(vault_root or k2bi_vault()).expanduser()
    watchlist_path = root / "wiki" / "watchlist" / f"{clean_symbol}.md"
    if not watchlist_path.exists():
        raise ValueError(
            f"A1 thesis dispatch requires {clean_symbol} watchlist entry at "
            f"{watchlist_path}; status must be 'promoted' or 'screened'"
        )
    frontmatter = _read_frontmatter(watchlist_path)
    status = (frontmatter.get("status") or "").strip()
    # Thesis (Stage 5-7) runs AFTER screen (Stage 4), which advances the
    # watchlist promoted -> screened. So the pre-thesis status is normally
    # 'screened'; 'promoted' is also valid (thesis before a redundant screen).
    # Requiring exactly 'promoted' here was the A1.1 bug: a real screen made
    # status 'screened' and every thesis dispatch was rejected (live MVP #1,
    # 2026-06-07). Accept both; reject only pre-promote / terminal states.
    if status not in {"promoted", "screened"}:
        raise ValueError(
            f"A1 thesis dispatch requires {clean_symbol} watchlist entry "
            f"to be status 'promoted' or 'screened' before thesis dispatch; got {status!r}"
        )


def _preflight_a1_verify_thesis(payload: dict) -> tuple[bool, str]:
    try:
        assert_a1_promoted_precondition(
            str(payload.get("symbol", "")),
            vault_root=payload.get("vault_root") or payload.get("k2bi_vault_root"),
        )
    except ValueError as exc:
        return (False, str(exc))
    return (True, "")


def _inline_adapter_payload(payload: dict) -> dict | None:
    raw = payload.get("payload_json")
    if raw is None:
        return None
    if not isinstance(raw, str) or not raw.strip():
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _preflight_a1_adapter_payload_shape(command_key: str, payload: dict) -> tuple[bool, str]:
    adapter_payload = _inline_adapter_payload(payload)
    if adapter_payload is None:
        return (True, "")
    if command_key == "k2bi-verify-and-generate-thesis":
        thesis_input = adapter_payload.get("thesis_input")
        claim_decisions = adapter_payload.get("claim_decisions")
        # Fail-fast shape check ONLY -- the K2Bi adapter's strict dataclass
        # coercion (invest_thesis.ThesisInput) is the real validator. Check the
        # two fields the real merged ThesisInput actually carries: `symbol` and
        # `ticker_type`. The earlier `title`/`base_sources` checks were written
        # against an obsolete stub schema (those fields do not exist on the merged
        # ThesisInput at K2Bi 0ef1631), so a correctly-shaped real payload was
        # rejected here before the worker ever ran (live MVP run #2, 2026-06-07).
        if not isinstance(thesis_input, dict) or not thesis_input.get("symbol"):
            return (False, "A1 thesis payload missing thesis_input.symbol")
        if not thesis_input.get("ticker_type"):
            return (False, "A1 thesis payload missing thesis_input.ticker_type")
        if not isinstance(claim_decisions, list) or not claim_decisions:
            return (False, "A1 thesis payload missing non-empty claim_decisions list")
        for item in claim_decisions:
            if not isinstance(item, dict):
                return (False, "A1 thesis payload claim_decisions items must be objects")
            if not item.get("claim_id"):
                return (False, "A1 thesis payload claim_decisions item missing claim_id")
            if not item.get("operator_mark"):
                return (False, "A1 thesis payload claim_decisions item missing operator_mark")
            operator_mark = item.get("operator_mark")
            if operator_mark not in ALLOWED_A1_OPERATOR_MARKS:
                allowed = "/".join(sorted(ALLOWED_A1_OPERATOR_MARKS))
                return (
                    False,
                    f"A1 thesis payload claim_decisions item has invalid operator_mark "
                    f"{operator_mark!r}; must be one of {allowed}",
                )
    if command_key == "k2bi-run-bear-case":
        bear_input = adapter_payload.get("bear_input")
        if not isinstance(bear_input, dict) or not bear_input:
            return (False, "A1 bear payload missing bear_input object")
    if command_key in {"k2bi-write-strategy-spec", "k2bi-author-strategy-to-repo"}:
        # Fail-fast shape ONLY -- the K2Bi StrategySpecDecision validators are the
        # real gate; the runner enforces symbol == decision.symbol == order.ticker
        # and nested forward-guidance types (Checkpoint-1 C1/C4) for BOTH carriers.
        decision = adapter_payload.get("decision")
        if not isinstance(decision, dict) or not decision.get("slug"):
            label = "A3 author" if command_key == "k2bi-author-strategy-to-repo" else "A2 strategy"
            return (False, f"{label} payload missing decision.slug")
        if not decision.get("symbol"):
            label = "A3 author" if command_key == "k2bi-author-strategy-to-repo" else "A2 strategy"
            return (False, f"{label} payload missing decision.symbol")
        order = decision.get("order")
        if not isinstance(order, dict) or not order.get("ticker"):
            label = "A3 author" if command_key == "k2bi-author-strategy-to-repo" else "A2 strategy"
            return (False, f"{label} payload missing decision.order.ticker")
    if command_key == "k2bi-run-backtest":
        slug = adapter_payload.get("slug")
        if not isinstance(slug, str) or not slug.strip():
            return (False, "A2 backtest payload missing slug")
    return (True, "")


def _parse_iso_dt(value: str | None):
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _preflight_a1_verified_thesis_artifact(payload: dict) -> tuple[bool, str]:
    if payload.get("thesis_artifact_drift_detected_at"):
        return (
            False,
            "A1 bear dispatch blocked: thesis artifact drift detected; "
            "run clear-thesis-artifact or force-verify-thesis-artifact",
        )
    if payload.get("thesis_artifact_verified") is not True:
        return (False, "A1 bear dispatch requires thesis_artifact_verified=true")
    raw_path = payload.get("thesis_path") or payload.get("thesis_artifact_path")
    if not raw_path:
        return (False, "A1 bear dispatch requires thesis artifact path")
    path = Path(str(raw_path)).expanduser()
    if not path.is_file():
        return (False, f"A1 bear dispatch thesis artifact missing or not a file: {path}")
    if path.stat().st_size == 0:
        return (False, f"A1 bear dispatch thesis artifact is empty: {path}")
    expected_sha = payload.get("thesis_artifact_sha256")
    if expected_sha:
        actual_sha = _sha256_file(path)
        if expected_sha != actual_sha:
            return (False, f"A1 bear dispatch thesis artifact sha256 mismatch: {path}")
        return (True, "")
    dispatch_dt = _parse_iso_dt(payload.get("thesis_dispatch_started_at"))
    if dispatch_dt is None:
        return (
            False,
            "A1 bear dispatch requires thesis_artifact_sha256 or thesis_dispatch_started_at",
        )
    artifact_dt = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    if artifact_dt < dispatch_dt:
        return (False, f"A1 bear dispatch thesis artifact older than dispatch: {path}")
    return (True, "")


def _preflight_a1_vault_root_matches_profile(payload: dict) -> tuple[bool, str]:
    # Validate EVERY root key present against the profile K2Bi vault: thesis/bear
    # carry `vault_root`, A2 strategy carries `repo_root` (the base under which
    # wiki/strategies/ lives -- the K2Bi vault), A2 backtest carries `vault_root`.
    # None may point anywhere but the profile K2Bi vault (Checkpoint-1 C2). This is
    # the inline-payload half; the runner's _resolve_allowed_k2bi_root is the
    # carrier-uniform gate for payload_path-carried payloads.
    profile_root = Path(k2bi_vault()).expanduser().resolve(strict=False)
    for key in ("vault_root", "k2bi_vault_root", "repo_root"):
        explicit_root = payload.get(key)
        if not explicit_root:
            continue
        payload_root = Path(str(explicit_root)).expanduser().resolve(strict=False)
        if payload_root != profile_root:
            return (
                False,
                f"A1 payload {key} {payload_root} does not match profile K2Bi vault "
                f"{profile_root}",
            )
    return (True, "")


def _preflight_a3_repo_root_matches_profile(payload: dict) -> tuple[bool, str]:
    profile_root = Path(k2bi_repo()).expanduser().resolve(strict=False)
    explicit_root = payload.get("repo_root")
    if not explicit_root:
        return (False, "A3 author payload missing repo_root")
    payload_root = Path(str(explicit_root)).expanduser().resolve(strict=False)
    if payload_root != profile_root:
        return (
            False,
            f"A3 payload repo_root {payload_root} does not match profile K2Bi repo {profile_root}",
        )
    return (True, "")


def _preflight_a1_symbol_matches_entity(task, payload: dict) -> tuple[bool, str]:
    command_key = task.get("command_key", "")
    # A1 ticker-scoped commands must prove the symbol is canonical. The legacy
    # LRCX smoke command is hard-coded compatibility coverage for Ship-1a and
    # intentionally remains outside this A1 chain gate; the legacy narrative
    # command is retired above unless an explicit operator override enables it.
    if command_key == "k2bi-smoke-enrich-lrcx":
        return (True, "")
    if command_key not in {
        "k2bi-screen-enrich",
        "k2bi-verify-and-generate-thesis",
        "k2bi-run-bear-case",
        "k2bi-write-strategy-spec",
        "k2bi-run-backtest",
        "k2bi-author-strategy-to-repo",
        "k2bi-run-full-ship",
    }:
        return (True, "")
    symbol = _payload_symbol(payload)
    if symbol is None:
        return (False, f"{command_key} payload missing or invalid symbol")
    entity = str(task.get("entity_key") or "").strip().upper()
    if not entity or not SYMBOL_RE.match(entity):
        return (False, f"{command_key} entity_key missing or invalid")
    if entity != symbol:
        return (False, f"payload symbol {symbol} does not match entity_key {entity}")
    return _canonical_symbol_known(symbol)


def _strategy_slug_from_path(path: Path) -> str | None:
    name = path.name
    if not (name.startswith("strategy_") and name.endswith(".md")):
        return None
    slug = name[len("strategy_"):-len(".md")]
    return slug or None


def _strategy_path_under_repo(path_value) -> tuple[Path | None, str]:
    if not path_value:
        return (None, "A3 ship payload missing strategy_path")
    repo = Path(k2bi_repo()).expanduser().resolve(strict=False)
    strategies_dir = (repo / "wiki" / "strategies").resolve(strict=False)
    path = Path(str(path_value)).expanduser().resolve(strict=False)
    try:
        under = os.path.commonpath([str(path), str(strategies_dir)]) == str(strategies_dir)
    except ValueError:
        under = False
    if not under:
        return (None, f"A3 strategy_path {path} is outside K2Bi repo strategies dir {strategies_dir}")
    if _strategy_slug_from_path(path) is None:
        return (None, f"A3 strategy_path must be strategy_<slug>.md under wiki/strategies: {path}")
    return (path, "")


def _limits_slug_from_proposal_path(path: Path) -> str | None:
    stem = path.stem
    m = re.match(r"^\d{4}-\d{2}-\d{2}_limits-proposal_(.+)$", stem)
    slug = m.group(1) if m else stem
    return slug or None


def _limits_token(
    slug: str,
    proposal_sha: str,
    config_sha: str,
    approved_at: str,
    lease: str,
) -> str:
    return f"APPROVE_LIMITS:{slug}:{proposal_sha}:{config_sha}:{approved_at}:{lease}"


def _proposal_path_under_repo(path_value) -> tuple[Path | None, str]:
    if not path_value:
        return (None, "A4 limits payload missing proposal_path")
    repo = Path(k2bi_repo()).expanduser().resolve(strict=False)
    approvals_dir = (repo / "review" / "strategy-approvals").resolve(strict=False)
    path = Path(str(path_value)).expanduser().resolve(strict=False)
    try:
        under = os.path.commonpath([str(path), str(approvals_dir)]) == str(approvals_dir)
    except ValueError:
        under = False
    if not under:
        return (
            None,
            f"A4 proposal_path {path} is outside K2Bi repo strategy-approvals dir {approvals_dir}",
        )
    if path.suffix != ".md" or "_limits-proposal_" not in path.stem:
        return (
            None,
            f"A4 proposal_path must be *_limits-proposal_*.md under review/strategy-approvals: {path}",
        )
    if _limits_slug_from_proposal_path(path) is None:
        return (None, f"A4 proposal_path has no derivable limits slug: {path}")
    return (path, "")


def _status_line_paths(raw_line: str) -> list[str]:
    path_text = raw_line[3:] if len(raw_line) >= 3 else ""
    if " -> " in path_text:
        return path_text.split(" -> ", 1)
    return [path_text]


def _status_line_is_allowed_a3_target(raw_line: str, rel_path: str) -> bool:
    if len(raw_line) < 4:
        return False
    status = raw_line[:2]
    if _status_line_paths(raw_line) != [rel_path]:
        return False
    if "U" in status:
        return False
    return status in {"??", " M", "M ", "MM", "A ", "AM"}


def _git_tree_clean_except_target(repo: Path, target: Path | None = None) -> tuple[bool, str]:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), "status", "--short", "--untracked-files=all"],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except subprocess.TimeoutExpired:
        return (False, "K2Bi git preflight timed out")
    except FileNotFoundError:
        return (False, "git not found")
    if result.returncode != 0:
        return (False, f"K2Bi git preflight failed: {(result.stderr or '')[:200]}")
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    if target is None:
        if lines:
            return (False, f"K2Bi git tree dirty: {'; '.join(lines[:5])[:200]}")
        return (True, "")
    try:
        rel_path = target.resolve(strict=False).relative_to(repo.resolve(strict=False)).as_posix()
    except ValueError:
        return (False, f"A3 strategy_path {target} is outside K2Bi repo {repo}")
    dirty = [line for line in lines if not _status_line_is_allowed_a3_target(line, rel_path)]
    if dirty:
        return (False, f"K2Bi git tree dirty outside A3 target: {'; '.join(dirty[:5])[:200]}")
    return (True, "")


def _git_diff_quiet(repo: Path, *, cached: bool, rel_path: str) -> tuple[bool | None, str]:
    cmd = ["git", "-C", str(repo), "diff"]
    if cached:
        cmd.append("--cached")
    cmd.extend(["--quiet", "--", rel_path])
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except subprocess.TimeoutExpired:
        return (None, "K2Bi git preflight timed out")
    except FileNotFoundError:
        return (None, "git not found")
    if result.returncode == 0:
        return (True, "")
    if result.returncode == 1:
        return (False, "")
    return (None, f"K2Bi git diff preflight failed: {(result.stderr or '')[:200]}")


def _git_tree_clean_for_limits_apply(repo: Path, proposal: Path, config: Path) -> tuple[bool, str]:
    try:
        repo_resolved = repo.resolve(strict=False)
        proposal_rel = proposal.resolve(strict=False).relative_to(repo_resolved).as_posix()
        config_rel = config.resolve(strict=False).relative_to(repo_resolved).as_posix()
    except ValueError:
        return (False, "A4 proposal/config path is outside K2Bi repo")

    for cached, label in ((True, "staged"), (False, "unstaged")):
        quiet, reason = _git_diff_quiet(repo, cached=cached, rel_path=config_rel)
        if quiet is None:
            return (False, reason)
        if quiet is False:
            return (False, f"A4 config dirty: {label} change present for {config_rel}")

    for cached, label in ((True, "staged"), (False, "tracked")):
        quiet, reason = _git_diff_quiet(repo, cached=cached, rel_path=proposal_rel)
        if quiet is None:
            return (False, reason)
        if quiet is False:
            return (False, f"A4 proposal dirty: {label} change present for {proposal_rel}")

    try:
        result = subprocess.run(
            ["git", "-C", str(repo), "status", "--short", "--untracked-files=all"],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except subprocess.TimeoutExpired:
        return (False, "K2Bi git preflight timed out")
    except FileNotFoundError:
        return (False, "git not found")
    if result.returncode != 0:
        return (False, f"K2Bi git preflight failed: {(result.stderr or '')[:200]}")
    dirty = []
    for line in result.stdout.splitlines():
        if not line.strip():
            continue
        status = line[:2]
        paths = _status_line_paths(line)
        if status == "??" and paths == [proposal_rel]:
            continue
        dirty.append(line)
    if dirty:
        return (False, f"K2Bi git tree dirty for A4 limits apply: {'; '.join(dirty[:5])[:200]}")
    return (True, "")


def _approval_payload(payload: dict) -> tuple[dict | None, str]:
    approval = payload.get("approval")
    if not isinstance(approval, dict):
        return (None, "A3 ship payload approval must be an object")
    for key in ("final_approval_token", "approved_by", "approved_at", "ship_lease_id"):
        value = approval.get(key)
        if not isinstance(value, str) or not value.strip():
            return (None, f"A3 ship payload approval.{key} must be a non-empty string")
    return (approval, "")


def _limits_approval_payload(payload: dict) -> tuple[dict | None, str]:
    approval = payload.get("approval")
    if not isinstance(approval, dict):
        return (None, "A4 limits payload approval must be an object")
    for key in ("final_approval_token", "approved_by", "approved_at", "apply_lease_id"):
        value = approval.get(key)
        if not isinstance(value, str) or not value.strip():
            return (None, f"A4 limits payload approval.{key} must be a non-empty string")
    return (approval, "")


def _markdown_yaml_block_after_heading(text: str, heading: str) -> tuple[dict | None, str]:
    pattern = re.compile(
        rf"(?ims)^##\s+{re.escape(heading)}\s*$.*?^```yaml\s*$\n(.*?)^```\s*$"
    )
    match = pattern.search(text)
    if not match:
        return (None, f"missing ## {heading} yaml block")
    try:
        import yaml
    except ImportError:
        return (None, "yaml unavailable -- cannot parse limits proposal")
    try:
        data = yaml.safe_load(match.group(1))
    except Exception as exc:
        return (None, f"limits proposal ## {heading} block is not parseable YAML: {exc}")
    if not isinstance(data, dict):
        return (None, f"limits proposal ## {heading} block is not a mapping")
    return (data, "")


def _limits_change_from_proposal(path: Path) -> tuple[dict | None, str]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return (None, f"limits proposal unreadable: {exc}")
    return _markdown_yaml_block_after_heading(text, "Change")


def _normalized_symbols(value) -> list[str] | None:
    if not isinstance(value, list):
        return None
    normalized = sorted({str(item).strip().upper() for item in value if str(item).strip()})
    return normalized


def _preflight_a4_limits(task, payload: dict) -> tuple[bool, str]:
    vault = Path(k2bi_vault()).expanduser()
    if (vault / "System" / ".killed").exists():
        return (False, "engine kill-switch engaged (System/.killed present); limits apply refused")

    repo = Path(k2bi_repo()).expanduser().resolve(strict=False)
    validators = repo / "execution" / "validators" / "config.yaml"
    try:
        if not validators.is_file() or validators.stat().st_size == 0:
            return (False, "K2Bi validators config missing or empty; limits apply refused")
    except OSError:
        return (False, "K2Bi validators config unreadable; limits apply refused")

    proposal_path, reason = _proposal_path_under_repo(payload.get("proposal_path"))
    if proposal_path is None:
        return (False, reason)
    if not proposal_path.is_file():
        return (False, f"A4 proposal_path missing or not a file: {proposal_path}")
    if proposal_path.stat().st_size == 0:
        return (False, f"A4 proposal_path is empty: {proposal_path}")
    slug = _limits_slug_from_proposal_path(proposal_path)
    assert slug is not None

    try:
        fm = _read_frontmatter(proposal_path)
    except ValueError as exc:
        return (False, f"A4 proposal frontmatter invalid: {exc}")
    if str(fm.get("type", "")).strip() != "limits-proposal":
        return (False, "A4 proposal type must be limits-proposal")
    status = str(fm.get("status") or "").strip().lower()
    if status != "proposed":
        return (False, f"A4 proposal status must be proposed before apply; got {status!r}")
    applies_to = str(fm.get("applies-to") or "").strip()
    if applies_to != "execution/validators/config.yaml":
        return (False, "A4 proposal applies-to must be execution/validators/config.yaml")

    change, reason = _limits_change_from_proposal(proposal_path)
    if change is None:
        return (False, reason)
    rule = str(change.get("rule") or "").strip()
    change_type = str(change.get("change_type") or "").strip()
    if rule != "instrument_whitelist" or change_type != "add":
        return (
            False,
            "A4 supports only instrument_whitelist/add this increment; route others "
            "to the manual /invest-ship --approve-limits path",
        )
    if _normalized_symbols(change.get("after")) is None:
        return (False, "A4 proposal ## Change after must be a symbol list")

    approval, reason = _limits_approval_payload(payload)
    if approval is None:
        return (False, reason)
    approved_at_text = approval["approved_at"]
    try:
        approved_at = datetime.fromisoformat(approved_at_text)
    except ValueError:
        return (False, "A4 approved_at must be ISO-8601")
    if approved_at.tzinfo is None or approved_at.utcoffset() is None:
        return (False, "A4 approved_at must include an explicit timezone offset")
    if approved_at.utcoffset().total_seconds() != 0:
        return (False, "A4 approved_at must be UTC")
    lease = approval["apply_lease_id"]
    if not SHIP_LEASE_RE.match(lease):
        return (False, "A4 apply_lease_id does not match the required format")

    current_proposal_sha = _sha256_file(proposal_path)
    current_config_sha = _sha256_file(validators)
    expected_token = _limits_token(
        slug,
        current_proposal_sha,
        current_config_sha,
        approved_at_text,
        lease,
    )
    if approval["final_approval_token"] != expected_token:
        return (False, "A4 approval token does not bind current proposal/config sha")

    return _git_tree_clean_for_limits_apply(repo, proposal_path, validators)


def k2bi_instrument_whitelist() -> tuple[list[str] | None, str]:
    """READ-ONLY read of the K2Bi engine instrument whitelist (allowed-stocks list).

    Returns (symbols, "") on success or (None, reason) when it cannot be read --
    callers MUST fail CLOSED on (None, reason). The whitelist is an operator-owned
    validator (`execution/validators/config.yaml`, `instrument_whitelist.symbols`):
    the engine refuses any order for a ticker not on it. A3 only READS it (to surface
    "this ticker is not approved for trading yet" EARLY, instead of as a last-step ship
    rollback -- Keith's workflow finding, 2026-06-08). A3 NEVER edits it; adding a ticker
    goes through `/invest-propose-limits` + operator approval.
    """
    cfg = Path(k2bi_repo()).expanduser() / "execution" / "validators" / "config.yaml"
    try:
        import yaml
    except ImportError:
        return (None, "yaml unavailable -- cannot read the K2Bi instrument whitelist")
    try:
        data = yaml.safe_load(cfg.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        return (None, f"K2Bi instrument whitelist unreadable ({cfg}): {exc}")
    if not isinstance(data, dict):
        return (None, "K2Bi validators config is not a mapping")
    block = data.get("instrument_whitelist")
    if not isinstance(block, dict):
        return (None, "K2Bi validators config has no instrument_whitelist block")
    symbols = block.get("symbols")
    if symbols is None:
        symbols = []
    if not isinstance(symbols, list):
        return (None, "instrument_whitelist.symbols is not a list")
    return ([str(s).strip().upper() for s in symbols], "")


def ticker_whitelisted(symbol: str) -> tuple[bool, str]:
    """True iff `symbol` is on the K2Bi engine allowed-stocks list. Fail-closed: if the
    whitelist cannot be read, returns (False, reason). The refusal message routes the
    operator to the sanctioned add path (`/invest-propose-limits`)."""
    clean = str(symbol or "").strip().upper()
    if not clean:
        return (False, "ticker missing for whitelist check")
    symbols, reason = k2bi_instrument_whitelist()
    if symbols is None:
        return (False, reason)
    if clean not in symbols:
        allowed = ", ".join(symbols) if symbols else "(none -- engine refuses all)"
        return (
            False,
            f"ticker {clean} is not on the K2Bi engine allowed-list (instrument_whitelist); "
            f"approve it via /invest-propose-limits + operator approval before shipping. "
            f"Currently allowed: {allowed}",
        )
    return (True, "")


def _preflight_a3_capital(task, payload: dict) -> tuple[bool, str]:
    vault = Path(k2bi_vault()).expanduser()
    if (vault / "System" / ".killed").exists():
        return (False, "engine kill-switch engaged (System/.killed present); ship refused")

    repo = Path(k2bi_repo()).expanduser().resolve(strict=False)
    validators = repo / "execution" / "validators" / "config.yaml"
    try:
        if not validators.is_file() or validators.stat().st_size == 0:
            return (False, "K2Bi validators config missing or empty; ship refused")
    except OSError:
        return (False, "K2Bi validators config unreadable; ship refused")

    strategy_path, reason = _strategy_path_under_repo(payload.get("strategy_path"))
    if strategy_path is None:
        return (False, reason)
    if not strategy_path.is_file():
        return (False, f"A3 strategy_path missing or not a file: {strategy_path}")
    slug = _strategy_slug_from_path(strategy_path)
    assert slug is not None

    approval, reason = _approval_payload(payload)
    if approval is None:
        return (False, reason)
    approved_at_text = approval["approved_at"]
    try:
        approved_at = datetime.fromisoformat(approved_at_text)
    except ValueError:
        return (False, "A3 approved_at must be ISO-8601")
    if approved_at.tzinfo is None or approved_at.utcoffset() is None:
        return (False, "A3 approved_at must include an explicit timezone offset")
    if approved_at.utcoffset().total_seconds() != 0:
        return (False, "A3 approved_at must be UTC")
    lease = approval["ship_lease_id"]
    if not SHIP_LEASE_RE.match(lease):
        return (False, "A3 ship_lease_id does not match the required format")
    current_sha = _sha256_file(strategy_path)
    expected_token = _ship_token(slug, current_sha, approved_at_text, lease)
    if approval["final_approval_token"] != expected_token:
        return (False, "A3 approval token does not bind current strategy sha")

    fm = _read_frontmatter(strategy_path)
    status = str(fm.get("status") or "").strip().lower()
    if status != "proposed":
        return (False, f"A3 strategy status must be proposed before ship; got {status!r}")
    entity = str(task.get("entity_key") or "").strip().upper()
    order = fm.get("order")
    order_ticker = str(order.get("ticker", "")).strip().upper() if isinstance(order, dict) else ""
    ticker = order_ticker or str(fm.get("ticker", "")).strip().upper()
    if entity and ticker != entity:
        return (False, f"A3 strategy ticker {ticker!r} does not match entity_key {entity!r}")

    # Allowed-list (instrument whitelist) pre-check (Keith's workflow finding, 2026-06-08;
    # Kimi A3.1 review F2: check the ACTUAL shipped ticker -- the strategy's order.ticker,
    # verified == entity above -- not just the task entity_key, so a strategy whose order
    # trades a different/non-whitelisted ticker cannot slip through). The engine rejects any
    # order for a non-whitelisted ticker, so catch it HERE (before run_full_ship) + route to
    # the operator approval path instead of a last-step rollback. READ-ONLY; A3 never edits it.
    ok, reason = ticker_whitelisted(ticker)
    if not ok:
        return (False, reason)

    return _git_tree_clean_except_target(repo, strategy_path)


def _ship_token(slug: str, sha: str, approved_at: str, lease: str) -> str:
    return f"APPROVE_STRATEGY:{slug}:{sha}:{approved_at}:{lease}"


def preflight_k2bi(task) -> tuple[bool, str]:
    command_key = task.get("command_key", "")
    payload = _task_payload(task)

    # 0. k2bi-narrative dispatch is RETIRED (checked BEFORE the generic allowlist
    # check so the message is clear, not "not allowlisted"). Chat 2 is now
    # agent-native (waiting_for_agent_theme + verify-theme; the agent generates
    # candidates + validates/repairs citations in-session -- no Kimi). resolve_command
    # ALSO gates this at the worker boundary; this is the clear-message preflight half.
    if command_key == "k2bi-narrative" and os.environ.get(
        "K2B_ORCH_ALLOW_LEGACY_NARRATIVE"
    ) != "1":
        return (
            False,
            "k2bi-narrative dispatch is RETIRED (Kimi path); use the agent-native "
            "Chat-2 path (waiting_for_agent_theme + verify-theme). Set "
            "K2B_ORCH_ALLOW_LEGACY_NARRATIVE=1 only to force the legacy Kimi pipeline.",
        )

    # 1. allowlist check
    if resolve_command("k2bi", command_key, payload) is None:
        return (False, f"command_key not allowlisted: {command_key}")

    ok, reason = _preflight_a1_symbol_matches_entity(task, payload)
    if not ok:
        return (False, reason)

    # 2. workspace exists
    workspace = resolve_workspace("k2bi")
    if not os.path.isdir(workspace):
        print(f"K2Bi repo path missing: {workspace}", file=os.sys.stderr)
        return (False, "K2Bi repo path missing")

    # 3. vault exists
    if not os.path.isdir(k2bi_vault()):
        print(f"K2Bi vault path missing: {k2bi_vault()}", file=os.sys.stderr)
        return (False, "K2Bi vault path missing")

    # 4. worker lock (with stale-lock detection)
    worker_lock = "/tmp/k2b-orch-k2bi-worker.lock"
    if os.path.exists(worker_lock):
        if _worker_lock_is_stale(worker_lock):
            try:
                os.unlink(worker_lock)
            except OSError:
                pass
        else:
            return (False, "active K2Bi worker lock present")

    # 5. human lock
    prof = get_profile("k2bi")
    human_lock = prof.get("human_lock") if prof else None
    if human_lock and os.path.exists(human_lock):
        return (False, "active K2Bi human-session lock present")

    # Narrative lane: run P0-P5, skip git status
    if command_key == "k2bi-narrative":
        return _preflight_narrative(task)

    if command_key in {"k2bi-verify-and-generate-thesis", "k2bi-run-bear-case"}:
        ok, reason = _preflight_a1_vault_root_matches_profile(payload)
        if not ok:
            return (False, reason)
        ok, reason = _preflight_a1_verify_thesis(payload)
        if not ok:
            return (False, reason)
        ok, reason = _preflight_a1_adapter_payload_shape(command_key, payload)
        if not ok:
            return (False, reason)
        if command_key == "k2bi-run-bear-case":
            ok, reason = _preflight_a1_verified_thesis_artifact(payload)
            if not ok:
                return (False, reason)

    # A2 (strategy half): no promoted precondition (that is thesis-specific) and no
    # capital path. Chain-validity ("thesis approved before strategy") is owned by
    # the resume oracle, not this child preflight -- keeping the preflight narrow
    # avoids re-introducing an over-strict gate like the A1.1 bug. The strategy file
    # writes to the K2Bi VAULT (a separate non-git dir), so the clean-tree git
    # preflight on ~/Projects/K2Bi below still applies and stays satisfied.
    if command_key in {"k2bi-write-strategy-spec", "k2bi-run-backtest"}:
        ok, reason = _preflight_a1_vault_root_matches_profile(payload)
        if not ok:
            return (False, reason)
        ok, reason = _preflight_a1_adapter_payload_shape(command_key, payload)
        if not ok:
            return (False, reason)

    # A3 author-to-repo writes the proposed strategy into the git-tracked K2Bi repo,
    # not the vault. Do not send it through the A2 vault-root check.
    if command_key == "k2bi-author-strategy-to-repo":
        ok, reason = _preflight_a3_repo_root_matches_profile(payload)
        if not ok:
            return (False, reason)
        ok, reason = _preflight_a1_adapter_payload_shape(command_key, payload)
        if not ok:
            return (False, reason)

    # A3 full ship has its own capital preflight and relaxed tree rule: clean except
    # for the just-authored target strategy file.
    if command_key == "k2bi-run-full-ship":
        return _preflight_a3_capital(task, payload)

    if command_key == "k2bi-apply-limits":
        return _preflight_a4_limits(task, payload)

    # 6. git status (existing behavior for non-narrative commands)
    ok, reason = _git_tree_clean_except_target(Path(workspace).expanduser().resolve(strict=False))
    if not ok:
        return (False, reason)

    return (True, "")
