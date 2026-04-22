#!/usr/bin/env bash
# BINARY MVP TEST for feature_k2b-integrated-loop Ship 1.
# Scenario: 5 fixture candidates -> 3 accepts + 2 rejects via loop-apply.sh.
# Pass conditions (all must hold):
#   Gate 1: self_improve_learnings.md has 3 new L-2026-04-23-NNN entries with
#           Source: observer-candidates tag.
#   Gate 2: observations.archive/rejected-2026-04-23.jsonl has 2 lines with
#           rejected: keith 2026-04-23 marker.
#   Gate 3: observer-candidates.md has 0 remaining Candidate Learnings items.
#   Gate 4: No duplicate L-IDs (dedupe invariant).
#   Gate 5: Dashboard renders the fixture with 5 numbered items before routing.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
FIXTURE="$ROOT/tests/fixtures/loop-mvp"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

cp "$FIXTURE/observer-candidates.md" "$TMP/observer-candidates.md"
cp "$FIXTURE/self_improve_learnings.md" "$TMP/self_improve_learnings.md"
mkdir -p "$TMP/observations.archive" "$TMP/review" "$TMP/raw/research"

export K2B_LOOP_CANDIDATES="$TMP/observer-candidates.md"
export K2B_LOOP_LEARNINGS="$TMP/self_improve_learnings.md"
export K2B_LOOP_ARCHIVE_DIR="$TMP/observations.archive"
export K2B_LOOP_REVIEW_DIR="$TMP/review"
export K2B_LOOP_RESEARCH_DIR="$TMP/raw/research"
export K2B_LOOP_DATE="2026-04-23"
export K2B_LOOP_ACTOR="keith"
export K2B_LOOP_OBSERVER_RUN="2026-04-22 21:44"

# Gate 5: dashboard renders 5 numbered items
dashboard="$("$ROOT/scripts/loop/loop-render-dashboard.sh")"
for n in 1 2 3 4 5; do
  echo "$dashboard" | grep -Eq "^\s*\[${n}\] " || { echo "FAIL gate 5: dashboard missing [${n}]"; echo "$dashboard"; exit 1; }
done
echo "$dashboard" | grep -q "Observer candidates (5)" || { echo "FAIL gate 5: wrong count"; exit 1; }
echo "  gate 5 PASS: dashboard renders 5 numbered items"

# Route: accept 1,2,3 / reject 4,5 (MVP scenario)
"$ROOT/scripts/loop/loop-apply.sh" --accept 1 --accept 2 --accept 3 --reject 4 --reject 5 >/dev/null

# Gate 1: 3 new L-2026-04-23-NNN entries with Source tag
new_lids=$(grep -cE '^### L-2026-04-23-00[123]$' "$TMP/self_improve_learnings.md" || true)
if [ "$new_lids" != "3" ]; then
  echo "FAIL gate 1: expected 3 L-2026-04-23-00[123] entries, got $new_lids"
  exit 1
fi
source_tags=$(grep -c 'Source:\*\* observer-candidates (auto-applied 2026-04-23 via session-start dashboard)' "$TMP/self_improve_learnings.md" || true)
if [ "$source_tags" != "3" ]; then
  echo "FAIL gate 1: expected 3 Source: observer-candidates tags, got $source_tags"
  exit 1
fi
echo "  gate 1 PASS: 3 new L-IDs with Source tag"

# Gate 2: 2 reject lines
archive="$TMP/observations.archive/rejected-2026-04-23.jsonl"
if [ ! -f "$archive" ]; then echo "FAIL gate 2: archive missing"; exit 1; fi
rej=$(wc -l < "$archive" | tr -d ' ')
if [ "$rej" != "2" ]; then echo "FAIL gate 2: expected 2 rejects, got $rej"; exit 1; fi
if ! grep -q '"rejected": "keith 2026-04-23"' "$archive"; then
  echo "FAIL gate 2: rejected marker missing"
  exit 1
fi
echo "  gate 2 PASS: 2 rejects archived with keith marker"

# Gate 3: 0 remaining candidates
remaining=$(awk '
  /^## Candidate Learnings/ { inside=1; next }
  /^## / && inside { exit }
  inside && /^- \[/ { n++ }
  END { print n+0 }
' "$TMP/observer-candidates.md")
if [ "$remaining" != "0" ]; then
  echo "FAIL gate 3: expected 0 remaining candidates, got $remaining"
  exit 1
fi
echo "  gate 3 PASS: 0 remaining candidates"

# Gate 4: no duplicate L-IDs
dupes=$(grep -oE '^### L-[0-9]{4}-[0-9]{2}-[0-9]{2}-[0-9]{3}' "$TMP/self_improve_learnings.md" | sort | uniq -d | wc -l | tr -d ' ')
if [ "$dupes" != "0" ]; then
  echo "FAIL gate 4: duplicate L-IDs detected"
  grep -oE '^### L-[0-9]{4}-[0-9]{2}-[0-9]{2}-[0-9]{3}' "$TMP/self_improve_learnings.md" | sort | uniq -d
  exit 1
fi
echo "  gate 4 PASS: no duplicate L-IDs"

echo ""
echo "BINARY MVP: SHIP (5/5 gates passed)"
