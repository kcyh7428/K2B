#!/usr/bin/env bash
# tests/raw-needs-compile.test.sh
# Tests scripts/raw-needs-compile.py against a sandbox vault.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
HELPER="$REPO_ROOT/scripts/raw-needs-compile.py"

TMPDIR="$(mktemp -d)"
trap 'rm -rf "$TMPDIR"' EXIT

fail() { echo "FAIL: $*" >&2; exit 1; }

VAULT="$TMPDIR/K2B-Vault"
mkdir -p "$VAULT/raw/tldrs" "$VAULT/raw/research" "$VAULT/raw/youtube"

write_note() {
  local path="$1"
  local compiled_line="$2"
  mkdir -p "$(dirname "$path")"
  cat > "$path" <<EOF
---
tags: [test]
date: 2026-07-01
type: tldr
origin: k2b-extract
${compiled_line}
---

# Test Note

Body.
EOF
}

write_note "$VAULT/raw/tldrs/missing.md" ""
write_note "$VAULT/raw/tldrs/false.md" "compiled: false"
write_note "$VAULT/raw/tldrs/empty.md" "compiled:"
write_note "$VAULT/raw/tldrs/true.md" "compiled: true"
write_note "$VAULT/raw/tldrs/skipped.md" "compiled: skipped"
write_note "$VAULT/raw/research/recent.md" "compiled: false"
write_note "$VAULT/raw/youtube/video.sync-conflict-20260704-120000-ABCD.md" "compiled: false"
mkdir -p "$VAULT/raw/.obsidian"
write_note "$VAULT/raw/.obsidian/tooling.md" "compiled: false"
cat > "$VAULT/raw/tldrs/index.md" <<'EOF'
# Index
EOF
python3 - "$VAULT/raw/tldrs/bom-crlf-true.md" <<'PY'
from pathlib import Path
import sys

Path(sys.argv[1]).write_bytes(
    b"\xef\xbb\xbf\r\n---\r\ntags: [test]\r\ndate: 2026-07-01\r\ntype: tldr\r\norigin: k2b-extract\r\ncompiled: true\r\n---\r\n\r\n# BOM CRLF\r\n"
)
PY
cat > "$TMPDIR/outside.md" <<'EOF'
---
tags: [test]
date: 2026-07-01
type: tldr
origin: k2b-extract
compiled: false
---

# Outside
EOF
ln -s "$TMPDIR/outside.md" "$VAULT/raw/tldrs/symlink.md"

python3 - "$VAULT" <<'PY'
from pathlib import Path
import os
import time
import sys

vault = Path(sys.argv[1])
old = time.time() - 49 * 3600
recent = time.time()
for path in vault.glob("raw/**/*.md"):
    os.utime(path, (old, old))
os.utime(vault / "raw/research/recent.md", (recent, recent))
PY

out="$(
  K2B_VAULT_ROOT="$VAULT" \
    python3 "$HELPER" --format markdown --min-age-hours 24 --max 10
)"

echo "$out" | grep -q 'raw/tldrs/empty.md' || fail "compiled empty should be listed"
echo "$out" | grep -q 'raw/tldrs/false.md' || fail "compiled false should be listed"
echo "$out" | grep -q 'raw/tldrs/missing.md' || fail "missing compiled should be listed"
echo "$out" | grep -q '/compile raw/tldrs/false.md' || fail "markdown output should include compile command"
if echo "$out" | grep -q 'true.md'; then fail "compiled true should be excluded"; fi
if echo "$out" | grep -q 'skipped.md'; then fail "compiled skipped should be excluded"; fi
if echo "$out" | grep -q 'bom-crlf-true.md'; then fail "BOM/CRLF compiled true should be excluded"; fi
if echo "$out" | grep -q 'index.md'; then fail "index.md should be excluded"; fi
if echo "$out" | grep -q 'sync-conflict'; then fail "sync-conflict files should be excluded"; fi
if echo "$out" | grep -q 'recent.md'; then fail "recent file should be excluded by min age"; fi
if echo "$out" | grep -q 'tooling.md'; then fail "hidden/tooling directories should be excluded"; fi
if echo "$out" | grep -q 'symlink.md'; then fail "symlinked files should be excluded"; fi

json="$(
  K2B_VAULT_ROOT="$VAULT" \
    python3 "$HELPER" --format json --min-age-hours 24 --max 2
)"
python3 - "$json" <<'PY'
import json
import sys

items = json.loads(sys.argv[1])
assert len(items) == 2, items
assert all(item["path"].startswith("raw/") for item in items), items
assert all(item["action"].startswith("/compile ") for item in items), items
PY

echo "raw-needs-compile.test.sh: all tests passed"
