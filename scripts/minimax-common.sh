#!/usr/bin/env bash
# Shared utilities for MiniMax API scripts
# Sourced by all minimax-*.sh scripts

set -euo pipefail

# --- Config ---
MINIMAX_API_KEY="${MINIMAX_API_KEY:?Set MINIMAX_API_KEY in your environment}"
MINIMAX_API_HOST="${MINIMAX_API_HOST:-https://api.minimaxi.com}"

# Detect vault path: Mac Mini (fastshower) vs MacBook (keithmbpm2)
if [ -n "${K2B_VAULT:-}" ]; then
  : # Already set via env
elif [ -d "/Users/fastshower/Projects/K2B-Vault" ]; then
  K2B_VAULT="/Users/fastshower/Projects/K2B-Vault"
elif [ -d "/Users/keithmbpm2/Projects/K2B-Vault" ]; then
  K2B_VAULT="/Users/keithmbpm2/Projects/K2B-Vault"
else
  K2B_VAULT="$HOME/Projects/K2B-Vault"
fi
ASSETS_DIR="${K2B_VAULT}/Assets"

# --- Helpers ---

today() {
  date +%Y-%m-%d
}

slugify() {
  echo "$1" | tr '[:upper:]' '[:lower:]' | sed 's/[^a-z0-9]/-/g' | sed 's/--*/-/g' | sed 's/^-//;s/-$//'
}

# Make an authenticated API call
# Usage: mm_api POST /v1/image_generation '{"model":"image-01",...}'
mm_api() {
  local method="$1"
  local path="$2"
  local body="${3:-}"

  local url="${MINIMAX_API_HOST}${path}"
  local args=(
    --silent
    --show-error
    --fail-with-body
    -X "$method"
    -H "Authorization: Bearer ${MINIMAX_API_KEY}"
    -H "Content-Type: application/json"
  )

  if [[ -n "$body" ]]; then
    args+=(-d "$body")
  fi

  local response
  response=$(curl "${args[@]}" "$url" 2>&1) || {
    echo "ERROR: API call failed" >&2
    echo "$response" >&2
    return 1
  }

  # Check for API-level errors
  local error_code
  error_code=$(echo "$response" | jq -r '.base_resp.status_code // .error_code // empty' 2>/dev/null)
  if [[ -n "$error_code" && "$error_code" != "0" && "$error_code" != "null" ]]; then
    local error_msg
    error_msg=$(echo "$response" | jq -r '.base_resp.status_msg // .error_message // "Unknown error"' 2>/dev/null)
    echo "ERROR: API returned error ${error_code}: ${error_msg}" >&2
    echo "$response" >&2
    return 1
  fi

  echo "$response"
}

# Download a file from URL
# Usage: download_file "https://..." "/path/to/output.png"
download_file() {
  local url="$1"
  local output="$2"

  curl --silent --show-error --fail -o "$output" "$url" || {
    echo "ERROR: Failed to download ${url}" >&2
    return 1
  }
  echo "Saved: ${output}"
}

# Ensure an assets subdirectory exists
# Usage: ensure_dir images
ensure_dir() {
  local subdir="${ASSETS_DIR}/$1"
  mkdir -p "$subdir"
  echo "$subdir"
}

# Print the Obsidian embed path for an asset
# Usage: obsidian_embed "Assets/images/2026-03-25_image_test.png"
obsidian_embed() {
  echo "![[${1}]]"
}
