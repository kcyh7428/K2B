#!/bin/bash
# Shared read-only local startup body. Called by the bootstrap renderer and shell fallback.
# Conversation capture discovers Codex JSONL directly; it does not depend on
# PostToolUse, UserPromptSubmit, or Stop lifecycle events.
set -euo pipefail
export LC_ALL=C

REPO_ROOT="${K2B_PROJECT_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
VAULT="${K2B_VAULT_PATH:-$HOME/Projects/K2B-Vault}"
MEMORY_DIR="$VAULT/System/memory"
STATUS_FILE="${K2B_CAPTURE_STATUS_FILE:-$HOME/.local/state/k2b/capture-status.json}"
vault_missing=false
conflict_tmp=""
conflict_pid=""
cleanup() {
  if [ -n "$conflict_pid" ]; then
    kill "$conflict_pid" 2>/dev/null || true
    wait "$conflict_pid" 2>/dev/null || true
  fi
  if [ -n "$conflict_tmp" ]; then
    rm -f "$conflict_tmp"
  fi
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

# Emit bounded completed sections immediately. A later status deadline must
# not discard index/rules that were already read successfully.
emit_section() {
  local label="$1" path="$2" filter="${3:-plain}" content raw_count
  if [ ! -f "$path" ] || [ -L "$path" ]; then
    printf '%s\n\n' "$label unavailable (source is not a regular local file)"
    return
  fi
  if ! raw_count="$(head -c 65537 "$path" | wc -c)"; then
    printf '%s\n\n' "$label unavailable (source read failed)"
    return
  fi
  if [ "$raw_count" -gt 65536 ]; then
    printf '%s\n\n' "$label unavailable (source exceeds 65536 byte bound)"
    return
  fi
  # A sentinel preserves trailing newlines in command substitution. Comparing
  # raw length then detects NUL stripping or a changed file without injecting
  # a silently altered rules section. Both reads remain byte bounded.
  if ! content="$(head -c 65537 "$path" && printf '.')"; then
    printf '%s\n\n' "$label unavailable (source read failed)"
    return
  fi
  content="${content%.}"
  if [ "${#content}" -ne "$raw_count" ]; then
    printf '%s\n\n' "$label unavailable (non-text or changed source)"
    return
  fi
  if ! command -v iconv >/dev/null 2>&1; then
    printf '%s\n\n' "$label unavailable (UTF-8 validator unavailable)"
    return
  fi
  if ! printf '%s' "$content" | iconv -f UTF-8 -t UTF-8 >/dev/null 2>&1; then
    printf '%s\n\n' "$label unavailable (invalid UTF-8 source)"
    return
  fi
  printf '%s\n' "$label"
  if [ "$filter" = rules ]; then
    printf '%s\n' "$content" | awk 'BEGIN { RS=""; ORS="\n\n" } !/^(Last promoted:|Last audited:|\*\*Cap:)/ { print }'
  else
    printf '%s\n\n' "$content"
  fi
}

if [ ! -e "$VAULT" ]; then
  vault_missing=true
  warning="K2B SESSION HOOK WARNING: K2B_VAULT_PATH does not exist: $VAULT. Dotfile memory fallback is active for bootstrap only."
  echo "$warning" >&2
  printf '%s\n\n' "$warning"
elif [ ! -d "$VAULT" ]; then
  printf '%s\n\n' "K2B SESSION HOOK WARNING: K2B_VAULT_PATH is not a directory: $VAULT"
elif [ ! -d "$MEMORY_DIR" ]; then
  warning="K2B SESSION HOOK WARNING: vault memory directory is missing: $MEMORY_DIR. Dotfile memory fallback is suppressed because the vault root exists."
  echo "$warning" >&2
  printf '%s\n\n' "$warning"
fi

wiki_index="$VAULT/wiki/index.md"
if [ -f "$wiki_index" ]; then
  emit_section "K2B KNOWLEDGE INDEX:" "$wiki_index"
fi

active_rules=""
if [ -f "$MEMORY_DIR/active_rules.md" ]; then
  active_rules="$MEMORY_DIR/active_rules.md"
elif $vault_missing; then
  active_rules=$(find -L "$HOME/.codex/memories" -maxdepth 4 -name active_rules.md -type f 2>/dev/null | head -1 || true)
fi
if [ -f "$active_rules" ] 2>/dev/null; then
  # These historical metadata paragraphs are not rules. Preserve every other
  # paragraph, including unnumbered rules and continuations, and never edit the
  # source. In particular, do not inject retired workflows from the audit log.
  emit_section "ACTIVE RULES (current text; audit metadata remains in $active_rules):" "$active_rules" rules
fi

# The python block must never abort the hook: if python3 itself is missing or
# exits non-zero for an uncaught reason, degrade to an explicit unavailable
# summary and keep the rest of the startup output.
status_summary=$(python3 - "$STATUS_FILE" "$REPO_ROOT" <<'PY'
import json
import os
import pwd
import signal
import stat
import sys
from pathlib import Path

status_file = Path(sys.argv[1])
try:
    if status_file.is_file():
        info = status_file.lstat()
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode) or info.st_size > 65536:
            raise ValueError("legacy status is unsafe or oversized")
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
    diagnostic_host = os.environ.get("K2B_BOOTSTRAP_HOST")
    if diagnostic_host:
        # Unknown is deliberately fail-closed, never silently select a
        # different host's job from the OS username during a diagnostic run.
        role = {"home": "home", "sjm": "sjm-source-only"}.get(diagnostic_host, "unknown")
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
printf '%s\n\n' "$status_summary"

# Bounded, read-only Syncthing conflict-copy warning. Only real conflict
# copies produce prose; the check never writes, locks, or contacts a host.
# Enforce a real short execution deadline: kill the check if it stalls.
if [ -d "$VAULT" ]; then
  note_write_script="$REPO_ROOT/scripts/vault-note-write.py"
  if [ -f "$note_write_script" ]; then
    if conflict_tmp="$(mktemp "${TMPDIR:-/tmp}/k2b-conflict.XXXXXX")"; then
    conflict_deadline=$((SECONDS + 5))
    python3 "$note_write_script" conflicts >"$conflict_tmp" 2>/dev/null &
    conflict_pid=$!
    while kill -0 "$conflict_pid" 2>/dev/null && [ "$SECONDS" -lt "$conflict_deadline" ]; do
      sleep 0.1
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
    conflict_pid=""
    rm -f "$conflict_tmp"
    conflict_tmp=""
    if [ -n "$conflict_summary" ]; then
      printf '%s\n\n' "$conflict_summary"
    fi
    else
      printf '%s\n\n' "VAULT CONFLICTS: unknown (temporary file unavailable)"
    fi
  else
    printf '%s\n\n' "VAULT CONFLICTS: unknown (conflict checker missing)"
  fi
fi
