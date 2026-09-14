#!/usr/bin/env bash
# tests/vault-note-authority.test.sh
# Reference + CLI routing checks for the simple ordinary-note save path.
# No provider, SSH, Syncthing API or network surface is touched; the CLI
# cases run against a synthetic vault under mktemp.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CLI="$REPO_ROOT/scripts/vault-note-write.py"
VAULT_WRITER_SKILL="$REPO_ROOT/.agents/skills/k2b-vault-writer/SKILL.md"
SYNC_SKILL="$REPO_ROOT/.agents/skills/k2b-sync/SKILL.md"

TMPROOT="$(mktemp -d)"
cleanup() { rm -rf "$TMPROOT"; }
trap cleanup EXIT

fail() { echo "FAIL: $*" >&2; exit 1; }

echo "=== vault-note-authority.test.sh ==="

# --- documentation pressure cases (regression-first routing) --------------

grep -q "vault-note-write.py" "$VAULT_WRITER_SKILL" \
  || fail "k2b-vault-writer must route ordinary saves through scripts/vault-note-write.py"
grep -qi "offline\|local writer" "$VAULT_WRITER_SKILL" \
  || fail "k2b-vault-writer must document the SJM offline/urgent local-writer route"
grep -q "preserve both versions\|both versions" "$SYNC_SKILL" \
  || fail "k2b-sync must document that a conflict preserves both versions"
grep -q "shared hub" "$SYNC_SKILL" \
  || fail "k2b-sync must keep Home as the sole shared-hub writer"
echo "PASS: routing documentation present"

# --- CLI: policy/control paths stay disallowed ----------------------------

root="$TMPROOT/policy"
mkdir -p "$root/vault/wiki/context" "$root/state" "$root/locks" "$root/drafts"
cat > "$root/vault/wiki/context/policy-ledger.jsonl" <<'EOF'
{"scope":"*","rule":"fixture"}
EOF
cat > "$root/drafts/policy.md" <<'EOF'
---
tags: [context]
date: 2026-09-14
type: context
origin: keith
up: "[[MOC_K2B-System]]"
---
# Forged Policy

attempted policy write
EOF
out=$(K2B_VAULT_PATH="$root/vault" \
      K2B_LOCAL_STATE="$root/state" \
      K2B_NOTE_WRITE_LOCK="$root/locks/note.lock.d" \
      K2B_COMPILE_INDEX_LOCK="$root/locks/compile.lock.d" \
      python3 "$CLI" write \
        --path "wiki/context/context_forged-policy.md" \
        --content-file "$root/drafts/policy.md" \
        --expected-sha256 missing \
        --summary "forged policy" \
        --source-ref "authority-test" 2>&1) && rc=0 || rc=$?
[ "$rc" -eq 1 ] || fail "policy-path write must be refused with exit 1, got $rc: $out"
[ ! -e "$root/vault/wiki/context/context_forged-policy.md" ] \
  || fail "policy-path write must not create the note"
echo "PASS: policy/control path refused"

# --- CLI: actual conflict preserves both versions -------------------------

root="$TMPROOT/conflict"
mkdir -p "$root/vault/wiki/work" "$root/state" "$root/locks" "$root/drafts"
cat > "$root/vault/wiki/work/index.md" <<'EOF'
---
tags: [index, wiki]
---
# Wiki Work Index

Last updated: 2026-09-01 | Entries: 1

| Page | Status | Summary | Updated |
|------|--------|---------|---------|
| [[work_kingdee-hris-recovery]] | active | Kingdee HRIS recovery | 2026-09-01 |
EOF
cat > "$root/vault/wiki/index.md" <<'EOF'
# K2B Wiki -- Master Index

## Subfolders

| Folder | Purpose | Entries |
|--------|---------|---------|
| [work/](work/index.md) | Work pages | 1 |

**Total wiki pages: 1**
EOF
cat > "$root/vault/wiki/work/work_kingdee-hris-recovery.md" <<'EOF'
---
tags: [work]
date: 2026-09-01
type: work
origin: keith
up: "[[MOC_SJM-Work]]"
---
# Kingdee HRIS Recovery

original local version
EOF
cat > "$root/vault/wiki/work/work_kingdee-hris-recovery.sync-conflict-20260914-120000.md" <<'EOF'
---
tags: [work]
date: 2026-09-14
type: work
origin: keith
up: "[[MOC_SJM-Work]]"
---
# Kingdee HRIS Recovery

arrived counterpart version
EOF
current_hash=$(shasum -a 256 "$root/vault/wiki/work/work_kingdee-hris-recovery.md" | awk '{print $1}')
cat > "$root/drafts/update.md" <<'EOF'
---
tags: [work]
date: 2026-09-14
type: work
origin: keith
up: "[[MOC_SJM-Work]]"
---
# Kingdee HRIS Recovery

attempted overwrite
EOF
out=$(K2B_VAULT_PATH="$root/vault" \
      K2B_LOCAL_STATE="$root/state" \
      K2B_NOTE_WRITE_LOCK="$root/locks/note.lock.d" \
      K2B_COMPILE_INDEX_LOCK="$root/locks/compile.lock.d" \
      python3 "$CLI" write \
        --path "wiki/work/work_kingdee-hris-recovery.md" \
        --content-file "$root/drafts/update.md" \
        --expected-sha256 "$current_hash" \
        --summary "attempted overwrite" \
        --source-ref "authority-test" 2>&1) && rc=0 || rc=$?
[ "$rc" -eq 2 ] || fail "conflict-copy write must be refused with exit 2, got $rc: $out"
grep -q "original local version" "$root/vault/wiki/work/work_kingdee-hris-recovery.md" \
  || fail "target must be unchanged when a conflict copy exists"
grep -q "arrived counterpart version" \
  "$root/vault/wiki/work/work_kingdee-hris-recovery.sync-conflict-20260914-120000.md" \
  || fail "conflict copy must be preserved untouched"
echo "PASS: actual conflict preserves both versions"

# --- CLI: conflicts subcommand is provider-free and bounded ---------------

out=$(K2B_VAULT_PATH="$root/vault" python3 "$CLI" conflicts)
line_count=$(printf '%s\n' "$out" | sed '/^$/d' | wc -l | tr -d ' ')
[ "$line_count" -le 10 ] || fail "conflicts output must be bounded, got $line_count lines"
printf '%s' "$out" | grep -q "work_kingdee-hris-recovery.sync-conflict-" \
  || fail "conflicts must list the known conflict path"
echo "PASS: conflicts subcommand lists bounded path summaries"

# --- CLI: unattended SJM shared-memory reconciliation stays disallowed ----

# The writer only accepts ordinary wiki note paths; a shared-memory/control
# write from SJM has no route through this tool.
out=$(K2B_VAULT_PATH="$root/vault" \
      K2B_LOCAL_STATE="$root/state" \
      K2B_NOTE_WRITE_LOCK="$root/locks/note.lock.d" \
      K2B_COMPILE_INDEX_LOCK="$root/locks/compile.lock.d" \
      python3 "$CLI" write \
        --path "System/memory/active_rules.md" \
        --content-file "$root/drafts/update.md" \
        --expected-sha256 missing \
        --summary "unattended reconciliation" \
        --source-ref "authority-test" 2>&1) && rc=0 || rc=$?
[ "$rc" -eq 1 ] || fail "shared-memory path must be refused with exit 1, got $rc: $out"
echo "PASS: unattended shared-memory reconciliation has no route"

echo "vault-note-authority: PASS"
