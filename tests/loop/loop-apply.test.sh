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

# MEDIUM-4 regression: duplicate --accept on same index must NOT produce duplicate writes.
TMP2="$(mktemp -d)"
trap 'rm -rf "$TMP" "$TMP2"' EXIT
cp "$FIXTURE/observer-candidates.md" "$TMP2/observer-candidates.md"
cp "$FIXTURE/self_improve_learnings.md" "$TMP2/self_improve_learnings.md"
mkdir -p "$TMP2/observations.archive"
K2B_LOOP_CANDIDATES="$TMP2/observer-candidates.md" \
K2B_LOOP_LEARNINGS="$TMP2/self_improve_learnings.md" \
K2B_LOOP_ARCHIVE_DIR="$TMP2/observations.archive" \
K2B_LOOP_DATE=2026-04-23 K2B_LOOP_ACTOR=keith K2B_LOOP_OBSERVER_RUN=test \
"$ROOT/scripts/loop/loop-apply.sh" --accept 1 --accept 1 >/dev/null
dup_lids=$(grep -cE '^### L-2026-04-23-' "$TMP2/self_improve_learnings.md")
if [ "$dup_lids" != "1" ]; then
  echo "FAIL MEDIUM-4: duplicate --accept 1 --accept 1 produced $dup_lids learnings (expected 1)"
  exit 1
fi

# MEDIUM-4 regression: cross-action conflict must exit 2 without mutations.
TMP3="$(mktemp -d)"
trap 'rm -rf "$TMP" "$TMP2" "$TMP3" "${TMP4:-}"' EXIT
cp "$FIXTURE/observer-candidates.md" "$TMP3/observer-candidates.md"
cp "$FIXTURE/self_improve_learnings.md" "$TMP3/self_improve_learnings.md"
mkdir -p "$TMP3/observations.archive"
set +e
K2B_LOOP_CANDIDATES="$TMP3/observer-candidates.md" \
K2B_LOOP_LEARNINGS="$TMP3/self_improve_learnings.md" \
K2B_LOOP_ARCHIVE_DIR="$TMP3/observations.archive" \
K2B_LOOP_DATE=2026-04-23 K2B_LOOP_ACTOR=keith K2B_LOOP_OBSERVER_RUN=test \
"$ROOT/scripts/loop/loop-apply.sh" --accept 2 --reject 2 >/dev/null 2>&1
conflict_exit=$?
set -e
if [ "$conflict_exit" != "2" ]; then
  echo "FAIL MEDIUM-4: cross-action conflict expected exit 2, got $conflict_exit"
  exit 1
fi

# R-2026-04-23-001 regression: with NO env overrides, MEM_DEFAULT must resolve
# to $HOME/Projects/K2B-Vault/System/memory (the Syncthing-synced canonical
# home), not to a path containing the literal MacBook user "keithmbpm2".
# Apply must succeed when running under a $HOME that doesn't contain that
# literal -- which is exactly the Mini's situation (HOME=/Users/fastshower).
TMP4="$(mktemp -d)"
trap 'rm -rf "$TMP" "$TMP2" "$TMP3" "$TMP4"' EXIT
mkdir -p "$TMP4/Projects/K2B-Vault/System/memory"
mkdir -p "$TMP4/Projects/K2B-Vault/wiki/context/observations.archive"
mkdir -p "$TMP4/Projects/K2B-Vault/review"
cp "$FIXTURE/observer-candidates.md" "$TMP4/Projects/K2B-Vault/wiki/context/observer-candidates.md"
cp "$FIXTURE/self_improve_learnings.md" "$TMP4/Projects/K2B-Vault/System/memory/self_improve_learnings.md"

# Sanity: this fake HOME does NOT contain the MacBook username string.
case "$TMP4" in
  *keithmbpm2*) echo "FAIL R-2026-04-23-001 setup: tmpdir contains 'keithmbpm2'"; exit 1;;
esac

# Run with HOME pointing at the fake vault and ALL K2B_LOOP_* path vars
# unset (so the script falls through to defaults derived from $HOME).
# The earlier test cases at top-of-file `export`ed K2B_LOOP_CANDIDATES etc.
# pointing at their own $TMP -- those values would otherwise leak in here
# and mask the very behavior we're testing.
env -u K2B_LOOP_CANDIDATES \
    -u K2B_LOOP_LEARNINGS \
    -u K2B_LOOP_ARCHIVE_DIR \
    -u K2B_LOOP_DEFERS \
    -u K2B_LOOP_REVIEW_DIR \
    -u K2B_LOOP_REVIEW_READY_DIR \
    -u K2B_LOOP_REVIEW_ARCHIVE_ROOT \
    HOME="$TMP4" \
    K2B_LOOP_DATE=2026-04-23 K2B_LOOP_ACTOR=keith K2B_LOOP_OBSERVER_RUN=test \
    "$ROOT/scripts/loop/loop-apply.sh" --accept 1 >/dev/null

if ! grep -qE '^### L-2026-04-23-001$' "$TMP4/Projects/K2B-Vault/System/memory/self_improve_learnings.md"; then
  echo "FAIL R-2026-04-23-001: default MEM_DEFAULT did not resolve to vault path under tmp HOME"
  echo "  expected new L-2026-04-23-001 in $TMP4/Projects/K2B-Vault/System/memory/self_improve_learnings.md"
  exit 1
fi

echo "  PASS: R-2026-04-23-001 default memory path resolves under tmp HOME (Mini-portable)"

echo "PASS: loop-apply.test.sh"
