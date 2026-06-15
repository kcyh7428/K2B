#!/usr/bin/env bash
# /plate -- read pending state from canonical sources
# Per feature_pending-discipline (shipped 2026-05-16): read-only aggregator over
# wiki/concepts/feature_*.md (pending-action frontmatter), wiki/context/reminders.md,
# raw/sessions/*_handoff_*.md, K2Bi Resume Card, concepts/index.md lanes,
# self_improve_requests.md / self_improve_errors.md.

set -uo pipefail

VAULT="${K2B_VAULT_PATH:-$HOME/Projects/K2B-Vault}"
K2BI="${K2BI_VAULT_PATH:-$HOME/Projects/K2Bi-Vault}"
MEMORY="${K2B_MEMORY_DIR:-$HOME/Projects/K2B-Vault/System/memory}"

# Set K2B_PLATE_DEBUG=1 to show awk/grep errors that are otherwise suppressed.
# By default we swallow errors to keep the dashboard clean -- but this hides
# diagnostics when a feature note's frontmatter is malformed or a vault file
# is missing. Debug mode is the escape hatch for "why isn't X showing up?".
quiet() {
  if [[ -n "${K2B_PLATE_DEBUG:-}" ]]; then
    "$@"
  else
    "$@" 2>/dev/null
  fi
}

# --- helpers ---
# Use index()-based matching instead of regex to avoid BSD awk's \| illegal-primary error.

# Extract single-line YAML scalar from frontmatter only
# Usage: fm_get <file> <key>
fm_get() {
  local file="$1" key="$2"
  quiet awk -v key="$key" '
    /^---$/ { fm++; if (fm==2) exit; next }
    fm==1 && index($0, key ":") == 1 && index($0, "|") != length($0) - length(key) - 1 - match($0, /\|/) {
      sub("^" key ":[[:space:]]*", "", $0)
      # Strip surrounding quotes if present
      gsub(/^"|"$/, "", $0)
      print
      exit
    }
  ' "$file"
}

# Simpler version: just extract the line content after "key:"
fm_get_scalar() {
  local file="$1" key="$2"
  quiet awk -v key="$key" '
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
  ' "$file"
}

# Extract pipe-block YAML scalar (e.g. `pending-action: |`) from frontmatter only
# Usage: fm_get_block <file> <key>
# Defensive: caps block at 50 lines to prevent body-content leakage if frontmatter
# is malformed (no closing ---, weird next-key formatting, etc.). The fm==1 guard
# is the primary defense; the line cap is belt-and-suspenders.
fm_get_block() {
  local file="$1" key="$2"
  quiet awk -v key="$key" '
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
  ' "$file"
}

# Skip sync-conflict files
skip_conflict() {
  [[ "$(basename "$1")" == *.sync-conflict-* ]]
}

# Date 7 days ago (portable BSD/GNU)
date_7d_ago() {
  quiet date -v-7d +%Y-%m-%d || quiet date -d '7 days ago' +%Y-%m-%d
}

# --- Section 1: Needs your decision now ---

echo "## ⚠ Needs your decision now"
echo ""

has_anything=0

# 1a. Pending-actions on in-progress features (feature_*.md) and active projects (project_*.md)
for f in \
    "$VAULT"/wiki/concepts/feature_*.md \
    "$VAULT"/wiki/concepts/project_*.md \
    "$VAULT"/wiki/projects/project_*.md; do
  [[ -f "$f" ]] || continue
  skip_conflict "$f" && continue
  status=$(fm_get_scalar "$f" "status")
  # feature_*.md uses status: in-progress; project_*.md uses status: active.
  # Both signal "currently being worked" and should surface pending-actions.
  [[ "$status" == "in-progress" || "$status" == "active" ]] || continue
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
  open_reminder_lines=$(quiet grep -E '^- \[open\]' "$reminders" || true)
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
    title=$(quiet grep -m1 '^# ' "$h" | sed 's/^# //' || true)
    linked=$(fm_get_scalar "$h" "linked-feature")
    echo "- **${slug}** ${linked:+(${linked})}"
    [[ -n "$title" ]] && echo "  ${title}"
  done < <(quiet find "$VAULT/raw/sessions/" -name '*_handoff_*.md' -mtime -7 | sort -r)
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
  quiet awk '
    /^> \*\*K2Bi? PM checkpoint/ { in_cp=1; line_count=0 }
    in_cp { print; line_count++ }
    in_cp && line_count >= 30 { exit }
    in_cp && /^(> )?---$/ && line_count > 1 { exit }
  ' "$k2bi_index"
  echo ""
fi

# --- Section 3: Recently shipped (last 7 days) ---

echo "## ✅ Recently shipped (last 7 days)"
echo ""
cutoff=$(date_7d_ago)
shipped_lines=$(quiet grep -E '^\| \[\[Shipped/feature_' "$VAULT"/wiki/concepts/index.md | head -20 | while IFS= read -r line; do
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
python3 - "$VAULT/wiki/concepts/index.md" <<'PY'
from pathlib import Path
import os
import re
import sys
import unicodedata

path = Path(sys.argv[1])

def display_width(text: str) -> int:
    width = 0
    for ch in text:
        if unicodedata.combining(ch):
            continue
        width += 2 if unicodedata.east_asian_width(ch) in {"F", "W"} else 1
    return width

ZERO_WIDTH = {
    "\u200b", "\u200c", "\u200d", "\u200e", "\u200f",
    "\u202a", "\u202b", "\u202c", "\u202d", "\u202e",
    "\u2060", "\ufeff",
}

def strip_invisible(text: str) -> str:
    return "".join(ch for ch in text if ch not in ZERO_WIDTH)

def split_markdown_row(line: str) -> list[str]:
    cells = []
    buf = []
    bracket_depth = 0
    i = 0
    while i < len(line):
        pair = line[i:i + 2]
        if pair == "[[":
            bracket_depth += 1
            buf.append(pair)
            i += 2
            continue
        if pair == "]]":
            bracket_depth = max(0, bracket_depth - 1)
            buf.append(pair)
            i += 2
            continue
        ch = line[i]
        if ch == "|" and bracket_depth == 0:
            cells.append("".join(buf).strip())
            buf = []
        else:
            buf.append(ch)
        i += 1
    cells.append("".join(buf).strip())
    if cells and cells[0] == "":
        cells = cells[1:]
    if cells and cells[-1] == "":
        cells = cells[:-1]
    return cells

def truncate_display(text: str, max_width: int = 120) -> str:
    width = 0
    out = []
    for ch in text:
        ch_width = 0 if unicodedata.combining(ch) else (2 if unicodedata.east_asian_width(ch) in {"F", "W"} else 1)
        if width + ch_width > max_width - 3:
            suffix = "".join(out).rstrip()
            if " " in suffix:
                suffix = suffix.rsplit(" ", 1)[0].rstrip() or suffix
            return suffix + "..."
        out.append(ch)
        width += ch_width
    return "".join(out)

in_section = False
for line in path.read_text(encoding="utf-8").splitlines():
    if line.startswith("## In Progress"):
        in_section = True
        continue
    if in_section and line.startswith("## "):
        break
    if not in_section or not (line.startswith("| [[feature_") or line.startswith("| [[project_")):
        continue

    try:
        m = re.search(r"\[\[([^]]+)\]\]", line)
        if not m:
            continue
        name = re.sub(r"\\?\|.*", "", m.group(1))
        parts = split_markdown_row(line)
        status = " | ".join(parts[1:-3]).strip() if len(parts) >= 5 else (parts[1] if len(parts) > 1 else "")
        status = status.replace("|", "·")
        status = strip_invisible(status).strip()
        if display_width(status) > 120:
            status = truncate_display(status)
        date = next((p for p in reversed(parts) if re.fullmatch(r"20\d\d-\d\d-\d\d", p)), "?")
        if status:
            print(f"- **{name}** (updated {date}): {status}")
        else:
            print(f"- **{name}** (updated {date})")
    except Exception as exc:
        if "K2B_PLATE_DEBUG" in os.environ:
            print(f"plate: skipped malformed In Progress row: {exc}", file=sys.stderr)
PY
echo ""

# --- Section 5: Memory flags ---

echo "## 🧠 Memory flags"
echo ""
requests="$MEMORY/self_improve_requests.md"
errors="$MEMORY/self_improve_errors.md"
mem_has=0
if [[ -f "$requests" ]]; then
  open_rids=$(quiet grep -E '^## R-' "$requests" | head -5 || true)
  if [[ -n "$open_rids" ]]; then
    echo "**Open R-IDs (top 5):**"
    echo "$open_rids" | sed 's/^## /- /'
    echo ""
    mem_has=1
  fi
fi
if [[ -f "$errors" ]]; then
  recent_eids=$(quiet grep -E '^## E-2026-' "$errors" | head -3 || true)
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
