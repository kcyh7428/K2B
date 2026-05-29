#!/usr/bin/env bash
# Standalone adversarial code reviewer (Kimi K2.6 by default; the "minimax" in the name is historical).
# Single-shot, JSON output. Touches nothing in /ship or the codex plugin --
# runs as its own tool.
#
# Usage:
#   scripts/minimax-review.sh                                  # working-tree (default)
#   scripts/minimax-review.sh --focus "auth path"              # extra focus area
#   scripts/minimax-review.sh --json                           # raw JSON to stdout
#   scripts/minimax-review.sh --model kimi-for-coding          # override model
#
# Scopes (Phase B):
#   --scope working-tree                                       # default, all dirty files
#   --scope diff --files a.py,b.py                             # only listed files + diffs
#   --scope plan --plan plans/2026-04-19_my-plan.md            # plan + files it references
#   --scope files --files a.py,b.py                            # explicit list, no git context
#
# Provider routing lives in scripts/lib/minimax_common.py: K2B_LLM_PROVIDER (default
# kimi) -> Kimi K2.6 at api.kimi.com/coding. Set K2B_LLM_PROVIDER=minimax to route to
# MiniMax at MINIMAX_API_HOST (default https://api.minimaxi.com; subscription dead since 2026-05-27).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Non-interactive shells (cron, pm2, Bash tool) don't load ~/.zshrc, so the
# MiniMax key won't be in env. Source it if available; minimax_common.py also
# parses ~/.zshrc as a last resort.
if [ -z "${MINIMAX_API_KEY:-}" ] && [ -f "$HOME/.zshrc" ]; then
  # set +eu while sourcing: a stale `source /path/to/missing-file` line in
  # ~/.zshrc will fail INSIDE .zshrc with set -e still active, killing the
  # whole shell before the trailing `|| true` can catch it. Also disable
  # set -u so unset-variable expansions in .zshrc (referenced before any
  # subsequent export) cannot abort the source the same way. Matches the
  # bracket already used in scripts/minimax-common.sh and claude-minimaxi.sh.
  set +eu
  # shellcheck disable=SC1091
  source "$HOME/.zshrc" 2>/dev/null || true
  set -eu
fi

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || true)"
if [ -z "$REPO_ROOT" ]; then
  echo "minimax-review: must be run inside a git repository" >&2
  exit 1
fi

cd "$REPO_ROOT"
exec python3 "$SCRIPT_DIR/lib/minimax_review.py" "$@"
