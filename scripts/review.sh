#!/usr/bin/env bash
# Unified code-review entrypoint for K2B.
#
# Guarantees the reviewer can never hang the ship:
#   * deadline: hard SIGTERM at --deadline seconds (default 360 = 6 min)
#   * fallback: if Codex fails/times out, auto-runs Kimi on same scope
#   * visibility: watchdog injects HEARTBEAT / HEARTBEAT_STALE / INFERENCE_WAIT
#     lines into the unified log every few seconds, so `scripts/review-poll.sh`
#     always shows fresh activity even during pure-inference phases.
#
# Default mode backgrounds the child and returns a JSON envelope with job_id
# and log_path. Use `--wait` to block until the review finishes.
# Kimi --wait: exit 0 is approval; exit 4 is completed with needs-attention,
# without fallback. Dispatch/poll success and status=completed are not approval.
# See plans/2026-10-26_kimi-review-result-contract.md for evidence retention.
#
# Usage:
#   scripts/review.sh diff --files a.py,b.py
#   scripts/review.sh working-tree
#   scripts/review.sh plan --plan plans/2026-04-21_foo.md
#   scripts/review.sh files --files a.py,b.py --focus "regex safety"
#
# Common flags:
#   --primary auto|codex|kimi default: codex for ad-hoc reviews; required with --builder-family
#   auto counts the complete packet: Qwen <=12000 tokens / 120 seconds,
#   otherwise standard Kimi K2.8 Preview. Invalid local output also selects Kimi.
#   --builder-family openai|anthropic|kimi|other|qwen|deepseek
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
