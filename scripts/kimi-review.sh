#!/usr/bin/env bash
# Canonical Kimi-backed adversarial code reviewer.
# Historical compatibility alias: scripts/minimax-review.sh
# Single-shot, JSON output. Touches nothing in /ship or the codex plugin --
# runs as its own tool.
#
# Usage:
#   scripts/kimi-review.sh                                  # working-tree (default)
#   scripts/kimi-review.sh --focus "auth path"              # extra focus area
#   scripts/kimi-review.sh --json                           # raw JSON to stdout
#   scripts/kimi-review.sh --model kimi-k2.7-code           # override model
#
# Scopes (Phase B):
#   --scope working-tree                                    # default, all dirty files
#   --scope diff --files a.py,b.py                          # only listed files + diffs
#   --scope plan --plan plans/2026-04-19_my-plan.md         # plan + files it references
#   --scope files --files a.py,b.py                         # explicit list, no git context
#
# Provider routing lives in scripts/lib/minimax_common.py: K2B_LLM_PROVIDER
# (default kimi) -> Kimi K2.7 Code at api.kimi.com/coding. Set
# K2B_LLM_PROVIDER=minimax to route to the legacy MiniMax fallback at
# MINIMAX_API_HOST (default https://api.minimaxi.com; subscription dead since
# 2026-05-27).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Track whether help was explicitly requested.
HELP_REQUEST=0
for arg in "$@"; do
  if [[ "$arg" == "--help" || "$arg" == "-h" ]]; then
    HELP_REQUEST=1
  fi
done

if [[ "${HELP_REQUEST:-0}" -eq 1 ]]; then
  cat <<'USAGE'
Usage: scripts/kimi-review.sh [--help] [flags]

Usage:
  scripts/kimi-review.sh                                  # working-tree (default)
  scripts/kimi-review.sh --focus "auth path"              # extra focus area
  scripts/kimi-review.sh --json                           # raw JSON to stdout
  scripts/kimi-review.sh --model kimi-k2.7-code           # override model

Scopes:
  --scope working-tree                                    # default, all dirty files
  --scope diff --files a.py,b.py                          # only listed files + diffs
  --scope plan --plan plans/2026-04-19_my-plan.md         # plan + files it references
  --scope files --files a.py,b.py                         # explicit list, no git context

Environment:
  - K2B_LLM_PROVIDER=kimi|minimax (default: kimi)
  - K2B_LLM_MODEL (defaults per provider)
  - KIMI_API_KEY / MINIMAX_API_KEY as required by provider
USAGE
  exit 0
fi

# Shared wrapper initialization: handles provider mapping, optional ~/.zshrc
# fallback, KIMI_MODEL defaults, and key fail-fast behavior.
source "$SCRIPT_DIR/minimax-common.sh"

if [[ "${K2B_LLM_PROVIDER:-kimi}" == "kimi" ]]; then
  if [[ -z "${KIMI_API_KEY:-}" ]]; then
    echo "kimi-review: KIMI_API_KEY not set" >&2
    exit 1
  fi
  if (( ${#KIMI_API_KEY} < 16 )); then
    echo "kimi-review: KIMI_API_KEY is too short" >&2
    exit 1
  fi
fi

if [[ "${K2B_LLM_PROVIDER:-kimi}" == "minimax" ]]; then
  echo "kimi-review: K2B_LLM_PROVIDER=minimax is deprecated and disabled (MiniMax subscription expired)." >&2
  echo "Set K2B_LLM_PROVIDER=kimi and rerun." >&2
  exit 1
fi

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || true)"
if [ -z "$REPO_ROOT" ]; then
  echo "kimi-review: must be run inside a git repository" >&2
  exit 1
fi

cd "$REPO_ROOT"
exec python3 "$SCRIPT_DIR/lib/minimax_review.py" "$@"
