#!/usr/bin/env bash
# Tests for scripts/washing-machine/extract-attachment.sh.
# Uses MINIMAX_VLM_MOCK to bypass the real VLM endpoint so the unit tests
# are deterministic and offline.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/../.." && pwd)"
SCRIPT="$REPO/scripts/washing-machine/extract-attachment.sh"
FIXDIR="$REPO/tests/washing-machine/fixtures/images"
TMP=$(mktemp -d)
trap "rm -rf $TMP" EXIT

# minimax-common.sh (sourced by minimax-vlm.sh) requires MINIMAX_API_KEY
export MINIMAX_API_KEY="${MINIMAX_API_KEY:-test-key-for-unit-test}"

PASS=0
FAIL=0
assert_contains() {
  local label="$1" haystack="$2" needle="$3"
  case "$haystack" in
    *"$needle"*) PASS=$((PASS+1)); echo "PASS $label" ;;
    *) FAIL=$((FAIL+1)); echo "FAIL $label: '$haystack' missing '$needle'" ;;
  esac
}
assert_eq() {
  if [ "$2" = "$3" ]; then PASS=$((PASS+1)); echo "PASS $1"; else FAIL=$((FAIL+1)); echo "FAIL $1: got '$2' want '$3'"; fi
}
assert_nonzero_exit() {
  local label="$1"; shift
  if "$@" >/dev/null 2>&1; then
    FAIL=$((FAIL+1)); echo "FAIL $label: expected non-zero"
  else
    PASS=$((PASS+1)); echo "PASS $label"
  fi
}

# --- Case 1: photo → VLM mock → normalized_text set, attachment_type=photo ---
cat >"$TMP/mock-ok.json" <<'EOF'
{"base_resp":{"status_code":0,"status_msg":"ok"},"content":"Dr. Lo Hak Keung\nTel: 2830 3709"}
EOF
export MINIMAX_VLM_MOCK="$TMP/mock-ok.json"
input='{"type":"photo","path":"'$FIXDIR'/dr-lo-card.png","message_ts":1711987200000}'
out=$(printf '%s' "$input" | "$SCRIPT" 2>/dev/null)
type=$(printf '%s' "$out" | python3 -c 'import json,sys; print(json.load(sys.stdin)["attachment_type"])')
text=$(printf '%s' "$out" | python3 -c 'import json,sys; print(json.load(sys.stdin)["normalized_text"])')
assert_eq "case1 photo: attachment_type" "$type" "photo"
assert_contains "case1 photo: text contains phone" "$text" "2830 3709"

# --- Case 2: text pass-through ---
input='{"type":"text","text":"Hello world","message_ts":1711987200000}'
out=$(printf '%s' "$input" | "$SCRIPT" 2>/dev/null)
text=$(printf '%s' "$out" | python3 -c 'import json,sys; print(json.load(sys.stdin)["normalized_text"])')
ptype=$(printf '%s' "$out" | python3 -c 'import json,sys; print(json.load(sys.stdin)["provider"])')
assert_eq "case2 text: pass-through text" "$text" "Hello world"
assert_eq "case2 text: provider" "$ptype" "passthrough"

# --- Case 3: GIF rejected (delegates rejection to minimax-vlm.sh) ---
input='{"type":"photo","path":"'$FIXDIR'/invalid.gif","message_ts":1711987200000}'
assert_nonzero_exit "case3 gif rejected" bash -c "printf '%s' '$input' | '$SCRIPT'"

# --- Case 4: unknown type ---
input='{"type":"unknown","message_ts":1711987200000}'
assert_nonzero_exit "case4 unknown type" bash -c "printf '%s' '$input' | '$SCRIPT'"

# --- Case 5: photo missing path → usage error ---
input='{"type":"photo","message_ts":1711987200000}'
assert_nonzero_exit "case5 photo missing path" bash -c "printf '%s' '$input' | '$SCRIPT'"

# --- Case 6: message_ts round-trips to output ---
input='{"type":"text","text":"hi","message_ts":1711987200123}'
out=$(printf '%s' "$input" | "$SCRIPT" 2>/dev/null)
ts=$(printf '%s' "$out" | python3 -c 'import json,sys; print(json.load(sys.stdin)["message_ts"])')
assert_eq "case6 message_ts round-trip" "$ts" "1711987200123"

# --- Case 7: text type without text field → empty normalized_text (not a crash) ---
input='{"type":"text","message_ts":1711987200000}'
out=$(printf '%s' "$input" | "$SCRIPT" 2>/dev/null)
text=$(printf '%s' "$out" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(repr(d["normalized_text"]))')
assert_eq "case7 text without text field" "$text" "''"

# --- Case 8: plain-text document (text/markdown) → passthrough ---
cat >"$TMP/doc.md" <<'EOF'
# Meeting notes
Discussed phone 2830 3709 with Dr. Lo.
EOF
input='{"type":"document","path":"'$TMP'/doc.md","message_ts":1711987200000}'
out=$(printf '%s' "$input" | "$SCRIPT" 2>/dev/null)
prov=$(printf '%s' "$out" | python3 -c 'import json,sys; print(json.load(sys.stdin)["provider"])')
text=$(printf '%s' "$out" | python3 -c 'import json,sys; print(json.load(sys.stdin)["normalized_text"])')
assert_eq "case8 md document provider" "$prov" "passthrough"
assert_contains "case8 md document text" "$text" "2830 3709"

# --- Case 9: binary document (PNG renamed) → exit 2, unsupported mime ---
cp "$FIXDIR/test-128.png" "$TMP/fake.bin"
input='{"type":"document","path":"'$TMP'/fake.bin","message_ts":1711987200000}'
assert_nonzero_exit "case9 binary document rejected" bash -c "printf '%s' '$input' | '$SCRIPT'"

# --- Case 10: missing document path ---
input='{"type":"document","message_ts":1711987200000}'
assert_nonzero_exit "case10 document missing path" bash -c "printf '%s' '$input' | '$SCRIPT'"

echo "---"
echo "TOTAL PASS=$PASS  FAIL=$FAIL"
[ $FAIL -eq 0 ]
