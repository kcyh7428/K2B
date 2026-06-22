#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import pwd
import re
import subprocess
import sys
from pathlib import Path
from typing import Any


def iso_now(value: str | None) -> dt.datetime:
    if value:
        return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    return dt.datetime.now(dt.timezone.utc)


def isoformat(value: dt.datetime) -> str:
    return value.astimezone(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_state_time(value: Any) -> dt.datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(dt.timezone.utc)


def hkt_time(value: dt.datetime) -> str:
    return value.astimezone(dt.timezone(dt.timedelta(hours=8))).strftime("%H:%M HKT")


def json_dumps(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def safe_expanduser(path: str) -> str:
    try:
        return os.path.expanduser(path)
    except (RuntimeError, ValueError):
        return path


def usable_ssh_key_path(path: str) -> str | None:
    if not path:
        return None
    expanded = safe_expanduser(path)
    if expanded.startswith("-") or any(ord(char) < 32 for char in expanded):
        return None
    if ".." in Path(expanded).parts:
        return None
    absolute = os.path.abspath(expanded)
    if os.path.islink(absolute):
        return None
    try:
        file_stat = os.stat(absolute, follow_symlinks=False)
    except OSError:
        return None
    if not os.path.isfile(absolute):
        return None
    if file_stat.st_mode & 0o077:
        return None
    test_mode = os.environ.get("K2B_R5C_AUTORECOVERY_TEST_MODE") == "1"
    if test_mode and os.environ.get("K2B_R5C_AUTORECOVERY_TEST_ALLOW_ANY_SSH_KEY") == "1":
        return absolute
    home = os.path.abspath(os.path.expanduser("~"))
    if home != os.path.abspath(pwd.getpwuid(os.getuid()).pw_dir):
        return None
    ssh_dir = os.path.join(home, ".ssh")
    if not (absolute == ssh_dir or absolute.startswith(ssh_dir + os.sep)):
        return None
    current = os.path.dirname(absolute) or "."
    while True:
        try:
            dir_stat = os.stat(current, follow_symlinks=False)
        except OSError:
            return None
        if os.path.islink(current) or (dir_stat.st_mode & 0o022):
            return None
        if os.path.abspath(current) == ssh_dir:
            break
        parent = os.path.dirname(current)
        if parent == current:
            return None
        current = parent
    return absolute


def usable_ssh_target(target: str) -> bool:
    if not target or target.startswith("-"):
        return False
    return not any(char.isspace() or ord(char) < 32 for char in target)


def read_json(path: str) -> dict[str, Any]:
    if not path or not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            payload = json.load(f)
        return payload if isinstance(payload, dict) else {}
    except (OSError, json.JSONDecodeError, ValueError):
        return {}


def atomic_write_json(path: str, payload: dict[str, Any]) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(json_dumps(payload) + "\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def append_jsonl(path: str, payload: dict[str, Any]) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json_dumps(payload) + "\n")
        f.flush()
        os.fsync(f.fileno())


def emit_alert(config: Config, event: dict[str, Any]) -> dict[str, Any]:
    event = dict(event)
    append_jsonl(config.alerts_file, event)
    result: dict[str, Any] = {"attempted": False, "ok": None, "error": None}
    if not config.send_alert_cmd:
        return result
    result["attempted"] = True
    try:
        completed = subprocess.run(
            [config.send_alert_cmd, "--event-json", "-"],
            input=json_dumps(event),
            text=True,
            timeout=20,
            capture_output=True,
            check=False,
        )
        result["ok"] = completed.returncode == 0
        if completed.returncode != 0:
            result["error"] = (completed.stderr or completed.stdout or f"rc={completed.returncode}")[:1000]
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
        result["ok"] = False
        result["error"] = str(exc)[:1000]
    return result


def redact_command(command: list[str]) -> list[str]:
    out = []
    skip_next = False
    for i, token in enumerate(command):
        if skip_next:
            skip_next = False
            out.append("[redacted]")
            continue
        if token == "-i" and i + 1 < len(command):
            skip_next = True
            out.append(token)
            continue
        if re.search(r"/[.\w_-]+key", token, re.IGNORECASE) or "/.ssh/" in token:
            out.append("[redacted-key-path]")
            continue
        out.append(token)
    return out


def run_command(command: list[str], timeout: int) -> dict[str, Any]:
    def textify(value: str | bytes | None) -> str:
        if value is None:
            return ""
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="replace")
        return value

    try:
        completed = subprocess.run(command, text=True, capture_output=True, timeout=timeout, check=False)
        return {
            "ok": completed.returncode == 0,
            "returncode": completed.returncode,
            "stdout": completed.stdout[:2000],
            "stderr": completed.stderr[:2000],
        }
    except FileNotFoundError as exc:
        return {"ok": False, "returncode": None, "stdout": "", "stderr": str(exc)}
    except subprocess.TimeoutExpired as exc:
        stdout = textify(exc.stdout)
        stderr = textify(exc.stderr)
        return {
            "ok": False,
            "returncode": None,
            "stdout": stdout[:2000],
            "stderr": (stderr + "\ncommand timed out")[:2000],
        }


def probe_error(name: str, error: str) -> dict[str, Any]:
    return {name: "unavailable", "ok": None, "error": error}


def ping_probe(host: str, ping_bin: str, timeout_ms: int) -> dict[str, Any]:
    if not host:
        return {"host": "unset", "ok": False, "error": "host unset"}
    result = run_command([ping_bin, "-c", "1", "-W", str(timeout_ms), host], max(2, int(timeout_ms / 1000) + 2))
    output = "\n".join(part for part in (result.get("stdout", ""), result.get("stderr", "")) if part)
    return {
        "host": host,
        "ok": result["ok"],
        "error": None if result["ok"] else (result.get("stderr") or result.get("stdout") or "ping failed"),
    }


def http_probe(url: str, curl_bin: str, timeout_s: int) -> dict[str, Any]:
    result = run_command([curl_bin, "-sS", "-m", str(timeout_s), "-o", "/dev/null", "-w", "%{http_code}", url], timeout_s + 2)
    code = result.get("stdout", "").strip()
    try:
        code_int = int(code)
    except ValueError:
        code_int = 0
    ok = result["ok"] and code_int in (200, 301, 302, 401)
    return {
        "url": url,
        "ok": ok,
        "http_code": code_int,
        "error": None if ok else (result.get("stderr") or f"HTTP {code_int or '000'}"),
    }


def mihomo_api_probe(base: str, curl_bin: str, timeout_s: int, secret: str = "") -> dict[str, Any]:
    url = f"{base.rstrip('/')}/version"
    command = [curl_bin, "-sS", "-m", str(timeout_s), url]
    if secret:
        command = [curl_bin, "-sS", "-m", str(timeout_s), "-H", f"Authorization: Bearer {secret}", url]
    result = run_command(command, timeout_s + 2)
    ok = result["ok"]
    if ok:
        try:
            json.loads(result["stdout"])
        except (json.JSONDecodeError, ValueError):
            ok = False
    return {
        "url": url,
        "ok": ok,
        "error": None if ok else (result.get("stderr") or "mihomo version JSON unavailable"),
    }


def ssh_probe(target: str, key_path: str | None, ssh_bin: str, timeout_s: int, strict: str) -> dict[str, Any]:
    if not usable_ssh_target(target):
        return {"target": "[invalid]", "ok": False, "error": "invalid SSH target"}
    if not key_path:
        return {"target": target, "ok": False, "error": "validated ASUS SSH key required"}
    command = [
        ssh_bin,
        "-F",
        "/dev/null",
        "-o",
        "BatchMode=yes",
        "-o",
        "IdentitiesOnly=yes",
        "-o",
        "IdentityAgent=none",
        "-o",
        f"ConnectTimeout={timeout_s}",
        "-o",
        f"StrictHostKeyChecking={strict}",
        "-i",
        key_path,
    ]
    command.extend([target, "/bin/true"])
    result = run_command(command, timeout_s + 3)
    return {
        "target": target,
        "ok": result["ok"],
        "error": None if result["ok"] else (result.get("stderr") or "SSH probe failed"),
    }


def reboot_asus(target: str, key_path: str | None, ssh_bin: str, timeout_s: int, strict: str) -> dict[str, Any]:
    if not usable_ssh_target(target):
        return {"target": "[invalid]", "ok": False, "error": "invalid SSH target"}
    if not key_path:
        return {"target": target, "ok": False, "error": "validated ASUS SSH key required"}
    command = [
        ssh_bin,
        "-F",
        "/dev/null",
        "-o",
        "BatchMode=yes",
        "-o",
        "IdentitiesOnly=yes",
        "-o",
        "IdentityAgent=none",
        "-o",
        f"ConnectTimeout={timeout_s}",
        "-o",
        f"StrictHostKeyChecking={strict}",
        "-i",
        key_path,
    ]
    command.extend([target, "/sbin/reboot"])
    return run_command(command, timeout_s + 3)


def resolve_asus_ssh_key(raw_path: str) -> tuple[str | None, str | None]:
    if not raw_path:
        return None, "configured ASUS SSH key is required"
    key_path = usable_ssh_key_path(raw_path)
    if key_path is None:
        return None, "configured ASUS SSH key is unusable"
    return key_path, None


def resolve_ssh_bin(raw_path: str) -> tuple[str | None, str | None]:
    if not raw_path:
        return None, "configured SSH binary is required"
    if raw_path.startswith("-") or any(ord(char) < 32 for char in raw_path):
        return None, "configured SSH binary is unsafe"
    path = os.path.abspath(safe_expanduser(raw_path))
    test_mode = os.environ.get("K2B_R5C_AUTORECOVERY_TEST_MODE") == "1"
    if test_mode and os.environ.get("K2B_R5C_AUTORECOVERY_TEST_ALLOW_FAKE_SSH_BIN") == "1":
        if os.path.islink(path) or not os.path.isfile(path) or not os.access(path, os.X_OK):
            return None, "configured SSH binary is unusable"
        return path, None
    if os.path.realpath(path) != "/usr/bin/ssh":
        return None, "configured SSH binary is not trusted"
    try:
        file_stat = os.stat(path, follow_symlinks=False)
    except OSError:
        return None, "configured SSH binary is unusable"
    if os.path.islink(path) or not os.path.isfile(path) or not os.access(path, os.X_OK):
        return None, "configured SSH binary is unusable"
    if file_stat.st_uid != 0 or (file_stat.st_mode & 0o022):
        return None, "configured SSH binary is not trusted"
    return path, None


def resolve_probe_bin(raw_path: str, trusted_path: str, label: str) -> tuple[str | None, str | None]:
    if not raw_path:
        return None, f"configured {label} binary is required"
    if raw_path.startswith("-") or any(ord(char) < 32 for char in raw_path):
        return None, f"configured {label} binary is unsafe"
    path = os.path.abspath(safe_expanduser(raw_path))
    test_mode = os.environ.get("K2B_R5C_AUTORECOVERY_TEST_MODE") == "1"
    if test_mode and os.environ.get("K2B_R5C_AUTORECOVERY_TEST_ALLOW_FAKE_PROBE_BINS") == "1":
        if os.path.islink(path) or not os.path.isfile(path) or not os.access(path, os.X_OK):
            return None, f"configured {label} binary is unusable"
        return path, None
    if os.path.realpath(path) != trusted_path:
        return None, f"configured {label} binary is not trusted"
    try:
        file_stat = os.stat(path, follow_symlinks=False)
    except OSError:
        return None, f"configured {label} binary is unusable"
    if os.path.islink(path) or not os.path.isfile(path) or not os.access(path, os.X_OK):
        return None, f"configured {label} binary is unusable"
    if file_stat.st_uid != 0 or (file_stat.st_mode & 0o022):
        return None, f"configured {label} binary is not trusted"
    return path, None


def collect_probes(config: Config) -> dict[str, Any]:
    key_path, key_error = resolve_asus_ssh_key(config.asus_ssh_key)
    ssh_bin, ssh_bin_error = resolve_ssh_bin(config.ssh_bin)
    ping_bin, ping_error = resolve_probe_bin(config.ping_bin, "/sbin/ping", "ping")
    curl_bin, curl_error = resolve_probe_bin(config.curl_bin, "/usr/bin/curl", "curl")
    if key_error:
        asus_ssh = {"target": config.asus_ssh_target, "ok": False, "error": key_error}
    elif ssh_bin_error:
        asus_ssh = {"target": config.asus_ssh_target, "ok": False, "error": ssh_bin_error}
    else:
        asus_ssh = ssh_probe(config.asus_ssh_target, key_path, ssh_bin, config.ssh_timeout_s, config.strict_host_key)
    return {
        "r5c_lan": probe_error("host", ping_error) if ping_error else ping_probe(config.r5c_lan_ip, ping_bin, config.ping_timeout_ms),
        "router_http": probe_error("url", curl_error) if curl_error else http_probe(f"http://{config.r5c_lan_ip}/", curl_bin, config.http_timeout_s),
        "mihomo_api": probe_error("url", curl_error) if curl_error else mihomo_api_probe(config.mihomo_api_base, curl_bin, config.http_timeout_s, config.mihomo_secret),
        "asus_ssh": asus_ssh,
    }


def probes_show_hard_down(probes: dict[str, Any]) -> bool:
    return (
        probes.get("r5c_lan", {}).get("ok") is False
        and probes.get("router_http", {}).get("ok") is False
        and probes.get("mihomo_api", {}).get("ok") is False
    )


def latest_jsonl_row(path: str) -> dict[str, Any] | None:
    if not os.path.exists(path):
        return None
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            position = f.tell()
            buffer = b""
            while position > 0:
                read_size = min(8192, position)
                position -= read_size
                f.seek(position)
                data = f.read(read_size)
                buffer = data + buffer
                lines = buffer.split(b"\n")
                buffer = lines[0]
                for line in reversed(lines[1:]):
                    if line:
                        try:
                            return json.loads(line.decode("utf-8", errors="replace"))
                        except (json.JSONDecodeError, ValueError):
                            continue
            if buffer:
                try:
                    return json.loads(buffer.decode("utf-8", errors="replace"))
                except (json.JSONDecodeError, ValueError):
                    pass
    except OSError:
        pass
    return None


class Config:
    def __init__(self) -> None:
        app_dir = safe_expanduser(os.environ.get("K2B_ROUTER_WATCHDOG_APP_DIR", "~/Library/Application Support/k2b-router-watchdog"))
        log_dir = safe_expanduser(os.environ.get("K2B_ROUTER_WATCHDOG_LOG_DIR", "~/Library/Logs/k2b-router-watchdog"))
        self.state_file = safe_expanduser(os.environ.get("K2B_R5C_AUTORECOVERY_STATE_FILE", f"{app_dir}/r5c-autorecovery-state.json"))
        self.log_file = safe_expanduser(os.environ.get("K2B_R5C_AUTORECOVERY_LOG", f"{log_dir}/r5c-autorecovery.jsonl"))
        self.evidence_dir = safe_expanduser(os.environ.get("K2B_R5C_AUTORECOVERY_EVIDENCE_DIR", f"{log_dir}/r5c-autorecovery"))
        self.alerts_file = safe_expanduser(os.environ.get("K2B_R5C_AUTORECOVERY_ALERTS_LOG", f"{log_dir}/r5c-autorecovery-alerts.jsonl"))
        self.send_alert_cmd = safe_expanduser(os.environ.get("K2B_R5C_AUTORECOVERY_SEND_ALERT_CMD", ""))
        self.sentinel = safe_expanduser(os.environ.get("K2B_R5C_AUTORECOVERY_SENTINEL", "~/.k2b-r5c-autorecovery-enabled"))
        self.threshold = self._int("K2B_R5C_AUTORECOVERY_THRESHOLD", 3, 1, 10)
        self.cooldown_minutes = self._int("K2B_R5C_AUTORECOVERY_COOLDOWN_MINUTES", 30, 5, 1440)
        self.r5c_lan_ip = os.environ.get("K2B_R5C_AUTORECOVERY_R5C_LAN_IP", "192.168.9.1")
        self.mihomo_api_base = os.environ.get("K2B_R5C_AUTORECOVERY_MIHOMO_API_BASE", os.environ.get("MIHOMO_API_BASE", "http://192.168.9.1:9090"))
        self.asus_host = os.environ.get("K2B_R5C_AUTORECOVERY_ASUS_HOST", "192.168.9.2")
        self.asus_ssh_target = os.environ.get("K2B_R5C_AUTORECOVERY_ASUS_SSH_TARGET", "admin@192.168.9.2")
        self.asus_ssh_key = os.environ.get("K2B_R5C_AUTORECOVERY_ASUS_SSH_KEY", "~/.ssh/router_id_ed25519")
        self.ping_bin = os.environ.get("K2B_R5C_AUTORECOVERY_PING_BIN", "/sbin/ping")
        self.curl_bin = os.environ.get("K2B_R5C_AUTORECOVERY_CURL_BIN", "/usr/bin/curl")
        self.ssh_bin = os.environ.get("K2B_R5C_AUTORECOVERY_SSH_BIN", "/usr/bin/ssh")
        self.ping_timeout_ms = self._int("K2B_R5C_AUTORECOVERY_PING_TIMEOUT_MS", 1000, 200, 5000)
        self.http_timeout_s = self._int("K2B_R5C_AUTORECOVERY_HTTP_TIMEOUT_S", 3, 1, 15)
        self.ssh_timeout_s = self._int("K2B_R5C_AUTORECOVERY_SSH_TIMEOUT_S", 8, 2, 30)
        self.strict_host_key = os.environ.get("K2B_R5C_AUTORECOVERY_SSH_STRICT_HOST_KEY_CHECKING", "yes")
        self.mihomo_secret = os.environ.get("MIHOMO_API_SECRET", "")
        self.alert_failure_stale_hours = self._int("K2B_R5C_AUTORECOVERY_ALERT_FAILURE_STALE_HOURS", 24, 1, 720)

    def _int(self, name: str, default: int, minimum: int, maximum: int) -> int:
        try:
            value = int(os.environ.get(name, str(default)))
        except ValueError:
            value = default
        return min(max(value, minimum), maximum)


def decide(
    config: Config,
    state: dict[str, Any],
    probes: dict[str, Any],
    now: dt.datetime,
) -> tuple[str, dict[str, Any]]:
    hard_down = probes_show_hard_down(probes)

    cooldown_until = state.get("cooldown_until")
    on_cooldown = False
    if cooldown_until:
        try:
            cooldown_dt = dt.datetime.fromisoformat(str(cooldown_until).replace("Z", "+00:00"))
            on_cooldown = now < cooldown_dt
        except ValueError:
            on_cooldown = False

    consecutive = int(state.get("consecutive_hard_down", 0))

    if hard_down and on_cooldown:
        state["consecutive_hard_down"] = consecutive
        state["last_check_at"] = isoformat(now)
        return "blocked_cooldown", {
            "threshold": config.threshold,
            "consecutive_hard_down": consecutive,
            "cooldown_active": True,
            "sentinel_exists": os.path.exists(config.sentinel),
            "asus_reachable": probes.get("asus_ssh", {}).get("ok") is True,
        }

    if hard_down:
        consecutive = min(consecutive + 1, config.threshold)
    else:
        consecutive = 0

    state["consecutive_hard_down"] = consecutive
    state["last_check_at"] = isoformat(now)

    context = {
        "threshold": config.threshold,
        "consecutive_hard_down": consecutive,
        "cooldown_active": False,
        "sentinel_exists": os.path.exists(config.sentinel),
        "asus_reachable": probes.get("asus_ssh", {}).get("ok") is True,
    }

    if not hard_down:
        return "no_action", context

    if consecutive < config.threshold:
        return "no_action", context

    if not os.path.exists(config.sentinel):
        return "blocked_sentinel_missing", context

    if probes.get("asus_ssh", {}).get("ok") is not True:
        return "blocked_asus_unreachable", context

    return "reboot_attempted", context


def alert_base(now: dt.datetime, event_type: str, message: str, context: dict[str, Any]) -> dict[str, Any]:
    return {
        "timestamp": isoformat(now),
        "type": event_type,
        "check": "r5c_autorecovery",
        "consecutive_hard_down": context.get("consecutive_hard_down"),
        "threshold": context.get("threshold"),
        "message": message,
    }


def sync_r5c_alert_state(
    config: Config,
    state: dict[str, Any],
    action: str,
    context: dict[str, Any],
    probes: dict[str, Any],
    now: dt.datetime,
    final_probes: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    active_probes = final_probes or probes
    hard_down = probes_show_hard_down(active_probes)
    events: list[dict[str, Any]] = []

    def queue(event_type: str, message: str) -> None:
        event = alert_base(now, event_type, message, context)
        events.append(event)
        state["r5c_last_alert_type"] = event_type
        state["r5c_last_alert_at"] = isoformat(now)

    def recently_alerted(event_type: str, within_minutes: int = 30) -> bool:
        if state.get("r5c_last_alert_type") != event_type:
            return False
        alerted_at = parse_state_time(state.get("r5c_last_alert_at"))
        if alerted_at is None:
            return False
        return now.astimezone(dt.timezone.utc) - alerted_at <= dt.timedelta(minutes=within_minutes)

    if hard_down:
        if not state.get("r5c_incident_open"):
            state["r5c_incident_open"] = True
            state["r5c_incident_started_at"] = isoformat(now)
            state["r5c_reboot_attempted_in_incident"] = False
            state["r5c_fire_alerted"] = False
            state["r5c_blocked_alerted_action"] = None
            count = context.get("consecutive_hard_down")
            threshold = context.get("threshold")
            if not recently_alerted("r5c_hard_down_suspected"):
                queue(
                    "r5c_hard_down_suspected",
                    "\n".join(
                        [
                            f"K2B R5C autorecovery: hard-down suspected ({count}/{threshold}) at {hkt_time(now)}",
                            "R5C LAN, router HTTP, and Mihomo API are all failing.",
                            "No ASUS reboot yet.",
                        ]
                    ),
                )

        if action in {"reboot_attempted", "reboot_failed"} and not state.get("r5c_fire_alerted"):
            state["r5c_reboot_attempted_in_incident"] = True
            state["r5c_fire_alerted"] = True
            status = "ASUS reboot command was attempted."
            if action == "reboot_failed":
                status = "ASUS reboot command was attempted but failed; cooldown is still active."
            queue(
                "r5c_autorecovery_fired",
                "\n".join(
                    [
                        f"K2B R5C autorecovery: firing ASUS power-cycle at {hkt_time(now)} ({context.get('consecutive_hard_down')}/{context.get('threshold')})",
                        status,
                    ]
                ),
            )
        elif action in {"blocked_sentinel_missing", "blocked_asus_unreachable", "blocked_final_confirmation_failed"}:
            if state.get("r5c_blocked_alerted_action") != action:
                state["r5c_blocked_alerted_action"] = action
                queue(
                    "r5c_autorecovery_blocked",
                    "\n".join(
                        [
                            f"K2B R5C autorecovery: blocked at {hkt_time(now)} ({action})",
                            "No ASUS reboot was attempted.",
                        ]
                    ),
                )
        return events

    if state.get("r5c_incident_open"):
        started = state.get("r5c_incident_started_at")
        attempted = bool(state.get("r5c_reboot_attempted_in_incident"))
        event_type = "r5c_autorecovery_recovered" if attempted else "r5c_autorecovery_cleared"
        title = "recovered after ASUS power-cycle attempt" if attempted else "cleared before reboot"
        details = [f"K2B R5C autorecovery: {title} at {hkt_time(now)}"]
        if started:
            details.append(f"Incident started: {started}")
        queue(event_type, "\n".join(details))
        state["r5c_incident_open"] = False
        if started:
            state["r5c_last_incident_started_at"] = started
        state["r5c_last_cleared_at"] = isoformat(now)
        state["r5c_reboot_attempted_in_incident"] = False
        state["r5c_fire_alerted"] = False
        state["r5c_blocked_alerted_action"] = None
    return events


def write_evidence(
    config: Config,
    state: dict[str, Any],
    probes: dict[str, Any],
    now: dt.datetime,
    action: str,
    planned_command: list[str],
    final_probes: dict[str, Any] | None = None,
) -> str:
    Path(config.evidence_dir).mkdir(parents=True, exist_ok=True)
    health_row = latest_jsonl_row(os.path.join(os.path.dirname(config.log_file), "health.jsonl"))
    private_row = latest_jsonl_row(os.path.join(os.path.dirname(config.log_file), "private-vpn-health.jsonl"))
    confirmation = final_probes or probes
    payload = {
        "timestamp": isoformat(now),
        "action": action,
        "state_before": state,
        "probes": probes,
        "final_probes": final_probes,
        "latest_health_row": health_row,
        "latest_private_vpn_health_row": private_row,
        "asus_reachability": confirmation.get("asus_ssh"),
        "final_confirmation": {
            "r5c_lan_ok": confirmation.get("r5c_lan", {}).get("ok"),
            "router_http_ok": confirmation.get("router_http", {}).get("ok"),
            "mihomo_api_ok": confirmation.get("mihomo_api", {}).get("ok"),
            "asus_ssh_ok": confirmation.get("asus_ssh", {}).get("ok"),
        },
        "planned_command": redact_command(planned_command),
        "planned_command_shape": "ssh -i [key] [target] /sbin/reboot",
    }
    filename = f"{now.strftime('%Y-%m-%dT%H%M%S')}.{now.microsecond:06d}Z-r5c-autorecovery.json"
    path = str(Path(config.evidence_dir) / filename)
    atomic_write_json(path, payload)
    return path


def alert_delivery_failures(state: dict[str, Any]) -> int:
    try:
        return int(state.get("r5c_alert_delivery_failures", 0))
    except (TypeError, ValueError):
        return 0


def clear_stale_alert_delivery_failure(config: Config, state: dict[str, Any], now: dt.datetime) -> None:
    if alert_delivery_failures(state) <= 0:
        return
    failed_at = parse_state_time(state.get("r5c_last_alert_delivery_failed_at"))
    if failed_at is None:
        return
    if now.astimezone(dt.timezone.utc) - failed_at <= dt.timedelta(hours=config.alert_failure_stale_hours):
        return
    state["r5c_alert_delivery_failures"] = 0
    state.pop("r5c_last_alert_delivery_error", None)
    state.pop("r5c_last_alert_delivery_failed_at", None)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--now", default=None)
    args = parser.parse_args()

    config = Config()
    now = iso_now(args.now)
    state = read_json(config.state_file)
    state.setdefault("consecutive_hard_down", 0)

    probes: dict[str, Any] = collect_probes(config)

    action, context = decide(config, state, probes, now)

    planned_command: list[str] = []
    evidence_path: str | None = None
    reboot_result: dict[str, Any] | None = None
    final_probes: dict[str, Any] | None = None

    if action == "reboot_attempted":
        key_path, key_error = resolve_asus_ssh_key(config.asus_ssh_key)
        ssh_bin, ssh_bin_error = resolve_ssh_bin(config.ssh_bin)
        if key_error:
            action = "blocked_asus_unreachable"
            probes["asus_ssh"] = {"target": config.asus_ssh_target, "ok": False, "error": key_error}
            evidence_path = write_evidence(config, state, probes, now, action, planned_command)
        elif ssh_bin_error:
            action = "blocked_asus_unreachable"
            probes["asus_ssh"] = {"target": config.asus_ssh_target, "ok": False, "error": ssh_bin_error}
            evidence_path = write_evidence(config, state, probes, now, action, planned_command)
        else:
            planned_command = [
                ssh_bin,
                "-F",
                "/dev/null",
                "-o",
                "BatchMode=yes",
                "-o",
                "IdentitiesOnly=yes",
                "-o",
                "IdentityAgent=none",
                "-o",
                f"ConnectTimeout={config.ssh_timeout_s}",
                "-o",
                f"StrictHostKeyChecking={config.strict_host_key}",
                "-i",
                key_path,
            ]
            planned_command.extend([config.asus_ssh_target, "/sbin/reboot"])

            final_probes = collect_probes(config)
            if not probes_show_hard_down(final_probes) or final_probes.get("asus_ssh", {}).get("ok") is not True:
                action = "blocked_final_confirmation_failed"
                state["last_final_confirmation_failed_at"] = isoformat(now)
                state["consecutive_hard_down"] = 0
                evidence_path = write_evidence(config, state, probes, now, action, planned_command, final_probes)
            else:
                evidence_path = write_evidence(config, state, probes, now, action, planned_command, final_probes)
                reboot_result = reboot_asus(config.asus_ssh_target, key_path, ssh_bin, config.ssh_timeout_s, config.strict_host_key)
                cooldown_until = now + dt.timedelta(minutes=config.cooldown_minutes)
                state["cooldown_until"] = isoformat(cooldown_until)
                state["last_reboot_attempt_at"] = isoformat(now)
                state["consecutive_hard_down"] = 0
                if reboot_result["ok"]:
                    state["last_reboot_at"] = isoformat(now)
                else:
                    action = "reboot_failed"
                    state["last_reboot_failed_at"] = isoformat(now)
    elif action in ("blocked_cooldown", "blocked_sentinel_missing", "blocked_asus_unreachable"):
        evidence_path = write_evidence(config, state, probes, now, action, planned_command)
    elif action == "no_action" and context["consecutive_hard_down"] > 0:
        evidence_path = write_evidence(config, state, probes, now, "no_action_threshold_building", planned_command)

    clear_stale_alert_delivery_failure(config, state, now)
    alert_events = sync_r5c_alert_state(config, state, action, context, probes, now, final_probes)
    alert_results = []
    for event in alert_events:
        result = emit_alert(config, event)
        if result.get("ok") is False:
            state["r5c_alert_delivery_failures"] = min(alert_delivery_failures(state) + 1, 100)
            state["r5c_last_alert_delivery_error"] = result.get("error") or "unknown delivery failure"
            state["r5c_last_alert_delivery_failed_at"] = isoformat(now)
        elif result.get("ok") is True:
            state["r5c_alert_delivery_failures"] = 0
            state.pop("r5c_last_alert_delivery_error", None)
            state.pop("r5c_last_alert_delivery_failed_at", None)
        alert_results.append({"type": event.get("type"), **result})
    log_row = {
        "timestamp": isoformat(now),
        "action": action,
        "context": context,
        "probes": {
            name: {
                "ok": result.get("ok"),
                "error": result.get("error"),
                "http_code": result.get("http_code"),
            }
            for name, result in probes.items()
        },
    }
    if evidence_path:
        log_row["evidence_path"] = evidence_path
    if reboot_result is not None:
        log_row["reboot_result"] = {
            "ok": reboot_result["ok"],
            "returncode": reboot_result["returncode"],
            "stderr": reboot_result["stderr"],
        }
    if final_probes is not None:
        log_row["final_confirmation"] = {
            name: {
                "ok": result.get("ok"),
                "error": result.get("error"),
                "http_code": result.get("http_code"),
            }
            for name, result in final_probes.items()
        }
    if alert_results:
        log_row["alerts"] = alert_results

    append_jsonl(config.log_file, log_row)
    atomic_write_json(config.state_file, state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
