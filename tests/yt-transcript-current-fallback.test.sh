#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TMPROOT="$(mktemp -d)"
trap 'rm -rf "$TMPROOT"' EXIT
mkdir -p "$TMPROOT/bin"

cat > "$TMPROOT/bin/yt-dlp" <<'SH'
#!/usr/bin/env bash
for arg in "$@"; do
  if [[ "$arg" == '--cookies' || "$arg" == '--cookies-from-browser' ]]; then
    printf '%s' "$arg" > "$FAKE_COOKIE_MARKER"
  fi
done
exit 1
SH
cat > "$TMPROOT/bin/deno" <<'SH'
#!/usr/bin/env bash
exit 0
SH
cat > "$TMPROOT/current-yt-dlp" <<'PY'
import os
from pathlib import Path
import sys

args = sys.argv[1:]
assert '--ignore-config' in args
assert '--js-runtimes' in args
assert '--no-remote-components' in args
assert '--remote-components' not in args
assert '--cookies' not in args
assert '--cookies-from-browser' not in args
output = args[args.index('-o') + 1].replace('%(ext)s', 'webm')
Path(output).write_bytes(b'synthetic public audio')
Path(os.environ['FAKE_DLP_MARKER']).write_text(output)
PY
cat > "$TMPROOT/whisper.sh" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
if [[ "$1" == http* ]]; then
  if [[ "${FAKE_WHISPER_MISSING_KEY:-0}" == '1' ]]; then
    echo 'TRANSCRIPTION_ERROR: groq-key' >&2
    exit 1
  fi
  if [[ "${FAKE_WHISPER_OLD_JSON_ERROR:-0}" == '1' ]]; then
    printf '%s\n' '{"error":{"message":"quota exceeded"}}'
    exit 0
  fi
  if [[ "${FAKE_WHISPER_OLD_TEXT_ERROR:-0}" == '1' ]]; then
    printf '%s\n' 'quota exceeded'
    exit 0
  fi
  exit 1
fi
[[ -s "$1" ]]
if [[ $# -gt 1 ]]; then
  [[ "$2" == '--language' && "$3" == 'zh' ]]
fi
if [[ "${FAKE_WHISPER_JSON_ERROR:-0}" == '1' ]]; then
  printf '%s\n' '{"error":{"message":"quota exceeded"}}'
  exit 0
fi
if [[ "${FAKE_WHISPER_TEXT_ERROR:-0}" == '1' ]]; then
  printf '%s\n' 'quota exceeded'
  exit 0
fi
printf '%s\n' 'Synthetic Chinese transcript from local audio.'
SH
chmod +x "$TMPROOT/bin/yt-dlp" "$TMPROOT/bin/deno" "$TMPROOT/whisper.sh"

OUT="$TMPROOT/stdout"
ERR="$TMPROOT/stderr"
PATH="$TMPROOT/bin:$PATH" \
  FAKE_COOKIE_MARKER="$TMPROOT/cookie-used" \
  K2B_YT_COOKIES_FILE=none \
  YT_DLP_COOKIE_BROWSER=none \
  K2B_YT_CURRENT_DLP_FALLBACK=1 \
  K2B_YT_CURRENT_DLP_BIN="$TMPROOT/current-yt-dlp" \
  K2B_YT_WHISPER_HELPER="$TMPROOT/whisper.sh" \
  FAKE_DLP_MARKER="$TMPROOT/downloaded-audio-path" \
  /bin/bash "$ROOT/scripts/yt-transcript.sh" \
    'https://www.youtube.com/watch?v=synthetic123' --language zh \
    > "$OUT" 2> "$ERR"

grep -q 'Synthetic Chinese transcript' "$OUT"
grep -q 'DOWNLOAD_METHOD: local-yt-dlp-unverified' "$ERR"
grep -q 'METHOD: groq-whisper-current-dlp' "$ERR"
[[ ! -e "$TMPROOT/cookie-used" ]]
AUDIO_PATH="$(cat "$TMPROOT/downloaded-audio-path")"
[[ ! -e "$AUDIO_PATH" ]] || { echo 'Temporary audio was not removed' >&2; exit 1; }
echo 'PASS: public current-downloader recovery reaches local-file Whisper without cookies'

PATH="$TMPROOT/bin:$PATH" \
  K2B_YT_COOKIES_FILE=none \
  YT_DLP_COOKIE_BROWSER=none \
  K2B_YT_CURRENT_DLP_FALLBACK=1 \
  K2B_YT_CURRENT_DLP_BIN="$TMPROOT/current-yt-dlp" \
  K2B_YT_WHISPER_HELPER="$TMPROOT/whisper.sh" \
  FAKE_DLP_MARKER="$TMPROOT/downloaded-audio-path" \
  /bin/bash "$ROOT/scripts/yt-transcript.sh" \
    'https://www.youtube.com/watch?v=synthetic123' \
    > "$OUT" 2> "$ERR"
grep -q 'Synthetic Chinese transcript' "$OUT"
grep -q 'METHOD: groq-whisper-current-dlp' "$ERR"
echo 'PASS: language autodetection path works under macOS Bash'

PATH="$TMPROOT/bin:$PATH" \
  K2B_YT_COOKIES_FILE=none \
  YT_DLP_COOKIE_BROWSER=none \
  K2B_YT_CURRENT_DLP_FALLBACK=1 \
  K2B_YT_CURRENT_DLP_BIN="$TMPROOT/current-yt-dlp" \
  K2B_YT_WHISPER_HELPER="$TMPROOT/whisper.sh" \
  FAKE_WHISPER_OLD_JSON_ERROR=1 \
  FAKE_DLP_MARKER="$TMPROOT/downloaded-audio-path" \
  /bin/bash "$ROOT/scripts/yt-transcript.sh" \
    'https://www.youtube.com/watch?v=synthetic123' --language zh \
    > "$OUT" 2> "$ERR" && exit 1
[[ ! -s "$OUT" ]]
! grep -q 'quota exceeded' "$OUT"
grep -q 'Groq returned an API error' "$ERR"
! grep -q 'Downloading public audio' "$ERR"
echo 'PASS: Groq JSON API errors stop before a redundant download'

PATH="$TMPROOT/bin:$PATH" \
  K2B_YT_COOKIES_FILE=none \
  YT_DLP_COOKIE_BROWSER=none \
  K2B_YT_CURRENT_DLP_FALLBACK=1 \
  K2B_YT_CURRENT_DLP_BIN="$TMPROOT/current-yt-dlp" \
  K2B_YT_WHISPER_HELPER="$TMPROOT/whisper.sh" \
  FAKE_WHISPER_OLD_TEXT_ERROR=1 \
  FAKE_DLP_MARKER="$TMPROOT/downloaded-audio-path" \
  /bin/bash "$ROOT/scripts/yt-transcript.sh" \
    'https://www.youtube.com/watch?v=synthetic123' --language zh \
    > "$OUT" 2> "$ERR" && exit 1
[[ ! -s "$OUT" ]]
! grep -q 'quota exceeded' "$OUT"
! grep -q 'Downloading public audio' "$ERR"
echo 'PASS: Groq plain-text API errors stop before a redundant download'

PATH="$TMPROOT/bin:$PATH" \
  K2B_YT_COOKIES_FILE=none \
  YT_DLP_COOKIE_BROWSER=none \
  K2B_YT_CURRENT_DLP_FALLBACK=1 \
  K2B_YT_CURRENT_DLP_BIN="$TMPROOT/current-yt-dlp" \
  K2B_YT_WHISPER_HELPER="$TMPROOT/whisper.sh" \
  FAKE_WHISPER_MISSING_KEY=1 \
  FAKE_DLP_MARKER="$TMPROOT/downloaded-audio-path" \
  /bin/bash "$ROOT/scripts/yt-transcript.sh" \
    'https://www.youtube.com/watch?v=synthetic123' --language zh \
    > "$OUT" 2> "$ERR" && exit 1
[[ ! -s "$OUT" ]]
grep -q 'TRANSCRIPTION_ERROR: groq-key' "$ERR"
! grep -q 'Downloading public audio' "$ERR"
echo 'PASS: missing Groq key stops before a redundant download'

OUT="$TMPROOT/error-stdout"
ERR="$TMPROOT/error-stderr"
RC=0
PATH="$TMPROOT/bin:$PATH" \
  K2B_YT_COOKIES_FILE=none \
  YT_DLP_COOKIE_BROWSER=none \
  K2B_YT_CURRENT_DLP_FALLBACK=1 \
  K2B_YT_CURRENT_DLP_BIN="$TMPROOT/current-yt-dlp" \
  K2B_YT_WHISPER_HELPER="$TMPROOT/whisper.sh" \
  FAKE_WHISPER_JSON_ERROR=1 \
  FAKE_DLP_MARKER="$TMPROOT/downloaded-audio-path" \
  /bin/bash "$ROOT/scripts/yt-transcript.sh" \
    'https://www.youtube.com/watch?v=synthetic123' --language zh \
    > "$OUT" 2> "$ERR" || RC=$?
[[ "$RC" -ne 0 ]] || { echo 'API error was treated as success' >&2; exit 1; }
[[ ! -s "$OUT" ]] || { echo 'API error leaked onto transcript stdout' >&2; exit 1; }
grep -q 'Whisper returned an API error' "$ERR"
grep -q 'METHOD: failed' "$ERR"
echo 'PASS: Groq API errors are not reported as transcripts'

RC=0
PATH="$TMPROOT/bin:$PATH" \
  K2B_YT_COOKIES_FILE=none \
  YT_DLP_COOKIE_BROWSER=none \
  K2B_YT_CURRENT_DLP_FALLBACK=1 \
  K2B_YT_CURRENT_DLP_BIN="$TMPROOT/current-yt-dlp" \
  K2B_YT_WHISPER_HELPER="$TMPROOT/whisper.sh" \
  FAKE_WHISPER_TEXT_ERROR=1 \
  FAKE_DLP_MARKER="$TMPROOT/downloaded-audio-path" \
  /bin/bash "$ROOT/scripts/yt-transcript.sh" \
    'https://www.youtube.com/watch?v=synthetic123' --language zh \
    > "$OUT" 2> "$ERR" || RC=$?
[[ "$RC" -ne 0 ]] || { echo 'Plain-text API error was treated as success' >&2; exit 1; }
[[ ! -s "$OUT" ]] || { echo 'Plain-text API error leaked onto transcript stdout' >&2; exit 1; }
grep -q 'Whisper returned an API error' "$ERR"
echo 'PASS: plain-text API errors are not reported as transcripts'

RC=0
PATH="$TMPROOT/bin:$PATH" \
  K2B_YT_CURRENT_DLP_BIN="$TMPROOT/current-yt-dlp" \
  /bin/bash "$ROOT/scripts/yt-transcribe-current-dlp.sh" \
    'https://youtube.com.evil.example/watch?v=synthetic123' \
    > "$OUT" 2> "$ERR" || RC=$?
[[ "$RC" -eq 2 ]] || { echo 'Non-YouTube host was accepted' >&2; exit 1; }
grep -q 'expected an HTTPS YouTube URL' "$ERR"
echo 'PASS: current-downloader helper accepts only YouTube HTTPS hosts'

cat > "$TMPROOT/bin/curl" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
[[ " $* " == *' response_format=json '* ]]
printf '%s\n%s' "$FAKE_CURL_BODY" "${FAKE_CURL_STATUS:-200}"
SH
cat > "$TMPROOT/bin/ffprobe" <<'SH'
#!/usr/bin/env bash
echo 1
SH
chmod +x "$TMPROOT/bin/curl"
chmod +x "$TMPROOT/bin/ffprobe"
printf '%s' 'synthetic audio' > "$TMPROOT/audio.webm"
PATH="$TMPROOT/bin:$PATH" GROQ_API_KEY=synthetic \
  FAKE_CURL_BODY='{"text":"A short, valid transcript."}' \
  /bin/bash "$ROOT/scripts/yt-transcribe-whisper.sh" "$TMPROOT/audio.webm" \
  > "$OUT" 2> "$ERR"
grep -q 'A short, valid transcript.' "$OUT"
echo 'PASS: Groq JSON text field becomes a transcript'

for body in '{"error":{"message":"model unavailable"}}' '{"text":""}'; do
  RC=0
  PATH="$TMPROOT/bin:$PATH" GROQ_API_KEY=synthetic FAKE_CURL_BODY="$body" \
    /bin/bash "$ROOT/scripts/yt-transcribe-whisper.sh" "$TMPROOT/audio.webm" \
    > "$OUT" 2> "$ERR" || RC=$?
  [[ "$RC" -ne 0 && ! -s "$OUT" ]]
  grep -q 'TRANSCRIPTION_ERROR: groq-api' "$ERR"
done
echo 'PASS: Groq error and empty JSON text cannot masquerade as transcripts'

RC=0
PATH="$TMPROOT/bin:$PATH" GROQ_API_KEY=synthetic \
  FAKE_CURL_STATUS=429 FAKE_CURL_BODY='{"error":{"message":"quota exhausted"}}' \
  /bin/bash "$ROOT/scripts/yt-transcribe-whisper.sh" "$TMPROOT/audio.webm" \
  > "$OUT" 2> "$ERR" || RC=$?
[[ "$RC" -ne 0 && ! -s "$OUT" ]]
grep -q 'Groq HTTP 429: quota exhausted' "$ERR"
echo 'PASS: Groq HTTP errors retain concrete diagnostics without transcript output'

cat > "$TMPROOT/bin/curl" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
while [[ $# -gt 0 ]]; do
  if [[ "$1" == '-o' ]]; then
    printf '%s' 'tampered release asset' > "$2"
    exit 0
  fi
  shift
done
exit 2
SH
chmod +x "$TMPROOT/bin/curl"
RC=0
PATH="$TMPROOT/bin:$PATH" \
  /bin/bash "$ROOT/scripts/yt-transcribe-current-dlp.sh" \
    'https://www.youtube.com/watch?v=synthetic123' \
    > "$OUT" 2> "$ERR" || RC=$?
[[ "$RC" -ne 0 ]] || { echo 'Tampered downloader was accepted' >&2; exit 1; }
grep -q 'checksum mismatch' "$ERR"
echo 'PASS: altered official release asset is rejected before execution'
