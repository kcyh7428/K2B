#!/usr/bin/env bash
# tests/audit-date-handling.test.sh
# Tests for scripts/audit-date-handling.sh.
#
# Covers the three bug-class forms (E-2026-05-20-001 class):
#   1. $(date '+%Y-%m-%d')   classic quoted-format command sub (regression)
#   2. $(date +%Y-%m-%d)     command sub with UNQUOTED format string
#   3. `date '+%Y-%m-%d'`    backtick command sub (quoted)
#   3b.`date +%Y-%m-%d`      backtick command sub (unquoted)
#
# And confirms negatives:
#   - date -v-1d / -v-7d are NOT flagged (yesterday-aware)
#   - $(date '+%Y-%m-%dT%H:%M:%S') is NOT flagged (log timestamp, not bucket)
#
# The fixture dir is wired in via the K2B_AUDIT_SCRIPTS_DIR env var.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
AUDIT="$REPO_ROOT/scripts/audit-date-handling.sh"
FIXTURES="$REPO_ROOT/tests/fixtures/audit-date"

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

[ -x "$AUDIT" ] || fail "audit script not executable: $AUDIT"
[ -d "$FIXTURES" ] || fail "fixtures dir missing: $FIXTURES"

# --- Test 1: --json mode flags exactly 4 lines and exits 1 -------------------
# Use JSON because text output's leading "audit-date-handling: ... -v-1d ..."
# header collides with naive grep checks. The JSON findings array gives us a
# clean, structured surface to assert against.
set +e
JOUT=$(K2B_AUDIT_SCRIPTS_DIR="$FIXTURES" "$AUDIT" --json 2>&1)
JRC=$?
set -e

[ "$JRC" -eq 1 ] || fail "test1: --json expected exit 1, got $JRC. Output:\n$JOUT"

COUNT=$(printf '%s' "$JOUT" | python3 -c 'import json,sys; print(json.load(sys.stdin)["flagged_count"])')
[ "$COUNT" -eq 4 ] || fail "test1: expected flagged_count=4 (3 bug-class forms + 1 regression), got $COUNT. Output:\n$JOUT"

# --- Test 2: each of the three new forms appears in findings ------------------
contains() {
  # contains "<label>" "<needle>" — fail if the JSON findings don't contain needle.
  printf '%s' "$JOUT" | python3 -c '
import json, sys
needle = sys.argv[1]
data = json.load(sys.stdin)
hit = any(needle in f for f in data["findings"])
sys.exit(0 if hit else 1)
' "$2" || fail "test2 $1: needle [$2] not in findings. JSON:\n$JOUT"
}

contains "backtick+quoted"   "\`date '+%Y-%m-%d'\`"
contains "backtick+unquoted" "\`date +%Y-%m-%d\`"
contains "\$()+unquoted"     "\$(date +%Y-%m-%d)"
contains "classic regression" "\$(date '+%Y-%m-%d')"

# --- Test 3: safe (-v-1d / -v-7d) and ISO-timestamp fixtures are NOT flagged --
not_contains() {
  printf '%s' "$JOUT" | python3 -c '
import json, sys
needle = sys.argv[1]
data = json.load(sys.stdin)
hit = any(needle in f for f in data["findings"])
sys.exit(1 if hit else 0)
' "$2" || fail "test3 $1: needle [$2] WAS in findings (should be filtered). JSON:\n$JOUT"
}

not_contains "yesterday-safe -v-1d" "-v-1d"
not_contains "yesterday-safe -v-7d" "-v-7d"
not_contains "ISO log timestamp"    "%Y-%m-%dT%H"

# --- Test 4: exit 0 / OK when only safe fixtures are present ------------------
TMPDIR_SAFE=$(mktemp -d "${TMPDIR:-/tmp}/k2b-audit-safe.XXXXXX")
trap 'rm -rf "$TMPDIR_SAFE"' EXIT
cp "$FIXTURES/cron_yesterday_safe.sh" "$TMPDIR_SAFE/"
cp "$FIXTURES/cron_log_timestamp.sh" "$TMPDIR_SAFE/"

set +e
SOUT=$(K2B_AUDIT_SCRIPTS_DIR="$TMPDIR_SAFE" "$AUDIT" 2>&1)
SRC=$?
set -e

[ "$SRC" -eq 0 ] || fail "test4: safe-only dir expected exit 0, got $SRC. Output:\n$SOUT"
echo "$SOUT" | grep -q "OK" || fail "test4: expected OK message. Output:\n$SOUT"

echo "PASS: audit-date-handling.test.sh (4 tests)"
