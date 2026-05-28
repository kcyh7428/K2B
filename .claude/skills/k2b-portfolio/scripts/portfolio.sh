#!/usr/bin/env bash
# /portfolio -- read K2Bi ticker pipeline state across all stages
# Read-only aggregator over K2Bi vault paths.

set -uo pipefail

K2BI="${K2BI_VAULT_PATH:-$HOME/Projects/K2Bi-Vault}"

# --- Stale-data guard ---
if [[ ! -d "$K2BI" ]] || [[ ! -d "$K2BI/wiki" ]]; then
  echo "K2Bi vault unreachable at ${K2BI}" >&2
  exit 1
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

SECTION="${1:-all}"

case "$SECTION" in
  all)
    section_awaiting
    section_watchlist
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
    echo "Unknown section: ${SECTION}. Known: awaiting watchlist theses strategies positions closed" >&2
    exit 2
    ;;
esac
