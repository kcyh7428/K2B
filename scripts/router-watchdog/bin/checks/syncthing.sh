#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=lib.sh
source "$SCRIPT_DIR/lib.sh"

start="$(now_ms)"
set +e
pgrep -f syncthing >/dev/null 2>&1
rc=$?
set -e
end="$(now_ms)"
latency=$((end - start))

if [[ $rc -eq 0 ]]; then
  json_result syncthing_process true "$latency" ok "syncthing process found" true "{}"
else
  json_result syncthing_process false "$latency" fail "syncthing process not found" true "{}"
fi
