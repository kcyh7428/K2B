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
[[ "$out" != *"VAULT LEARNING TOKEN"* ]] || fail "disabled learning watchlist should not be loaded"
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

for invalid_status in top-level-list counts-list; do
  root="$(mktmp)"
  home_dir="$root/home"
  vault="$root/vault"
  status_dir="$home_dir/.local/state/k2b"
  mkdir -p "$status_dir" "$vault/System/memory"
  if [ "$invalid_status" = "top-level-list" ]; then
    printf '[]\n' > "$status_dir/capture-status.json"
  else
    printf '{"counts":[]}\n' > "$status_dir/capture-status.json"
  fi
  out="$(run_hook "$home_dir" "$vault")"
  [[ "$out" == *"CAPTURE STATUS: unreadable local status (TypeError)"* ]] \
    || fail "$invalid_status status should degrade to an unreadable summary"
done
echo "PASS: non-object capture status degrades safely"

# --- Syncthing conflict-copy warning ---------------------------------------

root="$(mktmp)"
home_dir="$root/home"
vault="$root/vault"
mkdir -p "$home_dir" "$vault/wiki/work"
printf -- '---\ntags: [work]\n---\n# conflict version\n' \
  > "$vault/wiki/work/work_kingdee-hris-recovery.sync-conflict-20260914-120000.md"
out="$(run_hook "$home_dir" "$vault")"
[[ "$out" == *"VAULT CONFLICT: wiki/work/work_kingdee-hris-recovery.sync-conflict-20260914-120000.md"* ]] \
  || fail "known conflict copy should produce a SessionStart warning"
echo "PASS: conflict copy warns"

root="$(mktmp)"
home_dir="$root/home"
vault="$root/vault"
mkdir -p "$home_dir" "$vault/wiki/work"
printf -- '---\ntags: [work]\n---\n# ordinary note\n' \
  > "$vault/wiki/work/work_ordinary.md"
out="$(run_hook "$home_dir" "$vault")"
[[ "$out" != *"VAULT CONFLICT"* ]] \
  || fail "no conflict copies should produce no conflict prose"
echo "PASS: no conflict copies stays silent"

root="$(mktmp)"
home_dir="$root/home"
vault="$root/missing-vault"
mkdir -p "$home_dir/.codex/memories"
cat > "$home_dir/.codex/memories/active_rules.md" <<'EOF'
# Active Rules

DOTFILE FALLBACK RULE TOKEN
EOF
out="$(run_hook "$home_dir" "$vault")"
[[ "$out" != *"VAULT CONFLICTS"* ]] \
  || fail "missing vault should not run the conflict check or add unknown prose"
echo "PASS: missing vault preserves existing hook contract"

root="$(mktmp)"
home_dir="$root/home"
vault="$root/vault"
mkdir -p "$home_dir" "$vault/wiki/work"
chmod 000 "$vault"
out="$(run_hook "$home_dir" "$vault" 2>/dev/null)"
chmod 700 "$vault"
[[ "$out" == *"VAULT CONFLICTS: unknown"* ]] \
  || fail "unreadable vault should yield an unknown conflict summary, got: $out"
echo "PASS: unreadable vault yields unknown, not a false all-clear"

root="$(mktmp)"
home_dir="$root/home"
vault="$root/vault"
bin_dir="$root/bin"
mkdir -p "$home_dir" "$vault/wiki/work" "$bin_dir"
REAL_PYTHON="$(command -v python3)"
cat > "$bin_dir/python3" <<EOF
#!/bin/bash
for arg in "\$@"; do
  if [ "\$arg" = "conflicts" ]; then
    sleep 30
    exit 0
  fi
done
exec "$REAL_PYTHON" "\$@"
EOF
chmod +x "$bin_dir/python3"
start=$SECONDS
out="$(env PATH="$bin_dir:$PATH" HOME="$home_dir" \
  K2B_PROJECT_ROOT="$REPO_ROOT" K2B_VAULT_PATH="$vault" bash "$SCRIPT" 2>/dev/null)"
elapsed=$((SECONDS - start))
[[ "$out" == *"VAULT CONFLICTS: unknown (conflict check timed out)"* ]] \
  || fail "stalled conflict check should report a timeout unknown, got: $out"
[ "$elapsed" -lt 20 ] || fail "hook should enforce a real deadline (elapsed ${elapsed}s)"
echo "PASS: stalled conflict check is killed and reported unknown"
