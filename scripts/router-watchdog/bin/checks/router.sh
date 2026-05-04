#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=lib.sh
source "$SCRIPT_DIR/lib.sh"

start="$(now_ms)"
set +e
code="$(curl -sS -m 3 \
  -H "Authorization: Bearer ${MIHOMO_API_SECRET}" \
  -o /dev/null \
  -w '%{http_code}' \
  "${MIHOMO_API_BASE%/}/" 2>/dev/null)"
rc=$?
set -e
end="$(now_ms)"
latency=$((end - start))

case "$code" in
  200|301|302|401)
    json_result router_reachable true "$latency" ok "router returned HTTP $code" true "{\"http_code\":\"$code\"}"
    ;;
  *)
    if [[ $rc -ne 0 ]]; then
      json_result router_reachable false "$latency" fail "router did not respond" true "{\"http_code\":\"${code:-000}\"}"
    else
      json_result router_reachable false "$latency" fail "router returned HTTP ${code:-000}" true "{\"http_code\":\"${code:-000}\"}"
    fi
    ;;
esac
