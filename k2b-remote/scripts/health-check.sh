#!/bin/bash
# health-check.sh -- Independent health monitor for k2b-remote
# Runs via launchd every 10 minutes. Alerts via Telegram if heartbeat is stale.
# Parameterized for V2B replication.

BRAIN_ID="${BRAIN_ID:-k2b}"
STORE_DIR="${STORE_DIR:-$HOME/Projects/K2B/k2b-remote/store}"
HEALTH_FILE="$STORE_DIR/health.json"
MAX_AGE_SECONDS=600  # 10 minutes

# Telegram config (read from .env)
ENV_FILE="$(dirname "$STORE_DIR")/.env"
if [ -f "$ENV_FILE" ]; then
  TELEGRAM_BOT_TOKEN=$(grep '^TELEGRAM_BOT_TOKEN=' "$ENV_FILE" | cut -d= -f2)
  ALLOWED_CHAT_ID=$(grep '^ALLOWED_CHAT_ID=' "$ENV_FILE" | cut -d= -f2)
fi

send_alert() {
  local msg="$1"
  if [ -n "$TELEGRAM_BOT_TOKEN" ] && [ -n "$ALLOWED_CHAT_ID" ]; then
    curl -s -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
      -d chat_id="$ALLOWED_CHAT_ID" \
      -d text="$msg" > /dev/null 2>&1
  fi
}

# Check if health file exists
if [ ! -f "$HEALTH_FILE" ]; then
  send_alert "[!] ${BRAIN_ID}-remote health file missing. Process may not be running."
  exit 1
fi

# Check age of heartbeat
EPOCH=$(python3 -c "import json; print(json.load(open('$HEALTH_FILE'))['epoch'])" 2>/dev/null)
if [ -z "$EPOCH" ]; then
  send_alert "[!] ${BRAIN_ID}-remote health file corrupt."
  exit 1
fi

NOW=$(python3 -c "import time; print(int(time.time() * 1000))")
AGE_MS=$(( NOW - EPOCH ))
AGE_SECONDS=$(( AGE_MS / 1000 ))

if [ "$AGE_SECONDS" -gt "$MAX_AGE_SECONDS" ]; then
  send_alert "[!] ${BRAIN_ID}-remote heartbeat stale (${AGE_SECONDS}s old). Attempting pm2 restart..."
  /opt/homebrew/bin/pm2 restart "${BRAIN_ID}-remote" 2>/dev/null
  sleep 30
  # Re-check
  EPOCH2=$(python3 -c "import json; print(json.load(open('$HEALTH_FILE'))['epoch'])" 2>/dev/null)
  NOW2=$(python3 -c "import time; print(int(time.time() * 1000))")
  AGE2=$(( (NOW2 - EPOCH2) / 1000 ))
  if [ "$AGE2" -gt "$MAX_AGE_SECONDS" ]; then
    send_alert "[X] ${BRAIN_ID}-remote STILL DOWN after restart attempt. Manual intervention needed."
  else
    send_alert "[OK] ${BRAIN_ID}-remote recovered after pm2 restart."
  fi
fi
