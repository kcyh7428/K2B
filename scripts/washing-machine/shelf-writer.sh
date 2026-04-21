#!/usr/bin/env bash
# scripts/washing-machine/shelf-writer.sh
# Atomic append to wiki/context/shelves/<shelf>.md. Single writer per shelf.
#
# Locking: flock-if-available, mkdir fallback (macOS has no flock by default).
# Write: mktemp in same dir + mv. If the temp write cannot land, the target
# file is not mutated and the script exits non-zero.
#
# Row format (after leading "- " bullet):
#   <YYYY-MM-DD> | <type> | <slug> | <key>:<value> | ...
# Pipes in values are escaped by lib/shelf_rows.py serialize.
#
# Plan: plans/2026-04-21_washing-machine-ship-1.md Commit 1.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LIB="$SCRIPT_DIR/lib/shelf_rows.py"

WASHING_MACHINE_ENV="${WASHING_MACHINE_ENV:-$HOME/.config/k2b/washing-machine.env}"
if [ -f "$WASHING_MACHINE_ENV" ]; then
  # shellcheck disable=SC1090
  . "$WASHING_MACHINE_ENV"
fi
PYTHON_BIN="${WASHING_MACHINE_PYTHON:-python3}"

VAULT="${K2B_VAULT:-$HOME/Projects/K2B-Vault}"
SHELVES_DIR="${K2B_SHELVES_DIR:-$VAULT/wiki/context/shelves}"
LOCK_DIR_ROOT="${K2B_SHELF_LOCK_DIR:-/tmp}"

usage() {
  cat >&2 <<'EOF'
usage: shelf-writer.sh --shelf NAME --date YYYY-MM-DD --type TYPE --slug SLUG
                       [--attr key:value ...]
EOF
  exit 64
}

SHELF=""
DATE=""
TYPE=""
SLUG=""
ATTRS=()

while [ "$#" -gt 0 ]; do
  case "$1" in
    --shelf) SHELF="${2:-}"; shift 2 ;;
    --date)  DATE="${2:-}";  shift 2 ;;
    --type)  TYPE="${2:-}";  shift 2 ;;
    --slug)  SLUG="${2:-}";  shift 2 ;;
    --attr)  ATTRS+=("${2:-}"); shift 2 ;;
    -h|--help) usage ;;
    *) echo "shelf-writer: unknown arg '$1'" >&2; usage ;;
  esac
done

[ -n "$SHELF" ] || usage
[ -n "$DATE" ]  || usage
[ -n "$TYPE" ]  || usage
[ -n "$SLUG" ]  || usage

if ! printf '%s' "$SHELF" | grep -Eq '^[a-z][a-z0-9_-]*$'; then
  echo "shelf-writer: shelf name must match [a-z][a-z0-9_-]*, got '$SHELF'" >&2
  exit 64
fi

TARGET="$SHELVES_DIR/$SHELF.md"
LOCK="$LOCK_DIR_ROOT/k2b-shelf-$SHELF.lock"

# ---- lock (flock-if-available, mkdir fallback) ----
_LOCK_DIR=""
_release_lock() {
  if [ -n "$_LOCK_DIR" ]; then
    rmdir "$_LOCK_DIR" 2>/dev/null || true
  fi
}
trap _release_lock EXIT

acquire_lock() {
  if command -v flock >/dev/null 2>&1; then
    exec 9>"$LOCK"
    if ! flock -x -w 10 9; then
      echo "shelf-writer: could not acquire flock $LOCK after 10s" >&2
      exit 3
    fi
  else
    _LOCK_DIR="${LOCK}.d"
    local tries=0
    while ! mkdir "$_LOCK_DIR" 2>/dev/null; do
      tries=$((tries + 1))
      if [ "$tries" -gt 200 ]; then
        echo "shelf-writer: could not acquire $_LOCK_DIR after 10s" >&2
        exit 3
      fi
      sleep 0.05
    done
  fi
}

# ---- serialize the row via lib/shelf_rows.py ----
build_serialize_cmd() {
  SERIALIZE_CMD=("$PYTHON_BIN" "$LIB" serialize --date "$DATE" --type "$TYPE" --slug "$SLUG")
  local a
  for a in "${ATTRS[@]}"; do
    SERIALIZE_CMD+=(--attr "$a")
  done
}

# ---- compose new file content ----
# Reads existing file (if any) from stdin; writes new content to stdout.
# Inserts the new row bullet at end of "## Rows" section (or end of body),
# updates frontmatter row-count to $NEW_COUNT.
# Variables are passed via environment (awk -v strips backslash escapes,
# which would eat our \| pipe escapes).
rewrite_content() {
  local shelf="$1" new_count="$2" new_row="$3"
  AWK_SHELF="$shelf" \
  AWK_NEW_COUNT="$new_count" \
  AWK_NEW_ROW="$new_row" \
  awk '
    BEGIN {
      shelf     = ENVIRON["AWK_SHELF"]
      new_count = ENVIRON["AWK_NEW_COUNT"]
      new_row   = ENVIRON["AWK_NEW_ROW"]
      fm=0; fm_seen=0
      row_count_written=0
      in_rows=0
      rows_have_bullet=0
      appended=0
      body_has_content=0
    }
    # Frontmatter delimiter handling
    /^---$/ {
      if (!fm_seen) { fm=1; fm_seen=1; print; next }
      else if (fm) {
        if (!row_count_written) {
          print "row-count: " new_count
          row_count_written=1
        }
        fm=0
        print
        next
      }
      print; next
    }
    # Inside frontmatter: rewrite row-count line if present.
    fm {
      if ($0 ~ /^row-count:[[:space:]]*[0-9]+/) {
        print "row-count: " new_count
        row_count_written=1
        next
      }
      print
      next
    }
    # After frontmatter: track enter/leave of the Rows section.
    /^## Rows[[:space:]]*$/ {
      # Defensive: append into any prior Rows block before a second header.
      if (in_rows && !appended) {
        print "- " new_row
        appended=1
      }
      print
      in_rows=1
      body_has_content=1
      next
    }
    # Any other level-2 header closes the Rows section.
    /^##[^#]/ {
      if (in_rows && !appended) {
        print "- " new_row
        appended=1
      }
      in_rows=0
      body_has_content=1
      print
      next
    }
    # Everything else passes through.
    {
      if (NF > 0) body_has_content=1
      if (in_rows && /^- /) rows_have_bullet=1
      print
    }
    END {
      if (appended) { exit }
      if (in_rows) {
        # Reached EOF inside Rows: append the bullet. If no prior bullets
        # existed (first-ever write), keep a blank line between header and
        # bullet for readability.
        if (!rows_have_bullet) print ""
        print "- " new_row
        exit
      }
      if (!fm_seen) {
        # Caller emits a template for new files, so this branch is the
        # malformed-existing-file fallback only.
        print "---"
        print "tags: [context, shelf, " shelf ", washing-machine]"
        print "type: shelf"
        print "shelf: " shelf
        print "origin: k2b-classifier"
        print "row-count: " new_count
        print "up: \"[[index]]\""
        print "---"
        print ""
        print "# " toupper(substr(shelf,1,1)) substr(shelf,2) " shelf"
        print ""
        print "## Rows"
        print ""
        print "- " new_row
        exit
      }
      # Frontmatter present but no Rows section -- inject one at the end.
      if (body_has_content) print ""
      print "## Rows"
      print ""
      print "- " new_row
    }
  '
}

# ---- read current row-count from frontmatter (0 if absent/file-missing) ----
read_row_count() {
  local file="$1"
  if [ ! -f "$file" ]; then
    echo 0
    return
  fi
  awk '
    BEGIN { fm=0; seen=0 }
    /^---$/ {
      if (!seen) { fm=1; seen=1; next }
      else if (fm) { exit }
    }
    fm && /^row-count:[[:space:]]*[0-9]+/ {
      sub("^row-count:[[:space:]]*", "")
      print $0 + 0
      exit
    }
    END { if (!seen) print 0 }
  ' "$file"
}

# ---- initial frontmatter template for a brand-new shelf file ----
new_shelf_template() {
  local shelf="$1"
  # Capitalize first letter via awk (portable across bash 3.2 + 4+).
  local title
  title="$(printf '%s' "$shelf" | awk '{printf "%s%s", toupper(substr($0,1,1)), substr($0,2)}')"
  cat <<EOF
---
tags: [context, shelf, ${shelf}, washing-machine]
type: shelf
shelf: ${shelf}
origin: k2b-classifier
row-count: 0
up: "[[index]]"
---

# ${title} shelf

Rows written by the Washing Machine classifier. Atomic append via scripts/washing-machine/shelf-writer.sh. Do not edit by hand; embed-index.py tracks row-hash idempotence.

## Rows

EOF
}

# ---- main ----
main() {
  build_serialize_cmd
  local new_row
  if ! new_row="$("${SERIALIZE_CMD[@]}")"; then
    echo "shelf-writer: serialize failed" >&2
    exit 65
  fi

  mkdir -p "$SHELVES_DIR" || {
    echo "shelf-writer: cannot create shelves dir $SHELVES_DIR" >&2
    exit 2
  }

  acquire_lock

  # Refuse to write into a file with >1 "## Rows" header: the awk rewrite
  # would append at an ambiguous location. This protects against hand-edited
  # shelves that violate the single-section invariant.
  if [ -f "$TARGET" ]; then
    local header_count
    header_count="$(grep -c '^## Rows' "$TARGET" || true)"
    if [ "${header_count:-0}" -gt 1 ]; then
      echo "shelf-writer: $TARGET has ${header_count} '## Rows' headers; refusing to write ambiguously" >&2
      exit 2
    fi
  fi

  local current_count new_count
  current_count="$(read_row_count "$TARGET")"
  new_count=$((current_count + 1))

  # Build source content (existing file or a fresh template).
  local src
  if [ -f "$TARGET" ]; then
    src="$(cat "$TARGET")"
  else
    src="$(new_shelf_template "$SHELF")"
  fi

  # Atomic write: mktemp in same dir as target + mv.
  local tmp
  if ! tmp="$(mktemp "$TARGET.tmp.XXXXXX" 2>/dev/null)"; then
    echo "shelf-writer: cannot create temp file next to $TARGET (dir writable?)" >&2
    exit 2
  fi

  if ! printf '%s\n' "$src" | rewrite_content "$SHELF" "$new_count" "$new_row" >"$tmp"; then
    rm -f "$tmp"
    echo "shelf-writer: failed to compose new content" >&2
    exit 2
  fi

  if ! mv "$tmp" "$TARGET"; then
    rm -f "$tmp"
    echo "shelf-writer: mv to $TARGET failed" >&2
    exit 2
  fi
}

main "$@"
