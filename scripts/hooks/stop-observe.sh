#!/bin/bash
# K2B Stop-Observe Hook
# Fires after every Claude response. Captures observations for the background observer.
# Reads JSON from stdin (Claude's stop hook payload).

set -euo pipefail

VAULT="/Users/keithmbpm2/Projects/K2B-Vault"
OBS_FILE="$VAULT/Notes/Context/observations.jsonl"

# Prevent infinite loops
input=$(cat)
stop_active=$(echo "$input" | jq -r '.stop_hook_active // false' 2>/dev/null)
if [ "$stop_active" = "true" ]; then
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
  recent_vault_changes=$(find "$VAULT" -name "*.md" -newer /tmp/k2b-last-observe -type f 2>/dev/null | head -10 || true)
fi

# Only log if there's something meaningful to capture
if [ -n "$recent_vault_changes" ]; then
  # Build observation entries for each changed file
  while IFS= read -r filepath; do
    [ -z "$filepath" ] && continue
    # Get relative path from vault root
    relpath="${filepath#$VAULT/}"
    # Detect skill from file path patterns
    skill="unknown"
    case "$relpath" in
      Inbox/content_*) skill="k2b-insight-extractor" ;;
      Inbox/*tldr*) skill="k2b-tldr" ;;
      Inbox/*youtube*|Inbox/*video*) skill="k2b-youtube-capture" ;;
      Daily/*) skill="k2b-daily-capture" ;;
      Notes/People/*) skill="k2b-vault-writer" ;;
      Notes/Projects/*) skill="k2b-vault-writer" ;;
      Notes/Content-Ideas/*) skill="k2b-inbox" ;;
      Archive/*) skill="k2b-inbox" ;;
      Notes/Context/preference-*) skill="k2b-observer" ;;
    esac

    # Detect action from file location
    action="modify"
    if echo "$relpath" | grep -q "^Archive/"; then
      action="archive"
    elif echo "$relpath" | grep -q "^Notes/Content-Ideas/"; then
      action="promote"
    fi

    # Scrub any secrets from the path (shouldn't have any, but be safe)
    relpath=$(echo "$relpath" | sed 's/[A-Za-z0-9_-]*api[_-]*key[A-Za-z0-9_-]*//gi')

    obs="{\"ts\":\"$ts\",\"session\":\"$session_id\",\"skill\":\"$skill\",\"action\":\"$action\",\"file\":\"$relpath\"}"
    echo "$obs" >> "$OBS_FILE"
  done <<< "$recent_vault_changes"
fi

# Update the timestamp marker for next comparison
touch /tmp/k2b-last-observe

exit 0
