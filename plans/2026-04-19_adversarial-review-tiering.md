---
title: Adversarial review tiering -- Ship 1 (classifier + /ship Step 3 routing)
date: 2026-04-19
status: codex-reviewed-rework-folded-into-v2
feature: feature_adversarial-review-tiering
ships-under: feature_adversarial-review-tiering
checkpoint-1: codex-review-complete-rework-folded-into-v2
checkpoint-2: at-ship-time
up: "[[plans/index]]"
---

# Plan: Adversarial review tiering -- Ship 1 (Codex-reviewed v2)

Implements Ship 1 of [[feature_adversarial-review-tiering]]. Adds a 4-tier classifier (`scripts/ship-detect-tier.py` + `scripts/lib/tier_detection.py`) that reads the uncommitted working-tree diff and emits `0`, `1`, `2`, or `3`, plus a routing update to `.claude/skills/k2b-ship/SKILL.md` Step 3 that calls the classifier and branches on the tier. Adds `scripts/tier3-paths.yml` (opt-in allowlist) and `tests/ship-detect-tier.test.sh` (shell-test convention).

Ship 2 (manual `/ship --tier N` override + Codex `--cached` vs `--working-tree` diagnostic) is deferred to let Ship 1 bake for ~1 week first.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

## v2 design pivots (in response to Codex Checkpoint 1)

Codex v1 returned REWORK with 5 HIGH, 5 MEDIUM, 3 LOW findings plus a scope-split recommendation and an omissions list. All folded into this v2 before implementation.

### HIGH severity folds (must-fix, all addressed)

- **HIGH #1 (`/ship --tier N` has no parser in the skill world).** Codex is right: `k2b-ship` is a skill doc loaded into Claude's context, not a bash CLI with argparse. The v1 plan's "argparse in `/ship` command handler" was fictional. **v2 fix:** remove `/ship --tier N` from Ship 1 entirely. Ship 2 will re-introduce it as an explicit skill-level contract (e.g. "if invocation text exactly matches `/ship --tier N` where N in {0,1,2,3}, set TIER_OVERRIDE before Step 3"), with an integration test asserting the contract. Deferring also lets Ship 1 bake long enough to reveal whether the override is actually needed.

- **HIGH #2 (Tier 1 MiniMax failure silently bypasses the gate).** v1 had the Tier 1 while-loop `break` on MiniMax non-zero exit, which continues to commit without any adversarial pass. This regresses `/ship`'s existing "both reviewers unavailable -> REFUSE" invariant. **v2 fix:** on MiniMax non-zero exit in Tier 1, escalate to Tier 2 Codex single-pass (reassign `TIER=2` and fall through). If Codex is ALSO unavailable in Tier 2, REFUSE with the same loud-error path as today's Step 3. Task 11 explicitly tests the escalation and both-fail REFUSE.

- **HIGH #3 (Tier 1 verdict parsing reads the wrong key at the wrong case).** Verified against [scripts/lib/minimax_review.py](scripts/lib/minimax_review.py:442) (`verdict = parsed.get("verdict", "?")`) and :451 (`"APPROVE" if verdict == "approve"`): the verdict lives inside `parsed["verdict"]` and the value is lowercase `"approve"`. v1's `json.load(...).get('verdict', '')` would read the top-level archive record which does NOT contain `verdict` (it contains `parsed` as a sub-dict). **v2 fix:** invoke `scripts/minimax-review.sh --json` to get the `parsed` dict on stdout directly (see [scripts/lib/minimax_review.py:713-714](scripts/lib/minimax_review.py)), then compare `verdict.lower() == "approve"`. No archive scraping. Task 11 pins this.

- **HIGH #4 ("new script under scripts/ -> Tier 3" over-broad).** v1 classified every new `scripts/*.py` as Tier 3. That over-classifies read-only helpers, migration scripts, and scaffolds (e.g. a new `scripts/normalize-dates.py`-style utility would incorrectly hit Tier 3). **v2 fix:** rule deleted entirely. Ship 1 relies on the allowlist + scale + Tier 2 default only. If a new script is blast-radius-critical, add it to `tier3-paths.yml` before shipping.

- **HIGH #5 (missing `tier3-paths.yml` silently empties the allowlist).** In K2B the config is part of the shipped contract, like `ownership-watchlist.yml`. Missing = broken install = silent downgrade of all Tier 3 paths. **v2 fix:** if the config path is provided (default or explicit) and missing, the classifier raises an error. The CLI wrapper catches the error, exits 1, and `/ship` Step 3a falls back to Tier 3 (loudest-safe behavior). Forks opting out would need an explicit `--no-config` flag; Ship 1 does not ship that flag (YAGNI until a fork actually needs it).

### MEDIUM severity folds (should-fix, all addressed)

- **MEDIUM #1 (LOC threshold wrong).** v1 used 100 LOC. Keith's spec calls `7cd1f6c` (155 LOC across 2 files) "Tier 2 HEALTHY" -- contradiction with the 100 threshold. Codex recommends 200. **v2 fix:** scale rule fires at `>3 files OR >200 LOC` (insertions + deletions). Pinned by Task 5. Test for `7cd1f6c`-shape now correctly asserts Tier 2.
- **MEDIUM #2 (7cd1f6c regression test uses neutral paths, hides production shape).** v1's test of `7cd1f6c` used `neutral_code.py` to isolate the scale rule from the allowlist rule -- useful as calibration, but not a regression for the real production shape (where `scripts/promote-learnings.py` IS in the Tier 3 allowlist). **v2 fix:** Task 6 splits into two tests. Test 6a = calibration fixture with neutral path (asserts Tier 2 under scale rule). Test 6b = production-shape regression with real `scripts/promote-learnings.py` (asserts Tier 3 because allowlist wins over size).
- **MEDIUM #3 (scale-before-docs reintroduces the doc-clog problem).** v1 ordering was: Tier 0 -> Tier 3 allowlist -> Tier 3 new-script -> Tier 3 scale -> Tier 1 docs -> Tier 2. A 250-line SKILL.md doc commit would hit the scale rule and fall to Tier 3 before the docs rule fires, directly contradicting the motivating `73984d3` "doc clog" case. **v2 fix:** rule order is now Tier 0 -> Tier 3 allowlist -> Tier 1 docs -> Tier 3 scale -> Tier 2. Docs never hit Tier 3 scale. Task 4 pins docs ahead of Task 5 (scale).
- **MEDIUM #4 (`awk '{print $NF}'` unsafe on spaces and renames).** **v2 fix:** the Step 3 bash uses the classifier's parsed file list (echoed as null-separated on stderr or via a second CLI mode), OR uses `git diff --name-only -z HEAD | tr '\0' ','` for a safe comma-separated list. Task 10 documents both options; Task 11 picks one and tests it.
- **MEDIUM #5 (Task 13 Codex `--cached` diagnostic open-ended).** v1 allowed Task 13 to "maybe complete this session, maybe ticket out" -- ambiguous acceptance. Codex recommends splitting. **v2 fix:** Task 13 removed from Ship 1. The Codex scope diagnostic becomes part of Ship 2 as a bounded investigation with its own acceptance criterion.

### LOW severity folds

- **LOW #1 (`**` glob handling ad hoc).** v1's `"foo/**"` -> prefix match is not standard glob semantics. **v2 fix:** document the exact contract in `scripts/tier3-paths.yml` header comment and test it with a fixture that matches `k2b-remote/src/**` against `k2b-remote/src/nested/deep/file.ts`. If a future edit wants real recursive glob semantics, that's a follow-up.
- **LOW #2 (Task 14 smoke expected tier relies on sibling-repo file).** The v1 plan predicted Tier 0 because the feature spec in `K2B-Vault/` is Tier 0. But the classifier runs against the K2B git repo, which does NOT track `K2B-Vault/`. **v2 fix:** Task 12 (renumbered) asserts classifier output for the actual K2B working tree at that moment (likely Tier 2 or Tier 3 depending on the ship diff shape), not a synthetic expectation.
- **LOW #3 (`K2B-Vault/` in Tier 0 is mostly dead code).** True -- vault-only changes don't use `/ship` per SKILL.md. **v2 fix:** keep the rule (harmless, future-proof for a mono-repo layout) and document it in the classifier docstring as "fork/mono-repo portability, not primary K2B path."

### Omissions addressed (from Codex's omissions list)

- **`--skip-codex` + Tier 1 MiniMax fail:** v2 Task 11 flow: if `--skip-codex` is passed AND MiniMax fails in Tier 1, REFUSE (both reviewers blocked). Pinned by test.
- **Multi-ship features: tier recorded per ship row.** The DEVLOG entry and feature-note Shipping Status table get `tier: N (reason)` per ship. Documented in Task 10.
- **Renames + paths with spaces:** `git diff --name-only -z` handles both. Task 10 uses `-z`.
- **Repo portability (K2B vs fork):** Tier 0 rule is documented in the classifier as "K2B-specific Tier 0 prefixes + optional fork overrides." Forks copy and edit `scripts/tier3-paths.yml`. Ship 1 doesn't ship a fork-override flag (YAGNI).
- **Staged vs unstaged diff consistency:** classifier reads `git status --porcelain` (covers both) + `git diff --numstat` + `git diff --cached --numstat` (task 1 merges both sources). Same parsed file list is what Step 3 passes to MiniMax `--scope diff`, so the review sees the same files the classifier saw.
- **Tier 2 Codex single-pass invariant:** explicitly preserves today's "MiniMax fallback if Codex unreachable" path. `--skip-codex <reason>` in Tier 2 routes straight to MiniMax `--scope diff`. Documented in Task 10.
- **`.minimax-reviews/` nonexistence:** the MiniMax script creates it on archive (see `archive_dir.mkdir(parents=True, exist_ok=True)` at minimax_review.py:505). Tier 1 loop does not read it -- parses `--json` stdout instead -- so the dir can be absent.
- **`.claude/plans/` Tier 0:** kept in Tier 0 list alongside `plans/`. Tested in Task 2.

## Goal

Auto-classify commits at `/ship` and route to reviewer + intensity that matches blast radius, ending the "one-protocol-fits-all" clog where a doc commit ran 9 Codex findings over 2 passes while a trading-code commit correctly ran 22 rounds.

## Architecture

Heuristic classifier (Python, `scripts/lib/tier_detection.py` + `scripts/ship-detect-tier.py` CLI) reads `git status --porcelain` + `git diff --numstat` + `git diff --cached --numstat`, applies first-match-wins rules:

1. **Tier 0** -- all files under `K2B-Vault/`, `DEVLOG.md`, `plans/`, `.claude/plans/`
2. **Tier 3 (allowlist)** -- any file matches glob in `scripts/tier3-paths.yml`
3. **Tier 1 (pure docs)** -- all files are `.md` under `.claude/skills/`, `wiki/`, `CLAUDE.md`, or `README.md`
4. **Tier 3 (scale)** -- `>3 files` OR `>200 LOC` (insertions + deletions; binary = 0)
5. **Tier 2** -- default

Prints `tier: N\nreason: <text>`. Exit 0 success, 1 error. `scripts/tier3-paths.yml` holds YAML allowlist (glob patterns) parsed via PyYAML 6.0.2.

The `k2b-ship` SKILL.md Step 3 runs the classifier first (Step 3a), then branches (Step 3b): Tier 0 = skip, Tier 1 = MiniMax diff-scoped 2-pass cap with escalation-to-Tier-2 on failure, Tier 2 = today's single-pass Codex-or-MiniMax flow, Tier 3 = today's iterate-until-clean flow verbatim (Step 3c).

## Tech stack

Python 3 (stdlib + PyYAML 6.0.2, already available); bash + `python3 -c` for shell tests following the `tests/minimax-review-scope.test.sh` convention. No new runtime dependencies.

## File map

- **Create** `scripts/lib/tier_detection.py` -- classifier logic (~130 lines projected): parse git inputs, load YAML, apply rules, return `(tier: int, reason: str)`.
- **Create** `scripts/ship-detect-tier.py` -- CLI wrapper (~40 lines): argparse, dispatch, exit codes, format output.
- **Create** `scripts/tier3-paths.yml` -- initial K2B allowlist (~30 lines, declarative).
- **Create** `tests/ship-detect-tier.test.sh` -- shell tests driving the classifier via `python3 -c` imports and fixture git repos (same pattern as `tests/minimax-review-scope.test.sh`).
- **Modify** `.claude/skills/k2b-ship/SKILL.md` -- insert Step 3a (tier detection) + Step 3b (tier branching) before the existing flow, preserve the existing flow verbatim as Step 3c "Tier 3 unchanged."

No other files change. No vault writes (the feature-note Updates entry happens at `/ship` time, not in this plan). No schema or config changes beyond `scripts/tier3-paths.yml`.

## Self-review against the spec + Codex findings

| Requirement | Task |
|---|---|
| `scripts/ship-detect-tier.py` emits tier 0-3 per heuristic rules | Tasks 1-8 |
| Unit-testable | Task 0 scaffolds; Tasks 2-7 drive via fixture repos |
| `scripts/tier3-paths.yml` opt-in allowlist | Task 3, Task 9 |
| k2b-ship Step 3 runs tier detection before invoking reviewer | Task 10 |
| Tier 0 = skip (log only) | Task 10 |
| Tier 1 = MiniMax `--scope diff --files <diff files>`, cap 2 passes, escalate to Tier 2 on MiniMax failure | Task 11 |
| Tier 2 = Codex single pass, `--skip-codex` routes to MiniMax diff-scoped | Task 10 |
| Tier 3 = current multi-round Codex flow unchanged | Task 10 |
| TDD per evidence case (K2B `73984d3` = 1, K2B `7cd1f6c` = 2, K2Bi `befc26b` = 3, K2Bi `530eb81` = 3) | Tasks 2, 4, 5, 6 |
| Classifier failure -> fall back to Tier 3 (loud, not silent) | Task 7, Task 10 |
| Missing `tier3-paths.yml` = error, not silent empty (Codex HIGH #5) | Task 7 |
| Tier 1 MiniMax failure does NOT silently break the gate (Codex HIGH #2) | Task 11 |
| Tier 1 verdict parsing reads `parsed.verdict` case-insensitively (Codex HIGH #3) | Task 11 |
| No "new script" rule (Codex HIGH #4) | Not implemented (omitted by design) |
| LOC threshold 200, not 100 (Codex MEDIUM #1) | Task 5 |
| Evidence test split: calibration + production (Codex MEDIUM #2) | Task 6 |
| Docs rule ahead of scale rule (Codex MEDIUM #3) | Task 4 before Task 5 |
| `git diff --name-only -z` for changed-file list (Codex MEDIUM #4) | Task 10 |
| `**` glob semantics documented + tested (Codex LOW #1) | Task 3 |
| Smoke test uses repo-local expectation (Codex LOW #2) | Task 12 |
| Multi-ship feature tier recorded per ship row | Task 10 |
| Renames + spaces in diff file list | Task 10 |
| `--skip-codex` + Tier 1 MiniMax fail -> REFUSE | Task 11 |

No placeholders. No TODOs.

## Plan of attack (TDD)

Tasks 0 -- 1 are setup. Tasks 2 -- 7 follow strict RED -> GREEN per rule, driving the classifier via fixture git repos. Task 8 is the CLI wrapper. Task 9 ships the initial YAML allowlist. Tasks 10 -- 11 are SKILL.md surgery. Task 12 is the final integration smoke on live K2B.

---

### Task 0: Scaffold test harness + module stub (no behavior change)

**Files:**
- Create: `tests/ship-detect-tier.test.sh`
- Create: `scripts/lib/tier_detection.py` (stub)

**Goal:** Establish the test harness and import path before writing real logic.

- [ ] **Step 1: Create the test file**

Write `tests/ship-detect-tier.test.sh`:

```bash
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
```

```bash
chmod +x tests/ship-detect-tier.test.sh
```

- [ ] **Step 2: Create the module stub**

Write `scripts/lib/tier_detection.py`:

```python
"""Tier detection for adversarial review routing.

classify_tier(repo_root, tier3_config_path) -> (tier: int, reason: str)

Reads git status + numstat from repo_root, applies first-match-wins rules,
returns (tier, reason). See feature_adversarial-review-tiering.md.

Rule order (first match wins):
  1. Tier 0 -- all files under K2B-Vault/, DEVLOG.md, plans/, .claude/plans/
  2. Tier 3 -- any file matches glob in tier3-paths.yml allowlist
  3. Tier 1 -- all files are .md under .claude/skills/, wiki/, CLAUDE.md, README.md
  4. Tier 3 -- >3 files OR >200 LOC (insertions + deletions)
  5. Tier 2 -- default (real code or tests within budget)

Note on K2B-Vault/ in Tier 0: K2B-Vault/ is a sibling directory, not tracked
by this repo. The rule exists for fork/mono-repo portability. In primary K2B
usage it is effectively dead code (vault-only changes never invoke /ship per
k2b-ship SKILL.md "When NOT to Use").
"""

from __future__ import annotations

import fnmatch
import subprocess
from pathlib import Path


def classify_tier(
    repo_root: str | Path,
    tier3_config_path: str | Path | None = None,
) -> tuple[int, str]:
    """Stub -- raises NotImplementedError until Task 2."""
    raise NotImplementedError("classify_tier stub -- see Task 2")
```

- [ ] **Step 3: Verify the test harness**

```bash
bash tests/ship-detect-tier.test.sh
```

Expected: `all tests passed` (no tests registered yet, harness loads cleanly).

- [ ] **Step 4: Commit**

```bash
git add tests/ship-detect-tier.test.sh scripts/lib/tier_detection.py
git commit -m "test(tier-detection): scaffold test harness + module stub"
```

---

### Task 1: Helper to gather git input (RED + GREEN)

**Files:**
- Modify: `scripts/lib/tier_detection.py`
- Modify: `tests/ship-detect-tier.test.sh`

**Goal:** Factor out the "read git status + numstat" step as a testable primitive. Classifier itself still stubbed.

- [ ] **Step 1: Write the failing test**

Insert before the `echo "all tests passed"` line:

```bash
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
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
bash tests/ship-detect-tier.test.sh
```

Expected: `FAIL: clean tree should have no files` or `ImportError: cannot import name 'gather_tree_state'`.

- [ ] **Step 3: Implement `gather_tree_state()`**

Add to `scripts/lib/tier_detection.py` (after the imports, before `classify_tier`):

```python
def _run_git(*args: str, cwd: Path) -> str:
    return subprocess.check_output(
        ["git", *args], cwd=str(cwd), text=True, errors="replace"
    )


def gather_tree_state(repo_root: str | Path) -> dict:
    """Return the current working-tree state for classification.

    Uses `git status --porcelain -z` to correctly handle paths with spaces,
    renames, and unusual characters (Codex omission fix). Merges staged and
    unstaged numstat so classification matches exactly what the review would
    see (Codex staged-vs-unstaged consistency fix).

    Returns:
      - files: list[str] -- paths with any working-tree change
      - statuses: dict[str, str] -- path -> "A"/"M"/"D"/"R"/"?"
      - total_loc: int -- insertions + deletions across tracked diffs, plus
        untracked-file line counts (binary files count as 0)
    """
    root = Path(repo_root)
    # -z prints NUL-separated records; each record is "XY path" where XY are
    # two status chars. Rename records are "XY old\x00new".
    porcelain = _run_git("status", "--porcelain", "-z", cwd=root)

    files: list[str] = []
    statuses: dict[str, str] = {}

    # Iterate NUL-separated records. Renames consume two records (old + new);
    # we want the new path.
    records = porcelain.split("\x00")
    i = 0
    while i < len(records):
        rec = records[i]
        if not rec:
            i += 1
            continue
        if len(rec) < 3:
            i += 1
            continue
        idx, wt = rec[0], rec[1]
        path = rec[3:]  # skip "XY "
        # Rename: next record is the new path; current `path` is the old.
        if idx == "R" or wt == "R":
            i += 1
            if i < len(records):
                path = records[i]
            status = "R"
        elif idx == "?" and wt == "?":
            status = "A"  # untracked == added for classification
        elif "A" in (idx, wt):
            status = "A"
        elif "D" in (idx, wt):
            status = "D"
        elif "M" in (idx, wt):
            status = "M"
        else:
            status = "?"
        files.append(path)
        statuses[path] = status
        i += 1

    # LOC: combine unstaged + staged numstat. Untracked files not in either;
    # count them separately by reading line count.
    total_loc = 0
    tracked_loc_seen: set[str] = set()
    for diff_args in (("diff", "--numstat"), ("diff", "--cached", "--numstat")):
        try:
            numstat = _run_git(*diff_args, cwd=root)
        except subprocess.CalledProcessError:
            continue
        for line in numstat.splitlines():
            if not line.strip():
                continue
            parts = line.split("\t", 2)
            if len(parts) != 3:
                continue
            ins, dels, path = parts
            if ins == "-" and dels == "-":
                tracked_loc_seen.add(path)  # binary, 0 LOC
                continue
            try:
                total_loc += int(ins) + int(dels)
                tracked_loc_seen.add(path)
            except ValueError:
                continue

    for path, status in statuses.items():
        if status == "A" and path not in tracked_loc_seen:
            full_path = root / path
            if full_path.is_file():
                try:
                    with full_path.open("rb") as f:
                        total_loc += sum(1 for _ in f)
                except OSError:
                    pass

    return {"files": files, "statuses": statuses, "total_loc": total_loc}
```

- [ ] **Step 4: Run tests to verify they pass**

- [ ] **Step 5: Commit**

```bash
git add scripts/lib/tier_detection.py tests/ship-detect-tier.test.sh
git commit -m "feat(tier-detection): gather_tree_state reads porcelain -z + numstat"
```

---

### Task 2: Tier 0 rule (vault/devlog/plans only)

**Files:**
- Modify: `scripts/lib/tier_detection.py`
- Modify: `tests/ship-detect-tier.test.sh`

**Goal:** Rule 1 -- all files under `K2B-Vault/`, `DEVLOG.md`, `plans/`, `.claude/plans/` -> Tier 0.

- [ ] **Step 1: Write the failing test**

Append before the invocations block:

```bash
test_tier_0_vault_only() {
  local repo
  repo="$(mktmp)"
  build_fixture_repo "$repo"
  mkdir -p "$repo/K2B-Vault/raw/tldrs"
  (cd "$repo" && printf 'tldr\n' > K2B-Vault/raw/tldrs/today.md)
  (cd "$repo" && printf 'devlog\n' > DEVLOG.md)

  local out
  out=$(call_classifier "$repo")
  echo "$out" | grep -q "tier:0" || fail "vault+devlog should be tier 0; got: $out"
  echo "PASS: test_tier_0_vault_only"
}

test_tier_0_plans_dot_claude() {
  # Covers Codex omission: .claude/plans/ consistency with plans/.
  local repo
  repo="$(mktmp)"
  build_fixture_repo "$repo"
  mkdir -p "$repo/.claude/plans"
  (cd "$repo" && printf 'plan\n' > .claude/plans/2026-04-19_thing.md)

  local out
  out=$(call_classifier "$repo")
  echo "$out" | grep -q "tier:0" || fail ".claude/plans should be tier 0; got: $out"
  echo "PASS: test_tier_0_plans_dot_claude"
}

test_tier_0_plans_toplevel() {
  local repo
  repo="$(mktmp)"
  build_fixture_repo "$repo"
  mkdir -p "$repo/plans"
  (cd "$repo" && printf 'plan\n' > plans/2026-04-19_other.md)

  local out
  out=$(call_classifier "$repo")
  echo "$out" | grep -q "tier:0" || fail "plans/ should be tier 0; got: $out"
  echo "PASS: test_tier_0_plans_toplevel"
}

test_tier_0_vault_only
test_tier_0_plans_dot_claude
test_tier_0_plans_toplevel
```

- [ ] **Step 2: Run tests to verify they fail**

- [ ] **Step 3: Implement Tier 0 rule**

Replace the `classify_tier` stub in `scripts/lib/tier_detection.py`:

```python
TIER_0_PREFIXES = (
    "K2B-Vault/",   # fork/mono-repo portability (dead code in primary K2B)
    "plans/",
    ".claude/plans/",
)
TIER_0_EXACT = ("DEVLOG.md",)


def _is_tier_0_path(path: str) -> bool:
    if path in TIER_0_EXACT:
        return True
    return any(path.startswith(p) for p in TIER_0_PREFIXES)


def classify_tier(
    repo_root: str | Path,
    tier3_config_path: str | Path | None = None,
) -> tuple[int, str]:
    state = gather_tree_state(repo_root)
    files = state["files"]

    if not files:
        return 2, "no changes (classifier should not run here -- /ship step 1 handles this)"

    # Rule 1: Tier 0 -- all files vault/devlog/plans only
    if all(_is_tier_0_path(f) for f in files):
        return 0, f"tier-0: {len(files)} file(s), all vault/devlog/plans"

    # All other rules not yet implemented -- fall through to Tier 2 default
    return 2, "tier-2: default (rules 2-5 pending)"
```

- [ ] **Step 4: Run tests to verify they pass**

- [ ] **Step 5: Commit**

```bash
git add scripts/lib/tier_detection.py tests/ship-detect-tier.test.sh
git commit -m "feat(tier-detection): tier 0 rule (vault/devlog/plans only)"
```

---

### Task 3: Tier 3 allowlist rule (with missing-config = error)

**Files:**
- Modify: `scripts/lib/tier_detection.py`
- Modify: `tests/ship-detect-tier.test.sh`

**Goal:** Rule 2 -- any file matches glob in `tier3-paths.yml` -> Tier 3. Codex HIGH #5: missing default config = classifier error (not silent empty). Codex LOW #1: `**` glob semantics documented + tested.

- [ ] **Step 1: Write the failing tests**

Append:

```bash
test_tier_3_allowlist_hit_literal() {
  local repo
  repo="$(mktmp)"
  build_fixture_repo "$repo"
  mkdir -p "$repo/scripts/lib"
  (cd "$repo" && printf 'def f(): pass\n' > scripts/lib/minimax_review.py)

  local config="$(mktmp)/tier3-paths.yml"
  cat > "$config" <<'YAML'
paths:
  - "scripts/lib/minimax_review.py"
YAML

  local out
  out=$(call_classifier "$repo" "$config")
  echo "$out" | grep -q "tier:3" || fail "allowlist literal hit should be tier 3; got: $out"
  echo "PASS: test_tier_3_allowlist_hit_literal"
}

test_tier_3_allowlist_hit_glob_recursive() {
  # Codex LOW #1: document + test ** semantics. ** means "any path under the
  # prefix before **", with any number of intervening directories.
  local repo
  repo="$(mktmp)"
  build_fixture_repo "$repo"
  mkdir -p "$repo/k2b-remote/src/nested/deep"
  (cd "$repo" && printf 'const x = 1\n' > k2b-remote/src/nested/deep/file.ts)

  local config="$(mktmp)/tier3-paths.yml"
  cat > "$config" <<'YAML'
paths:
  - "k2b-remote/src/**"
YAML

  local out
  out=$(call_classifier "$repo" "$config")
  echo "$out" | grep -q "tier:3" || fail "k2b-remote/src/** should match nested path; got: $out"
  echo "PASS: test_tier_3_allowlist_hit_glob_recursive"
}

test_tier_3_allowlist_glob_does_not_overmatch() {
  # k2b-remote/src/** must NOT match k2b-remote/README.md.
  local repo
  repo="$(mktmp)"
  build_fixture_repo "$repo"
  mkdir -p "$repo/k2b-remote"
  (cd "$repo" && printf 'readme\n' > k2b-remote/README.md)

  local config="$(mktmp)/tier3-paths.yml"
  cat > "$config" <<'YAML'
paths:
  - "k2b-remote/src/**"
YAML

  local out
  out=$(call_classifier "$repo" "$config")
  # Should NOT be tier 3 -- path is sibling of src/, not under it
  if echo "$out" | grep -q "tier:3"; then
    fail "k2b-remote/src/** should NOT match k2b-remote/README.md; got: $out"
  fi
  echo "PASS: test_tier_3_allowlist_glob_does_not_overmatch"
}

test_error_missing_config_at_explicit_path() {
  # Codex HIGH #5: explicit config path that's missing = classifier error.
  local repo
  repo="$(mktmp)"
  build_fixture_repo "$repo"
  (cd "$repo" && printf 'x=1\n' > code.py)

  if call_classifier "$repo" "/definitely/does/not/exist.yml" 2>/dev/null; then
    fail "missing explicit config should raise error"
  fi
  echo "PASS: test_error_missing_config_at_explicit_path"
}

test_no_config_argument_means_no_allowlist() {
  # When no config is passed at all (Python None), treat as empty allowlist.
  # This is distinct from "default path that's missing" -- that's an error.
  local repo
  repo="$(mktmp)"
  build_fixture_repo "$repo"
  (cd "$repo" && printf 'x=1\n' > code.py)

  # Call without config argument
  local out
  out=$(PYTHONPATH="$LIB_DIR" python3 -c "
import sys
sys.path.insert(0, r'$LIB_DIR')
from tier_detection import classify_tier
tier, reason = classify_tier(repo_root=r'$repo', tier3_config_path=None)
print(f'tier:{tier} reason:{reason}')
")
  echo "$out" | grep -q "tier:2" || fail "no config arg should default to tier 2 (empty allowlist); got: $out"
  echo "PASS: test_no_config_argument_means_no_allowlist"
}

test_tier_3_allowlist_hit_literal
test_tier_3_allowlist_hit_glob_recursive
test_tier_3_allowlist_glob_does_not_overmatch
test_error_missing_config_at_explicit_path
test_no_config_argument_means_no_allowlist
```

- [ ] **Step 2: Run tests to verify they fail**

- [ ] **Step 3: Implement the allowlist rule + missing-config error**

Add to `scripts/lib/tier_detection.py`:

```python
import yaml


def _load_tier3_globs(config_path: str | Path | None) -> list[str]:
    """Load the Tier 3 glob allowlist from YAML.

    If config_path is None -> return []. This is the "no allowlist requested"
    case (typically tests or forks without the file).

    If config_path is a path that doesn't exist -> raise FileNotFoundError.
    The caller (CLI wrapper) is responsible for deciding whether this is a
    hard error (default-path missing in K2B) or soft (explicit --no-config
    flag, which Ship 1 does not ship).

    If config_path exists but is malformed -> raise ValueError.
    """
    if config_path is None:
        return []
    p = Path(config_path)
    if not p.exists():
        raise FileNotFoundError(f"tier3 config not found at {p}")
    with p.open("r") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict) or "paths" not in data:
        raise ValueError(f"malformed tier3 config at {p}: expected dict with 'paths' key")
    paths = data["paths"]
    if not isinstance(paths, list):
        raise ValueError(f"malformed tier3 config at {p}: 'paths' must be a list")
    return [str(pat) for pat in paths]


def _matches_any_glob(path: str, patterns: list[str]) -> str | None:
    """Return the first matching pattern, or None.

    ** semantics: "<prefix>/**" matches any path whose parent segments start
    with "<prefix>/". No support for "**" in the middle of a pattern (e.g.
    "a/**/b") in Ship 1. If a future pattern needs mid-** semantics, switch
    to pathlib.PurePath.match or fnmatch.translate.
    """
    for pat in patterns:
        if pat.endswith("/**"):
            prefix = pat[:-2]  # "k2b-remote/src/**" -> "k2b-remote/src/"
            if path.startswith(prefix):
                return pat
        elif "**" in pat:
            # Mid-** not supported in Ship 1; document + skip
            continue
        elif fnmatch.fnmatch(path, pat):
            return pat
    return None
```

Extend `classify_tier` after the Tier 0 rule:

```python
    # Rule 2: Tier 3 -- allowlist hit
    try:
        globs = _load_tier3_globs(tier3_config_path)
    except (FileNotFoundError, ValueError) as exc:
        # Propagate -- caller (CLI) decides fail-safe behavior
        raise
    for f in files:
        hit = _matches_any_glob(f, globs)
        if hit:
            return 3, f"tier-3: allowlist match '{hit}' for path {f}"
```

- [ ] **Step 4: Run tests to verify they pass**

- [ ] **Step 5: Commit**

```bash
git add scripts/lib/tier_detection.py tests/ship-detect-tier.test.sh
git commit -m "feat(tier-detection): tier 3 allowlist rule + missing-config error"
```

---

### Task 4: Tier 1 pure-docs rule (BEFORE Tier 3 scale, per Codex MEDIUM #3)

**Files:**
- Modify: `scripts/lib/tier_detection.py`
- Modify: `tests/ship-detect-tier.test.sh`

**Goal:** Rule 3 -- all files are `.md` AND under `.claude/skills/`, `wiki/`, `CLAUDE.md`, or `README.md` -> Tier 1. **Must fire BEFORE the scale rule (Task 5)** so large pure-docs commits don't hit Tier 3.

- [ ] **Step 1: Write the failing tests**

Append:

```bash
test_tier_1_skill_docs_only() {
  local repo
  repo="$(mktmp)"
  build_fixture_repo "$repo"
  mkdir -p "$repo/.claude/skills/k2b-test"
  (cd "$repo" && printf '# test\n' > .claude/skills/k2b-test/SKILL.md)

  local config="$(mktmp)/tier3-paths.yml"
  cat > "$config" <<'YAML'
paths: []
YAML

  local out
  out=$(call_classifier "$repo" "$config")
  echo "$out" | grep -q "tier:1" || fail "skill docs should be tier 1; got: $out"
  echo "PASS: test_tier_1_skill_docs_only"
}

test_tier_1_claude_md() {
  local repo
  repo="$(mktmp)"
  build_fixture_repo "$repo"
  (cd "$repo" && printf '# updated\n' > CLAUDE.md)

  local config="$(mktmp)/tier3-paths.yml"
  cat > "$config" <<'YAML'
paths: []
YAML

  local out
  out=$(call_classifier "$repo" "$config")
  echo "$out" | grep -q "tier:1" || fail "CLAUDE.md should be tier 1; got: $out"
  echo "PASS: test_tier_1_claude_md"
}

test_tier_1_wiki_docs() {
  local repo
  repo="$(mktmp)"
  build_fixture_repo "$repo"
  mkdir -p "$repo/wiki/concepts"
  (cd "$repo" && printf '# concept\n' > wiki/concepts/thing.md)

  local config="$(mktmp)/tier3-paths.yml"
  cat > "$config" <<'YAML'
paths: []
YAML

  local out
  out=$(call_classifier "$repo" "$config")
  echo "$out" | grep -q "tier:1" || fail "wiki docs should be tier 1; got: $out"
  echo "PASS: test_tier_1_wiki_docs"
}

test_tier_1_big_docs_still_tier_1_not_scale_tier_3() {
  # Codex MEDIUM #3 regression: a 250-line pure-docs commit must NOT fall
  # through to Tier 3 scale. Docs rule fires before scale rule.
  local repo
  repo="$(mktmp)"
  build_fixture_repo "$repo"
  mkdir -p "$repo/.claude/skills/k2b-big"
  (cd "$repo" && python3 -c "print('\n'.join(['line ' + str(i) for i in range(250)]))" > .claude/skills/k2b-big/SKILL.md)

  local config="$(mktmp)/tier3-paths.yml"
  cat > "$config" <<'YAML'
paths: []
YAML

  local out
  out=$(call_classifier "$repo" "$config")
  echo "$out" | grep -q "tier:1" || fail "big docs-only commit should still be tier 1 (docs before scale); got: $out"
  echo "PASS: test_tier_1_big_docs_still_tier_1_not_scale_tier_3"
}

test_tier_1_mixed_docs_and_code_is_NOT_tier_1() {
  local repo
  repo="$(mktmp)"
  build_fixture_repo "$repo"
  (cd "$repo" && printf 'docs\n' > doc.md)
  (cd "$repo" && printf 'x=1\n' > code.py)

  local config="$(mktmp)/tier3-paths.yml"
  cat > "$config" <<'YAML'
paths: []
YAML

  local out
  out=$(call_classifier "$repo" "$config")
  # Code in the mix means docs rule fails; falls to default tier 2
  echo "$out" | grep -q "tier:2" || fail "mixed docs+code should be tier 2; got: $out"
  echo "PASS: test_tier_1_mixed_docs_and_code_is_NOT_tier_1"
}

test_tier_1_skill_docs_only
test_tier_1_claude_md
test_tier_1_wiki_docs
test_tier_1_big_docs_still_tier_1_not_scale_tier_3
test_tier_1_mixed_docs_and_code_is_NOT_tier_1
```

- [ ] **Step 2: Run tests to verify they fail**

- [ ] **Step 3: Implement the rule**

Add to `scripts/lib/tier_detection.py`:

```python
TIER_1_DOC_PREFIXES = (".claude/skills/", "wiki/")
TIER_1_DOC_EXACT = ("CLAUDE.md", "README.md")


def _is_tier_1_doc(path: str) -> bool:
    if not path.endswith(".md"):
        return False
    if path in TIER_1_DOC_EXACT:
        return True
    return any(path.startswith(p) for p in TIER_1_DOC_PREFIXES)
```

Extend `classify_tier` after the Tier 3 allowlist rule:

```python
    # Rule 3: Tier 1 -- all docs under skills/wiki/CLAUDE.md/README.md
    # IMPORTANT: this rule MUST fire BEFORE the scale rule (Rule 4) so that
    # large pure-docs commits don't get Tier-3-scaled. See Codex MEDIUM #3.
    if all(_is_tier_1_doc(f) for f in files):
        return 1, (
            f"tier-1: {len(files)} file(s), all .md docs under "
            "skills/wiki/CLAUDE/README"
        )
```

- [ ] **Step 4: Run tests to verify they pass**

- [ ] **Step 5: Commit**

```bash
git add scripts/lib/tier_detection.py tests/ship-detect-tier.test.sh
git commit -m "feat(tier-detection): tier 1 pure-docs rule (before scale)"
```

---

### Task 5: Tier 3 scale rule (>3 files OR >200 LOC, per Codex MEDIUM #1)

**Files:**
- Modify: `scripts/lib/tier_detection.py`
- Modify: `tests/ship-detect-tier.test.sh`

**Goal:** Rule 4 -- `>3 files` OR `>200 LOC` (insertions + deletions).

- [ ] **Step 1: Write the failing tests**

Append:

```bash
test_tier_3_scale_file_count() {
  local repo
  repo="$(mktmp)"
  build_fixture_repo "$repo"
  (cd "$repo" && for i in 1 2 3 4; do printf 'tiny\n' > "file_$i.py"; done)

  local config="$(mktmp)/tier3-paths.yml"
  cat > "$config" <<'YAML'
paths: []
YAML

  local out
  out=$(call_classifier "$repo" "$config")
  echo "$out" | grep -q "tier:3" || fail "4 files should be tier 3; got: $out"
  echo "PASS: test_tier_3_scale_file_count"
}

test_tier_3_scale_loc_over_200() {
  local repo
  repo="$(mktmp)"
  build_fixture_repo "$repo"
  # Single file, 250 lines (>200)
  (cd "$repo" && python3 -c "print('\n'.join(['x = 1'] * 250))" > big.py)

  local config="$(mktmp)/tier3-paths.yml"
  cat > "$config" <<'YAML'
paths: []
YAML

  local out
  out=$(call_classifier "$repo" "$config")
  echo "$out" | grep -q "tier:3" || fail "250 LOC should be tier 3; got: $out"
  echo "PASS: test_tier_3_scale_loc_over_200"
}

test_tier_2_scale_just_under_200() {
  # 155 LOC (7cd1f6c-shape) must NOT trip scale rule; falls to tier 2 default.
  local repo
  repo="$(mktmp)"
  build_fixture_repo "$repo"
  (cd "$repo" && python3 -c "print('\n'.join(['x = 1'] * 155))" > medium.py)

  local config="$(mktmp)/tier3-paths.yml"
  cat > "$config" <<'YAML'
paths: []
YAML

  local out
  out=$(call_classifier "$repo" "$config")
  echo "$out" | grep -q "tier:2" || fail "155 LOC should be tier 2 (under 200); got: $out"
  echo "PASS: test_tier_2_scale_just_under_200"
}

test_tier_2_scale_three_small_files() {
  local repo
  repo="$(mktmp)"
  build_fixture_repo "$repo"
  (cd "$repo" && printf 'x=1\n' > a.py && printf 'x=2\n' > b.py && printf 'x=3\n' > c.py)

  local config="$(mktmp)/tier3-paths.yml"
  cat > "$config" <<'YAML'
paths: []
YAML

  local out
  out=$(call_classifier "$repo" "$config")
  echo "$out" | grep -q "tier:2" || fail "3 small files should be tier 2; got: $out"
  echo "PASS: test_tier_2_scale_three_small_files"
}

test_tier_3_scale_file_count
test_tier_3_scale_loc_over_200
test_tier_2_scale_just_under_200
test_tier_2_scale_three_small_files
```

- [ ] **Step 2: Run tests to verify they fail**

- [ ] **Step 3: Implement the rule**

Extend `classify_tier` after the Tier 1 rule:

```python
    # Rule 4: Tier 3 -- scale (>3 files or >200 LOC)
    # Threshold chosen to keep 7cd1f6c-shape (155 LOC, 2 files) in Tier 2
    # per Keith's "Tier 2 HEALTHY" classification. See Codex MEDIUM #1.
    if len(files) > 3:
        return 3, f"tier-3: {len(files)} files changed (>3)"
    if state["total_loc"] > 200:
        return 3, f"tier-3: {state['total_loc']} LOC changed (>200)"
```

- [ ] **Step 4: Run tests to verify they pass**

- [ ] **Step 5: Commit**

```bash
git add scripts/lib/tier_detection.py tests/ship-detect-tier.test.sh
git commit -m "feat(tier-detection): tier 3 scale rule (>3 files or >200 LOC)"
```

---

### Task 6: Tier 2 default + evidence-case regressions (calibration + production, per Codex MEDIUM #2)

**Files:**
- Modify: `scripts/lib/tier_detection.py`
- Modify: `tests/ship-detect-tier.test.sh`

**Goal:** Pin the four evidence cases. Per Codex MEDIUM #2, the `7cd1f6c` case is split into two tests: calibration fixture (neutral paths, isolates scale rule) + production shape (real `scripts/promote-learnings.py` path with allowlist, asserts allowlist wins).

- [ ] **Step 1: Write the evidence tests**

Append:

```bash
test_evidence_k2b_73984d3_skill_md_81_lines() {
  # K2B 73984d3: 81 lines of .md inside .claude/skills/, no other files.
  # Expected tier: 1 (pure docs under skills/, scale-under threshold).
  local repo
  repo="$(mktmp)"
  build_fixture_repo "$repo"
  mkdir -p "$repo/.claude/skills/k2b-research"
  (cd "$repo" && python3 -c "print('\n'.join(['line ' + str(i) for i in range(81)]))" > .claude/skills/k2b-research/SKILL.md)

  local config="$(mktmp)/tier3-paths.yml"
  cat > "$config" <<'YAML'
paths: []
YAML

  local out
  out=$(call_classifier "$repo" "$config")
  echo "$out" | grep -q "tier:1" || fail "evidence 73984d3 should be tier 1; got: $out"
  echo "PASS: test_evidence_k2b_73984d3_skill_md_81_lines"
}

test_evidence_k2b_7cd1f6c_calibration_neutral_path() {
  # Calibration fixture: 155 LOC across neutral paths (no allowlist hit).
  # Expected tier: 2 (scale rule does not fire at 200 threshold).
  local repo
  repo="$(mktmp)"
  build_fixture_repo "$repo"
  (cd "$repo" && python3 -c "print('\n'.join(['x=1'] * 80))" > neutral_code.py)
  mkdir -p "$repo/tests"
  (cd "$repo" && python3 -c "print('\n'.join(['# test'] * 75))" > tests/neutral.test.sh)

  local config="$(mktmp)/tier3-paths.yml"
  cat > "$config" <<'YAML'
paths: []
YAML

  local out
  out=$(call_classifier "$repo" "$config")
  echo "$out" | grep -q "tier:2" || fail "calibration 155 LOC/2 files should be tier 2; got: $out"
  echo "PASS: test_evidence_k2b_7cd1f6c_calibration_neutral_path"
}

test_evidence_k2b_7cd1f6c_production_shape_allowlist_wins() {
  # Production shape: real 7cd1f6c touched scripts/promote-learnings.py which
  # IS in the Tier 3 allowlist (memory persistence). Allowlist wins over scale.
  # This is the regression that catches if future allowlist edits unshield
  # memory-persistence files.
  local repo
  repo="$(mktmp)"
  build_fixture_repo "$repo"
  mkdir -p "$repo/scripts"
  (cd "$repo" && python3 -c "print('\n'.join(['x=1'] * 80))" > scripts/promote-learnings.py)

  local config="$(mktmp)/tier3-paths.yml"
  cat > "$config" <<'YAML'
paths:
  - "scripts/promote-learnings.py"
YAML

  local out
  out=$(call_classifier "$repo" "$config")
  echo "$out" | grep -q "tier:3" || fail "production promote-learnings.py should be tier 3 via allowlist; got: $out"
  echo "PASS: test_evidence_k2b_7cd1f6c_production_shape_allowlist_wins"
}

test_evidence_k2bi_befc26b_multi_file_runtime() {
  # K2Bi befc26b: multiple new files under src/ with >200 LOC total AND >3 files
  # would trigger. Use enough files to trip both triggers individually.
  local repo
  repo="$(mktmp)"
  build_fixture_repo "$repo"
  mkdir -p "$repo/src/approval"
  (cd "$repo" && python3 -c "print('\n'.join(['x=1'] * 80))" > src/approval/gate.py)
  (cd "$repo" && python3 -c "print('\n'.join(['y=2'] * 80))" > src/approval/queue.py)
  (cd "$repo" && python3 -c "print('\n'.join(['z=3'] * 80))" > src/approval/dispatcher.py)
  (cd "$repo" && python3 -c "print('\n'.join(['w=4'] * 80))" > src/approval/runner.py)

  local config="$(mktmp)/tier3-paths.yml"
  cat > "$config" <<'YAML'
paths: []
YAML

  local out
  out=$(call_classifier "$repo" "$config")
  echo "$out" | grep -q "tier:3" || fail "befc26b should be tier 3 (4 files OR 320 LOC); got: $out"
  echo "PASS: test_evidence_k2bi_befc26b_multi_file_runtime"
}

test_evidence_k2bi_530eb81_trading_path_allowlist() {
  # K2Bi 530eb81: trading-order submit path. Small change, should be tier 3
  # via allowlist. K2Bi fork of tier3-paths.yml would include src/orders/**.
  local repo
  repo="$(mktmp)"
  build_fixture_repo "$repo"
  mkdir -p "$repo/src/orders"
  (cd "$repo" && printf 'def submit(): pass\n' > src/orders/submit.py)

  local config="$(mktmp)/tier3-paths.yml"
  cat > "$config" <<'YAML'
paths:
  - "src/orders/**"
YAML

  local out
  out=$(call_classifier "$repo" "$config")
  echo "$out" | grep -q "tier:3" || fail "530eb81 should be tier 3 via allowlist; got: $out"
  echo "PASS: test_evidence_k2bi_530eb81_trading_path_allowlist"
}

test_tier_2_default_small_code() {
  local repo
  repo="$(mktmp)"
  build_fixture_repo "$repo"
  (cd "$repo" && printf 'x=1\n' > small.py)

  local config="$(mktmp)/tier3-paths.yml"
  cat > "$config" <<'YAML'
paths: []
YAML

  local out
  out=$(call_classifier "$repo" "$config")
  echo "$out" | grep -q "tier:2" || fail "small code change should be tier 2; got: $out"
  echo "PASS: test_tier_2_default_small_code"
}

test_evidence_k2b_73984d3_skill_md_81_lines
test_evidence_k2b_7cd1f6c_calibration_neutral_path
test_evidence_k2b_7cd1f6c_production_shape_allowlist_wins
test_evidence_k2bi_befc26b_multi_file_runtime
test_evidence_k2bi_530eb81_trading_path_allowlist
test_tier_2_default_small_code
```

- [ ] **Step 2: Run tests. The Tier 2 default stub already returns `tier:2` so most should pass.**

- [ ] **Step 3: Finalize Rule 5 (Tier 2 default) with a clean reason string**

Replace the stub fall-through at the end of `classify_tier`:

```python
    # Rule 5: Tier 2 -- default (real code or tests within budget)
    return 2, (
        f"tier-2: default ({len(files)} file(s), {state['total_loc']} LOC, "
        "no allowlist hit, not all docs)"
    )
```

- [ ] **Step 4: Run tests to verify all pass**

- [ ] **Step 5: Commit**

```bash
git add scripts/lib/tier_detection.py tests/ship-detect-tier.test.sh
git commit -m "feat(tier-detection): tier 2 default + evidence-case regressions"
```

---

### Task 7: Classifier error-handling consolidation

**Files:**
- Modify: `tests/ship-detect-tier.test.sh`

**Goal:** Pin the error-handling contract: malformed YAML -> error; missing `paths` key -> error; None config_path -> no error, empty allowlist.

- [ ] **Step 1: Write the tests**

Append:

```bash
test_error_malformed_yaml() {
  local repo
  repo="$(mktmp)"
  build_fixture_repo "$repo"
  (cd "$repo" && printf 'x=1\n' > code.py)

  local config="$(mktmp)/tier3-paths.yml"
  printf 'not: yaml: {broken\n' > "$config"

  if call_classifier "$repo" "$config" 2>/dev/null; then
    fail "malformed YAML should raise an error"
  fi
  echo "PASS: test_error_malformed_yaml"
}

test_error_yaml_missing_paths_key() {
  local repo
  repo="$(mktmp)"
  build_fixture_repo "$repo"
  (cd "$repo" && printf 'x=1\n' > code.py)

  local config="$(mktmp)/tier3-paths.yml"
  cat > "$config" <<'YAML'
notpaths:
  - "nope"
YAML

  if call_classifier "$repo" "$config" 2>/dev/null; then
    fail "missing 'paths' key should raise an error"
  fi
  echo "PASS: test_error_yaml_missing_paths_key"
}

test_error_paths_not_a_list() {
  local repo
  repo="$(mktmp)"
  build_fixture_repo "$repo"
  (cd "$repo" && printf 'x=1\n' > code.py)

  local config="$(mktmp)/tier3-paths.yml"
  cat > "$config" <<'YAML'
paths:
  foo: bar
YAML

  if call_classifier "$repo" "$config" 2>/dev/null; then
    fail "'paths' as dict instead of list should raise an error"
  fi
  echo "PASS: test_error_paths_not_a_list"
}

test_error_malformed_yaml
test_error_yaml_missing_paths_key
test_error_paths_not_a_list
```

- [ ] **Step 2: Run tests. `_load_tier3_globs` already raises ValueError / FileNotFoundError / yaml.YAMLError from Task 3. Verify they propagate.**

- [ ] **Step 3: If any test fails due to missing error propagation, tighten `_load_tier3_globs`.**

- [ ] **Step 4: Commit**

```bash
git add tests/ship-detect-tier.test.sh
git commit -m "test(tier-detection): error-handling contract (malformed/missing-key)"
```

---

### Task 8: CLI wrapper `scripts/ship-detect-tier.py`

**Files:**
- Create: `scripts/ship-detect-tier.py`
- Modify: `tests/ship-detect-tier.test.sh`

**Goal:** Thin CLI. Default config path = `<repo>/scripts/tier3-paths.yml`. Missing default = exit 1 (caller / `/ship` falls back to Tier 3). Malformed = exit 1. Success = print `tier: N\nreason: <text>` + exit 0.

- [ ] **Step 1: Write the failing tests**

Append:

```bash
test_cli_wrapper_success_with_default_config() {
  local repo
  repo="$(mktmp)"
  build_fixture_repo "$repo"
  (cd "$repo" && printf 'x=1\n' > code.py)
  mkdir -p "$repo/scripts/lib"
  cp "$SCRIPT" "$repo/scripts/ship-detect-tier.py"
  cp "$LIB_DIR/tier_detection.py" "$repo/scripts/lib/tier_detection.py"
  cat > "$repo/scripts/tier3-paths.yml" <<'YAML'
paths: []
YAML
  chmod +x "$repo/scripts/ship-detect-tier.py"

  local out
  out=$(cd "$repo" && ./scripts/ship-detect-tier.py)
  echo "$out" | grep -q "^tier: 2$" || fail "CLI should print 'tier: 2'; got: $out"
  echo "$out" | grep -q "^reason:" || fail "CLI should print 'reason:' line; got: $out"
  echo "PASS: test_cli_wrapper_success_with_default_config"
}

test_cli_wrapper_missing_default_config_exits_1() {
  # Codex HIGH #5: missing default config is an error, not silent empty.
  local repo
  repo="$(mktmp)"
  build_fixture_repo "$repo"
  (cd "$repo" && printf 'x=1\n' > code.py)
  mkdir -p "$repo/scripts/lib"
  cp "$SCRIPT" "$repo/scripts/ship-detect-tier.py"
  cp "$LIB_DIR/tier_detection.py" "$repo/scripts/lib/tier_detection.py"
  # Intentionally NO tier3-paths.yml
  chmod +x "$repo/scripts/ship-detect-tier.py"

  if (cd "$repo" && ./scripts/ship-detect-tier.py) 2>/dev/null; then
    fail "CLI without default config should exit 1"
  fi
  echo "PASS: test_cli_wrapper_missing_default_config_exits_1"
}

test_cli_wrapper_outside_git_repo_fails() {
  local notrepo
  notrepo="$(mktmp)"
  mkdir -p "$notrepo/scripts/lib"
  cp "$SCRIPT" "$notrepo/scripts/ship-detect-tier.py"
  cp "$LIB_DIR/tier_detection.py" "$notrepo/scripts/lib/tier_detection.py"
  chmod +x "$notrepo/scripts/ship-detect-tier.py"

  if (cd "$notrepo" && ./scripts/ship-detect-tier.py) 2>/dev/null; then
    fail "CLI outside git repo should exit 1"
  fi
  echo "PASS: test_cli_wrapper_outside_git_repo_fails"
}

test_cli_wrapper_explicit_config_flag() {
  local repo
  repo="$(mktmp)"
  build_fixture_repo "$repo"
  (cd "$repo" && printf 'x=1\n' > code.py)
  mkdir -p "$repo/scripts/lib"
  cp "$SCRIPT" "$repo/scripts/ship-detect-tier.py"
  cp "$LIB_DIR/tier_detection.py" "$repo/scripts/lib/tier_detection.py"
  chmod +x "$repo/scripts/ship-detect-tier.py"

  local altconfig="$(mktmp)/alt.yml"
  cat > "$altconfig" <<'YAML'
paths:
  - "code.py"
YAML

  local out
  out=$(cd "$repo" && ./scripts/ship-detect-tier.py --config "$altconfig")
  echo "$out" | grep -q "^tier: 3$" || fail "--config override should land tier 3 via allowlist; got: $out"
  echo "PASS: test_cli_wrapper_explicit_config_flag"
}

test_cli_wrapper_success_with_default_config
test_cli_wrapper_missing_default_config_exits_1
test_cli_wrapper_outside_git_repo_fails
test_cli_wrapper_explicit_config_flag
```

- [ ] **Step 2: Run tests to verify they fail (CLI doesn't exist)**

- [ ] **Step 3: Implement the CLI**

Create `scripts/ship-detect-tier.py`:

```python
#!/usr/bin/env python3
"""Adversarial review tier classifier.

Reads the current working-tree diff and emits one of tier 0, 1, 2, 3 on stdout,
for /ship step 3 routing. See feature_adversarial-review-tiering.

Exit codes:
  0 -- classification succeeded; stdout:
         tier: N
         reason: <text>
  1 -- classifier error (not in a git repo, missing default config,
       malformed tier3-paths.yml, etc.)

The classifier itself returns an error for missing default config. The caller
(/ship step 3a) treats exit 1 as "fall back to Tier 3" per the feature spec's
fail-safe rule.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR / "lib"))

from tier_detection import classify_tier  # noqa: E402


def _repo_root() -> Path:
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "--show-toplevel"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except subprocess.CalledProcessError:
        print("ship-detect-tier: not in a git repository", file=sys.stderr)
        sys.exit(1)
    return Path(out)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        default=None,
        help=(
            "Path to tier3-paths.yml. Default: <repo>/scripts/tier3-paths.yml. "
            "Missing default = exit 1 (caller falls back to Tier 3)."
        ),
    )
    args = parser.parse_args()

    root = _repo_root()
    config = Path(args.config) if args.config else root / "scripts" / "tier3-paths.yml"

    try:
        tier, reason = classify_tier(repo_root=root, tier3_config_path=config)
    except Exception as exc:
        print(f"ship-detect-tier: classifier error: {exc}", file=sys.stderr)
        return 1

    print(f"tier: {tier}")
    print(f"reason: {reason}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

```bash
chmod +x scripts/ship-detect-tier.py
```

- [ ] **Step 4: Run tests to verify they pass**

- [ ] **Step 5: Commit**

```bash
git add scripts/ship-detect-tier.py tests/ship-detect-tier.test.sh
git commit -m "feat(tier-detection): CLI wrapper scripts/ship-detect-tier.py"
```

---

### Task 9: Initial `scripts/tier3-paths.yml` allowlist

**Files:**
- Create: `scripts/tier3-paths.yml`

**Goal:** Ship the initial K2B allowlist.

- [ ] **Step 1: Write the config**

Create `scripts/tier3-paths.yml`:

```yaml
# scripts/tier3-paths.yml
# Adversarial review Tier 3 allowlist.
#
# Files matching any of these glob patterns are classified as Tier 3
# (iterate-until-clean Codex review) regardless of LOC or file count.
#
# Rationale: each path is a blast-radius path where a latent bug lands
# in production state that cannot be rolled back by reverting the commit.
# New paths added here effectively declare "this file deserves Tier 3
# scrutiny" -- prefer adding over removing.
#
# Glob semantics (Ship 1):
#   - Literal path match: "scripts/foo.py"
#   - Recursive prefix match: "scripts/hooks/**" matches any path starting
#     with "scripts/hooks/". Mid-** (e.g. "a/**/b") is NOT supported in Ship 1.
#
# Missing this file is a classifier error, not silent empty allowlist.

paths:
  # Memory persistence + active rules (bug here corrupts learnings history)
  - "scripts/promote-learnings.py"
  - "scripts/select-lru-victim.py"
  - "scripts/increment-access-count.py"
  - "scripts/demote-rule.sh"
  - "scripts/lib/importance.py"

  # Adversarial review infrastructure (bug here blinds every future review)
  - "scripts/lib/minimax_review.py"
  - "scripts/lib/adversarial-review.md"
  - "scripts/minimax-review.sh"

  # Single-writer mutation helpers (bug here corrupts shared logs/indexes)
  - "scripts/wiki-log-append.sh"
  - "scripts/compile-index-update.py"

  # This classifier itself (bug here mis-tiers every future review)
  - "scripts/lib/tier_detection.py"
  - "scripts/ship-detect-tier.py"
  - "scripts/tier3-paths.yml"

  # Deployment + git hooks (bug here wedges every machine)
  - "scripts/deploy-to-mini.sh"
  - ".githooks/**"
  - "scripts/hooks/**"

  # Production services (bug here pages Keith at 3am)
  - "k2b-remote/src/**"
  - "k2b-dashboard/src/**"
```

- [ ] **Step 2: Verify the classifier loads it without error**

```bash
scripts/ship-detect-tier.py
```

Expected: prints `tier: N\nreason: <text>` based on current working tree. Exit 0.

- [ ] **Step 3: Commit**

```bash
git add scripts/tier3-paths.yml
git commit -m "feat(tier-detection): initial tier 3 allowlist for K2B"
```

---

### Task 10: k2b-ship SKILL.md Step 3 surgery -- Steps 3a (detect) + 3b (branch)

**Files:**
- Modify: `.claude/skills/k2b-ship/SKILL.md`

**Goal:** Insert Step 3a (classifier invocation) + Step 3b (tier branching) at the top of Step 3. Preserve today's entire "Try Codex first" flow as Step 3c "Tier 3 (unchanged from pre-tiering)." Add Step 3d (record tier used).

Tier 1's review loop (the non-trivial part) is in Task 11.

- [ ] **Step 1: Identify exact line range of current Step 3**

```bash
grep -n "^### 3\." .claude/skills/k2b-ship/SKILL.md | head -3
grep -n "^### 4\." .claude/skills/k2b-ship/SKILL.md | head -3
```

Note the boundaries.

- [ ] **Step 2: Draft the new Step 3 structure**

New structure (apply via Edit tool, replacing the current Step 3 body):

- The opening paragraph of Step 3 (mandatory-unless-skipped language) stays.
- Insert new **Step 3a** after the opener: tier detection, classifier invocation, fail-safe to Tier 3 on classifier error.
- Insert new **Step 3b**: branching table + bash skeleton.
- Existing "Two reviewers can fill this gate" paragraph through the "both reviewers unavailable" refusal, verbatim, becomes **Step 3c** titled "Tier 3 flow (unchanged from pre-tiering)."
- Insert new **Step 3d**: record the tier used in the ship record / DEVLOG entry.

Step 3a body:

```markdown
#### 3a. Tier detection

Run the classifier first. Tier detection determines which reviewer runs and at what intensity. Classifier exit 1 (including missing `scripts/tier3-paths.yml`, malformed config, or not-in-a-git-repo) is **fail-safe to Tier 3** -- do not silently soften the gate.

```bash
set +e
TIER_OUTPUT="$(scripts/ship-detect-tier.py 2>&1)"
TIER_EXIT=$?
set -e

if [ "$TIER_EXIT" -ne 0 ]; then
  echo "[warn] ship-detect-tier exited $TIER_EXIT -- falling back to Tier 3 (full Codex flow)." >&2
  echo "$TIER_OUTPUT" >&2
  TIER=3
  TIER_REASON="classifier-error: $TIER_OUTPUT"
else
  TIER=$(echo "$TIER_OUTPUT" | sed -n 's/^tier: //p')
  TIER_REASON=$(echo "$TIER_OUTPUT" | sed -n 's/^reason: //p')
  if [ -z "$TIER" ]; then
    echo "[warn] ship-detect-tier produced no tier line -- falling back to Tier 3." >&2
    TIER=3
    TIER_REASON="classifier-output-malformed"
  fi
fi

echo "tier: $TIER -- $TIER_REASON"
```
```

Step 3b body:

```markdown
#### 3b. Tier routing

Branch on `$TIER`:

| Tier | Routing |
|---|---|
| 0 | Skip review (log only). |
| 1 | MiniMax `--scope diff` single-pass, cap at 2 iterations, escalate to Tier 2 on MiniMax failure. Procedure in Step 3b.1 below. |
| 2 | Codex single-pass via today's background + poll pattern. `--skip-codex` routes to MiniMax `--scope diff` single-pass. Both-fail -> REFUSE (same as Tier 3). |
| 3 | Today's full iterate-until-clean flow, verbatim. See Step 3c. |

**Get the safe changed-file list once** (used by Tier 1 and Tier 2 diff-scoped reviewer invocations). Handles renames + spaces via `-z`:

```bash
CHANGED_FILES=$(git diff --name-only -z HEAD | tr '\0' ',' | sed 's/,$//')
```

Then dispatch:

**Tier 0:**

```bash
if [ "$TIER" = "0" ]; then
  echo "review skipped (tier-0: vault/devlog/plans only, $TIER_REASON)"
  REVIEW_RESULT="skipped-tier-0"
  # proceed to step 4
fi
```

**Tier 1:** see Step 3b.1 below.

**Tier 2:**

```bash
if [ "$TIER" = "2" ]; then
  # Single-pass Codex via background + poll (same invocation as Tier 3 below,
  # but Keith decides fix-or-defer on the FIRST pass -- no iterate-until-clean).
  # If --skip-codex <reason> was passed, route to MiniMax single-pass:
  if [ -n "${SKIP_CODEX:-}" ]; then
    if ! scripts/minimax-review.sh --scope diff --files "$CHANGED_FILES" \
        --focus "tier-2 single-pass review (--skip-codex: $SKIP_CODEX)"; then
      echo "[FATAL] tier-2 MiniMax failed AND --skip-codex blocks Codex fallback." >&2
      echo "Both reviewers unavailable. REFUSE /ship (same invariant as today's step 3)." >&2
      exit 3
    fi
    REVIEW_RESULT="tier-2-minimax-$SKIP_CODEX"
  else
    # Codex single-pass (reuse the background + poll block from Step 3c,
    # but do NOT iterate-until-clean -- treat one pass's findings as final).
    # See Step 3c for the exact Codex invocation. Here we just capture the
    # outcome and exit the tier branch after one pass.
    run_codex_single_pass  # (shorthand -- inline the Step 3c codex block)
    REVIEW_RESULT="tier-2-codex-single-pass"
  fi
fi
```

**Tier 3:** falls through to Step 3c below (the existing flow).
```

Step 3c body: the existing Step 3 body from the current SKILL.md, verbatim, under the new `#### 3c. Tier 3 flow (unchanged from pre-tiering)` heading.

Step 3d body:

```markdown
#### 3d. Record the tier used

For the ship audit trail, DEVLOG entry, and multi-ship feature Shipping Status table, record:

```
tier: <N> (<classifier | classifier-error-fallback | (Ship 2: overridden)>)
reason: <TIER_REASON>
review-result: <skipped-tier-0 | tier-1-approve | tier-1-auto-promoted-to-tier-2 | tier-2-codex-single-pass | tier-2-minimax-<reason> | tier-3-codex-multiround | tier-3-minimax-fallback>
```

For multi-ship features (Shipping Status table), append the tier + reason to the current ship row so future ships in the same feature can see the historical tier pattern.
```

- [ ] **Step 3: Apply the edit**

Use `Edit` (or `Write` if the entire Step 3 is being restructured) to replace the current Step 3 body with the new structure. Preserve every line of the existing Codex-primary + MiniMax-fallback flow under Step 3c.

- [ ] **Step 4: Verify SKILL.md renders cleanly**

```bash
grep -n "^### 3\." .claude/skills/k2b-ship/SKILL.md
grep -n "^#### 3" .claude/skills/k2b-ship/SKILL.md
grep -c "Try Codex first" .claude/skills/k2b-ship/SKILL.md  # Expected: 1 (only in 3c)
```

- [ ] **Step 5: Commit**

```bash
git add .claude/skills/k2b-ship/SKILL.md
git commit -m "feat(k2b-ship): step 3a tier detection + 3b routing skeleton"
```

---

### Task 11: Tier 1 review loop -- 2-pass cap with `--json` verdict parsing + escalation

**Files:**
- Modify: `.claude/skills/k2b-ship/SKILL.md` (Step 3b.1)

**Goal:** The Tier 1 loop is the non-trivial part of tiering -- it's the place where Codex HIGH #2 (silent gate bypass) and HIGH #3 (wrong verdict parsing) were flagged. Codex also flagged the `--skip-codex` + Tier 1 interaction under omissions.

**Contract:**
1. Up to 2 MiniMax `--scope diff` passes, each parsing `--json` stdout for `parsed.verdict` case-insensitively.
2. If verdict is `approve` on any pass, exit the loop successfully.
3. If MiniMax **exits non-zero** on any pass (network error, API down, unparseable response): escalate -- if `--skip-codex` is NOT set, reassign `TIER=2` and drop into the Tier 2 Codex flow. If `--skip-codex` IS set, REFUSE (both reviewers blocked).
4. If both passes return `NEEDS-ATTENTION` without MiniMax failure: Keith triages findings inline, then if Keith still wants to ship, auto-promote to Tier 2 Codex single-pass.
5. All findings surfaced to Keith verbatim -- no pre-filtering.

- [ ] **Step 1: Draft the Step 3b.1 content**

```markdown
##### 3b.1 Tier 1 review loop

```bash
if [ "$TIER" = "1" ]; then
  TIER_1_PASS=1
  TIER_1_MAX=2
  TIER_1_APPROVED=no
  TIER_1_MINIMAX_FAILED=no

  while [ "$TIER_1_PASS" -le "$TIER_1_MAX" ]; do
    echo "[tier-1] pass $TIER_1_PASS of $TIER_1_MAX -- running MiniMax --scope diff..."
    set +e
    VERDICT_JSON=$(scripts/minimax-review.sh \
      --scope diff \
      --files "$CHANGED_FILES" \
      --focus "tier-1 single-pass docs review (pass $TIER_1_PASS)" \
      --json 2>/tmp/tier1_pass_${TIER_1_PASS}.err)
    MINIMAX_EXIT=$?
    set -e

    if [ "$MINIMAX_EXIT" -ne 0 ]; then
      echo "[tier-1] MiniMax FAILED on pass $TIER_1_PASS (exit $MINIMAX_EXIT):" >&2
      cat /tmp/tier1_pass_${TIER_1_PASS}.err >&2
      TIER_1_MINIMAX_FAILED=yes
      break
    fi

    # Parse verdict: parsed.verdict (case-insensitive). See minimax_review.py:451.
    VERDICT=$(echo "$VERDICT_JSON" | python3 -c "
import json, sys
try:
    data = json.load(sys.stdin)
except json.JSONDecodeError:
    print('error')
    sys.exit(0)
v = data.get('verdict', '') or ''
print(v.strip().lower())
")

    if [ "$VERDICT" = "approve" ]; then
      echo "[tier-1] APPROVED on pass $TIER_1_PASS"
      TIER_1_APPROVED=yes
      break
    fi

    # Non-approve verdict (needs-attention, etc.). Surface findings to Keith.
    echo "[tier-1] pass $TIER_1_PASS verdict=$VERDICT -- surfacing findings:"
    echo "$VERDICT_JSON" | python3 -m json.tool

    # Keith decides: fix findings or continue. The loop only advances after
    # Keith confirms -- this is an interactive ship step, not a silent retry.
    echo "[tier-1] fix findings and re-run pass, or accept and continue? Keith: _____"
    # (Claude waits for Keith's reply before advancing TIER_1_PASS)

    TIER_1_PASS=$((TIER_1_PASS + 1))
  done

  if [ "$TIER_1_MINIMAX_FAILED" = "yes" ]; then
    if [ -n "${SKIP_CODEX:-}" ]; then
      echo "[FATAL] tier-1 MiniMax failed AND --skip-codex blocks Codex escalation." >&2
      echo "Both reviewers unavailable. REFUSE /ship (Keith must fix MiniMax or allow Codex)." >&2
      exit 3
    fi
    echo "[tier-1] escalating to tier-2 Codex single-pass due to MiniMax failure."
    TIER=2
    # Fall through to Tier 2 block in Step 3b above (loop back to the dispatch)
  elif [ "$TIER_1_APPROVED" = "no" ]; then
    echo "[tier-1] 2 passes returned non-approve verdict; auto-promoting to tier-2."
    TIER=2
    # Fall through to Tier 2 block
  else
    REVIEW_RESULT="tier-1-approve-pass-$TIER_1_PASS"
    # Proceed to step 4
  fi
fi
```
```

- [ ] **Step 2: Apply the edit to SKILL.md**

Insert the Step 3b.1 block immediately after Step 3b, as a subsection. The dispatch in Step 3b references "see Step 3b.1"; this fills that reference.

- [ ] **Step 3: Verify SKILL.md structure**

```bash
grep -n "^##### 3" .claude/skills/k2b-ship/SKILL.md
grep -c "parsed.verdict" .claude/skills/k2b-ship/SKILL.md  # Should appear in the python3 -c block
```

- [ ] **Step 4: Commit**

```bash
git add .claude/skills/k2b-ship/SKILL.md
git commit -m "feat(k2b-ship): step 3b.1 tier-1 loop with --json verdict + escalation"
```

---

### Task 12: Integration smoke on live K2B working tree

**Files:** None (verification only)

**Goal:** Run the classifier against the in-flight working tree and confirm it picks the expected tier **for this ship's actual diff** (not the v1 plan's vault-only prediction).

- [ ] **Step 1: Run the classifier**

```bash
scripts/ship-detect-tier.py
```

Expected: `tier: 3`. Reason: the ship modifies `.claude/skills/k2b-ship/SKILL.md` (NOT in the allowlist but tier-1 docs) PLUS adds `scripts/ship-detect-tier.py` + `scripts/lib/tier_detection.py` + `scripts/tier3-paths.yml` (all three ARE in the Tier 3 allowlist via self-inclusion). Allowlist rule fires on the three new-self files -> Tier 3.

If the classifier returns a different tier, trace the reason line and either adjust the expectation or fix the classifier.

- [ ] **Step 2: Run the full test suite**

```bash
bash tests/ship-detect-tier.test.sh
```

Expected: every test PASS, final `all tests passed`.

- [ ] **Step 3: No commit (verification only)**

---

## Rollout checklist at /ship time

- [ ] All tests in `tests/ship-detect-tier.test.sh` PASS (target ~25 tests).
- [ ] Integration smoke in Task 12 confirms the live working tree classifies as Tier 3.
- [ ] Codex Checkpoint 1 v2 review complete (this plan + v2 pivots all folded).
- [ ] Checkpoint 2 runs as Tier 3 on this ship (classifier classifies its own shipping diff via allowlist self-reference). Meta-correctness: the classifier is itself Tier 3 per the config it ships.

## Post-Ship 1 follow-ups (Ship 2 scope, NOT in this plan)

1. **`/ship --tier N` override** -- define the skill-level parsing contract ("if invocation text exactly matches `/ship --tier N`..."), add integration test, document in CLAUDE.md.
2. **Codex `--cached` vs `--working-tree` diagnostic** -- bounded investigation: synthetic repro, identify where the `--cached`-style first pass comes from, patch in SKILL.md if possible, escalate to `codex-companion.mjs` patch if not.
3. **`feature_minimax-scope-phase-b` HIGH-2 (wikilink caching)** -- independent, not blocked by this ship.
4. **K2Bi port of classifier + routing** -- ~1 week post-Ship-1 bake.
5. **Classifier calibration from override audit log** -- only if override usage shows a pattern.

## Risks during execution

- **`git status --porcelain -z` parsing is non-trivial.** Rename records use NUL-separated old/new path; test 1c pins spaces in paths.
- **YAML `**` semantics.** Ship 1 supports only trailing `**`. Documented in the config header and tested.
- **Tier 1 loop is an interactive ship step.** The bash sketch above has Keith-in-the-loop pauses. Claude (executing /ship) must stop and wait for Keith's reply between passes when findings surface.
- **SKILL.md surgery must preserve Step 3c verbatim.** A diff regression here could drop the "both reviewers unavailable -> REFUSE" path. Task 10 Step 4 greps for verification strings.
- **Changed-file list consistency between classifier and reviewer.** Both read working-tree; the classifier captures the file list in its state; Step 3b re-derives via `git diff --name-only -z HEAD`. The two lists should match. If they drift, change the classifier to emit its file list on stderr and have Step 3b reuse it.
