#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
APP_DIR="${K2B_ROUTER_WATCHDOG_APP_DIR:-$HOME/Library/Application Support/k2b-router-watchdog}"
LOG_DIR="${K2B_ROUTER_WATCHDOG_LOG_DIR:-$HOME/Library/Logs/k2b-router-watchdog}"
ENV_FILE="${K2B_ROUTER_WATCHDOG_ENV_FILE:-$HOME/.k2b-router-watchdog.env}"
STATE_FILE="${K2B_ROUTER_WATCHDOG_STATE_FILE:-$APP_DIR/state.json}"
HEALTH_LOG="${K2B_ROUTER_WATCHDOG_HEALTH_LOG:-$LOG_DIR/health.jsonl}"
QUEUE_FILE="${K2B_ROUTER_WATCHDOG_PARTITION_QUEUE:-$APP_DIR/pending-partition-events.jsonl}"
PARTITION_SUPPRESSION_LOG="${K2B_ROUTER_WATCHDOG_PARTITION_SUPPRESSION_LOG:-$LOG_DIR/partition-suppressions.jsonl}"
BACKOFF_SCHEDULE="${BACKOFF_SCHEDULE:-30m,2h,6h,24h}"
DRY_RUN=false

if [[ "${1:-}" == "--dry-run" ]]; then
  DRY_RUN=true
fi

if [[ ! -f "$ENV_FILE" ]]; then
  echo "env file missing: $ENV_FILE" >&2
  exit 1
fi

load_watchdog_env() {
  local line key value first last
  while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line%$'\r'}"
    [[ -z "$line" || "${line:0:1}" == "#" || "$line" != *=* ]] && continue
    key="${line%%=*}"
    value="${line#*=}"
    if [[ ${#value} -ge 2 ]]; then
      first="${value:0:1}"
      last="${value: -1}"
      if { [[ "$first" == "'" && "$last" == "'" ]] || [[ "$first" == '"' && "$last" == '"' ]]; }; then
        value="${value:1:${#value}-2}"
      fi
    fi
    case "$key" in
      TELEGRAM_BOT_TOKEN|KEITH_CHAT_ID|MIHOMO_API_BASE|MIHOMO_API_SECRET|MIHOMO_OPENAI_GROUP)
        printf -v "$key" '%s' "$value"
        export "$key"
        ;;
    esac
  done < "$ENV_FILE"
}

load_watchdog_env

: "${TELEGRAM_BOT_TOKEN:?env file is missing TELEGRAM_BOT_TOKEN}"
: "${KEITH_CHAT_ID:?env file is missing KEITH_CHAT_ID}"
: "${MIHOMO_API_BASE:?env file is missing MIHOMO_API_BASE}"
: "${MIHOMO_API_SECRET:?env file is missing MIHOMO_API_SECRET}"
: "${MIHOMO_OPENAI_GROUP:?env file is missing MIHOMO_OPENAI_GROUP}"

mkdir -p "$APP_DIR" "$LOG_DIR"
tmpdir="$(mktemp -d)"
trap 'rm -rf "$tmpdir"' EXIT

results_file="$tmpdir/results.json"
result_lines="$tmpdir/result-lines.jsonl"
alerts_file="$tmpdir/alerts.jsonl"
partition_actions="$tmpdir/partition-actions.jsonl"
timestamp="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"

write_ok_results() {
  python3 - "$results_file" <<'PY'
import json, sys
names = [
    "router_reachable",
    "mihomo_api",
    "openai_node",
    "chatgpt_https",
    "chatgpt_ws",
    "claude_https",
    "telegram_api",
    "tailscale_direct",
    "syncthing_process",
    "pm2_daemon",
    "pm2_services",
]
results = []
for name in names:
    results.append({
        "name": name,
        "ok": True,
        "alertable": name not in {"openai_node", "tailscale_direct"},
        "latency_ms": 0,
        "severity": "ok",
        "message": "dry-run ok",
        "details": {"pm2_restart_times": {}} if name == "pm2_services" else {},
    })
with open(sys.argv[1], "w", encoding="utf-8") as f:
    json.dump(results, f)
PY
}

if [[ -n "${K2B_ROUTER_WATCHDOG_FAKE_RESULTS:-}" ]]; then
  cp "$K2B_ROUTER_WATCHDOG_FAKE_RESULTS" "$results_file"
elif [[ "${K2B_ROUTER_WATCHDOG_DRY_RUN_SKIP_CHECKS:-}" == "1" ]]; then
  write_ok_results
else
  : > "$result_lines"
  for check in router.sh mihomo.sh openai.sh claude.sh telegram.sh tailscale.sh syncthing.sh pm2.sh; do
    bash "$SCRIPT_DIR/checks/$check" >> "$result_lines"
  done
  python3 - "$result_lines" "$results_file" <<'PY'
import json, sys
items = []
with open(sys.argv[1], encoding="utf-8") as f:
    for line in f:
        line = line.strip()
        if line:
            items.append(json.loads(line))
with open(sys.argv[2], "w", encoding="utf-8") as f:
    json.dump(items, f, ensure_ascii=False)
PY
fi

state_args=(
  "$SCRIPT_DIR/state-machine.py"
  --state-file "$STATE_FILE"
  --results-file "$results_file"
  --timestamp "$timestamp"
  --backoff "$BACKOFF_SCHEDULE"
  --alerts-file "$alerts_file"
  --partition-actions-file "$partition_actions"
  --health-log "$HEALTH_LOG"
)

if $DRY_RUN; then
  python3 "${state_args[@]}" --dry-run
  exit 0
fi

python3 "${state_args[@]}"
python3 "$SCRIPT_DIR/partition-queue.py" apply \
  --queue-file "$QUEUE_FILE" \
  --actions-file "$partition_actions" \
  --alerts-file "$alerts_file" \
  --r5c-state-file "${K2B_R5C_AUTORECOVERY_STATE_FILE:-$APP_DIR/r5c-autorecovery-state.json}" \
  --suppression-log "$PARTITION_SUPPRESSION_LOG"

if [[ -s "$alerts_file" ]]; then
  confirm_file="$tmpdir/confirm.jsonl"
  confirm_failures_log="${K2B_ROUTER_WATCHDOG_CONFIRM_FAILURES_LOG:-$LOG_DIR/confirm-failures.jsonl}"
  while IFS= read -r alert; do
    [[ -n "$alert" ]] || continue
    if printf '%s\n' "$alert" | "$SCRIPT_DIR/send-alert.sh" --event-json -; then
      # Delivery confirmed: advance per-check alert counters via state-machine.py
      # --confirm-delivered. Non-per-check alert types (partition, digest) are
      # no-ops in apply_confirmed and pass through harmlessly.
      # Defers per the 2026-05-18 silent-drop fix: state was previously mutated
      # at alert-generation time, so failed deliveries left state advanced
      # without Keith ever seeing the alert.
      printf '%s\n' "$alert" > "$confirm_file"
      if ! python3 "$SCRIPT_DIR/state-machine.py" --confirm-delivered \
        --state-file "$STATE_FILE" \
        --alert-file "$confirm_file"; then
        # --confirm-delivered failure after a successful Telegram send is the
        # rare-but-real divergence path Codex flagged (2026-05-18 review). The
        # alert reached Keith but state did not advance, so the next tick will
        # regenerate + re-send (Keith gets one duplicate). Make it visible so
        # operator can detect drift instead of swallowing it to stderr only.
        mkdir -p "$LOG_DIR"
        printf '{"timestamp":"%s","kind":"confirm_failed_after_send","alert":%s}\n' \
          "$timestamp" "$alert" >> "$confirm_failures_log"
        echo "WATCHDOG-CONFIRM-FAILED: state did not advance after successful send (see $confirm_failures_log)" >&2
      fi
    else
      echo "failed to send watchdog alert" >&2
    fi
  done < "$alerts_file"
fi
