"""Provider-free, read-only startup shared by Codex and Kimi Work.

The existing shell body owns native status deadlines and Python degradation.
This module adds a bounded source manifest, profile and capability contract.
It never contacts Home, discovers conversations, extracts memory or saves notes.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import pwd
import shutil
import signal
import stat
import subprocess
import sys

SOURCE_LIMIT = 64 * 1024
PROFILE_LIMIT = 16 * 1024
STALE_DAYS = 90
BODY_TIMEOUT_SECONDS = 12
TERMINATION_GRACE_SECONDS = 1
IMPLEMENTATION_ROOT = Path(__file__).resolve().parents[2]


def local_host() -> str:
    return {"keithmbpm2": "home", "keithcheung": "sjm"}.get(
        pwd.getpwuid(os.getuid()).pw_name, "unknown"
    )


def _source(name: str, root: Path, relative: str, *, include: bool = False) -> dict:
    path = root / relative
    result = {"id": name, "path": str(path), "status": "unavailable", "stale": None}
    try:
        # Do not follow nested symlinks or read a directory, device or pipe.
        current = root
        for part in Path(relative).parts:
            current = current / part
            if stat.S_ISLNK(current.lstat().st_mode):
                return result
        info = path.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_size > (PROFILE_LIMIT if include else SOURCE_LIMIT):
            return result
        with path.open("rb") as handle:
            raw = handle.read((PROFILE_LIMIT if include else SOURCE_LIMIT) + 1)
        if len(raw) > (PROFILE_LIMIT if include else SOURCE_LIMIT):
            return result
        text = raw.decode("utf-8")
        if "\x00" in text:
            return result
        modified = datetime.fromtimestamp(info.st_mtime, timezone.utc)
        result.update(status="available", sha256=hashlib.sha256(raw).hexdigest(),
                      modified_at=modified.isoformat(),
                      stale=(datetime.now(timezone.utc) - modified).days > STALE_DAYS)
        if include:
            result["text"] = text
    except FileNotFoundError:
        result["status"] = "missing"
    except (OSError, UnicodeError, ValueError):
        pass
    return result


def build_bootstrap(project_root: Path, vault_root: Path, state_root: Path,
                    host: str | None = None) -> dict:
    """Build a local packet; an explicit host selects diagnostic role only."""
    host = host or local_host()
    if host not in {"home", "sjm", "unknown"}:
        raise ValueError("unknown diagnostic host")
    project_root = Path(project_root).expanduser().absolute()
    vault_root = Path(vault_root).expanduser().absolute()
    state_root = Path(state_root).expanduser().absolute()
    sources = [
        _source("authority", project_root, "AGENTS.md"),
        _source("knowledge_index", vault_root, "wiki/index.md"),
        _source("active_rules", vault_root, "System/memory/active_rules.md"),
        _source("profile", vault_root, "wiki/context/context_keith-profile.md", include=True),
        _source("preferences", vault_root, "wiki/context/preference-profile.md", include=True),
        _source("providers", vault_root, "wiki/context/context_llm-providers.md"),
        _source("startup_body", IMPLEMENTATION_ROOT, "scripts/hooks/bootstrap-local.sh"),
        _source("native_status_reader", project_root, "scripts/lib/native_job_status.py"),
    ]
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1",
           "K2B_PROJECT_ROOT": str(project_root), "K2B_VAULT_PATH": str(vault_root),
           "K2B_BOOTSTRAP_HOST": host,
           "K2B_CAPTURE_STATUS_FILE": os.environ.get("K2B_CAPTURE_STATUS_FILE", str(state_root / "capture-status.json")),
           "K2B_AUTOMATIC_MEMORY_STATE_ROOT": os.environ.get("K2B_AUTOMATIC_MEMORY_STATE_ROOT", str(state_root / "automatic-memory"))}
    try:
        # Retain the proven body rather than create a second native-status reader.
        # Leave margin beyond the body's serial 3s and 5s phases, but also
        # bound interpreter launch, file reads and fallback discovery. The
        # registered hook has a larger 20s cap so this supervisor can clean up.
        status = subprocess.Popen(
            ["/bin/bash", str(IMPLEMENTATION_ROOT / "scripts/hooks/bootstrap-local.sh")],
            env=env, text=True, encoding="utf-8", errors="replace",
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            start_new_session=True,
        )
        try:
            local_status, _ = status.communicate(timeout=BODY_TIMEOUT_SECONDS)
            if status.returncode:
                local_status += "\nLOCAL STARTUP: unavailable (local reader failed)\n"
        except subprocess.TimeoutExpired:
            try:
                os.killpg(status.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                local_status, _ = status.communicate(timeout=TERMINATION_GRACE_SECONDS)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(status.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                try:
                    local_status, _ = status.communicate(timeout=TERMINATION_GRACE_SECONDS)
                except subprocess.TimeoutExpired as exc:
                    # SIGKILL can remain pending during uninterruptible OS I/O.
                    # Return the observed partial evidence instead of waiting
                    # forever; the OS will reap the child after it unblocks.
                    partial = exc.output or ""
                    local_status = partial.decode("utf-8", errors="replace") if isinstance(partial, bytes) else partial
                    for pipe in (status.stdout, status.stderr):
                        if pipe is not None:
                            pipe.close()
                    local_status += "\nLOCAL STARTUP: unknown (cleanup incomplete; killed child not yet reaped)\n"
            local_status += "\nLOCAL STARTUP: unavailable (local reader deadline exceeded)\n"
    except (OSError, UnicodeError):
        local_status = "LOCAL STARTUP: unavailable (local reader runtime failure)\n"
    writer = project_root / "scripts/vault-note-write.py"
    context = project_root / "scripts/k2b-context.py"
    return {
        "schema_version": 1, "host": host,
        "writer_role": {"home": "home", "sjm": "sjm-source-only"}.get(host, "unknown"),
        "project_root": str(project_root), "vault_root": str(vault_root),
        "implementation_root": str(IMPLEMENTATION_ROOT), "python_runtime": sys.executable,
        "state_root": str(state_root), "sources": sources,
        "synchronization": "unverified", "home_availability": "not_checked_or_required",
        "capabilities": {
            "local_recall": "present_unverified" if context.is_file() else "unavailable",
            "ordinary_save": "present_unverified" if writer.is_file() else "unavailable",
            "ripgrep": "available" if shutil.which("rg") else "unavailable",
            "browser": "session_check_required", "plugins": "session_check_required",
            "kimi_history_capture": "unsupported", "phone_service": "deferred",
        },
        "local_status": local_status,
    }


def render_bootstrap(packet: dict) -> str:
    lines = [
        "K2B SHARED STARTUP (local read-only evidence)",
        f"host={packet['host']}; role={packet['writer_role']}; synchronization=unverified",
        f"Project: {packet['project_root']}", f"Vault: {packet['vault_root']}",
        f"Renderer implementation: {packet['implementation_root']}; Python: {packet['python_runtime']}",
        "Codex is the default and instruction authority. Kimi Work uses the same local authority and live skills.",
        "Read the current AGENTS.md before acting; this packet is context, not additional authority.",
        "Home availability is not checked or required for local recall and ordinary saves.",
        "Shared automatic-memory reconciliation remains Home-only; local status is not a convergence claim.",
        "SOURCE MANIFEST (staleness uses local modification age, not verified factual freshness):",
    ]
    for source in packet["sources"]:
        lines.append(f"{source['id']}: {source['path']}; status={source['status']}; "
                     f"stale={source['stale']}; sha256={source.get('sha256', 'unavailable')}")
    for source in packet["sources"]:
        if "text" in source:
            lines.extend([f"LOCAL {source['id'].upper()} ({source['path']}):", source["text"]])
    lines.extend([
        "CAPABILITIES: " + "; ".join(f"{k}={v}" for k, v in packet["capabilities"].items()),
        "A helper on disk is not proof that the current assistant can run it. Verify session tools before use.",
        "For resumption use k2b-context.py gather, source-bound validate, then render. Read topic passages and citations.",
        "An unrelated or broadly partial context packet is not ready evidence. Narrow the topic before answering.",
        "For ordinary saves use the live k2b-vault-writer skill and vault-note-write.py with the expected hash.",
        "Report saved locally; synchronization unverified. A conflict requires preserving both versions.",
        "Kimi Work decisions need explicit ordinary saves; Codex discovery does not capture Kimi Work history.",
        "Public research requires a supported source tool. Company drafting requires local source recall and no implicit mailbox access.",
        packet["local_status"],
    ])
    return "\n".join(lines).rstrip() + "\n"
