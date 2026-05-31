#!/usr/bin/env bash
# /portfolio -- read K2Bi ticker pipeline state across all stages
# Read-only aggregator over K2Bi vault paths.

set -uo pipefail

K2BI="${K2BI_VAULT_PATH:-$HOME/Projects/K2Bi-Vault}"
K2B="${K2B_VAULT_PATH:-$HOME/Projects/K2B-Vault}"
ORCH_DB="${K2B_ORCH_DB:-$K2B/System/orchestrator/orchestrator.sqlite}"

# Parse the section up-front so the K2Bi reachability guard can be scoped.
SECTION="${1:-all}"

# --- Stale-data guard (K2Bi-backed sections only) ---
# The `active` section reads ONLY the orchestrator SQLite (K2B vault), never K2Bi.
# A K2Bi vault sync/mount issue must not hide active orchestrator flights, so the
# guard is skipped for `active`. Every other section reads K2Bi and requires it.
if [[ "$SECTION" != "active" ]]; then
  if [[ ! -d "$K2BI" ]] || [[ ! -d "$K2BI/wiki" ]]; then
    echo "K2Bi vault unreachable at ${K2BI}" >&2
    exit 1
  fi
fi

# --- Helpers ---

# Skip syncthing conflict files
skip_conflict() {
  [[ "$(basename "$1")" == *.sync-conflict-* ]]
}

# Extract single-line YAML scalar from frontmatter only
fm_get_scalar() {
  local file="$1" key="$2"
  awk -v key="$key" '
    /^---$/ { fm++; if (fm==2) exit; next }
    fm==1 && index($0, key ":") == 1 {
      val = substr($0, length(key) + 2)
      sub(/^[[:space:]]+/, "", val)
      if (substr(val, 1, 1) == "|") next
      gsub(/^"|"$/, "", val)
      print val
      exit
    }
  ' "$file" 2>/dev/null
}

# Age of file in days
file_age_days() {
  local file="$1"
  local mtime now
  mtime=$(stat -f %m "$file" 2>/dev/null || stat -c %Y "$file" 2>/dev/null)
  now=$(date +%s)
  echo $(( (now - mtime) / 86400 ))
}

# Human-readable age
fmt_age() {
  local d="$1"
  if [[ "$d" == "0" || "$d" == "" ]]; then
    echo "today"
  else
    echo "${d}d"
  fi
}

# Timestamp date part (first 10 chars)
ts_date() {
  local ts="$1"
  echo "${ts:0:10}"
}

# Age of ISO timestamp in days
ts_age_days() {
  local ts="$1"
  local ts_epoch now
  # Strip timezone offset for macOS date parsing
  local ts_clean="${ts:0:19}"
  ts_epoch=$(date -j -f "%Y-%m-%dT%H:%M:%S" "$ts_clean" +%s 2>/dev/null || date -d "$ts_clean" +%s 2>/dev/null)
  now=$(date +%s)
  echo $(( (now - ts_epoch) / 86400 ))
}

# Human-readable age from ISO timestamp (s/m/h/d). Used for orchestrator flights
# where sub-day granularity matters (heartbeats), unlike the day-only vault ages.
# MUST be timezone-offset-aware: the orchestrator stores UTC timestamps WITH a
# +00:00 offset (datetime.now(timezone.utc).isoformat()). Stripping the offset and
# parsing as local wall-clock makes a 2-minute-old heartbeat read ~8h old on a
# UTC+8 machine -- which corrupts exactly the freshness signal this section exists
# to show. Python's fromisoformat() honors the offset; naive timestamps (no offset,
# as produced by `date -u` in test fixtures) are treated as UTC.
ts_age_human() {
  local ts="$1"
  [[ -n "$ts" ]] || { echo "?"; return; }
  # Python stderr is intentionally NOT suppressed: a malformed timestamp is a handled
  # soft case (prints '?' on stdout, exits 0, no stderr), but a real failure (python3
  # missing, stripped-PATH cron, unexpected datetime error) must surface to stderr so a
  # blanket '?' is diagnosable rather than silent. The `|| echo "?"` keeps output graceful.
  python3 - "$ts" <<'PY' || echo "?"
import sys, datetime
raw = sys.argv[1]
try:
    dt = datetime.datetime.fromisoformat(raw)
except ValueError:
    print("?"); sys.exit(0)
if dt.tzinfo is None:
    dt = dt.replace(tzinfo=datetime.timezone.utc)
diff = int((datetime.datetime.now(datetime.timezone.utc) - dt).total_seconds())
if diff < 0:
    diff = 0
if diff < 60:
    print(f"{diff}s")
elif diff < 3600:
    print(f"{diff // 60}m")
elif diff < 86400:
    print(f"{diff // 3600}h")
else:
    print(f"{diff // 86400}d")
PY
}

# Date +1 day
next_day() {
  local d="$1"
  date -j -v+1d -f "%Y-%m-%d" "$d" +%Y-%m-%d 2>/dev/null || date -d "$d +1 day" +%Y-%m-%d 2>/dev/null
}

# Extract ticker from strategy frontmatter (handles top-level or nested under order:)
strategy_ticker() {
  local file="$1"
  awk '
    /^---$/ { fm++; if (fm==2) exit; next }
    fm==1 && index($0, "ticker:") > 0 {
      pos = index($0, "ticker:")
      val = substr($0, pos + 7)
      sub(/^[[:space:]]+/, "", val)
      gsub(/^"|"$/, "", val)
      print val
      exit
    }
  ' "$file" 2>/dev/null
}

# Check if a retro insight file exists for a given date (same day or +1 day) AND mentions the ticker
find_retro() {
  local date_str="$1"
  local ticker="$2"
  local insights_dir="$K2BI/wiki/insights"
  local f
  for f in "$insights_dir/${date_str}"*retro*.md; do
    [[ -f "$f" ]] || continue
    skip_conflict "$f" && continue
    if grep -qi "$ticker" "$f" 2>/dev/null; then
      echo "found"
      return
    fi
  done
  local nd
  nd=$(next_day "$date_str")
  for f in "$insights_dir/${nd}"*retro*.md; do
    [[ -f "$f" ]] || continue
    skip_conflict "$f" && continue
    if grep -qi "$ticker" "$f" 2>/dev/null; then
      echo "found"
      return
    fi
  done
  echo "not_found"
}

# --- Section functions ---

section_awaiting() {
  echo "## ⚠ Awaiting promotion"
  echo ""
  local has_any=0
  local cutoff
  cutoff=$(date -v-30d +%Y-%m-%d 2>/dev/null || date -d '30 days ago' +%Y-%m-%d 2>/dev/null)

  for tf in "$K2BI/wiki/macro-themes/theme_"*.md; do
    [[ -f "$tf" ]] || continue
    skip_conflict "$tf" && continue
    local theme_age
    theme_age=$(file_age_days "$tf")
    # Only themes newer than 30 days (heuristic per spec)
    local theme_date
    theme_date=$(stat -f %Sm -t %Y-%m-%d "$tf" 2>/dev/null || stat -c %y "$tf" 2>/dev/null | cut -d' ' -f1)
    if [[ -n "$cutoff" && "$theme_date" < "$cutoff" ]]; then
      continue
    fi

    local slug
    slug=$(basename "$tf" .md | sed 's/^theme_//')
    local candidates
    candidates=$(awk '
      index($0, "| ") == 1 {
        rest = substr($0, 3)
        pos = index(rest, " |")
        if (pos > 0) {
          sym = substr(rest, 1, pos - 1)
          gsub(/^[[:space:]]+|[[:space:]]+$/, "", sym)
          if (sym ~ /^[A-Z][A-Z0-9]*$/ && length(sym) >= 1 && length(sym) <= 5) {
            if (sym != "Symbol" && sym != "Order" && sym != "Citation") {
              print sym
            }
          }
        }
      }
    ' "$tf" | sort -u)

    local unpromoted=""
    while IFS= read -r sym; do
      [[ -n "$sym" ]] || continue
      if [[ ! -f "$K2BI/wiki/watchlist/${sym}.md" ]]; then
        unpromoted="${unpromoted}${sym} "
      fi
    done <<< "$candidates"

    if [[ -n "$unpromoted" ]]; then
      has_any=1
      echo "- ⚠ ${slug} · waiting_for_promotion · pick ticker · $(fmt_age "$theme_age")"
    fi
  done

  if [[ $has_any -eq 0 ]]; then
    echo "(none)"
  fi
  echo ""
}

section_watchlist() {
  echo "## 📋 Watchlist (Stage 1-2)"
  echo ""
  local has_any=0
  for wf in "$K2BI/wiki/watchlist/"*.md; do
    [[ -f "$wf" ]] || continue
    skip_conflict "$wf" && continue
    local sym status
    sym=$(basename "$wf" .md)
    status=$(fm_get_scalar "$wf" "status")
    [[ "$status" == "promoted" || "$status" == "screened" ]] || continue
    has_any=1
    local age
    age=$(file_age_days "$wf")
    local action="auto"
    if [[ "$status" == "promoted" ]]; then
      # Check if Quick Score band exists (ark_6_metric_initial_scores present)
      if grep -q 'ark_6_metric_initial_scores:' "$wf" 2>/dev/null; then
        action="auto"
      else
        action="⚠ enrich"
      fi
    fi
    echo "- ${sym} · ${status} · ${action} · $(fmt_age "$age")"
  done
  if [[ $has_any -eq 0 ]]; then
    echo "(none)"
  fi
  echo ""
}

# High-resolution fingerprint of a file: mtime_ns, ctime_ns, size, inode. Brackets the
# unlocked immutable read so any mid-read mutation of the orchestrator DB is detected.
# Nanosecond mtime+ctime catch same-second, same-size, in-place page updates that a
# whole-second `stat` mtime would miss (st_ctime_ns changes on ANY write to the inode).
#
# Deliberately python3-only with NO whole-second `stat` fallback: a low-resolution
# fingerprint cannot prove the file was unchanged within the same second, so trusting it
# would silently reopen the same-second stale-read race in the degraded path. python3 is
# already a hard dependency of this section (ts_age_human), so its absence is not a real
# scenario; if it ever fails here we emit NOTHING, and the caller treats an empty
# fingerprint as "suspect" -- forcing the authoritative mode=ro reread rather than
# trusting the unlocked immutable rows.
orch_fp() {
  python3 -c 'import os,sys
try:
    s=os.stat(sys.argv[1]); print(f"{s.st_mtime_ns}-{s.st_ctime_ns}-{s.st_size}-{s.st_ino}")
except OSError:
    sys.exit(1)' "$1" 2>/dev/null
}

section_active() {
  echo "## 🛫 Active orchestrator flights"
  echo ""
  # Stale-data honesty: a missing DB means the orchestrator has never run (no flights);
  # a present-but-unreadable DB means we report unreachable rather than show stale data.
  if [[ ! -f "$ORCH_DB" ]]; then
    echo "(none)"
    echo ""
    return
  fi
  # The free-text blocker_reason is sanitized in SQL -- the unit-separator (0x1f)
  # field delimiter, CR, and LF are all replaced with spaces -- so an embedded
  # delimiter or newline cannot shift the remaining fields when bash `read` splits
  # the row. blocked_by is selected so a `ready` task waiting on another assignee's
  # lock is not mislabeled as freely queued.
  local query="SELECT id, coalesce(entity_key,''), coalesce(command_key,''), status, coalesce(stage_name,''), replace(replace(replace(coalesce(blocker_reason,''), char(31), ' '), char(13), ' '), char(10), ' '), coalesce(blocked_by,''), coalesce(created_at,''), coalesce(heartbeat_at,'') FROM tasks WHERE status NOT IN ('done','failed','cancelled') ORDER BY created_at;"
  local rows rc i
  # WAL-mode read strategy (strictly read-only -- never mutates orchestrator state):
  #
  #   1. mode=ro takes a shared lock and never checkpoints. It works while a writer is
  #      active (the -shm already exists then) and once a just-exited writer's -shm is
  #      rebuilt -- so we retry a few times to ride out that brief transient.
  #   2. If mode=ro still fails AND there is no -wal sidecar, the DB is AT REST: the last
  #      writer checkpointed and removed -wal/-shm. A read-only open then cannot create
  #      the -shm a WAL DB needs, so it fails CANTOPEN(14) *permanently* -- retrying never
  #      helps (this was the false "unreachable" seen 2026-05-31 on an idle board). In this
  #      no-writer state immutable=1 is provably SAFE: with no -wal there is no active
  #      writer and no checkpoint in progress, so the main DB file is a static, fully
  #      committed snapshot. We fall back to immutable=1 ONLY here.
  #   3. If mode=ro fails WHILE a -wal exists, a writer is active but we could not build
  #      the -shm. We do NOT drop to an unlocked immutable read (it would ignore the WAL
  #      and return a stale snapshot). We report unreachable and let the caller retry.
  local rows2 rc2 fp_before fp_after
  rc=1
  for i in 1 2 3 4 5; do
    rows=$(sqlite3 -separator $'\x1f' "file:${ORCH_DB}?mode=ro" "$query" 2>/dev/null)
    rc=$?
    [[ $rc -eq 0 ]] && break
    sleep 0.2
  done
  if [[ $rc -ne 0 && ! -f "${ORCH_DB}-wal" ]]; then
    # DB at rest, no active writer -> immutable read is safe and consistent. immutable=1
    # is unlocked, so we bracket it with a fingerprint of the main DB file (mtime, size,
    # inode -- BSD `stat -f`, GNU `stat -c` fallback) to detect ANY writer that touches
    # the DB during the read. That catches not just a writer that leaves a -wal behind,
    # but also a transient one that creates a -wal, checkpoints the main file, and removes
    # the sidecar again before the post-read check -- such a writer still changes the main
    # file's mtime/size, so the fingerprint differs even though no -wal remains.
    fp_before=$(orch_fp "$ORCH_DB")
    rows=$(sqlite3 -separator $'\x1f' "file:${ORCH_DB}?immutable=1" "$query" 2>/dev/null)
    rc=$?
    fp_after=$(orch_fp "$ORCH_DB")
    # Suspect if a writer left ANY evidence during the unlocked read: a -wal or -shm now
    # exists, OR the main-file fingerprint changed. In that case the immutable snapshot
    # may be stale/torn, so we must NOT render it. The mode=ro reread is authoritative
    # (the writer's -shm now exists, so it should succeed); retry it briefly. Its result
    # REPLACES the immutable rows; if it cannot be obtained, rc goes nonzero and the
    # caller reports "unreachable" rather than showing suspect data. (A write landing
    # AFTER this reread is outside our point-in-time read -- correct, not stale.)
    if [[ $rc -eq 0 && ( -f "${ORCH_DB}-wal" || -f "${ORCH_DB}-shm" || "$fp_before" != "$fp_after" || -z "$fp_before" ) ]]; then
      rc2=1
      for i in 1 2 3; do
        rows2=$(sqlite3 -separator $'\x1f' "file:${ORCH_DB}?mode=ro" "$query" 2>/dev/null)
        rc2=$?
        [[ $rc2 -eq 0 ]] && break
        sleep 0.2
      done
      rows="$rows2"
      rc=$rc2
    fi
  fi
  if [[ $rc -ne 0 ]]; then
    echo "⚠ orchestrator board unreachable (retry shortly)"
    echo ""
    return
  fi
  if [[ -z "$rows" ]]; then
    echo "(none)"
    echo ""
    return
  fi
  local id ticker cmd status stage reason blocked_by created hb
  while IFS=$'\x1f' read -r id ticker cmd status stage reason blocked_by created hb; do
    [[ -n "$id" ]] || continue
    local label="$ticker"
    [[ -n "$label" ]] || label="${cmd:-$id}"
    local prefix="" action="" age=""
    case "$status" in
      blocked)
        prefix="⚠ "
        local short="$reason"
        if [[ -n "$short" && ${#short} -gt 64 ]]; then short="${short:0:64}…"; fi
        [[ -n "$short" ]] || short="see board"
        action="unblock: ${short}"
        age=$(ts_age_human "$created")
        ;;
      zombie)
        prefix="⚠ "
        action="needs reclaim"
        age=$(ts_age_human "${hb:-$created}")
        ;;
      running)
        action="auto · running"
        age=$(ts_age_human "${hb:-$created}")
        ;;
      ready)
        if [[ -n "$blocked_by" ]]; then
          action="auto · waiting on ${blocked_by}"
        else
          action="auto · queued"
        fi
        age=$(ts_age_human "$created")
        ;;
      needs_human)
        prefix="⚠ "
        action="needs your input"
        age=$(ts_age_human "$created")
        ;;
      waiting_for_kimi_output)
        action="waiting on your Kimi run"
        age=$(ts_age_human "${hb:-$created}")
        ;;
      returned)
        action="returned -- K2B processing"
        age=$(ts_age_human "$created")
        ;;
      *)
        action="$status"
        age=$(ts_age_human "$created")
        ;;
    esac
    local stage_suffix=""
    if [[ -n "$stage" && "$stage" != "dispatch" ]]; then stage_suffix=" (${stage})"; fi
    echo "- ${prefix}${label}${stage_suffix} · ${status} · ${action} · ${age}"
  done <<< "$rows"
  echo ""
}

section_theses() {
  echo "## 📝 Theses drafted, awaiting review"
  echo ""
  local has_any=0
  for tf in "$K2BI/wiki/tickers/"*.md; do
    [[ -f "$tf" ]] || continue
    skip_conflict "$tf" && continue
    local sym approved
    sym=$(basename "$tf" .md)
    approved=$(fm_get_scalar "$tf" "thesis_approved_at")
    # Only process if thesis_approved_at is null/absent/empty
    if [[ -n "$approved" && "$approved" != "null" ]]; then
      continue
    fi
    # Check for plain ## Thesis or # Thesis heading in body
    local has_thesis=0
    awk '
      BEGIN { in_fm=0; body=0 }
      /^---$/ { in_fm++; if (in_fm==2) { body=1; next } }
      body==1 && (/^## Thesis$/ || /^# Thesis$/) { print "yes"; exit }
    ' "$tf" 2>/dev/null | grep -q "yes" && has_thesis=1
    if [[ $has_thesis -eq 0 ]]; then
      continue
    fi
    has_any=1
    local age
    age=$(file_age_days "$tf")
    echo "- ⚠ ${sym} · thesis_drafted · review thesis · $(fmt_age "$age")"
  done
  if [[ $has_any -eq 0 ]]; then
    echo "(none)"
  fi
  echo ""
}

section_strategies() {
  echo "## 📊 Strategies proposed, awaiting ship"
  echo ""
  local has_any=0
  for sf in "$K2BI/wiki/strategies/strategy_"*.md; do
    [[ -f "$sf" ]] || continue
    skip_conflict "$sf" && continue
    local status approved
    status=$(fm_get_scalar "$sf" "status")
    approved=$(fm_get_scalar "$sf" "approved_at")
    [[ "$status" == "proposed" ]] || continue
    [[ -z "$approved" || "$approved" == "null" ]] || continue
    has_any=1
    local slug age
    slug=$(basename "$sf" .md | sed 's/^strategy_//')
    age=$(file_age_days "$sf")
    echo "- ⚠ ${slug} · proposed · ship via /invest-ship · $(fmt_age "$age")"
  done
  if [[ $has_any -eq 0 ]]; then
    echo "(none)"
  fi
  echo ""
}

section_positions() {
  echo "## 💼 Live strategies + open positions"
  echo ""
  local has_any=0
  local journal_dir="$K2BI/raw/journal"
  for sf in "$K2BI/wiki/strategies/strategy_"*.md; do
    [[ -f "$sf" ]] || continue
    skip_conflict "$sf" && continue
    local status approved
    status=$(fm_get_scalar "$sf" "status")
    approved=$(fm_get_scalar "$sf" "approved_at")
    [[ "$status" == "approved" ]] || continue
    [[ -n "$approved" && "$approved" != "null" ]] || continue

    local ticker
    ticker=$(strategy_ticker "$sf")
    [[ -n "$ticker" ]] || continue

    # Query journal for latest buy/sell
    local latest_buy="" latest_sell=""
    if [[ -d "$journal_dir" ]]; then
      local lines
      lines=$(grep -h "\"ticker\":\"${ticker}\"" "$journal_dir"/*.jsonl 2>/dev/null | grep -v 'sync-conflict' | grep '"event_type":"order_filled"' | jq -r '[.ts,.side] | @tsv' 2>/dev/null | sort)
      while IFS=$'\t' read -r ts side; do
        [[ -n "$ts" ]] || continue
        if [[ "$side" == "buy" ]]; then
          latest_buy="$ts"
        elif [[ "$side" == "sell" ]]; then
          latest_sell="$ts"
        fi
      done <<< "$lines"
    fi

    if [[ -n "$latest_buy" ]]; then
      if [[ -z "$latest_sell" || "$latest_buy" > "$latest_sell" ]]; then
        has_any=1
        local age
        age=$(ts_age_days "$latest_buy")
        echo "- ${ticker} · position_open · auto (engine holding) · $(fmt_age "$age")"
      fi
    else
      has_any=1
      local age
      age=$(file_age_days "$sf")
      echo "- ${ticker} · approved_no_fill_yet · auto · $(fmt_age "$age")"
    fi
  done
  if [[ $has_any -eq 0 ]]; then
    echo "(none)"
  fi
  echo ""
}

section_closed() {
  echo "## ✅ Recently closed (last 14 days)"
  echo ""
  local has_any=0
  local journal_dir="$K2BI/raw/journal"
  local cutoff
  cutoff=$(date -v-14d +%Y-%m-%d 2>/dev/null || date -d '14 days ago' +%Y-%m-%d 2>/dev/null)

  if [[ -d "$journal_dir" ]]; then
    local lines
    lines=$(grep -h '"event_type":"order_filled"' "$journal_dir"/*.jsonl 2>/dev/null | grep -v 'sync-conflict' | jq -r 'select(.side=="sell") | [.ts,.ticker,.payload.realized_pnl] | @tsv' 2>/dev/null)
    while IFS=$'\t' read -r ts ticker pnl; do
      [[ -n "$ts" ]] || continue
      local ts_date
      ts_date=$(ts_date "$ts")
      if [[ -n "$cutoff" && "$ts_date" < "$cutoff" ]]; then
        continue
      fi
      has_any=1
      local age
      age=$(ts_age_days "$ts")
      local pnl_str="N/A"
      [[ -n "$pnl" && "$pnl" != "null" ]] && pnl_str="\$${pnl}"
      local retro
      retro=$(find_retro "$ts_date" "$ticker")
      if [[ "$retro" == "found" ]]; then
        echo "- ${ticker} · closed P&L ${pnl_str} · review retro · $(fmt_age "$age")"
      else
        echo "- ⚠ ${ticker} · closed P&L ${pnl_str} · retro pending · $(fmt_age "$age")"
      fi
    done <<< "$lines"
  fi

  if [[ $has_any -eq 0 ]]; then
    echo "(none)"
  fi
  echo ""
}

# --- Dispatch ---
# SECTION was parsed at the top of the script (before the scoped K2Bi guard).

case "$SECTION" in
  all)
    section_awaiting
    section_watchlist
    section_active
    section_theses
    section_strategies
    section_positions
    section_closed
    ;;
  awaiting)
    section_awaiting
    ;;
  watchlist)
    section_watchlist
    ;;
  active)
    section_active
    ;;
  theses)
    section_theses
    ;;
  strategies)
    section_strategies
    ;;
  positions)
    section_positions
    ;;
  closed)
    section_closed
    ;;
  *)
    echo "Unknown section: ${SECTION}. Known: awaiting watchlist active theses strategies positions closed" >&2
    exit 2
    ;;
esac
