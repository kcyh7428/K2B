#!/usr/bin/env bash
# Shared utilities for MiniMax API scripts
# Sourced by all minimax-*.sh scripts

set -euo pipefail

# --- Config ---
# The shell that sources this file may be non-interactive (e.g. Claude Code's
# Bash tool, a cron job, or a background pm2 process) and may not have sourced
# ~/.zshrc. If MINIMAX_API_KEY isn't in env, fall back to sourcing .zshrc once.
# This makes every minimax-*.sh script work from any shell without the caller
# having to remember to source the profile first. Same pattern already used
# by scripts/claude-minimaxi.sh and scripts/minimax-review.sh.
#
# Safety assumption: Keith's ~/.zshrc contains only PATH exports and API key
# exports (no early-exit guards like `[[ $- != *i* ]] || return`, no
# zsh-specific control flow). If that assumption ever stops holding, the
# better fix is a dedicated ~/.minimax-env credentials-only file updated in
# all three callers at once (MiniMax review note 2026-04-21).
if [[ -z "${MINIMAX_API_KEY:-}" && -f "$HOME/.zshrc" ]]; then
  set +u
  source "$HOME/.zshrc" >/dev/null 2>&1 || true
  set -u
fi

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

# Append one line per MiniMax job invocation to wiki/context/minimax-jobs.jsonl.
# This is the observability contract from the Opus->MiniMax offload plan
# (see wiki/projects/project_minimax-offload.md). Every new minimax-*.sh script
# should call this on both success and failure paths so drift is detectable.
#
# Usage: log_job_invocation <job_name> <prompt_version> <model> <input_bytes> <output_bytes> <parse_status> <duration_ms>
# parse_status values: ok | fence | invalid | empty_response | api_error
log_job_invocation() {
  local job="$1"
  local prompt_version="$2"
  local model="$3"
  local input_bytes="$4"
  local output_bytes="$5"
  local parse_status="$6"
  local duration_ms="$7"
  local ts
  ts=$(date -u +%Y-%m-%dT%H:%M:%SZ)

  local log_file="${K2B_VAULT}/wiki/context/minimax-jobs.jsonl"
  mkdir -p "$(dirname "$log_file")"

  jq -cn \
    --arg ts "$ts" \
    --arg job "$job" \
    --arg pv "$prompt_version" \
    --arg model "$model" \
    --argjson ib "$input_bytes" \
    --argjson ob "$output_bytes" \
    --arg ps "$parse_status" \
    --argjson dm "$duration_ms" \
    '{ts: $ts, job: $job, prompt_version: $pv, model: $model, input_bytes: $ib, output_bytes: $ob, parse_status: $ps, duration_ms: $dm, manual_override: false}' \
    >> "$log_file" 2>/dev/null || true
}
