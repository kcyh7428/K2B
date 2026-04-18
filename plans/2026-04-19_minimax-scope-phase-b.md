---
title: MiniMax reviewer scope -- Phase B
date: 2026-04-19
status: designed
feature: feature_minimax-scope-phase-b
ships-under: feature_minimax-scope-phase-b
checkpoint-1: codex-review-complete-rework-folded-into-v2
checkpoint-2: at-ship-time
up: "[[plans/index]]"
---

# Plan: MiniMax reviewer scope -- Phase B (Codex-reviewed v2)

Implements [[feature_minimax-scope-phase-b]]. Lifts the Phase A "working-tree only" scope of `scripts/lib/minimax_review.py` by adding three new context gatherers (diff-scoped, plan-scoped, file-list) and a `--scope` flag to choose between them. Default behavior is byte-for-byte unchanged for back-compat.

## v2 design pivots (in response to Codex Checkpoint 1)

Codex v1 returned REWORK with 3 P1 + 4 P2 + 1 P3 findings. All folded into this v2 before implementation.

- **P1 #1 (back-compat is unproven and silently broken):** v1's Task 2 only checked substrings; would have passed even if section ordering, whitespace, or returned file order drifted. Worse, v1 Task 10 changed the `target_label` string from `"working tree of ..."` to `"{args.scope} scope of ..."`, which is observable to MiniMax and breaks byte-for-byte equality. v2 fix: Task 2 is rewritten as a determinism + structural-shape regression test (dual-call equality + ordered section headers + sorted file list + deleted-file marker + untracked-file inclusion + line-numbering); Task 11 keeps the original `"working tree of ..."` string on the `working-tree` branch and only uses new wording for the new scopes.
- **P1 #2 (path detection too narrow):** v1's `PATH_REF_RE` only matched a prefix allowlist (`scripts|tests|plans|.claude|wiki|raw|K2B-Vault`), so absolute paths like `/Users/.../foo.py` and top-level relative paths like `README.md` or `docs/foo.md` were missed. v1's `_resolve_plan_reference()` also turned `K2B-Vault/...` into `<repo>/K2B-Vault/...`, which is wrong (K2B-Vault is a sibling of the repo, not a subdirectory). v2 fix: broaden `PATH_REF_RE` to match any token containing `/` OR ending in a known file extension, anchored on common punctuation; absolute paths resolve to `Path(token)`, relative paths resolve to `root / token`, and `K2B-Vault/...` shorthand is NOT specially handled (callers wanting vault paths use absolute paths). Tests cover absolute, top-level relative, and nested relative.
- **P1 #3 (plan-scope silently drops missing path-refs, contradicting the spec's "mark, don't drop" rule):** Spec carries forward Phase A's "mark unreadable/deleted files rather than silently drop" convention. v1's plan-scope only warned + skipped path-refs that pointed to missing files. v2 fix: split the failure modes -- unparseable wikilinks (we don't know what file is meant) warn + skip; path-refs that parse cleanly but point to missing files include a visible `_(file missing)_` marker in the output. New Task 10 covers this.
- **P2 #1 (`--scope files` with empty parsed list returns 0):** v1 returned 0 with `no readable files` even when `--files` was supplied but split into an empty list. That conflates "all files were skipped" with "user typo'd the flag." v2 fix: after splitting `args.files`, exit 1 if the parsed list is empty (Task 12). Reserve exit 0 for the "all supplied paths were missing or directories" case, which is genuinely a successful gather that produced nothing.
- **P2 #2 (file map missed `scripts/minimax-review.sh` and `CLAUDE.md`):** v1 said "no other files change", but the bash wrapper still has `# Phase A MVP: working-tree scope` in its docstring and shows only working-tree usage examples; CLAUDE.md still says "Working-tree scope only" in its Adversarial Review section. v2 fix: Tasks 14 and 15 update both files with minimal accurate doc changes.
- **P2 #3 (CLI dispatch shipped but not regression-tested):** v1 only had ad-hoc smoke commands in Task 10. Dispatch bugs in `main()` could ship undetected. v2 fix: Task 13 adds shell tests that invoke the script directly with `--scope diff|plan|files`, asserting exit codes and stderr messages for missing flags, empty lists, and the empty-files-list case. The tests stop short of network dispatch -- they exercise argparse + the gather + the early-return logic.
- **P2 #4 (trap cleanup chains overwrite each other):** v1 used `trap 'rm -rf $TMPN' EXIT` per-test; later traps replace earlier ones, so only the last fixture is reliably cleaned. v2 fix: Task 1 scaffold now uses `TMP_DIRS=()` array + a single cleanup trap that iterates the array; per-test code only appends.
- **P3 #1 (PATH_REF_RE punctuation):** v1's leading anchor `(?:^|[\s\`])` missed `(scripts/foo.py)` and `[scripts/foo.py]`. Broadening from P1 #2 above already addresses this -- the v2 regex includes `(`, `[`, `<` in the leading anchor and corresponding closers in the trailing anchor.

## Goal

Stop forcing the MiniMax reviewer into an all-or-nothing working-tree dump. Let the caller (Claude, a script, or `/ship` later) hand the reviewer a focused blob -- just the files that matter, just the plan being reviewed, or just an explicit file list -- without losing Phase A conventions (binary skip, line numbers, `MAX_FILE_BYTES` truncation).

## Why now

2026-04-19 `/ship` of `7cd1f6c` (importance-weighted rule promotion). MiniMax tripped on a 196K-character context after `gather_working_tree_context()` swept up an unrelated 905-line K2Bi plans file (`plans/2026-04-19_k2bi-bundle-3-approval-gate-spec.md`) that happened to be untracked. Codex was the only thing that finished the review. The MiniMax fallback contract -- "if Codex is down, MiniMax is the gate" -- silently broke.

The fix is in the wrapper, not the model: MiniMax-M2.7 has 200K context and the `adversarial-review.md` prompt accepts any blob via `{{REVIEW_INPUT}}`. Phase B teaches the wrapper to scope.

## Design summary

| Concern | Decision |
|---|---|
| Where the new code lives | Extends `scripts/lib/minimax_review.py` in place. ~370 lines today, projected ~600 after Phase B -- still hold-in-head. No new module. |
| How tests run | New `tests/minimax-review-scope.test.sh` shell test (matches existing repo convention). Tests build a fixture mini-repo in `mktemp -d`. No new Python test runner. |
| How tests reach the gatherers | Each gatherer gains an optional `repo_root: Path \| None = None` parameter. `None` -> use module-level `REPO_ROOT` (back-compat). Tests pass an explicit fixture path. |
| How `run_git()` reaches the fixture | `run_git()` gains an optional `cwd: Path \| None = None` parameter. Default unchanged. |
| Default `--scope` value | `working-tree`. Phase A consumers (the documented `scripts/minimax-review.sh` invocations and `/ship --skip-codex` path) keep working byte-for-byte. Pinned by the regression test. |
| Plan wikilink resolution order | `[[bare-name]]` searches `wiki/`, then `raw/`, then `<root>/<name>.md`. First hit wins. Unresolvable -> warn to stderr, skip (we can't mark what we couldn't identify). |
| Plan path-ref resolution | Absolute path -> `Path(token)`. Repo-relative path -> `root / token`. Resolved file missing? -> include `### <token>` section with `_(file missing)_` marker (the spec's "mark, don't drop" rule). Caller knows exactly which file is meant. |
| Plan path-ref regex coverage | Tokens with `/` in them (any depth, abs or rel), OR top-level tokens with a known file extension (`.py .sh .md .json .yml .yaml .toml .js .ts .html .css .sql .txt`). Anchored on left by start-of-line / whitespace / `(` / `[` / `<` / backtick; anchored on right by end / whitespace / `)` / `]` / `>` / `.,;:!?` / backtick. |
| `K2B-Vault/...` shorthand | NOT specially handled. K2B-Vault is a sibling of the repo, not a subdirectory; there's no clean way to make `K2B-Vault/...` resolve correctly without baking K2B's specific layout into a generic gatherer. Callers wanting vault paths use absolute paths. Documented in the gatherer docstring. |
| Missing files in `--files` | Warn to stderr, skip. Don't crash. |
| Directories in `--files` | Warn to stderr, skip. Don't crash. |
| Empty parsed `--files` list | Exit 1 (input invalid). Distinct from "all files skipped as missing/dirs" which exits 0 (the gather succeeded, the output is empty). |
| `MAX_FILE_BYTES` truncation | Same 256 KiB cap as Phase A across all four gatherers. Same visible note. |
| `target_label` text on default scope | Unchanged from Phase A: `"working tree of <repo> (<N> files changed)"`. New scopes get distinct labels. Pinned by Task 11. |

## File map

- **Modify** `scripts/lib/minimax_review.py` -- add `repo_root` / `cwd` parameters, three new gatherers, three new CLI flags, branched dispatch in `main()`.
- **Modify** `scripts/minimax-review.sh` -- update header docstring (Phase A MVP wording -> Phase B scope flag) and add usage examples for the new scopes.
- **Modify** `CLAUDE.md` -- update the Adversarial Review section line that says "Working-tree scope only" to mention the `--scope` flag.
- **Create** `tests/minimax-review-scope.test.sh` -- the new test file.
- **Create** `tests/fixtures/minimax-scope/README.md` -- one-line "ephemeral fixtures built per-test in tempdirs" pointer. (No checked-in fixture corpus -- everything is built in `mktemp -d` for determinism.)

No other files change. No vault writes, no schema changes, no config changes.

## Self-review against the spec

Spec section -> task in this v2 plan:

| Spec / Codex requirement | Tasks |
|---|---|
| `gather_diff_scoped_context(files)` -- only specified paths + diffs + statuses | Tasks 3, 4 |
| `gather_plan_context(plan_path)` -- plan + wikilink/path-referenced files | Tasks 8, 9, 10 |
| `gather_file_list_context(paths)` -- explicit paths, no git context | Tasks 5, 6, 7 |
| CLI `--scope working-tree\|diff\|plan\|files` (default working-tree) | Task 11 |
| CLI `--plan <path>` and `--files <path1,path2>` | Task 11 |
| Test: diff-scoped on clean vs dirty tree | Tasks 3 (clean), 4 (dirty) |
| Test: plan-scoped wikilink resolution (incl. absolute + top-level relative paths) | Task 8 |
| Test: plan-scoped unresolvable wikilink (warn) | Task 9 |
| Test: plan-scoped path-ref to missing file (mark in output, NOT silent) | Task 10 (P1 #3) |
| Test: file-list with missing files (skip + warn) and directories (skip) | Tasks 6, 7 |
| Test: regression -- default working-tree behavior preserved (determinism + structural shape) | Task 2 (rewritten per Codex P1 #1) |
| CLI regression tests (exit codes, missing flags, empty `--files` list) | Task 13 (added per Codex P2 #3) |
| `--scope files` empty parsed list exits 1 | Task 12 (added per Codex P2 #1) |
| `target_label` for working-tree branch unchanged | Asserted in Task 11 implementation note + Task 13 CLI dispatch test |
| Update bash wrapper docstring | Task 14 (added per Codex P2 #2) |
| Update CLAUDE.md Adversarial Review section | Task 15 (added per Codex P2 #2) |
| Carry forward Phase A: binary skip, MAX_FILE_BYTES, line numbering | Asserted in Task 5 (file-list happy path -- shared formatter under test) |

Coverage check: every spec bullet AND every Codex finding maps to at least one task. No placeholders below.

## Plan of attack (TDD)

Tasks 0 -- 1 are setup. Tasks 2 -- 9 follow strict RED -> GREEN per gatherer. Task 10 wires the CLI. Task 11 is a final smoke test on the live K2B working tree.

---

### Task 0: Refactor for testability (no behavior change)

**Files:**
- Modify: `scripts/lib/minimax_review.py` (function signatures only)

**Goal:** Make `gather_working_tree_context()` and `run_git()` accept an optional `repo_root` / `cwd` so tests can point them at fixture mini-repos. No behavior change for production callers.

- [ ] **Step 1: Edit `run_git()` to accept optional cwd**

Replace:

```python
def run_git(*args: str) -> str:
    return subprocess.check_output(
        ["git", *args], cwd=REPO_ROOT, text=True, errors="replace"
    )
```

With:

```python
def run_git(*args: str, cwd: Path | None = None) -> str:
    return subprocess.check_output(
        ["git", *args], cwd=cwd or REPO_ROOT, text=True, errors="replace"
    )
```

- [ ] **Step 2: Edit `gather_working_tree_context()` signature and use it**

Replace the function signature line:

```python
def gather_working_tree_context() -> tuple[str, list[str]]:
```

With:

```python
def gather_working_tree_context(
    repo_root: Path | None = None,
) -> tuple[str, list[str]]:
```

Then add at the top of the function body (after the docstring):

```python
    root = repo_root or REPO_ROOT
```

Then within the function body, replace every use of `REPO_ROOT` with `root`, and every `run_git(...)` call with `run_git(..., cwd=root)`.

- [ ] **Step 3: Smoke-test no behavior change**

Run from the K2B repo root:

```bash
python3 -c "
import sys
sys.path.insert(0, 'scripts/lib')
from minimax_review import gather_working_tree_context
ctx, files = gather_working_tree_context()
print(f'files={len(files)} ctx_chars={len(ctx)}')
"
```

Expected: prints `files=N ctx_chars=M` for some N>0 and M>0 (the working tree currently has the new feature spec + plan).

- [ ] **Step 4: Commit**

```bash
git add scripts/lib/minimax_review.py
git commit -m "refactor(minimax-review): make gather/run_git accept optional repo_root for testability"
```

---

### Task 1: Test scaffolding helpers

**Files:**
- Create: `tests/minimax-review-scope.test.sh`
- Create: `tests/fixtures/minimax-scope/README.md`

**Goal:** Get a runnable shell-test file with helpers for building fixture git repos. No real assertions yet.

- [ ] **Step 1: Create the README marker**

Write this file:

```bash
cat > tests/fixtures/minimax-scope/README.md <<'EOF'
# minimax-scope fixtures

Fixtures for `tests/minimax-review-scope.test.sh` are built per-test in
`mktemp -d` rather than checked in here. This directory exists only so the
test can refer to a stable namespace.
EOF
```

- [ ] **Step 2: Create the test scaffold (with TMP_DIRS array + single trap, per Codex P2 #4)**

Write `tests/minimax-review-scope.test.sh`:

```bash
#!/usr/bin/env bash
# tests/minimax-review-scope.test.sh
# Tests for scripts/lib/minimax_review.py Phase B scope gatherers.
#
# Builds a fixture git repo in mktemp -d per scenario, then drives the
# gatherer functions via python3 -c. Asserts on the returned context string.
#
# Cleanup: each test appends its tempdir to TMP_DIRS; the single EXIT trap
# below iterates and removes them. (Per-test `trap ... EXIT` overrides
# earlier traps in bash, which would leak all but the last fixture.)

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
# separated by a sentinel line. Used for tests that need to assert on the
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
```

- [ ] **Step 3: Make it executable and run it**

```bash
chmod +x tests/minimax-review-scope.test.sh
bash tests/minimax-review-scope.test.sh
```

Expected: prints `(scaffold loaded; no tests yet)` and exits 0.

- [ ] **Step 4: Commit**

```bash
git add tests/minimax-review-scope.test.sh tests/fixtures/minimax-scope/README.md
git commit -m "test(minimax-review): add scope test scaffold + fixture marker"
```

---

### Task 2: Regression test -- working-tree default behavior preserved (Codex P1 #1)

**Files:**
- Modify: `tests/minimax-review-scope.test.sh`

**Goal:** Pin Phase A `gather_working_tree_context()` behavior so any change that drifts it fails loudly. v1 of this task only checked substrings; this v2 covers determinism, ordered section headers, sorted file list, deleted-file marker, untracked-file inclusion, line-numbering, and the diff-section-omitted-when-empty rule. Combined with Task 0's no-behavior-change refactor, this test pins the contract Phase A consumers depend on.

- [ ] **Step 1: Add the regression test**

Append to `tests/minimax-review-scope.test.sh` (replacing the `echo "(scaffold loaded; no tests yet)"` line):

```bash
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

# Sub-test 1h: diff-section-omitted-when-empty
# Adding a new file (no HEAD diff) shouldn't include "## diff vs HEAD" if diff is empty
TMP1_NEWONLY="$(mktmp)"
build_fixture_repo "$TMP1_NEWONLY"
# Reset to a state with only an untracked file (no tracked changes)
git -C "$TMP1_NEWONLY" checkout -- file_a.py file_b.py
out_newonly=$(call_gatherer gather_working_tree_context "$TMP1_NEWONLY")
# extra.py is untracked, so git status sees it but git diff HEAD shows nothing.
# Phase A omits the "## diff vs HEAD" section if diff is empty.
if printf '%s\n' "$out_newonly" | grep -q '## diff vs HEAD'; then
  fail "test1h: empty diff should not produce '## diff vs HEAD' section (Phase A behavior)"
fi
# But the "## Full file contents" header SHOULD appear (extra.py is in there)
printf '%s\n' "$out_newonly" | grep -q '## Full file contents' || \
  fail "test1h: untracked-only case missing 'Full file contents' header"

echo "ok test1: working-tree gatherer regression (1a-1h)"
```

- [ ] **Step 2: Run and verify it passes**

```bash
bash tests/minimax-review-scope.test.sh
```

Expected: prints `ok test1: working-tree gatherer regression (1a-1h)` and exits 0.

If sub-tests fail, the failure is a real Phase A regression introduced by Task 0's refactor -- diagnose immediately, do not skip the assertion.

- [ ] **Step 3: Commit**

```bash
git add tests/minimax-review-scope.test.sh
git commit -m "test(minimax-review): pin Phase A working-tree behavior with determinism + shape regression test"
```

---

### Task 3: Diff-scoped gatherer -- clean tree case

**Files:**
- Modify: `scripts/lib/minimax_review.py` (add new gatherer)
- Modify: `tests/minimax-review-scope.test.sh` (add test)

- [ ] **Step 1: Write the failing test**

Append to `tests/minimax-review-scope.test.sh`:

```bash
# --- Test 2: diff-scoped on a clean tree (no diffs to show) ----------
TMP2="$(mktemp -d)"
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
rm -rf "$TMP2"
echo "ok test2: diff-scoped clean tree"
```

- [ ] **Step 2: Run test, verify it FAILS for the right reason**

```bash
bash tests/minimax-review-scope.test.sh
```

Expected: exits non-zero with `ImportError` or `AttributeError` because `gather_diff_scoped_context` does not exist yet.

- [ ] **Step 3: Implement minimal gatherer**

Add this function to `scripts/lib/minimax_review.py` (after `gather_working_tree_context`):

```python
def gather_diff_scoped_context(
    files: list[str],
    repo_root: Path | None = None,
) -> tuple[str, list[str]]:
    """Return (context_text, file_list) restricted to the given files.

    Includes per-file `git diff HEAD <file>` and per-file `git status -- <file>`,
    plus full content of each file. Other dirty files in the working tree
    are NOT included -- this is the "review only what I asked for" gatherer.
    """
    root = repo_root or REPO_ROOT
    if not files:
        return "", []
    sections: list[str] = []
    sections.append("## diff-scoped review (explicit file list)")
    for rel in sorted(set(files)):
        path = root / rel
        try:
            status = run_git("status", "--short", "--", rel, cwd=root).rstrip()
        except subprocess.CalledProcessError:
            status = ""
        try:
            diff = run_git("diff", "HEAD", "--", rel, cwd=root).rstrip()
        except subprocess.CalledProcessError:
            diff = ""
        sections.append(f"### {rel}")
        if status:
            sections.append("```\n" + status + "\n```")
        else:
            sections.append("_(no working-tree change vs HEAD)_")
        if diff:
            sections.append("```diff\n" + diff + "\n```")
        if not path.exists():
            sections.append("_(file missing from working tree)_")
            continue
        if path.is_dir():
            sections.append("_(directory, skipped)_")
            continue
        if is_binary(path):
            sections.append("_(binary, skipped)_")
            continue
        try:
            data = path.read_bytes()
        except OSError as e:
            sections.append(f"_(unreadable: {e})_")
            continue
        truncated_note = ""
        if len(data) > MAX_FILE_BYTES:
            data = data[:MAX_FILE_BYTES]
            truncated_note = f"\n_(truncated to {MAX_FILE_BYTES} bytes)_"
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            text = data.decode("utf-8", errors="replace")
        numbered = "\n".join(
            f"{i + 1:5d}  {line}" for i, line in enumerate(text.splitlines())
        )
        sections.append(f"```\n{numbered}\n```{truncated_note}")
    return "\n\n".join(sections), list(sorted(set(files)))
```

- [ ] **Step 4: Run test, verify it passes**

```bash
bash tests/minimax-review-scope.test.sh
```

Expected: `ok test1` and `ok test2` both print, exits 0.

- [ ] **Step 5: Commit**

```bash
git add scripts/lib/minimax_review.py tests/minimax-review-scope.test.sh
git commit -m "feat(minimax-review): add gather_diff_scoped_context (clean-tree case)"
```

---

### Task 4: Diff-scoped gatherer -- dirty tree, only listed files appear

**Files:**
- Modify: `tests/minimax-review-scope.test.sh`

**Goal:** This is the literal incident from 2026-04-19 -- prove the gatherer no longer scoops up unrelated dirty files.

- [ ] **Step 1: Write the failing test**

Append:

```bash
# --- Test 3: diff-scoped on a dirty tree -- unrelated dirty files excluded
TMP3="$(mktemp -d)"
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
rm -rf "$TMP3"
echo "ok test3: diff-scoped excludes unrelated dirty files"
```

- [ ] **Step 2: Run, verify it passes (gatherer already does the right thing)**

```bash
bash tests/minimax-review-scope.test.sh
```

Expected: tests 1, 2, 3 all pass.

- [ ] **Step 3: Commit**

```bash
git add tests/minimax-review-scope.test.sh
git commit -m "test(minimax-review): pin diff-scoped exclusion of unrelated dirty files"
```

---

### Task 5: File-list gatherer -- happy path

**Files:**
- Modify: `scripts/lib/minimax_review.py`
- Modify: `tests/minimax-review-scope.test.sh`

- [ ] **Step 1: Write the failing test**

Append:

```bash
# --- Test 4: file-list happy path -- two files, both in output -------
TMP4="$(mktemp -d)"
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
rm -rf "$TMP4"
echo "ok test4: file-list happy path"
```

- [ ] **Step 2: Verify RED**

```bash
bash tests/minimax-review-scope.test.sh
```

Expected: fails with `AttributeError: ... has no attribute 'gather_file_list_context'`.

- [ ] **Step 3: Implement minimal gatherer**

Add to `scripts/lib/minimax_review.py` (after `gather_diff_scoped_context`):

```python
def gather_file_list_context(
    paths: list[str],
    repo_root: Path | None = None,
) -> tuple[str, list[str]]:
    """Return (context_text, file_list) for an explicit list of file paths.

    No git context. Missing files and directories are skipped with a
    stderr warning -- never crash. Useful for ad-hoc "review these files"
    runs not tied to a diff or a plan.
    """
    root = repo_root or REPO_ROOT
    if not paths:
        return "", []
    sections: list[str] = []
    sections.append("## file-list review (no git context)")
    included: list[str] = []
    for rel in paths:
        path = (root / rel) if not Path(rel).is_absolute() else Path(rel)
        if not path.exists():
            print(
                f"[minimax-review] warning: skipping missing file: {rel}",
                file=sys.stderr,
            )
            continue
        if path.is_dir():
            print(
                f"[minimax-review] warning: skipping directory: {rel}",
                file=sys.stderr,
            )
            continue
        if is_binary(path):
            sections.append(f"### {rel}\n_(binary, skipped)_")
            included.append(rel)
            continue
        try:
            data = path.read_bytes()
        except OSError as e:
            sections.append(f"### {rel}\n_(unreadable: {e})_")
            continue
        truncated_note = ""
        if len(data) > MAX_FILE_BYTES:
            data = data[:MAX_FILE_BYTES]
            truncated_note = f"\n_(truncated to {MAX_FILE_BYTES} bytes)_"
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            text = data.decode("utf-8", errors="replace")
        numbered = "\n".join(
            f"{i + 1:5d}  {line}" for i, line in enumerate(text.splitlines())
        )
        sections.append(f"### {rel}{truncated_note}\n```\n{numbered}\n```")
        included.append(rel)
    return "\n\n".join(sections), included
```

- [ ] **Step 4: Verify GREEN**

```bash
bash tests/minimax-review-scope.test.sh
```

Expected: tests 1-4 all pass.

- [ ] **Step 5: Commit**

```bash
git add scripts/lib/minimax_review.py tests/minimax-review-scope.test.sh
git commit -m "feat(minimax-review): add gather_file_list_context (no git context)"
```

---

### Task 6: File-list gatherer -- missing files warn + skip

**Files:**
- Modify: `tests/minimax-review-scope.test.sh`

- [ ] **Step 1: Write the failing test (in case implementation diverges)**

Append:

```bash
# --- Test 5: file-list with one missing path -- warn + skip ----------
TMP5="$(mktemp -d)"
build_fixture_repo "$TMP5"

# Capture stderr separately
ctx=$(call_gatherer gather_file_list_context "$TMP5" '["file_a.py", "missing.py"]' 2>"$TMP5/stderr.log")

echo "$ctx" | grep -q 'file_a.py' || fail "test5: file_a.py missing from output"
if echo "$ctx" | grep -q 'missing.py'; then
  fail "test5: missing.py should NOT appear in the context output"
fi
grep -q 'skipping missing file: missing.py' "$TMP5/stderr.log" || \
  fail "test5: expected stderr warning for missing.py"
rm -rf "$TMP5"
echo "ok test5: file-list warns + skips missing files"
```

- [ ] **Step 2: Run, verify it passes (Task 5 implementation already handles this)**

```bash
bash tests/minimax-review-scope.test.sh
```

Expected: tests 1-5 all pass.

- [ ] **Step 3: Commit**

```bash
git add tests/minimax-review-scope.test.sh
git commit -m "test(minimax-review): pin missing-file warn+skip behavior in file-list scope"
```

---

### Task 7: File-list gatherer -- directories warn + skip

**Files:**
- Modify: `tests/minimax-review-scope.test.sh`

- [ ] **Step 1: Write the failing test**

Append:

```bash
# --- Test 6: file-list with a directory entry -- warn + skip ---------
TMP6="$(mktemp -d)"
build_fixture_repo "$TMP6"
mkdir -p "$TMP6/subdir"
printf 'inside\n' > "$TMP6/subdir/inner.py"

ctx=$(call_gatherer gather_file_list_context "$TMP6" '["file_a.py", "subdir"]' 2>"$TMP6/stderr.log")

echo "$ctx" | grep -q 'file_a.py' || fail "test6: file_a.py missing"
if echo "$ctx" | grep -q 'subdir'; then
  fail "test6: subdir should not be in the context output"
fi
if echo "$ctx" | grep -q 'inner.py'; then
  fail "test6: inner.py (inside subdir) leaked -- gatherer should not recurse"
fi
grep -q 'skipping directory: subdir' "$TMP6/stderr.log" || \
  fail "test6: expected stderr warning for subdir"
rm -rf "$TMP6"
echo "ok test6: file-list warns + skips directories"
```

- [ ] **Step 2: Verify it passes**

```bash
bash tests/minimax-review-scope.test.sh
```

Expected: tests 1-6 all pass.

- [ ] **Step 3: Commit**

```bash
git add tests/minimax-review-scope.test.sh
git commit -m "test(minimax-review): pin directory warn+skip behavior in file-list scope"
```

---

### Task 8: Plan-scoped gatherer -- wikilinks + paths (incl. absolute + top-level relative)

**Files:**
- Modify: `scripts/lib/minimax_review.py`
- Modify: `tests/minimax-review-scope.test.sh`

**Goal:** Implement plan-scope gatherer with broad path coverage (Codex P1 #2). Spec requires absolute paths, top-level relative paths (`README.md`), nested relative paths (`scripts/foo.py`), AND `[[wikilinks]]`. The gatherer parses both, resolves both, and includes referenced files in the output.

- [ ] **Step 1: Write the failing test**

Append:

```bash
# --- Test 7: plan-scoped resolves [[wikilinks]], abs paths, rel paths ---
TMP7="$(mktmp)"
build_fixture_repo "$TMP7"
mkdir -p "$TMP7/wiki/concepts" "$TMP7/scripts" "$TMP7/tests" "$TMP7/docs"
printf 'def foo():\n    pass\n' > "$TMP7/scripts/foo.py"
printf 'echo bar\n' > "$TMP7/tests/bar.test.sh"
printf '# concept x\n' > "$TMP7/wiki/concepts/concept_x.md"
printf '# top-level readme\n' > "$TMP7/README.md"
printf '# nested doc\n' > "$TMP7/docs/notes.md"
# Absolute-path target lives outside the fixture repo (sibling location)
ABS_FIXTURE="$(mktmp)/abs_target.py"
mkdir -p "$(dirname "$ABS_FIXTURE")"
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
```

- [ ] **Step 2: Verify RED**

```bash
bash tests/minimax-review-scope.test.sh
```

Expected: fails with `AttributeError` for `gather_plan_context`.

- [ ] **Step 3: Implement gatherer + resolvers**

Add to `scripts/lib/minimax_review.py` (after `gather_file_list_context`):

```python
WIKILINK_RE = re.compile(r"\[\[([^\]|#]+)(?:[#|][^\]]*)?\]\]")

# Path references: any token containing '/' (any depth, abs or rel) OR any
# top-level token ending in a known extension (README.md, foo.sh).
# Anchored on left by start-of-line / whitespace / ` / ( / [ / < / , / ;
# Anchored on right by end / whitespace / ` / ) / ] / > / .,;:!? terminator
# `K2B-Vault/...` shorthand is NOT specially handled -- callers wanting vault
# files use absolute paths (K2B-Vault is a sibling of the repo, not a subdir).
_PATH_EXT = "py|sh|md|json|ya?ml|toml|js|ts|tsx|jsx|html|css|sql|txt|env"
PATH_REF_RE = re.compile(
    r"(?:^|[\s`(\[<,;])"
    r"(/?(?:[\w.\-]+/)+[\w.\-]+|[\w][\w.\-]*\.(?:" + _PATH_EXT + "))"
    r"(?=[\s`)\]>.,;:!?]|$)",
    re.MULTILINE,
)


def _resolve_wikilink(token: str, root: Path) -> Path | None:
    """Resolve a bare [[wikilink]] target by searching wiki/ then raw/.

    Returns the first matching .md file, or repo-root-relative <token>.md as
    a final fallback. None means we couldn't identify any file.
    """
    for subdir in ("wiki", "raw"):
        base = root / subdir
        if not base.is_dir():
            continue
        for ext in (".md", ""):
            for match in base.rglob(f"{token}{ext}"):
                if match.is_file():
                    return match
    candidate = root / f"{token}.md"
    if candidate.is_file():
        return candidate
    return None


def _resolve_path_ref(token: str, root: Path) -> Path:
    """Resolve a path token (abs or rel) to a Path.

    Returns the candidate Path (whether or not it exists). Caller checks
    `.is_file()` -- missing files are marked in the output, never silently
    dropped (per the Phase A 'mark, don't drop' rule).
    """
    if Path(token).is_absolute():
        return Path(token)
    return root / token


def gather_plan_context(
    plan_path: str,
    repo_root: Path | None = None,
) -> tuple[str, list[str]]:
    """Return (context_text, file_list) for a plan file and the files it references.

    Parses [[wikilinks]] (resolved via wiki/ then raw/ search), inline path
    references (anything containing '/' OR a known file extension), and
    absolute paths.

    Failure modes (intentionally distinct):
      - Unparseable wikilink (no file matches the search) -> warn to stderr,
        skip. We can't mark what we couldn't identify.
      - Path ref that resolves to a missing file -> include a `### <token>`
        section with `_(file missing)_` marker. Caller knows exactly which
        file was meant; reviewer needs to see the gap.
    """
    root = repo_root or REPO_ROOT
    plan_full = (
        Path(plan_path) if Path(plan_path).is_absolute() else (root / plan_path)
    )
    if not plan_full.is_file():
        raise FileNotFoundError(f"plan not found: {plan_full}")

    plan_text = plan_full.read_text(errors="replace")

    # Two parallel collections so we can render existing files vs missing files
    # in distinct subsections.
    found_refs: list[tuple[str, Path]] = []  # (display_name, real_path)
    missing_refs: list[str] = []  # display_name only
    seen: set[str] = set()

    def _track(display: str, real: Path | None) -> None:
        if display in seen or display == plan_path:
            return
        seen.add(display)
        if real is not None and real.is_file():
            found_refs.append((display, real))
        else:
            missing_refs.append(display)

    for match in WIKILINK_RE.finditer(plan_text):
        token = match.group(1).strip()
        resolved = _resolve_wikilink(token, root)
        if resolved is None:
            print(
                f"[minimax-review] warning: unresolvable wikilink: [[{token}]]",
                file=sys.stderr,
            )
            continue
        # display name is the path under root (or absolute if outside)
        try:
            display = str(resolved.relative_to(root))
        except ValueError:
            display = str(resolved)
        _track(display, resolved)

    for match in PATH_REF_RE.finditer(plan_text):
        token = match.group(1).strip()
        resolved = _resolve_path_ref(token, root)
        # display name preserves what the plan wrote (token), but we still
        # de-dup against the absolute / relative resolved form
        try:
            display = str(resolved.relative_to(root))
        except ValueError:
            display = token  # absolute path or out-of-tree
        _track(display, resolved if resolved.is_file() else None)

    sections: list[str] = []
    sections.append("## plan-scoped review")
    sections.append(f"### {plan_path} (plan)")
    numbered_plan = "\n".join(
        f"{i + 1:5d}  {line}" for i, line in enumerate(plan_text.splitlines())
    )
    sections.append(f"```\n{numbered_plan}\n```")

    if found_refs or missing_refs:
        sections.append("### Referenced files")
        for display, real in found_refs:
            if is_binary(real):
                sections.append(f"#### {display}\n_(binary, skipped)_")
                continue
            try:
                data = real.read_bytes()
            except OSError as e:
                sections.append(f"#### {display}\n_(unreadable: {e})_")
                continue
            truncated_note = ""
            if len(data) > MAX_FILE_BYTES:
                data = data[:MAX_FILE_BYTES]
                truncated_note = f"\n_(truncated to {MAX_FILE_BYTES} bytes)_"
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError:
                text = data.decode("utf-8", errors="replace")
            numbered = "\n".join(
                f"{i + 1:5d}  {line}" for i, line in enumerate(text.splitlines())
            )
            sections.append(f"#### {display}{truncated_note}\n```\n{numbered}\n```")
        for display in missing_refs:
            sections.append(f"#### {display}\n_(file missing)_")

    file_list = [plan_path] + [d for d, _ in found_refs] + missing_refs
    return "\n\n".join(sections), file_list
```

- [ ] **Step 4: Verify GREEN**

```bash
bash tests/minimax-review-scope.test.sh
```

Expected: tests 1-7 all pass.

- [ ] **Step 5: Commit**

```bash
git add scripts/lib/minimax_review.py tests/minimax-review-scope.test.sh
git commit -m "feat(minimax-review): add gather_plan_context with broad wikilink + abs/rel path resolution"
```

---

### Task 9: Plan-scoped -- unresolvable wikilink warns and continues

**Files:**
- Modify: `tests/minimax-review-scope.test.sh`

- [ ] **Step 1: Write the failing test**

Append:

```bash
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
# We can't mark what we couldn't identify -- the wikilink should NOT appear in output
if echo "$ctx" | grep -q 'does-not-exist'; then
  fail "test8: unresolvable wikilink should NOT appear in context (we don't know the file)"
fi
echo "ok test8: plan-scoped warns on unresolvable wikilinks"
```

- [ ] **Step 2: Verify it passes**

```bash
bash tests/minimax-review-scope.test.sh
```

Expected: tests 1-8 all pass.

- [ ] **Step 3: Commit**

```bash
git add tests/minimax-review-scope.test.sh
git commit -m "test(minimax-review): pin unresolvable-wikilink warn+skip behavior in plan scope"
```

---

### Task 10: Plan-scoped -- path-ref to missing file is MARKED, not silently dropped (Codex P1 #3)

**Files:**
- Modify: `tests/minimax-review-scope.test.sh`

**Goal:** Pin the spec's "mark, don't drop" rule for path-refs (the rule v1 broke). When a plan references `scripts/foo.py` and the file doesn't exist, the gatherer must include a visible `### scripts/foo.py` section with `_(file missing)_` -- never silently omit it.

- [ ] **Step 1: Write the failing test**

Append:

```bash
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
```

- [ ] **Step 2: Verify it passes**

```bash
bash tests/minimax-review-scope.test.sh
```

Expected: tests 1-9 all pass. (Task 8's implementation already handles this -- the resolver returns the candidate path, the gatherer checks `.is_file()` separately and routes to `missing_refs`.)

- [ ] **Step 3: Commit**

```bash
git add tests/minimax-review-scope.test.sh
git commit -m "test(minimax-review): pin 'mark missing path-refs' rule in plan scope"
```

---

### Task 11: Wire CLI flags (preserve back-compat target_label per Codex P1 #1)

**Files:**
- Modify: `scripts/lib/minimax_review.py` (`main()` only)

**Goal:** Add `--scope`, `--plan`, `--files` flags. Dispatch to the right gatherer. Critical: keep the existing `target_label` string `"working tree of <repo> (<N> files changed)"` for the working-tree branch -- that string is part of every prompt MiniMax sees and changing it breaks byte-for-byte back-compat.

- [ ] **Step 1: Replace the argparse block in `main()`**

Find:

```python
    parser.add_argument(
        "--scope",
        default="working-tree",
        choices=["working-tree"],
        help="(Phase A: working-tree only)",
    )
```

Replace with:

```python
    parser.add_argument(
        "--scope",
        default="working-tree",
        choices=["working-tree", "diff", "plan", "files"],
        help=(
            "Context gatherer: 'working-tree' (default, Phase A behavior), "
            "'diff' (only --files paths + their diffs), "
            "'plan' (--plan path + files it references), "
            "'files' (just --files paths, no git context)"
        ),
    )
    parser.add_argument(
        "--plan",
        default=None,
        help="Plan file path (required when --scope plan)",
    )
    parser.add_argument(
        "--files",
        default=None,
        help="Comma-separated list of paths (required when --scope diff or files)",
    )
```

- [ ] **Step 2: Replace the dispatcher in `main()`**

Find:

```python
    print(f"[minimax-review] gathering {args.scope} context...", file=sys.stderr)
    context, changed = gather_working_tree_context()
    if not changed:
        print("[minimax-review] no working-tree changes; nothing to review.", file=sys.stderr)
        return 0
```

Replace with:

```python
    print(f"[minimax-review] gathering {args.scope} context...", file=sys.stderr)
    if args.scope == "working-tree":
        context, changed = gather_working_tree_context()
        if not changed:
            print(
                "[minimax-review] no working-tree changes; nothing to review.",
                file=sys.stderr,
            )
            return 0
    elif args.scope == "diff":
        if not args.files:
            print("[minimax-review] --scope diff requires --files", file=sys.stderr)
            return 1
        file_list = [p.strip() for p in args.files.split(",") if p.strip()]
        if not file_list:
            print(
                "[minimax-review] --scope diff: --files parsed to empty list",
                file=sys.stderr,
            )
            return 1
        context, changed = gather_diff_scoped_context(file_list)
    elif args.scope == "plan":
        if not args.plan:
            print("[minimax-review] --scope plan requires --plan", file=sys.stderr)
            return 1
        try:
            context, changed = gather_plan_context(args.plan)
        except FileNotFoundError as e:
            print(f"[minimax-review] {e}", file=sys.stderr)
            return 1
    elif args.scope == "files":
        if not args.files:
            print("[minimax-review] --scope files requires --files", file=sys.stderr)
            return 1
        file_list = [p.strip() for p in args.files.split(",") if p.strip()]
        if not file_list:
            print(
                "[minimax-review] --scope files: --files parsed to empty list",
                file=sys.stderr,
            )
            return 1
        context, changed = gather_file_list_context(file_list)
    else:
        print(f"[minimax-review] unknown scope: {args.scope}", file=sys.stderr)
        return 1
```

- [ ] **Step 3: Replace the target_label block (Codex P1 #1: keep working-tree string identical to Phase A)**

Find:

```python
    target_label = (
        f"working tree of {REPO_ROOT.name} ({len(changed)} files changed)"
    )
```

Replace with:

```python
    if args.scope == "working-tree":
        # Phase A wording preserved verbatim -- byte-for-byte back-compat for
        # the prompt MiniMax sees. Do not alter.
        target_label = (
            f"working tree of {REPO_ROOT.name} ({len(changed)} files changed)"
        )
    elif args.scope == "diff":
        target_label = (
            f"diff-scoped review of {REPO_ROOT.name} ({len(changed)} files)"
        )
    elif args.scope == "plan":
        target_label = f"plan {args.plan} ({len(changed)} files referenced)"
    else:  # files
        target_label = (
            f"explicit file list ({len(changed)} files, repo {REPO_ROOT.name})"
        )
```

- [ ] **Step 4: Smoke-test help text and missing-flag exit codes**

```bash
python3 scripts/lib/minimax_review.py --help 2>&1 | grep -q -- '--scope' || \
  echo "FAIL: --scope flag missing from help"
python3 scripts/lib/minimax_review.py --help 2>&1 | grep -q -- '--plan' || \
  echo "FAIL: --plan flag missing from help"
python3 scripts/lib/minimax_review.py --help 2>&1 | grep -q -- '--files' || \
  echo "FAIL: --files flag missing from help"
echo "help text OK"
```

Expected: prints `help text OK`.

- [ ] **Step 5: Re-run the full test suite (function-level tests should still pass)**

```bash
bash tests/minimax-review-scope.test.sh
```

Expected: tests 1-9 all pass.

- [ ] **Step 6: Commit**

```bash
git add scripts/lib/minimax_review.py
git commit -m "feat(minimax-review): wire --scope/--plan/--files dispatcher (Phase A target_label preserved)"
```

---

### Task 12: `--scope files`/`diff` with empty parsed `--files` list exits 1 (Codex P2 #1)

**Files:**
- Modify: `tests/minimax-review-scope.test.sh`

**Goal:** Pin the exit-code contract. Task 11 already implements the exit-1 path; this task adds explicit test coverage so the contract doesn't silently regress.

- [ ] **Step 1: Add the test**

Append:

```bash
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
```

- [ ] **Step 2: Run, verify it passes**

```bash
bash tests/minimax-review-scope.test.sh
```

Expected: tests 1-10 all pass.

- [ ] **Step 3: Commit**

```bash
git add tests/minimax-review-scope.test.sh
git commit -m "test(minimax-review): pin exit-1 contract for empty parsed --files list"
```

---

### Task 13: CLI dispatch regression tests (Codex P2 #3)

**Files:**
- Modify: `tests/minimax-review-scope.test.sh`

**Goal:** The function-level tests don't exercise `main()`. Dispatch bugs in argparse + early returns could ship undetected. This task adds CLI-level tests that stop short of the actual MiniMax network call.

- [ ] **Step 1: Add the tests**

Append:

```bash
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

# --- Test 14: CLI accepts unknown --scope value via argparse ---------
# argparse should reject before our code runs
set +e
out=$(python3 "$SCRIPT" --scope bogus --no-archive 2>&1)
rc=$?
set -e
[ "$rc" -ne "0" ] || fail "test14: --scope bogus should fail, got rc=$rc"
echo "ok test14: argparse rejects invalid --scope"

# --- Test 15: working-tree default still kicks in (no --scope flag) --
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
# Also confirm scope defaulted to working-tree (not echoed to stderr explicitly,
# but the message "gathering working-tree context" is)
echo "$out" | grep -q 'gathering working-tree context' || \
  fail "test15: missing 'gathering working-tree context' (default scope)"
echo "ok test15: working-tree default scope unchanged"
```

- [ ] **Step 2: Run all tests**

```bash
bash tests/minimax-review-scope.test.sh
```

Expected: tests 1-15 all pass.

- [ ] **Step 3: Commit**

```bash
git add tests/minimax-review-scope.test.sh
git commit -m "test(minimax-review): add CLI-dispatch regression tests (exit codes, missing flags, default scope)"
```

---

### Task 14: Update `scripts/minimax-review.sh` header docstring (Codex P2 #2)

**Files:**
- Modify: `scripts/minimax-review.sh`

**Goal:** The wrapper still says `# Phase A MVP: working-tree scope, single-shot, JSON output.` and shows only working-tree usage examples. Once Phase B ships, that's a lie.

- [ ] **Step 1: Replace the header docstring**

Find in `scripts/minimax-review.sh`:

```bash
#!/usr/bin/env bash
# Standalone MiniMax M2.7 adversarial code reviewer.
# Phase A MVP: working-tree scope, single-shot, JSON output.
# Touches nothing in /ship or the codex plugin -- runs as its own tool.
#
# Usage:
#   scripts/minimax-review.sh                       # review working tree
#   scripts/minimax-review.sh --focus "auth path"   # extra focus area
#   scripts/minimax-review.sh --json                # raw JSON to stdout
#   scripts/minimax-review.sh --model MiniMax-M2.5  # try a different model
```

Replace with:

```bash
#!/usr/bin/env bash
# Standalone MiniMax M2.7 adversarial code reviewer.
# Single-shot, JSON output. Touches nothing in /ship or the codex plugin --
# runs as its own tool.
#
# Usage:
#   scripts/minimax-review.sh                                  # working-tree (default)
#   scripts/minimax-review.sh --focus "auth path"              # extra focus area
#   scripts/minimax-review.sh --json                           # raw JSON to stdout
#   scripts/minimax-review.sh --model MiniMax-M2.5             # different model
#
# Scopes (Phase B):
#   --scope working-tree                                       # default, all dirty files
#   --scope diff --files a.py,b.py                             # only listed files + diffs
#   --scope plan --plan plans/2026-04-19_my-plan.md            # plan + files it references
#   --scope files --files a.py,b.py                            # explicit list, no git context
```

- [ ] **Step 2: Smoke-test that the wrapper still runs**

```bash
bash scripts/minimax-review.sh --help 2>&1 | head -20
```

Expected: shows the standard `argparse` help output for the Python script (the wrapper just `exec`s it).

- [ ] **Step 3: Commit**

```bash
git add scripts/minimax-review.sh
git commit -m "docs(minimax-review): update wrapper docstring with Phase B --scope examples"
```

---

### Task 15: Update CLAUDE.md Adversarial Review section (Codex P2 #2)

**Files:**
- Modify: `CLAUDE.md`

**Goal:** The "Adversarial Review" section says MiniMax-M2.7 is "Working-tree scope only". Phase B lifts that. Update the line.

- [ ] **Step 1: Edit CLAUDE.md line ~292**

Find:

```markdown
- **MiniMax-M2.7** (fallback, via `scripts/minimax-review.sh`) -- when Codex daily quota is depleted OR for fast iterative passes during a single commit. Working-tree scope only; ~30 seconds per pass; same `MINIMAX_API_KEY` quota as the rest of K2B's MiniMax stack. Spec: [[wiki/concepts/Shipped/feature_minimax-adversarial-reviewer]]. Invoke with `/ship --skip-codex codex-quota-depleted` plus a manual `scripts/minimax-review.sh` run on the diff.
```

Replace with:

```markdown
- **MiniMax-M2.7** (fallback, via `scripts/minimax-review.sh`) -- when Codex daily quota is depleted OR for fast iterative passes during a single commit. Scopes: `--scope working-tree` (default, full dirty tree), `--scope diff --files a,b` (specified files + their diffs), `--scope plan --plan path/to/plan.md` (plan + files it references), `--scope files --files a,b` (explicit list, no git context). ~30 seconds per pass; same `MINIMAX_API_KEY` quota as the rest of K2B's MiniMax stack. Specs: [[wiki/concepts/Shipped/feature_minimax-adversarial-reviewer]] (Phase A) + [[wiki/concepts/feature_minimax-scope-phase-b]] (Phase B scope flag). Invoke with `/ship --skip-codex codex-quota-depleted` plus a manual `scripts/minimax-review.sh` run on the diff.
```

- [ ] **Step 2: Commit**

```bash
git add CLAUDE.md
git commit -m "docs(claude): update Adversarial Review section with Phase B scope flags"
```

---

### Task 16: Final verification

**Files:** none (verification only)

- [ ] **Step 1: Run the full new test file**

```bash
bash tests/minimax-review-scope.test.sh
```

Expected: prints `ok test1` through `ok test15` and exits 0.

- [ ] **Step 2: Run the existing test suite to confirm no regressions**

```bash
bash tests/sort-key.test.sh
bash tests/promote-learnings-importance.test.sh
bash tests/select-lru-victim-importance.test.sh
bash tests/lint-memory.test.sh
```

Expected: each exits 0 (these are unrelated to minimax_review.py but worth a sanity check that nothing leaked).

- [ ] **Step 3: Smoke-test the live working-tree path (back-compat sanity)**

```bash
python3 -c "
import sys
sys.path.insert(0, 'scripts/lib')
from minimax_review import gather_working_tree_context
ctx, files = gather_working_tree_context()
print(f'files={len(files)} ctx_chars={len(ctx)}')
"
```

Expected: prints `files=N ctx_chars=M` for the current K2B working tree.

- [ ] **Step 4: Smoke-test the new diff-scoped path on the live tree**

```bash
python3 -c "
import sys
sys.path.insert(0, 'scripts/lib')
from minimax_review import gather_diff_scoped_context
ctx, files = gather_diff_scoped_context(['scripts/lib/minimax_review.py'])
print(f'files={len(files)} ctx_chars={len(ctx)}')
print('files included:', files)
"
```

Expected: `files included: ['scripts/lib/minimax_review.py']`. Unrelated dirty files MUST NOT appear.

- [ ] **Step 5: Smoke-test the new plan-scoped path on this very plan**

```bash
python3 -c "
import sys
sys.path.insert(0, 'scripts/lib')
from minimax_review import gather_plan_context
ctx, files = gather_plan_context('plans/2026-04-19_minimax-scope-phase-b.md')
print(f'files={len(files)} ctx_chars={len(ctx)}')
print('referenced:', files[1:])
"
```

Expected: prints the plan + the files it references (`scripts/lib/minimax_review.py`, `tests/minimax-review-scope.test.sh`, etc.). Confirms wikilink/path resolution works against the live K2B repo.

---

## Out of scope for this plan (deferred)

- `/ship` integration -- next ship (`feature_adversarial-review-tiering`) wires the new scopes into the gate flow.
- Auto-classification of diffs -- also next ship.
- Caching of review results across scopes -- not on the roadmap.
- Plan-scope: deduping near-duplicate file paths via canonicalization -- the resolver picks the first hit, which is good enough today.

## Checkpoint 1 (Codex) -- complete

Codex v1 review returned REWORK with 3 P1 + 4 P2 + 1 P3 findings. All folded into the v2 plan above (see "v2 design pivots" section at top). Re-running Codex on v2 is OUT of scope -- v2 directly addresses every P1 and P2 inline, mirroring the importance-weighted plan workflow which also went one round (v1 -> v2 -> implement).

## Checkpoint 2 (Codex) -- at /ship time

Pre-commit Codex review runs as the standard /ship Step 3. If Codex quota is depleted at ship time, fall back to `scripts/minimax-review.sh` -- and given this very ship adds three new scopes, prefer `--scope diff --files <changed-files>` over the default working-tree to demonstrate the new feature reviewing itself.
