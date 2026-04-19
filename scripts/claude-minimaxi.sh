#!/usr/bin/env bash
# claude-minimaxi -- run Claude Code backed by MiniMax-M2.7 instead of Opus.
#
# Named after minimaxi.com (MiniMax's China region -- where Keith's API key is minted)
# to avoid confusion with the Mac Mini server.
#
# Usage:
#   claude-minimaxi "your task here" [extra claude flags]
#   echo "your task here" | claude-minimaxi [extra claude flags]
#
# Examples:
#   claude-minimaxi "what is 17 times 23"
#   claude-minimaxi "summarize this repo" --add-dir ~/Projects/K2B
#   cat prompt.md | claude-minimaxi --add-dir ~/Projects/K2B/.claude/skills
#
# Behavior:
#   - First positional arg (if not a flag) becomes the prompt, fed via stdin to
#     work around Claude Code's variadic --add-dir parsing bug.
#   - All remaining args pass through to `claude -p`.
#   - Shell env vars like CLAUDE_CODE_OAUTH_TOKEN and ANTHROPIC_API_KEY are
#     unset for the subprocess so they cannot override MiniMax auth.
#   - MiniMax endpoint + model are injected via --settings JSON (the only
#     mechanism that reliably overrides the Claude Code desktop app's auth).

set -euo pipefail

# The shell that invokes this script may be non-interactive (e.g. Claude Code's
# Bash tool) and may not have sourced ~/.zshrc. If MINIMAX_API_KEY isn't in
# env, try sourcing the user's shell profile as a fallback.
if [[ -z "${MINIMAX_API_KEY:-}" && -f "$HOME/.zshrc" ]]; then
  set +u
  source "$HOME/.zshrc" >/dev/null 2>&1 || true
  set -u
fi

: "${MINIMAX_API_KEY:?MINIMAX_API_KEY not set -- add export to ~/.zshrc and reload}"

# Source minimax-common.sh for log_job_invocation (observability contract
# shared with all other MiniMax worker scripts -- appends to minimax-jobs.jsonl).
# Resolve symlinks so installs via ~/.local/bin/claude-minimaxi find the sibling
# minimax-common.sh in the real scripts/ dir, not the symlink's dir.
SOURCE="${BASH_SOURCE[0]}"
_SYMLINK_HOPS=0
while [ -L "$SOURCE" ]; do
  if [ "$_SYMLINK_HOPS" -gt 20 ]; then
    echo "claude-minimaxi: symlink chain too deep or cyclic at $SOURCE" >&2
    exit 1
  fi
  _SYMLINK_HOPS=$((_SYMLINK_HOPS + 1))
  DIR="$(cd -P "$(dirname "$SOURCE")" >/dev/null && pwd)"
  SOURCE="$(readlink "$SOURCE")"
  [[ $SOURCE != /* ]] && SOURCE="$DIR/$SOURCE"
done
SCRIPT_DIR="$(cd -P "$(dirname "$SOURCE")" >/dev/null && pwd)"
# shellcheck source=/dev/null
source "$SCRIPT_DIR/minimax-common.sh"

# --settings JSON: injects the MiniMax endpoint + auth via Claude Code's
# documented settings path. Shell env vars do NOT reliably override the
# desktop app's OAuth token -- settings-level env does.
SETTINGS_JSON='{"env":{"ANTHROPIC_BASE_URL":"https://api.minimaxi.com/anthropic","ANTHROPIC_AUTH_TOKEN":"'"$MINIMAX_API_KEY"'","ANTHROPIC_MODEL":"MiniMax-M2.7"}}'

# If first arg exists and doesn't start with `-`, treat it as the prompt.
# Feed it via stdin so the variadic --add-dir flag can't swallow it.
PROMPT=""
if [[ $# -gt 0 && "$1" != -* ]]; then
  PROMPT="$1"
  shift
fi

# Run claude -p (backed by MiniMax) and capture output + duration so we can
# log metrics for the measurement dashboard. stderr is captured separately so
# it stays on stderr for pipe-friendly callers (e.g. `claude-minimaxi "x" | jq`).
START_MS=$(python3 -c 'import time; print(int(time.time()*1000))')
INPUT_BYTES=${#PROMPT}
ERR_FILE=$(mktemp -t claude-minimaxi-err.XXXXXX)
trap 'rm -f "$ERR_FILE"' EXIT

set +e
# K2B_OFFLOADED=1 is a sentinel: skills that would otherwise dispatch to
# claude-minimaxi check this env var and skip the re-dispatch when set, to
# avoid infinite recursion. See wiki/context/context_claude-minimaxi-routing.md.
if [[ -n "$PROMPT" ]]; then
  OUTPUT=$(env -u CLAUDE_CODE_OAUTH_TOKEN -u ANTHROPIC_API_KEY \
    K2B_OFFLOADED=1 \
    claude -p --dangerously-skip-permissions --settings "$SETTINGS_JSON" "$@" <<<"$PROMPT" 2>"$ERR_FILE")
else
  OUTPUT=$(env -u CLAUDE_CODE_OAUTH_TOKEN -u ANTHROPIC_API_KEY \
    K2B_OFFLOADED=1 \
    claude -p --dangerously-skip-permissions --settings "$SETTINGS_JSON" "$@" 2>"$ERR_FILE")
fi
EXIT=$?
set -e

END_MS=$(python3 -c 'import time; print(int(time.time()*1000))')
DURATION_MS=$((END_MS - START_MS))
OUTPUT_BYTES=${#OUTPUT}
PARSE_STATUS=$([[ $EXIT -eq 0 ]] && echo "ok" || echo "error")

# Log to wiki/context/minimax-jobs.jsonl via the shared helper. Use a distinct
# job label ("claude-minimaxi-session") so the dashboard can separate wrapper
# invocations from batch worker invocations (compile / research-extract / etc).
log_job_invocation "claude-minimaxi-session" "wrapper-v1" "MiniMax-M2.7" "$INPUT_BYTES" "$OUTPUT_BYTES" "$PARSE_STATUS" "$DURATION_MS" || true

# Emit stderr (so error messages show up in the terminal as usual) then stdout.
# Preserve the claude -p exit code.
if [[ -s "$ERR_FILE" ]]; then
  cat "$ERR_FILE" >&2
fi
printf '%s\n' "$OUTPUT"
exit $EXIT
