#!/usr/bin/env bash
# tests/verify-skills-parity.test.sh
# Tests scripts/verify-skills-parity.sh against fixture trees.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SCRIPT="$REPO_ROOT/scripts/verify-skills-parity.sh"

TMP_DIRS=()
cleanup() {
  local d
  for d in "${TMP_DIRS[@]}"; do
    [ -n "$d" ] && [ -d "$d" ] && rm -rf "$d"
  done
}
trap cleanup EXIT

mktmp() {
  local d
  d="$(mktemp -d)"
  TMP_DIRS+=("$d")
  echo "$d"
}

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

build_fixture() {
  local root="$1"
  mkdir -p "$root/.claude/skills/k2b-ship" "$root/.agents/skills/k2b-ship" "$root/.codex" "$root/plans/templates"
  printf '# Agents\n' > "$root/AGENTS.md"
  printf '{"hooks":{}}\n' > "$root/.codex/hooks.json"
  cat > "$root/plans/templates/ship-brief.md" <<'BRIEF'
# Prepare Keith-facing ship brief

## What you will notice

## What stays the same

## What is not included yet

## What to do now

## Under the hood

## Risk / rollback
BRIEF
  cat > "$root/.claude/skills/k2b-ship/SKILL.md" <<'SKILL'
---
name: k2b-ship
description: Ship K2B changes safely
---

# k2b-ship

Text worker calls use Kimi through historical minimax scripts.

### 8.5 Prepare Keith-facing ship brief

1. **What you will notice** -- day-to-day behavior change.
2. **What stays the same** -- compatibility lanes.
3. **What is not included yet** -- later ships.
4. **What to do now** -- next user action.
5. **Under the hood** -- implementation detail.
6. **Risk / rollback** -- one short sentence when relevant.
SKILL
  cp "$root/.claude/skills/k2b-ship/SKILL.md" "$root/.agents/skills/k2b-ship/SKILL.md"
}

expect_pass() {
  local name="$1" root="$2"
  local out="$root/parity.out"
  local err="$root/parity.err"
  "$SCRIPT" --root "$root" >"$out" 2>"$err" || {
    cat "$out"
    cat "$err" >&2
    fail "$name should pass"
  }
  echo "PASS: $name"
}

expect_fail() {
  local name="$1" root="$2" needle="$3"
  local combined
  local out="$root/parity.out"
  local err="$root/parity.err"
  if "$SCRIPT" --root "$root" >"$out" 2>"$err"; then
    fail "$name should fail"
  fi
  combined="$(cat "$out" "$err")"
  if [[ "$combined" != *"$needle"* ]]; then
    cat "$out"
    cat "$err" >&2
    fail "$name should mention $needle"
  fi
  echo "PASS: $name"
}

echo "=== verify-skills-parity.test.sh ==="

root="$(mktmp)"
build_fixture "$root"
expect_pass "matching fixture" "$root"

root="$(mktmp)"
build_fixture "$root"
sed -i.bak 's/description: Ship K2B changes safely/description: "Ship K2B changes safely"/' "$root/.claude/skills/k2b-ship/SKILL.md"
rm -f "$root/.claude/skills/k2b-ship/SKILL.md.bak"
expect_pass "semantically equivalent YAML frontmatter" "$root"

root="$(mktmp)"
build_fixture "$root"
rm -rf "$root/.agents/skills/k2b-ship"
expect_fail "missing agent skill" "$root" "missing"

root="$(mktmp)"
build_fixture "$root"
printf '\nSee .Codex/skills/k2b-ship for details.\n' >> "$root/.agents/skills/k2b-ship/SKILL.md"
expect_fail "stale .Codex path" "$root" "Codex skills path"

root="$(mktmp)"
build_fixture "$root"
printf '\nSee ~/.codex/skills/k2b-ship for details.\n' >> "$root/.agents/skills/k2b-ship/SKILL.md"
expect_fail "stale lowercase .codex path" "$root" "Codex skills path"

root="$(mktmp)"
build_fixture "$root"
printf '\nSee Codex/skills/k2b-ship for details.\n' >> "$root/.agents/skills/k2b-ship/SKILL.md"
expect_fail "stale bare Codex path" "$root" "Codex skills path"

root="$(mktmp)"
build_fixture "$root"
printf '\nSee .codex/skills-backup for old copies.\n' >> "$root/.agents/skills/k2b-ship/SKILL.md"
expect_fail "stale suffixed codex skills path" "$root" "Codex skills path"

root="$(mktmp)"
build_fixture "$root"
printf '\nSee .claude/skills/k2b-ship for details.\n' >> "$root/.agents/skills/k2b-ship/SKILL.md"
expect_fail "stale Claude skill path" "$root" "Claude skills path"

root="$(mktmp)"
build_fixture "$root"
mkdir -p "$root/.agents/skills/k2b-ship/scripts"
printf 'tool="$REPO_ROOT/.claude/skills/k2b-ship/scripts/ship.sh"\n' > "$root/.agents/skills/k2b-ship/scripts/tool.sh"
expect_fail "nested stale Claude skill path" "$root" "Claude skills path"

root="$(mktmp)"
build_fixture "$root"
mkdir -p "$root/.claude/skills/k2b-ship/scripts"
printf '#!/usr/bin/env bash\n' > "$root/.claude/skills/k2b-ship/scripts/ship.sh"
expect_fail "missing nested agent file" "$root" "missing .agents nested skill file"

root="$(mktmp)"
build_fixture "$root"
mkdir -p "$root/.agents/skills/k2b-ship/scripts"
printf '#!/usr/bin/env bash\n' > "$root/.agents/skills/k2b-ship/scripts/ship.sh"
expect_fail "orphan nested agent file" "$root" "orphan .agents nested skill file"

root="$(mktmp)"
build_fixture "$root"
printf '\nUse "$CLAUDE_PROJECT_DIR/scripts/hooks/session-start.sh".\n' >> "$root/.agents/skills/k2b-ship/SKILL.md"
expect_fail "Claude project dir reference" "$root" "CLAUDE_PROJECT_DIR"

root="$(mktmp)"
build_fixture "$root"
printf '\nMemory lives at ~/.claude/projects/-Users-keithmbpm2-Projects-K2B/memory/self_improve_requests.md.\n' >> "$root/.agents/skills/k2b-ship/SKILL.md"
expect_fail "Claude project memory path" "$root" "Claude project memory path"

root="$(mktmp)"
build_fixture "$root"
printf '\nSee .agents/skills/k2b-ship for the Codex mirror.\n' >> "$root/.claude/skills/k2b-ship/SKILL.md"
expect_fail "Codex-only path in Claude skill" "$root" "Codex-only path"

root="$(mktmp)"
build_fixture "$root"
printf '\nM2.7 handles text routing.\n' >> "$root/.agents/skills/k2b-ship/SKILL.md"
expect_fail "dead MiniMax worker wording" "$root" "MiniMax M2.7"

root="$(mktmp)"
build_fixture "$root"
printf '\nMiniMax M2.7 is the backup text worker.\n' >> "$root/.agents/skills/k2b-ship/SKILL.md"
expect_fail "MiniMax backup text worker wording" "$root" "MiniMax M2.7"

root="$(mktmp)"
build_fixture "$root"
printf '\nMiniMax M2.7 is the fallback for text routing when Kimi is unavailable.\n' >> "$root/.agents/skills/k2b-ship/SKILL.md"
expect_fail "MiniMax fallback text routing wording" "$root" "MiniMax M2.7"

root="$(mktmp)"
build_fixture "$root"
printf '\nK2B employs M2.7 for text calls.\n' >> "$root/.agents/skills/k2b-ship/SKILL.md"
expect_fail "MiniMax live-worker verb wording" "$root" "MiniMax M2.7"

root="$(mktmp)"
build_fixture "$root"
printf '\nMiniMax M2.7 is the standby text worker.\n' >> "$root/.agents/skills/k2b-ship/SKILL.md"
expect_fail "MiniMax standby text worker wording" "$root" "MiniMax M2.7"

root="$(mktmp)"
build_fixture "$root"
printf '\nDo not use MiniMax M2.7 as the active text worker.\n' >> "$root/.agents/skills/k2b-ship/SKILL.md"
expect_pass "negated MiniMax live-worker wording" "$root"

root="$(mktmp)"
build_fixture "$root"
printf '\nMiniMax M2.7 is a historical model; K2B no longer uses it as the live worker.\n' >> "$root/.agents/skills/k2b-ship/SKILL.md"
expect_pass "no-longer MiniMax live-worker wording" "$root"

root="$(mktmp)"
build_fixture "$root"
printf '\nThe MiniMax M2.7 API endpoint is documented for historical reference only.\n' >> "$root/.agents/skills/k2b-ship/SKILL.md"
expect_pass "reference-only MiniMax API wording" "$root"

root="$(mktmp)"
build_fixture "$root"
printf '\nMiniMax M2.7 is the active worker; see the docs for details.\n' >> "$root/.agents/skills/k2b-ship/SKILL.md"
expect_fail "MiniMax active worker with docs wording" "$root" "MiniMax M2.7"

root="$(mktmp)"
build_fixture "$root"
printf '\nThe active worker is MiniMax M2.7.\n' >> "$root/.agents/skills/k2b-ship/SKILL.md"
expect_fail "MiniMax reordered active worker wording" "$root" "MiniMax M2.7"

root="$(mktmp)"
build_fixture "$root"
printf '\nSet MiniMax M2.7 as active for text work.\n' >> "$root/.agents/skills/k2b-ship/SKILL.md"
expect_fail "MiniMax set active wording" "$root" "MiniMax M2.7"

root="$(mktmp)"
build_fixture "$root"
printf '\nMiniMax M2.7 active-worker status is still live.\n' >> "$root/.agents/skills/k2b-ship/SKILL.md"
expect_fail "MiniMax hyphenated active worker wording" "$root" "MiniMax M2.7"

root="$(mktmp)"
build_fixture "$root"
printf '\nMiniMax M2.7 was the text worker.\n' >> "$root/.agents/skills/k2b-ship/SKILL.md"
expect_fail "MiniMax past-tense text worker wording" "$root" "MiniMax M2.7"

root="$(mktmp)"
build_fixture "$root"
sed -i.bak 's/description: Ship K2B changes safely/description: Drifted description/' "$root/.agents/skills/k2b-ship/SKILL.md"
rm -f "$root/.agents/skills/k2b-ship/SKILL.md.bak"
expect_fail "frontmatter mismatch" "$root" "frontmatter mismatch"

root="$(mktmp)"
build_fixture "$root"
sed -i.bak 's/name: k2b-ship/name: k2b-review/' "$root/.agents/skills/k2b-ship/SKILL.md"
rm -f "$root/.agents/skills/k2b-ship/SKILL.md.bak"
expect_fail "frontmatter name must match directory" "$root" "frontmatter name does not match directory"

root="$(mktmp)"
build_fixture "$root"
sed -i.bak '/^description:/d' "$root/.agents/skills/k2b-ship/SKILL.md"
rm -f "$root/.agents/skills/k2b-ship/SKILL.md.bak"
expect_fail "frontmatter missing key" "$root" "frontmatter missing"

root="$(mktmp)"
build_fixture "$root"
sed -i.bak 's/description: Ship K2B changes safely/description: [unterminated/' "$root/.agents/skills/k2b-ship/SKILL.md"
rm -f "$root/.agents/skills/k2b-ship/SKILL.md.bak"
expect_fail "frontmatter malformed YAML" "$root" "frontmatter parse error"

root="$(mktmp)"
build_fixture "$root"
sed -i.bak '/What you will notice/d' "$root/.agents/skills/k2b-ship/SKILL.md"
rm -f "$root/.agents/skills/k2b-ship/SKILL.md.bak"
expect_fail "missing ship brief marker" "$root" "ship-brief marker"

root="$(mktmp)"
build_fixture "$root"
sed -i.bak '/What to do now/d' "$root/plans/templates/ship-brief.md"
rm -f "$root/plans/templates/ship-brief.md.bak"
expect_fail "missing template ship brief marker" "$root" "ship-brief marker"

root="$(mktmp)"
build_fixture "$root"
sed -i.bak '/What you will notice/d' "$root/.agents/skills/k2b-ship/SKILL.md"
rm -f "$root/.agents/skills/k2b-ship/SKILL.md.bak"
printf '\nThis prose merely says What you will notice, but it is not a structured brief item.\n' >> "$root/.agents/skills/k2b-ship/SKILL.md"
expect_fail "ship brief marker must be structured" "$root" "ship-brief marker"

root="$(mktmp)"
build_fixture "$root"
sed -i.bak 's/description: Ship K2B changes safely/description: null/' "$root/.agents/skills/k2b-ship/SKILL.md"
rm -f "$root/.agents/skills/k2b-ship/SKILL.md.bak"
expect_fail "frontmatter null mismatch" "$root" "frontmatter mismatch"

root="$(mktmp)"
build_fixture "$root"
mkdir -p "$root/.agents/skills/k2b-extra"
cp "$root/.agents/skills/k2b-ship/SKILL.md" "$root/.agents/skills/k2b-extra/SKILL.md"
expect_fail "orphan agent skill" "$root" "orphan"

root="$(mktmp)"
build_fixture "$root"
printf '\nMiniMax M2.7 historical fallback mention only.\n' >> "$root/.agents/skills/k2b-ship/SKILL.md"
expect_pass "historical MiniMax mention" "$root"
