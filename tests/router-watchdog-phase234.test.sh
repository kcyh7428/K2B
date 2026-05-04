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
# Test 3: Phase 4 does not mutate Mihomo when the sentinel is absent.
# ---------------------------------------------------------------------------
{
  d="$TMPROOT/autoswitch-disabled"
  mkdir -p "$d"
  cat > "$d/results.json" <<'EOF'
[{"name":"chatgpt_https","ok":false,"alertable":true,"latency_ms":10,"severity":"fail","message":"HTTP 000","details":{}},{"name":"openai_node","ok":true,"alertable":false,"latency_ms":10,"severity":"ok","message":"OpenAI selected node: bad-node","details":{"openai_group_selection":"♻️ 手动切换5","selected_node":"bad-node","selector_chain":["🤖 OpenAI","♻️ 手动切换5","bad-node"]}}]
EOF
  cat > "$d/state.json" <<'EOF'
{"chatgpt_https":{"status":"fail","consecutive_fails":3,"since":"2026-05-03T00:00:00Z","last_alert_at":"2026-05-03T00:20:00Z","alert_count_in_outage":1}}
EOF
  cat > "$d/node-score.jsonl" <<'EOF'
{"timestamp":"2026-05-03T00:00:00Z","candidate":"♻️ 手动切换1","resolved_leaf":"good-node","success_rate":1.0,"score":0.98,"quarantined":false}
{"timestamp":"2026-05-03T00:00:00Z","candidate":"♻️ 手动切换5","resolved_leaf":"bad-node","success_rate":0.0,"score":0.0,"quarantined":true,"quarantine_until":"2026-05-04T00:00:00Z"}
EOF

  MIHOMO_API_BASE="http://127.0.0.1:9" \
  MIHOMO_API_SECRET="test-secret" \
  MIHOMO_OPENAI_GROUP="🤖 OpenAI" \
  python3 "$AUTOSWITCH" \
    --results-file "$d/results.json" \
    --state-file "$d/state.json" \
    --score-log "$d/node-score.jsonl" \
    --decision-log "$d/auto-switch.jsonl" \
    --sentinel "$d/missing-sentinel" \
    --now "2026-05-03T00:30:00Z"

  grep -q '"enabled":false' "$d/auto-switch.jsonl" || fail "disabled auto-switch should log enabled=false"
  echo "  PASS: Phase 4 sentinel absent prevents mutation"
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
payload=""
while [[ \$# -gt 0 ]]; do
  case "\$1" in
    -d)
      payload="\${2:-}"
      shift 2
      ;;
    *)
      shift
      ;;
  esac
done
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
}

# ---------------------------------------------------------------------------
# Test 5: Phase 4 refreshes scores on a live failure trigger and switches to a
# clearly better manual selector even when the current selector is not yet
# quarantined.
# ---------------------------------------------------------------------------
{
  d="$TMPROOT/autoswitch-refresh"
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

    def do_PUT(self):
        parsed = urllib.parse.urlparse(self.path)
        path = urllib.parse.unquote(parsed.path)
        body = self.rfile.read(int(self.headers.get("Content-Length", "0"))).decode()
        with open(request_log, "a", encoding="utf-8") as f:
            f.write(json.dumps({"method": "PUT", "path": path, "body": body}, ensure_ascii=False) + "\n")
        if path == f"/proxies/{group}":
            self.write_json(204, {})
        else:
            self.write_json(404, {"message": "wrong endpoint"})

server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
with open(port_file, "w", encoding="utf-8") as f:
    f.write(str(server.server_port))
server.serve_forever()
PY
  server_pid=$!
  SERVER_PIDS+=("$server_pid")
  for _ in {1..50}; do [[ -s "$port_file" ]] && break; sleep 0.1; done
  [[ -s "$port_file" ]] || fail "fake Mihomo refresh auto-switch server did not start"
  touch "$d/sentinel"
  cat > "$d/results.json" <<'EOF'
[{"name":"chatgpt_https","ok":false,"alertable":true,"latency_ms":10,"severity":"fail","message":"HTTP 000","details":{}},{"name":"chatgpt_ws","ok":false,"alertable":true,"latency_ms":10,"severity":"fail","message":"HTTP 000","details":{}},{"name":"claude_https","ok":false,"alertable":true,"latency_ms":10,"severity":"fail","message":"HTTP 000","details":{}},{"name":"openai_node","ok":true,"alertable":false,"latency_ms":10,"severity":"ok","message":"OpenAI selected node: slow-node","details":{"openai_group_selection":"♻️ 手动切换","selected_node":"slow-node","selector_chain":["🤖 OpenAI","♻️ 手动切换","slow-node"]}}]
EOF
  cat > "$d/state.json" <<'EOF'
{"chatgpt_https":{"status":"fail","consecutive_fails":3,"since":"2026-05-03T00:00:00Z","last_alert_at":"2026-05-03T00:20:00Z","alert_count_in_outage":1},"chatgpt_ws":{"status":"fail","consecutive_fails":3,"since":"2026-05-03T00:00:00Z","last_alert_at":"2026-05-03T00:20:00Z","alert_count_in_outage":1},"claude_https":{"status":"fail","consecutive_fails":3,"since":"2026-05-03T00:00:00Z","last_alert_at":"2026-05-03T00:20:00Z","alert_count_in_outage":1}}
EOF
  cat > "$d/node-score.jsonl" <<'EOF'
{"timestamp":"2026-05-03T00:00:00Z","candidate":"♻️ 手动切换","resolved_leaf":"slow-node","success_rate":1.0,"score":0.91,"quarantined":false}
EOF
  cat > "$d/refresh-score.sh" <<EOF
#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' refreshed > "$d/refresh-called"
cat >> "$d/node-score.jsonl" <<'JSON'
{"timestamp":"2026-05-03T00:30:00Z","candidate":"♻️ 手动切换","resolved_leaf":"slow-node","success_rate":1.0,"score":0.84,"quarantined":false}
{"timestamp":"2026-05-03T00:30:00Z","candidate":"♻️ 手动切换2","resolved_leaf":"better-node","success_rate":1.0,"score":0.96,"quarantined":false}
JSON
EOF
  chmod +x "$d/refresh-score.sh"

  MIHOMO_API_BASE="http://127.0.0.1:$(cat "$port_file")" \
  MIHOMO_API_SECRET="test-secret" \
  MIHOMO_OPENAI_GROUP="🤖 OpenAI" \
  python3 "$AUTOSWITCH" \
    --results-file "$d/results.json" \
    --state-file "$d/state.json" \
    --score-log "$d/node-score.jsonl" \
    --decision-log "$d/auto-switch.jsonl" \
    --alerts-file "$d/alerts.jsonl" \
    --sentinel "$d/sentinel" \
    --score-command "$d/refresh-score.sh" \
    --now "2026-05-03T00:30:00Z"

  python3 - "$request_log" "$d/auto-switch.jsonl" "$d/alerts.jsonl" "$d/refresh-called" <<'PY' || fail "auto-switch should refresh scores and switch to clearly better live candidate"
import json
import sys

assert open(sys.argv[4], encoding="utf-8").read().strip() == "refreshed"
requests = [json.loads(line) for line in open(sys.argv[1], encoding="utf-8") if line.strip()]
assert len(requests) == 1
assert requests[0]["path"] == "/proxies/🤖 OpenAI"
assert json.loads(requests[0]["body"]) == {"name": "♻️ 手动切换2"}
decision = [json.loads(line) for line in open(sys.argv[2], encoding="utf-8") if line.strip()][-1]
assert decision["switched"] is True
assert decision["reason"] == "switched"
assert decision["score_refresh"] == "ok"
assert decision["from_candidate"] == "♻️ 手动切换"
assert decision["to_candidate"] == "♻️ 手动切换2"
assert decision["score_delta"] >= 0.05
alert = [json.loads(line) for line in open(sys.argv[3], encoding="utf-8") if line.strip()][-1]
assert alert["type"] == "auto_switch"
assert "♻️ 手动切换2" in alert["message"]
PY
  stop_servers
  echo "  PASS: Phase 4 refreshes scores and switches to better live candidate"
}

# ---------------------------------------------------------------------------
# Test 6: Phase 4, when explicitly enabled, performs exactly one scoped PUT to
# the OpenAI group and emits an alert event.
# ---------------------------------------------------------------------------
{
  d="$TMPROOT/autoswitch-enabled"
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

    def do_PUT(self):
        parsed = urllib.parse.urlparse(self.path)
        path = urllib.parse.unquote(parsed.path)
        body = self.rfile.read(int(self.headers.get("Content-Length", "0"))).decode()
        with open(request_log, "a", encoding="utf-8") as f:
            f.write(json.dumps({"method": "PUT", "path": path, "body": body}, ensure_ascii=False) + "\n")
        if path == f"/proxies/{group}":
            self.write_json(204, {})
        else:
            self.write_json(404, {"message": "wrong endpoint"})

server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
with open(port_file, "w", encoding="utf-8") as f:
    f.write(str(server.server_port))
server.serve_forever()
PY
  server_pid=$!
  SERVER_PIDS+=("$server_pid")
  for _ in {1..50}; do [[ -s "$port_file" ]] && break; sleep 0.1; done
  [[ -s "$port_file" ]] || fail "fake Mihomo auto-switch server did not start"
  touch "$d/sentinel"
  cat > "$d/results.json" <<'EOF'
[{"name":"chatgpt_https","ok":false,"alertable":true,"latency_ms":10,"severity":"fail","message":"HTTP 000","details":{}},{"name":"openai_node","ok":true,"alertable":false,"latency_ms":10,"severity":"ok","message":"OpenAI selected node: bad-node","details":{"openai_group_selection":"♻️ 手动切换5","selected_node":"bad-node","selector_chain":["🤖 OpenAI","♻️ 手动切换5","bad-node"]}}]
EOF
  cat > "$d/state.json" <<'EOF'
{"chatgpt_https":{"status":"fail","consecutive_fails":3,"since":"2026-05-03T00:00:00Z","last_alert_at":"2026-05-03T00:20:00Z","alert_count_in_outage":1}}
EOF
  cat > "$d/node-score.jsonl" <<'EOF'
{"timestamp":"2026-05-03T00:00:00Z","candidate":"♻️ 手动切换1","resolved_leaf":"good-node","success_rate":1.0,"score":0.98,"quarantined":false}
{"timestamp":"2026-05-03T00:00:00Z","candidate":"♻️ 手动切换5","resolved_leaf":"bad-node","success_rate":0.0,"score":0.0,"quarantined":true,"quarantine_until":"2026-05-04T00:00:00Z"}
EOF

  MIHOMO_API_BASE="http://127.0.0.1:$(cat "$port_file")" \
  MIHOMO_API_SECRET="test-secret" \
  MIHOMO_OPENAI_GROUP="🤖 OpenAI" \
  python3 "$AUTOSWITCH" \
    --results-file "$d/results.json" \
    --state-file "$d/state.json" \
    --score-log "$d/node-score.jsonl" \
    --decision-log "$d/auto-switch.jsonl" \
    --alerts-file "$d/alerts.jsonl" \
    --sentinel "$d/sentinel" \
    --now "2026-05-03T00:30:00Z"

  python3 - "$request_log" "$d/auto-switch.jsonl" "$d/alerts.jsonl" <<'PY' || fail "auto-switch should perform one scoped PUT and emit alert"
import json
import sys

requests = [json.loads(line) for line in open(sys.argv[1], encoding="utf-8") if line.strip()]
assert len(requests) == 1
assert requests[0]["method"] == "PUT"
assert requests[0]["path"] == "/proxies/🤖 OpenAI"
assert json.loads(requests[0]["body"]) == {"name": "♻️ 手动切换1"}
decision = [json.loads(line) for line in open(sys.argv[2], encoding="utf-8") if line.strip()][-1]
assert decision["enabled"] is True
assert decision["switched"] is True
assert decision["from_candidate"] == "♻️ 手动切换5"
assert decision["to_candidate"] == "♻️ 手动切换1"
alert = [json.loads(line) for line in open(sys.argv[3], encoding="utf-8") if line.strip()][-1]
assert alert["type"] == "auto_switch"
assert "♻️ 手动切换1" in alert["message"]
PY
  stop_servers
  echo "  PASS: Phase 4 scoped auto-switch when sentinel enabled"
}

# Test 7 (Codex finding 2 regression): a Mihomo PUT failure during auto-switch
# must NOT propagate as an exception that aborts check.sh's `set -e` pipeline
# before pending watchdog alerts get drained. auto-switch.py must catch the
# exception, log a `put_exception_*` decision + an `auto_switch_blocked` alert,
# and return 0.
{
  d="$(mktemp -d)"
  TMP_DIRS+=("$d")
  touch "$d/sentinel"
  cat > "$d/results.json" <<'EOF'
[{"name":"chatgpt_https","ok":false,"alertable":true,"latency_ms":10,"severity":"fail","message":"HTTP 000","details":{}},{"name":"openai_node","ok":true,"alertable":false,"latency_ms":10,"severity":"ok","message":"OpenAI selected node: bad-node","details":{"openai_group_selection":"♻️ 手动切换5","selected_node":"bad-node","selector_chain":["🤖 OpenAI","♻️ 手动切换5","bad-node"]}}]
EOF
  cat > "$d/state.json" <<'EOF'
{"chatgpt_https":{"status":"fail","consecutive_fails":3,"since":"2026-05-03T00:00:00Z","last_alert_at":"2026-05-03T00:20:00Z","alert_count_in_outage":1}}
EOF
  cat > "$d/node-score.jsonl" <<'EOF'
{"timestamp":"2026-05-03T00:00:00Z","candidate":"♻️ 手动切换1","resolved_leaf":"good-node","success_rate":1.0,"score":0.98,"quarantined":false}
{"timestamp":"2026-05-03T00:00:00Z","candidate":"♻️ 手动切换5","resolved_leaf":"bad-node","success_rate":0.0,"score":0.0,"quarantined":true,"quarantine_until":"2026-05-04T00:00:00Z"}
EOF

  # Point the script at a port that nothing is listening on. The PUT will
  # raise (ConnectionRefusedError or URLError) and the script must catch it.
  set +e
  MIHOMO_API_BASE="http://127.0.0.1:1" \
  MIHOMO_API_SECRET="test-secret" \
  MIHOMO_OPENAI_GROUP="🤖 OpenAI" \
  python3 "$AUTOSWITCH" \
    --results-file "$d/results.json" \
    --state-file "$d/state.json" \
    --score-log "$d/node-score.jsonl" \
    --decision-log "$d/auto-switch.jsonl" \
    --alerts-file "$d/alerts.jsonl" \
    --sentinel "$d/sentinel" \
    --now "2026-05-03T00:30:00Z"
  rc=$?
  set -e
  [[ "$rc" -eq 0 ]] || fail "auto-switch.py must return 0 on PUT failure, got $rc"

  python3 - "$d/auto-switch.jsonl" "$d/alerts.jsonl" <<'PY' || fail "auto-switch PUT failure should log blocked decision + blocked alert"
import json
import sys

decision = [json.loads(line) for line in open(sys.argv[1], encoding="utf-8") if line.strip()][-1]
assert decision["switched"] is False, f"switched should be False, got {decision.get('switched')}"
assert decision["http_status"] == 0, f"http_status should be 0 on exception, got {decision.get('http_status')}"
assert decision["reason"].startswith("put_exception_"), \
    f"reason should be put_exception_*, got {decision.get('reason')}"

alerts = [json.loads(line) for line in open(sys.argv[2], encoding="utf-8") if line.strip()]
assert any(a.get("type") == "auto_switch_blocked" for a in alerts), \
    "alerts file must contain at least one auto_switch_blocked entry"
PY
  echo "  PASS: Phase 4 PUT failure does not abort check.sh alert drain (Codex finding 2 regression)"
}

echo "router-watchdog-phase234.test.sh: all tests passed"
