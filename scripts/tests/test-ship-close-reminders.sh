#!/usr/bin/env bash
# Fixture tests for scripts/ship-close-reminders.py
# Covers the cases Codex flagged in the 2026-05-30 Step-14 review:
#   exact-slug match, loose-word false-positive (must stay open), multi-slug
#   per-line attribution, no `## Closed` header, idempotency, empty-slug safety.
set -uo pipefail

SCRIPT="$(cd "$(dirname "$0")/.." && pwd)/ship-close-reminders.py"
PASS=0
FAIL=0

mkfix() {  # $1 = vault root; writes a reminders.md fixture
  mkdir -p "$1/wiki/context"
  cat > "$1/wiki/context/reminders.md" <<'EOF'
# Reminders

## Open

- [open] AI demand mining brainstorm -- unrelated item (added 2026-04-22)
- [open] Build feature_k2b-orchestrator-v1 Ship 1a -- SQLite task board (added 2026-05-28)
- [open] Do NOT pm2 start the k2b-dispatcher until the orchestrator is machine-aware (added 2026-05-30)
- [open] Wire feature_query-mode follow-up (added 2026-05-29)

## Closed

- [closed] old thing (closed 2026-05-20)
EOF
}

run() {  # $1 vault, rest = args
  local v="$1"; shift
  K2B_VAULT_PATH="$v" python3 "$SCRIPT" "$@" 2>&1
}

check() {  # $1 = description, $2 = "pass"|"fail"
  if [ "$2" = "pass" ]; then PASS=$((PASS+1)); echo "  PASS: $1"; else FAIL=$((FAIL+1)); echo "  FAIL: $1"; fi
}

# T1: exact slug closes the build reminder, leaves the loose-word pm2 reminder OPEN
SB=$(mktemp -d); mkfix "$SB"
run "$SB" feature_k2b-orchestrator-v1 --date 2026-05-30 >/dev/null
RM="$SB/wiki/context/reminders.md"
grep -q '^- \[closed\] Build feature_k2b-orchestrator-v1 Ship 1a.*closed 2026-05-30 via /ship feature_k2b-orchestrator-v1' "$RM" && T1a=pass || T1a=fail
check "exact slug closes the build reminder w/ correct annotation" "$T1a"
grep -q '^- \[open\] Do NOT pm2 start' "$RM" && T1b=pass || T1b=fail
check "loose-word pm2 reminder (shares 'orchestrator') stays OPEN" "$T1b"
grep -q '^- \[open\] AI demand mining' "$RM" && T1c=pass || T1c=fail
check "unrelated reminder stays OPEN" "$T1c"
rm -rf "$SB"

# T2: idempotency -- second identical run is a no-op
SB=$(mktemp -d); mkfix "$SB"
run "$SB" feature_k2b-orchestrator-v1 --date 2026-05-30 >/dev/null
OUT=$(run "$SB" feature_k2b-orchestrator-v1 --date 2026-05-30)
echo "$OUT" | grep -q 'no open reminder matched' && T2=pass || T2=fail
check "idempotent: re-run is a no-op" "$T2"
rm -rf "$SB"

# T3: multi-slug attributes each closed line to the slug that actually matched it
SB=$(mktemp -d); mkfix "$SB"
run "$SB" feature_k2b-orchestrator-v1 feature_query-mode --date 2026-05-30 >/dev/null
RM="$SB/wiki/context/reminders.md"
grep -q 'Build feature_k2b-orchestrator-v1 Ship 1a.*via /ship feature_k2b-orchestrator-v1' "$RM" && T3a=pass || T3a=fail
check "multi-slug: orchestrator line attributed to orchestrator slug" "$T3a"
grep -q 'Wire feature_query-mode follow-up.*via /ship feature_query-mode' "$RM" && T3b=pass || T3b=fail
check "multi-slug: query-mode line attributed to query-mode slug (not slugs[0])" "$T3b"
rm -rf "$SB"

# T4: no `## Closed` header -> one is appended
SB=$(mktemp -d); mkdir -p "$SB/wiki/context"
printf '# Reminders\n\n## Open\n\n- [open] Build feature_foo-bar widget (added 2026-05-30)\n' > "$SB/wiki/context/reminders.md"
run "$SB" feature_foo-bar --date 2026-05-30 >/dev/null
RM="$SB/wiki/context/reminders.md"
grep -q '^## Closed' "$RM" && grep -q '^- \[closed\] Build feature_foo-bar widget' "$RM" && T4=pass || T4=fail
check "no ## Closed header: section appended with the closed line" "$T4"
rm -rf "$SB"

# T5: empty slug (unset \$SLUG expanding to "") is a clean no-op, exit 0
SB=$(mktemp -d); mkfix "$SB"
OUT=$(run "$SB" "" --date 2026-05-30); RC=$?
[ "$RC" = "0" ] && echo "$OUT" | grep -q 'no (non-empty) feature slug' && T5=pass || T5=fail
check "empty slug arg is a clean no-op (exit 0)" "$T5"
rm -rf "$SB"

# T7: prefix collision -- shipping feature_foo must NOT close feature_foo-v2 / feature_foo10
SB=$(mktemp -d); mkdir -p "$SB/wiki/context"
cat > "$SB/wiki/context/reminders.md" <<'EOF'
# Reminders

## Open

- [open] Build feature_foo widget (added 2026-05-30)
- [open] Build feature_foo-v2 follow-up (added 2026-05-30)
- [open] Build feature_foo10 variant (added 2026-05-30)

## Closed
EOF
run "$SB" feature_foo --date 2026-05-30 >/dev/null
RM="$SB/wiki/context/reminders.md"
grep -q '^- \[closed\] Build feature_foo widget' "$RM" && T7a=pass || T7a=fail
check "prefix collision: feature_foo closes its own reminder" "$T7a"
grep -q '^- \[open\] Build feature_foo-v2 follow-up' "$RM" && grep -q '^- \[open\] Build feature_foo10 variant' "$RM" && T7b=pass || T7b=fail
check "prefix collision: feature_foo-v2 and feature_foo10 stay OPEN" "$T7b"
rm -rf "$SB"

# T8: concurrency -- two parallel closes of different slugs both survive (flock)
# Without the advisory lock, the second os.replace() clobbers the first when the runs
# overlap, silently dropping one close. With flock they serialize and both survive.
SB=$(mktemp -d); mkfix "$SB"
run "$SB" feature_k2b-orchestrator-v1 --date 2026-05-30 >/dev/null &
P1=$!
run "$SB" feature_query-mode --date 2026-05-30 >/dev/null &
P2=$!
wait "$P1"; wait "$P2"
RM="$SB/wiki/context/reminders.md"
grep -q '^- \[closed\] Build feature_k2b-orchestrator-v1 Ship 1a' "$RM" && T8a=pass || T8a=fail
check "concurrency: orchestrator close survives parallel run" "$T8a"
grep -q '^- \[closed\] Wire feature_query-mode follow-up' "$RM" && T8b=pass || T8b=fail
check "concurrency: query-mode close survives parallel run" "$T8b"
rm -rf "$SB"

# T6: dry-run writes nothing
SB=$(mktemp -d); mkfix "$SB"
BEFORE=$(md5 -q "$SB/wiki/context/reminders.md" 2>/dev/null || md5sum "$SB/wiki/context/reminders.md" | cut -d' ' -f1)
run "$SB" feature_k2b-orchestrator-v1 --dry-run --date 2026-05-30 >/dev/null
AFTER=$(md5 -q "$SB/wiki/context/reminders.md" 2>/dev/null || md5sum "$SB/wiki/context/reminders.md" | cut -d' ' -f1)
[ "$BEFORE" = "$AFTER" ] && T6=pass || T6=fail
check "dry-run leaves the file byte-identical" "$T6"
rm -rf "$SB"

echo ""
echo "ship-close-reminders tests: $PASS passed, $FAIL failed"
[ "$FAIL" = "0" ]
