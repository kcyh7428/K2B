#!/bin/bash
# Check LinkedIn post status and engagement
# Usage: ./linkedin-status.sh [post-urn]
# Without args: list recent posts
# With post URN: get specific post details

set -euo pipefail

TOKEN_FILE="$HOME/.linkedin_token"
AUTHOR="urn:li:person:aZH9xq-NVZ"

if [ ! -f "$TOKEN_FILE" ]; then
  echo "ERROR: No token found at $TOKEN_FILE"
  echo "Run: cd ~/Projects/signhub-io/scripts && ./linkedin-auth.sh"
  exit 1
fi

TOKEN=$(cat "$TOKEN_FILE")

if [ -n "${1:-}" ]; then
  # Get specific post
  POST_URN="$1"
  echo "Fetching post: $POST_URN"
  curl -s -H "Authorization: Bearer $TOKEN" \
    -H "X-Restli-Protocol-Version: 2.0.0" \
    "https://api.linkedin.com/v2/ugcPosts/${POST_URN}" | python3 -m json.tool
else
  # List recent posts
  echo "Recent LinkedIn posts:"
  echo ""
  ENCODED_AUTHOR=$(python3 -c "import urllib.parse; print(urllib.parse.quote('$AUTHOR'))")
  RESPONSE=$(curl -s -H "Authorization: Bearer $TOKEN" \
    -H "X-Restli-Protocol-Version: 2.0.0" \
    "https://api.linkedin.com/v2/ugcPosts?q=authors&authors=List(${ENCODED_AUTHOR})&count=10")

  echo "$RESPONSE" | python3 -c "
import sys, json
from datetime import datetime

data = json.load(sys.stdin)
posts = data.get('elements', [])

if not posts:
    print('No posts found.')
    sys.exit(0)

for p in posts:
    ts = p.get('created', {}).get('time', 0) / 1000
    date = datetime.fromtimestamp(ts).strftime('%Y-%m-%d %H:%M')
    text = p.get('specificContent', {}).get('com.linkedin.ugc.ShareContent', {}).get('shareCommentary', {}).get('text', '')
    preview = text[:80].replace('\n', ' ')
    post_id = p.get('id', 'unknown')
    print(f'{date}  {preview}...')
    print(f'  URN: {post_id}')
    print()
" 2>/dev/null || echo "$RESPONSE" | python3 -m json.tool 2>/dev/null || echo "$RESPONSE"

  echo ""
  echo "Note: Engagement metrics (impressions, reactions, comments) require Community Management API."
  echo "Status: pending approval. Check email at keith.cheung@signhub.io for verification request."
fi
