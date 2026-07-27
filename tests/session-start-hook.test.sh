#!/usr/bin/env bash
# tests/session-start-hook.test.sh
# Verifies provider-neutral path handling in scripts/hooks/session-start.sh.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SCRIPT="$REPO_ROOT/scripts/hooks/session-start.sh"

TMP_PARENT="$(mktemp -d)"
cleanup() {
  [ -n "${TMP_PARENT:-}" ] && [ -d "$TMP_PARENT" ] && rm -rf "$TMP_PARENT"
}
trap cleanup EXIT

mktmp() {
  mktemp -d "$TMP_PARENT/case.XXXXXX"
}

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

run_hook() {
  local home_dir="$1" vault="$2"
  env -u CLAUDE_PROJECT_DIR \
    HOME="$home_dir" \
    K2B_PROJECT_ROOT="$REPO_ROOT" \
    K2B_VAULT_PATH="$vault" \
    bash "$SCRIPT"
}

echo "=== session-start-hook.test.sh ==="

root="$(mktmp)"
home_dir="$root/home"
vault="$root/vault"
mkdir -p "$home_dir/.codex/memories" "$vault/System/memory" "$vault/wiki/context" "$vault/review" "$vault/raw/research"
cat > "$vault/System/memory/active_rules.md" <<'EOF'
# Active Rules

VAULT RULE TOKEN
EOF
cat > "$home_dir/.codex/memories/active_rules.md" <<'EOF'
# Active Rules

DOTFILE RULE TOKEN
EOF
cat > "$vault/System/memory/self_improve_learnings.md" <<'EOF'
### L-2026-06-14-001
- **Area:** test
- **Learning:** VAULT LEARNING TOKEN
- **Reinforced:** 2
EOF

out="$(run_hook "$home_dir" "$vault")"
[[ "$out" == *"VAULT RULE TOKEN"* ]] || fail "vault active_rules.md was not loaded"
[[ "$out" != *"DOTFILE RULE TOKEN"* ]] || fail "dotfile active_rules.md should not override vault memory"
[[ "$out" == *"VAULT LEARNING TOKEN"* ]] || fail "vault self_improve_learnings.md was not loaded"
echo "PASS: vault memory preferred"

root="$(mktmp)"
home_dir="$root/home"
vault="$root/vault"
mkdir -p "$home_dir" "$vault/wiki/context" "$vault/review" "$vault/raw/research"
out="$(run_hook "$home_dir" "$vault")"
[[ "$out" != *"ACTIVE RULES"* ]] || fail "empty memory should not print active rules"
[[ "$out" == *"vault memory directory is missing"* ]] || fail "missing vault memory dir should warn"
echo "PASS: empty memory exits cleanly"

root="$(mktmp)"
home_dir="$root/home"
vault="$root/vault"
mkdir -p "$home_dir/.codex/memories" "$vault/System/memory" "$vault/wiki/context" "$vault/review" "$vault/raw/research"
cat > "$vault/System/memory/active_rules.md" <<'EOF'
# Active Rules

PARTIAL VAULT RULE TOKEN
EOF
cat > "$home_dir/.codex/memories/active_rules.md" <<'EOF'
# Active Rules

STALE DOTFILE RULE TOKEN
EOF
out="$(run_hook "$home_dir" "$vault")"
[[ "$out" == *"PARTIAL VAULT RULE TOKEN"* ]] || fail "partial vault active_rules.md was not loaded"
[[ "$out" != *"STALE DOTFILE RULE TOKEN"* ]] || fail "existing vault memory dir should suppress dotfile fallback"
echo "PASS: partial vault memory suppresses dotfile fallback"

root="$(mktmp)"
home_dir="$root/home"
vault="$root/missing-vault"
mkdir -p "$home_dir/.codex/memories"
cat > "$home_dir/.codex/memories/active_rules.md" <<'EOF'
# Active Rules

DOTFILE FALLBACK RULE TOKEN
EOF
out="$(run_hook "$home_dir" "$vault")"
[[ "$out" == *"DOTFILE FALLBACK RULE TOKEN"* ]] || fail "missing vault should load dotfile fallback"
[[ "$out" == *"K2B_VAULT_PATH does not exist"* ]] || fail "missing vault should warn"
echo "PASS: missing vault uses dotfile fallback"

root="$(mktmp)"
home_dir="$root/home"
vault="$root/vault"
mkdir -p "$home_dir/.codex/memories" "$vault/wiki/context" "$vault/review" "$vault/raw/research"
cat > "$home_dir/.codex/memories/active_rules.md" <<'EOF'
# Active Rules

STALE DOTFILE RULE TOKEN
EOF
cat > "$home_dir/.codex/memories/self_improve_learnings.md" <<'EOF'
### L-2026-06-14-002
- **Area:** test
- **Learning:** STALE DOTFILE LEARNING TOKEN
- **Reinforced:** 2
EOF
out="$(run_hook "$home_dir" "$vault")"
[[ "$out" != *"STALE DOTFILE RULE TOKEN"* ]] || fail "existing vault root should suppress dotfile fallback"
[[ "$out" != *"STALE DOTFILE LEARNING TOKEN"* ]] || fail "existing vault root should suppress dotfile learning fallback"
echo "PASS: existing vault root suppresses dotfile fallback"

root="$(mktmp)"
home_dir="$root/home"
vault="$root/not-a-directory"
mkdir -p "$home_dir/.codex/memories"
touch "$vault"
cat > "$home_dir/.codex/memories/active_rules.md" <<'EOF'
# Active Rules

STALE DOTFILE RULE TOKEN
EOF
out="$(run_hook "$home_dir" "$vault")"
[[ "$out" == *"K2B SESSION HOOK WARNING: K2B_VAULT_PATH is not a directory"* ]] || fail "non-directory vault path should warn"
[[ "$out" != *"STALE DOTFILE RULE TOKEN"* ]] || fail "non-directory vault path should not fall back to dotfile memory"
echo "PASS: non-directory vault path fails closed"
