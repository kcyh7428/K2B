#!/bin/bash
# K2B PostToolUse Hook -- Skill Tracker
# Fires after Skill tool invocations. Writes the active skill name
# to a provider-scoped state file so the stop hook can attribute vault
# file changes to the correct skill without cross-provider collisions.

set -euo pipefail

input=$(cat)
hook_provider="${K2B_HOOK_PROVIDER:-codex}"
case "$hook_provider" in
  *[!A-Za-z0-9_-]*|"") hook_provider="codex" ;;
esac
state_file="${K2B_CURRENT_SKILL_FILE:-/tmp/k2b-current-skill-$hook_provider}"

tool_name=$(echo "$input" | jq -r '.tool_name // empty' 2>/dev/null)
tool_name_lc=$(printf '%s' "$tool_name" | tr '[:upper:]' '[:lower:]')
skill_explicit=$(echo "$input" | jq -r '.tool_input.skill // .tool_input.skill_name // empty' 2>/dev/null)
skill_named=$(echo "$input" | jq -r '.tool_input.name // empty' 2>/dev/null)
skill=""
has_explicit_skill_payload=false
if [ -n "$skill_explicit" ] && [ "$skill_explicit" != "null" ]; then
  skill="$skill_explicit"
  has_explicit_skill_payload=true
elif [ -n "$skill_named" ] && [ "$skill_named" != "null" ]; then
  # `name` is accepted only when tool_name already proves this is a skill
  # tool. Without that guard, ordinary tools that happen to have input.name
  # would overwrite the active skill tracker.
  skill="$skill_named"
fi

# Only care about explicit Codex skill invocations. Codex payload tool names
# may use a lower-case or dotted namespace. Skill
# identifiers are repo-local names such as k2b-ship, so dots stay invalid in
# the tracked skill value even though they are valid in the tool_name.
is_skill_tool=false
case "$tool_name_lc" in
  skill|skills|skill-tool|skill_tool|codex-skill|codex_skill|codex.skill|codex.skill.*|agents-skill|agents_skill|agents.skill|agents.skill.*|agent_skill|agent.skill|agent.skill.*|openai-skill|openai_skill|openai.skill|openai.skill.*)
    is_skill_tool=true
    ;;
  "") exit 0 ;;
  *)
    if $has_explicit_skill_payload; then
      echo "post-tool-skill-track: ignoring unrecognized skill-like tool_name: $tool_name" >&2
    fi
    exit 0
    ;;
esac

if $is_skill_tool && [ -n "$skill" ] && [ "$skill" != "null" ]; then
  case "$skill" in
    *[!A-Za-z0-9:_-]*)
      echo "post-tool-skill-track: ignoring invalid skill name: $skill" >&2
      exit 0
      ;;
  esac
  mkdir -p "$(dirname "$state_file")"
  tmp_state="$(mktemp "${state_file}.tmp.XXXXXX")"
  printf '%s\n' "$skill" > "$tmp_state"
  mv -f "$tmp_state" "$state_file"
  sync "$state_file" >/dev/null 2>&1 || sync >/dev/null 2>&1 || true
fi

exit 0
