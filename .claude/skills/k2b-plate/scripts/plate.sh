#!/usr/bin/env bash
# /plate -- read pending state from canonical sources
# Per feature_pending-discipline (shipped 2026-05-16): read-only aggregator over
# wiki/concepts/feature_*.md (pending-action frontmatter), wiki/context/reminders.md,
# raw/sessions/*_handoff_*.md, K2Bi Resume Card, concepts/index.md lanes,
# self_improve_requests.md / self_improve_errors.md.

set -uo pipefail

VAULT="${K2B_VAULT_PATH:-$HOME/Projects/K2B-Vault}"
K2BI="${K2BI_VAULT_PATH:-$HOME/Projects/K2Bi-Vault}"
MEMORY="${K2B_MEMORY_DIR:-$HOME/.claude/projects/-Users-keithmbpm2-Projects-K2B/memory}"

# Set K2B_PLATE_DEBUG=1 to show awk/grep errors that are otherwise suppressed.
# By default we swallow errors to keep the dashboard clean -- but this hides
# diagnostics when a feature note's frontmatter is malformed or a vault file
# is missing. Debug mode is the escape hatch for "why isn't X showing up?".
if [[ -n "${K2B_PLATE_DEBUG:-}" ]]; then
  ERR_REDIRECT=""
else
  ERR_REDIRECT="2>/dev/null"
fi

# --- helpers ---
# Use index()-based matching instead of regex to avoid BSD awk's \| illegal-primary error.

# Extract single-line YAML scalar from frontmatter only
# Usage: fm_get <file> <key>
fm_get() {
  local file="$1" key="$2"
  awk -v key="$key" '
    /^---$/ { fm++; if (fm==2) exit; next }
    fm==1 && index($0, key ":") == 1 && index($0, "|") != length($0) - length(key) - 1 - match($0, /\|/) {
      sub("^" key ":[[:space:]]*", "", $0)
      # Strip surrounding quotes if present
      gsub(/^"|"$/, "", $0)
      print
      exit
    }
  ' "$file" 2>/dev/null
}

# Simpler version: just extract the line content after "key:"
fm_get_scalar() {
  local file="$1" key="$2"
  awk -v key="$key" '
    /^---$/ { fm++; if (fm==2) exit; next }
    fm==1 && index($0, key ":") == 1 {
      val = substr($0, length(key) + 2)
      sub(/^[[:space:]]+/, "", val)
      # If value starts with "|", it is a block scalar -- skip (different reader)
      if (substr(val, 1, 1) == "|") next
      gsub(/^"|"$/, "", val)
      print val
      exit
    }
  ' "$file" 2>/dev/null
}

# Extract pipe-block YAML scalar (e.g. `pending-action: |`) from frontmatter only
# Usage: fm_get_block <file> <key>
# Defensive: caps block at 50 lines to prevent body-content leakage if frontmatter
# is malformed (no closing ---, weird next-key formatting, etc.). The fm==1 guard
# is the primary defense; the line cap is belt-and-suspenders.
fm_get_block() {
  local file="$1" key="$2"
  awk -v key="$key" '
    /^---$/ { fm++; if (fm==2) exit; next }
    fm==1 {
      if (in_block) {
        # End block at next top-level YAML key (alpha start, colon)
        if (match($0, /^[a-zA-Z_][a-zA-Z0-9_-]*:/) == 1) {
          in_block = 0
        } else if (block_lines >= 50) {
          # Defensive cap -- legitimate pending-action blocks are < 10 lines.
          # If we hit 50 the frontmatter is malformed; stop printing.
          in_block = 0
        } else {
          print
          block_lines++
          next
        }
      }
      # Start block if line is "key:" followed by optional spaces and "|"
      if (index($0, key ":") == 1) {
        rest = substr($0, length(key) + 2)
        sub(/^[[:space:]]+/, "", rest)
        if (substr(rest, 1, 1) == "|") {
          in_block = 1
          block_lines = 0
        }
      }
    }
  ' "$file" 2>/dev/null
}

# Skip sync-conflict files
skip_conflict() {
  [[ "$(basename "$1")" == *.sync-conflict-* ]]
}

# Date 7 days ago (portable BSD/GNU)
date_7d_ago() {
  date -v-7d +%Y-%m-%d 2>/dev/null || date -d '7 days ago' +%Y-%m-%d 2>/dev/null
}

# --- Section 1: Needs your decision now ---

echo "## ⚠ Needs your decision now"
echo ""

has_anything=0

# 1a. Pending-actions on in-progress features
for f in "$VAULT"/wiki/concepts/feature_*.md; do
  [[ -f "$f" ]] || continue
  skip_conflict "$f" && continue
  status=$(fm_get_scalar "$f" "status")
  [[ "$status" == "in-progress" ]] || continue
  pa=$(fm_get_block "$f" "pending-action")
  [[ -n "$pa" ]] || continue
  has_anything=1
  slug=$(basename "$f" .md)
  since=$(fm_get_scalar "$f" "pending-action-since")
  echo "### ${slug} (pending since ${since:-?})"
  echo "$pa" | sed 's/^[[:space:]]*//'
  echo ""
done

# 1b. Open reminders
reminders="$VAULT/wiki/context/reminders.md"
if [[ -f "$reminders" ]]; then
  open_reminder_lines=$(grep -E '^- \[open\]' "$reminders" 2>/dev/null || true)
  if [[ -n "$open_reminder_lines" ]]; then
    has_anything=1
    echo "### Open reminders"
    echo "$open_reminder_lines"
    echo ""
  fi
fi

# 1c. Recent open handoffs (last 7 days)
any_h=0
if [[ -d "$VAULT/raw/sessions" ]]; then
  while IFS= read -r h; do
    [[ -n "$h" ]] || continue
    skip_conflict "$h" && continue
    status=$(fm_get_scalar "$h" "status")
    [[ "$status" == "open" ]] || continue
    if [[ $any_h -eq 0 ]]; then
      echo "### Recent open handoffs (last 7 days)"
      any_h=1
      has_anything=1
    fi
    slug=$(basename "$h" .md)
    title=$(grep -m1 '^# ' "$h" 2>/dev/null | sed 's/^# //' || true)
    linked=$(fm_get_scalar "$h" "linked-feature")
    echo "- **${slug}** ${linked:+(${linked})}"
    [[ -n "$title" ]] && echo "  ${title}"
  done < <(find "$VAULT/raw/sessions/" -name '*_handoff_*.md' -mtime -7 2>/dev/null | sort -r)
  [[ $any_h -eq 1 ]] && echo ""
fi

if [[ $has_anything -eq 0 ]]; then
  echo "_(nothing pending; clear plate)_"
  echo ""
fi

# --- Section 2: K2Bi PM current checkpoint ---

k2bi_index="$K2BI/wiki/planning/index.md"
if [[ -f "$k2bi_index" ]]; then
  echo "## 🎩 K2Bi PM current checkpoint"
  echo ""
  awk '
    /^> \*\*K2B PM checkpoint/ { in_cp=1; line_count=0 }
    in_cp { print; line_count++ }
    in_cp && line_count >= 30 { exit }
    in_cp && /^> ---$/ && line_count > 1 { exit }
  ' "$k2bi_index"
  echo ""
fi

# --- Section 3: Recently shipped (last 7 days) ---

echo "## ✅ Recently shipped (last 7 days)"
echo ""
cutoff=$(date_7d_ago)
shipped_lines=$(grep -E '^\| \[\[Shipped/feature_' "$VAULT"/wiki/concepts/index.md 2>/dev/null | head -20 | while IFS= read -r line; do
  # Extract date by regex (avoid \| split issues with escaped wikilinks)
  date_col=$(echo "$line" | grep -oE '20[0-9]{2}-[0-9]{2}-[0-9]{2}' | head -1)
  if [[ -n "$date_col" && -n "$cutoff" && ! "$date_col" < "$cutoff" ]]; then
    slug=$(echo "$line" | grep -oE 'feature_[a-z0-9-]+' | head -1)
    # Extract notes column: everything after the date, before the trailing |
    note=$(echo "$line" | sed -E "s/.*${date_col}[[:space:]]*\|[[:space:]]*//" | sed 's/[[:space:]]*|[[:space:]]*$//' | cut -c1-150)
    echo "- **${slug}** (${date_col}): ${note}..."
  fi
done)
if [[ -n "$shipped_lines" ]]; then
  echo "$shipped_lines"
else
  echo "_(none in last 7 days)_"
fi
echo ""

# --- Section 4: In Progress lanes ---

echo "## 🚧 In Progress lanes"
echo ""
awk '
  /^## In Progress/ { in_section=1; next }
  /^## / && in_section { exit }
  in_section && (/^\| \[\[feature_/ || /^\| \[\[project_/) {
    if (match($0, /\[\[[^]]+\]\]/)) {
      name = substr($0, RSTART+2, RLENGTH-4)
      # Strip alias including any escaped \|
      sub(/\\?\|.*/, "", name)
      # Get last date-like column
      n = split($0, parts, "|")
      # Walk backwards to find a date column
      for (i = n; i >= 1; i--) {
        col = parts[i]; gsub(/^[[:space:]]+|[[:space:]]+$/, "", col)
        if (match(col, /^20[0-9][0-9]-[0-9][0-9]-[0-9][0-9]$/)) {
          date = col
          break
        }
      }
      print "- **" name "** (updated " (date ? date : "?") ")"
    }
  }
' "$VAULT"/wiki/concepts/index.md
echo ""

# --- Section 5: Memory flags ---

echo "## 🧠 Memory flags"
echo ""
requests="$MEMORY/self_improve_requests.md"
errors="$MEMORY/self_improve_errors.md"
mem_has=0
if [[ -f "$requests" ]]; then
  open_rids=$(grep -E '^## R-' "$requests" 2>/dev/null | head -5 || true)
  if [[ -n "$open_rids" ]]; then
    echo "**Open R-IDs (top 5):**"
    echo "$open_rids" | sed 's/^## /- /'
    echo ""
    mem_has=1
  fi
fi
if [[ -f "$errors" ]]; then
  recent_eids=$(grep -E '^## E-2026-' "$errors" 2>/dev/null | head -3 || true)
  if [[ -n "$recent_eids" ]]; then
    echo "**Recent E-IDs (top 3):**"
    echo "$recent_eids" | sed 's/^## /- /'
    echo ""
    mem_has=1
  fi
fi
[[ $mem_has -eq 0 ]] && echo "_(no R-IDs or E-IDs surfaced)_" && echo ""

# --- Section 6: Next Up + Backlog top 3 ---

echo "## 📅 Next Up + Backlog top 3"
echo ""
awk '
  BEGIN { bl_count = 0 }
  /^## Next Up/ { in_next=1; in_bl=0; next }
  /^## Backlog/ { in_bl=1; in_next=0; bl_count=0; next }
  /^## / && (in_next || in_bl) { in_next=0; in_bl=0 }
  in_next && (/^\| \[\[feature_/ || /^\| \[\[project_/ || /^\| feature_/) {
    name = ""
    if (match($0, /\[\[[^]]+\]\]/)) {
      name = substr($0, RSTART+2, RLENGTH-4)
      sub(/\\?\|.*/, "", name)
    } else if (match($0, /feature_[a-zA-Z0-9_-]+/)) {
      name = substr($0, RSTART, RLENGTH)
    }
    if (name) print "- **(next)** " name
  }
  in_bl && (/^\| \[\[feature_/ || /^\| \[\[project_/ || /^\| feature_/) {
    if (bl_count < 3) {
      bl_count++
      name = ""
      if (match($0, /\[\[[^]]+\]\]/)) {
        name = substr($0, RSTART+2, RLENGTH-4)
        sub(/\\?\|.*/, "", name)
      } else if (match($0, /feature_[a-zA-Z0-9_-]+/)) {
        name = substr($0, RSTART, RLENGTH)
      }
      if (name) print "- **(backlog " bl_count ")** " name
    }
  }
' "$VAULT"/wiki/concepts/index.md
echo ""

# --- Footer ---

echo "---"
echo "_Read sources per feature_pending-discipline (2026-05-16). To capture a new pending item: set \`pending-action:\` + \`pending-action-since:\` in the feature note frontmatter, append to \`wiki/context/reminders.md\`, or write \`raw/sessions/YYYY-MM-DD_handoff_<slug>.md\`._"
