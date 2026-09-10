#!/usr/bin/env bash
# Tests the compatibility-named live Codex skill guard.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SCRIPT="$REPO_ROOT/scripts/verify-skills-parity.sh"
TMP_ROOT="$(mktemp -d)"
trap 'rm -rf "$TMP_ROOT"' EXIT

fail() { echo "FAIL: $*" >&2; exit 1; }

mktmp() {
  mktemp -d "$TMP_ROOT/case.XXXXXX"
}

build_fixture() {
  local root="$1"
  mkdir -p "$root/.agents/skills/k2b-ship" "$root/.codex" "$root/plans/templates"
  cat > "$root/AGENTS.md" <<'EOF'
# Agents

## Review and delivery

Plain implementation requests do not authorize delivery.
Same-family fallback does not count.
Commit/push and activation are distinct states.
EOF
  printf '{"hooks":{}}\n' > "$root/.codex/hooks.json"
  cat > "$root/plans/templates/ship-brief.md" <<'EOF'
# Ship brief

## What you will notice
## What stays the same
## What is not included yet
## What to do now
## Under the hood
## Risk / rollback
EOF
  cat > "$root/.agents/skills/k2b-ship/SKILL.md" <<'EOF'
---
name: k2b-ship
description: Ship explicitly authorized K2B changes.
---

# Ship

Plain implementation wording is not delivery authority.
Require explicit delivery wording.
Never present a same-family review presented as independent.
Activation on each Mac is a separate state.
EOF
}

expect_pass() {
  local name="$1" root="$2"
  "$SCRIPT" --root "$root" >"$root/out" 2>"$root/err" || {
    cat "$root/out" "$root/err" >&2
    fail "$name should pass"
  }
  echo "PASS: $name"
}

expect_fail() {
  local name="$1" root="$2" needle="$3"
  if "$SCRIPT" --root "$root" >"$root/out" 2>"$root/err"; then
    fail "$name should fail"
  fi
  grep -q "$needle" "$root/out" "$root/err" || {
    cat "$root/out" "$root/err" >&2
    fail "$name should mention $needle"
  }
  echo "PASS: $name"
}

echo "=== verify-skills-parity.test.sh ==="

root="$(mktmp)"
build_fixture "$root"
expect_pass "live Codex fixture" "$root"

root="$(mktmp)"
build_fixture "$root"
mkdir -p "$root/.claude"
expect_fail "retired Claude tree" "$root" "retired Claude project tree"

root="$(mktmp)"
build_fixture "$root"
sed -i.bak 's/name: k2b-ship/name: wrong-name/' "$root/.agents/skills/k2b-ship/SKILL.md"
rm -f "$root/.agents/skills/k2b-ship/SKILL.md.bak"
expect_fail "frontmatter directory mismatch" "$root" "frontmatter name mismatch"

root="$(mktmp)"
build_fixture "$root"
printf '\nSee .claude/skills/k2b-ship.\n' >> "$root/.agents/skills/k2b-ship/SKILL.md"
expect_fail "retired Claude skill path" "$root" "retired Claude skills path"

root="$(mktmp)"
build_fixture "$root"
printf '\nSee ~/.codex/skills/k2b-ship.\n' >> "$root/.agents/skills/k2b-ship/SKILL.md"
expect_fail "stale Codex skill path" "$root" "stale Codex skills path"

root="$(mktmp)"
build_fixture "$root"
printf '\nMiniMax M2.7 is the fallback text worker.\n' >> "$root/.agents/skills/k2b-ship/SKILL.md"
expect_fail "retired live worker" "$root" "retired live-worker wording"

root="$(mktmp)"
build_fixture "$root"
printf '\nMiniMax M2.7 is retired and never a live worker.\n' >> "$root/.agents/skills/k2b-ship/SKILL.md"
expect_pass "negated retired worker" "$root"

root="$(mktmp)"
build_fixture "$root"
sed -i.bak '/Activation on each Mac is a separate state/d' "$root/.agents/skills/k2b-ship/SKILL.md"
rm -f "$root/.agents/skills/k2b-ship/SKILL.md.bak"
expect_fail "missing ship safety marker" "$root" "Activation on each Mac is a separate state"

echo "verify-skills-parity.test.sh: all tests passed"
