#!/usr/bin/env bash
# Tests for .githooks/pre-commit (check 2: direct >> wiki/log.md append guard)
# Scenarios 4, 5, 6 from the Fix #8 plan.

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
  mkdir -p .githooks
  cp "$REPO_ROOT/.githooks/pre-commit" .githooks/pre-commit
  cp "$REPO_ROOT/.githooks/commit-msg" .githooks/commit-msg
  chmod 755 .githooks/pre-commit .githooks/commit-msg
  git config core.hooksPath .githooks
  echo "initial" > README.md
  git add README.md
  git commit -q -m "initial"
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

echo "=== pre-commit.test.sh ==="

# Scenario 4: commit with >> wiki/log.md append -> REJECT
setup_repo
cat > test-script.sh <<'SCRIPT'
echo "something" >> wiki/log.md
SCRIPT
git add test-script.sh
EXIT=0
git commit -q -m "bad append" 2>/dev/null || EXIT=$?
report "Scenario 4: direct >> wiki/log.md append is rejected" "1" "$EXIT"

# Scenario 5: commit with >> wiki/log.md AND Co-Shipped-By trailer -> STILL REJECT
setup_repo
cat > test-script.sh <<'SCRIPT'
echo "something" >> wiki/log.md
SCRIPT
git add test-script.sh
EXIT=0
git commit -q -m "$(cat <<'EOF'
feat: bad append with trailer

Co-Shipped-By: k2b-ship
EOF
)" 2>/dev/null || EXIT=$?
report "Scenario 5: trailer does NOT override log append check" "1" "$EXIT"

# Scenario 6: commit touching an unrelated file -> ALLOW
setup_repo
echo "harmless change" > unrelated.txt
git add unrelated.txt
EXIT=0
git commit -q -m "innocuous change" 2>/dev/null || EXIT=$?
report "Scenario 6: unrelated file commit passes" "0" "$EXIT"

# Bonus: K2B_ALLOW_LOG_APPEND=1 override allows the commit
setup_repo
cat > test-script.sh <<'SCRIPT'
echo "something" >> wiki/log.md
SCRIPT
git add test-script.sh
EXIT=0
K2B_ALLOW_LOG_APPEND=1 git commit -q -m "override append" 2>/dev/null || EXIT=$?
report "Bonus: K2B_ALLOW_LOG_APPEND=1 override allows commit" "0" "$EXIT"

echo ""
echo "Results: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ] || exit 1
