#!/usr/bin/env bash
# Generic MiniMax JSON-job wrapper
# Factors the shared plumbing out of minimax-research-extract / minimax-compile / minimax-lint-deep.
# Caller builds a prompt + user input; this wrapper calls MiniMax, strips fences,
# validates JSON strictly, logs the invocation, and prints the validated JSON on stdout.
#
# Usage:
#   minimax-json-job.sh [flags]
#
# Flags:
#   --prompt <path>         System prompt file. Mutually exclusive with --prompt-stdin.
#   --prompt-stdin          Read system prompt from stdin (for dynamic prompts).
#   --input <path>          User content file. Use '-' for stdin, but not both with --prompt-stdin.
#   --model <id>            MiniMax model id (default: MiniMax-M2.7)
#   --max-tokens <N>        Max completion tokens (default: 4000)
#   --temperature <F>       Temperature (default: 0.2)
#   --job-name <label>      Required. Logged in minimax-jobs.jsonl.
#   --prompt-version <ver>  Optional. Logged alongside job-name.
#   --role-name <name>      Optional user-message name field (default: "caller")
#   --system-name <name>    Optional system-message name field (default: "K2B Worker")
#
# Stdin vs flags: if --prompt-stdin is passed, system prompt is read from stdin and
# --input MUST be a file path (not '-'). If --input is '-', --prompt MUST be a file path.
# This avoids ambiguity with a single stdin channel.
#
# Output:
#   stdout: the validated JSON returned by MiniMax (fences stripped)
#   stderr: one-line progress markers ([minimax] calling API..., etc.) + any errors
#
# Exit codes:
#   0   success
#   1   API error, empty response, or invalid JSON
#   2   usage error

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=/dev/null
source "$SCRIPT_DIR/minimax-common.sh"

# --- Defaults ---
MODEL="MiniMax-M2.7"
MAX_TOKENS=4000
TEMPERATURE=0.2
ROLE_NAME="caller"
SYSTEM_NAME="K2B Worker"
PROMPT_VERSION=""
JOB_NAME=""
PROMPT_PATH=""
PROMPT_FROM_STDIN=0
INPUT_PATH=""

# --- Parse flags ---
while [[ $# -gt 0 ]]; do
  case "$1" in
    --prompt)         PROMPT_PATH="${2:?--prompt needs a path}"; shift 2 ;;
    --prompt-stdin)   PROMPT_FROM_STDIN=1; shift ;;
    --input)          INPUT_PATH="${2:?--input needs a path or -}"; shift 2 ;;
    --model)          MODEL="${2:?--model needs a value}"; shift 2 ;;
    --max-tokens)     MAX_TOKENS="${2:?--max-tokens needs a value}"; shift 2 ;;
    --temperature)    TEMPERATURE="${2:?--temperature needs a value}"; shift 2 ;;
    --job-name)       JOB_NAME="${2:?--job-name needs a value}"; shift 2 ;;
    --prompt-version) PROMPT_VERSION="${2:?--prompt-version needs a value}"; shift 2 ;;
    --role-name)      ROLE_NAME="${2:?--role-name needs a value}"; shift 2 ;;
    --system-name)    SYSTEM_NAME="${2:?--system-name needs a value}"; shift 2 ;;
    -h|--help)        sed -n '1,40p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *)                echo "ERROR: unknown flag: $1" >&2; exit 2 ;;
  esac
done

# --- Validate flag combinations ---
if [[ -z "$JOB_NAME" ]]; then
  echo "ERROR: --job-name is required (logged in minimax-jobs.jsonl)" >&2
  exit 2
fi

if [[ -z "$PROMPT_PATH" && $PROMPT_FROM_STDIN -eq 0 ]]; then
  echo "ERROR: must pass --prompt <path> or --prompt-stdin" >&2
  exit 2
fi

if [[ -n "$PROMPT_PATH" && $PROMPT_FROM_STDIN -eq 1 ]]; then
  echo "ERROR: --prompt and --prompt-stdin are mutually exclusive" >&2
  exit 2
fi

if [[ -z "$INPUT_PATH" ]]; then
  echo "ERROR: --input <path> is required (use - for stdin when prompt is from file)" >&2
  exit 2
fi

if [[ $PROMPT_FROM_STDIN -eq 1 && "$INPUT_PATH" == "-" ]]; then
  echo "ERROR: cannot read both --prompt-stdin and --input - in the same call" >&2
  exit 2
fi

# --- Read prompt ---
if [[ $PROMPT_FROM_STDIN -eq 1 ]]; then
  system_prompt=$(cat)
else
  if [[ ! -f "$PROMPT_PATH" ]]; then
    echo "ERROR: prompt file not found: $PROMPT_PATH" >&2
    exit 2
  fi
  system_prompt=$(< "$PROMPT_PATH")
fi

if [[ -z "${system_prompt//[[:space:]]/}" ]]; then
  echo "ERROR: system prompt is empty" >&2
  exit 2
fi

# --- Read input ---
if [[ "$INPUT_PATH" == "-" ]]; then
  user_content=$(cat)
else
  if [[ ! -f "$INPUT_PATH" ]]; then
    echo "ERROR: input file not found: $INPUT_PATH" >&2
    exit 2
  fi
  user_content=$(< "$INPUT_PATH")
fi

input_bytes=${#user_content}
if [[ $input_bytes -eq 0 || -z "${user_content//[[:space:]]/}" ]]; then
  echo "ERROR: input is empty or whitespace-only" >&2
  exit 2
fi

# --- Call MiniMax ---
echo "[minimax] calling ${MODEL} (input ${input_bytes}B, max ${MAX_TOKENS} tokens)..." >&2

request_body=$(jq -n \
  --arg model "$MODEL" \
  --arg system "$system_prompt" \
  --arg user "$user_content" \
  --arg sysname "$SYSTEM_NAME" \
  --arg username "$ROLE_NAME" \
  --argjson max_tokens "$MAX_TOKENS" \
  --argjson temp "$TEMPERATURE" \
  '{
    model: $model,
    messages: [
      { role: "system", name: $sysname, content: $system },
      { role: "user",   name: $username, content: $user }
    ],
    max_completion_tokens: $max_tokens,
    temperature: $temp
  }')

start_ms=$(python3 -c 'import time; print(int(time.time()*1000))')

if ! response=$(mm_api POST /v1/text/chatcompletion_v2 "$request_body"); then
  end_ms=$(python3 -c 'import time; print(int(time.time()*1000))')
  duration_ms=$((end_ms - start_ms))
  log_job_invocation "$JOB_NAME" "$PROMPT_VERSION" "$MODEL" "$input_bytes" 0 "api_error" "$duration_ms"
  echo "[minimax] API call failed after ${duration_ms}ms" >&2
  exit 1
fi

end_ms=$(python3 -c 'import time; print(int(time.time()*1000))')
duration_ms=$((end_ms - start_ms))

echo "[minimax] got response in ${duration_ms}ms, parsing..." >&2

# --- Extract content ---
content=$(echo "$response" | jq -r '.choices[0].message.content // empty')

if [[ -z "$content" ]]; then
  log_job_invocation "$JOB_NAME" "$PROMPT_VERSION" "$MODEL" "$input_bytes" 0 "empty_response" "$duration_ms"
  echo "[minimax] empty response content" >&2
  echo "$response" >&2
  exit 1
fi

# --- Strip ```json fences if present ---
cleaned=$(echo "$content" | sed 's/^```json//; s/^```//; s/```$//')
parse_status="ok"
if [[ "$cleaned" != "$content" ]]; then
  parse_status="fence"
fi

# --- Strict JSON validation (jq -e, not bare jq .) ---
echo "[minimax] validating JSON (${#cleaned}B)..." >&2
jq_err_file=$(mktemp -t k2b-minimax-json-err.XXXXXX)
trap 'rm -f "$jq_err_file"' EXIT

if ! echo "$cleaned" | jq -e . >/dev/null 2>"$jq_err_file"; then
  log_job_invocation "$JOB_NAME" "$PROMPT_VERSION" "$MODEL" "$input_bytes" "${#cleaned}" "invalid" "$duration_ms"
  jq_err=$(cat "$jq_err_file" 2>/dev/null || true)
  echo "[minimax] invalid JSON from MiniMax" >&2
  jq -n \
    --arg err "Invalid JSON from MiniMax" \
    --arg jq_error "$jq_err" \
    --arg raw "$cleaned" \
    '{error: $err, jq_error: $jq_error, raw_response: $raw}' >&2
  exit 1
fi

# --- Success: log and emit ---
output_bytes=${#cleaned}
log_job_invocation "$JOB_NAME" "$PROMPT_VERSION" "$MODEL" "$input_bytes" "$output_bytes" "$parse_status" "$duration_ms"
echo "[minimax] ok (${parse_status}, ${output_bytes}B output)" >&2
printf '%s\n' "$cleaned"
