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
INSERT INTO tasks VALUES('T-KIMI','F8','dispatch','KIMITEST','k2bi','waiting_for_kimi_output','k2bi-smoke-enrich-kimi',NULL,NULL,'${ORCH_C5}','${ORCH_C5}',NULL);
INSERT INTO tasks VALUES('T-HUMAN','F9','dispatch','HUMANTEST','k2bi','needs_human','k2bi-smoke-enrich-human',NULL,NULL,'${ORCH_C5}','${ORCH_C5}',NULL);
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
for sym in NVDA k2bi-smoke-enrich-lrcx AMD MSFT PARSETEST KIMITEST HUMANTEST; do
  if ! grep -q "$sym" "$SANDBOX/active-only.md"; then
    echo "FAIL: active-only missing $sym"; cat "$SANDBOX/active-only.md"; exit 1
  fi
done
if grep -qE "TSLA|INTC" "$SANDBOX/active-only.md"; then
  echo "FAIL: active-only must exclude terminal tasks"; cat "$SANDBOX/active-only.md"; exit 1
fi
echo "PASS: /portfolio active dispatch returns the active flights, terminal excluded"

# New state labels for Ship 1b
if ! echo "$active_section" | grep -q "KIMITEST · waiting_for_kimi_output · waiting on your Kimi run"; then
  echo "FAIL: waiting_for_kimi_output row missing or wrong"; echo "$active_section"; exit 1
fi
echo "PASS: waiting_for_kimi_output label correct"

if ! echo "$active_section" | grep -q "⚠ HUMANTEST · needs_human · needs your input"; then
  echo "FAIL: needs_human row missing or wrong"; echo "$active_section"; exit 1
fi
echo "PASS: needs_human label correct"

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

# 8c. Read-after-write: resilience + freshness + read-only (live bug 2026-05-30).
# mode=ro fails transiently right after a write while the WAL index tears down;
# section_active retries mode=ro until it settles. Make a VISIBLE state change
# (cancel AMD), then assert /portfolio active (a) is not "unreachable", (b) reflects
# the post-write state -- the cancelled row is GONE, proving a fresh read not a stale
# one, and (c) did not itself mutate the DB. Runs AFTER 8b so the deliberate write
# does not trip that snapshot.
sqlite3 "$K2B_ORCH_DB" "UPDATE tasks SET status='cancelled' WHERE id='T-RDY';" 2>/dev/null  # AMD ready -> cancelled
RAW_SNAP_BEFORE=$(orch_snapshot)
K2BI_VAULT_PATH="$SANDBOX" bash "$PORTFOLIO_SH" active > "$SANDBOX/active-after-write.md"
RAW_SNAP_AFTER=$(orch_snapshot)
if grep -q "orchestrator board unreachable" "$SANDBOX/active-after-write.md"; then
  echo "FAIL: /portfolio active was unreachable right after a DB write (mode=ro retry insufficient?)"; cat "$SANDBOX/active-after-write.md"; exit 1
fi
if grep -q "AMD" "$SANDBOX/active-after-write.md"; then
  echo "FAIL: read after write is stale -- AMD was cancelled but still shows as an active flight"; cat "$SANDBOX/active-after-write.md"; exit 1
fi
if ! grep -q "NVDA" "$SANDBOX/active-after-write.md"; then
  echo "FAIL: active-after-write should still render the remaining flights"; cat "$SANDBOX/active-after-write.md"; exit 1
fi
if [[ "$RAW_SNAP_BEFORE" != "$RAW_SNAP_AFTER" ]]; then
  echo "FAIL: the post-write /portfolio read mutated the DB:"; echo "--- before ---"; echo "$RAW_SNAP_BEFORE"; echo "--- after ---"; echo "$RAW_SNAP_AFTER"; exit 1
fi
echo "PASS: /portfolio active reads fresh post-write state and stays read-only"

# 8d. AT-REST WAL DB regression (live bug 2026-05-31): a WAL-mode DB whose -wal/-shm
# sidecars are gone (last writer checkpointed + exited, or Syncthing dropped them)
# cannot be opened `mode=ro` -- a read-only open can't create the -shm a WAL header
# demands, so it fails CANTOPEN(14) permanently and the retry loop never clears it.
# The -wal-gated `immutable=1` fallback (rung 2) must read it correctly instead of
# printing "unreachable". The main fixture above keeps its -wal, so mode=ro works there
# and rung 2 was never exercised before the bug shipped.
#
# Whether a real at-rest WAL DB trips CANTOPEN is sqlite/build/filesystem dependent
# (non-deterministic across machines -- production reliably fails, a fresh CLI fixture
# often does not). To pin the test on rung 2 DETERMINISTICALLY everywhere, we shim
# `sqlite3` on PATH: the shim returns CANTOPEN(14) for exactly this fixture's `mode=ro`
# URI and delegates every other call (including the `immutable=1` fallback) to the real
# sqlite3. So the script's rung-1 always fails here and rung-2 is the only way to read --
# deleting the immutable fallback makes this case print "unreachable" and FAIL the test.
REST_DB="$SANDBOX/orchestrator-atrest.sqlite"
sqlite3 "$REST_DB" <<SQL
PRAGMA journal_mode=WAL;
CREATE TABLE tasks (
  id TEXT PRIMARY KEY, flight_id TEXT, stage_name TEXT, entity_key TEXT,
  assignee_profile TEXT, status TEXT, command_key TEXT, blocker_reason TEXT,
  blocked_by TEXT, created_at TEXT, updated_at TEXT, heartbeat_at TEXT
);
INSERT INTO tasks VALUES('R-RUN','F1','dispatch','COIN','k2bi','running','k2bi-smoke-enrich-coin',NULL,NULL,'${ORCH_C10}','${ORCH_HB2}','${ORCH_HB2}');
INSERT INTO tasks VALUES('R-DONE','F2','dispatch','PLTR','k2bi','done','k2bi-smoke-enrich-pltr',NULL,NULL,'${ORCH_C1H}','${ORCH_C1H}',NULL);
PRAGMA wal_checkpoint(TRUNCATE);
SQL
# At-rest shape: no -wal/-shm sidecars (checkpoint above flushed every row into the main
# DB, so immutable=1 reads a complete, consistent snapshot).
rm -f "$REST_DB-wal" "$REST_DB-shm"
# Build the PATH shim: CANTOPEN for this fixture's mode=ro, real sqlite3 otherwise.
REAL_SQLITE3="$(command -v sqlite3)"
SHIM_DIR="$SANDBOX/shimbin"
mkdir -p "$SHIM_DIR"
cat > "$SHIM_DIR/sqlite3" <<SHIM
#!/usr/bin/env bash
for a in "\$@"; do
  case "\$a" in
    *orchestrator-atrest*mode=ro*) echo "Error: in prepare, unable to open database file (14)" >&2; exit 1 ;;
  esac
done
exec "$REAL_SQLITE3" "\$@"
SHIM
chmod +x "$SHIM_DIR/sqlite3"
# Sanity: the shim must fail mode=ro and pass immutable for THIS fixture.
if PATH="$SHIM_DIR:$PATH" sqlite3 "file:${REST_DB}?mode=ro" "SELECT 1;" >/dev/null 2>&1; then
  echo "FAIL: 8d shim did not force mode=ro CANTOPEN"; exit 1
fi
if ! PATH="$SHIM_DIR:$PATH" sqlite3 "file:${REST_DB}?immutable=1" "SELECT 1;" >/dev/null 2>&1; then
  echo "FAIL: 8d shim broke the immutable=1 delegate path"; exit 1
fi
PATH="$SHIM_DIR:$PATH" K2B_ORCH_DB="$REST_DB" K2BI_VAULT_PATH="$SANDBOX" bash "$PORTFOLIO_SH" active > "$SANDBOX/active-atrest.md"
if grep -q "orchestrator board unreachable" "$SANDBOX/active-atrest.md"; then
  echo "FAIL: at-rest WAL DB reported unreachable (rung-2 immutable fallback missing/broken)"; cat "$SANDBOX/active-atrest.md"; exit 1
fi
if ! grep -q "COIN · running" "$SANDBOX/active-atrest.md"; then
  echo "FAIL: at-rest read missing the running COIN flight"; cat "$SANDBOX/active-atrest.md"; exit 1
fi
if grep -q "PLTR" "$SANDBOX/active-atrest.md"; then
  echo "FAIL: at-rest read should exclude terminal PLTR"; cat "$SANDBOX/active-atrest.md"; exit 1
fi
# The immutable fallback must not recreate the -wal/-shm sidecars (read-only invariant).
if [[ -f "$REST_DB-wal" || -f "$REST_DB-shm" ]]; then
  echo "FAIL: rung-2 immutable read recreated a -wal/-shm sidecar (not read-only)"; exit 1
fi
echo "PASS: at-rest WAL DB read via deterministic rung-2 immutable fallback (no unreachable, fresh, read-only)"

# 8e. POST-READ WRITER-DETECTED, REREAD-FAILS regression: if a writer appears DURING the
# immutable=1 fallback (a -wal materializes mid-read), the immutable snapshot is suspect.
# The script must re-read via mode=ro; if that reread cannot be obtained, it must report
# "unreachable" rather than render the known-suspect immutable rows. We simulate the
# writer-appears-mid-read deterministically with a shim: its immutable=1 handler touches
# the -wal (so the script's post-read `-f -wal` check fires) then delegates the read;
# its mode=ro handler always CANTOPENs (so both rung-1 and the post-read reread fail).
REST_DB2="$SANDBOX/orchestrator-atrest2.sqlite"
sqlite3 "$REST_DB2" <<SQL
PRAGMA journal_mode=WAL;
CREATE TABLE tasks (
  id TEXT PRIMARY KEY, flight_id TEXT, stage_name TEXT, entity_key TEXT,
  assignee_profile TEXT, status TEXT, command_key TEXT, blocker_reason TEXT,
  blocked_by TEXT, created_at TEXT, updated_at TEXT, heartbeat_at TEXT
);
INSERT INTO tasks VALUES('R-RUN','F1','dispatch','COIN','k2bi','running','k2bi-smoke-enrich-coin',NULL,NULL,'${ORCH_C10}','${ORCH_HB2}','${ORCH_HB2}');
PRAGMA wal_checkpoint(TRUNCATE);
SQL
rm -f "$REST_DB2-wal" "$REST_DB2-shm"
SHIM_DIR2="$SANDBOX/shimbin2"
mkdir -p "$SHIM_DIR2"
cat > "$SHIM_DIR2/sqlite3" <<SHIM
#!/usr/bin/env bash
for a in "\$@"; do
  case "\$a" in
    *orchestrator-atrest2*mode=ro*) echo "Error: in prepare, unable to open database file (14)" >&2; exit 1 ;;
    *orchestrator-atrest2*immutable=1*) touch "${REST_DB2}-wal" ;;  # writer "appears" mid-read
  esac
done
exec "$REAL_SQLITE3" "\$@"
SHIM
chmod +x "$SHIM_DIR2/sqlite3"
PATH="$SHIM_DIR2:$PATH" K2B_ORCH_DB="$REST_DB2" K2BI_VAULT_PATH="$SANDBOX" bash "$PORTFOLIO_SH" active > "$SANDBOX/active-reread-fail.md"
if ! grep -q "orchestrator board unreachable" "$SANDBOX/active-reread-fail.md"; then
  echo "FAIL: writer-detected + failed mode=ro reread must report unreachable, not render suspect immutable rows"; cat "$SANDBOX/active-reread-fail.md"; exit 1
fi
if grep -q "COIN" "$SANDBOX/active-reread-fail.md"; then
  echo "FAIL: suspect immutable rows leaked after writer detected + reread failed"; cat "$SANDBOX/active-reread-fail.md"; exit 1
fi
rm -f "$REST_DB2-wal" "$REST_DB2-shm"
echo "PASS: writer-detected-mid-read + failed reread -> unreachable (no suspect rows rendered)"

# 8f. TRANSIENT-WRITER regression: a writer that appears AND disappears during the
# immutable=1 read (creates a -wal, checkpoints the main file, removes the -wal again)
# leaves NO -wal at the post-read check, so the -wal-presence check alone would miss it
# and render a suspect immutable snapshot. The main-file fingerprint (mtime/size/inode)
# must catch the mid-read mutation and force the authoritative mode=ro reread (which here
# CANTOPENs) -> "unreachable", not suspect rows. The shim simulates the transient writer
# by stamping the main DB's mtime to a fixed past value during the immutable call WITHOUT
# leaving a -wal, so the post-read -f -wal check is false but the fingerprint differs.
REST_DB3="$SANDBOX/orchestrator-atrest3.sqlite"
sqlite3 "$REST_DB3" <<SQL
PRAGMA journal_mode=WAL;
CREATE TABLE tasks (
  id TEXT PRIMARY KEY, flight_id TEXT, stage_name TEXT, entity_key TEXT,
  assignee_profile TEXT, status TEXT, command_key TEXT, blocker_reason TEXT,
  blocked_by TEXT, created_at TEXT, updated_at TEXT, heartbeat_at TEXT
);
INSERT INTO tasks VALUES('R-RUN','F1','dispatch','COIN','k2bi','running','k2bi-smoke-enrich-coin',NULL,NULL,'${ORCH_C10}','${ORCH_HB2}','${ORCH_HB2}');
PRAGMA wal_checkpoint(TRUNCATE);
SQL
rm -f "$REST_DB3-wal" "$REST_DB3-shm"
SHIM_DIR3="$SANDBOX/shimbin3"
mkdir -p "$SHIM_DIR3"
cat > "$SHIM_DIR3/sqlite3" <<SHIM
#!/usr/bin/env bash
for a in "\$@"; do
  case "\$a" in
    *orchestrator-atrest3*mode=ro*) echo "Error: in prepare, unable to open database file (14)" >&2; exit 1 ;;
    *orchestrator-atrest3*immutable=1*) touch -t 202001010000 "${REST_DB3}" ;;  # transient writer: mutate main file, no -wal left behind
  esac
done
exec "$REAL_SQLITE3" "\$@"
SHIM
chmod +x "$SHIM_DIR3/sqlite3"
PATH="$SHIM_DIR3:$PATH" K2B_ORCH_DB="$REST_DB3" K2BI_VAULT_PATH="$SANDBOX" bash "$PORTFOLIO_SH" active > "$SANDBOX/active-transient.md"
if [[ -f "$REST_DB3-wal" ]]; then
  echo "FAIL: 8f setup invalid -- a -wal was left behind, so this would just be the 8e path"; exit 1
fi
if ! grep -q "orchestrator board unreachable" "$SANDBOX/active-transient.md"; then
  echo "FAIL: transient mid-read writer (fingerprint changed, no -wal) must report unreachable, not render suspect rows"; cat "$SANDBOX/active-transient.md"; exit 1
fi
if grep -q "COIN" "$SANDBOX/active-transient.md"; then
  echo "FAIL: suspect immutable rows leaked after a fingerprint-detected mid-read mutation"; cat "$SANDBOX/active-transient.md"; exit 1
fi
echo "PASS: transient mid-read writer caught by main-file fingerprint -> unreachable (no suspect rows)"

# 8g. SAME-SECOND IN-PLACE mutation: a transient writer can update existing pages and
# checkpoint within a single whole second WITHOUT changing file size or inode. A
# whole-second mtime fingerprint would see identical before/after and render suspect
# rows; the nanosecond st_mtime_ns/st_ctime_ns fingerprint must still catch it. The shim
# `touch`es the main file (no -t, so mtime=now -- same whole second as the pre-read
# fingerprint in this sub-millisecond test, but a different nanosecond; size+inode
# unchanged; no -wal left behind). Asserts the guard still forces the reread -> unreachable.
REST_DB4="$SANDBOX/orchestrator-atrest4.sqlite"
sqlite3 "$REST_DB4" <<SQL
PRAGMA journal_mode=WAL;
CREATE TABLE tasks (
  id TEXT PRIMARY KEY, flight_id TEXT, stage_name TEXT, entity_key TEXT,
  assignee_profile TEXT, status TEXT, command_key TEXT, blocker_reason TEXT,
  blocked_by TEXT, created_at TEXT, updated_at TEXT, heartbeat_at TEXT
);
INSERT INTO tasks VALUES('R-RUN','F1','dispatch','COIN','k2bi','running','k2bi-smoke-enrich-coin',NULL,NULL,'${ORCH_C10}','${ORCH_HB2}','${ORCH_HB2}');
PRAGMA wal_checkpoint(TRUNCATE);
SQL
rm -f "$REST_DB4-wal" "$REST_DB4-shm"
SHIM_DIR4="$SANDBOX/shimbin4"
mkdir -p "$SHIM_DIR4"
cat > "$SHIM_DIR4/sqlite3" <<SHIM
#!/usr/bin/env bash
for a in "\$@"; do
  case "\$a" in
    *orchestrator-atrest4*mode=ro*) echo "Error: in prepare, unable to open database file (14)" >&2; exit 1 ;;
    *orchestrator-atrest4*immutable=1*) touch "${REST_DB4}" ;;  # in-place mtime bump, same size/inode, no -wal
  esac
done
exec "$REAL_SQLITE3" "\$@"
SHIM
chmod +x "$SHIM_DIR4/sqlite3"
ssz_before=$(stat -f '%z-%i' "$REST_DB4" 2>/dev/null || stat -c '%s-%i' "$REST_DB4" 2>/dev/null)
PATH="$SHIM_DIR4:$PATH" K2B_ORCH_DB="$REST_DB4" K2BI_VAULT_PATH="$SANDBOX" bash "$PORTFOLIO_SH" active > "$SANDBOX/active-samesec.md"
ssz_after=$(stat -f '%z-%i' "$REST_DB4" 2>/dev/null || stat -c '%s-%i' "$REST_DB4" 2>/dev/null)
if [[ "$ssz_before" != "$ssz_after" ]]; then
  echo "FAIL: 8g setup invalid -- size/inode changed ($ssz_before -> $ssz_after), not a same-size in-place case"; exit 1
fi
if [[ -f "$REST_DB4-wal" ]]; then
  echo "FAIL: 8g setup invalid -- a -wal was left behind (would collapse into the 8e path)"; exit 1
fi
if ! grep -q "orchestrator board unreachable" "$SANDBOX/active-samesec.md"; then
  echo "FAIL: same-size in-place mid-read mutation must report unreachable (nanosecond fingerprint regressed to whole-second?)"; cat "$SANDBOX/active-samesec.md"; exit 1
fi
if grep -q "COIN" "$SANDBOX/active-samesec.md"; then
  echo "FAIL: suspect rows leaked after a same-size in-place mid-read mutation"; cat "$SANDBOX/active-samesec.md"; exit 1
fi
echo "PASS: same-size in-place mid-read mutation caught by nanosecond fingerprint -> unreachable"

# 8h. PYTHON3-UNAVAILABLE degraded path: orch_fp() is python3-only with no whole-second
# stat fallback, so if python3 fails the fingerprint is empty and MUST be treated as
# suspect -- never trusting the unlocked immutable rows. We mask python3 with a stub that
# exits non-zero and also CANTOPEN mode=ro, then drive the same-size in-place case. The
# script must NOT render rows: empty fingerprint -> suspect -> mode=ro reread -> CANTOPEN
# -> unreachable. (python3 is a hard dep of this section anyway; this guards the degraded
# path against silently reopening the same-second race Codex flagged.)
REST_DB5="$SANDBOX/orchestrator-atrest5.sqlite"
sqlite3 "$REST_DB5" <<SQL
PRAGMA journal_mode=WAL;
CREATE TABLE tasks (
  id TEXT PRIMARY KEY, flight_id TEXT, stage_name TEXT, entity_key TEXT,
  assignee_profile TEXT, status TEXT, command_key TEXT, blocker_reason TEXT,
  blocked_by TEXT, created_at TEXT, updated_at TEXT, heartbeat_at TEXT
);
INSERT INTO tasks VALUES('R-RUN','F1','dispatch','COIN','k2bi','running','k2bi-smoke-enrich-coin',NULL,NULL,'${ORCH_C10}','${ORCH_HB2}','${ORCH_HB2}');
PRAGMA wal_checkpoint(TRUNCATE);
SQL
rm -f "$REST_DB5-wal" "$REST_DB5-shm"
SHIM_DIR5="$SANDBOX/shimbin5"
mkdir -p "$SHIM_DIR5"
cat > "$SHIM_DIR5/sqlite3" <<SHIM
#!/usr/bin/env bash
for a in "\$@"; do
  case "\$a" in
    *orchestrator-atrest5*mode=ro*) echo "Error: in prepare, unable to open database file (14)" >&2; exit 1 ;;
    *orchestrator-atrest5*immutable=1*) touch "${REST_DB5}" ;;
  esac
done
exec "$REAL_SQLITE3" "\$@"
SHIM
chmod +x "$SHIM_DIR5/sqlite3"
# python3 stub that always fails -> orch_fp emits empty -> suspect path must fire.
cat > "$SHIM_DIR5/python3" <<'PYSTUB'
#!/usr/bin/env bash
exit 127
PYSTUB
chmod +x "$SHIM_DIR5/python3"
PATH="$SHIM_DIR5:$PATH" K2B_ORCH_DB="$REST_DB5" K2BI_VAULT_PATH="$SANDBOX" bash "$PORTFOLIO_SH" active > "$SANDBOX/active-nopy.md" 2>/dev/null
if ! grep -q "orchestrator board unreachable" "$SANDBOX/active-nopy.md"; then
  echo "FAIL: with python3 unavailable, empty fingerprint must be treated as suspect -> unreachable (low-res trust regressed?)"; cat "$SANDBOX/active-nopy.md"; exit 1
fi
if grep -q "COIN" "$SANDBOX/active-nopy.md"; then
  echo "FAIL: suspect rows leaked on the python3-unavailable degraded path"; cat "$SANDBOX/active-nopy.md"; exit 1
fi
echo "PASS: python3-unavailable -> empty fingerprint treated as suspect -> unreachable (no low-res trust)"

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
