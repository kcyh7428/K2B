#!/usr/bin/env bash
# Daily YouTube transcript canary. Runs scripts/yt-transcript.sh against a
# known-stable canary video and posts a Telegram alert when the cookie-backed
# caption path no longer succeeds. The intent is early warning: Mini cookies
# rotate every few weeks, and silent failure only surfaces the next time Keith
# pastes a real video into Telegram. The canary catches it within 24 hours.
#
# Cron suggestion (Mac Mini, daily 09:00 Macau, UTC+8):
#   0 9 * * *  ~/Projects/K2B/scripts/yt-canary.sh >> ~/Projects/K2B/logs/yt-canary.log 2>&1
#
# Env:
#   K2B_CANARY_VIDEO_URL         override canary URL (default: jNQXAC9IVRw)
#   K2B_CANARY_EXPECTED_METHOD   expected METHOD line (default: captions-en)
#   K2B_CANARY_TELEGRAM          set to "0" to suppress Telegram alert
#   K2B_BOT_TOKEN, K2B_CHAT_ID   passed through to send-telegram.sh
#   K2B_YT_TRANSCRIPT_SCRIPT     override transcript helper (tests use this)
#   K2B_CANARY_SEND_COMMAND      override send-telegram entry point (tests)
#
# Exit codes: always 0 unless the script itself is misconfigured. Failures are
# reported via Telegram + the cron log -- a non-zero exit would only spam cron
# email and would not help diagnose the problem any faster.

set -euo pipefail
umask 077

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

CANARY_URL="${K2B_CANARY_VIDEO_URL:-https://www.youtube.com/watch?v=jNQXAC9IVRw}"
EXPECTED="${K2B_CANARY_EXPECTED_METHOD:-captions-en}"
TRANSCRIPT_SCRIPT="${K2B_YT_TRANSCRIPT_SCRIPT:-$SCRIPT_DIR/yt-transcript.sh}"
SEND_COMMAND="${K2B_CANARY_SEND_COMMAND:-$SCRIPT_DIR/send-telegram.sh}"

# Pull bot creds from k2b-remote/.env if not already exported. This mirrors
# how cron jobs source eod-capture.env -- the cron environment is bare so we
# explicitly resolve credentials here.
if [[ -z "${K2B_BOT_TOKEN:-}" && -z "${TELEGRAM_BOT_TOKEN:-}" && -f "$REPO_ROOT/k2b-remote/.env" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$REPO_ROOT/k2b-remote/.env"
  set +a
fi

ts() { date '+%Y-%m-%dT%H:%M:%S%z'; }

TMP=$(mktemp -d "${TMPDIR:-/tmp}/yt-canary.XXXXXX")
trap 'rm -rf "$TMP"' EXIT

set +e
bash "$TRANSCRIPT_SCRIPT" "$CANARY_URL" >"$TMP/stdout" 2>"$TMP/stderr"
RC=$?
set -e

METHOD=$(grep -E '^METHOD: ' "$TMP/stderr" | tail -1 | sed -E 's/^METHOD: //')
METHOD="${METHOD:-<no-method-line>}"

if [[ $RC -eq 0 && "$METHOD" == "$EXPECTED" ]]; then
  echo "$(ts) OK method=$METHOD url=$CANARY_URL"
  exit 0
fi

# Tail the transcript stderr so the Telegram alert carries diagnostic context.
# Strip ANSI just in case and cap to ~800 chars so we never blow Telegram's
# message limit even when send-telegram.sh's chunker is mocked out.
STDERR_TAIL=$(tail -c 800 "$TMP/stderr" | sed -E $'s/\x1b\\[[0-9;]*[A-Za-z]//g')

echo "$(ts) FAIL method=$METHOD exit=$RC url=$CANARY_URL"

if [[ "${K2B_CANARY_TELEGRAM:-1}" == "0" ]]; then
  echo "$(ts) telegram alert suppressed by K2B_CANARY_TELEGRAM=0"
  exit 0
fi

ALERT_FILE="$TMP/alert.txt"
HOST_SHORT=$(hostname -s)
# Build the alert body via printf instead of a heredoc. STDERR_TAIL comes from
# yt-dlp output; in the (extremely unlikely) case of a backtick or $(...) in
# stderr, an unquoted heredoc would shell-expand it. printf treats every arg
# as a literal string, so STDERR_TAIL's contents can never invoke a subshell
# even if a future yt-dlp ships compromised error text. (Codex review 2026-05-21.)
{
  printf 'K2B YouTube transcript canary FAILED.\n\n'
  printf 'Method:   %s\n' "$METHOD"
  printf 'Exit:     %s\n' "$RC"
  printf 'Expected: %s\n' "$EXPECTED"
  printf 'Video:    %s\n' "$CANARY_URL"
  printf 'Host:     %s\n\n' "$HOST_SHORT"
  printf 'The Mini YouTube cookies most likely expired. Re-export from a fresh Chrome\n'
  printf 'Incognito session (sign in, visit one video, export, close the window without\n'
  printf 'signing out) and run:\n\n'
  printf '  python3 scripts/yt-cookies-json-to-netscape.py <export.json> \\\n'
  printf '    ~/.config/k2b/youtube-cookies.txt\n'
  printf '  scp ~/.config/k2b/youtube-cookies.txt macmini:~/.config/k2b/youtube-cookies.txt\n\n'
  printf -- '--- yt-transcript stderr tail ---\n'
  printf '%s\n' "$STDERR_TAIL"
} > "$ALERT_FILE"

if ! "$SEND_COMMAND" --file "$ALERT_FILE"; then
  echo "$(ts) WARN telegram alert delivery failed" >&2
fi

exit 0
