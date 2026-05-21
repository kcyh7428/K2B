#!/usr/bin/env bash
# Regression tests for scripts/yt-cookies-json-to-netscape.py.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SCRIPT="$REPO_ROOT/scripts/yt-cookies-json-to-netscape.py"

TMPROOT="$(mktemp -d)"
trap 'rm -rf "$TMPROOT"' EXIT

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

echo "=== yt-cookies-json-to-netscape.test.sh ==="

input="$TMPROOT/cookies.json"
output="$TMPROOT/youtube-cookies.txt"
cat > "$input" <<'JSON'
[
  {
    "domain": ".youtube.com",
    "hostOnly": false,
    "path": "/",
    "secure": true,
    "expirationDate": 1893456000.7,
    "name": "SID",
    "value": "sid-value"
  },
  {
    "domain": "youtube.com",
    "path": "/watch",
    "secure": false,
    "session": true,
    "name": "YSC",
    "value": "ysc-value"
  },
  {
    "domain": ".example.com",
    "hostOnly": false,
    "path": "/",
    "secure": true,
    "expirationDate": 1893456000,
    "name": "SID",
    "value": "example-value"
  }
]
JSON

python3 "$SCRIPT" "$input" "$output" > "$TMPROOT/stdout.txt" 2> "$TMPROOT/stderr.txt"

[[ -f "$output" ]] || fail "expected output cookie file"
grep -q '^# Netscape HTTP Cookie File$' "$output" || fail "expected Netscape header"
grep -q $'^.youtube.com\tTRUE\t/\tTRUE\t1893456000\tSID\tsid-value$' "$output" || fail "expected persistent cookie line"
grep -q $'^youtube.com\tFALSE\t/watch\tFALSE\t0\tYSC\tysc-value$' "$output" || fail "expected missing hostOnly on host domain to stay host-only"
! grep -q 'example-value' "$output" || fail "non-YouTube cookies must be filtered"

mode=$(python3 - "$output" <<'PY'
import os, stat, sys
print(oct(stat.S_IMODE(os.stat(sys.argv[1]).st_mode)))
PY
)
[[ "$mode" == "0o600" ]] || fail "expected output mode 0600, got $mode"
grep -q '^Converted 2 cookies ' "$TMPROOT/stdout.txt" || fail "expected conversion summary for filtered cookies"
! grep -q 'sid-value\|ysc-value' "$TMPROOT/stdout.txt" || fail "cookie values must not be printed"
! grep -q 'sid-value\|ysc-value' "$TMPROOT/stderr.txt" || fail "cookie values must not be printed on stderr"

echo "  PASS: JSON export converts to Netscape cookie file without printing values"
