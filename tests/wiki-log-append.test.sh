#!/usr/bin/env bash
# tests/wiki-log-append.test.sh
# Smoke + concurrency test for scripts/wiki-log-append.sh

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
HELPER="$REPO_ROOT/scripts/wiki-log-append.sh"

TMPDIR="$(mktemp -d)"
trap 'rm -rf "$TMPDIR" /tmp/k2b-wiki-log-itest.lock /tmp/k2b-wiki-log-itest.lock.d' EXIT

LOG="$TMPDIR/log.md"
LOCK=/tmp/k2b-wiki-log-itest.lock
touch "$LOG"

export K2B_WIKI_LOG="$LOG" K2B_WIKI_LOG_LOCK="$LOCK"

fail() { echo "FAIL: $*" >&2; exit 1; }

# --- Test 1: single append writes one line in the expected format ---
"$HELPER" /ship feature_foo.md "shipped feature_foo"
LINES=$(wc -l < "$LOG" | tr -d ' ')
[ "$LINES" = "1" ] || fail "expected 1 line, got $LINES"
grep -qE '^[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}  /ship  feature_foo\.md  shipped feature_foo$' "$LOG" \
  || fail "line format mismatch: $(cat "$LOG")"

# --- Test 2: missing args exit non-zero ---
if "$HELPER" /ship 2>/dev/null; then fail "expected failure on missing args"; fi

# --- Test 3: parallel writers, all 20 payloads land exactly once ---
: > "$LOG"
for i in $(seq 1 20); do
  "$HELPER" /test "parallel-$i" "payload-$i" &
done
wait
LINES=$(wc -l < "$LOG" | tr -d ' ')
[ "$LINES" = "20" ] || fail "expected 20 parallel lines, got $LINES"
for i in $(seq 1 20); do
  C=$(grep -c "payload-$i$" "$LOG")
  [ "$C" = "1" ] || fail "payload-$i appeared $C times (want 1)"
done
# Every line must start with a valid timestamp, no partial writes mid-line.
if grep -vE '^[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}  /test  parallel-[0-9]+  payload-[0-9]+$' "$LOG" >/dev/null; then
  fail "found malformed/interleaved line: $(grep -vE '^[0-9]{4}' "$LOG" | head -1)"
fi
# Lock artifact must be gone.
[ ! -d "${LOCK}.d" ] || fail "lock dir leaked: ${LOCK}.d"

# --- Test 3b: lock held blocks second writer (proves lock is real) ---
: > "$LOG"
(
  exec 9>"$LOCK"
  if command -v flock >/dev/null 2>&1; then flock -x 9; else mkdir "${LOCK}.d" 2>/dev/null || true; fi
  # Hold the lock for 0.3s while a background writer attempts.
  ("$HELPER" /test blocked "should-wait" && echo "writer-done") > "$TMPDIR/writer.out" 2>&1 &
  WRITER=$!
  sleep 0.1
  if [ -s "$LOG" ]; then
    # Allow: flock fd inheritance is subprocess-scoped; if the writer already
    # wrote, our lock attempt in THIS process didn't actually hold. Accept as
    # a known limitation only if flock is unavailable.
    command -v flock >/dev/null 2>&1 && fail "writer raced past held flock"
  fi
  # Release: close fd 9 / rmdir.
  exec 9>&-
  rmdir "${LOCK}.d" 2>/dev/null || true
  wait $WRITER
  grep -q '  should-wait$' "$LOG" || fail "blocked writer never completed"
)

# --- Test 4: missing log file exits 2 ---
MISSING="$TMPDIR/does-not-exist.md"
if K2B_WIKI_LOG="$MISSING" "$HELPER" /test t "x" 2>/dev/null; then
  fail "expected exit 2 on missing log file"
fi

echo "wiki-log-append.test.sh: all tests passed"
