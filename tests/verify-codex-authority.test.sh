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
    "$root/scripts/lib" \
    "$root/scripts/loop" \
    "$root/scripts/washing-machine/prompts" \
    "$root/plans" \
    "$root/docs/migration-exports" \
    "$root/.code-reviews" \
    "$root/archive/claude" \
    "$root/vault/System/memory" \
    "$root/vault/wiki"

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
  printf '%s\n' '{"hooks":{"SessionStart":[{"hooks":[{"type":"command","command":"scripts/hooks/session-start.sh"}]}]}}' > "$root/.codex/hooks.json"
  printf '%s\n' '# Active Rules' 'Codex owns live instructions.' > "$root/vault/System/memory/active_rules.md"
  printf '%s\n' '# Wiki Index' 'Current K2B knowledge.' > "$root/vault/wiki/index.md"
  printf '%s\n' 'Codex renders current loop choices.' > "$root/scripts/loop/loop_render.py"
  printf '%s\n' 'Codex owns final research reasoning.' > "$root/scripts/research-extract-prompt.md"
  printf '%s\n' 'Bounded manual preference analysis.' > "$root/scripts/observer-prompt.md"
  printf '%s\n' 'Do not create reminders automatically.' > "$root/scripts/washing-machine/prompts/washing-machine-classifier-v1.0.txt"
  printf '%s\n' '{}' > "$root/.mcp.json"
  printf '%s\n' '# Kimi provider client' > "$root/scripts/lib/minimax_common.py"
  printf '%s\n' '# Kimi review client' > "$root/scripts/lib/minimax_review.py"
  printf '%s\n' '# Review runner' > "$root/scripts/lib/review_runner.py"
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
  if [[ "$relative_path" == vault/System/memory/* ]]; then
    printf '\n99. %s\n' "$forbidden" >> "$fixture/$relative_path"
  else
    printf '\n%s\n' "$forbidden" >> "$fixture/$relative_path"
  fi

  if K2B_VAULT_PATH="$fixture/vault" bash "$SCANNER" "$fixture" >"$out" 2>&1; then
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
K2B_VAULT_PATH="$valid/vault" bash "$SCANNER" "$valid" >/dev/null ||
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
  .mcp.json \
  scripts/hooks/session-start.sh \
  scripts/loop/loop_render.py \
  scripts/research-extract-prompt.md \
  scripts/observer-prompt.md \
  scripts/washing-machine/prompts/washing-machine-classifier-v1.0.txt
do
  expect_violation "$surface" "CLAUDE_PROJECT_DIR"
done

printf '\n%s\n' '~/.claude/projects' >> "$valid/scripts/lib/minimax_common.py"
if K2B_VAULT_PATH="$valid/vault" bash "$SCANNER" "$valid" >/dev/null 2>&1; then
  fail "scanner accepted retired state in an operational provider module"
fi
echo "PASS: operational dependencies are scanned"

for surface in \
  vault/System/memory/active_rules.md
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
  "K2B_LLM_PROVIDER=minimax" \
  "deploy-to-mini.sh" \
  "ssh macmini" \
  ".pending-sync/" \
  "pm2 restart k2b-remote" \
  "Operations Console attention queue" \
  "K2B_YT_REMOTE_HOST=macmini" \
  "Stop hook captures observations" \
  "scheduled cron task fires on Mac Mini"
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
  printf '%s\n' "CLAUDE_PROJECT_DIR ~/.claude/projects .claude/skills Claude Code desktop Telegram scheduler Primary workday capture path is Telegram Desktop Claude Code can still own orchestration Claude Code and Telegram compatibility You can still use Claude Code Telegram is unchanged Telegram feedback path Telegram ad-hoc URL flow send the URL to the Telegram bot directly K2B_LLM_PROVIDER=minimax deploy-to-mini.sh ssh macmini .pending-sync/ pm2 restart k2b-remote Operations Console attention queue K2B_YT_REMOTE_HOST=macmini Stop hook captures observations scheduled cron task fires on Mac Mini" > "$historical/$path"
done
K2B_VAULT_PATH="$historical/vault" bash "$SCANNER" "$historical" >/dev/null ||
  fail "historical allowlist should not be treated as live authority"
echo "PASS: historical prose remains outside live authority"

K2B_VAULT_PATH="$valid/vault" bash "$SCANNER" "$REPO_ROOT"
echo "PASS: repository Codex authority is clean"
