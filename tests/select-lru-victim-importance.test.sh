#!/usr/bin/env bash
# tests/select-lru-victim-importance.test.sh
# Test that select-lru-victim.py uses importance_score ASC and skips non-L rules.
#
# Score: (reinforcement_count * max(1, access_count)) / max(1, age_in_days)
# Age anchor for eviction candidates: `last-reinforced:` from parenthetical.
# Non-L rules (no L-ID in parenthetical) are PINNED -- skipped in sort (P1 #2).
#
# Env overrides for testing:
#   K2B_ACTIVE_RULES_FILE  path to active_rules.md
#   K2B_ACCESS_COUNTS_TSV  path to access_counts.tsv
#   K2B_TODAY              ISO today override

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
HELPER="$REPO_ROOT/scripts/select-lru-victim.py"

TMPROOT="$(mktemp -d)"
trap 'rm -rf "$TMPROOT"' EXIT

fail() { echo "FAIL: $*" >&2; exit 1; }

seed_tsv() {
  local path="$1"
  shift
  {
    echo "# access_counts.tsv"
    printf 'learn_id\tcount\tlast_accessed\n'
    for pair in "$@"; do
      local lid="${pair%%:*}"
      local cnt="${pair##*:}"
      printf '%s\t%s\t2026-04-18\n' "$lid" "$cnt"
    done
  } > "$path"
}

victim_field() {
  # victim_field <json> <key>
  python3 -c "
import json
d = json.loads(open('$1').read())
print(d['$2'])
"
}

# --- Test 1: lowest score is evicted, not oldest --------------------------
# Anchor 2026-04-19:
#   Rule 1 L-A: reinforced 5x, access 0, last-reinforced 2026-04-15 (age=4)
#     -> score = 5*1/4 = 1.25
#   Rule 2 L-B: reinforced 2x, access 0, last-reinforced 2026-03-19 (age=31)
#     -> score = 2*1/31 ≈ 0.065
#   Rule 3 L-C: reinforced 3x, access 5, last-reinforced 2026-03-19 (age=31)
#     -> score = 3*5/31 ≈ 0.484
# ASC: L-B (lowest). The OLD sort would have picked L-B too (oldest), but the
# key change is we also confirm L-C does NOT win despite being old, because
# its access count lifts its score above L-B.
A_FILE="$TMPROOT/t1_rules.md"
TSV="$TMPROOT/t1.tsv"
cat > "$A_FILE" <<'EOF'
---
name: K2B Active Rules
---

# Rules

1. **Rule A.** body (L-2026-04-01-101, reinforced 5x, last-reinforced: 2026-04-15)

2. **Rule B.** body (L-2026-04-02-202, reinforced 2x, last-reinforced: 2026-03-19)

3. **Rule C.** body (L-2026-04-03-303, reinforced 3x, last-reinforced: 2026-03-19)
EOF
seed_tsv "$TSV" "L-2026-04-03-303:5"

K2B_TODAY=2026-04-19 \
  K2B_ACTIVE_RULES_FILE="$A_FILE" \
  K2B_ACCESS_COUNTS_TSV="$TSV" \
  "$HELPER" > "$TMPROOT/t1.json"

lid=$(victim_field "$TMPROOT/t1.json" learn_id)
[ "$lid" = "L-2026-04-02-202" ] || fail "test1: expected victim L-B, got [$lid]"

# --- Test 2: access lifts an otherwise-eligible rule out of victim slot ---
# Anchor 2026-04-19:
#   Rule 1 L-X: reinforced 2x, access 0, last-reinforced 2026-03-19 (age=31)
#     -> score = 2*1/31 ≈ 0.065
#   Rule 2 L-Y: reinforced 2x, access 20, last-reinforced 2026-03-19 (age=31)
#     -> score = 2*20/31 ≈ 1.29
#   Rule 3 L-Z: reinforced 3x, access 0, last-reinforced 2026-04-12 (age=7)
#     -> score = 3*1/7 ≈ 0.429
# ASC: L-X (lowest). Without access counts, the OLD sort would tie L-X and
# L-Y on date (2026-03-19) and pick by reinforcement_count asc (tie at 2)
# then L-ID asc -- L-X wins alphabetically. Under the new sort, L-X still wins
# BUT for the correct reason (access_count = 0 vs 20).
A_FILE="$TMPROOT/t2_rules.md"
TSV="$TMPROOT/t2.tsv"
cat > "$A_FILE" <<'EOF'
# Rules

1. **Rule X.** body (L-2026-04-01-111, reinforced 2x, last-reinforced: 2026-03-19)

2. **Rule Y.** body (L-2026-04-02-222, reinforced 2x, last-reinforced: 2026-03-19)

3. **Rule Z.** body (L-2026-04-03-333, reinforced 3x, last-reinforced: 2026-04-12)
EOF
seed_tsv "$TSV" "L-2026-04-02-222:20"

K2B_TODAY=2026-04-19 \
  K2B_ACTIVE_RULES_FILE="$A_FILE" \
  K2B_ACCESS_COUNTS_TSV="$TSV" \
  "$HELPER" > "$TMPROOT/t2.json"

lid=$(victim_field "$TMPROOT/t2.json" learn_id)
[ "$lid" = "L-2026-04-01-111" ] || fail "test2: expected victim L-X, got [$lid]"

# --- Test 3: non-L rules are PINNED (skipped entirely) ------------------
# Rule 1 has no L-ID -- must NOT be picked as victim even though it's
# otherwise the weakest candidate.
A_FILE="$TMPROOT/t3_rules.md"
TSV="$TMPROOT/t3.tsv"
cat > "$A_FILE" <<'EOF'
# Rules

1. **Foundation rule.** Manually written, no L-ID. last-reinforced: 2024-01-01

2. **Weak L-rule.** (L-2026-04-01-401, reinforced 2x, last-reinforced: 2026-03-19)

3. **Strong L-rule.** (L-2026-04-02-402, reinforced 8x, last-reinforced: 2026-04-17)
EOF
seed_tsv "$TSV"

K2B_TODAY=2026-04-19 \
  K2B_ACTIVE_RULES_FILE="$A_FILE" \
  K2B_ACCESS_COUNTS_TSV="$TSV" \
  "$HELPER" > "$TMPROOT/t3.json"

lid=$(victim_field "$TMPROOT/t3.json" learn_id)
[ "$lid" = "L-2026-04-01-401" ] || fail "test3: non-L rule must be pinned, expected L-W01 as victim, got [$lid]"

# --- Test 4: ALL rules are non-L -> exit 1 (no victim) -------------------
A_FILE="$TMPROOT/t4_rules.md"
TSV="$TMPROOT/t4.tsv"
cat > "$A_FILE" <<'EOF'
# Rules

1. **Foundation A.** manually written. last-reinforced: 2025-01-01

2. **Foundation B.** manually written. last-reinforced: 2025-02-01
EOF
seed_tsv "$TSV"

K2B_TODAY=2026-04-19 \
  K2B_ACTIVE_RULES_FILE="$A_FILE" \
  K2B_ACCESS_COUNTS_TSV="$TSV" \
  "$HELPER" > "$TMPROOT/t4.json" 2>/dev/null
rc=$?
[ "$rc" = "1" ] || fail "test4: expected exit 1 when no L-linked rules exist, got $rc"

# --- Test 5: missing reinforcement metadata defaults to 1 ---------------
# Rule 1 L-A: no reinforced Nx in parenthetical, access 0, last-reinforced 2026-04-15
#   -> reinforced defaults to 1, score = 1*1/4 = 0.25
# Rule 2 L-B: reinforced 3x, access 0, last-reinforced 2026-04-15
#   -> score = 3*1/4 = 0.75
# ASC: L-A (lowest, via default)
A_FILE="$TMPROOT/t5_rules.md"
TSV="$TMPROOT/t5.tsv"
cat > "$A_FILE" <<'EOF'
# Rules

1. **Rule A.** (L-2026-04-10-501, last-reinforced: 2026-04-15)

2. **Rule B.** (L-2026-04-10-502, reinforced 3x, last-reinforced: 2026-04-15)
EOF
seed_tsv "$TSV"

K2B_TODAY=2026-04-19 \
  K2B_ACTIVE_RULES_FILE="$A_FILE" \
  K2B_ACCESS_COUNTS_TSV="$TSV" \
  "$HELPER" > "$TMPROOT/t5.json"

lid=$(victim_field "$TMPROOT/t5.json" learn_id)
[ "$lid" = "L-2026-04-10-501" ] || fail "test5: missing reinforced -> default 1, L-A should win, got [$lid]"

# --- Test 6: output schema includes importance_score ----------------------
A_FILE="$TMPROOT/t6_rules.md"
TSV="$TMPROOT/t6.tsv"
cat > "$A_FILE" <<'EOF'
# Rules

1. **Rule A.** (L-2026-04-10-501, reinforced 2x, last-reinforced: 2026-04-15)
EOF
seed_tsv "$TSV" "L-2026-04-10-501:3"

K2B_TODAY=2026-04-19 \
  K2B_ACTIVE_RULES_FILE="$A_FILE" \
  K2B_ACCESS_COUNTS_TSV="$TSV" \
  "$HELPER" > "$TMPROOT/t6.json"

python3 -c "
import json, sys
d = json.load(open('$TMPROOT/t6.json'))
for key in ('rule_number','title','learn_id','last_reinforced','reinforcement_count','access_count','importance_score'):
    if key not in d:
        print('missing key: ' + key); sys.exit(1)
" || fail "test6: missing expected schema keys"

echo "ALL TESTS PASSED"
