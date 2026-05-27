#!/usr/bin/env bash
# Tests for scripts/gptsapi-vlm.sh. Uses GPTSAPI_VLM_MOCK or a mocked curl
# binary so the unit tests are deterministic and offline.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/../.." && pwd)"
SCRIPT="$REPO/scripts/gptsapi-vlm.sh"
FIXDIR="$REPO/tests/washing-machine/fixtures/images"
TMP=$(mktemp -d)
trap "rm -rf $TMP" EXIT

export GPTSAPI_KEY="${GPTSAPI_KEY:-test-gptsapi-key-for-unit-test}"
export GPTSAPI_VLM_STATE_DIR="$TMP/state"

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

assert_contains() {
  local label="$1" haystack="$2" needle="$3"
  case "$haystack" in
    *"$needle"*) PASS=$((PASS+1)); echo "PASS $label" ;;
    *) FAIL=$((FAIL+1)); echo "FAIL $label: '$haystack' missing '$needle'" ;;
  esac
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

# --- Case 1: success with GPTsAPI-shaped mock response ---
cat >"$TMP/mock-ok.json" <<'EOF'
{"choices":[{"message":{"content":"TEST\n中文"}}]}
EOF
out=$(GPTSAPI_VLM_MOCK="$TMP/mock-ok.json" "$SCRIPT" --image "$FIXDIR/test-128.png" --prompt "Transcribe text." --job-name case1-ok 2>/dev/null)
assert "case1 success mock returns content" "$out" $'TEST\n中文'

# --- Case 2: missing file is an argv/env error ---
assert_nonzero_exit "case2 missing file" env GPTSAPI_VLM_MOCK="$TMP/mock-ok.json" "$SCRIPT" --image "$TMP/nope.png" --prompt p --job-name case2-missing

# --- Case 3: empty model content exits non-zero instead of silently succeeding ---
cat >"$TMP/mock-empty.json" <<'EOF'
{"choices":[{"message":{"content":"   "}}]}
EOF
assert_nonzero_exit "case3 empty response rejected" env GPTSAPI_VLM_MOCK="$TMP/mock-empty.json" "$SCRIPT" --image "$FIXDIR/test-128.png" --prompt p --job-name case3-empty

# --- Case 4: refusal-ish content exits non-zero ---
cat >"$TMP/mock-refusal.json" <<'EOF'
{"choices":[{"message":{"content":"I cannot extract text from this image."}}]}
EOF
assert_nonzero_exit "case4 refusal rejected" env GPTSAPI_VLM_MOCK="$TMP/mock-refusal.json" "$SCRIPT" --image "$FIXDIR/test-128.png" --prompt p --job-name case4-refusal

# --- Case 4b: whole-output Markdown fences are stripped from OCR text ---
cat >"$TMP/mock-fenced.json" <<'EOF'
{"choices":[{"message":{"content":"```\nTalentSignals Q3 review\n```"}}]}
EOF
out=$(GPTSAPI_VLM_MOCK="$TMP/mock-fenced.json" GPTSAPI_VLM_STATE_DIR="$TMP/state-fenced" "$SCRIPT" --image "$FIXDIR/test-128.png" --prompt p --job-name case4b-fenced 2>/dev/null)
assert "case4b strips whole-output fences" "$out" "TalentSignals Q3 review"

# --- Case 4c: language-tag fences and trailing newline are stripped ---
cat >"$TMP/mock-fenced-lang.json" <<'EOF'
{"choices":[{"message":{"content":"```text\n充值记录\n当前余额（元）\n```\n"}}]}
EOF
out=$(GPTSAPI_VLM_MOCK="$TMP/mock-fenced-lang.json" GPTSAPI_VLM_STATE_DIR="$TMP/state-fenced-lang" "$SCRIPT" --image "$FIXDIR/test-128.png" --prompt p --job-name case4c-fenced-lang 2>/dev/null)
assert "case4c strips language fences" "$out" $'充值记录\n当前余额（元）'

# --- Case 5: real request body uses chat-completions image_url shape ---
mkdir -p "$TMP/bin"
cat >"$TMP/bin/curl" <<'EOF'
#!/usr/bin/env bash
body=""
if [ -n "${GPTSAPI_VLM_FAIL_ONCE_STATE:-}" ]; then
  count=0
  if [ -f "$GPTSAPI_VLM_FAIL_ONCE_STATE" ]; then
    count="$(cat "$GPTSAPI_VLM_FAIL_ONCE_STATE")"
  fi
  count=$((count + 1))
  printf '%s\n' "$count" > "$GPTSAPI_VLM_FAIL_ONCE_STATE"
  if [ "$count" -eq 1 ]; then
    echo "curl: (35) LibreSSL SSL_connect: SSL_ERROR_SYSCALL" >&2
    exit 35
  fi
fi
if [ -n "${GPTSAPI_VLM_FAIL_ALWAYS_STATE:-}" ]; then
  count=0
  if [ -f "$GPTSAPI_VLM_FAIL_ALWAYS_STATE" ]; then
    count="$(cat "$GPTSAPI_VLM_FAIL_ALWAYS_STATE")"
  fi
  count=$((count + 1))
  printf '%s\n' "$count" > "$GPTSAPI_VLM_FAIL_ALWAYS_STATE"
  echo "curl: (35) LibreSSL SSL_connect: SSL_ERROR_SYSCALL" >&2
  exit 35
fi
while [ "$#" -gt 0 ]; do
  case "$1" in
    -d)
      body="$2"
      shift 2
      ;;
    --data-binary)
      if [[ "$2" == @* ]]; then
        body="$(cat "${2#@}")"
      else
        body="$2"
      fi
      shift 2
      ;;
    *)
      shift
      ;;
  esac
done
printf '%s' "$body" > "$GPTSAPI_VLM_CAPTURE_BODY"
printf '{"choices":[{"message":{"content":"CAPTURED"}}]}'
EOF
chmod +x "$TMP/bin/curl"
capture="$TMP/body.json"
out=$(PATH="$TMP/bin:$PATH" GPTSAPI_VLM_CAPTURE_BODY="$capture" GPTSAPI_VLM_STATE_DIR="$TMP/state2" "$SCRIPT" --image "$FIXDIR/test-128.png" --prompt "Extract only text." --job-name case5-body 2>/dev/null)
assert "case5 mocked curl returns content" "$out" "CAPTURED"
model=$(python3 - "$capture" <<'PY'
import json, sys
d = json.load(open(sys.argv[1], encoding="utf-8"))
print(d["model"])
PY
)
assert "case5 default model" "$model" "gpt-4o-mini"
body_summary=$(python3 - "$capture" <<'PY'
import json, sys
d = json.load(open(sys.argv[1], encoding="utf-8"))
content = d["messages"][0]["content"]
print(content[0]["type"])
print(content[0]["text"])
print(content[1]["type"])
print(content[1]["image_url"]["url"][:22])
PY
)
assert_contains "case5 body has prompt" "$body_summary" "Extract only text."
assert_contains "case5 body has image_url data url" "$body_summary" "data:image/png;base64,"

# --- Case 5b: transient curl failures are retried once ---
retry_state="$TMP/retry-count"
out=$(PATH="$TMP/bin:$PATH" GPTSAPI_VLM_CAPTURE_BODY="$capture" GPTSAPI_VLM_FAIL_ONCE_STATE="$retry_state" GPTSAPI_VLM_RETRY_DELAY_SECONDS=1 GPTSAPI_VLM_STATE_DIR="$TMP/state2b" "$SCRIPT" --image "$FIXDIR/test-128.png" --prompt "Extract only text." --job-name case5b-retry 2>/dev/null)
assert "case5b retry returns content" "$out" "CAPTURED"
attempts=$(cat "$retry_state")
assert "case5b retry attempts" "$attempts" "2"

# --- Case 5c: retry boundary exits instead of firing a zero-delay second attempt ---
boundary_state="$TMP/boundary-fail-count"
assert_nonzero_exit "case5c boundary timeout rejects retry" env PATH="$TMP/bin:$PATH" GPTSAPI_VLM_CAPTURE_BODY="$capture" GPTSAPI_VLM_FAIL_ALWAYS_STATE="$boundary_state" GPTSAPI_VLM_TIMEOUT_SECONDS=1 GPTSAPI_VLM_RETRIES=1 GPTSAPI_VLM_RETRY_DELAY_SECONDS=1 GPTSAPI_VLM_STATE_DIR="$TMP/state2c" "$SCRIPT" --image "$FIXDIR/test-128.png" --prompt "Extract only text." --job-name case5c-boundary
boundary_attempts=$(cat "$boundary_state")
assert "case5c boundary attempts" "$boundary_attempts" "1"

# --- Case 6: env model override / --model are honored ---
capture2="$TMP/body2.json"
PATH="$TMP/bin:$PATH" GPTSAPI_VLM_CAPTURE_BODY="$capture2" GPTSAPI_VLM_STATE_DIR="$TMP/state3" "$SCRIPT" --image "$FIXDIR/test-128.png" --prompt p --job-name case6-model --model gpt-4o >/dev/null 2>/dev/null
model2=$(python3 - "$capture2" <<'PY'
import json, sys
print(json.load(open(sys.argv[1], encoding="utf-8"))["model"])
PY
)
assert "case6 --model override" "$model2" "gpt-4o"

# --- Case 7: daily call-count guard trips before runaway usage ---
capture3="$TMP/body3.json"
PATH="$TMP/bin:$PATH" GPTSAPI_VLM_CAPTURE_BODY="$capture3" GPTSAPI_VLM_STATE_DIR="$TMP/state4" GPTSAPI_VLM_DAILY_LIMIT=1 "$SCRIPT" --image "$FIXDIR/test-128.png" --prompt p --job-name case7-first >/dev/null 2>/dev/null
assert_nonzero_exit "case7 daily limit rejects second call" env PATH="$TMP/bin:$PATH" GPTSAPI_VLM_CAPTURE_BODY="$capture3" GPTSAPI_VLM_STATE_DIR="$TMP/state4" GPTSAPI_VLM_DAILY_LIMIT=1 "$SCRIPT" --image "$FIXDIR/test-128.png" --prompt p --job-name case7-second

# --- Case 7b: stale daily counter lock is reclaimed ---
capture4="$TMP/body4.json"
mkdir -p "$TMP/state-stale/gptsapi-vlm-count.lock"
out=$(PATH="$TMP/bin:$PATH" GPTSAPI_VLM_CAPTURE_BODY="$capture4" GPTSAPI_VLM_STATE_DIR="$TMP/state-stale" GPTSAPI_VLM_DAILY_LIMIT=2 GPTSAPI_VLM_LOCK_STALE_SECONDS=0 "$SCRIPT" --image "$FIXDIR/test-128.png" --prompt p --job-name case7b-stale-lock 2>/dev/null)
assert "case7b stale lock reclaimed" "$out" "CAPTURED"

echo "---"
echo "TOTAL PASS=$PASS  FAIL=$FAIL"
[ $FAIL -eq 0 ]
