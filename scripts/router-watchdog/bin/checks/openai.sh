#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=lib.sh
source "$SCRIPT_DIR/lib.sh"

http_reachable_result chatgpt_https "https://chatgpt.com/" 5
http_reachable_result chatgpt_ws "https://ws.chatgpt.com/" 5
