#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
targets=(
  "$ROOT/.agents/skills/k2b-media-generator/SKILL.md"
  "$ROOT/scripts/yt-transcribe-whisper.sh"
  "$ROOT/scripts/gptsapi-image.sh"
  "$ROOT/scripts/washing-machine/extract-attachment.sh"
)

if rg -n 'k2b-remote/\.env|k2b-remote/src|Telegram (photo|document|text|bot)' "${targets[@]}"; then
  echo "retired Telegram runtime remains a live credential or caller path" >&2
  exit 1
fi

rg -q '\.k2b-env' "$ROOT/scripts/yt-transcribe-whisper.sh"
echo "retired credential path guard: PASS"
