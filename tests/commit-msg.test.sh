#!/usr/bin/env bash
# Tests for .githooks/commit-msg (check 1: status edit trailer guard)
# Scenarios 1, 2, 3 from the Fix #8 plan.

set -euo pipefail

PASS=0
FAIL=0
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

cleanup() {
  if [ -n "${TMPDIR_TEST:-}" ] && [ -d "$TMPDIR_TEST" ]; then
    rm -rf "$TMPDIR_TEST"
  fi
}
trap cleanup EXIT

setup_repo() {
  TMPDIR_TEST=$(mktemp -d)
  cd "$TMPDIR_TEST"
  git init -q
  git config user.email "test@test.com"
  git config user.name "Test"
  mkdir -p .githooks wiki/concepts
  cp "$REPO_ROOT/.githooks/pre-commit" .githooks/pre-commit
  cp "$REPO_ROOT/.githooks/commit-msg" .githooks/commit-msg
  chmod 755 .githooks/pre-commit .githooks/commit-msg
  # Create initial feature file BEFORE enabling hooks
  cat > wiki/concepts/feature_test-feature.md <<'FEAT'
---
status: ideating
priority: high
---
# Test Feature
FEAT
  git add wiki/concepts/feature_test-feature.md
  git commit -q -m "initial feature file"
  # Now enable hooks for subsequent commits
  git config core.hooksPath .githooks
}

report() {
  local name="$1" expected="$2" actual="$3"
  if [ "$expected" = "$actual" ]; then
    echo "  PASS: $name"
    PASS=$((PASS + 1))
  else
    echo "  FAIL: $name (expected exit $expected, got $actual)"
    FAIL=$((FAIL + 1))
  fi
}

echo "=== commit-msg.test.sh ==="

# Scenario 1: status edit without trailer -> REJECT
setup_repo
sed -i.bak 's/status: ideating/status: shipped/' wiki/concepts/feature_test-feature.md
rm -f wiki/concepts/feature_test-feature.md.bak
git add wiki/concepts/feature_test-feature.md
EXIT=0
git commit -q -m "chore: update status" 2>/dev/null || EXIT=$?
report "Scenario 1: status edit without trailer is rejected" "1" "$EXIT"

# Scenario 2: status edit with Co-Shipped-By trailer -> ALLOW
setup_repo
sed -i.bak 's/status: ideating/status: shipped/' wiki/concepts/feature_test-feature.md
rm -f wiki/concepts/feature_test-feature.md.bak
git add wiki/concepts/feature_test-feature.md
EXIT=0
git commit -q -m "$(cat <<'EOF'
feat: ship test feature

Co-Shipped-By: k2b-ship
EOF
)" 2>/dev/null || EXIT=$?
report "Scenario 2: status edit with Co-Shipped-By trailer passes" "0" "$EXIT"

# Scenario 3: status edit with K2B_ALLOW_STATUS_EDIT=1 -> ALLOW with warning
setup_repo
sed -i.bak 's/status: ideating/status: shipped/' wiki/concepts/feature_test-feature.md
rm -f wiki/concepts/feature_test-feature.md.bak
git add wiki/concepts/feature_test-feature.md
EXIT=0
K2B_ALLOW_STATUS_EDIT=1 git commit -q -m "chore: manual status fix" 2>/dev/null || EXIT=$?
report "Scenario 3: K2B_ALLOW_STATUS_EDIT=1 override allows commit" "0" "$EXIT"

echo ""
echo "Results: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ] || exit 1
