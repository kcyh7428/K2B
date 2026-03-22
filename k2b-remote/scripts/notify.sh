#!/bin/bash
# Send a message to Telegram from the shell
# Usage: ./scripts/notify.sh "Your message here"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

# Read .env
if [ -f "$PROJECT_DIR/.env" ]; then
  TELEGRAM_BOT_TOKEN=$(grep '^TELEGRAM_BOT_TOKEN=' "$PROJECT_DIR/.env" | cut -d'=' -f2-)
  ALLOWED_CHAT_ID=$(grep '^ALLOWED_CHAT_ID=' "$PROJECT_DIR/.env" | cut -d'=' -f2-)
fi

if [ -z "$TELEGRAM_BOT_TOKEN" ] || [ -z "$ALLOWED_CHAT_ID" ]; then
  echo "Error: TELEGRAM_BOT_TOKEN or ALLOWED_CHAT_ID not set in .env"
  exit 1
fi

MESSAGE="${1:-No message provided}"

curl -s -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
  -H "Content-Type: application/json" \
  -d "{\"chat_id\": \"${ALLOWED_CHAT_ID}\", \"text\": \"${MESSAGE}\"}" > /dev/null

echo "Sent."
