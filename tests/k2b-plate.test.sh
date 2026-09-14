#!/usr/bin/env bash
# Tests for k2b-plate freshness sources and stale-status audit.

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PLATE_SCRIPT="$REPO_ROOT/.agents/skills/k2b-plate/scripts/plate.sh"
AUDIT_SCRIPT="$REPO_ROOT/scripts/audit-plate-freshness.py"

TMP_ROOT="$(mktemp -d)"
trap 'rm -rf "$TMP_ROOT"' EXIT

fail() { echo "FAIL: $*" >&2; exit 1; }

mktmp() {
  mktemp -d "$TMP_ROOT/case.XXXXXX"
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

write_clean_audit_sources() {
  local k2b_vault="$1"
  mkdir -p "$k2b_vault/wiki/concepts"
  cat > "$k2b_vault/wiki/concepts/index.md" <<'EOF'
# Wiki Concepts Index

## Shipped

| Page | Shipped | Notes |
|------|---------|-------|
| [[feature_orchestrator-deploy-gate]] | 2026-06-20 | A5 shipped. |
EOF
  cat > "$k2b_vault/wiki/concepts/feature_k2b-orchestrator.md" <<'EOF'
# K2B Orchestrator

**Where we are now (2026-06-21):** A5 deploy-to-engine shipped separately; no current orchestrator build gate remains.
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

test_needs_digestion_surfaces_uncompiled_raw_sources() {
  local tmp k2b_vault k2bi_vault out
  tmp="$(mktmp)"
  k2b_vault="$tmp/K2B-Vault"
  k2bi_vault="$tmp/K2Bi-Vault"

  write_minimal_vaults "$k2b_vault" "$k2bi_vault"
  mkdir -p "$k2b_vault/raw/tldrs"
  cat > "$k2b_vault/wiki/concepts/index.md" <<'EOF'
# Wiki Concepts Index

## Shipped

| Page | Shipped | Notes |
|------|---------|-------|

## In Progress lanes

| Page | Status | Updated |
|------|--------|---------|
EOF
  cat > "$k2b_vault/raw/tldrs/pending.md" <<'EOF'
---
tags: [tldr]
date: 2026-07-01
type: tldr
origin: k2b-extract
compiled: false
---

# Pending Digest
EOF
  python3 - "$k2b_vault/raw/tldrs/pending.md" <<'PY'
from pathlib import Path
import os
import sys
import time

path = Path(sys.argv[1])
old = time.time() - 49 * 3600
os.utime(path, (old, old))
PY

  out="$(
    K2B_VAULT_PATH="$k2b_vault" \
    K2BI_VAULT_PATH="$k2bi_vault" \
    K2B_MEMORY_DIR="$k2b_vault/System/memory" \
      bash "$PLATE_SCRIPT"
  )"

  echo "$out" | grep -q "## 🧭 Needs digestion" || fail "Needs digestion section missing"
  echo "$out" | grep -q 'raw/tldrs/pending.md' || fail "pending raw source missing from Needs digestion"
  echo "$out" | grep -q '/compile raw/tldrs/pending.md' || fail "Needs digestion should show compile action"

  cat > "$k2b_vault/raw/tldrs/pending.md" <<'EOF'
---
tags: [tldr]
date: 2026-07-01
type: tldr
origin: k2b-extract
compiled: true
---

# Pending Digest
EOF
  out="$(
    K2B_VAULT_PATH="$k2b_vault" \
    K2BI_VAULT_PATH="$k2bi_vault" \
    K2B_MEMORY_DIR="$k2b_vault/System/memory" \
      bash "$PLATE_SCRIPT"
  )"
  if echo "$out" | grep -q "## 🧭 Needs digestion"; then
    fail "Needs digestion section should be omitted when no pending raw sources exist"
  fi

  echo "PASS: test_needs_digestion_surfaces_uncompiled_raw_sources"
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

test_stale_audit_fails_explicit_missing_k2bi_path() {
  local tmp k2b_vault missing_k2bi out rc
  tmp="$(mktmp)"
  k2b_vault="$tmp/K2B-Vault"
  missing_k2bi="$tmp/missing-K2Bi-Vault"

  write_clean_audit_sources "$k2b_vault"

  set +e
  out="$(
    K2B_VAULT_PATH="$k2b_vault" \
    K2B_PLATE_ALLOW_MISSING_K2BI_VAULT=1 \
    K2BI_VAULT_PATH="$missing_k2bi" \
      python3 "$AUDIT_SCRIPT" 2>&1
  )"
  rc=$?
  set -e

  [ "$rc" -eq 2 ] || fail "explicit missing K2BI_VAULT_PATH should fail with rc=2, got rc=$rc: $out"
  echo "$out" | grep -q "required source file missing" || fail "explicit missing K2BI_VAULT_PATH should be a required-source failure: $out"
  echo "$out" | grep -q "wiki/planning/index.md" || fail "missing K2Bi planning path should be named: $out"
  echo "PASS: test_stale_audit_fails_explicit_missing_k2bi_path"
}

test_stale_audit_fails_empty_explicit_k2bi_path() {
  local tmp k2b_vault fake_home default_k2bi out rc
  tmp="$(mktmp)"
  k2b_vault="$tmp/K2B-Vault"
  fake_home="$tmp/home"
  default_k2bi="$fake_home/Projects/K2Bi-Vault"

  write_clean_audit_sources "$k2b_vault"
  mkdir -p "$default_k2bi/wiki/planning"
  cat > "$default_k2bi/wiki/planning/index.md" <<'EOF'
# K2Bi Planning Workspace

> **Current state.** Clean default planning file exists and should not be used when K2BI_VAULT_PATH is explicitly empty.
EOF

  set +e
  out="$(
    HOME="$fake_home" \
    K2B_VAULT_PATH="$k2b_vault" \
    K2B_PLATE_ALLOW_MISSING_K2BI_VAULT=1 \
    K2BI_VAULT_PATH= \
      python3 "$AUDIT_SCRIPT" 2>&1
  )"
  rc=$?
  set -e

  [ "$rc" -eq 2 ] || fail "empty explicit K2BI_VAULT_PATH should fail with rc=2, got rc=$rc: $out"
  echo "$out" | grep -q "required source file missing" || fail "empty explicit K2BI_VAULT_PATH should be a required-source failure: $out"
  echo "$out" | grep -q "K2BI_VAULT_PATH is set but empty" || fail "empty explicit K2BI_VAULT_PATH should be rejected before default-path resolution: $out"
  echo "PASS: test_stale_audit_fails_empty_explicit_k2bi_path"
}

test_stale_audit_allows_explicit_missing_k2bi_vault_opt_in() {
  local tmp k2b_vault fake_home out rc
  tmp="$(mktmp)"
  k2b_vault="$tmp/K2B-Vault"
  fake_home="$tmp/home"

  write_clean_audit_sources "$k2b_vault"
  mkdir -p "$fake_home"

  cat > "$k2b_vault/wiki/concepts/feature_k2b-orchestrator.md" <<'EOF'
# K2B Orchestrator

**Where we are now (2026-06-21):** Next work is feature_orchestrator-deploy-gate.
EOF

  set +e
  out="$(
    env -u K2BI_VAULT_PATH \
      HOME="$fake_home" \
      K2B_VAULT_PATH="$k2b_vault" \
      K2B_PLATE_ALLOW_MISSING_K2BI_VAULT=1 \
      python3 "$AUDIT_SCRIPT" 2>&1
  )"
  rc=$?
  set -e

  [ "$rc" -eq 1 ] || fail "K2B stale detection should still run when missing K2Bi is explicitly allowed, got rc=$rc: $out"
  echo "$out" | grep -q "optional source file missing" || fail "allowed missing K2Bi-Vault should print warning: $out"
  echo "$out" | grep -q "feature_k2b-orchestrator.md" || fail "K2B stale detection should still report stale orchestrator tracker: $out"

  write_clean_audit_sources "$k2b_vault"

  set +e
  out="$(
    env -u K2BI_VAULT_PATH \
      HOME="$fake_home" \
      K2B_VAULT_PATH="$k2b_vault" \
      K2B_PLATE_ALLOW_MISSING_K2BI_VAULT=1 \
      python3 "$AUDIT_SCRIPT" 2>&1
  )"
  rc=$?
  set -e

  [ "$rc" -eq 0 ] || fail "explicit missing K2Bi opt-in should pass clean K2B sources with warning, got rc=$rc: $out"
  echo "$out" | grep -q "optional source file missing" || fail "allowed missing K2Bi-Vault should print warning on clean pass: $out"
  echo "$out" | grep -q "plate-freshness audit passed" || fail "allowed missing K2Bi-Vault should pass after K2B sources are clean: $out"
  echo "PASS: test_stale_audit_allows_explicit_missing_k2bi_vault_opt_in"
}

test_stale_audit_checks_default_k2bi_vault_when_present() {
  local tmp k2b_vault fake_home default_k2bi out rc
  tmp="$(mktmp)"
  k2b_vault="$tmp/K2B-Vault"
  fake_home="$tmp/home"
  default_k2bi="$fake_home/Projects/K2Bi-Vault"

  write_clean_audit_sources "$k2b_vault"
  mkdir -p "$default_k2bi/wiki/planning"
  cat > "$default_k2bi/wiki/planning/index.md" <<'EOF'
# K2Bi Planning Workspace

> **Current state.** Current work still includes feature_orchestrator-deploy-gate.
EOF

  set +e
  out="$(
    env -u K2BI_VAULT_PATH \
      HOME="$fake_home" \
      K2B_VAULT_PATH="$k2b_vault" \
      K2B_PLATE_ALLOW_MISSING_K2BI_VAULT=1 \
      python3 "$AUDIT_SCRIPT" 2>&1
  )"
  rc=$?
  set -e

  [ "$rc" -eq 1 ] || fail "default K2Bi path should be scanned when present, got rc=$rc: $out"
  echo "$out" | grep -q "wiki/planning/index.md" || fail "default K2Bi stale finding should name planning file: $out"
  if echo "$out" | grep -q "optional source file missing"; then
    fail "present default K2Bi path should not emit missing-source warning: $out"
  fi

  cat > "$default_k2bi/wiki/planning/index.md" <<'EOF'
# K2Bi Planning Workspace

> **Current state.** No shipped feature is listed as current or next.
EOF

  env -u K2BI_VAULT_PATH \
    HOME="$fake_home" \
    K2B_VAULT_PATH="$k2b_vault" \
    python3 "$AUDIT_SCRIPT" >"$tmp/default-present-clean.out" 2>&1 || {
      cat "$tmp/default-present-clean.out" >&2
      fail "default K2Bi path should pass when present and clean"
    }
  grep -q "plate-freshness audit passed" "$tmp/default-present-clean.out" || fail "clean default K2Bi path should print pass message"
  if grep -q "optional source file missing" "$tmp/default-present-clean.out"; then
    cat "$tmp/default-present-clean.out" >&2
    fail "clean present default K2Bi path should not print missing-source warning"
  fi
  echo "PASS: test_stale_audit_checks_default_k2bi_vault_when_present"
}

test_plate_script_has_sole_live_authority() {
  [ -x "$REPO_ROOT/.agents/skills/k2b-plate/scripts/plate.sh" ] || \
    fail "live .agents k2b-plate script is missing or not executable"
  [ ! -e "$REPO_ROOT/.claude" ] || \
    fail "retired .claude project tree must be absent"
  echo "PASS: test_plate_script_has_sole_live_authority"
}

write_memory_logs() {
  # $1 = vault dir; writes self_improve_requests.md and self_improve_errors.md
  local mem="$1/System/memory"
  mkdir -p "$mem"
  cat > "$mem/self_improve_requests.md" <<EOF
### R-2026-09-12-001
- **Request:** Show open requests on the plate
- **Why needed:** Truthful status
- **Status:** open
- **Date:** 2026-09-12
### R-2026-09-12-002
- **Request:** Already closed request
- **Why needed:** Fixture
- **Status:** closed
- **Date:** 2026-09-12
EOF
  cat > "$mem/self_improve_errors.md" <<EOF
### E-$(date -v-3d +%F)-001
- **What happened:** recent open error
- **Root cause:** fixture
- **Fix:** none
- **Status:** open
- **Date:** $(date -v-3d +%F)
### E-$(date -v-10d +%F)-002
- **What happened:** recent resolved error
- **Root cause:** fixture
- **Fix:** fixed
- **Status:** resolved
- **Date:** $(date -v-10d +%F)
### E-$(date -v-45d +%F)-003
- **What happened:** stale error
- **Root cause:** fixture
- **Fix:** fixed
- **Status:** resolved
- **Date:** $(date -v-45d +%F)
EOF
}

test_memory_flags_show_explicitly_open_requests_only() {
  local tmp k2b_vault k2bi_vault out
  tmp="$(mktmp)"
  k2b_vault="$tmp/K2B-Vault"
  k2bi_vault="$tmp/K2Bi-Vault"

  write_minimal_vaults "$k2b_vault" "$k2bi_vault"
  write_memory_logs "$k2b_vault"
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

  echo "$out" | grep -q "Memory flags" || fail "Memory flags section missing"
  echo "$out" | grep -q "R-2026-09-12-001" || fail "open R-ID should be listed"
  if echo "$out" | grep -q "R-2026-09-12-002"; then
    fail "closed R-ID must not appear as open"
  fi
  echo "$out" | grep -q "E-$(date -v-3d +%F)-001" || fail "recent open E-ID should be listed"
  echo "$out" | grep -q "E-$(date -v-10d +%F)-002" || fail "recent resolved E-ID should be listed"
  if echo "$out" | grep -q "E-$(date -v-45d +%F)-003"; then
    fail "E-ID older than 30 days must not appear"
  fi
  echo "PASS: test_memory_flags_show_explicitly_open_requests_only"
}

test_memory_flags_addendum_latest_dated_status_wins() {
  local tmp k2b_vault k2bi_vault out mem
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
  mem="$k2b_vault/System/memory"
  # Original entry open on day-20, resolved addendum dated day-5: not open.
  cat > "$mem/self_improve_requests.md" <<EOF
### R-2026-08-01-001
- **Request:** Duplicated request
- **Status:** open
- **Date:** $(date -v-20d +%F)
### R-2026-08-01-001
- **Request:** Duplicated request
- **Status:** resolved
- **Date:** $(date -v-5d +%F)
EOF
  cat > "$mem/self_improve_errors.md" <<'EOF'
EOF

  out="$(
    K2B_VAULT_PATH="$k2b_vault" \
    K2BI_VAULT_PATH="$k2bi_vault" \
    K2B_MEMORY_DIR="$k2b_vault/System/memory" \
      bash "$PLATE_SCRIPT"
  )"
  if echo "$out" | grep -q "R-2026-08-01-001"; then
    fail "resolved addendum must override the older open status for the same ID"
  fi

  # Reverse: resolved first, newer dated open addendum wins.
  cat > "$mem/self_improve_requests.md" <<EOF
### R-2026-08-01-001
- **Request:** Duplicated request
- **Status:** resolved
- **Date:** $(date -v-20d +%F)
### R-2026-08-01-001
- **Request:** Duplicated request
- **Status:** open
- **Date:** $(date -v-5d +%F)
EOF
  out="$(
    K2B_VAULT_PATH="$k2b_vault" \
    K2BI_VAULT_PATH="$k2bi_vault" \
    K2B_MEMORY_DIR="$k2b_vault/System/memory" \
      bash "$PLATE_SCRIPT"
  )"
  echo "$out" | grep -q "R-2026-08-01-001" || fail "newer dated open addendum should make the ID open"
  echo "PASS: test_memory_flags_addendum_latest_dated_status_wins"
}

test_memory_flags_legacy_headings_malformed_and_missing_files() {
  local tmp k2b_vault k2bi_vault out mem
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
  mem="$k2b_vault/System/memory"
  # Legacy ## headings, missing status, malformed date, future-dated error.
  cat > "$mem/self_improve_requests.md" <<EOF
## R-$(date -v-2d +%F)-001
- **Request:** Legacy heading open request
- **Status:** open
- **Date:** $(date -v-2d +%F)
## R-$(date -v-2d +%F)-002
- **Request:** Entry with no status line
- **Date:** $(date -v-2d +%F)
EOF
  cat > "$mem/self_improve_errors.md" <<EOF
### E-$(date -v-1d +%F)-001
- **What happened:** no status field
- **Date:** $(date -v-1d +%F)
### E-not-a-date-002
- **What happened:** malformed date
- **Date:** not-a-date
### E-$(date -v+2d +%F)-003
- **What happened:** future dated
- **Date:** $(date -v+2d +%F)
EOF

  out="$(
    K2B_VAULT_PATH="$k2b_vault" \
    K2BI_VAULT_PATH="$k2bi_vault" \
    K2B_MEMORY_DIR="$k2b_vault/System/memory" \
      bash "$PLATE_SCRIPT"
  )"
  echo "$out" | grep -q "R-$(date -v-2d +%F)-001" || fail "legacy ## open request should be listed"
  if echo "$out" | grep -q "R-$(date -v-2d +%F)-002"; then
    fail "entry with missing Status must never appear as open"
  fi
  echo "$out" | grep -q "E-$(date -v-1d +%F)-001" || fail "unspecified-status E-ID with real date should be listed"
  echo "$out" | grep -q "unspecified" || fail "E-ID without Status must be labelled unspecified"
  if echo "$out" | grep -q "E-not-a-date-002"; then
    fail "E-ID with malformed date must not appear"
  fi
  if echo "$out" | grep -q "E-$(date -v+2d +%F)-003"; then
    fail "future-dated E-ID must not appear"
  fi

  # Missing log files produce truthful unavailable lines, not exceptions.
  rm "$mem/self_improve_requests.md" "$mem/self_improve_errors.md"
  out="$(
    K2B_VAULT_PATH="$k2b_vault" \
    K2BI_VAULT_PATH="$k2bi_vault" \
    K2B_MEMORY_DIR="$k2b_vault/System/memory" \
      bash "$PLATE_SCRIPT"
  )"
  echo "$out" | grep -q "requests log unavailable" || fail "missing requests log should say unavailable"
  echo "$out" | grep -q "errors log unavailable" || fail "missing errors log should say unavailable"
  echo "PASS: test_memory_flags_legacy_headings_malformed_and_missing_files"
}

test_memory_flags_titled_headings_and_status_variants() {
  local tmp k2b_vault k2bi_vault out mem
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
  mem="$k2b_vault/System/memory"
  cat > "$mem/self_improve_requests.md" <<EOF
### R-2026-05-17-001 — eod-capture pipeline lock FD leak to detached grandchildren
- **Request:** Titled open entry
- **Status:** open
- **Date:** $(date -v-4d +%F)
### R-2026-05-17-002 — open with qualifier
- **Request:** Qualified status entry
- **Status:** open (waiting-for-data)
- **Date:** $(date -v-4d +%F)
### R-2026-05-17-003 — obsolete entry
- **Request:** Obsolete entry
- **Status:** closed-obsolete (superseded by R-2026-05-17-001)
- **Date:** $(date -v-4d +%F)
EOF
  cat > "$mem/self_improve_errors.md" <<'EOF'
EOF

  out="$(
    K2B_VAULT_PATH="$k2b_vault" \
    K2BI_VAULT_PATH="$k2bi_vault" \
    K2B_MEMORY_DIR="$k2b_vault/System/memory" \
      bash "$PLATE_SCRIPT"
  )"
  echo "$out" | grep -q "R-2026-05-17-001" || fail "titled open entry should be listed"
  echo "$out" | grep -q "R-2026-05-17-002" || fail "open (waiting-for-data) should be listed as open"
  if echo "$out" | grep -q "R-2026-05-17-003"; then
    fail "closed-obsolete must never appear as open"
  fi
  echo "PASS: test_memory_flags_titled_headings_and_status_variants"
}

test_memory_flags_unrelated_headings_do_not_pollute_entries() {
  local tmp k2b_vault k2bi_vault out mem
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
  mem="$k2b_vault/System/memory"
  cat > "$mem/self_improve_requests.md" <<EOF
### R-2026-06-01-001
- **Request:** Open entry before unrelated headings
- **Status:** open
- **Date:** $(date -v-20d +%F)
## Unrelated section
- **Status:** resolved
- **Date:** $(date -v-1d +%F)
### R-2026-06-01-002
- **Request:** Entry with a details subheading
- **Status:** open
- **Date:** $(date -v-20d +%F)
#### Details
- **Status:** resolved
- **Date:** $(date -v-1d +%F)
EOF
  cat > "$mem/self_improve_errors.md" <<'EOF'
EOF

  out="$(
    K2B_VAULT_PATH="$k2b_vault" \
    K2BI_VAULT_PATH="$k2bi_vault" \
    K2B_MEMORY_DIR="$k2b_vault/System/memory" \
      bash "$PLATE_SCRIPT"
  )"
  echo "$out" | grep -q "R-2026-06-01-001" || fail "unrelated peer heading must not overwrite the open status"
  echo "$out" | grep -q "R-2026-06-01-002" || fail "subheading must terminate the entry record"
  echo "PASS: test_memory_flags_unrelated_headings_do_not_pollute_entries"
}

test_memory_flags_undated_duplicates_never_open_or_clobber() {
  local tmp k2b_vault k2bi_vault out mem
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
  mem="$k2b_vault/System/memory"
  # Dated open followed by an undated resolved addendum: stays open, no crash.
  cat > "$mem/self_improve_requests.md" <<EOF
### R-2026-06-02-001
- **Request:** Dated open entry
- **Status:** open
- **Date:** $(date -v-20d +%F)
### R-2026-06-02-001 — addendum
- **Request:** Undated addendum
- **Status:** resolved
EOF
  cat > "$mem/self_improve_errors.md" <<'EOF'
EOF
  out="$(
    K2B_VAULT_PATH="$k2b_vault" \
    K2BI_VAULT_PATH="$k2bi_vault" \
    K2B_MEMORY_DIR="$k2b_vault/System/memory" \
      bash "$PLATE_SCRIPT"
  )"
  echo "$out" | grep -q "R-2026-06-02-001" || fail "undated addendum must not clobber a valid dated open status"

  # Dated resolved followed by an undated open addendum: must not become open.
  cat > "$mem/self_improve_requests.md" <<EOF
### R-2026-06-02-001
- **Request:** Dated resolved entry
- **Status:** resolved
- **Date:** $(date -v-20d +%F)
### R-2026-06-02-001 — addendum
- **Request:** Undated open addendum
- **Status:** open
EOF
  out="$(
    K2B_VAULT_PATH="$k2b_vault" \
    K2BI_VAULT_PATH="$k2bi_vault" \
    K2B_MEMORY_DIR="$k2b_vault/System/memory" \
      bash "$PLATE_SCRIPT"
  )"
  if echo "$out" | grep -q "R-2026-06-02-001"; then
    fail "undated open addendum must not make a resolved request open"
  fi

  # Duplicate with malformed dates must not crash or open.
  cat > "$mem/self_improve_requests.md" <<EOF
### R-2026-06-02-001
- **Request:** Malformed date entry
- **Status:** open
- **Date:** not-a-date
### R-2026-06-02-002
- **Request:** Future dated entry
- **Status:** open
- **Date:** $(date -v+5d +%F)
EOF
  out="$(
    K2B_VAULT_PATH="$k2b_vault" \
    K2BI_VAULT_PATH="$k2bi_vault" \
    K2B_MEMORY_DIR="$k2b_vault/System/memory" \
      bash "$PLATE_SCRIPT"
  )"
  if echo "$out" | grep -q "R-2026-06-02-001"; then
    fail "malformed-date entry must not become open"
  fi
  if echo "$out" | grep -q "R-2026-06-02-002"; then
    fail "future-date entry must not become open"
  fi
  echo "PASS: test_memory_flags_undated_duplicates_never_open_or_clobber"
}

test_memory_flags_error_addenda_collapse_by_latest_status() {
  local tmp k2b_vault k2bi_vault out mem n
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
  mem="$k2b_vault/System/memory"
  cat > "$mem/self_improve_requests.md" <<'EOF'
EOF
  # Addenda repeat the same heading ID with a newer Date field: 001 open
  # then resolved; 002 resolved then open; 003 carries a same-date addendum
  # where the last occurrence must win.
  cat > "$mem/self_improve_errors.md" <<EOF
### E-$(date -v-5d +%F)-001
- **What happened:** first open
- **Status:** open
- **Date:** $(date -v-20d +%F)
### E-$(date -v-5d +%F)-001
- **What happened:** resolved addendum
- **Status:** resolved
- **Date:** $(date -v-5d +%F)
### E-$(date -v-5d +%F)-002
- **What happened:** first resolved
- **Status:** resolved
- **Date:** $(date -v-20d +%F)
### E-$(date -v-5d +%F)-002
- **What happened:** reopened addendum
- **Status:** open
- **Date:** $(date -v-5d +%F)
### E-$(date -v-5d +%F)-003
- **What happened:** resolved first
- **Status:** resolved
- **Date:** $(date -v-5d +%F)
### E-$(date -v-5d +%F)-003
- **What happened:** same-day open addendum
- **Status:** open
- **Date:** $(date -v-5d +%F)
EOF

  out="$(
    K2B_VAULT_PATH="$k2b_vault" \
    K2BI_VAULT_PATH="$k2bi_vault" \
    K2B_MEMORY_DIR="$k2b_vault/System/memory" \
      bash "$PLATE_SCRIPT"
  )"

  n="$(printf '%s\n' "$out" | grep -c -- "- E-.*-001 (")"
  [ "$n" -eq 1 ] || fail "collapsed E-...-001 must appear exactly once, got $n"
  printf '%s\n' "$out" | grep -q -- "- E-.*-001 ($(date -v-5d +%F), resolved)" \
    || fail "newer resolved addendum must label E-...-001 resolved"
  printf '%s\n' "$out" | grep -q -- "- E-.*-002 ($(date -v-5d +%F), open)" \
    || fail "newer open addendum must label E-...-002 open"
  printf '%s\n' "$out" | grep -q -- "- E-.*-003 ($(date -v-5d +%F), open)" \
    || fail "same-date tie must use the last occurrence (open)"
  echo "PASS: test_memory_flags_error_addenda_collapse_by_latest_status"
}

test_memory_flags_error_slots_use_collapsed_ids() {
  local tmp k2b_vault k2bi_vault out mem n
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
  mem="$k2b_vault/System/memory"
  cat > "$mem/self_improve_requests.md" <<'EOF'
EOF
  # 001: same heading open outside the window, resolved inside -> resolved,
  # one slot. 002: open inside, future open addendum must not change or
  # double it. 003: resolved inside, malformed-date addendum must not
  # clobber it. 004/005/006: distinct recent errors pushed out of the top-3
  # slots by the collapsed first three IDs.
  cat > "$mem/self_improve_errors.md" <<EOF
### E-$(date -v-5d +%F)-001
- **What happened:** old open
- **Status:** open
- **Date:** $(date -v-40d +%F)
### E-$(date -v-5d +%F)-001
- **What happened:** resolved addendum
- **Status:** resolved
- **Date:** $(date -v-5d +%F)
### E-$(date -v-10d +%F)-002
- **What happened:** open
- **Status:** open
- **Date:** $(date -v-10d +%F)
### E-$(date -v-10d +%F)-002
- **What happened:** future open addendum
- **Status:** open
- **Date:** $(date -v+2d +%F)
### E-$(date -v-10d +%F)-003
- **What happened:** resolved
- **Status:** resolved
- **Date:** $(date -v-10d +%F)
### E-$(date -v-10d +%F)-003
- **What happened:** malformed-date addendum
- **Status:** open
- **Date:** not-a-date
### E-$(date -v-4d +%F)-004
- **What happened:** distinct recent
- **Status:** open
- **Date:** $(date -v-4d +%F)
### E-$(date -v-3d +%F)-005
- **What happened:** distinct recent
- **Status:** open
- **Date:** $(date -v-3d +%F)
### E-$(date -v-2d +%F)-006
- **What happened:** distinct recent
- **Status:** open
- **Date:** $(date -v-2d +%F)
EOF

  out="$(
    K2B_VAULT_PATH="$k2b_vault" \
    K2BI_VAULT_PATH="$k2bi_vault" \
    K2B_MEMORY_DIR="$k2b_vault/System/memory" \
      bash "$PLATE_SCRIPT"
  )"

  n="$(printf '%s\n' "$out" | grep -c -- "- E-.*-001 (")"
  [ "$n" -eq 1 ] || fail "out-of-window duplicate must collapse to one in-window row, got $n"
  printf '%s\n' "$out" | grep -q -- "- E-.*-001 ($(date -v-5d +%F), resolved)" \
    || fail "in-window resolved addendum must win for E-...-001"
  n="$(printf '%s\n' "$out" | grep -c -- "- E-.*-002 (")"
  [ "$n" -eq 1 ] || fail "future addendum must not create a second E-...-002 row, got $n"
  printf '%s\n' "$out" | grep -q -- "- E-.*-002 ($(date -v-10d +%F), open)" \
    || fail "future addendum must not override the valid dated open status"
  printf '%s\n' "$out" | grep -q -- "- E-.*-003 ($(date -v-10d +%F), resolved)" \
    || fail "malformed-date addendum must not override the valid dated resolved status"
  if printf '%s\n' "$out" | grep -q -- "- E-.*-004 "; then
    fail "E-...-004 must be pushed out of the top-3 by collapsed duplicates"
  fi
  if printf '%s\n' "$out" | grep -q -- "- E-.*-005 "; then
    fail "E-...-005 must be pushed out of the top-3 by collapsed duplicates"
  fi
  if printf '%s\n' "$out" | grep -q -- "- E-.*-006 "; then
    fail "E-...-006 must be pushed out of the top-3 by collapsed duplicates"
  fi
  n="$(printf '%s\n' "$out" | grep -c -- "^- E-")"
  [ "$n" -eq 3 ] || fail "top-3 must show exactly three collapsed rows, got $n"
  echo "PASS: test_memory_flags_error_slots_use_collapsed_ids"
}

test_memory_flags_today_uses_hkt_calendar_date() {
  local tmp k2b_vault k2bi_vault out mem
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
  # Deterministic HKT boundary with a non-HKT host timezone: a fake `date`
  # shadows the +%Y-%m-%d calls, pinning one instant where the HKT calendar
  # date is 2026-09-14 and a UTC host date is 2026-09-13. An error dated
  # 2026-08-15 is exactly 30 days old under the HKT date (window boundary,
  # shown); 2026-08-14 is 31 days old under HKT but 30 under a UTC host date
  # (hidden). A bare host-local --today would show both. No reliance on the
  # current wall-clock hour.
  mkdir -p "$tmp/bin"
  cat > "$tmp/bin/date" <<'EOF'
#!/usr/bin/env bash
for arg in "$@"; do
  case "$arg" in
    +%Y-%m-%d|+%F)
      if [[ "${TZ:-}" == "Asia/Hong_Kong" ]]; then
        echo "2026-09-14"
      else
        echo "2026-09-13"
      fi
      exit 0 ;;
  esac
done
exec /bin/date "$@"
EOF
  chmod +x "$tmp/bin/date"
  mem="$k2b_vault/System/memory"
  cat > "$mem/self_improve_requests.md" <<'EOF'
EOF
  cat > "$mem/self_improve_errors.md" <<'EOF'
### E-2026-08-14-001
- **What happened:** thirty-one days old by the HKT date
- **Root cause:** fixture
- **Status:** open
- **Date:** 2026-08-14
### E-2026-08-15-001
- **What happened:** exactly thirty days old by the HKT date
- **Root cause:** fixture
- **Status:** open
- **Date:** 2026-08-15
EOF

  out="$(
    PATH="$tmp/bin:$PATH" \
    TZ=UTC \
    K2B_VAULT_PATH="$k2b_vault" \
    K2BI_VAULT_PATH="$k2bi_vault" \
    K2B_MEMORY_DIR="$k2b_vault/System/memory" \
      bash "$PLATE_SCRIPT"
  )"

  echo "$out" | grep -q "E-2026-08-15-001" \
    || fail "error exactly 30 days old under the HKT calendar date must be listed"
  if echo "$out" | grep -q "E-2026-08-14-001"; then
    fail "error 31 days old under the HKT calendar date must be hidden even when the UTC host date would show it"
  fi
  echo "PASS: test_memory_flags_today_uses_hkt_calendar_date"
}

test_recent_shipped_reads_inline_and_archived_rows
test_recent_shipped_empty_section_falls_back_to_none
test_recent_shipped_missing_section_falls_back_to_none
test_needs_digestion_surfaces_uncompiled_raw_sources
test_stale_audit_detects_shipped_feature_named_current_or_next
test_stale_audit_fails_explicit_missing_k2bi_path
test_stale_audit_fails_empty_explicit_k2bi_path
test_stale_audit_allows_explicit_missing_k2bi_vault_opt_in
test_stale_audit_checks_default_k2bi_vault_when_present
test_plate_script_has_sole_live_authority
test_memory_flags_show_explicitly_open_requests_only
test_memory_flags_addendum_latest_dated_status_wins
test_memory_flags_legacy_headings_malformed_and_missing_files
test_memory_flags_titled_headings_and_status_variants
test_memory_flags_unrelated_headings_do_not_pollute_entries
test_memory_flags_undated_duplicates_never_open_or_clobber
test_memory_flags_error_addenda_collapse_by_latest_status
test_memory_flags_error_slots_use_collapsed_ids
test_memory_flags_today_uses_hkt_calendar_date
