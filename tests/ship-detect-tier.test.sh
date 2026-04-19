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
  # Codex omission: .claude/plans/ consistency with plans/.
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
  # Codex LOW #1: ** semantics -- trailing prefix match, nested path.
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
  local repo
  repo="$(mktmp)"
  build_fixture_repo "$repo"
  (cd "$repo" && printf 'x=1\n' > code.py)

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
  # Codex MEDIUM #3 regression: 250-line pure-docs commit must NOT fall
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
  echo "$out" | grep -q "tier:2" || fail "mixed docs+code should be tier 2; got: $out"
  echo "PASS: test_tier_1_mixed_docs_and_code_is_NOT_tier_1"
}

test_tier_1_skill_docs_only
test_tier_1_claude_md
test_tier_1_wiki_docs
test_tier_1_big_docs_still_tier_1_not_scale_tier_3
test_tier_1_mixed_docs_and_code_is_NOT_tier_1

echo "all tests passed"
