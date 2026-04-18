#!/usr/bin/env bash
# tests/lint-memory.test.sh
# Tests for scripts/lint-memory.sh -- memory integrity audit.
#
# The helper reads a memory dir (K2B_MEMORY_DIR override) and prints
# [memory] findings to stdout when:
#   - a `[text](path)` pointer in MEMORY.md does not resolve
#   - MEMORY.md is over 190 lines
#   - active_rules.md is over 190 lines
#   - active_rules.md is missing
# Exit 0 regardless (advisory audit, never blocks).

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
HELPER="$REPO_ROOT/scripts/lint-memory.sh"

TMPROOT="$(mktemp -d)"
trap 'rm -rf "$TMPROOT"' EXIT

fail() { echo "FAIL: $*" >&2; exit 1; }

make_mem_dir() {
  local name="$1"
  local dir="$TMPROOT/$name"
  mkdir -p "$dir"
  echo "$dir"
}

# --- Test 1: happy path -- all pointers resolve, line counts under cap ---
DIR=$(make_mem_dir happy)
cat > "$DIR/MEMORY.md" <<'EOF'
# Memory Index

## User
- [Keith](user.md) - profile

## Reference
- [Mac Mini](ref_mac.md) - SSH
EOF
: > "$DIR/user.md"
: > "$DIR/ref_mac.md"
: > "$DIR/active_rules.md"

out=$(K2B_MEMORY_DIR="$DIR" "$HELPER")
[ -z "$out" ] || fail "test1: expected no output, got [$out]"

# --- Test 2: missing pointer target -------------------------------------
DIR=$(make_mem_dir missing_ptr)
cat > "$DIR/MEMORY.md" <<'EOF'
- [Keith](user.md) - profile
- [Orphan](ghost.md) - does not exist
EOF
: > "$DIR/user.md"
: > "$DIR/active_rules.md"

out=$(K2B_MEMORY_DIR="$DIR" "$HELPER")
echo "$out" | grep -q "\[memory\].*ghost\.md" || fail "test2: expected missing-pointer warning, got [$out]"
echo "$out" | grep -q "user\.md" && fail "test2: user.md should not be flagged, got [$out]"

# --- Test 3: MEMORY.md over 190 lines -----------------------------------
DIR=$(make_mem_dir mem_big)
{
  for i in $(seq 1 200); do echo "- line $i"; done
} > "$DIR/MEMORY.md"
: > "$DIR/active_rules.md"

out=$(K2B_MEMORY_DIR="$DIR" "$HELPER")
echo "$out" | grep -q "\[memory\].*MEMORY\.md.*200.*lines" \
  || echo "$out" | grep -q "\[memory\].*MEMORY\.md.*190" \
  || fail "test3: expected line-cap warning for MEMORY.md, got [$out]"

# --- Test 4: active_rules.md over 190 lines -----------------------------
DIR=$(make_mem_dir rules_big)
cat > "$DIR/MEMORY.md" <<'EOF'
# Index
EOF
{
  for i in $(seq 1 195); do echo "rule $i"; done
} > "$DIR/active_rules.md"

out=$(K2B_MEMORY_DIR="$DIR" "$HELPER")
echo "$out" | grep -q "\[memory\].*active_rules\.md.*195" \
  || echo "$out" | grep -q "\[memory\].*active_rules\.md.*190" \
  || fail "test4: expected line-cap warning for active_rules.md, got [$out]"

# --- Test 5: active_rules.md missing ------------------------------------
DIR=$(make_mem_dir rules_missing)
cat > "$DIR/MEMORY.md" <<'EOF'
# Index
EOF
# no active_rules.md created

out=$(K2B_MEMORY_DIR="$DIR" "$HELPER")
echo "$out" | grep -q "\[memory\].*active_rules\.md.*not found" \
  || fail "test5: expected not-found warning, got [$out]"

# --- Test 6: http URLs ignored ------------------------------------------
DIR=$(make_mem_dir http)
cat > "$DIR/MEMORY.md" <<'EOF'
- [Docs](https://example.com/doc.md) - external
- [Local](good.md) - local
EOF
: > "$DIR/good.md"
: > "$DIR/active_rules.md"

out=$(K2B_MEMORY_DIR="$DIR" "$HELPER")
[ -z "$out" ] || fail "test6: expected no output (http skipped), got [$out]"

# --- Test 7: relative traversal paths (policy-ledger shape) -------------
DIR=$(make_mem_dir traverse)
mkdir -p "$TMPROOT/traverse_ext"
: > "$TMPROOT/traverse_ext/policy.jsonl"
cat > "$DIR/MEMORY.md" <<EOF
- [Policy](../traverse_ext/policy.jsonl) - external
EOF
: > "$DIR/active_rules.md"

out=$(K2B_MEMORY_DIR="$DIR" "$HELPER")
[ -z "$out" ] || fail "test7: expected no output (traversal resolved), got [$out]"

# --- Test 8: relative traversal to missing file flagged -----------------
DIR=$(make_mem_dir traverse_miss)
cat > "$DIR/MEMORY.md" <<'EOF'
- [Policy](../nonexistent/policy.jsonl) - should fail
EOF
: > "$DIR/active_rules.md"

out=$(K2B_MEMORY_DIR="$DIR" "$HELPER")
echo "$out" | grep -q "\[memory\].*nonexistent/policy\.jsonl" \
  || fail "test8: expected missing-pointer warning, got [$out]"

# --- Test 9: MEMORY.md at exactly 190 -- no warning ---------------------
DIR=$(make_mem_dir at_cap)
{
  for i in $(seq 1 190); do echo "- line $i"; done
} > "$DIR/MEMORY.md"
: > "$DIR/active_rules.md"

out=$(K2B_MEMORY_DIR="$DIR" "$HELPER")
echo "$out" | grep -q "MEMORY\.md" && fail "test9: at cap should not warn, got [$out]"

# --- Test 10: exit code is 0 on warnings (advisory only) ---------------
DIR=$(make_mem_dir advisory)
cat > "$DIR/MEMORY.md" <<'EOF'
- [Ghost](nope.md)
EOF
: > "$DIR/active_rules.md"

K2B_MEMORY_DIR="$DIR" "$HELPER" >/dev/null
rc=$?
[ "$rc" = "0" ] || fail "test10: expected exit 0 even with warnings, got $rc"

# --- Test 11: missing MEMORY.md flagged, exit still 0 -------------------
DIR=$(make_mem_dir no_index)
: > "$DIR/active_rules.md"

out=$(K2B_MEMORY_DIR="$DIR" "$HELPER")
echo "$out" | grep -q "\[memory\].*MEMORY\.md.*not found" \
  || fail "test11: expected not-found warning for MEMORY.md, got [$out]"

# --- Test 12a: 191 logical lines, NO trailing newline -- must still warn --
# Regression guard: wc -l counts newline bytes, so a file without a trailing
# \n underreports by one. awk (which lint-memory.sh uses) counts the final
# line correctly. If someone swaps back to wc, this test fails.
DIR=$(make_mem_dir no_trailing_newline)
{
  for i in $(seq 1 191); do printf "line %s\n" "$i"; done
} > "$DIR/MEMORY.md"
# Strip the final newline so the file has 191 logical lines but 190 \n bytes.
perl -i -pe 'chomp if eof' "$DIR/MEMORY.md"
# Sanity check: grep -c '' reports 191 logical lines even without trailing NL.
actual=$(awk 'END {print NR}' "$DIR/MEMORY.md")
[ "$actual" = "191" ] || fail "test12a precondition: expected 191, got $actual"
: > "$DIR/active_rules.md"

out=$(K2B_MEMORY_DIR="$DIR" "$HELPER")
echo "$out" | grep -q "\[memory\].*MEMORY\.md.*191" \
  || fail "test12a: expected 191-line warning even without trailing NL, got [$out]"

# --- Test 12b: non-UTF-8 byte in MEMORY.md -- audit must not silently pass --
# Regression guard: an unhandled UnicodeDecodeError in the Python heredoc
# previously let the script exit 0 with no [memory] output. The fix surfaces
# a crash marker AND uses errors='replace' so normal pointer-resolution
# continues. We assert at least one of: (a) crash marker present, or (b) the
# ghost pointer after the bad byte still gets flagged -- either proves the
# audit didn't silently skip.
DIR=$(make_mem_dir binary_byte)
printf '[Good](good.md)\n[Bad\xff](ghost.md)\n' > "$DIR/MEMORY.md"
: > "$DIR/good.md"
: > "$DIR/active_rules.md"

out=$(K2B_MEMORY_DIR="$DIR" "$HELPER")
if ! echo "$out" | grep -q "\[memory\]"; then
  fail "test12b: expected at least one [memory] line on non-UTF-8 input, got [$out]"
fi

# --- Test 13: multiple issues compound ----------------------------------
DIR=$(make_mem_dir compound)
{
  echo "- [Ghost1](miss1.md)"
  echo "- [Ghost2](miss2.md)"
  for i in $(seq 1 195); do echo "- line $i"; done
} > "$DIR/MEMORY.md"
: > "$DIR/active_rules.md"

out=$(K2B_MEMORY_DIR="$DIR" "$HELPER")
ghost_count=$(echo "$out" | grep -c "\[memory\].*miss")
[ "$ghost_count" = "2" ] || fail "test12: expected 2 ghost lines, got $ghost_count in [$out]"
echo "$out" | grep -q "MEMORY\.md" || fail "test12: expected MEMORY.md line-cap warning, got [$out]"

echo "ALL TESTS PASSED"
