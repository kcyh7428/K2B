#!/usr/bin/env bash
# Guard the sole live Codex delivery contract.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
AGENTS="$REPO_ROOT/AGENTS.md"
SHIP="$REPO_ROOT/.agents/skills/k2b-ship/SKILL.md"

fail() { echo "FAIL: $*" >&2; exit 1; }

echo "=== codex-ship-discipline.test.sh ==="

grep -q "## Review and delivery" "$AGENTS" || \
  fail "AGENTS.md must expose the live review and delivery contract"
grep -q "Plain implementation requests do not authorize" "$AGENTS" || \
  fail "AGENTS.md must not treat plain implementation wording as delivery authority"
grep -q "Same-family fallback does not count" "$AGENTS" || \
  fail "AGENTS.md must require an independent review"
grep -q "Commit/push and activation are distinct states" "$AGENTS" || \
  fail "AGENTS.md must distinguish Git delivery from host activation"

grep -q "Plain implementation wording is not delivery authority" "$SHIP" || \
  fail "k2b-ship must preserve the delivery authority boundary"
grep -q "explicit delivery wording" "$SHIP" || \
  fail "k2b-ship must require explicit delivery wording"
grep -q "same-family review presented as independent" "$SHIP" || \
  fail "k2b-ship must not mislabel same-family review"
grep -q "Activation on each Mac is a separate state" "$SHIP" || \
  fail "k2b-ship must verify activation separately on each Mac"
grep -q "Never assume.*main" "$SHIP" || \
  fail "k2b-ship must not assume the push branch"
grep -q "preserve an uncommitted checkpoint" "$SHIP" || \
  fail "k2b-ship must retain the uncommitted checkpoint path"

[ ! -e "$REPO_ROOT/CLAUDE.md" ] || fail "retired CLAUDE.md must be absent"
[ ! -e "$REPO_ROOT/.claude" ] || fail "retired .claude project tree must be absent"

echo "codex-ship-discipline.test.sh: all tests passed"
