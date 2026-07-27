#!/usr/bin/env bash
# tests/post-tool-skill-track.test.sh
# Verifies Skill hook payload filtering stays precise for Codex PostToolUse.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SCRIPT="$REPO_ROOT/scripts/hooks/post-tool-skill-track.sh"
TMP_DIR="$(mktemp -d)"
STATE="$TMP_DIR/k2b-current-skill"

cleanup() {
  rm -rf "$TMP_DIR"
  rm -f /tmp/k2b-current-skill-codex
}
trap cleanup EXIT

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

echo "=== post-tool-skill-track.test.sh ==="

printf '{"hook_event_name":"PostToolUse","turn_id":"turn_fixture","tool_name":"Skill","tool_use_id":"tool_fixture","tool_input":{"skill":"k2b-ship"},"tool_response":{"ok":true}}' | K2B_CURRENT_SKILL_FILE="$STATE" "$SCRIPT"
[ "$(cat "$STATE")" = "k2b-ship" ] || fail "Codex Skill payload was not tracked"
echo "PASS: Codex Skill payload"

rm -f "$STATE"
printf '{"hook_event_name":"PostToolUse","tool_name":"codex.skill.v2","tool_input":{"skill":"k2b-review"}}' | K2B_CURRENT_SKILL_FILE="$STATE" "$SCRIPT"
[ "$(cat "$STATE")" = "k2b-review" ] || fail "namespaced Codex skill payload was not tracked"
echo "PASS: namespaced Codex skill payload"

rm -f "$STATE"
printf '{"tool_input":{"skill":"k2b-review"}}' | K2B_CURRENT_SKILL_FILE="$STATE" "$SCRIPT"
[ ! -f "$STATE" ] || fail "payload without Codex tool_name should not be tracked"
echo "PASS: payload without Codex tool_name ignored"

rm -f "$STATE"
printf '{"tool_name":"Read","input":{"name":"k2b-ship"}}' | K2B_CURRENT_SKILL_FILE="$STATE" "$SCRIPT"
[ ! -f "$STATE" ] || fail "arbitrary input.name should not be tracked"
echo "PASS: arbitrary input.name ignored"

rm -f "$STATE"
printf '{"input":{"name":"k2b-ship"}}' | K2B_CURRENT_SKILL_FILE="$STATE" "$SCRIPT"
[ ! -f "$STATE" ] || fail "input.name without tool_name should not be tracked"
echo "PASS: input.name without tool_name ignored"

rm -f "$STATE"
printf '{"tool_name":"SkillSet","tool_input":{"skill":"not-a-real-skill-call"}}' | K2B_CURRENT_SKILL_FILE="$STATE" "$SCRIPT"
[ ! -f "$STATE" ] || fail "substring tool name should not be tracked"
echo "PASS: substring tool name ignored"

rm -f "$STATE"
printf '{"tool_name":"Skill","tool_input":{"skill":{"name":"k2b-ship"}}}' | K2B_CURRENT_SKILL_FILE="$STATE" "$SCRIPT"
[ ! -f "$STATE" ] || fail "non-string skill value should not be tracked"
echo "PASS: malformed skill value ignored"

rm -f "$STATE"
printf '{"tool_name":"Skill","tool_input":{"skill":"k2b ship"}}' | K2B_CURRENT_SKILL_FILE="$STATE" "$SCRIPT"
[ ! -f "$STATE" ] || fail "space-containing skill name should not be tracked"
echo "PASS: space-containing skill value ignored"

rm -f "$STATE"
printf '{"tool_name":"Skill","tool_input":{"skill":"k2b.ship"}}' | K2B_CURRENT_SKILL_FILE="$STATE" "$SCRIPT"
[ ! -f "$STATE" ] || fail "dot-containing skill name should not be tracked"
echo "PASS: dot-containing skill value ignored"

rm -f "$STATE"
printf '{"tool_name":"Skill","tool_input":{"skill":null}}' | K2B_CURRENT_SKILL_FILE="$STATE" "$SCRIPT"
[ ! -f "$STATE" ] || fail "null skill value should not be tracked"
echo "PASS: null skill value ignored"

rm -f "$STATE"
printf '{"tool_name":"Skill","tool_input":{"skill":""}}' | K2B_CURRENT_SKILL_FILE="$STATE" "$SCRIPT"
[ ! -f "$STATE" ] || fail "empty skill value should not be tracked"
echo "PASS: empty skill value ignored"

rm -f "$STATE"
printf '{"tool_name":"Skill","tool_input":{"skill":"   "}}' | K2B_CURRENT_SKILL_FILE="$STATE" "$SCRIPT"
[ ! -f "$STATE" ] || fail "whitespace skill value should not be tracked"
echo "PASS: whitespace skill value ignored"

rm -f /tmp/k2b-current-skill-codex /tmp/k2b-current-skill-default
printf '{"tool_name":"Skill","tool_input":{"skill":"k2b-review"}}' | K2B_HOOK_PROVIDER=codex "$SCRIPT"
[ "$(cat /tmp/k2b-current-skill-codex)" = "k2b-review" ] || fail "Codex provider state file was not isolated"
[ ! -e /tmp/k2b-current-skill-default ] || fail "Codex hook should not write default provider state"
echo "PASS: Codex provider state file"
