#!/bin/bash
# K2B PostToolUse Hook -- Skill Tracker
# Fires after Skill tool invocations. Writes the active skill name
# to /tmp/k2b-current-skill so the stop hook can attribute vault
# file changes to the correct skill.

set -euo pipefail

input=$(cat)

tool_name=$(echo "$input" | jq -r '.tool_name // empty' 2>/dev/null)

# Only care about Skill tool invocations
if [ "$tool_name" != "Skill" ]; then
  exit 0
fi

# Extract the skill name from tool_input
skill=$(echo "$input" | jq -r '.tool_input.skill // empty' 2>/dev/null)

if [ -n "$skill" ]; then
  echo "$skill" > /tmp/k2b-current-skill
fi

exit 0
