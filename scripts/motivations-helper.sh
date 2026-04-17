#!/usr/bin/env bash
# scripts/motivations-helper.sh
# Single writer for active-motivations.md + active-questions.md.
# Owns the write procedure per the memory ownership matrix; CLAUDE.md only
# advertises intent routing.
#
# Commands:
#   add-question "text"       append to active-questions.md (dedup, flock, atomic)
#   remove-question "pattern" filter matching bullet from active-questions.md
#   sync-building             rebuild Building section of active-motivations.md
#                             from wiki/concepts/index.md In Progress + Next Up
#   read                      emit concatenated view for skills to consume
#                             (honors K2B_MOTIVATIONS_ENABLED rollback toggle)
#
# Locking: flock if available, mkdir fallback for macOS (pattern copied from
# scripts/wiki-log-append.sh).
# Writes: atomic tmp+mv. Exits non-zero on lock contention.

set -euo pipefail

VAULT="${K2B_VAULT:-$HOME/Projects/K2B-Vault}"
MOT_FILE="${K2B_MOTIVATIONS_FILE:-$VAULT/wiki/context/active-motivations.md}"
Q_FILE="${K2B_QUESTIONS_FILE:-$VAULT/wiki/context/active-questions.md}"
CONCEPTS_INDEX="${K2B_CONCEPTS_INDEX:-$VAULT/wiki/concepts/index.md}"
LOCK="${K2B_MOTIVATIONS_LOCK:-/tmp/k2b-motivations.lock}"

usage() {
  cat >&2 <<EOF
usage: $0 {add-question "text"|remove-question "pattern"|sync-building|read}
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
  if command -v flock >/dev/null 2>&1; then
    exec 9>"$LOCK"
    if ! flock -x -w 10 9; then
      echo "motivations-helper: could not acquire flock $LOCK after 10s" >&2
      exit 3
    fi
  else
    _LOCK_DIR="${LOCK}.d"
    local tries=0
    while ! mkdir "$_LOCK_DIR" 2>/dev/null; do
      tries=$((tries + 1))
      if [ "$tries" -gt 200 ]; then
        echo "motivations-helper: could not acquire $_LOCK_DIR after 10s" >&2
        exit 3
      fi
      sleep 0.05
    done
    trap _release_lock EXIT
  fi
}

atomic_write() {
  # Reads stdin, writes to $1 atomically via tmp+mv in same directory.
  local target="$1" tmp
  tmp="$(mktemp "${target}.tmp.XXXXXX")"
  if ! cat >"$tmp"; then
    rm -f "$tmp"
    return 1
  fi
  mv "$tmp" "$target"
}

strip_frontmatter() {
  awk '
    BEGIN { fm=0; seen=0 }
    /^---$/ {
      if (!seen) { fm=1; seen=1; next }
      else if (fm) { fm=0; next }
    }
    !fm
  ' "$1"
}

# Emit a distilled view of the named section (header regex) from a file:
# section header line + bullet-or-italic-placeholder lines only. Drops narrative
# paragraphs, HTML comments, and blank padding. Output is LLM-friendly motivation
# context with no skill-ownership prose.
emit_section() {
  local header_re="$1" file="$2"
  [ -f "$file" ] || return 0
  awk -v hdr="$header_re" '
    $0 ~ "^" hdr { print; inside=1; blank=0; next }
    inside && /^## / { inside=0 }
    !inside { next }
    /^<!--/ { next }
    /^[[:space:]]*$/ { blank=1; next }
    /^- / || /^\*\(/ {
      if (blank) { print ""; blank=0 }
      print
    }
  ' "$file"
}

ensure_questions_file() {
  if [ -f "$Q_FILE" ]; then return 0; fi
  mkdir -p "$(dirname "$Q_FILE")"
  cat >"$Q_FILE" <<'EOF'
---
tags: [context, motivations, keith-owned]
type: context
origin: keith
up: "[[index]]"
---

# Active Questions (Keith-maintained)

Things Keith wants to learn more about. Append-only via inline "add X to my active questions"; Keith edits directly in Obsidian to remove. Observer never writes this file.

## Questions

*(empty -- Keith adds via inline commands)*
EOF
}

cmd_add_question() {
  local text="${1:-}"
  if [ -z "$text" ]; then
    echo "add-question: text required" >&2
    exit 64
  fi
  acquire_lock
  ensure_questions_file
  local today bullet
  today="$(date '+%Y-%m-%d')"
  bullet="- ${text} *(added ${today})*"
  # Dedup: exact-text bullet already present (regardless of date)
  if grep -Fq -- "- ${text} *(added " "$Q_FILE"; then
    echo "motivations-helper: already present: ${text}" >&2
    return 0
  fi
  # If placeholder is present, replace it with the first bullet; else append.
  if grep -Fq -- "*(empty -- Keith adds via inline commands)*" "$Q_FILE"; then
    awk -v b="$bullet" '
      /\*\(empty -- Keith adds via inline commands\)\*/ { print b; next }
      { print }
    ' "$Q_FILE" | atomic_write "$Q_FILE"
  else
    awk -v b="$bullet" '
      { print }
      END { print b }
    ' "$Q_FILE" | atomic_write "$Q_FILE"
  fi
}

cmd_remove_question() {
  local pattern="${1:-}"
  if [ -z "$pattern" ]; then
    echo "remove-question: pattern required" >&2
    exit 64
  fi
  if [ ! -f "$Q_FILE" ]; then
    echo "motivations-helper: $Q_FILE not found, nothing to remove" >&2
    return 0
  fi
  acquire_lock
  # Filter any bullet line whose bullet text contains the pattern (substring).
  # Preserves frontmatter, headers, and placeholder.
  awk -v p="$pattern" '
    BEGIN { removed=0 }
    /^- / {
      # Match only bullet lines; others pass through untouched.
      if (index($0, p) > 0) { removed++; next }
    }
    { print }
    END {
      # Emit count to stderr via a side-channel isnt possible from awk cleanly;
      # skip -- caller can re-run read to verify.
    }
  ' "$Q_FILE" | atomic_write "$Q_FILE"
  # If no bullets remain under ## Questions, restore placeholder.
  if ! grep -Eq '^- ' "$Q_FILE"; then
    awk '
      BEGIN { done=0 }
      /^## Questions/ { print; in_q=1; next }
      in_q && !done && NF==0 { print; next }
      in_q && !done {
        # First non-blank non-bullet line after header: inject placeholder above it.
        # But since we stripped bullets, this line could be anything; just ensure
        # placeholder is present once.
        print "*(empty -- Keith adds via inline commands)*"
        done=1
      }
      { print }
      END {
        if (in_q && !done) print "*(empty -- Keith adds via inline commands)*"
      }
    ' "$Q_FILE" | atomic_write "$Q_FILE"
  fi
}

extract_building_from_concepts() {
  # Parse wiki/concepts/index.md In Progress + Next Up rows deterministically.
  # Emits markdown bullets, one per row, plus an explicit placeholder when
  # Next Up is empty.
  awk '
    /^## In Progress/ { section="in_progress"; next_up_count=0; next }
    /^## Next Up/    {
      if (section=="in_progress") section=""  # leaving in_progress cleanly
      section="next_up"; next_up_count=0; next
    }
    /^## / {
      if (section=="next_up" && next_up_count==0) {
        print "- *(Next Up lane empty -- promote a Backlog item on next /ship)*"
      }
      section=""
      next
    }
    section=="in_progress" && /^\| \[\[/ {
      line=$0
      start = index(line, "[[") + 2
      end   = index(line, "]]")
      name  = substr(line, start, end - start)
      n = split(line, cells, "|")
      phase = cells[3]; priority = cells[4]
      gsub(/^[ \t]+|[ \t]+$/, "", phase)
      gsub(/^[ \t]+|[ \t]+$/, "", priority)
      print "- **" name "** -- " phase " (in_progress, priority " priority ")"
    }
    section=="next_up" && /^\| \[\[/ {
      line=$0
      start = index(line, "[[") + 2
      end   = index(line, "]]")
      name  = substr(line, start, end - start)
      n = split(line, cells, "|")
      phase = cells[3]; priority = cells[4]
      gsub(/^[ \t]+|[ \t]+$/, "", phase)
      gsub(/^[ \t]+|[ \t]+$/, "", priority)
      print "- **" name "** -- " phase " (next, priority " priority ")"
      next_up_count++
    }
    END {
      if (section=="next_up" && next_up_count==0) {
        print "- *(Next Up lane empty -- promote a Backlog item on next /ship)*"
      }
    }
  ' "$CONCEPTS_INDEX"
}

extract_existing_frontmatter_field() {
  # $1 = field name, $2 = file path. Empty stdout if not found.
  awk -v want="$1" '
    BEGIN { fm=0; seen=0 }
    /^---$/ {
      if (!seen) { fm=1; seen=1; next }
      else if (fm) { fm=0; exit }
    }
    fm {
      if (match($0, "^" want ":[[:space:]]*")) {
        print substr($0, RLENGTH+1)
        exit
      }
    }
  ' "$2"
}

extract_existing_emerging_body() {
  # Emit the body of the "## Emerging Interests" section (everything after the
  # header line to EOF). If the header is missing, emits nothing.
  awk '
    /^## Emerging Interests/ { found=1; next }
    found { print }
  ' "$1"
}

cmd_sync_building() {
  if [ ! -f "$CONCEPTS_INDEX" ]; then
    echo "sync-building: concepts index not found: $CONCEPTS_INDEX" >&2
    exit 2
  fi
  acquire_lock
  mkdir -p "$(dirname "$MOT_FILE")"
  local now_iso last_observer emerging_body building_body
  now_iso="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
  last_observer="$now_iso"
  emerging_body=$'\n<!-- observer populates starting Ship 2. Empty in Ship 1. -->\n\n*(empty)*\n'
  if [ -f "$MOT_FILE" ]; then
    local existing
    existing="$(extract_existing_frontmatter_field 'last-observer-update' "$MOT_FILE")"
    if [ -n "$existing" ]; then
      last_observer="$existing"
    fi
    local existing_emerging
    existing_emerging="$(extract_existing_emerging_body "$MOT_FILE")"
    if [ -n "$existing_emerging" ]; then
      emerging_body="$existing_emerging"
    fi
  fi
  building_body="$(extract_building_from_concepts)"
  if [ -z "$building_body" ]; then
    echo "sync-building: no In Progress or Next Up rows parsed from $CONCEPTS_INDEX" >&2
    exit 2
  fi
  {
    printf -- '---\n'
    printf -- 'tags: [context, motivations, observer-owned]\n'
    printf -- 'type: context\n'
    printf -- 'origin: k2b-observer\n'
    printf -- 'up: "[[index]]"\n'
    printf -- 'last-observer-update: %s\n' "$last_observer"
    printf -- 'building-last-synced: %s\n' "$now_iso"
    printf -- '---\n\n'
    printf -- '# Active Motivations (observer-maintained)\n\n'
    printf -- "Keith's active motivations as detected from concepts/index.md and observer signals. Read by /research, /compile, /daily, NBLM prompts. For Keith's self-added questions see [[active-questions]].\n\n"
    printf -- '## Building\n\n'
    printf -- '<!-- sync-building script rewrites this section from wiki/concepts/index.md In Progress + Next Up lanes -->\n\n'
    printf -- '%s\n\n' "$building_body"
    printf -- '## Emerging Interests\n'
    printf -- '%s' "$emerging_body"
    # Ensure trailing newline
    case "$emerging_body" in
      *$'\n') ;;
      *) printf -- '\n' ;;
    esac
  } | atomic_write "$MOT_FILE"
}

cmd_read() {
  if [[ "${K2B_MOTIVATIONS_ENABLED:-true}" != "true" ]]; then
    return 0
  fi
  local out
  out=$(
    emit_section '## Building' "$MOT_FILE"
    emit_section '## Emerging Interests' "$MOT_FILE"
    emit_section '## Questions' "$Q_FILE"
  )
  [ -n "$out" ] && printf '%s\n' "$out"
}

sub="${1:-}"; shift || true
case "$sub" in
  add-question)    cmd_add_question "$@" ;;
  remove-question) cmd_remove_question "$@" ;;
  sync-building)   cmd_sync_building "$@" ;;
  read)            cmd_read "$@" ;;
  -h|--help|help|"") usage ;;
  *) echo "motivations-helper: unknown command: $sub" >&2; usage ;;
esac
