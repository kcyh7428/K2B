#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SKILL="$ROOT/.agents/skills/k2b-sync/SKILL.md"

grep -q 'two authorized Macs' "$SKILL" \
  || { echo "FAIL: k2b-sync must describe the two-Mac scope" >&2; exit 1; }
grep -q 'Code moves through Git' "$SKILL" \
  || { echo "FAIL: k2b-sync must route code through Git" >&2; exit 1; }
grep -q 'Vault moves through Syncthing' "$SKILL" \
  || { echo "FAIL: k2b-sync must route vault data through Syncthing" >&2; exit 1; }
grep -q 'never overwrite them with a blind pull' "$SKILL" \
  || { echo "FAIL: k2b-sync must preserve per-host adaptations" >&2; exit 1; }

if rg -n 'deploy-to-mini|ssh macmini|rsync .*macmini|pm2 restart k2b-remote|pending-sync' "$SKILL" >/dev/null; then
  echo "FAIL: k2b-sync still contains retired direct-deployment behavior" >&2
  exit 1
fi

echo "k2b-sync-two-mac: PASS"
