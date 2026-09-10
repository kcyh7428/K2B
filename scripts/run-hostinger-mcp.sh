#!/usr/bin/env bash
# Load the per-machine Hostinger credential and run its MCP server.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# shellcheck source=lib/private-env.sh
source "$SCRIPT_DIR/lib/private-env.sh"

if [[ -z "${HOSTINGER_API_TOKEN:-}" ]]; then
  k2b_load_private_env "${K2B_ENV_FILE:-${HOME:-}/.k2b-env}" || exit 1
fi

if [[ -z "${HOSTINGER_API_TOKEN:-}" ]]; then
  echo "ERROR: HOSTINGER_API_TOKEN not set in the environment or per-machine ~/.k2b-env" >&2
  exit 1
fi

# The MCP expects API_TOKEN. Export only that name to the child process and
# remove the K2B-facing alias before exec. Do not put the token in argv.
API_TOKEN="$HOSTINGER_API_TOKEN"
export API_TOKEN
unset HOSTINGER_API_TOKEN
exec npx -y hostinger-api-mcp@1.58.0
