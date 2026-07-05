#!/usr/bin/env bash
# Reap orphaned background processes that K2B sessions tend to leak.
# Safe by construction: only kills (a) named MCP helpers that are stateless,
# (b) orphaned (ppid=1) processes older than the threshold below.
#
# Wired in K2B/.claude/settings.json on the Stop hook so it runs at the end
# of every Claude Code session in this project.

set -u

THRESHOLD_MIN=15         # ppid=1 processes younger than this are left alone
# Machine-independent log path; the old -Users-keithmbpm2- slug created a bogus
# nested dir on the Mini. Override with K2B_ZOMBIE_LOG if needed.
LOG="${K2B_ZOMBIE_LOG:-$HOME/.claude/cleanup-zombies.log}"

mkdir -p "$(dirname "$LOG")"
exec >>"$LOG" 2>&1
echo "--- $(date '+%Y-%m-%d %H:%M:%S') cleanup-zombies start ---"

# 1. Stateless MCP helpers: never need to outlive a session.
killed_mcp=$(pkill -f "minimax-mcp-js" 2>&1 && echo killed || echo none)
echo "minimax-mcp-js sweep: ${killed_mcp}"

# 2. Orphaned (ppid=1) leak-prone processes older than THRESHOLD_MIN minutes.
#    etime format: [[dd-]hh:]mm:ss -- collapse to total minutes for comparison.
ps -eo pid,ppid,etime,command | awk -v threshold="$THRESHOLD_MIN" '
  $2 != 1 { next }
  /pytest|chrome_crashpad_handler.*Visual Studio Code|claude-code\/2\.1\.[0-9]+/ {
    et = $3
    days = 0; hours = 0; mins = 0; secs = 0
    n = split(et, parts, "[-:]")
    if (n == 4)      { days = parts[1]; hours = parts[2]; mins = parts[3]; secs = parts[4] }
    else if (n == 3) { hours = parts[1]; mins = parts[2]; secs = parts[3] }
    else if (n == 2) { mins = parts[1]; secs = parts[2] }
    total_min = days*1440 + hours*60 + mins
    if (total_min >= threshold) print $1, total_min, $4
  }
' | while read -r pid age cmd; do
  echo "killing orphan pid=${pid} age_min=${age} cmd=${cmd}"
  kill "${pid}" 2>/dev/null || kill -9 "${pid}" 2>/dev/null
done

echo "--- done ---"
