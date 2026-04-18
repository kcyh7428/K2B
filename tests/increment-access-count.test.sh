#!/usr/bin/env bash
# tests/increment-access-count.test.sh
# Tests for scripts/increment-access-count.py (v2 TSV pivot).
#
# v2 contract (post-Codex plan review): access counts live in a standalone
# TSV file, NOT in self_improve_learnings.md. /learn stays the sole writer
# of the learnings file. This helper is the ONLY writer of access_counts.tsv.
#
# Behavior:
#   - Takes L-IDs as argv.
#   - Reads access_counts.tsv (creates it if missing, with header row).
#   - For each unique L-ID: bumps count by 1, stamps last_accessed = today.
#   - Dedupes argv per call (L-ID passed three times = one bump).
#   - Unknown (not-yet-seen) L-ID starts at count=1.
#   - Writes atomically via temp + os.replace.
#   - Exit codes: 0 success, 1 usage (no args), 2 IO error.
#
# Env overrides for testing:
#   K2B_ACCESS_COUNTS_TSV  path to the TSV (default: canonical)

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
HELPER="$REPO_ROOT/scripts/increment-access-count.py"

TMPROOT="$(mktemp -d)"
trap 'rm -rf "$TMPROOT"' EXIT

fail() { echo "FAIL: $*" >&2; exit 1; }

seed_tsv() {
  # seed_tsv <path> [row ...] -- header is always written, rows optional.
  local path="$1"
  shift
  {
    echo "# access_counts.tsv -- citation counts per L-ID"
    printf 'learn_id\tcount\tlast_accessed\n'
    for row in "$@"; do
      printf '%s\n' "$row"
    done
  } > "$path"
}

get_count() {
  # get_count <tsv> <learn_id> -> echoes count, or empty if not present.
  local tsv="$1" lid="$2"
  awk -v id="$lid" -F'\t' '$1 == id { print $2 }' "$tsv"
}

get_date() {
  local tsv="$1" lid="$2"
  awk -v id="$lid" -F'\t' '$1 == id { print $3 }' "$tsv"
}

TODAY="$(date +%Y-%m-%d)"

# --- Test 1: first-ever bump on a brand-new L-ID starts count at 1 ------
TSV="$TMPROOT/t1.tsv"
seed_tsv "$TSV"  # no rows

K2B_ACCESS_COUNTS_TSV="$TSV" "$HELPER" L-2026-04-01-001 >/dev/null
[ "$(get_count "$TSV" L-2026-04-01-001)" = "1" ] \
  || fail "test1: expected count=1, got [$(get_count "$TSV" L-2026-04-01-001)]"
[ "$(get_date "$TSV" L-2026-04-01-001)" = "$TODAY" ] \
  || fail "test1: expected last_accessed=$TODAY, got [$(get_date "$TSV" L-2026-04-01-001)]"

# --- Test 2: existing count bumps by +1 ---------------------------------
TSV="$TMPROOT/t2.tsv"
seed_tsv "$TSV" $'L-2026-04-05-001\t2\t2026-04-10'

K2B_ACCESS_COUNTS_TSV="$TSV" "$HELPER" L-2026-04-05-001 >/dev/null
[ "$(get_count "$TSV" L-2026-04-05-001)" = "3" ] \
  || fail "test2: expected count=3 (2+1), got [$(get_count "$TSV" L-2026-04-05-001)]"
[ "$(get_date "$TSV" L-2026-04-05-001)" = "$TODAY" ] \
  || fail "test2: last_accessed should be updated to today, got [$(get_date "$TSV" L-2026-04-05-001)]"

# --- Test 3: duplicate L-IDs in one call count as one bump --------------
TSV="$TMPROOT/t3.tsv"
seed_tsv "$TSV" $'L-2026-04-05-001\t5\t2026-04-10'

K2B_ACCESS_COUNTS_TSV="$TSV" "$HELPER" L-2026-04-05-001 L-2026-04-05-001 L-2026-04-05-001 >/dev/null
[ "$(get_count "$TSV" L-2026-04-05-001)" = "6" ] \
  || fail "test3: dedup should bump to 6 (5+1), not 8, got [$(get_count "$TSV" L-2026-04-05-001)]"

# --- Test 4: no argv -> exit 1 (usage) ---------------------------------
TSV="$TMPROOT/t4.tsv"
seed_tsv "$TSV"

K2B_ACCESS_COUNTS_TSV="$TSV" "$HELPER" >/dev/null 2>&1
rc=$?
[ "$rc" = "1" ] || fail "test4: expected exit 1 on no args, got $rc"

# --- Test 5: multiple distinct L-IDs in one call ------------------------
TSV="$TMPROOT/t5.tsv"
seed_tsv "$TSV" \
  $'L-2026-04-01-001\t2\t2026-04-12' \
  $'L-2026-04-05-001\t4\t2026-04-12'
# L-2026-04-10-001 is NOT in the TSV yet; helper starts it at 1.

K2B_ACCESS_COUNTS_TSV="$TSV" "$HELPER" L-2026-04-01-001 L-2026-04-05-001 L-2026-04-10-001 >/dev/null
[ "$(get_count "$TSV" L-2026-04-01-001)" = "3" ] || fail "test5a: L-001 should be 3 (2+1)"
[ "$(get_count "$TSV" L-2026-04-05-001)" = "5" ] || fail "test5b: L-005 should be 5 (4+1)"
[ "$(get_count "$TSV" L-2026-04-10-001)" = "1" ] || fail "test5c: L-010 should be 1 (new)"

# --- Test 6: atomic write -- no temp files left behind ------------------
TSV="$TMPROOT/t6.tsv"
seed_tsv "$TSV"

K2B_ACCESS_COUNTS_TSV="$TSV" "$HELPER" L-2026-04-01-001 >/dev/null
stragglers=$(find "$TMPROOT" -name ".tmp_*" -o -name "*.tmp" 2>/dev/null | wc -l | tr -d ' ')
[ "$stragglers" = "0" ] || fail "test6: expected 0 temp stragglers, found $stragglers"

# --- Test 7: missing TSV file is created on first run -------------------
TSV="$TMPROOT/t7.tsv"  # deliberately NOT created
K2B_ACCESS_COUNTS_TSV="$TSV" "$HELPER" L-2026-04-01-001 >/dev/null
[ -f "$TSV" ] || fail "test7: helper should create TSV if missing"
grep -q $'^learn_id\tcount\tlast_accessed$' "$TSV" || fail "test7: header row missing"
[ "$(get_count "$TSV" L-2026-04-01-001)" = "1" ] || fail "test7: expected count=1"

# --- Test 8: header row preserved across updates ------------------------
TSV="$TMPROOT/t8.tsv"
seed_tsv "$TSV" $'L-2026-04-01-001\t3\t2026-04-10'

K2B_ACCESS_COUNTS_TSV="$TSV" "$HELPER" L-2026-04-01-001 >/dev/null
grep -q $'^learn_id\tcount\tlast_accessed$' "$TSV" || fail "test8: header row was dropped"
grep -q "^# access_counts.tsv" "$TSV" || fail "test8: comment line was dropped"

# --- Test 9: malformed L-ID (wrong shape) still creates an entry (best-effort) ---
# Per v2 design: helper is dumb, it takes whatever L-IDs are passed. The
# callers (promote-learnings / select-lru-victim) are responsible for
# filtering. But we still verify the helper doesn't crash.
TSV="$TMPROOT/t9.tsv"
seed_tsv "$TSV"

K2B_ACCESS_COUNTS_TSV="$TSV" "$HELPER" "not-an-L-id" >/dev/null 2>/dev/null
# Helper should either skip or record; must not crash.
rc=$?
[ "$rc" = "0" ] || fail "test9: expected exit 0 on malformed ID, got $rc"

# --- Test 10: unchanged rows preserved when one ID is bumped ------------
TSV="$TMPROOT/t10.tsv"
seed_tsv "$TSV" \
  $'L-2026-04-01-001\t3\t2026-04-10' \
  $'L-2026-04-05-001\t5\t2026-04-12' \
  $'L-2026-04-10-001\t1\t2026-04-15'

K2B_ACCESS_COUNTS_TSV="$TSV" "$HELPER" L-2026-04-05-001 >/dev/null

[ "$(get_count "$TSV" L-2026-04-01-001)" = "3" ] || fail "test10a: L-001 should be untouched"
[ "$(get_date "$TSV" L-2026-04-01-001)" = "2026-04-10" ] || fail "test10b: L-001 date should be untouched"
[ "$(get_count "$TSV" L-2026-04-05-001)" = "6" ] || fail "test10c: L-005 should bump to 6"
[ "$(get_count "$TSV" L-2026-04-10-001)" = "1" ] || fail "test10d: L-010 should be untouched"

echo "ALL TESTS PASSED"
