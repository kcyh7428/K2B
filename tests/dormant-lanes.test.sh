#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"

for skill in k2b-observer k2b-weave; do
  file="$ROOT/.agents/skills/$skill/SKILL.md"
  rg -qi 'dormant|disabled' "$file" || {
    echo "$skill must be explicitly dormant" >&2
    exit 1
  }
  if rg -n 'Mac Mini|Stop hook|pm2|three times a week|scheduled cron|runs automatically|Operations Console' "$file"; then
    echo "$skill still promises retired background behavior" >&2
    exit 1
  fi
done

if rg -n 'session-start hook|fire automated|automated actions' "$ROOT/.agents/skills/k2b-usage-tracker/SKILL.md"; then
  echo "usage tracker still promises automatic triggers" >&2
  exit 1
fi

echo "dormant lane contract: PASS"
