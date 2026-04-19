#!/usr/bin/env bash
# Write a Telegram outbox manifest for the k2b-remote bot to pick up.
# Replaces the hand-rolled `echo '{...}' > outbox/file.json` pattern which
# fails on emojis, exclamation marks, apostrophes, and other escape edge
# cases (the scanner's JSON.parse throws SyntaxError and the catch block
# silently unlinks the invalid manifest -- loud in logs, silent to the
# caller, file never sent).
#
# Usage:
#   scripts/telegram-outbox-write.sh <type> <path> [caption]
#
# Args:
#   type:    photo | audio | video | document
#   path:    absolute path to the file to send (must exist)
#   caption: optional. Any Unicode/emojis/punctuation is safe; the helper
#            uses python3 json.dump which produces guaranteed-valid JSON.
#
# Writes atomically (temp file + rename) to
#   $K2B_ROOT/k2b-remote/workspace/telegram-outbox/<unixts>_<rand>.json
# Defaults K2B_ROOT to $HOME/Projects/K2B so the helper works on both the
# MacBook (user keithmbpm2) and the Mac Mini (user fastshower) without
# edits.
#
# Prints the manifest path on success. Exits:
#   0 -- manifest written
#   1 -- bad arguments (missing type/path, invalid type)
#   2 -- source file does not exist
#   3 -- write failed

set -euo pipefail

TYPE="${1:-}"
FILE_PATH="${2:-}"
CAPTION="${3:-}"

if [[ -z "$TYPE" || -z "$FILE_PATH" ]]; then
  echo "usage: $(basename "$0") <type> <path> [caption]" >&2
  echo "  type: photo | audio | video | document" >&2
  exit 1
fi

case "$TYPE" in
  photo|audio|video|document) ;;
  *)
    echo "error: invalid type '$TYPE' (must be photo|audio|video|document)" >&2
    exit 1
    ;;
esac

if [[ ! -f "$FILE_PATH" ]]; then
  echo "error: file not found: $FILE_PATH" >&2
  exit 2
fi

K2B_ROOT="${K2B_ROOT:-$HOME/Projects/K2B}"
OUTBOX_DIR="$K2B_ROOT/k2b-remote/workspace/telegram-outbox"

mkdir -p "$OUTBOX_DIR"

MANIFEST_NAME="$(date +%s)_$RANDOM.json"
FINAL_PATH="$OUTBOX_DIR/$MANIFEST_NAME"
TMP_PATH="$OUTBOX_DIR/.tmp_$MANIFEST_NAME"

# Python builds the JSON: it handles UTF-8, emojis, embedded quotes,
# backslashes, and every other escape-sensitive byte correctly -- and it
# fsyncs before the rename so the scanner never sees a partial write.
# The outbox scanner explicitly skips .tmp_ files, so the temp path is
# invisible to it until the atomic rename completes.
if ! python3 - "$TYPE" "$FILE_PATH" "$CAPTION" "$TMP_PATH" <<'PY'
import json, os, sys

type_, path, caption, tmp_path = sys.argv[1:5]

payload = {"type": type_, "path": path}
if caption:
    payload["caption"] = caption

with open(tmp_path, "w", encoding="utf-8") as f:
    json.dump(payload, f, ensure_ascii=False)
    f.flush()
    os.fsync(f.fileno())
PY
then
  echo "error: failed to write manifest body" >&2
  rm -f "$TMP_PATH"
  exit 3
fi

mv "$TMP_PATH" "$FINAL_PATH"

echo "$FINAL_PATH"
