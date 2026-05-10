#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "Usage: send-alert.sh MESSAGE | send-alert.sh --event-json PATH|-" >&2
}

APP_DIR="${K2B_ROUTER_WATCHDOG_APP_DIR:-$HOME/Library/Application Support/k2b-router-watchdog}"
LOG_DIR="${K2B_ROUTER_WATCHDOG_LOG_DIR:-$HOME/Library/Logs/k2b-router-watchdog}"
ENV_FILE="${K2B_ROUTER_WATCHDOG_ENV_FILE:-$HOME/.k2b-router-watchdog.env}"
ALERTS_LOG="${K2B_ROUTER_WATCHDOG_ALERTS_LOG:-$LOG_DIR/alerts.jsonl}"

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
      TELEGRAM_BOT_TOKEN|KEITH_CHAT_ID|K2B_NETWORK_ALERT_CHAT_ID|K2B_NETWORK_ALERT_THREAD_ID|MIHOMO_API_BASE|MIHOMO_API_SECRET|MIHOMO_OPENAI_GROUP)
        printf -v "$key" '%s' "$value"
        export "$key"
        ;;
    esac
  done < "$ENV_FILE"
}

load_watchdog_env

: "${TELEGRAM_BOT_TOKEN:?env file is missing TELEGRAM_BOT_TOKEN}"
: "${KEITH_CHAT_ID:?env file is missing KEITH_CHAT_ID}"

mkdir -p "$APP_DIR" "$LOG_DIR"

event_json=""
if [[ "${1:-}" == "--event-json" ]]; then
  src="${2:-}"
  [[ -n "$src" ]] || { usage; exit 1; }
  if [[ "$src" == "-" ]]; then
    event_json="$(cat)"
  else
    event_json="$(cat "$src")"
  fi
else
  [[ $# -gt 0 ]] || { usage; exit 1; }
  message="$*"
  event_json="$(python3 - "$message" <<'PY'
import datetime as dt
import json
import sys
print(json.dumps({
    "timestamp": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
    "type": "manual",
    "check": "manual",
    "status": "manual",
    "message": sys.argv[1],
}, ensure_ascii=False, separators=(",", ":")))
PY
)"
fi

message="$(printf '%s' "$event_json" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("message",""))')"
payloads="$(python3 - "$KEITH_CHAT_ID" "${K2B_NETWORK_ALERT_CHAT_ID:-}" "${K2B_NETWORK_ALERT_THREAD_ID:-}" "$message" <<'PY'
import json
import sys

keith_chat_id, network_chat_id, network_thread_id, message = sys.argv[1:]
if network_chat_id:
    targets = [{"chat_id": network_chat_id}]
    if network_thread_id:
        targets[0]["message_thread_id"] = int(network_thread_id)
else:
    targets = [{"chat_id": keith_chat_id}]

for target in targets:
    payload = {
        "chat_id": target["chat_id"],
        "text": message,
        "disable_web_page_preview": True,
    }
    if "message_thread_id" in target:
        payload["message_thread_id"] = target["message_thread_id"]
    print(json.dumps(payload, ensure_ascii=False))
PY
)"

delivered_targets="$(mktemp)"
failed_targets="$(mktemp)"
current_payload_file=""
cleanup_send_alert() {
  rm -f "$delivered_targets" "$failed_targets"
  if [[ -n "$current_payload_file" ]]; then
    rm -f "$current_payload_file"
  fi
  return 0
}
trap cleanup_send_alert EXIT INT TERM

target_index=0
while IFS= read -r payload; do
  [[ -n "$payload" ]] || continue
  target_index=$((target_index + 1))
  set +e
  # Pass URL via -K stdin config so TELEGRAM_BOT_TOKEN never appears in argv
  # (visible to ps -ef otherwise). Payload is sent via --data-binary @file
  # so JSON quoting in a curl-config "data = ..." line is not a hazard.
  current_payload_file="$(mktemp "${TMPDIR:-/tmp}/k2b-router-alert.XXXXXX")"
  printf '%s' "$payload" > "$current_payload_file"
  chmod 600 "$current_payload_file" 2>/dev/null || true
  # env -u scrubs the bot token from the curl child environment so it does
  # not appear in /proc/<pid>/environ-style lookups. Defense in depth on
  # top of the -K - stdin trick that already keeps it out of argv. Other
  # script-loaded secrets (KEITH_CHAT_ID, MIHOMO_*) are not credentials
  # for this curl call, but we drop them too for hygiene.
  response="$(
    env -u TELEGRAM_BOT_TOKEN -u KEITH_CHAT_ID \
      -u K2B_NETWORK_ALERT_CHAT_ID -u K2B_NETWORK_ALERT_THREAD_ID \
      -u MIHOMO_API_BASE -u MIHOMO_API_SECRET -u MIHOMO_OPENAI_GROUP \
      curl -sS -m 10 \
        -H 'Content-Type: application/json' \
        -X POST \
        --data-binary "@$current_payload_file" \
        -K - <<EOF
url = "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage"
EOF
  )"
  curl_rc=$?
  rm -f "$current_payload_file"
  current_payload_file=""
  if [[ $curl_rc -eq 0 ]]; then
    printf '%s' "$response" | python3 -c 'import json,sys; sys.exit(0 if json.load(sys.stdin).get("ok") else 1)'
    ok_rc=$?
  else
    ok_rc=$curl_rc
  fi
  set -e
  if [[ $ok_rc -eq 0 ]]; then
    printf '%s\n' "$payload" | python3 -c 'import json,sys; p=json.load(sys.stdin); t={"chat_id":str(p["chat_id"])}; mt=p.get("message_thread_id"); t.update({"message_thread_id":mt} if mt is not None else {}); print(json.dumps(t, ensure_ascii=False, separators=(",",":")))' >> "$delivered_targets"
  else
    printf '%s\n' "$payload" | python3 -c 'import json,sys; p=json.load(sys.stdin); t={"chat_id":str(p["chat_id"])}; mt=p.get("message_thread_id"); t.update({"message_thread_id":mt} if mt is not None else {}); print(json.dumps(t, ensure_ascii=False, separators=(",",":")))' >> "$failed_targets"
    echo "telegram send failed for target $target_index: $response" >&2
    if [[ $target_index -eq 1 ]]; then
      exit 1
    fi
  fi
done <<< "$payloads"

python3 - "$event_json" "$ALERTS_LOG" "$delivered_targets" "$failed_targets" <<'PY'
import datetime as dt
import json
import os
import sys
event = json.loads(sys.argv[1])
path = sys.argv[2]
delivered_path = sys.argv[3]
failed_path = sys.argv[4]
def load_targets(p):
    if not os.path.exists(p):
        return []
    with open(p, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]
event["delivered_at"] = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
event["source"] = "direct-curl"
event["delivered_targets"] = load_targets(delivered_path)
failed_targets = load_targets(failed_path)
if failed_targets:
    event["failed_targets"] = failed_targets
os.makedirs(os.path.dirname(path), exist_ok=True)
with open(path, "a", encoding="utf-8") as f:
    f.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n")
PY
