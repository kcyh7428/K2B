#!/usr/bin/env bash
# Attachment extraction dispatcher (Ship 1B).
#
# Reads a single JSON envelope from stdin and emits a single JSON envelope
# on stdout. Used by k2b-remote/src/attachmentIngest.ts to turn a Telegram
# photo / document / voice into plain OCR text that flows through the
# existing Ship 1 Normalization Gate (text-only classifier).
#
# Input:
#   {
#     "type": "photo" | "document" | "text",
#     "path": "/abs/path/to/file"    # required for photo / document
#     "text": "message body"         # required for text
#     "message_ts": <ms-since-epoch>
#   }
#
# Output:
#   {
#     "normalized_text": "<extracted text>",
#     "attachment_type": "photo" | "document" | "text",
#     "source_path": "/abs/path/to/file" | null,
#     "provider": "minimax-vlm" | "pdftotext" | "passthrough",
#     "message_ts": <ms-since-epoch>
#   }
#
# Exit codes:
#   0  success
#   2  usage / validation error
#   3  downstream extraction failure (VLM / pdftotext)

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
VLM="$REPO_ROOT/scripts/minimax-vlm.sh"

# --- Read envelope from stdin ---
input=$(cat)
if [ -z "$input" ]; then
  echo "extract-attachment: stdin empty; expected JSON envelope" >&2
  exit 2
fi

# Single python pass that pulls every field we need. Uses -c instead of a
# heredoc because a heredoc would replace python's stdin, swallowing the
# JSON envelope we actually want to parse.
fields=$(printf '%s' "$input" | python3 -c '
import json, sys
try:
    d = json.loads(sys.stdin.read())
except Exception as e:
    sys.stderr.write(f"extract-attachment: bad JSON envelope: {e}\n")
    sys.exit(2)
print(d.get("type", ""))
print(d.get("path", "") or "")
print(d.get("text", "") if d.get("text") is not None else "")
print(d.get("message_ts", "") if d.get("message_ts") is not None else "")
') || { echo "extract-attachment: failed to parse envelope" >&2; exit 2; }

# Split the 4-line blob into type / path / text / message_ts. awk keeps
# empty trailing fields better than bash read when the last field is blank.
type=$(printf '%s\n' "$fields" | awk 'NR==1')
path=$(printf '%s\n' "$fields" | awk 'NR==2')
text=$(printf '%s\n' "$fields" | awk 'NR==3')
msg_ts=$(printf '%s\n' "$fields" | awk 'NR==4')

emit_json() {
  # Args: normalized_text, attachment_type, source_path (or empty), provider, message_ts (or empty)
  python3 -c '
import json, sys
args = sys.argv[1:]
norm, atype, src, prov, ts = args
payload = {
    "normalized_text": norm,
    "attachment_type": atype,
    "source_path": src or None,
    "provider": prov,
    "message_ts": int(ts) if ts else None,
}
json.dump(payload, sys.stdout, ensure_ascii=False)
' "$@"
}

case "$type" in
  photo)
    [ -n "$path" ] || { echo "extract-attachment: photo requires path" >&2; exit 2; }
    [ -f "$path" ] || { echo "extract-attachment: photo path not found: $path" >&2; exit 2; }
    prompt='Transcribe every field on this business card. Return plain text, one field per line as Key: Value. Include both English and Chinese text if present. Be literal: no interpretation, no commentary.'
    if ! ocr=$("$VLM" --image "$path" --prompt "$prompt" --job-name attachment-photo --fallback auto 2>&1); then
      echo "extract-attachment: VLM failed for $path: $ocr" >&2
      exit 3
    fi
    emit_json "$ocr" "photo" "$path" "minimax-vlm" "$msg_ts"
    ;;
  document)
    [ -n "$path" ] || { echo "extract-attachment: document requires path" >&2; exit 2; }
    [ -f "$path" ] || { echo "extract-attachment: document path not found: $path" >&2; exit 2; }
    mime=$(file -b --mime-type "$path")
    if [ "$mime" = "application/pdf" ]; then
      if ! content=$(pdftotext "$path" - 2>&1); then
        echo "extract-attachment: pdftotext failed for $path: $content" >&2
        exit 3
      fi
      # Zero-length output means either a scanned PDF with no OCR layer or
      # a corrupt PDF. Exit non-zero so the caller knows to retry / alert
      # instead of silently writing an empty row to the shelf.
      if [ -z "$content" ]; then
        echo "extract-attachment: pdftotext produced empty output for $path (scanned PDF?)" >&2
        exit 3
      fi
      emit_json "$content" "document" "$path" "pdftotext" "$msg_ts"
    else
      # Plain-text document (markdown, txt, etc.) -- pass through raw content.
      # Binary documents would poison the classifier; reject anything non-text.
      case "$mime" in
        text/*) content=$(cat "$path") ;;
        *) echo "extract-attachment: unsupported document mime '$mime' for $path" >&2; exit 2 ;;
      esac
      emit_json "$content" "document" "$path" "passthrough" "$msg_ts"
    fi
    ;;
  text)
    emit_json "$text" "text" "" "passthrough" "$msg_ts"
    ;;
  *)
    echo "extract-attachment: unknown type '$type' (expected photo|document|text)" >&2
    exit 2
    ;;
esac
