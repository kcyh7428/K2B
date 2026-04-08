#!/usr/bin/env bash
# Transcribe audio via Groq Whisper API
# Accepts a YouTube URL (extracts audio first) or a local audio file path
# Splits files >240s into chunks for API limits
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TMPDIR_BASE="${TMPDIR:-/tmp}/yt-whisper-$$"
LANGUAGE=""

cleanup() {
  rm -rf "$TMPDIR_BASE"
}
trap cleanup EXIT

usage() {
  echo "Usage: yt-transcribe-whisper.sh <video-url-or-audio-path> [--language <lang>]" >&2
  echo "" >&2
  echo "  If given a YouTube URL, extracts audio first via yt-playlist-poll.sh --extract-audio" >&2
  echo "  If given a file path, uses it directly" >&2
  echo "  --language <lang>  Language code (e.g. zh, en). Default: auto-detect" >&2
  exit 1
}

if [[ $# -lt 1 ]]; then
  usage
fi

INPUT="$1"
shift

while [[ $# -gt 0 ]]; do
  case "$1" in
    --language)
      LANGUAGE="$2"
      shift 2
      ;;
    *)
      echo "ERROR: Unknown option: $1" >&2
      usage
      ;;
  esac
done

# Resolve Groq API key
GROQ_KEY="${GROQ_API_KEY:-}"
if [[ -z "$GROQ_KEY" ]]; then
  ENV_FILE="$SCRIPT_DIR/../k2b-remote/.env"
  if [[ -f "$ENV_FILE" ]]; then
    GROQ_KEY=$(grep -E '^GROQ_API_KEY=' "$ENV_FILE" | head -1 | cut -d= -f2- | tr -d '"' | tr -d "'")
  fi
fi
if [[ -z "$GROQ_KEY" ]]; then
  echo "ERROR: GROQ_API_KEY not set and not found in k2b-remote/.env" >&2
  exit 1
fi

mkdir -p "$TMPDIR_BASE"

# Determine audio file path
AUDIO_FILE=""
if [[ "$INPUT" == http* ]]; then
  # YouTube URL -- extract audio
  echo "Extracting audio from YouTube URL..." >&2
  "$SCRIPT_DIR/yt-playlist-poll.sh" --extract-audio "$INPUT" "$TMPDIR_BASE"
  AUDIO_FILE=$(find "$TMPDIR_BASE" -type f \( -name '*.m4a' -o -name '*.mp3' -o -name '*.wav' -o -name '*.opus' -o -name '*.webm' \) | head -1)
  if [[ -z "$AUDIO_FILE" ]]; then
    echo "ERROR: Audio extraction produced no output" >&2
    exit 1
  fi
else
  # Local file path
  if [[ ! -f "$INPUT" ]]; then
    echo "ERROR: File not found: $INPUT" >&2
    exit 1
  fi
  AUDIO_FILE="$INPUT"
fi

# Check duration with ffprobe
DURATION=$(ffprobe -v error -show_entries format=duration -of default=noprint_wrappers=1:nokey=1 "$AUDIO_FILE" 2>/dev/null | cut -d. -f1)
DURATION="${DURATION:-0}"

MAX_DURATION=240

transcribe_chunk() {
  local chunk_file="$1"
  local lang_args=()
  if [[ -n "$LANGUAGE" ]]; then
    lang_args=(-F "language=$LANGUAGE")
  fi

  curl -s --retry 2 --retry-delay 3 \
    https://api.groq.com/openai/v1/audio/transcriptions \
    -H "Authorization: Bearer $GROQ_KEY" \
    -F "file=@${chunk_file}" \
    -F "model=whisper-large-v3" \
    -F "response_format=text" \
    "${lang_args[@]}"
}

if [[ "$DURATION" -le "$MAX_DURATION" ]]; then
  # Single file, no splitting needed
  transcribe_chunk "$AUDIO_FILE"
else
  # Split into chunks
  echo "Audio is ${DURATION}s, splitting into ${MAX_DURATION}s chunks..." >&2
  CHUNK_DIR="$TMPDIR_BASE/chunks"
  mkdir -p "$CHUNK_DIR"

  # Get the file extension for the output chunks
  EXT="${AUDIO_FILE##*.}"
  ffmpeg -v error -i "$AUDIO_FILE" -f segment -segment_time "$MAX_DURATION" -c copy "${CHUNK_DIR}/chunk_%03d.${EXT}"

  # Transcribe each chunk in order
  FULL_TRANSCRIPT=""
  for chunk in $(ls "${CHUNK_DIR}"/chunk_*.${EXT} | sort); do
    echo "Transcribing $(basename "$chunk")..." >&2
    RESULT=$(transcribe_chunk "$chunk")
    if [[ -n "$FULL_TRANSCRIPT" ]]; then
      FULL_TRANSCRIPT="${FULL_TRANSCRIPT} ${RESULT}"
    else
      FULL_TRANSCRIPT="$RESULT"
    fi
  done

  echo "$FULL_TRANSCRIPT"
fi
