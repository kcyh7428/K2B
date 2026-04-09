#!/usr/bin/env bash
# Poll a YouTube playlist for new videos, or extract audio from a video
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# --- Extract-audio mode ---
if [[ "${1:-}" == "--extract-audio" ]]; then
  if [[ $# -lt 3 ]]; then
    echo "Usage: yt-playlist-poll.sh --extract-audio <video-url> <output-dir>" >&2
    exit 1
  fi
  VIDEO_URL="$2"
  OUTPUT_DIR="$3"
  mkdir -p "$OUTPUT_DIR"

  # Extract video ID from URL (avoid second yt-dlp call)
  VIDEO_ID=$(echo "$VIDEO_URL" | grep -oE '[?&]v=([^&]+)' | head -1 | cut -d= -f2)
  if [[ -z "$VIDEO_ID" ]]; then
    # Fallback: try youtu.be format
    VIDEO_ID=$(echo "$VIDEO_URL" | grep -oE 'youtu\.be/([^?&]+)' | head -1 | cut -d/ -f2)
  fi
  if [[ -z "$VIDEO_ID" ]]; then
    # Fallback: try youtube.com/shorts/ format
    VIDEO_ID=$(echo "$VIDEO_URL" | grep -oE 'youtube\.com/shorts/([^?&]+)' | head -1 | sed 's|youtube\.com/shorts/||')
  fi
  if [[ -z "$VIDEO_ID" ]]; then
    echo "ERROR: Could not extract video ID from URL: ${VIDEO_URL}" >&2
    exit 1
  fi

  # Download and convert to mp3
  yt-dlp -x --audio-format mp3 --audio-quality 5 \
    -o "${OUTPUT_DIR}/${VIDEO_ID}.%(ext)s" \
    "$VIDEO_URL" >&2

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
PLAYLIST_JSON=$(yt-dlp --flat-playlist -j "$PLAYLIST_URL" 2>/dev/null) || {
  echo "ERROR: Failed to fetch playlist: ${PLAYLIST_URL}" >&2
  exit 1
}

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
