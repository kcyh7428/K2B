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
  set +u
  for d in "${TMP_DIRS[@]}"; do
    [ -n "$d" ] && [ -d "$d" ] && rm -rf "$d"
  done
  set -u
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
#   CLAUDE.md, AGENTS.md, README.md, .mcp.json, DEVLOG.md
#   .claude/skills/k2b-ship/SKILL.md
#   .agents/skills/k2b-ship/SKILL.md
#   .codex/hooks.json
#   scripts/deploy-to-mini.sh, scripts/review.sh
#   launchd/com.k2b.router-watchdog.plist
#   README-router-watchdog.md
#   k2b-remote/src/index.ts, k2b-remote/package.json
#   k2b-dashboard/src/App.tsx
#   k2b-remote/node_modules/junk/x.js   (excluded dir)
build_tree() {
  local base="$1" tag="${2:-baseline}"
  mkdir -p "$base/.claude/skills/k2b-ship"
  mkdir -p "$base/.agents/skills/k2b-ship"
  mkdir -p "$base/.codex"
  mkdir -p "$base/scripts"
  mkdir -p "$base/launchd"
  mkdir -p "$base/k2b-remote/src"
  mkdir -p "$base/k2b-remote/node_modules/junk"
  mkdir -p "$base/k2b-dashboard/src"
  printf 'CLAUDE content %s\n' "$tag" > "$base/CLAUDE.md"
  printf 'AGENTS content %s\n' "$tag" > "$base/AGENTS.md"
  printf 'README %s\n' "$tag" > "$base/README.md"
  printf '{"tag":"%s"}\n' "$tag" > "$base/.mcp.json"
  printf 'DEVLOG %s\n' "$tag" > "$base/DEVLOG.md"
  printf '{"hooks":{"SessionStart":[{"hooks":[{"type":"command","command":"%s"}]}]}}\n' "$tag" > "$base/.codex/hooks.json"
  printf 'router watchdog runbook %s\n' "$tag" > "$base/README-router-watchdog.md"
  printf 'skill body %s\n' "$tag" > "$base/.claude/skills/k2b-ship/SKILL.md"
  printf 'agent skill body %s\n' "$tag" > "$base/.agents/skills/k2b-ship/SKILL.md"
  printf '#!/bin/bash\n# deploy %s\n' "$tag" > "$base/scripts/deploy-to-mini.sh"
  printf '#!/bin/bash\n# review %s\n' "$tag" > "$base/scripts/review.sh"
  printf '<plist>%s</plist>\n' "$tag" > "$base/launchd/com.k2b.router-watchdog.plist"
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
# Scenario 5: only DEVLOG.md differs -> skills category (revised 2026-05-07)
# Prior to 2026-05-07, DEVLOG.md was excluded from rsync -- a follow-up devlog
# commit was treated as no-op for sync. After the Mac Mini .git removal on
# 2026-05-07, Mini-side processes (bot, observer) read DEVLOG.md to introspect
# "what shipped" since `git log` no longer works on Mini. DEVLOG.md is now
# part of the top-level docs sync, which is in the skills category, so a
# DEVLOG-only diff must flag "skills". See L-2026-05-07-001.
# ---------------------------------------------------------------------------
{
  LOCAL="$(mktmp)"
  REMOTE="$(mktmp)"
  build_tree "$LOCAL" "v2"
  build_tree "$REMOTE" "v2"
  printf 'DEVLOG NEW entry\n' > "$LOCAL/DEVLOG.md"
  out="$(run_detect "$LOCAL" "$REMOTE")"
  assert_detect "only DEVLOG.md differs: skills category flagged" "skills" "$out"
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

# ---------------------------------------------------------------------------
# Scenario 9: router-watchdog launchd/runbook files travel with scripts sync
# ---------------------------------------------------------------------------
{
  LOCAL="$(mktmp)"
  REMOTE="$(mktmp)"
  build_tree "$LOCAL" "v2"
  build_tree "$REMOTE" "v2"
  printf '<plist>NEW</plist>\n' > "$LOCAL/launchd/com.k2b.router-watchdog.plist"
  printf 'router watchdog runbook NEW\n' > "$LOCAL/README-router-watchdog.md"
  out="$(run_detect "$LOCAL" "$REMOTE")"
  assert_detect "launchd + router watchdog runbook detected as scripts sync" "scripts" "$out"
}

# ---------------------------------------------------------------------------
# Scenario 10: local-shaped target (test mode) skips ssh+install entirely
#
# Why this matters: 2026-05-07 dry-run inspection caught install/source
# drift -- /sync rsynced new optimize-leaves.py to Mini but launchd kept
# running the old installed snapshot at ~/Library/Application Support/.../
# bin/. The fix in sync_scripts() is to ssh+install.sh after rsync.
# This test asserts that the test-mode invariant (local-shaped target,
# is_remote_target=false) skips the new ssh+install path entirely, so the
# existing 9 scenarios above still pass with no SSH attempts.
#
# A full positive test (remote-shaped target → install.sh fires) was
# attempted via PATH-mock ssh but is intractable without also mocking the
# rsync-over-ssh server protocol. The positive case is smoke-tested via
# manual /sync to Mac Mini after this lands and confirming install.sh ran
# (visible in launchd-leaf-optimizer.err.log + install-manifest.sha256
# updated).
# ---------------------------------------------------------------------------
{
  LOCAL="$(mktmp)"
  REMOTE="$(mktmp)"
  build_tree "$LOCAL" "v2"
  build_tree "$REMOTE" "v1"
  # Install.sh present locally (gate condition for the new install step).
  mkdir -p "$LOCAL/scripts/router-watchdog"
  printf '#!/usr/bin/env bash\necho should-not-run\n' \
    > "$LOCAL/scripts/router-watchdog/install.sh"
  chmod +x "$LOCAL/scripts/router-watchdog/install.sh"

  MOCK_DIR="$(mktmp)"
  cat > "$MOCK_DIR/ssh" <<'MOCK'
#!/bin/bash
echo "$@" >> "$MOCK_LOG"
exit 0
MOCK
  chmod +x "$MOCK_DIR/ssh"
  MOCK_LOG="$MOCK_DIR/ssh-invocations.log"
  : > "$MOCK_LOG"

  # Local-shaped target (no colon) -- is_remote_target() returns false,
  # so no ssh path should fire. This is what existing tests rely on.
  EXIT=0
  PATH="$MOCK_DIR:$PATH" \
  K2B_LOCAL_BASE="$LOCAL" \
  K2B_RSYNC_TARGET_PREFIX="$REMOTE" \
  MOCK_LOG="$MOCK_LOG" \
    bash "$SCRIPT" scripts >/dev/null 2>&1 || EXIT=$?

  if [ "$EXIT" -ne 0 ]; then
    echo "  FAIL: deploy script exited non-zero ($EXIT) before reaching the install gate."
    FAIL=$((FAIL + 1))
  elif [ -s "$MOCK_LOG" ]; then
    echo "  FAIL: ssh fired on local-shaped target. ssh log:"
    cat "$MOCK_LOG" | sed 's/^/    /'
    FAIL=$((FAIL + 1))
  else
    echo "  PASS: local-shaped target skips ssh+install (test-mode invariant)"
    PASS=$((PASS + 1))
  fi
}

# ---------------------------------------------------------------------------
# Scenario 11: Codex instruction surfaces travel with skills sync
# ---------------------------------------------------------------------------
{
  LOCAL="$(mktmp)"
  REMOTE="$(mktmp)"
  build_tree "$LOCAL" "v2"
  build_tree "$REMOTE" "v2"
  printf 'AGENTS NEW\n' > "$LOCAL/AGENTS.md"
  printf 'agent skill body NEW\n' > "$LOCAL/.agents/skills/k2b-ship/SKILL.md"
  printf '{"hooks":{"SessionStart":[{"hooks":[{"type":"command","command":"NEW"}]}]}}\n' > "$LOCAL/.codex/hooks.json"
  out="$(run_detect "$LOCAL" "$REMOTE")"
  assert_detect "Codex instruction surfaces detected as skills sync" "skills" "$out"
}

# ---------------------------------------------------------------------------
# Scenario 12: first Codex sync to a Mini missing local-only Codex surfaces
# ---------------------------------------------------------------------------
{
  LOCAL="$(mktmp)"
  REMOTE="$(mktmp)"
  build_tree "$LOCAL" "v2"
  build_tree "$REMOTE" "v2"
  mkdir -p "$LOCAL/.codex/archive"
  printf 'local job\n' > "$LOCAL/.codex/job.md"
  printf 'archive\n' > "$LOCAL/.codex/archive/old.md"
  rm -f "$REMOTE/AGENTS.md"
  rm -rf "$REMOTE/.agents" "$REMOTE/.codex"
  out="$(run_detect "$LOCAL" "$REMOTE")"
  assert_detect "Codex instruction surfaces missing remotely trigger skills sync" "skills" "$out"
}

# ---------------------------------------------------------------------------
# Scenario 13: remote shell quoting preserves tilde expansion and spaces
# ---------------------------------------------------------------------------
{
  quoted="$(K2B_DEPLOY_SELFTEST=remote-shell-quote bash "$SCRIPT" '~/Projects/K2B With Space/.agents/skills')"
  REMOTE_HOME="$(mktmp)/remote home"
  mkdir -p "$REMOTE_HOME"
  cmd="mkdir -p $quoted"
  if HOME="$REMOTE_HOME" bash -c "$cmd" && [ -d "$REMOTE_HOME/Projects/K2B With Space/.agents/skills" ]; then
    echo "  PASS: remote shell quote preserves remote HOME expansion and spaces"
    PASS=$((PASS + 1))
  else
    echo "  FAIL: remote shell quote did not create expected path"
    echo "    quoted: $quoted"
    echo "    cmd:    $cmd"
    FAIL=$((FAIL + 1))
  fi

  quoted="$(K2B_DEPLOY_SELFTEST=remote-shell-quote bash "$SCRIPT" "~/Projects/K2B's Space/.agents/skills")"
  REMOTE_HOME="$(mktmp)/remote home quote"
  mkdir -p "$REMOTE_HOME"
  cmd="mkdir -p $quoted"
  if HOME="$REMOTE_HOME" bash -c "$cmd" && [ -d "$REMOTE_HOME/Projects/K2B's Space/.agents/skills" ]; then
    echo "  PASS: remote shell quote preserves single quotes"
    PASS=$((PASS + 1))
  else
    echo "  FAIL: remote shell quote did not handle single quote path"
    echo "    quoted: $quoted"
    echo "    cmd:    $cmd"
    FAIL=$((FAIL + 1))
  fi

  quoted="$(K2B_DEPLOY_SELFTEST=remote-shell-quote bash "$SCRIPT" "~/Projects/K2B'")"
  REMOTE_HOME="$(mktmp)/remote home trailing quote"
  mkdir -p "$REMOTE_HOME"
  cmd="mkdir -p $quoted"
  if HOME="$REMOTE_HOME" bash -c "$cmd" && [ -d "$REMOTE_HOME/Projects/K2B'" ]; then
    echo "  PASS: remote shell quote preserves trailing single quote"
    PASS=$((PASS + 1))
  else
    echo "  FAIL: remote shell quote did not handle trailing single quote"
    echo "    quoted: $quoted"
    echo "    cmd:    $cmd"
    FAIL=$((FAIL + 1))
  fi

  quoted="$(K2B_DEPLOY_SELFTEST=remote-shell-quote bash "$SCRIPT" "~/Projects/K2B'a'b")"
  REMOTE_HOME="$(mktmp)/remote home multi quote"
  mkdir -p "$REMOTE_HOME"
  cmd="mkdir -p $quoted"
  if HOME="$REMOTE_HOME" bash -c "$cmd" && [ -d "$REMOTE_HOME/Projects/K2B'a'b" ]; then
    echo "  PASS: remote shell quote preserves multiple single quotes"
    PASS=$((PASS + 1))
  else
    echo "  FAIL: remote shell quote did not handle multiple single quotes"
    echo "    quoted: $quoted"
    echo "    cmd:    $cmd"
    FAIL=$((FAIL + 1))
  fi

  EXIT=0
  K2B_DEPLOY_SELFTEST=remote-shell-quote bash "$SCRIPT" '~/Projects/K2B "Quoted"/.agents/skills' >/dev/null 2>&1 || EXIT=$?
  if [ "$EXIT" -ne 0 ]; then
    echo "  PASS: remote shell quote rejects double quotes"
    PASS=$((PASS + 1))
  else
    echo "  FAIL: remote shell quote should reject double quotes"
    FAIL=$((FAIL + 1))
  fi

  EXIT=0
  K2B_DEPLOY_SELFTEST=remote-shell-quote bash "$SCRIPT" $'~/Projects/K2B\tBad/.agents/skills' >/dev/null 2>&1 || EXIT=$?
  if [ "$EXIT" -ne 0 ]; then
    echo "  PASS: remote shell quote rejects control characters"
    PASS=$((PASS + 1))
  else
    echo "  FAIL: remote shell quote should reject control characters"
    FAIL=$((FAIL + 1))
  fi

  EXIT=0
  K2B_DEPLOY_SELFTEST=remote-shell-quote bash "$SCRIPT" '~/Projects/K2B\bad' >/dev/null 2>&1 || EXIT=$?
  if [ "$EXIT" -ne 0 ]; then
    echo "  PASS: remote shell quote rejects backslashes"
    PASS=$((PASS + 1))
  else
    echo "  FAIL: remote shell quote should reject backslashes"
    FAIL=$((FAIL + 1))
  fi

  MOCK_DIR="$(mktmp)"
  MOCK_LOG="$MOCK_DIR/ssh-cmd.log"
  REMOTE_HOME="$(mktmp)/remote home ssh"
  mkdir -p "$REMOTE_HOME"
  cat > "$MOCK_DIR/ssh" <<'MOCK'
#!/bin/bash
: "${REMOTE_HOME:?REMOTE_HOME unset}"
host="$1"
shift
if [ "$#" -ne 1 ]; then
  echo "expected exactly one remote command argument, got $#" >&2
  exit 99
fi
cmd="$1"
printf '%s\n' "$cmd" > "$MOCK_LOG"
bash -n -c "$cmd" || exit 98
HOME="$REMOTE_HOME" bash -c "$cmd"
MOCK
  chmod +x "$MOCK_DIR/ssh"
  EXIT=0
  PATH="$MOCK_DIR:$PATH" \
  MOCK_LOG="$MOCK_LOG" \
  REMOTE_HOME="$REMOTE_HOME" \
  K2B_RSYNC_TARGET_PREFIX="mockhost:~/Projects/K2B With Space" \
  K2B_DEPLOY_SELFTEST=ensure-target-dirs \
    bash "$SCRIPT" ".agents/skills" ".codex" >/dev/null 2>&1 || EXIT=$?
  if [ "$EXIT" -eq 0 ] && \
     [ -d "$REMOTE_HOME/Projects/K2B With Space" ] && \
     [ -d "$REMOTE_HOME/Projects/K2B With Space/.agents/skills" ] && \
     [ -d "$REMOTE_HOME/Projects/K2B With Space/.codex" ]; then
    logged_cmd="$(cat "$MOCK_LOG")"
    expected_cmd='mkdir -p "$HOME"/'"'"'Projects/K2B With Space'"'"' "$HOME"/'"'"'Projects/K2B With Space/.agents/skills'"'"' "$HOME"/'"'"'Projects/K2B With Space/.codex'"'"''
    if [ "$logged_cmd" = "$expected_cmd" ]; then
      echo "  PASS: remote ensure_target_dirs command works through mock ssh"
      PASS=$((PASS + 1))
    else
      echo "  FAIL: ensure_target_dirs produced unexpected ssh command"
      echo "    expected: $expected_cmd"
      echo "    actual:   $logged_cmd"
      FAIL=$((FAIL + 1))
    fi
  else
    echo "  FAIL: ensure_target_dirs mock ssh path failed (exit $EXIT)"
    [ -f "$MOCK_LOG" ] && sed 's/^/    ssh-cmd: /' "$MOCK_LOG"
    FAIL=$((FAIL + 1))
  fi

  MOCK_LOG="$MOCK_DIR/ssh-dry-run.log"
  : > "$MOCK_LOG"
  REMOTE_HOME="$(mktmp)/remote home dry run"
  mkdir -p "$REMOTE_HOME"
  EXIT=0
  PATH="$MOCK_DIR:$PATH" \
  MOCK_LOG="$MOCK_LOG" \
  REMOTE_HOME="$REMOTE_HOME" \
  K2B_RSYNC_TARGET_PREFIX="mockhost:~/Projects/K2B Dry Run" \
  K2B_DEPLOY_SELFTEST=ensure-target-dirs-dry-run \
    bash "$SCRIPT" ".agents/skills" ".codex" >/dev/null 2>&1 || EXIT=$?
  if [ "$EXIT" -eq 0 ] && [ ! -s "$MOCK_LOG" ] && [ ! -e "$REMOTE_HOME/Projects/K2B Dry Run" ]; then
    echo "  PASS: ensure_target_dirs dry-run does not invoke ssh"
    PASS=$((PASS + 1))
  else
    echo "  FAIL: ensure_target_dirs dry-run should not invoke ssh or create dirs (exit $EXIT)"
    [ -f "$MOCK_LOG" ] && sed 's/^/    ssh-cmd: /' "$MOCK_LOG"
    FAIL=$((FAIL + 1))
  fi
}

# ---------------------------------------------------------------------------
# Scenario 14: skills sync creates missing Codex target dirs
# ---------------------------------------------------------------------------
{
  LOCAL="$(mktmp)"
  REMOTE="$(mktmp)"
  build_tree "$LOCAL" "v2"
  build_tree "$REMOTE" "v2"
  mkdir -p "$LOCAL/.codex/archive"
  printf 'local job\n' > "$LOCAL/.codex/job.md"
  printf 'archive\n' > "$LOCAL/.codex/archive/old.md"
  rm -f "$REMOTE/AGENTS.md"
  rm -rf "$REMOTE/.agents" "$REMOTE/.codex"

  EXIT=0
  K2B_LOCAL_BASE="$LOCAL" K2B_RSYNC_TARGET_PREFIX="$REMOTE" \
      bash "$SCRIPT" skills >/dev/null 2>&1 || EXIT=$?

  if [ "$EXIT" -ne 0 ]; then
    echo "  FAIL: skills sync exited non-zero on missing Codex target dirs ($EXIT)"
    FAIL=$((FAIL + 1))
  elif [ ! -d "$REMOTE/.codex" ] || \
       [ ! -f "$REMOTE/AGENTS.md" ] || \
       [ ! -f "$REMOTE/.agents/skills/k2b-ship/SKILL.md" ] || \
       [ ! -f "$REMOTE/.codex/hooks.json" ]; then
    echo "  FAIL: skills sync did not create expected Codex target files"
    FAIL=$((FAIL + 1))
  elif [ -e "$REMOTE/.codex/job.md" ] || [ -e "$REMOTE/.codex/archive/old.md" ] || [ -d "$REMOTE/.codex/archive" ]; then
    echo "  FAIL: skills sync leaked runtime .codex artifacts"
    FAIL=$((FAIL + 1))
  else
    echo "  PASS: skills sync creates missing Codex target dirs"
    PASS=$((PASS + 1))
  fi
}

# ---------------------------------------------------------------------------
# Scenario 15: remote-shaped auto sync exercises detect + sync + verification
# ---------------------------------------------------------------------------
{
  LOCAL="$(mktmp)"
  REMOTE_HOME="$(mktmp)/remote home full sync"
  REMOTE_PROJECT="$REMOTE_HOME/Projects/K2B Remote"
  build_tree "$LOCAL" "v2"
  build_tree "$REMOTE_PROJECT" "v2"
  mkdir -p "$LOCAL/.codex/archive"
  printf 'local job\n' > "$LOCAL/.codex/job.md"
  printf 'archive\n' > "$LOCAL/.codex/archive/old.md"
  rm -f "$REMOTE_PROJECT/AGENTS.md"
  rm -rf "$REMOTE_PROJECT/.agents" "$REMOTE_PROJECT/.codex"

  MOCK_DIR="$(mktmp)"
  SSH_LOG="$MOCK_DIR/ssh-full.log"
  RSYNC_LOG="$MOCK_DIR/rsync-full.log"
  : > "$SSH_LOG"
  : > "$RSYNC_LOG"

  cat > "$MOCK_DIR/ssh" <<'MOCK'
#!/bin/bash
set -euo pipefail
: "${REMOTE_HOME:?REMOTE_HOME unset}"
: "${SSH_LOG:?SSH_LOG unset}"
while [ "$#" -gt 0 ] && [[ "$1" == -* ]]; do
  if [ "$1" = "-o" ]; then
    shift 2
  else
    shift
  fi
done
host="${1:?missing host}"
shift
if [ "$#" -ne 1 ]; then
  echo "expected exactly one remote command argument, got $#" >&2
  exit 99
fi
cmd="$1"
printf '%s\n' "$cmd" >> "$SSH_LOG"
if [ "$cmd" = "echo ok" ]; then
  echo ok
  exit 0
fi
HOME="$REMOTE_HOME" bash -c "$cmd"
MOCK

  cat > "$MOCK_DIR/rsync" <<'MOCK'
#!/bin/bash
set -euo pipefail
: "${REMOTE_HOME:?REMOTE_HOME unset}"
: "${RSYNC_LOG:?RSYNC_LOG unset}"

map_target() {
  local target="$1"
  if [[ "$target" == mockhost:* ]]; then
    target="${target#mockhost:}"
    if [[ "$target" == "~" ]]; then
      printf '%s\n' "$REMOTE_HOME"
    elif [[ "$target" == "~/"* ]]; then
      printf '%s/%s\n' "$REMOTE_HOME" "${target#\~/}"
    else
      printf '%s\n' "$target"
    fi
  else
    printf '%s\n' "$target"
  fi
}

dry_run=false
delete=false
args=("$@")
for arg in "$@"; do
  case "$arg" in
    -acn|--dry-run)
      dry_run=true
      ;;
    --delete)
      delete=true
      ;;
  esac
done

count="${#args[@]}"
if [ "$count" -lt 2 ]; then
  echo "mock rsync expected source and destination" >&2
  exit 97
fi
src="${args[$((count - 2))]}"
dst="${args[$((count - 1))]}"
dst_path="$(map_target "$dst")"

if $dry_run; then
  printf 'dry %s -> %s\n' "$src" "$dst_path" >> "$RSYNC_LOG"
  if [ ! -e "$dst_path" ]; then
    echo ">f++++++++ $(basename "${src%/}")"
    exit 0
  fi
  if [ -d "$src" ]; then
    if ! diff -qr "$src" "$dst_path" >/dev/null 2>&1; then
      echo ">f..t...... ."
    fi
  elif ! cmp -s "$src" "$dst_path"; then
    echo ">f..t...... $(basename "$src")"
  fi
  exit 0
fi

printf 'copy %s -> %s\n' "$src" "$dst_path" >> "$RSYNC_LOG"
if $delete && [ -e "$dst_path" ]; then
  rm -rf "$dst_path"
fi
if [ -d "$src" ]; then
  mkdir -p "$dst_path"
  cp -a "$src"/. "$dst_path"/
else
  parent="$(dirname "$dst_path")"
  if [ ! -d "$parent" ]; then
    echo "mock rsync refusing file copy into missing parent: $parent" >&2
    exit 23
  fi
  cp -p "$src" "$dst_path"
fi
MOCK

  chmod +x "$MOCK_DIR/ssh" "$MOCK_DIR/rsync"

  EXIT=0
  PATH="$MOCK_DIR:$PATH" \
  SSH_LOG="$SSH_LOG" \
  RSYNC_LOG="$RSYNC_LOG" \
  REMOTE_HOME="$REMOTE_HOME" \
  K2B_LOCAL_BASE="$LOCAL" \
  K2B_RSYNC_TARGET_PREFIX="mockhost:~/Projects/K2B Remote" \
    bash "$SCRIPT" auto >"$MOCK_DIR/deploy.out" 2>"$MOCK_DIR/deploy.err" || EXIT=$?

  if [ "$EXIT" -ne 0 ]; then
    echo "  FAIL: remote-shaped Codex auto sync exited non-zero ($EXIT)"
    sed 's/^/    stdout: /' "$MOCK_DIR/deploy.out"
    sed 's/^/    stderr: /' "$MOCK_DIR/deploy.err"
    FAIL=$((FAIL + 1))
  elif [ ! -f "$REMOTE_PROJECT/AGENTS.md" ] || \
       [ ! -f "$REMOTE_PROJECT/.agents/skills/k2b-ship/SKILL.md" ] || \
       [ ! -f "$REMOTE_PROJECT/.codex/hooks.json" ]; then
    echo "  FAIL: remote-shaped Codex auto sync did not create expected files"
    FAIL=$((FAIL + 1))
  elif [ -e "$REMOTE_PROJECT/.codex/job.md" ] || \
       [ -e "$REMOTE_PROJECT/.codex/archive/old.md" ] || \
       [ -d "$REMOTE_PROJECT/.codex/archive" ]; then
    echo "  FAIL: remote-shaped Codex auto sync leaked runtime .codex artifacts"
    FAIL=$((FAIL + 1))
  elif ! grep -q 'test -d .*.codex' "$SSH_LOG" || \
       ! grep -q '^mkdir -p ' "$SSH_LOG" || \
       ! grep -q 'test -f .*.codex/hooks.json' "$SSH_LOG"; then
    echo "  FAIL: remote-shaped Codex auto sync did not exercise detect/create/verify SSH path"
    sed 's/^/    ssh: /' "$SSH_LOG"
    FAIL=$((FAIL + 1))
  else
    echo "  PASS: remote-shaped Codex auto sync detects, creates, syncs, and verifies"
    PASS=$((PASS + 1))
  fi
}

# ---------------------------------------------------------------------------
# Scenario 16: forced remote skills dry-run does not create target dirs
# ---------------------------------------------------------------------------
{
  LOCAL="$(mktmp)"
  REMOTE_HOME="$(mktmp)/remote home forced dry run"
  build_tree "$LOCAL" "v2"

  MOCK_DIR="$(mktmp)"
  SSH_LOG="$MOCK_DIR/ssh-forced-dry-run.log"
  RSYNC_LOG="$MOCK_DIR/rsync-forced-dry-run.log"
  : > "$SSH_LOG"
  : > "$RSYNC_LOG"

  cat > "$MOCK_DIR/ssh" <<'MOCK'
#!/bin/bash
set -euo pipefail
: "${SSH_LOG:?SSH_LOG unset}"
while [ "$#" -gt 0 ] && [[ "$1" == -* ]]; do
  if [ "$1" = "-o" ]; then
    shift 2
  else
    shift
  fi
done
host="${1:?missing host}"
shift
if [ "$#" -ne 1 ]; then
  echo "expected exactly one remote command argument, got $#" >&2
  exit 99
fi
cmd="$1"
printf '%s\n' "$cmd" >> "$SSH_LOG"
if [ "$cmd" = "echo ok" ]; then
  echo ok
  exit 0
fi
exit 96
MOCK

  cat > "$MOCK_DIR/rsync" <<'MOCK'
#!/bin/bash
set -euo pipefail
: "${RSYNC_LOG:?RSYNC_LOG unset}"
printf '%s\n' "$*" >> "$RSYNC_LOG"
exit 0
MOCK

  chmod +x "$MOCK_DIR/ssh" "$MOCK_DIR/rsync"

  EXIT=0
  PATH="$MOCK_DIR:$PATH" \
  SSH_LOG="$SSH_LOG" \
  RSYNC_LOG="$RSYNC_LOG" \
  K2B_LOCAL_BASE="$LOCAL" \
  K2B_RSYNC_TARGET_PREFIX="mockhost:~/Projects/K2B Forced Dry Run" \
    bash "$SCRIPT" --dry-run skills >"$MOCK_DIR/deploy.out" 2>"$MOCK_DIR/deploy.err" || EXIT=$?

  if [ "$EXIT" -ne 0 ]; then
    echo "  FAIL: forced remote skills dry-run exited non-zero ($EXIT)"
    sed 's/^/    stdout: /' "$MOCK_DIR/deploy.out"
    sed 's/^/    stderr: /' "$MOCK_DIR/deploy.err"
    FAIL=$((FAIL + 1))
  elif grep -q '^mkdir -p ' "$SSH_LOG" || [ -e "$REMOTE_HOME/Projects/K2B Forced Dry Run" ]; then
    echo "  FAIL: forced remote skills dry-run created target dirs"
    sed 's/^/    ssh: /' "$SSH_LOG"
    FAIL=$((FAIL + 1))
  elif grep -v -- '--dry-run' "$RSYNC_LOG" >/dev/null; then
    echo "  FAIL: forced remote skills dry-run had a non-dry-run rsync invocation"
    sed 's/^/    rsync: /' "$RSYNC_LOG"
    FAIL=$((FAIL + 1))
  elif ! grep -E -- '--dry-run .*mockhost:~/Projects/K2B Forced Dry Run/.codex/hooks.json' "$RSYNC_LOG" >/dev/null; then
    echo "  FAIL: forced remote skills dry-run did not dry-run the .codex/hooks.json rsync"
    sed 's/^/    rsync: /' "$RSYNC_LOG"
    FAIL=$((FAIL + 1))
  else
    echo "  PASS: forced remote skills dry-run skips mkdir and uses rsync dry-run"
    PASS=$((PASS + 1))
  fi
}

# ---------------------------------------------------------------------------
# Scenario 17: k2b-remote runtime upload scratch (workspace/) is NOT code drift
# The bot writes incoming Telegram attachments to k2b-remote/workspace/uploads/
# at runtime. Leftover .ogg/.jpg artifacts on the MacBook (absent on the Mini)
# are runtime scratch, not source -- they must NOT flag the code category, which
# would needlessly `npm run build && pm2 restart k2b-remote` the production bot.
# Mirrors Scenario 6 (node_modules) for the workspace scratch dir.
# ---------------------------------------------------------------------------
{
  LOCAL="$(mktmp)"
  REMOTE="$(mktmp)"
  build_tree "$LOCAL" "v2"
  build_tree "$REMOTE" "v2"
  # Leftover Telegram upload artifacts on the MacBook only -- runtime scratch.
  mkdir -p "$LOCAL/k2b-remote/workspace/uploads"
  printf 'OGG-BYTES\n' > "$LOCAL/k2b-remote/workspace/uploads/1774182849905_file.ogg"
  printf 'JPG-BYTES\n' > "$LOCAL/k2b-remote/workspace/uploads/1774182948381_file.jpg"
  out="$(run_detect "$LOCAL" "$REMOTE")"
  assert_detect "k2b-remote workspace upload scratch ignored (not a code-category change)" "" "$out"
}

# ---------------------------------------------------------------------------
# Scenario 18: detect_changes and sync_code MUST carry identical k2b-remote
# exclude lists. The script's own line-143 invariant says the dry-run flags
# must mirror the real-sync flags or detection lies (it would report no code
# drift while the real sync ships different content, or vice versa). This is a
# static guard: extract both exclude lists from the script and compare.
# ---------------------------------------------------------------------------
{
  # Exact, order-sensitive sequence (NOT a sorted set) compared against the
  # known-good expected value, so an order divergence OR a broken/partial grep
  # extraction both fail loudly -- a brittle pattern that yields a partial list
  # cannot silently pass the equality check.
  expected_excl="--exclude node_modules --exclude dist --exclude store --exclude .env --exclude /workspace/"
  detect_excl="$(grep -A1 'rsync_has_changes "\$LOCAL_BASE/k2b-remote/"' "$SCRIPT" \
    | grep -oE '\-\-exclude [A-Za-z0-9._/-]+' | paste -sd' ' -)"
  sync_excl="$(awk '/^sync_code\(\)/,/^}/' "$SCRIPT" \
    | grep -oE '\-\-exclude [A-Za-z0-9._/-]+' | paste -sd' ' -)"
  if [ "$detect_excl" = "$expected_excl" ] && [ "$sync_excl" = "$expected_excl" ]; then
    echo "  PASS: k2b-remote exclude lists match expected + identical (line-143 invariant)"
    PASS=$((PASS + 1))
  else
    echo "  FAIL: k2b-remote exclude list drift (line-143 mirror invariant)"
    echo "    expected: $expected_excl"
    echo "    detect:   $detect_excl"
    echo "    sync:     $sync_excl"
    FAIL=$((FAIL + 1))
  fi
}

# ---------------------------------------------------------------------------
# Scenario 19: workspace scratch present on BOTH machines with different
# content -- the real steady state, since each machine collects its own
# Telegram uploads independently. Must still NOT flag the code category.
# Scenario 17 covers first-run (MacBook has, Mini doesn't); this covers the
# ongoing two-machine drift case.
# ---------------------------------------------------------------------------
{
  LOCAL="$(mktmp)"
  REMOTE="$(mktmp)"
  build_tree "$LOCAL" "v2"
  build_tree "$REMOTE" "v2"
  mkdir -p "$LOCAL/k2b-remote/workspace/uploads" "$REMOTE/k2b-remote/workspace/uploads"
  printf 'MACBOOK-OGG\n' > "$LOCAL/k2b-remote/workspace/uploads/aaa_file.ogg"
  printf 'MINI-OGG\n'    > "$REMOTE/k2b-remote/workspace/uploads/bbb_file.ogg"
  out="$(run_detect "$LOCAL" "$REMOTE")"
  assert_detect "k2b-remote workspace differing on both sides still not code drift" "" "$out"
}

# ---------------------------------------------------------------------------
# Scenario 20: a regular FILE named workspace (not the runtime dir) at the
# k2b-remote root IS code drift. The exclude is `/workspace/` (trailing slash =
# directory only), so a file named workspace must still sync + flag code. This
# guards the directory-only intent: a regression to a bare `--exclude workspace`
# would wrongly hide such a file and fail this test.
# ---------------------------------------------------------------------------
{
  LOCAL="$(mktmp)"
  REMOTE="$(mktmp)"
  build_tree "$LOCAL" "v2"
  build_tree "$REMOTE" "v2"
  printf 'i am a source file, not a scratch dir\n' > "$LOCAL/k2b-remote/workspace"
  out="$(run_detect "$LOCAL" "$REMOTE")"
  assert_detect "k2b-remote file named workspace still flags code (dir-only exclude)" "code" "$out"
}

echo ""
echo "Results: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ] || exit 1
