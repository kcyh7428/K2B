#!/usr/bin/env bash
# Search YouTube and return results as JSON lines
# Standalone script -- does not source minimax-common.sh
set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "Usage: yt-search.sh \"<query>\" [--max N]" >&2
  exit 1
fi

QUERY="$1"
shift

MAX_RESULTS=10
while [[ $# -gt 0 ]]; do
  case "$1" in
    --max)
      MAX_RESULTS="$2"
      shift 2
      ;;
    *)
      echo "ERROR: Unknown option: $1" >&2
      exit 1
      ;;
  esac
done

# Fetch results first, then parse (so yt-dlp errors are caught by set -e)
RESULTS=$(yt-dlp "ytsearch${MAX_RESULTS}:${QUERY}" --flat-playlist -j 2>/dev/null) || {
  echo "ERROR: Search failed for query: ${QUERY}" >&2
  exit 1
}

if [[ -z "$RESULTS" ]]; then
  echo "No results found for: ${QUERY}" >&2
  exit 0
fi

echo "$RESULTS" | jq -c '{
  id: .id,
  title: .title,
  channel: (.channel // .uploader // "unknown"),
  duration_string: (.duration_string // "unknown"),
  view_count: (.view_count // 0),
  upload_date: (.upload_date // "unknown")
}'
