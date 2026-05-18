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
        # pending_initial_alert: set True when an initial-failure alert is appended
        # to the alerts list (inside `if not suppress_alerts:` so partition-suppressed
        # outages do NOT set it). Cleared on --confirm-delivered for the failure
        # alert, or on state reset (recovery confirmed, or partition-path immediate
        # reset). The missed-outage recovery branch requires this flag to be True,
        # so partition-suppressed outages don't emit misleading "briefly failed"
        # recoveries when the partition clears.
        "pending_initial_alert": False,
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
    # Recovery: if count == 0, the initial failure alert was generated but never
    # confirmed delivered (Telegram blip during the failure window). Tell Keith
    # what he missed so the recovery isn't a context-free "recovered at X".
    prior_count = int(check_state.get("alert_count_in_outage") or 0)
    if prior_count == 0:
        consec = int(check_state.get("consecutive_fails") or 0)
        since = check_state.get("since") or ts
        return (
            f"K2B router watchdog: {name} briefly failed "
            f"({consec} consecutive ticks since {since}) and recovered at {ts}. "
            "Initial failure alert delivery was not confirmed (Telegram may have been transiently unreachable)."
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


def transition_partition(state: dict, results_by_name: dict, ts: str) -> tuple[bool, bool, list[dict]]:
    """Decide partition state. Returns (suppress_alerts, partition_now, actions).

    partition_now is exposed so transition_checks can refrain from setting
    pending_initial_alert on the first partition tick (where suppress_alerts
    is not yet True). When partition is confirmed (queued threshold reached),
    we also clear any carry-in pending_initial_alert from the 4 external
    partition checks — that handles the case where a check was already at
    consecutive_fails=2 with pending=True before partition started; without
    this clear, the eventual partition recovery would fire a misleading
    per-check 'briefly failed' alongside network_partition_recovered.
    """
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
            # Clear carry-in pending_initial_alert for the 4 external partition
            # checks. The partition queue's network_partition_recovered alert
            # will cover the recovery; we don't want a duplicate per-check
            # "briefly failed" recovery for outages that ended up being part of
            # a partition.
            for chk in EXTERNAL_PARTITION_CHECKS:
                chk_state = state.get(chk)
                if chk_state and chk_state.get("pending_initial_alert"):
                    chk_state["pending_initial_alert"] = False
                    state[chk] = chk_state
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
    return suppress_alerts, partition_now, actions


def transition_checks(state: dict, results: list[dict], ts: str, backoffs: list[int], suppress_alerts: bool, partition_now: bool = False) -> list[dict]:
    """Generate alert dicts for state transitions; defer alert-count and last-alert-at
    mutations until --confirm-delivered is called after a successful Telegram send.

    Why deferred: prior to 2026-05-18, this function set alert_count_in_outage and
    last_alert_at the moment an alert was generated, regardless of whether the
    downstream send-alert.sh call actually delivered to Telegram. When delivery
    silently failed (e.g., transient network blip + send-alert.sh's pre-fix exit-1
    on single-target failure), the state said "alert sent" but Keith got nothing,
    and the watchdog then waited the full +30m backoff before retrying. Now the
    only state changes here are consecutive_fails and the status fail/ok flag;
    alert_count_in_outage and last_alert_at are advanced only on confirmed delivery
    so a failed send naturally re-fires on the next tick.
    """
    alerts = []
    now = parse_ts(ts)
    for result in results:
        name = result["name"]
        if not result.get("alertable", True):
            continue
        check_state = dict(state.get(name) or default_check_state())
        ok = bool(result.get("ok"))
        if ok:
            # Fire recovery alert when EITHER:
            #   (a) count > 0 — at least one prior alert was delivered to Keith, so
            #       he needs to know the outage cleared (original behavior), OR
            #   (b) pending_initial_alert == True AND count == 0 — the failure
            #       alert was actually generated (appended to alerts list) but
            #       delivery was never confirmed (silent-drop edge case Codex
            #       2026-05-18 second-pass HIGH-1). The flag is set only inside
            #       the `not suppress_alerts` branch, so partition-suppressed
            #       outages do NOT trigger this (Codex 2026-05-18 third-pass MED-1).
            #       The recovery message in case (b) includes consecutive_fails +
            #       since so Keith sees the missed-outage context.
            missed_outage_pending = (
                bool(check_state.get("pending_initial_alert"))
                and int(check_state.get("alert_count_in_outage") or 0) == 0
            )
            should_fire_recovery = (
                check_state.get("status") == "fail"
                and not suppress_alerts
                and (
                    int(check_state.get("alert_count_in_outage") or 0) > 0
                    or missed_outage_pending
                )
            )
            if should_fire_recovery:
                alerts.append({
                    "timestamp": ts,
                    "type": "recovery",
                    "check": name,
                    "status": "recovered",
                    "outage_since": check_state.get("since"),
                    "message": alert_message("recovery", name, check_state, result, ts),
                })
                # Defer state reset to --confirm-delivered so a recovery-delivery
                # failure re-fires the recovery on the next tick.
            else:
                # Either no prior threshold-crossing or partition-suppressed.
                # Safe to reset state immediately.
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
                # Generate initial-failure alert; defer count/last_alert_at to
                # --confirm-delivered so an undelivered alert re-fires next tick.
                # Set pending_initial_alert so the recovery branch knows a real
                # initial alert WAS emitted (vs partition-suppressed). This
                # mutation is NOT delivery-state — it tracks generation, not
                # confirmation, so it can be safely set on alert append.
                # EXCEPT on partition_now=True tick 1: the alert is still
                # emitted (suppress_alerts is False yet), but we don't set
                # pending so the eventual partition recovery won't fire a
                # duplicate per-check missed-outage alert (Codex 2026-05-18
                # fourth-pass MED: partition tick 1 carry-in).
                alerts.append({
                    "timestamp": ts,
                    "type": "failure",
                    "check": name,
                    "status": "failing",
                    "outage_since": check_state.get("since"),
                    "message": alert_message("failure", name, check_state, result, ts),
                })
                if not (partition_now and name in EXTERNAL_PARTITION_CHECKS):
                    check_state["pending_initial_alert"] = True
            elif count > 0 and count < 5 and check_state.get("last_alert_at"):
                threshold = backoffs[min(count - 1, len(backoffs) - 1)]
                elapsed = int((now - parse_ts(check_state["last_alert_at"])).total_seconds())
                if elapsed >= threshold:
                    # Generate repeat_failure alert; defer mutation to --confirm-delivered.
                    alerts.append({
                        "timestamp": ts,
                        "type": "repeat_failure",
                        "check": name,
                        "status": "failing",
                        "outage_since": check_state.get("since"),
                        "message": alert_message("repeat_failure", name, check_state, result, ts),
                    })
        state[name] = check_state
    return alerts


def apply_confirmed(state: dict, alert: dict) -> None:
    """Apply state mutation for one alert known to have been delivered.

    Idempotent: re-applying the same alert does not double-count. Only
    per-check alerts generated by transition_checks (failure / repeat_failure /
    recovery) are handled here. Network partition, auto-switch, digest, and
    other alert types are state-free or have state managed by their own modules.

    Stale-safe: alerts carry the outage epoch (outage_since = state.since at
    alert generation time). If the current outage epoch differs (recovery
    already happened + new outage started + state.since advanced), the
    confirmation is rejected. Without this guard, replaying an old recovery
    alert could reset the current failing state; replaying an old failure
    alert could overwrite count/last_alert_at against the new outage.
    """
    check_name = alert.get("check")
    alert_type = alert.get("type")
    alert_ts = alert.get("timestamp")
    alert_epoch = alert.get("outage_since")
    if not check_name or not alert_type or not alert_ts:
        return
    check_state = dict(state.get(check_name) or default_check_state())
    # Stale-epoch guard: if the alert carries an outage_since, it must match
    # the check_state.since for the mutation to make sense. Alerts without
    # outage_since (legacy or non-per-check) skip the guard.
    if alert_epoch is not None and check_state.get("since") != alert_epoch:
        return
    if alert_type == "failure":
        # 0 -> 1; idempotent because we only set when count == 0. Also requires
        # status == "fail" so a stale failure-confirm after recovery is rejected.
        # Clear pending_initial_alert since this confirmation IS the delivery.
        if check_state.get("status") == "fail" and int(check_state.get("alert_count_in_outage") or 0) == 0:
            check_state["alert_count_in_outage"] = 1
            check_state["last_alert_at"] = alert_ts
            check_state["pending_initial_alert"] = False
            state[check_name] = check_state
    elif alert_type == "repeat_failure":
        # increment count + advance last_alert_at; idempotent via timestamp comparison.
        # Also requires status == "fail" so a stale repeat-confirm after recovery is rejected.
        if check_state.get("status") != "fail":
            return
        current_last = check_state.get("last_alert_at")
        if current_last is None or parse_ts(current_last) < parse_ts(alert_ts):
            check_state["alert_count_in_outage"] = int(check_state.get("alert_count_in_outage") or 0) + 1
            check_state["last_alert_at"] = alert_ts
            state[check_name] = check_state
    elif alert_type == "recovery":
        # Reset to ok; idempotent (no-op if already ok). Requires status == "fail"
        # so a stale recovery-confirm after a new outage started is rejected
        # (because state.since would be the new outage's start, mismatching alert_epoch
        # already guarded above; this is belt-and-suspenders).
        if check_state.get("status") != "ok":
            state[check_name] = default_check_state() | {
                "status": "ok",
                "since": alert_ts,
                "consecutive_fails": 0,
            }


def confirm_delivered(args) -> int:
    state_dir = os.path.dirname(args.state_file) or "."
    state_lock_path = os.path.join(state_dir, os.path.basename(args.state_file) + ".lock")
    with file_lock(state_lock_path):
        state = load_json(args.state_file, {})
        with open(args.alert_file, encoding="utf-8") as f:
            alerts = [json.loads(line) for line in f if line.strip()]
        for alert in alerts:
            apply_confirmed(state, alert)
        write_atomic_json(args.state_file, state)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-file", required=True)
    parser.add_argument("--confirm-delivered", action="store_true",
                        help="Apply state mutations for one or more alerts that were "
                             "delivered. Requires --alert-file (JSONL of alerts).")
    parser.add_argument("--alert-file",
                        help="Required when --confirm-delivered is set. JSONL with one "
                             "alert per line, as written by the transition mode's --alerts-file.")
    # transition-mode args (required only when --confirm-delivered is NOT set)
    parser.add_argument("--results-file")
    parser.add_argument("--timestamp")
    parser.add_argument("--backoff")
    parser.add_argument("--alerts-file")
    parser.add_argument("--partition-actions-file")
    parser.add_argument("--health-log")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if args.confirm_delivered:
        if not args.alert_file:
            parser.error("--confirm-delivered requires --alert-file")
        return confirm_delivered(args)

    for required in ("results_file", "timestamp", "backoff", "alerts_file",
                     "partition_actions_file", "health_log"):
        if not getattr(args, required):
            parser.error(f"--{required.replace('_', '-')} is required in transition mode")

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

        suppress_alerts, partition_now, partition_actions = transition_partition(state, results_by_name, args.timestamp)
        alerts = transition_checks(state, results, args.timestamp, backoffs, suppress_alerts, partition_now)

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
