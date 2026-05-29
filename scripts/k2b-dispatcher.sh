#!/usr/bin/env bash
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO"
export PYTHONPATH="$REPO${PYTHONPATH:+:$PYTHONPATH}"
LOCK="/tmp/k2b-dispatcher.lock"
# Single-instance guard. flock is often absent on macOS, so fall back to an
# atomic mkdir lock (matching the Python idiom). Without this, pm2 autorestart
# could run two dispatchers and reintroduce the assignee-lock race.
if command -v flock >/dev/null 2>&1; then
  exec 9>"$LOCK"
  flock -n 9 || { echo "k2b-dispatcher already running; exiting"; exit 0; }
else
  LOCKDIR="${LOCK}.d"
  if ! mkdir "$LOCKDIR" 2>/dev/null; then
    # Stale-lock guard: if the recorded PID is dead, reclaim the dir.
    if [ -f "$LOCKDIR/pid" ] && ! kill -0 "$(cat "$LOCKDIR/pid" 2>/dev/null)" 2>/dev/null; then
      rm -rf "$LOCKDIR"; mkdir "$LOCKDIR" 2>/dev/null || { echo "k2b-dispatcher already running; exiting"; exit 0; }
    else
      echo "k2b-dispatcher already running; exiting"; exit 0
    fi
  fi
  echo "$$" > "$LOCKDIR/pid"
  trap 'rm -rf "$LOCKDIR"' EXIT INT TERM
fi
INTERVAL="${K2B_DISPATCHER_INTERVAL:-60}"
echo "k2b-dispatcher up (interval ${INTERVAL}s, db=$(python3 -m scripts.lib.orchestrator_store print-db 2>/dev/null || echo default))"
while true; do
  python3 -m scripts.lib.orchestrator_store poll-once || echo "poll-once errored (continuing)"
  sleep "$INTERVAL"
done
