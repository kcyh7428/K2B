#!/usr/bin/env bash
# tests/compile-index-update.test.sh
# Smoke tests for scripts/compile-index-update.py (Fix #2, revised).
#
# Cases (see plan docs/superpowers/plans/2026-04-15-fix2-atomic-4-index.md Task 2):
#   1. Happy path, single subfolder.
#   2. Nested subfolder resolution.
#   3. Mixed subfolder compile.
#   4. Malformed master (exit 1, nothing written).
#   5. Log append failure (exit 2, subfolder indexes already written).
#   6. Bad args (exit 1).

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
HELPER="$REPO_ROOT/scripts/compile-index-update.py"
FIXTURE="$REPO_ROOT/tests/fixtures/compile-index-update"
TODAY="$(date '+%Y-%m-%d')"

TMPROOT="$(mktemp -d)"
trap 'rm -rf "$TMPROOT" /tmp/k2b-compile-index-itest.lock.d /tmp/k2b-wiki-log-itest.lock /tmp/k2b-wiki-log-itest.lock.d' EXIT

export K2B_COMPILE_INDEX_LOCK=/tmp/k2b-compile-index-itest.lock.d
export K2B_WIKI_LOG_LOCK=/tmp/k2b-wiki-log-itest.lock

fail() { echo "FAIL: $*" >&2; exit 1; }

setup_vault() {
  local name="$1"
  local dest="$TMPROOT/$name"
  cp -R "$FIXTURE" "$dest"
  export K2B_VAULT_ROOT="$dest"
  export K2B_WIKI_LOG="$dest/wiki/log.md"
}

# --- Test 1: happy path, single subfolder --------------------------------
setup_vault test1
"$HELPER" "raw/research/2026-04-15_sample.md" "wiki/projects/sample.md" ""
grep -q "^Last updated: $TODAY | Entries: 1$" "$K2B_VAULT_ROOT/wiki/projects/index.md" \
  || fail "test1: wiki/projects/index.md header not rewritten"
grep -q "^Last updated: $TODAY | Entries: 1 + 1 subfolder (k2b-investment/)$" "$K2B_VAULT_ROOT/raw/research/index.md" \
  || fail "test1: raw/research/index.md header tail not preserved"
grep -Fq "| [projects/](projects/index.md) | Project pages | 1 |" "$K2B_VAULT_ROOT/wiki/index.md" \
  || fail "test1: master projects row missing"
grep -Fq "**Total wiki pages: 3**" "$K2B_VAULT_ROOT/wiki/index.md" \
  || fail "test1: master total line missing"
grep -Fq "/compile  raw/research/2026-04-15_sample.md  updated: wiki/projects/sample.md | created: (none)" \
  "$K2B_VAULT_ROOT/wiki/log.md" \
  || fail "test1: log line missing or malformed: $(tail -1 "$K2B_VAULT_ROOT/wiki/log.md")"

# --- Test 2: nested subfolder --------------------------------------------
setup_vault test2
"$HELPER" "raw/research/2026-04-15_sample.md" "wiki/projects/k2b-investment/architecture.md" ""
grep -q "^Last updated: $TODAY | Entries: 1$" "$K2B_VAULT_ROOT/wiki/projects/k2b-investment/index.md" \
  || fail "test2: nested k2b-investment index not rewritten"
# Top-level projects index must NOT have been touched (still 2026-04-14).
grep -q "^Last updated: 2026-04-14 | Entries: 1$" "$K2B_VAULT_ROOT/wiki/projects/index.md" \
  || fail "test2: top-level projects index was unexpectedly touched"
grep -Fq "| [projects/k2b-investment/](projects/k2b-investment/index.md) | Nested planning workspace | 1 |" \
  "$K2B_VAULT_ROOT/wiki/index.md" \
  || fail "test2: master nested row missing"

# --- Test 3: mixed subfolders --------------------------------------------
setup_vault test3
"$HELPER" "raw/research/2026-04-15_sample.md" \
  "wiki/projects/sample.md,wiki/people/person_X.md" ""
grep -q "^Last updated: $TODAY | Entries: 1$" "$K2B_VAULT_ROOT/wiki/projects/index.md" \
  || fail "test3: projects index not touched"
grep -q "^Last updated: $TODAY | Entries: 1$" "$K2B_VAULT_ROOT/wiki/people/index.md" \
  || fail "test3: people index not touched"
grep -Fq "| [people/](people/index.md) | Person pages | 1 |" "$K2B_VAULT_ROOT/wiki/index.md" \
  || fail "test3: master people row missing"
grep -Fq "| [projects/](projects/index.md) | Project pages | 1 |" "$K2B_VAULT_ROOT/wiki/index.md" \
  || fail "test3: master projects row missing"

# --- Test 4: malformed master (exit 1, nothing written) ------------------
setup_vault test4
# Strip the 3-column header to simulate unrecognized shape.
sed -i.bak '/| Folder | Purpose | Entries |/d' "$K2B_VAULT_ROOT/wiki/index.md"
rm -f "$K2B_VAULT_ROOT/wiki/index.md.bak"
set +e
"$HELPER" "raw/research/2026-04-15_sample.md" "wiki/projects/sample.md" "" 2>/dev/null
rc=$?
set -e
[ "$rc" = "1" ] || fail "test4: expected exit 1 on malformed master, got $rc"
grep -q "^Last updated: 2026-04-14 | Entries: 1$" "$K2B_VAULT_ROOT/wiki/projects/index.md" \
  || fail "test4: wiki/projects/index.md was modified despite validation failure"
grep -q "^Last updated: 2026-04-14 | Entries: 1 + 1 subfolder (k2b-investment/)$" \
  "$K2B_VAULT_ROOT/raw/research/index.md" \
  || fail "test4: raw/research/index.md was modified despite validation failure"
# No stray tempfiles.
if ls "$K2B_VAULT_ROOT/wiki/projects/"*.tmp >/dev/null 2>&1; then
  fail "test4: tempfile leaked under wiki/projects/"
fi
if ls "$K2B_VAULT_ROOT/wiki/"*.tmp >/dev/null 2>&1; then
  fail "test4: tempfile leaked under wiki/"
fi

# --- Test 5: log append failure -> exit 2 (partial write) ----------------
setup_vault test5
MISSING_LOG="$TMPROOT/test5-nonexistent.md"
set +e
K2B_WIKI_LOG="$MISSING_LOG" "$HELPER" \
  "raw/research/2026-04-15_sample.md" "wiki/projects/sample.md" "" 2>/dev/null
rc=$?
set -e
[ "$rc" = "2" ] || fail "test5: expected exit 2 on log append failure, got $rc"
# Subfolder indexes ARE already on disk (partial-write signal).
grep -q "^Last updated: $TODAY | Entries: 1$" "$K2B_VAULT_ROOT/wiki/projects/index.md" \
  || fail "test5: expected partial write -- projects index should be updated"
grep -q "^Last updated: $TODAY | Entries: 1 + 1 subfolder (k2b-investment/)$" \
  "$K2B_VAULT_ROOT/raw/research/index.md" \
  || fail "test5: expected partial write -- raw/research index should be updated"
# Missing log file was never created.
[ ! -f "$MISSING_LOG" ] || fail "test5: missing log file should not exist"

# --- Test 6: bad args ----------------------------------------------------
setup_vault test6
set +e
"$HELPER" 2>/dev/null
rc=$?
set -e
[ "$rc" = "1" ] || fail "test6: expected exit 1 on no args, got $rc"

set +e
"$HELPER" "raw/research/2026-04-15_sample.md" "" "" 2>/dev/null
rc=$?
set -e
[ "$rc" = "1" ] || fail "test6: expected exit 1 on empty updated+created, got $rc"

# Fixture vault should still be untouched after bad-args failures.
grep -q "^Last updated: 2026-04-14 | Entries: 1$" "$K2B_VAULT_ROOT/wiki/projects/index.md" \
  || fail "test6: wiki/projects/index.md was modified despite bad args"

echo "compile-index-update.test.sh: all tests passed"
