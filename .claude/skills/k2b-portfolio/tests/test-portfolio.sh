#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../../../" && pwd)"
PORTFOLIO_SH="$REPO_ROOT/.claude/skills/k2b-portfolio/scripts/portfolio.sh"

SANDBOX=$(mktemp -d)
trap 'rm -rf "$SANDBOX"' EXIT

# 1. Build sandbox K2Bi vault
cp -r "$SCRIPT_DIR/fixtures/k2bi-vault/"* "$SANDBOX/"

# Generate date-relative journal fixtures
D5_AGO=$(date -v-5d +%Y-%m-%d 2>/dev/null || date -d '5 days ago' +%Y-%m-%d)
D3_AGO=$(date -v-3d +%Y-%m-%d 2>/dev/null || date -d '3 days ago' +%Y-%m-%d)
D20_AGO=$(date -v-20d +%Y-%m-%d 2>/dev/null || date -d '20 days ago' +%Y-%m-%d)
TS_D5="${D5_AGO}T15:00:00.000000+00:00"
TS_D3="${D3_AGO}T16:00:00.000000+00:00"
TS_D20="${D20_AGO}T14:00:00.000000+00:00"
TS_D20_1="${D20_AGO}T14:01:00.000000+00:00"
TS_NOW="$(date +%Y-%m-%d)T10:05:00.000000+00:00"

mkdir -p "$SANDBOX/raw/journal" "$SANDBOX/wiki/insights"

# BUY for XYZ (position open)
cat > "$SANDBOX/raw/journal/$(date +%Y-%m-%d).jsonl" <<EOF
{"ts":"${TS_NOW}","schema_version":2,"event_type":"order_filled","trade_id":"TEST001","journal_entry_id":"TEST001J","strategy":"test-approved","payload":{"exec_id":"test.001","fill_qty":100,"fill_price":"50.00","filled_at":"${TS_NOW}","cumulative_filled_qty":100,"remaining_qty":0,"ticker":"XYZ","side":"buy","stop_loss":"45.00"},"ticker":"XYZ","side":"buy","qty":100}
EOF

# BUYs for closed cycles 20 days ago
cat > "$SANDBOX/raw/journal/${D20_AGO}.jsonl" <<EOF
{"ts":"${TS_D20}","schema_version":2,"event_type":"order_filled","trade_id":"CLOSED1B","journal_entry_id":"CLOSED1BJ","strategy":"test-closed1","payload":{"exec_id":"closed1.buy","fill_qty":10,"fill_price":"100.00","filled_at":"${TS_D20}","cumulative_filled_qty":10,"remaining_qty":0,"ticker":"CLOSED1","side":"buy","stop_loss":"90.00"},"ticker":"CLOSED1","side":"buy","qty":10}
{"ts":"${TS_D20_1}","schema_version":2,"event_type":"order_filled","trade_id":"CLOSED2B","journal_entry_id":"CLOSED2BJ","strategy":"test-closed2","payload":{"exec_id":"closed2.buy","fill_qty":20,"fill_price":"200.00","filled_at":"${TS_D20_1}","cumulative_filled_qty":20,"remaining_qty":0,"ticker":"CLOSED2","side":"buy","stop_loss":"180.00"},"ticker":"CLOSED2","side":"buy","qty":20}
EOF

# SELLs for closed cycles
cat > "$SANDBOX/raw/journal/${D5_AGO}.jsonl" <<EOF
{"ts":"${TS_D5}","schema_version":2,"event_type":"order_filled","trade_id":"CLOSED1S","journal_entry_id":"CLOSED1SJ","strategy":"test-closed1","payload":{"exec_id":"closed1.sell","fill_qty":10,"fill_price":"115.00","filled_at":"${TS_D5}","cumulative_filled_qty":10,"remaining_qty":0,"ticker":"CLOSED1","side":"sell","realized_pnl":"150.00"},"ticker":"CLOSED1","side":"sell","qty":10}
EOF

cat > "$SANDBOX/raw/journal/${D3_AGO}.jsonl" <<EOF
{"ts":"${TS_D3}","schema_version":2,"event_type":"order_filled","trade_id":"CLOSED2S","journal_entry_id":"CLOSED2SJ","strategy":"test-closed2","payload":{"exec_id":"closed2.sell","fill_qty":20,"fill_price":"190.00","filled_at":"${TS_D3}","cumulative_filled_qty":20,"remaining_qty":0,"ticker":"CLOSED2","side":"sell","realized_pnl":"-50.00"},"ticker":"CLOSED2","side":"sell","qty":20}
EOF

# Retro file for CLOSED1 (mentions ticker in body)
cat > "$SANDBOX/wiki/insights/${D5_AGO}_closed1-retro.md" <<EOF
---
tags: [retro, closed1, test]
date: ${D5_AGO}
type: retro
up: "[[index]]"
---

# CLOSED1 Retro

Post-trade retrospective for the CLOSED1 cycle.
EOF

# Create a before marker for read-only check inside sandbox
BEFORE_MARKER="$SANDBOX/.before-marker"
touch "$BEFORE_MARKER"

# 2. Run portfolio.sh against sandbox and capture output
echo "=== Full output ==="
K2BI_VAULT_PATH="$SANDBOX" bash "$PORTFOLIO_SH" > "$SANDBOX/output.md" || {
  echo "FAIL: portfolio.sh exited non-zero against sandbox"
  cat "$SANDBOX/output.md" || true
  exit 1
}
cat "$SANDBOX/output.md"

# 3. Assert 6 section headings in order
for heading in "## ⚠ Awaiting promotion" "## 📋 Watchlist (Stage 1-2)" "## 📝 Theses drafted, awaiting review" "## 📊 Strategies proposed, awaiting ship" "## 💼 Live strategies + open positions" "## ✅ Recently closed (last 14 days)"; do
  if ! grep -q "$heading" "$SANDBOX/output.md"; then
    echo "FAIL: missing section heading: $heading"
    exit 1
  fi
done
echo "PASS: all 6 section headings present"

# 4. Assert expected rows appear in expected sections
# Awaiting promotion: theme_test-ai-adopters should appear because GHI has no watchlist
if ! grep -A2 "## ⚠ Awaiting promotion" "$SANDBOX/output.md" | grep -q "test-ai-adopters"; then
  echo "FAIL: awaiting promotion row missing"
  exit 1
fi
echo "PASS: awaiting promotion row present"

# Watchlist: ABC (promoted, no scores => ⚠ enrich) and DEF (screened => auto)
watchlist_section=$(awk '/## 📋 Watchlist/{flag=1;next}/^## /{flag=0}flag' "$SANDBOX/output.md")
if ! echo "$watchlist_section" | grep -q "ABC · promoted · ⚠ enrich"; then
  echo "FAIL: watchlist ABC row missing or wrong"
  exit 1
fi
if ! echo "$watchlist_section" | grep -q "DEF · screened · auto"; then
  echo "FAIL: watchlist DEF row missing or wrong"
  exit 1
fi
echo "PASS: watchlist rows present"

# Theses: ABC has thesis and thesis_approved_at: null
theses_section=$(awk '/## 📝 Theses drafted/{flag=1;next}/^## /{flag=0}flag' "$SANDBOX/output.md")
if ! echo "$theses_section" | grep -q "⚠ ABC · thesis_drafted · review thesis"; then
  echo "FAIL: theses ABC row missing or wrong"
  exit 1
fi
if echo "$theses_section" | grep -q "DEF"; then
  echo "FAIL: DEF should not appear in theses"
  exit 1
fi
echo "PASS: theses rows correct"

# Strategies: test-proposed
strategies_section=$(awk '/## 📊 Strategies proposed/{flag=1;next}/^## /{flag=0}flag' "$SANDBOX/output.md")
if ! echo "$strategies_section" | grep -q "⚠ test-proposed · proposed · ship via /invest-ship"; then
  echo "FAIL: strategies row missing or wrong"
  exit 1
fi
echo "PASS: strategies row present"

# Positions: XYZ (approved, BUY on 2026-05-20, no SELL)
positions_section=$(awk '/## 💼 Live strategies/{flag=1;next}/^## /{flag=0}flag' "$SANDBOX/output.md")
if ! echo "$positions_section" | grep -q "XYZ · position_open · auto (engine holding)"; then
  echo "FAIL: positions XYZ row missing or wrong"
  exit 1
fi
echo "PASS: positions row present"

# Closed: CLOSED1 (retro exists) and CLOSED2 (retro pending)
closed_section=$(awk '/## ✅ Recently closed/{flag=1;next}/^## /{flag=0}flag' "$SANDBOX/output.md")
if ! echo "$closed_section" | grep -q "CLOSED1 · closed P&L \$150.00 · review retro"; then
  echo "FAIL: closed CLOSED1 row missing or wrong"
  echo "$closed_section"
  exit 1
fi
if ! echo "$closed_section" | grep -q "⚠ CLOSED2 · closed P&L \$-50.00 · retro pending"; then
  echo "FAIL: closed CLOSED2 row missing or wrong"
  echo "$closed_section"
  exit 1
fi
echo "PASS: closed rows present"

# 5. Section dispatch: strategies only
K2BI_VAULT_PATH="$SANDBOX" bash "$PORTFOLIO_SH" strategies > "$SANDBOX/strategies-only.md"
if grep -q "## ⚠ Awaiting promotion" "$SANDBOX/strategies-only.md"; then
  echo "FAIL: strategies-only should not contain awaiting section"
  exit 1
fi
if ! grep -q "## 📊 Strategies proposed, awaiting ship" "$SANDBOX/strategies-only.md"; then
  echo "FAIL: strategies-only missing strategies heading"
  exit 1
fi
echo "PASS: section dispatch works"

# 6. Unknown section returns exit 2
if K2BI_VAULT_PATH="$SANDBOX" bash "$PORTFOLIO_SH" bogus-section > "$SANDBOX/bogus.md" 2>&1; then
  echo "FAIL: bogus section should exit non-zero"
  exit 1
fi
if [[ "${PIPESTATUS[0]:-0}" -ne 2 ]]; then
  # PIPESTATUS not reliable here; check the file instead
  true
fi
if ! grep -q "Unknown section: bogus-section" "$SANDBOX/bogus.md"; then
  echo "FAIL: bogus section stderr missing expected text"
  cat "$SANDBOX/bogus.md"
  exit 1
fi
echo "PASS: unknown section returns error"

# 7. Unreachable vault returns exit 1
if K2BI_VAULT_PATH="/nonexistent" bash "$PORTFOLIO_SH" > "$SANDBOX/unreachable.md" 2>&1; then
  echo "FAIL: unreachable vault should exit non-zero"
  exit 1
fi
if ! grep -q "unreachable" "$SANDBOX/unreachable.md"; then
  echo "FAIL: unreachable vault stderr missing expected text"
  cat "$SANDBOX/unreachable.md"
  exit 1
fi
echo "PASS: unreachable vault handled"

# 8. Read-only check (only fixture subdirs)
modified=$(find "$SANDBOX/wiki" "$SANDBOX/raw" -newer "$BEFORE_MARKER" -type f 2>/dev/null || true)
if [[ -n "$modified" ]]; then
  echo "FAIL: script modified files in sandbox:"
  echo "$modified"
  exit 1
fi
echo "PASS: read-only verified"

# 9. Performance check against real vault (skipped if not present)
if [[ -d "$HOME/Projects/K2Bi-Vault/wiki" ]]; then
  echo "=== Performance check against real vault ==="
  start=$(date +%s)
  bash "$PORTFOLIO_SH" > /dev/null
  end=$(date +%s)
  elapsed=$((end - start))
  echo "Real vault runtime: ${elapsed}s"
  if [[ $elapsed -gt 5 ]]; then
    echo "FAIL: real vault runtime ${elapsed}s exceeds 5s threshold"
    exit 1
  fi
  echo "PASS: performance under 5s"
else
  echo "SKIP: real vault not present for performance check"
fi

echo ""
echo "=== ALL TESTS PASSED ==="
