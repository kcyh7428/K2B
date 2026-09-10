#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

STUB="$TMP/discover-stub.sh"
CALLS="$TMP/calls.txt"
printf '%s\n' '#!/usr/bin/env bash' > "$STUB"
printf '%s\n' 'printf "%s\n" "$*" >> "$K2B_DISCOVERY_CALLS"' >> "$STUB"
printf '%s\n' 'if [[ "${K2B_DISCOVERY_SKIP_STATUS:-0}" != "1" ]]; then printf "{\"counts\":{\"discovered\":2,\"waiting\":2,\"failed\":0},\"discovery_run_id\":\"%s\",\"since\":\"%s\",\"through\":\"%s\",\"writer_role\":\"%s\"}\n" "$K2B_DISCOVERY_RUN_ID" "$K2B_DISCOVERY_SINCE_EFFECTIVE" "$K2B_DISCOVERY_THROUGH_EFFECTIVE" "$K2B_DISCOVERY_ROLE_EFFECTIVE" > "$K2B_CAPTURE_STATUS_FILE"; fi' >> "$STUB"
printf '%s\n' 'printf "%s\n" "{\"counts\":{\"discovered\":2,\"waiting\":2,\"failed\":0},\"capture_mode\":\"interactive\"}"' >> "$STUB"
printf '%s\n' 'exit "${K2B_DISCOVERY_STUB_RC:-0}"' >> "$STUB"
chmod +x "$STUB"

STATE="$TMP/state"
mkdir -p "$TMP/sjm-vault"
set +e
K2B_DISCOVERY_STATE_DIR="$TMP/sjm-vault/.staging/discovery-state" \
K2B_DISCOVERY_COMMAND="$STUB" \
K2B_DISCOVERY_CALLS="$CALLS" \
K2B_VAULT_PATH="$TMP/sjm-vault" \
  "$ROOT/scripts/codex-discovery-job.sh" sjm-source-only > "$TMP/sjm-vault-state.out" 2> "$TMP/sjm-vault-state.err"
sjm_vault_state_rc=$?
set -e
test "$sjm_vault_state_rc" = "2"
test ! -e "$TMP/sjm-vault/.staging/discovery-state"
grep -q 'outside K2B_VAULT_PATH' "$TMP/sjm-vault-state.err"

K2B_DISCOVERY_STATE_DIR="$STATE" \
K2B_DISCOVERY_COMMAND="$STUB" \
K2B_DISCOVERY_CALLS="$CALLS" \
K2B_DISCOVERY_THROUGH="2026-09-10" \
  "$ROOT/scripts/codex-discovery-job.sh" sjm-source-only > "$TMP/success.out"

grep -qx 'discover --since 2026-07-26 --through 2026-09-10 --writer-role sjm-source-only' "$CALLS"
test -f "$STATE/capture-discovery-last-run.json"
test "$(find "$STATE/capture-discovery-receipts" -type f -name '*.json' | wc -l | tr -d ' ')" = "1"
python3 - "$STATE/capture-discovery-last-run.json" <<'PY'
import json
import os
import stat
import sys
from pathlib import Path

path = sys.argv[1]
data = json.load(open(path, encoding="utf-8"))
assert data["job_id"] == "k2b-codex-discovery"
assert data["owner_role"] == "sjm-source-only"
assert data["exit_status"] == 0
assert data["through"] == "2026-09-10"
assert data["status_file_updated"] is True
assert data["artifact_paths"] == [str(Path(path).parent / "capture-status.json")]
assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
PY

LOCK_READY="$TMP/lock-ready"
/usr/bin/lockf -s -t 0 "$STATE/capture-discovery-job.lock" \
  sh -c 'touch "$1"; sleep 10' sh "$LOCK_READY" &
LOCK_HOLDER=$!
for _ in $(seq 1 50); do
  test -f "$LOCK_READY" && break
  sleep 0.02
done
test -f "$LOCK_READY"
set +e
K2B_DISCOVERY_STATE_DIR="$STATE" \
K2B_DISCOVERY_COMMAND="$STUB" \
K2B_DISCOVERY_CALLS="$CALLS" \
  "$ROOT/scripts/codex-discovery-job.sh" home > "$TMP/overlap.out" 2> "$TMP/overlap.err"
rc=$?
set -e
kill "$LOCK_HOLDER" 2>/dev/null || true
wait "$LOCK_HOLDER" 2>/dev/null || true
test "$rc" = "75"
grep -q 'another run is active' "$TMP/overlap.err"

printf '999999\n' > "$STATE/capture-discovery-job.lock"
K2B_DISCOVERY_STATE_DIR="$STATE" \
K2B_DISCOVERY_COMMAND="$STUB" \
K2B_DISCOVERY_CALLS="$CALLS" \
K2B_DISCOVERY_THROUGH="2026-09-10" \
  "$ROOT/scripts/codex-discovery-job.sh" home > "$TMP/stale-lock.out"
test ! -e "$STATE/capture-discovery-job.lock"

set +e
K2B_DISCOVERY_STATE_DIR="$TMP/failure-state" \
K2B_DISCOVERY_COMMAND="$STUB" \
K2B_DISCOVERY_CALLS="$CALLS" \
K2B_DISCOVERY_STUB_RC=7 \
K2B_DISCOVERY_SKIP_STATUS=1 \
K2B_DISCOVERY_THROUGH="2026-09-10" \
  "$ROOT/scripts/codex-discovery-job.sh" home > "$TMP/failure.out" 2> "$TMP/failure.err"
rc=$?
set -e
test "$rc" = "7"
python3 - "$TMP/failure-state/capture-discovery-last-run.json" <<'PY'
import json
import sys

data = json.load(open(sys.argv[1], encoding="utf-8"))
assert data["owner_role"] == "home"
assert data["exit_status"] == 7
assert data["status_file_updated"] is False
assert data["artifact_paths"] == []
PY

set +e
K2B_DISCOVERY_STATE_DIR="$TMP/stale-success-state" \
K2B_DISCOVERY_COMMAND="$STUB" \
K2B_DISCOVERY_CALLS="$CALLS" \
K2B_DISCOVERY_SKIP_STATUS=1 \
K2B_DISCOVERY_THROUGH="2026-09-10" \
  "$ROOT/scripts/codex-discovery-job.sh" home > "$TMP/stale-success.out" 2> "$TMP/stale-success.err"
rc=$?
set -e
test "$rc" = "3"
python3 - "$TMP/stale-success-state/capture-discovery-last-run.json" <<'PY'
import json
import sys

data = json.load(open(sys.argv[1], encoding="utf-8"))
assert data["exit_status"] == 0
assert data["status_file_updated"] is False
assert data["artifact_paths"] == []
PY

for index in $(seq 1 95); do
  printf '{}\n' > "$STATE/capture-discovery-receipts/20000101T000000$(printf '%06d' "$index")Z.json"
done
K2B_DISCOVERY_STATE_DIR="$STATE" \
K2B_DISCOVERY_COMMAND="$STUB" \
K2B_DISCOVERY_CALLS="$CALLS" \
K2B_DISCOVERY_THROUGH="2026-09-10" \
  "$ROOT/scripts/codex-discovery-job.sh" home > "$TMP/retention.out"
test "$(find "$STATE/capture-discovery-receipts" -type f -name '*.json' | wc -l | tr -d ' ')" = "90"
test -f "$STATE/capture-status.json"

REAL_STATE="$TMP/real-writer-state"
REAL_SESSIONS="$TMP/real-sessions"
mkdir -p "$REAL_SESSIONS"
K2B_DISCOVERY_STATE_DIR="$REAL_STATE" \
K2B_DISCOVERY_COMMAND="$ROOT/scripts/eod-capture.py" \
K2B_CODEX_SESSIONS_ROOT="$REAL_SESSIONS" \
K2B_VAULT_PATH="$TMP/real-vault" \
K2B_DISCOVERY_SINCE="2026-09-10" \
K2B_DISCOVERY_THROUGH="2026-09-10" \
  "$ROOT/scripts/codex-discovery-job.sh" home > "$TMP/real-writer.out"
python3 - "$REAL_STATE/capture-discovery-last-run.json" "$REAL_STATE/capture-status.json" <<'PY'
import json
import sys

receipt = json.load(open(sys.argv[1], encoding="utf-8"))
status = json.load(open(sys.argv[2], encoding="utf-8"))
assert receipt["status_file_updated"] is True
assert receipt["discovery_run_id"] == status["discovery_run_id"]
assert (status["since"], status["through"], status["writer_role"]) == (
    "2026-09-10", "2026-09-10", "home"
)
PY

RECEIPT_FAILURE_STATE="$TMP/receipt-failure-state"
mkdir -p "$RECEIPT_FAILURE_STATE/capture-discovery-last-run.json"
set +e
K2B_DISCOVERY_STATE_DIR="$RECEIPT_FAILURE_STATE" \
K2B_DISCOVERY_COMMAND="$STUB" \
K2B_DISCOVERY_CALLS="$CALLS" \
K2B_DISCOVERY_THROUGH="2026-09-10" \
  "$ROOT/scripts/codex-discovery-job.sh" home > "$TMP/receipt-failure.out" 2> "$TMP/receipt-failure.err"
rc=$?
set -e
test "$rc" = "1"
grep -q 'receipt/status finalization failed' "$TMP/receipt-failure.err"

set +e
"$ROOT/scripts/codex-discovery-job.sh" invalid > "$TMP/invalid.out" 2> "$TMP/invalid.err"
rc=$?
set -e
test "$rc" = "2"
grep -q 'usage:' "$TMP/invalid.err"

echo "PASS: codex discovery job writes observable machine-local receipts"
