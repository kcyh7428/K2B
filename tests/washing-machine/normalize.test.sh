#!/usr/bin/env bash
# tests/washing-machine/normalize.test.sh
# Unit tests for scripts/washing-machine/normalize.py (Ship 1 Commit 3).
# No API calls. Exercises backward + forward relative-date resolution.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
NORMALIZE="$REPO_ROOT/scripts/washing-machine/normalize.py"

if [ ! -x "$NORMALIZE" ] && [ ! -f "$NORMALIZE" ]; then
  echo "FAIL(precondition): normalize.py missing at $NORMALIZE" >&2
  exit 1
fi

WASHING_MACHINE_ENV="${WASHING_MACHINE_ENV:-$HOME/.config/k2b/washing-machine.env}"
if [ -f "$WASHING_MACHINE_ENV" ]; then
  # shellcheck disable=SC1090
  . "$WASHING_MACHINE_ENV"
fi
PYTHON_BIN="${WASHING_MACHINE_PYTHON:-python3}"

PASS=0
FAIL=0

fail() { echo "FAIL: $*" >&2; FAIL=$((FAIL + 1)); }
pass() { PASS=$((PASS + 1)); }

assert_eq() {
  local label="$1" expected="$2" actual="$3"
  if [ "$expected" = "$actual" ]; then
    pass
  else
    fail "$label\n  expected: $expected\n  actual:   $actual"
  fi
}

run_norm() {
  # Usage: run_norm <anchor> <input>
  printf '%s' "$2" | "$PYTHON_BIN" "$NORMALIZE" --anchor "$1"
}

run_norm_json() {
  printf '%s' "$2" | "$PYTHON_BIN" "$NORMALIZE" --anchor "$1" --json
}

# --- Test 1: tomorrow ---
got="$(run_norm 2026-04-01 "meeting tomorrow at 9am")"
assert_eq "tomorrow" "meeting 2026-04-02 at 9am" "$got"

# --- Test 2: yesterday ---
got="$(run_norm 2026-04-01 "saw her yesterday")"
assert_eq "yesterday" "saw her 2026-03-31" "$got"

# --- Test 3: next Friday against Wednesday anchor ---
got="$(run_norm 2026-04-01 "Next Friday is the SJM board")"
assert_eq "next-friday-from-wed" "2026-04-03 is the SJM board" "$got"

# --- Test 4: next Friday against Friday anchor -> skip to the Friday after ---
got="$(run_norm 2026-04-03 "Next Friday meeting")"
assert_eq "next-friday-from-fri" "2026-04-10 meeting" "$got"

# --- Test 5: N days ago (backward wrapper) ---
got="$(run_norm 2026-04-01 "signed 3 days ago")"
assert_eq "3-days-ago" "signed 2026-03-29" "$got"

# --- Test 6: combined forward + backward in one sentence ---
got="$(run_norm 2026-04-01 "yesterday and tomorrow and next Friday")"
assert_eq "combined" "2026-03-31 and 2026-04-02 and 2026-04-03" "$got"

# --- Test 7: no relative date -> text unchanged ---
got="$(run_norm 2026-04-01 "Andrew's number is 9876 5432")"
assert_eq "no-relative" "Andrew's number is 9876 5432" "$got"

# --- Test 8: --json emits substitutions ---
got="$(run_norm_json 2026-04-01 "tomorrow meeting")"
if printf '%s' "$got" | "$PYTHON_BIN" -c "
import json, sys
d = json.loads(sys.stdin.read())
assert d['rewritten_text'] == '2026-04-02 meeting', d
kinds = [s['kind'] for s in d['substitutions']]
assert kinds == ['tomorrow'], kinds
print('ok')
" >/dev/null 2>&1; then
  pass
else
  fail "json-substitutions\n  got: $got"
fi

# --- Test 9: invalid anchor exits 2 ---
if printf 'test' | "$PYTHON_BIN" "$NORMALIZE" --anchor not-a-date >/dev/null 2>&1; then
  fail "invalid-anchor-should-exit-nonzero"
else
  rc=$?
  if [ "$rc" -eq 2 ]; then
    pass
  else
    fail "invalid-anchor exit code: expected 2, got $rc"
  fi
fi

# --- Test 10: precondition -- normalize.py --help exits 0 ---
# Proves the module imports successfully. Since _load_backward() runs at
# import time, a missing / broken scripts/normalize-dates.py would make
# even --help exit non-zero. Cheap canary against silent dependency breakage.
if "$PYTHON_BIN" "$NORMALIZE" --help >/dev/null 2>&1; then
  pass
else
  rc=$?
  fail "normalize.py --help should exit 0 (proves import works); got $rc"
fi

# --- Test 11: composability -- relative + already-ISO date in one input ---
# Guards against a future forward_pass regex accidentally matching ISO
# dates produced by the backward pass. "tomorrow is 2026-04-02" must
# produce "2026-04-02 is 2026-04-02", not double-substituted output.
got="$(run_norm 2026-04-01 "tomorrow is 2026-04-02")"
assert_eq "composability-tomorrow-plus-iso" "2026-04-02 is 2026-04-02" "$got"

# --- Test 12: composability -- ISO date near "next" keyword must not mis-match ---
# Forward pass regex is \bnext\s+(weekday|week)\b; a stray "next" near an
# ISO should never turn the ISO into a date. Lock this down with an example.
got="$(run_norm 2026-04-01 "2026-04-10 then next Friday")"
assert_eq "composability-iso-then-next-friday" "2026-04-10 then 2026-04-03" "$got"

echo "normalize.test.sh: $PASS pass, $FAIL fail"
[ "$FAIL" -eq 0 ]
