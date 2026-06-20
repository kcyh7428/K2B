#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${K2B_ROUTER_WATCHDOG_APP_DIR:-$HOME/Library/Application Support/k2b-router-watchdog}"
LOG_DIR="${K2B_ROUTER_WATCHDOG_LOG_DIR:-$HOME/Library/Logs/k2b-router-watchdog}"
ENV_FILE="${K2B_ROUTER_WATCHDOG_ENV_FILE:-$HOME/.k2b-router-watchdog.env}"
SCRIPT_DIR="${K2B_ROUTER_WATCHDOG_SCRIPT_DIR:-$APP_DIR/bin}"

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
      MIHOMO_*|K2B_PRIVATE_VPN_*)
        printf -v "$key" '%s' "$value"
        export "$key"
        ;;
    esac
  done < "$ENV_FILE"
}

[[ -f "$ENV_FILE" ]] || { echo "env file missing: $ENV_FILE" >&2; exit 1; }
load_watchdog_env

: "${K2B_PRIVATE_VPN_ROUTE_GROUP:=🎯 总模式}"
: "${K2B_PRIVATE_VPN_PRIVATE_GROUP:=🔒 私有线路}"
: "${K2B_PRIVATE_VPN_PRIMARY:=🇭🇰 K2B-VPS-HK}"
: "${K2B_PRIVATE_VPN_FAILOVER:=🇹🇼 K2B-VPS-TW}"
: "${K2B_PRIVATE_VPN_EMERGENCY:=🇲🇾 K2B-VPS-KL}"
: "${K2B_PRIVATE_VPN_FAIL_THRESHOLD:=2}"
: "${K2B_PRIVATE_VPN_RECOVERY_THRESHOLD:=5}"
: "${K2B_PRIVATE_VPN_INCIDENT_KEEP:=50}"
: "${K2B_PRIVATE_VPN_INCIDENT_MAX_AGE_DAYS:=30}"
: "${K2B_PRIVATE_VPN_AWS_PROFILE:=k2b-aws-signhubdev-hk}"
: "${K2B_PRIVATE_VPN_AWS_REGION:=ap-east-1}"
: "${K2B_PRIVATE_VPN_AWS_INSTANCE:=Ubuntu-1}"
: "${K2B_PRIVATE_VPN_ROUTER_SSH_TARGET:=router}"
: "${K2B_PRIVATE_VPN_ALERT_RECOVERY_MAX_AGE_HOURS:=168}"
: "${K2B_PRIVATE_VPN_EDGE_TRACE:=1}"
: "${K2B_PRIVATE_VPN_ROUTER_LAN_IP:=192.168.50.1}"
: "${K2B_PRIVATE_VPN_UPSTREAM_GATEWAY:=192.168.1.1}"
: "${K2B_PRIVATE_VPN_TCP_TARGETS:=1.1.1.1:443}"
: "${K2B_PRIVATE_VPN_TAILSCALE_STATUS:=1}"
: "${K2B_PRIVATE_VPN_TAILSCALE_STATUS_EVERY_TICK:=0}"
: "${K2B_PRIVATE_VPN_TAILSCALE_NETCHECK_ON_INCIDENT:=1}"
: "${K2B_PRIVATE_VPN_TAILSCALE_TIMEOUT:=4}"

export K2B_PRIVATE_VPN_ROUTE_GROUP K2B_PRIVATE_VPN_PRIVATE_GROUP
export K2B_PRIVATE_VPN_PRIMARY K2B_PRIVATE_VPN_FAILOVER K2B_PRIVATE_VPN_EMERGENCY
export K2B_PRIVATE_VPN_FAIL_THRESHOLD K2B_PRIVATE_VPN_RECOVERY_THRESHOLD
export K2B_PRIVATE_VPN_INCIDENT_KEEP K2B_PRIVATE_VPN_INCIDENT_MAX_AGE_DAYS
export K2B_PRIVATE_VPN_ALERT_RECOVERY_MAX_AGE_HOURS
export K2B_PRIVATE_VPN_AWS_PROFILE K2B_PRIVATE_VPN_AWS_REGION K2B_PRIVATE_VPN_AWS_INSTANCE
export K2B_PRIVATE_VPN_ROUTER_SSH_TARGET
export K2B_PRIVATE_VPN_EDGE_TRACE K2B_PRIVATE_VPN_ROUTER_LAN_IP K2B_PRIVATE_VPN_UPSTREAM_GATEWAY
export K2B_PRIVATE_VPN_TCP_TARGETS K2B_PRIVATE_VPN_TAILSCALE_STATUS K2B_PRIVATE_VPN_TAILSCALE_STATUS_EVERY_TICK
export K2B_PRIVATE_VPN_TAILSCALE_NETCHECK_ON_INCIDENT K2B_PRIVATE_VPN_TAILSCALE_TIMEOUT
[[ -n "${K2B_PRIVATE_VPN_HK_SSH_TARGET:-}" ]] && export K2B_PRIVATE_VPN_HK_SSH_TARGET

STATE_FILE="${K2B_PRIVATE_VPN_STATE_FILE:-$APP_DIR/private-vpn-state.json}"
HEALTH_LOG="${K2B_PRIVATE_VPN_HEALTH_LOG:-$LOG_DIR/private-vpn-health.jsonl}"
INCIDENT_DIR="${K2B_PRIVATE_VPN_INCIDENT_DIR:-$LOG_DIR/incidents}"
ALERTS_FILE="${K2B_PRIVATE_VPN_ALERTS_LOG:-$LOG_DIR/private-vpn-alerts.jsonl}"
SEND_ALERT_CMD="${K2B_PRIVATE_VPN_SEND_ALERT_CMD:-$SCRIPT_DIR/send-alert.sh}"
LOCK_FILE="${K2B_PRIVATE_VPN_LOCK_FILE:-$APP_DIR/private-vpn-watchdog.lock}"

mkdir -p "$APP_DIR" "$LOG_DIR" "$INCIDENT_DIR"

command -v python3 >/dev/null 2>&1 || { echo "python3 missing from PATH" >&2; exit 127; }

python3 "$SCRIPT_DIR/private-vpn-watchdog.py" \
  --state-file "$STATE_FILE" \
  --health-log "$HEALTH_LOG" \
  --incident-dir "$INCIDENT_DIR" \
  --alerts-file "$ALERTS_FILE" \
  --lock-file "$LOCK_FILE" \
  --send-alert-cmd "$SEND_ALERT_CMD" \
  "$@"
