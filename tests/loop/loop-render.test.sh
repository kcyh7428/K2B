#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
FIXTURE="$ROOT/tests/fixtures/loop-mvp"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

cp "$FIXTURE/observer-candidates.md" "$TMP/observer-candidates.md"
mkdir -p "$TMP/review" "$TMP/raw/research" "$TMP/pending-conflicts"
cat > "$TMP/pending-conflicts/2026-05-14_c.json" <<'JSON'
{
  "conflict_id": "c",
  "subject": "Dr. Lo Hak Keung",
  "predicate": "phone",
  "existing_value": "2840 3709",
  "new_value": "2830 3709",
  "source_session_path": "/tmp/session.jsonl",
  "surfaced_count": 0
}
JSON

export K2B_LOOP_CANDIDATES="$TMP/observer-candidates.md"
export K2B_LOOP_REVIEW_DIR="$TMP/review"
export K2B_LOOP_CONFLICTS_DIR="$TMP/pending-conflicts"
export K2B_LOOP_RESEARCH_DIR="$TMP/raw/research"

out="$("$ROOT/scripts/loop/loop-render-dashboard.sh")"

echo "$out" | grep -q "K2B LOOP DASHBOARD" || { echo "FAIL: missing header"; exit 1; }
echo "$out" | grep -Eq "^\s*\[1\] \[high\]" || { echo "FAIL: missing [1] high"; exit 1; }
echo "$out" | grep -Eq "^\s*\[5\] \[medium\]" || { echo "FAIL: missing [5] medium"; exit 1; }
echo "$out" | grep -q "a N / r N / d N" || { echo "FAIL: missing grammar hint"; exit 1; }
echo "$out" | grep -qE "Observer candidates \(5\)" || { echo "FAIL: bad candidate count"; exit 1; }
echo "$out" | grep -qE "Pending conflicts \(1\)" || { echo "FAIL: bad conflict count"; exit 1; }
echo "$out" | grep -qE "^\s*\[6\] conflict · Dr\. Lo Hak Keung phone" || { echo "FAIL: missing unified conflict index"; exit 1; }
echo "PASS: loop-render.test.sh"
