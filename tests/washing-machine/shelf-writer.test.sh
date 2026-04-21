#!/usr/bin/env bash
# tests/washing-machine/shelf-writer.test.sh
# Unit + concurrency + encoding tests for scripts/washing-machine/shelf-writer.sh.
# Plan: plans/2026-04-21_washing-machine-ship-1.md Commit 1.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
WRITER="$REPO_ROOT/scripts/washing-machine/shelf-writer.sh"
LIB="$REPO_ROOT/scripts/washing-machine/lib/shelf_rows.py"

if [ ! -x "$WRITER" ]; then
  echo "FAIL(precondition): shelf-writer.sh missing or not executable at $WRITER" >&2
  exit 1
fi
if [ ! -f "$LIB" ]; then
  echo "FAIL(precondition): lib/shelf_rows.py missing at $LIB" >&2
  exit 1
fi

PYTHON_BIN="${WASHING_MACHINE_PYTHON:-python3}"

TMPDIR="$(mktemp -d)"
LOCK_DIR="$(mktemp -d)"
cleanup() {
  chmod -R u+w "$TMPDIR" 2>/dev/null || true
  rm -rf "$TMPDIR" "$LOCK_DIR" 2>/dev/null || true
}
trap cleanup EXIT

export K2B_SHELVES_DIR="$TMPDIR"
export K2B_SHELF_LOCK_DIR="$LOCK_DIR"

fail() { echo "FAIL: $*" >&2; exit 1; }

frontmatter_get() {
  # Usage: frontmatter_get <file> <key>
  awk -v k="$2" '
    BEGIN { fm=0; seen=0 }
    /^---$/ {
      if (!seen) { fm=1; seen=1; next }
      else if (fm) { fm=0; next }
    }
    fm && $0 ~ "^" k ":" {
      sub("^" k ":[[:space:]]*", "")
      print
      exit
    }
  ' "$1"
}

count_rows() {
  # Usage: count_rows <file>   -- count row bullets after "## Rows" header.
  awk '
    /^## Rows/ { in_rows=1; next }
    in_rows && /^- / { n++ }
    END { print n+0 }
  ' "$1"
}

# --- Test 1: empty append (shelf file does not exist) ---
"$WRITER" --shelf semantic --date 2026-04-21 --type fact \
  --slug person_Dr-Lo-Hak-Keung \
  --attr "tel:2830 3709" --attr "role:Urology" \
  || fail "test 1: writer exited non-zero on first-ever append"

TARGET="$TMPDIR/semantic.md"
[ -f "$TARGET" ] || fail "test 1: shelf file not created at $TARGET"
grep -q '^---$' "$TARGET" || fail "test 1: no frontmatter written"
[ "$(frontmatter_get "$TARGET" row-count)" = "1" ] \
  || fail "test 1: row-count != 1 (got '$(frontmatter_get "$TARGET" row-count)')"
[ "$(count_rows "$TARGET")" = "1" ] \
  || fail "test 1: expected 1 row body, got $(count_rows "$TARGET")"
grep -Fq '2026-04-21 | fact | person_Dr-Lo-Hak-Keung | tel:2830 3709 | role:Urology' "$TARGET" \
  || fail "test 1: row text not present as expected"
# Exactly one "## Rows" header (no duplicate injection from the awk rewrite).
[ "$(grep -c '^## Rows' "$TARGET")" = "1" ] \
  || fail "test 1: expected exactly one '## Rows' header, got $(grep -c '^## Rows' "$TARGET")"

# --- Test 2: existing append (row-count increments, old rows intact) ---
"$WRITER" --shelf semantic --date 2026-04-22 --type decision \
  --slug migrate-historical --attr "rollback-safe:true" \
  || fail "test 2: writer exited non-zero on second append"
[ "$(frontmatter_get "$TARGET" row-count)" = "2" ] \
  || fail "test 2: row-count != 2 after second append"
[ "$(count_rows "$TARGET")" = "2" ] \
  || fail "test 2: expected 2 row bodies, got $(count_rows "$TARGET")"
# Old row still present.
grep -Fq '2026-04-21 | fact | person_Dr-Lo-Hak-Keung | tel:2830 3709 | role:Urology' "$TARGET" \
  || fail "test 2: original row clobbered"
# New row present.
grep -Fq '2026-04-22 | decision | migrate-historical | rollback-safe:true' "$TARGET" \
  || fail "test 2: new row missing"
# Still exactly one "## Rows" header after second append.
[ "$(grep -c '^## Rows' "$TARGET")" = "1" ] \
  || fail "test 2: '## Rows' header duplicated on second append (got $(grep -c '^## Rows' "$TARGET"))"

# --- Test 3: concurrent writers (20 parallel, all land once) ---
CONCURRENT_SHELF="$TMPDIR/concurrent.md"
rm -f "$CONCURRENT_SHELF"
for i in $(seq 1 20); do
  "$WRITER" --shelf concurrent --date 2026-04-21 --type fact \
    --slug "parallel_${i}" --attr "idx:${i}" &
done
wait
[ "$(frontmatter_get "$CONCURRENT_SHELF" row-count)" = "20" ] \
  || fail "test 3: row-count != 20 after parallel writers (got '$(frontmatter_get "$CONCURRENT_SHELF" row-count)')"
[ "$(count_rows "$CONCURRENT_SHELF")" = "20" ] \
  || fail "test 3: row body count != 20 (got $(count_rows "$CONCURRENT_SHELF"))"
for i in $(seq 1 20); do
  C=$(grep -cF "parallel_${i} | idx:${i}" "$CONCURRENT_SHELF" || true)
  [ "$C" = "1" ] || fail "test 3: parallel_${i} appeared $C times (want 1)"
done
# No malformed rows sneaked in from interleaved writes.
if grep -E '^- ' "$CONCURRENT_SHELF" | grep -vE '^- 2026-04-21 \| fact \| parallel_[0-9]+ \| idx:[0-9]+$' >/dev/null; then
  fail "test 3: malformed / interleaved row detected"
fi
# Lock artifact cleaned up.
[ -z "$(ls "$LOCK_DIR" 2>/dev/null)" ] || fail "test 3: lock dir not empty after writers: $(ls "$LOCK_DIR")"
# Still exactly one "## Rows" header after 20 parallel appends.
[ "$(grep -c '^## Rows' "$CONCURRENT_SHELF")" = "1" ] \
  || fail "test 3: '## Rows' header duplicated under concurrency (got $(grep -c '^## Rows' "$CONCURRENT_SHELF"))"

# --- Test 4: pipe in value is escaped as \| and round-trips through parser ---
"$WRITER" --shelf pipes --date 2026-04-21 --type fact \
  --slug pipe_case --attr "phone:2830 3709 | ext. 5" \
  --attr "note:alpha|beta|gamma" \
  || fail "test 4: writer exited non-zero on pipe-containing value"
PIPES="$TMPDIR/pipes.md"
# Stored form must have escaped pipes in the value region.
grep -Fq '\| ext. 5' "$PIPES" || fail "test 4: pipe in phone not escaped"
grep -Fq 'alpha\|beta\|gamma' "$PIPES" || fail "test 4: pipes in note not escaped"
# Must NOT create spurious row separators from the value pipes.
[ "$(count_rows "$PIPES")" = "1" ] \
  || fail "test 4: pipe-in-value created spurious row split (got $(count_rows "$PIPES") rows)"
# Round-trip via shelf_rows.py parse: values should come back with literal | restored.
ROW_BODY="$(grep -E '^- ' "$PIPES" | head -1 | sed 's/^- //')"
PARSED="$("$PYTHON_BIN" "$LIB" parse <<<"$ROW_BODY")" \
  || fail "test 4: shelf_rows.py parse exited non-zero"
echo "$PARSED" | "$PYTHON_BIN" -c "
import json, sys
p = json.load(sys.stdin)
assert p['date'] == '2026-04-21', p
assert p['type'] == 'fact', p
assert p['slug'] == 'pipe_case', p
assert p['attrs']['phone'] == '2830 3709 | ext. 5', p
assert p['attrs']['note'] == 'alpha|beta|gamma', p
" || fail "test 4: round-trip parse lost data"

# --- Test 5: UTF-8 Chinese text round-trips cleanly ---
"$WRITER" --shelf chinese --date 2026-04-21 --type fact \
  --slug "person_羅克強-醫生" \
  --attr "name:羅克強醫生" --attr "role:泌尿科專科" \
  || fail "test 5: writer exited non-zero on Chinese text"
CHINESE="$TMPDIR/chinese.md"
# Raw byte-level check: the Chinese bytes must be present unmangled.
grep -Fq '羅克強醫生' "$CHINESE" || fail "test 5: name in Chinese missing from file"
grep -Fq '泌尿科專科' "$CHINESE" || fail "test 5: specialty in Chinese missing from file"
grep -Fq 'person_羅克強-醫生' "$CHINESE" || fail "test 5: slug with Chinese missing from file"
# Semantic round-trip via parser.
ROW_BODY="$(grep -E '^- ' "$CHINESE" | head -1 | sed 's/^- //')"
PARSED="$("$PYTHON_BIN" "$LIB" parse <<<"$ROW_BODY")" \
  || fail "test 5: shelf_rows.py parse exited non-zero on Chinese row"
echo "$PARSED" | "$PYTHON_BIN" -c "
import json, sys
p = json.load(sys.stdin)
assert p['slug'] == 'person_羅克強-醫生', repr(p['slug'])
assert p['attrs']['name'] == '羅克強醫生', repr(p['attrs']['name'])
assert p['attrs']['role'] == '泌尿科專科', repr(p['attrs']['role'])
" || fail "test 5: Chinese round-trip mismatch"

# --- Test 6: rollback on temp-file failure (file unchanged when write can't land) ---
ROLLBACK_SHELF="$TMPDIR/rollback"
mkdir -p "$ROLLBACK_SHELF"  # use a subdir as shelves root so we can chmod just this one
ROLLBACK_SHELVES_ROOT="$ROLLBACK_SHELF"
# Seed: one successful row so the shelf file exists with known content.
K2B_SHELVES_DIR="$ROLLBACK_SHELVES_ROOT" "$WRITER" \
  --shelf rb --date 2026-04-21 --type fact --slug seed --attr "k:v" \
  || fail "test 6: seed write failed"
SEED_FILE="$ROLLBACK_SHELVES_ROOT/rb.md"
[ -f "$SEED_FILE" ] || fail "test 6: seed file not created"
BEFORE_HASH="$(shasum "$SEED_FILE" | awk '{print $1}')"

# Now make the shelves dir read-only so mktemp inside it fails.
chmod 555 "$ROLLBACK_SHELVES_ROOT"
set +e
K2B_SHELVES_DIR="$ROLLBACK_SHELVES_ROOT" "$WRITER" \
  --shelf rb --date 2026-04-22 --type fact --slug second --attr "k:v2"
WRITE_RC=$?
set -e
chmod 755 "$ROLLBACK_SHELVES_ROOT"

[ "$WRITE_RC" != "0" ] || fail "test 6: writer reported success when temp write should have failed"
AFTER_HASH="$(shasum "$SEED_FILE" | awk '{print $1}')"
[ "$BEFORE_HASH" = "$AFTER_HASH" ] \
  || fail "test 6: seed file mutated despite failed write (before=$BEFORE_HASH after=$AFTER_HASH)"

# --- Test 7: rollback on mv failure (mktemp OK, rename fails) ---
# Stub out `mv` via PATH so the writer's atomic rename call returns non-zero.
# Verifies the "temp created, rename failed" branch also leaves the target
# byte-identical and exits non-zero.
STUB_DIR="$(mktemp -d)"
cat >"$STUB_DIR/mv" <<'EOF'
#!/bin/sh
echo "stub mv: simulated rename failure" >&2
exit 1
EOF
chmod +x "$STUB_DIR/mv"

MV_SHELF="$TMPDIR/mv-stub"
mkdir -p "$MV_SHELF"
K2B_SHELVES_DIR="$MV_SHELF" "$WRITER" \
  --shelf mv --date 2026-04-21 --type fact --slug seed --attr "k:v" \
  || fail "test 7: seed write failed"
MV_FILE="$MV_SHELF/mv.md"
BEFORE_HASH="$(shasum "$MV_FILE" | awk '{print $1}')"
BEFORE_TMP_COUNT="$(find "$MV_SHELF" -maxdepth 1 -name '*.tmp.*' | wc -l | tr -d ' ')"

set +e
PATH="$STUB_DIR:$PATH" K2B_SHELVES_DIR="$MV_SHELF" "$WRITER" \
  --shelf mv --date 2026-04-22 --type fact --slug second --attr "k:v2"
MV_RC=$?
set -e
rm -rf "$STUB_DIR"

[ "$MV_RC" != "0" ] || fail "test 7: writer reported success when stubbed mv failed"
AFTER_HASH="$(shasum "$MV_FILE" | awk '{print $1}')"
[ "$BEFORE_HASH" = "$AFTER_HASH" ] \
  || fail "test 7: target mutated despite failed mv (before=$BEFORE_HASH after=$AFTER_HASH)"
AFTER_TMP_COUNT="$(find "$MV_SHELF" -maxdepth 1 -name '*.tmp.*' | wc -l | tr -d ' ')"
[ "$AFTER_TMP_COUNT" = "$BEFORE_TMP_COUNT" ] \
  || fail "test 7: temp files leaked on mv failure (before=$BEFORE_TMP_COUNT after=$AFTER_TMP_COUNT)"

# --- Test 8: runtime guard against >1 "## Rows" header ---
GUARD_DIR="$TMPDIR/guard"
mkdir -p "$GUARD_DIR"
cat >"$GUARD_DIR/dup.md" <<'EOF'
---
tags: [context, shelf, dup, washing-machine]
type: shelf
shelf: dup
row-count: 0
up: "[[index]]"
---

# Dup shelf

## Rows

## Rows

- 2026-01-01 | fact | stray | k:v
EOF
set +e
K2B_SHELVES_DIR="$GUARD_DIR" "$WRITER" \
  --shelf dup --date 2026-04-22 --type fact --slug new_row --attr "k:v" 2>/dev/null
GUARD_RC=$?
set -e
[ "$GUARD_RC" != "0" ] \
  || fail "test 8: writer accepted a shelf with duplicate '## Rows' headers"

# --- Test 9: parse() rejects attribute values containing newlines ---
set +e
RESULT="$(printf '2026-04-21 | fact | bad | key:line1\nline2' | "$PYTHON_BIN" "$LIB" parse 2>&1)"
PARSE_RC=$?
set -e
[ "$PARSE_RC" != "0" ] \
  || fail "test 9: parse() accepted a value with a newline (round-trip would corrupt file)"
printf '%s' "$RESULT" | grep -q 'newline' \
  || fail "test 9: parse() error message should mention the newline constraint (got: $RESULT)"

echo "shelf-writer.test.sh: all 9 tests passed"
