#!/usr/bin/env bash
# Integration test for scripts/loop/loop-apply.sh.
# Copies the fixture to a tmp dir, invokes loop-apply, verifies mutations.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
FIXTURE="$ROOT/tests/fixtures/loop-mvp"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

cp "$FIXTURE/observer-candidates.md" "$TMP/observer-candidates.md"
cp "$FIXTURE/self_improve_learnings.md" "$TMP/self_improve_learnings.md"
mkdir -p "$TMP/observations.archive"

export K2B_LOOP_CANDIDATES="$TMP/observer-candidates.md"
export K2B_LOOP_LEARNINGS="$TMP/self_improve_learnings.md"
export K2B_LOOP_ARCHIVE_DIR="$TMP/observations.archive"
export K2B_LOOP_DATE="2026-04-23"
export K2B_LOOP_ACTOR="keith"
export K2B_LOOP_OBSERVER_RUN="2026-04-22 21:44"

"$ROOT/scripts/loop/loop-apply.sh" --accept 1 --accept 2 --accept 3 --reject 4 --reject 5

count=$(grep -cE '^### L-2026-04-23-00[123]$' "$TMP/self_improve_learnings.md")
if [ "$count" != "3" ]; then
  echo "FAIL: expected 3 new L-2026-04-23-00X entries, got $count" >&2
  exit 1
fi

archive="$TMP/observations.archive/rejected-2026-04-23.jsonl"
if [ ! -f "$archive" ]; then
  echo "FAIL: archive file missing: $archive" >&2
  exit 1
fi
rejects=$(wc -l < "$archive" | tr -d ' ')
if [ "$rejects" != "2" ]; then
  echo "FAIL: expected 2 reject lines, got $rejects" >&2
  exit 1
fi

remaining=$(awk '
  /^## Candidate Learnings/ { inside=1; next }
  /^## / && inside { exit }
  inside && /^- \[/ { n++ }
  END { print n+0 }
' "$TMP/observer-candidates.md")
if [ "$remaining" != "0" ]; then
  echo "FAIL: expected 0 remaining candidates, got $remaining" >&2
  exit 1
fi

echo "PASS: loop-apply.test.sh"
