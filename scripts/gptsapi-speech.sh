#!/usr/bin/env bash
# Generate speech (TTS) via GPTsAPI /v1/audio/speech (OpenAI-compatible).
#
# Usage: gptsapi-speech.sh "text" [voice] [model] [slug]
#   voice: alloy | echo | fable | onyx | nova | shimmer  (default: onyx)
#   model: tts-1 | tts-1-hd  (default: tts-1-hd)
#   slug:  filename slug (default: derived from text)
#
# Requires GPTSAPI_KEY in the environment.
# Writes the MP3 to ${K2B_VAULT}/Assets/audio/YYYY-MM-DD_speech_<slug>.mp3.

set -euo pipefail

TEXT="${1:?Usage: gptsapi-speech.sh \"text\" [voice] [model] [slug]}"
VOICE="${2:-onyx}"
MODEL="${3:-tts-1-hd}"
SLUG_INPUT="${4:-}"

if [[ -z "${GPTSAPI_KEY:-}" ]]; then
  echo "ERROR: GPTSAPI_KEY is not set in the environment." >&2
  exit 1
fi

GPTSAPI_API_HOST="${GPTSAPI_API_HOST:-https://api.gptsapi.net}"
K2B_VAULT="${K2B_VAULT:-${HOME}/Projects/K2B-Vault}"
ASSETS_DIR="${K2B_VAULT}/Assets/audio"
mkdir -p "$ASSETS_DIR"

slugify() {
  echo "$1" | tr '[:upper:]' '[:lower:]' \
    | sed 's/[^a-z0-9]/-/g' \
    | sed 's/--*/-/g' \
    | sed 's/^-//;s/-$//' \
    | cut -c1-60
}

if [[ -z "$SLUG_INPUT" ]]; then
  SLUG=$(slugify "${TEXT:0:30}")
  # CJK / non-Latin input slugifies to empty -- fall back to content hash so
  # two different non-Latin phrases don't collide on the generic "clip" name.
  if [[ -z "$SLUG" ]]; then
    SLUG=$(printf '%s' "$TEXT" | md5 -q 2>/dev/null | cut -c1-8 \
      || printf '%s' "$TEXT" | md5sum | cut -c1-8)
    [[ -z "$SLUG" ]] && SLUG="clip"
  fi
else
  SLUG="$SLUG_INPUT"
fi

TODAY=$(date +%Y-%m-%d)
FILENAME="${TODAY}_speech_${SLUG}.mp3"
OUTPATH="${ASSETS_DIR}/${FILENAME}"

echo "Generating speech: ${TEXT:0:60}..." >&2
echo "Model: ${MODEL}, Voice: ${VOICE}" >&2

PAYLOAD=$(jq -n \
  --arg model "$MODEL" \
  --arg input "$TEXT" \
  --arg voice "$VOICE" \
  '{model: $model, input: $input, voice: $voice, response_format: "mp3"}')

# Write to a temp file first so a non-200 or partial response never lands at
# the final asset path (Obsidian/Syncthing/concurrent readers must never see a
# corrupt MP3). Only atomic-rename into place on full success.
TMPFILE=$(mktemp "${TMPDIR:-/tmp}/k2b-gptsapi-speech.XXXXXX") || {
  echo "ERROR: failed to create tmp file" >&2
  exit 1
}
trap 'rm -f "$TMPFILE"' EXIT

HTTP_CODE=$(curl -sS -m 60 -o "$TMPFILE" -w "%{http_code}" \
  -X POST "${GPTSAPI_API_HOST}/v1/audio/speech" \
  -H "Authorization: Bearer ${GPTSAPI_KEY}" \
  -H "Content-Type: application/json" \
  -d "$PAYLOAD") || {
    echo "ERROR: curl failed" >&2
    exit 1
  }

if [[ "$HTTP_CODE" != "200" ]]; then
  echo "ERROR: GPTsAPI returned HTTP ${HTTP_CODE}" >&2
  head -c 500 "$TMPFILE" >&2 || true
  echo "" >&2
  exit 1
fi

# Sanity check - make sure we got binary audio, not a JSON error body.
# (Secondary defense; atomic write above is the primary guard.)
if head -c 4 "$TMPFILE" | grep -q '{'; then
  echo "ERROR: GPTsAPI returned JSON instead of audio:" >&2
  cat "$TMPFILE" >&2
  exit 1
fi

mv "$TMPFILE" "$OUTPATH"
# Clear trap -- TMPFILE is now $OUTPATH and we want it kept.
trap - EXIT

VAULT_REL="Assets/audio/${FILENAME}"
echo "Saved: ${OUTPATH}"
echo ""
echo "Vault path: ${VAULT_REL}"
echo "Obsidian embed: ![[${VAULT_REL}]]"
