#!/usr/bin/env bash
# Generate speech (TTS) via MiniMax speech API
# Usage: minimax-speech.sh "text" [voice-id] [emotion] [slug]
#   voice-id: default "male-qn-qingse" (see MiniMax docs for full list)
#   emotion: neutral, happy, sad, angry, fearful, disgusted, surprised
#   slug: filename slug (default: auto-generated)

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/minimax-common.sh"

TEXT="${1:?Usage: minimax-speech.sh \"text\" [voice-id] [emotion] [slug]}"
VOICE="${2:-male-qn-qingse}"
EMOTION="${3:-neutral}"
SLUG="${4:-$(slugify "${TEXT:0:30}")}"

DIR=$(ensure_dir audio)
FILENAME="$(today)_speech_${SLUG}.mp3"
OUTPATH="${DIR}/${FILENAME}"

echo "Generating speech: ${TEXT:0:60}..." >&2
echo "Voice: ${VOICE}, Emotion: ${EMOTION}" >&2

RESPONSE=$(mm_api POST /v1/t2a_v2 "$(jq -n \
  --arg text "$TEXT" \
  --arg voice "$VOICE" \
  --arg emotion "$EMOTION" \
  '{
    model: "speech-2.8-hd",
    text: $text,
    voiceId: $voice,
    emotion: $emotion,
    format: "mp3",
    sampleRate: 32000,
    bitrate: 128000,
    languageBoost: "auto"
  }'
)")

# Extract audio - response may contain base64 audio or a URL
AUDIO_URL=$(echo "$RESPONSE" | jq -r '.data.audio_url // .audio_file.url // empty')

if [[ -n "$AUDIO_URL" ]]; then
  download_file "$AUDIO_URL" "$OUTPATH"
else
  # Try base64 hex/base64 audio data
  AUDIO_DATA=$(echo "$RESPONSE" | jq -r '.data.audio // .extra_info.audio // empty')
  if [[ -n "$AUDIO_DATA" ]]; then
    echo "$AUDIO_DATA" | base64 -d > "$OUTPATH" 2>/dev/null || \
    echo "$AUDIO_DATA" | xxd -r -p > "$OUTPATH"
    echo "Saved: ${OUTPATH}"
  else
    echo "ERROR: Could not extract audio from response" >&2
    echo "$RESPONSE" | jq . >&2
    exit 1
  fi
fi

VAULT_REL="Assets/audio/${FILENAME}"
echo ""
echo "Vault path: ${VAULT_REL}"
echo "Obsidian embed: ![[${VAULT_REL}]]"
