#!/usr/bin/env bash
set -euo pipefail

export LC_ALL=C

REPO_ROOT="${1:-$(cd "$(dirname "$0")/.." && pwd)}"

forbidden_dependencies=(
  "CLAUDE_PROJECT_DIR"
  "~/.claude/projects"
  ".claude/skills"
  "Claude Code desktop"
  "Telegram scheduler"
  "Primary workday capture path is Telegram Desktop"
  "Claude Code can still own orchestration"
  "Claude Code and Telegram compatibility"
  "You can still use Claude Code"
  "Telegram is unchanged"
  "Telegram feedback path"
  "Telegram ad-hoc URL flow"
  "send the URL to the Telegram bot directly"
  "K2B_LLM_PROVIDER=minimax"
  "deploy-to-mini.sh"
  "ssh macmini"
  ".pending-sync/"
  "pm2 restart k2b-remote"
  "Operations Console attention queue"
  "K2B_YT_REMOTE_HOST=macmini"
  "Stop hook captures observations"
  "scheduled cron task fires on Mac Mini"
  "MacBook for dev, Mac Mini for production"
  "MacBook-to-Mac-Mini sync"
)

forbidden_live_rules=(
  "CLAUDE.md"
  "Claude Code"
  "Telegram"
  "Mac Mini"
  "k2b-remote"
)

findings=0
skill_root="$REPO_ROOT/.agents/skills"

active_files=(
  "$REPO_ROOT/AGENTS.md"
  "$REPO_ROOT/.codex/hooks.json"
  "$REPO_ROOT/.mcp.json"
)

operational_files=(
  "$REPO_ROOT/scripts/k2b-shared-append.py"
  "$REPO_ROOT/scripts/lib/eod_capture.py"
  "$REPO_ROOT/scripts/lib/minimax_common.py"
  "$REPO_ROOT/scripts/lib/minimax_review.py"
  "$REPO_ROOT/scripts/lib/orchestrator_store.py"
  "$REPO_ROOT/scripts/lib/orchestrator_worker.py"
  "$REPO_ROOT/scripts/lib/review_runner.py"
  "$REPO_ROOT/scripts/lib/tier_detection.py"
  "$REPO_ROOT/scripts/loop/loop_lib.py"
)
operational_forbidden=(
  "CLAUDE_PROJECT_DIR"
  "~/.claude/projects"
  ".claude/skills"
  "/Users/keithmbpm2/Projects/K2B/scripts/"
  "/Users/keithcheung/Projects/K2B/scripts/"
  "source \"$HOME/.zshenv\""
)

for loaded_surface in \
  scripts/lib/adversarial-review.md \
  scripts/loop/loop_render.py \
  scripts/research-extract-prompt.md \
  scripts/observer-prompt.md \
  scripts/washing-machine/prompts/washing-machine-classifier-v1.0.txt
do
  if [ -f "$REPO_ROOT/$loaded_surface" ]; then
    active_files+=("$REPO_ROOT/$loaded_surface")
  fi
done

hooks_json="$REPO_ROOT/.codex/hooks.json"
if [ -f "$hooks_json" ]; then
  while IFS= read -r target; do
    [ -n "$target" ] || continue
    [ "$target" = "scripts/verify-codex-authority.sh" ] && continue
    operational_files+=("$REPO_ROOT/$target")
  done < <(grep -oE 'scripts/hooks/[A-Za-z0-9_.-]+' "$hooks_json" 2>/dev/null | sort -u)
fi

vault_root="${K2B_VAULT_PATH:-$HOME/Projects/K2B-Vault}"
active_rules="$vault_root/System/memory/active_rules.md"

if [ ! -e "$skill_root" ]; then
  printf '%s\n' ".agents/skills: missing live skill authority root" >&2
  findings=$((findings + 1))
elif [ ! -d "$skill_root" ]; then
  printf '%s\n' ".agents/skills: live skill authority root is not a directory" >&2
  findings=$((findings + 1))
else
  has_k2b_skill=0
  for path in "$skill_root"/k2b-*/SKILL.md; do
    if [ -f "$path" ] && [ ! -L "$path" ]; then
      has_k2b_skill=1
      break
    fi
  done
  if [ "$has_k2b_skill" -eq 0 ]; then
    printf '%s\n' \
      ".agents/skills: no regular k2b-*/SKILL.md live authority file" >&2
    findings=$((findings + 1))
  fi

  while IFS= read -r path; do
    active_files+=("$path")
  done < <(find "$skill_root" -type f -print | sort)

  # Skill procedures are live authority too. Follow their concrete script
  # references so a clean SKILL.md cannot hide a retired runtime dependency
  # in the executable it tells Codex to run.
  while IFS= read -r target; do
    [ "$target" = "scripts/verify-codex-authority.sh" ] && continue
    [ -f "$REPO_ROOT/$target" ] || continue
    operational_files+=("$REPO_ROOT/$target")
  done < <(
    grep -rhoE 'scripts/[A-Za-z0-9_.@/+:-]+\.(sh|py)' "$skill_root" \
      2>/dev/null | sed 's/[),;:]$//' | sort -u
  )
fi

for path in "${operational_files[@]}"; do
  [ -f "$path" ] || continue
  relative="${path#"$REPO_ROOT"/}"
  for forbidden in "${operational_forbidden[@]}"; do
    while IFS=: read -r line _; do
      [ -n "$line" ] || continue
      printf '%s:%s: forbidden operational dependency: %s\n' \
        "$relative" "$line" "$forbidden" >&2
      findings=$((findings + 1))
    done < <(grep -nF "$forbidden" "$path" 2>/dev/null || true)
  done
done

# Follow project-local MCP launchers from the shared MCP configuration.
if [ -f "$REPO_ROOT/.mcp.json" ]; then
  while IFS= read -r target; do
    [ -f "$REPO_ROOT/$target" ] || continue
    active_files+=("$REPO_ROOT/$target")
  done < <(
    grep -oE 'scripts/[A-Za-z0-9_.@/+:-]+\.sh' "$REPO_ROOT/.mcp.json" \
      2>/dev/null | sort -u
  )
fi

for path in "${active_files[@]}"; do
  if [ ! -f "$path" ]; then
    printf '%s\n' "${path#"$REPO_ROOT"/}: missing active authority file" >&2
    findings=$((findings + 1))
    continue
  fi

  relative="${path#"$REPO_ROOT"/}"
  for forbidden in "${forbidden_dependencies[@]}"; do
    while IFS=: read -r line _; do
      [ -n "$line" ] || continue
      printf '%s:%s: forbidden live dependency: %s\n' \
        "$relative" "$line" "$forbidden" >&2
      findings=$((findings + 1))
    done < <(grep -nF "$forbidden" "$path" 2>/dev/null || true)
  done
done

if [ -e "$vault_root" ]; then
  if [ ! -f "$active_rules" ]; then
    printf '%s\n' "$active_rules: missing loaded vault authority file" >&2
    findings=$((findings + 1))
  else
    for forbidden in "${forbidden_dependencies[@]}"; do
      while IFS=: read -r line _; do
        [ -n "$line" ] || continue
        printf '%s:%s: forbidden loaded rule dependency: %s\n' \
          "$active_rules" "$line" "$forbidden" >&2
        findings=$((findings + 1))
      done < <(grep -nF "$forbidden" "$active_rules" 2>/dev/null | grep -E '^[0-9]+:[0-9]+[.]' || true)
    done
    for forbidden in "${forbidden_live_rules[@]}"; do
      while IFS=: read -r line _; do
        [ -n "$line" ] || continue
        printf '%s:%s: forbidden loaded rule dependency: %s\n' \
          "$active_rules" "$line" "$forbidden" >&2
        findings=$((findings + 1))
      done < <(grep -nF "$forbidden" "$active_rules" 2>/dev/null | grep -E '^[0-9]+:[0-9]+[.]' || true)
    done
  fi
fi

if [ "$findings" -ne 0 ]; then
  printf 'FAIL: Codex authority scan found %s live dependency reference(s)\n' \
    "$findings" >&2
  exit 1
fi

echo "PASS: Codex live authority is independent of Claude state"
