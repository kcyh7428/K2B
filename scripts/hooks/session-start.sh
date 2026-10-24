#!/bin/bash
# Codex SessionStart uses the same local renderer as the Kimi Work entry path.
# This adapter is registered with Codex's bounded 20s hook allowance. Its
# Python-failure shell fallback is not a standalone Kimi Work entry point.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if packet="$(PYTHONDONTWRITEBYTECODE=1 python3 "$SCRIPT_DIR/../k2b-bootstrap.py" 2>/dev/null)" && [ -n "$packet" ]; then
  printf '%s\n' "$packet"
else
  echo "K2B SHARED STARTUP: renderer unavailable; local shell fallback only"
  exec /bin/bash "$SCRIPT_DIR/bootstrap-local.sh"
fi
