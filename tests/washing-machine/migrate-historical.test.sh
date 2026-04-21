#!/usr/bin/env bash
# tests/washing-machine/migrate-historical.test.sh
# Unit + idempotency tests for scripts/washing-machine/migrate-historical.py.
# Plan: plans/2026-04-21_washing-machine-ship-1.md Commit 1b.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
MIGRATE="$REPO_ROOT/scripts/washing-machine/migrate-historical.py"
WRITER="$REPO_ROOT/scripts/washing-machine/shelf-writer.sh"

if [ ! -f "$MIGRATE" ]; then
  echo "FAIL(precondition): migrate-historical.py missing at $MIGRATE" >&2
  exit 1
fi
if [ ! -x "$WRITER" ]; then
  echo "FAIL(precondition): shelf-writer.sh missing or not executable" >&2
  exit 1
fi

PYTHON_BIN="${WASHING_MACHINE_PYTHON:-python3}"
FIXT="$REPO_ROOT/tests/washing-machine/fixtures"
DAILY_FIXTURE="$FIXT/daily-2025-04-11-drlo.md"
JSONL_FIXTURE="$FIXT/telegram-drlo.jsonl"
DAILY_NODRLO="$FIXT/daily-nodrlo.md"
DAILY_BADUTF8="$FIXT/daily-badutf8.md"
DAILY_MULTICONTACT="$FIXT/daily-multi-contact.md"
DAILY_PREMARKER="$FIXT/daily-pre-marker-contact.md"
DAILY_TWOMARKER="$FIXT/daily-two-marker.md"

for f in "$DAILY_FIXTURE" "$JSONL_FIXTURE" "$DAILY_NODRLO" "$DAILY_BADUTF8" "$DAILY_MULTICONTACT" "$DAILY_PREMARKER" "$DAILY_TWOMARKER"; do
  [ -f "$f" ] || { echo "FAIL(precondition): fixture missing: $f" >&2; exit 1; }
done

TMPDIR="$(mktemp -d)"
LOCK_DIR="$(mktemp -d)"
trap 'rm -rf "$TMPDIR" "$LOCK_DIR"' EXIT

fail() { echo "FAIL: $*" >&2; exit 1; }

run_migrate() {
  # Usage: run_migrate <shelves_dir> <daily_note> <jsonl_glob> <log_path>
  K2B_SHELVES_DIR="$1" K2B_SHELF_LOCK_DIR="$LOCK_DIR" \
    "$PYTHON_BIN" "$MIGRATE" \
      --daily-note "$2" \
      --jsonl-glob "$3" \
      --log-path "$4"
}

# --- Test 1: fresh migration writes exactly 1 row with all expected fields ---
SHELVES1="$TMPDIR/fresh"
mkdir -p "$SHELVES1"
LOG1="$TMPDIR/migration1.log.md"
run_migrate "$SHELVES1" "$DAILY_FIXTURE" "$JSONL_FIXTURE" "$LOG1" \
  || fail "test 1: migration exited non-zero on fresh run"

SHELF1="$SHELVES1/semantic.md"
[ -f "$SHELF1" ] || fail "test 1: semantic.md not created"
ROW_COUNT="$(grep -cE '^- [0-9]{4}-[0-9]{2}-[0-9]{2} \|' "$SHELF1" || true)"
[ "$ROW_COUNT" = "1" ] \
  || fail "test 1: expected 1 row, got $ROW_COUNT"

grep -Fq 'person_Dr-Lo-Hak-Keung' "$SHELF1" || fail "test 1: slug missing"
grep -Fq 'Dr. Lo Hak Keung' "$SHELF1"       || fail "test 1: name missing"
grep -Fq '羅克強醫生' "$SHELF1"              || fail "test 1: name_zh missing"
grep -Fq '2830 3709' "$SHELF1"              || fail "test 1: tel missing"
grep -Fq '9861 9017' "$SHELF1"              || fail "test 1: whatsapp missing"
grep -Fq 'Urology' "$SHELF1"                || fail "test 1: role missing"
grep -Fq "St. Paul's Hospital" "$SHELF1"    || fail "test 1: organization missing"
grep -Fq 'Eastern Hospital Road' "$SHELF1"  || fail "test 1: address missing"
grep -Eq 'source_hash:[a-f0-9]{16,}' "$SHELF1" \
  || fail "test 1: source_hash (hex) missing"
# Date reflects the real capture (2026-04-01), NOT the mis-dated Daily (2025-04-11).
grep -Fq '2026-04-01 |' "$SHELF1" \
  || fail "test 1: row date should be 2026-04-01 (capture), not 2025-04-11 (mis-dated daily)"

# Log must record the write as a non-skip outcome.
[ -f "$LOG1" ] || fail "test 1: migration log not created"
grep -Fq 'wrote' "$LOG1" || fail "test 1: migration log missing 'wrote' entry"

# --- Test 2: re-run is zero-delta (idempotent) ---
BEFORE_HASH="$(shasum "$SHELF1" | awk '{print $1}')"
run_migrate "$SHELVES1" "$DAILY_FIXTURE" "$JSONL_FIXTURE" "$LOG1" \
  || fail "test 2: second migration exited non-zero"
AFTER_HASH="$(shasum "$SHELF1" | awk '{print $1}')"
[ "$BEFORE_HASH" = "$AFTER_HASH" ] \
  || fail "test 2: shelf mutated on re-run (before=$BEFORE_HASH after=$AFTER_HASH)"
AFTER_ROWS="$(grep -cE '^- [0-9]{4}-[0-9]{2}-[0-9]{2} \|' "$SHELF1" || true)"
[ "$AFTER_ROWS" = "1" ] \
  || fail "test 2: expected 1 row after re-run, got $AFTER_ROWS"
# Second run must log the idempotent skip so ops can see re-runs succeeded no-op.
grep -Fq 'skip' "$LOG1" || fail "test 2: migration log missing 'skip' entry for re-run"

# --- Test 3: missing Daily note exits clean, no shelf mutation ---
SHELVES3="$TMPDIR/nodaily"
mkdir -p "$SHELVES3"
LOG3="$TMPDIR/migration3.log.md"
run_migrate "$SHELVES3" "$TMPDIR/does-not-exist.md" "$JSONL_FIXTURE" "$LOG3" \
  || fail "test 3: migration should exit 0 when Daily note is missing"
[ ! -f "$SHELVES3/semantic.md" ] \
  || fail "test 3: shelf file created despite missing Daily note"
[ -f "$LOG3" ] && grep -Fq 'missing' "$LOG3" \
  || fail "test 3: migration log should record the missing-daily warning"

# --- Test 4: Daily note present but missing Dr. Lo block exits clean ---
SHELVES4="$TMPDIR/nodrlo"
mkdir -p "$SHELVES4"
LOG4="$TMPDIR/migration4.log.md"
run_migrate "$SHELVES4" "$DAILY_NODRLO" "$JSONL_FIXTURE" "$LOG4" \
  || fail "test 4: migration should exit 0 when Daily has no Dr. Lo block"
[ ! -f "$SHELVES4/semantic.md" ] \
  || fail "test 4: shelf created despite Daily note lacking Dr. Lo"
[ -f "$LOG4" ] && grep -Fq 'not found' "$LOG4" \
  || fail "test 4: migration log should record the 'not found' warning"

# --- Test 5: parallel migrations produce exactly 1 row (idempotent-under-race) ---
# wait-with-no-args returns 0 regardless of background job failures, so
# individual PIDs are tracked and each exit code is asserted. Without
# this, a silent subprocess crash on 4 of 5 writers would still pass.
SHELVES5="$TMPDIR/parallel"
mkdir -p "$SHELVES5"
LOG5="$TMPDIR/migration5.log.md"
PIDS=()
for _ in $(seq 1 5); do
  K2B_SHELVES_DIR="$SHELVES5" K2B_SHELF_LOCK_DIR="$LOCK_DIR" \
    "$PYTHON_BIN" "$MIGRATE" \
      --daily-note "$DAILY_FIXTURE" \
      --jsonl-glob "$JSONL_FIXTURE" \
      --log-path "$LOG5" &
  PIDS+=("$!")
done
for pid in "${PIDS[@]}"; do
  wait "$pid" \
    || fail "test 5: background migration pid=$pid exited non-zero ($?)"
done
SHELF5="$SHELVES5/semantic.md"
[ -f "$SHELF5" ] || fail "test 5: shelf file not created under parallel migration"
ROWS5="$(grep -cE '^- [0-9]{4}-[0-9]{2}-[0-9]{2} \|' "$SHELF5" || true)"
[ "$ROWS5" = "1" ] \
  || fail "test 5: parallel runs produced $ROWS5 rows (want 1 -- migration lock failed)"
HASH_HITS="$(grep -c 'source_hash:' "$SHELF5" || true)"
[ "$HASH_HITS" = "1" ] \
  || fail "test 5: expected 1 source_hash in shelf, got $HASH_HITS"

# --- Test 6: malformed UTF-8 Daily note is a hard failure (exit 2) ---
# Contract: exit 2 + stderr marker "is unreadable" + error log entry.
# Rationale: the migration target is the authoritative source. A present
# but unreadable Daily note means the Dr. Lo record cannot be recovered;
# reporting success would mask real data loss from /ship-style automation.
SHELVES6="$TMPDIR/badutf8"
mkdir -p "$SHELVES6"
LOG6="$TMPDIR/migration6.log.md"
STDERR6="$TMPDIR/migration6.stderr.txt"
set +e
K2B_SHELVES_DIR="$SHELVES6" K2B_SHELF_LOCK_DIR="$LOCK_DIR" \
  "$PYTHON_BIN" "$MIGRATE" \
    --daily-note "$DAILY_BADUTF8" \
    --jsonl-glob "$JSONL_FIXTURE" \
    --log-path "$LOG6" 2>"$STDERR6"
BAD_RC=$?
set -e
[ "$BAD_RC" = "2" ] \
  || fail "test 6: expected exit 2 on malformed-UTF-8 Daily note, got $BAD_RC"
[ ! -f "$SHELVES6/semantic.md" ] \
  || fail "test 6: shelf created despite malformed-UTF-8 Daily note"
[ -f "$LOG6" ] && grep -Fq 'malformed' "$LOG6" \
  || fail "test 6: log should record the malformed-UTF-8 error"
grep -Fq 'is unreadable' "$STDERR6" \
  || fail "test 6: stderr missing 'is unreadable' marker (got: $(cat "$STDERR6"))"

# --- Test 7: malformed UTF-8 + log-write failure still honours exit 2 ---
# Regression guard: log_entry() wraps filesystem writes and can raise
# OSError itself. If that escapes, the exit-2 contract is broken and
# automation sees a generic runtime failure instead of the documented
# hard-failure code. Use a read-only log dir to simulate the failure.
SHELVES7="$TMPDIR/badutf8_rologs"
mkdir -p "$SHELVES7"
RO_LOG_DIR="$TMPDIR/ro-logs"
mkdir -p "$RO_LOG_DIR"
chmod 555 "$RO_LOG_DIR"
STDERR7="$TMPDIR/migration7.stderr.txt"
set +e
K2B_SHELVES_DIR="$SHELVES7" K2B_SHELF_LOCK_DIR="$LOCK_DIR" \
  "$PYTHON_BIN" "$MIGRATE" \
    --daily-note "$DAILY_BADUTF8" \
    --jsonl-glob "$JSONL_FIXTURE" \
    --log-path "$RO_LOG_DIR/migration.log.md" 2>"$STDERR7"
BAD_RC=$?
set -e
chmod 755 "$RO_LOG_DIR"
[ "$BAD_RC" = "2" ] \
  || fail "test 7: exit 2 must survive log-write failure (got $BAD_RC)"
grep -Fq 'is unreadable' "$STDERR7" \
  || fail "test 7: primary stderr marker missing even when log write failed"

# --- Test 8: successful write + log-write failure still exits 0 ---
# Regression guard: the success path must treat log append as best-effort
# the same way the unreadable-source path does. If log_entry() raises
# mid-run after the shelf has already been mutated, flipping to exit 1
# would lie to automation about the actual state.
SHELVES8="$TMPDIR/ok_rologs"
mkdir -p "$SHELVES8"
RO_LOG_DIR8="$TMPDIR/ro-logs-success"
mkdir -p "$RO_LOG_DIR8"
chmod 555 "$RO_LOG_DIR8"
STDERR8="$TMPDIR/migration8.stderr.txt"
set +e
K2B_SHELVES_DIR="$SHELVES8" K2B_SHELF_LOCK_DIR="$LOCK_DIR" \
  "$PYTHON_BIN" "$MIGRATE" \
    --daily-note "$DAILY_FIXTURE" \
    --jsonl-glob "$JSONL_FIXTURE" \
    --log-path "$RO_LOG_DIR8/migration.log.md" 2>"$STDERR8"
GOOD_RC=$?
set -e
chmod 755 "$RO_LOG_DIR8"
[ "$GOOD_RC" = "0" ] \
  || fail "test 8: log-write failure must not flip success to failure (got $GOOD_RC)"
[ -f "$SHELVES8/semantic.md" ] \
  || fail "test 8: shelf not created despite successful write"
ROWS8="$(grep -cE '^- [0-9]{4}-[0-9]{2}-[0-9]{2} \|' "$SHELVES8/semantic.md" || true)"
[ "$ROWS8" = "1" ] \
  || fail "test 8: expected 1 row, got $ROWS8"
grep -Fq 'failed to append to log' "$STDERR8" \
  || fail "test 8: stderr should note the log-append failure (got: $(cat "$STDERR8"))"

# --- Test 9: marker present + fields >15 lines below = exit 2 (hard failure) ---
# Contract: "marker found but record unrecoverable" is a data-loss signal,
# not a silent skip. Must: exit 2, write shelf nothing, stderr marker
# "incomplete", and log "incomplete" entry.
SHELVES9="$TMPDIR/multi"
mkdir -p "$SHELVES9"
LOG9="$TMPDIR/migration9.log.md"
STDERR9="$TMPDIR/migration9.stderr.txt"
set +e
K2B_SHELVES_DIR="$SHELVES9" K2B_SHELF_LOCK_DIR="$LOCK_DIR" \
  "$PYTHON_BIN" "$MIGRATE" \
    --daily-note "$DAILY_MULTICONTACT" \
    --jsonl-glob "$JSONL_FIXTURE" \
    --log-path "$LOG9" 2>"$STDERR9"
MULTI_RC=$?
set -e
[ "$MULTI_RC" = "2" ] \
  || fail "test 9: expected exit 2 when marker present but fields out of proximity (got $MULTI_RC)"
[ ! -f "$SHELVES9/semantic.md" ] \
  || fail "test 9: shelf created despite out-of-proximity fields (would contaminate with supplier data)"
grep -Fq 'incomplete' "$STDERR9" \
  || fail "test 9: stderr missing 'incomplete' marker"
[ -f "$LOG9" ] && grep -Fq 'incomplete' "$LOG9" \
  || fail "test 9: log should record the 'incomplete' failure"

# --- Test 10: pre-marker contact must NOT contaminate Dr. Lo record ---
# Fixture has an unrelated supplier's Tel/WhatsApp/Address directly ABOVE
# the Dr. Lo marker (within 15 lines). Forward-only window means those
# fields are outside the extraction scope -- extractor must raise
# IncompleteBlock -> exit 2, NOT silently attribute supplier's fields to
# Dr. Lo. A symmetric-window extractor would fail this test.
SHELVES10="$TMPDIR/premarker"
mkdir -p "$SHELVES10"
LOG10="$TMPDIR/migration10.log.md"
STDERR10="$TMPDIR/migration10.stderr.txt"
set +e
K2B_SHELVES_DIR="$SHELVES10" K2B_SHELF_LOCK_DIR="$LOCK_DIR" \
  "$PYTHON_BIN" "$MIGRATE" \
    --daily-note "$DAILY_PREMARKER" \
    --jsonl-glob "$JSONL_FIXTURE" \
    --log-path "$LOG10" 2>"$STDERR10"
PRE_RC=$?
set -e
[ "$PRE_RC" = "2" ] \
  || fail "test 10: pre-marker contact must trigger IncompleteBlock (exit 2, got $PRE_RC)"
[ ! -f "$SHELVES10/semantic.md" ] \
  || fail "test 10: shelf created -- supplier's fields contaminated Dr. Lo record"
# Sanity: if contamination had occurred, these bad values would be in the shelf.
if [ -f "$SHELVES10/semantic.md" ]; then
  ! grep -Fq '9999 9999' "$SHELVES10/semantic.md" \
    || fail "test 10: supplier tel 9999 9999 stolen into shelf"
  ! grep -Fq '8888 8888' "$SHELVES10/semantic.md" \
    || fail "test 10: supplier whatsapp 8888 8888 stolen into shelf"
fi

# --- Test 11: two marker occurrences -- extractor must try all in order ---
# First occurrence (Focus Today) has no contact block in its forward window.
# Second occurrence (Key Activities) has the real contact block. Migration
# must recognize the second one instead of hard-failing on the first.
SHELVES11="$TMPDIR/twomarker"
mkdir -p "$SHELVES11"
LOG11="$TMPDIR/migration11.log.md"
K2B_SHELVES_DIR="$SHELVES11" K2B_SHELF_LOCK_DIR="$LOCK_DIR" \
  "$PYTHON_BIN" "$MIGRATE" \
    --daily-note "$DAILY_TWOMARKER" \
    --jsonl-glob "$JSONL_FIXTURE" \
    --log-path "$LOG11" \
  || fail "test 11: migration should succeed when a later marker has fields"
SHELF11="$SHELVES11/semantic.md"
[ -f "$SHELF11" ] || fail "test 11: shelf not created"
grep -Fq '2830 3709' "$SHELF11" \
  || fail "test 11: Dr. Lo tel missing (extractor gave up on first marker)"

# --- Test 12: stable-identity idempotency (slug+date beats content drift) ---
# Simulate "Daily note was corrected -> content hash changed" by pre-seeding
# a shelf row with the real slug+date but a nonsense source_hash. A content-
# only check would miss it and append a duplicate. The slug+date check
# must recognize the existing record and skip.
SHELVES12="$TMPDIR/identity"
mkdir -p "$SHELVES12"
LOG12="$TMPDIR/migration12.log.md"
K2B_SHELVES_DIR="$SHELVES12" K2B_SHELF_LOCK_DIR="$LOCK_DIR" \
  "$WRITER" --shelf semantic --date 2026-04-01 --type contact \
    --slug person_Dr-Lo-Hak-Keung \
    --attr "tel:0000 0000" \
    --attr "source_hash:deadbeefcafebabe" \
  || fail "test 12 prep: seed write failed"
BEFORE_HASH="$(shasum "$SHELVES12/semantic.md" | awk '{print $1}')"
BEFORE_ROWS="$(grep -cE '^- [0-9]{4}' "$SHELVES12/semantic.md" || true)"
[ "$BEFORE_ROWS" = "1" ] || fail "test 12 prep: expected 1 seed row, got $BEFORE_ROWS"

K2B_SHELVES_DIR="$SHELVES12" K2B_SHELF_LOCK_DIR="$LOCK_DIR" \
  "$PYTHON_BIN" "$MIGRATE" \
    --daily-note "$DAILY_FIXTURE" \
    --jsonl-glob "$JSONL_FIXTURE" \
    --log-path "$LOG12" \
  || fail "test 12: migration should skip cleanly when slug+date already present"

AFTER_HASH="$(shasum "$SHELVES12/semantic.md" | awk '{print $1}')"
AFTER_ROWS="$(grep -cE '^- [0-9]{4}' "$SHELVES12/semantic.md" || true)"
[ "$BEFORE_HASH" = "$AFTER_HASH" ] \
  || fail "test 12: shelf mutated on content-drift rerun (identity dedupe failed)"
[ "$AFTER_ROWS" = "1" ] \
  || fail "test 12: expected 1 row after identity-dedupe skip, got $AFTER_ROWS"
grep -Fq 'slug=person_Dr-Lo-Hak-Keung+date=2026-04-01' "$LOG12" \
  || fail "test 12: log should cite the slug+date identity match"

echo "migrate-historical.test.sh: all 12 tests passed"
