#!/bin/bash
# Publish a LinkedIn post from a K2B draft markdown file
# Usage: ./linkedin-publish.sh <draft-path> [image-path]
# Reads token from ~/.linkedin_token
# Returns post URN on success

set -euo pipefail

DRAFT_PATH="${1:?Usage: linkedin-publish.sh <draft-path> [image-path]}"
IMAGE_PATH="${2:-}"
TOKEN_FILE="$HOME/.linkedin_token"
AUTHOR="urn:li:person:aZH9xq-NVZ"

# Check token
if [ ! -f "$TOKEN_FILE" ]; then
  echo "ERROR: No token found at $TOKEN_FILE"
  echo "Run: cd ~/Projects/signhub-io/scripts && ./linkedin-auth.sh"
  exit 1
fi

TOKEN=$(cat "$TOKEN_FILE")

# Extract post text from ## Draft section
# Takes everything between "## Draft" and the next "##" or end of file
POST_TEXT=$(awk '/^## Draft/{found=1; next} /^## /{if(found) exit} found{print}' "$DRAFT_PATH" | sed '/^$/N;/^\n$/d' | sed 's/^[[:space:]]*//' | head -c 3000)

if [ -z "$POST_TEXT" ]; then
  echo "ERROR: Could not extract text from ## Draft section in $DRAFT_PATH"
  exit 1
fi

echo "Post text (${#POST_TEXT} chars):"
echo "---"
echo "$POST_TEXT"
echo "---"

# Handle image upload if provided
MEDIA_ASSET=""
if [ -n "$IMAGE_PATH" ] && [ -f "$IMAGE_PATH" ]; then
  echo "Uploading image: $IMAGE_PATH"

  # Step 1: Register upload
  REGISTER_RESPONSE=$(curl -s -X POST "https://api.linkedin.com/v2/assets?action=registerUpload" \
    -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: application/json" \
    -d "{
      \"registerUploadRequest\": {
        \"recipes\": [\"urn:li:digitalmediaRecipe:feedshare-image\"],
        \"owner\": \"urn:li:person:aZH9xq-NVZ\",
        \"serviceRelationships\": [{
          \"relationshipType\": \"OWNER\",
          \"identifier\": \"urn:li:userGeneratedContent\"
        }]
      }
    }")

  UPLOAD_URL=$(echo "$REGISTER_RESPONSE" | python3 -c "
import sys, json
r = json.load(sys.stdin)
print(r['value']['uploadMechanism']['com.linkedin.digitalmedia.uploading.MediaUploadHttpRequest']['uploadUrl'])
" 2>/dev/null)

  MEDIA_ASSET=$(echo "$REGISTER_RESPONSE" | python3 -c "
import sys, json
r = json.load(sys.stdin)
print(r['value']['asset'])
" 2>/dev/null)

  if [ -z "$UPLOAD_URL" ] || [ -z "$MEDIA_ASSET" ]; then
    echo "ERROR: Failed to register image upload"
    echo "$REGISTER_RESPONSE" | python3 -m json.tool 2>/dev/null || echo "$REGISTER_RESPONSE"
    exit 1
  fi

  # Step 2: Upload the image binary
  UPLOAD_STATUS=$(curl -s -o /dev/null -w "%{http_code}" \
    -X PUT "$UPLOAD_URL" \
    -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: image/png" \
    --upload-file "$IMAGE_PATH")

  if [ "$UPLOAD_STATUS" != "201" ] && [ "$UPLOAD_STATUS" != "200" ]; then
    echo "ERROR: Image upload failed with status $UPLOAD_STATUS"
    exit 1
  fi

  echo "Image uploaded: $MEDIA_ASSET"
fi

# Build the post payload
if [ -n "$MEDIA_ASSET" ]; then
  # Image post
  POST_PAYLOAD=$(python3 -c "
import json, sys
text = sys.stdin.read()
payload = {
    'author': '$AUTHOR',
    'lifecycleState': 'PUBLISHED',
    'specificContent': {
        'com.linkedin.ugc.ShareContent': {
            'shareCommentary': {'text': text},
            'shareMediaCategory': 'IMAGE',
            'media': [{
                'status': 'READY',
                'media': '$MEDIA_ASSET'
            }]
        }
    },
    'visibility': {
        'com.linkedin.ugc.MemberNetworkVisibility': 'PUBLIC'
    }
}
print(json.dumps(payload))
" <<< "$POST_TEXT")
else
  # Text-only post
  POST_PAYLOAD=$(python3 -c "
import json, sys
text = sys.stdin.read()
payload = {
    'author': '$AUTHOR',
    'lifecycleState': 'PUBLISHED',
    'specificContent': {
        'com.linkedin.ugc.ShareContent': {
            'shareCommentary': {'text': text},
            'shareMediaCategory': 'NONE'
        }
    },
    'visibility': {
        'com.linkedin.ugc.MemberNetworkVisibility': 'PUBLIC'
    }
}
print(json.dumps(payload))
" <<< "$POST_TEXT")
fi

# Publish
RESPONSE=$(curl -s -X POST "https://api.linkedin.com/v2/ugcPosts" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -H "X-Restli-Protocol-Version: 2.0.0" \
  -d "$POST_PAYLOAD")

# Check result
POST_ID=$(echo "$RESPONSE" | python3 -c "import sys,json; print(json.load(sys.stdin).get('id',''))" 2>/dev/null)

if [ -n "$POST_ID" ]; then
  echo ""
  echo "Published: $POST_ID"
  exit 0
else
  ERROR_CODE=$(echo "$RESPONSE" | python3 -c "import sys,json; print(json.load(sys.stdin).get('serviceErrorCode',''))" 2>/dev/null)
  if [ "$ERROR_CODE" = "65604" ] || [ "$ERROR_CODE" = "65601" ]; then
    echo ""
    echo "ERROR: Token expired or invalid. Re-authenticate:"
    echo "  cd ~/Projects/signhub-io/scripts && ./linkedin-auth.sh"
    exit 1
  fi
  echo ""
  echo "ERROR: Failed to publish"
  echo "$RESPONSE" | python3 -m json.tool 2>/dev/null || echo "$RESPONSE"
  exit 1
fi
