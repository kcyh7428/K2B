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

# Cookie guard: staged Netscape cookie files are rejected even when force-added
setup_repo
cat > youtube-cookies.txt <<'COOKIE'
# Netscape HTTP Cookie File
.youtube.com	TRUE	/	TRUE	1893456000	SID	secret
COOKIE
git add -f youtube-cookies.txt
EXIT=0
git commit -q -m "bad cookie file" 2>/dev/null || EXIT=$?
report "Cookie guard: Netscape YouTube cookie file is rejected" "1" "$EXIT"

# Cookie guard: HttpOnly-prefixed Netscape YouTube cookies are rejected
setup_repo
cat > httponly-cookies.txt <<'COOKIE'
# Netscape HTTP Cookie File
#HttpOnly_.youtube.com	TRUE	/	TRUE	1893456000	SID	secret
COOKIE
git add -f httponly-cookies.txt
EXIT=0
git commit -q -m "bad httponly cookie file" 2>/dev/null || EXIT=$?
report "Cookie guard: HttpOnly YouTube cookie file is rejected" "1" "$EXIT"

# Cookie guard: staged JSON exports with YouTube auth cookies are rejected
setup_repo
cat > www_youtube_com_cookies.json <<'JSON'
[
  {"domain": ".youtube.com", "name": "SID", "value": "secret"}
]
JSON
git add -f www_youtube_com_cookies.json
EXIT=0
git commit -q -m "bad cookie json" 2>/dev/null || EXIT=$?
report "Cookie guard: YouTube auth cookie JSON is rejected" "1" "$EXIT"

# Cookie guard: YouTube-domain cookie material is rejected even with non-auth names
setup_repo
cat > youtube-pref-cookie.txt <<'COOKIE'
# Netscape HTTP Cookie File
.youtube.com	TRUE	/	TRUE	1893456000	PREF	hl=en
COOKIE
git add -f youtube-pref-cookie.txt
EXIT=0
git commit -q -m "bad youtube pref cookie" 2>/dev/null || EXIT=$?
report "Cookie guard: YouTube-domain cookie rows are rejected by domain" "1" "$EXIT"

# Cookie guard still runs after K2B_ALLOW_LOG_APPEND override
setup_repo
cat > mixed-bad.sh <<'SCRIPT'
echo "something" >> wiki/log.md
SCRIPT
cat > youtube-cookies.txt <<'COOKIE'
# Netscape HTTP Cookie File
.youtube.com	TRUE	/	TRUE	1893456000	SID	secret
COOKIE
git add mixed-bad.sh
git add -f youtube-cookies.txt
EXIT=0
K2B_ALLOW_LOG_APPEND=1 git commit -q -m "override with cookie" 2>/dev/null || EXIT=$?
report "Cookie guard: log-append override does not bypass cookie scanner" "1" "$EXIT"

# Cookie guard: unrelated Netscape cookie jars are allowed by content guard
setup_repo
cat > non-youtube-cookies.txt <<'COOKIE'
# Netscape HTTP Cookie File
.example.com	TRUE	/	TRUE	1893456000	SID	not-youtube
COOKIE
git add -f non-youtube-cookies.txt
EXIT=0
git commit -q -m "non-youtube cookie jar" 2>/dev/null || EXIT=$?
report "Cookie guard: non-YouTube Netscape cookie file is allowed" "0" "$EXIT"

# Repo ignore rules should make the common accidental files untracked by default
cd "$REPO_ROOT"
git check-ignore -q youtube-cookies.txt
report "Gitignore: youtube-cookies.txt is ignored" "0" "$?"
git check-ignore -q www_youtube_com_cookies.json
report "Gitignore: exported cookie JSON is ignored" "0" "$?"

echo ""
echo "Results: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ] || exit 1
