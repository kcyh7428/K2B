#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=lib.sh
source "$SCRIPT_DIR/lib.sh"

target_ip="${K2B_MACBOOK_TAILSCALE_IP:-100.68.35.19}"
start="$(now_ms)"
set +e
tailscale_bin=""
if [[ -n "${K2B_TAILSCALE_BIN:-}" && -x "$K2B_TAILSCALE_BIN" ]]; then
  tailscale_bin="$K2B_TAILSCALE_BIN"
elif [[ -x /Applications/Tailscale.app/Contents/MacOS/Tailscale ]]; then
  tailscale_bin="/Applications/Tailscale.app/Contents/MacOS/Tailscale"
elif command -v tailscale >/dev/null 2>&1; then
  tailscale_bin="$(command -v tailscale)"
fi
if [[ -n "$tailscale_bin" ]]; then
  status_json="$("$tailscale_bin" status --json 2>/dev/null)"
  rc=$?
else
  status_json=""
  rc=127
fi
set -e
end="$(now_ms)"
latency=$((end - start))

if [[ $rc -ne 0 || -z "$status_json" ]]; then
  json_result tailscale_direct false "$latency" fail "tailscale status unavailable" false "{}"
  exit 0
fi

direct_count="$(
  TAILSCALE_STATUS_JSON="$status_json" python3 - "$target_ip" <<'PY' 2>/dev/null || echo 0
import json, os, sys
target = sys.argv[1]
d = json.loads(os.environ["TAILSCALE_STATUS_JSON"])
peer = {}
for item in (d.get("Peer") or {}).values():
    if target in (item.get("TailscaleIPs") or []):
        peer = item
        break
print(len(peer.get("DirectAddrs") or []))
PY
)"

if [[ "$direct_count" =~ ^[0-9]+$ && "$direct_count" -gt 0 ]]; then
  json_result tailscale_direct true "$latency" ok "tailscale direct path present" false "{\"direct_addrs\":$direct_count}"
else
  json_result tailscale_direct true "$latency" warn "tailscale relay-only or direct path missing" false "{\"direct_addrs\":0}"
fi
