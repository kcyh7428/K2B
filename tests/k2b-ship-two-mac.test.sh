#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SKILL="$ROOT/.agents/skills/k2b-ship/SKILL.md"

grep -q 'OpenAI-built changes require Kimi' "$SKILL" \
  || { echo "FAIL: ship must preserve the independent-review matrix" >&2; exit 1; }
grep -qi 'activation on each Mac is a separate state' "$SKILL" \
  || { echo "FAIL: ship must distinguish Git delivery from activation" >&2; exit 1; }
grep -q 'preserve an uncommitted checkpoint' "$SKILL" \
  || { echo "FAIL: ship must preserve the no-delivery-authority checkpoint" >&2; exit 1; }

if rg -n 'deploy-to-mini|ssh macmini|pending-sync|pm2 restart k2b-remote|Auto-sync to Mac Mini' "$SKILL" >/dev/null; then
  echo "FAIL: ship still contains retired Mini deployment behavior" >&2
  exit 1
fi

echo "k2b-ship-two-mac: PASS"
