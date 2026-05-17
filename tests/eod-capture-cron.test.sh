#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP="$(mktemp -d)"
cleanup() {
  pkill -9 -f "$TMP/lock-sleep-stub.sh" 2>/dev/null || true
  rm -rf "$TMP"
}
trap cleanup EXIT

STUB="$TMP/eod-stub.sh"
CALLS="$TMP/calls.txt"
LOGS="$TMP/vault/.staging/eod-capture-logs"
cat > "$STUB" <<'EOF'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "$K2B_EOD_CALLS"
if [ "${1:-}" = "job-a" ] && [ "${K2B_EOD_STUB_JOB_A_SLEEP:-0}" != "0" ]; then
  if [ "${K2B_EOD_STUB_CREATE_MARKER:-0}" = "1" ] && [ -n "${K2B_EOD_STUB_MARKER:-}" ]; then
    touch "$K2B_EOD_STUB_MARKER"
    trap 'rm -f "$K2B_EOD_STUB_MARKER"; exit 0' TERM INT HUP
    trap 'rm -f "$K2B_EOD_STUB_MARKER"' EXIT
  fi
  sleep "$K2B_EOD_STUB_JOB_A_SLEEP"
fi
if [ "${1:-}" = "job-a" ] && [ -n "${K2B_EOD_STUB_GRANDCHILD_MARKER:-}" ]; then
  (
    trap 'rm -f "$K2B_EOD_STUB_GRANDCHILD_MARKER"; exit 0' TERM INT HUP EXIT
    touch "$K2B_EOD_STUB_GRANDCHILD_MARKER"
    sleep 30
  ) &
  wait "$!"
fi
if [ "${1:-}" = "job-a" ] && [ "${K2B_EOD_STUB_JOB_A_RC:-0}" != "0" ]; then
  printf 'stub failed: %s\n' "$*"
  exit "$K2B_EOD_STUB_JOB_A_RC"
fi
if [ "${1:-}" = "job-b" ] && [ "${K2B_EOD_STUB_BLOCK_IF_MARKER:-0}" = "1" ] \
  && [ -n "${K2B_EOD_STUB_MARKER:-}" ] && [ -e "$K2B_EOD_STUB_MARKER" ]; then
  printf 'stub overlap: job-b saw active job-a marker\n'
  exit 9
fi
printf 'stub ran: %s\n' "$*"
EOF
chmod +x "$STUB"
: > "$CALLS"

SLEEP_STUB="$TMP/lock-sleep-stub.sh"
cat > "$SLEEP_STUB" <<'EOF'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "$K2B_EOD_CALLS"
printf '%s\n' "$$" > "$K2B_EOD_SLEEP_STUB_PID_FILE"
touch "$K2B_EOD_SLEEP_STUB_MARKER"
trap 'rm -f "$K2B_EOD_SLEEP_STUB_MARKER"; exit 0' TERM INT HUP
trap 'rm -f "$K2B_EOD_SLEEP_STUB_MARKER"' EXIT
sleep "${K2B_EOD_SLEEP_STUB_SECONDS:-20}" &
sleep_pid=$!
if [ -n "${K2B_EOD_SLEEP_CHILD_PID_FILE:-}" ]; then
  printf '%s\n' "$sleep_pid" > "$K2B_EOD_SLEEP_CHILD_PID_FILE"
fi
wait "$sleep_pid"
EOF
chmod +x "$SLEEP_STUB"

wait_for_file() {
  local path="$1"
  local label="$2"
  for _ in $(seq 1 60); do
    [ -e "$path" ] && return 0
    sleep 0.1
  done
  echo "FAIL: timed out waiting for $label"
  return 1
}

wait_for_child_pid() {
  local parent="$1"
  local pattern="$2"
  local pid
  for _ in $(seq 1 60); do
    pid="$(pgrep -P "$parent" -f "$pattern" | head -1 || true)"
    if [ -n "$pid" ]; then
      printf '%s\n' "$pid"
      return 0
    fi
    sleep 0.1
  done
  echo "FAIL: timed out waiting for child process matching $pattern under $parent" >&2
  return 1
}

assert_chain_lock_contention() {
  local label="$1"
  set +e
  K2B_EOD_COMMAND="$STUB" \
  K2B_EOD_CALLS="$CALLS" \
  K2B_EOD_ENV_FILE="$TMP/missing.env" \
  K2B_VAULT_PATH="$TMP/vault" \
  K2B_EOD_LOG_DIR="$LOGS" \
  "$ROOT/scripts/eod-capture-cron.sh" job-a-then-b 2026-05-14 >"$TMP/${label}.out" 2>"$TMP/${label}.err"
  local rc=$?
  set -e
  [ "$rc" = "3" ] \
    || { echo "FAIL: $label should see lock contention rc=3, got rc=$rc"; cat "$TMP/${label}.out"; cat "$TMP/${label}.err"; exit 1; }
  grep -q 'another pipeline run holds the lock' "$TMP/${label}.err" \
    || { echo "FAIL: $label contention message missing"; cat "$TMP/${label}.err"; exit 1; }
}

run_mode() {
  local mode="$1"
  K2B_EOD_COMMAND="$STUB" \
  K2B_EOD_CALLS="$CALLS" \
  K2B_EOD_ENV_FILE="$TMP/missing.env" \
  K2B_VAULT_PATH="$TMP/vault" \
  K2B_EOD_LOG_DIR="$LOGS" \
  "$ROOT/scripts/eod-capture-cron.sh" "$mode" 2026-05-14 >/dev/null
}

run_mode job-a
run_mode job-b
run_mode digest
run_mode digest-send

grep -qx 'job-a --date 2026-05-14 --vault '"$TMP"'/vault' "$CALLS" \
  || { echo "FAIL: job-a dispatch missing"; cat "$CALLS"; exit 1; }
grep -qx 'job-b --date 2026-05-14 --vault '"$TMP"'/vault' "$CALLS" \
  || { echo "FAIL: job-b dispatch missing"; cat "$CALLS"; exit 1; }
grep -qx 'digest --date 2026-05-14 --vault '"$TMP"'/vault' "$CALLS" \
  || { echo "FAIL: digest dispatch missing"; cat "$CALLS"; exit 1; }
grep -qx 'digest --date 2026-05-14 --vault '"$TMP"'/vault --send' "$CALLS" \
  || { echo "FAIL: digest-send dispatch missing"; cat "$CALLS"; exit 1; }

for mode in job-a job-b digest digest-send; do
  log="$LOGS/2026-05-14_${mode}.log"
  [ -f "$log" ] || { echo "FAIL: missing log $log"; exit 1; }
  grep -q "mode=${mode}" "$log" || { echo "FAIL: log missing mode $mode"; cat "$log"; exit 1; }
done

: > "$CALLS"
K2B_EOD_COMMAND="$STUB" \
K2B_EOD_CALLS="$CALLS" \
K2B_EOD_ENV_FILE="$TMP/missing.env" \
K2B_VAULT_PATH="$TMP/vault" \
K2B_EOD_LOG_DIR="$LOGS" \
"$ROOT/scripts/eod-capture-cron.sh" job-a-then-b 2026-05-14 >"$TMP/chain-success.out"
[ "$(grep -c '^job-a --date 2026-05-14 --vault '"$TMP"'/vault$' "$CALLS")" = "1" ] \
  || { echo "FAIL: job-a-then-b should invoke job-a once"; cat "$CALLS"; exit 1; }
[ "$(grep -c '^job-b --date 2026-05-14 --vault '"$TMP"'/vault$' "$CALLS")" = "1" ] \
  || { echo "FAIL: job-a-then-b should invoke job-b once"; cat "$CALLS"; exit 1; }
grep -q '\[eod-capture-cron\] job-a starting at ' "$TMP/chain-success.out" \
  || { echo "FAIL: job-a-then-b missing job-a start log"; cat "$TMP/chain-success.out"; exit 1; }
grep -q '\[eod-capture-cron\] job-a rc=0 finished at ' "$TMP/chain-success.out" \
  || { echo "FAIL: job-a-then-b missing job-a rc log"; cat "$TMP/chain-success.out"; exit 1; }
grep -q '\[eod-capture-cron\] job-b starting at ' "$TMP/chain-success.out" \
  || { echo "FAIL: job-a-then-b missing job-b start log"; cat "$TMP/chain-success.out"; exit 1; }
grep -q '\[eod-capture-cron\] job-b rc=0 finished at ' "$TMP/chain-success.out" \
  || { echo "FAIL: job-a-then-b missing job-b rc log"; cat "$TMP/chain-success.out"; exit 1; }

: > "$CALLS"
set +e
K2B_EOD_COMMAND="$STUB" \
K2B_EOD_CALLS="$CALLS" \
K2B_EOD_ENV_FILE="$TMP/missing.env" \
K2B_VAULT_PATH="$TMP/vault" \
K2B_EOD_LOG_DIR="$LOGS" \
K2B_EOD_STUB_JOB_A_RC=7 \
"$ROOT/scripts/eod-capture-cron.sh" job-a-then-b 2026-05-14 >"$TMP/chain-fail.out" 2>"$TMP/chain-fail.err"
rc=$?
set -e
[ "$rc" = "7" ] || { echo "FAIL: job-a-then-b should return job-a rc 7, got $rc"; exit 1; }
grep -q '^job-a --date 2026-05-14 --vault '"$TMP"'/vault$' "$CALLS" \
  || { echo "FAIL: failed job-a-then-b did not invoke job-a"; cat "$CALLS"; exit 1; }
! grep -q '^job-b --date 2026-05-14 --vault '"$TMP"'/vault$' "$CALLS" \
  || { echo "FAIL: failed job-a-then-b should not invoke job-b"; cat "$CALLS"; exit 1; }
grep -q '\[eod-capture-cron\] job-a failed rc=7; skipping job-b' "$TMP/chain-fail.out" \
  || { echo "FAIL: failed job-a-then-b missing skip log"; cat "$TMP/chain-fail.out"; exit 1; }

: > "$CALLS"
K2B_EOD_COMMAND="$STUB" \
K2B_EOD_CALLS="$CALLS" \
K2B_EOD_ENV_FILE="$TMP/missing.env" \
K2B_VAULT_PATH="$TMP/vault" \
K2B_EOD_LOG_DIR="$LOGS" \
"$ROOT/scripts/eod-capture-cron.sh" job-a-then-b 2026-05-14 >/dev/null
python3 - "$LOGS/2026-05-14_job-a-then-b.log" <<'PY'
from datetime import datetime
import re
import sys

text = open(sys.argv[1], encoding="utf-8").read()
finish = re.search(r"job-a rc=0 finished at ([^\n]+)", text)
start = re.search(r"job-b starting at ([^\n]+)", text)
if not finish or not start:
    raise SystemExit("missing serial ordering log lines")
finish_ts = datetime.fromisoformat(finish.group(1).replace("Z", "+00:00"))
start_ts = datetime.fromisoformat(start.group(1).replace("Z", "+00:00"))
if not start_ts > finish_ts:
    raise SystemExit(f"job-b start {start_ts} was not after job-a finish {finish_ts}")
PY

TERM_MARKER="$TMP/term-job-a-running"
set +e
K2B_EOD_COMMAND="$STUB" \
K2B_EOD_CALLS="$CALLS" \
K2B_EOD_ENV_FILE="$TMP/missing.env" \
K2B_VAULT_PATH="$TMP/vault" \
K2B_EOD_LOG_DIR="$LOGS" \
K2B_EOD_STUB_JOB_A_SLEEP=5 \
K2B_EOD_STUB_CREATE_MARKER=1 \
K2B_EOD_STUB_MARKER="$TERM_MARKER" \
"$ROOT/scripts/eod-capture-cron.sh" job-a-then-b 2026-05-14 >"$TMP/chain-term.out" 2>"$TMP/chain-term.err" &
term_pid=$!
set -e
for _ in $(seq 1 30); do
  [ -e "$TERM_MARKER" ] && break
  sleep 0.1
done
[ -e "$TERM_MARKER" ] || { echo "FAIL: TERM regression did not observe active job-a marker"; exit 1; }
kill -TERM "$term_pid"
set +e
wait "$term_pid"
term_rc=$?
set -e
[ "$term_rc" != "0" ] || { echo "FAIL: TERM should stop job-a-then-b non-zero"; exit 1; }
for _ in $(seq 1 20); do
  [ ! -e "$TERM_MARKER" ] && break
  sleep 0.1
done
[ ! -e "$TERM_MARKER" ] \
  || { echo "FAIL: TERM left child job-a running after wrapper exit"; cat "$TMP/chain-term.out"; cat "$TMP/chain-term.err"; exit 1; }

GRANDCHILD_MARKER="$TMP/term-grandchild-running"
set +e
K2B_EOD_COMMAND="$STUB" \
K2B_EOD_CALLS="$CALLS" \
K2B_EOD_ENV_FILE="$TMP/missing.env" \
K2B_VAULT_PATH="$TMP/vault" \
K2B_EOD_LOG_DIR="$LOGS" \
K2B_EOD_STUB_GRANDCHILD_MARKER="$GRANDCHILD_MARKER" \
"$ROOT/scripts/eod-capture-cron.sh" job-a-then-b 2026-05-14 >"$TMP/chain-grandchild-term.out" 2>"$TMP/chain-grandchild-term.err" &
grandchild_term_pid=$!
set -e
for _ in $(seq 1 30); do
  [ -e "$GRANDCHILD_MARKER" ] && break
  sleep 0.1
done
[ -e "$GRANDCHILD_MARKER" ] || { echo "FAIL: grandchild TERM regression did not observe active marker"; exit 1; }
kill -TERM "$grandchild_term_pid"
set +e
wait "$grandchild_term_pid"
grandchild_term_rc=$?
set -e
[ "$grandchild_term_rc" != "0" ] || { echo "FAIL: TERM should stop grandchild job-a-then-b non-zero"; exit 1; }
for _ in $(seq 1 20); do
  [ ! -e "$GRANDCHILD_MARKER" ] && break
  sleep 0.1
done
[ ! -e "$GRANDCHILD_MARKER" ] \
  || { echo "FAIL: TERM left grandchild job process running after wrapper exit"; cat "$TMP/chain-grandchild-term.out"; cat "$TMP/chain-grandchild-term.err"; exit 1; }

SIGKILL_MARKER="$TMP/sigkill-job-a-running"
K2B_EOD_COMMAND="$STUB" \
K2B_EOD_CALLS="$CALLS" \
K2B_EOD_ENV_FILE="$TMP/missing.env" \
K2B_VAULT_PATH="$TMP/vault" \
K2B_EOD_LOG_DIR="$LOGS" \
K2B_EOD_STUB_JOB_A_SLEEP=2 \
K2B_EOD_STUB_CREATE_MARKER=1 \
K2B_EOD_STUB_MARKER="$SIGKILL_MARKER" \
"$ROOT/scripts/eod-capture-cron.sh" job-a-then-b 2026-05-14 >"$TMP/chain-sigkill.out" 2>"$TMP/chain-sigkill.err" &
sigkill_pid=$!
for _ in $(seq 1 30); do
  [ -e "$SIGKILL_MARKER" ] && break
  sleep 0.1
done
[ -e "$SIGKILL_MARKER" ] || { echo "FAIL: sigkill lock test did not observe active chain marker"; exit 1; }
kill -KILL "$sigkill_pid"
set +e
wait "$sigkill_pid" 2>/dev/null
sigkill_rc=$?
set -e
[ "$sigkill_rc" != "0" ] || { echo "FAIL: SIGKILL should stop job-a-then-b non-zero"; exit 1; }
for _ in $(seq 1 60); do
  [ ! -e "$SIGKILL_MARKER" ] && break
  sleep 0.1
done
[ ! -e "$SIGKILL_MARKER" ] \
  || { echo "FAIL: SIGKILL test left orphan marker after recovery window"; exit 1; }
: > "$CALLS"
K2B_EOD_COMMAND="$STUB" \
K2B_EOD_CALLS="$CALLS" \
K2B_EOD_ENV_FILE="$TMP/missing.env" \
K2B_VAULT_PATH="$TMP/vault" \
K2B_EOD_LOG_DIR="$LOGS" \
"$ROOT/scripts/eod-capture-cron.sh" job-a-then-b 2026-05-14 >"$TMP/chain-after-sigkill.out" 2>"$TMP/chain-after-sigkill.err"
grep -q '^job-b --date 2026-05-14 --vault '"$TMP"'/vault$' "$CALLS" \
  || { echo "FAIL: pipeline lock did not recover after SIGKILL"; cat "$CALLS"; cat "$TMP/chain-after-sigkill.err"; exit 1; }

LOCK_WRAPPER_MARKER="$TMP/lock-wrapper-eod-running"
LOCK_WRAPPER_PID_FILE="$TMP/lock-wrapper-eod.pid"
LOCK_WRAPPER_CHILD_PID_FILE="$TMP/lock-wrapper-sleep.pid"
K2B_EOD_COMMAND="$SLEEP_STUB" \
K2B_EOD_CALLS="$CALLS" \
K2B_EOD_ENV_FILE="$TMP/missing.env" \
K2B_VAULT_PATH="$TMP/vault" \
K2B_EOD_LOG_DIR="$LOGS" \
K2B_EOD_SLEEP_STUB_SECONDS=20 \
K2B_EOD_SLEEP_STUB_MARKER="$LOCK_WRAPPER_MARKER" \
K2B_EOD_SLEEP_STUB_PID_FILE="$LOCK_WRAPPER_PID_FILE" \
K2B_EOD_SLEEP_CHILD_PID_FILE="$LOCK_WRAPPER_CHILD_PID_FILE" \
"$ROOT/scripts/eod-capture-cron.sh" job-a-then-b 2026-05-14 >"$TMP/lock-wrapper.out" 2>"$TMP/lock-wrapper.err" &
lock_wrapper_pid=$!
lock_supervisor_pid="$(wait_for_child_pid "$lock_wrapper_pid" '[Pp]ython')"
wait_for_file "$LOCK_WRAPPER_MARKER" "wrapper-sigkill EOD marker"
wait_for_file "$LOCK_WRAPPER_CHILD_PID_FILE" "wrapper-sigkill sleep child pid"
kill -KILL "$lock_wrapper_pid"
set +e
wait "$lock_wrapper_pid" 2>/dev/null
set -e
kill -0 "$lock_supervisor_pid" 2>/dev/null \
  || { echo "FAIL: supervisor did not remain alive after wrapper SIGKILL"; exit 1; }
assert_chain_lock_contention "lock-held-wrapper-sigkill"
kill -KILL -- "$(cat "$LOCK_WRAPPER_CHILD_PID_FILE")" "$(cat "$LOCK_WRAPPER_PID_FILE")" "$lock_supervisor_pid" 2>/dev/null || true
rm -f "$LOCK_WRAPPER_MARKER"

LOCK_SUPERVISOR_MARKER="$TMP/lock-supervisor-eod-running"
LOCK_SUPERVISOR_PID_FILE="$TMP/lock-supervisor-eod.pid"
LOCK_SUPERVISOR_CHILD_PID_FILE="$TMP/lock-supervisor-sleep.pid"
K2B_EOD_COMMAND="$SLEEP_STUB" \
K2B_EOD_CALLS="$CALLS" \
K2B_EOD_ENV_FILE="$TMP/missing.env" \
K2B_VAULT_PATH="$TMP/vault" \
K2B_EOD_LOG_DIR="$LOGS" \
K2B_EOD_SLEEP_STUB_SECONDS=20 \
K2B_EOD_SLEEP_STUB_MARKER="$LOCK_SUPERVISOR_MARKER" \
K2B_EOD_SLEEP_STUB_PID_FILE="$LOCK_SUPERVISOR_PID_FILE" \
K2B_EOD_SLEEP_CHILD_PID_FILE="$LOCK_SUPERVISOR_CHILD_PID_FILE" \
"$ROOT/scripts/eod-capture-cron.sh" job-a-then-b 2026-05-14 >"$TMP/lock-supervisor.out" 2>"$TMP/lock-supervisor.err" &
lock_supervisor_wrapper_pid=$!
lock_supervisor_pid="$(wait_for_child_pid "$lock_supervisor_wrapper_pid" '[Pp]ython')"
wait_for_file "$LOCK_SUPERVISOR_MARKER" "wrapper-and-supervisor-sigkill EOD marker"
wait_for_file "$LOCK_SUPERVISOR_CHILD_PID_FILE" "wrapper-and-supervisor-sigkill sleep child pid"
lock_eod_pid="$(cat "$LOCK_SUPERVISOR_PID_FILE")"
kill -KILL "$lock_supervisor_wrapper_pid" "$lock_supervisor_pid"
set +e
wait "$lock_supervisor_wrapper_pid" 2>/dev/null
set -e
kill -0 "$lock_eod_pid" 2>/dev/null \
  || { echo "FAIL: detached EOD subprocess did not remain alive after wrapper+supervisor SIGKILL"; exit 1; }
assert_chain_lock_contention "lock-held-wrapper-supervisor-sigkill"
kill -KILL -- "$(cat "$LOCK_SUPERVISOR_CHILD_PID_FILE")" "$lock_eod_pid" 2>/dev/null || true
rm -f "$LOCK_SUPERVISOR_MARKER"

LOCK_TREE_MARKER="$TMP/lock-tree-eod-running"
LOCK_TREE_PID_FILE="$TMP/lock-tree-eod.pid"
LOCK_TREE_CHILD_PID_FILE="$TMP/lock-tree-sleep.pid"
K2B_EOD_COMMAND="$SLEEP_STUB" \
K2B_EOD_CALLS="$CALLS" \
K2B_EOD_ENV_FILE="$TMP/missing.env" \
K2B_VAULT_PATH="$TMP/vault" \
K2B_EOD_LOG_DIR="$LOGS" \
K2B_EOD_SLEEP_STUB_SECONDS=20 \
K2B_EOD_SLEEP_STUB_MARKER="$LOCK_TREE_MARKER" \
K2B_EOD_SLEEP_STUB_PID_FILE="$LOCK_TREE_PID_FILE" \
K2B_EOD_SLEEP_CHILD_PID_FILE="$LOCK_TREE_CHILD_PID_FILE" \
"$ROOT/scripts/eod-capture-cron.sh" job-a-then-b 2026-05-14 >"$TMP/lock-tree.out" 2>"$TMP/lock-tree.err" &
lock_tree_wrapper_pid=$!
lock_tree_supervisor_pid="$(wait_for_child_pid "$lock_tree_wrapper_pid" '[Pp]ython')"
wait_for_file "$LOCK_TREE_MARKER" "full-tree EOD marker"
wait_for_file "$LOCK_TREE_CHILD_PID_FILE" "full-tree sleep child pid"
lock_tree_eod_pid="$(cat "$LOCK_TREE_PID_FILE")"
kill -KILL -- "$lock_tree_wrapper_pid" "$lock_tree_supervisor_pid" "$lock_tree_eod_pid" "$(cat "$LOCK_TREE_CHILD_PID_FILE")" 2>/dev/null || true
set +e
wait "$lock_tree_wrapper_pid" 2>/dev/null
set -e
rm -f "$LOCK_TREE_MARKER"
: > "$CALLS"
K2B_EOD_COMMAND="$SLEEP_STUB" \
K2B_EOD_CALLS="$CALLS" \
K2B_EOD_ENV_FILE="$TMP/missing.env" \
K2B_VAULT_PATH="$TMP/vault" \
K2B_EOD_LOG_DIR="$LOGS" \
K2B_EOD_SLEEP_STUB_SECONDS=0 \
K2B_EOD_SLEEP_STUB_MARKER="$TMP/lock-tree-second-marker" \
K2B_EOD_SLEEP_STUB_PID_FILE="$TMP/lock-tree-second.pid" \
"$ROOT/scripts/eod-capture-cron.sh" job-a-then-b 2026-05-14 >"$TMP/lock-tree-second.out" 2>"$TMP/lock-tree-second.err"
grep -q '^job-b --date 2026-05-14 --vault '"$TMP"'/vault$' "$CALLS" \
  || { echo "FAIL: full-tree death did not release lock for second pipeline"; cat "$CALLS"; cat "$TMP/lock-tree-second.err"; exit 1; }

CHAIN_ACTIVE_MARKER="$TMP/chain-job-a-running"
K2B_EOD_COMMAND="$STUB" \
K2B_EOD_CALLS="$CALLS" \
K2B_EOD_ENV_FILE="$TMP/missing.env" \
K2B_VAULT_PATH="$TMP/vault" \
K2B_EOD_LOG_DIR="$LOGS" \
K2B_EOD_STUB_JOB_A_SLEEP=2 \
K2B_EOD_STUB_CREATE_MARKER=1 \
K2B_EOD_STUB_MARKER="$CHAIN_ACTIVE_MARKER" \
"$ROOT/scripts/eod-capture-cron.sh" job-a-then-b 2026-05-14 >"$TMP/active-chain.out" 2>"$TMP/active-chain.err" &
active_chain_pid=$!
for _ in $(seq 1 30); do
  [ -e "$CHAIN_ACTIVE_MARKER" ] && break
  sleep 0.1
done
[ -e "$CHAIN_ACTIVE_MARKER" ] || { echo "FAIL: inverse lock test did not observe active chain marker"; exit 1; }
set +e
K2B_EOD_COMMAND="$STUB" \
K2B_EOD_CALLS="$CALLS" \
K2B_EOD_ENV_FILE="$TMP/missing.env" \
K2B_VAULT_PATH="$TMP/vault" \
K2B_EOD_LOG_DIR="$LOGS" \
"$ROOT/scripts/eod-capture-cron.sh" job-a-then-b 2026-05-14 >"$TMP/concurrent-chain.out" 2>"$TMP/concurrent-chain.err"
concurrent_chain_rc=$?
set -e
[ "$concurrent_chain_rc" = "3" ] \
  || { echo "FAIL: concurrent job-a-then-b should fail fast while chain lock is active, got rc=$concurrent_chain_rc"; cat "$TMP/concurrent-chain.out"; cat "$TMP/concurrent-chain.err"; exit 1; }
grep -q 'another pipeline run holds the lock' "$TMP/concurrent-chain.err" \
  || { echo "FAIL: concurrent job-a-then-b contention message missing"; cat "$TMP/concurrent-chain.err"; exit 1; }
set +e
K2B_EOD_COMMAND="$STUB" \
K2B_EOD_CALLS="$CALLS" \
K2B_EOD_ENV_FILE="$TMP/missing.env" \
K2B_VAULT_PATH="$TMP/vault" \
K2B_EOD_LOG_DIR="$LOGS" \
"$ROOT/scripts/eod-capture-cron.sh" job-a 2026-05-14 >"$TMP/manual-during-chain.out" 2>"$TMP/manual-during-chain.err"
manual_during_chain_rc=$?
set -e
wait "$active_chain_pid"
[ "$manual_during_chain_rc" = "3" ] \
  || { echo "FAIL: manual job-a should fail fast while chain lock is active, got rc=$manual_during_chain_rc"; cat "$TMP/manual-during-chain.out"; cat "$TMP/manual-during-chain.err"; exit 1; }
grep -q 'another pipeline run holds the lock' "$TMP/manual-during-chain.err" \
  || { echo "FAIL: manual job-a lock-active error missing"; cat "$TMP/manual-during-chain.err"; exit 1; }

FAIL_LOGS="$TMP/vault/.staging/eod-capture-logs-fail"
mkdir -p "$FAIL_LOGS/2026-05-16_job-a.log"
set +e
K2B_EOD_COMMAND="$STUB" \
K2B_EOD_CALLS="$CALLS" \
K2B_EOD_ENV_FILE="$TMP/missing.env" \
K2B_VAULT_PATH="$TMP/vault" \
K2B_EOD_LOG_DIR="$FAIL_LOGS" \
"$ROOT/scripts/eod-capture-cron.sh" job-a 2026-05-16 >"$TMP/append-fail.out" 2>"$TMP/append-fail.err"
rc=$?
set -e
[ "$rc" = "125" ] || { echo "FAIL: append failure expected exit 125, got $rc"; exit 1; }
grep -q "failed to append log" "$TMP/append-fail.err" \
  || { echo "FAIL: append failure error missing"; cat "$TMP/append-fail.err"; exit 1; }
FAILSAFE_LOG="$(find "$FAIL_LOGS" -name '2026-05-16_job-a.append-failed.*.log' -type f | head -1)"
[ -n "$FAILSAFE_LOG" ] || { echo "FAIL: append failure did not preserve output"; exit 1; }
grep -q 'stub ran: job-a --date 2026-05-16 --vault ' "$FAILSAFE_LOG" \
  || { echo "FAIL: preserved output missing command output"; cat "$FAILSAFE_LOG"; exit 1; }

set +e
K2B_EOD_COMMAND="$STUB" \
K2B_EOD_CALLS="$CALLS" \
K2B_EOD_ENV_FILE="$TMP/missing.env" \
K2B_VAULT_PATH="$TMP/vault" \
K2B_EOD_LOG_DIR="$LOGS" \
"$ROOT/scripts/eod-capture-cron.sh" nope 2026-05-14 >/dev/null 2>"$TMP/err.log"
rc=$?
set -e
[ "$rc" = "2" ] || { echo "FAIL: bad mode expected exit 2, got $rc"; exit 1; }
grep -q "unknown mode" "$TMP/err.log" || { echo "FAIL: bad mode error missing"; cat "$TMP/err.log"; exit 1; }

set +e
K2B_EOD_COMMAND="$STUB" \
K2B_EOD_CALLS="$CALLS" \
K2B_EOD_ENV_FILE="$TMP/missing.env" \
K2B_VAULT_PATH="$TMP/vault" \
K2B_EOD_LOG_DIR="$LOGS" \
"$ROOT/scripts/eod-capture-cron.sh" job-a 2026-13-45 >/dev/null 2>"$TMP/bad-date.log"
rc=$?
set -e
[ "$rc" = "2" ] || { echo "FAIL: bad date expected exit 2, got $rc"; exit 1; }
grep -q "invalid date" "$TMP/bad-date.log" || { echo "FAIL: bad date error missing"; cat "$TMP/bad-date.log"; exit 1; }

UNSAFE_ENV="$TMP/unsafe.env"
printf 'K2B_EOD_LOG_DIR=%s\n' "$TMP/other-logs" > "$UNSAFE_ENV"
chmod 0644 "$UNSAFE_ENV"
set +e
K2B_EOD_COMMAND="$STUB" \
K2B_EOD_CALLS="$CALLS" \
K2B_EOD_ENV_FILE="$UNSAFE_ENV" \
K2B_VAULT_PATH="$TMP/vault" \
K2B_EOD_LOG_DIR="$LOGS" \
"$ROOT/scripts/eod-capture-cron.sh" job-a 2026-05-14 >/dev/null 2>"$TMP/unsafe-env.log"
rc=$?
set -e
[ "$rc" = "2" ] || { echo "FAIL: unsafe env expected exit 2, got $rc"; exit 1; }
grep -q "unsafe env file" "$TMP/unsafe-env.log" || { echo "FAIL: unsafe env error missing"; cat "$TMP/unsafe-env.log"; exit 1; }

NONEXEC="$TMP/nonexec-eod.sh"
printf '#!/usr/bin/env bash\nexit 0\n' > "$NONEXEC"
chmod 0644 "$NONEXEC"
set +e
K2B_EOD_COMMAND="$NONEXEC" \
K2B_EOD_CALLS="$CALLS" \
K2B_EOD_ENV_FILE="$TMP/missing.env" \
K2B_VAULT_PATH="$TMP/vault" \
K2B_EOD_LOG_DIR="$LOGS" \
"$ROOT/scripts/eod-capture-cron.sh" job-a 2026-05-14 >/dev/null 2>"$TMP/nonexec.log"
rc=$?
set -e
[ "$rc" = "2" ] || { echo "FAIL: non-executable command expected exit 2, got $rc"; exit 1; }
grep -q "missing or not executable" "$TMP/nonexec.log" || { echo "FAIL: non-executable error missing"; cat "$TMP/nonexec.log"; exit 1; }

set +e
K2B_EOD_COMMAND="$STUB" \
K2B_EOD_CALLS="$CALLS" \
K2B_EOD_ENV_FILE="$TMP/missing.env" \
K2B_VAULT_PATH="$TMP/vault" \
K2B_EOD_LOG_DIR="/tmp/k2b-eod-unsafe-logs" \
"$ROOT/scripts/eod-capture-cron.sh" job-a 2026-05-14 >/dev/null 2>"$TMP/unsafe-log-dir.log"
rc=$?
set -e
[ "$rc" = "2" ] || { echo "FAIL: unsafe log dir expected exit 2, got $rc"; exit 1; }
grep -q "unsafe log dir" "$TMP/unsafe-log-dir.log" || { echo "FAIL: unsafe log dir error missing"; cat "$TMP/unsafe-log-dir.log"; exit 1; }

echo "PASS: eod-capture-cron.test.sh"
