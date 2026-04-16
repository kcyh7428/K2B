#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
HELPER="$REPO_ROOT/scripts/observer-mark-processed.sh"

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP" /tmp/k2b-preference-signals-test.lock /tmp/k2b-preference-signals-test.lock.d 2>/dev/null || true' EXIT

JSONL="$TMP/signals.jsonl"
touch "$JSONL"

export K2B_PREFERENCE_SIGNALS="$JSONL"
export K2B_PREFERENCE_SIGNALS_LOCK=/tmp/k2b-preference-signals-test.lock

fail() { echo "FAIL: $*" >&2; exit 1; }

# --- Test 1: happy path without learn_id ---
"$HELPER" a3f7b2c1 confirmed
LAST=$(tail -1 "$JSONL")
echo "$LAST" | python3 -c 'import json,sys; d=json.loads(sys.stdin.read()); assert d["signal_id"]=="a3f7b2c1" and d["action"]=="confirmed" and "learn_id" not in d' \
  || fail "line 1 schema mismatch: $LAST"

# --- Test 2: happy path with learn_id (confirmed path creates a learning) ---
"$HELPER" b4e8c3d2 confirmed L-2026-04-15-003
LAST=$(tail -1 "$JSONL")
echo "$LAST" | python3 -c 'import json,sys; d=json.loads(sys.stdin.read()); assert d["signal_id"]=="b4e8c3d2" and d["action"]=="confirmed" and d["learn_id"]=="L-2026-04-15-003"' \
  || fail "line 2 schema mismatch: $LAST"

# --- Test 3: invalid action rejected ---
if "$HELPER" c5d9e4f3 bogus 2>/dev/null; then fail "expected failure on invalid action"; fi

# --- Test 4: 20 parallel writers, SAME signal_id, cycling actions, no interleaving ---
: > "$JSONL"
ACTIONS=(confirmed rejected watching)
for i in $(seq 1 20); do
  idx=$(( (i - 1) % 3 ))
  "$HELPER" deadbeef "${ACTIONS[$idx]}" &
done
wait
LINES=$(wc -l < "$JSONL" | tr -d ' ')
[ "$LINES" = "20" ] || fail "expected 20 parallel lines, got $LINES"
python3 - <<'PY' "$JSONL"
import json, sys
total = 0
actions = {"confirmed": 0, "rejected": 0, "watching": 0}
for line in open(sys.argv[1]):
    line = line.strip()
    if not line: continue
    d = json.loads(line)  # raises on any interleaved corruption
    assert d["signal_id"] == "deadbeef", f"unexpected signal_id: {d}"
    assert d["type"] == "signal-processed", f"unexpected type: {d}"
    actions[d["action"]] += 1
    total += 1
assert total == 20, f"expected 20 rows, got {total}"
assert actions["confirmed"] + actions["rejected"] + actions["watching"] == 20, actions
print(f"parallel ok (actions={actions})")
PY

echo "observer-mark-processed.test.sh: all tests passed"
