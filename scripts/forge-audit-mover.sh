#!/usr/bin/env bash
# Move Codex Forge audit reports from /private/tmp/ to K2B-Vault/review/.
#
# Triggered by launchd WatchPaths on /private/tmp/ (any directory-content
# change fires the agent; this script then filters for forge-audit_*.md).
# Codex Automations' sandbox can write to /private/tmp/ but not to the
# vault, so this script bridges the gap.
#
# See K2B-Vault/wiki/context/context_codex-forge-audit.md for the full
# wiring rationale, and the launchd plist at
# scripts/launchd/com.k2b.forge-audit-mover.plist for the trigger config.
set -euo pipefail
shopt -s nullglob

VAULT_REVIEW="${K2B_VAULT_PATH:-$HOME/Projects/K2B-Vault}/review"
LOG_DIR="$HOME/Library/Logs"
LOG="$LOG_DIR/k2b-forge-audit-mover.log"
LOCK_DIR="/tmp/.k2b-forge-audit-mover.lock"

mkdir -p "$VAULT_REVIEW" "$LOG_DIR"

now_iso() { date -u +%Y-%m-%dT%H:%M:%SZ; }
log() { printf '%s %s\n' "$(now_iso)" "$1" >> "$LOG"; }

# Single-instance lock. WatchPaths can re-fire while a previous instance is
# still stabilising a file (stability loop runs up to 30s; ThrottleInterval
# only gates launchd's spawn cadence, not the script's wall-clock). Without
# this lock two runs could pass stability on the same src and race the mv.
if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  log "INFO another instance holds $LOCK_DIR, exiting"
  exit 0
fi
trap 'rmdir "$LOCK_DIR" 2>/dev/null || true' EXIT

for src in /private/tmp/forge-audit_*.md; do
  # File-stability guard: wait up to 30s for BOTH size and mtime to stop
  # changing for 3 consecutive 1-second checks. WatchPaths fires the moment
  # Codex opens the file for writing; moving mid-flush would give the
  # consumer a truncated report. Checking both size and mtime catches
  # bursty writers (flush + pause + flush) that would fool a size-only
  # check during the pause window.
  prev_sig=""
  stable_for=0
  stabilized=0
  for _ in $(seq 1 30); do
    cur_sig=$(stat -f '%z %m' "$src" 2>/dev/null) || { log "WARN stat failed during poll: $src"; break; }
    if [[ "$cur_sig" == "$prev_sig" ]]; then
      stable_for=$((stable_for + 1))
      if (( stable_for >= 3 )); then
        stabilized=1
        break
      fi
    else
      stable_for=0
      prev_sig=$cur_sig
    fi
    sleep 1
  done
  if (( stabilized == 0 )); then
    log "WARN file did not stabilize within 30s, leaving: $src"
    continue
  fi

  base=$(basename "$src")
  dest="$VAULT_REVIEW/$base"

  # Collision-safe dest: if the canonical filename already exists in the
  # vault, append a UNIX epoch suffix so we never silently leave the source
  # orphaned in /private/tmp/. Most likely cause of collision is Codex
  # re-running the same calendar day (ad-hoc test + scheduled run).
  if [[ -e "$dest" ]]; then
    dest="$VAULT_REVIEW/${base%.md}_$(date +%s).md"
    log "INFO destination existed, using suffixed name: $dest"
  fi

  # Capture mv stderr in a variable so it cannot interleave with concurrent
  # log() lines. Single-instance lock above makes concurrency rare, but a
  # single run with multiple files still benefits from atomic log lines.
  if mv_err=$(mv -n "$src" "$dest" 2>&1); then
    log "MOVED $src -> $dest"
  else
    log "FAILED mv $src -> $dest: ${mv_err//$'\n'/ }"
  fi
done
