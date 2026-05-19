#!/usr/bin/env bash
set -euo pipefail

python3 - <<'PY'
import datetime as dt
import json
import os
import pathlib
import subprocess
import time
from typing import Any


REQUIRED = ["k2b-remote", "k2b-dashboard", "k2b-observer-loop"]
REMOTE_NAME = os.environ.get("K2B_REMOTE_PM2_NAME", "k2b-remote")
HEALTH_FILE = os.environ.get(
    "K2B_REMOTE_HEALTH_FILE",
    os.path.expanduser("~/Projects/K2B/k2b-remote/store/health.json"),
)
MAX_AGE_SECONDS = int(os.environ.get("K2B_REMOTE_HEARTBEAT_MAX_AGE_SECONDS", "600"))
SETTLE_SECONDS = float(os.environ.get("K2B_REMOTE_RESTART_SETTLE_SECONDS", "5"))
LOG_DIR = os.environ.get(
    "K2B_ROUTER_WATCHDOG_LOG_DIR",
    os.path.expanduser("~/Library/Logs/k2b-router-watchdog"),
)
RESTART_LOG = os.environ.get(
    "K2B_ROUTER_WATCHDOG_RESTART_LOG",
    os.path.join(LOG_DIR, "restarts.jsonl"),
)


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def emit(name: str, ok: bool, latency_ms: int, severity: str, message: str, details: dict[str, Any] | None = None) -> None:
    print(json.dumps({
        "name": name,
        "ok": ok,
        "alertable": True,
        "latency_ms": latency_ms,
        "severity": severity,
        "message": message,
        "details": details or {},
    }, ensure_ascii=False, separators=(",", ":")))


def run_pm2_jlist() -> tuple[list[dict[str, Any]] | None, str | None]:
    try:
        proc = subprocess.run(["pm2", "jlist"], capture_output=True, text=True, timeout=5)
    except FileNotFoundError:
        return None, "pm2 command not found"
    except subprocess.TimeoutExpired:
        return None, "pm2 jlist timed out"
    if proc.returncode != 0:
        return None, "pm2 jlist failed"
    try:
        data = json.loads(proc.stdout)
    except Exception:
        return None, "pm2 jlist returned invalid JSON"
    if not isinstance(data, list):
        return None, "pm2 jlist returned non-list JSON"
    return [item for item in data if isinstance(item, dict)], None


def heartbeat_epoch_seconds(path: str) -> tuple[float | None, str | None]:
    """Return the heartbeat's recorded epoch in SECONDS (not age). Returns
    the underlying file value so callers can compare two reads of the same
    file without picking up wall-clock drift between the reads.
    """
    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
    except FileNotFoundError:
        return None, "health file missing"
    except Exception:
        return None, "health file corrupt"

    # Guard against valid-but-non-dict JSON (`null`, `[]`, bare string). Without
    # this, the `raw.get(...)` below would AttributeError and crash the entire
    # pm2.sh tick -- which is now the sole watchdog after Phase 3 retired the
    # standalone health-check.
    if not isinstance(raw, dict):
        return None, "health file not object"

    value = raw.get("epoch")
    if value is None:
        value = raw.get("last_heartbeat")
    if value is None:
        return None, "health file missing epoch"

    try:
        if isinstance(value, (int, float)):
            # health-check.sh stores epoch in milliseconds. Accept seconds too.
            seconds = float(value) / 1000.0 if float(value) > 10_000_000_000 else float(value)
            return seconds, None
        if isinstance(value, str):
            text = value.strip()
            if text.isdigit():
                seconds = float(text) / 1000.0 if float(text) > 10_000_000_000 else float(text)
                return seconds, None
            parsed = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=dt.timezone.utc)
            return parsed.timestamp(), None
    except Exception:
        return None, "health timestamp invalid"
    return None, "health timestamp invalid"


def heartbeat_age_seconds(path: str) -> tuple[float | None, str | None]:
    """Age = now - heartbeat epoch. Returns (age_seconds, error)."""
    epoch, err = heartbeat_epoch_seconds(path)
    if err:
        return None, err
    if epoch is None:
        return None, "health timestamp invalid"
    return time.time() - epoch, None


def analyze(procs: list[dict[str, Any]], allow_restart: bool) -> dict[str, Any]:
    by_name = {p.get("name"): p for p in procs}
    missing = [name for name in REQUIRED if name not in by_name]
    offline: list[str] = []
    restart_times: dict[str, int] = {}
    restart_mode = None
    restart_reason = None
    health_detail: dict[str, Any] = {"path": HEALTH_FILE, "max_age_seconds": MAX_AGE_SECONDS}

    for name in REQUIRED:
        proc = by_name.get(name)
        if not proc:
            continue
        env = proc.get("pm2_env") or {}
        restart_times[name] = int(env.get("restart_time") or 0)
        status = env.get("status")
        if status != "online":
            offline.append(f"{name}:{status}")
            if name == REMOTE_NAME:
                restart_mode = "stopped"
                restart_reason = f"{REMOTE_NAME} pm2 status is {status}"

    remote = by_name.get(REMOTE_NAME)
    if remote and (remote.get("pm2_env") or {}).get("status") == "online":
        age, err = heartbeat_age_seconds(HEALTH_FILE)
        if err:
            health_detail["status"] = err
            offline.append(f"{REMOTE_NAME}:{err.replace(' ', '_')}")
        elif age is not None:
            health_detail["age_seconds"] = int(age)
            health_detail["status"] = "stale" if age > MAX_AGE_SECONDS else "fresh"
            if age > MAX_AGE_SECONDS:
                offline.append(f"{REMOTE_NAME}:heartbeat_stale_{int(age)}s")
                restart_mode = "zombie"
                restart_reason = f"{REMOTE_NAME} heartbeat stale ({int(age)}s old)"

    pieces: list[str] = []
    if missing:
        pieces.append("missing " + ",".join(missing))
    if offline:
        pieces.append("offline " + ",".join(offline))

    ok = not pieces
    return {
        "ok": ok,
        "message": "required pm2 services online" if ok else "; ".join(pieces),
        "restart_times": restart_times,
        "health": health_detail,
        "restart_mode": restart_mode if allow_restart else None,
        "restart_reason": restart_reason,
    }


def append_restart_log(event: dict[str, Any]) -> None:
    pathlib.Path(RESTART_LOG).parent.mkdir(parents=True, exist_ok=True)
    with open(RESTART_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n")


start = time.time()
procs, error = run_pm2_jlist()
latency_ms = int((time.time() - start) * 1000)
if error:
    emit("pm2_daemon", False, latency_ms, "fail", error, {})
    emit("pm2_services", False, latency_ms, "fail", error, {})
    raise SystemExit(0)

analysis = analyze(procs or [], allow_restart=True)
restart_event = None
restart_log_error = None
if analysis.get("restart_mode"):
    mode = str(analysis["restart_mode"])
    reason = str(analysis.get("restart_reason") or mode)
    restart_started = utc_now()
    # Capture the pre-restart heartbeat epoch (file's recorded epoch, NOT
    # derived from time.time() -- wall-clock drift between pre/post reads
    # would mask the case where the file is unchanged). Without this gate,
    # a process that restarts but crashes during init re-uses the stale
    # heartbeat (still under MAX_AGE_SECONDS) and is falsely reported recovered.
    pre_epoch, _pre_err = heartbeat_epoch_seconds(HEALTH_FILE)
    try:
        restart_proc = subprocess.run(["pm2", "restart", REMOTE_NAME], capture_output=True, text=True, timeout=30)
        restart_rc = restart_proc.returncode
        restart_output = (restart_proc.stdout + restart_proc.stderr).strip()
    except Exception as exc:
        restart_rc = 1
        restart_output = repr(exc)

    if restart_rc == 0 and SETTLE_SECONDS > 0:
        time.sleep(SETTLE_SECONDS)

    after_procs, after_error = run_pm2_jlist()
    if after_error:
        after_analysis = {
            "ok": False,
            "message": after_error,
            "restart_times": analysis.get("restart_times") or {},
            "health": analysis.get("health") or {},
        }
    else:
        after_analysis = analyze(after_procs or [], allow_restart=False)

    # Post-restart heartbeat gate: if pre-restart heartbeat existed, require
    # the post-restart heartbeat file's epoch to be strictly newer. Otherwise
    # the bot may have restarted but crashed during init before writing a new
    # heartbeat, and the stale-but-still-within-MAX_AGE heartbeat would mask
    # it. We compare the FILE'S recorded epoch on both reads (not derived
    # from time.time()), so wall-clock drift between reads cannot falsely
    # advance the value.
    heartbeat_gate_failed = False
    if after_analysis.get("ok") and pre_epoch is not None:
        post_epoch, post_err = heartbeat_epoch_seconds(HEALTH_FILE)
        if post_err:
            after_analysis["ok"] = False
            after_analysis["message"] = f"restart attempted; post-restart heartbeat unreadable ({post_err})"
            heartbeat_gate_failed = True
        elif post_epoch is None or post_epoch <= pre_epoch:
            after_analysis["ok"] = False
            after_analysis["message"] = f"restart attempted; heartbeat not yet advanced (pre={int(pre_epoch)} post={int(post_epoch) if post_epoch else 'none'})"
            heartbeat_gate_failed = True

    restart_event = {
        "timestamp": restart_started,
        "service": REMOTE_NAME,
        "mode": mode,
        "reason": reason,
        "result": "success" if restart_rc == 0 and after_analysis.get("ok") else "failed",
        "pm2_exit_code": restart_rc,
        "post_restart_ok": bool(after_analysis.get("ok")),
        "post_restart_message": after_analysis.get("message"),
        "heartbeat_gate_failed": heartbeat_gate_failed,
    }
    if pre_epoch is not None:
        restart_event["pre_restart_heartbeat_epoch"] = int(pre_epoch)
    if restart_output:
        restart_event["pm2_output"] = restart_output[-500:]
    # Wrap restart-log persistence so write failures don't abort the entire
    # watchdog tick after pm2 has already been mutated. Failure detail is
    # propagated into the emitted check details so operators see it.
    try:
        append_restart_log(restart_event)
    except Exception as log_exc:
        restart_log_error = f"restart_log_write_failed: {log_exc!r}"

    analysis = after_analysis
    prefix = f"auto-restart {REMOTE_NAME} {mode} rc={restart_rc}"
    analysis["message"] = f"{prefix}; {analysis.get('message')}"

details = {
    "pm2_restart_times": analysis.get("restart_times") or {},
    "k2b_remote_health": analysis.get("health") or {},
}
if restart_event:
    details["auto_restart"] = {
        "service": restart_event["service"],
        "mode": restart_event["mode"],
        "result": restart_event["result"],
        "post_restart_ok": restart_event["post_restart_ok"],
        "heartbeat_gate_failed": restart_event.get("heartbeat_gate_failed", False),
    }
if restart_log_error:
    details["restart_log_error"] = restart_log_error

emit("pm2_daemon", True, latency_ms, "ok", "pm2 daemon returned valid JSON", {})
emit(
    "pm2_services",
    bool(analysis.get("ok")),
    latency_ms,
    "ok" if analysis.get("ok") else "fail",
    str(analysis.get("message") or ""),
    details,
)
PY
