#!/usr/bin/env bash
# Optional local MCP: read the owning machine's private plugin credential.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
exec python3 "$SCRIPT_DIR/lib/obsidian_mcp.py"
