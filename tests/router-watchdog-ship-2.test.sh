#!/usr/bin/env bash
# tests/router-watchdog-ship-2.test.sh
# Regression tests for the 4 deferred Codex findings closed by Ship 2:
#   HIGH-3 install.sh transactional bootout/bootstrap with rollback
#   MED-4  state.json writes use flock
#   MED-5  partition-queue writes use flock
#   MED-6  is covered in tests/router-watchdog-phase234.test.sh (Telegram fake-curl path)

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC_DIR="$REPO_ROOT/scripts/router-watchdog"
INSTALL="$SRC_DIR/install.sh"
STATE_MACHINE="$SRC_DIR/bin/state-machine.py"
PARTITION_QUEUE="$SRC_DIR/bin/partition-queue.py"

TMPROOT="$(mktemp -d)"
trap 'rm -rf "$TMPROOT"' EXIT

fail() { echo "FAIL: $*" >&2; exit 1; }

write_results() {
  local out="$1" failing="${2:-}"
  python3 - "$out" "$failing" <<'PY'
import json, sys
out, failing = sys.argv[1], sys.argv[2]
names = [
    "router_reachable","mihomo_api","openai_node","chatgpt_https","chatgpt_ws",
    "claude_https","telegram_api","tailscale_direct","syncthing_process",
    "pm2_daemon","pm2_services",
]
fail_set = {x for x in failing.split(",") if x}
results = []
for name in names:
    ok = name not in fail_set
    results.append({
        "name": name, "ok": ok,
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

echo "=== router-watchdog-ship-2.test.sh ==="

# ---------------------------------------------------------------------------
# Test HIGH-3a: install.sh rolls back when launchctl bootstrap fails partway.
#
# Stub launchctl to fail bootstrap on the 3rd plist. install.sh must:
#   - exit non-zero
#   - restore the prior installed bin tree from backup
#   - restore the prior plist files from backup
#   - leave $MANIFEST at the prior value (or absent)
# ---------------------------------------------------------------------------
{
  d="$TMPROOT/install-rollback"
  mkdir -p "$d/fakebin" "$d/launchd" "$d/launch-agents"
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

  # Seed install_bin with a "previous" snapshot so we can verify rollback.
  printf 'old-bin-version' > "$app_dir/bin/marker.txt"
  manifest_file="$app_dir/install-manifest.sha256"
  rm -f "$manifest_file"

  # Build a fake source tree with 5 plists and a few bin files.
  # Use the actual launchd dir from the repo (it has all 5 plists).
  cp -a "$REPO_ROOT/launchd" "$d/launchd-source"

  # Stage a fresh INSTALL source that matches the repo layout but with new bin content.
  install_src="$d/install-src"
  mkdir -p "$install_src/scripts/router-watchdog/bin"
  cp -a "$REPO_ROOT/scripts/router-watchdog/install.sh" "$install_src/scripts/router-watchdog/"
  cp -a "$REPO_ROOT/scripts/router-watchdog/bin/." "$install_src/scripts/router-watchdog/bin/"
  printf 'new-bin-version' > "$install_src/scripts/router-watchdog/bin/marker.txt"
  cp -a "$REPO_ROOT/launchd" "$install_src/launchd"

  # Seed the existing plist files (so we have "prior" plists to roll back to).
  for plist in com.k2b.router-watchdog.plist com.k2b.router-daily-rollup.plist \
               com.k2b.router-node-score.plist com.k2b.router-leaf-optimizer.plist \
               com.k2b.router-digest.plist; do
    printf 'OLD-%s\n' "$plist" > "$d/launch-agents/$plist"
  done

  # Stub launchctl: succeed for the first 2 bootstrap calls, fail on the 3rd.
  cat > "$d/fakebin/launchctl" <<EOF
#!/usr/bin/env bash
counter_file="$d/bootstrap-count"
if [[ "\${1:-}" == "bootstrap" ]]; then
  count=0
  [[ -f "\$counter_file" ]] && count="\$(<"\$counter_file")"
  count=\$((count + 1))
  printf '%d' "\$count" > "\$counter_file"
  if [[ \$count -eq 3 ]]; then
    echo "fake launchctl: bootstrap failed for \${3:-unknown} (simulated)" >&2
    exit 5
  fi
fi
exit 0
EOF
  chmod +x "$d/fakebin/launchctl"

  # Run install.sh from the staged source tree, with stubbed launchctl.
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

  [[ $rc -ne 0 ]] || { cat "$d/install.err" >&2; fail "HIGH-3: install.sh should exit non-zero on launchctl bootstrap failure (got rc=$rc)"; }

  # Bin must be restored: marker.txt should still say "old-bin-version".
  actual="$(<"$app_dir/bin/marker.txt")"
  if [[ "$actual" != "old-bin-version" ]]; then
    echo "--- install.err ---" >&2
    cat "$d/install.err" >&2
    echo "--- install.out ---" >&2
    cat "$d/install.out" >&2
    echo "--- app/bin contents ---" >&2
    ls -la "$app_dir/bin" >&2
    fail "HIGH-3: install bin was not rolled back (marker.txt='$actual', expected 'old-bin-version')"
  fi

  # Each plist must be restored to its OLD-* content.
  for plist in com.k2b.router-watchdog.plist com.k2b.router-daily-rollup.plist \
               com.k2b.router-node-score.plist com.k2b.router-leaf-optimizer.plist \
               com.k2b.router-digest.plist; do
    actual_plist="$(<"$d/launch-agents/$plist")"
    expected_plist="OLD-$plist"
    [[ "$actual_plist" == "$expected_plist" ]] || fail "HIGH-3: plist $plist was not rolled back (got '$actual_plist', expected '$expected_plist')"
  done

  # MANIFEST must NOT be advanced.
  if [[ -f "$manifest_file" ]]; then
    fail "HIGH-3: \$MANIFEST should NOT exist after rollback (was created at $manifest_file)"
  fi

  # Backup directory should be cleaned up by the EXIT trap.
  remaining_backups="$(find "$app_dir" -maxdepth 1 -type d -name '.install-backup.*' 2>/dev/null | wc -l | tr -d ' ')"
  [[ "$remaining_backups" == "0" ]] || fail "HIGH-3: backup dir not cleaned up ($remaining_backups remaining under $app_dir)"

  # The error message must include the rollback signal so /sync logs are useful.
  grep -q 'rolled back' "$d/install.err" || { cat "$d/install.err" >&2; fail "HIGH-3: install.err should mention rollback"; }

  echo "  PASS: HIGH-3 launchctl bootstrap failure rolls back install"
}

# ---------------------------------------------------------------------------
# Test HIGH-3b: clean install.sh run still creates MANIFEST and leaves no
# backup dir behind (the rollback wiring must not break the happy path).
# ---------------------------------------------------------------------------
{
  d="$TMPROOT/install-happy-path"
  mkdir -p "$d/fakebin" "$d/launchd" "$d/launch-agents"
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

  manifest_file="$app_dir/install-manifest.sha256"
  [[ -f "$manifest_file" ]] || { cat "$d/install.err" >&2; fail "HIGH-3b: happy-path install must write \$MANIFEST"; }

  remaining_backups="$(find "$app_dir" -maxdepth 1 -type d -name '.install-backup.*' 2>/dev/null | wc -l | tr -d ' ')"
  [[ "$remaining_backups" == "0" ]] || fail "HIGH-3b: backup dir not cleaned up on happy path ($remaining_backups remaining)"

  echo "  PASS: HIGH-3 happy path still writes manifest and cleans up backup"
}

# ---------------------------------------------------------------------------
# Test MED-4: N concurrent state-machine.py invocations against the same
# state.json must not lose increments. Each tick increments
# chatgpt_https.consecutive_fails by 1 (because the result feeds in
# chatgpt_https failing). With the flock, the final consecutive_fails value
# MUST equal N. Without the flock, a read-modify-write race between any
# two ticks could collapse two increments into one and the final value
# would drop below N.
# ---------------------------------------------------------------------------
{
  d="$TMPROOT/state-machine-flock"
  mkdir -p "$d"
  state="$d/state.json"
  results="$d/results.json"
  write_results "$results" "chatgpt_https"

  N=8
  pids=()
  for i in $(seq 1 "$N"); do
    # Each tick has a unique timestamp so transition_checks treats them as
    # successive ticks. The lock makes them serialize their RMW; the order
    # doesn't matter for the count.
    ts="$(printf '2026-05-03T00:%02d:00Z' "$i")"
    python3 "$STATE_MACHINE" \
      --state-file "$state" \
      --results-file "$results" \
      --timestamp "$ts" \
      --backoff "30s,2m,6m,24h" \
      --alerts-file "$d/alerts-$i.jsonl" \
      --partition-actions-file "$d/actions-$i.jsonl" \
      --health-log "$d/health-$i.jsonl" >"$d/out-$i" 2>"$d/err-$i" &
    pids+=("$!")
  done
  for pid in "${pids[@]}"; do
    wait "$pid" || { cat "$d"/err-* >&2; fail "MED-4: state-machine concurrent invocation failed"; }
  done

  python3 - "$state" "$N" <<'PY' || fail "MED-4: concurrent ticks lost increments"
import json
import sys

with open(sys.argv[1], encoding="utf-8") as f:
    state = json.load(f)
expected = int(sys.argv[2])
chat = state.get("chatgpt_https") or {}
got = chat.get("consecutive_fails")
assert got == expected, (
    f"MED-4: expected consecutive_fails={expected} after {expected} concurrent "
    f"ticks (flock serializes RMW); got {got!r}; state={chat}"
)
PY
  echo "  PASS: MED-4 ${N} concurrent state-machine ticks all serialize their RMW (flock)"
}

# ---------------------------------------------------------------------------
# Test MED-5: same shape for partition-queue.py. Two concurrent apply calls
# with different actions must not lose either.
# ---------------------------------------------------------------------------
{
  d="$TMPROOT/partition-queue-flock"
  mkdir -p "$d"
  queue="$d/pending-partition-events.jsonl"
  alerts="$d/alerts.jsonl"

  cat > "$d/actions-a.jsonl" <<'EOF'
{"type":"append_partition","start":"2026-05-03T00:00:00Z","timestamp":"2026-05-03T00:00:00Z"}
EOF
  cat > "$d/actions-b.jsonl" <<'EOF'
{"type":"append_partition","start":"2026-05-03T01:00:00Z","timestamp":"2026-05-03T01:00:00Z"}
EOF

  python3 "$PARTITION_QUEUE" apply \
    --queue-file "$queue" \
    --actions-file "$d/actions-a.jsonl" \
    --alerts-file "$alerts" &
  pid_a=$!
  python3 "$PARTITION_QUEUE" apply \
    --queue-file "$queue" \
    --actions-file "$d/actions-b.jsonl" \
    --alerts-file "$alerts" &
  pid_b=$!
  wait "$pid_a"
  wait "$pid_b"

  # Both partition events must end up in the queue.
  python3 - "$queue" <<'PY' || fail "MED-5: queue should contain both append_partition events from concurrent apply calls"
import json
import sys

with open(sys.argv[1], encoding="utf-8") as f:
    items = [json.loads(line) for line in f if line.strip()]
starts = sorted(item.get("start") for item in items if item.get("type") == "network_partition_started")
assert "2026-05-03T00:00:00Z" in starts, f"first event missing: {starts}"
assert "2026-05-03T01:00:00Z" in starts, f"second event missing: {starts}"
PY

  # Sanity: flock kept the writes from interleaving (no torn JSON lines).
  python3 - "$queue" <<'PY' || fail "MED-5: queue file has torn JSON (flock did not serialize writes)"
import json
import sys

with open(sys.argv[1], encoding="utf-8") as f:
    for i, line in enumerate(f, 1):
        line = line.strip()
        if not line:
            continue
        try:
            json.loads(line)
        except Exception as exc:
            raise SystemExit(f"line {i} not valid JSON: {exc!r}; line={line!r}")
PY
  echo "  PASS: MED-5 concurrent partition-queue applies do not interleave"
}

# ---------------------------------------------------------------------------
# Test MED-5b: append-vs-drain race. The original lost-update shape for
# partition-queue.py is more dangerous on drain than on append: drain
# reads pending entries, emits recovery alerts, then truncates the queue.
# Without the lock, an append landing between read-and-truncate can be
# silently lost. Codex review surfaced this gap in the basic test above.
# ---------------------------------------------------------------------------
{
  d="$TMPROOT/partition-queue-drain-race"
  mkdir -p "$d"
  queue="$d/pending-partition-events.jsonl"
  alerts="$d/alerts.jsonl"

  # Pre-seed an entry so drain has work to do.
  printf '%s\n' '{"type":"network_partition_started","start":"2026-05-03T00:00:00Z","detected_at":"2026-05-03T00:00:00Z"}' > "$queue"

  cat > "$d/actions-drain.jsonl" <<'EOF'
{"type":"drain_partition","end":"2026-05-03T01:00:00Z"}
EOF
  cat > "$d/actions-append.jsonl" <<'EOF'
{"type":"append_partition","start":"2026-05-03T02:00:00Z","timestamp":"2026-05-03T02:00:00Z"}
EOF

  python3 "$PARTITION_QUEUE" apply \
    --queue-file "$queue" \
    --actions-file "$d/actions-drain.jsonl" \
    --alerts-file "$alerts" &
  pid_drain=$!
  python3 "$PARTITION_QUEUE" apply \
    --queue-file "$queue" \
    --actions-file "$d/actions-append.jsonl" \
    --alerts-file "$alerts" &
  pid_append=$!
  wait "$pid_drain"
  wait "$pid_append"

  # Final state: drain emitted a recovery alert, and the post-drain append
  # remains in the queue (or, if append landed before drain, the queue is
  # now truncated AND the recovery alert mentions both). Either ordering
  # is valid; the invariant we test is "no lost data": the queue + alerts
  # together account for the pre-existing event AND the appended event.
  python3 - "$queue" "$alerts" <<'PY' || fail "MED-5b: drain-vs-append race lost data"
import json
import sys

def load_jsonl(p):
    try:
        with open(p, encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]
    except FileNotFoundError:
        return []

queue = load_jsonl(sys.argv[1])
alerts = load_jsonl(sys.argv[2])

queue_starts = [item.get("start") for item in queue if item.get("type") == "network_partition_started"]
alert_msgs = [a.get("message", "") for a in alerts if a.get("type") == "network_partition_recovered"]

# The pre-seeded 00:00 event must be accounted for: it is either still in
# the queue (drain ran first, append landed after) or it was drained and
# appears in a recovery alert.
seeded_in_queue = "2026-05-03T00:00:00Z" in queue_starts
seeded_in_alerts = any("2026-05-03T00:00:00Z" in m for m in alert_msgs)
assert seeded_in_queue or seeded_in_alerts, (
    f"MED-5b: pre-seeded 00:00 event lost; queue starts={queue_starts}, "
    f"alert msgs={alert_msgs}"
)

# The 02:00 append must also still be on disk, either in the queue (most
# orderings) or, if drain ran AFTER append, in the alert (single recovery
# covering both). With the flock the queue cannot be torn or truncated
# mid-RMW.
appended_in_queue = "2026-05-03T02:00:00Z" in queue_starts
appended_in_alerts = any("2026-05-03T02:00:00Z" in m for m in alert_msgs)
assert appended_in_queue or appended_in_alerts, (
    f"MED-5b: 02:00 append lost; queue starts={queue_starts}, "
    f"alert msgs={alert_msgs}"
)
PY
  echo "  PASS: MED-5b drain-vs-append race preserves both events"
}

echo "router-watchdog-ship-2.test.sh: all tests passed"
