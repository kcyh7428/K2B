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
shipped_lines=$(python3 - "$VAULT/wiki/concepts/index.md" "$cutoff" <<'PY'
from pathlib import Path
import re
import sys
import unicodedata

path = Path(sys.argv[1])
cutoff = sys.argv[2] if len(sys.argv) > 2 else ""

def split_markdown_row(line: str) -> list[str]:
    cells = []
    buf = []
    wiki_depth = 0
    square_depth = 0
    paren_depth = 0
    escape = False
    i = 0
    while i < len(line):
        ch = line[i]
        if escape:
            buf.append(ch)
            escape = False
            i += 1
            continue
        if ch == "\\":
            buf.append(ch)
            escape = True
            i += 1
            continue

        pair = line[i:i + 2]
        if pair == "[[":
            wiki_depth += 1
            buf.append(pair)
            i += 2
            continue
        if pair == "]]":
            wiki_depth = max(0, wiki_depth - 1)
            buf.append(pair)
            i += 2
            continue

        if wiki_depth == 0:
            if ch == "[":
                square_depth += 1
            elif ch == "]" and square_depth:
                square_depth -= 1
            elif ch == "(" and square_depth == 0 and buf and buf[-1] == "]":
                paren_depth += 1
            elif ch == ")" and paren_depth:
                paren_depth -= 1

        if ch == "|" and wiki_depth == 0 and square_depth == 0 and paren_depth == 0:
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

def strip_link(cell: str) -> str:
    match = re.search(r"\[\[([^]]+)\]\]", cell)
    if not match:
        return cell.strip()
    raw = match.group(1)
    buf = []
    target = raw
    alias = ""
    escape = False
    for index, ch in enumerate(raw):
        if escape:
            buf.append(ch)
            escape = False
            continue
        if ch == "\\":
            buf.append(ch)
            escape = True
            continue
        if ch == "|":
            target = "".join(buf).strip()
            alias = raw[index + 1:].replace("\\|", "|").strip()
            break
        buf.append(ch)
    target_slug = target.rsplit("/", 1)[-1].strip()
    if re.fullmatch(r"feature_[a-z0-9-]+", alias):
        return alias
    return target_slug

def truncate_display(text: str, max_width: int = 150) -> str:
    text = unicodedata.normalize("NFC", text)
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

if not path.exists():
    sys.exit(0)

in_section = False
count = 0
for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
    if line.startswith("## Shipped"):
        in_section = True
        continue
    if in_section and line.startswith("## "):
        break
    if not in_section or not line.startswith("|"):
        continue
    if "|------" in line or "| Page " in line:
        continue

    parts = split_markdown_row(line)
    if len(parts) < 3:
        continue
    slug = strip_link(parts[0])
    if not slug.startswith("feature_"):
        continue
    date = parts[1].strip() if len(parts) > 1 and re.fullmatch(r"20\d\d-\d\d-\d\d", parts[1].strip()) else ""
    if not date or (cutoff and date < cutoff):
        continue
    note = parts[-1].strip()
    print(f"- **{slug}** ({date}): {truncate_display(note)}")
    count += 1
    if count >= 20:
        break
PY
)
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
    wiki_depth = 0
    square_depth = 0
    paren_depth = 0
    escape = False
    i = 0
    while i < len(line):
        ch = line[i]
        if escape:
            buf.append(ch)
            escape = False
            i += 1
            continue
        if ch == "\\":
            buf.append(ch)
            escape = True
            i += 1
            continue

        pair = line[i:i + 2]
        if pair == "[[":
            wiki_depth += 1
            buf.append(pair)
            i += 2
            continue
        if pair == "]]":
            wiki_depth = max(0, wiki_depth - 1)
            buf.append(pair)
            i += 2
            continue

        if wiki_depth == 0:
            if ch == "[":
                square_depth += 1
            elif ch == "]" and square_depth:
                square_depth -= 1
            elif ch == "(" and square_depth == 0 and buf and buf[-1] == "]":
                paren_depth += 1
            elif ch == ")" and paren_depth:
                paren_depth -= 1

        if ch == "|" and wiki_depth == 0 and square_depth == 0 and paren_depth == 0:
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
    text = unicodedata.normalize("NFC", text)
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
for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
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
        if len(parts) < 2:
            continue
        # In Progress rows treat the rightmost YYYY-MM-DD cell as Updated;
        # every cell between Page and Updated is status context to display.
        date_index = next((i for i in range(len(parts) - 1, 0, -1) if re.fullmatch(r"20\d\d-\d\d-\d\d", parts[i])), None)
        if date_index is not None:
            status = " | ".join(parts[1:date_index]).strip()
        else:
            status = " | ".join(parts[1:]).strip()
        status = status.replace("|", "·")
        status = strip_invisible(status).strip()
        if display_width(status) > 120:
            status = truncate_display(status)
        date = parts[date_index] if date_index is not None else "?"
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
