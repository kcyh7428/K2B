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
  # Simulate successful delivery so the next tick's recovery branch sees count>0
  # (per the 2026-05-18 deferred-state fix, count is no longer auto-incremented).
  python3 "$STATE_MACHINE" --confirm-delivered --state-file "$state" --alert-file "$d/a3.jsonl"

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
    if [[ -s "$alerts" ]]; then
      # Simulate successful delivery so subsequent ticks advance through the backoff schedule.
      python3 "$STATE_MACHINE" --confirm-delivered --state-file "$state" --alert-file "$alerts"
      cat "$alerts" >> "$all_alerts"
    fi
  done

  write_results "$results" ""
  run_state_machine "$state" "$results" "2026-05-03T00:36:00Z" "$d/recovery.jsonl" "$d/recovery-actions.jsonl" "$health"
  if [[ -s "$d/recovery.jsonl" ]]; then
    python3 "$STATE_MACHINE" --confirm-delivered --state-file "$state" --alert-file "$d/recovery.jsonl"
    cat "$d/recovery.jsonl" >> "$all_alerts"
  fi

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
# Test 8: install validates enabled leaf-optimizer profile env keys only.
# ---------------------------------------------------------------------------
{
  d="$TMPROOT/install-profile-env"
  repo="$d/repo"
  app="$d/app"
  logs="$d/logs"
  agents="$d/LaunchAgents"
  env_file="$d/watchdog.env"
  mkdir -p "$repo/scripts" "$repo/launchd"
  cp -R "$SRC_DIR" "$repo/scripts/router-watchdog"
  cp -R "$REPO_ROOT/launchd/." "$repo/launchd/"
  python3 - "$repo/scripts/router-watchdog/bin/leaf-optimizer-profiles.json" <<'PY'
import json
import sys

path = sys.argv[1]
data = json.load(open(path, encoding="utf-8"))
data["profiles"]["general"] = {
    "enabled": True,
    "group_env_var": "MIHOMO_GENERAL_GROUP",
    "selector_regex": "^♻️ 手动切换",
}
with open(path, "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    f.write("\n")
PY
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

  set +e
  K2B_ROUTER_WATCHDOG_APP_DIR="$app" \
  K2B_ROUTER_WATCHDOG_LOG_DIR="$logs" \
  K2B_ROUTER_WATCHDOG_LAUNCH_AGENTS_DIR="$agents" \
  K2B_ROUTER_WATCHDOG_ENV_FILE="$env_file" \
  K2B_ROUTER_WATCHDOG_SKIP_LAUNCHCTL=1 \
  bash "$repo/scripts/router-watchdog/install.sh" > "$d/install.out" 2> "$d/install.err"
  rc=$?
  set -e
  [[ "$rc" -ne 0 ]] || fail "enabled profile missing env should fail install"
  grep -q "MIHOMO_GENERAL_GROUP" "$d/install.err" || fail "missing profile env should be named"

  python3 - "$repo/scripts/router-watchdog/bin/leaf-optimizer-profiles.json" <<'PY'
import json
import sys

path = sys.argv[1]
data = json.load(open(path, encoding="utf-8"))
data["profiles"]["general"]["enabled"] = False
with open(path, "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    f.write("\n")
PY
  git -C "$repo" add .
  git -C "$repo" -c user.name=Test -c user.email=test@example.com commit -qm disable-general-profile

  K2B_ROUTER_WATCHDOG_APP_DIR="$app" \
  K2B_ROUTER_WATCHDOG_LOG_DIR="$logs" \
  K2B_ROUTER_WATCHDOG_LAUNCH_AGENTS_DIR="$agents" \
  K2B_ROUTER_WATCHDOG_ENV_FILE="$env_file" \
  K2B_ROUTER_WATCHDOG_SKIP_LAUNCHCTL=1 \
  bash "$repo/scripts/router-watchdog/install.sh" >/dev/null

  echo "  PASS: install validates enabled profile env only"
}

# ---------------------------------------------------------------------------
# Test 9: Router mutation code is scope-locked.
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

# ---------------------------------------------------------------------------
# Test 10: alert state mutations defer until delivery is confirmed.
# Reproduces the silent-drop bug verified in the 2026-05-18 MVP fault-injection
# re-test: first-alert delivery to Telegram failed, send-alert.sh used to exit 1
# before alerts.jsonl was written AND state-machine.py incremented
# alert_count_in_outage as if delivery succeeded. Result: Keith got no initial
# alert, watchdog waited the full +30m backoff before the next attempt.
# Fix: state-machine.py only generates the alert dict; check.sh calls
# state-machine.py --confirm-delivered after successful send-alert.sh.
# ---------------------------------------------------------------------------
{
  d="$TMPROOT/state-machine-defer"
  mkdir -p "$d"
  state="$d/state.json"
  health="$d/health.jsonl"
  results="$d/results.json"

  write_results "$results" "pm2_services"
  # Ticks 1-2: failing, no alert, count stays 0.
  run_state_machine "$state" "$results" "2026-05-03T00:00:00Z" "$d/a1.jsonl" "$d/p1.jsonl" "$health"
  run_state_machine "$state" "$results" "2026-05-03T00:10:00Z" "$d/a2.jsonl" "$d/p2.jsonl" "$health"

  # Tick 3: third consecutive failure, generates initial 'failure' alert.
  # NEW BEHAVIOR: state.alert_count_in_outage MUST stay 0 until confirmation.
  run_state_machine "$state" "$results" "2026-05-03T00:20:00Z" "$d/a3.jsonl" "$d/p3.jsonl" "$health"
  [[ "$(count_lines "$d/a3.jsonl")" = 1 ]] || fail "tick 3 should generate one failure alert"
  grep -q '"type":"failure"' "$d/a3.jsonl" || fail "tick 3 alert type should be failure"
  count_after_3="$(python3 -c 'import json; print(json.load(open("'"$state"'"))["pm2_services"]["alert_count_in_outage"])')"
  [[ "$count_after_3" = "0" ]] || fail "alert_count_in_outage should stay 0 until delivery confirmed (got $count_after_3)"
  last_after_3="$(python3 -c 'import json; print(json.load(open("'"$state"'"))["pm2_services"].get("last_alert_at"))')"
  [[ "$last_after_3" = "None" ]] || fail "last_alert_at should stay None until delivery confirmed (got $last_after_3)"

  # Simulate delivery FAILURE: do NOT call --confirm-delivered.

  # Tick 4: still failing. Because count is still 0, state-machine.py should
  # re-generate the initial 'failure' alert (this is the retry).
  run_state_machine "$state" "$results" "2026-05-03T00:30:00Z" "$d/a4.jsonl" "$d/p4.jsonl" "$health"
  [[ "$(count_lines "$d/a4.jsonl")" = 1 ]] || fail "tick 4 should re-generate failure alert (delivery never confirmed)"
  grep -q '"type":"failure"' "$d/a4.jsonl" || fail "tick 4 alert type should still be failure (retry)"

  # Now simulate delivery SUCCESS for the tick-4 alert: call --confirm-delivered.
  python3 "$STATE_MACHINE" --confirm-delivered \
    --state-file "$state" \
    --alert-file "$d/a4.jsonl"

  count_after_confirm="$(python3 -c 'import json; print(json.load(open("'"$state"'"))["pm2_services"]["alert_count_in_outage"])')"
  [[ "$count_after_confirm" = "1" ]] || fail "alert_count_in_outage should be 1 after confirmation (got $count_after_confirm)"
  last_after_confirm="$(python3 -c 'import json; print(json.load(open("'"$state"'"))["pm2_services"]["last_alert_at"])')"
  [[ "$last_after_confirm" = "2026-05-03T00:30:00Z" ]] || fail "last_alert_at should be tick-4 ts after confirmation (got $last_after_confirm)"

  # Tick 5: 15s after tick 4. Failing. No alert (30s backoff threshold not yet elapsed).
  run_state_machine "$state" "$results" "2026-05-03T00:30:15Z" "$d/a5.jsonl" "$d/p5.jsonl" "$health"
  [[ "$(count_lines "$d/a5.jsonl")" = 0 ]] || fail "tick 5 should not alert (still under backoff threshold)"

  # Tick 6: 30s after tick 4. Failing. +30s backoff threshold elapsed -> repeat_failure alert.
  run_state_machine "$state" "$results" "2026-05-03T00:30:30Z" "$d/a6.jsonl" "$d/p6.jsonl" "$health"
  [[ "$(count_lines "$d/a6.jsonl")" = 1 ]] || fail "tick 6 should generate repeat_failure alert (+30s elapsed)"
  grep -q '"type":"repeat_failure"' "$d/a6.jsonl" || fail "tick 6 alert type should be repeat_failure"

  # Idempotency: confirming the same alert twice must not double-increment.
  python3 "$STATE_MACHINE" --confirm-delivered \
    --state-file "$state" \
    --alert-file "$d/a6.jsonl"
  count_after_first_confirm="$(python3 -c 'import json; print(json.load(open("'"$state"'"))["pm2_services"]["alert_count_in_outage"])')"
  python3 "$STATE_MACHINE" --confirm-delivered \
    --state-file "$state" \
    --alert-file "$d/a6.jsonl"
  count_after_second_confirm="$(python3 -c 'import json; print(json.load(open("'"$state"'"))["pm2_services"]["alert_count_in_outage"])')"
  [[ "$count_after_first_confirm" = "$count_after_second_confirm" ]] || fail "duplicate --confirm-delivered must be idempotent (was $count_after_first_confirm, became $count_after_second_confirm)"
  [[ "$count_after_first_confirm" = "2" ]] || fail "alert_count_in_outage should be 2 after tick-6 confirmation (got $count_after_first_confirm)"

  # Recovery: recovery alert generation also defers state reset until confirmed.
  write_results "$results" ""
  run_state_machine "$state" "$results" "2026-05-03T00:31:00Z" "$d/a7.jsonl" "$d/p7.jsonl" "$health"
  [[ "$(count_lines "$d/a7.jsonl")" = 1 ]] || fail "tick 7 (ok) should generate recovery alert"
  grep -q '"type":"recovery"' "$d/a7.jsonl" || fail "tick 7 alert type should be recovery"

  # NEW BEHAVIOR: status should still be 'fail' until recovery confirmed.
  status_before_recovery_confirm="$(python3 -c 'import json; print(json.load(open("'"$state"'"))["pm2_services"]["status"])')"
  [[ "$status_before_recovery_confirm" = "fail" ]] || fail "status should stay fail until recovery confirmed (got $status_before_recovery_confirm)"

  # Confirm recovery delivery: state resets.
  python3 "$STATE_MACHINE" --confirm-delivered \
    --state-file "$state" \
    --alert-file "$d/a7.jsonl"
  status_after_recovery_confirm="$(python3 -c 'import json; print(json.load(open("'"$state"'"))["pm2_services"]["status"])')"
  [[ "$status_after_recovery_confirm" = "ok" ]] || fail "status should reset to ok after recovery confirmed (got $status_after_recovery_confirm)"
  count_after_recovery_confirm="$(python3 -c 'import json; print(json.load(open("'"$state"'"))["pm2_services"]["alert_count_in_outage"])')"
  [[ "$count_after_recovery_confirm" = "0" ]] || fail "alert_count_in_outage should reset to 0 after recovery confirmed (got $count_after_recovery_confirm)"

  echo "  PASS: alert state defers until delivery confirmed (silent-drop fix)"
}

# ---------------------------------------------------------------------------
# Test 11: --confirm-delivered rejects stale alerts across outage epochs.
# Codex 2026-05-18 review HIGH-2: apply_confirmed previously matched only on
# alert.type, so a replayed old recovery could reset a NEW outage's failing
# state, or an old failure-confirm after recovery could overwrite ok-state
# counters. Alerts now carry outage_since pinning them to one epoch.
# ---------------------------------------------------------------------------
{
  d="$TMPROOT/state-machine-stale-epoch"
  mkdir -p "$d"
  state="$d/state.json"
  health="$d/health.jsonl"
  results="$d/results.json"

  # Outage 1: ticks 1-3 fail, generate + confirm initial alert, then recover + confirm.
  write_results "$results" "pm2_services"
  run_state_machine "$state" "$results" "2026-05-03T00:00:00Z" "$d/a1.jsonl" "$d/p1.jsonl" "$health"
  run_state_machine "$state" "$results" "2026-05-03T00:10:00Z" "$d/a2.jsonl" "$d/p2.jsonl" "$health"
  run_state_machine "$state" "$results" "2026-05-03T00:20:00Z" "$d/a3.jsonl" "$d/p3.jsonl" "$health"

  # Capture the OLD outage's initial alert (carries outage_since=tick1_ts) before confirming.
  cp "$d/a3.jsonl" "$d/old_failure_alert.jsonl"
  grep -q '"outage_since":"2026-05-03T00:00:00Z"' "$d/a3.jsonl" \
    || fail "initial failure alert should embed outage_since from outage start"

  python3 "$STATE_MACHINE" --confirm-delivered --state-file "$state" --alert-file "$d/a3.jsonl"

  write_results "$results" ""
  run_state_machine "$state" "$results" "2026-05-03T00:30:00Z" "$d/a4.jsonl" "$d/p4.jsonl" "$health"
  grep -q '"outage_since":"2026-05-03T00:00:00Z"' "$d/a4.jsonl" \
    || fail "recovery alert should embed outage_since from the outage that's recovering"

  # Capture OLD outage's recovery alert before confirming.
  cp "$d/a4.jsonl" "$d/old_recovery_alert.jsonl"
  python3 "$STATE_MACHINE" --confirm-delivered --state-file "$state" --alert-file "$d/a4.jsonl"

  status_after_outage_1="$(python3 -c 'import json; print(json.load(open("'"$state"'"))["pm2_services"]["status"])')"
  [[ "$status_after_outage_1" = "ok" ]] || fail "outage 1 should fully resolve to ok (got $status_after_outage_1)"

  # Outage 2 starts at tick 5 with a NEW outage_since.
  write_results "$results" "pm2_services"
  run_state_machine "$state" "$results" "2026-05-03T01:00:00Z" "$d/a5.jsonl" "$d/p5.jsonl" "$health"
  run_state_machine "$state" "$results" "2026-05-03T01:10:00Z" "$d/a6.jsonl" "$d/p6.jsonl" "$health"
  run_state_machine "$state" "$results" "2026-05-03T01:20:00Z" "$d/a7.jsonl" "$d/p7.jsonl" "$health"
  grep -q '"outage_since":"2026-05-03T01:00:00Z"' "$d/a7.jsonl" \
    || fail "outage 2 initial alert should embed the NEW outage_since"

  # STALE-CONFIRM SCENARIO 1: replay OLD outage's recovery alert during outage 2.
  # Without the epoch guard, this would reset state to ok and lose the active outage.
  python3 "$STATE_MACHINE" --confirm-delivered --state-file "$state" --alert-file "$d/old_recovery_alert.jsonl"
  status_after_stale_recovery="$(python3 -c 'import json; print(json.load(open("'"$state"'"))["pm2_services"]["status"])')"
  [[ "$status_after_stale_recovery" = "fail" ]] \
    || fail "stale recovery alert from prior outage must NOT reset current outage state (got $status_after_stale_recovery)"

  # STALE-CONFIRM SCENARIO 2: replay OLD outage's failure alert during outage 2.
  # Without the epoch guard, this would overwrite the (still-zero) alert_count_in_outage
  # of outage 2 to 1 with last_alert_at from the OLD outage's tick3 -- corrupting
  # the backoff schedule for the current outage.
  python3 "$STATE_MACHINE" --confirm-delivered --state-file "$state" --alert-file "$d/old_failure_alert.jsonl"
  count_after_stale_failure="$(python3 -c 'import json; print(json.load(open("'"$state"'"))["pm2_services"]["alert_count_in_outage"])')"
  last_after_stale_failure="$(python3 -c 'import json; print(json.load(open("'"$state"'"))["pm2_services"].get("last_alert_at"))')"
  [[ "$count_after_stale_failure" = "0" ]] \
    || fail "stale failure alert from prior outage must NOT bump count of current outage (got $count_after_stale_failure)"
  [[ "$last_after_stale_failure" = "None" ]] \
    || fail "stale failure alert must NOT write last_alert_at of current outage (got $last_after_stale_failure)"

  # Confirm CURRENT outage's failure alert (a7 carries the matching epoch) works.
  python3 "$STATE_MACHINE" --confirm-delivered --state-file "$state" --alert-file "$d/a7.jsonl"
  count_after_current_confirm="$(python3 -c 'import json; print(json.load(open("'"$state"'"))["pm2_services"]["alert_count_in_outage"])')"
  [[ "$count_after_current_confirm" = "1" ]] \
    || fail "current-outage failure alert SHOULD apply (got $count_after_current_confirm)"

  # STALE-CONFIRM SCENARIO 3: replay OLD failure alert AFTER outage 2 recovers.
  # State is then ok with state.since=ok_ts; old alert.outage_since=tick1_ts.
  write_results "$results" ""
  run_state_machine "$state" "$results" "2026-05-03T01:30:00Z" "$d/a8.jsonl" "$d/p8.jsonl" "$health"
  python3 "$STATE_MACHINE" --confirm-delivered --state-file "$state" --alert-file "$d/a8.jsonl"
  python3 "$STATE_MACHINE" --confirm-delivered --state-file "$state" --alert-file "$d/old_failure_alert.jsonl"
  status_final="$(python3 -c 'import json; print(json.load(open("'"$state"'"))["pm2_services"]["status"])')"
  count_final="$(python3 -c 'import json; print(json.load(open("'"$state"'"))["pm2_services"]["alert_count_in_outage"])')"
  [[ "$status_final" = "ok" ]] || fail "stale failure-confirm after recovery must NOT flip status back to fail (got $status_final)"
  [[ "$count_final" = "0" ]] || fail "stale failure-confirm after recovery must NOT bump count (got $count_final)"

  echo "  PASS: --confirm-delivered rejects stale alerts across outage epochs"
}

# ---------------------------------------------------------------------------
# Test 12: threshold-crossed outage that recovers before delivery is confirmed
# still produces a recovery alert. Codex 2026-05-18 second-pass review HIGH:
# the deferred-state fix introduced a new silent-drop class — if the initial
# failure alert was generated but never delivered (Telegram blip), AND the
# check recovered before the next tick could retry, the recovery branch would
# previously skip (count==0) AND state would reset silently. Watchdog passes
# threshold, no Telegram at all, zero trace. Fix: recovery fires when
# consecutive_fails>=3 even if count==0, with a "briefly failed" message
# that includes the missed-outage context.
# ---------------------------------------------------------------------------
{
  d="$TMPROOT/state-machine-missed-outage"
  mkdir -p "$d"
  state="$d/state.json"
  health="$d/health.jsonl"
  results="$d/results.json"

  # Tick 1, 2, 3: failing. Tick 3 generates initial failure alert.
  write_results "$results" "pm2_services"
  run_state_machine "$state" "$results" "2026-05-03T00:00:00Z" "$d/a1.jsonl" "$d/p1.jsonl" "$health"
  run_state_machine "$state" "$results" "2026-05-03T00:10:00Z" "$d/a2.jsonl" "$d/p2.jsonl" "$health"
  run_state_machine "$state" "$results" "2026-05-03T00:20:00Z" "$d/a3.jsonl" "$d/p3.jsonl" "$health"
  [[ "$(count_lines "$d/a3.jsonl")" = 1 ]] || fail "tick 3 should generate failure alert"
  grep -q '"type":"failure"' "$d/a3.jsonl" || fail "tick 3 should be a failure alert"

  # Simulate delivery FAILURE: do NOT call --confirm-delivered.
  # State stays count=0, status=fail, consecutive_fails=3, since=tick1_ts.

  # Tick 4: check recovers. Without the missed-outage branch, recovery would
  # be skipped (count==0) AND state would reset silently — the watchdog passed
  # its threshold and produced zero Telegram alerts. The fix fires recovery.
  write_results "$results" ""
  run_state_machine "$state" "$results" "2026-05-03T00:30:00Z" "$d/a4.jsonl" "$d/p4.jsonl" "$health"
  [[ "$(count_lines "$d/a4.jsonl")" = 1 ]] || fail "tick 4 (ok after silent-dropped failure) MUST generate a recovery alert"
  grep -q '"type":"recovery"' "$d/a4.jsonl" || fail "tick 4 alert should be type=recovery"
  grep -q '"outage_since":"2026-05-03T00:00:00Z"' "$d/a4.jsonl" || fail "missed-outage recovery should carry outage_since from outage start"
  grep -q "briefly failed" "$d/a4.jsonl" || fail "missed-outage recovery should use the 'briefly failed' message"
  grep -q "Initial failure alert delivery was not confirmed" "$d/a4.jsonl" \
    || fail "missed-outage recovery message should mention undelivered initial alert"

  # Confirm the recovery: state resets to ok.
  python3 "$STATE_MACHINE" --confirm-delivered --state-file "$state" --alert-file "$d/a4.jsonl"
  status_after="$(python3 -c 'import json; print(json.load(open("'"$state"'"))["pm2_services"]["status"])')"
  [[ "$status_after" = "ok" ]] || fail "missed-outage recovery confirmation should reset state to ok (got $status_after)"

  # Boundary: a 2-tick failure that recovers (NOT crossing the 3-tick threshold)
  # should NOT generate a recovery alert — there was nothing to recover from
  # the user's perspective. State just resets.
  write_results "$results" "pm2_services"
  run_state_machine "$state" "$results" "2026-05-03T01:00:00Z" "$d/b1.jsonl" "$d/q1.jsonl" "$health"
  run_state_machine "$state" "$results" "2026-05-03T01:10:00Z" "$d/b2.jsonl" "$d/q2.jsonl" "$health"
  write_results "$results" ""
  run_state_machine "$state" "$results" "2026-05-03T01:20:00Z" "$d/b3.jsonl" "$d/q3.jsonl" "$health"
  [[ "$(count_lines "$d/b3.jsonl")" = 0 ]] || fail "2-tick failure recovery should NOT generate a recovery alert (under threshold)"
  status_after_blip="$(python3 -c 'import json; print(json.load(open("'"$state"'"))["pm2_services"]["status"])')"
  [[ "$status_after_blip" = "ok" ]] || fail "2-tick failure should reset state immediately (got $status_after_blip)"

  echo "  PASS: threshold-crossed undelivered outage still fires recovery alert (missed-outage branch)"
}

# ---------------------------------------------------------------------------
# Test 13: full network partition does NOT emit misleading missed-outage
# recoveries when it clears. Codex 2026-05-18 third-pass MED: the missed-outage
# branch was too permissive — partition suppress_alerts kept count at 0 while
# consecutive_fails accumulated, so on partition clear my code would have
# emitted "briefly failed... delivery not confirmed" recoveries for EACH
# external check, in addition to the (correct) network_partition_recovered
# alert from the partition queue. Fix: pending_initial_alert flag is set ONLY
# inside the `not suppress_alerts` branch, so partition-suppressed alerts
# don't trigger missed-outage recovery.
# ---------------------------------------------------------------------------
{
  d="$TMPROOT/state-machine-partition-no-missed-recovery"
  mkdir -p "$d"
  state="$d/state.json"
  queue="$d/pending-partition-events.jsonl"
  health="$d/health.jsonl"
  results="$d/results.json"
  failures="chatgpt_https,chatgpt_ws,claude_https,telegram_api"

  # Ticks 1-4: full partition, all 4 external checks failing. suppress_alerts
  # kicks in on tick 2 (2 consecutive partition ticks). Per-check failure alerts
  # are suppressed; pending_initial_alert MUST stay False for these checks.
  write_results "$results" "$failures"
  for ts in "2026-05-03T00:00:00Z" "2026-05-03T00:10:00Z" "2026-05-03T00:20:00Z" "2026-05-03T00:30:00Z"; do
    alerts="$d/alerts-${ts//[:T-]/}.jsonl"
    actions="$d/actions-${ts//[:T-]/}.jsonl"
    run_state_machine "$state" "$results" "$ts" "$alerts" "$actions" "$health"
    python3 "$PARTITION_QUEUE" apply --queue-file "$queue" --actions-file "$actions" --alerts-file "$alerts"
  done

  # By tick 4, each per-check has consecutive_fails=4 BUT suppress_alerts has
  # been suppressing per-check alerts since tick 2. Verify pending_initial_alert
  # stayed False for each external check despite consecutive_fails >= 3.
  for chk in chatgpt_https chatgpt_ws claude_https telegram_api; do
    pending="$(python3 -c 'import json; s=json.load(open("'"$state"'")).get("'"$chk"'") or {}; print(s.get("pending_initial_alert"))')"
    [[ "$pending" = "False" ]] || fail "$chk should have pending_initial_alert=False during partition (got $pending)"
    consec="$(python3 -c 'import json; s=json.load(open("'"$state"'")).get("'"$chk"'") or {}; print(s.get("consecutive_fails"))')"
    [[ "$consec" -ge 3 ]] || fail "$chk should have consecutive_fails>=3 by tick 4 (got $consec)"
  done

  # Tick 5: partition clears. All 4 external checks recover. Per-check recovery
  # branch sees pending_initial_alert=False AND count==0, so missed-outage
  # condition is FALSE -- no per-check "briefly failed" recoveries emitted.
  # Partition queue handles the recovery via network_partition_recovered.
  write_results "$results" ""
  ts="2026-05-03T00:40:00Z"
  alerts="$d/alerts-${ts//[:T-]/}.jsonl"
  actions="$d/actions-${ts//[:T-]/}.jsonl"
  run_state_machine "$state" "$results" "$ts" "$alerts" "$actions" "$health"
  python3 "$PARTITION_QUEUE" apply --queue-file "$queue" --actions-file "$actions" --alerts-file "$alerts"

  # Per-check "briefly failed" recoveries MUST NOT appear in the alert stream.
  if grep -q "briefly failed" "$alerts"; then
    fail "partition recovery must NOT emit per-check 'briefly failed' recoveries (got: $(cat "$alerts"))"
  fi
  for chk in chatgpt_https chatgpt_ws claude_https telegram_api; do
    if grep -E "\"check\":\"$chk\".*\"type\":\"recovery\"" "$alerts"; then
      fail "$chk should NOT emit per-check recovery during partition clear (partition queue handles it)"
    fi
  done

  # Verify the partition queue DID emit its recovery alert (the proper channel).
  grep -q '"type":"network_partition_recovered"' "$alerts" \
    || fail "partition recovery alert MUST be emitted via partition queue"

  echo "  PASS: full partition recovery does not emit misleading per-check missed-outage recoveries"
}

# ---------------------------------------------------------------------------
# Test 14: partition tick-1 carry-in does not leak a per-check missed-outage
# recovery. Codex 2026-05-18 fourth-pass MED: if telegram_api was at
# consecutive_fails=2 BEFORE all 4 external checks went down, then tick 1 of
# partition (where suppress_alerts is still False) would generate a failure
# alert AND set pending_initial_alert=True for telegram_api. Later partition
# ticks suppress alerts, but pending stays True. On partition clear, recovery
# branch fires per-check 'briefly failed' alongside network_partition_recovered.
# Fix part A: transition_checks gets partition_now arg; on partition_now=True
# tick, the failure alert is still emitted (suppress_alerts is False yet) but
# pending_initial_alert is NOT set for external checks. Fix part B:
# transition_partition clears carry-in pending_initial_alert for the 4 external
# checks at the moment partition is queued.
# ---------------------------------------------------------------------------
{
  d="$TMPROOT/state-machine-partition-tick1-carryin"
  mkdir -p "$d"
  state="$d/state.json"
  queue="$d/pending-partition-events.jsonl"
  health="$d/health.jsonl"
  results="$d/results.json"

  # Seed: 2 ticks where ONLY telegram_api fails. After tick 2, telegram_api
  # is at consecutive_fails=2, pending=False (threshold not yet crossed).
  write_results "$results" "telegram_api"
  run_state_machine "$state" "$results" "2026-05-03T00:00:00Z" "$d/a1.jsonl" "$d/p1.jsonl" "$health"
  run_state_machine "$state" "$results" "2026-05-03T00:10:00Z" "$d/a2.jsonl" "$d/p2.jsonl" "$health"
  consec_pre="$(python3 -c 'import json; print(json.load(open("'"$state"'"))["telegram_api"]["consecutive_fails"])')"
  [[ "$consec_pre" = "2" ]] || fail "seeding failed: telegram_api consecutive_fails should be 2 (got $consec_pre)"

  # Tick 3: ALL 4 external checks fail (partition begins). For telegram_api,
  # consecutive_fails goes 2->3, crossing threshold. partition_now=True but
  # this is tick 1 of partition so suppress_alerts is still False.
  # OLD behavior would: append failure alert AND set pending=True.
  # NEW behavior: append failure alert BUT pending stays False (carry-in fix).
  write_results "$results" "chatgpt_https,chatgpt_ws,claude_https,telegram_api"
  run_state_machine "$state" "$results" "2026-05-03T00:20:00Z" "$d/a3.jsonl" "$d/p3.jsonl" "$health"
  pending_tick3="$(python3 -c 'import json; print(json.load(open("'"$state"'"))["telegram_api"]["pending_initial_alert"])')"
  [[ "$pending_tick3" = "False" ]] \
    || fail "tick 3 (partition_now=True) MUST NOT set telegram_api pending_initial_alert (got $pending_tick3)"

  # Tick 4: partition continues (consecutive_fails=2 for partition state ->
  # suppress_alerts=True, queued=True). The carry-in clear fires here as a
  # belt-and-suspenders: if pending was somehow True, it would be cleared now.
  run_state_machine "$state" "$results" "2026-05-03T00:30:00Z" "$d/a4.jsonl" "$d/p4.jsonl" "$health"
  python3 "$PARTITION_QUEUE" apply --queue-file "$queue" --actions-file "$d/p4.jsonl" --alerts-file "$d/a4.jsonl"
  pending_tick4="$(python3 -c 'import json; print(json.load(open("'"$state"'"))["telegram_api"]["pending_initial_alert"])')"
  [[ "$pending_tick4" = "False" ]] \
    || fail "tick 4 (queued partition) MUST keep/clear telegram_api pending_initial_alert to False (got $pending_tick4)"

  # Tick 5: partition clears. Expect: network_partition_recovered alert ONLY.
  # NO per-check 'briefly failed' for telegram_api (which had crossed threshold).
  write_results "$results" ""
  run_state_machine "$state" "$results" "2026-05-03T00:40:00Z" "$d/a5.jsonl" "$d/p5.jsonl" "$health"
  python3 "$PARTITION_QUEUE" apply --queue-file "$queue" --actions-file "$d/p5.jsonl" --alerts-file "$d/a5.jsonl"
  if grep -q "briefly failed" "$d/a5.jsonl"; then
    fail "partition recovery with telegram_api carry-in MUST NOT emit 'briefly failed' per-check recovery (got: $(cat "$d/a5.jsonl"))"
  fi
  grep -q '"type":"network_partition_recovered"' "$d/a5.jsonl" \
    || fail "partition recovery alert MUST be emitted via partition queue"

  # Counterpoint: a non-external check (e.g., syncthing_process) that crossed
  # threshold INDEPENDENTLY of partition should STILL fire missed-outage
  # recovery on its own recovery tick. The carry-in fix is scoped to the 4
  # external partition checks.
  echo "  PASS: partition tick-1 carry-in does not leak per-check missed-outage recovery"
}

# ---------------------------------------------------------------------------
# Test 15: non-external check (syncthing_process) carry-in is NOT affected by
# the partition guard. Regression for the scoping of Codex MED-1's fix.
# ---------------------------------------------------------------------------
{
  d="$TMPROOT/state-machine-non-external-carryin"
  mkdir -p "$d"
  state="$d/state.json"
  queue="$d/pending-partition-events.jsonl"
  health="$d/health.jsonl"
  results="$d/results.json"

  # Tick 1, 2, 3: syncthing_process failing. Tick 3 generates failure alert.
  # Even if partition is happening simultaneously, syncthing_process is NOT
  # one of EXTERNAL_PARTITION_CHECKS so the carry-in fix should not apply.
  # We verify by having ONLY syncthing_process failing (no partition).
  write_results "$results" "syncthing_process"
  run_state_machine "$state" "$results" "2026-05-03T00:00:00Z" "$d/a1.jsonl" "$d/p1.jsonl" "$health"
  run_state_machine "$state" "$results" "2026-05-03T00:10:00Z" "$d/a2.jsonl" "$d/p2.jsonl" "$health"
  run_state_machine "$state" "$results" "2026-05-03T00:20:00Z" "$d/a3.jsonl" "$d/p3.jsonl" "$health"
  pending_syncthing="$(python3 -c 'import json; print(json.load(open("'"$state"'"))["syncthing_process"]["pending_initial_alert"])')"
  [[ "$pending_syncthing" = "True" ]] \
    || fail "syncthing_process initial failure SHOULD set pending_initial_alert=True (got $pending_syncthing)"

  # Simulate delivery failure: no --confirm-delivered.
  # Tick 4: syncthing recovers. Missed-outage recovery should fire.
  write_results "$results" ""
  run_state_machine "$state" "$results" "2026-05-03T00:30:00Z" "$d/a4.jsonl" "$d/p4.jsonl" "$health"
  grep -q '"type":"recovery"' "$d/a4.jsonl" \
    || fail "syncthing_process missed-outage recovery MUST fire (non-external check unaffected by partition guard)"
  grep -q "briefly failed" "$d/a4.jsonl" \
    || fail "syncthing_process recovery should use 'briefly failed' missed-outage message"

  echo "  PASS: non-external check missed-outage recovery still works (partition guard scoped correctly)"
}

echo "router-watchdog.test.sh: all tests passed"
