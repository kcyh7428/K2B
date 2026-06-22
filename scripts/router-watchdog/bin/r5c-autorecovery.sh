#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
APP_DIR="${K2B_ROUTER_WATCHDOG_APP_DIR:-$HOME/Library/Application Support/k2b-router-watchdog}"
LOG_DIR="${K2B_ROUTER_WATCHDOG_LOG_DIR:-$HOME/Library/Logs/k2b-router-watchdog}"
ENV_FILE="${K2B_ROUTER_WATCHDOG_ENV_FILE:-$HOME/.k2b-router-watchdog.env}"

load_watchdog_env() {
  local line key value first last
  while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line%$'\r'}"
    [[ -z "$line" || "${line:0:1}" == "#" || "$line" != *=* ]] && continue
    key="${line%%=*}"
    value="${line#*=}"
    key="${key#export }"
    key="${key//[[:space:]]/}"
    if [[ ${#value} -ge 2 ]]; then
      first="${value:0:1}"
      last="${value: -1}"
      if { [[ "$first" == "'" && "$last" == "'" ]] || [[ "$first" == '"' && "$last" == '"' ]]; }; then
        value="${value:1:${#value}-2}"
      fi
    fi
    case "$key" in
      MIHOMO_*|K2B_R5C_AUTORECOVERY_STATE_FILE|K2B_R5C_AUTORECOVERY_LOG|K2B_R5C_AUTORECOVERY_EVIDENCE_DIR|K2B_R5C_AUTORECOVERY_SENTINEL|K2B_R5C_AUTORECOVERY_R5C_LAN_IP|K2B_R5C_AUTORECOVERY_MIHOMO_API_BASE|K2B_R5C_AUTORECOVERY_ASUS_HOST|K2B_R5C_AUTORECOVERY_ASUS_SSH_TARGET|K2B_R5C_AUTORECOVERY_ASUS_SSH_KEY|K2B_R5C_AUTORECOVERY_THRESHOLD|K2B_R5C_AUTORECOVERY_COOLDOWN_MINUTES|K2B_R5C_AUTORECOVERY_PING_TIMEOUT_MS|K2B_R5C_AUTORECOVERY_HTTP_TIMEOUT_S|K2B_R5C_AUTORECOVERY_SSH_TIMEOUT_S|K2B_R5C_AUTORECOVERY_SSH_STRICT_HOST_KEY_CHECKING)
        printf -v "$key" '%s' "$value"
        export "$key"
        ;;
    esac
  done < "$ENV_FILE"
}

[[ -f "$ENV_FILE" ]] || { echo "env file missing: $ENV_FILE" >&2; exit 1; }
load_watchdog_env

if [[ "${K2B_R5C_AUTORECOVERY_WRAPPER_TEST_MODE:-0}" != "1" ]]; then
  unset K2B_R5C_AUTORECOVERY_PING_BIN
  unset K2B_R5C_AUTORECOVERY_CURL_BIN
  unset K2B_R5C_AUTORECOVERY_SSH_BIN
  unset K2B_R5C_AUTORECOVERY_TEST_MODE
  unset K2B_R5C_AUTORECOVERY_TEST_ALLOW_ANY_SSH_KEY
  unset K2B_R5C_AUTORECOVERY_TEST_ALLOW_FAKE_PROBE_BINS
  unset K2B_R5C_AUTORECOVERY_TEST_ALLOW_FAKE_SSH_BIN
fi

mkdir -p "$APP_DIR" "$LOG_DIR"

env \
  K2B_ROUTER_WATCHDOG_APP_DIR="$APP_DIR" \
  K2B_ROUTER_WATCHDOG_LOG_DIR="$LOG_DIR" \
  K2B_ROUTER_WATCHDOG_ENV_FILE="$ENV_FILE" \
  python3 "$SCRIPT_DIR/r5c-autorecovery.py" "$@"
