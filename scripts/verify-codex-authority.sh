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
)

findings=0
skill_root="$REPO_ROOT/.agents/skills"

active_files=(
  "$REPO_ROOT/AGENTS.md"
  "$REPO_ROOT/.codex/hooks.json"
  "$REPO_ROOT/scripts/hooks/session-start.sh"
  "$REPO_ROOT/scripts/hooks/post-tool-skill-track.sh"
  "$REPO_ROOT/scripts/hooks/stop-observe.sh"
  "$REPO_ROOT/scripts/hooks/youtube-transcript-prefetch.sh"
)

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

if [ "$findings" -ne 0 ]; then
  printf 'FAIL: Codex authority scan found %s live dependency reference(s)\n' \
    "$findings" >&2
  exit 1
fi

echo "PASS: Codex live authority is independent of Claude state"
