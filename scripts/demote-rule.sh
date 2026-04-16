#!/usr/bin/env bash
# scripts/demote-rule.sh
# Move one rule from active_rules.md to self_improve_learnings.md "Demoted Rules" section.
#
# Usage: demote-rule.sh <rule-number>
#
# Reads the Nth numbered rule (lines starting with "N. **" at column 0),
# including any continuation lines until the next numbered rule or "##" heading,
# removes it from active_rules.md, appends it to the "## Demoted Rules" section of
# self_improve_learnings.md with a demoted-date, then renumbers the remaining rules.
#
# Idempotent? No. Calling twice with the same number demotes whatever rule happens
# to be in slot N after the first demotion. The caller resolves rule selection from
# promote-learnings.py / select-lru-victim.py output before calling this.

set -euo pipefail

N="${1:?demote-rule: rule number required}"
ACTIVE="${K2B_ACTIVE_RULES_PATH:-$HOME/.claude/projects/-Users-keithmbpm2-Projects-K2B/memory/active_rules.md}"
LEARNINGS="${K2B_LEARNINGS_PATH:-$HOME/Projects/K2B-Vault/System/memory/self_improve_learnings.md}"
TS="$(date '+%Y-%m-%d')"

[ -f "$ACTIVE" ] || { echo "demote-rule: $ACTIVE not found" >&2; exit 2; }
[ -f "$LEARNINGS" ] || { echo "demote-rule: $LEARNINGS not found" >&2; exit 2; }

# Extract the full block of rule N.
# A rule block starts at "^N. **" and runs until the next "^\d+. **" or "^## " heading.
RULE_BLOCK=$(awk -v n="$N" '
  BEGIN { in_rule = 0 }
  {
    if ($0 ~ "^[0-9]+\\. \\*\\*") {
      if ($0 ~ "^" n "\\. \\*\\*") {
        in_rule = 1
        print
        next
      }
      if (in_rule) { exit }
    }
    if ($0 ~ "^## ") {
      if (in_rule) { exit }
    }
    if (in_rule) { print }
  }
' "$ACTIVE")

if [ -z "$RULE_BLOCK" ]; then
  echo "demote-rule: no rule $N in $ACTIVE" >&2
  exit 1
fi

# Step 1: Write to learnings FIRST (if this fails, active_rules is untouched).
if ! grep -q '^## Demoted Rules' "$LEARNINGS"; then
  printf '\n## Demoted Rules\n\n' >> "$LEARNINGS"
fi

# Insert demoted rule inside the ## Demoted Rules section (before the next ## heading
# or at EOF), not blindly at EOF.
DEMOTED_BLOCK=$(printf '### Demoted %s (rule %s)\n\ndemoted-date: %s\n\n%s' "$TS" "$N" "$TS" "$RULE_BLOCK")
TMP_L="$(mktemp)"
python3 - <<'PY' "$LEARNINGS" "$DEMOTED_BLOCK" "$TMP_L"
import sys
path, block, out_path = sys.argv[1], sys.argv[2], sys.argv[3]
text = open(path).read()
marker = "## Demoted Rules"
idx = text.find(marker)
if idx == -1:
    open(out_path, "w").write(text)
    sys.exit(0)
after_heading = idx + len(marker)
rest = text[after_heading:]
import re
m = re.search(r'\n## ', rest)
if m:
    insert_at = after_heading + m.start()
    result = text[:insert_at] + "\n\n" + block + "\n" + text[insert_at:]
else:
    result = text.rstrip() + "\n\n" + block + "\n"
open(out_path, "w").write(result)
PY
mv "$TMP_L" "$LEARNINGS"

# Step 2: Remove the block from active_rules.md (only after learnings write succeeded).
TMP="$(mktemp)"
awk -v n="$N" '
  BEGIN { skip = 0 }
  {
    if ($0 ~ "^[0-9]+\\. \\*\\*") {
      if ($0 ~ "^" n "\\. \\*\\*") { skip = 1; next }
      if (skip) { skip = 0 }
    }
    if ($0 ~ "^## ") {
      if (skip) { skip = 0 }
    }
    if (!skip) { print }
  }
' "$ACTIVE" > "$TMP"
mv "$TMP" "$ACTIVE"

# Step 3: Renumber the remaining rules in active_rules.md so they stay contiguous.
python3 - <<'PY' "$ACTIVE"
import re, sys
path = sys.argv[1]
text = open(path).read()
lines = text.split("\n")
n = 0
out = []
for line in lines:
    m = re.match(r'^(\d+)\.\s+\*\*(.*)$', line)
    if m:
        n += 1
        out.append(f"{n}. **{m.group(2)}")
    else:
        out.append(line)
open(path, "w").write("\n".join(out))
PY

# Log via Fix #1 helper.
"$(dirname "$0")/wiki-log-append.sh" /ship "active_rules.md" "demoted rule $N to Demoted Rules section"

echo "demote-rule: demoted rule $N"
