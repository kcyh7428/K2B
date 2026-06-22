#!/usr/bin/env bash
# tests/router-watchdog-phase234.test.sh
# Local integration tests for router-watchdog recommendation, scoring, and
# sentinel-gated auto-switch phases.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC_DIR="$REPO_ROOT/scripts/router-watchdog"
DIGEST="$SRC_DIR/bin/digest.py"
SCORE="$SRC_DIR/bin/score-nodes.py"
AUTOSWITCH="$SRC_DIR/bin/auto-switch.py"

TMPROOT="$(mktemp -d)"
SERVER_PIDS=()
trap 'for pid in "${SERVER_PIDS[@]:-}"; do kill "$pid" 2>/dev/null || true; done; rm -rf "$TMPROOT"' EXIT

fail() { echo "FAIL: $*" >&2; exit 1; }
stop_servers() {
  local pid
  for pid in "${SERVER_PIDS[@]:-}"; do
    kill "$pid" 2>/dev/null || true
    wait "$pid" 2>/dev/null || true
  done
  SERVER_PIDS=()
}

echo "=== router-watchdog-phase234.test.sh ==="

# ---------------------------------------------------------------------------
# Test 1: Phase 2 digest recommends good nodes and calls out bad ones.
# ---------------------------------------------------------------------------
{
  d="$TMPROOT/digest"
  mkdir -p "$d"
  health="$d/health.jsonl"
  score="$d/node-score.jsonl"

  cat > "$health" <<'EOF'
{"timestamp":"2026-05-03T00:00:00Z","overall_ok":false,"checks":{"chatgpt_https":{"ok":false,"message":"HTTP 000"},"chatgpt_ws":{"ok":false,"message":"HTTP 000"},"claude_https":{"ok":false,"message":"HTTP 000"},"telegram_api":{"ok":true},"openai_node":{"ok":true,"details":{"selected_node":"bad-node","openai_group_selection":"♻️ 手动切换5","selector_chain":["🤖 OpenAI","♻️ 手动切换5","bad-node"]},"message":"OpenAI selected node: bad-node via 🤖 OpenAI -> ♻️ 手动切换5 -> bad-node"}}}
{"timestamp":"2026-05-03T00:10:00Z","overall_ok":true,"checks":{"chatgpt_https":{"ok":true},"chatgpt_ws":{"ok":true},"claude_https":{"ok":true},"telegram_api":{"ok":true},"openai_node":{"ok":true,"details":{"selected_node":"good-node","openai_group_selection":"♻️ 手动切换1","selector_chain":["🤖 OpenAI","♻️ 手动切换1","good-node"]},"message":"OpenAI selected node: good-node via 🤖 OpenAI -> ♻️ 手动切换1 -> good-node"}}}
EOF
  cat > "$score" <<'EOF'
{"timestamp":"2026-05-03T00:05:00Z","candidate":"♻️ 手动切换5","resolved_leaf":"bad-node","success_rate":0.0,"score":0.0,"quarantined":true,"quarantine_until":"2026-05-04T00:05:00Z"}
{"timestamp":"2026-05-03T00:05:00Z","candidate":"♻️ 手动切换1","resolved_leaf":"good-node","success_rate":1.0,"score":0.98,"quarantined":false}
EOF

  python3 "$DIGEST" \
    --health-log "$health" \
    --score-log "$score" \
    --now "2026-05-03T00:20:00Z" > "$d/digest.txt"

  grep -q "Router watchdog" "$d/digest.txt" || fail "digest title missing"
  grep -q "good-node" "$d/digest.txt" || fail "digest should recommend good node"
  grep -q "bad-node" "$d/digest.txt" || fail "digest should mention bad node"
  grep -qi "quarantine" "$d/digest.txt" || fail "digest should mention quarantine"
  echo "  PASS: Phase 2 digest recommendation"
}

# ---------------------------------------------------------------------------
# Test 2: Phase 3 scores OpenAI-group candidates against real target URLs and
# writes a top-3 list while quarantining failing candidates.
# ---------------------------------------------------------------------------
{
  d="$TMPROOT/score"
  mkdir -p "$d"
  port_file="$d/port"
  request_log="$d/requests.jsonl"
  python3 -u - "$port_file" "$request_log" <<'PY' &
import json
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

port_file, request_log = sys.argv[1], sys.argv[2]
group = "🤖 OpenAI"
members = ["♻️ 手动切换1", "♻️ 手动切换5"]
leaf = {"♻️ 手动切换1": "good-node", "♻️ 手动切换5": "bad-node"}

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def write_json(self, status, body):
        payload = json.dumps(body, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = urllib.parse.unquote(parsed.path)
        with open(request_log, "a", encoding="utf-8") as f:
            f.write(json.dumps({"method": "GET", "path": path, "query": parsed.query}, ensure_ascii=False) + "\n")
        if path == "/version":
            self.write_json(200, {"version": "fake"})
        elif path == f"/proxies/{group}":
            self.write_json(200, {"name": group, "type": "Selector", "now": "♻️ 手动切换5", "all": members})
        elif path == "/proxies/♻️ 手动切换1":
            self.write_json(200, {"name": "♻️ 手动切换1", "type": "Selector", "now": "good-node", "all": ["good-node"]})
        elif path == "/proxies/♻️ 手动切换5":
            self.write_json(200, {"name": "♻️ 手动切换5", "type": "Selector", "now": "bad-node", "all": ["bad-node"]})
        elif path == "/proxies/good-node":
            self.write_json(200, {"name": "good-node", "type": "AnyTLS"})
        elif path == "/proxies/bad-node":
            self.write_json(200, {"name": "bad-node", "type": "AnyTLS"})
        elif path == "/proxies/♻️ 手动切换1/delay":
            self.write_json(200, {"delay": 123})
        elif path == "/proxies/♻️ 手动切换5/delay":
            self.write_json(500, {"message": "timeout"})
        else:
            self.write_json(404, {"message": "not found"})

server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
with open(port_file, "w", encoding="utf-8") as f:
    f.write(str(server.server_port))
server.serve_forever()
PY
  server_pid=$!
  SERVER_PIDS+=("$server_pid")
  for _ in {1..50}; do [[ -s "$port_file" ]] && break; sleep 0.1; done
  [[ -s "$port_file" ]] || fail "fake Mihomo scoring server did not start"

  MIHOMO_API_BASE="http://127.0.0.1:$(cat "$port_file")" \
  MIHOMO_API_SECRET="test-secret" \
  MIHOMO_OPENAI_GROUP="🤖 OpenAI" \
  python3 "$SCORE" \
    --score-log "$d/node-score.jsonl" \
    --top3-file "$d/node-top3.json" \
    --now "2026-05-03T00:00:00Z"

  python3 - "$d/node-score.jsonl" "$d/node-top3.json" <<'PY' || fail "node scoring/quarantine output invalid"
import json
import sys

rows = [json.loads(line) for line in open(sys.argv[1], encoding="utf-8") if line.strip()]
assert {row["candidate"] for row in rows} == {"♻️ 手动切换1", "♻️ 手动切换5"}
good = next(row for row in rows if row["candidate"] == "♻️ 手动切换1")
bad = next(row for row in rows if row["candidate"] == "♻️ 手动切换5")
assert good["resolved_leaf"] == "good-node"
assert good["success_rate"] == 1.0
assert good["quarantined"] is False
assert bad["resolved_leaf"] == "bad-node"
assert bad["success_rate"] == 0.0
assert bad["quarantined"] is True
top = json.load(open(sys.argv[2], encoding="utf-8"))
assert top["top3"][0]["candidate"] == "♻️ 手动切换1"
assert all(item["candidate"] != "♻️ 手动切换5" for item in top["top3"])
PY
  stop_servers
  echo "  PASS: Phase 3 node scoring and quarantine"
}

# ---------------------------------------------------------------------------
# Test 3: Phase 4 (auto-switch) is retired and never mutates Mihomo.
# ---------------------------------------------------------------------------
{
  d="$TMPROOT/autoswitch-retired"
  mkdir -p "$d"
  test_home="$d/home"
  mkdir -p "$test_home"

  set +e
  HOME="$test_home" \
  MIHOMO_API_BASE="http://127.0.0.1:9" \
  MIHOMO_API_SECRET="test-secret" \
  MIHOMO_OPENAI_GROUP="🤖 OpenAI" \
  python3 "$AUTOSWITCH" \
    --results-file "$d/results.json" \
    --state-file "$d/state.json" \
    --score-log "$d/node-score.jsonl" \
    --decision-log "$d/auto-switch.jsonl" \
    --sentinel "$d/missing-sentinel" \
    --now "2026-05-03T00:30:00Z" >"$d/out" 2>"$d/err"
  rc=$?
  set -e
  [[ "$rc" -eq 0 ]] || fail "retired auto-switch.py should exit 0, got $rc"
  grep -qi "is retired and performed no mutation" "$d/err" || fail "retired auto-switch.py should explain retirement"
  [[ -s "$test_home/Library/Logs/k2b-router-watchdog/retired-mutations.jsonl" ]] || fail "retired auto-switch.py should leave an audit log"
  [[ ! -e "$d/auto-switch.jsonl" ]] || fail "retired auto-switch.py should not write a decision log"
  echo "  PASS: Phase 4 auto-switch is retired and performs no mutation"
}

# ---------------------------------------------------------------------------
# Test 4: Router watchdog alerts go to the optional Network topic only when configured.
# ---------------------------------------------------------------------------
{
  d="$TMPROOT/telegram-fanout"
  fakebin="$d/fakebin"
  mkdir -p "$fakebin"
  cat > "$d/watchdog.env" <<'EOF'
TELEGRAM_BOT_TOKEN=test-token
KEITH_CHAT_ID=8394008217
K2B_NETWORK_ALERT_CHAT_ID=-1003966532428
K2B_NETWORK_ALERT_THREAD_ID=6
EOF
  chmod 600 "$d/watchdog.env"
  cat > "$fakebin/curl" <<EOF
#!/usr/bin/env bash
# Fake curl for send-alert.sh testing. After MED-6, the real curl is invoked
# with --data-binary @PATH for the JSON payload and -K - for the URL config
# on stdin (so TELEGRAM_BOT_TOKEN never appears in argv). This stub mirrors
# both pathways and writes argv to argv.log so the test can assert the token
# never appeared in argv.
payload=""
data_file=""
read_config_from_stdin=false
all_argv="\$*"
printf '%s\n' "\$all_argv" >> "$d/argv.log"
while [[ \$# -gt 0 ]]; do
  case "\$1" in
    -d|--data)
      payload="\${2:-}"
      shift 2
      ;;
    --data-binary)
      arg="\${2:-}"
      if [[ "\${arg:0:1}" == "@" ]]; then
        data_file="\${arg:1}"
      else
        payload="\$arg"
      fi
      shift 2
      ;;
    -K|--config)
      if [[ "\${2:-}" == "-" ]]; then
        read_config_from_stdin=true
      fi
      shift 2
      ;;
    *)
      shift
      ;;
  esac
done
if [[ -n "\$data_file" && -f "\$data_file" ]]; then
  payload="\$(cat "\$data_file")"
fi
if \$read_config_from_stdin; then
  cat > "$d/curl-config.txt"
fi
printf '%s\n' "\$payload" >> "$d/payloads.jsonl"
printf '{"ok":true,"result":{"message_id":123}}\n'
EOF
  chmod +x "$fakebin/curl"
  cat > "$d/event.json" <<'EOF'
{"timestamp":"2026-05-03T00:00:00Z","type":"failure","check":"chatgpt_https","message":"test network alert"}
EOF

  PATH="$fakebin:$PATH" \
  K2B_ROUTER_WATCHDOG_ENV_FILE="$d/watchdog.env" \
  K2B_ROUTER_WATCHDOG_ALERTS_LOG="$d/alerts.jsonl" \
  "$SRC_DIR/bin/send-alert.sh" --event-json "$d/event.json"

  python3 - "$d/payloads.jsonl" "$d/alerts.jsonl" <<'PY' || fail "alerts should go to Network topic only when configured"
import json
import sys

payloads = [json.loads(line) for line in open(sys.argv[1], encoding="utf-8") if line.strip()]
assert len(payloads) == 1
assert payloads[0]["chat_id"] == "-1003966532428"
assert payloads[0]["message_thread_id"] == 6
events = [json.loads(line) for line in open(sys.argv[2], encoding="utf-8") if line.strip()]
assert events[-1]["delivered_targets"] == [
    {"chat_id": "-1003966532428", "message_thread_id": 6},
]
PY
  echo "  PASS: Telegram Network topic routing"

  # MED-6: TELEGRAM_BOT_TOKEN must never appear in argv (visible to ps -ef).
  # The token MUST appear in the curl-config stdin instead.
  if [[ -f "$d/argv.log" ]]; then
    if grep -q 'test-token' "$d/argv.log"; then
      echo "argv.log contained the bot token:" >&2
      cat "$d/argv.log" >&2
      fail "MED-6: TELEGRAM_BOT_TOKEN leaked into curl argv"
    fi
  else
    fail "MED-6: argv.log not created -- fake curl was not invoked"
  fi
  if [[ ! -f "$d/curl-config.txt" ]]; then
    fail "MED-6: curl was not invoked with -K - (URL not on stdin)"
  fi
  if ! grep -q 'test-token' "$d/curl-config.txt"; then
    fail "MED-6: curl-config stdin did not contain the bot token"
  fi
  if ! grep -q '^url = "https://api.telegram.org/bottest-token/sendMessage"' "$d/curl-config.txt"; then
    fail "MED-6: curl-config stdin did not have the expected url= line"
  fi
  echo "  PASS: MED-6 TELEGRAM_BOT_TOKEN not in curl argv (passed via -K - stdin)"
}

# ---------------------------------------------------------------------------
# Test 5: Phase 4 stays retired even when all historical enablement is present.
# ---------------------------------------------------------------------------
{
  d="$TMPROOT/autoswitch-stays-retired"
  mkdir -p "$d"
  test_home="$d/home"
  mkdir -p "$test_home"
  touch "$d/sentinel"

  set +e
  HOME="$test_home" \
  MIHOMO_API_BASE="http://127.0.0.1:1" \
  MIHOMO_API_SECRET="test-secret" \
  MIHOMO_OPENAI_GROUP="🤖 OpenAI" \
  K2B_ROUTER_MUTATION_RETIRED_ALLOW=1 \
  python3 "$AUTOSWITCH" \
    --results-file "$d/results.json" \
    --state-file "$d/state.json" \
    --score-log "$d/node-score.jsonl" \
    --decision-log "$d/auto-switch.jsonl" \
    --alerts-file "$d/alerts.jsonl" \
    --sentinel "$d/sentinel" \
    --now "2026-05-03T00:30:00Z" >"$d/out" 2>"$d/err"
  rc=$?
  set -e
  [[ "$rc" -eq 0 ]] || fail "retired auto-switch.py should exit 0 even with legacy enablement, got $rc"
  grep -qi "is retired and performed no mutation" "$d/err" || fail "retired auto-switch.py should explain retirement even with legacy enablement"
  [[ -s "$test_home/Library/Logs/k2b-router-watchdog/retired-mutations.jsonl" ]] || fail "retired auto-switch.py should leave an audit log"
  [[ ! -e "$d/auto-switch.jsonl" ]] || fail "retired auto-switch.py should not write a decision log"
  [[ ! -e "$d/alerts.jsonl" ]] || fail "retired auto-switch.py should not write an alerts file"
  echo "  PASS: Phase 4 stays retired regardless of legacy enablement"
}

echo "router-watchdog-phase234.test.sh: all tests passed"
