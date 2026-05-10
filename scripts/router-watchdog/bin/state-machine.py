#!/usr/bin/env python3
import argparse
import contextlib
import fcntl
import json
import os
from datetime import datetime, timezone


EXTERNAL_PARTITION_CHECKS = {"chatgpt_https", "chatgpt_ws", "claude_https", "telegram_api"}
WARNING_ONLY_CHECKS = {"openai_node", "tailscale_direct"}


def parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def parse_duration(value: str) -> int:
    value = value.strip().lower()
    if value.endswith("s"):
        return int(value[:-1])
    if value.endswith("m"):
        return int(value[:-1]) * 60
    if value.endswith("h"):
        return int(value[:-1]) * 3600
    return int(value)


@contextlib.contextmanager
def file_lock(lock_path: str):
    """Exclusive flock on a sidecar lock file.

    Blocks (does not skip) when another process holds the lock so concurrent
    state-machine ticks serialize their read-modify-write of state.json
    instead of racing. fcntl.flock works on macOS where bash flock(1) does
    not ship by default.
    """
    os.makedirs(os.path.dirname(lock_path) or ".", exist_ok=True)
    handle = open(lock_path, "a+", encoding="utf-8")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        handle.close()


def load_json(path: str, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default


def write_atomic_json(path: str, obj) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")
    os.replace(tmp, path)


def append_jsonl(path: str, obj: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False, separators=(",", ":")) + "\n")


def write_jsonl(path: str, items: list[dict]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for item in items:
            f.write(json.dumps(item, ensure_ascii=False, separators=(",", ":")) + "\n")


def default_check_state():
    return {
        "status": "unknown",
        "since": None,
        "consecutive_fails": 0,
        "last_alert_at": None,
        "alert_count_in_outage": 0,
    }


def alert_message(kind: str, name: str, check_state: dict, result: dict, ts: str) -> str:
    if kind == "failure":
        return (
            f"K2B router watchdog: {name} has failed 3 consecutive ticks "
            f"since {check_state.get('since') or ts}. {result.get('message') or 'No detail.'}"
        )
    if kind == "repeat_failure":
        return (
            f"K2B router watchdog: {name} is still failing "
            f"since {check_state.get('since') or ts}. {result.get('message') or 'No detail.'}"
        )
    return f"K2B router watchdog: {name} recovered at {ts}."


def normalize_results(raw_results: list[dict], state: dict) -> list[dict]:
    previous_restarts = state.get("_pm2_restart_times") or {}
    normalized = []
    for result in raw_results:
        item = dict(result)
        item.setdefault("ok", False)
        item.setdefault("alertable", item.get("name") not in WARNING_ONLY_CHECKS)
        item.setdefault("severity", "ok" if item["ok"] else "fail")
        item.setdefault("latency_ms", None)
        item.setdefault("message", "")
        item.setdefault("details", {})
        if item.get("name") == "pm2_services" and item.get("ok"):
            current = (item.get("details") or {}).get("pm2_restart_times") or {}
            jumped = []
            for name, value in current.items():
                prev = previous_restarts.get(name)
                if isinstance(prev, int) and isinstance(value, int) and value - prev >= 5:
                    jumped.append(f"{name}:{prev}->{value}")
            if jumped:
                item["ok"] = False
                item["severity"] = "fail"
                item["message"] = "pm2 restart budget exceeded: " + ",".join(jumped)
        normalized.append(item)
    for item in normalized:
        if item.get("name") == "pm2_services":
            current = (item.get("details") or {}).get("pm2_restart_times") or {}
            if current:
                state["_pm2_restart_times"] = current
    return normalized


def transition_partition(state: dict, results_by_name: dict, ts: str) -> tuple[bool, list[dict]]:
    actions = []
    partition_now = all(not results_by_name.get(name, {}).get("ok", False) for name in EXTERNAL_PARTITION_CHECKS)
    telegram_ok = results_by_name.get("telegram_api", {}).get("ok", False)
    part = state.get("_network_partition") or {
        "status": "ok",
        "since": None,
        "consecutive_fails": 0,
        "queued": False,
    }

    if partition_now:
        if part.get("status") != "fail":
            part = {"status": "fail", "since": ts, "consecutive_fails": 1, "queued": False}
        else:
            part["consecutive_fails"] = int(part.get("consecutive_fails") or 0) + 1
        if part["consecutive_fails"] >= 2 and not part.get("queued"):
            actions.append({"type": "append_partition", "start": part.get("since") or ts, "timestamp": ts})
            part["queued"] = True
    else:
        if part.get("status") == "fail" and part.get("queued") and telegram_ok:
            actions.append({"type": "drain_partition", "end": ts})
            part = {"status": "ok", "since": None, "consecutive_fails": 0, "queued": False}
        elif part.get("status") == "fail" and telegram_ok:
            part = {"status": "ok", "since": None, "consecutive_fails": 0, "queued": False}
        elif part.get("status") == "fail":
            part["consecutive_fails"] = int(part.get("consecutive_fails") or 0)
    state["_network_partition"] = part
    suppress_alerts = part.get("status") == "fail" and part.get("consecutive_fails", 0) >= 2
    return suppress_alerts, actions


def transition_checks(state: dict, results: list[dict], ts: str, backoffs: list[int], suppress_alerts: bool) -> list[dict]:
    alerts = []
    now = parse_ts(ts)
    for result in results:
        name = result["name"]
        if not result.get("alertable", True):
            continue
        check_state = dict(state.get(name) or default_check_state())
        ok = bool(result.get("ok"))
        if ok:
            if check_state.get("status") == "fail" and int(check_state.get("alert_count_in_outage") or 0) > 0 and not suppress_alerts:
                alerts.append({
                    "timestamp": ts,
                    "type": "recovery",
                    "check": name,
                    "status": "recovered",
                    "message": alert_message("recovery", name, check_state, result, ts),
                })
            state[name] = default_check_state() | {
                "status": "ok",
                "since": ts,
                "consecutive_fails": 0,
            }
            continue

        if check_state.get("status") != "fail":
            check_state = default_check_state() | {
                "status": "fail",
                "since": ts,
                "consecutive_fails": 1,
            }
        else:
            check_state["consecutive_fails"] = int(check_state.get("consecutive_fails") or 0) + 1

        count = int(check_state.get("alert_count_in_outage") or 0)
        if not suppress_alerts:
            if check_state["consecutive_fails"] >= 3 and count == 0:
                alerts.append({
                    "timestamp": ts,
                    "type": "failure",
                    "check": name,
                    "status": "failing",
                    "message": alert_message("failure", name, check_state, result, ts),
                })
                check_state["last_alert_at"] = ts
                check_state["alert_count_in_outage"] = 1
            elif count > 0 and count < 5 and check_state.get("last_alert_at"):
                threshold = backoffs[min(count - 1, len(backoffs) - 1)]
                elapsed = int((now - parse_ts(check_state["last_alert_at"])).total_seconds())
                if elapsed >= threshold:
                    alerts.append({
                        "timestamp": ts,
                        "type": "repeat_failure",
                        "check": name,
                        "status": "failing",
                        "message": alert_message("repeat_failure", name, check_state, result, ts),
                    })
                    check_state["last_alert_at"] = ts
                    check_state["alert_count_in_outage"] = count + 1
        state[name] = check_state
    return alerts


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-file", required=True)
    parser.add_argument("--results-file", required=True)
    parser.add_argument("--timestamp", required=True)
    parser.add_argument("--backoff", required=True)
    parser.add_argument("--alerts-file", required=True)
    parser.add_argument("--partition-actions-file", required=True)
    parser.add_argument("--health-log", required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    # Lock the entire read-modify-write of state.json so concurrent ticks
    # cannot lose updates by interleaving load / transition / write. The
    # sidecar lock file lives next to state.json. Blocking flock; ticks
    # queue rather than skip (they're cheap and infrequent).
    state_dir = os.path.dirname(args.state_file) or "."
    state_lock_path = os.path.join(state_dir, os.path.basename(args.state_file) + ".lock")
    with file_lock(state_lock_path):
        state = load_json(args.state_file, {})
        with open(args.results_file, encoding="utf-8") as f:
            raw_results = json.load(f)
        results = normalize_results(raw_results, state)
        results_by_name = {item["name"]: item for item in results}
        backoffs = [parse_duration(item) for item in args.backoff.split(",") if item.strip()]

        suppress_alerts, partition_actions = transition_partition(state, results_by_name, args.timestamp)
        alerts = transition_checks(state, results, args.timestamp, backoffs, suppress_alerts)

        overall_ok = all(item.get("ok", False) for item in results)
        health_event = {
            "timestamp": args.timestamp,
            "overall_ok": overall_ok,
            "network_partition": state.get("_network_partition"),
            "checks": {
                item["name"]: {
                    "ok": item.get("ok", False),
                    "alertable": item.get("alertable", True),
                    "severity": item.get("severity"),
                    "latency_ms": item.get("latency_ms"),
                    "message": item.get("message"),
                    "details": item.get("details") or {},
                }
                for item in results
            },
        }

        write_jsonl(args.alerts_file, alerts)
        write_jsonl(args.partition_actions_file, partition_actions)

        if args.dry_run:
            print(json.dumps(health_event, ensure_ascii=False, indent=2, sort_keys=True))
            return 0

        write_atomic_json(args.state_file, state)
        append_jsonl(args.health_log, health_event)
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
