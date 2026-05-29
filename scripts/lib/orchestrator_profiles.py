#!/usr/bin/env python3
"""Worker-profile registry, allowlist, and K2Bi preflight for K2B orchestrator."""

import os
import subprocess
from pathlib import Path


def _expand(p):
    return str(Path(p).expanduser())


def k2bi_workspace():
    return os.environ.get("K2B_ORCH_K2BI_WORKSPACE") or _expand("~/Projects/K2Bi")


def k2bi_vault():
    return os.environ.get("K2BI_VAULT_PATH") or _expand("~/Projects/K2Bi-Vault")


def k2bi_allowed_commands():
    return {
        "k2bi-smoke-enrich-lrcx": [
            "python3",
            "-m",
            "scripts.lib.invest_screen",
            "--enrich",
            "LRCX",
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


def resolve_command(profile_name, command_key) -> list[str] | None:
    if profile_name == "k2bi":
        cmds = k2bi_allowed_commands()
        if command_key in cmds:
            return list(cmds[command_key])  # copy
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


def preflight_k2bi(task) -> tuple[bool, str]:
    # 1. allowlist check first
    if resolve_command("k2bi", task.get("command_key", "")) is None:
        return (False, f"command_key not allowlisted: {task.get('command_key', '')}")

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

    # 6. git status
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
