#!/bin/bash
# K2B Session Start Hook
# Fires on every Claude Code session start. Outputs context to stdout.
# Deterministic replacement for the "Session Start" section in CLAUDE.md.

set -euo pipefail

VAULT="$HOME/Projects/K2B-Vault"
K2B="$HOME/Projects/K2B"
CONTEXT_DIR="$VAULT/Notes/Context"

output=""

# --- 1. Check usage triggers ---
trigger_result=$("$K2B/scripts/check-usage-triggers.sh" 2>/dev/null || true)
if echo "$trigger_result" | grep -q "USAGE TRIGGERS READY"; then
  output+="$trigger_result"$'\n\n'
fi

# --- 2. Scan Inbox for reviewed items ---
inbox_ready_count=0
inbox_reviewed_count=0

# Check Inbox/Ready/ for items Keith dragged there
if [ -d "$VAULT/Inbox/Ready" ]; then
  inbox_ready_count=$(find "$VAULT/Inbox/Ready" -name "*.md" -type f 2>/dev/null | wc -l | tr -d ' ')
fi

# Check Inbox/ for items with review-action set in frontmatter
if [ -d "$VAULT/Inbox" ]; then
  for f in "$VAULT/Inbox"/*.md; do
    [ -f "$f" ] || continue
    if head -30 "$f" | grep -q "^review-action:" 2>/dev/null; then
      action=$(head -30 "$f" | grep "^review-action:" | head -1 | sed 's/review-action: *//')
      if [ -n "$action" ] && [ "$action" != '""' ] && [ "$action" != "''" ]; then
        inbox_reviewed_count=$((inbox_reviewed_count + 1))
      fi
    fi
  done
fi

total_inbox=$((inbox_ready_count + inbox_reviewed_count))
if [ "$total_inbox" -gt 0 ]; then
  output+="INBOX: $total_inbox items ready to process"
  [ "$inbox_ready_count" -gt 0 ] && output+=" ($inbox_ready_count in Ready/)"
  [ "$inbox_reviewed_count" -gt 0 ] && output+=" ($inbox_reviewed_count with review-action set)"
  output+=". Run /inbox to process them."$'\n\n'
fi

# --- 3. Check observer candidates ---
candidates="$CONTEXT_DIR/observer-candidates.md"
if [ -f "$candidates" ] && [ -s "$candidates" ]; then
  output+="OBSERVER FINDINGS:"$'\n'
  output+="$(cat "$candidates")"$'\n\n'
fi

# --- 4. Load active rules ---
active_rules=$(find -L ~/.claude/projects/ -name "active_rules.md" -type f 2>/dev/null | head -1)
if [ -f "$active_rules" 2>/dev/null ]; then
  output+="ACTIVE RULES (follow these every session):"$'\n'
  output+="$(cat "$active_rules")"$'\n\n'
fi

# --- Output ---
if [ -n "$output" ]; then
  echo "$output"
fi

exit 0
