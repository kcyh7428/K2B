#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SCANNER="$REPO_ROOT/scripts/verify-codex-authority.sh"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

make_fixture() {
  local root="$1"
  mkdir -p \
    "$root/.agents/skills/k2b-example" \
    "$root/.codex" \
    "$root/scripts/hooks" \
    "$root/plans" \
    "$root/docs/migration-exports" \
    "$root/.code-reviews" \
    "$root/archive/claude"

  cat > "$root/AGENTS.md" <<'EOF'
# Fixture authority

Codex owns live instructions.
EOF
  cat > "$root/.agents/skills/k2b-example/SKILL.md" <<'EOF'
---
name: k2b-example
description: Fixture
---

Codex runs this skill.
EOF
  printf '{"hooks":{}}\n' > "$root/.codex/hooks.json"
  for hook in \
    session-start.sh \
    post-tool-skill-track.sh \
    stop-observe.sh \
    youtube-transcript-prefetch.sh
  do
    printf '#!/usr/bin/env bash\nexit 0\n' > "$root/scripts/hooks/$hook"
  done
}

expect_violation() {
  local relative_path="$1"
  local forbidden="$2"
  local fixture="$TMP_DIR/violation"
  local out="$TMP_DIR/violation.out"

  rm -rf "$fixture"
  make_fixture "$fixture"
  printf '\n%s\n' "$forbidden" >> "$fixture/$relative_path"

  if bash "$SCANNER" "$fixture" >"$out" 2>&1; then
    fail "scanner accepted $forbidden in $relative_path"
  fi
  grep -Fq "$relative_path" "$out" ||
    fail "scanner did not identify the violating path: $relative_path"
  grep -Fq "$forbidden" "$out" ||
    fail "scanner did not identify the forbidden dependency: $forbidden"
}

expect_skill_root_violation() {
  local case_name="$1"
  local fixture="$TMP_DIR/skill-root-$case_name"
  local out="$TMP_DIR/skill-root-$case_name.out"

  rm -rf "$fixture"
  make_fixture "$fixture"

  case "$case_name" in
    missing)
      rm -rf "$fixture/.agents/skills"
      ;;
    wrong-type)
      rm -rf "$fixture/.agents/skills"
      printf 'not a directory\n' > "$fixture/.agents/skills"
      ;;
    empty)
      rm -rf "$fixture/.agents/skills/k2b-example"
      ;;
    no-k2b-skill)
      rm -rf "$fixture/.agents/skills/k2b-example"
      mkdir -p "$fixture/.agents/skills/example"
      printf '%s\n' '# unrelated skill' > "$fixture/.agents/skills/example/SKILL.md"
      ;;
    *)
      fail "unknown skill-root fixture: $case_name"
      ;;
  esac

  if bash "$SCANNER" "$fixture" >"$out" 2>&1; then
    fail "scanner accepted invalid skill root: $case_name"
  fi
  grep -Fq ".agents/skills" "$out" ||
    fail "scanner did not identify the invalid skill root: $case_name"
}

echo "=== verify-codex-authority.test.sh ==="

[ -x "$SCANNER" ] || fail "authority scanner is missing or not executable: $SCANNER"

valid="$TMP_DIR/valid"
make_fixture "$valid"
bash "$SCANNER" "$valid" >/dev/null ||
  fail "clean active-authority fixture should pass"
echo "PASS: clean active-authority fixture"

for case_name in missing wrong-type empty no-k2b-skill; do
  expect_skill_root_violation "$case_name"
done
echo "PASS: invalid live skill roots fail closed"

for surface in \
  AGENTS.md \
  .agents/skills/k2b-example/SKILL.md \
  .codex/hooks.json \
  scripts/hooks/session-start.sh \
  scripts/hooks/post-tool-skill-track.sh \
  scripts/hooks/stop-observe.sh \
  scripts/hooks/youtube-transcript-prefetch.sh
do
  expect_violation "$surface" "CLAUDE_PROJECT_DIR"
done

for forbidden in \
  "~/.claude/projects" \
  ".claude/skills" \
  "Claude Code desktop" \
  "Telegram scheduler" \
  "Primary workday capture path is Telegram Desktop" \
  "Claude Code can still own orchestration" \
  "Claude Code and Telegram compatibility" \
  "You can still use Claude Code" \
  "Telegram is unchanged" \
  "Telegram feedback path" \
  "Telegram ad-hoc URL flow" \
  "send the URL to the Telegram bot directly" \
  "K2B_LLM_PROVIDER=minimax"
do
  expect_violation ".agents/skills/k2b-example/SKILL.md" "$forbidden"
done
echo "PASS: forbidden live dependencies fail closed"

historical="$TMP_DIR/historical"
make_fixture "$historical"
for path in \
  plans/old-plan.md \
  docs/migration-exports/old-export.md \
  .code-reviews/old-review.md \
  archive/claude/immutable-history.md
do
  printf '%s\n' "CLAUDE_PROJECT_DIR ~/.claude/projects .claude/skills Claude Code desktop Telegram scheduler Primary workday capture path is Telegram Desktop Claude Code can still own orchestration Claude Code and Telegram compatibility You can still use Claude Code Telegram is unchanged Telegram feedback path Telegram ad-hoc URL flow send the URL to the Telegram bot directly K2B_LLM_PROVIDER=minimax" > "$historical/$path"
done
bash "$SCANNER" "$historical" >/dev/null ||
  fail "historical allowlist should not be treated as live authority"
echo "PASS: historical prose remains outside live authority"

bash "$SCANNER" "$REPO_ROOT"
echo "PASS: repository Codex authority is clean"
