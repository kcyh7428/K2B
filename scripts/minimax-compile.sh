#!/usr/bin/env bash
# MiniMax-powered knowledge compilation
# Reads a raw source + wiki context, returns structured JSON for Opus to apply
#
# Usage: minimax-compile.sh <raw-source-path>
# Output: JSON to stdout with pages_to_update, pages_to_create, content_seeds

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "$SCRIPT_DIR/minimax-common.sh"

MODEL="MiniMax-M2.7"
MAX_TOKENS=8000
TEMPERATURE=0.2

# --- Args ---
RAW_SOURCE="${1:?Usage: minimax-compile.sh <raw-source-path>}"

if [[ ! -f "$RAW_SOURCE" ]]; then
  echo '{"error": "Raw source file not found: '"$RAW_SOURCE"'"}' >&2
  exit 1
fi

# --- Build context ---

# Read the raw source
raw_content=$(cat "$RAW_SOURCE")
raw_filename=$(basename "$RAW_SOURCE")
today=$(date +%Y-%m-%d)

# Read wiki master index
wiki_index=""
if [[ -f "$K2B_VAULT/wiki/index.md" ]]; then
  wiki_index=$(cat "$K2B_VAULT/wiki/index.md")
fi

# Read relevant subfolder indexes (people, projects, work, concepts, insights)
# Skip context/ and content-pipeline/ to save tokens -- less likely to need updates
subfolder_indexes=""
for folder in people projects work concepts insights reference; do
  idx="$K2B_VAULT/wiki/$folder/index.md"
  if [[ -f "$idx" ]]; then
    subfolder_indexes+="
--- wiki/$folder/index.md ---
$(cat "$idx")
"
  fi
done

# --- Build prompt ---

system_prompt='You are K2B'\''s knowledge compiler. Your job is to read a raw source capture and determine which wiki pages should be updated or created.

IMPORTANT: Return ONLY valid JSON. No markdown, no explanation, no code fences. Just the JSON object.

JSON schema:
{
  "source_title": "string -- title of the raw source",
  "summary": "string -- one-line summary of what will change",
  "pages_to_update": [
    {
      "path": "wiki/<subfolder>/<filename>.md",
      "section": "## Section Name",
      "content": "### YYYY-MM-DD -- [Source Title]\n- bullet points of new info\n- with [[wikilinks]] to related pages"
    }
  ],
  "pages_to_create": [
    {
      "path": "wiki/<subfolder>/<filename>.md",
      "frontmatter": "---\ntags: [...]\ndate: YYYY-MM-DD\ntype: ...\norigin: k2b-extract\nup: \"[[MOC_...]]\"\ncompiled-from: \"[[raw-source-filename]]\"\n---",
      "content": "# Page Title\n\nContent with [[wikilinks]]...",
      "index_entry": "| [[filename]] | One-line summary | YYYY-MM-DD |"
    }
  ],
  "content_seeds": [
    {
      "title": "string -- content idea title",
      "angle": "string -- brief angle description",
      "origin": "k2b-extract"
    }
  ]
}

Rules:
- Only create pages for entities/concepts with SUBSTANTIAL mention (not passing references)
- For updates: APPEND under dated headers, never overwrite existing content
- Use [[filename_without_extension]] for all wikilinks
- Person pages: person_Firstname-Lastname.md in wiki/people/
- Project pages: project_slug.md in wiki/projects/
- Concept pages: concept_slug.md in wiki/concepts/
- Work pages: work_slug.md in wiki/work/
- Insight pages: insight_slug.md in wiki/insights/
- Reference pages: YYYY-MM-DD_source_slug.md in wiki/reference/
- Check the indexes to see if a page already exists before creating a new one
- Minimum 2 wikilinks per new page
- No em dashes -- use double hyphens (--)
- Content seeds are ideas Keith could write about based on this source (only if genuinely interesting)
- If the source has nothing worth compiling (trivial content), return empty arrays
- Today'\''s date: '"$today"'
- Raw source filename: '"$raw_filename"''

user_content="RAW SOURCE:
$raw_content

WIKI MASTER INDEX:
$wiki_index

SUBFOLDER INDEXES:
$subfolder_indexes"

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
      { role: "system", name: "K2B Compiler", content: $system },
      { role: "user", name: "compiler", content: $user }
    ],
    max_completion_tokens: $max_tokens,
    temperature: $temp
  }')

response=$(mm_api POST /v1/text/chatcompletion_v2 "$request_body") || {
  echo '{"error": "MiniMax API call failed"}' >&2
  exit 1
}

# Extract the content from the response
content=$(echo "$response" | jq -r '.choices[0].message.content // empty')

if [[ -z "$content" ]]; then
  echo '{"error": "Empty response from MiniMax"}' >&2
  echo "$response" >&2
  exit 1
fi

# Strip markdown code fences if MiniMax wraps the JSON
content=$(echo "$content" | sed 's/^```json//; s/^```//; s/```$//')

# Validate it's valid JSON
if ! echo "$content" | jq . >/dev/null 2>&1; then
  echo '{"error": "Invalid JSON from MiniMax", "raw_response": '"$(echo "$content" | jq -Rs .)"'}' >&2
  exit 1
fi

# Output the parsed JSON
echo "$content"
