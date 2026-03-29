#!/usr/bin/env bash
# Generate images via MiniMax image-01 API
# Usage: minimax-image.sh "prompt" [aspect-ratio] [slug]
#   aspect-ratio: 1:1, 16:9, 4:3, 3:2, 2:3, 3:4, 9:16, 21:9 (default: 16:9)
#   slug: filename slug (default: auto-generated from prompt)

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/minimax-common.sh"

PROMPT="${1:?Usage: minimax-image.sh \"prompt\" [aspect-ratio] [slug]}"
ASPECT="${2:-16:9}"
SLUG="${3:-$(slugify "${PROMPT:0:40}")}"

DIR=$(ensure_dir images)
FILENAME="$(today)_image_${SLUG}.png"
OUTPATH="${DIR}/${FILENAME}"

echo "Generating image: ${PROMPT}" >&2
echo "Aspect ratio: ${ASPECT}" >&2

RESPONSE=$(mm_api POST /v1/image_generation "$(jq -n \
  --arg prompt "$PROMPT" \
  --arg aspect "$ASPECT" \
  '{
    model: "image-01",
    prompt: $prompt,
    aspect_ratio: $aspect,
    n: 1,
    promptOptimizer: true
  }'
)")

# Extract image URL from response
IMAGE_URL=$(echo "$RESPONSE" | jq -r '.data.image_urls[0] // .data[0].url // empty')

if [[ -z "$IMAGE_URL" ]]; then
  # Some responses embed base64
  IMAGE_B64=$(echo "$RESPONSE" | jq -r '.data.image_base64[0] // .data[0].b64_json // empty')
  if [[ -n "$IMAGE_B64" ]]; then
    echo "$IMAGE_B64" | base64 -d > "$OUTPATH"
    echo "Saved: ${OUTPATH}"
  else
    echo "ERROR: Could not extract image from response" >&2
    echo "$RESPONSE" | jq . >&2
    exit 1
  fi
else
  download_file "$IMAGE_URL" "$OUTPATH"
fi

VAULT_REL="Assets/images/${FILENAME}"
echo ""
echo "Vault path: ${VAULT_REL}"
echo "Obsidian embed: ![[${VAULT_REL}]]"
