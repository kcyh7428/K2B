#!/usr/bin/env bash
# MiniMax-powered contradiction detection for /lint deep
# Reads wiki pages within the same domain, finds contradictions
#
# Usage: minimax-lint-deep.sh [domain]
# If domain is omitted, scans all wiki pages
# Output: JSON to stdout with contradiction pairs

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "$SCRIPT_DIR/minimax-common.sh"

MODEL="MiniMax-M2.7"
MAX_TOKENS=4000
TEMPERATURE=0.1

DOMAIN="${1:-all}"

# --- Gather wiki pages ---

pages_content=""
page_count=0

if [[ "$DOMAIN" == "all" ]]; then
  # Read all wiki pages (excluding indexes and context/)
  while IFS= read -r -d '' f; do
    [[ "$(basename "$f")" == "index.md" ]] && continue
    [[ "$f" == *"/wiki/log.md" ]] && continue
    pages_content+="
--- $(basename "$f") ---
$(head -80 "$f")
"
    page_count=$((page_count + 1))
  done < <(find "$K2B_VAULT/wiki" -name "*.md" -not -path "*/context/*" -print0 2>/dev/null)
else
  # Filter by domain frontmatter
  while IFS= read -r -d '' f; do
    [[ "$(basename "$f")" == "index.md" ]] && continue
    if head -20 "$f" | grep -q "domain:.*$DOMAIN" 2>/dev/null; then
      pages_content+="
--- $(basename "$f") ---
$(head -80 "$f")
"
      page_count=$((page_count + 1))
    fi
  done < <(find "$K2B_VAULT/wiki" -name "*.md" -print0 2>/dev/null)
fi

if [[ $page_count -lt 2 ]]; then
  echo '{"contradictions": [], "pages_scanned": '"$page_count"', "note": "Need at least 2 pages to check for contradictions"}'
  exit 0
fi

# --- Build prompt ---

system_prompt='You are K2B'\''s contradiction detector. Read the wiki pages below and find factual claims that contradict each other across different pages.

Return ONLY valid JSON. No markdown, no explanation, no code fences.

JSON schema:
{
  "contradictions": [
    {
      "page_a": "filename_a.md",
      "claim_a": "What page A claims",
      "page_b": "filename_b.md",
      "claim_b": "What page B claims (contradicting A)",
      "severity": "high | medium | low",
      "suggestion": "Which claim is likely correct and why"
    }
  ],
  "pages_scanned": number,
  "notes": "Any general observations about consistency"
}

Rules:
- Only flag genuine contradictions (conflicting facts), not differences in perspective or emphasis
- Severity: high = factually incompatible, medium = potentially conflicting, low = slight inconsistency
- If no contradictions found, return empty array (this is good!)
- Do not flag stale information as contradictions -- note it in "notes" instead'

user_content="WIKI PAGES (domain: $DOMAIN, count: $page_count):
$pages_content"

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
      { role: "system", name: "K2B Lint", content: $system },
      { role: "user", name: "linter", content: $user }
    ],
    max_completion_tokens: $max_tokens,
    temperature: $temp
  }')

response=$(mm_api POST /v1/text/chatcompletion_v2 "$request_body") || {
  echo '{"error": "MiniMax API call failed"}' >&2
  exit 1
}

content=$(echo "$response" | jq -r '.choices[0].message.content // empty')

if [[ -z "$content" ]]; then
  echo '{"error": "Empty response from MiniMax"}' >&2
  exit 1
fi

# Strip markdown code fences if present
content=$(echo "$content" | sed 's/^```json//; s/^```//; s/```$//')

# Validate JSON
if ! echo "$content" | jq . >/dev/null 2>&1; then
  echo '{"error": "Invalid JSON from MiniMax", "raw_response": '"$(echo "$content" | jq -Rs .)"'}' >&2
  exit 1
fi

echo "$content"
