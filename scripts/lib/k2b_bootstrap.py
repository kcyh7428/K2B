"""Provider-free, read-only startup shared by Codex and Kimi Work.

The existing shell body owns native status deadlines and Python degradation.
This module adds a bounded source manifest, profile and capability contract.
It never contacts Home, discovers conversations, extracts memory or saves notes.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
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


def is_local_read_copy(root: Path) -> bool:
    target = str(Path(root).expanduser().resolve()).casefold()
    approved = str((Path.home() / "Projects/K2B-Vault-Offline/SynologyDrive").resolve()).casefold()
    return target == approved or target.startswith(approved + "/")


def is_local_family_source(path: Path) -> bool:
    """Check a candidate already resolved by the existing source guard."""
    target = str(path).casefold()
    excluded = str((Path.home() / "Projects/K2B-Vault-Offline/SynologyDrive"
                    / "wiki/reference/minnie-family-memory").resolve()).casefold()
    return target == excluded or target.startswith(excluded + "/")


def default_read_vault() -> Path:
    """Select the host's read preference without changing the NAS writer root."""
    setting = Path.home() / ".local/state/k2b/read-vault.json"
    if not setting.exists() and not setting.is_symlink():
        root = Path(os.environ.get("K2B_VAULT_PATH", str(Path.home() / "Projects/K2B-Vault"))).expanduser()
        if is_local_read_copy(root):
            raise ValueError("local copy requires read-vault.json activation or explicit --vault diagnostics")
        return root
    return _validated_read_setting(setting)[0]


def _validated_read_setting(setting: Path) -> tuple[Path, str]:
    with os.fdopen(os.open(setting, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), "rb") as handle:
        info = os.fstat(handle.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_size > 4096
                or info.st_uid != os.getuid() or info.st_mode & 0o077):
            raise ValueError("read-vault setting must be a private regular file")
        raw = handle.read(4097)
        if len(raw) > 4096:
            raise ValueError("read-vault setting exceeds its size limit")
        value = json.loads(raw)
    if set(value) != {"root", "verified_utc"} or local_host() != "sjm":
        raise ValueError("read-vault setting is restricted to SJM")
    root = Path(value["root"])
    if (root != Path.home() / "Projects/K2B-Vault-Offline/SynologyDrive"
            or root.is_symlink() or root.resolve(strict=True) != root):
        raise ValueError("read-vault root does not match the approved local copy")
    if not root.is_dir():
        raise ValueError("local read copy is unavailable")
    verified = datetime.strptime(value["verified_utc"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    if verified.strftime("%Y-%m-%dT%H:%M:%SZ") != value["verified_utc"] or verified > datetime.now(timezone.utc):
        raise ValueError("read-vault comparison time is in the future")
    return root, value["verified_utc"]


def read_copy_status(root: Path) -> dict | None:
    if not is_local_read_copy(root):
        return None
    setting = Path.home() / ".local/state/k2b/read-vault.json"
    compared = None
    invalid = False
    try:
        if setting.exists() or setting.is_symlink():
            configured, observed = _validated_read_setting(setting)
            if str(configured.resolve()).casefold() == str(Path(root).resolve()).casefold():
                compared = observed
    except (OSError, ValueError, TypeError, KeyError):
        invalid = True
    age = recreated = running = None
    if compared:
        when = datetime.strptime(compared, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        age = max(0, int((datetime.now(timezone.utc) - when).total_seconds() / 86400))
        try:
            birth = getattr(Path(root).stat(), "st_birthtime", None)
            recreated = birth > when.timestamp() + 1 if birth is not None else None
        except OSError:
            pass
    try:
        result = subprocess.run(["/bin/ps", "-A", "-ww", "-o", "comm="], capture_output=True,
                                text=True, timeout=1, check=True)
        running = any(Path(line.strip()).name == "cloud-drive-daemon" for line in result.stdout.splitlines())
    except (OSError, subprocess.SubprocessError):
        pass
    return {"kind": "synology_local_copy", "last_nas_comparison_utc": compared,
            "freshness": "may_lag_nas", "setting_invalid": invalid,
            "comparison_age_days": age, "copy_created_after_comparison": recreated,
            "sync_client_running": running}


def read_copy_health_notice(status: dict) -> str:
    running = status.get("sync_client_running")
    client = "running" if running is True else "not running" if running is False else "unknown"
    text = f"Synology client process: {client}; sync freshness unverified."
    if status.get("comparison_age_days") is not None:
        text += f" NAS comparison age: {status['comparison_age_days']} days."
    if status.get("copy_created_after_comparison"):
        text += " Copy folder was created after that comparison; compare it again."
    return text


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
        _source("central_vision", vault_root, "wiki/concepts/concept_k2b-agentic-os-vision.md"),
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
        "read_root": str(vault_root),
        "write_vault_root": str(Path(os.environ.get("K2B_VAULT_PATH") or os.environ.get("K2B_VAULT_ROOT")
                                    or str(Path.home() / "Projects/K2B-Vault")).expanduser().absolute()),
        "write_vault_root_status": "configured_unverified",
        "implementation_root": str(IMPLEMENTATION_ROOT), "python_runtime": sys.executable,
        "state_root": str(state_root), "sources": sources,
        "read_copy": read_copy_status(vault_root),
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
        f"Configured save target (unverified): {packet['write_vault_root']}",
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
    if packet.get("read_copy"):
        lines.append(read_copy_health_notice(packet["read_copy"]))
        lines.append("Synology local read copy. Last NAS comparison UTC " +
                     str(packet["read_copy"]["last_nas_comparison_utc"]) +
                     ". Local sources may lag NAS. Saves use the approved NAS writer root.")
        if packet["read_copy"]["setting_invalid"]:
            lines.append("Configured read setting is invalid; check ~/.local/state/k2b/read-vault.json.")
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
