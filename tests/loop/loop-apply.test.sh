#!/usr/bin/env bash
# Integration test for scripts/loop/loop-apply.sh.
# Copies the fixture to a tmp dir, invokes loop-apply, verifies mutations.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
FIXTURE="$ROOT/tests/fixtures/loop-mvp"
TMP="$(mktemp -d)"
TMP_DIRS=("$TMP")
cleanup() {
  local d
  for d in "${TMP_DIRS[@]}"; do
    rm -rf "$d"
  done
}
trap cleanup EXIT
trap 'cleanup; exit 130' INT
trap 'cleanup; exit 143' TERM
trap 'cleanup; exit 129' HUP

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
TMP_DIRS+=("$TMP2")
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
TMP_DIRS+=("$TMP3")
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
TMP_DIRS+=("$TMP4")
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

# EOD capture regression: conflict items route through the same loop-apply
# index, mutate canonical memory on accept, delete the pending JSON, and log.
TMP5="$(mktemp -d)"
TMP_DIRS+=("$TMP5")
mkdir -p "$TMP5/wiki/context/shelves" "$TMP5/wiki/context/observations.archive" "$TMP5/System/memory"
mkdir -p "$TMP5/review" "$TMP5/review/Ready" "$TMP5/Archive/review-archive" "$TMP5/.staging/pending-conflicts"
cat > "$TMP5/wiki/context/observer-candidates.md" <<'EOF'
## Candidate Learnings

EOF
cat > "$TMP5/System/memory/self_improve_learnings.md" <<'EOF'
# Learnings
EOF
cat > "$TMP5/wiki/context/shelves/semantic.md" <<'EOF'
## Rows
- 2026-05-14 | contact | dr-lo-hak-keung | name:Dr. Lo Hak Keung | phone:2840 3709 | dedupe_key:person:dr-lo-hak-keung:phone
EOF
touch "$TMP5/wiki/log.md"
cat > "$TMP5/.staging/pending-conflicts/2026-05-14_c.json" <<'JSON'
{
  "conflict_id": "c",
  "subject": "Dr. Lo Hak Keung",
  "predicate": "phone",
  "existing_value": "2840 3709",
  "existing_source": "wiki/context/shelves/semantic.md:2",
  "new_value": "2830 3709",
  "dedupe_key": "person:dr-lo-hak-keung:phone",
  "source_session_path": "/tmp/session.jsonl",
  "surfaced_count": 0
}
JSON

K2B_LOOP_CANDIDATES="$TMP5/wiki/context/observer-candidates.md" \
K2B_LOOP_LEARNINGS="$TMP5/System/memory/self_improve_learnings.md" \
K2B_LOOP_ARCHIVE_DIR="$TMP5/wiki/context/observations.archive" \
K2B_LOOP_DEFERS="$TMP5/wiki/context/observer-defers.jsonl" \
K2B_LOOP_REVIEW_DIR="$TMP5/review" \
K2B_LOOP_REVIEW_READY_DIR="$TMP5/review/Ready" \
K2B_LOOP_REVIEW_ARCHIVE_ROOT="$TMP5/Archive/review-archive" \
K2B_LOOP_CONFLICTS_DIR="$TMP5/.staging/pending-conflicts" \
K2B_LOOP_CONFLICT_ARCHIVE_DIR="$TMP5/.staging/conflicts.archive" \
K2B_VAULT_PATH="$TMP5" \
K2B_WIKI_LOG="$TMP5/wiki/log.md" \
K2B_LOOP_DATE=2026-05-14 K2B_LOOP_ACTOR=keith K2B_LOOP_OBSERVER_RUN=test \
"$ROOT/scripts/loop/loop-apply.sh" --accept 1 >/dev/null

grep -q 'phone:2830 3709' "$TMP5/wiki/context/shelves/semantic.md" \
  || { echo "FAIL EOD conflict: semantic value not updated"; exit 1; }
[ ! -f "$TMP5/.staging/pending-conflicts/2026-05-14_c.json" ] \
  || { echo "FAIL EOD conflict: pending JSON not deleted"; exit 1; }
grep -q '  /eod-capture  conflict-accepted  Dr. Lo Hak Keung phone: 2840 3709 -> 2830 3709$' "$TMP5/wiki/log.md" \
  || { echo "FAIL EOD conflict: accept was not logged"; cat "$TMP5/wiki/log.md"; exit 1; }

# Reject routes the same unified conflict index into conflicts.archive and logs.
TMP6="$(mktemp -d)"
TMP_DIRS+=("$TMP6")
mkdir -p "$TMP6/wiki/context/shelves" "$TMP6/wiki/context/observations.archive" "$TMP6/System/memory"
mkdir -p "$TMP6/review" "$TMP6/review/Ready" "$TMP6/Archive/review-archive" "$TMP6/.staging/pending-conflicts"
cat > "$TMP6/wiki/context/observer-candidates.md" <<'EOF'
## Candidate Learnings

EOF
cat > "$TMP6/System/memory/self_improve_learnings.md" <<'EOF'
# Learnings
EOF
touch "$TMP6/wiki/log.md"
cat > "$TMP6/.staging/pending-conflicts/2026-05-14_c.json" <<'JSON'
{
  "conflict_id": "c",
  "subject": "Dr. Lo Hak Keung",
  "predicate": "phone",
  "existing_value": "2840 3709",
  "existing_source": "wiki/context/shelves/semantic.md:2",
  "new_value": "2830 3709",
  "dedupe_key": "person:dr-lo-hak-keung:phone",
  "source_session_path": "/tmp/session.jsonl",
  "surfaced_count": 0
}
JSON

K2B_LOOP_CANDIDATES="$TMP6/wiki/context/observer-candidates.md" \
K2B_LOOP_LEARNINGS="$TMP6/System/memory/self_improve_learnings.md" \
K2B_LOOP_ARCHIVE_DIR="$TMP6/wiki/context/observations.archive" \
K2B_LOOP_DEFERS="$TMP6/wiki/context/observer-defers.jsonl" \
K2B_LOOP_REVIEW_DIR="$TMP6/review" \
K2B_LOOP_REVIEW_READY_DIR="$TMP6/review/Ready" \
K2B_LOOP_REVIEW_ARCHIVE_ROOT="$TMP6/Archive/review-archive" \
K2B_LOOP_CONFLICTS_DIR="$TMP6/.staging/pending-conflicts" \
K2B_LOOP_CONFLICT_ARCHIVE_DIR="$TMP6/.staging/conflicts.archive" \
K2B_VAULT_PATH="$TMP6" \
K2B_WIKI_LOG="$TMP6/wiki/log.md" \
K2B_LOOP_DATE=2026-05-14 K2B_LOOP_ACTOR=keith K2B_LOOP_OBSERVER_RUN=test \
"$ROOT/scripts/loop/loop-apply.sh" --reject 1 >/dev/null

[ ! -f "$TMP6/.staging/pending-conflicts/2026-05-14_c.json" ] \
  || { echo "FAIL EOD conflict reject: pending JSON not deleted"; exit 1; }
[ -f "$TMP6/.staging/conflicts.archive/2026-05-14/2026-05-14_c.json" ] \
  || { echo "FAIL EOD conflict reject: archive JSON missing"; exit 1; }
grep -q '  /eod-capture  conflict-rejected  Dr. Lo Hak Keung phone: kept 2840 3709, rejected 2830 3709$' "$TMP6/wiki/log.md" \
  || { echo "FAIL EOD conflict reject: reject was not logged"; cat "$TMP6/wiki/log.md"; exit 1; }

# Defer routes the same unified conflict index and auto-archives at threshold.
TMP7="$(mktemp -d)"
TMP_DIRS+=("$TMP7")
mkdir -p "$TMP7/wiki/context/observations.archive" "$TMP7/System/memory"
mkdir -p "$TMP7/review" "$TMP7/review/Ready" "$TMP7/Archive/review-archive" "$TMP7/.staging/pending-conflicts"
touch "$TMP7/wiki/log.md"
cat > "$TMP7/wiki/context/observer-candidates.md" <<'EOF'
## Candidate Learnings

EOF
cat > "$TMP7/System/memory/self_improve_learnings.md" <<'EOF'
# Learnings
EOF
cat > "$TMP7/.staging/pending-conflicts/2026-05-14_c.json" <<'JSON'
{
  "conflict_id": "c",
  "subject": "Dr. Lo Hak Keung",
  "predicate": "phone",
  "existing_value": "2840 3709",
  "existing_source": "wiki/context/shelves/semantic.md:2",
  "new_value": "2830 3709",
  "dedupe_key": "person:dr-lo-hak-keung:phone",
  "source_session_path": "/tmp/session.jsonl",
  "surfaced_count": 2
}
JSON
conflict_item_id=$(python3 - <<'PY'
import hashlib
print(hashlib.sha256(b"c").hexdigest()[:8])
PY
)
printf '{"item_id": "%s", "kind": "conflict", "deferred_at": "old"}\n' "$conflict_item_id" \
  > "$TMP7/wiki/context/observer-defers.jsonl"

K2B_LOOP_CANDIDATES="$TMP7/wiki/context/observer-candidates.md" \
K2B_LOOP_LEARNINGS="$TMP7/System/memory/self_improve_learnings.md" \
K2B_LOOP_ARCHIVE_DIR="$TMP7/wiki/context/observations.archive" \
K2B_LOOP_DEFERS="$TMP7/wiki/context/observer-defers.jsonl" \
K2B_LOOP_REVIEW_DIR="$TMP7/review" \
K2B_LOOP_REVIEW_READY_DIR="$TMP7/review/Ready" \
K2B_LOOP_REVIEW_ARCHIVE_ROOT="$TMP7/Archive/review-archive" \
K2B_LOOP_CONFLICTS_DIR="$TMP7/.staging/pending-conflicts" \
K2B_LOOP_CONFLICT_ARCHIVE_DIR="$TMP7/.staging/conflicts.archive" \
K2B_VAULT_PATH="$TMP7" \
K2B_WIKI_LOG="$TMP7/wiki/log.md" \
K2B_LOOP_DATE=2026-05-14 K2B_LOOP_ACTOR=keith K2B_LOOP_OBSERVER_RUN=test \
"$ROOT/scripts/loop/loop-apply.sh" --defer 1 >/dev/null

[ ! -f "$TMP7/.staging/pending-conflicts/2026-05-14_c.json" ] \
  || { echo "FAIL EOD conflict defer: pending JSON not auto-archived"; exit 1; }
grep -q '"archive_reason": "deferred_threshold"' "$TMP7/.staging/conflicts.archive/2026-05-14/2026-05-14_c.json" \
  || { echo "FAIL EOD conflict defer: archive reason missing"; exit 1; }
if grep -q '"kind": "conflict"' "$TMP7/wiki/context/observer-defers.jsonl"; then
  echo "FAIL EOD conflict defer: legacy shared defer counter was not reset"
  exit 1
fi
grep -q '  /eod-capture  conflict-auto-archived  Dr. Lo Hak Keung phone: deferred 3x$' "$TMP7/wiki/log.md" \
  || { echo "FAIL EOD conflict defer: auto-archive was not logged"; cat "$TMP7/wiki/log.md"; exit 1; }

# Direct loop_apply.py must not route conflicts against an implicit live-vault
# fallback; the shell wrapper supplies K2B_VAULT_PATH for normal operation.
TMP8="$(mktemp -d)"
TMP_DIRS+=("$TMP8")
mkdir -p "$TMP8/.staging/pending-conflicts" "$TMP8/wiki/context/observations.archive" "$TMP8/System/memory"
cat > "$TMP8/wiki/context/observer-candidates.md" <<'EOF'
## Candidate Learnings

EOF
cat > "$TMP8/System/memory/self_improve_learnings.md" <<'EOF'
# Learnings
EOF
cat > "$TMP8/.staging/pending-conflicts/2026-05-14_c.json" <<'JSON'
{
  "conflict_id": "c",
  "subject": "Dr. Lo Hak Keung",
  "predicate": "phone",
  "existing_value": "2840 3709",
  "new_value": "2830 3709",
  "dedupe_key": "person:dr-lo-hak-keung:phone",
  "source_session_path": "/tmp/session.jsonl",
  "surfaced_count": 0
}
JSON
set +e
env -u K2B_VAULT_PATH \
    K2B_LOOP_CANDIDATES="$TMP8/wiki/context/observer-candidates.md" \
    K2B_LOOP_LEARNINGS="$TMP8/System/memory/self_improve_learnings.md" \
    K2B_LOOP_ARCHIVE_DIR="$TMP8/wiki/context/observations.archive" \
    K2B_LOOP_CONFLICTS_DIR="$TMP8/.staging/pending-conflicts" \
    K2B_LOOP_DATE=2026-05-14 \
    K2B_LOOP_ACTOR=keith \
    K2B_LOOP_OBSERVER_RUN=test \
    python3 "$ROOT/scripts/loop/loop_apply.py" --accept 1 2>"$TMP8/err.log"
missing_vault_exit=$?
set -e
if [ "$missing_vault_exit" != "2" ]; then
  echo "FAIL EOD conflict direct apply: missing K2B_VAULT_PATH expected exit 2, got $missing_vault_exit"
  exit 1
fi
grep -q 'K2B_VAULT_PATH required' "$TMP8/err.log" \
  || { echo "FAIL EOD conflict direct apply: missing-vault error not surfaced"; cat "$TMP8/err.log"; exit 1; }

TMP9="$(mktemp -d)"
TMP_DIRS+=("$TMP9")
mkdir -p "$TMP9/.staging/pending-conflicts" "$TMP9/not-a-vault"
cat > "$TMP9/wiki-candidates.md" <<'EOF'
## Candidate Learnings

EOF
cat > "$TMP9/learnings.md" <<'EOF'
# Learnings
EOF
cat > "$TMP9/.staging/pending-conflicts/2026-05-14_c.json" <<'JSON'
{
  "conflict_id": "c",
  "subject": "Dr. Lo Hak Keung",
  "predicate": "phone",
  "existing_value": "2840 3709",
  "new_value": "2830 3709",
  "dedupe_key": "person:dr-lo-hak-keung:phone",
  "source_session_path": "/tmp/session.jsonl",
  "surfaced_count": 0
}
JSON
set +e
K2B_LOOP_CANDIDATES="$TMP9/wiki-candidates.md" \
K2B_LOOP_LEARNINGS="$TMP9/learnings.md" \
K2B_LOOP_ARCHIVE_DIR="$TMP9/archive" \
K2B_LOOP_CONFLICTS_DIR="$TMP9/.staging/pending-conflicts" \
K2B_VAULT_PATH="$TMP9/not-a-vault" \
K2B_LOOP_DATE=2026-05-14 \
K2B_LOOP_ACTOR=keith \
K2B_LOOP_OBSERVER_RUN=test \
python3 "$ROOT/scripts/loop/loop_apply.py" --accept 1 2>"$TMP9/err.log"
bad_vault_exit=$?
set -e
if [ "$bad_vault_exit" != "2" ]; then
  echo "FAIL EOD conflict direct apply: malformed vault expected exit 2, got $bad_vault_exit"
  exit 1
fi
grep -q 'K2B_VAULT_PATH is not a K2B vault' "$TMP9/err.log" \
  || { echo "FAIL EOD conflict direct apply: malformed-vault error not surfaced"; cat "$TMP9/err.log"; exit 1; }

TMP10="$(mktemp -d)"
TMP_DIRS+=("$TMP10")
mkdir -p "$TMP10/vault/wiki/context/shelves" "$TMP10/vault/System/memory" "$TMP10/vault/wiki/context/observations.archive" "$TMP10/outside-conflicts"
cat > "$TMP10/wiki-candidates.md" <<'EOF'
## Candidate Learnings

EOF
cat > "$TMP10/learnings.md" <<'EOF'
# Learnings
EOF
cat > "$TMP10/outside-conflicts/2026-05-14_c.json" <<'JSON'
{
  "conflict_id": "c",
  "subject": "Dr. Lo Hak Keung",
  "predicate": "phone",
  "existing_value": "2840 3709",
  "new_value": "2830 3709",
  "dedupe_key": "person:dr-lo-hak-keung:phone",
  "source_session_path": "/tmp/session.jsonl",
  "surfaced_count": 0
}
JSON
set +e
K2B_LOOP_CANDIDATES="$TMP10/wiki-candidates.md" \
K2B_LOOP_LEARNINGS="$TMP10/learnings.md" \
K2B_LOOP_ARCHIVE_DIR="$TMP10/archive" \
K2B_LOOP_CONFLICTS_DIR="$TMP10/outside-conflicts" \
K2B_VAULT_PATH="$TMP10/vault" \
K2B_LOOP_DATE=2026-05-14 \
K2B_LOOP_ACTOR=keith \
K2B_LOOP_OBSERVER_RUN=test \
python3 "$ROOT/scripts/loop/loop_apply.py" --accept 1 2>"$TMP10/err.log"
wrong_conflicts_dir_exit=$?
set -e
if [ "$wrong_conflicts_dir_exit" != "2" ]; then
  echo "FAIL EOD conflict direct apply: wrong conflicts dir expected exit 2, got $wrong_conflicts_dir_exit"
  exit 1
fi
grep -q 'K2B_LOOP_CONFLICTS_DIR must be' "$TMP10/err.log" \
  || { echo "FAIL EOD conflict direct apply: wrong-conflicts-dir error not surfaced"; cat "$TMP10/err.log"; exit 1; }

echo "PASS: loop-apply.test.sh"
