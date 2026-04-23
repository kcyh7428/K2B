#!/usr/bin/env bash
# MiniMax-VL primitive for K2B.
#
# Mirrors scripts/minimax-json-job.sh in shape: CLI flags + logging + error
# handling. Produces OCR / image-analysis text from a single image + prompt.
#
# Usage:
#   minimax-vlm.sh --image <path> --prompt <text> --job-name <label> [--fallback auto|never|always]
#
# Output:
#   stdout: the extracted text content (or empty string on silent failure in
#           --fallback always mode with both providers failing).
#   stderr: progress / error markers.
#   log:    one line to wiki/context/minimax-jobs.jsonl per call, via
#           log_job_invocation (same contract as minimax-json-job.sh).
#
# Exit codes:
#   0  success (stdout has content)
#   2  usage error
#   3  image file missing
#   4  unsupported MIME type (e.g. GIF)
#   5  VLM call failed, --fallback never refused fallback
#   6  VLM call failed AND Opus fallback also failed
#
# Env:
#   MINIMAX_API_KEY    required in real-run mode. minimax-common.sh sources
#                      ~/.zshrc if not set.
#   MINIMAX_VLM_MOCK   path to a mock response JSON. When set, skips real
#                      API + skips MIME/base64 work. Used by unit tests.
#   MINIMAX_VLM_CLAUDE path to a claude binary override. Used by tests and
#                      for environments where `claude` is not in PATH.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=scripts/minimax-common.sh
source "$SCRIPT_DIR/minimax-common.sh"

# --- Parse flags ---
image=""
prompt=""
job_name=""
fallback="auto"
while [ $# -gt 0 ]; do
  case "$1" in
    --image)    image="${2:?--image needs a path}"; shift 2 ;;
    --prompt)   prompt="${2:?--prompt needs text}"; shift 2 ;;
    --job-name) job_name="${2:?--job-name needs a label}"; shift 2 ;;
    --fallback) fallback="${2:?--fallback needs auto|never|always}"; shift 2 ;;
    -h|--help)  sed -n '1,32p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *)          echo "minimax-vlm: unknown flag: $1" >&2; exit 2 ;;
  esac
done

[ -n "$image" ]    || { echo "minimax-vlm: --image required" >&2; exit 2; }
[ -n "$prompt" ]   || { echo "minimax-vlm: --prompt required" >&2; exit 2; }
[ -n "$job_name" ] || { echo "minimax-vlm: --job-name required" >&2; exit 2; }
[ -f "$image" ]    || { echo "minimax-vlm: image not found: $image" >&2; exit 3; }

case "$fallback" in
  auto|never|always) ;;
  *) echo "minimax-vlm: --fallback must be auto|never|always (got '$fallback')" >&2; exit 2 ;;
esac

# --- MIME sniff ---
mime=$(file -b --mime-type "$image")
case "$mime" in
  image/png|image/jpeg|image/webp) ;;
  image/gif) echo "minimax-vlm: GIF not supported; convert to PNG/JPEG/WebP" >&2; exit 4 ;;
  *) echo "minimax-vlm: unsupported mime $mime" >&2; exit 4 ;;
esac

# --- VLM call ---
call_minimax_vlm() {
  local img="$1" prm="$2"
  if [ -n "${MINIMAX_VLM_MOCK:-}" ]; then
    cat "$MINIMAX_VLM_MOCK"
    VLM_CURL_EXIT=0
    return 0
  fi
  local b64
  b64=$(base64 < "$img" | tr -d '\n')
  local body
  body=$(python3 -c '
import json, sys
prompt, mime, b64 = sys.argv[1], sys.argv[2], sys.argv[3]
print(json.dumps({"prompt": prompt, "image_url": f"data:{mime};base64,{b64}"}))
' "$prm" "$mime" "$b64")
  # --max-time 60 caps the curl call at 60 s so a hung API never blocks the
  # pipeline indefinitely. Capture the curl exit code so the caller can
  # distinguish timeout (28) from HTTP non-2xx. `--fail-with-body` returns
  # a non-zero exit on non-2xx while still letting the response body flow
  # to stdout -- lets downstream JSON parser still try.
  local _out _rc
  _out=$(curl -sS --max-time 60 --fail-with-body -X POST "${MINIMAX_API_HOST}/v1/coding_plan/vlm" \
    -H "Authorization: Bearer ${MINIMAX_API_KEY}" \
    -H "MM-API-Source: K2B" \
    -H "Content-Type: application/json" \
    -d "$body") && _rc=0 || _rc=$?
  VLM_CURL_EXIT=$_rc
  printf '%s' "$_out"
}

# --- Opus fallback ---
# Wrap the claude binary in `timeout 120s` so a hung model / stuck stdio
# cannot freeze the pipeline. 120 s is the outer envelope; curl inside the
# VLM path is capped at 60 s, so both providers fail within ~3 min worst
# case. On BSD / macOS without coreutils, fall back to the `gtimeout` /
# plain-no-timeout path.
call_opus_vision() {
  local img="$1" prm="$2"
  local bin="${MINIMAX_VLM_CLAUDE:-claude}"
  if command -v timeout >/dev/null 2>&1; then
    timeout 120 "$bin" -p --image "$img" "$prm"
  elif command -v gtimeout >/dev/null 2>&1; then
    gtimeout 120 "$bin" -p --image "$img" "$prm"
  else
    # No timeout binary available; log and run without a guard.
    echo "minimax-vlm: neither 'timeout' nor 'gtimeout' available; Opus fallback unguarded" >&2
    "$bin" -p --image "$img" "$prm"
  fi
}

# --- Logging wrapper ---
log_vlm() {
  local parse_status="$1" duration_ms="$2" output_bytes="$3"
  # log_job_invocation args: job, prompt_version, model, input_bytes, output_bytes, parse_status, duration_ms
  local input_bytes
  input_bytes=$(wc -c <"$image" | tr -d ' ')
  log_job_invocation "$job_name" "vlm-v1" "MiniMax-VL" "$input_bytes" "$output_bytes" "$parse_status" "$duration_ms"
}

now_ms() {
  # macOS `date +%s%3N` emits a literal '3N' instead of milliseconds, so
  # always use python3 for a portable epoch-ms clock.
  python3 -c 'import time; print(int(time.time()*1000))'
}

# --- Main flow ---
started_ms=$(now_ms)

response=$(call_minimax_vlm "$image" "$prompt" 2>/dev/null || true)
# Parse once and emit two fields: status and content separated by a NUL
# byte. This lets us distinguish "parse failed" (exit 2 from python) from
# "parse succeeded but status was non-zero" (exit 0). Without that split
# both paths previously produced status=-1 and the log lost the signal.
# set -e + pipefail would terminate the script on a failing pipeline inside
# $(...). Wrap in an if-block so the non-zero exit is captured cleanly.
if parsed=$(printf '%s' "$response" | python3 -c '
import json, sys
raw = sys.stdin.read()
try:
    d = json.loads(raw) if raw.strip() else {}
except Exception:
    sys.exit(2)
print(d.get("base_resp", {}).get("status_code", -1))
print(d.get("content", ""), end="")
' 2>/dev/null); then
  parse_rc=0
else
  parse_rc=$?
  parsed=""
fi
if [ "$parse_rc" -ne 0 ]; then
  # Log a preview of the unparseable response so the cause is debuggable.
  preview=$(printf '%s' "$response" | head -c 200 | tr '\n' ' ')
  echo "minimax-vlm: JSON parse failed. Preview: ${preview}" >&2
  status="-1"
  content=""
else
  status=$(printf '%s' "$parsed" | head -n 1)
  content=$(printf '%s' "$parsed" | tail -n +2)
fi

duration_ms=$(( $(now_ms) - started_ms ))

if [ "$status" = "0" ] && [ -n "$content" ]; then
  log_vlm "ok" "$duration_ms" "$(printf '%s' "$content" | wc -c | tr -d ' ')"
  printf '%s' "$content"
  exit 0
fi

# --- Non-zero path: fallback or exit ---
# Log status distinguishes:
#   curl-timeout-28   curl --max-time fired
#   curl-error-<rc>   other curl failure (DNS, connection refused, http 4xx/5xx)
#   parse-fail        body arrived but JSON parse failed
#   vlm-fail-<code>   valid JSON with non-zero base_resp.status_code
# Ops can grep the log for any of these to triage without re-running the request.
if [ "${VLM_CURL_EXIT:-0}" -eq 28 ]; then
  log_vlm "curl-timeout-28" "$duration_ms" "0"
elif [ "${VLM_CURL_EXIT:-0}" -ne 0 ]; then
  log_vlm "curl-error-${VLM_CURL_EXIT}" "$duration_ms" "0"
elif [ "$parse_rc" -ne 0 ]; then
  log_vlm "parse-fail" "$duration_ms" "0"
else
  log_vlm "vlm-fail-$status" "$duration_ms" "0"
fi

case "$fallback" in
  never)
    echo "minimax-vlm: VLM failed (status=$status), --fallback never so no Opus retry" >&2
    exit 5
    ;;
  auto|always)
    opus_started_ms=$(now_ms)
    opus_out=$(call_opus_vision "$image" "$prompt" 2>/dev/null || true)
    opus_duration=$(( $(now_ms) - opus_started_ms ))
    if [ -n "$opus_out" ]; then
      log_job_invocation "$job_name" "vlm-v1" "Opus-vision" "$(wc -c <"$image" | tr -d ' ')" "$(printf '%s' "$opus_out" | wc -c | tr -d ' ')" "opus-ok" "$opus_duration"
      printf '%s' "$opus_out"
      exit 0
    fi
    log_job_invocation "$job_name" "vlm-v1" "Opus-vision" "$(wc -c <"$image" | tr -d ' ')" "0" "opus-fail" "$opus_duration"
    echo "minimax-vlm: VLM failed (status=$status) and Opus fallback also failed" >&2
    exit 6
    ;;
esac
