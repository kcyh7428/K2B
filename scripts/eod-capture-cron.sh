#!/usr/bin/env bash
# Cron-safe wrapper for End-of-Day Capture.
# Compatibility wrapper for explicit single-date runs. It does not install or
# enable a schedule. Stage 1 catch-up should use eod_capture.py's range-aware
# command on the home writer after source synchronization is healthy.
set -euo pipefail
umask 077

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
ORIGINAL_ARGS=("$@")

usage() {
  cat <<'EOF'
Usage: scripts/eod-capture-cron.sh <job-a|job-b|job-a-then-b|digest> [YYYY-MM-DD]

No schedule is enabled by this script. Manual diagnostic modes:
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

sanitize_run_output() {
  python3 - "$1" "$2" <<'PY'
import re
import sys

source, destination = sys.argv[1:3]
try:
    text = open(source, encoding="utf-8", errors="replace").read()
except OSError:
    raise SystemExit(1)

text = re.sub(r"([A-Za-z][A-Za-z0-9+.-]*://)[^/@\s]+@", r"\1[REDACTED]@", text)
text = re.sub(
    r"(?i)\b((?:[A-Z0-9_]*(?:API_?KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL|AUTH|COOKIE|OAUTH)[A-Z0-9_]*)=)([^\s]+)",
    r"\1[REDACTED]",
    text,
)
text = re.sub(
    r"(?i)(--(?:api[-_]?key|token|secret|password|credential|auth|cookie|oauth)(?:=|\s+))([^\s]+)",
    r"\1[REDACTED]",
    text,
)
text = re.sub(
    r"(?i)(\b(?:authorization|authentication)\s*:\s*(?:bearer\s+)?)([^\s]+)",
    r"\1[REDACTED]",
    text,
)
text = re.sub(
    r"\b(?:sk-[A-Za-z0-9_-]{8,}|ghp_[A-Za-z0-9]{36}|glpat-[A-Za-z0-9-]{20,}|AKIA[0-9A-Z]{16}|(?:tk|tok)_[A-Za-z0-9_-]{6,}|(?=[A-Za-z0-9+/]{40,}={0,2}(?![A-Za-z0-9+/=]))(?=[A-Za-z0-9+/]*[G-Zg-z+/])[A-Za-z0-9+/]{40,}={0,2})\b",
    "[REDACTED]",
    text,
)
try:
    with open(destination, "w", encoding="utf-8") as f:
        f.write(text)
except OSError:
    raise SystemExit(1)
PY
}

append_run_output() {
  python3 - "$1" "$2" <<'PY'
import os
import stat
import sys

source, destination = sys.argv[1:3]
source_fd = None
destination_fd = None
try:
    source_fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW)
    source_stat = os.fstat(source_fd)
    if not stat.S_ISREG(source_stat.st_mode) or source_stat.st_uid != os.getuid():
        raise OSError("source is not an owned regular file")
    destination_fd = os.open(
        destination,
        os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW,
        0o600,
    )
    destination_stat = os.fstat(destination_fd)
    if (
        not stat.S_ISREG(destination_stat.st_mode)
        or destination_stat.st_uid != os.getuid()
        or destination_stat.st_mode & 0o077
    ):
        raise OSError("destination is not an owned private regular file")
    while True:
        chunk = os.read(source_fd, 1024 * 1024)
        if not chunk:
            break
        view = memoryview(chunk)
        while view:
            written = os.write(destination_fd, view)
            view = view[written:]
    os.fsync(destination_fd)
except OSError as exc:
    print(f"eod-capture-cron: secure log append failed: {exc}", file=sys.stderr)
    raise SystemExit(1)
finally:
    if destination_fd is not None:
        os.close(destination_fd)
    if source_fd is not None:
        os.close(source_fd)
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

CAPTURE_WRITER_ROLE="${K2B_CAPTURE_WRITER_ROLE:-}"
if [[ -z "$CAPTURE_WRITER_ROLE" ]]; then
  if [[ "$(basename "$HOME")" == "keithmbpm2" ]]; then
    CAPTURE_WRITER_ROLE="home"
  else
    CAPTURE_WRITER_ROLE="sjm-source-only"
  fi
fi
if [[ "$CAPTURE_WRITER_ROLE" != "home" ]]; then
  echo "eod-capture-cron: compatibility wrapper is home-writer-only; SJM/source-only runs are rejected" >&2
  exit 2
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
  if [[ "${K2B_EOD_PIPELINE_LOCKED:-0}" == "1" ]]; then
    if ! python3 - <<'PY'
import os
import stat

metadata = os.fstat(9)
if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid():
    raise SystemExit(1)
PY
    then
      echo "eod-capture-cron: inherited pipeline lock is invalid" >&2
      return 2
    fi
    PIPELINE_LOCK_ACQUIRED=1
    return 0
  fi
  exec python3 - "$PIPELINE_LOCK_FILE" "$SELF_PATH" "${ORIGINAL_ARGS[@]}" <<'PY'
import fcntl
import os
import stat
import sys

lock_path, script, *args = sys.argv[1:]
fd = None
try:
    fd = os.open(
        lock_path,
        os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW,
        0o600,
    )
    metadata = os.fstat(fd)
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid():
        raise OSError("lock is not an owned regular file")
    if metadata.st_mode & 0o077:
        os.fchmod(fd, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
except BlockingIOError:
    print("[eod-capture-cron] another pipeline run holds the lock", file=sys.stderr)
    raise SystemExit(3)
except OSError as exc:
    print(f"eod-capture-cron: secure pipeline lock failed: {exc}", file=sys.stderr)
    raise SystemExit(2)

if fd != 9:
    os.dup2(fd, 9, inheritable=True)
    os.close(fd)
else:
    os.set_inheritable(9, True)
env = os.environ.copy()
env["K2B_EOD_PIPELINE_LOCKED"] = "1"
os.execve(script, [script, *args], env)
PY
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
RUN_SANITIZED="$(mktemp "$LOG_DIR/.${RUN_DATE}_${MODE}.sanitized.$$.XXXXXX")"
cleanup_run_output() {
  rm -f "$RUN_OUTPUT"
  rm -f "$RUN_STATUS"
  rm -f "$RUN_SANITIZED"
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
if ! sanitize_run_output "$RUN_OUTPUT" "$RUN_SANITIZED"; then
  echo "eod-capture-cron: failed to sanitize child output; raw output discarded" >&2
  exit 124
fi
mv "$RUN_SANITIZED" "$RUN_OUTPUT"
cat "$RUN_OUTPUT"
if ! append_run_output "$RUN_OUTPUT" "$LOG_FILE"; then
  FAILSAFE_LOG=""
  if FAILSAFE_LOG="$(mktemp "$LOG_DIR/${RUN_DATE}_${MODE}.append-failed.XXXXXX.log")" \
    && append_run_output "$RUN_OUTPUT" "$FAILSAFE_LOG"; then
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
