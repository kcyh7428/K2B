#!/usr/bin/env bash
# tests/okf-audit.test.sh
# Tests scripts/okf-audit.py against a sandbox markdown subtree.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
HELPER="$REPO_ROOT/scripts/okf-audit.py"

TMPDIR="$(mktemp -d)"
trap 'rm -rf "$TMPDIR"' EXIT

fail() { echo "FAIL: $*" >&2; exit 1; }

ROOT="$TMPDIR/wiki"
mkdir -p "$ROOT/reference" "$ROOT/concepts"

cat > "$ROOT/reference/good.md" <<'EOF'
---
type: reference
title: Good Reference
description: A complete reference page.
tags: [k2b-system]
---

# Good Reference

See [Concept](../concepts/concept-one.md).
EOF

cat > "$ROOT/concepts/concept-one.md" <<'EOF'
---
type: concept
title: Concept One
description: A complete concept page.
---

# Concept One
EOF

python3 "$HELPER" "$ROOT" > "$TMPDIR/good.out" 2>&1 || {
  cat "$TMPDIR/good.out" >&2
  fail "valid subtree should pass"
}
grep -q "okf-audit passed" "$TMPDIR/good.out" || fail "valid audit should print pass message"

cat > "$ROOT/reference/missing-type.md" <<'EOF'
---
title: Missing Type
description: This page has no type.
---

# Missing Type
EOF

set +e
python3 "$HELPER" "$ROOT" > "$TMPDIR/missing-type.out" 2>&1
rc=$?
set -e
[ "$rc" -eq 1 ] || fail "missing type should exit 1, got $rc"
grep -q "missing required type" "$TMPDIR/missing-type.out" || fail "missing type finding absent"
rm "$ROOT/reference/missing-type.md"

cat > "$ROOT/reference/warnings.md" <<'EOF'
---
type: reference
title: Warning Page
---

# Warning Page

See [[missing-target]] and [[concept-one]].
See [[concept-one|Concept One]] and [[concept-one#Details]].

```text
[[missing-in-code]]
```

Inline `[[missing-inline-code]]` should not count.

    [[missing-indented-code]]
EOF

python3 "$HELPER" "$ROOT" > "$TMPDIR/warnings.out" 2>&1 || {
  cat "$TMPDIR/warnings.out" >&2
  fail "warnings should not fail audit"
}
grep -q "missing recommended description" "$TMPDIR/warnings.out" || fail "description warning absent"
grep -q "non-portable wikilink" "$TMPDIR/warnings.out" || fail "wikilink portability warning absent"
grep -q "wikilink target not found: missing-target" "$TMPDIR/warnings.out" || fail "broken wikilink warning absent"
if grep -q "missing-in-code" "$TMPDIR/warnings.out"; then fail "code block wikilinks should be ignored"; fi
if grep -q "missing-inline-code" "$TMPDIR/warnings.out"; then fail "inline code wikilinks should be ignored"; fi
if grep -q "missing-indented-code" "$TMPDIR/warnings.out"; then fail "indented code wikilinks should be ignored"; fi
grep -q "okf-audit passed" "$TMPDIR/warnings.out" || fail "warnings-only audit should pass"

python3 - "$ROOT/reference/crlf.md" <<'PY'
from pathlib import Path
import sys

Path(sys.argv[1]).write_bytes(
    b"\xef\xbb\xbf\r\n--- \r\ntype: reference\r\ntitle: CRLF\r\ndescription: CRLF frontmatter.\r\n--- \r\n\r\n# CRLF\r\n"
)
PY
python3 "$HELPER" "$ROOT/reference/crlf.md" > "$TMPDIR/crlf.out" 2>&1 || {
  cat "$TMPDIR/crlf.out" >&2
  fail "BOM/CRLF/trailing-space frontmatter should pass"
}

cat > "$ROOT/reference/case-and-paths.md" <<'EOF'
---
type: reference
title: Case and Paths
description: Link resolution checks.
up: "[[missing-in-frontmatter]]"
---

# Case and Paths

See [[CONCEPT-ONE]], [[reference/good]], [[./good]], [[../concepts/concept-one]], and [[../outside]].
EOF

python3 "$HELPER" "$ROOT" > "$TMPDIR/links.out" 2>&1 || {
  cat "$TMPDIR/links.out" >&2
  fail "link warnings should not fail audit"
}
if grep -q "missing-in-frontmatter" "$TMPDIR/links.out"; then fail "frontmatter wikilinks should be ignored"; fi
if grep -q "wikilink target not found: CONCEPT-ONE" "$TMPDIR/links.out"; then fail "case-insensitive stem link should resolve"; fi
if grep -q "wikilink target not found: good" "$TMPDIR/links.out"; then fail "qualified existing path should resolve"; fi
if grep -q "wikilink target not found: concept-one" "$TMPDIR/links.out"; then fail "relative parent path should resolve"; fi
grep -q "wikilink target not found: outside" "$TMPDIR/links.out" || fail "escaped/missing relative path should warn"

python3 "$HELPER" "$ROOT/reference/case-and-paths.md" > "$TMPDIR/single-file.out" 2>&1 || {
  cat "$TMPDIR/single-file.out" >&2
  fail "single-file audit should pass"
}
if grep -q "wikilink target not found" "$TMPDIR/single-file.out"; then
  fail "single-file audit should not report cross-note missing targets"
fi

python3 - "$ROOT/reference/bad-utf8.md" <<'PY'
from pathlib import Path
import sys

Path(sys.argv[1]).write_bytes(b"---\ntype: reference\ntitle: Bad\ndescription: Bad.\n---\n\n\xff\n")
PY
set +e
python3 "$HELPER" "$ROOT/reference/bad-utf8.md" > "$TMPDIR/bad-utf8.out" 2>&1
rc=$?
set -e
[ "$rc" -eq 1 ] || fail "invalid UTF-8 should fail audit"
grep -q "UTF-8 decode failed" "$TMPDIR/bad-utf8.out" || fail "invalid UTF-8 error absent"

echo "okf-audit.test.sh: all tests passed"
