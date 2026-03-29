#!/usr/bin/env bash
# Add a video to a YouTube playlist via the Data API v3
# Requires prior auth via yt-auth.sh
set -euo pipefail

if [[ $# -ne 2 ]]; then
  echo "Usage: yt-playlist-add.sh <playlist-id> <video-id>" >&2
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
# client_secret.json can have credentials under .installed or .web
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

# Add video to playlist
BODY=$(jq -n \
  --arg pid "$PLAYLIST_ID" \
  --arg vid "$VIDEO_ID" \
  '{snippet: {playlistId: $pid, resourceId: {kind: "youtube#video", videoId: $vid}}}')

API_RESPONSE=$(/usr/bin/curl --silent --show-error \
  --fail-with-body \
  -X POST "https://www.googleapis.com/youtube/v3/playlistItems?part=snippet" \
  -H "Authorization: Bearer ${ACCESS_TOKEN}" \
  -H "Content-Type: application/json" \
  -d "$BODY") || {
    echo "ERROR: Failed to add video to playlist" >&2
    echo "$API_RESPONSE" >&2
    exit 1
  }

# Verify success
RESULT_ID=$(echo "$API_RESPONSE" | jq -r '.id // empty')
if [[ -n "$RESULT_ID" ]]; then
  echo "Added ${VIDEO_ID} to ${PLAYLIST_ID}"
else
  echo "ERROR: Unexpected API response" >&2
  echo "$API_RESPONSE" >&2
  exit 1
fi
