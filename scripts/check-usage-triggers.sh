#!/bin/bash
# Check K2B skill usage triggers
# Called on session start or via /usage check

# $HOME/Projects/K2B-Vault resolves on both the MacBook (keithmbpm2) and the
# Mini (fastshower); the old hardcoded keithmbpm2 path made this a silent no-op
# on the Mini. Files also moved Notes/Context -> wiki/context in the Apr 2026
# vault migration, so the old dir found nothing on either machine.
VAULT="${K2B_VAULT:-$HOME/Projects/K2B-Vault}"
LOG="$VAULT/wiki/context/skill-usage-log.tsv"
TRIGGERS="$VAULT/wiki/context/usage-triggers.md"

if [ ! -f "$LOG" ] || [ ! -f "$TRIGGERS" ]; then
  exit 0
fi

# Count lines in log (minus header)
TOTAL=$(tail -n +2 "$LOG" | wc -l | tr -d ' ')

if [ "$TOTAL" -eq 0 ]; then
  echo "No skill usage recorded yet."
  exit 0
fi

# Extract trigger rows from the markdown table (skip header rows)
ALERTS=""
while IFS='|' read -r _ skill threshold window action last_fired _; do
  skill=$(echo "$skill" | xargs)
  threshold=$(echo "$threshold" | xargs)
  window=$(echo "$window" | xargs)
  action=$(echo "$action" | xargs)
  last_fired=$(echo "$last_fired" | xargs)

  # Skip header-like rows
  [[ "$skill" == "Skill" ]] && continue
  [[ "$skill" == "---"* ]] && continue
  [[ -z "$skill" ]] && continue

  # Count uses since last_fired (or all if never)
  if [ "$last_fired" = "never" ]; then
    COUNT=$(tail -n +2 "$LOG" | awk -F'\t' -v s="$skill" '$2 == s' | wc -l | tr -d ' ')
  else
    COUNT=$(tail -n +2 "$LOG" | awk -F'\t' -v s="$skill" -v d="$last_fired" '$2 == s && $1 > d' | wc -l | tr -d ' ')
  fi

  if [ "$COUNT" -ge "$threshold" ]; then
    ALERTS="$ALERTS\n  - $skill: $COUNT uses (threshold: $threshold). Action: $action"
  fi
done < <(grep '|' "$TRIGGERS" | tail -n +3)

if [ -n "$ALERTS" ]; then
  echo "USAGE TRIGGERS READY:$ALERTS"
else
  echo "All triggers below threshold. ($TOTAL total logged uses)"
fi
