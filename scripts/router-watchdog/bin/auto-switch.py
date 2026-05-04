#!/usr/bin/env python3
import argparse
import datetime as dt
import json
import os
import urllib.parse
import urllib.request
import re
import subprocess


TRIGGER_CHECKS = {"chatgpt_https", "chatgpt_ws", "claude_https", "telegram_api"}


def parse_ts(value: str) -> dt.datetime:
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0)


def iso(value: dt.datetime) -> str:
    return value.astimezone(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def append_jsonl(path: str, payload: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")


def load_json(path: str, default):
    if not path or not os.path.exists(path):
        return default
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def latest_scores(path: str, now: dt.datetime, max_age_hours: int) -> dict[str, dict]:
    if not os.path.exists(path):
        return {}
    cutoff = now - dt.timedelta(hours=max_age_hours)
    latest: dict[str, dict] = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            row = json.loads(line)
            ts = row.get("timestamp")
            candidate = row.get("candidate")
            if not ts or not candidate or parse_ts(ts) < cutoff:
                continue
            old = latest.get(candidate)
            if old is None or ts >= old.get("timestamp", ""):
                latest[candidate] = row
    return latest


def recent_switch(path: str, now: dt.datetime, cooldown_minutes: int) -> dict | None:
    if cooldown_minutes <= 0 or not os.path.exists(path):
        return None
    cutoff = now - dt.timedelta(minutes=cooldown_minutes)
    latest: dict | None = None
    with open(path, encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            row = json.loads(line)
            if not row.get("switched") or not row.get("timestamp"):
                continue
            ts = parse_ts(row["timestamp"])
            if ts >= cutoff and (latest is None or row["timestamp"] >= latest.get("timestamp", "")):
                latest = row
    return latest


def float_value(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def append_blocked_alert(alerts_file: str | None, decision: dict, group: str, min_consecutive_fails: int) -> None:
    if not alerts_file or not decision.get("triggered") or decision.get("switched"):
        return
    if decision.get("max_consecutive_fails") != min_consecutive_fails:
        return
    target = decision.get("to_candidate") or "none"
    target_leaf = decision.get("to_leaf") or "none"
    append_jsonl(alerts_file, {
        "timestamp": decision["timestamp"],
        "type": "auto_switch_blocked",
        "check": "openai_node",
        "message": (
            "K2B router watchdog auto-switch blocked: "
            f"{group} stayed on {decision.get('from_candidate')} ({decision.get('from_leaf')}); "
            f"reason={decision.get('reason')}; "
            f"best_candidate={target} ({target_leaf}); "
            f"failed_ticks={decision.get('max_consecutive_fails')}."
        ),
    })


def maybe_refresh_scores(command: str | None, timeout_seconds: int, decision: dict) -> bool:
    if not command:
        decision["score_refresh"] = "not_configured"
        return True
    try:
        completed = subprocess.run(
            [command],
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except Exception as exc:
        decision["score_refresh"] = "failed"
        decision["score_refresh_error"] = str(exc)
        return False
    if completed.returncode != 0:
        decision["score_refresh"] = "failed"
        decision["score_refresh_rc"] = completed.returncode
        decision["score_refresh_stderr"] = (completed.stderr or "")[-500:]
        return False
    decision["score_refresh"] = "ok"
    return True


def put_openai_group(base: str, secret: str, group: str, candidate: str, timeout: int = 5) -> int:
    path = "/proxies/" + urllib.parse.quote(group, safe="")
    body = json.dumps({"name": candidate}, ensure_ascii=False).encode()
    req = urllib.request.Request(
        base.rstrip("/") + path,
        data=body,
        headers={
            "Authorization": "Bearer " + secret,
            "Content-Type": "application/json",
        },
        method="PUT",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--results-file", required=True)
    parser.add_argument("--state-file", required=True)
    parser.add_argument("--score-log", default=os.path.expanduser("~/Library/Logs/k2b-router-watchdog/node-score.jsonl"))
    parser.add_argument("--decision-log", default=os.path.expanduser("~/Library/Logs/k2b-router-watchdog/auto-switch.jsonl"))
    parser.add_argument("--alerts-file", default=None)
    parser.add_argument("--sentinel", default=os.path.expanduser("~/.k2b-router-autoswitch-enabled"))
    parser.add_argument("--now", default=None)
    parser.add_argument("--min-consecutive-fails", type=int, default=3)
    parser.add_argument("--min-success-rate", type=float, default=0.8)
    parser.add_argument("--min-score-improvement", type=float, default=float(os.environ.get("K2B_AUTOSWITCH_MIN_SCORE_IMPROVEMENT", "0.05")))
    parser.add_argument("--max-score-age-hours", type=int, default=24)
    parser.add_argument("--switch-cooldown-minutes", type=int, default=30)
    parser.add_argument("--candidate-regex", default=os.environ.get("K2B_AUTOSWITCH_CANDIDATE_REGEX", r"^♻️ 手动切换"))
    parser.add_argument("--score-command", default=None)
    parser.add_argument("--score-command-timeout-seconds", type=int, default=180)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    now = parse_ts(args.now) if args.now else utc_now()
    enabled = os.path.exists(args.sentinel)
    results = load_json(args.results_file, [])
    state = load_json(args.state_file, {})

    checks = {item.get("name"): item for item in results if item.get("name")}
    failing = [name for name in TRIGGER_CHECKS if not checks.get(name, {}).get("ok", True)]
    max_consecutive = max((state.get(name, {}).get("consecutive_fails", 0) for name in failing), default=0)
    openai = checks.get("openai_node", {})
    details = openai.get("details") or {}
    current_candidate = details.get("openai_group_selection") or details.get("selected_node")
    current_leaf = details.get("selected_node") or current_candidate

    decision = {
        "timestamp": iso(now),
        "enabled": enabled,
        "triggered": bool(failing and max_consecutive >= args.min_consecutive_fails),
        "switched": False,
        "dry_run": args.dry_run,
        "failing_checks": failing,
        "max_consecutive_fails": max_consecutive,
        "from_candidate": current_candidate,
        "from_leaf": current_leaf,
        "to_candidate": None,
        "to_leaf": None,
        "score_delta": None,
        "score_refresh": "not_run",
        "reason": "",
    }

    if not enabled:
        decision["reason"] = "sentinel_absent"
        append_jsonl(args.decision_log, decision)
        return 0
    if not decision["triggered"]:
        decision["reason"] = "not_triggered"
        append_jsonl(args.decision_log, decision)
        return 0
    last_switch = recent_switch(args.decision_log, now, args.switch_cooldown_minutes)
    if last_switch:
        decision["reason"] = "switch_cooldown_active"
        decision["last_switch_at"] = last_switch.get("timestamp")
        append_jsonl(args.decision_log, decision)
        return 0

    group = os.environ.get("MIHOMO_OPENAI_GROUP", "")
    if not args.dry_run and not maybe_refresh_scores(args.score_command, args.score_command_timeout_seconds, decision):
        decision["reason"] = "score_refresh_failed"
        append_jsonl(args.decision_log, decision)
        append_blocked_alert(args.alerts_file, decision, group, args.min_consecutive_fails)
        return 0

    score_rows = latest_scores(args.score_log, now, args.max_score_age_hours)
    if not score_rows:
        decision["reason"] = "no_recent_scores"
        append_jsonl(args.decision_log, decision)
        append_blocked_alert(args.alerts_file, decision, group, args.min_consecutive_fails)
        return 0

    current_score = score_rows.get(current_candidate or "")
    current_bad = bool(
        current_score
        and (
            current_score.get("quarantined")
            or current_score.get("success_rate", 1.0) < args.min_success_rate
        )
    )

    candidate_re = re.compile(args.candidate_regex)
    candidates = sorted(
        [
            row for row in score_rows.values()
            if not row.get("quarantined")
            and row.get("success_rate", 0) >= args.min_success_rate
            and row.get("candidate") != current_candidate
            and candidate_re.search(row.get("candidate") or "")
        ],
        key=lambda row: (row.get("score", 0), row.get("success_rate", 0)),
        reverse=True,
    )
    if not candidates:
        decision["reason"] = "no_better_candidate"
        append_jsonl(args.decision_log, decision)
        append_blocked_alert(args.alerts_file, decision, group, args.min_consecutive_fails)
        return 0

    target = candidates[0]
    decision["to_candidate"] = target.get("candidate")
    decision["to_leaf"] = target.get("resolved_leaf")
    target_score = float_value(target.get("score"))
    current_score_value = float_value(current_score.get("score")) if current_score else None
    if current_score_value is not None:
        decision["score_delta"] = round(target_score - current_score_value, 4)
    clear_better = current_bad or current_score is None or (
        decision["score_delta"] is not None
        and decision["score_delta"] >= args.min_score_improvement
    )
    if not clear_better:
        decision["reason"] = "no_clear_better_candidate"
        append_jsonl(args.decision_log, decision)
        append_blocked_alert(args.alerts_file, decision, group, args.min_consecutive_fails)
        return 0

    if args.dry_run:
        decision["reason"] = "dry_run_would_switch"
        append_jsonl(args.decision_log, decision)
        return 0

    try:
        status = put_openai_group(
            os.environ["MIHOMO_API_BASE"],
            os.environ["MIHOMO_API_SECRET"],
            os.environ["MIHOMO_OPENAI_GROUP"],
            decision["to_candidate"],
        )
    except Exception as exc:  # noqa: BLE001 -- any failure here must NOT abort check.sh's pending-alert drain
        decision["http_status"] = 0
        decision["switched"] = False
        decision["reason"] = f"put_exception_{type(exc).__name__}"
        append_jsonl(args.decision_log, decision)
        if args.alerts_file:
            append_jsonl(args.alerts_file, {
                "timestamp": iso(now),
                "type": "auto_switch_blocked",
                "check": "openai_node",
                "message": (
                    "K2B router watchdog auto-switch BLOCKED: "
                    f"PUT to {os.environ['MIHOMO_OPENAI_GROUP']} failed "
                    f"({type(exc).__name__}: {exc}). Pending watchdog alerts "
                    "still drained on this tick."
                ),
            })
        return 0
    decision["http_status"] = status
    decision["switched"] = 200 <= status < 300
    decision["reason"] = "switched" if decision["switched"] else f"put_http_{status}"
    append_jsonl(args.decision_log, decision)

    if decision["switched"] and args.alerts_file:
        append_jsonl(args.alerts_file, {
            "timestamp": iso(now),
            "type": "auto_switch",
            "check": "openai_node",
            "message": (
                "K2B router watchdog auto-switch: "
                f"{os.environ['MIHOMO_OPENAI_GROUP']} {current_candidate} ({current_leaf}) "
                f"-> {decision['to_candidate']} ({decision['to_leaf']}) "
                f"after {max_consecutive} failed ticks."
            ),
        })
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
