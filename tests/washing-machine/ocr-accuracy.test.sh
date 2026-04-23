#!/usr/bin/env bash
# Binary OCR accuracy gate: corpus accuracy >= 80% per ocr-expected.json.
#
# Offline (CI / dev without MINIMAX_API_KEY or OCR_ACCURACY_FORCE_LIVE=1):
#   builds a synthetic mock response that embeds every expected field's value
#   so the scoring math is exercised and the gate reports 1.00 corpus
#   accuracy. This is the unit-test contract: the script works when the
#   real API is unreachable.
#
# Live (OCR_ACCURACY_FORCE_LIVE=1 and MINIMAX_API_KEY set): calls the real
#   VLM endpoint once per image. Used on Mac Mini during Commit 5 MVP gate.

set -euo pipefail
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
GATE="$REPO/scripts/washing-machine/ocr-accuracy-gate.py"
FIXDIR="$REPO/tests/washing-machine/fixtures/images"
EXPECTED="$FIXDIR/ocr-expected.json"

if [ "${OCR_ACCURACY_FORCE_LIVE:-0}" = "1" ]; then
  if [ -z "${MINIMAX_API_KEY:-}" ]; then
    echo "OCR_ACCURACY_FORCE_LIVE=1 but MINIMAX_API_KEY unset" >&2
    exit 2
  fi
  exec python3 "$GATE"
fi

# Offline mock mode: build a single response file containing every expected
# field's value, then route every image call to it.
MOCK="$FIXDIR/.mock-ocr-all.json"
trap 'rm -f "$MOCK"' EXIT
python3 - "$EXPECTED" "$MOCK" <<'PY'
import json, sys
expected_path, mock_path = sys.argv[1], sys.argv[2]
spec = json.loads(open(expected_path, encoding="utf-8").read())
values = []
for _name, entry in spec["images"].items():
    for _k, v in entry["fields"].items():
        values.append(str(v))
content = "\n".join(values)
payload = {"base_resp": {"status_code": 0, "status_msg": "ok"}, "content": content}
open(mock_path, "w", encoding="utf-8").write(json.dumps(payload))
PY

# minimax-common.sh requires MINIMAX_API_KEY at source time (even in mock mode).
export MINIMAX_API_KEY="${MINIMAX_API_KEY:-test-key-for-ocr-gate-mock}"
export MINIMAX_VLM_MOCK="$MOCK"
python3 "$GATE"
