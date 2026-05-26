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
#
# Cookies: YouTube's bot-detection returns "Sign in to confirm you're not a bot"
# on some IPs even with the latest yt-dlp. K2B prefers a dedicated Netscape cookie
# file so background daemons never need macOS keychain/browser prompts:
#   K2B_YT_COOKIES_FILE=~/.config/k2b/youtube-cookies.txt
# The default is $HOME/.config/k2b/youtube-cookies.txt when present. As a final
# manual fallback, YT_DLP_COOKIE_BROWSER=chrome|firefox|safari|edge|none controls
# --cookies-from-browser (default: auto-detect Chrome, Firefox, then Safari).
set -euo pipefail

# PATH augmentation for non-interactive callers (cron, launchd, pm2 with a
# bare-env config). cron on macOS uses PATH=/usr/bin:/bin:/usr/sbin:/sbin
# which does NOT include /opt/homebrew/bin (Apple Silicon Homebrew) or
# /usr/local/bin (Intel-mac Homebrew). yt-dlp + ffmpeg live in one of those,
# so without the prefix the script silently falls through every cascade
# tier and reports METHOD: failed. Discovered 2026-05-26 -- yt-canary cron
# had 5 consecutive FAILs since afc59a6 landed because of this. Prepending
# both common Homebrew prefixes is safe (idempotent if already present)
# and a no-op for interactive invocations.
export PATH="/opt/homebrew/bin:/usr/local/bin:${PATH:-/usr/bin:/bin}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TMPDIR_BASE=""
LANGUAGE_HINT=""

cleanup() {
  if [[ -n "$TMPDIR_BASE" ]]; then
    rm -rf "$TMPDIR_BASE"
  fi
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

TMPDIR_BASE=$(mktemp -d "${TMPDIR:-/tmp}/yt-transcript.XXXXXX")
chmod 700 "$TMPDIR_BASE"

# Resolve the dedicated K2B cookie file. Empty result means "do not pass a file".
build_cookies_file() {
  local choice="${K2B_YT_COOKIES_FILE:-$HOME/.config/k2b/youtube-cookies.txt}"
  case "$choice" in
    none|"")
      return 0
      ;;
    "~/"*)
      choice="$HOME/${choice#~/}"
      ;;
  esac

  if [[ -r "$choice" && -s "$choice" ]]; then
    echo "$choice"
  elif [[ -n "${K2B_YT_COOKIES_FILE:-}" ]]; then
    echo "WARN: K2B_YT_COOKIES_FILE is set but not readable/non-empty: $choice" >&2
  fi
  return 0
}

# Build --cookies-from-browser arg based on env override + filesystem probe.
# Empty result means "do not pass browser cookies".
build_cookie_browser() {
  local choice="${YT_DLP_COOKIE_BROWSER:-auto}"
  case "$choice" in
    none|"")
      return 0
      ;;
    auto)
      if [[ -d "$HOME/Library/Application Support/Google/Chrome/Default" ]]; then
        echo "chrome"
      elif [[ -d "$HOME/Library/Application Support/Firefox" ]]; then
        echo "firefox"
      elif [[ -d "$HOME/Library/Containers/com.apple.Safari" ]]; then
        echo "safari"
      fi
      ;;
    *)
      echo "$choice"
      ;;
  esac
}

run_yt_dlp_with_timeout() {
  local timeout_s="${K2B_YT_DLP_ATTEMPT_TIMEOUT:-45}"
  case "$timeout_s" in
    ''|*[!0-9]*)
      timeout_s=45
      ;;
  esac
  if [[ "$timeout_s" -le 0 ]]; then
    timeout_s=45
  fi

  set +e
  python3 - "$timeout_s" yt-dlp "$@" <<'PY'
import os
import signal
import subprocess
import sys

timeout_s = float(sys.argv[1])
argv = sys.argv[2:]

try:
    proc = subprocess.Popen(
        argv,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
except FileNotFoundError:
    raise SystemExit(127)

try:
    raise SystemExit(proc.wait(timeout=timeout_s))
except subprocess.TimeoutExpired:
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        proc.wait(timeout=0.5)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.wait()
    raise SystemExit(124)
PY
  local rc=$?
  set -e
  if [[ "$rc" -eq 124 ]]; then
    echo "WARN: yt-dlp caption attempt timed out after ${timeout_s}s" >&2
  fi
  return "$rc"
}

run_command_with_timeout() {
  local timeout_s="$1"
  shift
  case "$timeout_s" in
    ''|*[!0-9]*)
      timeout_s=600
      ;;
  esac
  if [[ "$timeout_s" -le 0 ]]; then
    timeout_s=600
  fi

  set +e
  python3 - "$timeout_s" "$@" <<'PY'
import os
import signal
import subprocess
import sys

timeout_s = float(sys.argv[1])
argv = sys.argv[2:]

try:
    proc = subprocess.Popen(argv, start_new_session=True)
except FileNotFoundError:
    raise SystemExit(127)

try:
    raise SystemExit(proc.wait(timeout=timeout_s))
except subprocess.TimeoutExpired:
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        proc.wait(timeout=0.5)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.wait()
    raise SystemExit(124)
PY
  local rc=$?
  set -e
  if [[ "$rc" -eq 124 ]]; then
    echo "WARN: command timed out after ${timeout_s}s: $*" >&2
  fi
  return "$rc"
}

COOKIE_SOURCE_FILE=$(build_cookies_file)
COOKIE_BROWSER=$(build_cookie_browser)

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

  # Try WITHOUT cookies first (fast, no keychain access), then with cookies as
  # fallback for when YouTube fires the bot challenge. Ordering matters on
  # macOS: `yt-dlp --cookies-from-browser chrome` HANGS in non-interactive
  # shells when Chrome's keychain item is locked (no GUI prompt possible).
  # The no-cookies path errors fast with "Sign in to confirm" when challenged,
  # so we can detect failure quickly and retry with cookies. When YouTube
  # isn't challenging the IP, no-cookies succeeds in seconds and we never
  # touch the keychain at all. (Tested 2026-05-13 against the Tilbury video
  # from Macau via Oracle Cloud Singapore VPN exit -- behavior was stochastic
  # across the same session.)
  local attempts=("none")
  if [[ -n "$COOKIE_SOURCE_FILE" ]]; then
    attempts+=("file")
  fi
  if [[ -n "$COOKIE_BROWSER" ]]; then
    attempts+=("browser:$COOKIE_BROWSER")
  fi

  local vtt=""
  for attempt in "${attempts[@]}"; do
    rm -rf "$sub_dir"
    mkdir -p "$sub_dir"
    local yt_dlp_args=(
      --skip-download
      --write-auto-sub
      --sub-langs "${lang},${lang}-.*"
      --sub-format "vtt"
      --output "${sub_dir}/%(id)s"
    )
    case "$attempt" in
      none)
        ;;
      file)
        local cookie_copy="$sub_dir/youtube-cookies.txt"
        cp "$COOKIE_SOURCE_FILE" "$cookie_copy"
        chmod 600 "$cookie_copy"
        yt_dlp_args=(--cookies "$cookie_copy" "${yt_dlp_args[@]}")
        ;;
      browser:*)
        yt_dlp_args=(--cookies-from-browser "${attempt#browser:}" "${yt_dlp_args[@]}")
        ;;
    esac
    if run_yt_dlp_with_timeout "${yt_dlp_args[@]}" "$URL"; then
      vtt=$(find "$sub_dir" -type f -name "*.vtt" | head -1)
      if [[ -n "$vtt" && -s "$vtt" ]]; then
        break
      fi
    fi
    vtt=""
  done

  if [[ -z "$vtt" ]]; then
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
WHISPER_HELPER="${K2B_YT_WHISPER_HELPER:-$SCRIPT_DIR/yt-transcribe-whisper.sh}"
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
OUTPUT=$(run_command_with_timeout "${K2B_YT_WHISPER_TIMEOUT:-600}" "$WHISPER_HELPER" "${WHISPER_ARGS[@]}")
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
