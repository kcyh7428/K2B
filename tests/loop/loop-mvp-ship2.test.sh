#!/usr/bin/env bash
# BINARY MVP TEST for feature_k2b-integrated-loop Ship 2.
# Four gates, all must hold:
#   Gate A: default K2B_LOOP_CANDIDATES points at the live observer path;
#           a fixture placed at that path surfaces in the dashboard with no
#           env overrides (proves live wiring without touching the real vault).
#   Gate B: --defer increments a persistent counter; third defer auto-archives
#           the candidate and removes it from observer-candidates.md. Dashboard
#           shows "(deferred Nx)" badge for N in {1, 2}.
#   Gate C: review/ items share the unified index space (observer 1..O, review
#           O+1..O+R). --accept moves to review/Ready with review-action:
#           accepted. --reject moves to Archive/review-archive/YYYY-MM-DD with
#           review-action: rejected.
#   Gate D: each deprecated skill body contains the sentinel string
#           "DEPRECATED in Ship 2 of k2b-integrated-loop".
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
FIXTURE="$ROOT/tests/fixtures/loop-mvp-ship2"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# Sandbox HOME so default path resolution can be exercised without touching
# ~/Projects/K2B-Vault.
SBX_HOME="$TMP/sandbox-home"
SBX_VAULT="$SBX_HOME/Projects/K2B-Vault"
SBX_CTX="$SBX_VAULT/wiki/context"
SBX_REVIEW="$SBX_VAULT/review"
SBX_READY="$SBX_REVIEW/Ready"
SBX_ARCHIVE_OBS="$SBX_CTX/observations.archive"
SBX_ARCHIVE_REVIEW="$SBX_VAULT/Archive/review-archive"
SBX_MEM="$SBX_HOME/.claude/projects/-Users-keithmbpm2-Projects-K2B/memory"
mkdir -p "$SBX_CTX" "$SBX_REVIEW" "$SBX_READY" "$SBX_ARCHIVE_OBS" \
         "$SBX_ARCHIVE_REVIEW" "$SBX_MEM" "$SBX_VAULT/raw/research"

cp "$FIXTURE/observer-candidates.md" "$SBX_CTX/observer-candidates.md"
cp "$FIXTURE/observer-defers.jsonl" "$SBX_CTX/observer-defers.jsonl"
cp "$FIXTURE/self_improve_learnings.md" "$SBX_MEM/self_improve_learnings.md"
cp "$FIXTURE/review/content_ship2-sample.md" "$SBX_REVIEW/"
cp "$FIXTURE/review/crosslinks_ship2-sample.md" "$SBX_REVIEW/"

export HOME="$SBX_HOME"
export K2B_LOOP_DATE="2026-04-24"
export K2B_LOOP_ACTOR="keith"
export K2B_LOOP_OBSERVER_RUN="2026-04-24 20:02"

# --- Gate A: live observer wiring via default path ---

# Unset overrides so the shell scripts resolve their defaults against sandbox HOME.
unset K2B_LOOP_CANDIDATES K2B_LOOP_LEARNINGS K2B_LOOP_ARCHIVE_DIR \
      K2B_LOOP_REVIEW_DIR K2B_LOOP_RESEARCH_DIR K2B_LOOP_DEFERS \
      K2B_LOOP_REVIEW_READY_DIR K2B_LOOP_REVIEW_ARCHIVE_ROOT

dashboard_a="$("$ROOT/scripts/loop/loop-render-dashboard.sh")"
for n in 1 2 3; do
  echo "$dashboard_a" | grep -Eq "^\s*\[${n}\] \[(high|medium|low)\]" \
    || { echo "FAIL gate A: dashboard missing observer [${n}]"; echo "$dashboard_a"; exit 1; }
done
echo "$dashboard_a" | grep -q "Observer candidates (3)" \
  || { echo "FAIL gate A: observer count wrong"; echo "$dashboard_a"; exit 1; }
# Review items must share the unified index space -- they appear as [4] and [5]
echo "$dashboard_a" | grep -Eq "^\s*\[4\] review" \
  || { echo "FAIL gate A: missing routable review [4]"; echo "$dashboard_a"; exit 1; }
echo "$dashboard_a" | grep -Eq "^\s*\[5\] review" \
  || { echo "FAIL gate A: missing routable review [5]"; echo "$dashboard_a"; exit 1; }
echo "  gate A PASS: live path surfaces 3 observer + 2 review items"

# --- Gate B: defer counter + auto-archive at 3 ---

"$ROOT/scripts/loop/loop-apply.sh" --defer 1 >/dev/null
dashboard_b1="$("$ROOT/scripts/loop/loop-render-dashboard.sh")"
echo "$dashboard_b1" | grep -Eq '\[1\] .* \(deferred 1x\)' \
  || { echo "FAIL gate B: missing (deferred 1x) on [1] after first defer"; echo "$dashboard_b1"; exit 1; }

"$ROOT/scripts/loop/loop-apply.sh" --defer 1 >/dev/null
dashboard_b2="$("$ROOT/scripts/loop/loop-render-dashboard.sh")"
echo "$dashboard_b2" | grep -Eq '\[1\] .* \(deferred 2x\)' \
  || { echo "FAIL gate B: missing (deferred 2x) on [1] after second defer"; echo "$dashboard_b2"; exit 1; }

"$ROOT/scripts/loop/loop-apply.sh" --defer 1 >/dev/null
# After third defer, the candidate must be archived and gone from observer-candidates.md
remaining=$(awk '
  /^## Candidate Learnings/ { inside=1; next }
  /^## / && inside { exit }
  inside && /^- \[/ { n++ }
  END { print n+0 }
' "$SBX_CTX/observer-candidates.md")
if [ "$remaining" != "2" ]; then
  echo "FAIL gate B: expected 2 remaining observer candidates after auto-archive, got $remaining"
  cat "$SBX_CTX/observer-candidates.md"
  exit 1
fi
archive_file="$SBX_ARCHIVE_OBS/auto-archived-deferred-2026-04-24.jsonl"
if [ ! -f "$archive_file" ]; then
  echo "FAIL gate B: auto-archive file missing ($archive_file)"
  ls -la "$SBX_ARCHIVE_OBS" || true
  exit 1
fi
auto_lines=$(wc -l < "$archive_file" | tr -d ' ')
if [ "$auto_lines" != "1" ]; then
  echo "FAIL gate B: expected 1 auto-archive line, got $auto_lines"
  cat "$archive_file"
  exit 1
fi
grep -q '"auto_archived": "2026-04-24"' "$archive_file" \
  || { echo "FAIL gate B: missing auto_archived marker"; cat "$archive_file"; exit 1; }
echo "  gate B PASS: defer counter + auto-archive at 3 defers"

# --- Gate C: review routing via unified index space ---

# After auto-archiving candidate 1, the dashboard renumbers to 2 observer + 2 review.
# Review items now occupy [3] and [4].
dashboard_c="$("$ROOT/scripts/loop/loop-render-dashboard.sh")"
echo "$dashboard_c" | grep -Eq "^\s*\[3\] review" \
  || { echo "FAIL gate C: review index shift wrong (expected [3])"; echo "$dashboard_c"; exit 1; }

# Accept review at index 3 -> moves to Ready/ with review-action: accepted
"$ROOT/scripts/loop/loop-apply.sh" --accept 3 >/dev/null
ready_files=$(ls "$SBX_READY" 2>/dev/null | wc -l | tr -d ' ')
if [ "$ready_files" != "1" ]; then
  echo "FAIL gate C: expected 1 file in Ready, got $ready_files"
  ls -la "$SBX_READY" || true
  exit 1
fi
accepted_body="$(cat "$SBX_READY"/*.md 2>/dev/null || echo "")"
echo "$accepted_body" | grep -q "review-action: accepted" \
  || { echo "FAIL gate C: Ready file lacks review-action: accepted"; echo "$accepted_body"; exit 1; }
echo "$accepted_body" | grep -qv "review-action: pending" \
  || { echo "FAIL gate C: Ready file still has review-action: pending"; exit 1; }

# The surviving review item is now at [3] again after the accept reshaped indices.
dashboard_c2="$("$ROOT/scripts/loop/loop-render-dashboard.sh")"
echo "$dashboard_c2" | grep -Eq "^\s*\[3\] review" \
  || { echo "FAIL gate C: post-accept review index shift wrong"; echo "$dashboard_c2"; exit 1; }

# Reject review at index 3 -> moves to Archive/review-archive/2026-04-24/
"$ROOT/scripts/loop/loop-apply.sh" --reject 3 >/dev/null
archived_files=$(ls "$SBX_ARCHIVE_REVIEW/2026-04-24" 2>/dev/null | wc -l | tr -d ' ')
if [ "$archived_files" != "1" ]; then
  echo "FAIL gate C: expected 1 file in Archive/review-archive/2026-04-24, got $archived_files"
  ls -la "$SBX_ARCHIVE_REVIEW" || true
  exit 1
fi
rejected_body="$(cat "$SBX_ARCHIVE_REVIEW/2026-04-24"/*.md 2>/dev/null || echo "")"
echo "$rejected_body" | grep -q "review-action: rejected" \
  || { echo "FAIL gate C: rejected file lacks review-action: rejected"; echo "$rejected_body"; exit 1; }
echo "  gate C PASS: review accept/reject route via unified index"

# --- Gate D: deprecation sentinel in all three skill bodies ---

DEPRECATED_MARK="DEPRECATED in Ship 2 of k2b-integrated-loop"
for skill in k2b-autoresearch k2b-improve k2b-review; do
  body="$ROOT/.claude/skills/$skill/SKILL.md"
  if [ ! -f "$body" ]; then
    echo "FAIL gate D: $body not found"
    exit 1
  fi
  if ! grep -q "$DEPRECATED_MARK" "$body"; then
    echo "FAIL gate D: deprecation sentinel missing in $skill/SKILL.md"
    exit 1
  fi
done
echo "  gate D PASS: deprecation sentinel present in 3 skill bodies"

echo ""
echo "BINARY MVP SHIP 2: SHIP (4/4 gates passed)"
