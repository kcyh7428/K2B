#!/usr/bin/env bash
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"

role="${K2B_CAPTURE_WRITER_ROLE:-}"
if [[ -z "$role" ]]; then
  case "${USER:-}" in
    keithmbpm2) role="home" ;;
    keithcheung|fastshower) role="source-only" ;;
  esac
fi
if [[ "$role" != "home" ]]; then
  echo "k2b-orchestrator: Home-writer-only; SJM is source-only" >&2
  exit 78
fi

export PYTHONPATH="$REPO${PYTHONPATH:+:$PYTHONPATH}"
exec python3 -m scripts.lib.orchestrator_store "$@"
