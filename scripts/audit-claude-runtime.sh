#!/usr/bin/env bash
set -euo pipefail
AUDIT_ROOT="${K2B_AUDIT_ROOT:-$HOME}"
FIXTURE_MODE="${K2B_AUDIT_FIXTURE:-0}"
AUDIT_HOST_ROLE="${K2B_AUDIT_HOST_ROLE:-}"

exec python3 - "$AUDIT_ROOT" "$FIXTURE_MODE" "$AUDIT_HOST_ROLE" <<'PY'
import json
import os
import plistlib
import re
import shutil
import socket
import subprocess
import sys
from xml.parsers import expat
from pathlib import Path

root = Path(sys.argv[1]).expanduser().resolve()
fixture_mode = sys.argv[2] == "1"
host_role = sys.argv[3]
host = socket.gethostname()
if fixture_mode:
    scan_roots = [root]
else:
    scan_roots = [
        root / "Projects" / "K2B",
        root / ".claude",
        root / "Library" / "LaunchAgents",
        root,
    ]

findings = []
processes = []
launchd = []
pm2 = []
cron = []
packages = []
instruction_paths = []
hook_paths = []
credential_names = set()
seen_findings = set()
probe_status = {"processes": False, "launchctl": False, "pm2": False, "crontab": False}
keychain_credential_status = "out_of_scope"
filesystem_scope = {
    "scan_roots": [str(path) for path in scan_roots],
    "other_home_subdirectories": "out_of_scope",
}


def add_finding(kind, location, detail=None):
    """Store only a finding kind plus an allowed path, label, or key."""
    finding = {"kind": kind, "location": str(location)}
    if detail:
        finding["detail"] = detail
    identity = (kind, finding["location"], finding.get("detail"))
    if identity not in seen_findings:
        seen_findings.add(identity)
        findings.append(finding)


def resolve_host_identity():
    if host_role not in {"", "macbook", "mini"}:
        return None, "invalid-role"
    if host_role == "mini":
        return True, "explicit-mini"
    if host_role == "macbook":
        return False, "explicit-macbook"
    model = os.environ.get("K2B_AUDIT_HARDWARE_MODEL", "").strip() if fixture_mode else ""
    if not fixture_mode:
        executable = shutil.which("sysctl")
        if executable:
            try:
                completed = subprocess.run(
                    [executable, "-n", "hw.model"],
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                if completed.returncode == 0:
                    model = completed.stdout.strip()
            except (OSError, subprocess.SubprocessError):
                pass
    model_lower = model.lower()
    if model_lower.startswith("macmini"):
        return True, "hardware-mini"
    if model_lower.startswith("macbook"):
        return False, "hardware-macbook"
    return None, "ambiguous"


is_mini, host_identity_status = resolve_host_identity()
if host_identity_status == "invalid-role":
    add_finding("host-role-unrecognized", "K2B_AUDIT_HOST_ROLE")
elif host_identity_status == "ambiguous":
    add_finding("host-identity-unverified", "hardware-model")
add_finding("keychain-credential-enumeration-unverified", "macos-keychain", "out-of-scope")
add_finding("filesystem-scope-limited", "K2B_AUDIT_ROOT", "other-home-subdirectories-out-of-scope")


def text_from(path):
    try:
        if path.stat().st_size > 1_000_000:
            add_finding("skipped-oversized-file", path)
            return ""
        return path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""


def candidate_files():
    excluded = {".git", "node_modules", "venv", ".venv", ".cache", ".Trash"}
    seen_paths = set()
    for target in scan_roots:
        if target.is_symlink():
            kind = "skipped-symlink-directory" if target.is_dir() else "skipped-symlink-file"
            add_finding(kind, target)
            continue
        if not target.exists():
            continue
        if target.is_file():
            walk = [(str(target.parent), [], [target.name])]
        elif target == root and not fixture_mode:
            walk = [(str(root), [], [entry.name for entry in root.iterdir() if entry.is_file()])]
        else:
            walk = os.walk(target, topdown=True, followlinks=False)
        for directory, dirnames, filenames in walk:
            retained_dirs = []
            for name in dirnames:
                path = Path(directory, name)
                if path.is_symlink():
                    add_finding("skipped-symlink-directory", path)
                elif name not in excluded:
                    retained_dirs.append(name)
            dirnames[:] = retained_dirs
            for name in filenames:
                path = Path(directory, name)
                if path.is_symlink():
                    add_finding("skipped-symlink-file", path)
                    continue
                if path in seen_paths:
                    continue
                seen_paths.add(path)
                yield path


def is_env_file(path):
    name = path.name.casefold()
    return name == ".env" or name.startswith(".env.")


def looks_like_secret_name(name):
    return bool(re.search(r"(?:API_KEY|TOKEN|SECRET|PASSWORD|_KEY)$", name))


def inspect_package(path):
    try:
        payload = json.loads(text_from(path))
    except json.JSONDecodeError:
        return
    for section in ("dependencies", "devDependencies", "optionalDependencies", "peerDependencies"):
        dependencies = payload.get(section, {})
        if not isinstance(dependencies, dict):
            continue
        for key in dependencies:
            key_lower = key.lower()
            if "anthropic" in key_lower or "claude" in key_lower:
                packages.append({"path": str(path), "dependency": key})
                add_finding("anthropic-package", path, key)
            if key_lower in {"grammy", "telegraf", "node-telegram-bot-api", "telegram"} or "telegram" in key_lower:
                packages.append({"path": str(path), "dependency": key})
                add_finding("telegram-runtime", path, key)


def inspect_env_names(path):
    for line in text_from(path).splitlines():
        match = re.match(r"\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=", line)
        if not match:
            continue
        name = match.group(1)
        if looks_like_secret_name(name):
            credential_names.add(name)
        if name.startswith("OPENAI_") and looks_like_secret_name(name):
            add_finding("openai-api-credential-name", path, name)


def inspect_plist(path):
    try:
        with path.open("rb") as handle:
            payload = plistlib.load(handle)
    except (OSError, plistlib.InvalidFileException, ValueError, TypeError, AttributeError, expat.ExpatError):
        add_finding("unparseable-launchagent-plist", path)
        return
    if not isinstance(payload, dict):
        add_finding("unparseable-launchagent-plist", path)
        return
    label = payload.get("Label")
    if not isinstance(label, str) or not label:
        add_finding("unlabeled-launchagent-plist", path)
        return
    if "com.k2b-remote.app" in label or path.name == "com.k2b-remote.app.plist":
        launchd.append({"path": str(path), "label": label})
        add_finding("stale-k2b-launchagent", path, label)
    action_text = [label]
    program = payload.get("Program")
    if isinstance(program, str):
        action_text.append(program)
    program_arguments = payload.get("ProgramArguments")
    if isinstance(program_arguments, list):
        action_text.extend(value for value in program_arguments if isinstance(value, str))
    if any(re.search(r"(?:claude|anthropic|telegram)", value, re.IGNORECASE) for value in action_text):
        launchd.append({"path": str(path), "label": label})
        add_finding("claude-launch-action", path, label)


def inspect_processes():
    executable = shutil.which("ps")
    if not executable:
        return
    try:
        completed = subprocess.run(
            [executable, "-axo", "comm=,args="], check=False, capture_output=True, text=True, timeout=5
        )
    except (OSError, subprocess.SubprocessError):
        return
    if completed.returncode != 0:
        return
    commands = completed.stdout.splitlines()
    if not commands:
        return
    probe_status["processes"] = True
    for raw_command in commands:
        command = raw_command.strip()
        if not command or "\x00" in command:
            add_finding("unparseable-process-row", "ps")
            continue
        executable = command.split(maxsplit=1)[0]
        if re.search(r"(?:claude|anthropic|telegram)", command, re.IGNORECASE):
            processes.append({"executable": executable})
            kind = "telegram-process" if re.search(r"telegram", command, re.IGNORECASE) else "claude-or-anthropic-process"
            add_finding(kind, executable)


def inspect_launchctl():
    executable = shutil.which("launchctl")
    if not executable:
        return
    try:
        completed = subprocess.run(
            [executable, "list"], check=False, capture_output=True, text=True, timeout=8
        )
    except (OSError, subprocess.SubprocessError):
        return
    if completed.returncode != 0:
        return
    lines = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
    if not lines or lines[0].split() != ["PID", "Status", "Label"]:
        return
    probe_status["launchctl"] = True
    for line in lines[1:]:
        fields = line.split()
        if len(fields) != 3:
            add_finding("unparseable-launchctl-row", "launchctl")
            continue
        label = fields[2]
        if re.search(r"(?:claude|anthropic|telegram)", label, re.IGNORECASE):
            launchd.append({"source": "launchctl", "label": label})
            add_finding("claude-launch-action", "launchctl", label)


def inspect_pm2():
    configured_path = os.environ.get("K2B_PM2_PATH")
    if configured_path:
        configured = Path(configured_path).expanduser()
        executable = str(configured) if configured.is_file() and os.access(configured, os.X_OK) else None
    else:
        candidates = [
            shutil.which("pm2"),
            "/opt/homebrew/bin/pm2",
            "/usr/local/bin/pm2",
        ]
        executable = next(
            (
                str(Path(candidate))
                for candidate in candidates
                if candidate and Path(candidate).is_file() and os.access(candidate, os.X_OK)
            ),
            None,
        )
    if executable is None:
        return
    try:
        completed = subprocess.run(
            [executable, "jlist"], check=False, capture_output=True, text=True, timeout=8
        )
        if completed.returncode != 0:
            return
        payload = json.loads(completed.stdout)
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        return
    if not isinstance(payload, list):
        return
    probe_status["pm2"] = True
    for item in payload:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", ""))
        exec_path = str(item.get("pm2_env", {}).get("pm_exec_path", ""))
        if re.search(r"(?:claude|anthropic|telegram)", f"{name} {exec_path}", re.IGNORECASE):
            pm2.append({"name": name, "executable": exec_path})
            kind = "telegram-pm2-process" if re.search(r"telegram", f"{name} {exec_path}", re.IGNORECASE) else "claude-or-anthropic-pm2-process"
            add_finding(kind, name, exec_path or None)


def inspect_crontab():
    executable = shutil.which("crontab")
    if not executable:
        return
    try:
        completed = subprocess.run(
            [executable, "-l"], check=False, capture_output=True, text=True, timeout=5
        )
    except (OSError, subprocess.SubprocessError):
        return
    if completed.returncode != 0:
        if "no crontab for" in completed.stderr.lower():
            probe_status["crontab"] = True
        return
    for line in completed.stdout.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or re.match(r"[A-Za-z_][A-Za-z0-9_]*=", stripped):
            continue
        fields = stripped.split()
        if not (len(fields) >= 6 or (fields[0].startswith("@") and len(fields) >= 2)):
            add_finding("unparseable-crontab-entry", "current-user-crontab")
            continue
        if re.search(r"(?:claude|anthropic|telegram)", line, re.IGNORECASE):
            cron.append({"source": "current-user"})
            add_finding("claude-schedule", "current-user-crontab")
    probe_status["crontab"] = True


for path in candidate_files():
    parts_lower = {part.casefold() for part in path.parts}
    name_lower = path.name.casefold()
    suffix_lower = path.suffix.casefold()
    if name_lower == "package.json":
        inspect_package(path)
    if name_lower == "claude.md":
        instruction_paths.append(str(path))
        add_finding("claude-instruction", path)
    if ".claude" in parts_lower and name_lower == "skill.md":
        instruction_paths.append(str(path))
        add_finding("claude-skill", path)
    if is_env_file(path):
        inspect_env_names(path)
    if suffix_lower == ".plist" and "launchagents" in parts_lower:
        inspect_plist(path)
    if name_lower in {"crontab", "cron.txt"}:
        content = text_from(path)
        if re.search(r"(?:claude|anthropic|telegram)", content, re.IGNORECASE):
            cron.append({"source": str(path)})
            add_finding("claude-schedule", path)
    if ".claude" in parts_lower and name_lower.startswith("settings"):
        content = text_from(path)
        if re.search(r"\"hooks\"|\bhooks\b", content):
            hook_paths.append(str(path))
            add_finding("claude-hook", path)
        if re.search(r"mcpServers|\bmcp\b", content, re.IGNORECASE):
            add_finding("claude-mcp", path)
    if name_lower in {".claude.json", ".mcp.json"}:
        add_finding("claude-mcp", path)
    if suffix_lower in {".sh", ".json", ".md", ".yaml", ".yml"} and ".claude" not in parts_lower:
        content = text_from(path)
        if "CLAUDE_PROJECT_DIR" in content or ".claude/projects" in content:
            add_finding("claude-memory-reader", path)

inspect_processes()
inspect_launchctl()
inspect_pm2()
inspect_crontab()

if is_mini is not True or not all(probe_status.values()):
    add_finding("unverified-mini-surface", "macmini")

report = {
    "host": host,
    "host_identity_status": host_identity_status,
    "reachable": True,
    "processes": processes,
    "launchd": launchd,
    "pm2": pm2,
    "cron": cron,
    "packages": packages,
    "instruction_paths": instruction_paths,
    "hook_paths": hook_paths,
    "credential_names": sorted(credential_names),
    "keychain_credential_status": keychain_credential_status,
    "filesystem_scope": filesystem_scope,
    "probe_status": probe_status,
    "findings": findings,
}
print(json.dumps(report, sort_keys=True))
PY
