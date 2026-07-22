#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
fixture="$(mktemp -d)"
fake_bin="$fixture/bin"
system_path="$PATH"
trap 'rm -rf "$fixture"' EXIT

mkdir -p \
  "$fixture/.CLAUDE/skills/example" \
  "$fixture/Library/LaunchAgents" \
  "$fixture/config" \
  "$fake_bin"

python3 - "$fixture" <<'PY'
import json
import plistlib
import sys
from pathlib import Path

root = Path(sys.argv[1])
with (root / "Package.JSON").open("w", encoding="utf-8") as handle:
    json.dump(
        {
            "dependencies": {
                "@anthropic-ai/sdk": "fixture-version",
                "grammy": "fixture-version",
            }
        },
        handle,
    )
with (root / "Library/LaunchAgents/COM.EXAMPLE.BINARY.PLIST").open("wb") as handle:
    plistlib.dump(
        {
            "Label": "com.example.binary.claude",
            "ProgramArguments": ["/usr/bin/node", "/srv/claude-worker", "--token=fixture-plist-secret"],
        },
        handle,
        fmt=plistlib.FMT_BINARY,
    )
with (root / ".ENV.LARGE").open("w", encoding="utf-8") as handle:
    handle.write("X" * 1_000_001)
PY

printf '%s\n' '# fixture Claude instruction' > "$fixture/CLAUDE.MD"
printf '%s\n' '# fixture skill' > "$fixture/.CLAUDE/skills/example/SKILL.MD"
printf '%s\n' '{"hooks":{"PreToolUse":["fixture-hook"]},"mcpServers":{"fixture":{"command":"claude"}}}' > "$fixture/.CLAUDE/settings.JSON"
printf '%s\n' 'CLAUDE_PROJECT_DIR=/fixture/project' > "$fixture/config/memory-reader.sh"
printf '%s\n' '0 * * * * /usr/local/bin/claude-job' > "$fixture/crontab"
printf '%s\n' 'OPENAI_API_KEY=fixture-secret-value' > "$fixture/.ENV"
printf '%s\n' 'FIREFLIES_API_KEY=another-fixture-secret' >> "$fixture/.ENV"
printf '%s\n' '<plist><dict><key>Label</key><string>com.k2b-remote.app</string><key>ProgramArguments</key><array><string>/usr/local/bin/claude-action</string></array></dict></plist>' > "$fixture/Library/LaunchAgents/com.k2b-remote.app.plist"
printf '%s\n' '<plist><dict><key>Label</key><string>broken' > "$fixture/Library/LaunchAgents/broken.plist"
ln -s .ENV "$fixture/linked.env"
ln -s .CLAUDE "$fixture/linked-claude"
mkdir -p "$fixture/Projects"
ln -s ../.CLAUDE "$fixture/Projects/K2B"

cat > "$fake_bin/launchctl" <<'SH'
#!/usr/bin/env bash
if [ "$1" = "list" ]; then
  printf '%s\n' 'PID Status Label' 'malformed launchctl row detail' '- 0 com.example.claude.loaded'
fi
SH
cat > "$fake_bin/pm2" <<'SH'
#!/usr/bin/env bash
if [ "$1" = "jlist" ]; then
  printf '%s\n' '[{"name":"claude-worker","pm2_env":{"pm_exec_path":"/srv/claude-worker"}}]'
fi
SH
cat > "$fake_bin/crontab" <<'SH'
#!/usr/bin/env bash
if [ "$1" = "-l" ]; then
  printf '%s\n' '0 * * * * /usr/local/bin/claude-job'
fi
SH
cat > "$fake_bin/ps" <<'SH'
#!/usr/bin/env bash
if [ "$1" = "-axo" ] && [ "$2" = "comm=,args=" ]; then
  printf 'malformed\0process-secret\n/usr/bin/node node /srv/claude-worker --token=fixture-process-arg-secret\n'
fi
SH
chmod +x "$fake_bin/launchctl" "$fake_bin/pm2" "$fake_bin/crontab" "$fake_bin/ps"

result="$(PATH="$fake_bin:$system_path" K2B_AUDIT_ROOT="$fixture" K2B_AUDIT_FIXTURE=1 K2B_AUDIT_HOST_ROLE=macbook bash "$REPO_ROOT/scripts/audit-claude-runtime.sh")"

jq -e '.' <<<"$result" >/dev/null
for kind in \
  anthropic-package \
  claude-instruction \
  claude-skill \
  claude-hook \
  claude-schedule \
  claude-launch-action \
  claude-mcp \
  claude-memory-reader \
  telegram-runtime \
  openai-api-credential-name \
  stale-k2b-launchagent \
  unverified-mini-surface; do
  jq -e --arg kind "$kind" '.findings[] | select(.kind == $kind)' <<<"$result" >/dev/null
done

jq -e '.credential_names | index("OPENAI_API_KEY")' <<<"$result" >/dev/null
jq -e '.credential_names | index("FIREFLIES_API_KEY")' <<<"$result" >/dev/null
jq -e '.launchd | map(select(.label == "com.example.claude.loaded")) | length == 1' <<<"$result" >/dev/null
jq -e '.launchd | map(select(.label == "com.example.binary.claude")) | length == 1' <<<"$result" >/dev/null
jq -e '.findings[] | select(.kind == "unparseable-launchagent-plist" and (.location | endswith("/broken.plist")))' <<<"$result" >/dev/null
jq -e '.keychain_credential_status == "out_of_scope"' <<<"$result" >/dev/null
jq -e '.findings[] | select(.kind == "keychain-credential-enumeration-unverified")' <<<"$result" >/dev/null
jq -e '.findings[] | select(.kind == "claude-or-anthropic-process" and .location == "/usr/bin/node")' <<<"$result" >/dev/null
jq -e '.findings[] | select(.kind == "claude-or-anthropic-pm2-process" and .location == "claude-worker")' <<<"$result" >/dev/null
jq -e '.filesystem_scope.other_home_subdirectories == "out_of_scope"' <<<"$result" >/dev/null
jq -e '.findings[] | select(.kind == "filesystem-scope-limited")' <<<"$result" >/dev/null
jq -e '.findings[] | select(.kind == "skipped-symlink-file" and (.location | endswith("/linked.env")))' <<<"$result" >/dev/null
jq -e '.findings[] | select(.kind == "skipped-symlink-directory" and (.location | endswith("/linked-claude")))' <<<"$result" >/dev/null
jq -e '.findings[] | select(.kind == "skipped-oversized-file" and (.location | endswith("/.ENV.LARGE")))' <<<"$result" >/dev/null
jq -e '.findings[] | select(.kind == "unparseable-process-row")' <<<"$result" >/dev/null
jq -e '.findings[] | select(.kind == "unparseable-launchctl-row")' <<<"$result" >/dev/null
jq -e '(.processes | type) == "array" and (.launchd | type) == "array" and (.pm2 | type) == "array" and (.cron | type) == "array" and (.packages | type) == "array" and (.instruction_paths | type) == "array" and (.hook_paths | type) == "array" and (.credential_names | type) == "array" and (.probe_status | type) == "object" and (.findings | type) == "array"' <<<"$result" >/dev/null
if grep -Fq 'fixture-secret-value' <<<"$result" || grep -Fq 'another-fixture-secret' <<<"$result" || grep -Fq 'fixture-process-arg-secret' <<<"$result" || grep -Fq 'fixture-plist-secret' <<<"$result" || grep -Fq 'process-secret' <<<"$result"; then
  echo "audit output exposed a fixture secret value" >&2
  exit 1
fi

mini_result="$(PATH="$fake_bin:$system_path" K2B_AUDIT_ROOT="$fixture" K2B_AUDIT_FIXTURE=1 K2B_AUDIT_HOST_ROLE=mini K2B_PM2_PATH="$fake_bin/pm2" bash "$REPO_ROOT/scripts/audit-claude-runtime.sh")"
jq -e '.processes | map(select(.executable == "/usr/bin/node")) | length == 1' <<<"$mini_result" >/dev/null
jq -e '.probe_status == {"crontab": true, "launchctl": true, "pm2": true, "processes": true}' <<<"$mini_result" >/dev/null
jq -e '.findings | map(select(.kind == "unverified-mini-surface")) | length == 0' <<<"$mini_result" >/dev/null

invalid_role_result="$(PATH="$fake_bin:$system_path" K2B_AUDIT_ROOT="$fixture" K2B_AUDIT_FIXTURE=1 K2B_AUDIT_HOST_ROLE=not-a-role bash "$REPO_ROOT/scripts/audit-claude-runtime.sh")"
jq -e '.host_identity_status == "invalid-role"' <<<"$invalid_role_result" >/dev/null
jq -e '.findings[] | select(.kind == "host-role-unrecognized")' <<<"$invalid_role_result" >/dev/null
jq -e '.findings[] | select(.kind == "unverified-mini-surface")' <<<"$invalid_role_result" >/dev/null

uppercase_role_result="$(PATH="$fake_bin:$system_path" K2B_AUDIT_ROOT="$fixture" K2B_AUDIT_FIXTURE=1 K2B_AUDIT_HOST_ROLE=MINI bash "$REPO_ROOT/scripts/audit-claude-runtime.sh")"
jq -e '.host_identity_status == "invalid-role"' <<<"$uppercase_role_result" >/dev/null

ambiguous_host_result="$(PATH="$fake_bin:$system_path" K2B_AUDIT_ROOT="$fixture" K2B_AUDIT_FIXTURE=1 K2B_AUDIT_HARDWARE_MODEL=UnknownMac1,1 bash "$REPO_ROOT/scripts/audit-claude-runtime.sh")"
jq -e '.host_identity_status == "ambiguous"' <<<"$ambiguous_host_result" >/dev/null
jq -e '.findings[] | select(.kind == "unverified-mini-surface")' <<<"$ambiguous_host_result" >/dev/null

live_override_result="$(PATH="$fake_bin:$system_path" K2B_AUDIT_ROOT="$fixture" K2B_AUDIT_FIXTURE=0 K2B_AUDIT_HARDWARE_MODEL=Macmini9,1 bash "$REPO_ROOT/scripts/audit-claude-runtime.sh")"
jq -e '.host_identity_status != "hardware-mini"' <<<"$live_override_result" >/dev/null
jq -e '.findings[] | select(.kind == "skipped-symlink-directory" and (.location | endswith("/Projects/K2B")))' <<<"$live_override_result" >/dev/null

cat > "$fake_bin/ps" <<'SH'
#!/usr/bin/env bash
exit 7
SH
chmod +x "$fake_bin/ps"
failed_process_result="$(PATH="$fake_bin:$system_path" K2B_AUDIT_ROOT="$fixture" K2B_AUDIT_FIXTURE=1 K2B_AUDIT_HOST_ROLE=mini bash "$REPO_ROOT/scripts/audit-claude-runtime.sh")"
jq -e '.findings[] | select(.kind == "unverified-mini-surface")' <<<"$failed_process_result" >/dev/null

cat > "$fake_bin/ps" <<'SH'
#!/usr/bin/env bash
if [ "$1" = "-axo" ] && [ "$2" = "comm=,args=" ]; then
  printf '%s\n' '/usr/bin/node node /srv/claude-worker --token=fixture-process-arg-secret'
fi
SH
chmod +x "$fake_bin/ps"

cat > "$fake_bin/crontab" <<'SH'
#!/usr/bin/env bash
exit 7
SH
chmod +x "$fake_bin/crontab"
failed_probe_result="$(PATH="$fake_bin:$system_path" K2B_AUDIT_ROOT="$fixture" K2B_AUDIT_FIXTURE=1 K2B_AUDIT_HOST_ROLE=mini bash "$REPO_ROOT/scripts/audit-claude-runtime.sh")"
jq -e '.findings[] | select(.kind == "unverified-mini-surface")' <<<"$failed_probe_result" >/dev/null

cat > "$fake_bin/crontab" <<'SH'
#!/usr/bin/env bash
printf '%s\n' 'no crontab for fixture-user' >&2
exit 1
SH
chmod +x "$fake_bin/crontab"
no_crontab_result="$(PATH="$fake_bin:$system_path" K2B_AUDIT_ROOT="$fixture" K2B_AUDIT_FIXTURE=1 K2B_AUDIT_HOST_ROLE=mini bash "$REPO_ROOT/scripts/audit-claude-runtime.sh")"
jq -e '.probe_status.crontab == true' <<<"$no_crontab_result" >/dev/null
jq -e '.probe_status.processes == true' <<<"$no_crontab_result" >/dev/null
jq -e '.findings | map(select(.kind == "unverified-mini-surface")) | length == 0' <<<"$no_crontab_result" >/dev/null

cat > "$fake_bin/crontab" <<'SH'
#!/usr/bin/env bash
printf '%s\n' 'not a supported cron entry' '0 * * * * /usr/local/bin/claude-job'
SH
chmod +x "$fake_bin/crontab"
malformed_crontab_result="$(PATH="$fake_bin:$system_path" K2B_AUDIT_ROOT="$fixture" K2B_AUDIT_FIXTURE=1 K2B_AUDIT_HOST_ROLE=mini bash "$REPO_ROOT/scripts/audit-claude-runtime.sh")"
jq -e '.probe_status.crontab == true' <<<"$malformed_crontab_result" >/dev/null
jq -e '.findings[] | select(.kind == "unparseable-crontab-entry")' <<<"$malformed_crontab_result" >/dev/null
jq -e '.findings[] | select(.kind == "claude-schedule")' <<<"$malformed_crontab_result" >/dev/null

cat > "$fake_bin/pm2" <<'SH'
#!/usr/bin/env bash
if [ "$1" = "jlist" ]; then
  printf '%s' 'not-json'
fi
SH
chmod +x "$fake_bin/pm2"
malformed_pm2_result="$(PATH="$fake_bin:$system_path" K2B_AUDIT_ROOT="$fixture" K2B_AUDIT_FIXTURE=1 K2B_AUDIT_HOST_ROLE=mini K2B_PM2_PATH="$fake_bin/pm2" bash "$REPO_ROOT/scripts/audit-claude-runtime.sh")"
jq -e '.probe_status.pm2 == false' <<<"$malformed_pm2_result" >/dev/null
jq -e '.findings[] | select(.kind == "unverified-mini-surface")' <<<"$malformed_pm2_result" >/dev/null

cat > "$fake_bin/pm2" <<'SH'
#!/usr/bin/env bash
if [ "$1" = "jlist" ]; then
  printf '%s' ''
fi
SH
chmod +x "$fake_bin/pm2"
empty_pm2_result="$(PATH="$fake_bin:$system_path" K2B_AUDIT_ROOT="$fixture" K2B_AUDIT_FIXTURE=1 K2B_AUDIT_HOST_ROLE=mini K2B_PM2_PATH="$fake_bin/pm2" bash "$REPO_ROOT/scripts/audit-claude-runtime.sh")"
jq -e '.probe_status.pm2 == false' <<<"$empty_pm2_result" >/dev/null

missing_pm2_result="$(PATH="$fake_bin:$system_path" K2B_AUDIT_ROOT="$fixture" K2B_AUDIT_FIXTURE=1 K2B_AUDIT_HOST_ROLE=mini K2B_PM2_PATH="$fixture/missing-pm2" bash "$REPO_ROOT/scripts/audit-claude-runtime.sh")"
jq -e '.probe_status.pm2 == false' <<<"$missing_pm2_result" >/dev/null

echo "audit-claude-runtime: PASS"
