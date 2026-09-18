#!/bin/bash
# Minimal K2B SessionStart hook for the mostly-manual Stage 1 architecture.
# Conversation capture discovers Codex JSONL directly; it does not depend on
# PostToolUse, UserPromptSubmit, or Stop lifecycle events.
set -euo pipefail

REPO_ROOT="${K2B_PROJECT_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
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
  output+="ACTIVE RULES (current text; audit metadata remains in $active_rules):"$'\n'
  # These historical metadata paragraphs are not rules. Preserve every other
  # paragraph, including unnumbered rules and continuations, and never edit the
  # source. In particular, do not inject retired workflows from the audit log.
  output+="$(awk 'BEGIN { RS=""; ORS="\n\n" } !/^(Last promoted:|Last audited:|\*\*Cap:)/ { print }' "$active_rules")"$'\n\n'
fi

# The python block must never abort the hook: if python3 itself is missing or
# exits non-zero for an uncaught reason, degrade to an explicit unavailable
# summary and keep the rest of the startup output.
status_summary=$(python3 - "$STATUS_FILE" "$REPO_ROOT" <<'PY'
import json
import os
import pwd
import signal
import sys
from pathlib import Path

status_file = Path(sys.argv[1])
try:
    if status_file.is_file():
        data = json.loads(status_file.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise TypeError("status must be an object")
        counts = data.get("counts", {})
        if not isinstance(counts, dict):
            raise TypeError("status counts must be an object")
        print(
            "CAPTURE INVENTORY (legacy; not native extraction coverage): "
            f"last discovered={data.get('last_discovered') or 'none'}; "
            f"last reconciled={data.get('last_reconciled') or 'none'}; "
            f"waiting={int(counts.get('waiting', 0))}; "
            f"failed={int(counts.get('failed', 0))}; "
            f"role={data.get('writer_role', 'unknown')}"
        )
    else:
        print("CAPTURE INVENTORY: no local legacy inventory; native job state is separate")
except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
    print(f"CAPTURE INVENTORY: unreadable local status ({type(exc).__name__})")

# Reuse the native status reader; registration is not processing success or
# coverage. Do not import the extractor or contact another host at startup.
def timed_out(_signum, _frame):
    raise TimeoutError("native status deadline")

try:
    signal.signal(signal.SIGALRM, timed_out)
    # Bound import and read together within one short deadline so a stalled
    # import cannot consume the whole SessionStart timeout, but keep the two
    # phases distinguishable: a slow import is a reader problem, not unknown
    # job evidence.
    signal.alarm(3)
    phase = "import"
    sys.path.insert(0, str(Path(sys.argv[2]) / "scripts" / "lib"))
    from native_job_status import read_native_job_status

    phase = "read"
    role = {"keithmbpm2": "home", "keithcheung": "sjm-source-only"}.get(
        pwd.getpwuid(os.getuid()).pw_name
    )
    state_root = Path(os.environ.get(
        "K2B_AUTOMATIC_MEMORY_STATE_ROOT",
        str(Path.home() / ".local/state/k2b/automatic-memory"),
    ))
    native = read_native_job_status(writer_role=role, state_root=state_root)
    print(
        "AUTOMATIC MEMORY (local evidence; coverage and synchronization unverified): "
        f"registration={native['registration_state']}; "
        f"last success={native['last_completed_at'] or 'unknown'}; "
        f"outcome={native['last_run_outcome']}; "
        f"extraction hold={native['extraction_hold']}; role={role}"
    )
except TimeoutError:
    if phase == "import":
        print("AUTOMATIC MEMORY: status reader slow (import deadline)")
    else:
        print("AUTOMATIC MEMORY: unknown local evidence (TimeoutError)")
except Exception as exc:
    print(f"AUTOMATIC MEMORY: unknown local evidence ({type(exc).__name__})")
finally:
    signal.alarm(0)
PY
) || status_summary="CAPTURE INVENTORY: unavailable (status summary failed)
AUTOMATIC MEMORY: unknown local evidence (status summary failed)"
if [ -z "$status_summary" ]; then
  status_summary="CAPTURE INVENTORY: unavailable (empty status summary)
AUTOMATIC MEMORY: unknown local evidence (empty status summary)"
fi
output+="$status_summary"$'\n\n'

# Bounded, read-only Syncthing conflict-copy warning. Only real conflict
# copies produce prose; the check never writes, locks, or contacts a host.
# Enforce a real short execution deadline: kill the check if it stalls.
if [ -d "$VAULT" ]; then
  note_write_script="$REPO_ROOT/scripts/vault-note-write.py"
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
