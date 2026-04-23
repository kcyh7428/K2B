#!/usr/bin/env bash
# scripts/washing-machine/classify.sh
# Washing Machine Normalization Gate classifier. Wraps minimax-json-job.sh
# with the frozen v1.0 prompt, returns one JSON object per message:
#   {keep, category?, shelf?, discard_reason?, entities?, timestamp_iso?, date_confidence?}
#
# Usage
#   classify.sh --anchor YYYY-MM-DD [--input FILE|-]
#
# Reads the user message from --input (or stdin). Prepends an [anchor]
# context line before handing to the classifier. Emits the classifier's
# raw JSON on stdout; errors on stderr. Exit 0 only on valid JSON.
#
# Plan: plans/2026-04-21_washing-machine-ship-1.md Commit 3 (compressed
# 2026-04-23 per wiki/concepts/feature_washing-machine-memory.md Updates).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
PROMPT_PATH="$SCRIPT_DIR/prompts/washing-machine-classifier-v1.0.txt"
MINIMAX_JOB="$REPO_ROOT/scripts/minimax-json-job.sh"

ANCHOR=""
INPUT_PATH="-"

usage() {
  cat >&2 <<'EOF'
usage: classify.sh --anchor YYYY-MM-DD [--input FILE|-]

  --anchor  reference date for relative-date resolution (required, YYYY-MM-DD)
  --input   file path to the user message. Use - (or omit) for stdin.

Emits JSON on stdout. Exit 0 on success, non-zero on error.
EOF
  exit 64
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --anchor) ANCHOR="${2:-}"; shift 2 ;;
    --input)  INPUT_PATH="${2:-}"; shift 2 ;;
    -h|--help) usage ;;
    *) echo "classify.sh: unknown arg '$1'" >&2; usage ;;
  esac
done

[ -n "$ANCHOR" ] || usage
if ! printf '%s' "$ANCHOR" | grep -Eq '^[0-9]{4}-[0-9]{2}-[0-9]{2}$'; then
  echo "classify.sh: --anchor must be YYYY-MM-DD, got '$ANCHOR'" >&2
  exit 64
fi
if [ ! -f "$PROMPT_PATH" ]; then
  echo "classify.sh: prompt missing at $PROMPT_PATH" >&2
  exit 2
fi
if [ ! -x "$MINIMAX_JOB" ] && [ ! -f "$MINIMAX_JOB" ]; then
  echo "classify.sh: minimax-json-job.sh missing at $MINIMAX_JOB" >&2
  exit 2
fi

if [ "$INPUT_PATH" = "-" ]; then
  user_text="$(cat)"
else
  if [ ! -f "$INPUT_PATH" ]; then
    echo "classify.sh: input file not found: $INPUT_PATH" >&2
    exit 2
  fi
  user_text="$(cat "$INPUT_PATH")"
fi

trimmed="${user_text//[$' \t\r\n']/}"
if [ -z "$trimmed" ]; then
  echo "classify.sh: user message is empty or whitespace-only" >&2
  exit 2
fi

# The classifier sees the anchor context line then the user message.
# Keep the framing stable: the prompt already documents this shape.
TMP_INPUT="$(mktemp "${TMPDIR:-/tmp}/wm-classify.XXXXXX")"
trap 'rm -f "$TMP_INPUT"' EXIT
{
  printf '[anchor] %s\n' "$ANCHOR"
  printf '%s' "$user_text"
} >"$TMP_INPUT"

"$MINIMAX_JOB" \
  --prompt "$PROMPT_PATH" \
  --input "$TMP_INPUT" \
  --job-name washing-machine-classify \
  --prompt-version "classifier-v1.0" \
  --model MiniMax-M2.7 \
  --max-tokens 1200 \
  --temperature 0.1 \
  --system-name "K2B Washing Machine" \
  --role-name "telegram-user"
