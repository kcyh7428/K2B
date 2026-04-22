#!/usr/bin/env bash
# tests/deploy-to-mini.test.sh
# Tests for scripts/deploy-to-mini.sh detect_changes() via rsync checksum.
#
# Background: the prior git-diff-based detection missed files from a ship's
# earlier commit when a follow-up devlog commit landed on top. The fix
# compares local vs remote content via `rsync -acn` (dry-run + checksum),
# which is authoritative regardless of commit structure.
#
# Each scenario builds a fake LOCAL_BASE tree and a fake remote target tree
# in tempdirs, then runs deploy-to-mini.sh with:
#   K2B_LOCAL_BASE=<local-tempdir>
#   K2B_RSYNC_TARGET_PREFIX=<remote-tempdir>  (local path, no SSH)
#   K2B_DETECT_ONLY=true                      (print categories, exit)
#
# Cleanup: each test appends its tempdir to TMP_DIRS; single EXIT trap cleans
# them all (per-test traps would override each other).

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SCRIPT="$REPO_ROOT/scripts/deploy-to-mini.sh"

TMP_DIRS=()
cleanup() {
  local d
  for d in "${TMP_DIRS[@]}"; do
    [ -n "$d" ] && [ -d "$d" ] && rm -rf "$d"
  done
}
trap cleanup EXIT

PASS=0
FAIL=0

mktmp() {
  local d
  d="$(mktemp -d)"
  TMP_DIRS+=("$d")
  echo "$d"
}

# Build a minimal K2B-like tree at the given path.
# Files written:
#   CLAUDE.md, README.md, .mcp.json, DEVLOG.md
#   .claude/skills/k2b-ship/SKILL.md
#   scripts/deploy-to-mini.sh, scripts/review.sh
#   k2b-remote/src/index.ts, k2b-remote/package.json
#   k2b-dashboard/src/App.tsx
#   k2b-remote/node_modules/junk/x.js   (excluded dir)
build_tree() {
  local base="$1" tag="${2:-baseline}"
  mkdir -p "$base/.claude/skills/k2b-ship"
  mkdir -p "$base/scripts"
  mkdir -p "$base/k2b-remote/src"
  mkdir -p "$base/k2b-remote/node_modules/junk"
  mkdir -p "$base/k2b-dashboard/src"
  printf 'CLAUDE content %s\n' "$tag" > "$base/CLAUDE.md"
  printf 'README %s\n' "$tag" > "$base/README.md"
  printf '{"tag":"%s"}\n' "$tag" > "$base/.mcp.json"
  printf 'DEVLOG %s\n' "$tag" > "$base/DEVLOG.md"
  printf 'skill body %s\n' "$tag" > "$base/.claude/skills/k2b-ship/SKILL.md"
  printf '#!/bin/bash\n# deploy %s\n' "$tag" > "$base/scripts/deploy-to-mini.sh"
  printf '#!/bin/bash\n# review %s\n' "$tag" > "$base/scripts/review.sh"
  printf 'export const tag = "%s";\n' "$tag" > "$base/k2b-remote/src/index.ts"
  printf '{"name":"k2b-remote","tag":"%s"}\n' "$tag" > "$base/k2b-remote/package.json"
  printf 'export const App = "%s";\n' "$tag" > "$base/k2b-dashboard/src/App.tsx"
  printf 'junk %s\n' "$tag" > "$base/k2b-remote/node_modules/junk/x.js"
}

# Run detect-only and print the categories the script reports.
# Output is a sorted newline-separated list of category names.
run_detect() {
  local local_base="$1" remote_target="$2"
  K2B_LOCAL_BASE="$local_base" \
  K2B_RSYNC_TARGET_PREFIX="$remote_target" \
  K2B_DETECT_ONLY=true \
    bash "$SCRIPT" auto 2>/dev/null | LC_ALL=C sort
}

# assert_detect SCENARIO_NAME EXPECTED_LINES ACTUAL_OUTPUT
# EXPECTED_LINES is a literal multi-line string; empty string means "expect no
# categories flagged".
assert_detect() {
  local name="$1" expected="$2" actual="$3"
  if [ "$expected" = "$actual" ]; then
    echo "  PASS: $name"
    PASS=$((PASS + 1))
  else
    echo "  FAIL: $name"
    echo "    expected:"
    printf '      %s\n' "$expected"
    echo "    actual:"
    printf '      %s\n' "$actual"
    FAIL=$((FAIL + 1))
  fi
}

echo "=== deploy-to-mini.test.sh ==="

# ---------------------------------------------------------------------------
# Scenario 1: single-commit ship -- one scripts/ file differs
# ---------------------------------------------------------------------------
{
  LOCAL="$(mktmp)"
  REMOTE="$(mktmp)"
  build_tree "$LOCAL" "v2"
  build_tree "$REMOTE" "v1"
  # Reset all files to identical except scripts/deploy-to-mini.sh
  # (rebuild remote as v2 baseline, then revert only the scripts file)
  rm -rf "$REMOTE"
  build_tree "$REMOTE" "v2"
  printf '#!/bin/bash\n# deploy v1\n' > "$REMOTE/scripts/deploy-to-mini.sh"
  out="$(run_detect "$LOCAL" "$REMOTE")"
  assert_detect "single-commit ship: scripts change detected" "scripts" "$out"
}

# ---------------------------------------------------------------------------
# Scenario 2: two-commit ship (code + devlog pattern)
# First commit touched scripts/ and .claude/skills/; second commit was
# DEVLOG.md only. Under the old git-diff fallback, only DEVLOG.md was visible,
# and DEVLOG.md is not a syncable category -> "none in syncable categories".
# rsync-based detection sees the scripts + skills drift regardless.
# ---------------------------------------------------------------------------
{
  LOCAL="$(mktmp)"
  REMOTE="$(mktmp)"
  build_tree "$LOCAL" "v2"
  build_tree "$REMOTE" "v2"
  # The "first commit" files differ from remote:
  printf 'skill body NEW\n' > "$LOCAL/.claude/skills/k2b-ship/SKILL.md"
  printf '#!/bin/bash\n# deploy NEW\n' > "$LOCAL/scripts/deploy-to-mini.sh"
  # The "second commit" (devlog) also differs, but DEVLOG.md is not synced:
  printf 'DEVLOG NEW\n' > "$LOCAL/DEVLOG.md"
  out="$(run_detect "$LOCAL" "$REMOTE")"
  expected="$(printf 'scripts\nskills')"
  assert_detect "two-commit ship: both commits' syncable changes detected" "$expected" "$out"
}

# ---------------------------------------------------------------------------
# Scenario 3: three-commit ship -- all four categories differ
# ---------------------------------------------------------------------------
{
  LOCAL="$(mktmp)"
  REMOTE="$(mktmp)"
  build_tree "$LOCAL" "v2"
  build_tree "$REMOTE" "v2"
  # Skills (CLAUDE.md) change
  printf 'CLAUDE NEW\n' > "$LOCAL/CLAUDE.md"
  # Code change
  printf 'export const tag = "NEW";\n' > "$LOCAL/k2b-remote/src/index.ts"
  # Dashboard change
  printf 'export const App = "NEW";\n' > "$LOCAL/k2b-dashboard/src/App.tsx"
  # Scripts change
  printf '#!/bin/bash\n# review NEW\n' > "$LOCAL/scripts/review.sh"
  out="$(run_detect "$LOCAL" "$REMOTE")"
  expected="$(printf 'code\ndashboard\nscripts\nskills')"
  assert_detect "three-commit ship: all four categories detected" "$expected" "$out"
}

# ---------------------------------------------------------------------------
# Scenario 4: no changes -- identical trees -> no categories, clean exit
# ---------------------------------------------------------------------------
{
  LOCAL="$(mktmp)"
  REMOTE="$(mktmp)"
  build_tree "$LOCAL" "v2"
  build_tree "$REMOTE" "v2"
  out="$(run_detect "$LOCAL" "$REMOTE")"
  assert_detect "no changes: nothing detected" "" "$out"
}

# ---------------------------------------------------------------------------
# Scenario 5: only DEVLOG.md differs -> NO categories (the exact bug)
# This is the precise case that caused the 2026-04-22 failure: a follow-up
# devlog commit leaves the code already on the Mini, so detect should report
# clean -- NOT "changes detected but none syncable".
# ---------------------------------------------------------------------------
{
  LOCAL="$(mktmp)"
  REMOTE="$(mktmp)"
  build_tree "$LOCAL" "v2"
  build_tree "$REMOTE" "v2"
  printf 'DEVLOG NEW entry\n' > "$LOCAL/DEVLOG.md"
  out="$(run_detect "$LOCAL" "$REMOTE")"
  assert_detect "only DEVLOG.md differs: no sync categories flagged" "" "$out"
}

# ---------------------------------------------------------------------------
# Scenario 6: excluded paths (node_modules) don't trigger code sync
# ---------------------------------------------------------------------------
{
  LOCAL="$(mktmp)"
  REMOTE="$(mktmp)"
  build_tree "$LOCAL" "v2"
  build_tree "$REMOTE" "v2"
  # Only node_modules differs -- should NOT flag code
  printf 'junk NEW\n' > "$LOCAL/k2b-remote/node_modules/junk/x.js"
  out="$(run_detect "$LOCAL" "$REMOTE")"
  assert_detect "node_modules drift ignored (not a code-category change)" "" "$out"
}

# ---------------------------------------------------------------------------
# Scenario 7: brand-new file in scripts/ (uncommitted + untracked case)
# ---------------------------------------------------------------------------
{
  LOCAL="$(mktmp)"
  REMOTE="$(mktmp)"
  build_tree "$LOCAL" "v2"
  build_tree "$REMOTE" "v2"
  printf '#!/bin/bash\n# new tool\n' > "$LOCAL/scripts/new-tool.sh"
  out="$(run_detect "$LOCAL" "$REMOTE")"
  assert_detect "brand-new scripts/ file detected" "scripts" "$out"
}

# ---------------------------------------------------------------------------
# Scenario 8: rsync dry-run error aborts the script (P1 from Codex review).
# Previously rsync_has_changes swallowed all errors and treated an empty
# stdout as "no changes", which would let a broken-target `auto` run silently
# exit clean instead of surfacing the deployment failure.
# ---------------------------------------------------------------------------
{
  LOCAL="$(mktmp)"
  build_tree "$LOCAL" "v2"
  # A regular file as the RSYNC_TARGET prefix forces rsync to fail: it can't
  # create subdirs under a non-directory path.
  BAD_TARGET="$(mktemp)"
  EXIT=0
  K2B_LOCAL_BASE="$LOCAL" K2B_RSYNC_TARGET_PREFIX="$BAD_TARGET" K2B_DETECT_ONLY=true \
      bash "$SCRIPT" auto >/dev/null 2>&1 || EXIT=$?
  rm -f "$BAD_TARGET"
  if [[ $EXIT -ne 0 ]]; then
    echo "  PASS: rsync dry-run failure aborts the script (exit $EXIT)"
    PASS=$((PASS + 1))
  else
    echo "  FAIL: rsync dry-run failure did NOT abort the script (exit 0)"
    FAIL=$((FAIL + 1))
  fi
}

echo ""
echo "Results: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ] || exit 1
