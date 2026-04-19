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

# --- Test 2: diff-scoped on a clean tree (no diffs to show) ----------
TMP2="$(mktmp)"
build_fixture_repo "$TMP2"
# No mutations; tree is clean for tracked files.

ctx=$(call_gatherer gather_diff_scoped_context "$TMP2" '["file_a.py"]')

echo "$ctx" | grep -q 'file_a.py' || \
  fail "test2: missing file_a.py content"
echo "$ctx" | grep -qE '^\s*1\s+def a' || \
  fail "test2: missing line-numbered content"
# file_b.py was NOT in the request -- must not appear
if echo "$ctx" | grep -q 'file_b.py'; then
  fail "test2: file_b.py leaked into diff-scoped output (only file_a.py was requested)"
fi
echo "ok test2: diff-scoped clean tree"

# --- Test 3: diff-scoped on a dirty tree -- unrelated dirty files excluded
TMP3="$(mktmp)"
build_fixture_repo "$TMP3"
printf 'def a():\n    return 99\n' > "$TMP3/file_a.py"  # in scope, modified
printf 'def b():\n    return 99\n' > "$TMP3/file_b.py"  # NOT in scope, modified
# extra.py untracked, NOT in scope

ctx=$(call_gatherer gather_diff_scoped_context "$TMP3" '["file_a.py"]')

echo "$ctx" | grep -q 'file_a.py' || \
  fail "test3: missing file_a.py (in scope)"
if echo "$ctx" | grep -q 'file_b.py'; then
  fail "test3: file_b.py leaked into output (unrelated dirty file)"
fi
if echo "$ctx" | grep -q 'extra.py'; then
  fail "test3: extra.py leaked into output (unrelated untracked file)"
fi
echo "$ctx" | grep -q 'return 99' || \
  fail "test3: missing modified content of file_a.py"
echo "$ctx" | grep -q '```diff' || \
  fail "test3: missing diff section for file_a.py"
echo "ok test3: diff-scoped excludes unrelated dirty files"

# --- Test 4: file-list happy path -- two files, both in output -------
TMP4="$(mktmp)"
build_fixture_repo "$TMP4"

ctx=$(call_gatherer gather_file_list_context "$TMP4" '["file_a.py", "file_b.py"]')

echo "$ctx" | grep -q 'file_a.py' || fail "test4: missing file_a.py"
echo "$ctx" | grep -q 'file_b.py' || fail "test4: missing file_b.py"
echo "$ctx" | grep -qE '^\s*1\s+def a' || \
  fail "test4: missing line numbers on file_a"
echo "$ctx" | grep -qE '^\s*1\s+def b' || \
  fail "test4: missing line numbers on file_b"
# No git context expected
if echo "$ctx" | grep -q '## git status'; then
  fail "test4: file-list scope leaked git status"
fi
if echo "$ctx" | grep -q '```diff'; then
  fail "test4: file-list scope leaked git diff"
fi
echo "ok test4: file-list happy path"

# --- Test 5: file-list with one missing path -- warn + skip ----------
TMP5="$(mktmp)"
build_fixture_repo "$TMP5"

ctx=$(call_gatherer gather_file_list_context "$TMP5" '["file_a.py", "missing.py"]' 2>"$TMP5/stderr.log")

echo "$ctx" | grep -q 'file_a.py' || fail "test5: file_a.py missing from output"
if echo "$ctx" | grep -q 'missing.py'; then
  fail "test5: missing.py should NOT appear in the context output"
fi
grep -q 'skipping missing file: missing.py' "$TMP5/stderr.log" || \
  fail "test5: expected stderr warning for missing.py"
echo "ok test5: file-list warns + skips missing files"

# --- Test 6: file-list with a directory entry -- warn + skip ---------
TMP6="$(mktmp)"
build_fixture_repo "$TMP6"
mkdir -p "$TMP6/subdir"
printf 'inside\n' > "$TMP6/subdir/inner.py"

ctx=$(call_gatherer gather_file_list_context "$TMP6" '["file_a.py", "subdir"]' 2>"$TMP6/stderr.log")

echo "$ctx" | grep -q 'file_a.py' || fail "test6: file_a.py missing"
if echo "$ctx" | grep -q '### subdir'; then
  fail "test6: subdir should not be in the context output"
fi
if echo "$ctx" | grep -q 'inner.py'; then
  fail "test6: inner.py (inside subdir) leaked -- gatherer should not recurse"
fi
grep -q 'skipping directory: subdir' "$TMP6/stderr.log" || \
  fail "test6: expected stderr warning for subdir"
echo "ok test6: file-list warns + skips directories"

# --- Test 7: plan-scoped resolves [[wikilinks]], abs paths, rel paths ---
TMP7="$(mktmp)"
build_fixture_repo "$TMP7"
mkdir -p "$TMP7/wiki/concepts" "$TMP7/scripts" "$TMP7/tests" "$TMP7/docs"
printf 'def foo():\n    pass\n' > "$TMP7/scripts/foo.py"
printf 'echo bar\n' > "$TMP7/tests/bar.test.sh"
printf '# concept x\n' > "$TMP7/wiki/concepts/concept_x.md"
printf '# top-level readme\n' > "$TMP7/README.md"
printf '# nested doc\n' > "$TMP7/docs/notes.md"
# Absolute-path target lives outside the fixture repo
ABS_DIR="$(mktmp)"
ABS_FIXTURE="$ABS_DIR/abs_target.py"
printf 'def abs_func():\n    return "abs"\n' > "$ABS_FIXTURE"

cat > "$TMP7/plan.md" <<EOF
# Plan: example

References:
- [[concept_x]]
- scripts/foo.py
- tests/bar.test.sh
- README.md
- docs/notes.md
- $ABS_FIXTURE
EOF

ctx=$(call_gatherer gather_plan_context "$TMP7" '"plan.md"' 2>/dev/null)

echo "$ctx" | grep -q 'plan.md' || fail "test7: plan.md missing from output"
echo "$ctx" | grep -q 'wiki/concepts/concept_x.md' || \
  fail "test7: [[concept_x]] did not resolve via wiki/ search"
echo "$ctx" | grep -q 'scripts/foo.py' || \
  fail "test7: nested relative path scripts/foo.py not in output"
echo "$ctx" | grep -q 'tests/bar.test.sh' || \
  fail "test7: nested relative path tests/bar.test.sh not in output"
echo "$ctx" | grep -q 'README.md' || \
  fail "test7: top-level relative path README.md not in output"
echo "$ctx" | grep -q 'docs/notes.md' || \
  fail "test7: nested relative path docs/notes.md not in output"
echo "$ctx" | grep -q "$ABS_FIXTURE" || \
  fail "test7: absolute path $ABS_FIXTURE not in output"
echo "$ctx" | grep -qE '^\s*1\s+def foo' || \
  fail "test7: scripts/foo.py content not line-numbered"
echo "$ctx" | grep -qE '^\s*1\s+def abs_func' || \
  fail "test7: absolute-path file content not line-numbered"
echo "ok test7: plan-scoped resolves wikilinks + abs + top-level + nested rel paths"

# --- Test 8: plan-scoped with unresolvable wikilink -- warn + skip ---
TMP8="$(mktmp)"
build_fixture_repo "$TMP8"
cat > "$TMP8/plan.md" <<'EOF'
# Plan: example
References:
- [[does-not-exist]]
EOF

stderr_log="$TMP8/stderr.log"
ctx=$(call_gatherer gather_plan_context "$TMP8" '"plan.md"' 2>"$stderr_log")

echo "$ctx" | grep -q 'plan.md' || fail "test8: plan.md missing from output"
grep -q 'unresolvable wikilink: \[\[does-not-exist\]\]' "$stderr_log" || \
  fail "test8: expected stderr warning for unresolvable wikilink"
# We can't mark what we couldn't identify -- the unresolvable wikilink should
# NOT appear as a "Referenced files" section header. (It WILL appear in the
# plan body output because the gatherer echoes the plan text verbatim.)
if echo "$ctx" | grep -q '^#### does-not-exist'; then
  fail "test8: unresolvable wikilink leaked as a referenced-file section header"
fi
if echo "$ctx" | grep -q '### Referenced files'; then
  fail "test8: plan with only an unresolvable wikilink should not produce a 'Referenced files' section"
fi
echo "ok test8: plan-scoped warns on unresolvable wikilinks"

# --- Test 9: plan-scoped path-ref to missing file -- MARK in output ---
TMP9="$(mktmp)"
build_fixture_repo "$TMP9"
mkdir -p "$TMP9/scripts"
printf 'def real():\n    pass\n' > "$TMP9/scripts/real.py"

cat > "$TMP9/plan.md" <<'EOF'
# Plan: example
References:
- scripts/real.py
- scripts/missing.py
- /absolute/that/does/not/exist.py
EOF

ctx=$(call_gatherer gather_plan_context "$TMP9" '"plan.md"' 2>/dev/null)

# Real file appears with content
echo "$ctx" | grep -q 'scripts/real.py' || fail "test9: scripts/real.py missing"
echo "$ctx" | grep -qE '^\s*1\s+def real' || \
  fail "test9: scripts/real.py content not line-numbered"
# Missing relative path appears with marker
echo "$ctx" | grep -q 'scripts/missing.py' || \
  fail "test9: scripts/missing.py should be MARKED in output, not dropped"
echo "$ctx" | grep -q '_(file missing)_' || \
  fail "test9: missing-file marker not present"
# Missing absolute path also appears with marker
echo "$ctx" | grep -q '/absolute/that/does/not/exist.py' || \
  fail "test9: absolute missing path should be MARKED in output, not dropped"
echo "ok test9: plan-scoped marks missing path-refs (does not silently drop)"

# --- Test 9b: plan-scope ignores prose with slashes but no extension ---
# MiniMax Checkpoint 2 HIGH-1 fix: PATH_REF_RE used to match prose like
# 'gather/run_git' and 'abs/rel' as paths and mark them _(file missing)_,
# overwhelming the signal. Now requires either absolute path OR rel path
# with known extension OR bare filename with extension.
TMP9B="$(mktmp)"
build_fixture_repo "$TMP9B"
mkdir -p "$TMP9B/scripts"
printf 'def real():\n    pass\n' > "$TMP9B/scripts/real.py"

cat > "$TMP9B/plan.md" <<'EOF'
# Plan: example
The gatherer in `gather/run_git` does the heavy lifting.
We support abs/rel paths via Path resolution.
The 'unreadable/deleted' state is marked, not dropped.
Real reference: scripts/real.py
EOF

ctx=$(call_gatherer gather_plan_context "$TMP9B" '"plan.md"' 2>/dev/null)

# Real path-ref still resolves
echo "$ctx" | grep -q 'scripts/real.py' || \
  fail "test9b: real path scripts/real.py should still be matched"
# Prose tokens must NOT appear as referenced-file sections
if echo "$ctx" | grep -q '^#### gather/run_git'; then
  fail "test9b: 'gather/run_git' (no extension) leaked as a referenced-file section"
fi
if echo "$ctx" | grep -q '^#### abs/rel'; then
  fail "test9b: 'abs/rel' (no extension) leaked as a referenced-file section"
fi
if echo "$ctx" | grep -q '^#### unreadable/deleted'; then
  fail "test9b: 'unreadable/deleted' (no extension) leaked as a referenced-file section"
fi
echo "ok test9b: plan-scope ignores prose with slashes but no extension"

# --- Test 9c: gather_diff_scoped_context returns sorted file list -----
# MiniMax Checkpoint 2 MEDIUM-3 gap-fill: diff-scope returns files_sorted
# already, but no test asserted it. Pin the contract.
TMP9C="$(mktmp)"
build_fixture_repo "$TMP9C"
files_out=$(call_gatherer_full gather_diff_scoped_context "$TMP9C" '["file_b.py", "file_a.py"]' | sed -n '/=== files ===/,$p' | tail -n +2)
files_sorted=$(printf '%s\n' "$files_out" | sort)
[ "$files_out" = "$files_sorted" ] || \
  fail "test9c: diff-scope returned list not sorted (got: $(echo "$files_out" | tr '\n' ' '))"
echo "ok test9c: diff-scope returns sorted file list"

# --- Test 10: --scope files with empty parsed --files exits 1 --------
TMP10="$(mktmp)"
build_fixture_repo "$TMP10"

# Empty --files (just whitespace + commas)
set +e
out=$(cd "$TMP10" && python3 "$SCRIPT" --scope files --files ",, ," --no-archive 2>&1)
rc=$?
set -e
[ "$rc" = "1" ] || fail "test10: empty --files for --scope files should exit 1, got $rc"
echo "$out" | grep -q 'parsed to empty list' || \
  fail "test10: missing 'parsed to empty list' message"

# Same for --scope diff
set +e
out=$(cd "$TMP10" && python3 "$SCRIPT" --scope diff --files ",, ," --no-archive 2>&1)
rc=$?
set -e
[ "$rc" = "1" ] || fail "test10: empty --files for --scope diff should exit 1, got $rc"

echo "ok test10: empty parsed --files exits 1"

# --- Test 11: CLI rejects --scope plan without --plan ----------------
set +e
out=$(python3 "$SCRIPT" --scope plan --no-archive 2>&1)
rc=$?
set -e
[ "$rc" = "1" ] || fail "test11: --scope plan without --plan should exit 1, got $rc"
echo "$out" | grep -q 'requires --plan' || \
  fail "test11: missing 'requires --plan' message"
echo "ok test11: --scope plan requires --plan"

# --- Test 12: CLI rejects --scope diff without --files ---------------
set +e
out=$(python3 "$SCRIPT" --scope diff --no-archive 2>&1)
rc=$?
set -e
[ "$rc" = "1" ] || fail "test12: --scope diff without --files should exit 1, got $rc"
echo "$out" | grep -q 'requires --files' || \
  fail "test12: missing 'requires --files' message"
echo "ok test12: --scope diff requires --files"

# --- Test 13: CLI rejects --scope files without --files --------------
set +e
out=$(python3 "$SCRIPT" --scope files --no-archive 2>&1)
rc=$?
set -e
[ "$rc" = "1" ] || fail "test13: --scope files without --files should exit 1, got $rc"
echo "ok test13: --scope files requires --files"

# --- Test 14: argparse rejects bogus --scope value -------------------
set +e
out=$(python3 "$SCRIPT" --scope bogus --no-archive 2>&1)
rc=$?
set -e
[ "$rc" -ne "0" ] || fail "test14: --scope bogus should fail, got rc=$rc"
echo "ok test14: argparse rejects invalid --scope"

# --- Test 15: working-tree default kicks in when no --scope flag -----
TMP15="$(mktmp)"
build_fixture_repo "$TMP15"
# A clean fixture means working-tree gather returns empty -> exit 0 with
# 'no working-tree changes' message BEFORE any API call.
rm "$TMP15/extra.py"  # eliminate untracked file
set +e
out=$(cd "$TMP15" && python3 "$SCRIPT" --no-archive 2>&1)
rc=$?
set -e
[ "$rc" = "0" ] || fail "test15: clean working tree should exit 0 (no changes), got $rc"
echo "$out" | grep -q 'no working-tree changes' || \
  fail "test15: missing 'no working-tree changes' message"
echo "$out" | grep -q 'gathering working-tree context' || \
  fail "test15: missing 'gathering working-tree context' (default scope)"
echo "ok test15: working-tree default scope unchanged"
