#!/usr/bin/env bash
# Transcribe audio via GPTsAPI /v1/audio/transcriptions (OpenAI-compatible Whisper).
#
# Usage: gptsapi-transcribe.sh <audio-file> [language] [model]
#   audio-file: path to mp3/wav/m4a/ogg/flac/webm/mp4 (max ~25 MB)
#   language:   ISO-639-1 hint (e.g. "en", "zh"). Default: auto-detect.
#   model:      whisper-1  (only model GPTsAPI exposes today; default)
#
# Requires GPTSAPI_KEY in the environment.
# Prints the transcribed text to stdout.

set -euo pipefail

AUDIO_FILE="${1:?Usage: gptsapi-transcribe.sh <audio-file> [language] [model]}"
LANG_HINT="${2:-}"
MODEL="${3:-whisper-1}"

if [[ ! -f "$AUDIO_FILE" ]]; then
  echo "ERROR: audio file not found: $AUDIO_FILE" >&2
  exit 1
fi

if [[ -z "${GPTSAPI_KEY:-}" ]]; then
  echo "ERROR: GPTSAPI_KEY is not set in the environment." >&2
  exit 1
fi

GPTSAPI_API_HOST="${GPTSAPI_API_HOST:-https://api.gptsapi.net}"

echo "Transcribing: $(basename "$AUDIO_FILE")" >&2
echo "Model: ${MODEL}${LANG_HINT:+, language: $LANG_HINT}" >&2

# Build curl form args
FORM_ARGS=(
  -F "file=@${AUDIO_FILE}"
  -F "model=${MODEL}"
  -F "response_format=json"
)
[[ -n "$LANG_HINT" ]] && FORM_ARGS+=(-F "language=${LANG_HINT}")

RESPONSE=$(curl -sS --connect-timeout 30 --max-time 300 \
  -X POST "${GPTSAPI_API_HOST}/v1/audio/transcriptions" \
  -H "Authorization: Bearer ${GPTSAPI_KEY}" \
  "${FORM_ARGS[@]}") || {
    echo "ERROR: curl failed" >&2
    exit 1
  }

# Try to extract `.text`; if that fails it's likely an error JSON.
TEXT=$(echo "$RESPONSE" | jq -r '.text // empty' 2>/dev/null || true)

if [[ -z "$TEXT" ]]; then
  echo "ERROR: GPTsAPI did not return a transcription:" >&2
  echo "$RESPONSE" >&2
  exit 1
fi

printf '%s\n' "$TEXT"
