#!/usr/bin/env bash
# Unified code-review entrypoint for K2B.
#
# Guarantees the reviewer can never hang the ship:
#   * deadline: hard SIGTERM at --deadline seconds (default 360 = 6 min)
#   * fallback: if Codex fails/times out, auto-runs Kimi on same scope
#   * visibility: watchdog injects HEARTBEAT / HEARTBEAT_STALE / WEDGE_SUSPECTED
#     lines into the unified log every few seconds, so `scripts/review-poll.sh`
#     always shows fresh activity even during pure-inference phases.
#
# Default mode backgrounds the child and returns a JSON envelope with job_id
# and log_path. Use `--wait` to block until the review finishes.
#
# Usage:
#   scripts/review.sh diff --files a.py,b.py
#   scripts/review.sh working-tree
#   scripts/review.sh plan --plan plans/2026-04-21_foo.md
#   scripts/review.sh files --files a.py,b.py --focus "regex safety"
#
# Common flags:
#   --primary codex|kimi      default: codex for ad-hoc reviews; required with --builder-family
#   --builder-family openai|anthropic|kimi|other
#                              enforce the builder/reviewer family matrix
#   --skip-codex REASON       audit + enforce that Codex is not used
#   --other-reviewer-reason R required with --builder-family other
#   --deadline N              default: 360
#   --no-fallback             do not switch reviewers if primary fails
#   --wait                    block with final JSON instead of background
#
# See scripts/lib/review_runner.py for the orchestrator.

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "$SCRIPT_DIR/lib/review_runner.py" "$@"
