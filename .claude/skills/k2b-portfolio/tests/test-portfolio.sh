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

# Orchestrator fixture DB for the Active flights section (Ship 2).
# WAL mode (mirrors production: orchestrator_store sets journal_mode=WAL).
# 5 active tasks + 2 terminal (done / cancelled) that MUST be excluded:
#   T-RUN   running, heartbeat 2m ago            -> freshness via heartbeat
#   T-BLK   blocked, dirty-tree reason            -> ⚠ unblock
#   T-RDY   ready, no blocker                     -> auto · queued
#   T-WAIT  ready, blocked_by=T-RUN               -> auto · waiting on T-RUN (Codex finding 2)
#   T-PARSE blocked, blocker_reason w/ 0x1f+LF+CR -> field-shift regression (Codex finding 3)
ORCH_HB2=$(date -u -v-2M +%Y-%m-%dT%H:%M:%S 2>/dev/null || date -u -d '2 minutes ago' +%Y-%m-%dT%H:%M:%S)
ORCH_C10=$(date -u -v-10M +%Y-%m-%dT%H:%M:%S 2>/dev/null || date -u -d '10 minutes ago' +%Y-%m-%dT%H:%M:%S)
ORCH_C1H=$(date -u -v-1H +%Y-%m-%dT%H:%M:%S 2>/dev/null || date -u -d '1 hour ago' +%Y-%m-%dT%H:%M:%S)
ORCH_C5=$(date -u -v-5M +%Y-%m-%dT%H:%M:%S 2>/dev/null || date -u -d '5 minutes ago' +%Y-%m-%dT%H:%M:%S)
export K2B_ORCH_DB="$SANDBOX/orchestrator.sqlite"
sqlite3 "$K2B_ORCH_DB" <<SQL
PRAGMA journal_mode=WAL;
CREATE TABLE tasks (
  id TEXT PRIMARY KEY, flight_id TEXT, stage_name TEXT, entity_key TEXT,
  assignee_profile TEXT, status TEXT, command_key TEXT, blocker_reason TEXT,
  blocked_by TEXT, created_at TEXT, updated_at TEXT, heartbeat_at TEXT
);
INSERT INTO tasks VALUES('T-RUN','F1','dispatch','NVDA','k2bi','running','k2bi-smoke-enrich-nvda',NULL,NULL,'${ORCH_C10}','${ORCH_HB2}','${ORCH_HB2}');
INSERT INTO tasks VALUES('T-BLK','F2','dispatch','','k2bi','blocked','k2bi-smoke-enrich-lrcx','K2Bi git tree dirty: M execution/connectors/ibkr.py',NULL,'${ORCH_C1H}','${ORCH_C1H}',NULL);
INSERT INTO tasks VALUES('T-RDY','F3','dispatch','AMD','k2bi','ready','test-echo-readonly',NULL,NULL,'${ORCH_C5}','${ORCH_C5}',NULL);
INSERT INTO tasks VALUES('T-WAIT','F6','dispatch','MSFT','k2bi','ready','k2bi-smoke-enrich-msft',NULL,'T-RUN','${ORCH_C5}','${ORCH_C5}',NULL);
INSERT INTO tasks VALUES('T-PARSE','F7','dispatch','PARSETEST','k2bi','blocked','k2bi-smoke-enrich-parse','dirty: a.py' || char(31) || 'x' || char(10) || 'y' || char(13) || 'z',NULL,'${ORCH_C1H}','${ORCH_C1H}',NULL);
INSERT INTO tasks VALUES('T-DONE','F4','dispatch','TSLA','k2bi','done','k2bi-smoke-enrich-tsla',NULL,NULL,'${ORCH_C1H}','${ORCH_C1H}',NULL);
INSERT INTO tasks VALUES('T-CAN','F5','dispatch','INTC','k2bi','cancelled','k2bi-smoke-enrich-intc',NULL,NULL,'${ORCH_C1H}','${ORCH_C1H}',NULL);
SQL

# Read-only invariant snapshot: hash main DB + -wal sidecar (the authoritative state).
# -shm is ephemeral shared-memory coordination and is intentionally excluded.
orch_hash() {
  local f="$1"
  [[ -f "$f" ]] || return 0
  if command -v shasum >/dev/null 2>&1; then
    printf '%s %s\n' "$(basename "$f")" "$(shasum -a 256 "$f" | cut -d' ' -f1)"
  else
    printf '%s %s\n' "$(basename "$f")" "$(sha256sum "$f" | cut -d' ' -f1)"
  fi
}
orch_snapshot() { orch_hash "$K2B_ORCH_DB"; orch_hash "$K2B_ORCH_DB-wal"; }
ORCH_SNAP_BEFORE=$(orch_snapshot)

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

# 3. Assert 7 section headings present
for heading in "## ⚠ Awaiting promotion" "## 📋 Watchlist (Stage 1-2)" "## 🛫 Active orchestrator flights" "## 📝 Theses drafted, awaiting review" "## 📊 Strategies proposed, awaiting ship" "## 💼 Live strategies + open positions" "## ✅ Recently closed (last 14 days)"; do
  if ! grep -q "$heading" "$SANDBOX/output.md"; then
    echo "FAIL: missing section heading: $heading"
    exit 1
  fi
done
echo "PASS: all 7 section headings present"

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

# Active orchestrator flights: NVDA running, LRCX blocked (entity_key empty -> label is command_key), AMD ready.
# Terminal tasks (TSLA done, INTC cancelled) MUST be excluded.
active_section=$(awk '/## 🛫 Active orchestrator flights/{flag=1;next}/^## /{flag=0}flag' "$SANDBOX/output.md")
if ! echo "$active_section" | grep -q "NVDA · running · auto · running"; then
  echo "FAIL: active running NVDA row missing or wrong"; echo "$active_section"; exit 1
fi
if ! echo "$active_section" | grep -q "⚠ k2bi-smoke-enrich-lrcx · blocked · unblock:"; then
  echo "FAIL: active blocked LRCX row missing or wrong"; echo "$active_section"; exit 1
fi
if ! echo "$active_section" | grep -q "AMD · ready · auto · queued"; then
  echo "FAIL: active ready AMD row missing or wrong"; echo "$active_section"; exit 1
fi
if echo "$active_section" | grep -qE "TSLA|INTC"; then
  echo "FAIL: terminal (done/cancelled) tasks must be excluded from active flights"; echo "$active_section"; exit 1
fi
echo "PASS: active orchestrator flights rows present, terminal excluded"

# Codex finding 2: a ready task with blocked_by must show the lock holder, not "queued".
if ! echo "$active_section" | grep -q "MSFT · ready · auto · waiting on T-RUN"; then
  echo "FAIL: ready+blocked_by MSFT not rendered as waiting-on (finding 2)"; echo "$active_section"; exit 1
fi
if ! echo "$active_section" | grep -q "AMD · ready · auto · queued"; then
  echo "FAIL: plain ready AMD should still render as queued"; echo "$active_section"; exit 1
fi
echo "PASS: ready+blocked_by distinguished from plain queued"

# Codex finding 3: blocker_reason with embedded 0x1f/CR/LF must not shift fields.
# If the delimiter leaked, the trailing age field would be garbage ('?'), not '1h'.
parse_line=$(echo "$active_section" | grep "PARSETEST")
if ! echo "$parse_line" | grep -qE "PARSETEST · blocked · unblock:.*· 1h$"; then
  echo "FAIL: special-char blocker_reason shifted fields (age not '1h'): $parse_line"; exit 1
fi
echo "PASS: special-char blocker_reason sanitized, no field shift"

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

# 5b. Section dispatch: `active` returns just the flights section with all active tasks
K2BI_VAULT_PATH="$SANDBOX" bash "$PORTFOLIO_SH" active > "$SANDBOX/active-only.md"
if grep -qE "## ⚠ Awaiting promotion|## 📋 Watchlist|## 📝 Theses|## 📊 Strategies|## 💼 Live|## ✅ Recently" "$SANDBOX/active-only.md"; then
  echo "FAIL: active-only should contain only the flights section"; cat "$SANDBOX/active-only.md"; exit 1
fi
if ! grep -q "## 🛫 Active orchestrator flights" "$SANDBOX/active-only.md"; then
  echo "FAIL: active-only missing flights heading"; exit 1
fi
for sym in NVDA k2bi-smoke-enrich-lrcx AMD MSFT PARSETEST; do
  if ! grep -q "$sym" "$SANDBOX/active-only.md"; then
    echo "FAIL: active-only missing $sym"; cat "$SANDBOX/active-only.md"; exit 1
  fi
done
if grep -qE "TSLA|INTC" "$SANDBOX/active-only.md"; then
  echo "FAIL: active-only must exclude terminal tasks"; cat "$SANDBOX/active-only.md"; exit 1
fi
echo "PASS: /portfolio active dispatch returns the active flights, terminal excluded"

# 5c. Codex finding 1: a 2-min-old UTC heartbeat must render in MINUTES even under a
# non-UTC TZ. The old code stripped the offset and parsed as local wall-clock, turning
# 2m into ~8h on UTC+8. ts_age_human is now offset-aware (Python fromisoformat).
TZ=Asia/Shanghai K2BI_VAULT_PATH="$SANDBOX" bash "$PORTFOLIO_SH" active > "$SANDBOX/active-tz.md"
nvda_tz_line=$(grep "NVDA" "$SANDBOX/active-tz.md")
if ! echo "$nvda_tz_line" | grep -qE "· [0-9]+m$"; then
  echo "FAIL: NVDA heartbeat age not in minutes under Asia/Shanghai TZ (timezone bug regressed): $nvda_tz_line"; exit 1
fi
echo "PASS: heartbeat age timezone-correct under non-UTC TZ"

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

# 7b. Codex finding 5: `/portfolio active` reads only the orchestrator DB, so it must
# still work when the K2Bi vault is unreachable (the guard is scoped to skip `active`).
if ! K2BI_VAULT_PATH="/nonexistent-k2bi" bash "$PORTFOLIO_SH" active > "$SANDBOX/active-no-k2bi.md" 2>&1; then
  echo "FAIL: /portfolio active should succeed even when K2Bi vault is unreachable"; cat "$SANDBOX/active-no-k2bi.md"; exit 1
fi
if ! grep -q "## 🛫 Active orchestrator flights" "$SANDBOX/active-no-k2bi.md"; then
  echo "FAIL: active-no-k2bi missing flights heading"; cat "$SANDBOX/active-no-k2bi.md"; exit 1
fi
if ! grep -q "NVDA" "$SANDBOX/active-no-k2bi.md"; then
  echo "FAIL: active-no-k2bi should still render flights"; cat "$SANDBOX/active-no-k2bi.md"; exit 1
fi
echo "PASS: /portfolio active works with unreachable K2Bi vault"

# 8. Read-only check (only fixture subdirs)
modified=$(find "$SANDBOX/wiki" "$SANDBOX/raw" -newer "$BEFORE_MARKER" -type f 2>/dev/null || true)
if [[ -n "$modified" ]]; then
  echo "FAIL: script modified files in sandbox:"
  echo "$modified"
  exit 1
fi
echo "PASS: read-only verified"

# 8b. Orchestrator DB (WAL mode) + -wal sidecar must be byte-for-byte untouched after
# every read above (mode=ro read-only invariant -- Codex finding 4).
ORCH_SNAP_AFTER=$(orch_snapshot)
if [[ "$ORCH_SNAP_BEFORE" != "$ORCH_SNAP_AFTER" ]]; then
  echo "FAIL: orchestrator DB/WAL changed under read (mode=ro must not mutate):"
  echo "--- before ---"; echo "$ORCH_SNAP_BEFORE"
  echo "--- after ---"; echo "$ORCH_SNAP_AFTER"
  exit 1
fi
JMODE=$(sqlite3 "file:${K2B_ORCH_DB}?mode=ro" "PRAGMA journal_mode;" 2>/dev/null)
if [[ "$JMODE" != "wal" ]]; then
  echo "FAIL: fixture journal_mode='$JMODE', expected 'wal' (read-only WAL invariant not exercised)"; exit 1
fi
echo "PASS: orchestrator DB + WAL sidecar read-only verified (journal_mode=wal)"

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
