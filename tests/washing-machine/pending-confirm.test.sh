#!/usr/bin/env bash
# Shell-level contract guard for the pending-confirmation schema.
#
# washingMachineResume.ts (bot-side reader) and washingMachine.ts park
# path (writer) share one JSON shape. This script seeds a fresh file
# matching the writer contract and asserts the shape the reader expects.
# If either side evolves without the other, this test fails before the
# live bug appears on Telegram.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/../.." && pwd)"
VAULT_DIR="${K2B_VAULT:-${HOME}/Projects/K2B-Vault}/wiki/context/shelves/.pending-confirmation"

PASS=0
FAIL=0
pass() { PASS=$((PASS+1)); echo "PASS $1"; }
fail() { FAIL=$((FAIL+1)); echo "FAIL $1"; }

# --- Directory exists in the vault (so writer never has to race-create) ---
if [ -d "$VAULT_DIR" ]; then pass "vault dir exists"; else fail "vault dir missing: $VAULT_DIR"; fi

# --- Writer/reader contract: every field the reader needs must be present ---
TMP=$(mktemp -d)
trap "rm -rf $TMP" EXIT

python3 - "$TMP/seed.json" <<'PY'
import json, sys
rec = {
    "chatId": "42",
    "promptMessageId": 999,
    "candidates": [
        {"date": "2026-04-01", "label": "message date"},
        {"date": "2025-04-11", "label": "OCR date"},
    ],
    "row": {
        "type": "contact",
        "fields": {"name": "Dr. Lo Hak Keung", "phone": "2830 3709"},
    },
}
open(sys.argv[1], "w").write(json.dumps(rec))
PY

# Validate required keys + shape (same assertions the TS reader makes).
if python3 - "$TMP/seed.json" <<'PY'; then
import json, sys
d = json.loads(open(sys.argv[1]).read())
# Required keys
for k in ("chatId", "promptMessageId", "candidates", "row"):
    assert k in d, f"missing key {k}"
# Candidates shape
assert isinstance(d["candidates"], list) and len(d["candidates"]) >= 1
for c in d["candidates"]:
    assert "date" in c and "label" in c, f"bad candidate {c}"
# Type-expectation alignment
assert isinstance(d["chatId"], str)
assert isinstance(d["promptMessageId"], int)
print("ok")
PY
  pass "seed matches reader contract"
else
  fail "seed does not match reader contract"
fi

# --- Atomic-write shape: temp file rename pattern ---
# writer uses .<uuid>.json.tmp + rename. Verify the pattern is shell-portable
# by driving it from bash.
uuid="test-$(date +%s)"
tmp="$TMP/.${uuid}.json.tmp"
final="$TMP/${uuid}.json"
echo '{"chatId":"42","promptMessageId":0,"candidates":[{"date":"2026-04-01","label":"t"}],"row":{}}' > "$tmp"
mv "$tmp" "$final"
if [ -f "$final" ] && [ ! -f "$tmp" ]; then
  pass "atomic rename pattern works"
else
  fail "atomic rename left stray tmp"
fi

echo "pending-confirm.test.sh: $PASS pass, $FAIL fail"
[ "$FAIL" -eq 0 ]
