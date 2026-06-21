#!/usr/bin/env bash
# Tests for k2b-plate freshness sources and stale-status audit.

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PLATE_SCRIPT="$REPO_ROOT/.agents/skills/k2b-plate/scripts/plate.sh"
AUDIT_SCRIPT="$REPO_ROOT/scripts/audit-plate-freshness.py"

TMP_DIRS=()
cleanup() {
  local d
  for d in "${TMP_DIRS[@]}"; do
    [ -n "$d" ] && [ -d "$d" ] && rm -rf "$d"
  done
}
trap cleanup EXIT

fail() { echo "FAIL: $*" >&2; exit 1; }

mktmp() {
  local d
  d="$(mktemp -d)"
  TMP_DIRS+=("$d")
  echo "$d"
}

write_minimal_vaults() {
  local k2b_vault="$1"
  local k2bi_vault="$2"
  mkdir -p "$k2b_vault/wiki/concepts" "$k2b_vault/wiki/context" "$k2b_vault/raw/sessions"
  mkdir -p "$k2b_vault/System/memory"
  mkdir -p "$k2bi_vault/wiki/planning"

  : > "$k2b_vault/wiki/context/reminders.md"
  : > "$k2b_vault/System/memory/self_improve_requests.md"
  : > "$k2b_vault/System/memory/self_improve_errors.md"
  cat > "$k2bi_vault/wiki/planning/index.md" <<'EOF'
# K2Bi Planning Workspace

> **K2Bi PM checkpoint -- test current:**
>
> **Current state.** Nothing pending.
EOF
}

test_recent_shipped_reads_inline_and_archived_rows() {
  local tmp k2b_vault k2bi_vault today out
  tmp="$(mktmp)"
  k2b_vault="$tmp/K2B-Vault"
  k2bi_vault="$tmp/K2Bi-Vault"
  today="$(date +%Y-%m-%d)"

  write_minimal_vaults "$k2b_vault" "$k2bi_vault"
  cat > "$k2b_vault/wiki/concepts/index.md" <<EOF
# Wiki Concepts Index

## Shipped

| Page | Shipped | Notes |
|------|---------|-------|
| [[feature_recent-root]] | $today | Inline shipped note. |
| [[Shipped/feature_recent-archived|feature_recent-archived]] | $today | Archived shipped note. |
| [[Shipped/feature_recent-alias-pipe|Friendly \\| Alias]] | $today | Archived row uses target slug when alias is friendly text. |
| [[feature_recent-markdown-link]] | $today | Note has [pipe link](https://example.test/a|b) and keeps columns intact. |
| [[feature_recent-escaped-pipe]] | $today | Note has [[Some Page|alias \\| escaped]] and keeps columns intact. |
| [[feature_recent-empty-note]] | $today | |
| [[Shipped/feature_old|feature_old]] | 2000-01-01 | Old shipped note. |
| [[feature_old-url-date]] | 2000-01-01 | Old note has [date URL](https://example.test/$today). |

## In Progress lanes

| Page | Status | Updated |
|------|--------|---------|
| [[feature_in-progress-link]] | Status has [doc](https://example.test/a|b) and [[Some Page|alias \\| escaped]] | $today |

## Next Up

| Page | Why | Updated |
|------|-----|---------|

## Backlog

| Page | Why | Updated |
|------|-----|---------|
EOF

  out="$(
    K2B_VAULT_PATH="$k2b_vault" \
    K2BI_VAULT_PATH="$k2bi_vault" \
    K2B_MEMORY_DIR="$k2b_vault/System/memory" \
      bash "$PLATE_SCRIPT"
  )"

  echo "$out" | grep -q "feature_recent-root" || fail "inline shipped row was not shown in Recently shipped"
  echo "$out" | grep -q "feature_recent-archived" || fail "archived shipped row was not shown in Recently shipped"
  echo "$out" | grep -q "feature_recent-alias-pipe" || fail "archived row with friendly escaped alias did not fall back to target slug"
  echo "$out" | grep -q "feature_recent-markdown-link" || fail "markdown-link shipped row was not shown in Recently shipped"
  echo "$out" | grep -q "feature_recent-escaped-pipe" || fail "escaped-pipe shipped row was not shown in Recently shipped"
  echo "$out" | grep -Eq "\\*\\*feature_recent-empty-note\\*\\* \\($today\\):[[:space:]]*$" || fail "empty-note shipped row should keep the correct slug/date/empty-note shape"
  echo "$out" | grep -q "feature_in-progress-link.*updated $today" || fail "In Progress row with markdown link / escaped pipe was not parsed correctly"
  if echo "$out" | grep -q "feature_old"; then
    fail "old shipped row should not appear in last-7-days Recently shipped"
  fi
  if echo "$out" | grep -q "feature_old-url-date"; then
    fail "old shipped row with a recent date inside a URL should not appear"
  fi
  echo "PASS: test_recent_shipped_reads_inline_and_archived_rows"
}

test_recent_shipped_empty_section_falls_back_to_none() {
  local tmp k2b_vault k2bi_vault out
  tmp="$(mktmp)"
  k2b_vault="$tmp/K2B-Vault"
  k2bi_vault="$tmp/K2Bi-Vault"

  write_minimal_vaults "$k2b_vault" "$k2bi_vault"
  cat > "$k2b_vault/wiki/concepts/index.md" <<'EOF'
# Wiki Concepts Index

## Shipped

| Page | Shipped | Notes |
|------|---------|-------|

## In Progress lanes

| Page | Status | Updated |
|------|--------|---------|
EOF

  out="$(
    K2B_VAULT_PATH="$k2b_vault" \
    K2BI_VAULT_PATH="$k2bi_vault" \
    K2B_MEMORY_DIR="$k2b_vault/System/memory" \
      bash "$PLATE_SCRIPT"
  )"

  echo "$out" | grep -q "_(none in last 7 days)_" || fail "empty Shipped section should render none fallback"
  echo "PASS: test_recent_shipped_empty_section_falls_back_to_none"
}

test_recent_shipped_missing_section_falls_back_to_none() {
  local tmp k2b_vault k2bi_vault out
  tmp="$(mktmp)"
  k2b_vault="$tmp/K2B-Vault"
  k2bi_vault="$tmp/K2Bi-Vault"

  write_minimal_vaults "$k2b_vault" "$k2bi_vault"
  cat > "$k2b_vault/wiki/concepts/index.md" <<'EOF'
# Wiki Concepts Index

## In Progress lanes

| Page | Status | Updated |
|------|--------|---------|
EOF

  out="$(
    K2B_VAULT_PATH="$k2b_vault" \
    K2BI_VAULT_PATH="$k2bi_vault" \
    K2B_MEMORY_DIR="$k2b_vault/System/memory" \
      bash "$PLATE_SCRIPT"
  )"

  echo "$out" | grep -q "_(none in last 7 days)_" || fail "missing Shipped section should render none fallback"
  echo "PASS: test_recent_shipped_missing_section_falls_back_to_none"
}

test_stale_audit_detects_shipped_feature_named_current_or_next() {
  local tmp k2b_vault k2bi_vault out rc clean_out
  tmp="$(mktmp)"
  k2b_vault="$tmp/K2B-Vault"
  k2bi_vault="$tmp/K2Bi-Vault"
  clean_out="$tmp/audit-clean.out"

  write_minimal_vaults "$k2b_vault" "$k2bi_vault"
  cat > "$k2b_vault/wiki/concepts/index.md" <<'EOF'
# Wiki Concepts Index

## Shipped

| Page | Shipped | Notes |
|------|---------|-------|
| [[feature_orchestrator-deploy-gate]] | 2026-06-20 | A5 shipped. |
| [[feature_generic-done]] | 2026-06-20 | Generic feature shipped; note mentions feature_note-only. |

## In Progress lanes
EOF
  cat > "$k2b_vault/wiki/concepts/feature_k2b-orchestrator.md" <<'EOF'
# K2B Orchestrator

**Where we are now (2026-06-19):** Next separate feature: [[feature_orchestrator-deploy-gate]] (A5 deploy-to-engine).
EOF
  cat > "$k2bi_vault/wiki/planning/index.md" <<'EOF'
# K2Bi Planning Workspace

> **K2Bi PM checkpoint -- 2026-06-19 HKT ORCHESTRATOR SHIPPED / A5 DEPLOY-GATE NEXT:**
>
> **Current state.** Active next work is K2B-side A5 deploy-to-engine gate.
>
> **Next PM gate (in order).** (5) **CURRENT -- K2B A5 deploy-to-engine gate:** implement next.
>
> **Generic stale check.** Current work still includes feature_generic-done.
EOF

  set +e
  out="$(
    K2B_VAULT_PATH="$k2b_vault" \
    K2BI_VAULT_PATH="$k2bi_vault" \
      python3 "$AUDIT_SCRIPT" 2>&1
  )"
  rc=$?
  set -e

  [ "$rc" -ne 0 ] || fail "stale audit should fail when shipped A5 is still current/next"
  echo "$out" | grep -q "wiki/planning/index.md" || fail "audit did not name stale K2Bi Resume Card: $out"
  echo "$out" | grep -q "feature_k2b-orchestrator.md" || fail "audit did not name stale orchestrator tracker: $out"
  echo "$out" | grep -q "feature_generic-done" || fail "audit did not flag generic shipped feature current/next drift: $out"

  cat > "$k2b_vault/wiki/concepts/feature_k2b-orchestrator.md" <<'EOF'
# K2B Orchestrator

**Where we are now (2026-06-21):** A5 deploy-to-engine shipped separately; no current orchestrator build gate remains.

## Why v2 is mostly orchestration, not building

Historical context below this heading is not current plate state.

### 2026-06-07 -- old update

**MVP gate: NOT gate-passed.** feature_generic-done was live-MVP-probe pending in old history.
EOF
  cat > "$k2bi_vault/wiki/planning/index.md" <<'EOF'
# K2Bi Planning Workspace

> **K2Bi PM checkpoint -- 2026-06-21 HKT ORCHESTRATOR A5 SHIPPED / NEXT PM GATE UNSELECTED:**
>
> **Current state.** Orchestrator A5 is shipped. No current orchestrator gate remains.
>
> **Next PM gate.** Pick the next K2Bi PM item from the non-blocking follow-up list or a fresh operator-selected ticker workflow. feature_note-only may still be current because it only appeared in a shipped-row note, not the shipped Page column.
EOF

  K2B_VAULT_PATH="$k2b_vault" \
  K2BI_VAULT_PATH="$k2bi_vault" \
    python3 "$AUDIT_SCRIPT" >"$clean_out" 2>&1 || {
      cat "$clean_out" >&2
      fail "stale audit should pass after current/next text is repaired"
    }
  grep -q "plate-freshness audit passed" "$clean_out" || fail "clean audit should print pass message"
  echo "PASS: test_stale_audit_detects_shipped_feature_named_current_or_next"
}

test_plate_skill_script_mirrors_stay_identical() {
  cmp -s \
    "$REPO_ROOT/.agents/skills/k2b-plate/scripts/plate.sh" \
    "$REPO_ROOT/.claude/skills/k2b-plate/scripts/plate.sh" || \
    fail "Codex and Claude k2b-plate scripts diverged"
  echo "PASS: test_plate_skill_script_mirrors_stay_identical"
}

test_recent_shipped_reads_inline_and_archived_rows
test_recent_shipped_empty_section_falls_back_to_none
test_recent_shipped_missing_section_falls_back_to_none
test_stale_audit_detects_shipped_feature_named_current_or_next
test_plate_skill_script_mirrors_stay_identical
