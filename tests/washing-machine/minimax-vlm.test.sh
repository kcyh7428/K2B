#!/usr/bin/env bash
# Tests for scripts/minimax-vlm.sh. Uses MINIMAX_VLM_MOCK=/path/to/mock-response.json
# to bypass the real API so the unit tests are deterministic.
#
# MINIMAX_VLM_CLAUDE points at a shell stub used when the VLM call fails and
# the --fallback path tries Opus vision.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/../.." && pwd)"
SCRIPT="$REPO/scripts/minimax-vlm.sh"
FIXDIR="$REPO/tests/washing-machine/fixtures/images"
TMP=$(mktemp -d)
trap "rm -rf $TMP" EXIT

# minimax-common.sh requires MINIMAX_API_KEY at source time; a test key is fine
# because MINIMAX_VLM_MOCK bypasses real network calls.
export MINIMAX_API_KEY="${MINIMAX_API_KEY:-test-key-for-unit-test}"

PASS=0
FAIL=0
assert() {
  if [ "$2" = "$3" ]; then
    PASS=$((PASS+1))
    echo "PASS $1"
  else
    FAIL=$((FAIL+1))
    echo "FAIL $1: got '$2' want '$3'"
  fi
}

assert_nonzero_exit() {
  local label="$1"; shift
  if "$@" >/dev/null 2>&1; then
    FAIL=$((FAIL+1))
    echo "FAIL $label: expected non-zero exit, got 0"
  else
    PASS=$((PASS+1))
    echo "PASS $label"
  fi
}

# --- Case 1: success with mock (content returned verbatim) ---
cat >"$TMP/mock-ok.json" <<'EOF'
{"base_resp":{"status_code":0,"status_msg":"ok"},"content":"TEST"}
EOF
out=$(MINIMAX_VLM_MOCK="$TMP/mock-ok.json" "$SCRIPT" --image "$FIXDIR/test-128.png" --prompt "Transcribe text." --job-name case1-ok 2>/dev/null)
assert "case1 success mock returns content" "$out" "TEST"

# --- Case 2: GIF rejected before any API call ---
assert_nonzero_exit "case2 gif rejected" env MINIMAX_VLM_MOCK="$TMP/mock-ok.json" "$SCRIPT" --image "$FIXDIR/invalid.gif" --prompt p --job-name case2-gif

# --- Case 3: missing file ---
assert_nonzero_exit "case3 missing file" env MINIMAX_VLM_MOCK="$TMP/mock-ok.json" "$SCRIPT" --image "$TMP/nope.png" --prompt p --job-name case3-missing

# --- Case 4: non-zero status_code triggers Opus fallback (mock) ---
cat >"$TMP/mock-fail.json" <<'EOF'
{"base_resp":{"status_code":1008,"status_msg":"quota exhausted"},"content":""}
EOF
cat >"$TMP/fake-claude" <<'EOF'
#!/usr/bin/env bash
# Fake claude binary that returns deterministic output. Ignores args.
echo "OPUS_TEST"
EOF
chmod +x "$TMP/fake-claude"
out=$(MINIMAX_VLM_MOCK="$TMP/mock-fail.json" MINIMAX_VLM_CLAUDE="$TMP/fake-claude" "$SCRIPT" --image "$FIXDIR/test-128.png" --prompt p --job-name case4-fallback --fallback auto 2>/dev/null)
assert "case4 opus fallback when status non-zero" "$out" "OPUS_TEST"

# --- Case 5: --fallback never refuses fallback and exits non-zero ---
assert_nonzero_exit "case5 no-fallback when status non-zero" env MINIMAX_VLM_MOCK="$TMP/mock-fail.json" "$SCRIPT" --image "$FIXDIR/test-128.png" --prompt p --job-name case5-never --fallback never

# --- Case 5b: VLM fails AND Opus fallback returns empty → exit 6 ---
cat >"$TMP/fake-claude-empty" <<'EOF'
#!/usr/bin/env bash
# Fake claude binary that returns empty. Triggers the "VLM and Opus both failed" path.
exit 0
EOF
chmod +x "$TMP/fake-claude-empty"
assert_nonzero_exit "case5b both providers fail" env MINIMAX_VLM_MOCK="$TMP/mock-fail.json" MINIMAX_VLM_CLAUDE="$TMP/fake-claude-empty" "$SCRIPT" --image "$FIXDIR/test-128.png" --prompt p --job-name case5b-both --fallback auto

# --- Case 5c: unparseable VLM response → parse-fail → Opus fallback succeeds ---
cat >"$TMP/mock-garbage.txt" <<'EOF'
<html>Gateway Timeout</html>
EOF
out=$(MINIMAX_VLM_MOCK="$TMP/mock-garbage.txt" MINIMAX_VLM_CLAUDE="$TMP/fake-claude" "$SCRIPT" --image "$FIXDIR/test-128.png" --prompt p --job-name case5c-parsefail --fallback auto 2>/dev/null)
assert "case5c parse-fail falls through to opus" "$out" "OPUS_TEST"

# --- Case 6: missing --image / --prompt / --job-name → usage error ---
assert_nonzero_exit "case6a missing image" env MINIMAX_VLM_MOCK="$TMP/mock-ok.json" "$SCRIPT" --prompt p --job-name case6a
assert_nonzero_exit "case6b missing prompt" env MINIMAX_VLM_MOCK="$TMP/mock-ok.json" "$SCRIPT" --image "$FIXDIR/test-128.png" --job-name case6b
assert_nonzero_exit "case6c missing job-name" env MINIMAX_VLM_MOCK="$TMP/mock-ok.json" "$SCRIPT" --image "$FIXDIR/test-128.png" --prompt p

echo "---"
echo "TOTAL PASS=$PASS  FAIL=$FAIL"
[ $FAIL -eq 0 ]
