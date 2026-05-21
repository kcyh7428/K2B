#!/usr/bin/env bash
# Regression tests for scripts/yt-transcript.sh.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SCRIPT="$REPO_ROOT/scripts/yt-transcript.sh"

TMPROOT="$(mktemp -d)"
trap 'rm -rf "$TMPROOT"' EXIT

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

sha256_file() {
  python3 - "$1" <<'PY'
import hashlib
import sys
with open(sys.argv[1], "rb") as handle:
    print(hashlib.sha256(handle.read()).hexdigest())
PY
}

echo "=== yt-transcript.test.sh ==="

{
  d="$TMPROOT/no-cookie-caption"
  fakebin="$d/bin"
  mkdir -p "$fakebin"
  cat > "$fakebin/yt-dlp" <<'SH'
#!/usr/bin/env bash
set -euo pipefail

out=""
extract_audio=false
while [[ $# -gt 0 ]]; do
  case "$1" in
    --output|-o)
      out="$2"
      shift 2
      ;;
    -x)
      extract_audio=true
      shift
      ;;
    *)
      shift
      ;;
  esac
done

if [[ "$extract_audio" == true ]]; then
  echo "fake audio extraction blocked" >&2
  exit 1
fi

if [[ -z "$out" ]]; then
  echo "missing --output" >&2
  exit 1
fi

vtt="${out//%(id)s/fake-video}.en.vtt"
mkdir -p "$(dirname "$vtt")"
cat > "$vtt" <<'VTT'
WEBVTT
Kind: captions
Language: en

00:00:00.000 --> 00:00:05.000 align:start position:0%
This is a synthetic transcript line with enough content to exceed the minimum threshold for extraction.

00:00:05.000 --> 00:00:10.000 align:start position:0%
The helper should pass an empty no-cookie attempt to yt-dlp under Apple Bash without crashing.
VTT
SH
  chmod +x "$fakebin/yt-dlp"

  out="$d/out.txt"
  err="$d/err.txt"
  if ! PATH="$fakebin:$PATH" YT_DLP_COOKIE_BROWSER=none /bin/bash "$SCRIPT" "https://youtu.be/fakeVideo123" >"$out" 2>"$err"; then
    cat "$err" >&2
    fail "no-cookie captions should succeed under /bin/bash"
  fi
  grep -q "METHOD: captions-en" "$err" || fail "expected captions-en method"
  ! grep -q "unbound variable" "$err" || fail "empty cookie args must not trip set -u"
  grep -q "synthetic transcript line" "$out" || fail "expected transcript text on stdout"
  echo "  PASS: no-cookie captions work under Apple Bash"
}

{
  d="$TMPROOT/cookie-file-caption"
  fakebin="$d/bin"
  cookies="$d/youtube-cookies.txt"
  calls="$d/calls.txt"
  mkdir -p "$fakebin"
  printf '# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tTRUE\t1893456000\tSID\tfake\n' > "$cookies"
  original_cookie_sha=$(sha256_file "$cookies")
  cat > "$fakebin/yt-dlp" <<'SH'
#!/usr/bin/env bash
set -euo pipefail

cookie_file=""
out=""
extract_audio=false
while [[ $# -gt 0 ]]; do
  case "$1" in
    --cookies)
      cookie_file="$2"
      shift 2
      ;;
    --output|-o)
      out="$2"
      shift 2
      ;;
    -x)
      extract_audio=true
      shift
      ;;
    *)
      shift
      ;;
  esac
done

if [[ "$extract_audio" == true ]]; then
  echo "fake audio extraction blocked" >&2
  exit 1
fi

printf 'cookie:%s\n' "$cookie_file" >> "$FAKE_YTDLP_CALLS"
if [[ -z "$cookie_file" ]]; then
  echo "Sign in to confirm you're not a bot" >&2
  exit 1
fi
if [[ ! -r "$cookie_file" ]]; then
  echo "cookie file is not readable: $cookie_file" >&2
  exit 1
fi
if ! grep -q $'SID\tfake' "$cookie_file"; then
  echo "cookie file does not contain expected test cookie" >&2
  exit 1
fi
printf '# yt-dlp mutation should not reach the source file\n' >> "$cookie_file"
if [[ -z "$out" ]]; then
  echo "missing --output" >&2
  exit 1
fi

vtt="${out//%(id)s/fake-video}.en.vtt"
mkdir -p "$(dirname "$vtt")"
cat > "$vtt" <<'VTT'
WEBVTT
Kind: captions
Language: en

00:00:00.000 --> 00:00:05.000 align:start position:0%
This cookie-file transcript only appears after yt-dlp receives the configured Netscape cookies file.

00:00:05.000 --> 00:00:10.000 align:start position:0%
The helper must retry challenged caption extraction with --cookies instead of browser keychain cookies.
VTT
SH
  chmod +x "$fakebin/yt-dlp"

  out="$d/out.txt"
  err="$d/err.txt"
  if ! PATH="$fakebin:$PATH" \
    K2B_YT_COOKIES_FILE="$cookies" \
    YT_DLP_COOKIE_BROWSER=none \
    FAKE_YTDLP_CALLS="$calls" \
    EXPECTED_COOKIE_FILE="$cookies" \
    /bin/bash "$SCRIPT" "https://youtu.be/fakeVideo123" >"$out" 2>"$err"; then
    cat "$err" >&2
    fail "cookie-file captions should succeed after no-cookie challenge"
  fi
  grep -q "METHOD: captions-en" "$err" || fail "expected captions-en method with cookie file"
  grep -q "^cookie:$" "$calls" || fail "expected initial no-cookie attempt"
  ! grep -q "^cookie:$cookies$" "$calls" || fail "yt-dlp must receive a temp cookie copy, not the source file"
  grep -q "cookie-file transcript" "$out" || fail "expected transcript text from cookie-file retry"
  current_cookie_sha=$(sha256_file "$cookies")
  [[ "$current_cookie_sha" == "$original_cookie_sha" ]] || fail "source cookie file must not be mutated"
  echo "  PASS: cookie-file captions retry after challenge"
}

{
  d="$TMPROOT/timeout-then-cookie-caption"
  fakebin="$d/bin"
  cookies="$d/youtube-cookies.txt"
  mkdir -p "$fakebin"
  printf '# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tTRUE\t1893456000\tSID\tfake\n' > "$cookies"
  cat > "$fakebin/yt-dlp" <<'SH'
#!/usr/bin/env bash
set -euo pipefail

cookie_file=""
out=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --cookies)
      cookie_file="$2"
      shift 2
      ;;
    --output|-o)
      out="$2"
      shift 2
      ;;
    *)
      shift
      ;;
  esac
done

if [[ -z "$cookie_file" ]]; then
  sleep 4
  exit 1
fi
if [[ -z "$out" ]]; then
  echo "missing --output" >&2
  exit 1
fi

vtt="${out//%(id)s/fake-video}.en.vtt"
mkdir -p "$(dirname "$vtt")"
cat > "$vtt" <<'VTT'
WEBVTT
Kind: captions
Language: en

00:00:00.000 --> 00:00:05.000 align:start position:0%
This transcript proves that a hung no-cookie yt-dlp attempt timed out and the cookie retry still ran.

00:00:05.000 --> 00:00:10.000 align:start position:0%
The timeout must be per attempt so one bad network request cannot consume the entire prefetch budget.
VTT
SH
  chmod +x "$fakebin/yt-dlp"

  out="$d/out.txt"
  err="$d/err.txt"
  start=$(date +%s)
  if ! PATH="$fakebin:$PATH" \
    K2B_YT_COOKIES_FILE="$cookies" \
    K2B_YT_DLP_ATTEMPT_TIMEOUT=1 \
    YT_DLP_COOKIE_BROWSER=none \
    /bin/bash "$SCRIPT" "https://youtu.be/fakeVideo123" >"$out" 2>"$err"; then
    cat "$err" >&2
    fail "cookie retry should succeed after hung no-cookie attempt times out"
  fi
  elapsed=$(( $(date +%s) - start ))
  [[ "$elapsed" -lt 4 ]] || fail "hung no-cookie attempt was not bounded; elapsed=${elapsed}s"
  grep -q "METHOD: captions-en" "$err" || fail "expected captions-en method after timeout"
  grep -q "timed out after 1s" "$err" || fail "expected timeout warning"
  grep -q "hung no-cookie yt-dlp attempt timed out" "$out" || fail "expected transcript text after retry"
  echo "  PASS: hung caption attempts time out and fall through"
}

{
  d="$TMPROOT/whisper-timeout"
  fakebin="$d/bin"
  helper="$d/slow-whisper.sh"
  mkdir -p "$fakebin"
  cat > "$fakebin/yt-dlp" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
exit 1
SH
  chmod +x "$fakebin/yt-dlp"
  cat > "$helper" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
sleep 4
echo "should not print"
SH
  chmod +x "$helper"

  out="$d/out.txt"
  err="$d/err.txt"
  start=$(date +%s)
  EXIT=0
  PATH="$fakebin:$PATH" \
    K2B_YT_COOKIES_FILE=none \
    YT_DLP_COOKIE_BROWSER=none \
    K2B_YT_DLP_ATTEMPT_TIMEOUT=1 \
    K2B_YT_WHISPER_HELPER="$helper" \
    K2B_YT_WHISPER_TIMEOUT=1 \
    /bin/bash "$SCRIPT" "https://youtu.be/fakeVideo123" >"$out" 2>"$err" || EXIT=$?
  elapsed=$(( $(date +%s) - start ))
  [[ "$EXIT" -ne 0 ]] || fail "hung Whisper fallback should fail after timeout"
  [[ "$elapsed" -lt 4 ]] || fail "hung Whisper fallback was not bounded; elapsed=${elapsed}s"
  grep -q "command timed out after 1s" "$err" || fail "expected Whisper timeout warning"
  ! grep -q "should not print" "$out" || fail "timed-out helper output should not be treated as transcript"
  echo "  PASS: Whisper fallback is timeout-bounded"
}
