#!/usr/bin/env bash
# tests/washing-machine/embed-index.test.sh
# Idempotence + lifecycle tests for scripts/washing-machine/embed-index.py.
# Plan: plans/2026-04-21_washing-machine-ship-1.md Commit 2.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
WRITER="$REPO_ROOT/scripts/washing-machine/shelf-writer.sh"
INDEXER="$REPO_ROOT/scripts/washing-machine/embed-index.py"
RETRIEVER="$REPO_ROOT/scripts/washing-machine/retrieve.py"
LIB="$REPO_ROOT/scripts/washing-machine/lib/shelf_rows.py"

if [ ! -x "$WRITER" ]; then
  echo "FAIL(precondition): shelf-writer.sh missing at $WRITER" >&2
  exit 1
fi
if [ ! -f "$INDEXER" ]; then
  echo "FAIL(precondition): embed-index.py missing at $INDEXER" >&2
  exit 1
fi
if [ ! -f "$LIB" ]; then
  echo "FAIL(precondition): lib/shelf_rows.py missing at $LIB" >&2
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
  echo "  Run: $REPO_ROOT/scripts/washing-machine/preflight.sh" >&2
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

db_row_count() {
  "$PYTHON_BIN" - "$K2B_INDEX_DB" <<'PY'
import sqlite3, sys
p = sys.argv[1]
c = sqlite3.connect(p)
try:
    n = c.execute("SELECT COUNT(*) FROM rows").fetchone()[0]
except sqlite3.OperationalError:
    n = 0
print(n)
PY
}

db_snapshot() {
  # (id, shelf, row_hash) triples, sorted by id. Identical snapshot = idempotent.
  "$PYTHON_BIN" - "$K2B_INDEX_DB" <<'PY'
import sqlite3, sys
c = sqlite3.connect(sys.argv[1])
rows = sorted(c.execute("SELECT id, shelf, row_hash FROM rows").fetchall())
for r in rows:
    print(r)
PY
}

db_has_row_text() {
  # Usage: db_has_row_text <substring>  ->  prints "1" if any row_text contains it.
  "$PYTHON_BIN" - "$K2B_INDEX_DB" "$1" <<'PY'
import sqlite3, sys
c = sqlite3.connect(sys.argv[1])
needle = sys.argv[2]
r = c.execute("SELECT 1 FROM rows WHERE row_text LIKE ?", (f"%{needle}%",)).fetchone()
print("1" if r else "0")
PY
}

db_fts_has() {
  # FTS5 virtual table must also reflect the row_text (trigger-synced).
  "$PYTHON_BIN" - "$K2B_INDEX_DB" "$1" <<'PY'
import sqlite3, sys
c = sqlite3.connect(sys.argv[1])
needle = sys.argv[2]
try:
    r = c.execute("SELECT 1 FROM rows_fts WHERE rows_fts MATCH ?", (needle,)).fetchone()
except sqlite3.OperationalError:
    r = None
print("1" if r else "0")
PY
}

# --- Test 1: empty shelf (file does not exist) -> 0 rows, no crash ---
"$PYTHON_BIN" "$INDEXER" --shelf nonexistent \
  || fail "test 1: indexer exited non-zero on missing shelf"
COUNT="$(db_row_count)"
[ "$COUNT" = "0" ] || fail "test 1: expected 0 rows for missing shelf, got $COUNT"

# Also: shelf file exists but has zero row bullets -> 0 rows.
"$WRITER" --shelf drain --date 2026-04-22 --type fact --slug seed --attr "k:v" \
  || fail "test 1b: seed write failed"
# Wipe the bullets but keep the frontmatter + headers.
DRAIN="$TMPDIR/drain.md"
"$PYTHON_BIN" - "$DRAIN" <<'PY'
import sys, re
p = sys.argv[1]
text = open(p).read()
lines = text.splitlines()
kept = []
in_rows = False
for line in lines:
    if line.startswith("## Rows"):
        in_rows = True
        kept.append(line)
        continue
    if in_rows and line.startswith("- "):
        continue  # drop every bullet
    kept.append(line)
# Reset row-count to 0 in frontmatter.
out = "\n".join(kept)
out = re.sub(r"^row-count:\s*\d+\s*$", "row-count: 0", out, flags=re.M)
open(p, "w").write(out)
PY
"$PYTHON_BIN" "$INDEXER" --shelf drain || fail "test 1b: indexer crashed on empty-body shelf"
DRAIN_COUNT="$("$PYTHON_BIN" - "$K2B_INDEX_DB" <<'PY'
import sqlite3, sys
c = sqlite3.connect(sys.argv[1])
print(c.execute("SELECT COUNT(*) FROM rows WHERE shelf='drain'").fetchone()[0])
PY
)"
[ "$DRAIN_COUNT" = "0" ] || fail "test 1b: expected 0 rows for body-empty shelf, got $DRAIN_COUNT"

# --- Test 2: 5 rows -> 5 index entries, all row_hash unique ---
"$WRITER" --shelf semantic --date 2026-04-01 --type contact \
  --slug person_Alice --attr "tel:111 1111" --attr "role:Alpha"
"$WRITER" --shelf semantic --date 2026-04-02 --type contact \
  --slug person_Bob --attr "tel:222 2222" --attr "role:Beta"
"$WRITER" --shelf semantic --date 2026-04-03 --type contact \
  --slug person_Carol --attr "tel:333 3333" --attr "role:Gamma"
"$WRITER" --shelf semantic --date 2026-04-04 --type fact \
  --slug meeting_kickoff --attr "when:2026-04-20" --attr "where:HQ"
"$WRITER" --shelf semantic --date 2026-04-05 --type preference \
  --slug tone_no_em_dash --attr "value:enforced"

"$PYTHON_BIN" "$INDEXER" --shelf semantic || fail "test 2: indexer failed on 5-row shelf"

COUNT="$("$PYTHON_BIN" - "$K2B_INDEX_DB" <<'PY'
import sqlite3, sys
c = sqlite3.connect(sys.argv[1])
print(c.execute("SELECT COUNT(*) FROM rows WHERE shelf='semantic'").fetchone()[0])
PY
)"
[ "$COUNT" = "5" ] || fail "test 2: expected 5 rows, got $COUNT"

UNIQ="$("$PYTHON_BIN" - "$K2B_INDEX_DB" <<'PY'
import sqlite3, sys
c = sqlite3.connect(sys.argv[1])
print(c.execute("SELECT COUNT(DISTINCT row_hash) FROM rows WHERE shelf='semantic'").fetchone()[0])
PY
)"
[ "$UNIQ" = "5" ] || fail "test 2: expected 5 distinct row_hashes, got $UNIQ"

# Embedding must be non-empty + decodable as float32 x 384.
EMB_CHECK="$("$PYTHON_BIN" - "$K2B_INDEX_DB" <<'PY'
import sqlite3, sys
import numpy as np
c = sqlite3.connect(sys.argv[1])
rows = c.execute("SELECT embedding FROM rows WHERE shelf='semantic'").fetchall()
ok = all(np.frombuffer(b[0], dtype='float32').shape == (384,) for b in rows)
print("ok" if ok else "bad")
PY
)"
[ "$EMB_CHECK" = "ok" ] || fail "test 2: embeddings are not float32 x 384"

# FTS5 is populated by trigger on INSERT.
HAS_ALPHA="$(db_fts_has 'Alpha')"
[ "$HAS_ALPHA" = "1" ] || fail "test 2: FTS5 missing 'Alpha' (triggers not wired)"

# --- Test 3: re-index same shelf state -> zero-delta ---
SNAP_BEFORE="$(db_snapshot)"
sleep 1.1  # so updated_at would differ if any row were UPDATEd
"$PYTHON_BIN" "$INDEXER" --shelf semantic || fail "test 3: re-index failed"
SNAP_AFTER="$(db_snapshot)"
[ "$SNAP_BEFORE" = "$SNAP_AFTER" ] \
  || fail "test 3: re-index changed (id, row_hash) snapshot (not idempotent)"

TS_CHANGED="$("$PYTHON_BIN" - "$K2B_INDEX_DB" <<'PY'
import sqlite3, sys
c = sqlite3.connect(sys.argv[1])
rows = c.execute("SELECT created_at, updated_at FROM rows WHERE shelf='semantic'").fetchall()
bumped = sum(1 for ca, ua in rows if ua != ca)
print(bumped)
PY
)"
[ "$TS_CHANGED" = "0" ] || fail "test 3: $TS_CHANGED rows got updated_at != created_at on idempotent re-index"

# --- Test 4: edit a row in the .md -> old index entry replaced, not duplicated ---
SEM="$TMPDIR/semantic.md"
"$PYTHON_BIN" - "$SEM" <<'PY'
import sys
p = sys.argv[1]
t = open(p).read()
# Change Alice's tel from "111 1111" to "111 9999" in-place.
t2 = t.replace("tel:111 1111", "tel:111 9999")
assert t2 != t, "edit seed not present; test precondition broken"
open(p, "w").write(t2)
PY
"$PYTHON_BIN" "$INDEXER" --shelf semantic || fail "test 4: indexer failed after edit"

COUNT="$("$PYTHON_BIN" - "$K2B_INDEX_DB" <<'PY'
import sqlite3, sys
c = sqlite3.connect(sys.argv[1])
print(c.execute("SELECT COUNT(*) FROM rows WHERE shelf='semantic'").fetchone()[0])
PY
)"
[ "$COUNT" = "5" ] || fail "test 4: row count changed from 5 after edit (got $COUNT); edit should replace, not duplicate"

HAS_NEW="$(db_has_row_text '111 9999')"
[ "$HAS_NEW" = "1" ] || fail "test 4: edited row text not present in index"

HAS_OLD="$(db_has_row_text '111 1111')"
[ "$HAS_OLD" = "0" ] || fail "test 4: stale row text still present (edit duplicated instead of replaced)"

# FTS5 trigger must reflect the swap.
FTS_NEW="$(db_fts_has '9999')"
[ "$FTS_NEW" = "1" ] || fail "test 4: FTS5 missing edited token '9999'"

# Counts of person_Alice rows in DB: exactly 1 (old deleted, new inserted).
ALICE="$("$PYTHON_BIN" - "$K2B_INDEX_DB" <<'PY'
import sqlite3, sys
c = sqlite3.connect(sys.argv[1])
print(c.execute("SELECT COUNT(*) FROM rows WHERE shelf='semantic' AND row_text LIKE '%person_Alice%'").fetchone()[0])
PY
)"
[ "$ALICE" = "1" ] || fail "test 4: expected 1 Alice row after edit, got $ALICE"

# --- Test 5: delete a row from the .md -> removed from index on reindex ---
"$PYTHON_BIN" - "$SEM" <<'PY'
import sys
p = sys.argv[1]
text = open(p).read()
# Drop the Bob row; also decrement the row-count frontmatter field.
lines = text.splitlines()
kept = []
removed = False
for line in lines:
    if (not removed) and line.startswith("- ") and "person_Bob" in line:
        removed = True
        continue
    kept.append(line)
assert removed, "delete seed not found; test precondition broken"
out = "\n".join(kept)
import re
out = re.sub(r"^row-count:\s*\d+\s*$", "row-count: 4", out, flags=re.M)
open(p, "w").write(out)
PY
"$PYTHON_BIN" "$INDEXER" --shelf semantic || fail "test 5: indexer failed after delete"

COUNT="$("$PYTHON_BIN" - "$K2B_INDEX_DB" <<'PY'
import sqlite3, sys
c = sqlite3.connect(sys.argv[1])
print(c.execute("SELECT COUNT(*) FROM rows WHERE shelf='semantic'").fetchone()[0])
PY
)"
[ "$COUNT" = "4" ] || fail "test 5: expected 4 rows after delete, got $COUNT"

STILL="$(db_has_row_text 'person_Bob')"
[ "$STILL" = "0" ] || fail "test 5: deleted row still present in index"

FTS_STILL="$(db_fts_has 'Beta')"
[ "$FTS_STILL" = "0" ] || fail "test 5: FTS5 still returns deleted row's tokens (delete trigger not wired)"

# --- Test 6: pipe in an attribute value round-trips through serialize/parse ---
# Codex HIGH 2026-04-22 flagged that the previous embed-index serializer
# hand-concatenated " | " between raw values, which corrupts rows whose
# values legitimately contain a pipe (shelf_rows escapes them as \|).
# The canonical path must survive a pipe in the value: row_hash stays
# stable across reindex, the stored row_text escapes the pipe, the FTS
# row indexes the unescaped value tokens, and the slug extractor returns
# the correct field.
"$WRITER" --shelf pipes --date 2026-04-21 --type fact \
  --slug pipe_case --attr "phone:2830 3709 | ext 5" --attr "note:alpha|beta|gamma" \
  || fail "test 6 precondition: shelf-writer.sh rejected pipe-containing value"
"$PYTHON_BIN" "$INDEXER" --shelf pipes || fail "test 6: indexer failed on pipe-value shelf"

PIPE_DB="$("$PYTHON_BIN" - "$K2B_INDEX_DB" <<'PY'
import sqlite3, sys
c = sqlite3.connect(sys.argv[1])
r = c.execute(
    "SELECT row_text, row_hash FROM rows WHERE shelf='pipes'"
).fetchone()
if r is None:
    print("MISSING")
else:
    print(r[0])
    print(r[1])
PY
)"
[ "$PIPE_DB" != "MISSING" ] || fail "test 6: pipe-value row absent from index"

STORED_TEXT="$(echo "$PIPE_DB" | sed -n 1p)"
STORED_HASH="$(echo "$PIPE_DB" | sed -n 2p)"

# Canonical serializer escapes pipes in values. The stored row_text MUST
# contain the backslash-escaped form, not the raw pipe-inside-value.
case "$STORED_TEXT" in
  *'ext 5'*'alpha'*'gamma'*) : ;;  # content present in some form
  *) fail "test 6: stored row_text missing expected tokens: $STORED_TEXT" ;;
esac
echo "$STORED_TEXT" | grep -Fq 'phone:2830 3709 \| ext 5' \
  || fail "test 6: pipe in 'phone' value not escaped as \\| in stored row_text: $STORED_TEXT"
echo "$STORED_TEXT" | grep -Fq 'note:alpha\|beta\|gamma' \
  || fail "test 6: pipes in 'note' value not escaped in stored row_text: $STORED_TEXT"

# Re-run indexer: row_hash must stay stable (same canonical text in, same
# hash out). Any accidental difference in serialization would flip this.
"$PYTHON_BIN" "$INDEXER" --shelf pipes || fail "test 6: idempotent reindex failed"
RE_HASH="$("$PYTHON_BIN" - "$K2B_INDEX_DB" <<'PY'
import sqlite3, sys
c = sqlite3.connect(sys.argv[1])
print(c.execute("SELECT row_hash FROM rows WHERE shelf='pipes'").fetchone()[0])
PY
)"
[ "$RE_HASH" = "$STORED_HASH" ] \
  || fail "test 6: row_hash drifted across reindex (before=$STORED_HASH after=$RE_HASH)"

# Retriever must return the correct slug even though the stored text
# contains " | " sequences INSIDE an attribute value. A naive split on
# " | " would mistake those for field boundaries and return the wrong slug.
SLUG="$("$PYTHON_BIN" "$RETRIEVER" "pipe_case phone alpha" --shelf pipes --k 3 \
         | "$PYTHON_BIN" -c 'import json, sys; d=json.load(sys.stdin); print(d[0]["slug"] if d else "EMPTY")')"
[ "$SLUG" = "pipe_case" ] \
  || fail "test 6: retrieve.py extracted wrong slug for pipe-value row: got '$SLUG'"

# --- Test 7: parse error on one bullet must skip ALL writes for the shelf ---
# Codex HIGH pass 1 + pass 2 2026-04-22. The original code treated a
# ValueError during parse as a pure skip and then deleted every hash in
# the DB that was not in the (silently-truncated) parsed set -- silent
# data loss. The first fix suppressed deletes but kept inserts; that
# trade-off opened a second hole: an edited existing row would have its
# new row_hash inserted while the old row_hash stayed (delete suppressed
# by the partial-failure branch), so retrieval returned BOTH the stale
# and current version of the same record.
#
# Current contract: any parse error means the shelf is untrustworthy ->
# skip every write (insert AND delete) until the author fixes the file.
# This test exercises the exact danger path: an existing row is edited,
# a new valid row is added, and a malformed bullet is appended. All three
# writes must be skipped.
GUARD_SHELF="$TMPDIR/preserve.md"
cat >"$GUARD_SHELF" <<'EOF'
---
tags: [context, shelf, preserve, washing-machine]
type: shelf
shelf: preserve
row-count: 2
up: "[[index]]"
---

# Preserve shelf

## Rows

- 2026-04-21 | fact | keep_alpha | k:v1
- 2026-04-22 | fact | keep_beta | k:v2
EOF
"$PYTHON_BIN" "$INDEXER" --shelf preserve || fail "test 7 precondition: initial index failed"

SNAP_BEFORE="$("$PYTHON_BIN" - "$K2B_INDEX_DB" <<'PY'
import sqlite3, sys
c = sqlite3.connect(sys.argv[1])
rows = sorted(c.execute(
    "SELECT row_hash, row_text FROM rows WHERE shelf='preserve'"
).fetchall())
for h, t in rows:
    print(h, t)
PY
)"
BEFORE_COUNT="$(echo "$SNAP_BEFORE" | wc -l | tr -d ' ')"
[ "$BEFORE_COUNT" = "2" ] \
  || fail "test 7 precondition: expected 2 rows before mutation, got $BEFORE_COUNT"

# Rewrite the shelf on disk with three simultaneous changes:
#   * EDIT: keep_alpha's attr value changes v1 -> v1_edited (new row_hash)
#   * INSERT: a new valid row keep_gamma appears
#   * ERROR: a malformed bullet sits at the end (missing slug field)
# All three writes must be suppressed because the shelf as a whole is
# not trustworthy until the malformed bullet is fixed.
cat >"$GUARD_SHELF" <<'EOF'
---
tags: [context, shelf, preserve, washing-machine]
type: shelf
shelf: preserve
row-count: 3
up: "[[index]]"
---

# Preserve shelf

## Rows

- 2026-04-21 | fact | keep_alpha | k:v1_edited
- 2026-04-22 | fact | keep_beta | k:v2
- 2026-04-24 | fact | keep_gamma | k:v3
- 2026-04-23 | broken-because-missing-slug-field
EOF

STDERR_FILE="$TMPDIR/test7.stderr"
"$PYTHON_BIN" "$INDEXER" --shelf preserve 2>"$STDERR_FILE" \
  || fail "test 7: indexer exited non-zero on partially-malformed shelf"

grep -q 'malformed row' "$STDERR_FILE" \
  || fail "test 7: indexer did not warn about malformed row on stderr: $(cat "$STDERR_FILE")"

SNAP_AFTER="$("$PYTHON_BIN" - "$K2B_INDEX_DB" <<'PY'
import sqlite3, sys
c = sqlite3.connect(sys.argv[1])
rows = sorted(c.execute(
    "SELECT row_hash, row_text FROM rows WHERE shelf='preserve'"
).fetchall())
for h, t in rows:
    print(h, t)
PY
)"

[ "$SNAP_BEFORE" = "$SNAP_AFTER" ] || fail "test 7: DB state changed despite parse error -- contract violated.
BEFORE: $SNAP_BEFORE
AFTER:  $SNAP_AFTER"

# Explicit checks against the three specific risks the fix is guarding:
#   * old version of edited row must still be present (no silent delete)
#   * new version of edited row must NOT be present (no dup/stale pair)
#   * the newly valid keep_gamma row must NOT have been inserted
OLD_ALPHA="$("$PYTHON_BIN" - "$K2B_INDEX_DB" <<'PY'
import sqlite3, sys
c = sqlite3.connect(sys.argv[1])
r = c.execute(
    "SELECT 1 FROM rows WHERE shelf='preserve' AND row_text LIKE '%keep_alpha%' AND row_text LIKE '%v1%' AND row_text NOT LIKE '%v1_edited%'"
).fetchone()
print("1" if r else "0")
PY
)"
[ "$OLD_ALPHA" = "1" ] || fail "test 7: stale keep_alpha row dropped despite parse error (silent delete)"

NEW_ALPHA="$("$PYTHON_BIN" - "$K2B_INDEX_DB" <<'PY'
import sqlite3, sys
c = sqlite3.connect(sys.argv[1])
r = c.execute(
    "SELECT 1 FROM rows WHERE shelf='preserve' AND row_text LIKE '%keep_alpha%' AND row_text LIKE '%v1_edited%'"
).fetchone()
print("1" if r else "0")
PY
)"
[ "$NEW_ALPHA" = "0" ] \
  || fail "test 7: edited keep_alpha version was inserted despite parse error -- stale+current duplication"

GAMMA="$("$PYTHON_BIN" - "$K2B_INDEX_DB" <<'PY'
import sqlite3, sys
c = sqlite3.connect(sys.argv[1])
r = c.execute(
    "SELECT 1 FROM rows WHERE shelf='preserve' AND row_text LIKE '%keep_gamma%'"
).fetchone()
print("1" if r else "0")
PY
)"
[ "$GAMMA" = "0" ] || fail "test 7: new keep_gamma row was inserted despite parse error"

# Finally, after the author fixes the malformed line, a re-index must
# complete all three pending writes (edit applied, new row inserted,
# nothing stale left behind).
cat >"$GUARD_SHELF" <<'EOF'
---
tags: [context, shelf, preserve, washing-machine]
type: shelf
shelf: preserve
row-count: 3
up: "[[index]]"
---

# Preserve shelf

## Rows

- 2026-04-21 | fact | keep_alpha | k:v1_edited
- 2026-04-22 | fact | keep_beta | k:v2
- 2026-04-24 | fact | keep_gamma | k:v3
EOF
"$PYTHON_BIN" "$INDEXER" --shelf preserve || fail "test 7: fixed-shelf reindex failed"

RECOVERED="$("$PYTHON_BIN" - "$K2B_INDEX_DB" <<'PY'
import sqlite3, sys
c = sqlite3.connect(sys.argv[1])
n = c.execute("SELECT COUNT(*) FROM rows WHERE shelf='preserve'").fetchone()[0]
alpha_new = c.execute(
    "SELECT 1 FROM rows WHERE shelf='preserve' AND row_text LIKE '%keep_alpha%' AND row_text LIKE '%v1_edited%'"
).fetchone()
alpha_old = c.execute(
    "SELECT 1 FROM rows WHERE shelf='preserve' AND row_text LIKE '%keep_alpha%' AND row_text NOT LIKE '%v1_edited%'"
).fetchone()
gamma = c.execute("SELECT 1 FROM rows WHERE shelf='preserve' AND row_text LIKE '%keep_gamma%'").fetchone()
print(n, 1 if alpha_new else 0, 1 if alpha_old else 0, 1 if gamma else 0)
PY
)"
[ "$RECOVERED" = "3 1 0 1" ] \
  || fail "test 7: after fixing the shelf, reconcile did not land cleanly (got: $RECOVERED; want: '3 1 0 1')"

# --- Test 8: missing shelf file with existing indexed rows must skip writes ---
# Codex HIGH pass 3 2026-04-22. Before this guard, a shelf file that
# disappeared (path typo, corruption, wrong vault root) was treated as
# "authoritative empty" and the reconcile phase deleted every previously
# indexed row for that shelf. The fix treats an absent file as an
# untrustworthy input whenever the DB has rows for that shelf.
VANISH_DIR="$TMPDIR/vanish"
mkdir -p "$VANISH_DIR"
cat >"$VANISH_DIR/vanish.md" <<'EOF'
---
tags: [context, shelf, vanish, washing-machine]
type: shelf
shelf: vanish
row-count: 1
up: "[[index]]"
---

# Vanish shelf

## Rows

- 2026-04-21 | fact | vanish_seed | k:v
EOF
K2B_SHELVES_DIR="$VANISH_DIR" "$PYTHON_BIN" "$INDEXER" --shelf vanish \
  || fail "test 8 precondition: initial index on vanish shelf failed"

BEFORE="$("$PYTHON_BIN" - "$K2B_INDEX_DB" <<'PY'
import sqlite3, sys
c = sqlite3.connect(sys.argv[1])
print(c.execute("SELECT COUNT(*) FROM rows WHERE shelf='vanish'").fetchone()[0])
PY
)"
[ "$BEFORE" = "1" ] || fail "test 8 precondition: expected 1 vanish row, got $BEFORE"

# Simulate the file vanishing.
rm "$VANISH_DIR/vanish.md"

STDERR_FILE="$TMPDIR/test8.stderr"
K2B_SHELVES_DIR="$VANISH_DIR" "$PYTHON_BIN" "$INDEXER" --shelf vanish 2>"$STDERR_FILE" \
  || fail "test 8: indexer exited non-zero when shelf file missing"

grep -qi 'missing' "$STDERR_FILE" \
  || fail "test 8: indexer did not warn about missing shelf file on stderr: $(cat "$STDERR_FILE")"

AFTER="$("$PYTHON_BIN" - "$K2B_INDEX_DB" <<'PY'
import sqlite3, sys
c = sqlite3.connect(sys.argv[1])
print(c.execute("SELECT COUNT(*) FROM rows WHERE shelf='vanish'").fetchone()[0])
PY
)"
[ "$AFTER" = "1" ] \
  || fail "test 8: vanish shelf was wiped after file disappeared (got $AFTER rows, want 1)"

# First-time missing shelf with zero indexed rows must still be a harmless
# no-op -- the guard fires ONLY when prior rows exist.
K2B_SHELVES_DIR="$VANISH_DIR" "$PYTHON_BIN" "$INDEXER" --shelf neverexisted 2>/dev/null \
  || fail "test 8: indexer failed on never-existed shelf (should be a no-op)"

# --- Test 9: non-`-` bullets under ## Rows must freeze writes, not be skipped ---
# Codex HIGH pass 3 2026-04-22. The old line-filter only parsed bullets
# that literally started with "- ". An asterisk bullet, indented bullet,
# or stray prose under ## Rows was silently ignored and the reconcile
# phase deleted every previously indexed row that was missing from the
# truncated parsed set. Fix: every non-empty, non-header line inside the
# Rows section is routed through the parser, and unparseable ones are
# recorded as errors (which then trigger the "skip all writes" guard).
ALT_SHELF="$TMPDIR/alt.md"
cat >"$ALT_SHELF" <<'EOF'
---
tags: [context, shelf, alt, washing-machine]
type: shelf
shelf: alt
row-count: 2
up: "[[index]]"
---

# Alt shelf

## Rows

- 2026-04-21 | fact | alt_seed_a | k:v1
- 2026-04-22 | fact | alt_seed_b | k:v2
EOF
"$PYTHON_BIN" "$INDEXER" --shelf alt || fail "test 9 precondition: initial index failed"

ALT_SNAP_BEFORE="$("$PYTHON_BIN" - "$K2B_INDEX_DB" <<'PY'
import sqlite3, sys
c = sqlite3.connect(sys.argv[1])
for r in sorted(c.execute("SELECT row_hash, row_text FROM rows WHERE shelf='alt'").fetchall()):
    print(r[0], r[1])
PY
)"
[ "$(echo "$ALT_SNAP_BEFORE" | wc -l | tr -d ' ')" = "2" ] \
  || fail "test 9 precondition: expected 2 alt rows before mutation"

# Replace shelf contents with a legitimate row PLUS an asterisk-bullet
# that should be flagged as malformed, not silently skipped.
cat >"$ALT_SHELF" <<'EOF'
---
tags: [context, shelf, alt, washing-machine]
type: shelf
shelf: alt
row-count: 1
up: "[[index]]"
---

# Alt shelf

## Rows

- 2026-04-21 | fact | alt_seed_a | k:v1
* 2026-04-23 | fact | asterisk_bullet | k:v3
EOF

ALT_STDERR="$TMPDIR/test9.stderr"
"$PYTHON_BIN" "$INDEXER" --shelf alt 2>"$ALT_STDERR" \
  || fail "test 9: indexer exited non-zero on asterisk-bullet shelf"

grep -q 'malformed row' "$ALT_STDERR" \
  || fail "test 9: indexer did not warn about the asterisk bullet on stderr: $(cat "$ALT_STDERR")"

ALT_SNAP_AFTER="$("$PYTHON_BIN" - "$K2B_INDEX_DB" <<'PY'
import sqlite3, sys
c = sqlite3.connect(sys.argv[1])
for r in sorted(c.execute("SELECT row_hash, row_text FROM rows WHERE shelf='alt'").fetchall()):
    print(r[0], r[1])
PY
)"
[ "$ALT_SNAP_BEFORE" = "$ALT_SNAP_AFTER" ] \
  || fail "test 9: DB state changed despite asterisk-bullet parse error.
BEFORE: $ALT_SNAP_BEFORE
AFTER:  $ALT_SNAP_AFTER"

# Indented but otherwise well-formed bullets should still parse cleanly
# (we normalise leading whitespace). Sanity check so we do not overshoot
# and start rejecting legitimate authorships.
INDENT_SHELF="$TMPDIR/indent.md"
cat >"$INDENT_SHELF" <<'EOF'
---
tags: [context, shelf, indent, washing-machine]
type: shelf
shelf: indent
row-count: 1
up: "[[index]]"
---

# Indent shelf

## Rows

  - 2026-04-21 | fact | indent_row | k:v
EOF
"$PYTHON_BIN" "$INDEXER" --shelf indent 2>/dev/null \
  || fail "test 9: indexer rejected an indented (but otherwise valid) bullet"
INDENT_COUNT="$("$PYTHON_BIN" - "$K2B_INDEX_DB" <<'PY'
import sqlite3, sys
c = sqlite3.connect(sys.argv[1])
print(c.execute("SELECT COUNT(*) FROM rows WHERE shelf='indent'").fetchone()[0])
PY
)"
[ "$INDENT_COUNT" = "1" ] \
  || fail "test 9: indented valid bullet not indexed (got $INDENT_COUNT rows, want 1)"

echo "embed-index.test.sh: all 9 tests passed"
