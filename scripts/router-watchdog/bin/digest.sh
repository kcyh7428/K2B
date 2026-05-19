#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG_DIR="${K2B_ROUTER_WATCHDOG_LOG_DIR:-$HOME/Library/Logs/k2b-router-watchdog}"
HEALTH_LOG="${K2B_ROUTER_WATCHDOG_HEALTH_LOG:-$LOG_DIR/health.jsonl}"
SCORE_LOG="${K2B_ROUTER_WATCHDOG_NODE_SCORE_LOG:-$LOG_DIR/node-score.jsonl}"
CONFIRM_FAILURES_LOG="${K2B_ROUTER_WATCHDOG_CONFIRM_FAILURES_LOG:-$LOG_DIR/confirm-failures.jsonl}"
SEND=false

if [[ "${1:-}" == "--send" ]]; then
  SEND=true
  shift
fi

message="$(python3 "$SCRIPT_DIR/digest.py" --health-log "$HEALTH_LOG" --score-log "$SCORE_LOG" --confirm-failures-log "$CONFIRM_FAILURES_LOG" "$@")"

if ! $SEND; then
  printf '%s\n' "$message"
  exit 0
fi

event_json="$(python3 - "$message" <<'PY'
import datetime as dt
import json
import sys
print(json.dumps({
    "timestamp": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
    "type": "digest",
    "check": "router_watchdog",
    "message": sys.argv[1],
}, ensure_ascii=False, separators=(",", ":")))
PY
)"

printf '%s\n' "$event_json" | "$SCRIPT_DIR/send-alert.sh" --event-json -
