#!/bin/bash
# Wrapper that loads HOSTINGER_API_TOKEN from ~/.zshenv and execs the Hostinger MCP.
# Needed because Claude Code does not expand ${VAR} references in .mcp.json env blocks
# on macOS GUI launches (launchd doesn't source ~/.zshenv).

set -euo pipefail

# Sanity: HOME must be set so $HOME/.zshenv expansion is meaningful.
if [ -z "${HOME:-}" ]; then
  echo "ERROR: HOME not set; cannot locate ~/.zshenv" >&2
  exit 1
fi

# Source token from ~/.zshenv if not already in env. Sourcing the entire shell
# rc file is intentional and matches the file's purpose (env var declarations).
# The threat model assumes ~/.zshenv is owned and controlled by the same user
# running Claude Code -- the same trust boundary that already runs zsh login.
if [ -z "${HOSTINGER_API_TOKEN:-}" ] && [ -f "$HOME/.zshenv" ]; then
  # shellcheck disable=SC1091
  source "$HOME/.zshenv"
fi

if [ -z "${HOSTINGER_API_TOKEN:-}" ]; then
  echo "ERROR: HOSTINGER_API_TOKEN not set. Add 'export HOSTINGER_API_TOKEN=\"...\"' to ~/.zshenv" >&2
  exit 1
fi

# The MCP expects API_TOKEN, not HOSTINGER_API_TOKEN. Pass it via `exec env` so
# the variable is scoped to the spawned MCP process tree only -- it never enters
# this wrapper's own environment, and any sibling commands run by Claude Code
# don't see it.
exec env API_TOKEN="$HOSTINGER_API_TOKEN" npx -y hostinger-api-mcp@latest
