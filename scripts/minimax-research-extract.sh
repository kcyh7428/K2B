#!/usr/bin/env bash
# MiniMax-powered research extraction
# Reads a fetched research source (URL content, transcript, README, etc.)
# and returns a compressed, citation-backed JSON digest for Opus to reason over.
#
# Opus stays responsible for: fetching the source, deciding K2B applicability,
# writing the final raw/research/ note. MiniMax only does the extraction.
#
# Usage: minimax-research-extract.sh <content-file> <source-url> [source-title]
# Output: JSON to stdout with tldr, key_claims[], entities[], methodology_notes[], open_questions[]

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "$SCRIPT_DIR/minimax-common.sh"

MODEL="MiniMax-M2.7"
MAX_TOKENS=4000
TEMPERATURE=0.1
PROMPT_VERSION="v3"
JOB_NAME="research-extract"

# --- Args ---
CONTENT_FILE="${1:?Usage: minimax-research-extract.sh <content-file> <source-url> [source-title]}"
SOURCE_URL="${2:?Usage: minimax-research-extract.sh <content-file> <source-url> [source-title]}"
SOURCE_TITLE="${3:-}"

if [[ ! -f "$CONTENT_FILE" ]]; then
  echo '{"error": "Content file not found: '"$CONTENT_FILE"'"}' >&2
  exit 1
fi

# --- Load system prompt from separate file ---
PROMPT_FILE="$SCRIPT_DIR/research-extract-prompt.md"
if [[ ! -f "$PROMPT_FILE" ]]; then
  echo '{"error": "Prompt file not found: '"$PROMPT_FILE"'"}' >&2
  exit 1
fi
system_prompt=$(< "$PROMPT_FILE")

# --- Build user content ---
source_content=$(< "$CONTENT_FILE")
input_bytes=${#source_content}

# Guard against empty/whitespace-only content -- no point calling MiniMax for nothing.
if [[ $input_bytes -eq 0 || -z "${source_content//[[:space:]]/}" ]]; then
  echo '{"error": "Content file is empty or whitespace-only: '"$CONTENT_FILE"'"}' >&2
  exit 1
fi

user_content="SOURCE URL: ${SOURCE_URL}
SOURCE TITLE: ${SOURCE_TITLE:-(unknown)}

SOURCE CONTENT:
${source_content}"

# --- Call MiniMax API ---
request_body=$(jq -n \
  --arg model "$MODEL" \
  --arg system "$system_prompt" \
  --arg user "$user_content" \
  --argjson max_tokens "$MAX_TOKENS" \
  --argjson temp "$TEMPERATURE" \
  '{
    model: $model,
    messages: [
      { role: "system", name: "K2B Research Extractor", content: $system },
      { role: "user", name: "extractor", content: $user }
    ],
    max_completion_tokens: $max_tokens,
    temperature: $temp
  }')

start_ms=$(python3 -c 'import time; print(int(time.time()*1000))')
response=$(mm_api POST /v1/text/chatcompletion_v2 "$request_body") || {
  parse_status="api_error"
  end_ms=$(python3 -c 'import time; print(int(time.time()*1000))')
  duration_ms=$((end_ms - start_ms))
  log_job_invocation "$JOB_NAME" "$PROMPT_VERSION" "$MODEL" "$input_bytes" 0 "$parse_status" "$duration_ms"
  echo '{"error": "MiniMax API call failed"}' >&2
  exit 1
}
end_ms=$(python3 -c 'import time; print(int(time.time()*1000))')
duration_ms=$((end_ms - start_ms))

# Extract the content from the response
content=$(echo "$response" | jq -r '.choices[0].message.content // empty')

if [[ -z "$content" ]]; then
  log_job_invocation "$JOB_NAME" "$PROMPT_VERSION" "$MODEL" "$input_bytes" 0 "empty_response" "$duration_ms"
  echo '{"error": "Empty response from MiniMax"}' >&2
  echo "$response" >&2
  exit 1
fi

# Strip markdown code fences if MiniMax wraps the JSON
cleaned=$(echo "$content" | sed 's/^```json//; s/^```//; s/```$//')
fence_stripped=""
if [[ "$cleaned" != "$content" ]]; then
  fence_stripped="fence"
fi

# Validate it's valid JSON (use jq -e . for strict validation -- bare `jq .` exits 0 on parse errors)
if ! echo "$cleaned" | jq -e . >/dev/null 2>/tmp/k2b-minimax-json-err.$$; then
  log_job_invocation "$JOB_NAME" "$PROMPT_VERSION" "$MODEL" "$input_bytes" "${#cleaned}" "invalid" "$duration_ms"
  jq_err=$(cat /tmp/k2b-minimax-json-err.$$ 2>/dev/null || true)
  rm -f /tmp/k2b-minimax-json-err.$$
  echo '{"error": "Invalid JSON from MiniMax", "jq_error": '"$(echo "$jq_err" | jq -Rs .)"', "raw_response": '"$(echo "$cleaned" | jq -Rs .)"'}' >&2
  exit 1
fi
rm -f /tmp/k2b-minimax-json-err.$$

# Success: log and emit
output_bytes=${#cleaned}
log_job_invocation "$JOB_NAME" "$PROMPT_VERSION" "$MODEL" "$input_bytes" "$output_bytes" "${fence_stripped:-ok}" "$duration_ms"
echo "$cleaned"
