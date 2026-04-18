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

# --- Test 1: working-tree gatherer regression -- determinism + shape -----
TMP1="$(mktmp)"
build_fixture_repo "$TMP1"
# Mutations:
#  - file_a.py modified (tracked change)
#  - file_b.py deleted (tracked deletion -- exercises the _(deleted)_ marker)
#  - extra.py untracked (already created by build_fixture_repo)
printf 'def a():\n    return 99\n' > "$TMP1/file_a.py"
rm "$TMP1/file_b.py"

# Sub-test 1a: determinism -- two consecutive calls return identical output
out1=$(call_gatherer gather_working_tree_context "$TMP1")
out2=$(call_gatherer gather_working_tree_context "$TMP1")
[ "$out1" = "$out2" ] || \
  fail "test1a: gatherer not deterministic (two calls returned different output)"

# Sub-test 1b: section headers in expected order
expected_headers=(
  "## git status --short"
  "## diffstat (HEAD)"
  "## diff vs HEAD"
  "## Full file contents (changed and untracked)"
)
last_pos=0
for header in "${expected_headers[@]}"; do
  pos=$(printf '%s\n' "$out1" | grep -nF "$header" | head -1 | cut -d: -f1)
  [ -n "$pos" ] || fail "test1b: missing header: $header"
  [ "$pos" -gt "$last_pos" ] || \
    fail "test1b: header out of order: '$header' at line $pos, after $last_pos"
  last_pos="$pos"
done

# Sub-test 1c: deleted file marker present (file_b.py)
printf '%s\n' "$out1" | grep -q '_(deleted)_' || \
  fail "test1c: missing _(deleted)_ marker for deleted file"

# Sub-test 1d: untracked file (extra.py) included in Full file contents
printf '%s\n' "$out1" | grep -q '### extra.py' || \
  fail "test1d: untracked file extra.py not in 'Full file contents' section"

# Sub-test 1e: line numbering on modified content (file_a.py was rewritten)
printf '%s\n' "$out1" | grep -qE '^\s*1\s+def a\(\):$' || \
  fail "test1e: missing line numbers on file_a.py content"
printf '%s\n' "$out1" | grep -qE '^\s*2\s+    return 99$' || \
  fail "test1e: missing line 2 of file_a.py"

# Sub-test 1f: returned file list is sorted (caller expects deterministic order)
files_out=$(call_gatherer_full gather_working_tree_context "$TMP1" | sed -n '/=== files ===/,$p' | tail -n +2)
files_sorted=$(printf '%s\n' "$files_out" | sort)
[ "$files_out" = "$files_sorted" ] || \
  fail "test1f: returned file list not sorted (got: $(echo "$files_out" | tr '\n' ' '))"

# Sub-test 1g: clean-tree case -- empty result, no sections
TMP1_CLEAN="$(mktmp)"
build_fixture_repo "$TMP1_CLEAN"
rm "$TMP1_CLEAN/extra.py"  # eliminate the untracked file too
out_clean=$(call_gatherer gather_working_tree_context "$TMP1_CLEAN")
[ -z "$out_clean" ] || \
  fail "test1g: clean-tree case should return empty context, got: $(echo "$out_clean" | head -1)"

# Sub-test 1h: diff-section-omitted-when-empty (untracked-only working tree)
TMP1_NEWONLY="$(mktmp)"
build_fixture_repo "$TMP1_NEWONLY"
# extra.py is untracked, file_a/file_b unmodified -> git diff HEAD is empty.
# Phase A omits the "## diff vs HEAD" section if diff is empty.
out_newonly=$(call_gatherer gather_working_tree_context "$TMP1_NEWONLY")
if printf '%s\n' "$out_newonly" | grep -q '## diff vs HEAD'; then
  fail "test1h: empty diff should not produce '## diff vs HEAD' section (Phase A behavior)"
fi
# But the "## Full file contents" header SHOULD appear (extra.py is in there)
printf '%s\n' "$out_newonly" | grep -q '## Full file contents' || \
  fail "test1h: untracked-only case missing 'Full file contents' header"

echo "ok test1: working-tree gatherer regression (1a-1h)"
