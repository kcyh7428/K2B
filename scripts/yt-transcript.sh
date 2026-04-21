#!/usr/bin/env bash
# Unified YouTube-URL -> transcript helper.
# Cascade: yt-dlp auto-subs (en -> zh) -> Groq Whisper (via yt-transcribe-whisper.sh).
# Used by both k2b-youtube-capture (batch playlist flow) and k2b-remote (ad-hoc
# Telegram URL flow), so the caption-first-then-Whisper logic lives in ONE place.
#
# Usage: yt-transcript.sh <youtube-url> [--language <lang>]
#
# stdout: transcript text (success) or empty (failure)
# stderr: progress messages; final line is always "METHOD: <tier>" where
#         tier is one of: captions-en | captions-zh | groq-whisper | failed
# exit:   0 on success, 1 on total failure
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TMPDIR_BASE="${TMPDIR:-/tmp}/yt-transcript-$$"
LANGUAGE_HINT=""

cleanup() {
  rm -rf "$TMPDIR_BASE"
}
trap cleanup EXIT

usage() {
  echo "Usage: yt-transcript.sh <youtube-url> [--language <lang>]" >&2
  exit 1
}

if [[ $# -lt 1 ]]; then
  usage
fi

URL="$1"
shift

while [[ $# -gt 0 ]]; do
  case "$1" in
    --language)
      LANGUAGE_HINT="$2"
      shift 2
      ;;
    *)
      echo "ERROR: Unknown option: $1" >&2
      usage
      ;;
  esac
done

if [[ "$URL" != http* ]]; then
  echo "ERROR: Expected URL, got: $URL" >&2
  echo "METHOD: failed" >&2
  exit 1
fi

mkdir -p "$TMPDIR_BASE"

# Strip VTT timing/formatting into plain paragraph text.
# YouTube auto-captions use a rolling/progressive format where consecutive cues
# repeat the previous cue's last line. We dedupe only ADJACENT identical lines
# (not global duplicates), so that intentional repeats later in the video --
# choruses, repeated taglines, repeated section headings -- are preserved.
# Codex P2 fix: earlier `awk '!seen[$0]++'` silently dropped any line it had
# ever seen, destroying accuracy for lyric videos and structured talks.
vtt_to_text() {
  local vtt="$1"
  # Drop WEBVTT header, Kind/Language meta, timing lines, empty lines, cue ids,
  # style blocks, and common VTT metadata lines. Strip inline <c.colorXX> tags.
  grep -v -E '^(WEBVTT|Kind:|Language:|NOTE|STYLE|[0-9]+$|[0-9]{2}:[0-9]{2}:[0-9]{2}[.,][0-9]+ --> |align:start|position:)' "$vtt" \
    | sed -E 's/<[^>]*>//g' \
    | sed -E 's/^[[:space:]]+|[[:space:]]+$//g' \
    | awk 'NF && $0 != prev { print; prev = $0 }' \
    | tr '\n' ' ' \
    | sed 's/  */ /g' \
    | sed -E 's/^[[:space:]]+|[[:space:]]+$//g'
}

try_subs() {
  local lang="$1"
  local sub_dir="$TMPDIR_BASE/subs-$lang"
  mkdir -p "$sub_dir"

  # --skip-download so we don't fetch the video. auto-sub gets YT's auto-captions
  # (what the MCP tool returns); --sub-langs "$lang.*" also matches en-US, en-GB.
  if ! yt-dlp \
      --skip-download \
      --write-auto-sub \
      --sub-langs "${lang},${lang}-.*" \
      --sub-format "vtt" \
      --output "${sub_dir}/%(id)s" \
      "$URL" >/dev/null 2>&1; then
    return 1
  fi

  # Find the first VTT that was written.
  local vtt
  vtt=$(find "$sub_dir" -type f -name "*.vtt" | head -1)
  if [[ -z "$vtt" || ! -s "$vtt" ]]; then
    return 1
  fi

  local text
  text=$(vtt_to_text "$vtt")
  # Require at least 100 chars of real content (matches skill's threshold).
  if [[ "${#text}" -lt 100 ]]; then
    return 1
  fi

  printf '%s\n' "$text"
  return 0
}

# --- Tier 1a: English captions ---
echo "Trying YouTube auto-captions (en)..." >&2
if [[ -z "$LANGUAGE_HINT" || "$LANGUAGE_HINT" == "en" ]]; then
  if OUTPUT=$(try_subs "en"); then
    printf '%s\n' "$OUTPUT"
    echo "METHOD: captions-en" >&2
    exit 0
  fi
fi

# --- Tier 1b: Chinese captions ---
echo "English captions unavailable. Trying Chinese auto-captions..." >&2
if [[ -z "$LANGUAGE_HINT" || "$LANGUAGE_HINT" == "zh" ]]; then
  if OUTPUT=$(try_subs "zh"); then
    printf '%s\n' "$OUTPUT"
    echo "METHOD: captions-zh" >&2
    exit 0
  fi
fi

# --- Tier 2: Groq Whisper (audio download + ASR) ---
echo "No captions. Falling back to Groq Whisper (downloading audio)..." >&2
WHISPER_HELPER="$SCRIPT_DIR/yt-transcribe-whisper.sh"
if [[ ! -x "$WHISPER_HELPER" ]]; then
  echo "ERROR: $WHISPER_HELPER not executable" >&2
  echo "METHOD: failed" >&2
  exit 1
fi

WHISPER_ARGS=("$URL")
if [[ -n "$LANGUAGE_HINT" ]]; then
  WHISPER_ARGS+=(--language "$LANGUAGE_HINT")
fi

# Let the Whisper helper's stderr flow through so the caller (bot logs,
# Keith debugging from CLI) sees extraction-failure errors. In particular
# yt-playlist-poll.sh exits 2 with "Could not extract video ID from URL"
# when the URL shape isn't recognised -- that message is what diagnoses the
# problem; if we hid it, the user sees only "all transcript methods failed".
# Use an || branch to capture exit status while letting stderr pass.
set +e
OUTPUT=$("$WHISPER_HELPER" "${WHISPER_ARGS[@]}")
WHISPER_EXIT=$?
set -e

if [[ $WHISPER_EXIT -eq 0 && -n "$OUTPUT" ]]; then
  printf '%s\n' "$OUTPUT"
  echo "METHOD: groq-whisper" >&2
  exit 0
fi

echo "All transcript methods failed for $URL" >&2
echo "METHOD: failed" >&2
exit 1
