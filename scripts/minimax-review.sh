#!/usr/bin/env bash
# Historical compatibility alias for scripts/kimi-review.sh.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$SCRIPT_DIR/kimi-review.sh" "$@"
