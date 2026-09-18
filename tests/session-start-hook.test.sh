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
  # Mirror the real SessionStart hook environment: the registered hook
  # command prepends the Homebrew python path so the native status adapter
  # (Python 3.11+ tomllib) is importable on hosts whose default python3 is
  # older. Without this, the native-status assertions below would degrade
  # to ModuleNotFoundError in the test harness only.
  env -u CLAUDE_PROJECT_DIR \
    PATH="/opt/homebrew/opt/python@3.12/libexec/bin:/opt/homebrew/bin:$PATH" \
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
  [[ "$out" == *"CAPTURE INVENTORY: unreadable local status (TypeError)"* ]] \
    || fail "$invalid_status status should degrade to an unreadable summary"
done
echo "PASS: non-object capture status degrades safely"

# --- Relevant startup text and native status are separate from inventory ---

root="$(mktmp)"
home_dir="$root/home"
vault="$root/vault"
mkdir -p "$home_dir" "$vault/System/memory"
cat > "$vault/System/memory/active_rules.md" <<'EOF'
# Active Rules

**Cap: 12 rules.** OLD PROMOTION WORKFLOW

Last promoted: OLD PROMOTION HISTORY

Last audited: OLD AUDIT HISTORY
Wrapped audit prose remains historical.

## Current behavior

1. Keep this rule.
   Keep this continuation too.

Keep this unnumbered instruction.
EOF
rules_before="$(LC_ALL=C shasum -a 256 "$vault/System/memory/active_rules.md")"
out="$(run_hook "$home_dir" "$vault")"
[[ "$out" != *"OLD PROMOTION"* && "$out" != *"OLD AUDIT"* ]] \
  || fail "historical audit/promotion metadata should not be injected"
[[ "$out" == *"Keep this rule."* && "$out" == *"Keep this continuation too."* && "$out" == *"Keep this unnumbered instruction."* ]] \
  || fail "current rule paragraphs and continuations must survive filtering"
[[ "$(LC_ALL=C shasum -a 256 "$vault/System/memory/active_rules.md")" == "$rules_before" ]] \
  || fail "startup must not mutate rule history"
[[ "$out" == *"no local legacy inventory"* && "$out" != *"disabled or not yet run"* ]] \
  || fail "missing legacy inventory must not claim native extraction is disabled"
echo "PASS: history stays on disk and missing inventory is not native status"

# Both registrations are fixtures; the read-only hook selects the actual local
# operator's role. The status adapter's role isolation has its own unit tests.
for job_id in k2b-automatic-memory-home k2b-automatic-memory-sjm; do
  mkdir -p "$home_dir/.codex/automations/$job_id"
  printf 'id="%s"\nstatus="ACTIVE"\n' "$job_id" \
    > "$home_dir/.codex/automations/$job_id/automation.toml"
done
mkdir -p "$home_dir/.local/state/k2b/automatic-memory"
printf '{"created_at":"2026-09-18T00:00:00Z"}\n' \
  > "$home_dir/.local/state/k2b/automatic-memory/extraction-hold.json"
out="$(run_hook "$home_dir" "$vault")"
if [[ "$(id -un)" == "keithcheung" || "$(id -un)" == "keithmbpm2" ]]; then
  [[ "$out" == *"registration=active"* && "$out" == *"outcome=unknown"* && "$out" == *"extraction hold=present"* ]] \
    || fail "native registration, completion evidence and hold must remain distinct: $out"
else
  [[ "$out" == *"AUTOMATIC MEMORY: unknown local evidence"* ]] \
    || fail "unsupported operator must not select a native job"
fi
echo "PASS: native registration does not claim completion or coverage"

root="$(mktmp)"
home_dir="$root/home"
vault="$root/vault"
stub_root="$root/project"
mkdir -p "$home_dir" "$vault/System/memory" "$stub_root/scripts/lib"
cat > "$stub_root/scripts/lib/native_job_status.py" <<'EOF'
import time
def read_native_job_status(**kwargs):
    time.sleep(30)
EOF
start=$SECONDS
out="$(env HOME="$home_dir" K2B_PROJECT_ROOT="$stub_root" K2B_VAULT_PATH="$vault" bash "$SCRIPT")"
elapsed=$((SECONDS - start))
[[ "$out" == *"AUTOMATIC MEMORY: unknown local evidence (TimeoutError)"* ]] \
  || fail "stalled native observation should report unknown"
[ "$elapsed" -lt 6 ] || fail "native status observation must have a short deadline"
echo "PASS: stalled native status is bounded and remains unknown"

# A stalled module import must also be bounded and reported distinctly: it
# is a reader problem, not unknown job evidence, and it must not consume
# the whole SessionStart timeout.
root="$(mktmp)"
home_dir="$root/home"
vault="$root/vault"
stub_root="$root/project"
mkdir -p "$home_dir" "$vault/System/memory" "$stub_root/scripts/lib"
cat > "$stub_root/scripts/lib/native_job_status.py" <<'EOF'
import time
time.sleep(30)
def read_native_job_status(**kwargs):
    return {}
EOF
start=$SECONDS
out="$(env HOME="$home_dir" K2B_PROJECT_ROOT="$stub_root" K2B_VAULT_PATH="$vault" bash "$SCRIPT")"
elapsed=$((SECONDS - start))
[[ "$out" == *"AUTOMATIC MEMORY: status reader slow (import deadline)"* ]] \
  || fail "stalled native status import should report a slow reader, not unknown evidence"
[ "$elapsed" -lt 8 ] || fail "stalled native status import must have a short deadline"
echo "PASS: stalled native status import is bounded and distinct"

# A broken or missing python3 must not abort the hook: the rest of the
# startup output (index, rules, conflict warning) must still be produced.
root="$(mktmp)"
home_dir="$root/home"
vault="$root/vault"
stub_bin="$root/bin"
mkdir -p "$home_dir" "$vault/System/memory" "$vault/wiki" "$stub_bin"
cat > "$vault/System/memory/active_rules.md" <<'EOF'
# Active Rules

BROKEN-PYTHON RULE TOKEN
EOF
cat > "$vault/wiki/index.md" <<'EOF'
BROKEN-PYTHON INDEX TOKEN
EOF
printf '#!/bin/sh\nexit 1\n' > "$stub_bin/python3"
chmod +x "$stub_bin/python3"
out="$(env PATH="$stub_bin:/usr/bin:/bin" HOME="$home_dir" K2B_PROJECT_ROOT="$REPO_ROOT" K2B_VAULT_PATH="$vault" /bin/bash "$SCRIPT")"
[[ "$out" == *"BROKEN-PYTHON RULE TOKEN"* && "$out" == *"BROKEN-PYTHON INDEX TOKEN"* ]] \
  || fail "broken python3 must not abort index and rules output"
[[ "$out" == *"CAPTURE INVENTORY: unavailable"* ]] \
  || fail "broken python3 must degrade to an explicit unavailable summary"
echo "PASS: broken python3 degrades without aborting the hook"

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
