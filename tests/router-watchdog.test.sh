#!/usr/bin/env bash
# tests/router-watchdog.test.sh
# Local integration tests for the Phase 1 router watchdog core.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC_DIR="$REPO_ROOT/scripts/router-watchdog"
INSTALL="$SRC_DIR/install.sh"
STATE_MACHINE="$SRC_DIR/bin/state-machine.py"
PARTITION_QUEUE="$SRC_DIR/bin/partition-queue.py"
ROLLUP="$SRC_DIR/bin/rollup.sh"

TMPROOT="$(mktemp -d)"
SERVER_PIDS=()
trap 'for pid in "${SERVER_PIDS[@]:-}"; do kill "$pid" 2>/dev/null || true; done; rm -rf "$TMPROOT"' EXIT

fail() { echo "FAIL: $*" >&2; exit 1; }

write_results() {
  local out="$1" failing="${2:-}"
  python3 - "$out" "$failing" <<'PY'
import json, sys
out, failing = sys.argv[1], sys.argv[2]
names = [
    "router_reachable",
    "mihomo_api",
    "openai_node",
    "chatgpt_https",
    "chatgpt_ws",
    "claude_https",
    "telegram_api",
    "tailscale_direct",
    "syncthing_process",
    "pm2_daemon",
    "pm2_services",
]
fail_set = {x for x in failing.split(",") if x}
results = []
for name in names:
    ok = name not in fail_set
    results.append({
        "name": name,
        "ok": ok,
        "alertable": name not in {"openai_node", "tailscale_direct"},
        "latency_ms": 12,
        "severity": "ok" if ok else "fail",
        "message": "ok" if ok else f"{name} failed",
        "details": {"pm2_restart_times": {"k2b-remote": 0}} if name == "pm2_services" else {},
    })
with open(out, "w", encoding="utf-8") as f:
    json.dump(results, f)
PY
}

run_state_machine() {
  local state="$1" results="$2" ts="$3" alerts="$4" actions="$5" health="$6"
  python3 "$STATE_MACHINE" \
    --state-file "$state" \
    --results-file "$results" \
    --timestamp "$ts" \
    --backoff "30s,2m,6m,24h" \
    --alerts-file "$alerts" \
    --partition-actions-file "$actions" \
    --health-log "$health"
}

count_lines() {
  local path="$1"
  [[ -f "$path" ]] || { echo 0; return; }
  wc -l < "$path" | tr -d ' '
}

echo "=== router-watchdog.test.sh ==="

# ---------------------------------------------------------------------------
# Test 1: third consecutive failure alerts; recovery alerts only after alert.
# ---------------------------------------------------------------------------
{
  d="$TMPROOT/state-machine-basic"
  mkdir -p "$d"
  state="$d/state.json"
  health="$d/health.jsonl"
  results="$d/results.json"

  write_results "$results" "pm2_services"
  run_state_machine "$state" "$results" "2026-05-03T00:00:00Z" "$d/a1.jsonl" "$d/p1.jsonl" "$health"
  [[ "$(count_lines "$d/a1.jsonl")" = 0 ]] || fail "first failure should not alert"

  run_state_machine "$state" "$results" "2026-05-03T00:10:00Z" "$d/a2.jsonl" "$d/p2.jsonl" "$health"
  [[ "$(count_lines "$d/a2.jsonl")" = 0 ]] || fail "second failure should not alert"

  run_state_machine "$state" "$results" "2026-05-03T00:20:00Z" "$d/a3.jsonl" "$d/p3.jsonl" "$health"
  [[ "$(count_lines "$d/a3.jsonl")" = 1 ]] || fail "third failure should alert once"
  grep -q '"type":"failure"' "$d/a3.jsonl" || fail "third alert should be a failure alert"

  write_results "$results" ""
  run_state_machine "$state" "$results" "2026-05-03T00:30:00Z" "$d/a4.jsonl" "$d/p4.jsonl" "$health"
  [[ "$(count_lines "$d/a4.jsonl")" = 1 ]] || fail "recovery after an alert should alert once"
  grep -q '"type":"recovery"' "$d/a4.jsonl" || fail "recovery alert type missing"
  echo "  PASS: alert transition and recovery"
}

# ---------------------------------------------------------------------------
# Test 2: compressed 6-hour outage sends initial + 3 backoffs + recovery only.
# ---------------------------------------------------------------------------
{
  d="$TMPROOT/state-machine-backoff"
  mkdir -p "$d"
  state="$d/state.json"
  health="$d/health.jsonl"
  results="$d/results.json"
  all_alerts="$d/all-alerts.jsonl"
  : > "$all_alerts"

  write_results "$results" "chatgpt_ws"
  for ts in \
    "2026-05-03T00:00:00Z" \
    "2026-05-03T00:10:00Z" \
    "2026-05-03T00:20:00Z" \
    "2026-05-03T00:20:30Z" \
    "2026-05-03T00:22:30Z" \
    "2026-05-03T00:28:30Z" \
    "2026-05-03T00:35:00Z"; do
    alerts="$d/alerts-${ts//[:T-]/}.jsonl"
    run_state_machine "$state" "$results" "$ts" "$alerts" "$d/actions-${ts//[:T-]/}.jsonl" "$health"
    [[ -f "$alerts" ]] && cat "$alerts" >> "$all_alerts"
  done

  write_results "$results" ""
  run_state_machine "$state" "$results" "2026-05-03T00:36:00Z" "$d/recovery.jsonl" "$d/recovery-actions.jsonl" "$health"
  cat "$d/recovery.jsonl" >> "$all_alerts"

  [[ "$(count_lines "$all_alerts")" = 5 ]] || fail "compressed outage should produce exactly 5 total alerts"
  echo "  PASS: outage backoff cap"
}

# ---------------------------------------------------------------------------
# Test 3: full network partition queues one recovery alert and suppresses per-check alerts.
# ---------------------------------------------------------------------------
{
  d="$TMPROOT/partition"
  mkdir -p "$d"
  state="$d/state.json"
  queue="$d/pending-partition-events.jsonl"
  health="$d/health.jsonl"
  results="$d/results.json"
  failures="chatgpt_https,chatgpt_ws,claude_https,telegram_api"

  write_results "$results" "$failures"
  run_state_machine "$state" "$results" "2026-05-03T01:00:00Z" "$d/a1.jsonl" "$d/p1.jsonl" "$health"
  python3 "$PARTITION_QUEUE" apply --queue-file "$queue" --actions-file "$d/p1.jsonl" --alerts-file "$d/a1.jsonl"
  [[ "$(count_lines "$queue")" = 0 ]] || fail "partition should not queue on first tick"

  run_state_machine "$state" "$results" "2026-05-03T01:10:00Z" "$d/a2.jsonl" "$d/p2.jsonl" "$health"
  python3 "$PARTITION_QUEUE" apply --queue-file "$queue" --actions-file "$d/p2.jsonl" --alerts-file "$d/a2.jsonl"
  [[ "$(count_lines "$queue")" = 1 ]] || fail "partition should queue on second tick"

  run_state_machine "$state" "$results" "2026-05-03T01:20:00Z" "$d/a3.jsonl" "$d/p3.jsonl" "$health"
  python3 "$PARTITION_QUEUE" apply --queue-file "$queue" --actions-file "$d/p3.jsonl" --alerts-file "$d/a3.jsonl"
  [[ "$(count_lines "$d/a3.jsonl")" = 0 ]] || fail "partition should suppress per-check third-tick alerts"

  write_results "$results" ""
  run_state_machine "$state" "$results" "2026-05-03T01:30:00Z" "$d/a4.jsonl" "$d/p4.jsonl" "$health"
  python3 "$PARTITION_QUEUE" apply --queue-file "$queue" --actions-file "$d/p4.jsonl" --alerts-file "$d/a4.jsonl"
  [[ "$(count_lines "$queue")" = 0 ]] || fail "partition queue should drain on recovery"
  grep -q '"type":"network_partition_recovered"' "$d/a4.jsonl" || fail "partition recovery alert missing"
  echo "  PASS: partition queue and suppression"
}

# ---------------------------------------------------------------------------
# Test 4: command-output checks parse pm2 and tailscale JSON correctly.
# ---------------------------------------------------------------------------
{
  d="$TMPROOT/check-parsers"
  fakebin="$d/fakebin"
  mkdir -p "$fakebin"
  cat > "$fakebin/pm2" <<'EOF'
#!/usr/bin/env bash
if [[ "${1:-}" == "jlist" ]]; then
  cat <<'JSON'
[
  {"name":"k2b-remote","pm2_env":{"status":"online","restart_time":1}},
  {"name":"k2b-dashboard","pm2_env":{"status":"online","restart_time":2}},
  {"name":"k2b-observer-loop","pm2_env":{"status":"online","restart_time":3}}
]
JSON
fi
EOF
  cat > "$fakebin/tailscale" <<'EOF'
#!/usr/bin/env bash
if [[ "${1:-}" == "status" && "${2:-}" == "--json" ]]; then
  cat <<'JSON'
{"Peer":{"nodekey:test":{"HostName":"Keith’s MacBook Pro","TailscaleIPs":["100.68.35.19"],"DirectAddrs":["192.168.50.20:41641"]}}}
JSON
fi
EOF
  chmod +x "$fakebin/pm2" "$fakebin/tailscale"

  PATH="$fakebin:$PATH" bash "$SRC_DIR/bin/checks/pm2.sh" > "$d/pm2.jsonl"
  python3 - "$d/pm2.jsonl" <<'PY' || fail "pm2 parser should report daemon and services ok"
import json
import sys

rows = [json.loads(line) for line in open(sys.argv[1], encoding="utf-8") if line.strip()]
by_name = {row["name"]: row for row in rows}
assert by_name["pm2_daemon"]["ok"] is True
assert by_name["pm2_services"]["ok"] is True
PY

  K2B_TAILSCALE_BIN="$fakebin/tailscale" PATH="$fakebin:$PATH" bash "$SRC_DIR/bin/checks/tailscale.sh" > "$d/tailscale.jsonl"
  grep -q '"name":"tailscale_direct","ok":true' "$d/tailscale.jsonl" || fail "tailscale parser should report direct path ok"
  grep -q '"direct_addrs":1' "$d/tailscale.jsonl" || fail "tailscale parser should count direct addrs"
  echo "  PASS: command-output parsers"
}

# ---------------------------------------------------------------------------
# Test 5: Mihomo selector chains resolve through nested manual selectors.
# ---------------------------------------------------------------------------
{
  d="$TMPROOT/mihomo-selector"
  mkdir -p "$d"
  port_file="$d/port"
  python3 -u - "$port_file" <<'PY' &
import json
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

port_file = sys.argv[1]
group = "🤖 OpenAI"
selector = "♻️ 手动切换"
leaf = "5台湾-联通/移动(AnyTLS) [AP1]"

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_GET(self):
        path = urllib.parse.unquote(urllib.parse.urlparse(self.path).path)
        if path == "/version":
            body = {"version": "fake"}
        elif path == f"/proxies/{group}":
            body = {"name": group, "type": "Selector", "now": selector, "all": [selector]}
        elif path == f"/proxies/{selector}":
            body = {"name": selector, "type": "Selector", "now": leaf, "all": [leaf]}
        elif path == f"/proxies/{leaf}":
            body = {"name": leaf, "type": "AnyTLS", "history": [{"time": "2026-05-03T00:00:00Z", "delay": 999}]}
        else:
            self.send_response(404)
            self.end_headers()
            return
        payload = json.dumps(body, ensure_ascii=False).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
with open(port_file, "w", encoding="utf-8") as f:
    f.write(str(server.server_port))
server.serve_forever()
PY
  server_pid=$!
  SERVER_PIDS+=("$server_pid")
  for _ in {1..50}; do
    [[ -s "$port_file" ]] && break
    sleep 0.1
  done
  [[ -s "$port_file" ]] || fail "fake Mihomo server did not start"

  MIHOMO_API_BASE="http://127.0.0.1:$(cat "$port_file")" \
  MIHOMO_API_SECRET="test-secret" \
  MIHOMO_OPENAI_GROUP="🤖 OpenAI" \
  bash "$SRC_DIR/bin/checks/mihomo.sh" > "$d/mihomo.jsonl"

  python3 - "$d/mihomo.jsonl" <<'PY' || fail "mihomo selector chain should resolve to leaf node"
import json
import sys

rows = [json.loads(line) for line in open(sys.argv[1], encoding="utf-8") if line.strip()]
openai = next(row for row in rows if row["name"] == "openai_node")
details = openai["details"]
assert openai["ok"] is True
assert details["selected_node"] == "5台湾-联通/移动(AnyTLS) [AP1]"
assert details["openai_group_selection"] == "♻️ 手动切换"
assert details["selector_chain"] == ["🤖 OpenAI", "♻️ 手动切换", "5台湾-联通/移动(AnyTLS) [AP1]"]
assert "5台湾-联通/移动(AnyTLS) [AP1]" in openai["message"]
PY
  kill "$server_pid" 2>/dev/null || true
  wait "$server_pid" 2>/dev/null || true
  SERVER_PIDS=()
  echo "  PASS: Mihomo nested selector resolution"
}

# ---------------------------------------------------------------------------
# Test 6: install uses a clean source snapshot and is idempotent.
# ---------------------------------------------------------------------------
{
  d="$TMPROOT/install"
  repo="$d/repo"
  app="$d/app"
  logs="$d/logs"
  agents="$d/LaunchAgents"
  env_file="$d/watchdog.env"
  mkdir -p "$repo/scripts" "$repo/launchd"
  cp -R "$SRC_DIR" "$repo/scripts/router-watchdog"
  cp -R "$REPO_ROOT/launchd/." "$repo/launchd/"
  cat > "$env_file" <<'EOF'
TELEGRAM_BOT_TOKEN=test-token
KEITH_CHAT_ID=8394008217
MIHOMO_API_BASE=http://192.168.50.1:9990
MIHOMO_API_SECRET=test-secret
MIHOMO_OPENAI_GROUP=🤖 OpenAI
EOF
  chmod 600 "$env_file"
  git -C "$repo" init -q
  git -C "$repo" add .
  git -C "$repo" -c user.name=Test -c user.email=test@example.com commit -qm init

  K2B_ROUTER_WATCHDOG_APP_DIR="$app" \
  K2B_ROUTER_WATCHDOG_LOG_DIR="$logs" \
  K2B_ROUTER_WATCHDOG_LAUNCH_AGENTS_DIR="$agents" \
  K2B_ROUTER_WATCHDOG_ENV_FILE="$env_file" \
  K2B_ROUTER_WATCHDOG_SKIP_LAUNCHCTL=1 \
  bash "$repo/scripts/router-watchdog/install.sh" >/dev/null

  start="$(python3 - <<'PY'
import time
print(time.time())
PY
)"
  K2B_ROUTER_WATCHDOG_APP_DIR="$app" \
  K2B_ROUTER_WATCHDOG_LOG_DIR="$logs" \
  K2B_ROUTER_WATCHDOG_LAUNCH_AGENTS_DIR="$agents" \
  K2B_ROUTER_WATCHDOG_ENV_FILE="$env_file" \
  K2B_ROUTER_WATCHDOG_SKIP_LAUNCHCTL=1 \
  bash "$repo/scripts/router-watchdog/install.sh" >/dev/null
  end="$(python3 - <<'PY'
import time
print(time.time())
PY
)"

  python3 - "$start" "$end" <<'PY' || fail "second install should finish under 5s"
import sys
sys.exit(0 if float(sys.argv[2]) - float(sys.argv[1]) < 5 else 1)
PY
  grep -q "no changes" "$logs/install.log" || fail "second install should log no changes"
  cmp -s "$repo/scripts/router-watchdog/bin/check.sh" "$app/bin/check.sh" || fail "installed check.sh should match source"
  echo "  PASS: install idempotence and snapshot copy"
}

# ---------------------------------------------------------------------------
# Test 7: install allows first-time untracked rsync source, but only as a snapshot.
# ---------------------------------------------------------------------------
{
  d="$TMPROOT/install-untracked"
  repo="$d/repo"
  app="$d/app"
  logs="$d/logs"
  agents="$d/LaunchAgents"
  env_file="$d/watchdog.env"
  mkdir -p "$repo/scripts" "$repo/launchd"
  cp -R "$SRC_DIR" "$repo/scripts/router-watchdog"
  cp -R "$REPO_ROOT/launchd/." "$repo/launchd/"
  cat > "$env_file" <<'EOF'
TELEGRAM_BOT_TOKEN=test-token
KEITH_CHAT_ID=8394008217
MIHOMO_API_BASE=http://192.168.50.1:9990
MIHOMO_API_SECRET=test-secret
MIHOMO_OPENAI_GROUP=🤖 OpenAI
EOF
  chmod 600 "$env_file"
  git -C "$repo" init -q
  git -C "$repo" -c user.name=Test -c user.email=test@example.com commit --allow-empty -qm init

  K2B_ROUTER_WATCHDOG_APP_DIR="$app" \
  K2B_ROUTER_WATCHDOG_LOG_DIR="$logs" \
  K2B_ROUTER_WATCHDOG_LAUNCH_AGENTS_DIR="$agents" \
  K2B_ROUTER_WATCHDOG_ENV_FILE="$env_file" \
  K2B_ROUTER_WATCHDOG_SKIP_LAUNCHCTL=1 \
  bash "$repo/scripts/router-watchdog/install.sh" >/dev/null

  cmp -s "$repo/scripts/router-watchdog/bin/check.sh" "$app/bin/check.sh" || fail "untracked rsync source should still install as snapshot"
  echo "  PASS: install permits first-time untracked rsync snapshot"
}

# ---------------------------------------------------------------------------
# Test 8: Router mutation code is scope-locked.
# ---------------------------------------------------------------------------
{
  python3 - "$SRC_DIR" <<'PY' || fail "router mutation paths must stay scope locked"
import os
import re
import sys

root = sys.argv[1]
forbidden = []
put_hits = []
for dirpath, dirs, files in os.walk(root):
    dirs[:] = [d for d in dirs if d != "__pycache__"]
    for name in files:
        if name.endswith(".pyc"):
            continue
        path = os.path.join(dirpath, name)
        text = open(path, encoding="utf-8", errors="ignore").read()
        rel = os.path.relpath(path, root)
        if re.search(r"\b(PATCH|DELETE)\b", text):
            forbidden.append(rel)
        if re.search(r"\bPUT\b", text):
            put_hits.append(rel)

if forbidden:
    raise SystemExit(f"forbidden mutation verbs in {forbidden}")
allowed_putters = ["bin/auto-switch.py", "bin/optimize-leaves.py"]
if sorted(set(put_hits)) != allowed_putters:
    raise SystemExit(f"PUT must appear only in {allowed_putters}, got {sorted(set(put_hits))}")
PY
  echo "  PASS: router mutation paths are scope locked"
}

# ---------------------------------------------------------------------------
# Test 9: rollup writes a complete context page atomically.
# ---------------------------------------------------------------------------
{
  d="$TMPROOT/rollup"
  mkdir -p "$d/logs" "$d/vault/wiki/context"
  cat > "$d/logs/health.jsonl" <<'EOF'
{"timestamp":"2026-05-03T00:00:00Z","overall_ok":true,"checks":{"router_reachable":{"ok":true},"pm2_services":{"ok":true}}}
{"timestamp":"2026-05-03T00:10:00Z","overall_ok":false,"checks":{"router_reachable":{"ok":true},"pm2_services":{"ok":false,"message":"pm2 failed"}}}
EOF
  K2B_ROUTER_WATCHDOG_LOG_DIR="$d/logs" \
  K2B_VAULT_PATH="$d/vault" \
  K2B_ROUTER_WATCHDOG_NOW="2026-05-03T00:20:00Z" \
  bash "$ROLLUP"

  out="$d/vault/wiki/context/context_router-health.md"
  [[ -f "$out" ]] || fail "rollup output missing"
  grep -q "^# Router Health$" "$out" || fail "rollup header missing"
  grep -q "pm2_services" "$out" || fail "rollup should include failing check"
  if find "$d/vault/wiki/context" -name '*.tmp' | grep -q .; then
    fail "rollup leaked tempfile"
  fi
  echo "  PASS: rollup output"
}

echo "router-watchdog.test.sh: all tests passed"
