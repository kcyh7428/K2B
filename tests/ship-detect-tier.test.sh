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

test_cli_wrapper_success_with_default_config() {
  local repo
  repo="$(mktmp)"
  build_fixture_repo "$repo"
  # Install classifier into repo and commit it (so the only "change" below is code.py)
  mkdir -p "$repo/scripts/lib"
  cp "$SCRIPT" "$repo/scripts/ship-detect-tier.py"
  cp "$LIB_DIR/tier_detection.py" "$repo/scripts/lib/tier_detection.py"
  cat > "$repo/scripts/tier3-paths.yml" <<'YAML'
paths: []
YAML
  chmod +x "$repo/scripts/ship-detect-tier.py"
  (cd "$repo" && git add scripts && git commit -q -m "install classifier")

  # Now make the one change to be classified
  (cd "$repo" && printf 'x=1\n' > code.py)

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
  mkdir -p "$repo/scripts/lib"
  cp "$SCRIPT" "$repo/scripts/ship-detect-tier.py"
  cp "$LIB_DIR/tier_detection.py" "$repo/scripts/lib/tier_detection.py"
  chmod +x "$repo/scripts/ship-detect-tier.py"
  (cd "$repo" && git add scripts && git commit -q -m "install classifier")

  (cd "$repo" && printf 'x=1\n' > code.py)

  local altconfig="$(mktmp)/alt.yml"
  cat > "$altconfig" <<'YAML'
paths:
  - "code.py"
YAML

  local out
  out=$(cd "$repo" && ./scripts/ship-detect-tier.py --config "$altconfig")
  echo "$out" | grep -q "^tier: 3$" || fail "--config override should land tier 3; got: $out"
  echo "PASS: test_cli_wrapper_explicit_config_flag"
}

test_cli_wrapper_success_with_default_config
test_cli_wrapper_missing_default_config_exits_1
test_cli_wrapper_outside_git_repo_fails
test_cli_wrapper_explicit_config_flag

test_is_tier_1_doc_unit_cases() {
  # MiniMax Checkpoint 2 MEDIUM-2: direct unit tests for _is_tier_1_doc
  # edge cases (previously only tested end-to-end via call_classifier).
  local out
  out=$(PYTHONPATH="$LIB_DIR" python3 -c "
import sys
sys.path.insert(0, r'$LIB_DIR')
from tier_detection import _is_tier_1_doc
cases = [
    ('.claude/skills/foo/SKILL.md', True),
    ('wiki/concepts/thing.md', True),
    ('CLAUDE.md', True),
    ('README.md', True),
    ('foo.md', False),             # .md at root, not CLAUDE/README exact
    ('foo.md.bak', False),         # not ending in .md
    ('readme.md', False),          # lowercase, not exact README.md
    ('.claude/skills/foo/helper.py', False),  # not .md
    ('wiki/concepts/thing.txt', False),        # not .md
]
for path, expected in cases:
    got = _is_tier_1_doc(path)
    assert got == expected, f'FAIL: {path!r} expected {expected}, got {got}'
print('ok')
")
  [ "$out" = "ok" ] || fail "_is_tier_1_doc unit cases failed: $out"
  echo "PASS: test_is_tier_1_doc_unit_cases"
}

test_is_tier_0_path_unit_cases() {
  # Symmetry: direct unit tests for _is_tier_0_path edge cases.
  local out
  out=$(PYTHONPATH="$LIB_DIR" python3 -c "
import sys
sys.path.insert(0, r'$LIB_DIR')
from tier_detection import _is_tier_0_path
cases = [
    ('K2B-Vault/raw/foo.md', True),
    ('plans/2026-04-19_thing.md', True),
    ('.claude/plans/foo.md', True),
    ('DEVLOG.md', True),
    ('devlog.md', False),          # lowercase, not exact DEVLOG.md
    ('plans-archive/foo.md', False),  # not under plans/
    ('.claude/skills/foo/SKILL.md', False),  # .claude/skills/ is Tier 1
]
for path, expected in cases:
    got = _is_tier_0_path(path)
    assert got == expected, f'FAIL: {path!r} expected {expected}, got {got}'
print('ok')
")
  [ "$out" = "ok" ] || fail "_is_tier_0_path unit cases failed: $out"
  echo "PASS: test_is_tier_0_path_unit_cases"
}

test_rule_ordering_tier_3_allowlist_wins_over_docs() {
  # Invariant: allowlist (rule 2) fires before docs rule (rule 3).
  # A path that is BOTH a tier-3 allowlist hit AND a tier-1 doc should
  # be Tier 3 (allowlist wins).
  local repo
  repo="$(mktmp)"
  build_fixture_repo "$repo"
  (cd "$repo" && printf '# allowlisted doc\n' > CLAUDE.md)  # normally Tier 1

  local config="$(mktmp)/tier3-paths.yml"
  cat > "$config" <<'YAML'
paths:
  - "CLAUDE.md"
YAML

  local out
  out=$(call_classifier "$repo" "$config")
  echo "$out" | grep -q "tier:3" || fail "allowlist CLAUDE.md should be tier 3 (allowlist wins over docs); got: $out"
  echo "PASS: test_rule_ordering_tier_3_allowlist_wins_over_docs"
}

test_is_tier_1_doc_unit_cases
test_is_tier_0_path_unit_cases
test_rule_ordering_tier_3_allowlist_wins_over_docs

# ---------- Staged-vs-working-tree conflation (Ship 2 commit 1) ----------
# 2026-05-05: add explicit "staged" mode capability to gather_tree_state
# and classify_tier. Default behavior unchanged ("working-tree") to avoid
# the under-classification risk Codex flagged on the first revision: if
# the staged set is stale or only-partial, classifying staged-only would
# silently miss session changes that /ship step 5 will stage later.
# The staged mode is opt-in for callers that pre-stage the EXACT commit
# set and want to ignore unrelated dirty noise.

test_default_mode_is_working_tree_classifies_full_dirty_tree() {
  # Codex regression: a stale staged file MUST NOT cause the default
  # classifier to scope to the staged set, leaving unstaged session code
  # invisible. With default mode, the classifier sees BOTH staged and
  # unstaged files (this is the original pre-2026-05-05 contract).
  local repo
  repo="$(mktmp)"
  build_fixture_repo "$repo"

  # Pre-existing staged Tier-0 docs (e.g., leftover from a half-attempted commit)
  mkdir -p "$repo/.claude/plans"
  (cd "$repo" && printf 'plan\n' > .claude/plans/old.md)
  (cd "$repo" && git add .claude/plans/old.md)

  # Unstaged session code change
  (cd "$repo" && printf 'def real_session_code(): pass\n' > unstaged_code.py)

  local config="$(mktmp)/tier3-paths.yml"
  cat > "$config" <<'YAML'
paths: []
YAML

  local out
  out=$(call_classifier "$repo" "$config")
  # Default mode = working-tree. Both files visible. Tier 2 (1 plans file
  # in Tier-0 set + 1 code file -> not all-Tier-0, falls through to Tier 2
  # default since under scale thresholds).
  echo "$out" | grep -q "tier:2" || fail "default working-tree mode should classify staged + unstaged together (no under-review); got: $out"
  echo "PASS: test_default_mode_is_working_tree_classifies_full_dirty_tree"
}

test_explicit_working_tree_mode_sees_staged_and_unstaged() {
  # Symmetric to the default test, but invoking gather_tree_state directly
  # with mode='working-tree' to lock the contract at the function level.
  local repo
  repo="$(mktmp)"
  build_fixture_repo "$repo"

  (cd "$repo" && python3 -c "print('\n'.join(['x=1'] * 250))" > unrelated.py)
  mkdir -p "$repo/.claude/skills/k2b-test"
  (cd "$repo" && printf '# small\n' > .claude/skills/k2b-test/SKILL.md)
  (cd "$repo" && git add .claude/skills/k2b-test/SKILL.md)

  local out
  out=$(PYTHONPATH="$LIB_DIR" python3 -c "
import sys
sys.path.insert(0, r'$LIB_DIR')
from tier_detection import gather_tree_state
state = gather_tree_state(repo_root=r'$repo', mode='working-tree')
print('files:', sorted(state['files']))
print('total_loc:', state['total_loc'])
")
  echo "$out" | grep -q "'unrelated.py'" || fail "working-tree mode must include unstaged file; got: $out"
  echo "$out" | grep -q "'.claude/skills/k2b-test/SKILL.md'" || fail "working-tree mode must include staged file too; got: $out"
  echo "PASS: test_explicit_working_tree_mode_sees_staged_and_unstaged"
}

test_explicit_staged_mode_scopes_to_staged_only() {
  # Opt-in capability: when caller passes mode='staged', only the staged
  # set is classified. This is what /ship will use once step 3 stages
  # files first (separate follow-up commit on the SKILL.md).
  local repo
  repo="$(mktmp)"
  build_fixture_repo "$repo"

  # Unrelated 250-line dirty file (not staged) -- the "noise" that would
  # otherwise force Tier 3 in default mode.
  (cd "$repo" && python3 -c "print('\n'.join(['x=1'] * 250))" > unrelated.py)

  # Staged: 1-line docs change
  mkdir -p "$repo/.claude/skills/k2b-test"
  (cd "$repo" && printf '# small\n' > .claude/skills/k2b-test/SKILL.md)
  (cd "$repo" && git add .claude/skills/k2b-test/SKILL.md)

  local config="$(mktmp)/tier3-paths.yml"
  cat > "$config" <<'YAML'
paths: []
YAML

  local out
  out=$(PYTHONPATH="$LIB_DIR" python3 -c "
import sys
sys.path.insert(0, r'$LIB_DIR')
from tier_detection import classify_tier
tier, reason = classify_tier(repo_root=r'$repo', tier3_config_path=r'$config', mode='staged')
print(f'tier:{tier} reason:{reason}')
")
  echo "$out" | grep -q "tier:1" || fail "explicit staged mode should classify only the 1-line docs change as tier 1; got: $out"
  echo "PASS: test_explicit_staged_mode_scopes_to_staged_only"
}

test_explicit_staged_mode_ignores_unstaged_files() {
  # Direct gather_tree_state assertion that staged mode never includes
  # unstaged files.
  local repo
  repo="$(mktmp)"
  build_fixture_repo "$repo"

  (cd "$repo" && printf 'unstaged\n' > unstaged.py)
  (cd "$repo" && printf 'staged\n' > staged.py)
  (cd "$repo" && git add staged.py)

  local out
  out=$(PYTHONPATH="$LIB_DIR" python3 -c "
import sys
sys.path.insert(0, r'$LIB_DIR')
from tier_detection import gather_tree_state
state = gather_tree_state(repo_root=r'$repo', mode='staged')
print('files:', sorted(state['files']))
")
  echo "$out" | grep -q "'staged.py'" || fail "staged mode must include staged.py; got: $out"
  if echo "$out" | grep -q "'unstaged.py'"; then
    fail "staged mode must NOT include unstaged.py; got: $out"
  fi
  echo "PASS: test_explicit_staged_mode_ignores_unstaged_files"
}

test_explicit_staged_mode_allowlist_hit_still_tier_3() {
  # Staged file in the allowlist must still trigger Tier 3 even with
  # other unstaged dirty files. Allowlist semantics preserved within
  # the staged scope.
  local repo
  repo="$(mktmp)"
  build_fixture_repo "$repo"

  (cd "$repo" && printf 'docs\n' > random.md)

  mkdir -p "$repo/scripts/lib"
  (cd "$repo" && printf 'def f(): pass\n' > scripts/lib/minimax_review.py)
  (cd "$repo" && git add scripts/lib/minimax_review.py)

  local config="$(mktmp)/tier3-paths.yml"
  cat > "$config" <<'YAML'
paths:
  - "scripts/lib/minimax_review.py"
YAML

  local out
  out=$(PYTHONPATH="$LIB_DIR" python3 -c "
import sys
sys.path.insert(0, r'$LIB_DIR')
from tier_detection import classify_tier
tier, reason = classify_tier(repo_root=r'$repo', tier3_config_path=r'$config', mode='staged')
print(f'tier:{tier} reason:{reason}')
")
  echo "$out" | grep -q "tier:3" || fail "staged-mode allowlist hit should be tier 3 regardless of unstaged noise; got: $out"
  echo "PASS: test_explicit_staged_mode_allowlist_hit_still_tier_3"
}

test_unknown_mode_raises_value_error() {
  # Codex follow-up: catch typos in the mode parameter. ValueError must
  # propagate (the CLI catches it and exits 1 -> caller falls back to
  # Tier 3).
  local repo
  repo="$(mktmp)"
  build_fixture_repo "$repo"

  if PYTHONPATH="$LIB_DIR" python3 -c "
import sys
sys.path.insert(0, r'$LIB_DIR')
from tier_detection import gather_tree_state
gather_tree_state(repo_root=r'$repo', mode='auto')
" 2>/dev/null; then
    fail "unknown mode 'auto' (the rejected design) should raise ValueError"
  fi
  echo "PASS: test_unknown_mode_raises_value_error"
}

test_cli_wrapper_mode_staged_flag() {
  # CLI exposes --mode staged as opt-in for callers that pre-stage.
  local repo
  repo="$(mktmp)"
  build_fixture_repo "$repo"
  mkdir -p "$repo/scripts/lib"
  cp "$SCRIPT" "$repo/scripts/ship-detect-tier.py"
  cp "$LIB_DIR/tier_detection.py" "$repo/scripts/lib/tier_detection.py"
  cat > "$repo/scripts/tier3-paths.yml" <<'YAML'
paths: []
YAML
  chmod +x "$repo/scripts/ship-detect-tier.py"
  (cd "$repo" && git add scripts && git commit -q -m "install classifier")

  # Unrelated 250-LOC unstaged + 1-line staged docs
  (cd "$repo" && python3 -c "print('\n'.join(['x=1'] * 250))" > unrelated.py)
  mkdir -p "$repo/.claude/skills/k2b-test"
  (cd "$repo" && printf '# small\n' > .claude/skills/k2b-test/SKILL.md)
  (cd "$repo" && git add .claude/skills/k2b-test/SKILL.md)

  # Default mode (working-tree): both files visible, 250 LOC -> tier 3
  local default_out
  default_out=$(cd "$repo" && ./scripts/ship-detect-tier.py)
  echo "$default_out" | grep -q "^tier: 3$" || fail "default mode should see full dirty tree -> tier 3; got: $default_out"

  # --mode staged: only staged file, 1 line of docs -> tier 1
  local staged_out
  staged_out=$(cd "$repo" && ./scripts/ship-detect-tier.py --mode staged)
  echo "$staged_out" | grep -q "^tier: 1$" || fail "--mode staged should scope to staged docs -> tier 1; got: $staged_out"

  echo "PASS: test_cli_wrapper_mode_staged_flag"
}

test_cli_wrapper_mode_invalid_rejects() {
  # Argparse choices=("staged","working-tree") rejects bogus values.
  local repo
  repo="$(mktmp)"
  build_fixture_repo "$repo"
  mkdir -p "$repo/scripts/lib"
  cp "$SCRIPT" "$repo/scripts/ship-detect-tier.py"
  cp "$LIB_DIR/tier_detection.py" "$repo/scripts/lib/tier_detection.py"
  cat > "$repo/scripts/tier3-paths.yml" <<'YAML'
paths: []
YAML
  chmod +x "$repo/scripts/ship-detect-tier.py"
  (cd "$repo" && printf 'x=1\n' > code.py)

  if (cd "$repo" && ./scripts/ship-detect-tier.py --mode auto) 2>/dev/null; then
    fail "--mode auto (rejected design) should be refused by argparse"
  fi
  echo "PASS: test_cli_wrapper_mode_invalid_rejects"
}

test_staged_mode_fails_closed_on_unmerged_or_unknown_status() {
  # Codex pass-2 HIGH: classifier IS the guardrail. An unmerged ("U")
  # entry in the staged index, or any unknown name-status code, must
  # raise (CLI then exits 1 -> caller falls back to Tier 3) rather than
  # silently skipping. Test by monkey-patching _run_git to return a
  # crafted U token (creating a real merge conflict in a bash fixture
  # is flaky; the parser-level test is the load-bearing assertion).
  local out
  out=$(PYTHONPATH="$LIB_DIR" python3 -c "
import sys
sys.path.insert(0, r'$LIB_DIR')
import tier_detection
# Simulate 'git diff --cached --name-status -z' output containing an
# unmerged entry: 'U\\0path/with/conflict.py\\0'.
def fake_run_git(*args, cwd):
    if args == ('diff', '--cached', '--name-status', '-z'):
        return 'U\x00path/with/conflict.py\x00'
    if args == ('diff', '--cached', '--numstat'):
        return ''
    raise AssertionError(f'unexpected git call: {args}')
tier_detection._run_git = fake_run_git
try:
    tier_detection._gather_staged_state(__import__('pathlib').Path('/tmp'))
    print('NO_RAISE')
except ValueError as exc:
    msg = str(exc)
    if 'U' in msg and 'conflict.py' in msg:
        print('RAISED_OK')
    else:
        print(f'WRONG_MSG: {msg}')
")
  [ "$out" = "RAISED_OK" ] || fail "staged mode must raise on U/unknown code; got: $out"
  echo "PASS: test_staged_mode_fails_closed_on_unmerged_or_unknown_status"
}

test_staged_mode_cli_unmerged_exits_1_fail_safe() {
  # End-to-end: a real unmerged index entry causes the CLI to exit 1,
  # which /ship's step 3a treats as Tier 3 fail-safe. Built via genuine
  # merge-conflict commit chain (no plumbing tricks).
  local repo
  repo="$(mktmp)"
  mkdir -p "$repo/scripts/lib"
  cp "$SCRIPT" "$repo/scripts/ship-detect-tier.py"
  cp "$LIB_DIR/tier_detection.py" "$repo/scripts/lib/tier_detection.py"
  cat > "$repo/scripts/tier3-paths.yml" <<'YAML'
paths: []
YAML
  chmod +x "$repo/scripts/ship-detect-tier.py"

  (
    cd "$repo" || exit 1
    git init -q -b main
    git config user.email "test@example.com"
    git config user.name "test"
    printf 'shared\nbase\n' > conflicted.txt
    git add scripts conflicted.txt
    git commit -q -m "init"

    git checkout -q -b branch-a
    printf 'shared\nbranch-a-line\n' > conflicted.txt
    git commit -q -am "branch-a"

    git checkout -q main
    git checkout -q -b branch-b
    printf 'shared\nbranch-b-line\n' > conflicted.txt
    git commit -q -am "branch-b"

    git merge -q --no-edit branch-a >/dev/null 2>&1 || true
    # Now conflicted.txt has merge markers + index has unmerged entry (U).
  )

  if (cd "$repo" && ./scripts/ship-detect-tier.py --mode staged) 2>/dev/null; then
    fail "staged mode with unmerged index should exit 1 (fail-safe to Tier 3)"
  fi
  echo "PASS: test_staged_mode_cli_unmerged_exits_1_fail_safe"
}

test_default_mode_is_working_tree_classifies_full_dirty_tree
test_explicit_working_tree_mode_sees_staged_and_unstaged
test_explicit_staged_mode_scopes_to_staged_only
test_explicit_staged_mode_ignores_unstaged_files
test_explicit_staged_mode_allowlist_hit_still_tier_3
test_unknown_mode_raises_value_error
test_cli_wrapper_mode_staged_flag
test_cli_wrapper_mode_invalid_rejects
test_staged_mode_fails_closed_on_unmerged_or_unknown_status
test_staged_mode_cli_unmerged_exits_1_fail_safe

echo "all tests passed"
