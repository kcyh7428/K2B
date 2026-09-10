#!/usr/bin/env bash
# Shared utilities for Kimi text-worker scripts with historical minimax-* names.
# Sourced by all minimax-*.sh compatibility scripts.
#
# ============================================================================
# IMPORTANT: MiniMax subscription EXPIRED on or before 2026-05-27.
# All MiniMax endpoints (text chatcompletion, VLM, image, video, music) now
# return {"base_resp":{"status_code":2049,"status_msg":"invalid api key"}}.
#
# The MiniMax branch in this file (K2B_LLM_PROVIDER=minimax) is therefore
# INERT in production. The Kimi branch (K2B_LLM_PROVIDER=kimi, the default)
# is the only working text path.
#
# We keep the MiniMax branch code intact, not delete it, because:
# 1. The provider abstraction is useful if a future text provider plugs in.
# 2. Removing dead-but-quiescent text code is a separate cleanup ship.
# 3. Any future MiniMax reactivation must be an explicit provider ship, not a
#    quiet K2B_LLM_PROVIDER=minimax env flip.
#
# Spec: wiki/concepts/Shipped/feature_vlm-gptsapi-migration.md
# ============================================================================

set -euo pipefail

_K2B_MINIMAX_COMMON_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Optional dedicated credentials file for non-interactive environments.
K2B_ENV_FILE="${K2B_ENV_FILE:-$HOME/.k2b-env}"
K2B_LLM_PROVIDER="${K2B_LLM_PROVIDER:-kimi}"

# --- Config ---
# Load only the credential required by the selected provider. Once the
# dedicated file resolves it, do not source interactive shell startup logic.
if [[ "$K2B_LLM_PROVIDER" == "kimi" && -z "${KIMI_API_KEY:-}" && -f "$K2B_ENV_FILE" ]]; then
  if ! python3 -c '
import os
import stat
import sys

info = os.stat(sys.argv[1])
if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600:
    raise SystemExit(1)
' "$K2B_ENV_FILE"; then
    echo "ERROR: Kimi credential file must be owned by the current user and mode 0600: $K2B_ENV_FILE" >&2
    return 1 2>/dev/null || exit 1
  fi
  # shellcheck disable=SC1091
  set +eu
  source "$K2B_ENV_FILE" >/dev/null 2>&1 || true
  set -euo pipefail
fi

if [[ "$K2B_LLM_PROVIDER" == "minimax" ]]; then
  echo "ERROR: K2B_LLM_PROVIDER=minimax is deprecated and disabled (MiniMax subscription expired)." >&2
  echo "       Set K2B_LLM_PROVIDER=kimi." >&2
  return 1 2>/dev/null || exit 1
fi
MINIMAX_API_HOST="${MINIMAX_API_HOST:-https://api.minimaxi.com}"

# --- LLM text-provider switch (Kimi K2.7 primary, legacy MiniMax branch inert) ---
# 2026-04-25: MiniMax Plus plan started returning status_code 2061 "your current
# token plan not support model" for every text model (M2.7, M2.5, abab6.5, etc.).
# Kimi K2.7 via the /coding/v1 Anthropic-compatible endpoint is the new primary.
# Kimi is text-only. GPTsAPI owns image, VLM/OCR, TTS, and STT in K2B now.
# Do not set K2B_LLM_PROVIDER=minimax in live K2B. The branch remains only for
# historical compatibility until a future provider ship deliberately re-enables it.
KIMI_API_HOST="${KIMI_API_HOST:-https://api.kimi.com/coding}"
KIMI_DEFAULT_MODEL="${KIMI_DEFAULT_MODEL:-kimi-k2.7-code}"

# Kimi Code membership and Kimi Open Platform are separate services. Parse the
# URL rather than matching a literal string so case, ports, and path variants
# cannot bypass the metered-endpoint guard. Kimi credentials are never sent
# over plaintext HTTP.
_k2b_kimi_host_meta=$(python3 -c '
import sys
from urllib.parse import urlparse
parsed = urlparse(sys.argv[1])
if not parsed.scheme or not parsed.hostname:
    raise SystemExit(1)
print(parsed.scheme.lower() + "\t" + parsed.hostname.lower().rstrip("."))
' "$KIMI_API_HOST") || {
  echo "ERROR: KIMI_API_HOST must be a valid absolute URL." >&2
  return 1 2>/dev/null || exit 1
}
IFS=$'\t' read -r _k2b_kimi_scheme _k2b_kimi_hostname <<<"$_k2b_kimi_host_meta"
if [[ "$_k2b_kimi_scheme" != "https" ]]; then
  echo "ERROR: KIMI_API_HOST must use HTTPS." >&2
  return 1 2>/dev/null || exit 1
fi
case "$_k2b_kimi_hostname" in
  api.moonshot.cn|api.moonshot.ai)
    _k2b_metered_opt_in=$(printf '%s' "${K2B_ALLOW_METERED_KIMI_PLATFORM:-false}" | tr '[:upper:]' '[:lower:]')
    if [[ "$_k2b_metered_opt_in" != "true" ]]; then
      echo "ERROR: KIMI_API_HOST selects the pay-as-you-go Kimi Open Platform." >&2
      echo "       K2B defaults to Kimi Code membership at https://api.kimi.com/coding." >&2
      echo "       A metered platform switch requires explicit K2B_ALLOW_METERED_KIMI_PLATFORM=true." >&2
      return 1 2>/dev/null || exit 1
    fi
    ;;
esac
# Convenience exposed to callers: use this instead of hardcoding model ids.
if [[ "$K2B_LLM_PROVIDER" == "kimi" ]]; then
  K2B_LLM_MODEL="${K2B_LLM_MODEL:-$KIMI_DEFAULT_MODEL}"
  K2B_TEXT_WORKER_NAME="${K2B_TEXT_WORKER_NAME:-Kimi}"
  K2B_TEXT_WORKER_ERROR_KEY="${K2B_TEXT_WORKER_ERROR_KEY:-kimi_api_error}"
else
  K2B_LLM_MODEL="${K2B_LLM_MODEL:-MiniMax-M2.7}"
  K2B_TEXT_WORKER_NAME="${K2B_TEXT_WORKER_NAME:-MiniMax}"
  K2B_TEXT_WORKER_ERROR_KEY="${K2B_TEXT_WORKER_ERROR_KEY:-minimax_api_error}"
fi
export K2B_TEXT_WORKER_NAME K2B_TEXT_WORKER_ERROR_KEY

# KIMI_API_KEY must be in the environment or the dedicated per-machine file.
if [[ "$K2B_LLM_PROVIDER" == "kimi" && -z "${KIMI_API_KEY:-}" ]]; then
  echo "ERROR: KIMI_API_KEY is not set." >&2
  echo "       Configure the affected Mac's ~/.k2b-env with mode 0600." >&2
  echo "       Keep K2B_LLM_PROVIDER=kimi; MiniMax fallback is disabled." >&2
  return 1 2>/dev/null || exit 1
fi

# Detect the current Mac's vault path.
if [ -n "${K2B_VAULT:-}" ]; then
  : # Already set via env
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

# Make an authenticated API call.
# Routes text chatcompletion requests to Kimi (when K2B_LLM_PROVIDER=kimi).
# Non-text MiniMax paths still fall through to MiniMax if an old caller uses
# mm_api directly, but production image/VLM/TTS/STT callers have moved to
# GPTsAPI wrappers. Callers keep writing OpenAI-style bodies -- the Kimi branch
# translates to/from Anthropic Messages on the wire.
#
# Usage: mm_api POST /v1/text/chatcompletion_v2 '{"model":"...","messages":[...],...}'
#        mm_api POST /v1/image_generation      '{"model":"image-01",...}'
mm_api() {
  local method="$1"
  local path="$2"
  local body="${3:-}"

  if [[ "$K2B_LLM_PROVIDER" == "kimi" && "$path" == /v1/text/chatcompletion_v2* ]]; then
    _mm_api_kimi_text "$method" "$body"
    return $?
  fi
  _mm_api_minimax "$method" "$path" "$body"
}

# Direct MiniMax call -- original mm_api behavior, now only a legacy fallback
# for non-text MiniMax paths and text when K2B_LLM_PROVIDER=minimax.
_mm_api_minimax() {
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

# Kimi K2.7 (Anthropic Messages shape at /coding/v1/messages).
# Translates an OpenAI chatcompletion_v2 body IN, and returns an OpenAI-shape
# envelope OUT so existing jq callers see no wire difference:
#   .choices[0].message.content    <- content[0].text
#   .usage.{prompt,completion,total}_tokens  (Kimi already emits these)
#   .base_resp.status_code = 0     (injected so existing error checks pass)
_mm_api_kimi_text() {
  local method="$1"
  local body="${2:-}"

  if [[ "$method" != "POST" ]]; then
    echo "ERROR: _mm_api_kimi_text supports POST only" >&2
    return 1
  fi
  if [[ -z "$body" ]]; then
    echo "ERROR: _mm_api_kimi_text requires a JSON body" >&2
    return 1
  fi

  # Route legacy shell workers through the same streaming, retrying Python
  # client as the reviewer. This avoids upstream idle disconnects during
  # Kimi's long thinking phase and keeps one response-validation contract.
  local response
  response=$(printf '%s' "$body" | \
    KIMI_API_KEY="$KIMI_API_KEY" \
    KIMI_API_HOST="$KIMI_API_HOST" \
    KIMI_DEFAULT_MODEL="$K2B_LLM_MODEL" \
    K2B_LLM_PROVIDER="kimi" \
    K2B_LLM_MAX_TOKENS="${K2B_LLM_MAX_TOKENS:-16384}" \
    PYTHONPATH="${_K2B_MINIMAX_COMMON_DIR}/lib${PYTHONPATH:+:$PYTHONPATH}" \
    "${K2B_KIMI_PYTHON:-python3}" \
      "${_K2B_MINIMAX_COMMON_DIR}/lib/minimax_common.py" \
      --kimi-openai-payload) || {
    echo "ERROR: Kimi API call failed" >&2
    return 1
  }
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
