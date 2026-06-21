#!/usr/bin/env bash
# tests/router-watchdog-private-vpn.test.sh
# Private VPS route watchdog tests: HK primary, TW failover, incident traces,
# and route-aware digest output.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC_DIR="$REPO_ROOT/scripts/router-watchdog"
WATCHDOG="$SRC_DIR/bin/private-vpn-watchdog.py"
DIGEST="$SRC_DIR/bin/digest.py"

TMPROOT="$(mktemp -d)"
SERVER_PIDS=()
trap 'for pid in "${SERVER_PIDS[@]:-}"; do kill "$pid" 2>/dev/null || true; wait "$pid" 2>/dev/null || true; done; rm -rf "$TMPROOT"' EXIT

fail() { echo "FAIL: $*" >&2; exit 1; }

start_mihomo() {
  local d="$1" scenario="$2"
  local port_file="$d/port"
  local request_log="$d/requests.jsonl"
  rm -f "$port_file"
  python3 -u - "$port_file" "$request_log" "$scenario" <<'PY' &
import json
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

port_file, request_log, scenario_name = sys.argv[1], sys.argv[2], sys.argv[3]

route = "🎯 总模式"
private = "🔒 私有线路"
hk = "🇭🇰 K2B-VPS-HK"
tw = "🇹🇼 K2B-VPS-TW"
kl = "🇲🇾 K2B-VPS-KL"
direct = "DIRECT"

scenarios = {
    "hk_fail_tw_ok": {
        "now": tw,
        hk: None,
        tw: 104,
        kl: 291,
        direct: 15,
    },
    "all_private_fail_direct_ok": {
        "now": hk,
        hk: None,
        tw: None,
        kl: None,
        direct: 15,
    },
    "all_ok": {
        "now": hk,
        hk: 103,
        tw: 112,
        kl: 291,
        direct: 15,
    },
    "hk_partial_tw_ok": {
        "now": tw,
        hk: {"gstatic": 103, "apple": None},
        tw: 104,
        kl: 291,
        direct: 15,
    },
}

scenario = scenarios[scenario_name]

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

    def record(self):
        parsed = urllib.parse.urlparse(self.path)
        path = urllib.parse.unquote(parsed.path)
        with open(request_log, "a", encoding="utf-8") as f:
            f.write(json.dumps({"method": self.command, "path": path, "query": parsed.query}, ensure_ascii=False) + "\n")
        return path

    def do_GET(self):
        path = self.record()
        if path == "/version":
            self.write_json(200, {"version": "fake-mihomo"})
            return
        if path == "/proxies":
            self.write_json(200, {"proxies": {
                route: {"name": route, "type": "Selector", "now": private, "all": [private, hk, "DIRECT"], "alive": True},
                private: {"name": private, "type": "Fallback", "now": scenario["now"], "all": [hk, tw, kl], "alive": True},
                hk: {"name": hk, "type": "Hysteria2", "alive": scenario[hk] is not None, "history": []},
                tw: {"name": tw, "type": "Hysteria2", "alive": scenario[tw] is not None, "history": []},
                kl: {"name": kl, "type": "Hysteria2", "alive": scenario[kl] is not None, "history": []},
                direct: {"name": direct, "type": "Direct", "alive": scenario[direct] is not None, "history": []},
            }})
            return
        if path.startswith("/proxies/") and path.endswith("/delay"):
            name = path.removeprefix("/proxies/").removesuffix("/delay")
            delay = scenario.get(name)
            if isinstance(delay, dict):
                target = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query).get("url", [""])[0]
                delay = delay.get("apple" if "apple.com" in target else "gstatic")
            if delay is None:
                self.write_json(504, {"message": "timeout"})
            else:
                self.write_json(200, {"delay": delay})
            return
        if path.startswith("/proxies/"):
            name = path.removeprefix("/proxies/")
            if name == route:
                self.write_json(200, {"name": route, "type": "Selector", "now": private, "all": [private, hk, "DIRECT"], "alive": True})
            elif name == private:
                self.write_json(200, {"name": private, "type": "Fallback", "now": scenario["now"], "all": [hk, tw, kl], "alive": True})
            elif name in {hk, tw, kl, direct}:
                self.write_json(200, {"name": name, "type": "Hysteria2" if name != direct else "Direct", "alive": scenario.get(name) is not None})
            else:
                self.write_json(404, {"message": "not found"})
            return
        self.write_json(404, {"message": "not found"})

server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
with open(port_file, "w", encoding="utf-8") as f:
    f.write(str(server.server_port))
server.serve_forever()
PY
  SERVER_PIDS+=("$!")
  for _ in {1..50}; do [[ -s "$port_file" ]] && break; sleep 0.1; done
  [[ -s "$port_file" ]] || fail "fake Mihomo server did not start"
}

write_fake_commands() {
  local d="$1"
  mkdir -p "$d/fakebin"
  cat > "$d/fakebin/aws" <<EOF
#!/usr/bin/env bash
printf 'aws %s\n' "\$*" >> "$d/expensive-commands.log"
cat <<'JSON'
{"state":{"code":16,"name":"running"}}
JSON
EOF
  cat > "$d/fakebin/ssh" <<EOF
#!/usr/bin/env bash
printf 'ssh %s\n' "\$*" >> "$d/expensive-commands.log"
if printf '%s\n' "\$*" | grep -q 'router'; then
  printf 'router-log: no mihomo restart\n'
else
  cat <<'TEXT'
hysteria_active=active
hysteria_enabled=enabled
udp443=UNCONN 0 0 *:443 *:* users:(("hysteria",pid=123,fd=3))
TEXT
fi
EOF
  cat > "$d/fakebin/ping" <<'EOF'
#!/usr/bin/env bash
host="${@: -1}"
case "$host" in
  192.168.9.1)
    printf '64 bytes from %s: icmp_seq=0 ttl=64 time=2.4 ms\n' "$host"
    exit 0
    ;;
  192.168.1.1)
    printf '64 bytes from %s: icmp_seq=0 ttl=63 time=3.8 ms\n' "$host"
    exit 0
    ;;
  *)
    printf 'ping: request timeout for %s\n' "$host" >&2
    exit 2
    ;;
esac
EOF
  cat > "$d/fakebin/tailscale" <<'EOF'
#!/usr/bin/env bash
[[ -n "${K2B_FAKE_TAILSCALE_LOG:-}" ]] && printf 'tailscale %s\n' "$*" >> "$K2B_FAKE_TAILSCALE_LOG"
case "${1:-}" in
  status)
    cat <<'JSON'
{"BackendState":"Running","Self":{"Online":true},"CurrentTailnet":{"Name":"test-tailnet"}}
JSON
    ;;
  netcheck)
    udp="${K2B_FAKE_TAILSCALE_UDP:-true}"
    cat <<JSON
{"Report":{"UDP":$udp,"IPv4":true,"MappingVariesByDestIP":true,"PortMapping":"UPnP","NearestDERP":"sin"}}
JSON
    ;;
  *)
    exit 2
    ;;
esac
EOF
  chmod +x "$d/fakebin/aws" "$d/fakebin/ssh" "$d/fakebin/ping" "$d/fakebin/tailscale"
}

run_watchdog() {
  local d="$1" ts="$2" base="$3"
  local tcp_target="${base#http://}"
  tcp_target="${tcp_target%%/*}"
  local tcp_targets="${K2B_TEST_TCP_TARGETS:-$tcp_target}"
  PATH="$d/fakebin:$PATH" \
  MIHOMO_API_BASE="$base" \
  MIHOMO_API_SECRET="test-secret" \
  K2B_PRIVATE_VPN_FAIL_THRESHOLD=2 \
  K2B_PRIVATE_VPN_RECOVERY_THRESHOLD=3 \
  K2B_PRIVATE_VPN_AWS_PROFILE="test-profile" \
  K2B_PRIVATE_VPN_AWS_REGION="ap-east-1" \
  K2B_PRIVATE_VPN_AWS_INSTANCE="Ubuntu-1" \
  K2B_PRIVATE_VPN_INCIDENT_KEEP="${K2B_PRIVATE_VPN_INCIDENT_KEEP:-50}" \
  K2B_PRIVATE_VPN_INCIDENT_MAX_AGE_DAYS="${K2B_PRIVATE_VPN_INCIDENT_MAX_AGE_DAYS:-30}" \
  K2B_PRIVATE_VPN_HK_SSH_TARGET="ubuntu@127.0.0.1" \
  K2B_PRIVATE_VPN_ROUTER_LAN_IP="192.168.9.1" \
  K2B_PRIVATE_VPN_UPSTREAM_GATEWAY="192.168.1.1" \
  K2B_PRIVATE_VPN_TCP_TARGETS="$tcp_targets" \
  K2B_PRIVATE_VPN_PING_BIN="$d/fakebin/ping" \
  K2B_PRIVATE_VPN_TAILSCALE_BIN="$d/fakebin/tailscale" \
  K2B_FAKE_TAILSCALE_LOG="$d/tailscale-commands.log" \
  python3 "$WATCHDOG" \
    --state-file "$d/state.json" \
    --health-log "$d/private-vpn-health.jsonl" \
    --incident-dir "$d/incidents" \
    --alerts-file "$d/alerts.jsonl" \
    --now "$ts" \
    --send-alert-cmd ""
}

echo "=== router-watchdog-private-vpn.test.sh ==="

{
  d="$TMPROOT/hk-fail-tw-ok"
  mkdir -p "$d"
  write_fake_commands "$d"
  start_mihomo "$d" hk_fail_tw_ok
  base="http://127.0.0.1:$(cat "$d/port")"

  run_watchdog "$d" "2026-06-20T07:00:00Z" "$base"
  [[ ! -s "$d/alerts.jsonl" ]] || fail "first HK failure should log only"
  [[ ! -d "$d/incidents" || -z "$(find "$d/incidents" -type f -print -quit)" ]] || fail "first HK failure should not trace"
  [[ ! -e "$d/expensive-commands.log" ]] || fail "first HK failure should not run AWS or SSH trace"
  [[ ! -e "$d/tailscale-commands.log" ]] || fail "first HK failure should not run Tailscale status/netcheck"
  [[ ! -e "$d/private-vpn-watchdog.lock" ]] || fail "lock file should be removed after successful tick"

  run_watchdog "$d" "2026-06-20T07:01:00Z" "$base"
  [[ -s "$d/alerts.jsonl" ]] || fail "second HK failure should alert"
  grep -q "K2B VPN watchdog: private route degraded" "$d/alerts.jsonl" || fail "failure alert title missing"
  grep -q "Classification: hk_only_down" "$d/alerts.jsonl" || fail "failure alert classification missing"
  grep -q "route is using TW fallback" "$d/alerts.jsonl" || fail "failure alert should mention TW fallback"
  grep -q '^aws ' "$d/expensive-commands.log" || fail "second HK failure should run AWS trace"
  grep -q '^ssh ' "$d/expensive-commands.log" || fail "second HK failure should run SSH trace"
  grep -q 'StrictHostKeyChecking=yes' "$d/expensive-commands.log" || fail "router SSH trace should require a pinned host key by default"
  grep -q '^tailscale status --json$' "$d/tailscale-commands.log" || fail "incident trace should run Tailscale status"
  grep -q '^tailscale netcheck --json$' "$d/tailscale-commands.log" || fail "incident trace should run Tailscale netcheck"

  incident="$(find "$d/incidents" -type f -name '*private-vpn.json' | head -1)"
  [[ -n "$incident" ]] || fail "incident trace should be written on second failure"
  python3 - "$incident" <<'PY' || fail "incident should classify HK-only failure with AWS/server evidence"
import json, sys
payload = json.load(open(sys.argv[1], encoding="utf-8"))
assert payload["classification"] == "hk_only_down", payload
assert payload["aws"]["state"] == "running", payload
assert payload["server"]["hysteria_active"] == "active", payload
assert payload["checks"]["🇹🇼 K2B-VPS-TW"]["ok"] is True, payload
edge = payload["edge"]
assert edge["pings"]["router_lan"]["ok"] is True, edge
assert edge["pings"]["upstream_gateway"]["ok"] is True, edge
assert edge["tcp"]["targets"][0]["ok"] is True, edge
assert edge["tcp"]["targets"][0]["target"].startswith("[redacted-"), edge
assert edge["tailscale_status"]["backend_state"] == "Running", edge
assert edge["tailscale_netcheck"]["udp"] is True, edge
assert payload["evidence_hint"] == "primary_path_specific", payload
PY
  echo "  PASS: HK failure traces and alerts with TW fallback"
}

{
  d="$TMPROOT/edge-trace-disabled"
  mkdir -p "$d"
  write_fake_commands "$d"
  start_mihomo "$d" hk_fail_tw_ok
  base="http://127.0.0.1:$(cat "$d/port")"

  K2B_PRIVATE_VPN_EDGE_TRACE=0 run_watchdog "$d" "2026-06-20T07:03:00Z" "$base"
  K2B_PRIVATE_VPN_EDGE_TRACE=0 run_watchdog "$d" "2026-06-20T07:04:00Z" "$base"
  [[ ! -e "$d/tailscale-commands.log" ]] || fail "disabled edge tracing should not run Tailscale probes"
  incident="$(find "$d/incidents" -type f -name '*private-vpn.json' | head -1)"
  python3 - "$d/private-vpn-health.jsonl" "$incident" <<'PY' || fail "disabled edge tracing should be explicit in health and incident rows"
import json, sys
health_rows = [json.loads(line) for line in open(sys.argv[1], encoding="utf-8")]
incident = json.load(open(sys.argv[2], encoding="utf-8"))
assert health_rows[-1]["edge"] == {"enabled": False}, health_rows[-1]
assert incident["edge"] == {"enabled": False}, incident
PY
  echo "  PASS: edge tracing can be disabled without probe side effects"
}

{
  d="$TMPROOT/tcp-hostname-rejected"
  mkdir -p "$d"
  write_fake_commands "$d"
  start_mihomo "$d" all_ok
  base="http://127.0.0.1:$(cat "$d/port")"

  K2B_TEST_TCP_TARGETS="private.internal.example:443" run_watchdog "$d" "2026-06-20T07:04:30Z" "$base"
  python3 - "$d/private-vpn-health.jsonl" <<'PY' || fail "hostname TCP targets should be rejected before DNS resolution"
import json, sys
row = json.loads(open(sys.argv[1], encoding="utf-8").read().splitlines()[-1])
target = row["edge"]["tcp"]["targets"][0]
assert target["target"] == "[invalid-target]", target
assert target["ok"] is False, target
assert "IP literal" in target["error"], target
PY
  echo "  PASS: TCP probes reject hostnames before DNS"
}

{
  d="$TMPROOT/partial-target-fail"
  mkdir -p "$d"
  write_fake_commands "$d"
  start_mihomo "$d" hk_partial_tw_ok
  base="http://127.0.0.1:$(cat "$d/port")"

  run_watchdog "$d" "2026-06-20T07:05:00Z" "$base"
  run_watchdog "$d" "2026-06-20T07:06:00Z" "$base"
  grep -q "HK: FAIL partial_target_failure" "$d/alerts.jsonl" || fail "partial target success should still fail HK"
  grep -q "Classification: hk_only_down" "$d/alerts.jsonl" || fail "partial HK failure with TW OK should classify as hk_only_down"
  echo "  PASS: partial target failure is degraded"
}

{
  d="$TMPROOT/state-reset-still-down"
  mkdir -p "$d"
  write_fake_commands "$d"
  start_mihomo "$d" hk_fail_tw_ok
  base="http://127.0.0.1:$(cat "$d/port")"

  run_watchdog "$d" "2026-06-20T07:10:00Z" "$base"
  run_watchdog "$d" "2026-06-20T07:11:00Z" "$base"
  rm -f "$d/state.json"
  run_watchdog "$d" "2026-06-20T07:12:00Z" "$base"
  run_watchdog "$d" "2026-06-20T07:13:00Z" "$base"
  degraded_count="$(grep -c '"type":"private_vpn_degraded"' "$d/alerts.jsonl")"
  [[ "$degraded_count" == "1" ]] || fail "state reset during outage should not duplicate degraded alert, got $degraded_count"
  echo "  PASS: state reset during outage does not duplicate degraded alert"
}

{
  d="$TMPROOT/state-reset-before-threshold"
  mkdir -p "$d"
  write_fake_commands "$d"
  start_mihomo "$d" hk_fail_tw_ok
  base="http://127.0.0.1:$(cat "$d/port")"

  run_watchdog "$d" "2026-06-20T07:15:00Z" "$base"
  rm -f "$d/state.json"
  run_watchdog "$d" "2026-06-20T07:16:00Z" "$base"
  grep -q '"type":"private_vpn_degraded"' "$d/alerts.jsonl" || fail "state reset before threshold should reconstruct fail_count and alert"
  grep -q '"outage_started_at":"2026-06-20T07:15:00Z"' "$d/private-vpn-health.jsonl" || fail "health rows should retain outage_started_at"
  echo "  PASS: state reset before threshold still alerts"
}

{
  d="$TMPROOT/incident-prune-protects-new"
  mkdir -p "$d/incidents"
  write_fake_commands "$d"
  cat > "$d/incidents/2099-01-01T000000.000000Z-futureold-private-vpn.json" <<'JSON'
{"timestamp":"2099-01-01T00:00:00Z"}
JSON
  start_mihomo "$d" hk_fail_tw_ok
  base="http://127.0.0.1:$(cat "$d/port")"

  K2B_PRIVATE_VPN_INCIDENT_KEEP=1 run_watchdog "$d" "2026-06-20T07:20:00Z" "$base"
  K2B_PRIVATE_VPN_INCIDENT_KEEP=1 run_watchdog "$d" "2026-06-20T07:21:00Z" "$base"
  new_incident="$(find "$d/incidents" -type f -name '2026-06-20T072100*private-vpn.json' | head -1)"
  [[ -n "$new_incident" ]] || fail "new incident should survive pruning even with older timestamp than existing file"
  echo "  PASS: incident pruning protects just-written bundle"
}

{
  d="$TMPROOT/send-alert-contract"
  mkdir -p "$d"
  write_fake_commands "$d"
  cat > "$d/fake-alert.sh" <<'EOF'
#!/usr/bin/env bash
[[ "$1" == "--event-json" && "$2" == "-" ]] || exit 2
cat > "$TMPDIR/fake-alert-event.json"
EOF
  chmod +x "$d/fake-alert.sh"
  start_mihomo "$d" hk_fail_tw_ok
  base="http://127.0.0.1:$(cat "$d/port")"

  TMPDIR="$d" PATH="$d/fakebin:$PATH" \
  MIHOMO_API_BASE="$base" \
  MIHOMO_API_SECRET="test-secret" \
  K2B_PRIVATE_VPN_FAIL_THRESHOLD=2 \
  K2B_PRIVATE_VPN_RECOVERY_THRESHOLD=3 \
  K2B_PRIVATE_VPN_AWS_PROFILE="test-profile" \
  K2B_PRIVATE_VPN_AWS_REGION="ap-east-1" \
  K2B_PRIVATE_VPN_AWS_INSTANCE="Ubuntu-1" \
  K2B_PRIVATE_VPN_HK_SSH_TARGET="ubuntu@127.0.0.1" \
  python3 "$WATCHDOG" \
    --state-file "$d/state.json" \
    --health-log "$d/private-vpn-health.jsonl" \
    --incident-dir "$d/incidents" \
    --alerts-file "$d/alerts.jsonl" \
    --now "2026-06-20T07:30:00Z" \
    --send-alert-cmd "$d/fake-alert.sh"

  TMPDIR="$d" PATH="$d/fakebin:$PATH" \
  MIHOMO_API_BASE="$base" \
  MIHOMO_API_SECRET="test-secret" \
  K2B_PRIVATE_VPN_FAIL_THRESHOLD=2 \
  K2B_PRIVATE_VPN_RECOVERY_THRESHOLD=3 \
  K2B_PRIVATE_VPN_AWS_PROFILE="test-profile" \
  K2B_PRIVATE_VPN_AWS_REGION="ap-east-1" \
  K2B_PRIVATE_VPN_AWS_INSTANCE="Ubuntu-1" \
  K2B_PRIVATE_VPN_HK_SSH_TARGET="ubuntu@127.0.0.1" \
  python3 "$WATCHDOG" \
    --state-file "$d/state.json" \
    --health-log "$d/private-vpn-health.jsonl" \
    --incident-dir "$d/incidents" \
    --alerts-file "$d/alerts.jsonl" \
    --now "2026-06-20T07:31:00Z" \
    --send-alert-cmd "$d/fake-alert.sh"

  python3 - "$d/fake-alert-event.json" <<'PY' || fail "fake send-alert should receive degraded event JSON"
import json, sys
payload = json.load(open(sys.argv[1], encoding="utf-8"))
assert payload["type"] == "private_vpn_degraded", payload
assert payload["classification"] == "hk_only_down", payload
assert "message" in payload and len(payload["message"]) <= 3520, payload
PY
  echo "  PASS: send-alert command contract"
}

{
  d="$TMPROOT/all-private-fail"
  mkdir -p "$d"
  write_fake_commands "$d"
  start_mihomo "$d" all_private_fail_direct_ok
  base="http://127.0.0.1:$(cat "$d/port")"

  run_watchdog "$d" "2026-06-20T08:00:00Z" "$base"
  K2B_FAKE_TAILSCALE_UDP=false run_watchdog "$d" "2026-06-20T08:01:00Z" "$base"
  grep -q "Classification: all_private_udp_down" "$d/alerts.jsonl" || fail "all-private failure should classify as all_private_udp_down"
  grep -q "Evidence: UDP/NAT path suspect" "$d/alerts.jsonl" || fail "all-private failure alert should include UDP/NAT evidence hint"
  incident="$(find "$d/incidents" -type f -name '*private-vpn.json' | head -1)"
  python3 - "$incident" <<'PY' || fail "all-private incident should capture Tailscale UDP/NAT evidence"
import json, sys
payload = json.load(open(sys.argv[1], encoding="utf-8"))
assert payload["edge"]["tailscale_netcheck"]["udp"] is False, payload
assert payload["edge"]["tailscale_netcheck"]["mapping_varies_by_dest_ip"] is True, payload
assert payload["evidence_hint"] == "udp_nat_mapping_or_isp_modem_suspect", payload
PY
  echo "  PASS: all private leaves down classification"
}

{
  d="$TMPROOT/aws-stopped"
  mkdir -p "$d"
  write_fake_commands "$d"
  cat > "$d/fakebin/aws" <<'EOF'
#!/usr/bin/env bash
cat <<'JSON'
{"state":{"code":80,"name":"stopped"}}
JSON
EOF
  chmod +x "$d/fakebin/aws"
  start_mihomo "$d" hk_fail_tw_ok
  base="http://127.0.0.1:$(cat "$d/port")"

  run_watchdog "$d" "2026-06-20T08:10:00Z" "$base"
  run_watchdog "$d" "2026-06-20T08:11:00Z" "$base"
  grep -q "Classification: aws_down" "$d/alerts.jsonl" || fail "stopped AWS instance should classify as aws_down"
  echo "  PASS: AWS stopped classification"
}

{
  d="$TMPROOT/hysteria-inactive"
  mkdir -p "$d"
  write_fake_commands "$d"
  cat > "$d/fakebin/ssh" <<'EOF'
#!/usr/bin/env bash
if printf '%s\n' "$*" | grep -q 'router'; then
  printf 'router-log: no mihomo restart\n'
else
  cat <<'TEXT'
hysteria_active=inactive
hysteria_enabled=enabled
udp443=unknown
TEXT
fi
EOF
  chmod +x "$d/fakebin/ssh"
  start_mihomo "$d" hk_fail_tw_ok
  base="http://127.0.0.1:$(cat "$d/port")"

  run_watchdog "$d" "2026-06-20T08:20:00Z" "$base"
  run_watchdog "$d" "2026-06-20T08:21:00Z" "$base"
  grep -q "Classification: hysteria_service_down" "$d/alerts.jsonl" || fail "inactive hysteria should classify as hysteria_service_down"
  echo "  PASS: Hysteria inactive classification"
}

{
  d="$TMPROOT/mihomo-down"
  mkdir -p "$d"
  write_fake_commands "$d"
  base="http://127.0.0.1:9"

  run_watchdog "$d" "2026-06-20T09:00:00Z" "$base" || true
  run_watchdog "$d" "2026-06-20T09:01:00Z" "$base" || true
  grep -q "Classification: router_mihomo_down" "$d/alerts.jsonl" || fail "Mihomo API outage should classify as router_mihomo_down"
  echo "  PASS: Mihomo API unavailable classification"
}

{
  d="$TMPROOT/missing-config"
  mkdir -p "$d"
  set +e
  env -i PATH="$PATH" python3 "$WATCHDOG" \
    --state-file "$d/state.json" \
    --health-log "$d/private-vpn-health.jsonl" \
    --incident-dir "$d/incidents" \
    --alerts-file "$d/alerts.jsonl" \
    --now "2026-06-20T09:10:00Z" \
    --send-alert-cmd "" 2>"$d/stderr.log"
  rc=$?
  set -e
  [[ "$rc" != "0" ]] || fail "missing Mihomo config should exit nonzero"
  grep -q '"classification":"configuration_error"' "$d/private-vpn-health.jsonl" || fail "missing Mihomo config should log configuration_error"
  grep -q "missing MIHOMO_API_BASE" "$d/stderr.log" || fail "missing Mihomo config should write stderr"
  echo "  PASS: missing Mihomo config fails loud"
}

{
  d="$TMPROOT/recovery"
  mkdir -p "$d"
  write_fake_commands "$d"
  start_mihomo "$d" hk_fail_tw_ok
  fail_base="http://127.0.0.1:$(cat "$d/port")"
  run_watchdog "$d" "2026-06-20T10:00:00Z" "$fail_base"
  run_watchdog "$d" "2026-06-20T10:01:00Z" "$fail_base"

  start_mihomo "$d" all_ok
  ok_base="http://127.0.0.1:$(cat "$d/port")"
  run_watchdog "$d" "2026-06-20T10:02:00Z" "$ok_base"
  run_watchdog "$d" "2026-06-20T10:03:00Z" "$ok_base"
  run_watchdog "$d" "2026-06-20T10:04:00Z" "$ok_base"
  grep -q "K2B VPN watchdog: HK recovered" "$d/alerts.jsonl" || fail "recovery alert missing after recovery threshold"
  grep -q "HK: OK for 3 consecutive checks" "$d/alerts.jsonl" || fail "recovery alert should include good count"
  start_mihomo "$d" hk_fail_tw_ok
  fail_base="http://127.0.0.1:$(cat "$d/port")"
  run_watchdog "$d" "2026-06-20T10:05:00Z" "$fail_base"
  run_watchdog "$d" "2026-06-20T10:06:00Z" "$fail_base"
  start_mihomo "$d" all_ok
  ok_base="http://127.0.0.1:$(cat "$d/port")"
  run_watchdog "$d" "2026-06-20T10:07:00Z" "$ok_base"
  run_watchdog "$d" "2026-06-20T10:08:00Z" "$ok_base"
  run_watchdog "$d" "2026-06-20T10:09:00Z" "$ok_base"
  recovery_count="$(grep -c '"type":"private_vpn_recovery"' "$d/alerts.jsonl")"
  [[ "$recovery_count" == "2" ]] || fail "second outage should emit a second recovery alert, got $recovery_count"
  echo "  PASS: recovery alert threshold"
}

{
  d="$TMPROOT/state-reset-recovery"
  mkdir -p "$d"
  write_fake_commands "$d"
  start_mihomo "$d" hk_fail_tw_ok
  fail_base="http://127.0.0.1:$(cat "$d/port")"
  run_watchdog "$d" "2026-06-20T10:10:00Z" "$fail_base"
  run_watchdog "$d" "2026-06-20T10:11:00Z" "$fail_base"
  python3 - "$d/state.json" <<'PY' || fail "state reset fixture should start from alerted outage state"
import json, sys
payload = json.load(open(sys.argv[1], encoding="utf-8"))
assert payload["incident_alerted"] is True, payload
assert payload["recovery_alerted"] is False, payload
PY
  rm -f "$d/state.json"

  start_mihomo "$d" all_ok
  ok_base="http://127.0.0.1:$(cat "$d/port")"
  run_watchdog "$d" "2026-06-20T10:12:00Z" "$ok_base"
  run_watchdog "$d" "2026-06-20T10:13:00Z" "$ok_base"
  run_watchdog "$d" "2026-06-20T10:14:00Z" "$ok_base"
  grep -q "K2B VPN watchdog: HK recovered" "$d/alerts.jsonl" || fail "state reset should still allow recovery alert from alert log"
  recovery_count="$(grep -c '"type":"private_vpn_recovery"' "$d/alerts.jsonl")"
  [[ "$recovery_count" == "1" ]] || fail "state reset should emit exactly one recovery alert, got $recovery_count"
  echo "  PASS: state reset recovery alert"
}

{
  d="$TMPROOT/digest"
  mkdir -p "$d"
  cat > "$d/private-vpn-health.jsonl" <<'EOF'
{"timestamp":"2026-06-20T00:00:00Z","overall_ok":false,"classification":"hk_only_down","checks":{"🇭🇰 K2B-VPS-HK":{"ok":false,"latency_ms":null},"🇹🇼 K2B-VPS-TW":{"ok":true,"latency_ms":112},"🇲🇾 K2B-VPS-KL":{"ok":true,"latency_ms":291},"DIRECT":{"ok":true,"latency_ms":15}}}
{"timestamp":"2026-06-20T00:01:00Z","overall_ok":true,"classification":"ok","checks":{"🇭🇰 K2B-VPS-HK":{"ok":true,"latency_ms":103},"🇹🇼 K2B-VPS-TW":{"ok":true,"latency_ms":112},"🇲🇾 K2B-VPS-KL":{"ok":true,"latency_ms":291},"DIRECT":{"ok":true,"latency_ms":15}}}
EOF

  python3 "$DIGEST" \
    --health-log "$d/missing-health.jsonl" \
    --score-log "$d/missing-score.jsonl" \
    --confirm-failures-log "$d/missing-confirm.jsonl" \
    --private-vpn-log "$d/private-vpn-health.jsonl" \
    --now "2026-06-20T01:00:00Z" > "$d/digest.out"

  grep -q "Router VPN digest: OK" "$d/digest.out" || fail "private VPN digest title/status missing"
  grep -q "Overall router digest: OK (private VPN OK; router no recent ticks)" "$d/digest.out" || fail "combined digest status missing"
  grep -q "Router watchdog digest:" "$d/digest.out" || fail "digest should retain the general router watchdog section"
  grep -q "Primary route: HK" "$d/digest.out" || fail "private VPN digest should name HK primary"
  grep -q "Fallback route: TW" "$d/digest.out" || fail "private VPN digest should name TW fallback"
  if grep -q "Recommendation: prefer .*DoggyGo\\|Recommendation: prefer .*手动切换" "$d/digest.out"; then
    fail "private VPN digest should not lead with DoggyGo/subscription recommendation"
  fi
  echo "  PASS: private VPN daily digest"
}

{
  d="$TMPROOT/timeout-expired-bytes"
  mkdir -p "$d"
  python3 - "$WATCHDOG" <<'PY' || fail "run_command should redact real TimeoutExpired output safely"
import importlib.util
import sys
import sys

path = sys.argv[1]
spec = importlib.util.spec_from_file_location("private_vpn_watchdog", path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
result = module.run_command(
    [
        sys.executable,
        "-u",
        "-c",
        "import sys,time; sys.stdout.write('token=abc123 192.168.9.1 timed out\\n'); sys.stdout.flush(); sys.stderr.write('secret=xyz789 192.168.9.1 timed out\\n'); sys.stderr.flush(); time.sleep(5)",
    ],
    1,
)

assert result["ok"] is False, result
assert result["returncode"] is None, result
assert isinstance(result["stdout"], str), result
assert isinstance(result["stderr"], str), result
assert "[redacted]" in result["stdout"], result
assert "[redacted]" in result["stderr"], result
assert "[redacted-ip]" in result["stdout"], result
assert "[redacted-ip]" in result["stderr"], result
assert "command timed out" in result["stderr"], result
PY
  echo "  PASS: TimeoutExpired byte output is redacted safely"
}

{
  d="$TMPROOT/resolved-key-redaction"
  mkdir -p "$d"
  python3 - "$WATCHDOG" <<'PY' || fail "redact_text should hide resolved HK SSH key paths"
import importlib.util
import os
import tempfile
from pathlib import Path
import sys

path = sys.argv[1]
spec = importlib.util.spec_from_file_location("private_vpn_watchdog", path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

resolved = "/Users/example/.ssh/k2b-hk"
original = module.usable_ssh_key_path
module.usable_ssh_key_path = lambda _raw: resolved
os.environ["K2B_PRIVATE_VPN_HK_SSH_KEY"] = "~/.ssh/k2b-hk"
try:
    redacted = module.redact_text(f"identity file {resolved} failed to load")
finally:
    module.usable_ssh_key_path = original
    os.environ.pop("K2B_PRIVATE_VPN_HK_SSH_KEY", None)

assert resolved not in redacted, redacted
assert "[redacted]" in redacted, redacted
PY
  echo "  PASS: resolved HK SSH key paths are redacted"
}

echo "router-watchdog-private-vpn.test.sh: all tests passed"
