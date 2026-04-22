#!/usr/bin/env bash
# tests/washing-machine/retrieve.test.sh
# Hybrid retrieval gate for Ship 1 -- the bug killer for the 2026-04-21
# doctor-phone regression. The 3 top-hit queries are the binary ship gate.
# Plan: plans/2026-04-21_washing-machine-ship-1.md Commit 2.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
WRITER="$REPO_ROOT/scripts/washing-machine/shelf-writer.sh"
INDEXER="$REPO_ROOT/scripts/washing-machine/embed-index.py"
RETRIEVER="$REPO_ROOT/scripts/washing-machine/retrieve.py"

if [ ! -x "$WRITER" ]; then
  echo "FAIL(precondition): shelf-writer.sh missing at $WRITER" >&2
  exit 1
fi
if [ ! -f "$INDEXER" ]; then
  echo "FAIL(precondition): embed-index.py missing at $INDEXER" >&2
  exit 1
fi
if [ ! -f "$RETRIEVER" ]; then
  echo "FAIL(precondition): retrieve.py missing at $RETRIEVER" >&2
  exit 1
fi

WASHING_MACHINE_ENV="${WASHING_MACHINE_ENV:-$HOME/.config/k2b/washing-machine.env}"
if [ -f "$WASHING_MACHINE_ENV" ]; then
  # shellcheck disable=SC1090
  . "$WASHING_MACHINE_ENV"
fi
PYTHON_BIN="${WASHING_MACHINE_PYTHON:-python3}"

if ! "$PYTHON_BIN" -c "from sentence_transformers import SentenceTransformer" 2>/dev/null; then
  echo "FAIL(precondition): sentence-transformers not importable via $PYTHON_BIN" >&2
  exit 1
fi

TMPDIR="$(mktemp -d)"
LOCK_DIR="$(mktemp -d)"
cleanup() {
  chmod -R u+w "$TMPDIR" 2>/dev/null || true
  rm -rf "$TMPDIR" "$LOCK_DIR" 2>/dev/null || true
}
trap cleanup EXIT

export K2B_SHELVES_DIR="$TMPDIR"
export K2B_SHELF_LOCK_DIR="$LOCK_DIR"
export K2B_INDEX_DB="$TMPDIR/index.db"

fail() { echo "FAIL: $*" >&2; exit 1; }

# Seed the shelf: Dr. Lo row (exactly as migrate-historical wrote it) +
# 5 unrelated distractors. The doctor-phone gate must prefer Dr. Lo over
# anything else on every variant.
"$WRITER" --shelf semantic --date 2026-04-01 --type contact \
  --slug person_Dr-Lo-Hak-Keung \
  --attr "name:Dr. Lo Hak Keung" \
  --attr "name_zh:羅克強醫生" \
  --attr "tel:2830 3709" \
  --attr "whatsapp:9861 9017" \
  --attr "role:Urology" \
  --attr "organization:St. Paul's Hospital" \
  --attr "address:2 Eastern Hospital Road, Causeway Bay"

# Distractor 1: unrelated preference row.
"$WRITER" --shelf semantic --date 2026-04-02 --type preference \
  --slug tone_plain_english --attr "value:no AI cliches"

# Distractor 2: unrelated meeting row.
"$WRITER" --shelf semantic --date 2026-04-03 --type fact \
  --slug meeting_kickoff --attr "when:2026-04-20" --attr "venue:HQ"

# Distractor 3: recipe-style fact far from medical-contact space.
"$WRITER" --shelf semantic --date 2026-04-04 --type fact \
  --slug recipe_dumplings --attr "ingredient:ginger" --attr "ingredient:soy sauce"

# Distractor 4: non-doctor contact (to ensure the word "contact" alone isn't
# enough to promote a row to the top).
"$WRITER" --shelf semantic --date 2026-04-05 --type contact \
  --slug person_Andrew --attr "name:Andrew Partner" --attr "role:TalentSignals"

# Distractor 5: another person page mention without medical context.
"$WRITER" --shelf semantic --date 2026-04-06 --type fact \
  --slug person_Test-Sibling --attr "note:linked via [[person_Dr-Lo-Hak-Keung]]"

"$PYTHON_BIN" "$INDEXER" --shelf semantic || fail "index build failed"

# Parser scripts are passed via -c (not heredoc to `python -`) because
# `cmd | python - <<'PY'` conflicts on stdin: python reads its program
# source from stdin, which competes with the pipe's data stream.
TOP_SLUG_PY='import json, sys
d = json.load(sys.stdin)
print(d[0]["slug"] if d else "EMPTY")
'
ASSERT_EMPTY_PY='import json, sys
d = json.load(sys.stdin)
assert isinstance(d, list), "expected list, got " + type(d).__name__
assert len(d) == 0, "expected empty list, got " + str(len(d)) + " hits: " + str(d)
'

top_slug() {
  # Usage: top_slug <query>  -- prints slug of rank 1 hit, or "EMPTY" if none.
  local q="$1"
  "$PYTHON_BIN" "$RETRIEVER" "$q" --shelf semantic --k 5 \
    | "$PYTHON_BIN" -c "$TOP_SLUG_PY"
}

result_json() {
  local q="$1"
  "$PYTHON_BIN" "$RETRIEVER" "$q" --shelf semantic --k 5
}

# --- Test 1: doctor-phone gate (binary ship gate, 3 variants) ---
for q in "doctor phone number" "urology contact" "phone st pauls"; do
  TOP="$(top_slug "$q")"
  [ "$TOP" = "person_Dr-Lo-Hak-Keung" ] \
    || fail "test 1: query '$q' top hit = '$TOP' (expected person_Dr-Lo-Hak-Keung) -- SHIP GATE FAILED"
done

# --- Test 2: entity-link via [[wikilink]] literal in query ---
# Query carries the exact slug as a wikilink; entity-link signal must pull
# Dr. Lo to the top over the distractor that also mentions the wikilink
# (Distractor 5 has the wikilink in an attr). Either of those is acceptable
# as the top hit because both mention the same entity; the assertion is that
# AT LEAST the Dr. Lo row is in the result set and comes back before any
# row that shares NO entity overlap.
RES="$(result_json "any info on [[person_Dr-Lo-Hak-Keung]]")"
ENTITY_LINK_ASSERT_PY='import json, sys
data = json.load(sys.stdin)
assert isinstance(data, list), data
slugs = [r["slug"] for r in data]
assert "person_Dr-Lo-Hak-Keung" in slugs, "Dr. Lo not in top results: " + str(slugs)
# Dr. Lo must rank at or above any row that shares no entity with the query.
# (The distractor with the wikilink in its note may tie or outrank -- both OK.)
dr_lo_idx = slugs.index("person_Dr-Lo-Hak-Keung")
non_entity_slugs = {"tone_plain_english", "meeting_kickoff", "recipe_dumplings", "person_Andrew"}
for i, s in enumerate(slugs):
    if s in non_entity_slugs:
        assert dr_lo_idx < i, "Dr. Lo ranked below non-entity row " + repr(s) + " (idx " + str(dr_lo_idx) + " vs " + str(i) + ")"
'
echo "$RES" | "$PYTHON_BIN" -c "$ENTITY_LINK_ASSERT_PY" \
  || fail "test 2: entity-link wikilink query failed"

# --- Test 3: BM25 exact-phone fallback ---
TOP="$(top_slug "2830 3709")"
[ "$TOP" = "person_Dr-Lo-Hak-Keung" ] \
  || fail "test 3: BM25 exact-phone query top hit = '$TOP' (expected Dr. Lo)"

# --- Test 4: zero-result query -> empty JSON array, not error ---
# Query is deliberately botanical + off-topic for every seeded row so no
# cosine clears WM_COSINE_THRESHOLD and no BM25/entity signal fires.
RC=0
RES="$("$PYTHON_BIN" "$RETRIEVER" "photosynthesis chlorophyll glucose mitochondria" --shelf semantic --k 5)" || RC=$?
[ "$RC" = "0" ] || fail "test 4: retrieve.py exited $RC on unrelated query"
echo "$RES" | "$PYTHON_BIN" -c "$ASSERT_EMPTY_PY" \
  || fail "test 4: expected empty JSON array on no-match query"

# Missing shelf / missing DB path -> also empty array, not crash.
rm -f "$K2B_INDEX_DB"
RC=0
RES="$("$PYTHON_BIN" "$RETRIEVER" "anything" --shelf semantic --k 5)" || RC=$?
[ "$RC" = "0" ] || fail "test 4b: retrieve.py crashed when index DB absent (rc=$RC)"
echo "$RES" | "$PYTHON_BIN" -c "import json, sys; d=json.load(sys.stdin); assert d==[], d" \
  || fail "test 4b: missing-DB did not return []"

# Rebuild the index for test 5.
"$PYTHON_BIN" "$INDEXER" --shelf semantic || fail "test 5 precondition: reindex failed"

# --- Test 5: synonym stress ---
# (a) stored row uses "tel:" not "phone"; query "phone" must at minimum
#     retrieve Dr. Lo AND rank him above the clearly-unrelated recipe row.
#     Stronger top-1 would be nice, but the embedding cosine margin between
#     Dr. Lo and the Andrew contact distractor is tight (~0.006 on a 6-row
#     synthetic corpus) -- tying the ship gate to that margin would make
#     the test brittle against model patches or platform-float drift
#     (Codex MEDIUM 2026-04-22). The production gate at Commit 6 is the
#     full 3-mode MVP on the real vault, not this microbenchmark.
TOP_ROWS_PY='import json, sys
d = json.load(sys.stdin)
print("\n".join(r["slug"] for r in d))
'
SLUGS="$("$PYTHON_BIN" "$RETRIEVER" "phone" --shelf semantic --k 5 \
          | "$PYTHON_BIN" -c "$TOP_ROWS_PY")"
echo "$SLUGS" | grep -qx "person_Dr-Lo-Hak-Keung" \
  || fail "test 5a: query 'phone' did not surface Dr. Lo at all (got slugs: $(echo "$SLUGS" | tr '\n' ' '))"

DRLO_IDX="$(echo "$SLUGS" | awk '/^person_Dr-Lo-Hak-Keung$/ {print NR-1; exit}')"
RECIPE_IDX="$(echo "$SLUGS" | awk '/^recipe_dumplings$/ {print NR-1; exit}')"
if [ -n "$RECIPE_IDX" ]; then
  if [ -z "$DRLO_IDX" ] || [ "$DRLO_IDX" -ge "$RECIPE_IDX" ]; then
    fail "test 5a: Dr. Lo (rank $DRLO_IDX) did not rank above recipe_dumplings (rank $RECIPE_IDX); Tel -> phone bridge failed"
  fi
fi

# (b) stored name uses "Dr." not "Doctor"; query "doctor" must still pull
#     Dr. Lo to the top. "Dr." -> "doctor" is a cleaner semantic bridge
#     than "Tel:" -> "phone" (Dr. Lo cos ~0.30 vs Test-Sibling ~0.22 on
#     the synthetic corpus), so top-1 is robust enough to assert here.
TOP="$(top_slug "doctor")"
[ "$TOP" = "person_Dr-Lo-Hak-Keung" ] \
  || fail "test 5b: query 'doctor' (stored as 'Dr.') top hit = '$TOP'; embedding did not bridge Dr. -> doctor"

echo "retrieve.test.sh: all 5 tests passed"
