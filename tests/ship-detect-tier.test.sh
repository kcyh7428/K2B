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
  # 155 LOC (7cd1f6c-shape): must NOT trip scale rule at 200 threshold.
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

# Evidence-case regressions (the four commits from feature spec Problem section)

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
  # Production shape: real 7cd1f6c touched scripts/promote-learnings.py
  # which IS in the Tier 3 allowlist (memory persistence). Allowlist wins.
  # Per Codex MEDIUM #2: split from the calibration test.
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
  # K2Bi befc26b: multi-file runtime feature. 4 files AND 320 LOC -> Tier 3.
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
  echo "$out" | grep -q "tier:3" || fail "befc26b should be tier 3; got: $out"
  echo "PASS: test_evidence_k2bi_befc26b_multi_file_runtime"
}

test_evidence_k2bi_530eb81_trading_path_allowlist() {
  # K2Bi 530eb81: trading-order submit path. Small change, Tier 3 via allowlist.
  # K2Bi fork of tier3-paths.yml would include src/orders/**.
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

echo "all tests passed"
