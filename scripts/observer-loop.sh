#!/bin/bash
# K2B Observer Loop
# Background process that periodically analyzes session observations using MiniMax LLM.
# Designed to run on Mac Mini via pm2.
#
# Usage: ./observer-loop.sh
# pm2:  pm2 start scripts/observer-loop.sh --name k2b-observer --interpreter bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/minimax-common.sh"

# --- Config ---
# Detect vault path: Mac Mini uses fastshower, MacBook uses keithmbpm2
if [ -d "/Users/fastshower/Projects/K2B-Vault" ]; then
  DEFAULT_VAULT="/Users/fastshower/Projects/K2B-Vault"
elif [ -d "/Users/keithmbpm2/Projects/K2B-Vault" ]; then
  DEFAULT_VAULT="/Users/keithmbpm2/Projects/K2B-Vault"
else
  DEFAULT_VAULT="$HOME/Projects/K2B-Vault"
fi
VAULT="${K2B_VAULT:-$DEFAULT_VAULT}"
# 2026-04-10: migrated from Notes/Context to wiki/context (Karpathy 3-layer vault)
CONTEXT_DIR="$VAULT/wiki/context"
mkdir -p "$CONTEXT_DIR"
OBS_FILE="$CONTEXT_DIR/observations.jsonl"
OBS_ARCHIVE="$CONTEXT_DIR/observations.archive"
PROFILE_FILE="$CONTEXT_DIR/preference-profile.md"
CANDIDATES_FILE="$CONTEXT_DIR/observer-candidates.md"
SIGNALS_FILE="$CONTEXT_DIR/preference-signals.jsonl"
RUNS_FILE="$CONTEXT_DIR/observer-runs.jsonl"
PROMPT_FILE="$SCRIPT_DIR/observer-prompt.md"
LOCKFILE="/tmp/k2b-observer.lock"
LAST_RUN_FILE="/tmp/k2b-observer-last-run"

# Gate thresholds
MIN_OBSERVATIONS=20          # Minimum new observations before analyzing
COOLDOWN_SECONDS=3600        # 1 hour between analyses
ACTIVE_HOURS_START=7         # 7am HKT
ACTIVE_HOURS_END=23          # 11pm HKT
SLEEP_INTERVAL=300           # Check every 5 minutes
TAIL_COUNT=200               # Last N observations to analyze
MODEL="MiniMax-M2.7"        # MiniMax model (upgraded from M2.5 2026-04-08)

# --- Helpers ---

log() {
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] K2B-OBSERVER: $*"
}

cleanup() {
  rm -f "$LOCKFILE"
  log "Observer loop stopped."
}
trap cleanup EXIT

# --- Gate Checks ---

check_time_window() {
  local hour
  hour=$(date +%H | sed 's/^0//')
  if [ "$hour" -ge "$ACTIVE_HOURS_START" ] && [ "$hour" -lt "$ACTIVE_HOURS_END" ]; then
    return 0
  fi
  return 1
}

check_cooldown() {
  if [ ! -f "$LAST_RUN_FILE" ]; then
    return 0
  fi
  local last_run
  last_run=$(cat "$LAST_RUN_FILE" 2>/dev/null || echo 0)
  local now
  now=$(date +%s)
  local elapsed=$((now - last_run))
  if [ "$elapsed" -ge "$COOLDOWN_SECONDS" ]; then
    return 0
  fi
  return 1
}

check_observation_threshold() {
  if [ ! -f "$OBS_FILE" ]; then
    return 1
  fi
  local count
  count=$(wc -l < "$OBS_FILE" | tr -d ' ')
  if [ "$count" -ge "$MIN_OBSERVATIONS" ]; then
    return 0
  fi
  return 1
}

# --- Analysis ---

run_analysis() {
  log "Starting analysis..."

  # Acquire lock
  if ! mkdir "$LOCKFILE" 2>/dev/null; then
    log "Another analysis is running. Skipping."
    return 0
  fi

  # Tail observations
  local observations
  observations=$(tail -n "$TAIL_COUNT" "$OBS_FILE" 2>/dev/null || true)
  if [ -z "$observations" ]; then
    log "No observations to analyze."
    rm -rf "$LOCKFILE"
    return 0
  fi

  # Load current profile (if exists)
  local profile=""
  if [ -f "$PROFILE_FILE" ]; then
    profile=$(cat "$PROFILE_FILE" 2>/dev/null || true)
  fi

  # Load learnings (find the right memory file)
  local learnings=""
  local learnings_file
  learnings_file=$(find /Users/keithmbpm2/.claude/projects/ -name "self_improve_learnings.md" -type f 2>/dev/null | head -1 || true)
  # On Mac Mini, try the server path too
  if [ -z "$learnings_file" ]; then
    learnings_file=$(find /Users/fastshower/.claude/projects/ -name "self_improve_learnings.md" -type f 2>/dev/null | head -1 || true)
  fi
  if [ -n "$learnings_file" ] && [ -f "$learnings_file" ]; then
    learnings=$(cat "$learnings_file" 2>/dev/null || true)
  fi

  # Load system prompt
  local system_prompt
  system_prompt=$(cat "$PROMPT_FILE" 2>/dev/null || echo "Analyze the observations and return JSON with patterns, candidate_learnings, confidence_updates, and summary.")

  # Build user message
  local user_msg="## Recent Observations (JSONL)

$observations

## Current Preference Profile

${profile:-No preference profile exists yet.}

## Current Learnings

${learnings:-No learnings captured yet.}

Analyze these observations and return your findings as JSON."

  # Call MiniMax API
  local request_body
  request_body=$(jq -n \
    --arg model "$MODEL" \
    --arg system "$system_prompt" \
    --arg user "$user_msg" \
    '{
      model: $model,
      messages: [
        { role: "system", name: "K2B Observer", content: $system },
        { role: "user", name: "observer", content: $user }
      ],
      max_completion_tokens: 4000,
      temperature: 0.3
    }')

  log "Calling MiniMax API ($MODEL)..."
  local response
  response=$(mm_api POST /v1/text/chatcompletion_v2 "$request_body" 2>&1) || {
    log "ERROR: MiniMax API call failed: $response"
    rm -rf "$LOCKFILE"
    return 1
  }

  # Extract the assistant's response content
  local content
  content=$(echo "$response" | jq -r '.choices[0].message.content // empty' 2>/dev/null)
  if [ -z "$content" ]; then
    log "ERROR: Empty response from MiniMax API"
    log "Raw response: $(echo "$response" | head -c 500)"
    rm -rf "$LOCKFILE"
    return 1
  fi

  log "Got analysis response ($(echo "$content" | wc -c | tr -d ' ') bytes)"

  # Append run to observer-runs.jsonl for the dashboard Learning Inspector audit.
  # Captures the prompt + raw response so Keith can read what MiniMax actually saw.
  # Truncate via bash substring expansion to avoid SIGPIPE under set -e pipefail.
  local prompt_truncated response_truncated runs_line
  prompt_truncated="${system_prompt:0:8000}"
  response_truncated="${content:0:8000}"
  runs_line=$(jq -nc \
    --arg ts "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
    --arg prompt "$prompt_truncated" \
    --arg response "$response_truncated" \
    --arg model "$MODEL" \
    '{ts: $ts, model: $model, prompt: $prompt, response: $response}' 2>/dev/null) || runs_line=""
  if [ -n "$runs_line" ]; then
    echo "$runs_line" >> "$RUNS_FILE"
  fi

  # Try to parse as JSON (strip markdown code fences if present)
  local json_content
  json_content=$(echo "$content" | sed 's/^```json//;s/^```//' | jq '.' 2>/dev/null) || {
    log "WARNING: Response is not valid JSON. Saving raw content."
    json_content="$content"
  }

  # --- Process Results ---

  # Extract patterns with high confidence for the candidates file
  local summary
  summary=$(echo "$json_content" | jq -r '.summary // "Analysis complete but no summary provided."' 2>/dev/null || echo "$content")

  local high_patterns
  high_patterns=$(echo "$json_content" | jq -r '
    .patterns[]? |
    select(.confidence == "high" or .confidence == "medium") |
    "- [\(.confidence)] \(.description)\n  Recommendation: \(.recommendation)"
  ' 2>/dev/null || true)

  local candidate_learnings
  candidate_learnings=$(echo "$json_content" | jq -r '
    .candidate_learnings[]? |
    select(.confidence == "high" or .confidence == "medium") |
    "- [\(.confidence)] \(.area): \(.learning)\n  Evidence: \(.evidence)"
  ' 2>/dev/null || true)

  # Write observer-candidates.md (read by session-start hook)
  {
    echo "# Observer Candidates"
    echo ""
    echo "Last analysis: $(date '+%Y-%m-%d %H:%M')"
    echo "Observations analyzed: $(echo "$json_content" | jq -r '.observations_analyzed // "unknown"' 2>/dev/null)"
    echo ""
    echo "## Summary"
    echo "$summary"
    echo ""
    if [ -n "$high_patterns" ]; then
      echo "## Detected Patterns"
      echo "$high_patterns"
      echo ""
    fi
    if [ -n "$candidate_learnings" ]; then
      echo "## Candidate Learnings (confirm with Keith)"
      echo "$candidate_learnings"
      echo ""
    fi
  } > "$CANDIDATES_FILE"

  log "Updated observer-candidates.md"

  # Write youtube-taste-profile.md if analysis contains youtube_taste
  local has_taste
  has_taste=$(echo "$json_content" | jq -r '.youtube_taste // empty' 2>/dev/null)
  if [ -n "$has_taste" ]; then
    local taste_file="$CONTEXT_DIR/youtube-taste-profile.md"
    local signal_count confidence
    signal_count=$(echo "$json_content" | jq -r '.youtube_taste.signal_count // 0' 2>/dev/null)
    confidence=$(echo "$json_content" | jq -r '.youtube_taste.confidence // "low"' 2>/dev/null)
    {
      echo "---"
      echo "tags: [k2b-system, youtube, taste-profile]"
      echo "date: $(date '+%Y-%m-%d')"
      echo "type: reference"
      echo "origin: k2b-observer"
      echo "up: \"[[MOC_K2B-System]]\""
      echo "---"
      echo ""
      echo "# YouTube Taste Profile"
      echo ""
      echo "Last updated: $(date '+%Y-%m-%d %H:%M') ($signal_count signals, confidence: $confidence)"
      echo ""
      echo "## Channel Scores"
      echo "$json_content" | jq -r '.youtube_taste.channel_scores // {} | to_entries[] | "- **\(.key)**: \(.value)"' 2>/dev/null
      echo ""
      echo "## Topic Scores"
      echo "$json_content" | jq -r '.youtube_taste.topic_scores // {} | to_entries[] | "- **\(.key)**: \(.value)"' 2>/dev/null
      echo ""
      echo "## Depth Preference"
      echo "$json_content" | jq -r '.youtube_taste.depth_preference // "unknown"' 2>/dev/null
      echo ""
      echo "## Anti-Patterns"
      echo "$json_content" | jq -r '.youtube_taste.anti_patterns[]? // empty | "- \(.)"' 2>/dev/null
      echo ""
      echo "## Scoring Adjustments"
      echo "confidence_level: $confidence"
      echo "channel_boost: $(echo "$json_content" | jq -c '[.youtube_taste.channel_scores // {} | to_entries[] | select(.value > 0)] | from_entries' 2>/dev/null)"
      echo "channel_dampen: $(echo "$json_content" | jq -c '[.youtube_taste.channel_scores // {} | to_entries[] | select(.value < 0)] | from_entries' 2>/dev/null)"
      echo "topic_boost: $(echo "$json_content" | jq -c '[.youtube_taste.topic_scores // {} | to_entries[] | select(.value > 0)] | from_entries' 2>/dev/null)"
      echo "topic_dampen: $(echo "$json_content" | jq -c '[.youtube_taste.topic_scores // {} | to_entries[] | select(.value < 0)] | from_entries' 2>/dev/null)"
    } > "$taste_file"
    log "Updated youtube-taste-profile.md ($signal_count signals, confidence: $confidence)"
  fi

  # Append patterns to preference-signals.jsonl
  echo "$json_content" | jq -c '
    .patterns[]? |
    {
      date: (now | strftime("%Y-%m-%d")),
      source: "observer-loop",
      type: .type,
      description: .description,
      confidence: .confidence,
      skill: .skill
    }
  ' 2>/dev/null >> "$SIGNALS_FILE" || true

  # Archive processed observations
  mkdir -p "$OBS_ARCHIVE"
  local archive_name="$OBS_ARCHIVE/observations-$(date +%Y%m%d-%H%M%S).jsonl"
  cp "$OBS_FILE" "$archive_name"
  : > "$OBS_FILE"  # Truncate (not delete, preserves inode)
  log "Archived observations to $archive_name"

  # Prune old archives (keep last 30 days)
  find "$OBS_ARCHIVE" -name "*.jsonl" -mtime +30 -delete 2>/dev/null || true

  # Update last run timestamp
  date +%s > "$LAST_RUN_FILE"

  rm -rf "$LOCKFILE"
  log "Analysis complete."
}

# --- Main Loop ---

log "Observer loop starting. Model: $MODEL, Cooldown: ${COOLDOWN_SECONDS}s, Threshold: ${MIN_OBSERVATIONS} obs"

# Ensure context directory exists
mkdir -p "$CONTEXT_DIR"
mkdir -p "$OBS_ARCHIVE"

while true; do
  # Gate checks
  if ! check_time_window; then
    log "Outside active hours ($ACTIVE_HOURS_START-$ACTIVE_HOURS_END). Sleeping."
    sleep "$SLEEP_INTERVAL"
    continue
  fi

  if ! check_cooldown; then
    sleep "$SLEEP_INTERVAL"
    continue
  fi

  if ! check_observation_threshold; then
    sleep "$SLEEP_INTERVAL"
    continue
  fi

  # All gates passed -- run analysis
  run_analysis || log "Analysis failed, will retry next cycle"

  sleep "$SLEEP_INTERVAL"
done
