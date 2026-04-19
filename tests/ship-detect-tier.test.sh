#!/usr/bin/env bash
# tests/ship-detect-tier.test.sh
# Tests for scripts/lib/tier_detection.py (classify_tier) and
# scripts/ship-detect-tier.py (CLI wrapper). Builds fixture git repos
# in mktemp -d per scenario, drives classify_tier() via python3 -c.

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LIB_DIR="$REPO_ROOT/scripts/lib"
SCRIPT="$REPO_ROOT/scripts/ship-detect-tier.py"

TMP_DIRS=()
cleanup() {
  local d
  for d in "${TMP_DIRS[@]}"; do
    [ -n "$d" ] && [ -d "$d" ] && rm -rf "$d"
  done
}
trap cleanup EXIT

fail() { echo "FAIL: $*" >&2; exit 1; }

mktmp() {
  local d
  d="$(mktemp -d)"
  TMP_DIRS+=("$d")
  echo "$d"
}

# build_fixture_repo OUT_DIR
# Fresh git repo at OUT_DIR with one committed file.
build_fixture_repo() {
  local out="$1"
  mkdir -p "$out"
  (
    cd "$out" || exit 1
    git init -q -b main
    git config user.email "test@example.com"
    git config user.name "test"
    printf 'initial\n' > README.md
    git add README.md
    git commit -q -m "init"
  )
}

# call_classifier REPO_ROOT [TIER3_CONFIG_PATH]
# Runs classify_tier() in the fixture; stdout = "tier:N reason:<text>".
# Non-zero exit on classifier error.
call_classifier() {
  local repo="$1"
  local config="${2:-}"
  local config_arg=""
  if [ -n "$config" ]; then
    config_arg=", tier3_config_path=r'$config'"
  fi
  PYTHONPATH="$LIB_DIR" python3 -c "
import sys
sys.path.insert(0, r'$LIB_DIR')
from tier_detection import classify_tier
tier, reason = classify_tier(repo_root=r'$repo'${config_arg})
print(f'tier:{tier} reason:{reason}')
"
}

# ---------- tests registered below ----------

test_gather_tree_state_on_clean_tree() {
  local repo
  repo="$(mktmp)"
  build_fixture_repo "$repo"

  local out
  out=$(PYTHONPATH="$LIB_DIR" python3 -c "
import sys
sys.path.insert(0, r'$LIB_DIR')
from tier_detection import gather_tree_state
state = gather_tree_state(repo_root=r'$repo')
print('files:', state['files'])
print('total_loc:', state['total_loc'])
")

  echo "$out" | grep -q "files: \[\]" || fail "clean tree should have no files; got: $out"
  echo "$out" | grep -q "total_loc: 0" || fail "clean tree LOC should be 0; got: $out"
  echo "PASS: test_gather_tree_state_on_clean_tree"
}

test_gather_tree_state_with_modified_and_untracked() {
  local repo
  repo="$(mktmp)"
  build_fixture_repo "$repo"
  (cd "$repo" && printf 'modified\nmore\n' > README.md)
  (cd "$repo" && printf 'new\n' > new.py)

  local out
  out=$(PYTHONPATH="$LIB_DIR" python3 -c "
import sys
sys.path.insert(0, r'$LIB_DIR')
from tier_detection import gather_tree_state
state = gather_tree_state(repo_root=r'$repo')
print('files:', sorted(state['files']))
print('statuses:', sorted(state['statuses'].items()))
print('total_loc:', state['total_loc'])
")

  echo "$out" | grep -q "'README.md'" || fail "README.md should be in files; got: $out"
  echo "$out" | grep -q "'new.py'" || fail "new.py should be in files; got: $out"
  echo "PASS: test_gather_tree_state_with_modified_and_untracked"
}

test_gather_tree_state_handles_paths_with_spaces() {
  # Codex omission: renames and paths with spaces.
  local repo
  repo="$(mktmp)"
  build_fixture_repo "$repo"
  (cd "$repo" && printf 'x\n' > "has space.py")

  local out
  out=$(PYTHONPATH="$LIB_DIR" python3 -c "
import sys
sys.path.insert(0, r'$LIB_DIR')
from tier_detection import gather_tree_state
state = gather_tree_state(repo_root=r'$repo')
print('files:', sorted(state['files']))
")

  echo "$out" | grep -q "'has space.py'" || fail "space-path should be captured; got: $out"
  echo "PASS: test_gather_tree_state_handles_paths_with_spaces"
}

test_gather_tree_state_on_clean_tree
test_gather_tree_state_with_modified_and_untracked
test_gather_tree_state_handles_paths_with_spaces

echo "all tests passed"
