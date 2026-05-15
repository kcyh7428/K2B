#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP="$(mktemp -d)"
cleanup() {
  rm -rf "$TMP"
}
trap cleanup EXIT

STUB="$TMP/eod-stub.sh"
CALLS="$TMP/calls.txt"
LOGS="$TMP/vault/.staging/eod-capture-logs"
cat > "$STUB" <<'EOF'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "$K2B_EOD_CALLS"
printf 'stub ran: %s\n' "$*"
EOF
chmod +x "$STUB"

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
