#!/usr/bin/env bash
# Binary OCR accuracy gate: corpus accuracy >= 80% per ocr-expected.json.
#
# Offline (CI / dev without GPTSAPI_KEY or OCR_ACCURACY_FORCE_LIVE=1):
#   builds a synthetic mock response that embeds every expected field's value
#   so the scoring math is exercised and the gate reports 1.00 corpus
#   accuracy. This is the unit-test contract: the script works when the
#   real API is unreachable.
#
# Live (OCR_ACCURACY_FORCE_LIVE=1 and GPTSAPI_KEY set): calls the real
#   GPTsAPI VLM endpoint once per image. Used on Mac Mini during MVP gate.

set -euo pipefail
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
GATE="$REPO/scripts/washing-machine/ocr-accuracy-gate.py"
FIXDIR="$REPO/tests/washing-machine/fixtures/images"
EXPECTED="$FIXDIR/ocr-expected.json"

if [ "${OCR_ACCURACY_FORCE_LIVE:-0}" = "1" ]; then
  if [ -z "${GPTSAPI_KEY:-}" ]; then
    echo "OCR_ACCURACY_FORCE_LIVE=1 but GPTSAPI_KEY unset" >&2
    exit 2
  fi
  exec python3 "$GATE"
fi

# Offline mock mode: build a single response file containing every expected
# field's value, then route every image call to it.
MOCK="$FIXDIR/.mock-ocr-all.json"
STATE_DIR="$FIXDIR/.state"
trap 'rm -f "$MOCK"; rm -rf "$STATE_DIR"' EXIT
python3 - "$EXPECTED" "$MOCK" <<'PY'
import json, sys
expected_path, mock_path = sys.argv[1], sys.argv[2]
spec = json.loads(open(expected_path, encoding="utf-8").read())
values = []
for _name, entry in spec["images"].items():
    for _k, v in entry["fields"].items():
        values.append(str(v))
content = "\n".join(values)
payload = {"choices": [{"message": {"content": content}}]}
open(mock_path, "w", encoding="utf-8").write(json.dumps(payload))
PY

export GPTSAPI_KEY="${GPTSAPI_KEY:-test-key-for-ocr-gate-mock}"
export GPTSAPI_VLM_MOCK="$MOCK"
export GPTSAPI_VLM_STATE_DIR="$STATE_DIR"
python3 "$GATE"
