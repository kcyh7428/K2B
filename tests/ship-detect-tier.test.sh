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

echo "all tests passed"
