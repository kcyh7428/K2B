#!/usr/bin/env bash
# Recover public YouTube audio when the installed yt-dlp cannot download it.
# A verified official yt-dlp release runs only from a private temporary directory.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TMPDIR_BASE="$(mktemp -d "${TMPDIR:-/tmp}/k2b-yt-current.XXXXXX")"
chmod 700 "$TMPDIR_BASE"
trap 'rm -rf "$TMPDIR_BASE"' EXIT
trap 'exit 130' INT TERM

if [[ $# -lt 1 || "$1" != http* ]]; then
  echo "Usage: yt-transcribe-current-dlp.sh <youtube-url> [--language <lang>]" >&2
  exit 2
fi
URL="$1"
shift
if ! python3 - "$URL" <<'PY'
import sys
from urllib.parse import urlparse

url = urlparse(sys.argv[1])
host = (url.hostname or "").lower()
valid_host = host in {"youtube.com", "youtu.be", "youtube-nocookie.com"} or host.endswith((".youtube.com", ".youtube-nocookie.com"))
raise SystemExit(0 if url.scheme == "https" and valid_host else 1)
PY
then
  echo "ERROR: expected an HTTPS YouTube URL" >&2
  exit 2
fi
LANGUAGE=""
if [[ $# -gt 0 ]]; then
  if [[ $# -ne 2 || "$1" != "--language" || -z "$2" ]]; then
    echo "Usage: yt-transcribe-current-dlp.sh <youtube-url> [--language <lang>]" >&2
    exit 2
  fi
  LANGUAGE="$2"
fi

if ! command -v deno >/dev/null 2>&1; then
  for candidate in /opt/homebrew/bin/deno /usr/local/bin/deno; do
    if [[ -x "$candidate" ]]; then
      export PATH="${candidate%/deno}:$PATH"
      break
    fi
  done
fi
if ! command -v deno >/dev/null 2>&1; then
  echo "ERROR: Deno is required for the current yt-dlp YouTube extractor" >&2
  exit 1
fi

DLP_BIN="${K2B_YT_CURRENT_DLP_BIN:-}"
DOWNLOAD_METHOD="verified-yt-dlp-public"
if [[ -z "$DLP_BIN" ]]; then
  DLP_BIN="$TMPDIR_BASE/yt-dlp"
  echo "Fetching verified official yt-dlp 2026.08.19 into a temporary directory..." >&2
  curl -fsSL --max-time 60 \
    https://github.com/yt-dlp/yt-dlp/releases/download/2026.08.19/yt-dlp \
    -o "$DLP_BIN"
  if ! python3 - "$DLP_BIN" <<'PY'
import hashlib
import sys
from pathlib import Path

expected = "1fa6733c37ea6fb51c99ad8fe785e7b7e5f3246c9b980230329d4fb72ed8d4d6"
actual = hashlib.sha256(Path(sys.argv[1]).read_bytes()).hexdigest()
raise SystemExit(0 if actual == expected else 1)
PY
  then
    echo "ERROR: pinned yt-dlp checksum mismatch; refusing to run it" >&2
    exit 1
  fi
else
  DOWNLOAD_METHOD="local-yt-dlp-unverified"
  echo "WARN: local downloader override skips pinned-release verification" >&2
fi
if [[ ! -s "$DLP_BIN" ]]; then
  echo "ERROR: current yt-dlp is missing or empty" >&2
  exit 1
fi

TIMEOUT="${K2B_YT_CURRENT_DOWNLOAD_TIMEOUT:-900}"
case "$TIMEOUT" in
  ''|*[!0-9]*) TIMEOUT=900 ;;
esac
if [[ "$TIMEOUT" -le 0 ]]; then
  TIMEOUT=900
fi

echo "Downloading public audio without YouTube cookies or login..." >&2
python3 - "$TIMEOUT" python3 "$DLP_BIN" \
  --ignore-config --no-cache-dir \
  --js-runtimes "deno:$(command -v deno)" \
  --no-remote-components \
  --no-playlist --socket-timeout 20 --retries 1 \
  -f ba -o "$TMPDIR_BASE/audio.%(ext)s" "$URL" <<'PY'
import os
import signal
import subprocess
import sys

timeout = int(sys.argv[1])
proc = subprocess.Popen(sys.argv[2:], start_new_session=True)
try:
    raise SystemExit(proc.wait(timeout=timeout))
except subprocess.TimeoutExpired:
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        proc.wait(timeout=1)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.wait()
    print(f"ERROR: audio download timed out after {timeout}s", file=sys.stderr)
    raise SystemExit(124)
PY

AUDIO_FILE=""
for candidate in "$TMPDIR_BASE"/audio.*; do
  if [[ -f "$candidate" && "$candidate" != *.part ]]; then
    AUDIO_FILE="$candidate"
    break
  fi
done
if [[ -z "$AUDIO_FILE" ]]; then
  echo "ERROR: current yt-dlp did not produce an audio file" >&2
  exit 1
fi

WHISPER_HELPER="${K2B_YT_WHISPER_HELPER:-$SCRIPT_DIR/yt-transcribe-whisper.sh}"
if [[ ! -x "$WHISPER_HELPER" ]]; then
  echo "ERROR: Whisper helper is not executable: $WHISPER_HELPER" >&2
  exit 1
fi
echo "Transcribing downloaded audio with Groq Whisper..." >&2
if [[ -n "$LANGUAGE" ]]; then
  OUTPUT=$("$WHISPER_HELPER" "$AUDIO_FILE" --language "$LANGUAGE")
else
  OUTPUT=$("$WHISPER_HELPER" "$AUDIO_FILE")
fi
if [[ -z "$OUTPUT" ]]; then
  echo "ERROR: Whisper returned an empty transcript" >&2
  exit 1
fi
if printf '%s' "$OUTPUT" | python3 -c 'import json,sys
text = sys.stdin.read().strip()
if text.lower().rstrip(".") in {"quota exceeded", "rate limit", "rate limit exceeded", "unauthorized", "too many requests", "invalid api key"} or (len(text) < 200 and text.lower().startswith("error: ")):
    raise SystemExit(0)
try:
    data = json.loads(text)
except (ValueError, UnicodeDecodeError):
    raise SystemExit(1)
raise SystemExit(0 if isinstance(data, dict) and "error" in data else 1)'; then
  echo "ERROR: Whisper returned an API error instead of a transcript" >&2
  exit 1
fi
printf '%s\n' "$OUTPUT"
echo "DOWNLOAD_METHOD: $DOWNLOAD_METHOD" >&2
