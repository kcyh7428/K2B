#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
TOOL="$ROOT/scripts/k2b-shared-append.py"

vault="$TMP/vault"
pending="$TMP/local"

K2B_CAPTURE_WRITER_ROLE=sjm-source-only K2B_VAULT_PATH="$vault" \
  K2B_LOCAL_PENDING_ROOT="$pending" \
  python3 "$TOOL" usage --skill k2b-test --summary "source run" >/dev/null
test ! -e "$vault"
test -s "$pending/pending-usage-records.jsonl"
python3 - "$pending/pending-usage-records.jsonl" <<'PY'
import json, sys
record = json.loads(open(sys.argv[1], encoding="utf-8").read())
assert record["hub"] == "usage" and record["skill"] == "k2b-test"
PY

K2B_CAPTURE_WRITER_ROLE=home K2B_VAULT_PATH="$vault" \
  python3 "$TOOL" usage --skill k2b-test --summary "home run" >/dev/null
grep -Fq $'\tk2b-test\t' "$vault/wiki/context/skill-usage-log.tsv"

K2B_CAPTURE_WRITER_ROLE=sjm-source-only K2B_VAULT_PATH="$vault" \
  K2B_LOCAL_PENDING_ROOT="$pending" \
  python3 "$TOOL" preference --file note.md --source-skill k2b-review \
    --type idea --action archive --days-in-inbox 2 --has-feedback true \
    --feedback concise >/dev/null
test -s "$pending/pending-preference-records.jsonl"
test ! -e "$vault/wiki/context/preference-signals.jsonl"

unsafe_vault="$TMP/unsafe-vault"
mkdir -p "$unsafe_vault/wiki/context"
ln -s "$TMP/symlink-target" "$unsafe_vault/wiki/context/skill-usage-log.tsv"
set +e
K2B_CAPTURE_WRITER_ROLE=home K2B_VAULT_PATH="$unsafe_vault" \
  python3 "$TOOL" usage --skill k2b-test --summary bad \
  >/dev/null 2>&1
rc=$?
set -e
test "$rc" -ne 0

echo "PASS: shared append routing"
