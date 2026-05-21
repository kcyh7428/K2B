#!/usr/bin/env bash
# Regression tests for scripts/yt-playlist-poll.sh.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SCRIPT="$REPO_ROOT/scripts/yt-playlist-poll.sh"

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

echo "=== yt-playlist-poll.test.sh ==="

{
  d="$TMPROOT/extract-audio-cookies"
  fakebin="$d/bin"
  cookies="$d/youtube-cookies.txt"
  outdir="$d/audio"
  mkdir -p "$fakebin"
  printf '# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tTRUE\t1893456000\tSID\tfake\n' > "$cookies"
  original_cookie_sha=$(sha256_file "$cookies")

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
    -o)
      out="$2"
      shift 2
      ;;
    *)
      shift
      ;;
  esac
done

if [[ "$cookie_file" == "$EXPECTED_COOKIE_FILE" ]]; then
  echo "yt-dlp received the source cookie file directly" >&2
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
  echo "missing output template" >&2
  exit 1
fi

mp3="${out//%(ext)s/mp3}"
mkdir -p "$(dirname "$mp3")"
printf 'fake mp3\n' > "$mp3"
SH
  chmod +x "$fakebin/yt-dlp"

  output=$(PATH="$fakebin:$PATH" \
    K2B_YT_COOKIES_FILE="$cookies" \
    EXPECTED_COOKIE_FILE="$cookies" \
    /bin/bash "$SCRIPT" --extract-audio "https://youtu.be/fakeVideo123?si=test" "$outdir" 2>"$d/err.txt")
  [[ "$output" == "$outdir/fakeVideo123.mp3" ]] || fail "expected mp3 path, got $output"
  [[ -f "$outdir/fakeVideo123.mp3" ]] || fail "expected mp3 file"
  current_cookie_sha=$(sha256_file "$cookies")
  [[ "$current_cookie_sha" == "$original_cookie_sha" ]] || fail "source cookie file must not be mutated"
  echo "  PASS: extract-audio passes configured cookie file to yt-dlp"
}

{
  d="$TMPROOT/extract-audio-browser-cookies"
  fakebin="$d/bin"
  outdir="$d/audio"
  mkdir -p "$fakebin"

  cat > "$fakebin/yt-dlp" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
browser=""
cookies_file=""
out=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --cookies-from-browser) browser="$2"; shift 2 ;;
    --cookies) cookies_file="$2"; shift 2 ;;
    -o) out="$2"; shift 2 ;;
    *) shift ;;
  esac
done
if [[ "$browser" != "firefox" ]]; then
  echo "expected --cookies-from-browser firefox, got '$browser'" >&2
  exit 2
fi
if [[ -n "$cookies_file" ]]; then
  echo "expected no --cookies file when K2B_YT_COOKIES_FILE=none, got '$cookies_file'" >&2
  exit 3
fi
mp3="${out//%(ext)s/mp3}"
mkdir -p "$(dirname "$mp3")"
printf 'fake mp3 from firefox cookies\n' > "$mp3"
SH
  chmod +x "$fakebin/yt-dlp"

  output=$(PATH="$fakebin:$PATH" \
    K2B_YT_COOKIES_FILE=none \
    YT_DLP_COOKIE_BROWSER=firefox \
    /bin/bash "$SCRIPT" --extract-audio "https://youtu.be/fakeBrowser123" "$outdir" 2>"$d/err.txt")
  [[ "$output" == "$outdir/fakeBrowser123.mp3" ]] || fail "expected mp3 path, got $output (err: $(cat "$d/err.txt"))"
  echo "  PASS: extract-audio honors YT_DLP_COOKIE_BROWSER (firefox path)"
}

{
  d="$TMPROOT/extract-audio-timeout"
  fakebin="$d/bin"
  outdir="$d/audio"
  mkdir -p "$fakebin"

  cat > "$fakebin/yt-dlp" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
sleep 4
SH
  chmod +x "$fakebin/yt-dlp"

  start=$(date +%s)
  EXIT=0
  PATH="$fakebin:$PATH" \
    K2B_YT_COOKIES_FILE=none \
    K2B_YT_DLP_ATTEMPT_TIMEOUT=1 \
    /bin/bash "$SCRIPT" --extract-audio "https://youtu.be/fakeVideo123?si=test" "$outdir" >"$d/out.txt" 2>"$d/err.txt" || EXIT=$?
  elapsed=$(( $(date +%s) - start ))
  [[ "$EXIT" -ne 0 ]] || fail "hung audio extraction should fail after timeout"
  [[ "$elapsed" -lt 4 ]] || fail "hung audio extraction was not bounded; elapsed=${elapsed}s"
  grep -q "timed out after 1s" "$d/err.txt" || fail "expected timeout warning"
  echo "  PASS: extract-audio yt-dlp calls are timeout-bounded"
}
