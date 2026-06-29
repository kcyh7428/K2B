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
# Pre-flight INPUT budget, in rough estimated tokens (bytes/4; see cmd_run). Caps the
# input bundle ONLY. Kimi's long context window is shared by input + system prompt
# + JSON schema + the up-to-10-proposal output + Kimi's own reasoning tokens, and bytes/4
# measures none of those except the input. So this is set as a RESERVATION: 256K window
# minus ~86K held back for everything that is not the input bundle. ~86K is far above the
# realistic ~25K overhead, which (a) keeps us under the wall even on a heavy output run,
# and (b) buys margin for the bytes/4 estimate being rough -- it undercounts CJK and
# overcounts repetitive English. At ~1.6K est-tokens/page (full-body bundling) 170K
# covers ~104 in-scope pages.
# Recalibrated 2026-06-14: 120K -> 170K. The old 120K aborted at 76 pages (~3% over) --
# far below any real recall-degradation or context wall -- and the alert's suggested fix
# (drop wiki/reference/ from scope) was wrong on the ledger data (reference is the 2nd
# most productive crosslink source, 18 applied links). The durable scaling fix
# (summary-view bundling, then embedding prefilter) is tracked in
# wiki/concepts/feature_weave-embedding-prefilter.md. Do NOT raise past ~200K without
# that fix: at 200K input even a heavy output run approaches the 256K wall.
readonly MAX_TOKENS_BUDGET=170000
readonly TOP_N=10
readonly REJECTION_TTL_DAYS=30
readonly MAX_RETRY_COUNT=3
readonly SCOPE_FOLDERS=(people projects insights reference work concepts)

# --- Auto-apply config ---
# Effective gate = WEAVE_AUTO_APPLY=true (env kill-switch) AND the policy ledger's
# k2b-weave/crosslink_apply autonomy entry has auto_eligible=true. Either disables it.
# When the gate is closed, cmd_run falls back to the legacy digest+review path.
WEAVE_AUTO_APPLY="${WEAVE_AUTO_APPLY:-true}"
WEAVE_AUTO_APPLY_THRESHOLD="${WEAVE_AUTO_APPLY_THRESHOLD:-0.80}"
readonly POLICY_LEDGER_FILE="$K2B_VAULT/wiki/context/policy-ledger.jsonl"

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

# Return JSON array of {from, to} pair keys that should be excluded from text worker proposals,
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
        or .status == "held-low-confidence"
        or .status == "apply-failed-permanent"
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

# Validate Kimi response against the strict schema.
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
lines.append(f"Kimi K2.7 Code found {len(proposals)} candidate pairs. Mark each row with `check` / `x` / `defer` in the Decision column, save, then run `/review` to apply.")
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

# NOTE: ledger status transitions (applied/stale/rejected/deferred/held/apply-failed)
# now all go through the single-writer upsert recorder scripts/k2b-weave-record-apply.py
# (called via record_apply_outcome). The former mark_ledger_* / _update_ledger_pair
# helpers only mutated `pending` rows and reported success on no-match, which silently
# discarded decisions on retry/drifted/hand-made digests -- removed (Codex review).

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
# Find the proposals table header. Obsidian may add extra whitespace inside cells,
# so normalize cells before matching: split on |, strip each cell, compare keywords.
lines = content.splitlines()
in_table = False
header_seen = False

def split_row(line):
    """Split a markdown table row on unescaped `|` only.

    The digest writer escapes literal pipes inside cell content as `\\|`
    so the table renders correctly when an evidence_span contains, e.g.,
    a wikilink alias `[[slug|Title]]`. A naive `split("|")` ignores that
    escape and over-splits, pushing the alias suffix into the Decision
    column. Protect `\\|` with a sentinel before the split, then restore."""
    protected = line.replace("\\|", "\x00")
    return [c.replace("\x00", "|").strip() for c in protected.strip("|").split("|")]

def is_header_row(line):
    """Match the proposals table header.

    Both the proposals table and the trailing `## Utility scores` table
    start with `| # | From | To |`; distinguish by requiring `Decision`
    at column 6 (proposals-only). Without this, the utility table is
    re-parsed and its `-` placeholders are read as decisions."""
    cells = [c.lower() for c in split_row(line.strip())]
    return (
        len(cells) >= 7
        and cells[0] == "#"
        and cells[1] == "from"
        and cells[2] == "to"
        and cells[6] == "decision"
    )

def is_separator_row(line):
    """Match '| --- | --- | ...' separator rows."""
    return bool(re.match(r"^\|[\s\-:]+(\|[\s\-:]+)+\|?\s*$", line.strip()))

for line in lines:
    stripped = line.strip()
    if not in_table and is_header_row(stripped):
        in_table = True
        header_seen = False
        continue
    if in_table:
        if is_separator_row(stripped):
            header_seen = True
            continue
        if not stripped.startswith("|"):
            in_table = False
            continue
        if not header_seen:
            continue
        # Parse row: split on unescaped |, handle missing trailing pipe
        if not stripped.endswith("|"):
            stripped += " |"
        cells = split_row(stripped)
        if len(cells) < 3:
            continue
        # Map by position: #, From, To, Confidence, Why, Evidence, Decision
        from_slug = cells[1] if len(cells) > 1 else ""
        to_slug = cells[2] if len(cells) > 2 else ""
        decision = cells[6] if len(cells) > 6 else ""
        if not from_slug or not to_slug:
            continue
        # Normalize decision tokens
        d = decision.lower().strip()
        if d in ("check", "✓", "yes", "y", "ok"):
            d_norm = "check"
        elif d in ("x", "✗", "no", "n", "reject"):
            d_norm = "x"
        elif d in ("defer", ""):
            d_norm = "defer"
        else:
            d_norm = d
        print(f"{from_slug}\t{to_slug}\t{d_norm}")
PY
}

# --- Apply: apply a single approved proposal to a page ---

# Delegates to Python helper for safe YAML frontmatter editing.
# Exit codes (distinct so the retryable concurrency signal survives -- the helper
# returns 2 for an mtime race, which must NOT be collapsed into a hard error):
#   0  applied (or already present -- idempotent)
#   2  stale -- FROM or TO page not found
#   3  concurrency-retry (helper mtime race; retry next run, no retry budget spent)
#   1  hard error (missing frontmatter, write failure, etc.)
apply_one_proposal() {
  local from_slug="$1"
  local to_slug="$2"
  local from_path to_path
  from_path=$(find_page_by_slug "$from_slug" || true)
  if [[ -z "$from_path" || ! -f "$from_path" ]]; then
    return 2
  fi
  # Verify the TO page still exists too. The retry queue replays old ledger rows, and a
  # TO page renamed/deleted since the original failure would otherwise get a broken
  # `related: [[to_slug]]` link recorded as applied (Codex review). Treat as stale.
  to_path=$(find_page_by_slug "$to_slug" || true)
  if [[ -z "$to_path" || ! -f "$to_path" ]]; then
    return 2
  fi
  # Capture the helper rc WITHOUT toggling the global `set -e` (toggling it inside a
  # function leaks the option to the caller and makes `return 1` abort the script).
  local hrc=0
  python3 "$SCRIPT_DIR/k2b-weave-add-related.py" "$from_path" "$to_slug" || hrc=$?
  case "$hrc" in
    0) return 0 ;;
    2) return 3 ;;
    *) return 1 ;;
  esac
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

# --- Auto-apply gate + helpers ---

# True (0) iff the env kill-switch is on AND the policy ledger grants autonomy for
# k2b-weave/crosslink_apply. Makes the previously documentation-only policy gate
# executable, so a hands-off cron run honors it.
auto_apply_enabled() {
  [[ "$WEAVE_AUTO_APPLY" == "true" ]] || return 1
  [[ -f "$POLICY_LEDGER_FILE" ]] || return 1
  local eligible
  eligible=$(jq -s -r '
    map(select(.type == "autonomy" and .scope == "k2b-weave" and .action == "crosslink_apply"))
    | (.[-1].auto_eligible // false) | tostring
  ' "$POLICY_LEDGER_FILE" 2>/dev/null || echo false)
  [[ "$eligible" == "true" ]]
}

# Drop proposals whose {from,to} pair is already excluded by ledger state
# (applied/pending/deferred/held/permanently-rejected/rejected-in-TTL) or by an
# existing wikilink. Enforced AFTER the worker returns so a re-proposed pair can
# never override a prior decision under auto-apply (Codex plan-review finding R1).
# Prints filtered proposals JSON on stdout.
filter_excluded_proposals() {
  local scored="$1"
  local ledger_excl wikilink_excl combined
  ledger_excl=$(get_ledger_exclusions)
  wikilink_excl=$(build_wikilink_exclusions)
  combined=$(jq -n --argjson a "$ledger_excl" --argjson b "$wikilink_excl" '$a + $b | unique')
  # Collision-proof key: a JSON-encoded [from,to] array (no fragile separator byte).
  jq -n --argjson scored "$scored" --argjson excl "$combined" '
    def pkey(f; t): ([(f | ascii_downcase), (t | ascii_downcase)] | @json);
    ($excl | map({ key: pkey(.from; .to), value: true }) | from_entries) as $exmap
    | $scored
    | map(select(($exmap[pkey(.from_slug; .to_slug)] // false) | not))
  '
}

# Emit the open retry queue as a proposals array: ledger rows still in `apply-failed`
# (non-permanent). These are deterministically retried each run regardless of whether
# Kimi re-proposes them, so a one-off apply failure always progresses toward
# apply-failed-permanent + alert instead of stalling forever (Codex review finding).
load_retry_pairs() {
  [[ -f "$LEDGER_FILE" ]] || { echo "[]"; return; }
  jq -s '[ .[]
    | select(.status == "apply-failed")
    | { from_path: (.from_path // ""), to_path: (.to_path // ""),
        from_slug, to_slug, confidence: (.confidence // 1),
        rationale: (.rationale // ""), evidence_span: (.evidence_span // "") } ]' "$LEDGER_FILE"
}

# Record one apply outcome for a pair via the python upsert recorder. Echoes the
# final ledger status (e.g. "applied", "held-low-confidence", "apply-failed-permanent").
# Args: from_slug to_slug outcome [run_id from_path to_path confidence rationale evidence]
record_apply_outcome() {
  local from_slug="$1" to_slug="$2" outcome="$3"
  local run_id="${4:-}" from_path="${5:-}" to_path="${6:-}" confidence="${7:-0}" rationale="${8:-}" evidence="${9:-}"
  python3 "$SCRIPT_DIR/k2b-weave-record-apply.py" \
    --ledger "$LEDGER_FILE" \
    --from "$from_slug" --to "$to_slug" \
    --outcome "$outcome" --max-retry "$MAX_RETRY_COUNT" \
    --run-id "$run_id" --date "$(date +%Y-%m-%d)" \
    --from-path "$from_path" --to-path "$to_path" \
    --confidence "$confidence" --rationale "$rationale" --evidence "$evidence"
}

# Auto-apply the top-N proposals directly (no digest, no human gate). Mutates the
# ledger with final per-pair statuses. Prints a "applied=N held=M stale=K failed=J permanent=P"
# summary line on stdout for the caller to log.
# Sets the global AUTO_APPLY_SUMMARY and returns 0 on success, non-zero on a FATAL
# error (filter failure or a record-write failure on an applied/held pair) so the
# caller can fall back to writing a digest instead of logging a false success.
# NOT run through command substitution -- $(...) clears errexit and would mask a
# mid-function failure (Codex review finding).
# True (0) iff the ledger can be written: parent dir is a writable directory AND the
# ledger is either absent or a writable regular file. Probed BEFORE any page mutation
# so an unwritable ledger aborts cleanly (no partial apply) instead of mutating pages
# and then losing the durable retry/audit row (Codex review finding).
ledger_writable() {
  local dir
  dir=$(dirname "$LEDGER_FILE")
  [[ -d "$dir" && -w "$dir" ]] || return 1
  if [[ -e "$LEDGER_FILE" ]]; then
    [[ -f "$LEDGER_FILE" && -w "$LEDGER_FILE" ]] || return 1
  fi
  return 0
}

# Deterministically re-attempt every open apply-failed pair, INDEPENDENT of the text
# worker. Runs early in cmd_run (before the Kimi call) so a degraded worker -- token
# overflow, missing wrapper, API failure, invalid schema -- can never stall the retry
# queue. Best-effort: record failures are non-fatal (the apply-failed row persists and
# is retried next run; apply_one_proposal is idempotent). Each call advances a stuck
# pair's retry_count by exactly one, so it reaches apply-failed-permanent + alert in
# bounded time regardless of Kimi's health. No threshold, no exclusion filter.
process_retry_queue() {
  local run_id="$1"
  ledger_writable || { log_error "retry-queue: ledger not writable -- skipping retries this run"; return 0; }
  local retry_only n_retry
  retry_only=$(load_retry_pairs | jq 'unique_by([(.from_slug | ascii_downcase), (.to_slug | ascii_downcase)])')
  n_retry=$(echo "$retry_only" | jq 'length')
  (( n_retry == 0 )) && return 0
  log_info "Retry queue: re-attempting $n_retry open apply-failed pair(s)"
  local r_row r_from r_to r_fp r_tp r_conf r_rat r_ev r_rc r_final
  while IFS= read -r r_row; do
    [[ -z "$r_row" ]] && continue
    r_from=$(echo "$r_row" | jq -r '.from_slug')
    r_to=$(echo "$r_row" | jq -r '.to_slug')
    r_fp=$(echo "$r_row" | jq -r '.from_path // ""')
    r_tp=$(echo "$r_row" | jq -r '.to_path // ""')
    r_conf=$(echo "$r_row" | jq -r '.confidence // 1')
    r_rat=$(echo "$r_row" | jq -r '.rationale // ""')
    r_ev=$(echo "$r_row" | jq -r '.evidence_span // ""')
    r_rc=0
    apply_one_proposal "$r_from" "$r_to" || r_rc=$?
    case "$r_rc" in
      0)
        record_apply_outcome "$r_from" "$r_to" "applied" "$run_id" "$r_fp" "$r_tp" "$r_conf" "$r_rat" "$r_ev" >/dev/null \
          || { log_error "retry: applied $r_from -> $r_to but FAILED to record (self-heals next run)"; notify_failure "weave retry: applied $r_from -> $r_to but ledger record failed"; }
        ;;
      2)
        record_apply_outcome "$r_from" "$r_to" "stale" "$run_id" "$r_fp" "$r_tp" "$r_conf" "$r_rat" "$r_ev" >/dev/null \
          || log_error "retry: failed to record stale $r_from -> $r_to"
        ;;
      3)
        record_apply_outcome "$r_from" "$r_to" "failed-concurrency" "$run_id" "$r_fp" "$r_tp" "$r_conf" "$r_rat" "$r_ev" >/dev/null \
          || log_error "retry: failed to record concurrency $r_from -> $r_to"
        ;;
      *)
        r_final=$(record_apply_outcome "$r_from" "$r_to" "failed-hard" "$run_id" "$r_fp" "$r_tp" "$r_conf" "$r_rat" "$r_ev") \
          || log_error "retry: failed to record hard-fail $r_from -> $r_to"
        if [[ "$r_final" == "apply-failed-permanent" ]]; then
          notify_failure "weave: $r_from -> $r_to hard-failed $MAX_RETRY_COUNT times; marked apply-failed-permanent"
        fi
        ;;
    esac
  done < <(echo "$retry_only" | jq -c '.[]')
  return 0
}

# auto_apply_proposals sets two globals and returns 0 on success, non-zero on a fatal
# abort. On abort, AUTO_APPLY_REMAINING holds the proposals that were NOT applied (so
# the caller writes a digest of exactly those -- never re-queuing an already-applied
# pair). NOT run through command substitution -- $(...) clears errexit (Codex finding).
AUTO_APPLY_SUMMARY=""
AUTO_APPLY_REMAINING="[]"
auto_apply_proposals() {
  local scored="$1" run_id="$2"
  AUTO_APPLY_SUMMARY=""
  AUTO_APPLY_REMAINING="$scored"   # default fallback set = everything, until we apply

  # Preflight: if the ledger is unwritable, abort before mutating any page. The full
  # scored set then falls back to a digest cleanly (nothing was applied).
  if ! ledger_writable; then
    log_error "auto-apply: ledger not writable ($LEDGER_FILE) -- aborting before any apply"
    return 1
  fi

  # Open apply-failed pairs are retried deterministically in a SEPARATE loop after the
  # new-proposal pass (below), bypassing the confidence threshold and the exclusion
  # filter -- they are already-approved high-confidence pairs that failed to apply, so
  # they must progress to apply-failed-permanent, never be held or dropped.
  local retry_pairs
  retry_pairs=$(load_retry_pairs)

  local filtered before after dropped
  # Fatal if the exclusion filter fails -- applying an unfiltered set could override
  # a prior rejected/deferred/held decision.
  if ! filtered=$(filter_excluded_proposals "$scored"); then
    log_error "auto-apply: exclusion filter failed -- aborting auto-apply"
    return 1
  fi
  before=$(echo "$scored" | jq 'length')
  after=$(echo "$filtered" | jq 'length')
  dropped=$(( before - after ))
  (( dropped > 0 )) && log_info "Dropped $dropped already-excluded pair(s) before auto-apply"

  # Pull apply-failed (retry) pairs OUT of the new-proposal set. apply-failed is not
  # excluded by get_ledger_exclusions, so a below-threshold re-proposal of an open
  # apply-failed pair would otherwise survive the filter and be rewritten as
  # held-low-confidence -- stalling the retry counter and the permanent alert. Retry
  # pairs are owned exclusively by the retry loop (no threshold, no held path).
  local filtered_new
  filtered_new=$(jq -n --argjson f "$filtered" --argjson r "$retry_pairs" '
    def pkey: ([(.from_slug | ascii_downcase), (.to_slug | ascii_downcase)] | @json);
    ([ $r[] | { key: pkey, value: true } ] | from_entries) as $rk
    | [ $f[] | select(($rk[pkey] // false) | not) ]
  ')
  AUTO_APPLY_REMAINING="$filtered_new"   # excluded + retry pairs are not digest-fallback material

  local applied=0 held=0 stale=0 failed=0 permanent=0 idx=0
  local row from_slug to_slug from_path to_path conf rationale evidence rc final
  while IFS= read -r row; do
    [[ -z "$row" ]] && continue
    from_slug=$(echo "$row" | jq -r '.from_slug')
    to_slug=$(echo "$row" | jq -r '.to_slug')
    from_path=$(echo "$row" | jq -r '.from_path // ""')
    to_path=$(echo "$row" | jq -r '.to_path // ""')
    conf=$(echo "$row" | jq -r '.confidence // 0')
    rationale=$(echo "$row" | jq -r '.rationale // ""')
    evidence=$(echo "$row" | jq -r '.evidence_span // ""')

    # Below-threshold: record held-low-confidence, never apply. A failed record here
    # is fatal -- abort with the un-applied remainder (this pair onward).
    if ! awk -v c="$conf" -v t="$WEAVE_AUTO_APPLY_THRESHOLD" 'BEGIN { exit !(c >= t) }'; then
      if ! record_apply_outcome "$from_slug" "$to_slug" "held" "$run_id" "$from_path" "$to_path" "$conf" "$rationale" "$evidence" >/dev/null; then
        log_error "auto-apply: failed to record held pair $from_slug -> $to_slug -- aborting"
        AUTO_APPLY_REMAINING=$(echo "$filtered_new" | jq -c ".[$idx:]")
        return 1
      fi
      held=$(( held + 1 ))
      idx=$(( idx + 1 ))
      continue
    fi

    rc=0
    apply_one_proposal "$from_slug" "$to_slug" || rc=$?
    case "$rc" in
      0)
        # The link is now in place. If the audit row cannot be written, do NOT report
        # a clean success: alert and abort. The fallback remainder starts AFTER this
        # already-applied pair (idx+1) so we never re-queue an applied link; the page
        # mutation is durable and wikilink-exclusion will keep it from re-proposing.
        if ! record_apply_outcome "$from_slug" "$to_slug" "applied" "$run_id" "$from_path" "$to_path" "$conf" "$rationale" "$evidence" >/dev/null; then
          log_error "auto-apply: applied $from_slug -> $to_slug but FAILED to record ledger row -- aborting"
          notify_failure "weave: applied $from_slug -> $to_slug but could not write the ledger audit row (run $run_id)"
          # INCLUDE the current pair (idx:) in the fallback. The link is on the page but
          # has no audit row; the idempotent apply on the fallback digest will record
          # `applied` on retry. Excluding it (idx+1) would leave a permanent audit gap
          # because wikilink-exclusion drops the now-linked pair on future runs.
          AUTO_APPLY_REMAINING=$(echo "$filtered_new" | jq -c ".[$idx:]")
          return 1
        fi
        applied=$(( applied + 1 ))
        ;;
      2)
        # FROM page not found -- nothing mutated. Record is retry-irrelevant but still
        # part of the audit trail; a failure here is fatal (abort with remainder).
        if ! record_apply_outcome "$from_slug" "$to_slug" "stale" "$run_id" "$from_path" "$to_path" "$conf" "$rationale" "$evidence" >/dev/null; then
          log_error "auto-apply: failed to record stale $from_slug -> $to_slug -- aborting"
          AUTO_APPLY_REMAINING=$(echo "$filtered_new" | jq -c ".[$idx:]")
          return 1
        fi
        stale=$(( stale + 1 ))
        log_info "Stale-renamed: $from_slug -> $to_slug (FROM page not found)"
        ;;
      3)
        # Concurrency race -- page not mutated. The retry depends on this ledger row,
        # so a failed record is fatal.
        if ! record_apply_outcome "$from_slug" "$to_slug" "failed-concurrency" "$run_id" "$from_path" "$to_path" "$conf" "$rationale" "$evidence" >/dev/null; then
          log_error "auto-apply: failed to record concurrency $from_slug -> $to_slug -- aborting"
          AUTO_APPLY_REMAINING=$(echo "$filtered_new" | jq -c ".[$idx:]")
          return 1
        fi
        failed=$(( failed + 1 ))
        log_info "Concurrency retry deferred to next run: $from_slug -> $to_slug"
        ;;
      *)
        # Hard failure -- page not mutated. The retry counter and the permanent-failure
        # alert DEPEND on this ledger write, so a failed record is fatal.
        if ! final=$(record_apply_outcome "$from_slug" "$to_slug" "failed-hard" "$run_id" "$from_path" "$to_path" "$conf" "$rationale" "$evidence"); then
          log_error "auto-apply: failed to record hard-fail $from_slug -> $to_slug -- aborting"
          AUTO_APPLY_REMAINING=$(echo "$filtered_new" | jq -c ".[$idx:]")
          return 1
        fi
        failed=$(( failed + 1 ))
        if [[ "$final" == "apply-failed-permanent" ]]; then
          permanent=$(( permanent + 1 ))
          notify_failure "weave: $from_slug -> $to_slug hard-failed $MAX_RETRY_COUNT times; marked apply-failed-permanent"
        fi
        log_error "auto-apply failed for $from_slug -> $to_slug (rc=$rc, status=$final)"
        ;;
    esac
    idx=$(( idx + 1 ))
  done < <(echo "$filtered_new" | jq -c '.[]')

  AUTO_APPLY_REMAINING="[]"   # full success -- nothing to fall back
  AUTO_APPLY_SUMMARY=$(printf 'applied=%d held=%d stale=%d failed=%d permanent=%d dropped=%d' \
    "$applied" "$held" "$stale" "$failed" "$permanent" "$dropped")
  return 0
}

# --- Commands ---

cmd_run() {
  local is_dry_run="${1:-false}"
  acquire_lock
  recover_ledger

  local run_id
  run_id=$(date +%Y%m%d-%H%M)

  # Retry the open apply-failed queue FIRST, before any Kimi-dependent work. This runs
  # every real (non-dry) auto-apply run regardless of whether the worker later succeeds,
  # so a degraded Kimi/token/schema condition can never stall a stuck pair's progress
  # toward apply-failed-permanent.
  if [[ "$is_dry_run" != "true" ]] && auto_apply_enabled; then
    process_retry_queue "$run_id"
  fi

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

  # Build page bundle for the text worker
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
    notify_failure "weave: input ${estimated_tokens} est-tokens > budget ${MAX_TOKENS_BUDGET}. Full-body single-prompt has reached its ceiling. Ship the scaling fix tracked in wiki/concepts/feature_weave-embedding-prefilter.md (try summary-view bundling before embeddings). Do not just raise the cap again -- ~140 pages is Kimi's real context wall."
    append_metrics "$page_count" 0 0 0 "$input_bytes" "token_budget_exceeded" "$run_id"
    exit 1
  fi

  # Call the text worker
  log_info "Calling Kimi K2.7 Code..."
  local start_ms end_ms duration_ms
  start_ms=$(python3 -c 'import time; print(int(time.time()*1000))')
  local response
  local weave_worker="$SCRIPT_DIR/minimax-weave.sh"
  if [[ ! -x "$weave_worker" ]]; then
    notify_failure "weave: missing minimax-weave.sh compatibility wrapper for Kimi"
    append_metrics "$page_count" 0 0 0 "$input_bytes" "worker_wrapper_missing" "$run_id"
    exit 1
  fi
  if ! response=$(printf '%s' "$input_json" | "$weave_worker"); then
    notify_failure "weave: ${K2B_TEXT_WORKER_NAME} call failed"
    append_metrics "$page_count" 0 0 0 "$input_bytes" "$K2B_TEXT_WORKER_ERROR_KEY" "$run_id"
    exit 1
  fi
  end_ms=$(python3 -c 'import time; print(int(time.time()*1000))')
  duration_ms=$(( end_ms - start_ms ))

  # Validate schema
  if ! validate_response_schema "$response"; then
    mkdir -p "$(dirname "$ERRORS_FILE")"
    printf '=== %s run=%s ===\n%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$run_id" "$response" >> "$ERRORS_FILE"
    notify_failure "weave: ${K2B_TEXT_WORKER_NAME} returned invalid JSON schema. See $ERRORS_FILE"
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
    # Retry queue already ran at the top of cmd_run, so a clean worker run is a true no-op.
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

  # Proposals that the legacy digest path writes. Default = everything; on an
  # auto-apply fallback this is narrowed to only the un-applied remainder.
  local digest_proposals="$scored"

  if [[ "$is_dry_run" == "true" ]]; then
    log_info "DRY RUN -- would write $top_count proposals:"
    echo "$scored" | jq -r '.[] | "  [\(.utility_score // 0)] \(.from_slug) -> \(.to_slug) (conf=\(.confidence)) -- \(.rationale)"'
    return 0
  fi

  # --- Auto-apply path (gated): apply directly, no digest, no human review ---
  # Called WITHOUT command substitution so a mid-function failure propagates; on a
  # fatal failure we fall through to the digest path so nothing is silently lost.
  if auto_apply_enabled; then
    log_info "Auto-apply enabled (policy ledger grants k2b-weave/crosslink_apply autonomy)"
    if auto_apply_proposals "$scored" "$run_id"; then
      local summary="$AUTO_APPLY_SUMMARY"
      local n_applied
      n_applied=$(echo "$summary" | grep -oE 'applied=[0-9]+' | cut -d= -f2)

      append_metrics "$page_count" "$(echo "$response" | jq 'length')" "$top_count" "$duration_ms" "$input_bytes" "" "$run_id"
      append_log_line "[weave] $run_id -- auto-apply ($summary)"

      mkdir -p "$K2B_VAULT/wiki/context"
      printf '%s\tk2b-weave\t%s\tweave run: %s proposals, %s applied (auto)\n' "$(date +%Y-%m-%d)" "$(echo $RANDOM | md5sum 2>/dev/null | head -c 8 || echo $RANDOM)" "$top_count" "${n_applied:-0}" >> "$K2B_VAULT/wiki/context/skill-usage-log.tsv" 2>/dev/null || true

      log_info "Done (auto-apply). $summary"
      return 0
    fi
    log_error "auto-apply aborted mid-run -- falling back to digest so proposals are not lost"
    notify_failure "weave: auto-apply aborted for run $run_id; wrote review digest as fallback"
    # Only the un-applied remainder goes to the fallback digest -- never re-queue an
    # already-applied pair.
    digest_proposals="$AUTO_APPLY_REMAINING"
    # fall through to the digest path below
  fi

  # --- Legacy digest path: write a review digest and wait for Keith ---
  local digest_count
  digest_count=$(echo "$digest_proposals" | jq 'length' 2>/dev/null || echo 0)
  if (( digest_count == 0 )); then
    log_info "No proposals to write to a digest (nothing pending after auto-apply)."
    append_metrics "$page_count" "$(echo "$response" | jq 'length')" "$top_count" "$duration_ms" "$input_bytes" "" "$run_id"
    append_log_line "[weave] $run_id -- auto-apply fallback, no remainder to digest"
    return 0
  fi
  local today_short
  today_short=$(date +%Y-%m-%d_%H%M)
  local digest_path="$REVIEW_DIR/crosslinks_${today_short}.md"
  mkdir -p "$REVIEW_DIR"
  write_digest "$digest_path" "$run_id" "$digest_proposals"
  log_info "Wrote digest: $digest_path"

  # Log proposals to ledger
  append_proposals_to_ledger "$digest_proposals" "$run_id"

  # Metrics and log
  append_metrics "$page_count" "$(echo "$response" | jq 'length')" "$top_count" "$duration_ms" "$input_bytes" "" "$run_id"
  append_log_line "[weave] $run_id -- $digest_count proposals in review/$(basename "$digest_path")"

  # Skill usage log
  mkdir -p "$K2B_VAULT/wiki/context"
  printf '%s\tk2b-weave\t%s\tweave run: %s proposals, 0 applied\n' "$(date +%Y-%m-%d)" "$(echo $RANDOM | md5sum 2>/dev/null | head -c 8 || echo $RANDOM)" "$digest_count" >> "$K2B_VAULT/wiki/context/skill-usage-log.tsv" 2>/dev/null || true

  log_info "Done. $digest_count proposals in $(basename "$digest_path")"
}

cmd_apply() {
  local digest_file="$1"
  if [[ ! -f "$digest_file" ]]; then
    log_error "Digest not found: $digest_file"
    exit 1
  fi
  acquire_lock
  recover_ledger

  # Preflight: never mutate a page if the ledger cannot record the outcome. Otherwise a
  # checked row could be applied while its ledger marker silently no-ops, and the digest
  # would then be deleted -- a page change with no durable record (Codex review finding).
  if ! ledger_writable; then
    log_error "apply: ledger not writable ($LEDGER_FILE) -- refusing to apply; digest preserved"
    return 1
  fi

  local applied=0 rejected=0 deferred=0 stale=0 failed=0

  local decisions
  decisions=$(parse_decision_table "$digest_file")

  if [[ -z "$decisions" ]]; then
    log_info "No decisions found in digest. Leaving as-is."
    return 0
  fi

  local from_slug to_slug decision rc final
  while IFS=$'\t' read -r from_slug to_slug decision; do
    case "$decision" in
      check)
        set +e
        apply_one_proposal "$from_slug" "$to_slug"
        rc=$?
        set -e
        case "$rc" in
          0)
            # Route through the upsert recorder, NOT mark_ledger_applied: that helper
            # only updates a `pending` row and reports success even on no-match, so an
            # apply-failed retry row (or a drifted/hand-made digest) would apply the link,
            # leave the ledger stale, and still delete the digest. The recorder updates
            # any-status row (or appends one), so a durable applied row always exists.
            if record_apply_outcome "$from_slug" "$to_slug" "applied" "" "" "" "0" "" "" >/dev/null; then
              applied=$(( applied + 1 ))
            else
              log_error "apply: applied $from_slug -> $to_slug but FAILED to record -- preserving digest"
              failed=$(( failed + 1 ))
            fi
            ;;
          2)
            if record_apply_outcome "$from_slug" "$to_slug" "stale" "" "" "" "0" "" "" >/dev/null; then
              stale=$(( stale + 1 ))
            else
              log_error "apply: failed to record stale $from_slug -> $to_slug -- preserving digest"
              failed=$(( failed + 1 ))
            fi
            log_info "Stale-renamed: $from_slug -> $to_slug (FROM page not found)"
            ;;
          3)
            # Concurrency race: retryable, not a hard failure. Mark apply-failed
            # (non-suppressed) so a later run retries; preserve the digest.
            record_apply_outcome "$from_slug" "$to_slug" "failed-concurrency" "" "" "" "0" "" "" >/dev/null
            failed=$(( failed + 1 ))
            log_info "Concurrency retry: $from_slug -> $to_slug (will retry)"
            ;;
          *)
            # Hard failure: record (with retry accumulation) instead of leaving the
            # row pending, and count it so the digest is preserved below.
            final=$(record_apply_outcome "$from_slug" "$to_slug" "failed-hard" "" "" "" "0" "" "")
            failed=$(( failed + 1 ))
            log_error "apply failed for $from_slug -> $to_slug (rc=$rc, status=$final)"
            ;;
        esac
        ;;
      x)
        # Upsert recorder (not mark_ledger_rejected) so a missing/non-pending row does
        # not silently no-op and discard a human rejection while deleting the digest.
        if record_apply_outcome "$from_slug" "$to_slug" "rejected" "" "" "" "0" "" "" >/dev/null; then
          rejected=$(( rejected + 1 ))
        else
          log_error "apply: failed to record rejection $from_slug -> $to_slug -- preserving digest"
          failed=$(( failed + 1 ))
        fi
        ;;
      defer|"")
        if record_apply_outcome "$from_slug" "$to_slug" "deferred" "" "" "" "0" "" "" >/dev/null; then
          deferred=$(( deferred + 1 ))
        else
          log_error "apply: failed to record defer $from_slug -> $to_slug -- preserving digest"
          failed=$(( failed + 1 ))
        fi
        ;;
      *)
        log_info "Unknown decision '$decision' for $from_slug -> $to_slug, treating as defer"
        if record_apply_outcome "$from_slug" "$to_slug" "deferred" "" "" "" "0" "" "" >/dev/null; then
          deferred=$(( deferred + 1 ))
        else
          log_error "apply: failed to record defer $from_slug -> $to_slug -- preserving digest"
          failed=$(( failed + 1 ))
        fi
        ;;
    esac
  done <<< "$decisions"

  append_log_line "[weave-apply] $(basename "$digest_file") -- $applied applied, $rejected rejected, $deferred deferred, $stale stale-renamed, $failed failed"
  log_info "Applied $applied, rejected $rejected, deferred $deferred, stale $stale, failed $failed"

  # PRESERVE the digest and exit non-zero if any checked row failed, so the approved
  # decision record is never lost on a transient error (Codex finding: digest must
  # not be deleted unconditionally). Only delete on a clean pass.
  if (( failed > 0 )); then
    log_error "$failed checked row(s) failed -- preserving digest $digest_file for retry"
    return 1
  fi
  rm -f "$digest_file"
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
