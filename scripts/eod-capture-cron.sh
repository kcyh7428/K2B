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
Usage: scripts/eod-capture-cron.sh <job-a|job-b|digest|digest-send> [YYYY-MM-DD]

Suggested Mac Mini crontab lines, after transcript dirs are synced:
  0 2 * * *  cd ~/Projects/K2B && scripts/eod-capture-cron.sh job-a
  30 2 * * * cd ~/Projects/K2B && scripts/eod-capture-cron.sh job-b
  0 8 * * *  cd ~/Projects/K2B && scripts/eod-capture-cron.sh digest-send

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

RUN_DATE="${1:-${K2B_EOD_DATE:-$(date '+%Y-%m-%d')}}"
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

mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/${RUN_DATE}_${MODE}.log"
RUN_OUTPUT="$(mktemp "$LOG_DIR/.${RUN_DATE}_${MODE}.$$.XXXXXX")"
RUN_STATUS="$(mktemp "$LOG_DIR/.${RUN_DATE}_${MODE}.status.$$.XXXXXX")"
cleanup_run_output() {
  rm -f "$RUN_OUTPUT"
  rm -f "$RUN_STATUS"
}
trap cleanup_run_output EXIT

case "$MODE" in
  job-a)
    CMD=("$EOD_COMMAND" job-a --date "$RUN_DATE" --vault "$VAULT")
    ;;
  job-b)
    CMD=("$EOD_COMMAND" job-b --date "$RUN_DATE" --vault "$VAULT")
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

set +e
{
  printf '[%s] mode=%s date=%s vault=%s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$MODE" "$RUN_DATE" "$VAULT"
  "${CMD[@]}"
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
