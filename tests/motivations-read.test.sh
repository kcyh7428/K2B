#!/usr/bin/env bash
# Tests for motivations-helper.sh read: current Building from the concepts
# index, explicit questions unchanged, observer-timestamped Emerging
# Interests freshness, everything write-free.

set -uo pipefail
PATH=/usr/local/bin:$PATH

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
HELPER="$REPO_ROOT/scripts/motivations-helper.sh"

TMP_ROOT="$(mktemp -d)"
trap 'rm -rf "$TMP_ROOT"' EXIT

fail() { echo "FAIL: $*" >&2; exit 1; }

TODAY="2026-09-13"
FRESH_TS="2026-09-10T09:00:00Z"   # 3 days old
BOUNDARY_TS="2026-08-14T09:00:00Z" # exactly 30 days old
STALE_TS="2026-08-13T09:00:00Z"   # 31 days old
FUTURE_TS="2026-09-20T09:00:00Z"

hash_fixture() {
  shasum -a 256 "$1" | awk '{print $1}'
}

write_index() {
  cat > "$1" <<'EOF'
# Wiki Concepts Index

## In Progress

| Page | Phase | Priority | Updated |
|------|-------|----------|---------|
| [[feature_current-project]] | build | 1 | 2026-09-13 |

## Next Up

| Page | Why | Updated |
|------|-----|---------|

## Backlog

| Page | Why | Updated |
|------|-----|---------|
EOF
}

write_motivations() {
  # $1 = file, $2 = last-observer-update value
  cat > "$1" <<EOF
---
tags: [context, motivations, observer-owned]
type: context
origin: k2b-observer
up: "[[index]]"
last-observer-update: $2
building-last-synced: 2026-07-27T00:00:00Z
---

# Active Motivations (observer-maintained)

## Building

<!-- sync-building script rewrites this section from wiki/concepts/index.md In Progress + Next Up lanes -->

- **feature_retired-mini-plan** -- shipped (in_progress, priority 1)

## Emerging Interests

<!-- observer populates starting Ship 2. Empty in Ship 1. -->

- Inferred interest alpha (first seen 2026-01-05)
- Inferred interest beta
  continued prose line about beta
EOF
}

write_questions() {
  cat > "$1" <<'EOF'
---
tags: [context, motivations, keith-owned]
type: context
origin: keith
up: "[[index]]"
---

# Active Questions (Keith-maintained)

## Questions

- KEITH_QUESTION *(added 2026-09-01)*
EOF
}

setup_case() {
  local tmp="$1" ts="$2"
  mkdir -p "$tmp/wiki/concepts" "$tmp/wiki/context"
  write_index "$tmp/wiki/concepts/index.md"
  write_motivations "$tmp/wiki/context/active-motivations.md" "$ts"
  write_questions "$tmp/wiki/context/active-questions.md"
}

read_motivations() {
  local tmp="$1"; shift
  K2B_MOTIVATIONS_FILE="$tmp/wiki/context/active-motivations.md" \
  K2B_QUESTIONS_FILE="$tmp/wiki/context/active-questions.md" \
  K2B_CONCEPTS_INDEX="$tmp/wiki/concepts/index.md" \
  K2B_MOTIVATIONS_TODAY="$TODAY" \
    bash "$HELPER" read "$@"
}

test_read_section_freshness_gates_all_inferred_content() {
  local tmp out
  tmp="$(mktemp -d "$TMP_ROOT/case.XXXXXX")"
  setup_case "$tmp" "$FRESH_TS"
  local h_index h_mot h_q
  h_index="$(hash_fixture "$tmp/wiki/concepts/index.md")"
  h_mot="$(hash_fixture "$tmp/wiki/context/active-motivations.md")"
  h_q="$(hash_fixture "$tmp/wiki/context/active-questions.md")"

  out="$(read_motivations "$tmp")"

  echo "$out" | grep -q "feature_current-project" || fail "Building must come from the current concepts index"
  echo "$out" | grep -q "KEITH_QUESTION" || fail "explicit questions must be preserved"
  echo "$out" | grep -q "Inferred interest alpha" || fail "fresh section keeps inferred bullets"
  echo "$out" | grep -q "continued prose line about beta" || fail "continuation lines are inferred content and stay with a fresh section"
  if echo "$out" | grep -q "feature_retired-mini-plan"; then
    fail "cached Building prose must never be used"
  fi
  echo "$out" | grep -qi "not live deployment truth" || fail "Building must be labelled as index-derived, not deployment truth"

  [ "$(hash_fixture "$tmp/wiki/concepts/index.md")" = "$h_index" ] || fail "index was written"
  [ "$(hash_fixture "$tmp/wiki/context/active-motivations.md")" = "$h_mot" ] || fail "motivations file was written"
  [ "$(hash_fixture "$tmp/wiki/context/active-questions.md")" = "$h_q" ] || fail "questions file was written"
  echo "PASS: test_read_section_freshness_gates_all_inferred_content"
}

test_read_unrelated_dates_never_grant_freshness() {
  local tmp out
  tmp="$(mktemp -d "$TMP_ROOT/case.XXXXXX")"
  # Section observer timestamp is stale; a bullet mentions a fresh-looking
  # content date. The unrelated date must not rescue the interest.
  setup_case "$tmp" "$STALE_TS"
  cat >> "$tmp/wiki/context/active-motivations.md" <<'EOF'
- Breaking news interest (happening 2026-09-12)
EOF
  out="$(read_motivations "$tmp")"
  if echo "$out" | grep -q "Inferred interest alpha"; then
    fail "stale observer section must omit inferred bullets"
  fi
  if echo "$out" | grep -q "Breaking news interest"; then
    fail "an unrelated date inside an interest must not override the expired observer timestamp"
  fi
  if echo "$out" | grep -q "continued prose line about beta"; then
    fail "stale observer section must omit continuation prose too"
  fi
  echo "$out" | grep -qi "not current" || fail "stale omission should be labelled"
  echo "PASS: test_read_unrelated_dates_never_grant_freshness"
}

test_read_section_timestamp_boundaries_and_invalid_values() {
  local tmp out
  tmp="$(mktemp -d "$TMP_ROOT/case.XXXXXX")"

  # Exactly 30 days old: kept.
  setup_case "$tmp" "$BOUNDARY_TS"
  out="$(read_motivations "$tmp")"
  echo "$out" | grep -q "Inferred interest alpha" || fail "exact 30-day boundary section should be kept"

  # 31 days old: omitted.
  setup_case "$tmp" "$STALE_TS"
  out="$(read_motivations "$tmp")"
  if echo "$out" | grep -q "Inferred interest alpha"; then
    fail "31-day-old observer section must be omitted"
  fi

  # Future timestamp: omitted.
  setup_case "$tmp" "$FUTURE_TS"
  out="$(read_motivations "$tmp")"
  if echo "$out" | grep -q "Inferred interest alpha"; then
    fail "future-dated observer section must be omitted"
  fi

  # Missing timestamp: omitted with a note.
  setup_case "$tmp" ""
  out="$(read_motivations "$tmp")"
  if echo "$out" | grep -q "Inferred interest alpha"; then
    fail "missing observer timestamp must omit inferred content"
  fi
  echo "$out" | grep -qi "missing or invalid" || fail "missing timestamp omission should be labelled"

  # Invalid timestamp: omitted with a note.
  setup_case "$tmp" "not-a-date"
  out="$(read_motivations "$tmp")"
  if echo "$out" | grep -q "Inferred interest alpha"; then
    fail "invalid observer timestamp must omit inferred content"
  fi
  echo "$out" | grep -qi "missing or invalid" || fail "invalid timestamp omission should be labelled"

  # Leap-impossible calendar date: omitted.
  setup_case "$tmp" "2026-02-31T09:00:00Z"
  out="$(read_motivations "$tmp")"
  if echo "$out" | grep -q "Inferred interest alpha"; then
    fail "impossible calendar date must be rejected, not normalized"
  fi
  echo "PASS: test_read_section_timestamp_boundaries_and_invalid_values"
}

test_read_emerging_section_stops_at_next_heading() {
  local tmp out
  tmp="$(mktemp -d "$TMP_ROOT/case.XXXXXX")"
  setup_case "$tmp" "$FRESH_TS"
  cat >> "$tmp/wiki/context/active-motivations.md" <<'EOF'

## Later Notes

- LEAKED_LINE from a later section
EOF
  out="$(read_motivations "$tmp")"
  if echo "$out" | grep -q "LEAKED_LINE"; then
    fail "content from a later section must not leak into the Emerging Interests view"
  fi
  echo "PASS: test_read_emerging_section_stops_at_next_heading"
}

test_read_missing_index_and_questions_stay_truthful() {
  local tmp out
  tmp="$(mktemp -d "$TMP_ROOT/case.XXXXXX")"
  setup_case "$tmp" "$FRESH_TS"
  rm "$tmp/wiki/concepts/index.md" "$tmp/wiki/context/active-questions.md"

  out="$(read_motivations "$tmp")"
  if echo "$out" | grep -q "feature_retired-mini-plan"; then
    fail "missing index must not fall back to cached Building"
  fi
  echo "$out" | grep -qi "Building unavailable" || fail "missing index must produce an unavailable note"
  if echo "$out" | grep -q "## Questions"; then
    fail "missing questions file must not emit a Questions section"
  fi
  echo "PASS: test_read_missing_index_and_questions_stay_truthful"
}

test_read_disabled_toggle_and_strict_date_override() {
  local tmp out rc
  tmp="$(mktemp -d "$TMP_ROOT/case.XXXXXX")"
  setup_case "$tmp" "$FRESH_TS"

  out="$(
    K2B_MOTIVATIONS_FILE="$tmp/wiki/context/active-motivations.md" \
    K2B_QUESTIONS_FILE="$tmp/wiki/context/active-questions.md" \
    K2B_CONCEPTS_INDEX="$tmp/wiki/concepts/index.md" \
    K2B_MOTIVATIONS_TODAY="$TODAY" \
    K2B_MOTIVATIONS_ENABLED=false \
      bash "$HELPER" read
  )"
  [ -z "$out" ] || fail "disabled toggle must emit no content"

  for bad in "not-a-date" "2026-02-31" "2026-09-14garbage"; do
    set +e
    out="$(
      K2B_MOTIVATIONS_FILE="$tmp/wiki/context/active-motivations.md" \
      K2B_QUESTIONS_FILE="$tmp/wiki/context/active-questions.md" \
      K2B_CONCEPTS_INDEX="$tmp/wiki/concepts/index.md" \
      K2B_MOTIVATIONS_TODAY="$bad" \
        bash "$HELPER" read 2>"$tmp/stderr.log"
    )"
    rc=$?
    set -e
    [ "$rc" -ne 0 ] || fail "invalid K2B_MOTIVATIONS_TODAY ($bad) must exit nonzero"
    [ -z "$out" ] || fail "invalid date override ($bad) must emit no output"
    grep -q "K2B_MOTIVATIONS_TODAY" "$tmp/stderr.log" || fail "invalid override ($bad) must print a short diagnostic"
  done
  echo "PASS: test_read_disabled_toggle_and_strict_date_override"
}

test_read_explicit_questions_preserved_verbatim() {
  local tmp out
  tmp="$(mktemp -d "$TMP_ROOT/case.XXXXXX")"
  setup_case "$tmp" "$STALE_TS"
  cat > "$tmp/wiki/context/active-questions.md" <<'EOF'
---
tags: [context, motivations, keith-owned]
type: context
origin: keith
up: "[[index]]"
---

# Active Questions (Keith-maintained)

## Questions

- UNDATED_KEITH_QUESTION
- OLD_KEITH_QUESTION *(added 2026-01-01)*
EOF
  out="$(read_motivations "$tmp")"
  echo "$out" | grep -q "UNDATED_KEITH_QUESTION" || fail "undated explicit question must be preserved"
  echo "$out" | grep -q "OLD_KEITH_QUESTION" || fail "old explicit question must not expire"
  if echo "$out" | grep -q "Inferred interest alpha"; then
    fail "stale observer content must be omitted even when questions exist"
  fi
  echo "PASS: test_read_explicit_questions_preserved_verbatim"
}

test_read_observer_ts_full_timestamp_validation() {
  local tmp out
  tmp="$(mktemp -d "$TMP_ROOT/case.XXXXXX")"

  # Same-day garbage after T must be rejected, not stripped into a valid day
  # (this is the observed bug: 2026-09-13Tgarbage read as fresh).
  setup_case "$tmp" "2026-09-13Tgarbage"
  out="$(read_motivations "$tmp")"
  if echo "$out" | grep -q "Inferred interest alpha"; then
    fail "trailing garbage in observer timestamp must not be stripped at T"
  fi
  echo "$out" | grep -qi "missing or invalid" || fail "garbage observer timestamp omission should be labelled"

  # Invalid time of day.
  setup_case "$tmp" "2026-09-10T25:00:00Z"
  out="$(read_motivations "$tmp")"
  if echo "$out" | grep -q "Inferred interest alpha"; then
    fail "invalid observer time of day must omit inferred content"
  fi

  # Invalid UTC offset.
  setup_case "$tmp" "2026-09-10T09:00:00+08:60"
  out="$(read_motivations "$tmp")"
  if echo "$out" | grep -q "Inferred interest alpha"; then
    fail "invalid observer offset must omit inferred content"
  fi

  # Trailing junk after an otherwise valid timestamp.
  setup_case "$tmp" "2026-09-10T09:00:00Zjunk"
  out="$(read_motivations "$tmp")"
  if echo "$out" | grep -q "Inferred interest alpha"; then
    fail "trailing junk after a valid timestamp must omit inferred content"
  fi

  # Date-only and timezone-naive values are not observer timestamps.
  setup_case "$tmp" "2026-09-10"
  out="$(read_motivations "$tmp")"
  if echo "$out" | grep -q "Inferred interest alpha"; then
    fail "date-only observer value must omit inferred content"
  fi
  setup_case "$tmp" "2026-09-10T09:00:00"
  out="$(read_motivations "$tmp")"
  if echo "$out" | grep -q "Inferred interest alpha"; then
    fail "timezone-naive observer timestamp must omit inferred content"
  fi
  echo "PASS: test_read_observer_ts_full_timestamp_validation"
}

test_read_observer_ts_timezone_offset_normalizes_to_utc() {
  local tmp out
  tmp="$(mktemp -d "$TMP_ROOT/case.XXXXXX")"

  # 2026-08-14T08:00:00+08:00 is 2026-08-14T00:00:00Z: exactly 30 days old.
  setup_case "$tmp" "2026-08-14T08:00:00+08:00"
  out="$(read_motivations "$tmp")"
  echo "$out" | grep -q "Inferred interest alpha" || fail "offset timestamp at the exact 30-day UTC boundary should be kept"

  # One minute earlier local is 2026-08-13T23:59:00Z: 31 days old.
  setup_case "$tmp" "2026-08-14T07:59:00+08:00"
  out="$(read_motivations "$tmp")"
  if echo "$out" | grep -q "Inferred interest alpha"; then
    fail "offset timestamp normalizes to UTC and must not use the local calendar day"
  fi
  echo "PASS: test_read_observer_ts_timezone_offset_normalizes_to_utc"
}

test_read_section_freshness_gates_all_inferred_content
test_read_unrelated_dates_never_grant_freshness
test_read_section_timestamp_boundaries_and_invalid_values
test_read_emerging_section_stops_at_next_heading
test_read_missing_index_and_questions_stay_truthful
test_read_disabled_toggle_and_strict_date_override
test_read_explicit_questions_preserved_verbatim
test_read_observer_ts_full_timestamp_validation
test_read_observer_ts_timezone_offset_normalizes_to_utc
