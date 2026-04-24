#!/usr/bin/env bash
# K2B integrated-loop routing entry point.
# Applies --accept/--reject/--defer N actions under flock.
# Env defaults cover day-to-day invocation; tests override. Ship 2 adds
# defer counter + review-item routing; the extra env vars default to
# vault-local paths so no overrides are needed in production.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

VAULT_DEFAULT="$HOME/Projects/K2B-Vault"
MEM_DEFAULT="$HOME/.claude/projects/-Users-keithmbpm2-Projects-K2B/memory"

export K2B_LOOP_CANDIDATES="${K2B_LOOP_CANDIDATES:-$VAULT_DEFAULT/wiki/context/observer-candidates.md}"
export K2B_LOOP_LEARNINGS="${K2B_LOOP_LEARNINGS:-$MEM_DEFAULT/self_improve_learnings.md}"
export K2B_LOOP_ARCHIVE_DIR="${K2B_LOOP_ARCHIVE_DIR:-$VAULT_DEFAULT/wiki/context/observations.archive}"
export K2B_LOOP_DEFERS="${K2B_LOOP_DEFERS:-$VAULT_DEFAULT/wiki/context/observer-defers.jsonl}"
export K2B_LOOP_REVIEW_DIR="${K2B_LOOP_REVIEW_DIR:-$VAULT_DEFAULT/review}"
export K2B_LOOP_REVIEW_READY_DIR="${K2B_LOOP_REVIEW_READY_DIR:-$VAULT_DEFAULT/review/Ready}"
export K2B_LOOP_REVIEW_ARCHIVE_ROOT="${K2B_LOOP_REVIEW_ARCHIVE_ROOT:-$VAULT_DEFAULT/Archive/review-archive}"
export K2B_LOOP_DATE="${K2B_LOOP_DATE:-$(date '+%Y-%m-%d')}"
export K2B_LOOP_ACTOR="${K2B_LOOP_ACTOR:-keith}"
export K2B_LOOP_OBSERVER_RUN="${K2B_LOOP_OBSERVER_RUN:-unknown}"

LOCK="${K2B_LOOP_LOCK:-/tmp/k2b-loop-apply.lock}"

run_apply() {
  python3 "$SCRIPT_DIR/loop_apply.py" "$@"
}

if command -v flock >/dev/null 2>&1; then
  exec 9>"$LOCK"
  flock -x 9
  run_apply "$@"
else
  LOCK_DIR="${LOCK}.d"
  TRIES=0
  while ! mkdir "$LOCK_DIR" 2>/dev/null; do
    TRIES=$((TRIES + 1))
    if [ "$TRIES" -gt 200 ]; then
      echo "loop-apply: could not acquire $LOCK_DIR after 10s" >&2
      exit 4
    fi
    sleep 0.05
  done
  trap 'rmdir "$LOCK_DIR" 2>/dev/null || true' EXIT
  run_apply "$@"
fi
