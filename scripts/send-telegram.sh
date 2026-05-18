#!/usr/bin/env bash
# Send a plain text message to Keith via Telegram Bot API.
# Auto-splits messages over the Telegram 4096-char hard limit on the safest
# available boundary (blank line, then newline, then character) and FAILS
# loudly on any non-2xx response so /schedule runs cannot silently drop.
#
# Usage: scripts/send-telegram.sh [--chat-id ID] [--thread-id ID] "message text"
#        scripts/send-telegram.sh [--chat-id ID] [--thread-id ID] --file path/to/message.txt
# Env:   K2B_BOT_TOKEN (falls back to TELEGRAM_BOT_TOKEN, required)
#        K2B_CHAT_ID   (falls back to ALLOWED_CHAT_ID, defaults to 8394008217)
# Flags: --chat-id ID   override chat target (e.g. K2B Alerts supergroup)
#        --thread-id ID post to a specific topic (forum_topic) inside the chat

set -euo pipefail

TOKEN="${K2B_BOT_TOKEN:-${TELEGRAM_BOT_TOKEN:-}}"
[[ -n "$TOKEN" ]] || { echo "K2B_BOT_TOKEN / TELEGRAM_BOT_TOKEN env var not set" >&2; exit 1; }

CHAT_ID="${K2B_CHAT_ID:-${ALLOWED_CHAT_ID:-8394008217}}"
THREAD_ID=""
TEXT=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --chat-id)
      CHAT_ID="${2:?--chat-id needs a value}"
      shift 2
      ;;
    --thread-id)
      THREAD_ID="${2:?--thread-id needs a value}"
      shift 2
      ;;
    --file)
      [[ -f "${2:-}" ]] || { echo "file not found: ${2:-}" >&2; exit 1; }
      TEXT="$(cat "$2")"
      shift 2
      ;;
    --)
      shift
      TEXT="${1:?message text required after --}"
      shift
      ;;
    -*)
      echo "unknown flag: $1" >&2
      exit 1
      ;;
    *)
      TEXT="$1"
      shift
      ;;
  esac
done

[[ -n "$TEXT" ]] || { echo "message text required" >&2; exit 1; }

# Telegram hard limit per sendMessage call. Stay well under 4096 to leave
# room for any URL-encoding overhead and message-numbering suffixes.
MAX_CHUNK=3800

# Split TEXT into <= MAX_CHUNK chunks. Prefer breaks on a blank line, then a
# single newline, then a hard character boundary. Implementation in python so
# we don't have to fight bash word-splitting on multiline content.
CHUNKS_FILE=$(mktemp -t k2b-tg-chunks.XXXXXX)
trap 'rm -f "$CHUNKS_FILE"' EXIT

python3 - "$TEXT" "$MAX_CHUNK" "$CHUNKS_FILE" <<'PY'
import sys

text = sys.argv[1]
max_chunk = int(sys.argv[2])
out_path = sys.argv[3]

def split_one(s, limit):
    if len(s) <= limit:
        return s, ""
    window = s[:limit]
    # 1. blank line
    cut = window.rfind("\n\n")
    if cut > limit // 2:
        return s[:cut], s[cut+2:]
    # 2. single newline
    cut = window.rfind("\n")
    if cut > limit // 2:
        return s[:cut], s[cut+1:]
    # 3. hard boundary at char limit
    return s[:limit], s[limit:]

chunks = []
remaining = text
while remaining:
    head, remaining = split_one(remaining, max_chunk)
    chunks.append(head)

# Number the chunks if there are 2+
if len(chunks) > 1:
    total = len(chunks)
    chunks = [f"({i+1}/{total})\n{c}" for i, c in enumerate(chunks)]

with open(out_path, "wb") as f:
    # NUL-terminated (not separated): every chunk ends with \0 so the bash
    # reader `read -d ''` always sees a delimiter, even on the single-chunk
    # case. A trailing-only NUL would silently skip single-chunk messages.
    for c in chunks:
        f.write(c.encode("utf-8"))
        f.write(b"\0")
PY

# Iterate chunks (NUL-terminated) and POST each. Fail fast on first non-2xx.
RC=0
while IFS= read -r -d '' CHUNK; do
  CURL_OPTS=(-sS -o /tmp/k2b_tg_resp.$$ -w '%{http_code}'
    -X POST "https://api.telegram.org/bot${TOKEN}/sendMessage"
    -d "chat_id=${CHAT_ID}"
    --data-urlencode "text=${CHUNK}"
    -d "disable_web_page_preview=false")
  if [[ -n "$THREAD_ID" ]]; then
    CURL_OPTS+=(-d "message_thread_id=${THREAD_ID}")
  fi
  HTTP_STATUS=$(curl "${CURL_OPTS[@]}") || HTTP_STATUS="000"
  if [[ "$HTTP_STATUS" != 2* ]]; then
    echo "telegram sendMessage failed: HTTP $HTTP_STATUS" >&2
    cat /tmp/k2b_tg_resp.$$ >&2
    rm -f /tmp/k2b_tg_resp.$$
    exit 1
  fi
  rm -f /tmp/k2b_tg_resp.$$
done < "$CHUNKS_FILE"

exit $RC
