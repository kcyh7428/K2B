#!/usr/bin/env bash
# scripts/audit-date-handling.sh
# Advisory check (non-blocking) for the E-2026-05-20-001 bug class.
#
# Flags bare $(date '+%Y-%m-%d') calls inside scripts whose filename suggests
# they are scheduled to run on a calendar boundary (cron, daily, eod, nightly,
# morning). Such scripts that fire after HKT midnight will silently process the
# WRONG calendar day unless they use -v-1d (BSD) or --date='yesterday' (GNU).
#
# Convention: K2B-Vault/wiki/context/context_timezone-convention.md
#
# Exit codes:
#   0 - no flagged lines
#   1 - flagged lines found (caller decides whether to surface)
#   2 - usage / config error
#
# Usage:
#   scripts/audit-date-handling.sh           # human-readable report
#   scripts/audit-date-handling.sh --json    # machine-readable

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SCRIPTS_DIR="$REPO_ROOT/scripts"
FORMAT="text"

if [[ "${1:-}" == "--json" ]]; then
  FORMAT="json"
elif [[ -n "${1:-}" && "${1:-}" != "--" ]]; then
  echo "audit-date-handling: unknown arg: $1" >&2
  echo "usage: $0 [--json]" >&2
  exit 2
fi

[ -d "$SCRIPTS_DIR" ] || { echo "audit-date-handling: scripts dir not found: $SCRIPTS_DIR" >&2; exit 2; }

# Trigger filename patterns: scripts likely fired by cron/launchd on calendar boundary.
TRIGGER_RE='(cron|daily|eod|nightly|morning)'

# Find candidate script files (sh, py) whose basename matches a trigger pattern.
mapfile -t CANDIDATES < <(
  find "$SCRIPTS_DIR" -type f \( -name '*.sh' -o -name '*.py' \) \
    -print0 2>/dev/null \
    | xargs -0 -I{} bash -c '
        f="$1"
        base="$(basename "$f")"
        if [[ "$base" =~ '"$TRIGGER_RE"' ]]; then
          printf "%s\n" "$f"
        fi
      ' _ {}
)

# Findings file: one finding per line, format "<file>:<lineno>:<text>". Using a
# real file instead of a bash array makes the JSON path trivial (jq -R -s) and
# survives quoting hazards in arbitrary code lines (quotes, $, %, etc).
FINDINGS_FILE="$(mktemp "${TMPDIR:-/tmp}/k2b-audit-date.XXXXXX")"
trap 'rm -f "$FINDINGS_FILE"' EXIT

# Grep each candidate for the bug pattern:
#   $(date ... %Y-%m-%d['"]) WITHOUT -v-1d/--date='yesterday'/-d yesterday/sliding window
# Then filter: skip lines where the trimmed CODE portion (after file:line:) starts with `#`.
# DO NOT just skip any line containing "# " — that suppresses real lines with
# inline comments (e.g. RUN_DATE="$(date '+%Y-%m-%d')" # default) which is exactly
# the bug class this audit catches.
for f in "${CANDIDATES[@]}"; do
  while IFS= read -r match; do
    [ -n "$match" ] || continue
    # Grep with a single file argument outputs "<lineno>:<full-line>". Strip
    # ONLY the leading "<digits>:" so the rest is the actual code line.
    full_line="$(printf '%s\n' "$match" | sed -E 's/^[0-9]+://')"
    trimmed="$(printf '%s\n' "$full_line" | sed -E 's/^[[:space:]]+//')"
    case "$trimmed" in
      '#'*) continue ;;  # pure-comment line, skip
    esac
    # Split into code-portion / comment-portion so the "is this already a
    # yesterday call?" check evaluates the CODE only. A line like
    #   RUN_DATE="$(date '+%Y-%m-%d')" # FIXME: should be -v-1d
    # otherwise passes the -v-1d filter on the comment and the real bug hides.
    # Heuristic: split on the first occurrence of " #" (space-hash). Bash
    # comments are unambiguous when preceded by whitespace; embedded "#" in
    # strings without a leading space (e.g. printf '#') is not a comment.
    code_portion="${full_line%% #*}"
    if echo "$code_portion" | grep -qE -- "-v-1d|--date=[\"']yesterday[\"']|-d [\"']yesterday[\"']|-v-7d|-v-30d|--date=[\"']7 days ago[\"']"; then
      continue
    fi
    printf '%s\n' "$match" >> "$FINDINGS_FILE"
  # Match ONLY date-bucketing format strings (%Y-%m-%d followed by closing quote),
  # NOT full ISO log timestamps (%Y-%m-%dT%H:...). The bug class is "wrong calendar day,"
  # not "log format cosmetics."
  done < <(grep -nE "\\\$\\(date[[:space:]][^)]*%Y-%m-%d['\"]" "$f" 2>/dev/null || true)
done

FINDINGS_COUNT="$(wc -l < "$FINDINGS_FILE" | tr -d ' ')"

if [[ "$FORMAT" == "json" ]]; then
  # Emit JSON entirely in Python; pass findings via stdin so arbitrary
  # characters (quotes, backslashes, %, $) survive the boundary intact.
  python3 -c '
import json, sys
findings = [line.rstrip("\n") for line in sys.stdin if line.strip()]
print(json.dumps({"flagged_count": len(findings), "findings": findings}, indent=2))
' < "$FINDINGS_FILE"
  if [[ "$FINDINGS_COUNT" -gt 0 ]]; then exit 1; fi
  exit 0
fi

# Text format
if [[ "$FINDINGS_COUNT" -eq 0 ]]; then
  echo "audit-date-handling: OK -- no bare \$(date ...) calls flagged in cron/daily/eod/nightly/morning scripts."
  exit 0
fi

echo "audit-date-handling: $FINDINGS_COUNT flagged line(s) -- review whether each should use -v-1d for 'yesterday's data':"
echo ""
while IFS= read -r line; do
  printf '  %s\n' "$line"
done < "$FINDINGS_FILE"
echo ""
echo "Convention: wiki/context/context_timezone-convention.md (Rule 1 -- 'Yesterday's data' in HKT requires explicit -v-1d)."
exit 1
