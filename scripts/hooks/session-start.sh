#!/bin/bash
# Codex SessionStart uses the same local renderer as the Kimi Work entry path.
# This adapter is registered with Codex's bounded 20s hook allowance. Its
# Python-failure shell fallback is not a standalone Kimi Work entry point.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if packet="$(PYTHONDONTWRITEBYTECODE=1 python3 "$SCRIPT_DIR/../k2b-bootstrap.py" 2>/dev/null)" && [ -n "$packet" ]; then
  printf '%s\n' "$packet"
else
  k2b_setup_error="K2B SHARED STARTUP: local-copy setup is unavailable; no vault sources loaded. Check read-vault.json or use an explicit NAS path after mount preflight."
  if [ -e "$HOME/.local/state/k2b/read-vault.json" ] || [ -L "$HOME/.local/state/k2b/read-vault.json" ]; then
    printf '%s\n' "$k2b_setup_error"
    exit 0
  fi
  k2b_copy_root="$(cd -P "$HOME/Projects/K2B-Vault-Offline/SynologyDrive" 2>/dev/null && pwd -P)" \
    || k2b_copy_root="$HOME/Projects/K2B-Vault-Offline/SynologyDrive"
  k2b_fallback_root="${K2B_VAULT_PATH:-$HOME/Projects/K2B-Vault}"
  k2b_fallback_root="$(cd -P "$k2b_fallback_root" 2>/dev/null && pwd -P)" \
    || k2b_fallback_root="${K2B_VAULT_PATH:-$HOME/Projects/K2B-Vault}"
  k2b_copy_fold="$(printf '%s/' "$k2b_copy_root" | LC_ALL=C tr '[:upper:]' '[:lower:]')"
  k2b_fallback_fold="$(printf '%s/' "$k2b_fallback_root" | LC_ALL=C tr '[:upper:]' '[:lower:]')"
  case "$k2b_fallback_fold" in
    "$k2b_copy_fold"*)
      printf '%s\n' "$k2b_setup_error"
      exit 0
      ;;
  esac
  echo "K2B SHARED STARTUP: renderer unavailable; local shell fallback only"
  exec /bin/bash "$SCRIPT_DIR/bootstrap-local.sh"
fi
