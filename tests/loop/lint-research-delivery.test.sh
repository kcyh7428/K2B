#!/usr/bin/env bash
# Smoke test for the research-without-delivery-link lint check.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

mkdir -p "$TMP/raw/research"

old_date=$(date -v-60d '+%Y-%m-%d')
cat > "$TMP/raw/research/${old_date}_research_old.md" <<EOF
---
type: research
date: ${old_date}
follow-up-delivery: null
---
# old
EOF

new_date=$(date -v-3d '+%Y-%m-%d')
cat > "$TMP/raw/research/${new_date}_research_fresh.md" <<EOF
---
type: research
date: ${new_date}
follow-up-delivery: null
---
# fresh
EOF

cat > "$TMP/raw/research/${old_date}_research_linked.md" <<EOF
---
type: research
date: ${old_date}
follow-up-delivery: feature_foo
---
# linked
EOF

export K2B_LINT_RESEARCH_DIR="$TMP/raw/research"
out="$("$ROOT/scripts/loop/lint-research-delivery.sh")"

if ! echo "$out" | grep -q "${old_date}_research_old.md"; then
  echo "FAIL: old stale note not flagged"
  exit 1
fi
if echo "$out" | grep -q "${new_date}_research_fresh.md"; then
  echo "FAIL: fresh note was flagged"
  exit 1
fi
if echo "$out" | grep -q "${old_date}_research_linked.md"; then
  echo "FAIL: linked note was flagged"
  exit 1
fi
echo "PASS: lint-research-delivery.test.sh"
