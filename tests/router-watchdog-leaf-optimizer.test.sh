#!/usr/bin/env bash
# tests/router-watchdog-leaf-optimizer.test.sh
# Local integration tests for the router manual-selector leaf optimizer.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC_DIR="$REPO_ROOT/scripts/router-watchdog"
OPTIMIZER="$SRC_DIR/bin/optimize-leaves.py"

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

start_fake_mihomo() {
  local d="$1"
  local port_file="$d/port"
  local request_log="$d/requests.jsonl"
  python3 -u - "$port_file" "$request_log" <<'PY' &
import json
import os
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

port_file, request_log = sys.argv[1], sys.argv[2]
group = "🤖 OpenAI"
manuals = ["♻️ 手动切换1", "♻️ 手动切换2"]
leafs = ["🇭🇰2香港-专线(AnyTLS) [AP1]", "5台湾-联通/移动(AnyTLS) [AP1]", "🇸🇬15新加坡-专线(AnyTLS) [AP1]", "bad-ai-node"]
now = {
    "♻️ 手动切换1": "🇭🇰2香港-专线(AnyTLS) [AP1]",
    "♻️ 手动切换2": "bad-ai-node",
}
delays = {
    "🇭🇰2香港-专线(AnyTLS) [AP1]": 20,
    "5台湾-联通/移动(AnyTLS) [AP1]": 160,
    "🇸🇬15新加坡-专线(AnyTLS) [AP1]": 220,
}

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

    def record(self, body=None):
        parsed = urllib.parse.urlparse(self.path)
        with open(request_log, "a", encoding="utf-8") as f:
            f.write(json.dumps({
                "method": self.command,
                "path": urllib.parse.unquote(parsed.path),
                "query": parsed.query,
                "body": body,
            }, ensure_ascii=False) + "\n")
        return parsed, urllib.parse.unquote(parsed.path)

    def do_GET(self):
        parsed, path = self.record()
        if path == f"/proxies/{group}":
            self.write_json(200, {"name": group, "type": "Selector", "now": "♻️ 手动切换1", "all": [group] + manuals})
        elif path in {f"/proxies/{name}" for name in manuals}:
            name = path.removeprefix("/proxies/")
            self.write_json(200, {"name": name, "type": "Selector", "now": now[name], "all": leafs})
        elif path.startswith("/proxies/") and path.endswith("/delay"):
            name = path.removeprefix("/proxies/").removesuffix("/delay")
            if name == "bad-ai-node":
                self.write_json(500, {"message": "timeout"})
            else:
                self.write_json(200, {"delay": delays[name]})
        elif path == "/proxies":
            proxies = {
                group: {"name": group, "type": "Selector", "now": "♻️ 手动切换1", "all": [group] + manuals},
            }
            for name in manuals:
                proxies[name] = {"name": name, "type": "Selector", "now": now[name], "all": leafs}
            for name in leafs:
                proxies[name] = {"name": name, "type": "AnyTLS"}
            self.write_json(200, {"proxies": proxies})
        elif path.startswith("/proxies/"):
            name = path.removeprefix("/proxies/")
            self.write_json(200, {"name": name, "type": "AnyTLS"})
        else:
            self.write_json(404, {"message": "not found"})

    def do_PUT(self):
        raw = self.rfile.read(int(self.headers.get("Content-Length", "0"))).decode()
        body = json.loads(raw or "{}")
        _, path = self.record(body)
        if path in {f"/proxies/{name}" for name in manuals}:
            name = path.removeprefix("/proxies/")
            now[name] = body["name"]
            self.write_json(204, {})
        else:
            self.write_json(500, {"message": "unsafe PUT"})

server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
with open(port_file, "w", encoding="utf-8") as f:
    f.write(str(server.server_port))
server.serve_forever()
PY
  SERVER_PIDS+=("$!")
  for _ in {1..50}; do [[ -s "$port_file" ]] && break; sleep 0.1; done
  [[ -s "$port_file" ]] || fail "fake Mihomo server did not start"
}

start_stable_fake_mihomo() {
  local d="$1"
  local current_delay="$2"
  local better_delay="$3"
  local port_file="$d/port"
  local request_log="$d/requests.jsonl"
  python3 -u - "$port_file" "$request_log" "$current_delay" "$better_delay" <<'PY' &
import json
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

port_file, request_log, current_delay, better_delay = sys.argv[1], sys.argv[2], int(sys.argv[3]), int(sys.argv[4])
group = "🤖 OpenAI"
selector = "♻️ 手动切换1"
current = "5台湾-联通/移动(AnyTLS) [AP1]"
better = "🇸🇬15新加坡-专线(AnyTLS) [AP1]"
delays = {current: current_delay, better: better_delay}
now = {"leaf": current}

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

    def record(self, body=None):
        parsed = urllib.parse.urlparse(self.path)
        path = urllib.parse.unquote(parsed.path)
        with open(request_log, "a", encoding="utf-8") as f:
            f.write(json.dumps({"method": self.command, "path": path, "query": parsed.query, "body": body}, ensure_ascii=False) + "\n")
        return path

    def do_GET(self):
        path = self.record()
        if path == f"/proxies/{group}":
            self.write_json(200, {"name": group, "type": "Selector", "now": selector, "all": [selector]})
        elif path == f"/proxies/{selector}":
            self.write_json(200, {"name": selector, "type": "Selector", "now": now["leaf"], "all": [current, better]})
        elif path == "/proxies":
            self.write_json(200, {"proxies": {
                group: {"name": group, "type": "Selector", "now": selector, "all": [selector]},
                selector: {"name": selector, "type": "Selector", "now": current, "all": [current, better]},
                current: {"name": current, "type": "AnyTLS"},
                better: {"name": better, "type": "AnyTLS"},
            }})
        elif path.startswith("/proxies/") and path.endswith("/delay"):
            name = path.removeprefix("/proxies/").removesuffix("/delay")
            self.write_json(200, {"delay": delays[name]})
        elif path.startswith("/proxies/"):
            name = path.removeprefix("/proxies/")
            self.write_json(200, {"name": name, "type": "AnyTLS"})
        else:
            self.write_json(404, {"message": "not found"})

    def do_PUT(self):
        raw = self.rfile.read(int(self.headers.get("Content-Length", "0"))).decode()
        body = json.loads(raw or "{}")
        path = self.record(body)
        if path == f"/proxies/{selector}":
            now["leaf"] = body["name"]
        self.write_json(204 if path == f"/proxies/{selector}" else 500, {})

server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
with open(port_file, "w", encoding="utf-8") as f:
    f.write(str(server.server_port))
server.serve_forever()
PY
  SERVER_PIDS+=("$!")
  for _ in {1..50}; do [[ -s "$port_file" ]] && break; sleep 0.1; done
  [[ -s "$port_file" ]] || fail "fake Mihomo server did not start"
}

start_stale_fake_mihomo() {
  local d="$1"
  local port_file="$d/port"
  local request_log="$d/requests.jsonl"
  python3 -u - "$port_file" "$request_log" <<'PY' &
import json
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

port_file, request_log = sys.argv[1], sys.argv[2]
group = "🤖 OpenAI"
selector = "♻️ 手动切换1"
current = "5台湾-联通/移动(AnyTLS) [AP1]"
better = "🇸🇬15新加坡-专线(AnyTLS) [AP1]"
external = "manual-outside-change"
selector_gets = {"count": 0}

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

    def record(self, body=None):
        parsed = urllib.parse.urlparse(self.path)
        path = urllib.parse.unquote(parsed.path)
        with open(request_log, "a", encoding="utf-8") as f:
            f.write(json.dumps({"method": self.command, "path": path, "query": parsed.query, "body": body}, ensure_ascii=False) + "\n")
        return path

    def do_GET(self):
        path = self.record()
        if path == f"/proxies/{group}":
            self.write_json(200, {"name": group, "type": "Selector", "now": selector, "all": [selector]})
        elif path == f"/proxies/{selector}":
            selector_gets["count"] += 1
            now = current if selector_gets["count"] == 1 else external
            self.write_json(200, {"name": selector, "type": "Selector", "now": now, "all": [current, better, external]})
        elif path == "/proxies":
            self.write_json(200, {"proxies": {
                group: {"name": group, "type": "Selector", "now": selector, "all": [selector]},
                selector: {"name": selector, "type": "Selector", "now": current, "all": [current, better, external]},
                current: {"name": current, "type": "AnyTLS"},
                better: {"name": better, "type": "AnyTLS"},
                external: {"name": external, "type": "AnyTLS"},
            }})
        elif path.startswith("/proxies/") and path.endswith("/delay"):
            name = path.removeprefix("/proxies/").removesuffix("/delay")
            self.write_json(200, {"delay": 100 if name == better else 800})
        elif path.startswith("/proxies/"):
            self.write_json(200, {"name": path.removeprefix("/proxies/"), "type": "AnyTLS"})
        else:
            self.write_json(404, {"message": "not found"})

    def do_PUT(self):
        raw = self.rfile.read(int(self.headers.get("Content-Length", "0"))).decode()
        path = self.record(json.loads(raw or "{}"))
        self.write_json(500, {"message": f"unexpected PUT {path}"})

server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
with open(port_file, "w", encoding="utf-8") as f:
    f.write(str(server.server_port))
server.serve_forever()
PY
  SERVER_PIDS+=("$!")
  for _ in {1..50}; do [[ -s "$port_file" ]] && break; sleep 0.1; done
  [[ -s "$port_file" ]] || fail "fake Mihomo server did not start"
}

start_put_behavior_fake_mihomo() {
  local d="$1"
  local behavior="$2"
  local port_file="$d/port"
  local request_log="$d/requests.jsonl"
  python3 -u - "$port_file" "$request_log" "$behavior" <<'PY' &
import json
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

port_file, request_log, behavior = sys.argv[1], sys.argv[2], sys.argv[3]
group = "🤖 OpenAI"
selector = "♻️ 手动切换1"
current = "5台湾-联通/移动(AnyTLS) [AP1]"
better = "🇸🇬15新加坡-专线(AnyTLS) [AP1]"
now = {"leaf": current}
put_count = {"count": 0}

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

    def record(self, body=None):
        parsed = urllib.parse.urlparse(self.path)
        path = urllib.parse.unquote(parsed.path)
        with open(request_log, "a", encoding="utf-8") as f:
            f.write(json.dumps({"method": self.command, "path": path, "query": parsed.query, "body": body}, ensure_ascii=False) + "\n")
        return path

    def do_GET(self):
        path = self.record()
        if path == f"/proxies/{group}":
            self.write_json(200, {"name": group, "type": "Selector", "now": selector, "all": [selector]})
        elif path == f"/proxies/{selector}":
            self.write_json(200, {"name": selector, "type": "Selector", "now": now["leaf"], "all": [current, better]})
        elif path == "/proxies":
            self.write_json(200, {"proxies": {
                group: {"name": group, "type": "Selector", "now": selector, "all": [selector]},
                selector: {"name": selector, "type": "Selector", "now": now["leaf"], "all": [current, better]},
                current: {"name": current, "type": "AnyTLS"},
                better: {"name": better, "type": "AnyTLS"},
            }})
        elif path.startswith("/proxies/") and path.endswith("/delay"):
            name = path.removeprefix("/proxies/").removesuffix("/delay")
            self.write_json(200, {"delay": 100 if name == better else 800})
        elif path.startswith("/proxies/"):
            self.write_json(200, {"name": path.removeprefix("/proxies/"), "type": "AnyTLS"})
        else:
            self.write_json(404, {"message": "not found"})

    def do_PUT(self):
        raw = self.rfile.read(int(self.headers.get("Content-Length", "0"))).decode()
        body = json.loads(raw or "{}")
        path = self.record(body)
        put_count["count"] += 1
        if path != f"/proxies/{selector}":
            self.write_json(500, {"message": "unsafe PUT"})
        elif behavior == "retry" and put_count["count"] == 1:
            self.write_json(500, {"message": "transient"})
        elif behavior == "nochange":
            self.write_json(204, {})
        else:
            now["leaf"] = body["name"]
            self.write_json(204, {})

server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
with open(port_file, "w", encoding="utf-8") as f:
    f.write(str(server.server_port))
server.serve_forever()
PY
  SERVER_PIDS+=("$!")
  for _ in {1..50}; do [[ -s "$port_file" ]] && break; sleep 0.1; done
  [[ -s "$port_file" ]] || fail "fake Mihomo server did not start"
}

start_meta_fake_mihomo() {
  local d="$1"
  local port_file="$d/port"
  local request_log="$d/requests.jsonl"
  python3 -u - "$port_file" "$request_log" <<'PY' &
import json
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

port_file, request_log = sys.argv[1], sys.argv[2]
group = "🤖 OpenAI"
selector = "♻️ 手动切换1"
meta = "🌏自动最优线路(AnyTLS)-网址: www.dg6.me [AP1]"
current = "5台湾-联通/移动(AnyTLS) [AP1]"
better = "🇸🇬15新加坡-专线(AnyTLS) [AP1]"
delays = {meta: 10, current: 220, better: 100}
now = {"leaf": current}

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

    def record(self, body=None):
        parsed = urllib.parse.urlparse(self.path)
        path = urllib.parse.unquote(parsed.path)
        with open(request_log, "a", encoding="utf-8") as f:
            f.write(json.dumps({"method": self.command, "path": path, "query": parsed.query, "body": body}, ensure_ascii=False) + "\n")
        return path

    def do_GET(self):
        path = self.record()
        if path == f"/proxies/{group}":
            self.write_json(200, {"name": group, "type": "Selector", "now": selector, "all": [selector]})
        elif path == f"/proxies/{selector}":
            self.write_json(200, {"name": selector, "type": "Selector", "now": now["leaf"], "all": [meta, current, better]})
        elif path == "/proxies":
            self.write_json(200, {"proxies": {
                group: {"name": group, "type": "Selector", "now": selector, "all": [selector]},
                selector: {"name": selector, "type": "Selector", "now": now["leaf"], "all": [meta, current, better]},
                meta: {"name": meta, "type": "AnyTLS"},
                current: {"name": current, "type": "AnyTLS"},
                better: {"name": better, "type": "AnyTLS"},
            }})
        elif path.startswith("/proxies/") and path.endswith("/delay"):
            name = path.removeprefix("/proxies/").removesuffix("/delay")
            self.write_json(200, {"delay": delays[name]})
        elif path.startswith("/proxies/"):
            name = path.removeprefix("/proxies/")
            self.write_json(200, {"name": name, "type": "AnyTLS"})
        else:
            self.write_json(404, {"message": "not found"})

    def do_PUT(self):
        raw = self.rfile.read(int(self.headers.get("Content-Length", "0"))).decode()
        body = json.loads(raw or "{}")
        path = self.record(body)
        if path == f"/proxies/{selector}":
            now["leaf"] = body["name"]
            self.write_json(204, {})
        else:
            self.write_json(500, {"message": "unsafe PUT"})

server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
with open(port_file, "w", encoding="utf-8") as f:
    f.write(str(server.server_port))
server.serve_forever()
PY
  SERVER_PIDS+=("$!")
  for _ in {1..50}; do [[ -s "$port_file" ]] && break; sleep 0.1; done
  [[ -s "$port_file" ]] || fail "fake Mihomo server did not start"
}

start_direct_leaf_group_fake_mihomo() {
  local d="$1"
  local port_file="$d/port"
  local request_log="$d/requests.jsonl"
  python3 -u - "$port_file" "$request_log" <<'PY' &
import json
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

port_file, request_log = sys.argv[1], sys.argv[2]
group = "Ⓜ️ 延迟最低"
leafs = ["🇭🇰2香港-专线(AnyTLS) [AP1]", "5台湾-联通/移动(AnyTLS) [AP1]"]

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

    def record(self, body=None):
        parsed = urllib.parse.urlparse(self.path)
        path = urllib.parse.unquote(parsed.path)
        with open(request_log, "a", encoding="utf-8") as f:
            f.write(json.dumps({"method": self.command, "path": path, "query": parsed.query, "body": body}, ensure_ascii=False) + "\n")
        return path

    def do_GET(self):
        path = self.record()
        if path == f"/proxies/{group}":
            self.write_json(200, {"name": group, "type": "URLTest", "now": leafs[0], "all": leafs})
        elif path == "/proxies":
            proxies = {group: {"name": group, "type": "URLTest", "now": leafs[0], "all": leafs}}
            for name in leafs:
                proxies[name] = {"name": name, "type": "AnyTLS"}
            self.write_json(200, {"proxies": proxies})
        elif path.startswith("/proxies/") and path.endswith("/delay"):
            self.write_json(200, {"delay": 20})
        elif path.startswith("/proxies/"):
            self.write_json(200, {"name": path.removeprefix("/proxies/"), "type": "AnyTLS"})
        else:
            self.write_json(404, {"message": "not found"})

    def do_PUT(self):
        raw = self.rfile.read(int(self.headers.get("Content-Length", "0"))).decode()
        self.record(json.loads(raw or "{}"))
        self.write_json(500, {"message": "unexpected PUT"})

server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
with open(port_file, "w", encoding="utf-8") as f:
    f.write(str(server.server_port))
server.serve_forever()
PY
  SERVER_PIDS+=("$!")
  for _ in {1..50}; do [[ -s "$port_file" ]] && break; sleep 0.1; done
  [[ -s "$port_file" ]] || fail "fake Mihomo server did not start"
}

echo "=== router-watchdog-leaf-optimizer.test.sh ==="

# ---------------------------------------------------------------------------
# Test 1: dry-run excludes HK leaves and proposes diverse AI-compatible leaves.
# ---------------------------------------------------------------------------
{
  d="$TMPROOT/dry-run"
  mkdir -p "$d"
  start_fake_mihomo "$d"

  MIHOMO_API_BASE="http://127.0.0.1:$(cat "$d/port")" \
  MIHOMO_API_SECRET="test-secret" \
  MIHOMO_OPENAI_GROUP="🤖 OpenAI" \
  python3 "$OPTIMIZER" \
    --decision-log "$d/leaf-optimizer.jsonl" \
    --now "2026-05-06T00:00:00Z" \
    --dry-run > "$d/stdout.json"

  python3 - "$d/stdout.json" "$d/requests.jsonl" <<'PY' || fail "dry-run optimizer output invalid"
import json
import sys

summary = json.load(open(sys.argv[1], encoding="utf-8"))
assert summary["dry_run"] is True
assignments = {item["selector"]: item for item in summary["assignments"]}
assert assignments["♻️ 手动切换1"]["target_leaf"] == "5台湾-联通/移动(AnyTLS) [AP1]"
assert assignments["♻️ 手动切换2"]["target_leaf"] == "🇸🇬15新加坡-专线(AnyTLS) [AP1]"
assert all("香港" not in item["target_leaf"] and "🇭🇰" not in item["target_leaf"] for item in assignments.values())
requests = [json.loads(line) for line in open(sys.argv[2], encoding="utf-8") if line.strip()]
assert not any(req["method"] == "PUT" for req in requests)
PY
  stop_servers
  echo "  PASS: dry-run excludes HK and preserves diversity"
}

# ---------------------------------------------------------------------------
# Test 2: sentinel absent skips live mutation and logs the skip.
# ---------------------------------------------------------------------------
{
  d="$TMPROOT/sentinel-absent"
  mkdir -p "$d"
  start_fake_mihomo "$d"

  MIHOMO_API_BASE="http://127.0.0.1:$(cat "$d/port")" \
  MIHOMO_API_SECRET="test-secret" \
  MIHOMO_OPENAI_GROUP="🤖 OpenAI" \
  python3 "$OPTIMIZER" \
    --decision-log "$d/leaf-optimizer.jsonl" \
    --sentinel "$d/missing-sentinel" \
    --now "2026-05-06T00:00:00Z" > "$d/stdout.json"

  python3 - "$d/stdout.json" "$d/leaf-optimizer.jsonl" "$d/requests.jsonl" <<'PY' || fail "missing sentinel should skip mutation"
import json
import os
import sys

summary = json.load(open(sys.argv[1], encoding="utf-8"))
assert summary["enabled"] is False
assert summary["dry_run"] is True
assert summary["reason"] == "sentinel_missing"
assert "skipped: sentinel missing" in summary["message"]
logged = [json.loads(line) for line in open(sys.argv[2], encoding="utf-8") if line.strip()]
assert logged[-1]["reason"] == "sentinel_missing"
requests = [json.loads(line) for line in open(sys.argv[3], encoding="utf-8") if line.strip()] if os.path.exists(sys.argv[3]) else []
assert not any(req["method"] == "PUT" for req in requests)
PY
  stop_servers
  echo "  PASS: sentinel absent skips mutation"
}

# ---------------------------------------------------------------------------
# Test 3: live optimizer PUTs only manual selectors when sentinel is present.
# ---------------------------------------------------------------------------
{
  d="$TMPROOT/live"
  mkdir -p "$d"
  touch "$d/sentinel"
  cat > "$d/state.json" <<'EOF'
{"version":2,"last_change_at":{},"consecutive_wins":{},"score_history":{}}
EOF
  start_fake_mihomo "$d"

  MIHOMO_API_BASE="http://127.0.0.1:$(cat "$d/port")" \
  MIHOMO_API_SECRET="test-secret" \
  MIHOMO_OPENAI_GROUP="🤖 OpenAI" \
  python3 "$OPTIMIZER" \
    --decision-log "$d/leaf-optimizer.jsonl" \
    --state-file "$d/state.json" \
    --sentinel "$d/sentinel" \
    --now "2026-05-06T00:00:00Z" > "$d/stdout.json"

  python3 - "$d/stdout.json" "$d/requests.jsonl" <<'PY' || fail "live optimizer mutation boundary invalid"
import json
import sys

summary = json.load(open(sys.argv[1], encoding="utf-8"))
assert summary["dry_run"] is False
assert summary["changed"] == 2
requests = [json.loads(line) for line in open(sys.argv[2], encoding="utf-8") if line.strip()]
puts = [req for req in requests if req["method"] == "PUT"]
assert {req["path"] for req in puts} == {"/proxies/♻️ 手动切换1", "/proxies/♻️ 手动切换2"}
assert all(req["path"] != "/proxies/🤖 OpenAI" for req in puts)
assert {req["body"]["name"] for req in puts} == {"5台湾-联通/移动(AnyTLS) [AP1]", "🇸🇬15新加坡-专线(AnyTLS) [AP1]"}
PY
  stop_servers
  echo "  PASS: live optimizer mutates only manual selectors"
}

# ---------------------------------------------------------------------------
# Test 4: broad regex cannot mutate the OpenAI parent group.
# ---------------------------------------------------------------------------
{
  d="$TMPROOT/scope"
  mkdir -p "$d"
  touch "$d/sentinel"
  start_fake_mihomo "$d"

  set +e
  MIHOMO_API_BASE="http://127.0.0.1:$(cat "$d/port")" \
  MIHOMO_API_SECRET="test-secret" \
  MIHOMO_OPENAI_GROUP="🤖 OpenAI" \
  python3 "$OPTIMIZER" \
    --decision-log "$d/leaf-optimizer.jsonl" \
    --state-file "$d/state.json" \
    --sentinel "$d/sentinel" \
    --candidate-regex ".*" \
    --now "2026-05-06T00:00:00Z" > "$d/stdout.json"
  rc=$?
  set -e
  [[ "$rc" -eq 2 ]] || fail "scope violation should exit 2, got $rc"

  python3 - "$d/stdout.json" "$d/requests.jsonl" <<'PY' || fail "scope violation output invalid"
import json
import os
import sys

summary = json.load(open(sys.argv[1], encoding="utf-8"))
assert summary["reason"] == "scope_violation"
assert summary["scope_violation"] == "group_matches_candidate_regex"
requests = [json.loads(line) for line in open(sys.argv[2], encoding="utf-8") if line.strip()] if os.path.exists(sys.argv[2]) else []
assert not any(req["method"] == "PUT" and req["path"] == "/proxies/🤖 OpenAI" for req in requests)
assert not any(req["method"] == "PUT" for req in requests)
PY
  stop_servers
  echo "  PASS: broad regex cannot mutate parent group"
}

# ---------------------------------------------------------------------------
# Test 5: HK exclusion catches common HK spelling variants.
# ---------------------------------------------------------------------------
{
  python3 - "$OPTIMIZER" <<'PY' || fail "HK variant matching invalid"
import importlib.util
import sys

spec = importlib.util.spec_from_file_location("optimize_leaves", sys.argv[1])
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
for name in ["Hong-Kong 01", "[HK] fast", "(HK) line", "HK-01", "🇭🇰2香港"]:
    assert mod.is_hk_leaf(name), name
for name in ["Thailand HKBN unrelated", "Tokyo line", "Singapore"]:
    assert not mod.is_hk_leaf(name), name
PY
  echo "  PASS: HK variants excluded"
}

# ---------------------------------------------------------------------------
# Test 5b: leaf scoring weights latency strongly and does not cap slow nodes.
# ---------------------------------------------------------------------------
{
  python3 - "$OPTIMIZER" <<'PY' || fail "leaf scoring formula invalid"
import importlib.util
import sys

spec = importlib.util.spec_from_file_location("optimize_leaves", sys.argv[1])
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

fast = mod.leaf_score(1.0, 100)
slow = mod.leaf_score(1.0, 1000)
assert fast > slow, (fast, slow)
assert fast - slow >= 0.10, (fast, slow)

very_slow = mod.leaf_score(1.0, 2500)
glacial = mod.leaf_score(1.0, 10000)
assert glacial < very_slow, (very_slow, glacial)

asia = mod.leaf_score(1.0, 205)
west = mod.leaf_score(1.0, 712)
assert asia > west, (asia, west)
assert asia - west >= 0.10, (asia, west)

unreliable_fast = mod.leaf_score(0.5, 50)
reliable_medium = mod.leaf_score(1.0, 300)
assert unreliable_fast < reliable_medium, (unreliable_fast, reliable_medium)

partial_fast = {"success_rate": 0.8, "score": mod.leaf_score(0.8, 100), "avg_delay_ms": 100}
full_medium = {"success_rate": 1.0, "score": mod.leaf_score(1.0, 300), "avg_delay_ms": 300}
assert mod.leaf_rank_key(full_medium) > mod.leaf_rank_key(partial_fast)

same_success_fast = {"success_rate": 1.0, "score": mod.leaf_score(1.0, 100), "avg_delay_ms": 100}
same_success_slow = {"success_rate": 1.0, "score": mod.leaf_score(1.0, 300), "avg_delay_ms": 300}
assert mod.leaf_rank_key(same_success_fast) > mod.leaf_rank_key(same_success_slow)

full_slow = {"success_rate": 1.0, "score": mod.leaf_score(1.0, 2000), "avg_delay_ms": 2000}
slightly_partial_fast = {"success_rate": 0.9, "score": mod.leaf_score(0.9, 100), "avg_delay_ms": 100}
assert mod.leaf_rank_key(slightly_partial_fast) > mod.leaf_rank_key(full_slow)

malformed_delay = {"success_rate": 1.0, "score": mod.leaf_score(1.0, 100), "avg_delay_ms": "bad"}
assert mod.leaf_rank_key(same_success_fast) > mod.leaf_rank_key(malformed_delay)

assert mod.leaf_score(1.0, -10) == 1.0
assert mod.leaf_score(0.5, None) == 0.5
PY
  echo "  PASS: latency dominates tied-success leaf scoring"
}

# ---------------------------------------------------------------------------
# Test 6: stable but better leaves need two consecutive wins before PUT.
# ---------------------------------------------------------------------------
{
  d="$TMPROOT/consecutive"
  mkdir -p "$d"
  touch "$d/sentinel"
  cat > "$d/state.json" <<'EOF'
{"version":2,"last_change_at":{},"consecutive_wins":{},"score_history":{}}
EOF
  start_stable_fake_mihomo "$d" 800 100

  common_env=(
    MIHOMO_API_BASE="http://127.0.0.1:$(cat "$d/port")"
    MIHOMO_API_SECRET="test-secret"
    MIHOMO_OPENAI_GROUP="🤖 OpenAI"
  )
  env "${common_env[@]}" python3 "$OPTIMIZER" \
    --decision-log "$d/leaf-optimizer.jsonl" \
    --state-file "$d/state.json" \
    --sentinel "$d/sentinel" \
    --now "2026-05-06T00:00:00Z" > "$d/stdout1.json"
  env "${common_env[@]}" python3 "$OPTIMIZER" \
    --decision-log "$d/leaf-optimizer.jsonl" \
    --state-file "$d/state.json" \
    --sentinel "$d/sentinel" \
    --now "2026-05-06T06:00:00Z" > "$d/stdout2.json"

  python3 - "$d/stdout1.json" "$d/stdout2.json" "$d/requests.jsonl" <<'PY' || fail "consecutive win behavior invalid"
import json
import sys

first = json.load(open(sys.argv[1], encoding="utf-8"))
second = json.load(open(sys.argv[2], encoding="utf-8"))
assert first["changed"] == 0, first
assert first["assignments"][0]["reason"] == "waiting_for_consecutive_win", first
assert first["assignments"][0]["success_rate_delta"] == 0.0, first
assert second["changed"] == 1, second
requests = [json.loads(line) for line in open(sys.argv[3], encoding="utf-8") if line.strip()]
puts = [req for req in requests if req["method"] == "PUT"]
assert len(puts) == 1, puts
PY
  stop_servers
  echo "  PASS: consecutive wins required"
}

# ---------------------------------------------------------------------------
# Test 7: 12-hour dwell blocks valid-leaf churn.
# ---------------------------------------------------------------------------
{
  d="$TMPROOT/dwell"
  mkdir -p "$d"
  touch "$d/sentinel"
  start_stable_fake_mihomo "$d" 500 100
  cat > "$d/state.json" <<'EOF'
{"version":2,"last_change_at":{"♻️ 手动切换1":"2026-05-06T00:00:00Z"},"consecutive_wins":{"♻️ 手动切换1":{"🇸🇬15新加坡-专线(AnyTLS) [AP1]":2}},"score_history":{}}
EOF

  MIHOMO_API_BASE="http://127.0.0.1:$(cat "$d/port")" \
  MIHOMO_API_SECRET="test-secret" \
  MIHOMO_OPENAI_GROUP="🤖 OpenAI" \
  python3 "$OPTIMIZER" \
    --decision-log "$d/leaf-optimizer.jsonl" \
    --state-file "$d/state.json" \
    --sentinel "$d/sentinel" \
    --now "2026-05-06T06:00:00Z" > "$d/stdout.json"

  python3 - "$d/stdout.json" "$d/requests.jsonl" <<'PY' || fail "dwell behavior invalid"
import json
import sys

summary = json.load(open(sys.argv[1], encoding="utf-8"))
assert summary["changed"] == 0
assert summary["assignments"][0]["reason"] == "dwell_active"
requests = [json.loads(line) for line in open(sys.argv[2], encoding="utf-8") if line.strip()]
assert not any(req["method"] == "PUT" for req in requests)
PY
  stop_servers
  echo "  PASS: dwell blocks churn"
}

# ---------------------------------------------------------------------------
# Test 8: rolling average blocks one-run jitter.
# ---------------------------------------------------------------------------
{
  d="$TMPROOT/rolling"
  mkdir -p "$d"
  touch "$d/sentinel"
  start_stable_fake_mihomo "$d" 300 100
  python3 - "$d/state.json" <<'PY'
import json
import sys

current = "5台湾-联通/移动(AnyTLS) [AP1]"
better = "🇸🇬15新加坡-专线(AnyTLS) [AP1]"
state = {
    "version": 2,
    "last_change_at": {},
    "consecutive_wins": {"♻️ 手动切换1": {better: 2}},
    "score_history": {
        "♻️ 手动切换1": {
            current: [0.7692] * 2,
            better: [0.7407] * 2,
        }
    },
}
with open(sys.argv[1], "w", encoding="utf-8") as f:
    json.dump(state, f, ensure_ascii=False, separators=(",", ":"))
PY

  MIHOMO_API_BASE="http://127.0.0.1:$(cat "$d/port")" \
  MIHOMO_API_SECRET="test-secret" \
  MIHOMO_OPENAI_GROUP="🤖 OpenAI" \
  python3 "$OPTIMIZER" \
    --decision-log "$d/leaf-optimizer.jsonl" \
    --state-file "$d/state.json" \
    --sentinel "$d/sentinel" \
    --now "2026-05-06T06:00:00Z" > "$d/stdout.json"

  python3 - "$d/stdout.json" "$d/requests.jsonl" <<'PY' || fail "rolling average behavior invalid"
import json
import sys

summary = json.load(open(sys.argv[1], encoding="utf-8"))
assert summary["changed"] == 0
assert summary["assignments"][0]["reason"] == "below_min_score_improvement"
assert summary["assignments"][0]["score_delta"] < 0.05
requests = [json.loads(line) for line in open(sys.argv[2], encoding="utf-8") if line.strip()]
assert not any(req["method"] == "PUT" for req in requests)
PY
  stop_servers
  echo "  PASS: rolling average blocks jitter"
}

# ---------------------------------------------------------------------------
# Test 9: required API outage exits nonzero with explicit reason.
# ---------------------------------------------------------------------------
{
  d="$TMPROOT/api-down"
  mkdir -p "$d"
  touch "$d/sentinel"
  set +e
  MIHOMO_API_BASE="http://127.0.0.1:9" \
  MIHOMO_API_SECRET="test-secret" \
  MIHOMO_OPENAI_GROUP="🤖 OpenAI" \
  python3 "$OPTIMIZER" \
    --decision-log "$d/leaf-optimizer.jsonl" \
    --state-file "$d/state.json" \
    --sentinel "$d/sentinel" \
    --now "2026-05-06T00:00:00Z" > "$d/stdout.json"
  rc=$?
  set -e
  [[ "$rc" -eq 2 ]] || fail "API outage should exit 2, got $rc"
  grep -q '"reason":"api_unreachable"' "$d/leaf-optimizer.jsonl" || fail "api_unreachable decision missing"
  echo "  PASS: API outage is explicit"
}

# ---------------------------------------------------------------------------
# Test 10: lock busy exits without network calls or state writes.
# ---------------------------------------------------------------------------
{
  d="$TMPROOT/lock-busy"
  mkdir -p "$d"
  python3 - "$d/lock" <<'PY' &
import fcntl
import sys
import time

with open(sys.argv[1], "a+", encoding="utf-8") as f:
    fcntl.flock(f.fileno(), fcntl.LOCK_EX)
    print("locked", flush=True)
    time.sleep(5)
PY
  lock_pid=$!
  SERVER_PIDS+=("$lock_pid")
  sleep 0.3

  MIHOMO_API_BASE="http://127.0.0.1:9" \
  MIHOMO_API_SECRET="test-secret" \
  MIHOMO_OPENAI_GROUP="🤖 OpenAI" \
  python3 "$OPTIMIZER" \
    --decision-log "$d/leaf-optimizer.jsonl" \
    --state-file "$d/state.json" \
    --lock-file "$d/lock" \
    --sentinel "$d/missing-sentinel" \
    --now "2026-05-06T00:00:00Z" > "$d/stdout.json"

  python3 - "$d/stdout.json" "$d/leaf-optimizer.jsonl" <<'PY' || fail "lock busy output invalid"
import json
import sys

summary = json.load(open(sys.argv[1], encoding="utf-8"))
assert summary["reason"] == "lock_busy"
logged = [json.loads(line) for line in open(sys.argv[2], encoding="utf-8") if line.strip()]
assert logged[-1]["reason"] == "lock_busy"
PY
  kill "$lock_pid" 2>/dev/null || true
  wait "$lock_pid" 2>/dev/null || true
  SERVER_PIDS=("${SERVER_PIDS[@]/$lock_pid}")
  echo "  PASS: lock busy skips run"
}

# ---------------------------------------------------------------------------
# Test 11: missing state initializes safely without live PUT.
# ---------------------------------------------------------------------------
{
  d="$TMPROOT/state-missing"
  mkdir -p "$d"
  touch "$d/sentinel"
  start_fake_mihomo "$d"

  MIHOMO_API_BASE="http://127.0.0.1:$(cat "$d/port")" \
  MIHOMO_API_SECRET="test-secret" \
  MIHOMO_OPENAI_GROUP="🤖 OpenAI" \
  python3 "$OPTIMIZER" \
    --decision-log "$d/leaf-optimizer.jsonl" \
    --state-file "$d/state.json" \
    --sentinel "$d/sentinel" \
    --now "2026-05-06T00:00:00Z" > "$d/stdout.json"

  python3 - "$d/stdout.json" "$d/state.json" "$d/requests.jsonl" <<'PY' || fail "missing state behavior invalid"
import json
import sys

summary = json.load(open(sys.argv[1], encoding="utf-8"))
state = json.load(open(sys.argv[2], encoding="utf-8"))
assert summary["changed"] == 0
assert {item["reason"] for item in summary["assignments"]} == {"state_missing"}
assert state["version"] == 2
requests = [json.loads(line) for line in open(sys.argv[3], encoding="utf-8") if line.strip()]
assert not any(req["method"] == "PUT" for req in requests)
PY
  stop_servers
  echo "  PASS: missing state initializes safely"
}

# ---------------------------------------------------------------------------
# Test 12: corrupt state enters safe mode and does not PUT.
# ---------------------------------------------------------------------------
{
  d="$TMPROOT/state-invalid"
  mkdir -p "$d"
  touch "$d/sentinel"
  printf '{bad json\n' > "$d/state.json"
  start_fake_mihomo "$d"

  MIHOMO_API_BASE="http://127.0.0.1:$(cat "$d/port")" \
  MIHOMO_API_SECRET="test-secret" \
  MIHOMO_OPENAI_GROUP="🤖 OpenAI" \
  python3 "$OPTIMIZER" \
    --decision-log "$d/leaf-optimizer.jsonl" \
    --state-file "$d/state.json" \
    --sentinel "$d/sentinel" \
    --now "2026-05-06T00:00:00Z" > "$d/stdout.json"

  python3 - "$d/stdout.json" "$d/state.json" "$d/requests.jsonl" <<'PY' || fail "invalid state behavior invalid"
import json
import sys

summary = json.load(open(sys.argv[1], encoding="utf-8"))
state = json.load(open(sys.argv[2], encoding="utf-8"))
assert summary["changed"] == 0
assert {item["reason"] for item in summary["assignments"]} == {"state_invalid"}
assert state["version"] == 2
requests = [json.loads(line) for line in open(sys.argv[3], encoding="utf-8") if line.strip()]
assert not any(req["method"] == "PUT" for req in requests)
PY
  stop_servers
  echo "  PASS: invalid state safely reinitializes"
}

# ---------------------------------------------------------------------------
# Test 12b: state version mismatch logs the score-history reset.
# ---------------------------------------------------------------------------
{
  d="$TMPROOT/state-version-mismatch"
  mkdir -p "$d"
  touch "$d/sentinel"
  cat > "$d/state.json" <<'EOF'
{"version":1,"last_change_at":{},"consecutive_wins":{"♻️ 手动切换1":{"old-leaf":2}},"score_history":{"♻️ 手动切换1":{"old-leaf":[0.9,0.9]}}}
EOF
  start_fake_mihomo "$d"

  MIHOMO_API_BASE="http://127.0.0.1:$(cat "$d/port")" \
  MIHOMO_API_SECRET="test-secret" \
  MIHOMO_OPENAI_GROUP="🤖 OpenAI" \
  python3 "$OPTIMIZER" \
    --decision-log "$d/leaf-optimizer.jsonl" \
    --state-file "$d/state.json" \
    --sentinel "$d/sentinel" \
    --now "2026-05-06T00:00:00Z" > "$d/stdout.json" 2> "$d/stderr.txt"

  python3 - "$d/stdout.json" "$d/state.json" "$d/requests.jsonl" "$d/stderr.txt" <<'PY' || fail "state version warning invalid"
import json
import sys

summary = json.load(open(sys.argv[1], encoding="utf-8"))
state = json.load(open(sys.argv[2], encoding="utf-8"))
stderr = open(sys.argv[4], encoding="utf-8").read()
assert summary["changed"] == 0
assert {item["reason"] for item in summary["assignments"]} == {"state_version_migrated"}
assert state["version"] == 2
assert "old-leaf" not in json.dumps(state, ensure_ascii=False)
assert "state version mismatch" in stderr, stderr
assert "discarding rolling-score history" in stderr, stderr
requests = [json.loads(line) for line in open(sys.argv[3], encoding="utf-8") if line.strip()]
assert not any(req["method"] == "PUT" for req in requests)
PY
  stop_servers
  echo "  PASS: state version reset is operator-visible"
}

# ---------------------------------------------------------------------------
# Test 13: stale selector read blocks TOCTOU mutation.
# ---------------------------------------------------------------------------
{
  d="$TMPROOT/stale-selector"
  mkdir -p "$d"
  touch "$d/sentinel"
  cat > "$d/state.json" <<'EOF'
{"version":2,"last_change_at":{},"consecutive_wins":{"♻️ 手动切换1":{"🇸🇬15新加坡-专线(AnyTLS) [AP1]":2}},"score_history":{}}
EOF
  start_stale_fake_mihomo "$d"

  MIHOMO_API_BASE="http://127.0.0.1:$(cat "$d/port")" \
  MIHOMO_API_SECRET="test-secret" \
  MIHOMO_OPENAI_GROUP="🤖 OpenAI" \
  python3 "$OPTIMIZER" \
    --decision-log "$d/leaf-optimizer.jsonl" \
    --state-file "$d/state.json" \
    --sentinel "$d/sentinel" \
    --now "2026-05-06T00:00:00Z" > "$d/stdout.json"

  python3 - "$d/stdout.json" "$d/requests.jsonl" <<'PY' || fail "stale selector behavior invalid"
import json
import sys

summary = json.load(open(sys.argv[1], encoding="utf-8"))
assert summary["changed"] == 0
assert summary["assignments"][0]["reason"] == "stale_selector"
requests = [json.loads(line) for line in open(sys.argv[2], encoding="utf-8") if line.strip()]
assert not any(req["method"] == "PUT" for req in requests)
PY
  stop_servers
  echo "  PASS: stale selector blocks PUT"
}

# ---------------------------------------------------------------------------
# Test 14: transient PUT failure retries and verifies the selected leaf.
# ---------------------------------------------------------------------------
{
  d="$TMPROOT/put-retry"
  mkdir -p "$d"
  touch "$d/sentinel"
  cat > "$d/state.json" <<'EOF'
{"version":2,"last_change_at":{},"consecutive_wins":{"♻️ 手动切换1":{"🇸🇬15新加坡-专线(AnyTLS) [AP1]":2}},"score_history":{}}
EOF
  start_put_behavior_fake_mihomo "$d" "retry"

  MIHOMO_API_BASE="http://127.0.0.1:$(cat "$d/port")" \
  MIHOMO_API_SECRET="test-secret" \
  MIHOMO_OPENAI_GROUP="🤖 OpenAI" \
  python3 "$OPTIMIZER" \
    --decision-log "$d/leaf-optimizer.jsonl" \
    --state-file "$d/state.json" \
    --sentinel "$d/sentinel" \
    --now "2026-05-06T00:00:00Z" > "$d/stdout.json"

  python3 - "$d/stdout.json" "$d/requests.jsonl" <<'PY' || fail "PUT retry behavior invalid"
import json
import sys

summary = json.load(open(sys.argv[1], encoding="utf-8"))
assert summary["changed"] == 1
assert summary["assignments"][0]["changed"] is True
assert summary["assignments"][0]["http_status"] == 204
requests = [json.loads(line) for line in open(sys.argv[2], encoding="utf-8") if line.strip()]
puts = [req for req in requests if req["method"] == "PUT"]
assert len(puts) == 2, puts
PY
  stop_servers
  echo "  PASS: PUT retry verifies mutation"
}

# ---------------------------------------------------------------------------
# Test 15: 2xx PUT without selector movement is rejected.
# ---------------------------------------------------------------------------
{
  d="$TMPROOT/put-verify"
  mkdir -p "$d"
  touch "$d/sentinel"
  cat > "$d/state.json" <<'EOF'
{"version":2,"last_change_at":{},"consecutive_wins":{"♻️ 手动切换1":{"🇸🇬15新加坡-专线(AnyTLS) [AP1]":2}},"score_history":{}}
EOF
  start_put_behavior_fake_mihomo "$d" "nochange"

  MIHOMO_API_BASE="http://127.0.0.1:$(cat "$d/port")" \
  MIHOMO_API_SECRET="test-secret" \
  MIHOMO_OPENAI_GROUP="🤖 OpenAI" \
  python3 "$OPTIMIZER" \
    --decision-log "$d/leaf-optimizer.jsonl" \
    --state-file "$d/state.json" \
    --sentinel "$d/sentinel" \
    --now "2026-05-06T00:00:00Z" > "$d/stdout.json"

  python3 - "$d/stdout.json" "$d/requests.jsonl" <<'PY' || fail "PUT verification behavior invalid"
import json
import sys

summary = json.load(open(sys.argv[1], encoding="utf-8"))
assert summary["changed"] == 0
assert summary["assignments"][0]["reason"] == "put_verify_failed"
requests = [json.loads(line) for line in open(sys.argv[2], encoding="utf-8") if line.strip()]
puts = [req for req in requests if req["method"] == "PUT"]
assert len(puts) == 1, puts
PY
  stop_servers
  echo "  PASS: PUT verification rejects false success"
}

# ---------------------------------------------------------------------------
# Test 16: profile can allow HK leaves without marking HK current leaf invalid.
# ---------------------------------------------------------------------------
{
  d="$TMPROOT/profile-allow-hk"
  mkdir -p "$d"
  start_fake_mihomo "$d"
  cat > "$d/state.json" <<'EOF'
{"version":2,"last_change_at":{},"consecutive_wins":{},"score_history":{}}
EOF
  cat > "$d/profiles.json" <<'EOF'
{"profiles":{"allow-hk":{"enabled":true,"group_env_var":"MIHOMO_OPENAI_GROUP","exclude_hk":false}}}
EOF

  MIHOMO_API_BASE="http://127.0.0.1:$(cat "$d/port")" \
  MIHOMO_API_SECRET="test-secret" \
  MIHOMO_OPENAI_GROUP="🤖 OpenAI" \
  python3 "$OPTIMIZER" \
    --profiles-file "$d/profiles.json" \
    --profile allow-hk \
    --decision-log "$d/leaf-optimizer.jsonl" \
    --state-file "$d/state.json" \
    --now "2026-05-06T00:00:00Z" \
    --dry-run > "$d/stdout.json"

  python3 - "$d/stdout.json" <<'PY' || fail "profile allow HK behavior invalid"
import json
import sys

summary = json.load(open(sys.argv[1], encoding="utf-8"))
assignments = {item["selector"]: item for item in summary["assignments"]}
first = assignments["♻️ 手动切换1"]
assert first["target_leaf"] == "🇭🇰2香港-专线(AnyTLS) [AP1]", first
assert first["reason"] == "already_best", first
assert first["current_invalid"] is False, first
assert first["would_change"] is False, first
PY
  stop_servers
  echo "  PASS: profile can include HK leaves"
}

# ---------------------------------------------------------------------------
# Test 17: named group env var is required and missing var exits clearly.
# ---------------------------------------------------------------------------
{
  d="$TMPROOT/profile-missing-env"
  mkdir -p "$d"
  start_fake_mihomo "$d"
  cat > "$d/profiles.json" <<'EOF'
{"profiles":{"missing-env":{"enabled":true,"group_env_var":"MIHOMO_GENERAL_GROUP"}}}
EOF

  set +e
  MIHOMO_API_BASE="http://127.0.0.1:$(cat "$d/port")" \
  MIHOMO_API_SECRET="test-secret" \
  python3 "$OPTIMIZER" \
    --profiles-file "$d/profiles.json" \
    --profile missing-env \
    --decision-log "$d/leaf-optimizer.jsonl" \
    --now "2026-05-06T00:00:00Z" > "$d/stdout.json"
  rc=$?
  set -e
  [[ "$rc" -eq 2 ]] || fail "missing group env should exit 2, got $rc"

  python3 - "$d/stdout.json" "$d/requests.jsonl" <<'PY' || fail "missing group env output invalid"
import json
import os
import sys

summary = json.load(open(sys.argv[1], encoding="utf-8"))
assert summary["reason"] == "env_missing", summary
assert summary["missing_env_var"] == "MIHOMO_GENERAL_GROUP", summary
requests = [json.loads(line) for line in open(sys.argv[2], encoding="utf-8") if line.strip()] if os.path.exists(sys.argv[2]) else []
assert not any(req["method"] == "PUT" for req in requests)
PY
  stop_servers
  echo "  PASS: missing profile group env fails closed"
}

# ---------------------------------------------------------------------------
# Test 18: profile excludes synthetic/meta leaf candidates by regex.
# ---------------------------------------------------------------------------
{
  d="$TMPROOT/profile-exclude-meta"
  mkdir -p "$d"
  start_meta_fake_mihomo "$d"
  cat > "$d/profiles.json" <<'EOF'
{"profiles":{"ai":{"enabled":true,"group_env_var":"MIHOMO_OPENAI_GROUP","exclude_leaf_regex":"^🌏自动最优线路"}}}
EOF

  MIHOMO_API_BASE="http://127.0.0.1:$(cat "$d/port")" \
  MIHOMO_API_SECRET="test-secret" \
  MIHOMO_OPENAI_GROUP="🤖 OpenAI" \
  python3 "$OPTIMIZER" \
    --profiles-file "$d/profiles.json" \
    --profile ai \
    --decision-log "$d/leaf-optimizer.jsonl" \
    --now "2026-05-06T00:00:00Z" \
    --dry-run > "$d/stdout.json"

  python3 - "$d/stdout.json" <<'PY' || fail "exclude meta profile behavior invalid"
import json
import sys

summary = json.load(open(sys.argv[1], encoding="utf-8"))
assignment = summary["assignments"][0]
assert assignment["target_leaf"] == "🇸🇬15新加坡-专线(AnyTLS) [AP1]", assignment
assert "🌏自动最优线路(AnyTLS)-网址: www.dg6.me [AP1]" not in summary["eligible_leaf_names"], summary
excluded = summary["excluded_leafs"]
assert excluded["🌏自动最优线路(AnyTLS)-网址: www.dg6.me [AP1]"] == "excluded_leaf_regex", excluded
PY
  stop_servers
  echo "  PASS: profile excludes meta leaf candidates"
}

# ---------------------------------------------------------------------------
# Test 19: sub-threshold dry-run target is reported as non-actionable.
# ---------------------------------------------------------------------------
{
  d="$TMPROOT/below-threshold"
  mkdir -p "$d"
  touch "$d/sentinel"
  cat > "$d/state.json" <<'EOF'
{"version":2,"last_change_at":{},"consecutive_wins":{"♻️ 手动切换1":{"🇸🇬15新加坡-专线(AnyTLS) [AP1]":2}},"score_history":{"♻️ 手动切换1":{"5台湾-联通/移动(AnyTLS) [AP1]":[0.8,0.8],"🇸🇬15新加坡-专线(AnyTLS) [AP1]":[0.81,0.81]}}}
EOF
  start_stable_fake_mihomo "$d" 250 235

  MIHOMO_API_BASE="http://127.0.0.1:$(cat "$d/port")" \
  MIHOMO_API_SECRET="test-secret" \
  MIHOMO_OPENAI_GROUP="🤖 OpenAI" \
  python3 "$OPTIMIZER" \
    --decision-log "$d/leaf-optimizer.jsonl" \
    --state-file "$d/state.json" \
    --sentinel "$d/sentinel" \
    --now "2026-05-06T00:00:00Z" > "$d/stdout.json"

  python3 - "$d/stdout.json" "$d/requests.jsonl" <<'PY' || fail "below threshold behavior invalid"
import json
import sys

summary = json.load(open(sys.argv[1], encoding="utf-8"))
assignment = summary["assignments"][0]
assert assignment["target_leaf"] == "🇸🇬15新加坡-专线(AnyTLS) [AP1]", assignment
assert 0 <= assignment["score_delta"] < 0.05, assignment
assert assignment["reason"] == "below_min_score_improvement", assignment
assert assignment["would_change"] is False, assignment
assert "below_min_score_improvement" in assignment["change_blockers"], assignment
requests = [json.loads(line) for line in open(sys.argv[2], encoding="utf-8") if line.strip()]
assert not any(req["method"] == "PUT" for req in requests)
PY
  stop_servers
  echo "  PASS: below-threshold target is non-actionable"
}

# ---------------------------------------------------------------------------
# Test 20: all-enabled profile mode preserves sentinel-missing skip.
# ---------------------------------------------------------------------------
{
  d="$TMPROOT/profile-sentinel-missing"
  mkdir -p "$d"
  start_fake_mihomo "$d"
  cat > "$d/profiles.json" <<EOF
{"profiles":{"ai":{"enabled":true,"group_env_var":"MIHOMO_OPENAI_GROUP","sentinel":"$d/missing-sentinel","decision_log":"$d/profile-leaf-optimizer.jsonl"}}}
EOF

  MIHOMO_API_BASE="http://127.0.0.1:$(cat "$d/port")" \
  MIHOMO_API_SECRET="test-secret" \
  MIHOMO_OPENAI_GROUP="🤖 OpenAI" \
  python3 "$OPTIMIZER" \
    --profiles-file "$d/profiles.json" \
    --all-enabled-profiles \
    --now "2026-05-06T00:00:00Z" > "$d/stdout.json"

  python3 - "$d/stdout.json" "$d/profile-leaf-optimizer.jsonl" <<'PY' || fail "profile sentinel missing behavior invalid"
import json
import sys

summary = json.load(open(sys.argv[1], encoding="utf-8"))
assert summary["profile"] == "ai", summary
assert summary["reason"] == "sentinel_missing", summary
assert summary["enabled"] is False, summary
logged = [json.loads(line) for line in open(sys.argv[2], encoding="utf-8") if line.strip()]
assert logged[-1]["profile"] == "ai"
assert logged[-1]["reason"] == "sentinel_missing"
PY
  stop_servers
  echo "  PASS: profile mode preserves sentinel-missing skip"
}

# ---------------------------------------------------------------------------
# Test 21: two enabled profiles sharing selectors fail closed before mutation.
# ---------------------------------------------------------------------------
{
  d="$TMPROOT/profile-overlap"
  mkdir -p "$d"
  start_fake_mihomo "$d"
  cat > "$d/profiles.json" <<EOF
{"profiles":{
  "ai":{"enabled":true,"group_env_var":"MIHOMO_OPENAI_GROUP","sentinel":"$d/sentinel-ai","decision_log":"$d/ai.jsonl"},
  "general":{"enabled":true,"group_env_var":"MIHOMO_GENERAL_GROUP","sentinel":"$d/sentinel-general","decision_log":"$d/general.jsonl"}
}}
EOF
  touch "$d/sentinel-ai" "$d/sentinel-general"

  set +e
  MIHOMO_API_BASE="http://127.0.0.1:$(cat "$d/port")" \
  MIHOMO_API_SECRET="test-secret" \
  MIHOMO_OPENAI_GROUP="🤖 OpenAI" \
  MIHOMO_GENERAL_GROUP="🤖 OpenAI" \
  python3 "$OPTIMIZER" \
    --profiles-file "$d/profiles.json" \
    --all-enabled-profiles \
    --now "2026-05-06T00:00:00Z" > "$d/stdout.json"
  rc=$?
  set -e
  [[ "$rc" -eq 2 ]] || fail "overlapping profiles should exit 2, got $rc"

  python3 - "$d/stdout.json" "$d/requests.jsonl" <<'PY' || fail "profile overlap output invalid"
import json
import sys

summary = json.load(open(sys.argv[1], encoding="utf-8"))
assert summary["reason"] == "profile_scope_conflict", summary
assert summary["changed"] == 0, summary
assert {"ai", "general"} == set(summary["profiles"]), summary
requests = [json.loads(line) for line in open(sys.argv[2], encoding="utf-8") if line.strip()]
assert not any(req["method"] == "PUT" for req in requests)
PY
  stop_servers
  echo "  PASS: overlapping profiles fail closed"
}

# ---------------------------------------------------------------------------
# Test 22: direct leaf groups report no matching manual selectors and do not PUT.
# ---------------------------------------------------------------------------
{
  d="$TMPROOT/direct-leaf-group"
  mkdir -p "$d"
  start_direct_leaf_group_fake_mihomo "$d"
  cat > "$d/profiles.json" <<'EOF'
{"profiles":{"latency":{"enabled":true,"group_env_var":"MIHOMO_LATENCY_GROUP","exclude_hk":false}}}
EOF

  MIHOMO_API_BASE="http://127.0.0.1:$(cat "$d/port")" \
  MIHOMO_API_SECRET="test-secret" \
  MIHOMO_LATENCY_GROUP="Ⓜ️ 延迟最低" \
  python3 "$OPTIMIZER" \
    --profiles-file "$d/profiles.json" \
    --profile latency \
    --decision-log "$d/latency.jsonl" \
    --now "2026-05-06T00:00:00Z" \
    --dry-run > "$d/stdout.json"

  python3 - "$d/stdout.json" "$d/requests.jsonl" <<'PY' || fail "direct leaf group output invalid"
import json
import sys

summary = json.load(open(sys.argv[1], encoding="utf-8"))
assert summary["reason"] == "no_matching_selectors", summary
assert summary["selectors"] == [], summary
assert summary["changed"] == 0, summary
requests = [json.loads(line) for line in open(sys.argv[2], encoding="utf-8") if line.strip()]
assert not any(req["method"] == "PUT" for req in requests)
PY
  stop_servers
  echo "  PASS: direct leaf group is explicit no-op"
}
