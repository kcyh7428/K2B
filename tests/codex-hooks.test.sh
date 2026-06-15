#!/usr/bin/env bash
# tests/codex-hooks.test.sh
# Verifies Codex hook commands use provider-neutral repo-root resolution.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LOCAL_HOOKS_JSON="$REPO_ROOT/.codex/hooks.json"

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

echo "=== codex-hooks.test.sh ==="

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

VALID_FIXTURE="$TMP_DIR/hooks.valid.json"
INVALID_FIXTURE="$TMP_DIR/hooks.invalid.json"

cat > "$VALID_FIXTURE" <<'JSON'
{
  "hooks": {
    "PostToolUse": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "export K2B_HOOK_PROVIDER=codex; export K2B_PROJECT_ROOT=\"${K2B_PROJECT_ROOT:-$HOME/Projects/K2B}\"; \"$K2B_PROJECT_ROOT/scripts/hooks/post-tool-skill-track.sh\"",
            "timeout": 3
          }
        ]
      }
    ],
    "SessionStart": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "export K2B_HOOK_PROVIDER=codex; export K2B_PROJECT_ROOT=\"${K2B_PROJECT_ROOT:-$HOME/Projects/K2B}\"; \"$K2B_PROJECT_ROOT/scripts/hooks/session-start.sh\"",
            "timeout": 10
          }
        ]
      }
    ],
    "Stop": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "export K2B_HOOK_PROVIDER=codex; export K2B_PROJECT_ROOT=\"${K2B_PROJECT_ROOT:-$HOME/Projects/K2B}\"; \"$K2B_PROJECT_ROOT/scripts/hooks/stop-observe.sh\"",
            "timeout": 5
          }
        ]
      }
    ]
  }
}
JSON

cat > "$INVALID_FIXTURE" <<'JSON'
{
  "hooks": {
    "SessionStart": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "\"$CLAUDE_PROJECT_DIR\"/scripts/hooks/session-start.sh"
          }
        ]
      }
    ]
  }
}
JSON

TIMEOUT_FIXTURE="$TMP_DIR/hooks.timeout.invalid.json"
cat > "$TIMEOUT_FIXTURE" <<'JSON'
{
  "hooks": {
    "SessionStart": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "export K2B_HOOK_PROVIDER=codex; export K2B_PROJECT_ROOT=\"${K2B_PROJECT_ROOT:-$HOME/Projects/K2B}\"; \"$K2B_PROJECT_ROOT/scripts/hooks/session-start.sh\"",
            "timeout": 300
          }
        ]
      }
    ]
  }
}
JSON

check_hooks() {
  local hooks_json="$1"
  python3 - "$hooks_json" "$REPO_ROOT" <<'PY'
import json
import re
import sys
from pathlib import Path

path = Path(sys.argv[1])
repo_root = Path(sys.argv[2])
data = json.loads(path.read_text())
commands = []
found_targets = set()
expected_targets = {
    "scripts/hooks/post-tool-skill-track.sh",
    "scripts/hooks/session-start.sh",
    "scripts/hooks/stop-observe.sh",
}
expected_timeouts = {
    "scripts/hooks/post-tool-skill-track.sh": 3,
    "scripts/hooks/session-start.sh": 10,
    "scripts/hooks/stop-observe.sh": 5,
}
for hook_entries in data.get("hooks", {}).values():
    for entry in hook_entries:
        for hook in entry.get("hooks", []):
            if hook.get("type") == "command":
                command = hook.get("command", "")
                commands.append(command)
                if "CLAUDE_PROJECT_DIR" in command:
                    raise SystemExit(f"command contains CLAUDE_PROJECT_DIR: {command}")
                if "export K2B_PROJECT_ROOT=" not in command:
                    raise SystemExit(f"command does not export K2B_PROJECT_ROOT: {command}")
                if "export K2B_HOOK_PROVIDER=codex" not in command:
                    raise SystemExit(f"command does not export Codex hook provider: {command}")
                if "git rev-parse" in command:
                    raise SystemExit(f"command depends on git rev-parse: {command}")
                if "scripts/hooks/" not in command:
                    raise SystemExit(f"command does not point at scripts/hooks: {command}")
                match = re.search(r"scripts/hooks/[A-Za-z0-9_.-]+", command)
                if not match:
                    raise SystemExit(f"could not extract scripts/hooks target: {command}")
                rel_target = match.group(0)
                found_targets.add(rel_target)
                timeout = hook.get("timeout")
                if type(timeout) is not int:
                    raise SystemExit(f"timeout missing or non-integer for {rel_target}: {timeout!r}")
                expected_timeout = expected_timeouts[rel_target]
                if timeout != expected_timeout:
                    raise SystemExit(f"timeout mismatch for {rel_target}: expected {expected_timeout}, got {timeout}")
                target = repo_root / rel_target
                if not target.is_file():
                    raise SystemExit(f"hook target does not exist: {target}")
                if not (target.stat().st_mode & 0o111):
                    raise SystemExit(f"hook target is not executable: {target}")

missing = expected_targets - found_targets
if missing:
    raise SystemExit(f"missing expected hook command target(s): {sorted(missing)}")

for command in commands:
    print(command)
PY
}

run_hook_commands() {
  local commands_file="$1"
  local runtime_dir="$TMP_DIR/hook-runtime"
  local stub_root="$TMP_DIR/hook-stub-root"
  local target
  mkdir -p "$runtime_dir/vault/wiki/context" "$runtime_dir/vault/review" "$runtime_dir/vault/raw/research"
  for target in \
    scripts/hooks/post-tool-skill-track.sh \
    scripts/hooks/session-start.sh \
    scripts/hooks/stop-observe.sh
  do
    mkdir -p "$stub_root/$(dirname "$target")"
    printf '#!/usr/bin/env bash\ncat >/dev/null\nexit 0\n' > "$stub_root/$target"
    chmod +x "$stub_root/$target"
  done
  while IFS= read -r command; do
    [ -n "$command" ] || continue
    bash -n -c "$command" || fail "hook command is not valid bash: $command"
    env -u CLAUDE_PROJECT_DIR \
      K2B_PROJECT_ROOT="$stub_root" \
      K2B_VAULT_PATH="$runtime_dir/vault" \
      K2B_CURRENT_SKILL_FILE="$runtime_dir/current-skill" \
      K2B_LAST_OBSERVE_FILE="$runtime_dir/last-observe" \
      bash -c "$command" </dev/null >/dev/null || fail "hook command failed: $command"
  done < "$commands_file"
}

VALID_OUT="$TMP_DIR/k2b-codex-hooks.valid.out"
INVALID_OUT="$TMP_DIR/k2b-codex-hooks.invalid.out"
INVALID_ERR="$TMP_DIR/k2b-codex-hooks.invalid.err"
TIMEOUT_OUT="$TMP_DIR/k2b-codex-hooks.timeout.out"
TIMEOUT_ERR="$TMP_DIR/k2b-codex-hooks.timeout.err"
LOCAL_OUT="$TMP_DIR/k2b-codex-hooks.local.out"

check_hooks "$VALID_FIXTURE" >"$VALID_OUT"
run_hook_commands "$VALID_OUT"

INVALID_RC=0
check_hooks "$INVALID_FIXTURE" >"$INVALID_OUT" 2>"$INVALID_ERR" || INVALID_RC=$?
if [ "$INVALID_RC" -eq 0 ]; then
  fail "invalid fixture should fail"
fi
invalid_error="$(cat "$INVALID_ERR")"
if [ -s "$INVALID_OUT" ] || [[ "$invalid_error" != command\ contains\ CLAUDE_PROJECT_DIR:* ]]; then
  cat "$INVALID_OUT"
  cat "$INVALID_ERR" >&2
  fail "invalid fixture should fail specifically on CLAUDE_PROJECT_DIR"
fi

TIMEOUT_RC=0
check_hooks "$TIMEOUT_FIXTURE" >"$TIMEOUT_OUT" 2>"$TIMEOUT_ERR" || TIMEOUT_RC=$?
if [ "$TIMEOUT_RC" -eq 0 ]; then
  fail "timeout fixture should fail"
fi
timeout_error="$(cat "$TIMEOUT_ERR")"
if [ -s "$TIMEOUT_OUT" ] || [[ "$timeout_error" != timeout\ mismatch\ for\ scripts/hooks/session-start.sh:* ]]; then
  cat "$TIMEOUT_OUT"
  cat "$TIMEOUT_ERR" >&2
  fail "timeout fixture should fail specifically on timeout mismatch"
fi

[ -f "$LOCAL_HOOKS_JSON" ] || fail "local .codex/hooks.json is missing"
check_hooks "$LOCAL_HOOKS_JSON" >"$LOCAL_OUT"
run_hook_commands "$LOCAL_OUT"
echo "PASS: local .codex/hooks.json is provider-neutral"

git -C "$REPO_ROOT" check-ignore --no-index -q .codex/job.md || fail ".codex/job.md should be ignored"
git -C "$REPO_ROOT" check-ignore --no-index -q .codex/archive/old.md || fail ".codex/archive should be ignored"
git -C "$REPO_ROOT" check-ignore --no-index -q .agents/skills/k2b-ship/eval/results.tsv || fail ".agents skill eval results should be ignored"
git -C "$REPO_ROOT" check-ignore --no-index -q .agents/skills/k2b-ship/eval/run.log || fail ".agents skill eval runtime files should be ignored"
git -C "$REPO_ROOT" check-ignore --no-index -q .agents/skills/k2b-ship/eval/cache/run.json || fail ".agents skill eval cache should be ignored"
git -C "$REPO_ROOT" check-ignore --no-index -q .agents/skills/k2b-ship/eval/tmp/run.json || fail ".agents skill eval tmp should be ignored"
git -C "$REPO_ROOT" check-ignore --no-index -q .agents/skills/k2b-ship/eval/.cache/run.json || fail ".agents skill eval dot-cache should be ignored"
git -C "$REPO_ROOT" check-ignore --no-index -q .agents/skills/k2b-ship/cache/blob.tmp || fail ".agents skill cache should be ignored"
git -C "$REPO_ROOT" check-ignore --no-index -q .agents/skills/k2b-ship/tmp/job.json || fail ".agents skill tmp should be ignored"
git -C "$REPO_ROOT" check-ignore --no-index -q .agents/skills/k2b-ship/state.json || fail ".agents skill unknown runtime state should be ignored"
if git -C "$REPO_ROOT" check-ignore --no-index -q .codex/hooks.json; then
  fail ".codex/hooks.json should be versionable"
fi
if git -C "$REPO_ROOT" check-ignore --no-index -q .agents/skills/k2b-ship/SKILL.md; then
  fail ".agents skill SKILL.md should be versionable"
fi
if git -C "$REPO_ROOT" check-ignore --no-index -q .agents/skills/k2b-ship/eval/eval.json; then
  fail ".agents skill eval fixtures should be versionable"
fi
if git -C "$REPO_ROOT" check-ignore --no-index -q .agents/skills/k2b-vault-writer/references/CALLOUTS.md; then
  fail ".agents skill references should be versionable"
fi
if git -C "$REPO_ROOT" check-ignore --no-index -q .agents/skills/k2b-plate/scripts/plate.sh; then
  fail ".agents skill scripts should be versionable"
fi
echo "PASS: runtime .codex artifacts stay ignored"

OUTSIDE_GIT="$TMP_DIR/outside-git"
mkdir -p "$OUTSIDE_GIT"
(
  cd "$OUTSIDE_GIT"
  env -u CLAUDE_PROJECT_DIR K2B_PROJECT_ROOT="$REPO_ROOT" bash -lc '
    K2B_PROJECT_ROOT="${K2B_PROJECT_ROOT:-$HOME/Projects/K2B}"
    test -x "$K2B_PROJECT_ROOT/scripts/hooks/session-start.sh"
    test -x "$K2B_PROJECT_ROOT/scripts/hooks/post-tool-skill-track.sh"
    test -x "$K2B_PROJECT_ROOT/scripts/hooks/stop-observe.sh"
  '
) || fail "K2B_PROJECT_ROOT should resolve hook targets outside a git cwd"

echo "PASS: hook command fixtures are provider-neutral"
