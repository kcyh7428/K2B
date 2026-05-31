#!/usr/bin/env python3
"""Worker-profile registry, allowlist, and K2Bi preflight for K2B orchestrator."""

import json
import os
import re
import socket
import subprocess
from pathlib import Path
from urllib.parse import urlparse


def _expand(p):
    return str(Path(p).expanduser())


def k2bi_workspace():
    return os.environ.get("K2B_ORCH_K2BI_WORKSPACE") or _expand("~/Projects/K2Bi")


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


def resolve_command(profile_name, command_key, payload=None) -> list[str] | None:
    if profile_name == "k2bi":
        cmds = k2bi_allowed_commands()
        if command_key in cmds:
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
                if payload and isinstance(payload, dict):
                    narrative = payload.get("narrative", "")
                    argv.append(f"--narrative={narrative}")
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


def preflight_k2bi(task) -> tuple[bool, str]:
    command_key = task.get("command_key", "")

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
    if resolve_command("k2bi", command_key) is None:
        return (False, f"command_key not allowlisted: {command_key}")

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

    # 6. git status (existing behavior for non-narrative commands)
    try:
        result = subprocess.run(
            ["git", "-C", workspace, "status", "--short"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if result.returncode != 0:
            err = (result.stderr or "")[:200]
            return (False, f"K2Bi git preflight failed: {err}")
        if result.stdout.strip():
            dirty = result.stdout.strip()[:200]
            return (False, f"K2Bi git tree dirty: {dirty}")
    except subprocess.TimeoutExpired:
        return (False, "K2Bi git preflight timed out")
    except FileNotFoundError:
        return (False, "git not found")

    return (True, "")
