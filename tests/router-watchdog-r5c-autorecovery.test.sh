#!/usr/bin/env bash
# tests/router-watchdog-r5c-autorecovery.test.sh
# Fail-closed integration tests for the R5C auto-recovery helper.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC_DIR="$REPO_ROOT/scripts/router-watchdog"
RECOVERY="$SRC_DIR/bin/r5c-autorecovery.py"
INSTALL="$SRC_DIR/install.sh"
R5C_PLIST="$REPO_ROOT/launchd/com.k2b.router-r5c-autorecovery.plist"
R5C_EXPECTED_INTERVAL=60

TMPROOT="$(mktemp -d)"
trap 'rm -rf "$TMPROOT"' EXIT

fail() { echo "FAIL: $*" >&2; exit 1; }

write_fake_commands() {
  local d="$1"
  mkdir -p "$d/fakebin"
  printf 'fake-key\n' > "$d/fakekey"
  chmod 600 "$d/fakekey"

  cat > "$d/fakebin/ping" <<'EOF'
#!/usr/bin/env bash
host="${@: -1}"
case "$host" in
  192.168.9.1)
    if [[ "${K2B_FAKE_R5C_PING_OK:-0}" == "1" || ( -n "${K2B_FAKE_R5C_RESTORED_FILE:-}" && -f "$K2B_FAKE_R5C_RESTORED_FILE" ) ]]; then
      printf '64 bytes from %s: icmp_seq=0 ttl=64 time=2.4 ms\n' "$host"
      exit 0
    fi
    printf 'ping: request timeout for %s\n' "$host" >&2
    exit 2
    ;;
  192.168.9.2)
    if [[ "${K2B_FAKE_ASUS_PING_OK:-1}" == "1" ]]; then
      printf '64 bytes from %s: icmp_seq=0 ttl=64 time=1.8 ms\n' "$host"
      exit 0
    fi
    printf 'ping: request timeout for %s\n' "$host" >&2
    exit 2
    ;;
  *)
    printf 'ping: request timeout for %s\n' "$host" >&2
    exit 2
    ;;
esac
EOF

  cat > "$d/fakebin/curl" <<'EOF'
#!/usr/bin/env bash
url="${@: -1}"
case "$url" in
  http://192.168.9.1:9090/*|http://192.168.9.1/*)
    if [[ "${K2B_FAKE_R5C_HTTP_OK:-0}" == "1" || ( -n "${K2B_FAKE_R5C_RESTORED_FILE:-}" && -f "$K2B_FAKE_R5C_RESTORED_FILE" ) ]]; then
      printf '{"version":"fake"}\n'
      exit 0
    fi
    exit 7
    ;;
  *)
    exit 7
    ;;
esac
EOF

  cat > "$d/fakebin/ssh" <<EOF
#!/usr/bin/env bash
printf 'ssh %s\n' "\$*" >> "$d/ssh-commands.log"
if [[ "\$*" == *"/bin/true"* && -n "\${K2B_FAKE_RESTORE_ON_SSH_PROBE_COUNT:-}" && -n "\${K2B_FAKE_SSH_COUNTER_FILE:-}" && -n "\${K2B_FAKE_R5C_RESTORED_FILE:-}" ]]; then
  count=0
  [[ -f "\$K2B_FAKE_SSH_COUNTER_FILE" ]] && count="\$(<"\$K2B_FAKE_SSH_COUNTER_FILE")"
  count=\$((count + 1))
  printf '%d' "\$count" > "\$K2B_FAKE_SSH_COUNTER_FILE"
  if [[ "\$count" -ge "\$K2B_FAKE_RESTORE_ON_SSH_PROBE_COUNT" ]]; then
    touch "\$K2B_FAKE_R5C_RESTORED_FILE"
  fi
fi
if [[ "\${K2B_FAKE_ASUS_SSH_OK:-1}" == "1" ]]; then
  printf 'rebooting\n'
  exit 0
fi
exit 255
EOF
  cat > "$d/fakebin/send-alert.sh" <<EOF
#!/usr/bin/env bash
if [[ "\${1:-}" == "--event-json" ]]; then
  if [[ "\${2:-}" == "-" ]]; then
    cat >> "$d/sent-alerts.jsonl"
    printf '\\n' >> "$d/sent-alerts.jsonl"
  else
    cat "\${2:-}" >> "$d/sent-alerts.jsonl"
    printf '\\n' >> "$d/sent-alerts.jsonl"
  fi
else
  printf '%s\\n' "\$*" >> "$d/sent-alerts.txt"
fi
exit 0
EOF
  chmod +x "$d/fakebin/ping" "$d/fakebin/curl" "$d/fakebin/ssh" "$d/fakebin/send-alert.sh"
}

run_recovery() {
  local d="$1" ts="$2"
  shift 2
  local asus_ssh_key
  if [[ ${K2B_R5C_AUTORECOVERY_ASUS_SSH_KEY+x} ]]; then
    asus_ssh_key="$K2B_R5C_AUTORECOVERY_ASUS_SSH_KEY"
  else
    asus_ssh_key="$d/fakekey"
  fi
  PATH="$d/fakebin:$PATH" \
  K2B_ROUTER_WATCHDOG_APP_DIR="$d/app" \
  K2B_ROUTER_WATCHDOG_LOG_DIR="$d/logs" \
  K2B_ROUTER_WATCHDOG_ENV_FILE="$d/watchdog.env" \
  K2B_R5C_AUTORECOVERY_STATE_FILE="$d/state.json" \
  K2B_R5C_AUTORECOVERY_LOG="$d/recovery.jsonl" \
  K2B_R5C_AUTORECOVERY_EVIDENCE_DIR="$d/evidence" \
  K2B_R5C_AUTORECOVERY_ALERTS_LOG="$d/r5c-alerts.jsonl" \
  K2B_R5C_AUTORECOVERY_SEND_ALERT_CMD="$d/fakebin/send-alert.sh" \
  K2B_R5C_AUTORECOVERY_SENTINEL="$d/sentinel" \
  K2B_R5C_AUTORECOVERY_R5C_LAN_IP="192.168.9.1" \
  K2B_R5C_AUTORECOVERY_MIHOMO_API_BASE="http://192.168.9.1:9090" \
  K2B_R5C_AUTORECOVERY_ASUS_HOST="192.168.9.2" \
  K2B_R5C_AUTORECOVERY_ASUS_SSH_TARGET="admin@192.168.9.2" \
  K2B_R5C_AUTORECOVERY_ASUS_SSH_KEY="$asus_ssh_key" \
  K2B_R5C_AUTORECOVERY_THRESHOLD="3" \
  K2B_R5C_AUTORECOVERY_COOLDOWN_MINUTES="30" \
  K2B_R5C_AUTORECOVERY_PING_BIN="$d/fakebin/ping" \
  K2B_R5C_AUTORECOVERY_CURL_BIN="$d/fakebin/curl" \
  K2B_R5C_AUTORECOVERY_SSH_BIN="$d/fakebin/ssh" \
  K2B_R5C_AUTORECOVERY_TEST_MODE=1 \
  K2B_R5C_AUTORECOVERY_TEST_ALLOW_ANY_SSH_KEY=1 \
  K2B_R5C_AUTORECOVERY_TEST_ALLOW_FAKE_PROBE_BINS="${K2B_R5C_AUTORECOVERY_TEST_ALLOW_FAKE_PROBE_BINS:-1}" \
  K2B_R5C_AUTORECOVERY_TEST_ALLOW_FAKE_SSH_BIN="${K2B_R5C_AUTORECOVERY_TEST_ALLOW_FAKE_SSH_BIN:-1}" \
  python3 "$RECOVERY" --now "$ts" "$@"
}

write_health_rows() {
  local d="$1"
  mkdir -p "$d/logs"
  cat > "$d/logs/health.jsonl" <<'EOF'
{"timestamp":"2026-06-22T04:30:00Z","overall_ok":false,"checks":{"router_reachable":{"ok":true},"mihomo_api":{"ok":true}}}
EOF
  cat > "$d/logs/private-vpn-health.jsonl" <<'EOF'
{"timestamp":"2026-06-22T04:30:00Z","overall_ok":false,"classification":"all_private_udp_down","checks":{"HK":{"ok":false},"TW":{"ok":true}}}
EOF
}

count_log() {
  local path="$1"
  [[ -f "$path" ]] || { echo 0; return; }
  wc -l < "$path" | tr -d ' '
}

count_actions() {
  local d="$1" action="$2"
  [[ -f "$d/recovery.jsonl" ]] || { echo 0; return; }
  grep -c '"action":"'"$action"'"' "$d/recovery.jsonl" || true
}

echo "=== router-watchdog-r5c-autorecovery.test.sh ==="

# ---------------------------------------------------------------------------
# Test 1: threshold 3 fires exactly once for the 24-hour replay shape.
# ---------------------------------------------------------------------------
{
  d="$TMPROOT/threshold-replay"
  mkdir -p "$d"
  write_fake_commands "$d"
  write_health_rows "$d"
  touch "$d/sentinel"

  # One isolated hard-down sample -> no fire.
  run_recovery "$d" "2026-06-21T13:43:19Z"
  [[ "$(count_actions "$d" reboot_attempted)" == "0" ]] || fail "isolated hard-down sample should not fire"
  [[ "$(count_log "$d/recovery.jsonl")" == "1" ]] || fail "one sample should produce one log row"

  # 17 consecutive hard-down samples -> exactly one fire at the third sample.
  for i in $(seq 1 17); do
    ts="$(printf '2026-06-22T04:38:%02dZ' "$i")"
    run_recovery "$d" "$ts"
  done
  fire_count="$(count_actions "$d" reboot_attempted)"
  [[ "$fire_count" == "1" ]] || fail "17 consecutive hard-down samples should fire exactly once, got $fire_count"

  # Two isolated final hard-down samples -> no second fire.
  for i in $(seq 53 54); do
    ts="$(printf '2026-06-22T06:13:%02dZ' "$i")"
    run_recovery "$d" "$ts"
  done
  fire_count="$(count_actions "$d" reboot_attempted)"
  [[ "$fire_count" == "1" ]] || fail "final two isolated samples should not cause a second fire, got $fire_count"

  echo "  PASS: threshold 3 replay fires exactly once"
}

# ---------------------------------------------------------------------------
# Test 2: private route classifications alone do not fire when R5C LAN reachable.
# ---------------------------------------------------------------------------
{
  d="$TMPROOT/private-route-only"
  mkdir -p "$d"
  write_fake_commands "$d"
  write_health_rows "$d"
  touch "$d/sentinel"

  # R5C LAN reachable, router HTTP reachable, mihomo reachable.
  K2B_FAKE_R5C_PING_OK=1 K2B_FAKE_R5C_HTTP_OK=1 \
  run_recovery "$d" "2026-06-22T05:00:00Z"
  run_recovery "$d" "2026-06-22T05:01:00Z"
  run_recovery "$d" "2026-06-22T05:02:00Z"
  [[ "$(count_actions "$d" reboot_attempted)" == "0" ]] || fail "reachable R5C must not trigger recovery"
  grep -q '"action":"no_action"' "$d/recovery.jsonl" || fail "reachable R5C should log no_action"

  # Simulate bad private classifications but R5C LAN still reachable.
  cat > "$d/logs/private-vpn-health.jsonl" <<'EOF'
{"timestamp":"2026-06-22T05:00:00Z","overall_ok":false,"classification":"hk_only_down","checks":{"HK":{"ok":false},"TW":{"ok":true}}}
EOF
  K2B_FAKE_R5C_PING_OK=1 K2B_FAKE_R5C_HTTP_OK=1 \
  run_recovery "$d" "2026-06-22T05:03:00Z"
  K2B_FAKE_R5C_PING_OK=1 K2B_FAKE_R5C_HTTP_OK=1 \
  run_recovery "$d" "2026-06-22T05:04:00Z"
  K2B_FAKE_R5C_PING_OK=1 K2B_FAKE_R5C_HTTP_OK=1 \
  run_recovery "$d" "2026-06-22T05:05:00Z"
  [[ "$(count_actions "$d" reboot_attempted)" == "0" ]] || fail "private-route-only degradation must not fire"

  echo "  PASS: private route bad + R5C LAN reachable does not fire"
}

# ---------------------------------------------------------------------------
# Test 3: R5C alerts are unified around first hiccup, fire, and clear.
# ---------------------------------------------------------------------------
{
  d="$TMPROOT/unified-alerts"
  mkdir -p "$d"
  write_fake_commands "$d"
  write_health_rows "$d"
  touch "$d/sentinel"

  run_recovery "$d" "2026-06-22T04:38:18Z"
  run_recovery "$d" "2026-06-22T04:39:19Z"
  run_recovery "$d" "2026-06-22T04:40:27Z"
  run_recovery "$d" "2026-06-22T04:41:28Z"
  K2B_FAKE_R5C_PING_OK=1 K2B_FAKE_R5C_HTTP_OK=1 \
  run_recovery "$d" "2026-06-22T04:56:38Z"

  run_recovery "$d" "2026-06-22T06:13:53Z"
  K2B_FAKE_R5C_PING_OK=1 K2B_FAKE_R5C_HTTP_OK=1 \
  run_recovery "$d" "2026-06-22T06:16:18Z"

  python3 - "$d/r5c-alerts.jsonl" "$d/sent-alerts.jsonl" <<'PY' || fail "R5C unified alert sequence did not match expected 24h shape"
import json
import sys

def load(path):
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]

alerts = load(sys.argv[1])
sent = load(sys.argv[2])
types = [item.get("type") for item in alerts]
assert types.count("r5c_hard_down_suspected") == 2, types
assert types.count("r5c_autorecovery_fired") == 1, types
assert types.count("r5c_autorecovery_recovered") == 1, types
assert types.count("r5c_autorecovery_cleared") == 1, types
assert "12:38 HKT" in alerts[0]["message"], alerts[0]["message"]
assert "1/3" in alerts[0]["message"], alerts[0]["message"]
assert "12:40 HKT" in next(a["message"] for a in alerts if a.get("type") == "r5c_autorecovery_fired")
assert len(sent) == len(alerts), (len(sent), len(alerts))
PY

  echo "  PASS: R5C unified alert sequence covers first hiccup, fire, recover, and clear"
}

# ---------------------------------------------------------------------------
# Test 4: Telegram delivery failure is durable state, not a hidden no-op.
# ---------------------------------------------------------------------------
{
  d="$TMPROOT/alert-delivery-failure"
  mkdir -p "$d"
  write_fake_commands "$d"
  write_health_rows "$d"
  touch "$d/sentinel"
  cat > "$d/fakebin/send-alert.sh" <<'EOF'
#!/usr/bin/env bash
cat >/dev/null
echo "simulated telegram failure" >&2
exit 9
EOF
  chmod +x "$d/fakebin/send-alert.sh"

  run_recovery "$d" "2026-06-22T07:13:53Z"

  [[ -s "$d/r5c-alerts.jsonl" ]] || fail "local R5C alert log should still be written when Telegram send fails"
  python3 - "$d/state.json" <<'PY' || fail "R5C state should persist alert delivery failure"
import json, sys
state = json.load(open(sys.argv[1], encoding="utf-8"))
assert state.get("r5c_alert_delivery_failures") == 1, state
assert "simulated telegram failure" in state.get("r5c_last_alert_delivery_error", ""), state
PY

  cat > "$d/fakebin/send-alert.sh" <<EOF
#!/usr/bin/env bash
cat >> "$d/sent-alerts.jsonl"
printf '\\n' >> "$d/sent-alerts.jsonl"
exit 0
EOF
  chmod +x "$d/fakebin/send-alert.sh"
  run_recovery "$d" "2026-06-22T07:14:53Z"
  run_recovery "$d" "2026-06-22T07:15:53Z"
  python3 - "$d/state.json" <<'PY' || fail "successful R5C alert delivery should clear stale delivery failure"
import json, sys
state = json.load(open(sys.argv[1], encoding="utf-8"))
assert state.get("r5c_alert_delivery_failures") == 0, state
assert "r5c_last_alert_delivery_error" not in state, state
PY

  echo "  PASS: R5C alert delivery failure is persisted"
}

# ---------------------------------------------------------------------------
# Test 5: Telegram delivery failure count is capped.
# ---------------------------------------------------------------------------
{
  d="$TMPROOT/alert-delivery-failure-cap"
  mkdir -p "$d"
  write_fake_commands "$d"
  write_health_rows "$d"
  touch "$d/sentinel"
  printf '%s\n' '{"consecutive_hard_down":0,"r5c_alert_delivery_failures":100}' > "$d/state.json"
  cat > "$d/fakebin/send-alert.sh" <<'EOF'
#!/usr/bin/env bash
cat >/dev/null
echo "simulated telegram failure" >&2
exit 9
EOF
  chmod +x "$d/fakebin/send-alert.sh"

  run_recovery "$d" "2026-06-22T07:20:53Z"

  python3 - "$d/state.json" <<'PY' || fail "R5C alert delivery failure count should be capped"
import json, sys
state = json.load(open(sys.argv[1], encoding="utf-8"))
assert state.get("r5c_alert_delivery_failures") == 100, state
assert "simulated telegram failure" in state.get("r5c_last_alert_delivery_error", ""), state
PY

  echo "  PASS: R5C alert delivery failure count is capped"
}

# ---------------------------------------------------------------------------
# Test 6: stale Telegram delivery failure clears before a new alert attempt.
# ---------------------------------------------------------------------------
{
  d="$TMPROOT/alert-delivery-stale-before-new-failure"
  mkdir -p "$d"
  write_fake_commands "$d"
  write_health_rows "$d"
  touch "$d/sentinel"
  printf '%s\n' '{"consecutive_hard_down":0,"r5c_alert_delivery_failures":42,"r5c_last_alert_delivery_failed_at":"2026-06-20T07:00:00Z","r5c_last_alert_delivery_error":"old failure"}' > "$d/state.json"
  cat > "$d/fakebin/send-alert.sh" <<'EOF'
#!/usr/bin/env bash
cat >/dev/null
echo "new telegram failure" >&2
exit 9
EOF
  chmod +x "$d/fakebin/send-alert.sh"

  run_recovery "$d" "2026-06-22T07:25:53Z"

  python3 - "$d/state.json" <<'PY' || fail "stale R5C alert delivery failure should clear before counting new failure"
import json, sys
state = json.load(open(sys.argv[1], encoding="utf-8"))
assert state.get("r5c_alert_delivery_failures") == 1, state
assert "new telegram failure" in state.get("r5c_last_alert_delivery_error", ""), state
PY

  echo "  PASS: stale R5C alert delivery failure clears before new alert attempt"
}

# ---------------------------------------------------------------------------
# Test 7: suspected alert is deduped if incident-open state is lost.
# ---------------------------------------------------------------------------
{
  d="$TMPROOT/suspected-dedupe-state-loss"
  mkdir -p "$d"
  write_fake_commands "$d"
  write_health_rows "$d"
  touch "$d/sentinel"
  printf '%s\n' '{"consecutive_hard_down":0,"r5c_last_alert_type":"r5c_hard_down_suspected","r5c_last_alert_at":"2026-06-22T07:29:00Z"}' > "$d/state.json"

  run_recovery "$d" "2026-06-22T07:30:00Z"

  suspected_count="$(grep -c '"type":"r5c_hard_down_suspected"' "$d/r5c-alerts.jsonl" 2>/dev/null || echo 0)"
  [[ "$suspected_count" == "0" ]] || fail "recent suspected alert should not be duplicated after incident-open state loss, got $suspected_count"

  echo "  PASS: recent R5C suspected alert is deduped after state loss"
}

# ---------------------------------------------------------------------------
# Test 8: sentinel absence prevents ASUS reboot and writes dry-run decision.
# ---------------------------------------------------------------------------
{
  d="$TMPROOT/sentinel-missing"
  mkdir -p "$d"
  write_fake_commands "$d"
  write_health_rows "$d"
  # sentinel intentionally absent

  for i in $(seq 1 5); do
    ts="$(printf '2026-06-22T07:00:%02dZ' "$i")"
    run_recovery "$d" "$ts"
  done

  [[ "$(count_actions "$d" reboot_attempted)" == "0" ]] || fail "missing sentinel must not reboot"
  grep -q '"action":"blocked_sentinel_missing"' "$d/recovery.jsonl" || fail "missing sentinel should log blocked_sentinel_missing"
  if [[ -e "$d/ssh-commands.log" ]]; then
    grep -q '/sbin/reboot' "$d/ssh-commands.log" && fail "missing sentinel must not invoke SSH reboot"
  fi

  echo "  PASS: missing sentinel blocks reboot and logs dry-run decision"
}

# ---------------------------------------------------------------------------
# Test 5: cooldown prevents a second reboot in the same incident window.
# ---------------------------------------------------------------------------
{
  d="$TMPROOT/cooldown"
  mkdir -p "$d"
  write_fake_commands "$d"
  write_health_rows "$d"
  touch "$d/sentinel"

  for i in $(seq 1 5); do
    ts="$(printf '2026-06-22T08:00:%02dZ' "$i")"
    run_recovery "$d" "$ts"
  done
  [[ "$(count_actions "$d" reboot_attempted)" == "1" ]] || fail "first window should fire once"

  for i in $(seq 6 10); do
    ts="$(printf '2026-06-22T08:00:%02dZ' "$i")"
    run_recovery "$d" "$ts"
  done
  [[ "$(count_actions "$d" reboot_attempted)" == "1" ]] || fail "cooldown should prevent second reboot"
  grep -q '"action":"blocked_cooldown"' "$d/recovery.jsonl" || fail "cooldown should log blocked_cooldown"

  echo "  PASS: cooldown blocks second reboot"
}

# ---------------------------------------------------------------------------
# Test 6: ASUS unreachable means alert/log only, no blind reboot attempt.
# ---------------------------------------------------------------------------
{
  d="$TMPROOT/asus-unreachable"
  mkdir -p "$d"
  write_fake_commands "$d"
  write_health_rows "$d"
  touch "$d/sentinel"

  K2B_FAKE_ASUS_PING_OK=0 K2B_FAKE_ASUS_SSH_OK=0 \
  run_recovery "$d" "2026-06-22T09:00:00Z"
  K2B_FAKE_ASUS_PING_OK=0 K2B_FAKE_ASUS_SSH_OK=0 \
  run_recovery "$d" "2026-06-22T09:01:00Z"
  K2B_FAKE_ASUS_PING_OK=0 K2B_FAKE_ASUS_SSH_OK=0 \
  run_recovery "$d" "2026-06-22T09:02:00Z"

  [[ "$(count_actions "$d" reboot_attempted)" == "0" ]] || fail "unreachable ASUS must not attempt reboot"
  grep -q '"action":"blocked_asus_unreachable"' "$d/recovery.jsonl" || fail "unreachable ASUS should log blocked_asus_unreachable"
  if [[ -e "$d/ssh-commands.log" ]]; then
    grep -q '/sbin/reboot' "$d/ssh-commands.log" && fail "unreachable ASUS must not invoke SSH reboot"
  fi

  echo "  PASS: ASUS unreachable blocks reboot"
}

# ---------------------------------------------------------------------------
# Test 7: blank configured ASUS SSH key blocks without default-key fallback.
# ---------------------------------------------------------------------------
{
  d="$TMPROOT/asus-key-blank"
  mkdir -p "$d"
  write_fake_commands "$d"
  write_health_rows "$d"
  touch "$d/sentinel"

  for i in $(seq 1 3); do
    ts="$(printf '2026-06-22T09:20:%02dZ' "$i")"
    K2B_R5C_AUTORECOVERY_ASUS_SSH_KEY="" \
    run_recovery "$d" "$ts"
  done

  [[ "$(count_actions "$d" reboot_attempted)" == "0" ]] || fail "blank ASUS key must not attempt reboot"
  grep -q '"configured ASUS SSH key is required"' "$d/recovery.jsonl" || fail "blank ASUS key should be logged"
  [[ ! -e "$d/ssh-commands.log" ]] || fail "blank ASUS key must not fall back to default SSH credentials"

  echo "  PASS: blank ASUS SSH key blocks without fallback"
}

# ---------------------------------------------------------------------------
# Test 8: unusable configured ASUS SSH key blocks without default-key fallback.
# ---------------------------------------------------------------------------
{
  d="$TMPROOT/asus-key-unusable"
  mkdir -p "$d"
  write_fake_commands "$d"
  write_health_rows "$d"
  touch "$d/sentinel"
  chmod 644 "$d/fakekey"

  for i in $(seq 1 3); do
    ts="$(printf '2026-06-22T09:30:%02dZ' "$i")"
    K2B_R5C_AUTORECOVERY_ASUS_SSH_KEY="$d/fakekey" \
    run_recovery "$d" "$ts"
  done

  [[ "$(count_actions "$d" reboot_attempted)" == "0" ]] || fail "unusable ASUS key must not attempt reboot"
  grep -q '"configured ASUS SSH key is unusable"' "$d/recovery.jsonl" || fail "unusable ASUS key should be logged"
  [[ ! -e "$d/ssh-commands.log" ]] || fail "unusable ASUS key must not fall back to default SSH credentials"

  echo "  PASS: unusable ASUS SSH key blocks without fallback"
}

# ---------------------------------------------------------------------------
# Test 9: untrusted probe binaries cannot manufacture hard-down.
# ---------------------------------------------------------------------------
{
  d="$TMPROOT/probe-bin-untrusted"
  mkdir -p "$d"
  write_fake_commands "$d"
  write_health_rows "$d"
  touch "$d/sentinel"

  for i in $(seq 1 3); do
    ts="$(printf '2026-06-22T09:35:%02dZ' "$i")"
    K2B_R5C_AUTORECOVERY_TEST_ALLOW_FAKE_PROBE_BINS=0 \
    run_recovery "$d" "$ts"
  done

  [[ "$(count_actions "$d" reboot_attempted)" == "0" ]] || fail "untrusted probe bins must not attempt reboot"
  grep -q '"configured ping binary is not trusted"' "$d/recovery.jsonl" || fail "untrusted ping binary should be logged"
  grep -q '"configured curl binary is not trusted"' "$d/recovery.jsonl" || fail "untrusted curl binary should be logged"
  if [[ -e "$d/ssh-commands.log" ]]; then
    grep -q '/sbin/reboot' "$d/ssh-commands.log" && fail "untrusted probe bins must not invoke reboot"
  fi

  echo "  PASS: untrusted probe binaries cannot manufacture hard-down"
}

# ---------------------------------------------------------------------------
# Test 10: untrusted SSH binary blocks without invoking the fake executable.
# ---------------------------------------------------------------------------
{
  d="$TMPROOT/ssh-bin-untrusted"
  mkdir -p "$d"
  write_fake_commands "$d"
  write_health_rows "$d"
  touch "$d/sentinel"

  for i in $(seq 1 3); do
    ts="$(printf '2026-06-22T09:40:%02dZ' "$i")"
    K2B_R5C_AUTORECOVERY_TEST_ALLOW_FAKE_SSH_BIN=0 \
    run_recovery "$d" "$ts"
  done

  [[ "$(count_actions "$d" reboot_attempted)" == "0" ]] || fail "untrusted SSH binary must not attempt reboot"
  grep -q '"configured SSH binary is not trusted"' "$d/recovery.jsonl" || fail "untrusted SSH binary should be logged"
  [[ ! -e "$d/ssh-commands.log" ]] || fail "untrusted SSH binary must not be invoked"

  echo "  PASS: untrusted SSH binary blocks without execution"
}

# ---------------------------------------------------------------------------
# Test 11: failed reboot command still starts cooldown and clears streak.
# ---------------------------------------------------------------------------
{
  d="$TMPROOT/reboot-failed-cooldown"
  mkdir -p "$d"
  write_fake_commands "$d"
  write_health_rows "$d"
  touch "$d/sentinel"

  mv "$d/fakebin/ssh" "$d/fakebin/ssh.real"
  cat > "$d/fakebin/ssh" <<EOF
#!/usr/bin/env bash
printf 'ssh %s\n' "\$*" >> "$d/ssh-commands.log"
if [[ "\$*" == *"/sbin/reboot"* ]]; then
  exit 255
fi
exit 0
EOF
  chmod +x "$d/fakebin/ssh"

  for i in $(seq 1 3); do
    ts="$(printf '2026-06-22T09:45:%02dZ' "$i")"
    run_recovery "$d" "$ts"
  done
  [[ "$(count_actions "$d" reboot_failed)" == "1" ]] || fail "failed reboot command should log reboot_failed"

  for i in $(seq 4 8); do
    ts="$(printf '2026-06-22T09:45:%02dZ' "$i")"
    run_recovery "$d" "$ts"
  done

  reboot_invocations="$(grep -c '/sbin/reboot' "$d/ssh-commands.log" || true)"
  [[ "$reboot_invocations" == "1" ]] || fail "failed reboot should still cooldown; got $reboot_invocations reboot commands"
  grep -q -- '-F /dev/null' "$d/ssh-commands.log" || fail "SSH command must ignore ambient user config"
  grep -q -- '-o IdentitiesOnly=yes' "$d/ssh-commands.log" || fail "SSH command must force configured key only"
  grep -q -- '-o IdentityAgent=none' "$d/ssh-commands.log" || fail "SSH command must disable ambient ssh-agent fallback"
  grep -q -- "-i $d/fakekey" "$d/ssh-commands.log" || fail "SSH command must include configured key"
  grep -q '"action":"blocked_cooldown"' "$d/recovery.jsonl" || fail "failed reboot should be followed by cooldown block"

  echo "  PASS: failed reboot command starts cooldown"
}

# ---------------------------------------------------------------------------
# Test 12: evidence is written before the reboot command is attempted.
# ---------------------------------------------------------------------------
{
  d="$TMPROOT/evidence-first"
  mkdir -p "$d"
  write_fake_commands "$d"
  write_health_rows "$d"
  touch "$d/sentinel"

  # Wrap the fake SSH so it records evidence file mtime before exiting.
  mv "$d/fakebin/ssh" "$d/fakebin/ssh.real"
  cat > "$d/fakebin/ssh" <<EOF
#!/usr/bin/env bash
printf 'ssh %s\n' "\$*" >> "$d/ssh-commands.log"
if [[ "\$*" == *"/sbin/reboot"* ]]; then
  ls -l "$d/evidence" > "$d/evidence-ls-at-ssh.txt"
fi
exit 0
EOF
  chmod +x "$d/fakebin/ssh"

  for i in $(seq 1 3); do
    ts="$(printf '2026-06-22T10:00:%02dZ' "$i")"
    run_recovery "$d" "$ts"
  done

  [[ "$(count_actions "$d" reboot_attempted)" == "1" ]] || fail "evidence-first scenario should fire once"
  [[ -d "$d/evidence" ]] || fail "evidence directory should exist"
  evidence_count="$(find "$d/evidence" -maxdepth 1 -type f -name '*.json' | wc -l | tr -d ' ')"
  [[ "$evidence_count" -ge 1 ]] || fail "at least one evidence JSON should be written"
  [[ -s "$d/evidence-ls-at-ssh.txt" ]] || fail "SSH wrapper did not run"
  grep -q '.json' "$d/evidence-ls-at-ssh.txt" || fail "evidence file must exist before SSH reboot attempt"

  echo "  PASS: evidence written before reboot command"
}

# ---------------------------------------------------------------------------
# Test 13: final confirmation blocks if R5C recovers before reboot.
# ---------------------------------------------------------------------------
{
  d="$TMPROOT/final-confirmation"
  mkdir -p "$d"
  write_fake_commands "$d"
  write_health_rows "$d"
  touch "$d/sentinel"

  for i in $(seq 1 3); do
    ts="$(printf '2026-06-22T10:30:%02dZ' "$i")"
    K2B_FAKE_R5C_RESTORED_FILE="$d/r5c-restored" \
    K2B_FAKE_RESTORE_ON_SSH_PROBE_COUNT=4 \
    K2B_FAKE_SSH_COUNTER_FILE="$d/ssh-probe-count" \
    run_recovery "$d" "$ts"
  done

  [[ "$(count_actions "$d" reboot_attempted)" == "0" ]] || fail "final confirmation recovery must not reboot"
  grep -q '"action":"blocked_final_confirmation_failed"' "$d/recovery.jsonl" || fail "final confirmation recovery should log blocked_final_confirmation_failed"
  grep -q '"final_confirmation"' "$d/recovery.jsonl" || fail "final confirmation details should be logged"
  if [[ -e "$d/ssh-commands.log" ]]; then
    grep -q '/sbin/reboot' "$d/ssh-commands.log" && fail "final confirmation recovery must not invoke SSH reboot"
  fi

  rm -f "$d/r5c-restored"
  run_recovery "$d" "2026-06-22T10:31:00Z"
  [[ "$(count_actions "$d" reboot_attempted)" == "0" ]] || fail "post-recovery first hard-down sample must not fire"

  echo "  PASS: final confirmation blocks if R5C recovers before reboot"
}

# ---------------------------------------------------------------------------
# Test 14: ASUS final-confirmation failure consumes the threshold window.
# ---------------------------------------------------------------------------
{
  d="$TMPROOT/final-confirmation-asus-lost"
  mkdir -p "$d"
  write_fake_commands "$d"
  write_health_rows "$d"
  touch "$d/sentinel"

  mv "$d/fakebin/ssh" "$d/fakebin/ssh.real"
  cat > "$d/fakebin/ssh" <<EOF
#!/usr/bin/env bash
printf 'ssh %s\n' "\$*" >> "$d/ssh-commands.log"
if [[ "\$*" == *"/bin/true"* ]]; then
  count=0
  [[ -f "$d/ssh-probe-count" ]] && count="\$(<"$d/ssh-probe-count")"
  count=\$((count + 1))
  printf '%d' "\$count" > "$d/ssh-probe-count"
  if [[ \$count -eq 4 ]]; then
    exit 255
  fi
fi
exit 0
EOF
  chmod +x "$d/fakebin/ssh"

  for i in $(seq 1 3); do
    ts="$(printf '2026-06-22T10:35:%02dZ' "$i")"
    run_recovery "$d" "$ts"
  done
  grep -q '"action":"blocked_final_confirmation_failed"' "$d/recovery.jsonl" || fail "ASUS lost during final confirmation should log blocked_final_confirmation_failed"

  run_recovery "$d" "2026-06-22T10:36:00Z"
  [[ "$(count_actions "$d" reboot_attempted)" == "0" ]] || fail "ASUS final-confirmation failure must consume threshold window"
  if [[ -e "$d/ssh-commands.log" ]]; then
    grep -q '/sbin/reboot' "$d/ssh-commands.log" && fail "ASUS final-confirmation failure must not invoke SSH reboot"
  fi

  echo "  PASS: ASUS final-confirmation failure consumes threshold window"
}

# ---------------------------------------------------------------------------
# Test 15: timeout probe output is decoded and logged instead of crashing.
# ---------------------------------------------------------------------------
{
  d="$TMPROOT/timeout-output"
  mkdir -p "$d"
  write_fake_commands "$d"
  write_health_rows "$d"

  cat > "$d/fakebin/ping" <<'EOF'
#!/usr/bin/env bash
printf 'partial timeout stderr\n' >&2
sleep 5
EOF
  chmod +x "$d/fakebin/ping"

  K2B_R5C_AUTORECOVERY_PING_TIMEOUT_MS=200 \
  run_recovery "$d" "2026-06-22T10:40:00Z"

  [[ -f "$d/recovery.jsonl" ]] || fail "timeout output should be logged, not crash the tick"
  grep -q 'command timed out' "$d/recovery.jsonl" || fail "timeout output should include command timed out"

  echo "  PASS: timeout probe output is decoded safely"
}

# ---------------------------------------------------------------------------
# Test 16: wrapper scrubs inherited fake executable overrides in normal mode.
# ---------------------------------------------------------------------------
{
  d="$TMPROOT/wrapper-scrub"
  mkdir -p "$d"
  write_fake_commands "$d"
  write_health_rows "$d"
  for bin in ping curl ssh; do
    cat > "$d/fakebin/$bin.bad" <<EOF
#!/usr/bin/env bash
touch "$d/bad-$bin-ran"
exit 0
EOF
    chmod +x "$d/fakebin/$bin.bad"
  done
  cat > "$d/watchdog.env" <<EOF
MIHOMO_API_BASE=http://192.168.9.1:9090
MIHOMO_API_SECRET=test-secret
K2B_R5C_AUTORECOVERY_STATE_FILE=$d/state.json
K2B_R5C_AUTORECOVERY_LOG=$d/recovery.jsonl
K2B_R5C_AUTORECOVERY_EVIDENCE_DIR=$d/evidence
K2B_R5C_AUTORECOVERY_SENTINEL=$d/sentinel
K2B_R5C_AUTORECOVERY_ASUS_SSH_KEY=$d/fakekey
K2B_R5C_AUTORECOVERY_PING_TIMEOUT_MS=200
K2B_R5C_AUTORECOVERY_HTTP_TIMEOUT_S=1
K2B_R5C_AUTORECOVERY_SSH_TIMEOUT_S=2
EOF

  PATH="$d/fakebin:$PATH" \
  K2B_R5C_AUTORECOVERY_PING_BIN="$d/fakebin/ping.bad" \
  K2B_R5C_AUTORECOVERY_CURL_BIN="$d/fakebin/curl.bad" \
  K2B_R5C_AUTORECOVERY_SSH_BIN="$d/fakebin/ssh.bad" \
  K2B_R5C_AUTORECOVERY_TEST_MODE=1 \
  K2B_R5C_AUTORECOVERY_TEST_ALLOW_ANY_SSH_KEY=1 \
  K2B_R5C_AUTORECOVERY_TEST_ALLOW_FAKE_PROBE_BINS=1 \
  K2B_R5C_AUTORECOVERY_TEST_ALLOW_FAKE_SSH_BIN=1 \
  K2B_ROUTER_WATCHDOG_APP_DIR="$d/app" \
  K2B_ROUTER_WATCHDOG_LOG_DIR="$d/logs" \
  K2B_ROUTER_WATCHDOG_ENV_FILE="$d/watchdog.env" \
  bash "$SRC_DIR/bin/r5c-autorecovery.sh" --now "2026-06-22T10:44:00Z"

  [[ -f "$d/recovery.jsonl" ]] || fail "normal wrapper mode should still log a tick"
  [[ ! -e "$d/bad-ping-ran" ]] || fail "normal wrapper must scrub inherited fake ping"
  [[ ! -e "$d/bad-curl-ran" ]] || fail "normal wrapper must scrub inherited fake curl"
  [[ ! -e "$d/bad-ssh-ran" ]] || fail "normal wrapper must scrub inherited fake ssh"

  echo "  PASS: wrapper scrubs inherited fake executable overrides"
}

# ---------------------------------------------------------------------------
# Test 17: wrapper loads R5C env overrides from the watchdog env file.
# ---------------------------------------------------------------------------
{
  d="$TMPROOT/wrapper-env"
  mkdir -p "$d"
  write_fake_commands "$d"
  write_health_rows "$d"
  cat > "$d/fakebin/ssh.bad" <<EOF
#!/usr/bin/env bash
touch "$d/bad-ssh-ran"
exit 0
EOF
  chmod +x "$d/fakebin/ssh.bad"
  for bin in ping curl; do
    cat > "$d/fakebin/$bin.bad" <<EOF
#!/usr/bin/env bash
touch "$d/bad-$bin-ran"
exit 0
EOF
    chmod +x "$d/fakebin/$bin.bad"
  done
  cat > "$d/watchdog.env" <<EOF
MIHOMO_API_BASE=http://192.168.9.1:9090
MIHOMO_API_SECRET=test-secret
K2B_R5C_AUTORECOVERY_STATE_FILE=$d/state-from-env.json
K2B_R5C_AUTORECOVERY_LOG=$d/log-from-env.jsonl
K2B_R5C_AUTORECOVERY_EVIDENCE_DIR=$d/evidence-from-env
K2B_R5C_AUTORECOVERY_SENTINEL=$d/sentinel
K2B_R5C_AUTORECOVERY_PING_BIN=$d/fakebin/ping.bad
K2B_R5C_AUTORECOVERY_CURL_BIN=$d/fakebin/curl.bad
K2B_R5C_AUTORECOVERY_SSH_BIN=$d/fakebin/ssh.bad
K2B_R5C_AUTORECOVERY_ASUS_SSH_KEY=$d/fakekey
K2B_R5C_AUTORECOVERY_THRESHOLD=3
K2B_R5C_AUTORECOVERY_COOLDOWN_MINUTES=30
EOF

  PATH="$d/fakebin:$PATH" \
  K2B_R5C_AUTORECOVERY_PING_BIN="$d/fakebin/ping" \
  K2B_R5C_AUTORECOVERY_CURL_BIN="$d/fakebin/curl" \
  K2B_R5C_AUTORECOVERY_SSH_BIN="$d/fakebin/ssh" \
  K2B_R5C_AUTORECOVERY_WRAPPER_TEST_MODE=1 \
  K2B_R5C_AUTORECOVERY_TEST_MODE=1 \
  K2B_R5C_AUTORECOVERY_TEST_ALLOW_ANY_SSH_KEY=1 \
  K2B_R5C_AUTORECOVERY_TEST_ALLOW_FAKE_PROBE_BINS=1 \
  K2B_R5C_AUTORECOVERY_TEST_ALLOW_FAKE_SSH_BIN=1 \
  K2B_ROUTER_WATCHDOG_APP_DIR="$d/app" \
  K2B_ROUTER_WATCHDOG_LOG_DIR="$d/logs" \
  K2B_ROUTER_WATCHDOG_ENV_FILE="$d/watchdog.env" \
  bash "$SRC_DIR/bin/r5c-autorecovery.sh" --now "2026-06-22T10:45:00Z"

  [[ -f "$d/state-from-env.json" ]] || fail "wrapper must honor state path from env file"
  [[ -f "$d/log-from-env.jsonl" ]] || fail "wrapper must honor log path from env file"
  [[ -f "$d/ssh-commands.log" ]] || fail "wrapper should use process-provided test SSH binary"
  [[ ! -e "$d/bad-ping-ran" ]] || fail "wrapper must ignore PING_BIN from env file"
  [[ ! -e "$d/bad-curl-ran" ]] || fail "wrapper must ignore CURL_BIN from env file"
  [[ ! -e "$d/bad-ssh-ran" ]] || fail "wrapper must ignore SSH_BIN from env file"

  echo "  PASS: wrapper loads R5C env overrides from watchdog env file"
}

# ---------------------------------------------------------------------------
# Test 18: missing send-alert writes disabled alert state to the R5C alert log.
# ---------------------------------------------------------------------------
{
  d="$TMPROOT/wrapper-alerting-disabled"
  mkdir -p "$d/staged-bin"
  write_fake_commands "$d"
  write_health_rows "$d"
  touch "$d/sentinel"
  cp "$SRC_DIR/bin/r5c-autorecovery.sh" "$d/staged-bin/"
  cp "$SRC_DIR/bin/r5c-autorecovery.py" "$d/staged-bin/"
  chmod +x "$d/staged-bin/r5c-autorecovery.sh"
  cat > "$d/watchdog.env" <<EOF
MIHOMO_API_BASE=http://192.168.9.1:9090
MIHOMO_API_SECRET=test-secret
K2B_R5C_AUTORECOVERY_STATE_FILE=$d/state.json
K2B_R5C_AUTORECOVERY_LOG=$d/recovery.jsonl
K2B_R5C_AUTORECOVERY_ALERTS_LOG=$d/r5c-alerts.jsonl
K2B_R5C_AUTORECOVERY_EVIDENCE_DIR=$d/evidence
K2B_R5C_AUTORECOVERY_SENTINEL=$d/sentinel
K2B_R5C_AUTORECOVERY_PING_BIN=$d/fakebin/ping
K2B_R5C_AUTORECOVERY_CURL_BIN=$d/fakebin/curl
K2B_R5C_AUTORECOVERY_SSH_BIN=$d/fakebin/ssh
K2B_R5C_AUTORECOVERY_ASUS_SSH_KEY=$d/fakekey
K2B_R5C_AUTORECOVERY_THRESHOLD=3
K2B_R5C_AUTORECOVERY_COOLDOWN_MINUTES=30
EOF

  PATH="$d/fakebin:$PATH" \
  K2B_R5C_AUTORECOVERY_WRAPPER_TEST_MODE=1 \
  K2B_R5C_AUTORECOVERY_TEST_MODE=1 \
  K2B_R5C_AUTORECOVERY_TEST_ALLOW_ANY_SSH_KEY=1 \
  K2B_R5C_AUTORECOVERY_TEST_ALLOW_FAKE_PROBE_BINS=1 \
  K2B_R5C_AUTORECOVERY_TEST_ALLOW_FAKE_SSH_BIN=1 \
  K2B_ROUTER_WATCHDOG_APP_DIR="$d/app" \
  K2B_ROUTER_WATCHDOG_LOG_DIR="$d/logs" \
  K2B_ROUTER_WATCHDOG_ENV_FILE="$d/watchdog.env" \
  bash "$d/staged-bin/r5c-autorecovery.sh" --now "2026-06-22T10:46:00Z" 2>"$d/wrapper.err"

  grep -q '"type":"alerting_disabled"' "$d/r5c-alerts.jsonl" || fail "missing send-alert should write alerting_disabled to R5C alert log"
  ! grep -q '"action":"alerting_disabled"' "$d/recovery.jsonl" || fail "alerting_disabled should not be written to the R5C decision log"

  echo "  PASS: missing send-alert writes alerting_disabled to R5C alert log"
}

# ---------------------------------------------------------------------------
# Test 19: launchd plist cadence and installer fleet membership.
# ---------------------------------------------------------------------------
{
  python3 - "$R5C_PLIST" "$R5C_EXPECTED_INTERVAL" <<'PY' || fail "R5C autorecovery launchd cadence incorrect"
import plistlib
import sys

with open(sys.argv[1], "rb") as f:
    plist = plistlib.load(f)

expected = int(sys.argv[2])
start = plist.get("StartInterval")
throttle = plist.get("ThrottleInterval")

if start != expected:
    raise SystemExit(f"StartInterval expected {expected}, got {start!r}")
if throttle != expected:
    raise SystemExit(f"ThrottleInterval expected {expected}, got {throttle!r}")
if throttle < start:
    raise SystemExit(f"ThrottleInterval must not be lower than StartInterval")
PY
  echo "  PASS: R5C autorecovery launchd cadence is 60 seconds"

  d="$TMPROOT/install-fleet"
  mkdir -p "$d/fakebin" "$d/launch-agents"
  app_dir="$d/app"
  log_dir="$d/log"
  mkdir -p "$app_dir/bin" "$log_dir"
  env_file="$d/watchdog.env"
  cat > "$env_file" <<'EOF'
TELEGRAM_BOT_TOKEN=test-token
KEITH_CHAT_ID=1
MIHOMO_API_BASE=http://127.0.0.1:9
MIHOMO_API_SECRET=test-secret
MIHOMO_OPENAI_GROUP=test-group
EOF
  chmod 600 "$env_file"

  install_src="$d/install-src"
  mkdir -p "$install_src/scripts/router-watchdog/bin"
  cp -a "$REPO_ROOT/scripts/router-watchdog/install.sh" "$install_src/scripts/router-watchdog/"
  cp -a "$REPO_ROOT/scripts/router-watchdog/bin/." "$install_src/scripts/router-watchdog/bin/"
  cp -a "$REPO_ROOT/launchd" "$install_src/launchd"

  cat > "$d/fakebin/launchctl" <<'EOF'
#!/usr/bin/env bash
exit 0
EOF
  chmod +x "$d/fakebin/launchctl"

  PATH="$d/fakebin:$PATH" \
  K2B_ROUTER_WATCHDOG_APP_DIR="$app_dir" \
  K2B_ROUTER_WATCHDOG_LOG_DIR="$log_dir" \
  K2B_ROUTER_WATCHDOG_ENV_FILE="$env_file" \
  K2B_ROUTER_WATCHDOG_LAUNCH_AGENTS_DIR="$d/launch-agents" \
  K2B_ROUTER_WATCHDOG_DRY_RUN_SKIP_CHECKS=1 \
  bash "$install_src/scripts/router-watchdog/install.sh" >"$d/install.out" 2>"$d/install.err"

  [[ -f "$d/launch-agents/com.k2b.router-r5c-autorecovery.plist" ]] || fail "installer must deploy R5C autorecovery plist"
  [[ -f "$app_dir/bin/r5c-autorecovery.py" ]] || fail "installer must copy r5c-autorecovery.py"
  [[ -f "$app_dir/bin/r5c-autorecovery.sh" ]] || fail "installer must copy r5c-autorecovery.sh"
  [[ -x "$app_dir/bin/r5c-autorecovery.py" ]] || fail "r5c-autorecovery.py must be executable after install"
  [[ -x "$app_dir/bin/r5c-autorecovery.sh" ]] || fail "r5c-autorecovery.sh must be executable after install"

  echo "  PASS: installer deploys R5C autorecovery fleet files"
}

# ---------------------------------------------------------------------------
# Test 20: install rollback removes the new plist when launchctl bootstrap fails.
# ---------------------------------------------------------------------------
{
  d="$TMPROOT/install-rollback"
  mkdir -p "$d/fakebin" "$d/launch-agents"
  app_dir="$d/app"
  log_dir="$d/log"
  mkdir -p "$app_dir/bin" "$log_dir"
  env_file="$d/watchdog.env"
  cat > "$env_file" <<'EOF'
TELEGRAM_BOT_TOKEN=test-token
KEITH_CHAT_ID=1
MIHOMO_API_BASE=http://127.0.0.1:9
MIHOMO_API_SECRET=test-secret
MIHOMO_OPENAI_GROUP=test-group
EOF
  chmod 600 "$env_file"

  printf 'old-bin-version' > "$app_dir/bin/marker.txt"
  manifest_file="$app_dir/install-manifest.sha256"
  rm -f "$manifest_file"

  install_src="$d/install-src"
  mkdir -p "$install_src/scripts/router-watchdog/bin"
  cp -a "$REPO_ROOT/scripts/router-watchdog/install.sh" "$install_src/scripts/router-watchdog/"
  cp -a "$REPO_ROOT/scripts/router-watchdog/bin/." "$install_src/scripts/router-watchdog/bin/"
  cp -a "$REPO_ROOT/launchd" "$install_src/launchd"
  printf 'new-bin-version' > "$install_src/scripts/router-watchdog/bin/marker.txt"

  for plist in com.k2b.router-watchdog.plist com.k2b.router-daily-rollup.plist \
               com.k2b.router-node-score.plist com.k2b.router-leaf-optimizer.plist \
               com.k2b.router-private-vpn-watchdog.plist \
               com.k2b.router-digest.plist \
               com.k2b.router-r5c-autorecovery.plist; do
    printf 'OLD-%s\n' "$plist" > "$d/launch-agents/$plist"
  done

  counter_file="$d/bootstrap-count"
  cat > "$d/fakebin/launchctl" <<EOF
#!/usr/bin/env bash
if [[ "\${1:-}" == "bootstrap" ]]; then
  count=0
  [[ -f "$counter_file" ]] && count="\$(<"$counter_file")"
  count=\$((count + 1))
  printf '%d' "\$count" > "$counter_file"
  if [[ \$count -eq 3 ]]; then
    echo "fake launchctl: bootstrap failed for \${3:-unknown} (simulated)" >&2
    exit 5
  fi
fi
exit 0
EOF
  chmod +x "$d/fakebin/launchctl"

  set +e
  PATH="$d/fakebin:$PATH" \
  K2B_ROUTER_WATCHDOG_APP_DIR="$app_dir" \
  K2B_ROUTER_WATCHDOG_LOG_DIR="$log_dir" \
  K2B_ROUTER_WATCHDOG_ENV_FILE="$env_file" \
  K2B_ROUTER_WATCHDOG_LAUNCH_AGENTS_DIR="$d/launch-agents" \
  K2B_ROUTER_WATCHDOG_DRY_RUN_SKIP_CHECKS=1 \
  bash "$install_src/scripts/router-watchdog/install.sh" >"$d/install.out" 2>"$d/install.err"
  rc=$?
  set -e

  [[ $rc -ne 0 ]] || fail "rollback test: install should fail when launchctl bootstrap fails"
  actual="$(<"$app_dir/bin/marker.txt")"
  [[ "$actual" == "old-bin-version" ]] || fail "rollback test: bin was not restored"
  for plist in com.k2b.router-watchdog.plist com.k2b.router-daily-rollup.plist \
               com.k2b.router-node-score.plist com.k2b.router-leaf-optimizer.plist \
               com.k2b.router-private-vpn-watchdog.plist \
               com.k2b.router-digest.plist \
               com.k2b.router-r5c-autorecovery.plist; do
    actual_plist="$(<"$d/launch-agents/$plist")"
    [[ "$actual_plist" == "OLD-$plist" ]] || fail "rollback test: $plist was not restored"
  done
  [[ ! -f "$manifest_file" ]] || fail "rollback test: MANIFEST should not exist after rollback"

  echo "  PASS: installer rollback restores R5C autorecovery fleet files"
}

echo "router-watchdog-r5c-autorecovery.test.sh: all tests passed"
