#!/usr/bin/env bash
# scripts/audit-ownership.sh
# Reads scripts/ownership-watchlist.yml and flags any known rule phrase that appears
# outside its canonical home.
#
# Supports two rule entry styles:
#   phrase:       literal fixed-string (grep -rFln)
#   phrase_regex: extended regex       (grep -rEln)
#
# Exit codes:
#   0 - no drift
#   1 - drift detected (the caller decides whether to block)
#   2 - usage / config error
#
# Usage: scripts/audit-ownership.sh

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
WATCHLIST="$REPO_ROOT/scripts/ownership-watchlist.yml"
VAULT="$HOME/Projects/K2B-Vault"

[ -f "$WATCHLIST" ] || { echo "audit-ownership: watchlist not found: $WATCHLIST" >&2; exit 2; }

# Search scope: K2B repo + vault memory + vault wiki/.
SEARCH_PATHS=(
  "$REPO_ROOT/CLAUDE.md"
  "$REPO_ROOT/.claude/skills"
  "$REPO_ROOT/scripts"
  "$VAULT/System/memory"
  "$VAULT/wiki"
)

EXISTING_PATHS=()
for p in "${SEARCH_PATHS[@]}"; do
  [ -e "$p" ] && EXISTING_PATHS+=("$p")
done

# Run the audit in python for YAML parsing + regex/fixed dispatch.
REPO_ROOT="$REPO_ROOT" VAULT="$VAULT" WATCHLIST="$WATCHLIST" \
  python3 - "${EXISTING_PATHS[@]}" <<'PY'
import json, os, subprocess, sys, yaml

watchlist = os.environ["WATCHLIST"]
repo_root = os.environ["REPO_ROOT"]
paths = sys.argv[1:]

with open(watchlist) as f:
    data = yaml.safe_load(f)
rules = data.get("rules", [])

def canonicalize(h):
    h = os.path.expanduser(h)
    if not os.path.isabs(h):
        h = os.path.join(repo_root, h)
    return os.path.abspath(h)

drift_rules = 0
total_offenders = 0

for rule in rules:
    rid = rule["id"]
    home = [canonicalize(h) for h in rule.get("canonical_home", [])]

    if "phrase" in rule:
        needle = rule["phrase"]
        cmd = ["grep", "-rIFln", "--", needle, *paths]
        display = needle
    elif "phrase_regex" in rule:
        needle = rule["phrase_regex"]
        cmd = ["grep", "-rIEln", "--", needle, *paths]
        display = f"/{needle}/"
    else:
        print(f"audit-ownership: rule {rid} missing phrase/phrase_regex", file=sys.stderr)
        sys.exit(2)

    try:
        out = subprocess.run(cmd, capture_output=True, text=True, check=False)
    except FileNotFoundError:
        print("audit-ownership: grep not available", file=sys.stderr)
        sys.exit(2)

    matches = [l for l in out.stdout.splitlines() if l]

    offenders = []
    for m in matches:
        m_abs = os.path.abspath(m)
        if not any(
            m_abs == h or m_abs.startswith(h + os.sep)
            for h in home
        ):
            offenders.append(m_abs)

    if offenders:
        drift_rules += 1
        total_offenders += len(offenders)
        print(f"drift: rule={rid} phrase={display!r}")
        for o in sorted(offenders):
            print(f"   offender: {o}")

if drift_rules:
    print(f"audit-ownership: {drift_rules} rule(s) with drift, {total_offenders} offender file(s)")
    sys.exit(1)

print("audit-ownership: no drift")
sys.exit(0)
PY
