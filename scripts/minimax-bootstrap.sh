#!/usr/bin/env bash
# One-time wiki bootstrap: cross-link enrichment pass
# Reads all wiki pages in 3 batches, asks MiniMax to identify missing connections
#
# Usage: minimax-bootstrap.sh
# Output: JSON plan to stdout (pipe to file for later application)

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "$SCRIPT_DIR/minimax-common.sh"

MODEL="MiniMax-M2.7"
MAX_TOKENS=16000
TEMPERATURE=0.2

log() { echo "[bootstrap] $*" >&2; }

# --- Read wiki pages by subfolder ---
read_pages() {
  local folder="$1"
  local max_lines="${2:-100}"  # Truncate long pages to save tokens
  local content=""
  for f in "$K2B_VAULT/wiki/$folder"/*.md; do
    [[ ! -f "$f" ]] && continue
    local base=$(basename "$f")
    [[ "$base" == "index.md" ]] && continue
    content+="
--- $base ---
$(head -"$max_lines" "$f")
"
  done
  echo "$content"
}

# --- Common system prompt ---
SYSTEM_BASE='You are K2B'\''s wiki bootstrapper. Your job is to analyze existing wiki pages and identify missing cross-links, concept pages that should be created, and connections between entities.

Return ONLY valid JSON. No markdown, no explanation, no code fences.

Rules:
- Wikilinks use [[filename_without_extension]] format
- Person pages: person_Firstname-Lastname.md
- Project pages: project_slug.md
- Concept pages: concept_slug.md
- Work pages: work_slug.md
- Insight pages: insight_slug.md
- Only suggest links where there is a genuine semantic connection (mentioned, discussed, involved)
- Do NOT suggest links just because two pages are in the same folder
- No em dashes -- use double hyphens (--)
- Today: '"$(date +%Y-%m-%d)"''

# ============================================================
# BATCH 1: Entity mapping (people + projects + work)
# ============================================================
log "Batch 1: Reading people, projects, work..."
people_content=$(read_pages "people" 60)
projects_content=$(read_pages "projects" 80)
work_content=$(read_pages "work" 60)

batch1_prompt='Analyze these wiki pages and identify missing cross-links between people, projects, and work items.

JSON schema:
{
  "entity_map": {
    "person_Name": ["project_slug", "work_slug", ...],
    ...
  },
  "wikilinks_to_add": [
    {
      "page": "filename.md",
      "section": "## Related Notes",
      "links": ["[[target1]]", "[[target2]]"]
    }
  ],
  "missing_person_stubs": [
    {
      "name": "Firstname Lastname",
      "mentioned_in": ["page1.md", "page2.md"],
      "context": "brief description of who they are"
    }
  ]
}

Only include wikilinks_to_add where the link does NOT already exist in the page. Check carefully.

PEOPLE PAGES:
'"$people_content"'

PROJECT PAGES:
'"$projects_content"'

WORK PAGES:
'"$work_content"''

log "Batch 1: Calling MiniMax..."
batch1_body=$(jq -n \
  --arg model "$MODEL" \
  --arg system "$SYSTEM_BASE" \
  --arg user "$batch1_prompt" \
  --argjson max_tokens "$MAX_TOKENS" \
  --argjson temp "$TEMPERATURE" \
  '{
    model: $model,
    messages: [
      { role: "system", name: "K2B Bootstrap", content: $system },
      { role: "user", name: "bootstrapper", content: $user }
    ],
    max_completion_tokens: $max_tokens,
    temperature: $temp
  }')

batch1_response=""
for attempt in 1 2 3; do
  batch1_response=$(mm_api POST /v1/text/chatcompletion_v2 "$batch1_body" 2>&1) && break
  log "Batch 1 attempt $attempt failed, waiting 20s..."
  sleep 20
done
if [[ -z "$batch1_response" ]] || echo "$batch1_response" | grep -q '"status_code":1000'; then
  log "ERROR: Batch 1 failed after 3 attempts"
  exit 1
fi
batch1_result=$(echo "$batch1_response" | jq -r '.choices[0].message.content // empty' | sed 's/^```json//; s/^```//; s/```$//')
log "Batch 1: Done"

# Extract entity map for batch 2
entity_map=$(echo "$batch1_result" | jq -r '.entity_map // {}' 2>/dev/null)

log "Waiting 10s before Batch 2 (rate limit cooldown)..."
sleep 10

# ============================================================
# BATCH 2: Knowledge mapping (concepts + insights + reference)
# ============================================================
log "Batch 2: Reading concepts, insights, reference..."
concepts_content=$(read_pages "concepts" 60)
insights_content=$(read_pages "insights" 60)
reference_content=$(read_pages "reference" 50)

batch2_prompt='Analyze these knowledge pages. Also consider the entity map from Batch 1 showing which people connect to which projects.

Tasks:
1. Identify concept pages that SHOULD exist based on topics mentioned across multiple pages
2. Identify missing cross-links between knowledge pages and entity pages
3. Identify which reference/insight pages should link to which entity pages

JSON schema:
{
  "concept_pages_to_create": [
    {
      "filename": "concept_slug.md",
      "title": "Concept Title",
      "description": "What this concept covers",
      "related_pages": ["[[page1]]", "[[page2]]", "..."],
      "content": "# Title\n\nDescription.\n\n## Related\n- [[page1]]\n- [[page2]]"
    }
  ],
  "wikilinks_to_add": [
    {
      "page": "filename.md",
      "section": "## Related Notes",
      "links": ["[[target1]]", "[[target2]]"]
    }
  ]
}

Only create concept pages for topics that appear in 2+ existing pages. Only suggest links that do NOT already exist.

ENTITY MAP FROM BATCH 1:
'"$(echo "$entity_map" | jq -c .)"'

CONCEPT PAGES:
'"$concepts_content"'

INSIGHT PAGES:
'"$insights_content"'

REFERENCE PAGES:
'"$reference_content"''

log "Batch 2: Calling MiniMax..."
batch2_body=$(jq -n \
  --arg model "$MODEL" \
  --arg system "$SYSTEM_BASE" \
  --arg user "$batch2_prompt" \
  --argjson max_tokens "$MAX_TOKENS" \
  --argjson temp "$TEMPERATURE" \
  '{
    model: $model,
    messages: [
      { role: "system", name: "K2B Bootstrap", content: $system },
      { role: "user", name: "bootstrapper", content: $user }
    ],
    max_completion_tokens: $max_tokens,
    temperature: $temp
  }')

batch2_response=""
for attempt in 1 2 3; do
  batch2_response=$(mm_api POST /v1/text/chatcompletion_v2 "$batch2_body" 2>&1) && break
  log "Batch 2 attempt $attempt failed, waiting 15s..."
  sleep 15
done
if [[ -z "$batch2_response" ]] || echo "$batch2_response" | grep -q '"status_code":1000'; then
  log "ERROR: Batch 2 failed after 3 attempts"
  exit 1
fi
batch2_result=$(echo "$batch2_response" | jq -r '.choices[0].message.content // empty' | sed 's/^```json//; s/^```//; s/```$//')
log "Batch 2: Done"

# ============================================================
# BATCH 3: Content + context + final synthesis
# ============================================================
log "Waiting 10s before Batch 3 (rate limit cooldown)..."
sleep 10

log "Batch 3: Reading content-pipeline, context..."
pipeline_content=$(read_pages "content-pipeline" 40)
context_content=$(read_pages "context" 40)

# Gather all wikilinks and concepts from batches 1-2
batch1_links=$(echo "$batch1_result" | jq -c '.wikilinks_to_add // []' 2>/dev/null)
batch2_links=$(echo "$batch2_result" | jq -c '.wikilinks_to_add // []' 2>/dev/null)
batch2_concepts=$(echo "$batch2_result" | jq -c '.concept_pages_to_create // []' 2>/dev/null)
batch1_stubs=$(echo "$batch1_result" | jq -c '.missing_person_stubs // []' 2>/dev/null)

batch3_prompt='Final pass. Review remaining pages and the accumulated findings from Batches 1-2.

Tasks:
1. Any additional cross-links for content-pipeline and context pages
2. Review the proposed concept pages -- are any redundant or too thin?
3. Any final missing connections

JSON schema:
{
  "wikilinks_to_add": [
    {
      "page": "filename.md",
      "section": "## Related Notes",
      "links": ["[[target1]]", "[[target2]]"]
    }
  ],
  "concept_pages_to_remove": ["concept_slug.md"],
  "notes": "Any observations about the wiki structure"
}

FINDINGS FROM BATCH 1 (entity links):
'"$batch1_links"'

FINDINGS FROM BATCH 2 (concept pages + knowledge links):
Concepts to create: '"$batch2_concepts"'
Links to add: '"$batch2_links"'

PERSON STUBS FROM BATCH 1:
'"$batch1_stubs"'

CONTENT PIPELINE PAGES:
'"$pipeline_content"'

CONTEXT PAGES:
'"$context_content"''

log "Batch 3: Calling MiniMax..."
batch3_body=$(jq -n \
  --arg model "$MODEL" \
  --arg system "$SYSTEM_BASE" \
  --arg user "$batch3_prompt" \
  --argjson max_tokens 8000 \
  --argjson temp "$TEMPERATURE" \
  '{
    model: $model,
    messages: [
      { role: "system", name: "K2B Bootstrap", content: $system },
      { role: "user", name: "bootstrapper", content: $user }
    ],
    max_completion_tokens: $max_tokens,
    temperature: $temp
  }')

batch3_response=""
for attempt in 1 2 3; do
  batch3_response=$(mm_api POST /v1/text/chatcompletion_v2 "$batch3_body" 2>&1) && break
  log "Batch 3 attempt $attempt failed, waiting 15s..."
  sleep 15
done
if [[ -z "$batch3_response" ]] || echo "$batch3_response" | grep -q '"status_code":1000'; then
  log "WARNING: Batch 3 failed after 3 attempts, continuing with Batch 1+2 results only"
  batch3_result='{"wikilinks_to_add":[],"concept_pages_to_remove":[],"notes":"Batch 3 skipped due to API errors"}'
fi
batch3_result=$(echo "$batch3_response" | jq -r '.choices[0].message.content // empty' | sed 's/^```json//; s/^```//; s/```$//')
log "Batch 3: Done"

# ============================================================
# Combine results
# ============================================================
log "Combining results..."

batch3_links=$(echo "$batch3_result" | jq -c '.wikilinks_to_add // []' 2>/dev/null)
batch3_removals=$(echo "$batch3_result" | jq -c '.concept_pages_to_remove // []' 2>/dev/null)
batch3_notes=$(echo "$batch3_result" | jq -r '.notes // ""' 2>/dev/null)

# Merge all wikilinks_to_add from all 3 batches
all_links=$(jq -n \
  --argjson b1 "$batch1_links" \
  --argjson b2 "$batch2_links" \
  --argjson b3 "$batch3_links" \
  '$b1 + $b2 + $b3')

# Filter out removed concepts
filtered_concepts=$(jq -n \
  --argjson concepts "$batch2_concepts" \
  --argjson removals "$batch3_removals" \
  '$concepts | [.[] | select(.filename as $f | ($removals | index($f)) == null)]')

combined=$(jq -n \
  --argjson wikilinks "$all_links" \
  --argjson concepts "$filtered_concepts" \
  --argjson stubs "$batch1_stubs" \
  --arg notes "$batch3_notes" \
  --argjson entity_map "$entity_map" \
  '{
    wikilinks_to_add: $wikilinks,
    concept_pages_to_create: $concepts,
    missing_person_stubs: $stubs,
    entity_map: $entity_map,
    notes: $notes,
    stats: {
      total_links_to_add: ($wikilinks | length),
      concept_pages: ($concepts | length),
      person_stubs: ($stubs | length)
    }
  }')

log "Bootstrap complete."
log "Stats: $(echo "$combined" | jq -c '.stats')"

echo "$combined"
