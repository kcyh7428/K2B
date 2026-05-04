#!/usr/bin/env bash

now_ms() {
  python3 - <<'PY'
import time
print(int(time.time() * 1000))
PY
}

json_result() {
  local name="$1"
  local ok="$2"
  local latency_ms="$3"
  local severity="$4"
  local message="$5"
  local alertable="${6:-true}"
  local details_json="${7:-}"
  if [[ -z "$details_json" ]]; then
    details_json="{}"
  fi

  python3 - "$name" "$ok" "$latency_ms" "$severity" "$message" "$alertable" "$details_json" <<'PY'
import json, sys

name, ok, latency, severity, message, alertable, details = sys.argv[1:]
try:
    details_obj = json.loads(details)
except json.JSONDecodeError:
    details_obj = {}

print(json.dumps({
    "name": name,
    "ok": ok.lower() == "true",
    "alertable": alertable.lower() == "true",
    "latency_ms": int(latency),
    "severity": severity,
    "message": message,
    "details": details_obj,
}, ensure_ascii=False, separators=(",", ":")))
PY
}

http_reachable_result() {
  local name="$1"
  local url="$2"
  local timeout="$3"
  shift 3

  local start end latency output rc code
  start="$(now_ms)"
  set +e
  output="$(curl -sS -m "$timeout" --noproxy '*' -o /dev/null -w '%{http_code}' "$@" "$url" 2>&1)"
  rc=$?
  set -e
  end="$(now_ms)"
  latency=$((end - start))
  code="${output: -3}"

  if [[ $rc -eq 0 && "$code" != "000" ]]; then
    json_result "$name" true "$latency" ok "HTTP $code" true "{\"http_code\":\"$code\"}"
  else
    json_result "$name" false "$latency" fail "HTTP handshake failed (${code:-000})" true "{\"http_code\":\"${code:-000}\"}"
  fi
}
