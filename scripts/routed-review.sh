#!/usr/bin/env bash
# Internal entry for bounded Qwen / standard Kimi review. No trial overrides.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "$SCRIPT_DIR/lib/minimax_review.py" "$@"
