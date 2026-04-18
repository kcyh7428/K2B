#!/usr/bin/env bash
# scripts/lint-memory.sh
#
# Memory integrity audit for k2b-lint Check 13. Read-only. Advisory only.
# Always exits 0 so a caller running alongside other lint checks never
# short-circuits on a memory warning.
#
# Checks:
#   1. Every `[text](path)` link in MEMORY.md resolves (relative to the
#      memory dir, or absolute). http(s):/mailto: links are ignored.
#   2. MEMORY.md line count is at or under 190 (Anthropic truncates auto-memory
#      after ~200 lines, 190 leaves a 10-line margin).
#   3. active_rules.md line count is at or under 190.
#   4. Both files exist.
#
# Output: one `[memory]` line per finding, to stdout. No output means healthy.
#
# Env:
#   K2B_MEMORY_DIR  override the memory dir (default:
#                   ~/Projects/K2B-Vault/System/memory, which is the symlink
#                   target of ~/.claude/projects/*/memory)

set -u

MEMORY_DIR="${K2B_MEMORY_DIR:-$HOME/Projects/K2B-Vault/System/memory}"
CAP=190

INDEX="$MEMORY_DIR/MEMORY.md"
ACTIVE="$MEMORY_DIR/active_rules.md"

if [ ! -f "$INDEX" ]; then
  echo "[memory] MEMORY.md not found at $INDEX"
else
  if ! python3 - "$INDEX" "$MEMORY_DIR" <<'PYEOF'
import os, re, sys
index_path, memory_dir = sys.argv[1], sys.argv[2]
with open(index_path, encoding="utf-8", errors="replace") as fh:
    text = fh.read()
for match in re.finditer(r'\[[^\]]+\]\(([^)]+)\)', text):
    target = match.group(1).strip()
    if target.startswith(("http://", "https://", "mailto:", "#")):
        continue
    resolved = os.path.normpath(os.path.join(memory_dir, target))
    if not os.path.exists(resolved):
        print(f"[memory] MEMORY.md points to missing file: {target} (resolved: {resolved})")
PYEOF
  then
    echo "[memory] MEMORY.md pointer audit crashed (python exited non-zero; see stderr)"
  fi

  # Use awk, not wc -l: wc counts newline bytes, so a file with N logical
  # lines but no trailing \n reports N-1 and silently misses the cap check.
  mem_lines=$(awk 'END {print NR}' "$INDEX")
  if [ "$mem_lines" -gt "$CAP" ]; then
    echo "[memory] MEMORY.md is $mem_lines lines (cap $CAP; Anthropic auto-memory truncates after ~200)"
  fi
fi

if [ ! -f "$ACTIVE" ]; then
  echo "[memory] active_rules.md not found at $ACTIVE"
else
  act_lines=$(awk 'END {print NR}' "$ACTIVE")
  if [ "$act_lines" -gt "$CAP" ]; then
    echo "[memory] active_rules.md is $act_lines lines (cap $CAP)"
  fi
fi

exit 0
