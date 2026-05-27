#!/usr/bin/env bash
# Extract text from an image via GPTsAPI vision.
#
# Usage: gptsapi-vlm.sh <image-path> [--prompt "custom prompt"] [--model gpt-4o]
#        gptsapi-vlm.sh --image <image-path> --prompt "custom prompt"
#
# stdout: extracted text (UTF-8, may include CJK / mixed scripts)
# stderr: progress + error messages
# exit:
#   0 -- success, non-empty extracted text on stdout
#   1 -- API call failed (network, auth, timeout, model error, daily limit)
#   2 -- argv / env error
#   3 -- success on the wire but the model returned empty / refused
#
# Replaces the legacy MiniMax OCR wrapper after subscription expiry made the
# endpoint return status_code 2049. Spec:
# wiki/concepts/Shipped/feature_vlm-gptsapi-migration.md
set -euo pipefail
umask 077

export PATH="/opt/homebrew/bin:/usr/local/bin:${PATH:-/usr/bin:/bin}"

DEFAULT_PROMPT="Extract all text visible in this image. Preserve layout where helpful. Return only the extracted text, no commentary."

usage() {
  sed -n '1,24p' "$0" | sed 's/^# \{0,1\}//' >&2
}

err() {
  echo "gptsapi-vlm: $*" >&2
}

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || {
    err "$1 is required"
    exit 2
  }
}

file_size_bytes() {
  if stat -f%z "$1" >/dev/null 2>&1; then
    stat -f%z "$1"
  else
    stat -c%s "$1"
  fi
}

tmp_files=()
cleanup() {
  local f
  for f in "${tmp_files[@]:-}"; do
    if [ -n "$f" ]; then
      rm -f "$f"
    fi
  done
  true
}
trap cleanup EXIT

new_tmp() {
  local f
  f="$(mktemp "${TMPDIR:-/tmp}/k2b-gptsapi-vlm.XXXXXX")"
  tmp_files+=("$f")
  printf '%s' "$f"
}

sniff_mime() {
  file -b --mime-type "$1"
}

convert_image() {
  local src="$1" format="$2" suffix="$3" dst
  dst="$(new_tmp).${suffix}"
  tmp_files+=("$dst")

  if command -v magick >/dev/null 2>&1; then
    magick "${src}[0]" -auto-orient "$dst" >/dev/null
  elif command -v convert >/dev/null 2>&1; then
    convert "${src}[0]" -auto-orient "$dst" >/dev/null
  elif command -v sips >/dev/null 2>&1; then
    sips -s format "$format" "$src" --out "$dst" >/dev/null
  else
    err "cannot convert $(sniff_mime "$src"); install ImageMagick or use macOS sips"
    exit 2
  fi

  [ -s "$dst" ] || {
    err "image conversion produced empty output"
    exit 2
  }
  printf '%s' "$dst"
}

resize_if_large() {
  local src="$1" max_bytes="$2" size dst
  size="$(file_size_bytes "$src")"
  if [ "$size" -le "$max_bytes" ]; then
    printf '%s' "$src"
    return
  fi

  dst="$(new_tmp).jpg"
  tmp_files+=("$dst")
  if command -v magick >/dev/null 2>&1; then
    magick "$src" -auto-orient -resize '2048x2048>' -quality 88 "$dst" >/dev/null
  elif command -v convert >/dev/null 2>&1; then
    convert "$src" -auto-orient -resize '2048x2048>' -quality 88 "$dst" >/dev/null
  elif command -v sips >/dev/null 2>&1; then
    sips -Z 2048 -s format jpeg "$src" --out "$dst" >/dev/null
  else
    err "image is ${size} bytes, above ${max_bytes}; install ImageMagick or use macOS sips to resize"
    exit 2
  fi
  err "resized large image from ${size} bytes to $(file_size_bytes "$dst") bytes"
  printf '%s' "$dst"
}

increment_daily_counter() {
  local limit="$1"
  if ! [[ "$limit" =~ ^[0-9]+$ ]]; then
    err "GPTSAPI_VLM_DAILY_LIMIT must be an integer"
    exit 2
  fi
  if [ "$limit" -eq 0 ]; then
    return
  fi

  local state_dir today count_file lock_dir count next waited stale_seconds lock_mtime now
  state_dir="${GPTSAPI_VLM_STATE_DIR:-${XDG_STATE_HOME:-${HOME}/.local/state}/k2b}"
  mkdir -p "$state_dir"
  today="$(date +%Y-%m-%d)"
  count_file="$state_dir/gptsapi-vlm-${today}.count"
  lock_dir="$state_dir/gptsapi-vlm-count.lock"
  stale_seconds="${GPTSAPI_VLM_LOCK_STALE_SECONDS:-300}"
  if ! [[ "$stale_seconds" =~ ^[0-9]+$ ]]; then
    stale_seconds=300
  fi
  waited=0
  while ! mkdir "$lock_dir" 2>/dev/null; do
    if [ -d "$lock_dir" ]; then
      lock_mtime="$(stat -f %m "$lock_dir" 2>/dev/null || stat -c %Y "$lock_dir" 2>/dev/null || printf '0')"
      now="$(date +%s)"
      if [[ "$lock_mtime" =~ ^[0-9]+$ ]] && [ $((now - lock_mtime)) -ge "$stale_seconds" ]; then
        if rmdir "$lock_dir" 2>/dev/null; then
          err "reclaimed stale daily counter lock"
          continue
        fi
      fi
    fi
    if [ "$waited" -ge 100 ]; then
      err "could not acquire daily counter lock"
      exit 1
    fi
    sleep 0.1
    waited=$((waited + 1))
  done

  count=0
  if [ -f "$count_file" ]; then
    count="$(cat "$count_file" 2>/dev/null || printf '0')"
  fi
  if ! [[ "$count" =~ ^[0-9]+$ ]]; then
    count=0
  fi
  next=$((count + 1))
  if [ "$next" -gt "$limit" ]; then
    rmdir "$lock_dir"
    err "daily call limit exceeded (${next}/${limit}); set GPTSAPI_VLM_DAILY_LIMIT=0 to disable"
    exit 1
  fi
  printf '%s\n' "$next" > "${count_file}.tmp"
  mv "${count_file}.tmp" "$count_file"
  sync "$count_file" 2>/dev/null || sync
  rmdir "$lock_dir"
  err "daily call count ${next}/${limit}"
}

parse_content() {
  python3 - "$1" <<'PY'
import json
import re
import sys

raw = open(sys.argv[1], encoding="utf-8").read()
try:
    data = json.loads(raw)
except json.JSONDecodeError as exc:
    print(f"gptsapi-vlm: non-JSON response: {exc}", file=sys.stderr)
    sys.exit(1)

if isinstance(data, dict) and data.get("error"):
    print(f"gptsapi-vlm: API error: {data.get('error')}", file=sys.stderr)
    sys.exit(1)

message = (((data.get("choices") or [{}])[0]).get("message") or {})
content = message.get("content", "")
if isinstance(content, list):
    parts = []
    for item in content:
        if isinstance(item, dict):
            text = item.get("text") or item.get("content")
            if text:
                parts.append(str(text))
        elif item:
            parts.append(str(item))
    content = "\n".join(parts)
elif content is None:
    content = ""
else:
    content = str(content)

text = content.strip()
if re.match(r"^```[A-Za-z0-9_-]*\s*\n", text) and re.search(r"\n```\s*$", text):
    stripped = re.sub(r"^```[A-Za-z0-9_-]*\s*\n", "", text)
    stripped = re.sub(r"\n```\s*$", "", stripped).strip()
    if stripped:
        text = stripped
if not text:
    print("gptsapi-vlm: model returned empty extracted text", file=sys.stderr)
    sys.exit(3)

low = text.lower()
refusals = (
    "i cannot extract",
    "i can't extract",
    "cannot extract text",
    "can't extract text",
    "unable to extract",
)
if any(pat in low for pat in refusals):
    print(f"gptsapi-vlm: model refusal/empty OCR response: {text[:240]}", file=sys.stderr)
    sys.exit(3)

print(text)
PY
}

image=""
prompt="$DEFAULT_PROMPT"
model="${GPTSAPI_VLM_MODEL:-gpt-4o-mini}"
job_name="gptsapi-vlm"
timeout_seconds="${GPTSAPI_VLM_TIMEOUT_SECONDS:-60}"
max_tokens="${GPTSAPI_VLM_MAX_TOKENS:-2000}"
max_image_bytes="${GPTSAPI_VLM_MAX_IMAGE_BYTES:-14000000}"
daily_limit="${GPTSAPI_VLM_DAILY_LIMIT:-50}"
retry_count="${GPTSAPI_VLM_RETRIES:-2}"
retry_delay_seconds="${GPTSAPI_VLM_RETRY_DELAY_SECONDS:-2}"
retry_delay_max_seconds="${GPTSAPI_VLM_RETRY_DELAY_MAX_SECONDS:-10}"
min_attempt_seconds="${GPTSAPI_VLM_MIN_ATTEMPT_SECONDS:-15}"

while [ "$#" -gt 0 ]; do
  case "$1" in
    --image)
      image="${2:?--image needs a path}"
      shift 2
      ;;
    --prompt)
      prompt="${2:?--prompt needs text}"
      shift 2
      ;;
    --model)
      model="${2:?--model needs a value}"
      shift 2
      ;;
    --job-name)
      job_name="${2:?--job-name needs a label}"
      shift 2
      ;;
    --fallback)
      # Compatibility with the deleted legacy VLM caller shape. This
      # wrapper never falls back to MiniMax.
      case "${2:-}" in
        auto|never|always) shift 2 ;;
        *) err "--fallback must be auto|never|always"; exit 2 ;;
      esac
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    --*)
      err "unknown flag: $1"
      usage
      exit 2
      ;;
    *)
      if [ -n "$image" ]; then
        err "unexpected positional argument: $1"
        exit 2
      fi
      image="$1"
      shift
      ;;
  esac
done

[ -n "$image" ] || { err "image path required"; usage; exit 2; }
[ -f "$image" ] || { err "image not found: $image"; exit 2; }
[ -n "$prompt" ] || { err "prompt required"; exit 2; }
[ -n "$model" ] || { err "model required"; exit 2; }

if [[ -z "${GPTSAPI_KEY:-}" ]]; then
  err "GPTSAPI_KEY is not set in the environment inherited by ${job_name}"
  exit 2
fi
if ! [[ "$timeout_seconds" =~ ^[0-9]+$ ]] || [ "$timeout_seconds" -lt 1 ]; then
  err "GPTSAPI_VLM_TIMEOUT_SECONDS must be a positive integer"
  exit 2
fi
if ! [[ "$max_tokens" =~ ^[0-9]+$ ]] || [ "$max_tokens" -lt 1 ]; then
  err "GPTSAPI_VLM_MAX_TOKENS must be a positive integer"
  exit 2
fi
if ! [[ "$max_image_bytes" =~ ^[0-9]+$ ]] || [ "$max_image_bytes" -lt 1 ]; then
  err "GPTSAPI_VLM_MAX_IMAGE_BYTES must be a positive integer"
  exit 2
fi
if ! [[ "$retry_count" =~ ^[0-9]+$ ]]; then
  err "GPTSAPI_VLM_RETRIES must be a non-negative integer"
  exit 2
fi
if ! [[ "$retry_delay_seconds" =~ ^[0-9]+$ ]] || { [ "$retry_count" -gt 0 ] && [ "$retry_delay_seconds" -lt 1 ]; }; then
  err "GPTSAPI_VLM_RETRY_DELAY_SECONDS must be a positive integer when retries are enabled"
  exit 2
fi
if ! [[ "$retry_delay_max_seconds" =~ ^[0-9]+$ ]] || [ "$retry_delay_max_seconds" -lt 1 ]; then
  err "GPTSAPI_VLM_RETRY_DELAY_MAX_SECONDS must be a positive integer"
  exit 2
fi
if ! [[ "$min_attempt_seconds" =~ ^[0-9]+$ ]] || [ "$min_attempt_seconds" -lt 5 ]; then
  err "GPTSAPI_VLM_MIN_ATTEMPT_SECONDS must be an integer >= 5"
  exit 2
fi

need_cmd python3
need_cmd file
need_cmd curl

work_image="$image"
mime="$(sniff_mime "$work_image")"
case "$mime" in
  image/png|image/jpeg|image/webp) ;;
  image/gif)
    err "converting GIF first frame to PNG"
    work_image="$(convert_image "$work_image" png png)"
    mime="image/png"
    ;;
  image/heic|image/heif)
    err "converting HEIC/HEIF to JPEG"
    work_image="$(convert_image "$work_image" jpeg jpg)"
    mime="image/jpeg"
    ;;
  *)
    err "unsupported image MIME type: $mime"
    exit 2
    ;;
esac

work_image="$(resize_if_large "$work_image" "$max_image_bytes")"
mime="$(sniff_mime "$work_image")"
case "$mime" in
  image/png|image/jpeg|image/webp) ;;
  *)
    err "converted image has unsupported MIME type: $mime"
    exit 2
    ;;
esac

increment_daily_counter "$daily_limit"

if [ -n "${GPTSAPI_VLM_MOCK:-}" ]; then
  parse_content "$GPTSAPI_VLM_MOCK"
  exit $?
fi

api_host="${GPTSAPI_API_HOST:-https://api.gptsapi.net}"
body_file="$(new_tmp).json"
tmp_files+=("$body_file")
python3 - "$work_image" "$mime" "$prompt" "$model" "$max_tokens" "$body_file" <<'PY'
import base64
import json
import sys

image_path, mime, prompt, model, max_tokens, body_file = sys.argv[1:]
with open(image_path, "rb") as fh:
    b64 = base64.b64encode(fh.read()).decode("ascii")
payload = {
    "model": model,
    "messages": [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
            ],
        }
    ],
    "max_tokens": int(max_tokens),
    "temperature": 0,
}
with open(body_file, "w", encoding="utf-8") as out:
    json.dump(payload, out, ensure_ascii=False, separators=(",", ":"))
PY

response_file="$(new_tmp).json"
tmp_files+=("$response_file")
err "calling GPTsAPI vision model=${model} image_mime=${mime}"
attempt=1
max_attempts=$((retry_count + 1))
deadline_epoch=$(($(date +%s) + timeout_seconds))
while :; do
  now_epoch=$(date +%s)
  remaining_seconds=$((deadline_epoch - now_epoch))
  attempt_floor="$min_attempt_seconds"
  if [ "$timeout_seconds" -lt "$attempt_floor" ]; then
    attempt_floor="$timeout_seconds"
  fi
  if [ "$remaining_seconds" -lt "$attempt_floor" ]; then
    err "GPTsAPI VLM overall timeout exceeded (${timeout_seconds}s)"
    exit 1
  fi
  curl_timeout="$remaining_seconds"
  if [ "$curl_timeout" -gt 30 ]; then
    curl_timeout=30
  fi
  if [ "$curl_timeout" -lt "$attempt_floor" ]; then
    curl_timeout="$attempt_floor"
  fi
  set +e
  curl \
    --silent --show-error --fail-with-body \
    --http1.1 \
    --max-time "$curl_timeout" \
    -X POST "${api_host}/v1/chat/completions" \
    -H "Authorization: Bearer ${GPTSAPI_KEY}" \
    -H "Content-Type: application/json" \
    --data-binary "@${body_file}" \
    > "$response_file"
  curl_rc=$?
  set -e
  if [ "$curl_rc" -eq 0 ]; then
    break
  fi
  case "$curl_rc" in
    5|6|7|18|28|35|52|55|56)
      if [ "$attempt" -lt "$max_attempts" ]; then
        err "GPTsAPI VLM HTTP call failed with curl rc=${curl_rc}; retrying attempt $((attempt + 1))/${max_attempts}"
        if [ "$retry_delay_seconds" -gt 0 ]; then
          now_epoch=$(date +%s)
          remaining_seconds=$((deadline_epoch - now_epoch))
          if [ "$remaining_seconds" -le 1 ]; then
            err "GPTsAPI VLM overall timeout exceeded before retry"
            exit 1
          fi
          sleep_seconds=$((retry_delay_seconds * attempt))
          if [ "$retry_delay_max_seconds" -gt 0 ] && [ "$sleep_seconds" -gt "$retry_delay_max_seconds" ]; then
            sleep_seconds="$retry_delay_max_seconds"
          fi
          if [ "$sleep_seconds" -ge "$remaining_seconds" ]; then
            sleep_seconds=$((remaining_seconds - 1))
          fi
          if [ "$sleep_seconds" -gt 0 ]; then
            sleep "$sleep_seconds"
          fi
        fi
        attempt=$((attempt + 1))
        continue
      fi
      ;;
  esac
  err "GPTsAPI VLM HTTP call failed (curl rc=${curl_rc})"
  exit 1
done

parse_content "$response_file"
