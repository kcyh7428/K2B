# K2B Audit Fix #8 -- Git Hooks for Status Edits & Direct Log Appends

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans.

**Goal:** Block commits that modify the `status:` line in `wiki/concepts/feature_*.md` unless they came from `/ship`. Also block commits that contain `>> wiki/log.md` direct appends, sealing Fix #1's escape hatch. Commit only lands when `/ship` tagged it with a `Co-Shipped-By: k2b-ship` trailer, or when Keith overrides via `K2B_ALLOW_STATUS_EDIT=1` (status edits) / `K2B_ALLOW_LOG_APPEND=1` (log appends).

**Architecture:** Two repo-tracked hooks under `.githooks/`, enabled via `git config core.hooksPath .githooks`.

- `.githooks/pre-commit` runs check 2 only. Greps the staged diff for direct `>> wiki/log.md` appends. This check reads the staged diff, not the commit message, so it is safe in `pre-commit`. Override: `K2B_ALLOW_LOG_APPEND=1` (documented use: rewriting `scripts/wiki-log-append.sh` itself).
- `.githooks/commit-msg` runs check 1. Greps the staged diff for `^[+-]status:` lines in feature files. When any are present, requires a `Co-Shipped-By: k2b-ship` trailer in the commit message file passed as `$1`. Override: `K2B_ALLOW_STATUS_EDIT=1`.

**Why split the hook?** In `pre-commit`, the read order of `COMMIT_EDITMSG` is git-version-dependent (sometimes the file is written before the hook runs, sometimes after). The `commit-msg` hook runs after the message is in `$1` and is guaranteed to see the full message.

**Install scope:** K2B repo only. `K2B-Vault` is NOT a git repo, so there is no commit path to guard on the vault side. Feature files currently live in the vault (not commit-tracked), so check 1 has no current target files. It is latent; it will activate automatically if feature files ever move into the K2B repo.

**Tech stack:** Bash, git trailer handling, `git config core.hooksPath`.

**Spec source:** `docs/superpowers/specs/2026-04-15-k2b-audit-fixes-design.md` Fix #8.

**Dependencies:**
- **Fix #1 already landed.** `scripts/wiki-log-append.sh` exists and is the single writer, so check 2 will not false-reject legitimate commits.

---

## File Structure

**Create:**
- `.githooks/pre-commit` -- check 2 (direct log append guard).
- `.githooks/commit-msg` -- check 1 (status-edit trailer guard).
- `scripts/install-hooks.sh` -- one-command installer.
- `tests/pre-commit.test.sh` -- scenarios 4, 5, 6 (log append + innocuous change).
- `tests/commit-msg.test.sh` -- scenarios 1, 2, 3 (status edit, trailer, override).

**Modify:**
- `.claude/skills/k2b-ship/SKILL.md` -- commit heredoc gets `Co-Shipped-By: k2b-ship` appended as a trailer.
- Repo config: `git config core.hooksPath .githooks` (executed by install-hooks.sh).

---

## Task 1: Install scope already resolved

Per codex review: `K2B-Vault` is NOT a git repo. Install scope is **K2B repo only**. No vault-side hook is needed. Feature files are not currently commit-tracked in this repo; check 1 is latent by design.

No investigation step required. Proceed to Task 2.

---

## Task 2: Write `.githooks/pre-commit` (check 2 only)

Create `.githooks/pre-commit` that greps the staged diff for direct `>> wiki/log.md` appends. Exits 1 on match unless `K2B_ALLOW_LOG_APPEND=1`.

Regex uses `>>[[:space:]]*\S*wiki/log\.md` which requires the double chevron (single `>` does not match) and tolerates whitespace plus quoted/absolute paths. Known edge: heredoc bodies containing `>> wiki/log.md` as literal text will also match. Use the env override for that case.

```bash
#!/usr/bin/env bash
# .githooks/pre-commit
# Check 2: block direct `>> wiki/log.md` append. Single writer is
# scripts/wiki-log-append.sh (audit Fix #1).
# Override: K2B_ALLOW_LOG_APPEND=1 (use only when rewriting the helper itself).
#
# Note: the regex may flag heredoc body data that contains `>> wiki/log.md`
# as literal text. Rewrite the heredoc or use K2B_ALLOW_LOG_APPEND=1.

set -euo pipefail

LOG_DIRECT=$(git diff --cached | grep -E '^\+.*>>[[:space:]]*\S*wiki/log\.md' || true)
if [ -n "$LOG_DIRECT" ]; then
  if [ "${K2B_ALLOW_LOG_APPEND:-}" = "1" ]; then
    echo "warning: direct >> wiki/log.md append, K2B_ALLOW_LOG_APPEND override acknowledged"
    exit 0
  fi
  echo "error: direct >> append to wiki/log.md detected."
  echo "Use scripts/wiki-log-append.sh (audit Fix #1) as the single writer."
  echo ""
  echo "Offending lines:"
  echo "$LOG_DIRECT"
  echo ""
  echo "Override (rewriting the helper itself only): K2B_ALLOW_LOG_APPEND=1 git commit ..."
  exit 1
fi

exit 0
```

Chmod 755, syntax-check with `bash -n`, commit.

---

## Task 3: Write `.githooks/commit-msg` (check 1)

Create `.githooks/commit-msg`. Runs after the commit message is in `$1`. Greps the staged diff for `^[+-]status:` lines in feature files. When any are present, requires the `Co-Shipped-By: k2b-ship` trailer in `$1`, or the `K2B_ALLOW_STATUS_EDIT=1` override.

```bash
#!/usr/bin/env bash
# .githooks/commit-msg
# Check 1: status: line edits in wiki/concepts/feature_*.md require /ship
# provenance (Co-Shipped-By: k2b-ship trailer) OR K2B_ALLOW_STATUS_EDIT=1 override.
#
# Runs as commit-msg (not pre-commit) because pre-commit's COMMIT_EDITMSG read
# order is git-version-dependent. commit-msg gets the message file as $1 and
# is guaranteed to see the full message.

set -euo pipefail
MSG_FILE="$1"

STATUS_CHANGES=$(git diff --cached --unified=0 -- \
  'wiki/concepts/feature_*.md' \
  'wiki/concepts/Shipped/feature_*.md' \
  'K2B-Vault/wiki/concepts/feature_*.md' \
  'K2B-Vault/wiki/concepts/Shipped/feature_*.md' \
  2>/dev/null | grep -E '^[+-]status:' || true)

if [ -n "$STATUS_CHANGES" ]; then
  if ! grep -q '^Co-Shipped-By: k2b-ship' "$MSG_FILE"; then
    if [ "${K2B_ALLOW_STATUS_EDIT:-}" != "1" ]; then
      echo "error: status: line modified in wiki/concepts/feature_*.md outside /ship"
      echo ""
      echo "Changed status lines:"
      echo "$STATUS_CHANGES"
      echo ""
      echo "Use /ship to transition feature status."
      echo "Override (one-off repairs only): K2B_ALLOW_STATUS_EDIT=1 git commit ..."
      exit 1
    fi
    echo "warning: status: line edited outside /ship, K2B_ALLOW_STATUS_EDIT override acknowledged"
  fi
fi

exit 0
```

Chmod 755, syntax-check with `bash -n`, commit.

---

## Task 4: Write `scripts/install-hooks.sh`

```bash
#!/usr/bin/env bash
# scripts/install-hooks.sh
# One-time install of repo-tracked git hooks. Idempotent.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

git config core.hooksPath .githooks
chmod 755 .githooks/pre-commit
chmod 755 .githooks/commit-msg

echo "install-hooks: core.hooksPath now points to .githooks"
echo "install-hooks: pre-commit and commit-msg are executable"
```

Chmod 755, run it, verify with `git config core.hooksPath`, commit.

---

## Task 5: Update k2b-ship commit heredoc

In `.claude/skills/k2b-ship/SKILL.md`, locate the commit message heredoc and append `Co-Shipped-By: k2b-ship` as an additional trailer alongside the existing `Co-Authored-By:` line.

Order: `Co-Authored-By:` first, `Co-Shipped-By: k2b-ship` second, each on its own line, blank line before the trailers block.

Commit.

---

## Task 6: Split functional tests

### `tests/pre-commit.test.sh` -- scenarios 4, 5, 6

- Scenario 4: commit with `>> wiki/log.md` append -> REJECT.
- Scenario 5: commit with `>> wiki/log.md` append AND `Co-Shipped-By: k2b-ship` trailer -> STILL REJECT (trailer does not override check 2).
- Scenario 6: commit touching an unrelated file -> ALLOW.

### `tests/commit-msg.test.sh` -- scenarios 1, 2, 3

- Scenario 1: status edit without trailer -> REJECT.
- Scenario 2: status edit with `Co-Shipped-By: k2b-ship` trailer -> ALLOW.
- Scenario 3: status edit with `K2B_ALLOW_STATUS_EDIT=1` -> ALLOW with warning.

Each test harness creates a throwaway repo, copies in BOTH hook files, sets `core.hooksPath`, and exercises its scenarios. Chmod 755, run, commit.

---

## Task 7: End-to-end smoke

- Run `scripts/install-hooks.sh` against the real K2B repo.
- Verify `git config core.hooksPath` returns `.githooks`.
- Attempt a commit that touches `scripts/wiki-log-append.sh` (innocuous change) and verify it passes.
- Attempt a commit that contains a `>> wiki/log.md` line and verify it is rejected.
- There are no feature files in this repo, so check 1 cannot be smoked end-to-end against real data; it is covered by `tests/commit-msg.test.sh`.

---

## Self-review checklist

- [ ] `.githooks/pre-commit` and `.githooks/commit-msg` exist, are executable, pass `bash -n`.
- [ ] `git config core.hooksPath` returns `.githooks`.
- [ ] All 5 scenarios pass across the two test files (plus scenario 6 innocuous in pre-commit).
- [ ] `Co-Shipped-By: k2b-ship` trailer is appended in every `/ship` commit heredoc.
- [ ] `K2B_ALLOW_STATUS_EDIT=1` and `K2B_ALLOW_LOG_APPEND=1` emit warnings, not silent passes.
- [ ] Regex `>>[[:space:]]*\S*wiki/log\.md` requires double chevron; single `>` does not match.
- [ ] No em dashes in any file touched.
