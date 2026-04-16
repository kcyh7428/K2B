#!/usr/bin/env bash
# scripts/observer-mark-processed.sh
# Single writer of signal-processed lines in preference-signals.jsonl.
# Usage: observer-mark-processed.sh <signal_id> <action> [learn_id]
#   signal_id:  8-hex content hash from the signal being acted on
#   action:     confirmed | rejected | watching
#   learn_id:   optional L-ID when the action produced a learning
#
# Locking pattern mirrors Fix #1 (scripts/wiki-log-append.sh): flock -x if
# available, mkdir fallback for macOS. Reference pattern only, NOT the same
# lockfile.
#
# CROSS-MACHINE LOCK LIMITATION:
#   observer-loop.sh runs on Mac Mini (pm2) and this helper runs on the Mac.
#   Both write to the Syncthing-synced jsonl. Filesystem locks DO NOT cross
#   machines. Cross-machine conflicts manifest as Syncthing *.sync-conflict*
#   files. Accepted risk per Fix #6 design; not mitigated here.

set -euo pipefail

SIG="${1:?observer-mark-processed: signal_id required}"
ACTION="${2:?observer-mark-processed: action required}"
LEARN="${3:-}"

if ! printf '%s' "$SIG" | grep -qE '^[0-9a-f]{8}$'; then
  echo "observer-mark-processed: signal_id must be 8 hex chars, got: $SIG" >&2
  exit 2
fi

case "$ACTION" in
  confirmed|rejected|watching) ;;
  *) echo "observer-mark-processed: action must be one of confirmed|rejected|watching" >&2; exit 2 ;;
esac

if [ -n "$LEARN" ] && ! printf '%s' "$LEARN" | grep -qE '^L-[0-9]{4}-[0-9]{2}-[0-9]{2}-[0-9]{3}$'; then
  echo "observer-mark-processed: learn_id must match L-YYYY-MM-DD-NNN, got: $LEARN" >&2
  exit 2
fi

JSONL="${K2B_PREFERENCE_SIGNALS:-$HOME/Projects/K2B-Vault/wiki/context/preference-signals.jsonl}"
LOCK="${K2B_PREFERENCE_SIGNALS_LOCK:-/tmp/k2b-preference-signals.lock}"
TS="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"

if [ ! -f "$JSONL" ]; then
  echo "observer-mark-processed: jsonl not found: $JSONL" >&2
  exit 3
fi

if [ -n "$LEARN" ]; then
  LINE=$(printf '{"type":"signal-processed","signal_id":"%s","at":"%s","by":"session-start-inline","action":"%s","learn_id":"%s"}' \
    "$SIG" "$TS" "$ACTION" "$LEARN")
else
  LINE=$(printf '{"type":"signal-processed","signal_id":"%s","at":"%s","by":"session-start-inline","action":"%s"}' \
    "$SIG" "$TS" "$ACTION")
fi

if command -v flock >/dev/null 2>&1; then
  exec 9>"$LOCK"
  flock -x 9
  printf '%s\n' "$LINE" >> "$JSONL"
else
  LOCK_DIR="${LOCK}.d"
  TRIES=0
  while ! mkdir "$LOCK_DIR" 2>/dev/null; do
    TRIES=$((TRIES + 1))
    if [ "$TRIES" -gt 200 ]; then
      echo "observer-mark-processed: could not acquire $LOCK_DIR after 10s" >&2
      exit 4
    fi
    sleep 0.05
  done
  trap 'rmdir "$LOCK_DIR" 2>/dev/null || true' EXIT
  printf '%s\n' "$LINE" >> "$JSONL"
fi

echo "observer-mark-processed: marked $SIG as $ACTION"
