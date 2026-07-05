#!/usr/bin/env bash
# Generate images via GPTsAPI gpt-image-2.
# GPTSAPI_KEY MUST be supplied by the caller's environment. The script does
# not read .env files of its own accord; the TypeScript caller in
# k2b-remote/src/mediaCommand.ts is the single source of truth for keys.

set -euo pipefail

usage() {
  cat >&2 <<'EOF'
Usage: gptsapi-image.sh --prompt "text" [options]

Options:
  --prompt TEXT          Image prompt. Required.
  --aspect-ratio RATIO  1:1, 16:9, 9:16, 4:3, or 3:4. Default: 16:9.
  --output-format FMT   png or jpeg. Default: png.
  --output PATH         Output image path. Default: K2B vault Assets/images.
  --slug SLUG           Filename slug when --output is omitted.
  --timeout SECONDS     Poll timeout. Default: 120.
  --poll-interval SEC   Poll interval. Default: 5.
EOF
}

slugify() {
  printf '%s' "$1" \
    | tr '[:upper:]' '[:lower:]' \
    | sed 's/[^a-z0-9]/-/g' \
    | sed 's/--*/-/g' \
    | sed 's/^-//;s/-$//' \
    | cut -c1-60
}

# Compute encoded variants of the key without putting it in the python3
# process environment. The key is fed via stdin; only the variant strings
# come back on stdout. Cached at first call.
GPTSAPI_KEY_VARIANTS=""
compute_key_variants() {
  if [[ -z "${GPTSAPI_KEY:-}" || -n "$GPTSAPI_KEY_VARIANTS" ]]; then
    return
  fi
  if ! command -v python3 >/dev/null 2>&1; then
    GPTSAPI_KEY_VARIANTS="$GPTSAPI_KEY"
    return
  fi
  GPTSAPI_KEY_VARIANTS=$(printf '%s' "$GPTSAPI_KEY" | python3 -c '
import json
import sys
import urllib.parse

key = sys.stdin.read()
if not key:
    sys.exit(0)
variants = {key, urllib.parse.quote(key, safe=""), json.dumps(key)[1:-1]}
print("\n".join(sorted(variants, key=len, reverse=True)))
' 2>/dev/null) || GPTSAPI_KEY_VARIANTS="$GPTSAPI_KEY"
}

emit_error() {
  local code="$1"
  local detail="$2"
  # Strip null bytes. `$'\0'` inside `${var//...}` is a bad substitution on
  # macOS system bash (3.2), which aborts the whole error handler. Use the
  # printf|tr idiom (matches the sed redactions below) for portability.
  detail=$(printf '%s' "$detail" | tr -d '\0')
  if [[ -n "${HOME:-}" ]]; then
    detail="${detail//${HOME}/~}"
  fi
  if [[ -n "${K2B_VAULT_PATH:-}" ]]; then
    detail="${detail//${K2B_VAULT_PATH}/\$K2B_VAULT_PATH}"
  fi
  # Mask the key and its URL/JSON encoded variants. The key never crosses
  # a process environment during redaction (variants are computed via
  # stdin to python3, then this function uses bash parameter substitution).
  if [[ -n "${GPTSAPI_KEY:-}" ]]; then
    compute_key_variants
    if [[ -n "$GPTSAPI_KEY_VARIANTS" ]]; then
      while IFS= read -r variant; do
        if [[ -n "$variant" ]]; then
          detail="${detail//${variant}/<gptsapi-key>}"
        fi
      done <<<"$GPTSAPI_KEY_VARIANTS"
    fi
  fi
  # `I` (case-insensitive) is GNU-sed only. Drop it for macOS BSD sed
  # portability; Authorization headers are always canonical-cased anyway.
  detail=$(printf '%s' "$detail" | sed -E 's#Authorization:[[:space:]]*Bearer[[:space:]]+[^[:space:]]+#Authorization: Bearer <gptsapi-key>#g')
  detail=$(printf '%s' "$detail" | sed -E 's#/tmp/[^"[:space:]]+#<tmp>#g; s#/var/folders/[^"[:space:]]+#<tmp>#g')
  if (( ${#detail} > 4000 )); then
    detail="${detail:0:4000}... [truncated]"
  fi
  jq -cn --arg code "$code" --arg detail "$detail" '{error: $code, detail: $detail}' >&2 \
    || printf '{"error":"%s","detail":"failed to serialize error detail"}\n' "$code" >&2
}

prompt=""
aspect_ratio="16:9"
output_format="png"
output_path=""
slug=""
timeout_seconds=120
poll_interval=5

while [[ $# -gt 0 ]]; do
  case "$1" in
    --prompt)
      prompt="${2:?--prompt needs text}"
      shift 2
      ;;
    --aspect-ratio)
      aspect_ratio="${2:?--aspect-ratio needs a value}"
      shift 2
      ;;
    --output-format)
      output_format="${2:?--output-format needs a value}"
      shift 2
      ;;
    --output)
      output_path="${2:?--output needs a path}"
      shift 2
      ;;
    --slug)
      slug="${2:?--slug needs text}"
      shift 2
      ;;
    --timeout)
      timeout_seconds="${2:?--timeout needs seconds}"
      shift 2
      ;;
    --poll-interval)
      poll_interval="${2:?--poll-interval needs seconds}"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      emit_error "bad_argument" "$1"
      usage
      exit 2
      ;;
  esac
done

if [[ -z "$prompt" ]]; then
  emit_error "missing_prompt" "--prompt is required"
  usage
  exit 2
fi

case "$aspect_ratio" in
  1:1|16:9|9:16|4:3|3:4) ;;
  *)
    emit_error "bad_aspect_ratio" "$aspect_ratio"
    exit 2
    ;;
esac

case "$output_format" in
  png|jpeg) ;;
  *)
    emit_error "bad_output_format" "$output_format"
    exit 2
    ;;
esac

if ! [[ "$timeout_seconds" =~ ^[0-9]+$ ]] || (( timeout_seconds < 1 )); then
  emit_error "bad_timeout" "$timeout_seconds"
  exit 2
fi

if ! [[ "$poll_interval" =~ ^[0-9]+$ ]] || (( poll_interval < 1 )); then
  emit_error "bad_poll_interval" "$poll_interval"
  exit 2
fi

if ! command -v python3 >/dev/null 2>&1; then
  emit_error "missing_python3" "python3 is required to decode GPTsAPI image output"
  exit 2
fi
if ! command -v jq >/dev/null 2>&1; then
  emit_error "missing_jq" "jq is required to build and parse GPTsAPI image requests"
  exit 2
fi

if [[ -z "${GPTSAPI_KEY:-}" ]]; then
  emit_error "missing_gptsapi_key" "Set GPTSAPI_KEY in your environment"
  exit 2
fi
if [[ "${GPTSAPI_KEY}" =~ [[:space:][:cntrl:]] ]] || (( ${#GPTSAPI_KEY} < 8 )); then
  emit_error "bad_gptsapi_key" "GPTSAPI_KEY is missing or malformed"
  exit 2
fi
GPTSAPI_API_HOST="${GPTSAPI_API_HOST:-https://api.gptsapi.net}"
# Per-curl call timeout. Retries are disabled at the curl layer; the Node.js
# caller owns retry policy via process timeout. Keep this strictly under the
# script's poll-loop budget.
GPTSAPI_CURL_TIMEOUT_SECONDS="${GPTSAPI_CURL_TIMEOUT_SECONDS:-30}"
cache_root="${XDG_CACHE_HOME:-${HOME}/.cache}/k2b-gptsapi-image"
mkdir -p "$cache_root"
chmod 700 "$cache_root"

if [[ -z "$output_path" ]]; then
  vault="${K2B_VAULT_PATH:-${K2B_VAULT:-}}"
  if [[ -z "$vault" ]]; then
    vault="$HOME/Projects/K2B-Vault"
  fi
  image_dir="${vault}/Assets/images"
  mkdir -p "$image_dir"
  slug="${slug:-$(slugify "${prompt:0:80}")}"
  if [[ -z "$slug" ]]; then
    slug="gptsapi-image"
  fi
  output_path="${image_dir}/$(date +%Y-%m-%d)_image_${slug}.${output_format}"
else
  mkdir -p "$(dirname "$output_path")"
fi

lock_root="${cache_root}/locks"
mkdir -p "$lock_root"
chmod 700 "$lock_root"
lock_id="$(python3 - <<'PY' "$output_path"
import hashlib
import sys

print(hashlib.sha256(sys.argv[1].encode("utf-8")).hexdigest())
PY
)"
lock_dir="${lock_root}/${lock_id}.lock"
if ! mkdir "$lock_dir" 2>/dev/null; then
  # Stale-lock detection: a lock older than 2x the script's poll budget
  # cannot belong to a live writer. find -mmin requires integer minutes,
  # so round up. For a default 120s timeout this becomes 4 minutes.
  stale_minutes=$(( (timeout_seconds * 2 + 59) / 60 ))
  if (( stale_minutes < 1 )); then
    stale_minutes=1
  fi
  if [[ -d "$lock_dir" ]] && find "$lock_dir" -maxdepth 0 -mmin "+${stale_minutes}" 2>/dev/null | grep -q .; then
    rmdir "$lock_dir" 2>/dev/null || true
    if ! mkdir "$lock_dir" 2>/dev/null; then
      emit_error "image_generation_in_progress" "another generation is already writing this output"
      exit 1
    fi
  else
    emit_error "image_generation_in_progress" "another generation is already writing this output"
    exit 1
  fi
fi
printf '%s\n' "$$" >"${lock_dir}/pid" 2>/dev/null || true
cleanup_lock() {
  rmdir "$lock_dir" 2>/dev/null || rm -rf "$lock_dir" 2>/dev/null || true
}
trap cleanup_lock EXIT

submit_url="${GPTSAPI_API_HOST}/api/v3/openai/gpt-image-2/text-to-image"
submit_body=$(jq -cn \
  --arg prompt "$prompt" \
  --arg aspect_ratio "$aspect_ratio" \
  --arg output_format "$output_format" \
  '{prompt: $prompt, aspect_ratio: $aspect_ratio, output_format: $output_format}')

echo "gptsapi-image: submitting gpt-image-2 aspect=${aspect_ratio} format=${output_format}" >&2
submit_response=$(curl \
  --silent --show-error --fail-with-body \
  --max-time "$GPTSAPI_CURL_TIMEOUT_SECONDS" \
  --retry 0 \
  -X POST \
  -H "Authorization: Bearer ${GPTSAPI_KEY}" \
  -H "Content-Type: application/json" \
  -d "$submit_body" \
  "$submit_url" 2>&1) || {
    emit_error "submit_failed" "$submit_response"
    exit 1
  }

prediction_id=$(jq -r '.data.id // .id // empty' <<<"$submit_response" 2>/dev/null || true)
if [[ -z "$prediction_id" || "$prediction_id" == "null" ]]; then
  emit_error "missing_prediction_id" "$submit_response"
  exit 1
fi

poll_url="${GPTSAPI_API_HOST}/api/v3/predictions/${prediction_id}/result"

start_epoch=$(date +%s)
completed_response=""
poll_attempt=0

while true; do
  now_epoch=$(date +%s)
  elapsed=$((now_epoch - start_epoch))
  if (( elapsed > timeout_seconds )); then
    emit_error "timeout" "prediction ${prediction_id} did not complete within ${timeout_seconds}s"
    exit 1
  fi

  poll_response=$(curl \
    --silent --show-error --fail-with-body \
    --max-time "$GPTSAPI_CURL_TIMEOUT_SECONDS" \
    --retry 2 --retry-delay 3 \
    -H "Authorization: Bearer ${GPTSAPI_KEY}" \
    "$poll_url" 2>&1) || {
      emit_error "poll_failed" "$poll_response"
      exit 1
    }

  status=$(jq -r '.data.status // .status // empty' <<<"$poll_response" 2>/dev/null || true)
  if [[ -z "$status" || "$status" == "null" ]]; then
    status="unknown"
  fi
  echo "gptsapi-image: poll elapsed=${elapsed}s status=${status}" >&2

  case "$status" in
    completed)
      completed_response="$poll_response"
      break
      ;;
    failed|error|canceled|cancelled)
      emit_error "prediction_${status}" "$poll_response"
      exit 1
      ;;
  esac

  poll_attempt=$((poll_attempt + 1))
  # Backoff with jitter: base interval, doubled after first 3 attempts
  # (capped at 4x), plus 0-2s jitter. Keeps fast-fail loops off the API.
  if (( poll_attempt <= 3 )); then
    next_interval=$poll_interval
  elif (( poll_attempt <= 6 )); then
    next_interval=$(( poll_interval * 2 ))
  else
    next_interval=$(( poll_interval * 4 ))
  fi
  jitter=$(( RANDOM % 3 ))
  sleep_for=$(( next_interval + jitter ))
  remaining=$((timeout_seconds - elapsed))
  if (( remaining <= 0 )); then
    emit_error "timeout" "prediction ${prediction_id} did not complete within ${timeout_seconds}s"
    exit 1
  fi
  if (( remaining < sleep_for )); then
    sleep "$remaining"
  else
    sleep "$sleep_for"
  fi
done

image_payload=$(jq -r '.data.outputs[0] // .outputs[0] // empty' <<<"$completed_response" 2>/dev/null || true)
if [[ -z "$image_payload" || "$image_payload" == "null" ]]; then
  emit_error "missing_output" "$completed_response"
  exit 1
fi

cache_root="${XDG_CACHE_HOME:-${HOME}/.cache}/k2b-gptsapi-image"
mkdir -p "$cache_root"
chmod 700 "$cache_root"
tmp_dir="$(mktemp -d "${cache_root}/run.XXXXXX")"
tmp_file="${tmp_dir}/image.${output_format}"
cleanup_run() {
  rm -f "${tmp_file:-}"
  rmdir "${tmp_dir:-}" 2>/dev/null || true
  cleanup_lock
}
trap cleanup_run EXIT

# GPTSAPI's gpt-image-2 returns a URL in outputs[]; the legacy endpoint returned
# base64. Handle both: download URLs, decode base64 inline.
if [[ -n "$image_payload" && "$image_payload" =~ ^https?:// ]]; then
  download_elapsed=$(($(date +%s) - start_epoch))
  download_timeout=$((timeout_seconds - download_elapsed))
  if (( download_timeout <= 0 )); then
    emit_error "timeout" "prediction completed but no time remained to download output"
    exit 1
  fi
  if (( download_timeout > GPTSAPI_CURL_TIMEOUT_SECONDS )); then
    download_timeout="$GPTSAPI_CURL_TIMEOUT_SECONDS"
  fi
  if ! curl --silent --show-error --fail-with-body \
       --connect-timeout 10 --max-time "$download_timeout" --retry 2 --retry-delay 3 --retry-max-time "$download_timeout" \
       -o "$tmp_file" "$image_payload"; then
    emit_error "download_failed" "could not fetch image from output URL"
    exit 1
  fi
elif ! python3 -c '
import base64
import binascii
import sys

payload = sys.stdin.read().strip()
output_path = sys.argv[1]
if payload.startswith("data:"):
    if "," not in payload:
        sys.exit(2)
    payload = payload.split(",", 1)[1]
try:
    data = base64.b64decode(payload, validate=True)
except (binascii.Error, ValueError):
    sys.exit(3)
if not data:
    sys.exit(4)
with open(output_path, "wb") as fh:
    fh.write(data)
' "$tmp_file" <<<"$image_payload"; then
  emit_error "base64_decode_failed" "$completed_response"
  exit 1
fi

# Magic-byte validation applies to both URL-downloaded and base64-decoded files.
if ! python3 -c '
import sys
fmt = sys.argv[1]
with open(sys.argv[2], "rb") as fh:
    data = fh.read()
if len(data) < 100:
    sys.exit(7)
if fmt == "png" and not data.startswith(b"\x89PNG\r\n\x1a\n"):
    sys.exit(5)
if fmt == "png" and not data.endswith(b"IEND\xaeB`\x82"):
    sys.exit(8)
if fmt == "jpeg" and not data.startswith(b"\xff\xd8\xff"):
    sys.exit(6)
if fmt == "jpeg" and not data.endswith(b"\xff\xd9"):
    sys.exit(9)
' "$output_format" "$tmp_file"; then
  emit_error "magic_byte_check_failed" "downloaded file did not match expected ${output_format} signature"
  exit 1
fi

# Commit the image. mv -f is atomic on the same filesystem.
mv -f "$tmp_file" "$output_path"
# Print the saved path BEFORE releasing the lock so a SIGKILL between mv
# and lock-release leaves the lock held (a stale lock is preferable to a
# clobber by a concurrent invocation; see HIGH #4 in 2026-05-06 review).
echo "Saved: ${output_path}"
printf '%s\n' "$output_path"
trap - EXIT
rmdir "${tmp_dir}" 2>/dev/null || true
cleanup_lock
