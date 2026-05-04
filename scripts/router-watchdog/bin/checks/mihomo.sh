#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=lib.sh
source "$SCRIPT_DIR/lib.sh"

json_quote() {
  python3 - "$1" <<'PY'
import json, sys
print(json.dumps(sys.argv[1], ensure_ascii=False))
PY
}

urlencode() {
  python3 -c 'import urllib.parse, sys; print(urllib.parse.quote(sys.argv[1], safe=""))' "$1"
}

start="$(now_ms)"
set +e
version_body="$(curl -sS -m 3 \
  -H "Authorization: Bearer ${MIHOMO_API_SECRET}" \
  "${MIHOMO_API_BASE%/}/version" 2>/dev/null)"
version_rc=$?
set -e
end="$(now_ms)"
latency=$((end - start))

if [[ $version_rc -eq 0 ]] && printf '%s' "$version_body" | python3 -c 'import json, sys; json.load(sys.stdin)' 2>/dev/null; then
  json_result mihomo_api true "$latency" ok "mihomo version JSON parsed" true "{}"
else
  json_result mihomo_api false "$latency" fail "mihomo version JSON unavailable" true "{}"
fi

group_enc="$(urlencode "$MIHOMO_OPENAI_GROUP")"
start="$(now_ms)"
set +e
proxy_body="$(curl -sS -m 3 \
  -H "Authorization: Bearer ${MIHOMO_API_SECRET}" \
  "${MIHOMO_API_BASE%/}/proxies/${group_enc}" 2>/dev/null)"
proxy_rc=$?
set -e
end="$(now_ms)"
latency=$((end - start))

node_details="$(
  if [[ $proxy_rc -eq 0 ]]; then
    PROXY_BODY="$proxy_body" python3 - "$MIHOMO_API_BASE" "$MIHOMO_API_SECRET" "$MIHOMO_OPENAI_GROUP" <<'PY' 2>/dev/null || true
import json
import os
import sys
import urllib.parse
import urllib.request

base, secret, group = sys.argv[1], sys.argv[2], sys.argv[3]
base = base.rstrip("/")

try:
    current = json.loads(os.environ.get("PROXY_BODY", ""))
except json.JSONDecodeError:
    sys.exit(0)

chain = [group]
seen = {group}
first = current.get("now") or ""
leaf = first
if first:
    chain.append(first)
    seen.add(first)

def fetch_proxy(name):
    url = f"{base}/proxies/{urllib.parse.quote(name, safe='')}"
    req = urllib.request.Request(url, headers={"Authorization": "Bearer " + secret})
    with urllib.request.urlopen(req, timeout=2) as resp:
        return json.load(resp)

while leaf:
    try:
        data = fetch_proxy(leaf)
    except Exception:
        break
    nxt = data.get("now") or ""
    if not nxt or nxt in seen:
        break
    chain.append(nxt)
    seen.add(nxt)
    leaf = nxt

details = {
    "selected_node": leaf or first,
    "openai_group_selection": first,
    "selector_chain": chain,
}
print(json.dumps(details, ensure_ascii=False, separators=(",", ":")))
PY
  fi
)"

node="$(
  if [[ -n "$node_details" ]]; then
    printf '%s' "$node_details" | python3 -c 'import json, sys; print(json.load(sys.stdin).get("selected_node",""))' 2>/dev/null || true
  fi
)"

chain="$(
  if [[ -n "$node_details" ]]; then
    printf '%s' "$node_details" | python3 -c 'import json, sys; print(" -> ".join(json.load(sys.stdin).get("selector_chain", [])))' 2>/dev/null || true
  fi
)"

if [[ -n "$node" ]]; then
  if [[ -n "$chain" && "$chain" != "$node" ]]; then
    json_result openai_node true "$latency" ok "OpenAI selected node: $node via $chain" false "$node_details"
  else
    json_result openai_node true "$latency" ok "OpenAI selected node: $node" false "$node_details"
  fi
else
  json_result openai_node true "$latency" warn "OpenAI selected node unavailable" false "{}"
fi
