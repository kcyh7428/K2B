#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
APP_DIR="${K2B_ROUTER_WATCHDOG_APP_DIR:-$HOME/Library/Application Support/k2b-router-watchdog}"
LOG_DIR="${K2B_ROUTER_WATCHDOG_LOG_DIR:-$HOME/Library/Logs/k2b-router-watchdog}"
ENV_FILE="${K2B_ROUTER_WATCHDOG_ENV_FILE:-$HOME/.k2b-router-watchdog.env}"
SCORE_LOG="${K2B_ROUTER_WATCHDOG_NODE_SCORE_LOG:-$LOG_DIR/node-score.jsonl}"
TOP3_FILE="${K2B_ROUTER_WATCHDOG_NODE_TOP3_FILE:-$APP_DIR/node-top3.json}"

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
      MIHOMO_API_BASE|MIHOMO_API_SECRET|MIHOMO_OPENAI_GROUP)
        printf -v "$key" '%s' "$value"
        export "$key"
        ;;
    esac
  done < "$ENV_FILE"
}

[[ -f "$ENV_FILE" ]] || { echo "env file missing: $ENV_FILE" >&2; exit 1; }
load_watchdog_env
: "${MIHOMO_API_BASE:?env file is missing MIHOMO_API_BASE}"
: "${MIHOMO_API_SECRET:?env file is missing MIHOMO_API_SECRET}"
: "${MIHOMO_OPENAI_GROUP:?env file is missing MIHOMO_OPENAI_GROUP}"

mkdir -p "$APP_DIR" "$LOG_DIR"
python3 "$SCRIPT_DIR/score-nodes.py" \
  --score-log "$SCORE_LOG" \
  --top3-file "$TOP3_FILE" \
  "$@"
