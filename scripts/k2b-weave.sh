#!/usr/bin/env bash
# k2b-weave.sh -- background cross-link weaver orchestrator
#
# See .claude/skills/k2b-weave/SKILL.md for the contract.
#
# Usage:
#   k2b-weave.sh run                    -- run a weaving pass (writes to vault)
#   k2b-weave.sh dry-run                -- run a pass, print proposals, no writes
#   k2b-weave.sh apply <digest-file>    -- apply decisions from a processed digest
#   k2b-weave.sh status                 -- show recent runs, ledger summary
#   k2b-weave.sh --help                 -- show this usage

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "$SCRIPT_DIR/minimax-common.sh"

# --- Config ---

readonly LOCK_FILE="$K2B_VAULT/wiki/.weave.lock"
readonly LOCK_TTL_SECONDS=1800  # 30 min
readonly LEDGER_FILE="$K2B_VAULT/wiki/context/crosslink-ledger.jsonl"
readonly METRICS_FILE="$K2B_VAULT/wiki/context/weave-metrics.jsonl"
readonly ERRORS_FILE="$K2B_VAULT/wiki/context/weave-errors.log"
readonly LOG_FILE="$K2B_VAULT/wiki/log.md"
readonly REVIEW_DIR="$K2B_VAULT/review"
readonly WIKI_DIR="$K2B_VAULT/wiki"
readonly MAX_TOKENS_BUDGET=120000
readonly TOP_N=10
readonly REJECTION_TTL_DAYS=30
readonly MAX_RETRY_COUNT=3
readonly SCOPE_FOLDERS=(people projects insights reference work concepts)

# --- Logging ---

log_info()  { echo "[weave] $*" >&2; }
log_error() { echo "[weave:ERROR] $*" >&2; }

# --- Atomic write helper ---
# Usage: atomic_write <target-path> <content>
# Uses fsync(temp) + rename + fsync(dir) per Codex recommendation for crash durability.
atomic_write() {
  local target="$1"
  local content="$2"
  local dir
  dir=$(dirname "$target")
  mkdir -p "$dir"
  local tmp="${target}.tmp.$$"
  printf '%s' "$content" > "$tmp"
  python3 -c "
import os, sys
tmp, dst = sys.argv[1], sys.argv[2]
fd = os.open(tmp, os.O_RDONLY)
try: os.fsync(fd)
finally: os.close(fd)
os.replace(tmp, dst)
dfd = os.open(os.path.dirname(dst) or '.', os.O_RDONLY)
try: os.fsync(dfd)
finally: os.close(dfd)
" "$tmp" "$target"
}

# Usage: atomic_append <target-path> <line>
# Append a single line safely. JSONL append is already atomic for lines < PIPE_BUF (4KB on macOS),
# but we use a lock-file-guarded append for safety with longer lines.
atomic_append() {
  local target="$1"
  local line="$2"
  mkdir -p "$(dirname "$target")"
  printf '%s\n' "$line" >> "$target"
}

# --- Lock management ---

acquire_lock() {
  mkdir -p "$(dirname "$LOCK_FILE")"
  if [[ -f "$LOCK_FILE" ]]; then
    local lock_mtime lock_age
    lock_mtime=$(stat -f %m "$LOCK_FILE" 2>/dev/null || stat -c %Y "$LOCK_FILE" 2>/dev/null || echo 0)
    lock_age=$(( $(date +%s) - lock_mtime ))
    if (( lock_age < LOCK_TTL_SECONDS )); then
      log_info "Lock present and fresh (age=${lock_age}s), concurrent run detected -- exiting 0"
      exit 0
    else
      log_info "Stale lock reclaimed (age=${lock_age}s)"
    fi
  fi
  printf '{"pid":%d,"started":"%s"}\n' "$$" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$LOCK_FILE"
  trap release_lock EXIT INT TERM
}

release_lock() {
  rm -f "$LOCK_FILE" 2>/dev/null || true
}

# --- Ledger helpers ---

# Recover ledger from tearing: drop any non-JSON trailing lines
recover_ledger() {
  [[ -f "$LEDGER_FILE" ]] || return 0
  local tmp="${LEDGER_FILE}.rec.$$"
  local recovered=0
  local skipped=0
  while IFS= read -r line || [[ -n "$line" ]]; do
    if [[ -z "$line" ]]; then
      continue
    fi
    if echo "$line" | jq -e . >/dev/null 2>&1; then
      printf '%s\n' "$line" >> "$tmp"
      recovered=$((recovered + 1))
    else
      skipped=$((skipped + 1))
    fi
  done < "$LEDGER_FILE"
  if (( skipped > 0 )); then
    log_info "Ledger recovery: $recovered ok, $skipped bad lines truncated"
  fi
  if [[ -f "$tmp" ]]; then
    mv -f "$tmp" "$LEDGER_FILE"
  else
    : > "$LEDGER_FILE"
  fi
}

# Return JSON array of {from, to} pair keys that should be excluded from MiniMax proposals,
# based on ledger state (applied, pending, deferred, permanently-rejected, or rejected within TTL).
get_ledger_exclusions() {
  if [[ ! -f "$LEDGER_FILE" ]]; then
    echo "[]"
    return
  fi
  local now_epoch ttl_seconds
  now_epoch=$(date +%s)
  ttl_seconds=$(( REJECTION_TTL_DAYS * 86400 ))
  jq -s --argjson now "$now_epoch" --argjson ttl "$ttl_seconds" --argjson maxr "$MAX_RETRY_COUNT" '
    map(
      select(
        .status == "applied"
        or .status == "pending"
        or .status == "deferred"
        or .status == "permanently-rejected"
        or (
          .status == "rejected"
          and (
            ((.rejected_at // "") | if . == "" then 0 else (. | strptime("%Y-%m-%dT%H:%M:%SZ") | mktime) end) + $ttl > $now
            or ((.retry_count // 0) >= $maxr)
          )
        )
      )
    )
    | map({from: (.from_slug // ""), to: (.to_slug // "")})
    | map(select(.from != "" and .to != ""))
    | unique
  ' "$LEDGER_FILE"
}

# --- Scope scanning ---

# Print one path per line for all in-scope pages
list_in_scope_pages() {
  local folder dir base
  for folder in "${SCOPE_FOLDERS[@]}"; do
    dir="$WIKI_DIR/$folder"
    [[ -d "$dir" ]] || continue
    while IFS= read -r f; do
      base=$(basename "$f")
      [[ "$base" == "index.md" ]] && continue
      [[ "$base" == feature_*.md ]] && continue
      printf '%s\n' "$f"
    done < <(find "$dir" -maxdepth 1 -name '*.md' -type f)
  done
}

# Slug from path: basename minus .md, normalized lowercase.
# Prints with trailing newline so redirect-append usage works.
# Command-substitution callers ($(path_to_slug ...)) strip the trailing newline automatically.
path_to_slug() {
  local base
  base=$(basename "$1" .md)
  printf '%s\n' "$base" | tr '[:upper:]' '[:lower:]'
}

# Extract slugs from all wikilinks in a file body (ignores frontmatter block)
extract_wikilink_slugs() {
  local file="$1"
  awk '
    BEGIN { in_fm = 0; fm_count = 0 }
    /^---$/ {
      if (fm_count == 0) { in_fm = 1; fm_count = 1; next }
      else if (in_fm) { in_fm = 0; next }
    }
    !in_fm { print }
  ' "$file" 2>/dev/null \
    | grep -oE '\[\[[^]|#]+' 2>/dev/null \
    | sed 's/^\[\[//' \
    | tr '[:upper:]' '[:lower:]' \
    | sort -u
}

# Build exclusion set from existing wikilinks across all in-scope pages.
# Returns JSON array of {from, to} pair keys.
build_wikilink_exclusions() {
  local tmp
  tmp=$(mktemp)
  trap 'rm -f "$tmp"' RETURN
  local file from_slug to_slug
  while IFS= read -r file; do
    from_slug=$(path_to_slug "$file")
    while IFS= read -r to_slug; do
      [[ -z "$to_slug" ]] && continue
      [[ "$to_slug" == "$from_slug" ]] && continue
      jq -cn --arg f "$from_slug" --arg t "$to_slug" '{from: $f, to: $t}' >> "$tmp"
    done < <(extract_wikilink_slugs "$file")
  done < <(list_in_scope_pages)
  if [[ -s "$tmp" ]]; then
    jq -s 'unique' "$tmp"
  else
    echo "[]"
  fi
}

# --- Page bundling for MiniMax ---

# Build a JSON array of {path, slug, title, frontmatter_type, category, body} for each in-scope page.
# Used as the primary input to minimax-weave.sh.
build_page_bundle() {
  local tmp
  tmp=$(mktemp)
  trap 'rm -f "$tmp"' RETURN
  local file body title fm_type category rel_path slug
  while IFS= read -r file; do
    rel_path="${file#"$K2B_VAULT"/}"
    slug=$(path_to_slug "$file")
    body=$(cat "$file")
    # Extract first-heading title, fallback to slug
    title=$(awk '/^# / { sub(/^# /, ""); print; exit }' "$file")
    [[ -z "$title" ]] && title="$slug"
    # Extract type from frontmatter (simple grep)
    fm_type=$(awk '/^type:/ { sub(/^type:[ ]*/, ""); gsub(/["'"'"']/, ""); print; exit }' "$file")
    # Derive category from parent folder name
    category=$(basename "$(dirname "$file")")
    jq -cn \
      --arg path "$rel_path" \
      --arg slug "$slug" \
      --arg title "$title" \
      --arg type "$fm_type" \
      --arg category "$category" \
      --arg body "$body" \
      '{path: $path, slug: $slug, title: $title, type: $type, category: $category, body: $body}' >> "$tmp"
  done < <(list_in_scope_pages)
  if [[ -s "$tmp" ]]; then
    jq -s . "$tmp"
  else
    echo "[]"
  fi
}

# --- Response validation ---

# Validate MiniMax response against the strict schema.
# Accepts JSON on stdin. Returns 0 if valid, 1 otherwise.
validate_response_schema() {
  local response="$1"
  echo "$response" | jq -e '
    type == "array"
    and (length == 0 or all(
      type == "object"
      and (.from_path | type == "string")
      and (.to_path | type == "string")
      and (.from_slug | type == "string")
      and (.to_slug | type == "string")
      and (.confidence | type == "number")
      and (.confidence >= 0 and .confidence <= 1)
      and (.rationale | type == "string")
      and (.evidence_span | type == "string")
    ))
  ' >/dev/null 2>&1
}

# --- Evidence span verification ---

# For each proposal, verify that the evidence_span actually appears as a substring of the
# from page body. Drops hallucinated spans. Returns filtered JSON array on stdout.
verify_evidence_spans() {
  local response="$1"
  local scope_paths="$2"  # JSON array of absolute paths
  python3 - "$response" "$scope_paths" "$K2B_VAULT" <<'PY'
import json, sys, os
response = json.loads(sys.argv[1])
scope_paths = json.loads(sys.argv[2])
vault_root = sys.argv[3]
# Build slug -> abs_path map
slug_to_path = {}
for p in scope_paths:
    slug = os.path.splitext(os.path.basename(p))[0].lower()
    slug_to_path[slug] = p

# Cache file bodies
body_cache = {}
def get_body(slug):
    if slug in body_cache:
        return body_cache[slug]
    path = slug_to_path.get(slug.lower())
    if not path or not os.path.isfile(path):
        body_cache[slug] = None
        return None
    with open(path, 'r') as f:
        body_cache[slug] = f.read()
    return body_cache[slug]

verified = []
for p in response:
    from_slug = (p.get("from_slug") or "").lower()
    to_slug = (p.get("to_slug") or "").lower()
    if not from_slug or not to_slug or from_slug == to_slug:
        continue
    if from_slug not in slug_to_path or to_slug not in slug_to_path:
        continue
    evidence = (p.get("evidence_span") or "").strip()
    body = get_body(from_slug)
    if body is None:
        continue
    # Evidence span must be a substring of the from page body (case-insensitive)
    if evidence and evidence.lower() not in body.lower():
        continue
    # Normalize slugs and paths before passing through
    p["from_slug"] = from_slug
    p["to_slug"] = to_slug
    p["from_path"] = os.path.relpath(slug_to_path[from_slug], vault_root)
    p["to_path"] = os.path.relpath(slug_to_path[to_slug], vault_root)
    verified.append(p)
print(json.dumps(verified))
PY
}

# --- Utility scoring ---

# Assign utility score to each verified proposal and keep top N.
# Score:
#   +3 if TO is currently an orphan (zero inbound wikilinks)
#   +2 if FROM and TO are in different wiki subfolders (cross-category)
#   +1 if confidence > 0.75
# Sort descending by score then confidence, take top N.
score_and_cut_top10() {
  local verified="$1"
  local orphan_slugs_json="$2"  # JSON array of orphan slugs
  python3 - "$verified" "$orphan_slugs_json" "$TOP_N" <<'PY'
import json, sys
verified = json.loads(sys.argv[1])
orphans = set(s.lower() for s in json.loads(sys.argv[2]))
top_n = int(sys.argv[3])

def category_from_path(path):
    # path like "wiki/projects/project_k2b.md" -> "projects"
    parts = path.split("/")
    if len(parts) >= 2 and parts[0] == "wiki":
        return parts[1]
    return ""

for p in verified:
    score = 0
    if p["to_slug"] in orphans:
        score += 3
    if category_from_path(p["from_path"]) != category_from_path(p["to_path"]):
        score += 2
    if p.get("confidence", 0) > 0.75:
        score += 1
    p["utility_score"] = score

verified.sort(key=lambda p: (-p["utility_score"], -p.get("confidence", 0)))
print(json.dumps(verified[:top_n]))
PY
}

# Get orphan slugs from the set of in-scope pages.
# An orphan is a page with zero inbound wikilinks from any other in-scope page.
compute_orphan_slugs() {
  local tmp_inbound
  tmp_inbound=$(mktemp)
  trap 'rm -f "$tmp_inbound"' RETURN

  local file slug
  # Collect all inbound link targets
  while IFS= read -r file; do
    while IFS= read -r slug; do
      [[ -n "$slug" ]] && echo "$slug" >> "$tmp_inbound"
    done < <(extract_wikilink_slugs "$file")
  done < <(list_in_scope_pages)

  sort -u "$tmp_inbound" > "${tmp_inbound}.uniq"

  # Find in-scope slugs with no entry in the inbound list
  local all_slugs_tmp
  all_slugs_tmp=$(mktemp)
  while IFS= read -r file; do
    path_to_slug "$file" >> "$all_slugs_tmp"
  done < <(list_in_scope_pages)
  sort -u "$all_slugs_tmp" > "${all_slugs_tmp}.uniq"

  comm -23 "${all_slugs_tmp}.uniq" "${tmp_inbound}.uniq" | jq -R . | jq -s .

  rm -f "$all_slugs_tmp" "${all_slugs_tmp}.uniq" "${tmp_inbound}.uniq"
}

# --- Digest writing ---

# Write the digest note atomically with the top-N proposals.
# Args: digest_path, run_id, proposals_json
write_digest() {
  local digest_path="$1"
  local run_id="$2"
  local proposals="$3"
  local today
  today=$(date +%Y-%m-%d)

  local body
  body=$(python3 - "$proposals" "$run_id" "$today" <<'PY'
import json, sys
proposals = json.loads(sys.argv[1])
run_id = sys.argv[2]
today = sys.argv[3]

lines = []
lines.append("---")
lines.append("tags: [crosslink-digest, weave, review]")
lines.append(f"date: {today}")
lines.append("type: crosslink-digest")
lines.append("origin: k2b-generate")
lines.append(f"run-id: {run_id}")
lines.append("review-action: pending")
lines.append('review-notes: ""')
lines.append('up: "[[index]]"')
lines.append("---")
lines.append("")
lines.append(f"# Cross-link proposals -- {today} ({run_id})")
lines.append("")
lines.append(f"MiniMax M2.7 found {len(proposals)} candidate pairs. Mark each row with `check` / `x` / `defer` in the Decision column, save, then run `/inbox` to apply.")
lines.append("")
lines.append("| # | From | To | Confidence | Why | Evidence | Decision |")
lines.append("|---|------|-----|------------|-----|----------|----------|")
for i, p in enumerate(proposals, start=1):
    rationale = (p.get("rationale") or "").replace("|", "\\|").replace("\n", " ")[:180]
    evidence = (p.get("evidence_span") or "").replace("|", "\\|").replace("\n", " ")[:120]
    lines.append(
        f"| {i} | {p['from_slug']} | {p['to_slug']} | {p.get('confidence', 0):.2f} | {rationale} | {evidence} |  |"
    )
lines.append("")
lines.append("## How to decide")
lines.append("")
lines.append("- **check** -- I want this link. K2B will add `[[to_slug]]` to the FROM page's `related:` field.")
lines.append("- **x** -- Reject. K2B will remember and not propose this pair again for 30 days.")
lines.append("- **defer** (or leave blank) -- Not now. Will come back next run.")
lines.append("")
lines.append(f"## Utility scores")
lines.append("")
lines.append("| # | From | To | Score | Orphan-reduce | Cross-cat | High-conf |")
lines.append("|---|------|-----|-------|---------------|-----------|-----------|")
for i, p in enumerate(proposals, start=1):
    score = p.get("utility_score", 0)
    lines.append(f"| {i} | {p['from_slug']} | {p['to_slug']} | {score} | - | - | - |")
lines.append("")
print("\n".join(lines))
PY
)
  atomic_write "$digest_path" "$body"
}

# --- Ledger write helpers ---

append_proposals_to_ledger() {
  local proposals="$1"
  local run_id="$2"
  local today
  today=$(date +%Y-%m-%d)
  local row
  while IFS= read -r row; do
    atomic_append "$LEDGER_FILE" "$row"
  done < <(echo "$proposals" | jq -c --arg run "$run_id" --arg date "$today" '
    .[] | {
      date: $date,
      run_id: $run,
      from_path: .from_path,
      to_path: .to_path,
      from_slug: .from_slug,
      to_slug: .to_slug,
      tier: "MEDIUM",
      confidence: .confidence,
      rationale: .rationale,
      evidence_span: .evidence_span,
      status: "pending",
      retry_count: 0,
      rejected_at: null
    }
  ')
}

# Mark a pair as rejected in the ledger (or increment retry_count if already there).
# Args: from_slug, to_slug
mark_ledger_rejected() {
  local from_slug="$1"
  local to_slug="$2"
  local now
  now=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  _update_ledger_pair "$from_slug" "$to_slug" "$(jq -cn --arg now "$now" --argjson maxr "$MAX_RETRY_COUNT" '{status: "rejected", rejected_at: $now, _increment_retry: true, _max_retry: $maxr}')"
}

mark_ledger_applied() {
  local from_slug="$1"
  local to_slug="$2"
  _update_ledger_pair "$from_slug" "$to_slug" '{"status": "applied"}'
}

mark_ledger_deferred() {
  local from_slug="$1"
  local to_slug="$2"
  _update_ledger_pair "$from_slug" "$to_slug" '{"status": "deferred"}'
}

mark_ledger_stale_renamed() {
  local from_slug="$1"
  local to_slug="$2"
  _update_ledger_pair "$from_slug" "$to_slug" '{"status": "stale-renamed"}'
}

_update_ledger_pair() {
  local from_slug="$1"
  local to_slug="$2"
  local patch="$3"
  [[ -f "$LEDGER_FILE" ]] || return 0
  local tmp="${LEDGER_FILE}.upd.$$"
  jq -c --arg from "$from_slug" --arg to "$to_slug" --argjson patch "$patch" '
    if .from_slug == $from and .to_slug == $to and .status == "pending" then
      . + $patch
      | (if ($patch._increment_retry // false) then
          .retry_count = ((.retry_count // 0) + 1)
          | (if .retry_count >= ($patch._max_retry // 3) then .status = "permanently-rejected" else . end)
        else . end)
      | del(._increment_retry, ._max_retry)
    else . end
  ' "$LEDGER_FILE" > "$tmp"
  mv -f "$tmp" "$LEDGER_FILE"
}

# --- Metrics and log ---

append_metrics() {
  local pages_scanned="$1"
  local candidates_raw="$2"
  local proposals_top10="$3"
  local duration_ms="$4"
  local input_bytes="$5"
  local error="${6:-}"
  local run_id="${7:-}"
  local today
  today=$(date +%Y-%m-%d)
  local row
  row=$(jq -cn \
    --arg date "$today" \
    --arg run_id "$run_id" \
    --argjson pages_scanned "$pages_scanned" \
    --argjson candidates_raw "$candidates_raw" \
    --argjson proposals_top10 "$proposals_top10" \
    --argjson duration_ms "$duration_ms" \
    --argjson input_bytes "$input_bytes" \
    --arg error "$error" \
    '{
      date: $date,
      run_id: $run_id,
      pages_scanned: $pages_scanned,
      candidates_raw: $candidates_raw,
      proposals_top10: $proposals_top10,
      duration_ms: $duration_ms,
      input_bytes: $input_bytes,
      error: (if $error == "" then null else $error end)
    }')
  atomic_append "$METRICS_FILE" "$row"
}

append_log_line() {
  local line="$1"
  local ts
  ts=$(date +%Y-%m-%dT%H:%M:%S)
  atomic_append "$LOG_FILE" "- $ts $line"
}

notify_failure() {
  local msg="$1"
  log_error "$msg"
  # Notification path: append to a well-known alerts file that /improve can surface.
  # In v2 this can route through the Telegram bot.
  local alerts_file="$K2B_VAULT/wiki/context/weave-alerts.md"
  mkdir -p "$(dirname "$alerts_file")"
  printf -- '- %s -- %s\n' "$(date +%Y-%m-%dT%H:%M:%S)" "$msg" >> "$alerts_file"
}

# --- Apply: parse digest decision table ---

# Emit one TSV line per decision: from_slug<TAB>to_slug<TAB>decision
parse_decision_table() {
  local digest_file="$1"
  python3 - "$digest_file" <<'PY'
import re, sys
path = sys.argv[1]
with open(path, 'r') as f:
    content = f.read()
# Find the first "| # | From | To |" table header
lines = content.splitlines()
in_table = False
header_seen = False
for line in lines:
    stripped = line.strip()
    if stripped.startswith("| # | From | To |"):
        in_table = True
        header_seen = False
        continue
    if in_table:
        # Separator line like |---|----|
        if re.match(r"^\|[\s\-:]+\|", stripped):
            header_seen = True
            continue
        if not stripped.startswith("|"):
            in_table = False
            continue
        if not header_seen:
            continue
        # Parse row
        cells = [c.strip() for c in stripped.strip("|").split("|")]
        if len(cells) < 7:
            continue
        _num, from_slug, to_slug, _conf, _why, _evidence, decision = cells[:7]
        # Normalize decision tokens
        d = decision.lower().strip()
        if d in ("check", "✓", "yes", "y", "ok", "x-check"):
            d_norm = "check"
        elif d in ("x", "✗", "no", "n", "reject"):
            d_norm = "x"
        elif d == "defer":
            d_norm = "defer"
        elif d == "":
            d_norm = "defer"
        else:
            d_norm = d
        print(f"{from_slug}\t{to_slug}\t{d_norm}")
PY
}

# --- Apply: apply a single approved proposal to a page ---

# Delegates to Python helper for safe YAML frontmatter editing.
# Returns 0 on success, 2 if the FROM page cannot be found (stale-renamed), 1 on error.
apply_one_proposal() {
  local from_slug="$1"
  local to_slug="$2"
  local from_path
  from_path=$(find_page_by_slug "$from_slug" || true)
  if [[ -z "$from_path" || ! -f "$from_path" ]]; then
    return 2
  fi
  if ! python3 "$SCRIPT_DIR/k2b-weave-add-related.py" "$from_path" "$to_slug"; then
    return 1
  fi
  return 0
}

# Find the absolute path for a slug across the in-scope wiki folders.
find_page_by_slug() {
  local slug="$1"
  local folder path
  for folder in "${SCOPE_FOLDERS[@]}"; do
    path="$WIKI_DIR/$folder/${slug}.md"
    if [[ -f "$path" ]]; then
      printf '%s' "$path"
      return 0
    fi
  done
  return 1
}

# --- Commands ---

cmd_run() {
  local is_dry_run="${1:-false}"
  acquire_lock
  recover_ledger

  local run_id
  run_id=$(date +%Y%m%d-%H%M)

  log_info "Scanning in-scope pages..."
  local pages_list
  pages_list=$(list_in_scope_pages)
  local page_count
  page_count=$(printf '%s\n' "$pages_list" | grep -c . || true)
  log_info "Found $page_count in-scope pages"

  if (( page_count == 0 )); then
    log_info "No in-scope pages. Nothing to do."
    append_metrics "$page_count" 0 0 0 0 "" "$run_id"
    append_log_line "[weave] $run_id -- no in-scope pages"
    return 0
  fi

  # Build page bundle for MiniMax
  local page_bundle
  page_bundle=$(build_page_bundle)

  # Build exclusion set (ledger + existing wikilinks)
  local ledger_excl wikilink_excl combined_excl
  ledger_excl=$(get_ledger_exclusions)
  wikilink_excl=$(build_wikilink_exclusions)
  combined_excl=$(jq -n --argjson a "$ledger_excl" --argjson b "$wikilink_excl" '$a + $b | unique')

  # Pre-flight token estimate
  local input_json input_bytes estimated_tokens
  input_json=$(jq -cn --argjson pages "$page_bundle" --argjson exclude "$combined_excl" '{pages: $pages, exclude: $exclude}')
  input_bytes=$(printf '%s' "$input_json" | wc -c | tr -d ' ')
  estimated_tokens=$(( input_bytes / 4 ))
  log_info "Input size: ${input_bytes} bytes, ~${estimated_tokens} tokens"

  if (( estimated_tokens > MAX_TOKENS_BUDGET )); then
    notify_failure "weave: vault too large for single-prompt approach (${estimated_tokens} > ${MAX_TOKENS_BUDGET}). Time to add embedding prefilter."
    append_metrics "$page_count" 0 0 0 "$input_bytes" "token_budget_exceeded" "$run_id"
    exit 1
  fi

  # Call MiniMax
  log_info "Calling MiniMax M2.7..."
  local start_ms end_ms duration_ms
  start_ms=$(python3 -c 'import time; print(int(time.time()*1000))')
  local response
  if ! response=$(printf '%s' "$input_json" | "$SCRIPT_DIR/minimax-weave.sh"); then
    notify_failure "weave: MiniMax call failed"
    append_metrics "$page_count" 0 0 0 "$input_bytes" "minimax_api_error" "$run_id"
    exit 1
  fi
  end_ms=$(python3 -c 'import time; print(int(time.time()*1000))')
  duration_ms=$(( end_ms - start_ms ))

  # Validate schema
  if ! validate_response_schema "$response"; then
    mkdir -p "$(dirname "$ERRORS_FILE")"
    printf '=== %s run=%s ===\n%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$run_id" "$response" >> "$ERRORS_FILE"
    notify_failure "weave: MiniMax returned invalid JSON schema. See $ERRORS_FILE"
    append_metrics "$page_count" 0 0 "$duration_ms" "$input_bytes" "schema_violation" "$run_id"
    exit 1
  fi

  # Build a JSON array of scope paths (absolute) for evidence verification
  local scope_paths_json
  scope_paths_json=$(printf '%s\n' "$pages_list" | jq -R . | jq -s .)

  # Verify evidence spans
  local verified
  verified=$(verify_evidence_spans "$response" "$scope_paths_json")
  local verified_count
  verified_count=$(echo "$verified" | jq 'length')
  log_info "Verified $verified_count/$(echo "$response" | jq 'length') proposals"

  if (( verified_count == 0 )); then
    log_info "Clean run -- no verified proposals"
    append_metrics "$page_count" "$(echo "$response" | jq 'length')" 0 "$duration_ms" "$input_bytes" "" "$run_id"
    append_log_line "[weave] $run_id -- clean run, no proposals"
    return 0
  fi

  # Compute orphans for utility scoring
  local orphans
  orphans=$(compute_orphan_slugs)

  # Score and take top N
  local scored
  scored=$(score_and_cut_top10 "$verified" "$orphans")
  local top_count
  top_count=$(echo "$scored" | jq 'length')

  if [[ "$is_dry_run" == "true" ]]; then
    log_info "DRY RUN -- would write $top_count proposals:"
    echo "$scored" | jq -r '.[] | "  [\(.utility_score // 0)] \(.from_slug) -> \(.to_slug) (conf=\(.confidence)) -- \(.rationale)"'
    return 0
  fi

  # Write digest (atomic)
  local today_short
  today_short=$(date +%Y-%m-%d_%H%M)
  local digest_path="$REVIEW_DIR/crosslinks_${today_short}.md"
  mkdir -p "$REVIEW_DIR"
  write_digest "$digest_path" "$run_id" "$scored"
  log_info "Wrote digest: $digest_path"

  # Log proposals to ledger
  append_proposals_to_ledger "$scored" "$run_id"

  # Metrics and log
  append_metrics "$page_count" "$(echo "$response" | jq 'length')" "$top_count" "$duration_ms" "$input_bytes" "" "$run_id"
  append_log_line "[weave] $run_id -- $top_count proposals in review/$(basename "$digest_path")"

  # Skill usage log
  mkdir -p "$K2B_VAULT/wiki/context"
  printf '%s\tk2b-weave\t%s\tweave run: %s proposals, 0 applied\n' "$(date +%Y-%m-%d)" "$(echo $RANDOM | md5sum 2>/dev/null | head -c 8 || echo $RANDOM)" "$top_count" >> "$K2B_VAULT/wiki/context/skill-usage-log.tsv" 2>/dev/null || true

  log_info "Done. $top_count proposals in $(basename "$digest_path")"
}

cmd_apply() {
  local digest_file="$1"
  if [[ ! -f "$digest_file" ]]; then
    log_error "Digest not found: $digest_file"
    exit 1
  fi
  acquire_lock
  recover_ledger

  local applied=0 rejected=0 deferred=0 stale=0

  local decisions
  decisions=$(parse_decision_table "$digest_file")

  if [[ -z "$decisions" ]]; then
    log_info "No decisions found in digest. Leaving as-is."
    return 0
  fi

  local from_slug to_slug decision rc
  while IFS=$'\t' read -r from_slug to_slug decision; do
    case "$decision" in
      check)
        set +e
        apply_one_proposal "$from_slug" "$to_slug"
        rc=$?
        set -e
        case "$rc" in
          0)
            mark_ledger_applied "$from_slug" "$to_slug"
            applied=$(( applied + 1 ))
            ;;
          2)
            mark_ledger_stale_renamed "$from_slug" "$to_slug"
            stale=$(( stale + 1 ))
            log_info "Stale-renamed: $from_slug -> $to_slug (FROM page not found)"
            ;;
          *)
            log_error "apply failed for $from_slug -> $to_slug (rc=$rc)"
            ;;
        esac
        ;;
      x)
        mark_ledger_rejected "$from_slug" "$to_slug"
        rejected=$(( rejected + 1 ))
        ;;
      defer|"")
        mark_ledger_deferred "$from_slug" "$to_slug"
        deferred=$(( deferred + 1 ))
        ;;
      *)
        log_info "Unknown decision '$decision' for $from_slug -> $to_slug, treating as defer"
        mark_ledger_deferred "$from_slug" "$to_slug"
        deferred=$(( deferred + 1 ))
        ;;
    esac
  done <<< "$decisions"

  # Delete digest
  rm -f "$digest_file"

  append_log_line "[weave-apply] $(basename "$digest_file") -- $applied applied, $rejected rejected, $deferred deferred, $stale stale-renamed"
  log_info "Applied $applied, rejected $rejected, deferred $deferred, stale $stale"
}

cmd_status() {
  echo "=== k2b-weave status ==="
  if [[ -f "$METRICS_FILE" ]]; then
    echo ""
    echo "Last 5 runs:"
    tail -5 "$METRICS_FILE" 2>/dev/null | jq -r '. | "  \(.date) run=\(.run_id // "-") pages=\(.pages_scanned // 0) proposals=\(.proposals_top10 // 0) duration=\(.duration_ms // 0)ms \(if .error then "ERROR: \(.error)" else "" end)"' 2>/dev/null || echo "  (empty or unparseable)"
  else
    echo "  (no metrics yet)"
  fi

  if [[ -f "$LEDGER_FILE" ]]; then
    echo ""
    echo "Ledger summary:"
    jq -s -r 'group_by(.status) | map("  \(.[0].status): \(length)") | .[]' "$LEDGER_FILE" 2>/dev/null || echo "  (unparseable)"
    local ledger_count
    ledger_count=$(wc -l < "$LEDGER_FILE" | tr -d ' ')
    echo "  total rows: $ledger_count"
  else
    echo ""
    echo "Ledger: empty"
  fi

  if [[ -f "$LOCK_FILE" ]]; then
    echo ""
    echo "Lock file present:"
    cat "$LOCK_FILE"
  fi

  # Graph density (rough)
  local page_count inbound_total density
  page_count=$(list_in_scope_pages | wc -l | tr -d ' ')
  if (( page_count > 0 )); then
    inbound_total=0
    while IFS= read -r f; do
      local links
      links=$(extract_wikilink_slugs "$f" | wc -l | tr -d ' ')
      inbound_total=$(( inbound_total + links ))
    done < <(list_in_scope_pages)
    density=$(awk -v t="$inbound_total" -v p="$page_count" 'BEGIN { printf "%.2f", t / p }')
    echo ""
    echo "Graph density: $density links per page ($inbound_total total / $page_count pages)"
  fi
}

# --- Main dispatch ---

cmd="${1:-}"
case "$cmd" in
  run)       cmd_run false ;;
  dry-run)   cmd_run true ;;
  apply)     [[ -n "${2:-}" ]] || { echo "Usage: k2b-weave.sh apply <digest-file>" >&2; exit 2; }; cmd_apply "$2" ;;
  status)    cmd_status ;;
  --help|-h|"") cat <<EOF
k2b-weave.sh -- background cross-link weaver

Usage:
  k2b-weave.sh run                    Run a weaving pass (writes to vault)
  k2b-weave.sh dry-run                Run a pass, print proposals, no writes
  k2b-weave.sh apply <digest-file>    Apply decisions from a processed digest
  k2b-weave.sh status                 Show recent runs, ledger summary

See .claude/skills/k2b-weave/SKILL.md for the contract.
EOF
    ;;
  *) echo "Unknown command: $cmd. Run with --help for usage." >&2; exit 2 ;;
esac
