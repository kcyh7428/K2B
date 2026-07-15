#!/usr/bin/env bash
# higgsfield.sh -- K2B wrapper around the Higgsfield CLI (@higgsfield/cli).
#
# Generates image / video / audio via Higgsfield, waits for completion, downloads
# the result into K2B-Vault/Assets/, and prints the Obsidian embed + absolute path.
#
# Auth: the Higgsfield CLI stores an OAuth token locally (higgsfield auth login).
# No env key is needed. Non-interactive shells do NOT source ~/.zshrc, so the CLI
# is not on PATH by default -- this wrapper adds ~/.npm-global/bin itself.
#
# Usage:
#   scripts/higgsfield.sh --type image  --model nano_banana_pro --prompt "..." [--slug s] [-- <extra cli flags>]
#   scripts/higgsfield.sh --type video  --model kling_o1        --prompt "..." --slug s --start-image ./ref.png
#   scripts/higgsfield.sh --type audio  --model text2speech_v2  --prompt "..." --slug s
#
# Any flags after a literal `--` are passed straight through to
# `higgsfield generate create <model>` (e.g. --image-references, --aspect-ratio,
# --start-image, --end-image). Local file paths in those flags are auto-uploaded
# by the CLI.
#
# Exit codes: 0 ok; 1 usage/arg error; 2 auth missing; 3 generation failed; 4 download failed.
set -euo pipefail

export PATH="$HOME/.npm-global/bin:$PATH"
VAULT="$HOME/Projects/K2B-Vault"

die() { echo "higgsfield: $*" >&2; exit "${2:-1}"; }
command -v higgsfield >/dev/null 2>&1 || die "CLI not found. Install: npm install -g @higgsfield/cli" 2
command -v jq >/dev/null 2>&1 || die "jq not found (brew install jq)" 1

TYPE="" MODEL="" PROMPT="" SLUG=""
PASSTHRU=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --type)   TYPE="${2:-}"; shift 2 ;;
    --model)  MODEL="${2:-}"; shift 2 ;;
    --prompt) PROMPT="${2:-}"; shift 2 ;;
    --slug)   SLUG="${2:-}"; shift 2 ;;
    --)       shift; PASSTHRU=("$@"); break ;;
    *)        die "unknown flag: $1 (put CLI passthrough flags after --)" 1 ;;
  esac
done

[[ -n "$TYPE"  ]] || die "missing --type image|video|audio" 1
[[ -n "$MODEL" ]] || die "missing --model (see: higgsfield model list)" 1
[[ -n "$PROMPT" ]] || die "missing --prompt" 1

case "$TYPE" in
  image) SUBDIR="images" ;;
  video) SUBDIR="video"  ;;
  audio) SUBDIR="audio"  ;;
  *)     die "--type must be image, video, or audio" 1 ;;
esac

# Auth check: token present?
higgsfield auth token >/dev/null 2>&1 || die "not authenticated. Run: higgsfield auth login" 2

# Resolve the slug BEFORE spending credits. A caller-supplied slug is sanitized
# the same way as a prompt-derived one, so a slash / traversal / overlong value
# can never reach the `mv` target after a paid generation has already run.
if [[ -z "$SLUG" ]]; then
  SLUG=$(printf '%s' "$PROMPT" | tr '[:upper:]' '[:lower:]' \
    | tr -cs 'a-z0-9' '-' | sed 's/^-*//; s/-*$//' | cut -c1-40)
else
  SLUG=$(printf '%s' "$SLUG" | tr '[:upper:]' '[:lower:]' \
    | tr -cs 'a-z0-9' '-' | sed 's/^-*//; s/-*$//' | cut -c1-40)
fi
[[ -n "$SLUG" ]] || SLUG="untitled"

DEST_DIR="$VAULT/Assets/$SUBDIR"
mkdir -p "$DEST_DIR"
DATE=$(date +%F)
BASE="$DEST_DIR/${DATE}_${TYPE}_${SLUG}"

echo "higgsfield: generating $TYPE via $MODEL ..." >&2
RAW=$(higgsfield generate create "$MODEL" --prompt "$PROMPT" ${PASSTHRU[@]+"${PASSTHRU[@]}"} \
        --wait --wait-timeout 20m --wait-interval 5s --json 2>/dev/null) \
  || die "generation failed (check credits: higgsfield account status)" 3

# CLI returns a JSON array of jobs; take the first completed result_url.
# A jq failure here means Higgsfield returned truncated / non-JSON output, which
# is a generation-side failure (exit 3), not a download failure (exit 4).
URL=$(printf '%s' "$RAW" | jq -r 'if type=="array" then .[0] else . end | .result_url // empty' 2>/dev/null) \
  || die "unexpected response from Higgsfield (not valid JSON)" 3
STATUS=$(printf '%s' "$RAW" | jq -r 'if type=="array" then .[0] else . end | .status // empty' 2>/dev/null) \
  || die "unexpected response from Higgsfield (not valid JSON)" 3
[[ "$STATUS" == "completed" && -n "$URL" ]] || {
  echo "$RAW" | jq . >&2 2>/dev/null || echo "$RAW" >&2
  die "job did not complete (status=$STATUS)" 3
}

# Extension from URL, fallback per type.
EXT="${URL##*.}"; EXT="${EXT%%\?*}"
if [[ "$EXT" == "$URL" || ${#EXT} -gt 5 ]]; then
  case "$TYPE" in image) EXT=png ;; video) EXT=mp4 ;; audio) EXT=mp3 ;; esac
fi

# Atomically reserve a collision-free final path. `set -o noclobber` makes the
# `: > "$DEST"` fail if the file already exists, so a sequential retry or a
# concurrent same-slug job each claims a distinct name (…-2, …-3) instead of
# overwriting an earlier paid result. The reserved 0-byte file persists and is
# replaced in place by the download.
DEST="${BASE}.${EXT}"; n=1
until ( set -o noclobber; : > "$DEST" ) 2>/dev/null; do
  n=$((n + 1)); DEST="${BASE}-${n}.${EXT}"
  [[ $n -le 999 ]] || die "too many same-slug collisions for ${BASE}.${EXT}" 1
done
TMP=$(mktemp "${DEST_DIR}/.hf.XXXXXX") || { rm -f "$DEST"; die "mktemp failed" 4; }
trap 'rm -f "$TMP" "$DEST"' EXIT
curl -fsSL "$URL" -o "$TMP" || die "download failed: $URL" 4
[[ -s "$TMP" ]] || die "downloaded file is empty" 4
mv "$TMP" "$DEST"
chmod 644 "$DEST"
trap - EXIT

REL="Assets/$SUBDIR/$(basename "$DEST")"
echo "![[${REL}]]"
echo "$DEST"
