#!/usr/bin/env bash
# tests/minimax-review-scope.test.sh
# Tests for scripts/lib/minimax_review.py Phase B scope gatherers.
#
# Builds a fixture git repo in mktemp -d per scenario, then drives the
# gatherer functions via python3 -c. Asserts on the returned context string.
#
# Cleanup: each test appends its tempdir to TMP_DIRS via mktmp(); the single
# EXIT trap below iterates and removes them. (Per-test `trap ... EXIT`
# overrides earlier traps in bash, which would leak all but the last fixture.)

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LIB_DIR="$REPO_ROOT/scripts/lib"
SCRIPT="$REPO_ROOT/scripts/lib/minimax_review.py"

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
# Initializes a fresh git repo with two committed files (file_a.py, file_b.py)
# and one untracked file (extra.py). Caller can then mutate as needed.
build_fixture_repo() {
  local out="$1"
  mkdir -p "$out"
  (
    cd "$out" || exit 1
    git init -q -b main
    git config user.email "test@example.com"
    git config user.name "test"
    printf 'def a():\n    return 1\n' > file_a.py
    printf 'def b():\n    return 2\n' > file_b.py
    git add file_a.py file_b.py
    git commit -q -m "init"
    printf 'def extra():\n    return 3\n' > extra.py  # untracked
  )
}

# call_gatherer FUNC_NAME REPO_ROOT [JSON_ARG]
# Runs the named gatherer function and prints the returned context to stdout.
# JSON_ARG is parsed as JSON; if it's a list, it becomes the first positional
# arg (file/path list); otherwise the value becomes the first positional arg
# (single string for plan_path).
call_gatherer() {
  local func="$1" repo="$2"
  shift 2
  local json_arg="${1:-}"
  python3 - "$LIB_DIR" "$func" "$repo" "$json_arg" <<'PY'
import sys
import json
from pathlib import Path
lib_dir, func, repo, json_arg = sys.argv[1:5]
sys.path.insert(0, lib_dir)
mod = __import__("minimax_review")
gatherer = getattr(mod, func)
if json_arg.strip():
    parsed = json.loads(json_arg)
    ctx, _ = gatherer(parsed, repo_root=Path(repo))
else:
    ctx, _ = gatherer(repo_root=Path(repo))
print(ctx)
PY
}

# call_gatherer_full FUNC_NAME REPO_ROOT [JSON_ARG]
# Like call_gatherer but prints both context and the returned file list,
# separated by sentinel lines. Used for tests that need to assert on the
# returned file ordering.
call_gatherer_full() {
  local func="$1" repo="$2"
  shift 2
  local json_arg="${1:-}"
  python3 - "$LIB_DIR" "$func" "$repo" "$json_arg" <<'PY'
import sys
import json
from pathlib import Path
lib_dir, func, repo, json_arg = sys.argv[1:5]
sys.path.insert(0, lib_dir)
mod = __import__("minimax_review")
gatherer = getattr(mod, func)
if json_arg.strip():
    parsed = json.loads(json_arg)
    ctx, files = gatherer(parsed, repo_root=Path(repo))
else:
    ctx, files = gatherer(repo_root=Path(repo))
print("=== context ===")
print(ctx)
print("=== files ===")
for f in files:
    print(f)
PY
}

echo "(scaffold loaded; no tests yet)"
