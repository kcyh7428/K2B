#!/usr/bin/env bash
# Retired leaf optimizer wrapper stub.
#
# Leaf Optimizer was retired on 2026-06-22 after the house network moved to
# R5C-owned Mihomo/private-VPN operation and the DoggyGo selector assumptions
# no longer applied. This wrapper remains only as a forensic audit stub; it
# never invokes the old optimizer logic, never loads Mihomo credentials, and
# has no runtime bypass to reactivate selector mutation.
set -euo pipefail

LOG_DIR="${K2B_ROUTER_WATCHDOG_LOG_DIR:-$HOME/Library/Logs/k2b-router-watchdog}"

write_retired_audit() {
  local audit_path max_bytes current_bytes
  audit_path="${K2B_ROUTER_RETIRED_AUDIT_LOG:-$LOG_DIR/retired-mutations.jsonl}"
  max_bytes="${K2B_ROUTER_RETIRED_AUDIT_MAX_BYTES:-1048576}"
  if ! mkdir -p "$(dirname "$audit_path")"; then
    echo "WARNING: failed to create retired mutation audit log directory: $(dirname "$audit_path")" >&2
    return 1
  fi
  if [[ -f "$audit_path" && "$max_bytes" =~ ^[0-9]+$ && "$max_bytes" -gt 0 ]]; then
    current_bytes="$(wc -c < "$audit_path" | tr -d '[:space:]')"
    if [[ "$current_bytes" -ge "$max_bytes" ]]; then
      if ! mv -f "$audit_path" "$audit_path.1"; then
        echo "WARNING: failed to rotate retired mutation audit log: $audit_path" >&2
        return 1
      fi
    fi
  fi
  if ! printf '{"timestamp":"%s","script":"optimize-leaves.sh","action":"retired_noop"}\n' \
    "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" >> "$audit_path"; then
    echo "WARNING: failed to write retired mutation audit log: $audit_path" >&2
    return 1
  fi
}

echo "optimize-leaves.sh is retired and performed no mutation for the current R5C/private-VPN topology." >&2
write_retired_audit || exit 2
exit 0
