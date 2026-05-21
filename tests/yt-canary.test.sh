#!/usr/bin/env bash
# Regression tests for scripts/yt-canary.sh.
#
# Fake yt-transcript.sh and send-telegram.sh are injected via env overrides so
# the canary can be exercised offline. The tests verify:
#   1. captions-en METHOD + exit 0  -> success, no Telegram call
#   2. METHOD: failed                 -> Telegram alert sent, alert body
#                                         contains diagnostic stderr tail
#   3. METHOD: groq-whisper           -> still alerts (caption path is dead
#                                         even though Whisper saved the
#                                         transcript -- early-warning signal)
#   4. K2B_CANARY_TELEGRAM=0          -> failure logs but does NOT send

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SCRIPT="$REPO_ROOT/scripts/yt-canary.sh"

TMPROOT="$(mktemp -d)"
trap 'rm -rf "$TMPROOT"' EXIT

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

make_fake_transcript() {
  local target="$1"
  local method="$2"
  local rc="$3"
  cat > "$target" <<SH
#!/usr/bin/env bash
set -euo pipefail
echo "fake transcript text"
echo "Trying YouTube auto-captions (en)..." >&2
echo "METHOD: $method" >&2
exit $rc
SH
  chmod +x "$target"
}

make_recording_sender() {
  local target="$1"
  local record="$2"
  cat > "$target" <<SH
#!/usr/bin/env bash
set -euo pipefail
mode=""
file=""
text=""
while [[ \$# -gt 0 ]]; do
  case "\$1" in
    --file)
      mode="file"; file="\$2"; shift 2 ;;
    --)
      shift; text="\$1"; shift ;;
    *)
      text="\$1"; shift ;;
  esac
done
{
  echo "INVOKED"
  echo "MODE: \$mode"
  if [[ -n "\$file" && -r "\$file" ]]; then
    echo "--- file ---"
    cat "\$file"
    echo "--- end ---"
  fi
  if [[ -n "\$text" ]]; then
    echo "TEXT: \$text"
  fi
} >> "$record"
SH
  chmod +x "$target"
}

echo "=== yt-canary.test.sh ==="

# --- 1. happy path: success is silent ---
{
  d="$TMPROOT/happy"
  mkdir -p "$d"
  fake_transcript="$d/fake-transcript.sh"
  fake_sender="$d/fake-sender.sh"
  record="$d/sender.log"
  : > "$record"

  make_fake_transcript "$fake_transcript" "captions-en" 0
  make_recording_sender "$fake_sender" "$record"

  out="$d/out.txt"
  err="$d/err.txt"
  if ! K2B_YT_TRANSCRIPT_SCRIPT="$fake_transcript" \
       K2B_CANARY_SEND_COMMAND="$fake_sender" \
       K2B_BOT_TOKEN="dummy" \
       /bin/bash "$SCRIPT" >"$out" 2>"$err"; then
    cat "$err" >&2
    fail "happy-path canary should exit 0"
  fi
  grep -q "OK method=captions-en" "$out" || fail "expected OK log line"
  if [[ -s "$record" ]]; then
    fail "happy-path must not invoke send-telegram (record: $(cat "$record"))"
  fi
  echo "  PASS: happy path -- captions-en METHOD, no alert"
}

# --- 2. METHOD=failed should alert with diagnostic tail ---
{
  d="$TMPROOT/failed"
  mkdir -p "$d"
  fake_transcript="$d/fake-transcript.sh"
  cat > "$fake_transcript" <<'SH'
#!/usr/bin/env bash
echo "ERROR: [youtube] Sign in to confirm you're not a bot" >&2
echo "All transcript methods failed" >&2
echo "METHOD: failed" >&2
exit 1
SH
  chmod +x "$fake_transcript"

  fake_sender="$d/fake-sender.sh"
  record="$d/sender.log"
  : > "$record"
  make_recording_sender "$fake_sender" "$record"

  out="$d/out.txt"
  err="$d/err.txt"
  if ! K2B_YT_TRANSCRIPT_SCRIPT="$fake_transcript" \
       K2B_CANARY_SEND_COMMAND="$fake_sender" \
       K2B_BOT_TOKEN="dummy" \
       /bin/bash "$SCRIPT" >"$out" 2>"$err"; then
    cat "$err" >&2
    fail "canary should exit 0 even on transcript failure"
  fi
  grep -q "FAIL method=failed" "$out" || fail "expected FAIL log line"
  [[ -s "$record" ]] || fail "expected send-telegram to be invoked"
  grep -q "INVOKED" "$record" || fail "send-telegram fake should have recorded INVOKED"
  grep -q "MODE: file" "$record" || fail "canary should call send-telegram with --file"
  grep -q "Method:   failed" "$record" || fail "alert body should include Method line"
  grep -q "Sign in to confirm" "$record" || fail "alert body should include stderr tail diagnostic"
  grep -q "Re-export from a fresh" "$record" || fail "alert body should include recovery instructions"
  echo "  PASS: METHOD=failed -- alert sent with diagnostic tail"
}

# --- 3. METHOD=groq-whisper should still alert (caption path dead) ---
{
  d="$TMPROOT/whisper-fallback"
  mkdir -p "$d"
  fake_transcript="$d/fake-transcript.sh"
  cat > "$fake_transcript" <<'SH'
#!/usr/bin/env bash
echo "Trying YouTube auto-captions (en)..." >&2
echo "WARNING: cookies no longer valid" >&2
echo "Falling back to Groq Whisper..." >&2
echo "fake whisper transcript body"
echo "METHOD: groq-whisper" >&2
exit 0
SH
  chmod +x "$fake_transcript"

  fake_sender="$d/fake-sender.sh"
  record="$d/sender.log"
  : > "$record"
  make_recording_sender "$fake_sender" "$record"

  out="$d/out.txt"
  err="$d/err.txt"
  K2B_YT_TRANSCRIPT_SCRIPT="$fake_transcript" \
    K2B_CANARY_SEND_COMMAND="$fake_sender" \
    K2B_BOT_TOKEN="dummy" \
    /bin/bash "$SCRIPT" >"$out" 2>"$err" || fail "canary exited non-zero unexpectedly: $(cat "$err")"
  grep -q "FAIL method=groq-whisper" "$out" \
    || fail "expected FAIL log for groq-whisper fallback (captions path dead)"
  [[ -s "$record" ]] || fail "expected Telegram alert on groq-whisper fallback"
  grep -q "Method:   groq-whisper" "$record" \
    || fail "alert body should report groq-whisper method"
  echo "  PASS: groq-whisper fallback alerts (cookies dead)"
}

# --- 4. K2B_CANARY_TELEGRAM=0 suppresses alert ---
{
  d="$TMPROOT/suppressed"
  mkdir -p "$d"
  fake_transcript="$d/fake-transcript.sh"
  cat > "$fake_transcript" <<'SH'
#!/usr/bin/env bash
echo "METHOD: failed" >&2
exit 1
SH
  chmod +x "$fake_transcript"

  fake_sender="$d/fake-sender.sh"
  record="$d/sender.log"
  : > "$record"
  make_recording_sender "$fake_sender" "$record"

  out="$d/out.txt"
  err="$d/err.txt"
  K2B_YT_TRANSCRIPT_SCRIPT="$fake_transcript" \
    K2B_CANARY_SEND_COMMAND="$fake_sender" \
    K2B_CANARY_TELEGRAM=0 \
    K2B_BOT_TOKEN="dummy" \
    /bin/bash "$SCRIPT" >"$out" 2>"$err" || fail "canary exited non-zero unexpectedly: $(cat "$err")"
  grep -q "telegram alert suppressed" "$out" || fail "expected suppression log line"
  if [[ -s "$record" ]]; then
    fail "K2B_CANARY_TELEGRAM=0 must prevent send-telegram invocation"
  fi
  echo "  PASS: K2B_CANARY_TELEGRAM=0 suppresses alert"
}
