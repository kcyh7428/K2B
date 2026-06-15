#!/usr/bin/env bash
# tests/stop-observe-hook.test.sh
# Verifies stop-observe attribution through the provider-scoped skill state file.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SCRIPT="$REPO_ROOT/scripts/hooks/stop-observe.sh"

TMP_DIR="$(mktemp -d)"
cleanup() {
  rm -rf "$TMP_DIR"
  rm -f /tmp/k2b-current-skill-codex /tmp/k2b-current-skill-claude
}
trap cleanup EXIT

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

echo "=== stop-observe-hook.test.sh ==="

vault="$TMP_DIR/vault"
state_file="$TMP_DIR/current-skill-codex"
last_file="$TMP_DIR/last-observe-codex"
mkdir -p "$vault/wiki/context" "$vault/wiki/concepts"
printf 'k2b-review\n' > "$state_file"
touch -t 202001010000 "$last_file"
printf '# Test concept\n' > "$vault/wiki/concepts/test-stop-observe.md"

printf '{"session_id":"codex-stop-test","input":{"reason":"complete"}}' | \
  K2B_HOOK_PROVIDER=codex \
  K2B_VAULT_PATH="$vault" \
  K2B_CURRENT_SKILL_FILE="$state_file" \
  K2B_LAST_OBSERVE_FILE="$last_file" \
  bash "$SCRIPT"

obs_file="$vault/wiki/context/observations.jsonl"
[ -f "$obs_file" ] || fail "observations file was not written"
grep -q '"session":"codex-stop-test"' "$obs_file" || fail "session id missing from observation"
grep -q '"skill":"k2b-review"' "$obs_file" || fail "tracked skill was not used"
grep -q '"file":"wiki/concepts/test-stop-observe.md"' "$obs_file" || fail "changed vault file was not recorded"
echo "PASS: Codex stop payload uses tracked skill state"

printf '{"session_id":"codex-stop-test","input":{"reason":"complete"}}' | \
  K2B_HOOK_PROVIDER=codex \
  K2B_VAULT_PATH="$vault" \
  K2B_CURRENT_SKILL_FILE="$state_file" \
  K2B_LAST_OBSERVE_FILE="$last_file" \
  bash "$SCRIPT"

line_count="$(wc -l < "$obs_file" | tr -d ' ')"
[ "$line_count" = "1" ] || fail "second run duplicated unchanged observation"
echo "PASS: marker advancement prevents duplicate observations"

rm -f /tmp/k2b-current-skill-codex /tmp/k2b-current-skill-claude
printf 'k2b-tldr\n' > /tmp/k2b-current-skill-codex
printf 'k2b-ship\n' > /tmp/k2b-current-skill-claude
provider_vault="$TMP_DIR/provider-vault"
provider_last="$TMP_DIR/provider-last-observe-codex"
mkdir -p "$provider_vault/wiki/context" "$provider_vault/wiki/concepts"
touch -t 202001010000 "$provider_last"
printf '# Provider isolation\n' > "$provider_vault/wiki/concepts/provider-isolation.md"

printf '{"session_id":"codex-provider-default","input":{"reason":"complete"}}' | \
  K2B_HOOK_PROVIDER=codex \
  K2B_VAULT_PATH="$provider_vault" \
  K2B_LAST_OBSERVE_FILE="$provider_last" \
  bash "$SCRIPT"

provider_obs="$provider_vault/wiki/context/observations.jsonl"
tail -n 1 "$provider_obs" | grep -q '"session":"codex-provider-default"' || fail "provider default run did not write observation"
tail -n 1 "$provider_obs" | grep -q '"skill":"k2b-tldr"' || fail "provider default did not read Codex state file"
if tail -n 1 "$provider_obs" | grep -q '"skill":"k2b-ship"'; then
  fail "provider default leaked Claude state file"
fi
echo "PASS: provider default state file is isolated in stop-observe"
