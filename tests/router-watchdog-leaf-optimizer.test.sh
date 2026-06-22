#!/usr/bin/env bash
# tests/router-watchdog-leaf-optimizer.test.sh
# Retirement tests for the leaf optimizer wrapper and Python stub.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC_DIR="$REPO_ROOT/scripts/router-watchdog"
OPTIMIZER="$SRC_DIR/bin/optimize-leaves.py"
OPTIMIZER_SH="$SRC_DIR/bin/optimize-leaves.sh"

TMPROOT="$(mktemp -d)"
trap 'rm -rf "$TMPROOT"' EXIT

fail() { echo "FAIL: $*" >&2; exit 1; }

echo "=== router-watchdog-leaf-optimizer.test.sh ==="

# ---------------------------------------------------------------------------
# Test 1: optimize-leaves.py is retired and performs no mutation by default.
# ---------------------------------------------------------------------------
{
  d="$TMPROOT/py-retired"
  mkdir -p "$d"
  test_home="$d/home"
  mkdir -p "$test_home"

  set +e
  HOME="$test_home" python3 "$OPTIMIZER" >"$d/out" 2>"$d/err"
  rc=$?
  set -e
  [[ "$rc" -eq 0 ]] || fail "retired optimize-leaves.py should exit 0, got $rc"
  grep -qi "is retired and performed no mutation" "$d/err" || fail "retired optimize-leaves.py should explain retirement"
  [[ -s "$test_home/Library/Logs/k2b-router-watchdog/retired-mutations.jsonl" ]] || fail "retired optimize-leaves.py should leave an audit log"
  echo "  PASS: optimize-leaves.py retired stub no-ops and audits"
}

# ---------------------------------------------------------------------------
# Test 2: optimize-leaves.py stays retired even with legacy enablement.
# ---------------------------------------------------------------------------
{
  d="$TMPROOT/py-no-bypass"
  mkdir -p "$d"
  test_home="$d/home"
  mkdir -p "$test_home"

  set +e
  HOME="$test_home" \
  K2B_ROUTER_MUTATION_RETIRED_ALLOW=1 \
  MIHOMO_API_BASE="http://127.0.0.1:1" \
  MIHOMO_API_SECRET="test-secret" \
  MIHOMO_OPENAI_GROUP="🤖 OpenAI" \
  python3 "$OPTIMIZER" \
    --profiles-file "$d/profiles.json" \
    --profile ai \
    --sentinel "$d/sentinel" \
    --now "2026-05-06T00:00:00Z" >"$d/out" 2>"$d/err"
  rc=$?
  set -e
  [[ "$rc" -eq 0 ]] || fail "retired optimize-leaves.py should exit 0 even with legacy enablement, got $rc"
  grep -qi "is retired and performed no mutation" "$d/err" || fail "retired optimize-leaves.py should explain retirement even with legacy enablement"
  [[ -s "$test_home/Library/Logs/k2b-router-watchdog/retired-mutations.jsonl" ]] || fail "retired optimize-leaves.py should leave an audit log"
  echo "  PASS: optimize-leaves.py has no runtime bypass"
}

# ---------------------------------------------------------------------------
# Test 3: optimize-leaves.sh is retired and performs no mutation.
# ---------------------------------------------------------------------------
{
  d="$TMPROOT/sh-retired"
  mkdir -p "$d"
  test_home="$d/home"
  mkdir -p "$test_home"

  set +e
  HOME="$test_home" bash "$OPTIMIZER_SH" --dry-run >"$d/out" 2>"$d/err"
  rc=$?
  set -e
  [[ "$rc" -eq 0 ]] || fail "retired optimize-leaves.sh should exit 0, got $rc"
  grep -qi "is retired and performed no mutation" "$d/err" || fail "retired optimize-leaves.sh should explain retirement"
  [[ -s "$test_home/Library/Logs/k2b-router-watchdog/retired-mutations.jsonl" ]] || fail "retired optimize-leaves.sh should leave an audit log"
  echo "  PASS: optimize-leaves.sh retired stub no-ops and audits"
}

# ---------------------------------------------------------------------------
# Test 4: retired audit log rotation works for the leaf optimizer.
# ---------------------------------------------------------------------------
{
  d="$TMPROOT/audit-rotation"
  mkdir -p "$d"
  test_home="$d/home"
  mkdir -p "$test_home"
  audit_log="$test_home/Library/Logs/k2b-router-watchdog/retired-mutations.jsonl"
  mkdir -p "$(dirname "$audit_log")"
  printf 'old audit row\n' > "$audit_log"

  HOME="$test_home" K2B_ROUTER_RETIRED_AUDIT_MAX_BYTES=1 python3 "$OPTIMIZER" >"$d/out" 2>"$d/err"
  [[ -f "$audit_log.1" ]] || fail "retired mutation audit log should rotate when capped"
  grep -q '"script":"optimize-leaves.py"' "$audit_log" || fail "retired mutation audit log should record after rotation"
  echo "  PASS: leaf optimizer audit log rotates"
}

# ---------------------------------------------------------------------------
# Test 5: retired audit logging failure exits non-zero with a warning.
# ---------------------------------------------------------------------------
{
  d="$TMPROOT/audit-failure"
  mkdir -p "$d"
  test_home="$d/home"
  mkdir -p "$test_home"

  set +e
  HOME="$test_home" K2B_ROUTER_RETIRED_AUDIT_LOG="$d" python3 "$OPTIMIZER" >"$d/out" 2>"$d/err"
  rc=$?
  set -e
  [[ "$rc" -eq 2 ]] || fail "optimize-leaves.py should return 2 when retired audit logging fails, got $rc"
  grep -q "failed to write retired mutation audit log" "$d/err" || fail "optimize-leaves.py audit failure should warn"
  echo "  PASS: leaf optimizer audit failure fails closed"
}

# ---------------------------------------------------------------------------
# Test 6: leaf-optimizer-profiles.json is not present in tracked source.
# ---------------------------------------------------------------------------
{
  [[ ! -f "$SRC_DIR/bin/leaf-optimizer-profiles.json" ]] || fail "retired leaf optimizer profile must not be in tracked source"
  echo "  PASS: leaf-optimizer-profiles.json absent from tracked source"
}

echo "router-watchdog-leaf-optimizer.test.sh: all tests passed"
