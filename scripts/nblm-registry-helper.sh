#!/usr/bin/env bash
# scripts/nblm-registry-helper.sh
# Single writer for wiki/context/notebooklm-registry.md.
# Owns the write procedure per the memory ownership matrix; SKILL.md only
# advertises command routing.
#
# Commands:
#   add <name> <id> [description]     Register a named notebook. Idempotent
#                                     on name (updates id/description if name
#                                     already exists).
#   get <name>                        Print notebook ID for <name>, or exit 4
#                                     if not found.
#   list                              Print registry table to stdout
#                                     (markdown, suitable for Telegram/reply).
#   remove <name>                     Remove registry entry (does NOT delete
#                                     the NotebookLM notebook itself; caller
#                                     must run `notebooklm delete` first if
#                                     they want full cleanup).
#   update <name> [--desc D] [--sources N] [--touch]
#                                     Update description, source count, or
#                                     last-used date of an existing entry.
#   path                              Print absolute path of the registry
#                                     markdown file.
#
# Format: all rows live between HTML markers in the markdown file so the
# helper can rewrite them without touching human-editable prose above or
# below the markers. Row schema (pipe-separated, nine columns):
#   | name | id | description | sources | created | last-used |
#
# Locking: flock if available, mkdir fallback for macOS.
# Writes: atomic tmp+mv in same directory (mv is atomic within a filesystem).

set -euo pipefail

VAULT="${K2B_VAULT:-$HOME/Projects/K2B-Vault}"
REG_FILE="${K2B_NBLM_REGISTRY_FILE:-$VAULT/wiki/context/notebooklm-registry.md}"
LOCK="${K2B_NBLM_REGISTRY_LOCK:-/tmp/k2b-nblm-registry.lock}"

MARKER_START='<!-- REGISTRY-TABLE-START (helper-owned, do not edit by hand) -->'
MARKER_END='<!-- REGISTRY-TABLE-END -->'
HEADER='| Name | Notebook ID | Description | Sources | Created | Last Used |'
DIVIDER='|------|-------------|-------------|---------|---------|-----------|'
EMPTY_ROW='| *(empty -- register a notebook via `/research notebook create`)* |  |  |  |  |  |'

usage() {
  cat >&2 <<EOF
usage: $0 {add <name> <id> [desc] | get <name> | list | remove <name>
           | update <name> [--desc D] [--sources N] [--touch]
           | path}
EOF
  exit 64
}

_LOCK_DIR=""
_release_lock() {
  if [ -n "$_LOCK_DIR" ]; then
    rmdir "$_LOCK_DIR" 2>/dev/null || true
  fi
}

acquire_lock() {
  # Register the cleanup trap BEFORE any lock-acquisition work so a signal
  # between mkdir success and a later `trap` call cannot leak the lockdir.
  # _release_lock is a no-op when _LOCK_DIR is empty, so registering early
  # in the flock branch is safe too.
  trap _release_lock EXIT
  if command -v flock >/dev/null 2>&1; then
    exec 9>"$LOCK"
    if ! flock -x -w 10 9; then
      echo "nblm-registry-helper: could not acquire flock $LOCK after 10s" >&2
      exit 3
    fi
  else
    _LOCK_DIR="${LOCK}.d"
    local tries=0
    while ! mkdir "$_LOCK_DIR" 2>/dev/null; do
      tries=$((tries + 1))
      if [ "$tries" -gt 200 ]; then
        echo "nblm-registry-helper: could not acquire $_LOCK_DIR after 10s" >&2
        exit 3
      fi
      sleep 0.05
    done
  fi
}

atomic_write() {
  local target="$1" tmp
  tmp="$(mktemp "${target}.tmp.XXXXXX")"
  if ! cat >"$tmp"; then
    rm -f "$tmp"
    return 1
  fi
  mv "$tmp" "$target"
}

ensure_registry_file() {
  if [ -f "$REG_FILE" ]; then return 0; fi
  mkdir -p "$(dirname "$REG_FILE")"
  local today
  today="$(date '+%Y-%m-%d')"
  cat >"$REG_FILE" <<EOF
---
tags: [context, notebooklm, registry]
date: ${today}
type: context
origin: k2b-generate
up: "[[index]]"
---

# NotebookLM Notebook Registry

Named NotebookLM notebooks for persistent multi-angle research. Keith reuses these across sessions to ask new questions against existing source corpora without re-indexing.

Registry rows are managed by \`scripts/nblm-registry-helper.sh\`; the block between the markers below is rewritten atomically on every write. Manual prose above and below the markers is preserved.

## Notebooks

${MARKER_START}

${HEADER}
${DIVIDER}
${EMPTY_ROW}

${MARKER_END}

## How to use

- \`/research notebook create <name> "<topic>"\` -- creates, gathers sources, registers here.
- \`/research notebook ask <name> "<question>"\` -- asks a new question against an existing notebook without re-indexing.
- \`/research notebook add-source <name> <url>\` -- adds a source to an existing notebook.
- \`/research notebook list\` -- prints this table.
- \`/research notebook remove <name>\` -- removes the registry entry.

For advanced NotebookLM operations (generate audio / mind map / infographic, save Q&A as note, share, etc.), look up the ID via \`scripts/nblm-registry-helper.sh get <name>\` and invoke the \`notebooklm\` CLI directly.
EOF
}

# ----------------------------------------------------------------------------
# Table read/write helpers. The block between MARKER_START and MARKER_END
# holds exactly: HEADER + DIVIDER + one row per notebook (or EMPTY_ROW if no
# notebooks registered). All other lines in the file are preserved verbatim.
# ----------------------------------------------------------------------------

# Emit the raw data rows (no header, no divider, no empty-row) to stdout.
# Exit 0 with empty output if the registry is empty.
read_rows() {
  [ -f "$REG_FILE" ] || return 0
  awk -v start="$MARKER_START" -v end="$MARKER_END" -v empty="$EMPTY_ROW" '
    index($0, start) { inside=1; next }
    index($0, end)   { inside=0; next }
    !inside          { next }
    /^\|---/         { next }
    /^\| Name \|/    { next }
    $0 == empty      { next }
    /^\|/            { print }
  ' "$REG_FILE"
}

# Row -> single field by 1-based index. Strips surrounding whitespace.
# Assumes pipe-separated markdown rows: |  name  |  id  |  ...  |
# awk's FS="|" yields empty first field before leading pipe and empty last
# field after trailing pipe; the nth visible cell is awk field n+1.
row_field() {
  local row="$1" idx="$2"
  printf '%s\n' "$row" | awk -F'|' -v idx="$((idx + 1))" '
    {
      val = $idx
      sub(/^[ \t]+/, "", val)
      sub(/[ \t]+$/, "", val)
      print val
    }
  '
}

row_name()        { row_field "$1" 1; }
row_id()          { row_field "$1" 2; }
row_description() { row_field "$1" 3; }
row_sources()     { row_field "$1" 4; }
row_created()     { row_field "$1" 5; }
row_last_used()   { row_field "$1" 6; }

# Escape HTML entities so user-supplied descriptions cannot corrupt the
# markdown table (pipe would break row structure) or render as live HTML
# when Obsidian previews the file (angle brackets / ampersand).
# Order matters: ampersand must go FIRST so we don't double-encode the
# entity codes we emit for the other characters.
escape_cell() {
  printf '%s' "$1" | sed 's/&/\&#38;/g; s/</\&#60;/g; s/>/\&#62;/g; s/|/\&#124;/g'
}

render_row() {
  # Args: name id description sources created last_used
  printf '| %s | %s | %s | %s | %s | %s |\n' \
    "$(escape_cell "$1")" "$(escape_cell "$2")" "$(escape_cell "$3")" \
    "$(escape_cell "$4")" "$(escape_cell "$5")" "$(escape_cell "$6")"
}

# Rewrite the registry file: preserve everything outside the markers, and
# rewrite the block between them from the current rows in ROWS_TMP (one row
# per line). If ROWS_TMP is empty, emit EMPTY_ROW.
write_rows() {
  local rows_file="$1" new_content
  new_content="$(mktemp)"
  awk -v start="$MARKER_START" -v end="$MARKER_END" \
      -v header="$HEADER" -v divider="$DIVIDER" -v empty_row="$EMPTY_ROW" \
      -v rows_file="$rows_file" '
    function emit_block(    rows_line, has_rows) {
      print header
      print divider
      has_rows = 0
      while ((getline rows_line < rows_file) > 0) {
        if (rows_line ~ /^\|/) { print rows_line; has_rows = 1 }
      }
      close(rows_file)
      if (!has_rows) print empty_row
    }
    index($0, start) { print; inside=1; next }
    index($0, end) {
      if (inside) {
        # Emit the block right before the end marker line. The marker is
        # emitted as-is afterwards. Also emit one blank line of padding.
        print ""
        emit_block()
        print ""
        print
        inside = 0
        next
      }
    }
    inside { next }   # swallow existing block contents
    { print }
  ' "$REG_FILE" >"$new_content"
  atomic_write "$REG_FILE" <"$new_content"
  rm -f "$new_content"
}

# ----------------------------------------------------------------------------
# Commands
# ----------------------------------------------------------------------------

cmd_add() {
  local name="${1:-}" id="${2:-}" desc="${3:-}"
  if [ -z "$name" ] || [ -z "$id" ]; then
    echo "add: <name> and <id> required" >&2
    exit 64
  fi
  # Validate name: kebab-case slug, 1-48 chars, alphanumeric + dash only.
  if ! printf '%s' "$name" | grep -Eq '^[a-z0-9][a-z0-9-]{0,47}$'; then
    echo "add: name must be kebab-case (a-z, 0-9, dash), 1-48 chars: $name" >&2
    exit 64
  fi
  acquire_lock
  ensure_registry_file
  local today rows_tmp existing_row
  today="$(date '+%Y-%m-%d')"
  rows_tmp="$(mktemp)"
  existing_row=""
  while IFS= read -r row; do
    [ -z "$row" ] && continue
    local rn
    rn="$(row_name "$row")"
    if [ "$rn" = "$name" ]; then
      existing_row="$row"
      continue   # drop existing, replace below
    fi
    printf '%s\n' "$row" >>"$rows_tmp"
  done < <(read_rows)

  local created sources_count last_used
  if [ -n "$existing_row" ]; then
    created="$(row_created "$existing_row")"
    sources_count="$(row_sources "$existing_row")"
    last_used="$today"
    [ -z "$desc" ] && desc="$(row_description "$existing_row")"
  else
    created="$today"
    sources_count="0"
    last_used="$today"
  fi
  render_row "$name" "$id" "$desc" "$sources_count" "$created" "$last_used" \
    >>"$rows_tmp"
  write_rows "$rows_tmp"
  rm -f "$rows_tmp"
  if [ -n "$existing_row" ]; then
    echo "nblm-registry-helper: updated $name -> $id" >&2
  else
    echo "nblm-registry-helper: registered $name -> $id" >&2
  fi
}

cmd_get() {
  local name="${1:-}"
  if [ -z "$name" ]; then
    echo "get: <name> required" >&2
    exit 64
  fi
  [ -f "$REG_FILE" ] || { echo "nblm-registry-helper: registry not found: $REG_FILE" >&2; exit 4; }
  while IFS= read -r row; do
    [ -z "$row" ] && continue
    if [ "$(row_name "$row")" = "$name" ]; then
      row_id "$row"
      return 0
    fi
  done < <(read_rows)
  echo "nblm-registry-helper: name not found: $name" >&2
  exit 4
}

cmd_list() {
  if [ ! -f "$REG_FILE" ]; then
    printf '%s\n%s\n%s\n' "$HEADER" "$DIVIDER" "$EMPTY_ROW"
    return 0
  fi
  printf '%s\n%s\n' "$HEADER" "$DIVIDER"
  local any=""
  while IFS= read -r row; do
    [ -z "$row" ] && continue
    printf '%s\n' "$row"
    any="1"
  done < <(read_rows)
  [ -z "$any" ] && printf '%s\n' "$EMPTY_ROW"
}

cmd_remove() {
  local name="${1:-}"
  if [ -z "$name" ]; then
    echo "remove: <name> required" >&2
    exit 64
  fi
  acquire_lock
  ensure_registry_file
  local rows_tmp found
  rows_tmp="$(mktemp)"
  found=""
  while IFS= read -r row; do
    [ -z "$row" ] && continue
    if [ "$(row_name "$row")" = "$name" ]; then
      found="1"
      continue
    fi
    printf '%s\n' "$row" >>"$rows_tmp"
  done < <(read_rows)
  if [ -z "$found" ]; then
    rm -f "$rows_tmp"
    echo "nblm-registry-helper: name not found: $name" >&2
    exit 4
  fi
  write_rows "$rows_tmp"
  rm -f "$rows_tmp"
  echo "nblm-registry-helper: removed $name" >&2
}

cmd_update() {
  local name="${1:-}"
  if [ -z "$name" ]; then
    echo "update: <name> required" >&2
    exit 64
  fi
  shift
  local set_desc="" set_sources="" do_touch=""
  local new_desc="" new_sources=""
  while [ $# -gt 0 ]; do
    case "$1" in
      --desc)    set_desc="1"; new_desc="${2:-}"; shift 2 ;;
      --sources) set_sources="1"; new_sources="${2:-}"; shift 2 ;;
      --touch)   do_touch="1"; shift ;;
      *)         echo "update: unknown flag: $1" >&2; exit 64 ;;
    esac
  done
  acquire_lock
  ensure_registry_file
  local rows_tmp today found
  rows_tmp="$(mktemp)"
  today="$(date '+%Y-%m-%d')"
  found=""
  while IFS= read -r row; do
    [ -z "$row" ] && continue
    if [ "$(row_name "$row")" = "$name" ]; then
      found="1"
      local rid rdesc rsrc rcreated rlast
      rid="$(row_id "$row")"
      rdesc="$(row_description "$row")"
      rsrc="$(row_sources "$row")"
      rcreated="$(row_created "$row")"
      rlast="$(row_last_used "$row")"
      [ "$set_desc" = "1" ]    && rdesc="$new_desc"
      [ "$set_sources" = "1" ] && rsrc="$new_sources"
      [ "$do_touch" = "1" ]    && rlast="$today"
      render_row "$name" "$rid" "$rdesc" "$rsrc" "$rcreated" "$rlast" >>"$rows_tmp"
      continue
    fi
    printf '%s\n' "$row" >>"$rows_tmp"
  done < <(read_rows)
  if [ -z "$found" ]; then
    rm -f "$rows_tmp"
    echo "nblm-registry-helper: name not found: $name" >&2
    exit 4
  fi
  write_rows "$rows_tmp"
  rm -f "$rows_tmp"
}

cmd_path() {
  printf '%s\n' "$REG_FILE"
}

sub="${1:-}"; shift || true
case "$sub" in
  add)       cmd_add "$@" ;;
  get)       cmd_get "$@" ;;
  list)      cmd_list "$@" ;;
  remove|rm) cmd_remove "$@" ;;
  update)    cmd_update "$@" ;;
  path)      cmd_path ;;
  -h|--help|help|"") usage ;;
  *) echo "nblm-registry-helper: unknown command: $sub" >&2; usage ;;
esac
