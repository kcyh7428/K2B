#!/usr/bin/env bash
# Send a plain text message to Keith via Telegram Bot API.
# Usage: scripts/send-telegram.sh "message text"
#        scripts/send-telegram.sh --file path/to/message.txt
# Env:   K2B_BOT_TOKEN (falls back to TELEGRAM_BOT_TOKEN, required)
#        K2B_CHAT_ID   (falls back to ALLOWED_CHAT_ID, defaults to 8394008217)

set -euo pipefail

TOKEN="${K2B_BOT_TOKEN:-${TELEGRAM_BOT_TOKEN:-}}"
[[ -n "$TOKEN" ]] || { echo "K2B_BOT_TOKEN / TELEGRAM_BOT_TOKEN env var not set" >&2; exit 1; }

CHAT_ID="${K2B_CHAT_ID:-${ALLOWED_CHAT_ID:-8394008217}}"

if [[ "${1:-}" == "--file" ]]; then
  [[ -f "${2:-}" ]] || { echo "file not found: ${2:-}" >&2; exit 1; }
  TEXT="$(cat "$2")"
else
  TEXT="${1:?message text required}"
fi

curl -fsS -X POST "https://api.telegram.org/bot${TOKEN}/sendMessage" \
  -d "chat_id=${CHAT_ID}" \
  --data-urlencode "text=${TEXT}" \
  -d "parse_mode=Markdown" \
  -d "disable_web_page_preview=false"
