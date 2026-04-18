#!/usr/bin/env bash
# tests/promote-learnings-importance.test.sh
# Test that promote-learnings.py orders candidates by importance score DESC.
#
# Score: (reinforcement_count * max(1, access_count)) / max(1, age_in_days)
# Age anchor for promote candidates: `- **Date:** YYYY-MM-DD` bullet.
# Access count source: access_counts.tsv (env override via K2B_ACCESS_COUNTS_TSV).
#
# Env overrides for testing (must be supported by the script):
#   K2B_LEARNINGS_FILE        path to learnings file
#   K2B_ACTIVE_RULES_FILE     path to active_rules.md
#   K2B_ACCESS_COUNTS_TSV     path to access_counts.tsv
#   K2B_TODAY                 ISO today override (for deterministic ages)

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
HELPER="$REPO_ROOT/scripts/promote-learnings.py"

TMPROOT="$(mktemp -d)"
trap 'rm -rf "$TMPROOT"' EXIT

fail() { echo "FAIL: $*" >&2; exit 1; }

seed_learnings() {
  # Three entries, all reinforced >= 3 so all are candidates.
  # Different (Reinforced, Date) to produce distinct scores with same access.
  # - L-A: Reinforced=3, Date=2026-04-18 -> age 1d, score = 3*max(1,A)/1
  # - L-B: Reinforced=3, Date=2026-04-12 -> age 7d, score = 3*max(1,A)/7
  # - L-C: Reinforced=6, Date=2026-04-12 -> age 7d, score = 6*max(1,A)/7
  cat > "$1" <<'EOF'
# Self-Improvement Learnings

### L-2026-04-18-001
- **Area:** workflow
- **Distilled rule:** Rule A
- **Learning:** Very recent.
- **Context:** ctx
- **Reinforced:** 3
- **Confidence:** medium
- **Date:** 2026-04-18

### L-2026-04-12-002
- **Area:** workflow
- **Distilled rule:** Rule B
- **Learning:** Older low-reinforced.
- **Context:** ctx
- **Reinforced:** 3
- **Confidence:** medium
- **Date:** 2026-04-12

### L-2026-04-12-003
- **Area:** workflow
- **Distilled rule:** Rule C
- **Learning:** Older but heavily reinforced.
- **Context:** ctx
- **Reinforced:** 6
- **Confidence:** high
- **Date:** 2026-04-12
EOF
}

seed_active_rules() {
  cat > "$1" <<'EOF'
---
name: K2B Active Rules
type: feedback
---

# K2B Active Rules

Cap: 12 rules
Last promoted: 2026-04-19

EOF
}

seed_tsv() {
  # Usage: seed_tsv <path> "<lid>:<count>" ...
  local path="$1"
  shift
  {
    echo "# access_counts.tsv -- test fixture"
    printf 'learn_id\tcount\tlast_accessed\n'
    for pair in "$@"; do
      local lid="${pair%%:*}"
      local cnt="${pair##*:}"
      printf '%s\t%s\t2026-04-18\n' "$lid" "$cnt"
    done
  } > "$path"
}

# Parse the JSON array in stdout and return L-IDs in order, newline-separated.
extract_order() {
  python3 -c "
import json, sys
data = json.load(sys.stdin)
for c in data:
    print(c['learn_id'])
"
}

# --- Test 1: access counts 0 across the board -- ordering is r/age ------
# Expected scores (K2B_TODAY=2026-04-19):
#   L-A: 3 * max(1,0) / max(1, 1)  = 3.0
#   L-B: 3 * max(1,0) / max(1, 7)  ≈ 0.429
#   L-C: 6 * max(1,0) / max(1, 7)  ≈ 0.857
# DESC: A > C > B
TSV="$TMPROOT/t1.tsv"
L_FILE="$TMPROOT/t1_learnings.md"
A_FILE="$TMPROOT/t1_rules.md"
seed_learnings "$L_FILE"
seed_active_rules "$A_FILE"
seed_tsv "$TSV"  # no rows = all counts=0

order=$(K2B_TODAY=2026-04-19 \
  K2B_LEARNINGS_FILE="$L_FILE" \
  K2B_ACTIVE_RULES_FILE="$A_FILE" \
  K2B_ACCESS_COUNTS_TSV="$TSV" \
  "$HELPER" | extract_order | tr '\n' ' ')
expected="L-2026-04-18-001 L-2026-04-12-003 L-2026-04-12-002 "
[ "$order" = "$expected" ] || fail "test1: expected [$expected], got [$order]"

# --- Test 2: access boost lifts L-B above L-C ---------------------------
# With L-B access=50:
#   L-A: 3*max(1,0)/1  = 3.0
#   L-B: 3*max(1,50)/7 ≈ 21.43
#   L-C: 6*max(1,0)/7  ≈ 0.857
# DESC: B > A > C
TSV="$TMPROOT/t2.tsv"
L_FILE="$TMPROOT/t2_learnings.md"
A_FILE="$TMPROOT/t2_rules.md"
seed_learnings "$L_FILE"
seed_active_rules "$A_FILE"
seed_tsv "$TSV" "L-2026-04-12-002:50"

order=$(K2B_TODAY=2026-04-19 \
  K2B_LEARNINGS_FILE="$L_FILE" \
  K2B_ACTIVE_RULES_FILE="$A_FILE" \
  K2B_ACCESS_COUNTS_TSV="$TSV" \
  "$HELPER" | extract_order | tr '\n' ' ')
expected="L-2026-04-12-002 L-2026-04-18-001 L-2026-04-12-003 "
[ "$order" = "$expected" ] || fail "test2: access boost -- expected [$expected], got [$order]"

# --- Test 3: candidates skipped if reinforced < 3 (unchanged from v1) ---
L_FILE="$TMPROOT/t3_learnings.md"
A_FILE="$TMPROOT/t3_rules.md"
TSV="$TMPROOT/t3.tsv"
cat > "$L_FILE" <<'EOF'
### L-2026-04-15-001
- **Reinforced:** 2
- **Distilled rule:** Too low
- **Date:** 2026-04-15

### L-2026-04-15-002
- **Reinforced:** 3
- **Distilled rule:** Just over
- **Date:** 2026-04-15
EOF
seed_active_rules "$A_FILE"
seed_tsv "$TSV"

order=$(K2B_TODAY=2026-04-19 \
  K2B_LEARNINGS_FILE="$L_FILE" \
  K2B_ACTIVE_RULES_FILE="$A_FILE" \
  K2B_ACCESS_COUNTS_TSV="$TSV" \
  "$HELPER" | extract_order | tr '\n' ' ')
expected="L-2026-04-15-002 "
[ "$order" = "$expected" ] || fail "test3: low-reinforced must be skipped -- expected [$expected], got [$order]"

# --- Test 4: missing `Date:` bullet -> age floored to 1 -----------------
# L-X (reinforced=3, no Date:) should score 3*1/1 = 3.0 (same as A above).
L_FILE="$TMPROOT/t4_learnings.md"
A_FILE="$TMPROOT/t4_rules.md"
TSV="$TMPROOT/t4.tsv"
cat > "$L_FILE" <<'EOF'
### L-2026-04-12-004
- **Reinforced:** 3
- **Distilled rule:** No date
- **Learning:** Missing date anchor.

### L-2026-04-12-005
- **Reinforced:** 3
- **Distilled rule:** Old
- **Date:** 2026-03-19
EOF
seed_active_rules "$A_FILE"
seed_tsv "$TSV"

order=$(K2B_TODAY=2026-04-19 \
  K2B_LEARNINGS_FILE="$L_FILE" \
  K2B_ACTIVE_RULES_FILE="$A_FILE" \
  K2B_ACCESS_COUNTS_TSV="$TSV" \
  "$HELPER" | extract_order | tr '\n' ' ')
# Expected: L-004 (3/1 = 3.0) > L-005 (3/31 ≈ 0.097)
expected="L-2026-04-12-004 L-2026-04-12-005 "
[ "$order" = "$expected" ] || fail "test4: missing Date: should floor to age=1 -- expected [$expected], got [$order]"

# --- Test 5: JSON schema still includes all expected keys --------------
L_FILE="$TMPROOT/t5_learnings.md"
A_FILE="$TMPROOT/t5_rules.md"
TSV="$TMPROOT/t5.tsv"
seed_learnings "$L_FILE"
seed_active_rules "$A_FILE"
seed_tsv "$TSV"

K2B_TODAY=2026-04-19 \
  K2B_LEARNINGS_FILE="$L_FILE" \
  K2B_ACTIVE_RULES_FILE="$A_FILE" \
  K2B_ACCESS_COUNTS_TSV="$TSV" \
  "$HELPER" > "$TMPROOT/t5.json"

python3 -c "
import json, sys
data = json.load(open('$TMPROOT/t5.json'))
required = {'learn_id','count','distilled_rule','source_excerpt','already_in_active_rules','rejected','would_exceed_cap','current_active_count','cap'}
for c in data:
    missing = required - set(c.keys())
    if missing:
        print('missing keys: %s' % missing); sys.exit(1)
" || fail "test5: JSON schema missing required keys"

echo "ALL TESTS PASSED"
