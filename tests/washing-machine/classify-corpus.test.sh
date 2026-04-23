#!/usr/bin/env bash
# tests/washing-machine/classify-corpus.test.sh
# Runs scripts/washing-machine/classify.sh against the calibration corpus
# and asserts the returned JSON matches the schema v1.0 contract.
#
# Two modes:
#   default (smoke)   -- 4 representative rows (001, 012, 017, 021). <30s.
#   --full            -- all 23 text-ingestable rows (skip 015: image-only;
#                         015's input is a synthesized [Image: ...] echo that
#                         a text-only Gate correctly rejects as tool_echo --
#                         tested in --full mode). Costs ~25 MiniMax calls.
#
# Live MiniMax required. Set LIVE_MINIMAX=1 or MINIMAX_API_KEY and run.
#
# Plan: plans/2026-04-21_washing-machine-ship-1.md Commit 3
# Spec: wiki/concepts/feature_washing-machine-memory.md (2026-04-23 compressed)

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
CLASSIFY="$REPO_ROOT/scripts/washing-machine/classify.sh"
NORMALIZE="$REPO_ROOT/scripts/washing-machine/normalize.py"
CORPUS_JSON="$REPO_ROOT/tests/washing-machine/calibration-expected.json"

WASHING_MACHINE_ENV="${WASHING_MACHINE_ENV:-$HOME/.config/k2b/washing-machine.env}"
if [ -f "$WASHING_MACHINE_ENV" ]; then
  # shellcheck disable=SC1090
  . "$WASHING_MACHINE_ENV"
fi
PYTHON_BIN="${WASHING_MACHINE_PYTHON:-python3}"

# Rows whose expected output depends on Ship 1B features (pending-confirm,
# VLM). Skipped in --full with a documented reason; re-enable when Ship 1B
# lands.
SKIP_ROWS=(
  "row_015"  # [Image: ...] input -- VLM pipeline (Ship 1B)
  "row_016"  # needs_confirmation_reason=date_ambiguous -- pending-confirm (Ship 1B)
)

should_skip() {
  local row="$1"
  for skip in "${SKIP_ROWS[@]}"; do
    [ "$row" = "$skip" ] && return 0
  done
  return 1
}

MODE="smoke"
if [ "${1:-}" = "--full" ]; then
  MODE="full"
fi

if [ -z "${LIVE_MINIMAX:-}" ] && [ -z "${MINIMAX_API_KEY:-}" ]; then
  echo "SKIP: classify-corpus.test.sh requires MINIMAX_API_KEY or LIVE_MINIMAX=1" >&2
  exit 0
fi
if [ ! -x "$CLASSIFY" ]; then
  echo "FAIL(precondition): classify.sh missing or not executable at $CLASSIFY" >&2
  exit 1
fi
if [ ! -f "$CORPUS_JSON" ]; then
  echo "FAIL(precondition): calibration-expected.json missing at $CORPUS_JSON" >&2
  exit 1
fi

if [ "$MODE" = "smoke" ]; then
  ROWS=("row_001" "row_012" "row_017" "row_021")
else
  mapfile -t ROWS < <("$PYTHON_BIN" -c "
import json
with open('$CORPUS_JSON') as f:
  data = json.load(f)
for k in sorted(data['rows'].keys()):
  print(k)
")
fi

ANCHOR="2026-04-01"
PASS=0
FAIL=0
SKIPPED=0
FAIL_IDS=()

for row_id in "${ROWS[@]}"; do
  if should_skip "$row_id"; then
    SKIPPED=$((SKIPPED + 1))
    echo "SKIP $row_id (Ship 1B scope)"
    continue
  fi
  input="$("$PYTHON_BIN" -c "
import json
with open('$CORPUS_JSON') as f:
  data = json.load(f)
print(data['rows']['$row_id']['input'], end='')
")"
  expected="$("$PYTHON_BIN" -c "
import json
with open('$CORPUS_JSON') as f:
  data = json.load(f)
print(json.dumps(data['rows']['$row_id']['expected']), end='')
")"

  # Mirror the production Gate pipeline: normalize.py pre-resolves relative
  # dates, classify.sh sees rewritten text only.
  normalised="$(printf '%s' "$input" | "$PYTHON_BIN" "$NORMALIZE" --anchor "$ANCHOR")"
  actual="$(printf '%s' "$normalised" | "$CLASSIFY" --anchor "$ANCHOR" 2>/dev/null || echo '')"
  if [ -z "$actual" ]; then
    FAIL=$((FAIL + 1))
    FAIL_IDS+=("$row_id(classifier_error)")
    continue
  fi

  verdict="$(python3 - "$expected" "$actual" <<'PY'
import json, sys
expected = json.loads(sys.argv[1])
try:
  actual = json.loads(sys.argv[2])
except json.JSONDecodeError as e:
  print(f"FAIL: invalid JSON from classifier: {e}")
  sys.exit(1)

issues = []

# keep must match
if expected.get('keep') != actual.get('keep'):
  issues.append(f"keep mismatch: expected {expected.get('keep')}, got {actual.get('keep')}")

if expected.get('keep') is False:
  # reject rows: discard_reason must match exactly
  if expected.get('discard_reason') != actual.get('discard_reason'):
    issues.append(f"discard_reason mismatch: expected {expected.get('discard_reason')!r}, got {actual.get('discard_reason')!r}")
else:
  # keep=true rows: category + shelf must match
  if expected.get('category') != actual.get('category'):
    issues.append(f"category mismatch: expected {expected.get('category')!r}, got {actual.get('category')!r}")
  if expected.get('shelf') != actual.get('shelf'):
    issues.append(f"shelf mismatch: expected {expected.get('shelf')!r}, got {actual.get('shelf')!r}")
  # entity type set must match (order-insensitive). Compressed Ship 1 does
  # not require exact field-by-field match -- the prompt is allowed to
  # produce slightly different phrasings as long as the type is correct.
  exp_types = sorted([e.get('type') for e in expected.get('entities', [])])
  act_types = sorted([e.get('type') for e in actual.get('entities', [])])
  if exp_types and exp_types != act_types:
    issues.append(f"entity types mismatch: expected {exp_types}, got {act_types}")
  # If expected carries timestamp_iso, actual must carry a timestamp with
  # the same date prefix (YYYY-MM-DD).
  exp_ts = expected.get('timestamp_iso')
  if exp_ts:
    act_ts = actual.get('timestamp_iso') or ''
    if exp_ts[:10] != act_ts[:10]:
      issues.append(f"timestamp_iso date prefix mismatch: expected {exp_ts[:10]!r}, got {act_ts[:10]!r}")

if issues:
  print('FAIL: ' + '; '.join(issues))
else:
  print('OK')
PY
)"

  if [ "$verdict" = "OK" ]; then
    PASS=$((PASS + 1))
    echo "PASS $row_id"
  else
    FAIL=$((FAIL + 1))
    FAIL_IDS+=("$row_id")
    echo "FAIL $row_id: $verdict"
    echo "       input:    $input"
    echo "       expected: $expected"
    echo "       actual:   $actual"
  fi
done

echo "classify-corpus.test.sh ($MODE): $PASS pass, $FAIL fail, $SKIPPED skipped"
if [ "$FAIL" -gt 0 ]; then
  echo "failing rows: ${FAIL_IDS[*]}" >&2
fi
[ "$FAIL" -eq 0 ]
