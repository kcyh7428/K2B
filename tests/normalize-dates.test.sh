#!/usr/bin/env bash
# tests/normalize-dates.test.sh
# Tests for scripts/normalize-dates.py.
#
# The helper reads text from stdin, rewrites relative date expressions
# ("yesterday", "3 days ago", "last Monday", etc.) to ISO YYYY-MM-DD,
# anchored to the argv[1] date (NOT system now()), and writes to stdout.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
HELPER="$REPO_ROOT/scripts/normalize-dates.py"

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

run() {
  # run "<anchor>" "<input>" -> prints normalized output
  printf '%s' "$2" | "$HELPER" "$1"
}

assert_eq() {
  # assert_eq "<label>" "<expected>" "<actual>"
  if [ "$2" != "$3" ]; then
    fail "$1: expected [$2], got [$3]"
  fi
}

# --- Test 1: "yesterday" -------------------------------------------------
out=$(run "2026-04-19" "yesterday's fix was solid")
assert_eq "test1 yesterday" "2026-04-18's fix was solid" "$out"

# --- Test 2: "today" ----------------------------------------------------
out=$(run "2026-04-19" "today I shipped feature X")
assert_eq "test2 today" "2026-04-19 I shipped feature X" "$out"

# --- Test 3: "tomorrow" -------------------------------------------------
out=$(run "2026-04-19" "plan for tomorrow is to review")
assert_eq "test3 tomorrow" "plan for 2026-04-20 is to review" "$out"

# --- Test 4: "3 days ago" -----------------------------------------------
out=$(run "2026-04-19" "fixed 3 days ago")
assert_eq "test4 3 days ago" "fixed 2026-04-16" "$out"

# --- Test 5: "a week ago" / "1 week ago" / "2 weeks ago" ----------------
out=$(run "2026-04-19" "broke a week ago")
assert_eq "test5a a week ago" "broke 2026-04-12" "$out"
out=$(run "2026-04-19" "broke 1 week ago")
assert_eq "test5b 1 week ago" "broke 2026-04-12" "$out"
out=$(run "2026-04-19" "broke 2 weeks ago")
assert_eq "test5c 2 weeks ago" "broke 2026-04-05" "$out"

# --- Test 6: "last week" -------------------------------------------------
out=$(run "2026-04-19" "decided last week")
assert_eq "test6 last week" "decided 2026-04-12" "$out"

# --- Test 7: case-insensitive -------------------------------------------
out=$(run "2026-04-19" "Yesterday was rough")
assert_eq "test7 Yesterday" "2026-04-18 was rough" "$out"

# --- Test 8: no matches unchanged ---------------------------------------
out=$(run "2026-04-19" "nothing relative here")
assert_eq "test8 unchanged" "nothing relative here" "$out"

# --- Test 9: multiple matches in one line -------------------------------
out=$(run "2026-04-19" "yesterday and today")
assert_eq "test9 multi" "2026-04-18 and 2026-04-19" "$out"

# --- Test 10: "last Monday" (anchor 2026-04-19 is Sunday) ---------------
# Most recent prior Monday before Sun 2026-04-19 is 2026-04-13.
out=$(run "2026-04-19" "last Monday we agreed")
assert_eq "test10 last Monday" "2026-04-13 we agreed" "$out"

# --- Test 11: "last Sunday" on a Sunday -> previous Sunday --------------
# Anchor Sun 2026-04-19 -> "last Sunday" = 2026-04-12, not the anchor itself.
out=$(run "2026-04-19" "last Sunday we shipped")
assert_eq "test11 last Sunday" "2026-04-12 we shipped" "$out"

# --- Test 12: month boundary --------------------------------------------
out=$(run "2026-04-03" "fixed 5 days ago")
assert_eq "test12 month boundary" "fixed 2026-03-29" "$out"

# --- Test 13: year boundary ---------------------------------------------
out=$(run "2026-01-03" "fixed 5 days ago")
assert_eq "test13 year boundary" "fixed 2025-12-29" "$out"

# --- Test 14: "last month" ----------------------------------------------
out=$(run "2026-04-19" "last month we planned")
assert_eq "test14 last month" "2026-03-19 we planned" "$out"

# --- Test 15: "last month" with day clamp (Mar 31 -> Feb 28) ------------
out=$(run "2026-03-31" "last month we planned")
assert_eq "test15 last month clamp" "2026-02-28 we planned" "$out"

# --- Test 16: "last year" -----------------------------------------------
out=$(run "2026-04-19" "last year we started")
assert_eq "test16 last year" "2025-04-19 we started" "$out"

# --- Test 17: empty input ------------------------------------------------
out=$(run "2026-04-19" "")
assert_eq "test17 empty" "" "$out"

# --- Test 18: already-ISO dates left alone ------------------------------
out=$(run "2026-04-19" "on 2026-04-15 we decided")
assert_eq "test18 existing ISO" "on 2026-04-15 we decided" "$out"

# --- Test 19: "yesterday's" possessive ---------------------------------
out=$(run "2026-04-19" "yesterday's bug")
assert_eq "test19 possessive" "2026-04-18's bug" "$out"

# --- Test 20: inside a word should NOT match ----------------------------
# "todayish" shouldn't become "2026-04-19ish". Word boundary discipline.
out=$(run "2026-04-19" "todayish vibe")
assert_eq "test20 no partial match" "todayish vibe" "$out"

# --- Test 21: "an hour ago" is NOT a day/week/month -> leave alone ------
out=$(run "2026-04-19" "an hour ago I saw it")
assert_eq "test21 hour ago unchanged" "an hour ago I saw it" "$out"

# --- Test 22: invalid anchor exits non-zero -----------------------------
if printf 'yesterday' | "$HELPER" "not-a-date" 2>/dev/null; then
  fail "test22 expected failure on invalid anchor"
fi

# --- Test 23: missing anchor exits non-zero -----------------------------
if printf 'yesterday' | "$HELPER" 2>/dev/null; then
  fail "test23 expected failure on missing anchor"
fi

# --- Test 24: newlines preserved byte-for-byte --------------------------
# Command substitution strips trailing newlines, so compare via temp file
# instead to catch regressions that would silently drop the final \n.
tmp_in=$(mktemp); tmp_out=$(mktemp); tmp_exp=$(mktemp)
trap 'rm -f "$tmp_in" "$tmp_out" "$tmp_exp"' EXIT
printf 'yesterday\ntoday\n' > "$tmp_in"
printf '2026-04-18\n2026-04-19\n' > "$tmp_exp"
"$HELPER" "2026-04-19" < "$tmp_in" > "$tmp_out"
cmp -s "$tmp_out" "$tmp_exp" || fail "test24 newlines: byte-diff mismatch: expected [$(cat "$tmp_exp" | xxd)], got [$(cat "$tmp_out" | xxd)]"

# --- Test 25: "one day ago" (word numeral) ------------------------------
out=$(run "2026-04-19" "broke one day ago")
assert_eq "test25 one day ago" "broke 2026-04-18" "$out"

# --- Test 26: "two weeks ago" (word numeral) ----------------------------
out=$(run "2026-04-19" "decided two weeks ago")
assert_eq "test26 two weeks ago" "decided 2026-04-05" "$out"

# --- Test 27: "last Monday" when anchor IS Monday -> previous Monday ----
# 2026-04-13 is a Monday; "last Monday" must return 2026-04-06, not 2026-04-13.
out=$(run "2026-04-13" "last Monday we agreed")
assert_eq "test27 last Monday on Monday" "2026-04-06 we agreed" "$out"

# --- Test 28: "last year" leap-day anchor -> Feb 28 clamp ---------------
# From 2024-02-29 (a leap day) "last year" must clamp to 2023-02-28.
out=$(run "2024-02-29" "last year we shipped")
assert_eq "test28 leap year clamp" "2023-02-28 we shipped" "$out"

# --- Test 29: "N years ago" leap-day anchor -----------------------------
out=$(run "2024-02-29" "1 year ago we shipped")
assert_eq "test29 1 year ago leap" "2023-02-28 we shipped" "$out"

echo "ALL TESTS PASSED"
