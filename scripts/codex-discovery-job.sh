#!/usr/bin/env bash
set -euo pipefail
umask 077

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

ROLE="${1:-}"
case "$ROLE" in
  home|sjm-source-only) ;;
  *)
    echo "usage: scripts/codex-discovery-job.sh <home|sjm-source-only>" >&2
    exit 2
    ;;
esac

STATE_DIR="${K2B_DISCOVERY_STATE_DIR:-$HOME/.local/state/k2b}"
RECEIPT_DIR="$STATE_DIR/capture-discovery-receipts"
STATUS_FILE="$STATE_DIR/capture-status.json"
DISCOVERY_COMMAND_OVERRIDE="${K2B_DISCOVERY_COMMAND:-}"
DISCOVERY_COMMAND="${DISCOVERY_COMMAND_OVERRIDE:-$REPO_ROOT/scripts/eod-capture.py}"

select_python() {
  local candidate=""
  if [[ -n "${K2B_PYTHON:-}" ]]; then
    candidate="$K2B_PYTHON"
    if [[ ! -x "$candidate" ]]; then
      echo "codex-discovery-job: K2B_PYTHON is not executable: $candidate" >&2
      return 1
    fi
    if ! "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)'; then
      echo "codex-discovery-job: K2B_PYTHON must be Python 3.12 or newer" >&2
      return 1
    fi
    printf '%s\n' "$candidate"
    return 0
  fi

  for candidate in \
    "$REPO_ROOT/venv/washing-machine/bin/python" \
    /opt/homebrew/bin/python3 \
    "$(command -v python3 2>/dev/null || true)"; do
    [[ -n "$candidate" && -x "$candidate" ]] || continue
    if "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)' 2>/dev/null; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done
  echo "codex-discovery-job: Python 3.12 or newer is required" >&2
  return 1
}

PYTHON_BIN="$(select_python)" || exit 2
# This is an idempotent full-queue inventory refresh, not an extraction job.
# Keeping the Stage 1 boundary visible on every run prevents older waiting
# sources from disappearing from the status count as the calendar advances.
SINCE="${K2B_DISCOVERY_SINCE:-2026-07-26}"
THROUGH="${K2B_DISCOVERY_THROUGH:-$(TZ=Asia/Hong_Kong date '+%Y-%m-%d')}"

if [[ "$ROLE" == "sjm-source-only" ]]; then
  VAULT_PATH="${K2B_VAULT_PATH:-$HOME/Projects/K2B-Vault}"
  if ! "$PYTHON_BIN" - "$STATE_DIR" "$VAULT_PATH" <<'PY'
import os
import sys

state = os.path.realpath(os.path.abspath(os.path.expanduser(sys.argv[1])))
vault = os.path.realpath(os.path.abspath(os.path.expanduser(sys.argv[2])))
if state == vault or state.startswith(vault.rstrip(os.sep) + os.sep):
    print(
        "codex-discovery-job: SJM state must remain outside K2B_VAULT_PATH",
        file=sys.stderr,
    )
    raise SystemExit(1)
PY
  then
    exit 2
  fi
fi

mkdir -p "$RECEIPT_DIR"
chmod 700 "$STATE_DIR" "$RECEIPT_DIR"

JOB_LOCK="$STATE_DIR/capture-discovery-job.lock"
# BSD lockf is kernel-backed: a crash releases the lock automatically and a
# leftover file is harmless. Re-exec once so the parent lockf process owns the
# lock for exactly the child job's lifetime; do not retain the lock file.
if [[ "${K2B_DISCOVERY_JOB_LOCKED:-0}" != "1" ]]; then
  set +e
  /usr/bin/lockf -s -t 0 "$JOB_LOCK" \
    env K2B_DISCOVERY_JOB_LOCKED=1 "$0" "$ROLE"
  LOCKED_RC=$?
  set -e
  if [[ "$LOCKED_RC" -eq 75 ]]; then
    echo "codex-discovery-job: another run is active" >&2
  fi
  exit "$LOCKED_RC"
fi

if [[ ! -x "$DISCOVERY_COMMAND" ]]; then
  echo "codex-discovery-job: missing command: $DISCOVERY_COMMAND" >&2
  exit 2
fi
if [[ -n "$DISCOVERY_COMMAND_OVERRIDE" ]]; then
  DISCOVERY_ARGV=("$DISCOVERY_COMMAND")
else
  DISCOVERY_ARGV=("$PYTHON_BIN" "$DISCOVERY_COMMAND")
fi

STARTED_AT="$("$PYTHON_BIN" -c 'from datetime import datetime, timezone; print(datetime.now(timezone.utc).isoformat())')"
RUN_ID="$("$PYTHON_BIN" -c 'import secrets; print(secrets.token_hex(16))')"
set +e
K2B_CAPTURE_STATUS_FILE="$STATUS_FILE" \
K2B_DISCOVERY_RUN_ID="$RUN_ID" \
K2B_DISCOVERY_SINCE_EFFECTIVE="$SINCE" \
K2B_DISCOVERY_THROUGH_EFFECTIVE="$THROUGH" \
K2B_DISCOVERY_ROLE_EFFECTIVE="$ROLE" \
  "${DISCOVERY_ARGV[@]}" discover \
    --since "$SINCE" \
    --through "$THROUGH" \
    --writer-role "$ROLE"
DISCOVERY_RC=$?
set -e
FINISHED_AT="$("$PYTHON_BIN" -c 'from datetime import datetime, timezone; print(datetime.now(timezone.utc).isoformat())')"

RECEIPT_STAMP="$("$PYTHON_BIN" -c 'from datetime import datetime, timezone; print(datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ"))')"
RECEIPT_PATH="$RECEIPT_DIR/${RECEIPT_STAMP}.json"
LAST_RUN_PATH="$STATE_DIR/capture-discovery-last-run.json"

set +e
"$PYTHON_BIN" - "$RECEIPT_PATH" "$LAST_RUN_PATH" "$ROLE" "$STARTED_AT" "$FINISHED_AT" "$DISCOVERY_RC" "$STATUS_FILE" "$SINCE" "$THROUGH" "$RUN_ID" <<'PY'
import json
import os
import sys
import tempfile
from pathlib import Path

receipt_path, last_run_path, role, started_at, finished_at, rc, status_file, since, through, run_id = sys.argv[1:]
try:
    status_payload = json.loads(Path(status_file).read_text(encoding="utf-8"))
    status_updated = isinstance(status_payload, dict) and (
        status_payload.get("discovery_run_id") == run_id
        and status_payload.get("since") == since
        and status_payload.get("through") == through
        and status_payload.get("writer_role") == role
    )
except (OSError, json.JSONDecodeError):
    status_updated = False
payload = {
    "schema_version": 1,
    "job_id": "k2b-codex-discovery",
    "owner_role": role,
    "started_at": started_at,
    "finished_at": finished_at,
    "exit_status": int(rc),
    "since": since,
    "through": through,
    "discovery_run_id": run_id,
    "status_file": status_file,
    "status_file_updated": status_updated,
    "artifact_paths": [status_file] if status_updated else [],
}

def atomic_write(path: str) -> None:
    directory = os.path.dirname(path)
    fd, temp_path = tempfile.mkstemp(prefix=".tmp_", suffix=".json", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp_path, 0o600)
        os.replace(temp_path, path)
    except Exception:
        try:
            os.unlink(temp_path)
        except FileNotFoundError:
            pass
        raise

atomic_write(receipt_path)
atomic_write(last_run_path)
receipts = sorted(
    path
    for path in Path(receipt_path).parent.glob("*.json")
    if not path.name.startswith(".tmp_")
)
for old_receipt in receipts[:-90]:
    old_receipt.unlink()
if int(rc) == 0 and not status_updated:
    raise SystemExit(3)
PY
RECEIPT_RC=$?
set -e

if [[ "$RECEIPT_RC" -ne 0 ]]; then
  echo "codex-discovery-job: receipt/status finalization failed" >&2
fi
printf 'codex-discovery-job: role=%s discovery_rc=%s receipt_rc=%s receipt=%s\n' \
  "$ROLE" "$DISCOVERY_RC" "$RECEIPT_RC" "$RECEIPT_PATH"
# EX_IOERR (74) means both discovery and receipt/status finalization failed;
# the printed discovery_rc and receipt_rc retain the two underlying codes.
if [[ "$DISCOVERY_RC" -ne 0 && "$RECEIPT_RC" -ne 0 ]]; then
  exit 74
fi
if [[ "$DISCOVERY_RC" -ne 0 ]]; then
  exit "$DISCOVERY_RC"
fi
exit "$RECEIPT_RC"
