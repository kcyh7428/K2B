#!/bin/bash
# K2B Stop-Observe Hook
# Fires after every Codex response. Captures observations for the background observer.
# Reads the Codex Stop hook JSON from stdin.

set -euo pipefail

VAULT="${K2B_VAULT_PATH:-$HOME/Projects/K2B-Vault}"
OBS_FILE="$VAULT/wiki/context/observations.jsonl"
hook_provider="${K2B_HOOK_PROVIDER:-codex}"
case "$hook_provider" in
  *[!A-Za-z0-9_-]*|"") hook_provider="codex" ;;
esac
CURRENT_SKILL_FILE="${K2B_CURRENT_SKILL_FILE:-/tmp/k2b-current-skill-$hook_provider}"
LAST_OBSERVE_FILE="${K2B_LAST_OBSERVE_FILE:-/tmp/k2b-last-observe-$hook_provider}"
RUN_MARKER=""
cleanup_run_marker() {
  [ -n "${RUN_MARKER:-}" ] && rm -f "$RUN_MARKER"
}
trap cleanup_run_marker EXIT ERR HUP INT TERM
RUN_MARKER="$(mktemp "${TMPDIR:-/tmp}/k2b-observe-start.XXXXXX")"
touch "$RUN_MARKER"
mkdir -p "$(dirname "$OBS_FILE")" 2>/dev/null || true
[ -f "$LAST_OBSERVE_FILE" ] || touch "$LAST_OBSERVE_FILE" 2>/dev/null || true

# Prevent infinite loops
input=$(cat)
stop_active=$(echo "$input" | jq -r '.stop_hook_active // false' 2>/dev/null)
if [ "$stop_active" = "true" ]; then
  printf '{}\n'
  exit 0
fi

session_id=$(echo "$input" | jq -r '.session_id // "unknown"' 2>/dev/null)

# Read the transcript/tool data from the stop hook
# The stop hook gets the assistant's recent tool uses and messages
# We extract skill invocations and vault file operations

# Check for k2b skill invocations in the conversation
# We look at the transcript_path if available, or use basic heuristics
ts=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

# Parse tool_results from the stop hook input if available
tool_name=$(echo "$input" | jq -r '.tool_name // empty' 2>/dev/null)
tool_input=$(echo "$input" | jq -r '.tool_input // empty' 2>/dev/null)

# For stop hooks, we get the conversation summary
# Extract any skill references from recent activity
# We'll check if any k2b- skills were invoked by looking at the Skill tool usage

# Simple approach: check if the conversation involved a k2b skill
# The stop hook fires after each assistant turn, so we log what we can detect

# Check for recent vault file changes (last 2 minutes)
recent_vault_changes=""
if [ -d "$VAULT" ]; then
  recent_vault_changes=$(find "$VAULT" -name "*.md" -newer "$LAST_OBSERVE_FILE" -type f 2>/dev/null | head -10 || true)
fi

# Only log if there's something meaningful to capture
if [ -n "$recent_vault_changes" ]; then
  # Read active skill from PostToolUse tracker (set by post-tool-skill-track.sh)
  tracked_skill=""
  if [ -f "$CURRENT_SKILL_FILE" ]; then
    tracked_skill=$(cat "$CURRENT_SKILL_FILE" 2>/dev/null || true)
  fi

  # Build observation entries for each changed file
  while IFS= read -r filepath; do
    [ -z "$filepath" ] && continue
    # Get relative path from vault root
    relpath="${filepath#$VAULT/}"
    # Prefer tracked skill from PostToolUse hook; fall back to path-based guess.
    # Paths updated 2026-04-11 after the 2026-04-08 Karpathy vault migration
    # (Notes/ -> wiki/ + raw/ + review/). Daily/ stays because it's a human journal.
    skill="${tracked_skill:-unknown}"
    [ -z "$skill" ] && skill="unknown"
    if [ "$skill" = "unknown" ]; then
      case "$relpath" in
        review/content_*) skill="k2b-insight-extractor" ;;
        review/*tldr*) skill="k2b-tldr" ;;
        review/*youtube*|review/*video*) skill="k2b-youtube-capture" ;;
        raw/youtube/*) skill="k2b-youtube-capture" ;;
        raw/meetings/*) skill="k2b-meeting-processor" ;;
        raw/research/*) skill="k2b-research" ;;
        raw/tldrs/*) skill="k2b-tldr" ;;
        raw/daily/*) skill="k2b-daily-capture" ;;
        Daily/*) skill="k2b-daily-capture" ;;
        wiki/people/*) skill="k2b-vault-writer" ;;
        wiki/projects/*) skill="k2b-vault-writer" ;;
        wiki/work/*) skill="k2b-vault-writer" ;;
        wiki/concepts/*) skill="k2b-vault-writer" ;;
        wiki/insights/*) skill="k2b-insight-extractor" ;;
        wiki/content-pipeline/*) skill="k2b-review" ;;
        wiki/reference/*) skill="k2b-compile" ;;
        wiki/context/preference-*) skill="k2b-observer" ;;
        Archive/*) skill="k2b-review" ;;
      esac
    fi

    # Detect action from file location.
    # promote = review/ -> wiki/content-pipeline/ (user adopted a content idea)
    # archive = anything landing in Archive/
    # modify  = everything else
    action="modify"
    if echo "$relpath" | grep -q "^Archive/"; then
      action="archive"
    elif echo "$relpath" | grep -q "^wiki/content-pipeline/"; then
      action="promote"
    fi

    # Scrub any secrets from the path (shouldn't have any, but be safe)
    relpath=$(echo "$relpath" | sed 's/[A-Za-z0-9_-]*api[_-]*key[A-Za-z0-9_-]*//gi')

    obs="{\"ts\":\"$ts\",\"session\":\"$session_id\",\"skill\":\"$skill\",\"action\":\"$action\",\"file\":\"$relpath\"}"
    echo "$obs" >> "$OBS_FILE"
  done <<< "$recent_vault_changes"
fi

# Advance the marker to the start of this hook run, not the end. Files changed
# while the hook is scanning remain newer than the marker and are picked up by
# the next run instead of falling between find -newer and a final touch.
touch -r "$RUN_MARKER" "$LAST_OBSERVE_FILE"
cleanup_run_marker
trap - EXIT ERR HUP INT TERM

printf '{}\n'
exit 0
