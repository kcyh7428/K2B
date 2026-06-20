#!/usr/bin/env bash
# tests/codex-ship-discipline.test.sh
# Guard Codex's K2B ship discipline against drifting below Claude Code's
# end-of-session behavior.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

fail() { echo "FAIL: $*" >&2; exit 1; }

echo "=== codex-ship-discipline.test.sh ==="

grep -q "## Session Discipline" "$REPO_ROOT/AGENTS.md" || \
  fail "AGENTS.md must expose a Codex-facing Session Discipline section"

grep -q "At the END of every K2B desktop session" "$REPO_ROOT/AGENTS.md" || \
  fail "AGENTS.md must mirror Claude Code's end-of-session ship obligation"

grep -q "Plain implementation requests without delivery command wording are not ship authorization" "$REPO_ROOT/AGENTS.md" || \
  fail "AGENTS.md must not treat plain implementation requests as ship authorization"

grep -q "Codex Desktop manual ship contract" "$REPO_ROOT/.agents/skills/k2b-ship/SKILL.md" || \
  fail ".agents k2b-ship must define the Codex Desktop manual ship contract"

grep -q "already authorized" "$REPO_ROOT/.agents/skills/k2b-ship/SKILL.md" || \
  fail ".agents k2b-ship must treat explicit build/commit/sync requests as already authorized"

grep -q "descriptive or architectural context" "$REPO_ROOT/.agents/skills/k2b-ship/SKILL.md" || \
  fail ".agents k2b-ship must reject descriptive sync/deploy/merge wording as ship authorization"

grep -q "If the wording is ambiguous" "$REPO_ROOT/.agents/skills/k2b-ship/SKILL.md" || \
  fail ".agents k2b-ship must ask before shipping ambiguous delivery wording"

grep -q "first clause must be complete" "$REPO_ROOT/.agents/skills/k2b-ship/SKILL.md" || \
  fail ".agents k2b-ship must require implementation completion before compound ship commands"

grep -q "list the done checks implied by X" "$REPO_ROOT/.agents/skills/k2b-ship/SKILL.md" || \
  fail ".agents k2b-ship must require done-check verification before compound shipping"

grep -q "concrete test command, file assertion, or live verification result" "$REPO_ROOT/.agents/skills/k2b-ship/SKILL.md" || \
  fail ".agents k2b-ship must require inspectable evidence before compound shipping"

grep -q "cannot produce inspectable evidence" "$REPO_ROOT/.agents/skills/k2b-ship/SKILL.md" || \
  fail ".agents k2b-ship must pause when inspectable evidence is missing"

grep -q "Mixed-mode examples" "$REPO_ROOT/.agents/skills/k2b-ship/SKILL.md" || \
  fail ".agents k2b-ship must document mixed-mode ambiguity examples"

grep -q "Codex Desktop manual ship contract" "$REPO_ROOT/.claude/skills/k2b-ship/SKILL.md" || \
  fail ".claude k2b-ship must keep the same ship contract for parity"

grep -q "already authorized" "$REPO_ROOT/.claude/skills/k2b-ship/SKILL.md" || \
  fail ".claude k2b-ship must keep the same authorization rule for parity"

grep -q "descriptive or architectural context" "$REPO_ROOT/.claude/skills/k2b-ship/SKILL.md" || \
  fail ".claude k2b-ship must keep the same negative authorization rule for parity"

grep -q "If the wording is ambiguous" "$REPO_ROOT/.claude/skills/k2b-ship/SKILL.md" || \
  fail ".claude k2b-ship must keep the same ambiguity rule for parity"

grep -q "first clause must be complete" "$REPO_ROOT/.claude/skills/k2b-ship/SKILL.md" || \
  fail ".claude k2b-ship must keep the same compound-command completion rule for parity"

grep -q "list the done checks implied by X" "$REPO_ROOT/.claude/skills/k2b-ship/SKILL.md" || \
  fail ".claude k2b-ship must keep the same done-check verification rule for parity"

grep -q "concrete test command, file assertion, or live verification result" "$REPO_ROOT/.claude/skills/k2b-ship/SKILL.md" || \
  fail ".claude k2b-ship must keep the same inspectable evidence rule for parity"

grep -q "cannot produce inspectable evidence" "$REPO_ROOT/.claude/skills/k2b-ship/SKILL.md" || \
  fail ".claude k2b-ship must keep the same missing-evidence pause rule for parity"

grep -q "Mixed-mode examples" "$REPO_ROOT/.claude/skills/k2b-ship/SKILL.md" || \
  fail ".claude k2b-ship must keep the same mixed-mode ambiguity examples for parity"

grep -q "If shipping is blocked" "$REPO_ROOT/AGENTS.md" || \
  fail "AGENTS.md must define a blocked-ship recovery path"

grep -q "explicitly declines to ship" "$REPO_ROOT/AGENTS.md" || \
  fail "AGENTS.md must allow explicit uncommitted-session deferral"

echo "codex-ship-discipline.test.sh: all tests passed"
