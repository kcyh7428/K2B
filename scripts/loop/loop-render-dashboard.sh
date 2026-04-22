#!/usr/bin/env bash
# Render the K2B loop dashboard. Called from scripts/hooks/session-start.sh.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

VAULT_DEFAULT="$HOME/Projects/K2B-Vault"
export K2B_LOOP_CANDIDATES="${K2B_LOOP_CANDIDATES:-$VAULT_DEFAULT/wiki/context/observer-candidates.md}"
export K2B_LOOP_REVIEW_DIR="${K2B_LOOP_REVIEW_DIR:-$VAULT_DEFAULT/review}"
export K2B_LOOP_RESEARCH_DIR="${K2B_LOOP_RESEARCH_DIR:-$VAULT_DEFAULT/raw/research}"

python3 "$SCRIPT_DIR/loop_render.py"
