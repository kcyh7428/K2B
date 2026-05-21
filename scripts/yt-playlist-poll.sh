#!/usr/bin/env bash
# Poll a YouTube playlist for new videos, or extract audio from a video
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TMPDIR_BASE=""

cleanup() {
  if [[ -n "$TMPDIR_BASE" ]]; then
    rm -rf "$TMPDIR_BASE"
  fi
}
trap cleanup EXIT

build_cookies_file() {
  local choice="${K2B_YT_COOKIES_FILE:-$HOME/.config/k2b/youtube-cookies.txt}"
  case "$choice" in
    none|"")
      return 0
      ;;
    "~/"*)
      choice="$HOME/${choice#~/}"
      ;;
  esac

  if [[ -r "$choice" && -s "$choice" ]]; then
    echo "$choice"
  elif [[ -n "${K2B_YT_COOKIES_FILE:-}" ]]; then
    echo "WARN: K2B_YT_COOKIES_FILE is set but not readable/non-empty: $choice" >&2
  fi
  return 0
}

prepare_cookies_file() {
  local source_file
  source_file=$(build_cookies_file)
  if [[ -z "$source_file" ]]; then
    return 0
  fi

  if [[ -z "$TMPDIR_BASE" ]]; then
    TMPDIR_BASE=$(mktemp -d "${TMPDIR:-/tmp}/yt-playlist-poll.XXXXXX")
    chmod 700 "$TMPDIR_BASE"
  fi

  local copy_file="$TMPDIR_BASE/youtube-cookies.txt"
  cp "$source_file" "$copy_file"
  chmod 600 "$copy_file"
  echo "$copy_file"
}

run_yt_dlp_with_timeout() {
  local timeout_s="${K2B_YT_DLP_ATTEMPT_TIMEOUT:-180}"
  case "$timeout_s" in
    ''|*[!0-9]*)
      timeout_s=180
      ;;
  esac
  if [[ "$timeout_s" -le 0 ]]; then
    timeout_s=180
  fi

  set +e
  python3 - "$timeout_s" yt-dlp "$@" <<'PY'
import os
import signal
import subprocess
import sys

timeout_s = float(sys.argv[1])
argv = sys.argv[2:]

try:
    proc = subprocess.Popen(argv, start_new_session=True)
except FileNotFoundError:
    raise SystemExit(127)

try:
    raise SystemExit(proc.wait(timeout=timeout_s))
except subprocess.TimeoutExpired:
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        proc.wait(timeout=0.5)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.wait()
    raise SystemExit(124)
PY
  local rc=$?
  set -e
  if [[ "$rc" -eq 124 ]]; then
    echo "WARN: yt-dlp attempt timed out after ${timeout_s}s" >&2
  fi
  return "$rc"
}

# --- Extract-audio mode ---
if [[ "${1:-}" == "--extract-audio" ]]; then
  if [[ $# -lt 3 ]]; then
    echo "Usage: yt-playlist-poll.sh --extract-audio <video-url> <output-dir>" >&2
    exit 1
  fi
  VIDEO_URL="$2"
  OUTPUT_DIR="$3"
  mkdir -p "$OUTPUT_DIR"

  # Extract video ID from URL (avoid second yt-dlp call).
  # `|| true` protects against set -euo pipefail killing the script when grep
  # finds no match on a given URL shape; fall through to the next pattern.
  VIDEO_ID=$(echo "$VIDEO_URL" | grep -oE '[?&]v=([^&]+)' | head -1 | cut -d= -f2 || true)
  if [[ -z "$VIDEO_ID" ]]; then
    # Fallback: try youtu.be format
    VIDEO_ID=$(echo "$VIDEO_URL" | grep -oE 'youtu\.be/([^?&]+)' | head -1 | cut -d/ -f2 || true)
  fi
  if [[ -z "$VIDEO_ID" ]]; then
    # Fallback: try youtube.com/shorts/ format
    VIDEO_ID=$(echo "$VIDEO_URL" | grep -oE 'youtube\.com/shorts/([^?&]+)' | head -1 | sed 's|youtube\.com/shorts/||' || true)
  fi
  if [[ -z "$VIDEO_ID" ]]; then
    # Exit code 2 = video ID extraction failed. Callers (yt-transcribe-whisper.sh,
    # yt-transcript.sh) distinguish this from "audio download failed" (exit 1)
    # so they can surface a useful error instead of the generic "no audio output"
    # message that masks the real cause. MiniMax HIGH finding #2.
    echo "ERROR: Could not extract video ID from URL: ${VIDEO_URL}" >&2
    exit 2
  fi

  COOKIE_FILE=$(prepare_cookies_file)

  # Download and convert to mp3
  if [[ -n "$COOKIE_FILE" ]]; then
    run_yt_dlp_with_timeout --cookies "$COOKIE_FILE" -x --audio-format mp3 --audio-quality 5 \
      -o "${OUTPUT_DIR}/${VIDEO_ID}.%(ext)s" \
      "$VIDEO_URL" >&2
  else
    run_yt_dlp_with_timeout -x --audio-format mp3 --audio-quality 5 \
      -o "${OUTPUT_DIR}/${VIDEO_ID}.%(ext)s" \
      "$VIDEO_URL" >&2
  fi

  MP3_PATH="${OUTPUT_DIR}/${VIDEO_ID}.mp3"
  if [[ -f "$MP3_PATH" ]]; then
    echo "$MP3_PATH"
  else
    echo "ERROR: Expected mp3 not found at ${MP3_PATH}" >&2
    exit 1
  fi
  exit 0
fi

# --- List mode (default) ---
if [[ $# -lt 2 ]]; then
  echo "Usage: yt-playlist-poll.sh <playlist-url> <processed-log-path> [--max N]" >&2
  echo "       yt-playlist-poll.sh --extract-audio <video-url> <output-dir>" >&2
  exit 1
fi

PLAYLIST_URL="$1"
PROCESSED_LOG="$2"
shift 2

MAX_NEW=5
while [[ $# -gt 0 ]]; do
  case "$1" in
    --max)
      MAX_NEW="$2"
      shift 2
      ;;
    *)
      echo "ERROR: Unknown option: $1" >&2
      exit 1
      ;;
  esac
done

# Ensure processed log exists
touch "$PROCESSED_LOG"

# Fetch playlist metadata as JSON lines
COOKIE_FILE=$(prepare_cookies_file)
if [[ -n "$COOKIE_FILE" ]]; then
  PLAYLIST_JSON=$(run_yt_dlp_with_timeout --cookies "$COOKIE_FILE" --flat-playlist -j "$PLAYLIST_URL" 2>/dev/null) || {
    echo "ERROR: Failed to fetch playlist: ${PLAYLIST_URL}" >&2
    exit 1
  }
else
  PLAYLIST_JSON=$(run_yt_dlp_with_timeout --flat-playlist -j "$PLAYLIST_URL" 2>/dev/null) || {
    echo "ERROR: Failed to fetch playlist: ${PLAYLIST_URL}" >&2
    exit 1
  }
fi

# Filter to new videos, extract fields, cap at --max
# Use process substitution to avoid subshell variable scope issues
COUNT=0
while IFS= read -r line; do
  VIDEO_ID=$(echo "$line" | jq -r '.id // empty')
  [[ -z "$VIDEO_ID" ]] && continue

  # Skip if already processed
  if grep -qF "$VIDEO_ID" "$PROCESSED_LOG" 2>/dev/null; then
    continue
  fi

  TITLE=$(echo "$line" | jq -r '.title // "Untitled"')
  UPLOAD_DATE=$(echo "$line" | jq -r '.upload_date // "unknown"')
  # Construct full URL (--flat-playlist may return just the ID in .url)
  URL="https://www.youtube.com/watch?v=${VIDEO_ID}"

  printf '%s\t%s\t%s\t%s\n' "$VIDEO_ID" "$TITLE" "$UPLOAD_DATE" "$URL"

  COUNT=$((COUNT + 1))
  if [[ $COUNT -ge $MAX_NEW ]]; then
    break
  fi
done < <(echo "$PLAYLIST_JSON")
