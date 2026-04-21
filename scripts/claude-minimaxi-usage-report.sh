#!/usr/bin/env bash
# claude-minimaxi-usage-report -- emit a weekly usage summary from minimax-jobs.jsonl.
# Run on a schedule (see k2b-scheduler) and pipe to Telegram, or run on-demand
# to get a quick snapshot of whether the parallel-brain dispatch is earning its keep.

set -euo pipefail

LOG="${K2B_VAULT:-$HOME/Projects/K2B-Vault}/wiki/context/minimax-jobs.jsonl"

if [[ ! -f "$LOG" ]]; then
  echo "claude-minimaxi report: no log file at $LOG yet"
  exit 0
fi

# 7-day window in UTC ISO. macOS and Linux `date` have different flags.
if date -u -v-7d +%Y-%m-%dT00:00:00Z >/dev/null 2>&1; then
  SINCE=$(date -u -v-7d +%Y-%m-%dT00:00:00Z)      # macOS / BSD
else
  SINCE=$(date -u -d '7 days ago' +%Y-%m-%dT00:00:00Z)  # GNU / Linux
fi

# Fetch weekly slice once, reuse.
WEEKLY=$(jq -s --arg since "$SINCE" \
  '[.[] | select(.job=="claude-minimaxi-session" and .ts >= $since)]' "$LOG")

COUNT=$(printf '%s' "$WEEKLY" | jq 'length')
OK=$(printf '%s' "$WEEKLY" | jq '[.[] | select(.parse_status=="ok")] | length')
ERR=$((COUNT - OK))

if (( COUNT == 0 )); then
  SUCCESS_PCT=0
  AVG_S=0
  MAX_S=0
else
  SUCCESS_PCT=$((OK * 100 / COUNT))
  AVG_MS=$(printf '%s' "$WEEKLY" | jq '([.[] | .duration_ms] | add/length) | floor')
  MAX_MS=$(printf '%s' "$WEEKLY" | jq '[.[] | .duration_ms] | max')
  AVG_S=$((AVG_MS / 1000))
  MAX_S=$((MAX_MS / 1000))
fi

ALL_TIME=$(jq -s '[.[] | select(.job=="claude-minimaxi-session")] | length' "$LOG")

# Threshold verdict per wiki/context/context_claude-minimaxi-routing.md
if (( COUNT >= 5 )) && (( SUCCESS_PCT >= 85 )); then
  VERDICT="ON TRACK (>=5/week AND >=85% success)"
elif (( COUNT < 5 )); then
  VERDICT="LOW VOLUME -- Opus not reaching for claude-minimaxi (under 5 dispatches)"
else
  VERDICT="QUALITY ISSUES -- success rate $SUCCESS_PCT% under 85%"
fi

cat <<EOF
claude-minimaxi weekly report -- $(date -u +%Y-%m-%d)

Past 7 days:
  Dispatches:    $COUNT
  Success rate:  ${SUCCESS_PCT}% ($OK ok, $ERR errors)
  Avg duration:  ${AVG_S}s (max ${MAX_S}s)

All-time total: $ALL_TIME dispatches

Verdict: $VERDICT

Rubric: wiki/context/context_claude-minimaxi-routing.md
EOF
