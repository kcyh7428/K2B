#!/usr/bin/env bash
# Remove a video from a YouTube playlist via the Data API v3
# Requires prior auth via yt-auth.sh
set -euo pipefail

if [[ $# -ne 2 ]]; then
  echo "Usage: yt-playlist-remove.sh <playlist-id> <video-id>" >&2
  exit 1
fi

PLAYLIST_ID="$1"
VIDEO_ID="$2"

TOKEN_FILE="${HOME}/.config/k2b/youtube-token.json"
CLIENT_SECRET_FILE="${HOME}/.config/gws/client_secret.json"

if [[ ! -f "$TOKEN_FILE" ]]; then
  echo "ERROR: Token file not found at ${TOKEN_FILE}. Run yt-auth.sh first." >&2
  exit 1
fi

if [[ ! -f "$CLIENT_SECRET_FILE" ]]; then
  echo "ERROR: Client secret not found at ${CLIENT_SECRET_FILE}." >&2
  exit 1
fi

# Read credentials
REFRESH_TOKEN=$(jq -r '.refresh_token' "$TOKEN_FILE")
CLIENT_ID=$(jq -r '(.installed // .web).client_id' "$CLIENT_SECRET_FILE")
CLIENT_SECRET=$(jq -r '(.installed // .web).client_secret' "$CLIENT_SECRET_FILE")

if [[ -z "$REFRESH_TOKEN" || "$REFRESH_TOKEN" == "null" ]]; then
  echo "ERROR: No refresh_token in ${TOKEN_FILE}. Run yt-auth.sh to re-authorize." >&2
  exit 1
fi

# Refresh access token
TOKEN_RESPONSE=$(/usr/bin/curl --silent --show-error \
  --fail-with-body \
  -X POST "https://oauth2.googleapis.com/token" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "client_id=${CLIENT_ID}" \
  -d "client_secret=${CLIENT_SECRET}" \
  -d "refresh_token=${REFRESH_TOKEN}" \
  -d "grant_type=refresh_token") || {
    echo "ERROR: Failed to refresh access token" >&2
    echo "$TOKEN_RESPONSE" >&2
    exit 1
  }

ACCESS_TOKEN=$(echo "$TOKEN_RESPONSE" | jq -r '.access_token')
if [[ -z "$ACCESS_TOKEN" || "$ACCESS_TOKEN" == "null" ]]; then
  echo "ERROR: No access_token in refresh response" >&2
  echo "$TOKEN_RESPONSE" >&2
  exit 1
fi

# Find the playlistItemId for this video in the playlist
SEARCH_RESPONSE=$(/usr/bin/curl --silent --show-error \
  --fail-with-body \
  -G "https://www.googleapis.com/youtube/v3/playlistItems" \
  --data-urlencode "part=id,snippet" \
  --data-urlencode "playlistId=${PLAYLIST_ID}" \
  --data-urlencode "videoId=${VIDEO_ID}" \
  --data-urlencode "maxResults=1" \
  -H "Authorization: Bearer ${ACCESS_TOKEN}") || {
    echo "ERROR: Failed to search playlist items" >&2
    echo "$SEARCH_RESPONSE" >&2
    exit 1
  }

ITEM_ID=$(echo "$SEARCH_RESPONSE" | jq -r '.items[0].id // empty')
if [[ -z "$ITEM_ID" ]]; then
  echo "WARNING: Video ${VIDEO_ID} not found in playlist ${PLAYLIST_ID}" >&2
  exit 0
fi

# Delete the playlist item
/usr/bin/curl --silent --show-error \
  --fail-with-body \
  -X DELETE "https://www.googleapis.com/youtube/v3/playlistItems?id=${ITEM_ID}" \
  -H "Authorization: Bearer ${ACCESS_TOKEN}" || {
    echo "ERROR: Failed to remove video from playlist" >&2
    exit 1
  }

echo "Removed ${VIDEO_ID} from ${PLAYLIST_ID}"
