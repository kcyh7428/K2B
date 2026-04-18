#!/usr/bin/env bash
# tests/sort-key.test.sh
# Unit tests for scripts/lib/importance.py::importance_score().
#
# Formula under test:
#   score = (reinforcement_count * max(1, access_count)) / max(1, age_in_days)
#
# All invariants here follow from the plan at
# plans/2026-04-19_importance-weighted-rule-promotion.md

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
HELPER="$REPO_ROOT/scripts/lib/importance.py"

fail() { echo "FAIL: $*" >&2; exit 1; }

run() {
  # run "<reinforced>" "<access>" "<last_reinforced_iso>" "<today_iso>"
  python3 -c "
import sys
sys.path.insert(0, '$REPO_ROOT/scripts/lib')
from importance import importance_score
print(importance_score(int('$1'), int('$2'), '$3', '$4'))
"
}

assert_close() {
  # assert_close "<label>" "<expected>" "<actual>" [<tolerance>]
  local label="$1" expected="$2" actual="$3" tol="${4:-0.0001}"
  python3 -c "
import sys
delta = abs(float('$expected') - float('$actual'))
if delta > float('$tol'):
    print('$label: expected $expected, got $actual (delta=%.6f, tol=$tol)' % delta); sys.exit(1)
" || fail "$label (expected=$expected actual=$actual)"
}

# --- Test 1: baseline -- reinforced=3, access=2, age=7 ------------------
out=$(run 3 2 2026-04-12 2026-04-19)
assert_close "test1 baseline" "0.857142857" "$out"  # 3*2/7

# --- Test 2: access_count=0 floored to 1 --------------------------------
out=$(run 5 0 2026-04-14 2026-04-19)
assert_close "test2 access 0 floored" "1.0" "$out"  # 5*1/5

# --- Test 3: age=0 floored to 1 -----------------------------------------
out=$(run 3 4 2026-04-19 2026-04-19)
assert_close "test3 age 0 floored" "12.0" "$out"  # 3*4/1

# --- Test 4: reinforcement=0 propagates to score=0 ----------------------
# Rationale: a 0-reinforcement learning SHOULDN'T rank above anything.
out=$(run 0 5 2026-04-18 2026-04-19)
assert_close "test4 reinforced 0" "0.0" "$out"

# --- Test 5: higher reinforcement beats lower at same access/age --------
a=$(run 2 3 2026-04-12 2026-04-19)
b=$(run 5 3 2026-04-12 2026-04-19)
python3 -c "import sys; sys.exit(0 if float('$b') > float('$a') else 1)" \
  || fail "test5: reinforced=5 should beat reinforced=2 at identical access/age ($a vs $b)"

# --- Test 6: higher access beats lower at same reinforced/age -----------
a=$(run 3 1 2026-04-12 2026-04-19)
b=$(run 3 10 2026-04-12 2026-04-19)
python3 -c "import sys; sys.exit(0 if float('$b') > float('$a') else 1)" \
  || fail "test6: access=10 should beat access=1 at identical reinforced/age ($a vs $b)"

# --- Test 7: fresher age beats staler at same reinforced/access ---------
stale=$(run 3 2 2026-03-19 2026-04-19)
fresh=$(run 3 2 2026-04-18 2026-04-19)
python3 -c "import sys; sys.exit(0 if float('$fresh') > float('$stale') else 1)" \
  || fail "test7: fresh should beat stale ($fresh vs $stale)"

# --- Test 8: leap-year age gap resolves correctly -----------------------
# From 2024-02-28 to 2024-03-01 = 2 days (crosses leap day).
out=$(run 4 1 2024-02-28 2024-03-01)
assert_close "test8 leap" "2.0" "$out"  # 4*1/2

# --- Test 9: year-crossing age gap --------------------------------------
# From 2025-12-31 to 2026-01-02 = 2 days.
out=$(run 6 1 2025-12-31 2026-01-02)
assert_close "test9 year cross" "3.0" "$out"  # 6*1/2

# --- Test 10: last_reinforced in the future treated as age=1 ------------
# Defensive: clock skew shouldn't produce negative ages, which would flip
# the sign of the score.
out=$(run 4 2 2026-04-20 2026-04-19)
assert_close "test10 future age" "8.0" "$out"  # 4*2/1 (age clamped to 1)

# --- Test 11: malformed last_reinforced falls back to today -------------
# "" or "0000-00-00" or garbage -> age defaults to 1 (score = r*a/1).
out=$(run 3 2 "" 2026-04-19)
assert_close "test11 empty date" "6.0" "$out"

out=$(run 3 2 "0000-00-00" 2026-04-19)
assert_close "test11b zero date" "6.0" "$out"

# --- Test 12: very large counts don't overflow --------------------------
out=$(run 1000000 1000000 2026-04-12 2026-04-19)
assert_close "test12 big numbers" "142857142857.142857" "$out" "1000000"  # loose tol for float

echo "ALL TESTS PASSED"
