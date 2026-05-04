#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=lib.sh
source "$SCRIPT_DIR/lib.sh"

start="$(now_ms)"
pm2_output="$(
  python3 - <<'PY'
import subprocess, sys
try:
    p = subprocess.run(["pm2", "jlist"], capture_output=True, text=True, timeout=5)
except FileNotFoundError:
    print("__K2B_PM2_ERROR__:pm2 command not found")
    sys.exit(0)
except subprocess.TimeoutExpired:
    print("__K2B_PM2_ERROR__:pm2 jlist timed out")
    sys.exit(0)
if p.returncode != 0:
    print("__K2B_PM2_ERROR__:pm2 jlist failed")
else:
    print(p.stdout)
PY
)"
end="$(now_ms)"
latency=$((end - start))

if [[ "$pm2_output" == __K2B_PM2_ERROR__:* ]]; then
  msg="${pm2_output#__K2B_PM2_ERROR__:}"
  json_result pm2_daemon false "$latency" fail "$msg" true "{}"
  json_result pm2_services false "$latency" fail "$msg" true "{}"
  exit 0
fi

analysis="$(
  PM2_OUTPUT="$pm2_output" python3 - <<'PY'
import json, os
required = ["k2b-remote", "k2b-dashboard", "k2b-observer-loop"]
try:
    procs = json.loads(os.environ["PM2_OUTPUT"])
except Exception:
    print(json.dumps({"daemon_ok": False, "ok": False, "message": "pm2 jlist returned invalid JSON", "restart_times": {}}))
    raise SystemExit
by_name = {p.get("name"): p for p in procs if isinstance(p, dict)}
missing = [name for name in required if name not in by_name]
offline = []
restart_times = {}
for name in required:
    p = by_name.get(name)
    if not p:
        continue
    env = p.get("pm2_env") or {}
    restart_times[name] = int(env.get("restart_time") or 0)
    if env.get("status") != "online":
        offline.append(f"{name}:{env.get('status')}")
if missing or offline:
    pieces = []
    if missing:
        pieces.append("missing " + ",".join(missing))
    if offline:
        pieces.append("offline " + ",".join(offline))
    ok = False
    msg = "; ".join(pieces)
else:
    ok = True
    msg = "required pm2 services online"
print(json.dumps({"daemon_ok": True, "ok": ok, "message": msg, "restart_times": restart_times}, separators=(",", ":")))
PY
)"

daemon_ok="$(printf '%s' "$analysis" | python3 -c 'import json,sys; print(str(json.load(sys.stdin)["daemon_ok"]).lower())')"
services_ok="$(printf '%s' "$analysis" | python3 -c 'import json,sys; print(str(json.load(sys.stdin)["ok"]).lower())')"
message="$(printf '%s' "$analysis" | python3 -c 'import json,sys; print(json.load(sys.stdin)["message"])')"
restart_details="$(printf '%s' "$analysis" | python3 -c 'import json,sys; print(json.dumps({"pm2_restart_times": json.load(sys.stdin)["restart_times"]}, separators=(",", ":")))')"

if [[ "$daemon_ok" == "true" ]]; then
  json_result pm2_daemon true "$latency" ok "pm2 daemon returned valid JSON" true "{}"
else
  json_result pm2_daemon false "$latency" fail "$message" true "{}"
fi

if [[ "$services_ok" == "true" ]]; then
  json_result pm2_services true "$latency" ok "$message" true "$restart_details"
else
  json_result pm2_services false "$latency" fail "$message" true "$restart_details"
fi
