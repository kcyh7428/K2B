#!/usr/bin/env bash
# Cron-safe wrapper for End-of-Day Capture.
# Does not install cron. Use this from crontab on the Mac Mini after Keith
# approves the schedule and transcript Syncthing setup.
set -euo pipefail
umask 077

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

usage() {
  cat <<'EOF'
Usage: scripts/eod-capture-cron.sh <job-a|job-b|job-a-then-b|digest|digest-send> [YYYY-MM-DD]

Suggested Mac Mini crontab lines, after transcript dirs are synced.

Form A (preferred -- bare invocation, relies on the script default):
  0 2 * * *  ~/Projects/K2B/scripts/eod-capture-cron.sh job-a-then-b
  0 8 * * *  ~/Projects/K2B/scripts/eod-capture-cron.sh digest-send

The script default at line 91 is `$(date -v-1d '+%Y-%m-%d')` which returns
yesterday HKT. Cron fires at 02:00 / 08:00 HKT (already the new HKT calendar
day) and the script default correctly resolves to the day whose work we want
to process.

Form B (explicit -- ONLY if you want the schedule layer to declare intent):
  0 2 * * *  ~/Projects/K2B/scripts/eod-capture-cron.sh job-a-then-b "$(date -v-1d '+\%Y-\%m-\%d')"
  0 8 * * *  ~/Projects/K2B/scripts/eod-capture-cron.sh digest-send    "$(date -v-1d '+\%Y-\%m-\%d')"

CRITICAL when using Form B: the `%` characters MUST be escaped as `\%`. cron
treats unescaped `%` as newline-to-stdin, which truncates the command into
"\$(date -v-1d '+" and the rest becomes stdin to the truncated command.
Form A avoids this footgun entirely.

Manual ad-hoc split mode remains available:
  scripts/eod-capture-cron.sh job-a
  scripts/eod-capture-cron.sh job-b

Env:
  K2B_VAULT_PATH       vault root, default ~/Projects/K2B-Vault
  K2B_EOD_ENV_FILE     optional env file, default ~/.config/k2b/eod-capture.env
  K2B_EOD_LOG_DIR      log dir, default $K2B_VAULT_PATH/.staging/eod-capture-logs
  K2B_EOD_COMMAND      test override for scripts/eod-capture.py

Exit codes:
  124  command output could not be preserved after log append failure
  125  command output was preserved to *.append-failed.<pid>.log after log append failure
EOF
}

validate_env_file() {
  python3 - "$1" <<'PY'
import os
import stat
import sys

path = sys.argv[1]
st = os.stat(path)
if not stat.S_ISREG(st.st_mode):
    print(f"{path} is not a regular file", file=sys.stderr)
    raise SystemExit(1)
if st.st_uid != os.getuid():
    print(f"{path} is not owned by the current user", file=sys.stderr)
    raise SystemExit(1)
if st.st_mode & 0o077:
    print(f"{path} must be chmod 600 or stricter", file=sys.stderr)
    raise SystemExit(1)
PY
}

validate_log_dir() {
  python3 - "$1" "$2" "$HOME" <<'PY'
import os
import sys

log_dir, vault, home = (os.path.realpath(p) for p in sys.argv[1:4])

def is_within(path, root):
    return path == root or path.startswith(root.rstrip(os.sep) + os.sep)

if not (is_within(log_dir, vault) or is_within(log_dir, home)):
    print(f"{log_dir} is outside vault/home", file=sys.stderr)
    raise SystemExit(1)
PY
}

MODE="${1:-}"
if [[ -z "$MODE" || "$MODE" == "-h" || "$MODE" == "--help" ]]; then
  usage
  exit 0
fi
shift || true

RUN_DATE="${1:-${K2B_EOD_DATE:-$(date -v-1d '+%Y-%m-%d')}}"
if [[ ! "$RUN_DATE" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]]; then
  echo "eod-capture-cron: invalid date: $RUN_DATE (expected YYYY-MM-DD)" >&2
  exit 2
fi
if ! python3 - "$RUN_DATE" <<'PY'
from datetime import date
import sys

try:
    parsed = date.fromisoformat(sys.argv[1])
except ValueError:
    raise SystemExit(1)
if parsed.isoformat() != sys.argv[1]:
    raise SystemExit(1)
PY
then
  echo "eod-capture-cron: invalid date: $RUN_DATE (expected real YYYY-MM-DD)" >&2
  exit 2
fi
ENV_FILE="${K2B_EOD_ENV_FILE:-$HOME/.config/k2b/eod-capture.env}"

if [[ -f "$ENV_FILE" ]]; then
  if ! validate_env_file "$ENV_FILE"; then
    echo "eod-capture-cron: unsafe env file: $ENV_FILE" >&2
    exit 2
  fi
  set -a
  # shellcheck source=/dev/null
  source "$ENV_FILE"
  set +a
fi

VAULT="${K2B_VAULT_PATH:-$HOME/Projects/K2B-Vault}"
LOG_DIR="${K2B_EOD_LOG_DIR:-$VAULT/.staging/eod-capture-logs}"
EOD_COMMAND="${K2B_EOD_COMMAND:-$REPO_ROOT/scripts/eod-capture.py}"
if ! validate_log_dir "$LOG_DIR" "$VAULT"; then
  echo "eod-capture-cron: unsafe log dir: $LOG_DIR" >&2
  exit 2
fi
if [[ ! -x "$EOD_COMMAND" ]]; then
  echo "eod-capture-cron: missing or not executable: $EOD_COMMAND" >&2
  exit 2
fi

SELF_PATH="$SCRIPT_DIR/$(basename "$0")"
CHILD_PID=""

timestamp_utc() {
  python3 <<'PY'
from datetime import datetime, timezone

print(datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z"))
PY
}

run_child_mode() {
  local child_mode="$1"
  run_eod_mode "$child_mode"
}

run_eod_mode() {
  local child_mode="$1"
  run_tracked_command "$EOD_COMMAND" "$child_mode" --date "$RUN_DATE" --vault "$VAULT"
}

run_tracked_command() {
  local rc
  python3 - "$@" <<'PY' &
import os
import signal
import subprocess
import sys

cmd = sys.argv[1:]
proc = None
pending_signals = []

def forward(signum, _frame):
    if proc is None:
        pending_signals.append(signum)
        return
    try:
        os.killpg(proc.pid, signum)
    except ProcessLookupError:
        pass

for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
    signal.signal(sig, forward)

try:
    proc = subprocess.Popen(cmd, start_new_session=True, pass_fds=([9] if os.path.exists("/dev/fd/9") else []))
except FileNotFoundError as exc:
    print(f"eod-capture-cron: command not found: {exc.filename}", file=sys.stderr)
    raise SystemExit(127)

for sig in pending_signals:
    forward(sig, None)

rc = proc.wait()
if rc < 0:
    raise SystemExit(128 + abs(rc))
raise SystemExit(rc)
PY
  CHILD_PID=$!
  wait "$CHILD_PID"
  rc=$?
  CHILD_PID=""
  return "$rc"
}

forward_signal_and_exit() {
  local signal_name="$1"
  local exit_rc="$2"
  if [[ -n "$CHILD_PID" ]]; then
    kill "-$signal_name" "$CHILD_PID" 2>/dev/null || true
    wait "$CHILD_PID" 2>/dev/null || true
    CHILD_PID=""
  fi
  exit "$exit_rc"
}

trap 'forward_signal_and_exit TERM 143' TERM
trap 'forward_signal_and_exit INT 130' INT
trap 'forward_signal_and_exit HUP 129' HUP

run_job_a_then_b() {
  local rc
  printf '[eod-capture-cron] job-a starting at %s\n' "$(timestamp_utc)"
  run_child_mode job-a
  rc=$?
  printf '[eod-capture-cron] job-a rc=%s finished at %s\n' "$rc" "$(timestamp_utc)"
  if [[ "$rc" -ne 0 ]]; then
    printf '[eod-capture-cron] job-a failed rc=%s; skipping job-b\n' "$rc"
    return "$rc"
  fi

  printf '[eod-capture-cron] job-b starting at %s\n' "$(timestamp_utc)"
  run_child_mode job-b
  rc=$?
  printf '[eod-capture-cron] job-b rc=%s finished at %s\n' "$rc" "$(timestamp_utc)"
  return "$rc"
}

PIPELINE_LOCK_ACQUIRED=0
PIPELINE_LOCK_FILE=""

lock_fd_nonblocking() {
  if command -v flock >/dev/null 2>&1; then
    flock -n 9
    return $?
  fi
  python3 - <<'PY'
import fcntl
import sys

try:
    fcntl.flock(9, fcntl.LOCK_EX | fcntl.LOCK_NB)
except BlockingIOError:
    raise SystemExit(1)
except OSError as exc:
    print(f"eod-capture-cron: pipeline lock error: {exc}", file=sys.stderr)
    raise SystemExit(2)
PY
}

unlock_fd() {
  if command -v flock >/dev/null 2>&1; then
    flock -u 9 2>/dev/null || true
    return 0
  fi
  python3 - <<'PY' 2>/dev/null || true
import fcntl

fcntl.flock(9, fcntl.LOCK_UN)
PY
}

acquire_pipeline_lock() {
  local lock_root="$VAULT/.staging"
  mkdir -p "$lock_root"
  PIPELINE_LOCK_FILE="$lock_root/.eod-pipeline.lock"
  exec 9>"$PIPELINE_LOCK_FILE"
  if ! lock_fd_nonblocking; then
    echo "[eod-capture-cron] another pipeline run holds the lock" >&2
    exec 9>&-
    return 3
  fi
  PIPELINE_LOCK_ACQUIRED=1
}

release_pipeline_lock() {
  if [[ "$PIPELINE_LOCK_ACQUIRED" -eq 1 ]]; then
    unlock_fd
    exec 9>&-
    PIPELINE_LOCK_ACQUIRED=0
  fi
}

mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/${RUN_DATE}_${MODE}.log"
RUN_OUTPUT="$(mktemp "$LOG_DIR/.${RUN_DATE}_${MODE}.$$.XXXXXX")"
RUN_STATUS="$(mktemp "$LOG_DIR/.${RUN_DATE}_${MODE}.status.$$.XXXXXX")"
cleanup_run_output() {
  rm -f "$RUN_OUTPUT"
  rm -f "$RUN_STATUS"
}
trap cleanup_run_output EXIT
cleanup_all() {
  release_pipeline_lock
  cleanup_run_output
}
trap cleanup_all EXIT

CHAIN_MODE=0
NEEDS_PIPELINE_LOCK=0
case "$MODE" in
  job-a)
    NEEDS_PIPELINE_LOCK=1
    CMD=("$EOD_COMMAND" job-a --date "$RUN_DATE" --vault "$VAULT")
    ;;
  job-b)
    NEEDS_PIPELINE_LOCK=1
    CMD=("$EOD_COMMAND" job-b --date "$RUN_DATE" --vault "$VAULT")
    ;;
  job-a-then-b)
    NEEDS_PIPELINE_LOCK=1
    CHAIN_MODE=1
    ;;
  digest)
    CMD=("$EOD_COMMAND" digest --date "$RUN_DATE" --vault "$VAULT")
    ;;
  digest-send)
    CMD=("$EOD_COMMAND" digest --date "$RUN_DATE" --vault "$VAULT" --send)
    ;;
  *)
    echo "eod-capture-cron: unknown mode: $MODE" >&2
    usage >&2
    exit 2
    ;;
esac

if [[ "$NEEDS_PIPELINE_LOCK" -eq 1 ]]; then
  if ! acquire_pipeline_lock; then
    exit 3
  fi
fi

set +e
{
  printf '[%s] mode=%s date=%s vault=%s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$MODE" "$RUN_DATE" "$VAULT"
  if [[ "$CHAIN_MODE" -eq 1 ]]; then
    run_job_a_then_b
  else
    run_tracked_command "${CMD[@]}"
  fi
  cmd_rc=$?
  if [[ "$cmd_rc" -eq 0 ]]; then
    printf '[%s] mode=%s done\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$MODE"
  fi
  printf '%s\n' "$cmd_rc" > "$RUN_STATUS"
} >"$RUN_OUTPUT" 2>&1
cmd_rc="$(cat "$RUN_STATUS")"
set -e
cat "$RUN_OUTPUT"
if ! cat "$RUN_OUTPUT" >> "$LOG_FILE"; then
  FAILSAFE_LOG="$LOG_DIR/${RUN_DATE}_${MODE}.append-failed.$$.log"
  if cp "$RUN_OUTPUT" "$FAILSAFE_LOG"; then
    echo "eod-capture-cron: preserved run output: $FAILSAFE_LOG" >&2
    append_failure_rc=125
  else
    echo "eod-capture-cron: failed to preserve run output after log append failure" >&2
    echo "eod-capture-cron: begin unpreserved run output" >&2
    sed -n '1,200p' "$RUN_OUTPUT" >&2 || true
    echo "eod-capture-cron: end unpreserved run output" >&2
    append_failure_rc=124
  fi
  echo "eod-capture-cron: failed to append log: $LOG_FILE" >&2
  exit "$append_failure_rc"
fi
exit "$cmd_rc"
