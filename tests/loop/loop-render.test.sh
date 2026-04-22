#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
FIXTURE="$ROOT/tests/fixtures/loop-mvp"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

cp "$FIXTURE/observer-candidates.md" "$TMP/observer-candidates.md"
mkdir -p "$TMP/review" "$TMP/raw/research"

export K2B_LOOP_CANDIDATES="$TMP/observer-candidates.md"
export K2B_LOOP_REVIEW_DIR="$TMP/review"
export K2B_LOOP_RESEARCH_DIR="$TMP/raw/research"

out="$("$ROOT/scripts/loop/loop-render-dashboard.sh")"

echo "$out" | grep -q "K2B LOOP DASHBOARD" || { echo "FAIL: missing header"; exit 1; }
echo "$out" | grep -Eq "^\s*\[1\] \[high\]" || { echo "FAIL: missing [1] high"; exit 1; }
echo "$out" | grep -Eq "^\s*\[5\] \[medium\]" || { echo "FAIL: missing [5] medium"; exit 1; }
echo "$out" | grep -q "a N / r N / d N" || { echo "FAIL: missing grammar hint"; exit 1; }
echo "$out" | grep -qE "Observer candidates \(5\)" || { echo "FAIL: bad candidate count"; exit 1; }
echo "PASS: loop-render.test.sh"
