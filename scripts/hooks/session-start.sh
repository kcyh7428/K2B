#!/bin/bash
# Minimal K2B SessionStart hook for the mostly-manual Stage 1 architecture.
# Conversation capture discovers Codex JSONL directly; it does not depend on
# PostToolUse, UserPromptSubmit, or Stop lifecycle events.
set -euo pipefail

VAULT="${K2B_VAULT_PATH:-$HOME/Projects/K2B-Vault}"
MEMORY_DIR="$VAULT/System/memory"
STATUS_FILE="${K2B_CAPTURE_STATUS_FILE:-$HOME/.local/state/k2b/capture-status.json}"
vault_missing=false
output=""

if [ ! -e "$VAULT" ]; then
  vault_missing=true
  warning="K2B SESSION HOOK WARNING: K2B_VAULT_PATH does not exist: $VAULT. Dotfile memory fallback is active for bootstrap only."
  echo "$warning" >&2
  output+="$warning"$'\n\n'
elif [ ! -d "$VAULT" ]; then
  output+="K2B SESSION HOOK WARNING: K2B_VAULT_PATH is not a directory: $VAULT"$'\n\n'
elif [ ! -d "$MEMORY_DIR" ]; then
  warning="K2B SESSION HOOK WARNING: vault memory directory is missing: $MEMORY_DIR. Dotfile memory fallback is suppressed because the vault root exists."
  echo "$warning" >&2
  output+="$warning"$'\n\n'
fi

wiki_index="$VAULT/wiki/index.md"
if [ -f "$wiki_index" ]; then
  output+="K2B KNOWLEDGE INDEX:"$'\n'
  output+="$(cat "$wiki_index")"$'\n\n'
fi

active_rules=""
if [ -f "$MEMORY_DIR/active_rules.md" ]; then
  active_rules="$MEMORY_DIR/active_rules.md"
elif $vault_missing; then
  active_rules=$(find -L "$HOME/.codex/memories" -name active_rules.md -type f 2>/dev/null | head -1 || true)
fi
if [ -f "$active_rules" ] 2>/dev/null; then
  output+="ACTIVE RULES (follow these every session):"$'\n'
  output+="$(cat "$active_rules")"$'\n\n'
fi

if [ -f "$STATUS_FILE" ]; then
  status_summary=$(python3 - "$STATUS_FILE" <<'PY'
import json
import sys

try:
    data = json.load(open(sys.argv[1], encoding="utf-8"))
    if not isinstance(data, dict):
        raise TypeError("status must be an object")
    counts = data.get("counts", {})
    if not isinstance(counts, dict):
        raise TypeError("status counts must be an object")
    print(
        "CAPTURE STATUS: "
        f"last discovered={data.get('last_discovered') or 'none'}; "
        f"last reconciled={data.get('last_reconciled') or 'none'}; "
        f"waiting={int(counts.get('waiting', 0))}; "
        f"failed={int(counts.get('failed', 0))}; "
        f"role={data.get('writer_role', 'unknown')}"
    )
except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
    print(f"CAPTURE STATUS: unreadable local status ({type(exc).__name__})")
PY
)
  output+="$status_summary"$'\n\n'
else
  output+="CAPTURE STATUS: disabled or not yet run on this Mac"$'\n\n'
fi

# Bounded, read-only Syncthing conflict-copy warning. Only real conflict
# copies produce prose; the check never writes, locks, or contacts a host.
# Enforce a real short execution deadline: kill the check if it stalls.
if [ -d "$VAULT" ]; then
  note_write_script="${K2B_PROJECT_ROOT:-$HOME/Projects/K2B}/scripts/vault-note-write.py"
  if [ -f "$note_write_script" ]; then
    conflict_tmp="$(mktemp)"
    conflict_deadline=$((SECONDS + 5))
    python3 "$note_write_script" conflicts >"$conflict_tmp" 2>/dev/null &
    conflict_pid=$!
    while kill -0 "$conflict_pid" 2>/dev/null && [ "$SECONDS" -lt "$conflict_deadline" ]; do
      sleep 1
    done
    if kill -0 "$conflict_pid" 2>/dev/null; then
      kill "$conflict_pid" 2>/dev/null || true
      wait "$conflict_pid" 2>/dev/null || true
      conflict_summary="VAULT CONFLICTS: unknown (conflict check timed out)"
    else
      conflict_summary=""
      if ! wait "$conflict_pid"; then
        conflict_summary="VAULT CONFLICTS: unknown (conflict check failed)"
      fi
      if [ -z "$conflict_summary" ]; then
        conflict_summary="$(head -n 12 "$conflict_tmp")"
      fi
    fi
    rm -f "$conflict_tmp"
    if [ -n "$conflict_summary" ]; then
      output+="$conflict_summary"$'\n\n'
    fi
  fi
fi

if [ -n "$output" ]; then
  echo "$output"
fi
