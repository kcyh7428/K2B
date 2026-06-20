#!/usr/bin/env python3
import argparse
import atexit
import copy
import datetime as dt
import fcntl
import ipaddress
import json
import os
import pwd
import re
import shutil
import socket
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any


TARGETS = [
    "http://www.gstatic.com/generate_204",
    "https://www.apple.com/library/test/success.html",
]


def iso_now(value: str | None) -> dt.datetime:
    if value:
        return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    return dt.datetime.now(dt.timezone.utc)


def isoformat(value: dt.datetime) -> str:
    return value.astimezone(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def json_dumps(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


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
    fsync_parent(path)


def fsync_parent(path: str) -> None:
    parent = os.path.dirname(path) or "."
    try:
        fd = os.open(parent, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def acquire_lock(path: str) -> Any | None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    handle = open(path, "w", encoding="utf-8")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        return None
    handle.write(str(os.getpid()))
    handle.flush()
    os.fsync(handle.fileno())
    return handle


def cleanup_lock(path: str, handle: Any) -> None:
    try:
        handle.close()
    finally:
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass
        except OSError:
            pass


def install_lock_signal_handlers(path: str, handle: Any) -> None:
    def _handler(signum, _frame):
        cleanup_lock(path, handle)
        signal.signal(signum, signal.SIG_DFL)
        os.kill(os.getpid(), signum)

    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, _handler)


def append_jsonl(path: str, payload: dict[str, Any]) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        try:
            f.write(json_dumps(payload) + "\n")
            f.flush()
            os.fsync(f.fileno())
        finally:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    fsync_parent(path)


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
    home = os.path.abspath(os.path.expanduser("~"))
    if home != os.path.abspath(pwd.getpwuid(os.getuid()).pw_dir):
        return None
    ssh_dir = os.path.join(home, ".ssh")
    if not (absolute == ssh_dir or absolute.startswith(ssh_dir + os.sep)):
        return None
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


def unusable_ssh_key_reason(path: str) -> str | None:
    if not path:
        return None
    expanded = safe_expanduser(path)
    if expanded.startswith("-") or any(ord(char) < 32 for char in expanded):
        return "ssh key path contains unsafe characters"
    if ".." in Path(expanded).parts:
        return "ssh key path must not contain parent-directory traversal"
    absolute = os.path.abspath(expanded)
    home = os.path.abspath(os.path.expanduser("~"))
    if home != os.path.abspath(pwd.getpwuid(os.getuid()).pw_dir):
        return "HOME does not match the current user home directory"
    ssh_dir = os.path.join(home, ".ssh")
    if not (absolute == ssh_dir or absolute.startswith(ssh_dir + os.sep)):
        return "ssh key path must be under ~/.ssh"
    if os.path.islink(absolute):
        return "ssh key path must not be a symlink"
    try:
        file_stat = os.stat(absolute, follow_symlinks=False)
    except OSError:
        return "ssh key path is not readable"
    if not os.path.isfile(absolute):
        return "ssh key path is not a regular file"
    if file_stat.st_mode & 0o077:
        return "ssh key permissions must not allow group/other access"
    current = os.path.dirname(absolute) or "."
    while True:
        try:
            dir_stat = os.stat(current, follow_symlinks=False)
        except OSError:
            return "ssh key parent directory is not readable"
        if os.path.islink(current):
            return "ssh key parent directories must not be symlinks"
        if dir_stat.st_mode & 0o022:
            return "ssh key parent directories must not be group/other writable"
        if os.path.abspath(current) == ssh_dir:
            break
        parent = os.path.dirname(current)
        if parent == current:
            return "ssh key path must be under ~/.ssh"
        current = parent
    return None


def usable_ssh_target(target: str) -> bool:
    if not target or target.startswith("-"):
        return False
    return not any(char.isspace() or ord(char) < 32 for char in target)


def redact_text(value: str) -> str:
    hk_target = os.environ.get("K2B_PRIVATE_VPN_HK_SSH_TARGET", "")
    router_target = os.environ.get("K2B_PRIVATE_VPN_ROUTER_SSH_TARGET", "")
    secrets = [
        os.environ.get("MIHOMO_API_SECRET", ""),
        os.environ.get("TELEGRAM_BOT_TOKEN", ""),
        os.environ.get("K2B_PRIVATE_VPN_AWS_PROFILE", ""),
        os.environ.get("K2B_PRIVATE_VPN_ROUTER_TRACE_COMMAND", ""),
        os.environ.get("K2B_PRIVATE_VPN_HK_TRACE_COMMAND", ""),
        hk_target,
        router_target,
        safe_expanduser(os.environ.get("K2B_PRIVATE_VPN_HK_SSH_KEY", "")),
    ]
    secrets.extend(re.findall(r"\b\d{1,3}(?:\.\d{1,3}){3}\b", hk_target))
    secrets.extend(re.findall(r"\b\d{1,3}(?:\.\d{1,3}){3}\b", router_target))
    out = value
    for secret in secrets:
        if secret:
            out = out.replace(secret, "[redacted]")
    out = re.sub(
        r"(?i)\b(password|passwd|secret|token|authorization|auth)\b(\s*[:=]\s*)(\S+)",
        r"\1\2[redacted]",
        out,
    )
    out = re.sub(r"\b(?:\d{1,3}\.){3}\d{1,3}(?:/\d{1,2})?\b", "[redacted-ip]", out)
    out = re.sub(r"\b(?:[0-9a-fA-F]{1,4}:){2,}[0-9a-fA-F:/]{0,}\b", "[redacted-ipv6]", out)
    return out


def bounded(value: str, limit: int = 12000) -> str:
    value = redact_text(value)
    if len(value) <= limit:
        return value
    return redact_text(value[:limit] + "\n...[truncated]")


def bounded_env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except ValueError:
        return default
    return min(max(value, minimum), maximum)


def bounded_env_float(name: str, default: float, minimum: float, maximum: float) -> float:
    try:
        value = float(os.environ.get(name, str(default)))
    except ValueError:
        return default
    return min(max(value, minimum), maximum)


def env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"", "0", "false", "no", "off", "disable", "disabled"}


def env_csv(name: str, default: str) -> list[str]:
    raw = os.environ.get(name, default)
    return [item.strip() for item in raw.split(",") if item.strip()]


def parse_latency_ms(output: str) -> float | None:
    match = re.search(r"time[=<]([0-9]+(?:\.[0-9]+)?)\s*ms", output)
    if match:
        return float(match.group(1))
    match = re.search(r"=\s*[0-9.]+/([0-9]+(?:\.[0-9]+)?)/[0-9.]+/[0-9.]+\s*ms", output)
    if match:
        return float(match.group(1))
    return None


def boolish(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "yes", "1", "ok"}:
            return True
        if normalized in {"false", "no", "0", "off"}:
            return False
    return None


def first_boolish(*values: Any) -> bool | None:
    for value in values:
        parsed = boolish(value)
        if parsed is not None:
            return parsed
    return None


def shell_safe_host(host: str, fallback: str) -> str:
    return host if re.fullmatch(r"[A-Za-z0-9_.-]+", host) else fallback


class MihomoClient:
    def __init__(self, base: str, secret: str, timeout: float) -> None:
        self.base = base.rstrip("/")
        self.secret = secret
        self.timeout = timeout

    def get(self, path: str, params: dict[str, str] | None = None) -> tuple[dict[str, Any] | None, str | None]:
        query = ""
        if params:
            query = "?" + urllib.parse.urlencode(params)
        url = f"{self.base}{path}{query}"
        headers = {}
        if self.secret:
            headers["Authorization"] = f"Bearer {self.secret}"
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
                return json.loads(raw) if raw else {}, None
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
            return None, f"http_{exc.code}: {bounded(raw, 300)}"
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError, ValueError) as exc:
            return None, f"{type(exc).__name__}: {exc}"

    def proxy_path(self, name: str, suffix: str = "") -> str:
        return "/proxies/" + urllib.parse.quote(name, safe="") + suffix


def short_label(name: str, primary: str, failover: str, emergency: str) -> str:
    if name == primary:
        return "HK"
    if name == failover:
        return "TW"
    if name == emergency:
        return "KL"
    if name == "DIRECT":
        return "DIRECT"
    return name


def check_leaf(client: MihomoClient, name: str) -> dict[str, Any]:
    attempts: list[dict[str, Any]] = []
    latencies: list[int] = []
    for target in TARGETS:
        payload, error = client.get(
            client.proxy_path(name, "/delay"),
            {"timeout": os.environ.get("K2B_PRIVATE_VPN_DELAY_TIMEOUT_MS", "7000"), "url": target},
        )
        if payload is not None and isinstance(payload.get("delay"), int):
            latency = int(payload["delay"])
            attempts.append({"target": target, "ok": True, "latency_ms": latency})
            latencies.append(latency)
        else:
            attempts.append({"target": target, "ok": False, "error": error or "missing_delay"})
    ok = len(latencies) == len(TARGETS)
    return {
        "ok": ok,
        "latency_ms": max(latencies) if ok else None,
        "error": None if ok else "partial_target_failure" if latencies else attempts[-1].get("error", "all_targets_failed"),
        "targets": attempts,
    }


def selected_proxy(proxies: dict[str, Any], name: str) -> dict[str, Any]:
    proxy = proxies.get(name)
    return proxy if isinstance(proxy, dict) else {}


def safe_proxy_snapshot(proxies: dict[str, Any], names: list[str]) -> dict[str, Any]:
    snapshot: dict[str, Any] = {}
    for name in names:
        proxy = selected_proxy(proxies, name)
        if not proxy:
            continue
        entry: dict[str, Any] = {}
        for key in ("name", "type", "now", "all", "alive", "history"):
            if key in proxy:
                entry[key] = proxy[key]
        snapshot[name] = entry
    return snapshot


def cheap_check(
    client: MihomoClient,
    route_group: str,
    private_group: str,
    primary: str,
    failover: str,
    emergency: str,
) -> dict[str, Any]:
    version, version_error = client.get("/version")
    proxies_payload, proxies_error = client.get("/proxies")
    if version is None or proxies_payload is None or not isinstance(proxies_payload, dict) or "proxies" not in proxies_payload:
        return {
            "api_ok": False,
            "error": version_error or proxies_error or "mihomo_api_unavailable",
            "checks": {},
            "chain": [],
            "proxies": {},
            "version": version,
        }

    proxies = proxies_payload.get("proxies") if isinstance(proxies_payload, dict) else {}
    if not isinstance(proxies, dict):
        proxies = {}
    route = selected_proxy(proxies, route_group)
    private = selected_proxy(proxies, private_group)
    current_private_leaf = private.get("now") if isinstance(private.get("now"), str) else "unknown"
    chain = [route_group, route.get("now") or private_group, current_private_leaf]

    checks = {
        primary: check_leaf(client, primary),
        failover: check_leaf(client, failover),
        emergency: check_leaf(client, emergency),
        "DIRECT": check_leaf(client, "DIRECT"),
    }
    return {
        "api_ok": True,
        "checks": checks,
        "chain": chain,
        "proxies": safe_proxy_snapshot(proxies, [route_group, private_group, primary, failover, emergency, "DIRECT"]),
        "version": version,
    }


def run_command(command: list[str], timeout: int) -> dict[str, Any]:
    try:
        completed = subprocess.run(command, text=True, capture_output=True, timeout=timeout, check=False)
        return {
            "ok": completed.returncode == 0,
            "returncode": completed.returncode,
            "stdout": bounded(completed.stdout),
            "stderr": bounded(completed.stderr),
        }
    except FileNotFoundError as exc:
        return {"ok": False, "returncode": None, "stdout": "", "stderr": str(exc)}
    except subprocess.TimeoutExpired as exc:
        return {
            "ok": False,
            "returncode": None,
            "stdout": bounded(exc.stdout or ""),
            "stderr": bounded((exc.stderr or "") + "\ncommand timed out"),
        }


def ping_probe(label: str, host: str) -> dict[str, Any]:
    if not host:
        return {"label": label, "host": "unset", "ok": False, "latency_ms": None, "error": "host unset"}
    ping_bin = os.environ.get("K2B_PRIVATE_VPN_PING_BIN", "ping")
    timeout_ms = bounded_env_int("K2B_PRIVATE_VPN_PING_TIMEOUT_MS", 1000, 200, 5000)
    result = run_command([ping_bin, "-c", "1", "-W", str(timeout_ms), host], max(2, int(timeout_ms / 1000) + 2))
    output = "\n".join(part for part in (result.get("stdout", ""), result.get("stderr", "")) if part)
    return {
        "label": label,
        "host": host,
        "ok": result["ok"],
        "latency_ms": parse_latency_ms(output),
        "error": None if result["ok"] else (result.get("stderr") or result.get("stdout") or "ping failed"),
    }


def parse_tcp_target(target: str) -> tuple[str, int] | None:
    if not target:
        return None
    if target.startswith("[") and "]:" in target:
        host, port = target[1:].split("]:", 1)
    elif target.count(":") == 1:
        host, port = target.rsplit(":", 1)
    else:
        return None
    try:
        return host, int(port)
    except ValueError:
        return None


def is_ip_literal(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


def redacted_tcp_target(target: str) -> str:
    parsed = parse_tcp_target(target)
    if parsed is None:
        return "[invalid-target]"
    host, port = parsed
    if re.fullmatch(r"\d{1,3}(?:\.\d{1,3}){3}", host) or ":" in host:
        return f"[redacted-ip]:{port}"
    return f"[redacted-host]:{port}"


def tcp_probe(target: str) -> dict[str, Any]:
    parsed = parse_tcp_target(target)
    if parsed is None:
        return {"target": "[invalid-target]", "ok": False, "latency_ms": None, "error": "invalid target"}
    host, port = parsed
    if not is_ip_literal(host):
        return {"target": "[invalid-target]", "ok": False, "latency_ms": None, "error": "TCP target must use an IP literal"}
    timeout = bounded_env_float("K2B_PRIVATE_VPN_TCP_TIMEOUT", 2.0, 0.5, 10.0)
    start = time.monotonic()
    try:
        with socket.create_connection((host, port), timeout=timeout):
            latency = round((time.monotonic() - start) * 1000, 1)
        return {"target": redacted_tcp_target(target), "ok": True, "latency_ms": latency, "error": None}
    except OSError as exc:
        return {"target": redacted_tcp_target(target), "ok": False, "latency_ms": None, "error": f"{type(exc).__name__}: {exc}"}


def find_tailscale_bin() -> str | None:
    configured = os.environ.get("K2B_PRIVATE_VPN_TAILSCALE_BIN", "")
    if configured:
        return configured
    mac_app = "/Applications/Tailscale.app/Contents/MacOS/Tailscale"
    if os.path.exists(mac_app):
        return mac_app
    return shutil.which("tailscale")


def trace_tailscale_status() -> dict[str, Any]:
    if not env_bool("K2B_PRIVATE_VPN_TAILSCALE_STATUS", True):
        return {"enabled": False}
    tailscale_bin = find_tailscale_bin()
    if not tailscale_bin:
        return {"enabled": True, "available": False, "ok": False, "error": "tailscale binary not found"}
    result = run_command([tailscale_bin, "status", "--json"], bounded_env_int("K2B_PRIVATE_VPN_TAILSCALE_TIMEOUT", 4, 2, 30))
    payload: dict[str, Any] = {}
    if result["stdout"]:
        try:
            raw = json.loads(result["stdout"])
            payload = raw if isinstance(raw, dict) else {}
        except (json.JSONDecodeError, ValueError):
            payload = {}
    self_info = payload.get("Self") if isinstance(payload.get("Self"), dict) else {}
    tailnet = payload.get("CurrentTailnet") if isinstance(payload.get("CurrentTailnet"), dict) else {}
    peers = payload.get("Peer") if isinstance(payload.get("Peer"), dict) else {}
    return {
        "enabled": True,
        "available": True,
        "ok": result["ok"],
        "backend_state": payload.get("BackendState", "unknown"),
        "self_online": boolish(self_info.get("Online")),
        "current_tailnet": tailnet.get("Name", "unknown"),
        "peer_count": len(peers),
        "stderr": result["stderr"] if not result["ok"] else "",
    }


def trace_tailscale_netcheck() -> dict[str, Any]:
    if not env_bool("K2B_PRIVATE_VPN_TAILSCALE_NETCHECK_ON_INCIDENT", True):
        return {"enabled": False, "skipped": True}
    tailscale_bin = find_tailscale_bin()
    if not tailscale_bin:
        return {"enabled": True, "available": False, "ok": False, "error": "tailscale binary not found"}
    result = run_command([tailscale_bin, "netcheck", "--json"], bounded_env_int("K2B_PRIVATE_VPN_TAILSCALE_TIMEOUT", 4, 2, 30))
    payload: dict[str, Any] = {}
    if result["stdout"]:
        try:
            raw = json.loads(result["stdout"])
            payload = raw if isinstance(raw, dict) else {}
        except (json.JSONDecodeError, ValueError):
            payload = {}
    report = payload.get("Report") if isinstance(payload.get("Report"), dict) else payload
    if not isinstance(report, dict):
        report = {}
    return {
        "enabled": True,
        "available": True,
        "ok": result["ok"],
        "udp": boolish(report.get("UDP")),
        "ipv4": first_boolish(report.get("IPv4"), report.get("IPv4CanSend")),
        "ipv6": first_boolish(report.get("IPv6"), report.get("IPv6CanSend")),
        "mapping_varies_by_dest_ip": boolish(report.get("MappingVariesByDestIP")),
        "port_mapping": report.get("PortMapping", "unknown"),
        "nearest_derp": report.get("NearestDERP", report.get("NearestDERPRegion", "unknown")),
        "stderr": result["stderr"] if not result["ok"] else "",
        "stdout": "" if payload else bounded(result["stdout"], 1200),
    }


def edge_diagnostics(include_netcheck: bool = False) -> dict[str, Any]:
    if not env_bool("K2B_PRIVATE_VPN_EDGE_TRACE", True):
        return {"enabled": False}
    tailscale_status_every_tick = env_bool("K2B_PRIVATE_VPN_TAILSCALE_STATUS_EVERY_TICK", False)
    router_lan = os.environ.get("K2B_PRIVATE_VPN_ROUTER_LAN_IP", "192.168.50.1")
    upstream = os.environ.get("K2B_PRIVATE_VPN_UPSTREAM_GATEWAY", "192.168.1.1")
    edge = {
        "enabled": True,
        "pings": {
            "router_lan": ping_probe("router_lan", router_lan),
            "upstream_gateway": ping_probe("upstream_gateway", upstream),
        },
        "tcp": {
            "targets": [tcp_probe(target) for target in env_csv("K2B_PRIVATE_VPN_TCP_TARGETS", "1.1.1.1:443")],
        },
        "tailscale_status": trace_tailscale_status() if include_netcheck or tailscale_status_every_tick else {"skipped": True},
    }
    if include_netcheck:
        edge["tailscale_netcheck"] = trace_tailscale_netcheck()
    else:
        edge["tailscale_netcheck"] = {"skipped": True}
    return edge


def any_tcp_ok(edge: dict[str, Any]) -> bool:
    tcp = edge.get("tcp") if isinstance(edge.get("tcp"), dict) else {}
    targets = tcp.get("targets") if isinstance(tcp.get("targets"), list) else []
    return any(isinstance(target, dict) and target.get("ok") is True for target in targets)


def evidence_hint(classification_value: str, edge: dict[str, Any]) -> str:
    pings = edge.get("pings") if isinstance(edge.get("pings"), dict) else {}
    router_lan = pings.get("router_lan") if isinstance(pings.get("router_lan"), dict) else {}
    upstream = pings.get("upstream_gateway") if isinstance(pings.get("upstream_gateway"), dict) else {}
    netcheck = edge.get("tailscale_netcheck") if isinstance(edge.get("tailscale_netcheck"), dict) else {}
    if router_lan.get("ok") is False:
        return "mac_mini_to_asus_lan_suspect"
    if upstream.get("ok") is False:
        return "upstream_modem_gateway_suspect"
    if classification_value == "hk_only_down":
        return "primary_path_specific"
    if classification_value == "all_private_udp_down" and (netcheck.get("udp") is False or any_tcp_ok(edge)):
        return "udp_nat_mapping_or_isp_modem_suspect"
    if classification_value == "router_mihomo_down":
        return "asus_mihomo_api_or_lan_suspect"
    return "insufficient_edge_evidence"


def evidence_text(hint: str) -> str:
    return {
        "primary_path_specific": "primary path specific",
        "udp_nat_mapping_or_isp_modem_suspect": "UDP/NAT path suspect",
        "mac_mini_to_asus_lan_suspect": "Mac Mini to ASUS LAN suspect",
        "upstream_modem_gateway_suspect": "upstream modem gateway suspect",
        "asus_mihomo_api_or_lan_suspect": "ASUS Mihomo API or LAN suspect",
        "insufficient_edge_evidence": "insufficient edge evidence",
    }.get(hint, hint)


def trace_aws() -> dict[str, Any]:
    profile = os.environ.get("K2B_PRIVATE_VPN_AWS_PROFILE", "k2b-aws-signhubdev-hk")
    region = os.environ.get("K2B_PRIVATE_VPN_AWS_REGION", "ap-east-1")
    instance = os.environ.get("K2B_PRIVATE_VPN_AWS_INSTANCE", "Ubuntu-1")
    command = [
        "aws",
        "lightsail",
        "get-instance-state",
        "--profile",
        profile,
        "--region",
        region,
        "--instance-name",
        instance,
        "--output",
        "json",
    ]
    result = run_command(command, bounded_env_int("K2B_PRIVATE_VPN_TRACE_TIMEOUT", 15, 5, 60))
    state = "unknown"
    if result["stdout"]:
        try:
            payload = json.loads(result["stdout"])
            raw_state = payload.get("state", {}).get("name")
            if isinstance(raw_state, str) and raw_state:
                state = raw_state
        except (json.JSONDecodeError, ValueError, AttributeError):
            pass
    return {
        "profile": "[redacted]",
        "region": region,
        "instance": instance,
        "state": state,
        "command_ok": result["ok"],
        "stderr": result["stderr"],
    }


def trace_router() -> dict[str, Any]:
    target = os.environ.get("K2B_PRIVATE_VPN_ROUTER_SSH_TARGET", "router")
    if not usable_ssh_target(target):
        return {
            "target": "[invalid]",
            "ok": False,
            "stdout": "",
            "stderr": "K2B_PRIVATE_VPN_ROUTER_SSH_TARGET is invalid",
        }
    upstream_gateway = shell_safe_host(os.environ.get("K2B_PRIVATE_VPN_UPSTREAM_GATEWAY", "192.168.1.1"), "192.168.1.1")
    command = os.environ.get(
        "K2B_PRIVATE_VPN_ROUTER_TRACE_COMMAND",
        "date; uptime; "
        "(ip route 2>/dev/null || route -n 2>/dev/null || true); "
        "(ifconfig eth0 2>/dev/null || true); "
        "(ifconfig br0 2>/dev/null || true); "
        "(cat /etc/resolv.conf 2>/dev/null || true); "
        f"(ping -c 2 -W 1 {upstream_gateway} 2>&1 || true); "
        "(ping -c 2 -W 1 1.1.1.1 2>&1 || true); "
        "(nslookup www.gstatic.com 2>&1 || true); "
        "tail -n 120 /tmp/syslog.log 2>/dev/null || true; "
        "tail -n 120 /tmp/clash_run.log 2>/dev/null || true",
    )
    result = run_command(
        ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", target, command],
        bounded_env_int("K2B_PRIVATE_VPN_TRACE_TIMEOUT", 15, 5, 60),
    )
    return {
        "target": target,
        "ok": result["ok"],
        "stdout": result["stdout"],
        "stderr": result["stderr"],
    }


def parse_key_value(stdout: str, key: str) -> str:
    pattern = re.compile(rf"^{re.escape(key)}=(.*)$", re.MULTILINE)
    match = pattern.search(stdout)
    if match:
        return match.group(1).strip()
    return "unknown"


def trace_server() -> dict[str, Any]:
    target = os.environ.get("K2B_PRIVATE_VPN_HK_SSH_TARGET", "")
    if not target:
        return {
            "target": "unset",
            "ok": False,
            "hysteria_active": "unknown",
            "hysteria_enabled": "unknown",
            "udp443": "unknown",
            "stdout": "",
            "stderr": "K2B_PRIVATE_VPN_HK_SSH_TARGET is unset",
        }
    if not usable_ssh_target(target):
        return {
            "target": "[invalid]",
            "ok": False,
            "hysteria_active": "unknown",
            "hysteria_enabled": "unknown",
            "udp443": "unknown",
            "stdout": "",
            "stderr": "K2B_PRIVATE_VPN_HK_SSH_TARGET is invalid",
        }
    key = os.environ.get("K2B_PRIVATE_VPN_HK_SSH_KEY", "")
    remote_command = os.environ.get(
        "K2B_PRIVATE_VPN_HK_TRACE_COMMAND",
        "printf 'hysteria_active='; "
        "(systemctl is-active hysteria-server 2>/dev/null || systemctl is-active hysteria 2>/dev/null || true); "
        "printf 'hysteria_enabled='; "
        "(systemctl is-enabled hysteria-server 2>/dev/null || systemctl is-enabled hysteria 2>/dev/null || true); "
        "printf 'udp443='; "
        "(ss -H -lunp 2>/dev/null | grep -E '(:|\\.)443\\b' || true)",
    )
    command = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8"]
    key_rejection = unusable_ssh_key_reason(key)
    if key_rejection:
        return {
            "target": target,
            "ok": False,
            "hysteria_active": "unknown",
            "hysteria_enabled": "unknown",
            "udp443": "unknown",
            "stdout": "",
            "stderr": key_rejection,
        }
    key_path = usable_ssh_key_path(key)
    if key_path:
        command.extend(["-i", key_path])
    command.extend([target, remote_command])
    result = run_command(command, bounded_env_int("K2B_PRIVATE_VPN_TRACE_TIMEOUT", 15, 5, 60))
    active = parse_key_value(result["stdout"], "hysteria_active")
    enabled = parse_key_value(result["stdout"], "hysteria_enabled")
    udp443 = parse_key_value(result["stdout"], "udp443")
    return {
        "target": target,
        "ok": result["ok"],
        "hysteria_active": active,
        "hysteria_enabled": enabled,
        "udp443": udp443,
        "stdout": result["stdout"],
        "stderr": result["stderr"],
    }


def classify(
    api_ok: bool,
    checks: dict[str, dict[str, Any]],
    primary: str,
    failover: str,
    emergency: str,
    aws: dict[str, Any] | None = None,
    server: dict[str, Any] | None = None,
) -> str:
    if not api_ok:
        return "router_mihomo_down"
    primary_ok = checks.get(primary, {}).get("ok") is True
    failover_ok = checks.get(failover, {}).get("ok") is True
    emergency_ok = checks.get(emergency, {}).get("ok") is True
    direct_ok = checks.get("DIRECT", {}).get("ok") is True
    if primary_ok:
        return "ok"
    if aws and aws.get("state") not in (None, "", "unknown", "running"):
        return "aws_down"
    if server and server.get("hysteria_active") not in (None, "", "unknown", "active"):
        return "hysteria_service_down"
    if not primary_ok and failover_ok:
        return "hk_only_down"
    if not primary_ok and not failover_ok and not emergency_ok and direct_ok:
        return "all_private_udp_down"
    if not direct_ok:
        return "wan_or_wifi_flap"
    return "unknown"


def format_check_line(label: str, check: dict[str, Any]) -> str:
    if check.get("ok"):
        latency = check.get("latency_ms")
        return f"{label}: OK {latency} ms" if latency is not None else f"{label}: OK"
    err = check.get("error") or "failed"
    if "timeout" in str(err).lower() or "504" in str(err):
        err = "timeout"
    return f"{label}: FAIL {err}"


def chain_text(chain: list[str]) -> str:
    clean = [str(item) for item in chain if item]
    return " -> ".join(clean) if clean else "unavailable"


def action_for(classification: str, chain: list[str], failover: str) -> str:
    if classification == "hk_only_down" and failover in chain:
        return "route is using TW fallback"
    if classification == "all_private_udp_down":
        return "private VPS leaves are down; DIRECT still proves router WAN is up"
    if classification == "router_mihomo_down":
        return "router Mihomo API is unreachable from the Mac Mini"
    if classification == "hysteria_service_down":
        return "HK server is reachable but Hysteria is not active"
    if classification == "aws_down":
        return "HK Lightsail is not running"
    return "check incident bundle before changing router state"


def failure_message(
    fail_count: int,
    chain: list[str],
    checks: dict[str, dict[str, Any]],
    primary: str,
    failover: str,
    emergency: str,
    classification: str,
    aws: dict[str, Any],
    server: dict[str, Any],
    hint: str,
    trace_path: str,
) -> str:
    lines = [
        "K2B VPN watchdog: private route degraded",
        "",
        f"Primary: HK failed {fail_count} consecutive 60s checks",
        f"Current chain (at check start): {chain_text(chain)}",
        "",
        "Checks:",
    ]
    for name in (primary, failover, emergency, "DIRECT"):
        lines.append(format_check_line(short_label(name, primary, failover, emergency), checks.get(name, {"ok": False, "error": "not checked"})))
    lines.extend(
        [
            "",
            f"AWS HK: {aws.get('state', 'unknown')}",
            f"HK server: hysteria {server.get('hysteria_active', 'unknown')}, UDP 443 {server.get('udp443', 'unknown')}",
            f"Classification: {classification}",
            f"Evidence: {evidence_text(hint)}",
            "",
            f"Action: {action_for(classification, chain, failover)}",
            f"Trace: {trace_path}",
        ]
    )
    return "\n".join(lines)


def recovery_message(
    recovery_count: int,
    chain: list[str],
    checks: dict[str, dict[str, Any]],
    primary: str,
    failover: str,
    emergency: str,
) -> str:
    failover_check = checks.get(failover, {})
    fallback = format_check_line(short_label(failover, primary, failover, emergency), failover_check)
    return "\n".join(
        [
            "K2B VPN watchdog: HK recovered",
            "",
            f"HK: OK for {recovery_count} consecutive checks",
            f"Current route (at check start): {chain_text(chain[-2:] if len(chain) >= 2 else chain)}",
            f"Fallback: {failover} remains {'healthy' if failover_check.get('ok') else 'not healthy'} ({fallback})",
        ]
    )


def emit_alert(alerts_file: str, send_alert_cmd: str, event: dict[str, Any]) -> None:
    event = copy.deepcopy(event)
    if isinstance(event.get("message"), str):
        event["message"] = bounded(event["message"], 3500)
    append_jsonl(alerts_file, event)
    if not send_alert_cmd:
        return
    try:
        completed = subprocess.run(
            [send_alert_cmd, "--event-json", "-"],
            input=json_dumps(event),
            text=True,
            timeout=20,
            capture_output=True,
            check=False,
        )
        if completed.returncode != 0:
            sys.stderr.write(f"send-alert failed rc={completed.returncode}: {bounded(completed.stderr, 1000)}\n")
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
        sys.stderr.write(f"send-alert failed: {exc}\n")


def write_incident(
    incident_dir: str,
    now: dt.datetime,
    classification_value: str,
    cheap: dict[str, Any],
    checks: dict[str, dict[str, Any]],
    state: dict[str, Any],
    edge: dict[str, Any],
) -> tuple[str, dict[str, Any], dict[str, Any], dict[str, Any], str, str]:
    Path(incident_dir).mkdir(parents=True, exist_ok=True)
    aws = trace_aws()
    router = trace_router()
    server = trace_server()
    incident_edge = copy.deepcopy(edge)
    if incident_edge.get("enabled") is not False:
        incident_edge["tailscale_status"] = trace_tailscale_status()
        incident_edge["tailscale_netcheck"] = trace_tailscale_netcheck()
    route_group = os.environ.get("K2B_PRIVATE_VPN_ROUTE_GROUP", "🎯 总模式")
    private_group = os.environ.get("K2B_PRIVATE_VPN_PRIVATE_GROUP", "🔒 私有线路")
    primary = os.environ.get("K2B_PRIVATE_VPN_PRIMARY", "🇭🇰 K2B-VPS-HK")
    failover = os.environ.get("K2B_PRIVATE_VPN_FAILOVER", "🇹🇼 K2B-VPS-TW")
    emergency = os.environ.get("K2B_PRIVATE_VPN_EMERGENCY", "🇲🇾 K2B-VPS-KL")
    final_classification = classify(
        cheap.get("api_ok") is True,
        checks,
        primary,
        failover,
        emergency,
        aws=aws,
        server=server,
    )
    if final_classification == "ok":
        final_classification = classification_value
    hint = evidence_hint(final_classification, incident_edge)
    filename = f"{now.strftime('%Y-%m-%dT%H%M%S')}.{now.microsecond:06d}Z-{uuid.uuid4().hex[:8]}-private-vpn.json"
    path = str(Path(incident_dir) / filename)
    payload = {
        "timestamp": isoformat(now),
        "classification": final_classification,
        "evidence_hint": hint,
        "chain": cheap.get("chain", []),
        "checks": checks,
        "edge": incident_edge,
        "aws": aws,
        "server": server,
        "router": router,
        "mihomo": {
            "version": cheap.get("version"),
            "proxies": cheap.get("proxies", {}),
            "route_group": route_group,
            "private_group": private_group,
        },
        "state_before": state,
    }
    atomic_write_json(path, payload)
    return path, aws, server, router, final_classification, hint


def incident_timestamp(path: Path) -> float:
    match = re.match(r"^(\d{4}-\d{2}-\d{2}T\d{6})(?:\.(\d{6}))?Z(?:-[0-9a-f]{8})?-private-vpn\.json$", path.name)
    if not match:
        return 0
    try:
        micros = match.group(2) or "000000"
        parsed = dt.datetime.strptime(f"{match.group(1)}.{micros}Z", "%Y-%m-%dT%H%M%S.%fZ").replace(tzinfo=dt.timezone.utc)
    except ValueError:
        return 0
    return parsed.timestamp()


def prune_incidents(incident_dir: str, protect_path: str | None = None) -> None:
    keep = int(os.environ.get("K2B_PRIVATE_VPN_INCIDENT_KEEP", "50"))
    max_age_days = int(os.environ.get("K2B_PRIVATE_VPN_INCIDENT_MAX_AGE_DAYS", "30"))
    root = Path(incident_dir)
    if not root.exists():
        return
    cutoff = dt.datetime.now(dt.timezone.utc).timestamp() - (max_age_days * 86400)
    files = list(root.glob("*private-vpn.json"))
    protected = None
    protected_raw = Path(protect_path) if protect_path else None
    if protected_raw:
        try:
            protected = protected_raw.resolve()
        except OSError:
            protected = protected_raw
    protected_abs = os.path.abspath(str(protected_raw)) if protected_raw else None
    if protected:
        filtered = []
        for path in files:
            try:
                if protected_raw and os.path.samefile(path, protected_raw):
                    continue
            except OSError:
                pass
            if protected_abs and os.path.abspath(str(path)) == protected_abs:
                continue
            try:
                if path.resolve() == protected:
                    continue
            except OSError:
                continue
            filtered.append(path)
        files = filtered
    files = sorted(files, key=lambda path: (incident_timestamp(path), path.name), reverse=True)
    for index, path in enumerate(files):
        try:
            ts = incident_timestamp(path)
            if index >= keep or (ts > 0 and ts < cutoff):
                path.unlink()
        except OSError:
            continue


def iter_lines_reverse(path: str, chunk_size: int = 65536):
    with open(path, "rb") as f:
        f.seek(0, os.SEEK_END)
        position = f.tell()
        buffer = b""
        while position > 0:
            read_size = min(chunk_size, position)
            position -= read_size
            f.seek(position)
            data = f.read(read_size)
            buffer = data + buffer
            lines = buffer.split(b"\n")
            buffer = lines[0]
            for line in reversed(lines[1:]):
                if line:
                    yield line.decode("utf-8", errors="replace")
        if buffer:
            yield buffer.decode("utf-8", errors="replace")


def has_unresolved_degraded_alert(alerts_file: str, now: dt.datetime) -> bool:
    if not os.path.exists(alerts_file):
        return False
    try:
        max_age_hours = int(os.environ.get("K2B_PRIVATE_VPN_ALERT_RECOVERY_MAX_AGE_HOURS", "168"))
        cutoff = now.astimezone(dt.timezone.utc) - dt.timedelta(hours=max_age_hours)
        for line in iter_lines_reverse(alerts_file):
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("type") not in {"private_vpn_degraded", "private_vpn_recovery"}:
                continue
            try:
                alerted_at = dt.datetime.fromisoformat(str(event.get("timestamp", "")).replace("Z", "+00:00"))
            except ValueError:
                continue
            if alerted_at <= cutoff:
                return False
            return event.get("type") == "private_vpn_degraded"
    except OSError:
        return False
    return False


def recover_state_from_health_log(health_log: str, now: dt.datetime) -> dict[str, Any]:
    if not os.path.exists(health_log):
        return {}
    cutoff = now.astimezone(dt.timezone.utc) - dt.timedelta(minutes=10)
    failures: list[dict[str, Any]] = []
    try:
        for line in iter_lines_reverse(health_log):
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(row, dict):
                continue
            try:
                ts = dt.datetime.fromisoformat(str(row.get("timestamp", "")).replace("Z", "+00:00"))
            except ValueError:
                continue
            if ts < cutoff:
                break
            if row.get("overall_ok") is True:
                break
            failures.append(row)
    except OSError:
        return {}
    if not failures:
        return {}
    oldest = failures[-1]
    return {
        "primary_fail_count": len(failures),
        "primary_recovery_count": 0,
        "status": "degraded",
        "incident_alerted": bool(oldest.get("trace")) or bool(failures[0].get("trace")),
        "recovery_alerted": False,
        "outage_started_at": oldest.get("outage_started_at") or oldest.get("timestamp") or isoformat(now),
    }


def latest_private_alert_type(alerts_file: str) -> str | None:
    if not os.path.exists(alerts_file):
        return None
    try:
        for line in iter_lines_reverse(alerts_file):
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(event, dict) and event.get("type") in {"private_vpn_degraded", "private_vpn_recovery"}:
                return str(event.get("type"))
    except OSError:
        return None
    return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-file", default=os.path.expanduser("~/Library/Application Support/k2b-router-watchdog/private-vpn-state.json"))
    parser.add_argument("--health-log", default=os.path.expanduser("~/Library/Logs/k2b-router-watchdog/private-vpn-health.jsonl"))
    parser.add_argument("--incident-dir", default=os.path.expanduser("~/Library/Logs/k2b-router-watchdog/incidents"))
    parser.add_argument("--alerts-file", default=os.path.expanduser("~/Library/Logs/k2b-router-watchdog/private-vpn-alerts.jsonl"))
    parser.add_argument("--lock-file", default=None)
    parser.add_argument("--send-alert-cmd", default="")
    parser.add_argument("--now", default=None)
    args = parser.parse_args()
    lock_file = args.lock_file or str(Path(args.state_file).with_name("private-vpn-watchdog.lock"))
    lock_handle = acquire_lock(lock_file)
    if lock_handle is None:
        return 0
    atexit.register(cleanup_lock, lock_file, lock_handle)
    install_lock_signal_handlers(lock_file, lock_handle)

    now = iso_now(args.now)
    if not os.environ.get("MIHOMO_API_BASE") or not os.environ.get("MIHOMO_API_SECRET"):
        message = "missing MIHOMO_API_BASE or MIHOMO_API_SECRET"
        append_jsonl(args.health_log, {
            "timestamp": isoformat(now),
            "overall_ok": False,
            "classification": "configuration_error",
            "message": message,
            "checks": {},
        })
        sys.stderr.write(message + "\n")
        return 2
    route_group = os.environ.get("K2B_PRIVATE_VPN_ROUTE_GROUP", "🎯 总模式")
    private_group = os.environ.get("K2B_PRIVATE_VPN_PRIVATE_GROUP", "🔒 私有线路")
    primary = os.environ.get("K2B_PRIVATE_VPN_PRIMARY", "🇭🇰 K2B-VPS-HK")
    failover = os.environ.get("K2B_PRIVATE_VPN_FAILOVER", "🇹🇼 K2B-VPS-TW")
    emergency = os.environ.get("K2B_PRIVATE_VPN_EMERGENCY", "🇲🇾 K2B-VPS-KL")
    fail_threshold = int(os.environ.get("K2B_PRIVATE_VPN_FAIL_THRESHOLD", "2"))
    recovery_threshold = int(os.environ.get("K2B_PRIVATE_VPN_RECOVERY_THRESHOLD", "5"))

    client = MihomoClient(
        os.environ.get("MIHOMO_API_BASE", "http://192.168.50.1:9990"),
        os.environ.get("MIHOMO_API_SECRET", ""),
        bounded_env_float("K2B_PRIVATE_VPN_MIHOMO_TIMEOUT", 8.0, 1.0, 15.0),
    )
    state = read_json(args.state_file)
    if not state:
        state = recover_state_from_health_log(args.health_log, now)
    state.setdefault("primary_fail_count", 0)
    state.setdefault("primary_recovery_count", 0)
    state.setdefault("status", "ok")
    state.setdefault("incident_alerted", False)
    state.setdefault("recovery_alerted", False)

    cheap = cheap_check(client, route_group, private_group, primary, failover, emergency)
    edge = edge_diagnostics(include_netcheck=False)
    checks = cheap.get("checks", {}) if isinstance(cheap.get("checks"), dict) else {}
    preliminary = classify(cheap.get("api_ok") is True, checks, primary, failover, emergency)
    primary_ok = preliminary == "ok"

    alert_event: dict[str, Any] | None = None
    trace_path = None
    aws: dict[str, Any] = {"state": "unknown"}
    server: dict[str, Any] = {"hysteria_active": "unknown", "udp443": "unknown"}
    final_classification = preliminary
    hint = "insufficient_edge_evidence"

    if primary_ok:
        previous_alerted = bool(state.get("incident_alerted")) or has_unresolved_degraded_alert(args.alerts_file, now)
        previous_fail_count = int(state.get("primary_fail_count", 0))
        if previous_fail_count > 0 or previous_alerted:
            state["primary_recovery_count"] = int(state.get("primary_recovery_count", 0)) + 1
        else:
            state["primary_recovery_count"] = 0
        state["primary_fail_count"] = 0
        if cheap.get("api_ok") is True and previous_alerted and state["primary_recovery_count"] >= recovery_threshold and not state.get("recovery_alerted"):
            message = recovery_message(state["primary_recovery_count"], cheap.get("chain", []), checks, primary, failover, emergency)
            alert_event = {
                "timestamp": isoformat(now),
                "type": "private_vpn_recovery",
                "check": "private_vpn",
                "classification": "ok",
                "message": message,
            }
            state["incident_alerted"] = False
            state["recovery_alerted"] = True
            state["status"] = "ok"
        elif previous_fail_count > 0 and not previous_alerted:
            state["status"] = "ok"
            state["incident_alerted"] = False
            state["recovery_alerted"] = False
        elif previous_alerted:
            state["status"] = "recovering"
        else:
            state["status"] = "ok"
            state["incident_alerted"] = False
            state["recovery_alerted"] = False
    else:
        previous_alerted = bool(state.get("incident_alerted")) or has_unresolved_degraded_alert(args.alerts_file, now)
        if int(state.get("primary_fail_count", 0)) == 0:
            state["outage_started_at"] = isoformat(now)
            state["recovery_alerted"] = False
        state["primary_fail_count"] = int(state.get("primary_fail_count", 0)) + 1
        state["primary_recovery_count"] = 0
        state["status"] = "degraded"
        if state["primary_fail_count"] >= fail_threshold and not state.get("incident_alerted") and not previous_alerted:
            trace_path, aws, server, _router, final_classification, hint = write_incident(
                args.incident_dir,
                now,
                preliminary,
                cheap,
                checks,
                copy.deepcopy(state),
                edge,
            )
            prune_incidents(args.incident_dir, protect_path=trace_path)
            message = failure_message(
                state["primary_fail_count"],
                cheap.get("chain", []),
                checks,
                primary,
                failover,
                emergency,
                final_classification,
                aws,
                server,
                hint,
                trace_path,
            )
            alert_event = {
                "timestamp": isoformat(now),
                "type": "private_vpn_degraded",
                "check": "private_vpn",
                "classification": final_classification,
                "trace": trace_path,
                "message": message,
            }
            state["incident_alerted"] = True
            state["recovery_alerted"] = False
            state["last_incident_path"] = trace_path
            state["last_classification"] = final_classification

    health_row = {
        "timestamp": isoformat(now),
        "overall_ok": primary_ok,
        "classification": final_classification,
        "chain": cheap.get("chain", []),
        "checks": checks,
        "edge": edge,
        "fail_count": state.get("primary_fail_count", 0),
        "recovery_count": state.get("primary_recovery_count", 0),
    }
    if state.get("primary_fail_count", 0):
        health_row["outage_started_at"] = state.get("outage_started_at") or isoformat(now)
    if trace_path:
        health_row["trace"] = trace_path
        health_row["aws_state"] = aws.get("state", "unknown")
    append_jsonl(args.health_log, health_row)
    if alert_event:
        emit_alert(args.alerts_file, args.send_alert_cmd, alert_event)
    atomic_write_json(args.state_file, state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
