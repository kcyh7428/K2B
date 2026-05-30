#!/usr/bin/env bash
# tests/orchestrator-dispatch.test.sh
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
export PYTHONPATH="$REPO${PYTHONPATH:+:$PYTHONPATH}"

TMPDIR="$(mktemp -d)"
trap 'rm -rf "$TMPDIR"' EXIT INT TERM

VAULT="$TMPDIR/vault"
mkdir -p "$VAULT/raw/orchestrator-results" "$VAULT/System/orchestrator"
DB="$TMPDIR/orch.sqlite"

RECORDER_OUT="$TMPDIR/recorder_out.txt"
touch "$RECORDER_OUT"
RECORDER="$TMPDIR/recorder.sh"
cat > "$RECORDER" <<EOF
#!/bin/bash
echo "\$@" >> "$RECORDER_OUT"
EOF
chmod +x "$RECORDER"

export K2B_VAULT_PATH="$VAULT"
export K2B_ORCH_DB="$DB"
export K2B_ORCH_TELEGRAM_CMD="$RECORDER"

PASS=0
FAIL=0

report() {
  if [[ $1 -eq 0 ]]; then
    echo "PASS: $2"
    PASS=$((PASS + 1))
  else
    echo "FAIL: $2"
    FAIL=$((FAIL + 1))
  fi
}

CLI="bash $REPO/scripts/k2b-orchestrator.sh"

# --- init ---
$CLI init >/dev/null

# === BLOCKED (dirty git) ===
echo "=== BLOCKED (dirty git) ==="
K2BI_WS="$TMPDIR/k2bi_dirty"
mkdir -p "$K2BI_WS"
git -C "$K2BI_WS" init -q
echo "dirty" > "$K2BI_WS/file.txt"
export K2B_ORCH_K2BI_WORKSPACE="$K2BI_WS"
TID_BLOCK=$($CLI add --profile k2bi --command-key test-echo-readonly --success ok --permissions analyst-command | awk '{print $NF}')
$CLI poll-once >/dev/null
STATUS_BLOCK=$($CLI show "$TID_BLOCK" --json | python3 -c "import sys,json; print(json.load(sys.stdin)['status'])")
report $? "add succeeded"
[[ "$STATUS_BLOCK" == "blocked" ]]; report $? "status is blocked"
REASON_BLOCK=$($CLI show "$TID_BLOCK" --json | python3 -c "import sys,json; print(json.load(sys.stdin).get('blocker_reason',''))")
[[ "$REASON_BLOCK" == *"dirty"* ]]; report $? "blocker_reason mentions dirty"
MSG_BLOCK=$(cat "$RECORDER_OUT")
[[ "$MSG_BLOCK" == *"BLOCKED"* ]]; report $? "recorder shows BLOCKED"
[[ ! -f "$VAULT/raw/orchestrator-results/${TID_BLOCK}-k2bi-smoke.md" ]]; report $? "no artifact for blocked task"
unset K2B_ORCH_K2BI_WORKSPACE
> "$RECORDER_OUT"

# === PASS (clean git, mock command) ===
echo "=== PASS (clean git, mock command) ==="
K2BI_WS="$TMPDIR/k2bi_clean"
mkdir -p "$K2BI_WS"
git -C "$K2BI_WS" init -q
echo "clean" > "$K2BI_WS/file.txt"
git -C "$K2BI_WS" add file.txt >/dev/null
GIT_AUTHOR_NAME="t" GIT_AUTHOR_EMAIL="t@t" GIT_COMMITTER_NAME="t" GIT_COMMITTER_EMAIL="t@t" git -C "$K2BI_WS" commit -m "init" -q
export K2B_ORCH_K2BI_WORKSPACE="$K2BI_WS"
TID_PASS=$($CLI add --profile k2bi --command-key test-echo-readonly --success ok --permissions analyst-command | awk '{print $NF}')
$CLI poll-once >/dev/null
# Wait up to ~15s for terminal status
for i in $(seq 1 30); do
  STATUS_PASS=$($CLI show "$TID_PASS" --json | python3 -c "import sys,json; print(json.load(sys.stdin)['status'])" || true)
  if [[ "$STATUS_PASS" == "done" || "$STATUS_PASS" == "failed" ]]; then
    break
  fi
  sleep 0.5
done
[[ "$STATUS_PASS" == "done" ]]; report $? "status is done"
ARTIFACT_PASS="$VAULT/raw/orchestrator-results/${TID_PASS}-k2bi-smoke.md"
[[ -f "$ARTIFACT_PASS" ]]; report $? "artifact exists"
[[ "$(cat "$ARTIFACT_PASS")" == *"orchestrator-smoke-ok"* ]]; report $? "artifact contains echo output"
MSG_PASS=$(cat "$RECORDER_OUT")
[[ "$MSG_PASS" == *"DONE"* ]]; report $? "recorder shows DONE"
[[ "$(wc -l < "$RECORDER_OUT" | tr -d ' ')" == "1" ]]; report $? "exactly one notification"
unset K2B_ORCH_K2BI_WORKSPACE
> "$RECORDER_OUT"

# === LOCK serialization ===
echo "=== LOCK serialization ==="
K2BI_WS="$TMPDIR/k2bi_lock"
mkdir -p "$K2BI_WS"
git -C "$K2BI_WS" init -q
echo "x" > "$K2BI_WS/x.txt"
git -C "$K2BI_WS" add x.txt >/dev/null
GIT_AUTHOR_NAME="t" GIT_AUTHOR_EMAIL="t@t" GIT_COMMITTER_NAME="t" GIT_COMMITTER_EMAIL="t@t" git -C "$K2BI_WS" commit -m "init" -q
export K2B_ORCH_K2BI_WORKSPACE="$K2BI_WS"

# Insert a synthetic running task
python3 - "$DB" <<'PY'
import sys, sqlite3, datetime
conn = sqlite3.connect(sys.argv[1])
now = datetime.datetime.now(datetime.timezone.utc).isoformat()
conn.execute("""
INSERT INTO tasks (id, flight_id, stage_name, assignee_profile, status, command_key, success_criteria, permissions, created_at, updated_at, started_at, heartbeat_at, worker_pid)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
""", ("2026-01-01-001", "2026-01-01-001", "dispatch", "k2bi", "running", "test-echo-readonly", "ok", "analyst-command", now, now, now, now, 99999))
conn.commit()
PY

TID_LOCK=$($CLI add --profile k2bi --command-key test-echo-readonly --success ok --permissions analyst-command | awk '{print $NF}')
$CLI poll-once >/dev/null
STATUS_LOCK=$($CLI show "$TID_LOCK" --json | python3 -c "import sys,json; print(json.load(sys.stdin)['status'])")
report $? "add for lock test succeeded"
[[ "$STATUS_LOCK" == "ready" ]]; report $? "second task stays ready"
BLOCKED_BY=$($CLI show "$TID_LOCK" --json | python3 -c "import sys,json; print(json.load(sys.stdin).get('blocked_by',''))")
[[ "$BLOCKED_BY" == "2026-01-01-001" ]]; report $? "blocked_by points to synthetic running task"
[[ ! -f "$VAULT/raw/orchestrator-results/${TID_LOCK}-k2bi-smoke.md" ]]; report $? "no artifact for second task"
> "$RECORDER_OUT"
unset K2B_ORCH_K2BI_WORKSPACE

# === --workspace rejected for k2bi ===
echo "=== --workspace rejected for k2bi ==="
set +e
OUTPUT=$($CLI add --profile k2bi --command-key test-echo-readonly --success x --workspace /tmp/evil 2>&1)
RC=$?
set -e
[[ "$RC" -ne 0 ]]; report $? "add with --workspace for k2bi exits non-zero"
[[ "$OUTPUT" == *"not allowed"* ]]; report $? "error message mentions not allowed"
# Verify no task was created
COUNT=$(python3 - "$DB" <<'PY'
import sys, sqlite3
conn = sqlite3.connect(sys.argv[1])
c = conn.execute("SELECT COUNT(*) FROM tasks")
print(c.fetchone()[0])
PY
)
# We should have exactly 4 tasks from previous scenarios
[[ "$COUNT" == "4" ]]; report $? "no extra task created"

# === Add with --status waiting_for_kimi_output ===
echo "=== Add with --status waiting_for_kimi_output ==="
TID_PARK=$($CLI add --profile k2bi --command-key test-echo-readonly --success ok --permissions analyst-command --status waiting_for_kimi_output --entity "open-source AI" | awk '{print $NF}')
STATUS_PARK=$($CLI show "$TID_PARK" --json | python3 -c "import sys,json; print(json.load(sys.stdin)['status'])")
[[ "$STATUS_PARK" == "waiting_for_kimi_output" ]]; report $? "status is waiting_for_kimi_output"

# === poll_once does NOT spawn parked task ===
echo "=== poll_once does NOT spawn parked task ==="
$CLI poll-once >/dev/null
STATUS_PARK2=$($CLI show "$TID_PARK" --json | python3 -c "import sys,json; print(json.load(sys.stdin)['status'])")
[[ "$STATUS_PARK2" == "waiting_for_kimi_output" ]]; report $? "parked task stays parked after poll_once"

# === One-flight lock ===
echo "=== One-flight lock ==="
set +e
OUTPUT_LOCK=$($CLI add --profile k2bi --command-key test-echo-readonly --success ok --permissions analyst-command --status waiting_for_kimi_output --entity "  Open-Source AI " 2>&1)
RC_LOCK=$?
set -e
[[ "$RC_LOCK" -ne 0 ]]; report $? "duplicate entity_key exits non-zero"
[[ "$OUTPUT_LOCK" == *"flight already active for"* ]]; report $? "lock error message present"

# === Return acceptance gate (pass) ===
echo "=== Return acceptance gate (pass) ==="
GOOD_CONTENT=$(python3 -c '
lines = [
    "This is a comprehensive research report on open-source AI with detailed analysis and many words.",
    "Here is a second substantive line with plenty of content to meet the threshold requirements fully.",
    "Third line: the landscape of open-source AI has shifted dramatically in recent months worldwide.",
    "Fourth line: companies like Meta and Google have released powerful models under permissive licenses.",
    "Fifth line: the implications for enterprise adoption are significant and far-reaching across sectors.",
]
urls = ["https://example.com/source1", "https://example.org/source2", "http://example.net/source3"]
print("\n".join(lines + urls))
print("The conclusion wraps up the analysis with a firm and definitive statement.")
')
# Append the task-bound completion sentinel the gate requires as the last line.
GOOD_CONTENT="${GOOD_CONTENT}
=== END OF KIMI RESEARCH: ${TID_PARK} ==="
$CLI return "$TID_PARK" --text "$GOOD_CONTENT" >/dev/null
STATUS_RET=$($CLI show "$TID_PARK" --json | python3 -c "import sys,json; print(json.load(sys.stdin)['status'])")
[[ "$STATUS_RET" == "returned" ]]; report $? "return transitions to returned"
[[ -f "$VAULT/raw/orchestrator-results/${TID_PARK}-kimi-raw.md" ]]; report $? "raw file stored"

# === Return idempotency ===
echo "=== Return idempotency ==="
set +e
OUTPUT_DUP=$($CLI return "$TID_PARK" --text "$GOOD_CONTENT" 2>&1)
RC_DUP=$?
set -e
[[ "$RC_DUP" -ne 0 ]]; report $? "duplicate return exits non-zero"
[[ "$OUTPUT_DUP" == *"already returned"* ]]; report $? "already returned message present"

# === Complete from returned ===
echo "=== Complete from returned ==="
$CLI complete "$TID_PARK" >/dev/null
STATUS_DONE=$($CLI show "$TID_PARK" --json | python3 -c "import sys,json; print(json.load(sys.stdin)['status'])")
[[ "$STATUS_DONE" == "done" ]]; report $? "complete moves returned to done"

# === Return acceptance gate (reject too small) ===
echo "=== Return acceptance gate (reject too small) ==="
TID_SMALL=$($CLI add --profile k2bi --command-key test-echo-readonly --success ok --permissions analyst-command --status waiting_for_kimi_output --entity "small-test" | awk '{print $NF}')
set +e
OUTPUT_SMALL=$($CLI return "$TID_SMALL" --text "tiny" 2>&1)
RC_SMALL=$?
set -e
[[ "$RC_SMALL" -ne 0 ]]; report $? "too-small return exits non-zero"
[[ "$OUTPUT_SMALL" == *"rejected: size"* ]]; report $? "too-small rejection message present"

# === TTL sweep ===
echo "=== TTL sweep ==="
TID_TTL=$($CLI add --profile k2bi --command-key test-echo-readonly --success ok --permissions analyst-command --status waiting_for_kimi_output --entity "ttl-test" | awk '{print $NF}')
python3 - "$DB" "$TID_TTL" <<'PY'
import sys, sqlite3, datetime
conn = sqlite3.connect(sys.argv[1])
old = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=15)).isoformat()
conn.execute("UPDATE tasks SET updated_at=? WHERE id=?", (old, sys.argv[2]))
conn.commit()
conn.close()
PY
TTL_RESULT=$($CLI poll-once)
[[ "$TTL_RESULT" == *"\"ttl_expired\": [\"$TID_TTL\"]"* ]]; report $? "ttl_expired contains old task"
STATUS_TTL=$($CLI show "$TID_TTL" --json | python3 -c "import sys,json; print(json.load(sys.stdin)['status'])")
[[ "$STATUS_TTL" == "cancelled" ]]; report $? "old parked task cancelled by ttl"
TTL_REASON=$($CLI show "$TID_TTL" --json | python3 -c "import sys,json; print(json.load(sys.stdin).get('blocker_reason',''))")
[[ "$TTL_REASON" == "ttl-expired" ]]; report $? "ttl blocker_reason set"

# === Child survives parent death (process-group kill) ===
echo "=== Child survives parent death ==="
# Run via inline Python inside the bash test to monkeypatch reclaim_zombies
python3 - "$REPO" "$DB" "$VAULT" "$TMPDIR" <<'PY'
import sys, os, subprocess, time, signal
repo = sys.argv[1]
os.environ["PYTHONPATH"] = repo
from scripts.lib import orchestrator_store as store
from scripts.lib import orchestrator_profiles as profiles

store.DB_PATH = sys.argv[2]
store.RESULTS_DIR = f"{sys.argv[3]}/raw/orchestrator-results"
store.init_db(store.connect())

# Create a forking fixture: parent spawns child that writes sentinel, then parent exits
fixture_script = f"""
import os, sys, time
sentinel = sys.argv[1]
# Fork a background child
pid = os.fork()
if pid == 0:
    # child
    for i in range(60):
        with open(sentinel, "a") as f:
            f.write("beat\\n")
        time.sleep(0.2)
    os._exit(0)
else:
    # parent exits immediately, leaving child orphaned (but in same pgid)
    os._exit(0)
"""

sentinel = f"{sys.argv[4]}/sentinel.txt"
# We will run the fixture via subprocess.Popen with start_new_session=True so the
# parent is the process-group leader. Then we simulate a task with that pgid.
proc = subprocess.Popen(
    [sys.executable, "-c", fixture_script, sentinel],
    start_new_session=True,
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
)
# Give child time to start writing
for _ in range(20):
    if os.path.exists(sentinel) and open(sentinel).read().count("beat") > 0:
        break
    time.sleep(0.1)

# Create a running task with this worker_pid (the group leader)
import sqlite3, datetime
conn = sqlite3.connect(store.DB_PATH)
now = datetime.datetime.now(datetime.timezone.utc).isoformat()
conn.execute("""
INSERT INTO tasks (id, flight_id, stage_name, assignee_profile, status, command_key, success_criteria, permissions, created_at, updated_at, started_at, heartbeat_at, worker_pid)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
""", ("2026-01-01-999", "2026-01-01-999", "dispatch", "k2bi", "running", "test-echo-readonly", "ok", "analyst-command", now, now, now, now, proc.pid))
conn.commit()
conn.close()

# Wait a bit then age the heartbeat so reclaim fires
conn = sqlite3.connect(store.DB_PATH)
old = "2020-01-01T00:00:00+00:00"
conn.execute("UPDATE tasks SET heartbeat_at=? WHERE id=?", (old, "2026-01-01-999"))
conn.commit()
conn.close()

# Read sentinel count before reclaim
before_count = open(sentinel).read().count("beat")

# Reclaim zombies
reclaimed = store.reclaim_zombies(timeout_s=300)
assert "2026-01-01-999" in reclaimed, "task should be reclaimed"

# After reclaim, sentinel should stop growing (group was killed)
time.sleep(0.8)
after_count = open(sentinel).read().count("beat")
# The child should have been killed; no more beats after reclaim
assert after_count <= before_count + 1, f"sentinel continued growing: {before_count} -> {after_count}"

# Task should be ready again
t = store.get_task("2026-01-01-999")
assert t["status"] == "ready", f"expected ready, got {t['status']}"
print("PASS: child process group killed before re-dispatch")
PY
report $? "child survives parent death scenario"

echo ""
echo "=============================="
echo "PASS: $PASS  FAIL: $FAIL"
echo "=============================="

[[ "$FAIL" -eq 0 ]]
