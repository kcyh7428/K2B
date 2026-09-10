#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRIPT="$ROOT/scripts/inventory-retired-runtime.sh"
TMP="$(mktemp -d)"
cleanup() {
  rm -rf "$TMP"
}
trap cleanup EXIT

if [ ! -f "$SCRIPT" ]; then
  echo "FAIL: missing $SCRIPT"
  exit 1
fi
if [ ! -x "$SCRIPT" ]; then
  chmod +x "$SCRIPT"
fi

mkdir -p "$TMP/fixtures/pm2" "$TMP/fixtures/launchd" "$TMP/fixtures/cron" "$TMP/fixtures/mcp" "$TMP/fixtures/state" "$TMP/output"

# PM2 fixture: normal dump arrays must retain every process, with sanitized fields only
PM2_COMMAND_SECRET="pm2-command-secret-value"
printf '[{"name":"k2b-remote","cmd":"node dist/bot.js --token %s","env":{"K2B_BOT_TOKEN":"super-secret","K2B_CHAT_ID":"-123"}},{"name":"k2b-dashboard","pm_exec_path":"server.js","env":{"PORT":"3000"}}]\n' "$PM2_COMMAND_SECRET" > "$TMP/fixtures/pm2/dump.pm2"

# Launchd fixture
printf '<?xml version="1.0"?><!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"><plist><dict><key>Label</key><string>com.k2b.router-watchdog</string></dict></plist>\n' > "$TMP/fixtures/launchd/com.k2b.router-watchdog.plist"

# Cron fixture
printf 'PATH=/opt/homebrew/bin:/usr/bin:/bin\nMAILTO=keith@example.com\n0 2 * * * /Users/keithmbpm2/Projects/K2B/scripts/eod-capture-cron.sh job-a-then-b\n0 8 * * * /Users/keithmbpm2/Projects/K2B/scripts/eod-capture-cron.sh digest\n' > "$TMP/fixtures/cron/crontab"

# MCP fixture
printf '{"mcpServers":{"k2b":{"command":"npx","env":{"OPENAI_API_KEY":"sk-test"}}}}\n' > "$TMP/fixtures/mcp/mcp.json"

# State fixture
printf '{"enabled":true}\n' > "$TMP/fixtures/state/retired-state.json"

FAKE_COMMIT="abc123def456"

# Run inventory for the home host with fixture overrides
K2B_INVENTORY_PM2_DIR="$TMP/fixtures/pm2" \
K2B_INVENTORY_LAUNCHD_DIR="$TMP/fixtures/launchd" \
K2B_INVENTORY_CRON_FILE="$TMP/fixtures/cron/crontab" \
K2B_INVENTORY_MCP_FILE="$TMP/fixtures/mcp/mcp.json" \
K2B_INVENTORY_STATE_DIR="$TMP/fixtures/state" \
K2B_INVENTORY_COMMIT="$FAKE_COMMIT" \
"$SCRIPT" --host home --output "$TMP/output/inventory.json"

OUT="$TMP/output/inventory.json"
if [ ! -f "$OUT" ]; then
  echo "FAIL: inventory output missing"
  exit 1
fi

jq -e '.host_kind == "home"' "$OUT" >/dev/null || { echo "FAIL: host_kind mismatch"; exit 1; }
jq -e '.created_at' "$OUT" >/dev/null || { echo "FAIL: created_at missing"; exit 1; }
jq -e ".source_commit == \"$FAKE_COMMIT\"" "$OUT" >/dev/null || { echo "FAIL: source_commit mismatch"; exit 1; }

# PM2 checks
jq -e '.pm2[0].name == "k2b-remote"' "$OUT" >/dev/null || { echo "FAIL: pm2 name missing"; exit 1; }
jq -e '.pm2[0].command' "$OUT" >/dev/null || { echo "FAIL: pm2 command missing"; exit 1; }
jq -e '.pm2[0].command | contains("node dist/bot.js")' "$OUT" >/dev/null || { echo "FAIL: pm2 command identity missing"; exit 1; }
jq -e '.pm2[0].command | contains("--token [REDACTED]")' "$OUT" >/dev/null || { echo "FAIL: pm2 command token not redacted"; exit 1; }
jq -e '.pm2[0].hash' "$OUT" >/dev/null || { echo "FAIL: pm2 hash missing"; exit 1; }
jq -e '.pm2[0].env_var_names | index("K2B_BOT_TOKEN")' "$OUT" >/dev/null || { echo "FAIL: pm2 env var name missing"; exit 1; }
jq -e '.pm2[0].env_var_names | index("K2B_CHAT_ID")' "$OUT" >/dev/null || { echo "FAIL: pm2 env var name missing"; exit 1; }
jq -e '.pm2 | length == 2' "$OUT" >/dev/null || { echo "FAIL: PM2 array did not retain every process"; exit 1; }
jq -e '.pm2[1].name == "k2b-dashboard" and .pm2[1].command == "server.js" and (.pm2[1].env_var_names | index("PORT"))' "$OUT" >/dev/null \
  || { echo "FAIL: second PM2 process missing or malformed"; exit 1; }
if jq -e '.pm2[0].env_values' "$OUT" >/dev/null 2>&1; then
  echo "FAIL: pm2 env values must not be recorded"
  exit 1
fi
if jq -e '.pm2[].env // empty | .K2B_BOT_TOKEN' "$OUT" >/dev/null 2>&1; then
  echo "FAIL: pm2 secret value leaked"
  exit 1
fi
if grep -qF "$PM2_COMMAND_SECRET" "$OUT"; then
  echo "FAIL: pm2 command secret leaked"
  exit 1
fi

# Launchd checks
jq -e '.launchd[0].label == "com.k2b.router-watchdog"' "$OUT" >/dev/null || { echo "FAIL: launchd label missing"; exit 1; }
jq -e '.launchd[0].plist_hash' "$OUT" >/dev/null || { echo "FAIL: launchd plist_hash missing"; exit 1; }
jq -e '.launchd[0].disposition == "decision-required"' "$OUT" >/dev/null || { echo "FAIL: launchd disposition missing"; exit 1; }

# Cron checks
jq -e '.cron.commands | length == 2' "$OUT" >/dev/null || { echo "FAIL: cron commands missing"; exit 1; }
jq -e '.cron.path and (.cron.hash | length == 64) and .cron.owner == "unknown" and .cron.disposition == "decision-required"' "$OUT" >/dev/null || { echo "FAIL: cron source metadata incomplete"; exit 1; }
if jq -e '.cron.raw_crontab' "$OUT" >/dev/null 2>&1; then
  echo "FAIL: raw crontab must not be recorded"
  exit 1
fi
if grep -q 'keith@example.com\|/opt/homebrew/bin:/usr/bin:/bin' "$OUT"; then
  echo "FAIL: crontab environment declarations must not be recorded"
  exit 1
fi

# MCP checks
jq -e '.mcp.path and (.mcp.hash | length == 64) and .mcp.owner == "unknown" and .mcp.disposition == "decision-required"' "$OUT" >/dev/null || { echo "FAIL: mcp source metadata incomplete"; exit 1; }
jq -e '.mcp.servers | length == 1' "$OUT" >/dev/null || { echo "FAIL: mcp servers missing"; exit 1; }
jq -e '.mcp.servers[0].name == "k2b" and .mcp.servers[0].command == "npx" and (.mcp.servers[0].env_var_names | index("OPENAI_API_KEY"))' "$OUT" >/dev/null || { echo "FAIL: mcp server detail missing"; exit 1; }
jq -e '.mcp.env_var_names | index("OPENAI_API_KEY")' "$OUT" >/dev/null || { echo "FAIL: mcp env var name missing"; exit 1; }
if jq -e '.mcp.env_values' "$OUT" >/dev/null 2>&1; then
  echo "FAIL: mcp env values must not be recorded"
  exit 1
fi

# State checks
jq -e '.state[0].path' "$OUT" >/dev/null || { echo "FAIL: state path missing"; exit 1; }
jq -e '.state[0].hash' "$OUT" >/dev/null || { echo "FAIL: state hash missing"; exit 1; }

# Unknown ownership is decision-required
jq -e '.ownership_unknown_disposition == "decision-required"' "$OUT" >/dev/null || { echo "FAIL: ownership disposition missing"; exit 1; }

# Manifest must not contain restart instructions
if grep -qi "restart" "$OUT"; then
  echo "FAIL: manifest must not contain restart instructions"
  exit 1
fi

# Verify mode should pass
"$SCRIPT" --verify --output "$OUT" || { echo "FAIL: verify failed"; exit 1; }

# Corrupt hash and verify should fail
jq '.state[0].hash = "0000000000000000000000000000000000000000000000000000000000000000"' "$OUT" > "$TMP/output/bad.json"
if "$SCRIPT" --verify --output "$TMP/output/bad.json"; then
  echo "FAIL: verify should fail with bad hash"
  exit 1
fi

# ---------------------------------------------------------------------------
# Regression: verify mode must reject structurally incomplete manifests
# ---------------------------------------------------------------------------

expect_verify_fail() {
  local name="$1" path="$2"
  local rc=0
  local stderr_file="$TMP/stderr-$name.txt"
  "$SCRIPT" --verify --output "$path" 2>"$stderr_file" || rc=$?
  if [ "$rc" -eq 0 ]; then
    echo "FAIL: verify should reject $name"
    exit 1
  fi
  if [ "$rc" -eq 141 ]; then
    echo "FAIL: verify produced SIGPIPE exit-141 for $name"
    exit 1
  fi
  if grep -qi "Traceback" "$stderr_file" || grep -q 'File "' "$stderr_file"; then
    echo "FAIL: verify produced traceback for $name"
    cat "$stderr_file" >&2
    exit 1
  fi
}

# Empty object
printf '{}' > "$TMP/output/empty.json"
expect_verify_fail "empty manifest" "$TMP/output/empty.json"

# Malformed JSON
printf 'not json' > "$TMP/output/malformed.json"
expect_verify_fail "malformed json" "$TMP/output/malformed.json"

# Missing required top-level fields
jq -n '{schema_version: "v1", host: "h", host_kind: "local", created_at: "2024-01-01T00:00:00Z", source_commit: "abc", ownership_unknown_disposition: "decision-required", pm2: [], launchd: [], cron: {path:"/absent/cron",hash:null,owner:"unknown",disposition:"decision-required",commands:[]}, mcp: {path:"/absent/mcp",hash:null,owner:"unknown",disposition:"decision-required",servers:[],env_var_names:[]}, state: []}' > "$TMP/output/missing_field.json"
for key in host host_kind created_at source_commit ownership_unknown_disposition; do
  jq "del(.${key})" "$TMP/output/missing_field.json" > "$TMP/output/missing_$key.json"
  expect_verify_fail "missing $key" "$TMP/output/missing_$key.json"
done

# Wrong ownership disposition
jq '.ownership_unknown_disposition = "remove"' "$TMP/output/missing_field.json" > "$TMP/output/bad_ownership.json"
expect_verify_fail "bad ownership disposition" "$TMP/output/bad_ownership.json"

# Missing required sections
for section in pm2 launchd cron mcp state; do
  jq "del(.${section})" "$TMP/output/missing_field.json" > "$TMP/output/missing_section_$section.json"
  expect_verify_fail "missing section $section" "$TMP/output/missing_section_$section.json"
done

# Wrong section types
jq '.pm2 = "bad"' "$TMP/output/missing_field.json" > "$TMP/output/pm2_wrong_type.json"
expect_verify_fail "pm2 wrong type" "$TMP/output/pm2_wrong_type.json"
jq '.cron = []' "$TMP/output/missing_field.json" > "$TMP/output/cron_wrong_type.json"
expect_verify_fail "cron wrong type" "$TMP/output/cron_wrong_type.json"

# Wrong entry types and missing fields
jq '.pm2 = ["not-an-object"]' "$TMP/output/missing_field.json" > "$TMP/output/pm2_bad_entry.json"
expect_verify_fail "pm2 bad entry type" "$TMP/output/pm2_bad_entry.json"

jq '.pm2 = [{name:"x",path:"/x",command:"c",owner:"unknown",disposition:"decision-required",env_var_names:[]}]' "$TMP/output/missing_field.json" > "$TMP/output/pm2_missing_hash.json"
expect_verify_fail "pm2 missing hash" "$TMP/output/pm2_missing_hash.json"

# Invalid hash formats
jq '.pm2[0].hash = "not-hex"' "$TMP/output/missing_field.json" > "$TMP/output/pm2_hash_not_hex.json"
expect_verify_fail "pm2 hash not hex" "$TMP/output/pm2_hash_not_hex.json"
jq '.pm2[0].hash = "abcd"' "$TMP/output/missing_field.json" > "$TMP/output/pm2_hash_short.json"
expect_verify_fail "pm2 hash short" "$TMP/output/pm2_hash_short.json"

# decision-required ownership enforced on entries
jq '.pm2[0].owner = "keith"' "$TMP/output/missing_field.json" > "$TMP/output/pm2_bad_owner.json"
expect_verify_fail "pm2 bad owner" "$TMP/output/pm2_bad_owner.json"
jq '.pm2[0].disposition = "keep"' "$TMP/output/missing_field.json" > "$TMP/output/pm2_bad_disposition.json"
expect_verify_fail "pm2 bad disposition" "$TMP/output/pm2_bad_disposition.json"

# Missing referenced file
jq '.state[0].path = "/does/not/exist"' "$TMP/output/missing_field.json" > "$TMP/output/missing_file.json"
expect_verify_fail "missing referenced file" "$TMP/output/missing_file.json"

# Entry list elements must be the correct shape
jq '.cron.commands = [123]' "$TMP/output/missing_field.json" > "$TMP/output/cron_bad_command.json"
expect_verify_fail "cron non-string command" "$TMP/output/cron_bad_command.json"

jq '.mcp.servers = [123]' "$TMP/output/missing_field.json" > "$TMP/output/mcp_bad_server.json"
expect_verify_fail "mcp non-object server" "$TMP/output/mcp_bad_server.json"

for section in cron mcp; do
  for field in path hash owner disposition; do
    jq "del(.${section}.${field})" "$TMP/output/missing_field.json" > "$TMP/output/${section}_missing_${field}.json"
    expect_verify_fail "$section missing $field" "$TMP/output/${section}_missing_${field}.json"
  done
done

jq '.mcp.servers = [{name:"k2b"}]' "$TMP/output/missing_field.json" > "$TMP/output/mcp_missing_server_detail.json"
expect_verify_fail "mcp missing server detail" "$TMP/output/mcp_missing_server_detail.json"

jq '.pm2[0].env_var_names = [123]' "$TMP/output/missing_field.json" > "$TMP/output/pm2_bad_env.json"
expect_verify_fail "pm2 non-string env var name" "$TMP/output/pm2_bad_env.json"

# ---------------------------------------------------------------------------
# Regression: cron commands must be redacted while preserving identity
# ---------------------------------------------------------------------------

mkdir -p "$TMP/secret/pm2" "$TMP/secret/launchd" "$TMP/secret/state"
printf '{"name":"x"}\n' > "$TMP/secret/pm2/d.pm2"
printf '<?xml version="1.0"?><plist><dict><key>Label</key><string>x</string></dict></plist>\n' > "$TMP/secret/launchd/x.plist"
printf '{}' > "$TMP/secret/state/s.json"

SECRET1="s3cr3t-value-1"
SECRET2="s3cr3t-value-2"
SECRET3="s3cr3t-value-3"
SECRET4="s3cr3t-value-4"
SECRET5="s3cr3t-value-5"
SECRET6="s3cr3t-value-6"
TOKEN7="tk_7xyz789"
SECRET8="synthetic-short-header"

cat > "$TMP/secret/crontab" <<EOF
0 2 * * * /opt/k2b/job.sh --token ${SECRET1}
0 3 * * * /opt/k2b/job.sh --token=${SECRET2}
0 4 * * * API_KEY=${SECRET3} /opt/k2b/job.sh
0 5 * * * export TOKEN=${SECRET4} /opt/k2b/job.sh
0 6 * * * curl -H "Authorization: Bearer ${SECRET5}" https://example.com
0 7 * * * /opt/k2b/job.sh --api_key ${SECRET6}
0 8 * * * /opt/k2b/job.sh --url https://user:pass@example.com/path
0 9 * * * /opt/k2b/job.sh ${TOKEN7}
0 10 * * * curl -H "X-Api-Key: ${SECRET8}" https://example.com
EOF

K2B_INVENTORY_PM2_DIR="$TMP/secret/pm2" \
K2B_INVENTORY_LAUNCHD_DIR="$TMP/secret/launchd" \
K2B_INVENTORY_CRON_FILE="$TMP/secret/crontab" \
K2B_INVENTORY_STATE_DIR="$TMP/secret/state" \
K2B_INVENTORY_MCP_FILE="$TMP/secret/nope" \
"$SCRIPT" --host local --output "$TMP/output/secret_inventory.json"

SECRET_OUT="$TMP/output/secret_inventory.json"

# All secret values must be absent from the entire manifest
for secret in "$SECRET1" "$SECRET2" "$SECRET3" "$SECRET4" "$SECRET5" "$SECRET6" "$TOKEN7" "$SECRET8"; do
  if grep -qF "$secret" "$SECRET_OUT"; then
    echo "FAIL: secret value leaked into manifest: $secret"
    exit 1
  fi
done

# Command identity must be preserved
jq -e '.cron.commands | length == 9' "$SECRET_OUT" >/dev/null || { echo "FAIL: secret cron command count"; exit 1; }
jq -e '.cron.commands | map(select(contains("/opt/k2b/job.sh"))) | length == 7' "$SECRET_OUT" >/dev/null || { echo "FAIL: cron command identity lost"; exit 1; }
jq -e '.cron.commands | map(select(contains("curl"))) | length == 2' "$SECRET_OUT" >/dev/null || { echo "FAIL: curl command identity lost"; exit 1; }
jq -e '.cron.commands | map(select(contains("example.com"))) | length == 3' "$SECRET_OUT" >/dev/null || { echo "FAIL: cron host identity lost"; exit 1; }

# Specific redaction shapes
jq -e '.cron.commands[0] | contains("--token [REDACTED]")' "$SECRET_OUT" >/dev/null || { echo "FAIL: --token value not redacted"; exit 1; }
jq -e '.cron.commands[1] | contains("--token=[REDACTED]")' "$SECRET_OUT" >/dev/null || { echo "FAIL: --token= value not redacted"; exit 1; }
jq -e '.cron.commands[2] | contains("API_KEY=[REDACTED]")' "$SECRET_OUT" >/dev/null || { echo "FAIL: API_KEY assignment not redacted"; exit 1; }
jq -e '.cron.commands[5] | contains("--api_key [REDACTED]")' "$SECRET_OUT" >/dev/null || { echo "FAIL: --api_key value not redacted"; exit 1; }
jq -e '.cron.commands[7] | contains("[REDACTED]")' "$SECRET_OUT" >/dev/null || { echo "FAIL: bare token-like argument not redacted"; exit 1; }
jq -e '.cron.commands[8] | contains("[REDACTED]")' "$SECRET_OUT" >/dev/null || { echo "FAIL: X-Api-Key header not redacted"; exit 1; }

# Empty/comment-only crontab must not crash or leak raw content
printf '# comment\n\n' > "$TMP/secret/empty_cron"
K2B_INVENTORY_PM2_DIR="$TMP/secret/pm2" \
K2B_INVENTORY_LAUNCHD_DIR="$TMP/secret/launchd" \
K2B_INVENTORY_CRON_FILE="$TMP/secret/empty_cron" \
K2B_INVENTORY_STATE_DIR="$TMP/secret/state" \
K2B_INVENTORY_MCP_FILE="$TMP/secret/nope" \
"$SCRIPT" --host local --output "$TMP/output/empty_cron_inventory.json"
jq -e '.cron.commands == []' "$TMP/output/empty_cron_inventory.json" >/dev/null || { echo "FAIL: empty cron commands should be []"; exit 1; }

# Without an explicit fixture, inventory the installed user crontab through a
# private snapshot and keep environment declarations out of the manifest.
mkdir -p "$TMP/fake-bin"
cat > "$TMP/fake-bin/crontab" <<'EOF'
#!/usr/bin/env bash
if [ "${1:-}" != "-l" ]; then
  exit 2
fi
printf 'PATH=/trusted/bin:/usr/bin\nMAILTO=private@example.com\n15 3 * * * /opt/k2b/discover.sh\n'
EOF
chmod +x "$TMP/fake-bin/crontab"
PATH="$TMP/fake-bin:$PATH" \
K2B_INVENTORY_PM2_DIR="$TMP/secret/pm2" \
K2B_INVENTORY_LAUNCHD_DIR="$TMP/secret/launchd" \
K2B_INVENTORY_STATE_DIR="$TMP/secret/state" \
K2B_INVENTORY_MCP_FILE="$TMP/secret/nope" \
"$SCRIPT" --host local --output "$TMP/output/installed_cron_inventory.json"
jq -e '.cron.path == "crontab://current-user" and (.cron.hash | length == 64) and .cron.commands == ["/opt/k2b/discover.sh"]' \
  "$TMP/output/installed_cron_inventory.json" >/dev/null \
  || { echo "FAIL: installed crontab inventory missing or malformed"; exit 1; }
if grep -q 'private@example.com\|/trusted/bin:/usr/bin' "$TMP/output/installed_cron_inventory.json"; then
  echo "FAIL: installed crontab environment leaked"
  exit 1
fi
PATH="$TMP/fake-bin:$PATH" "$SCRIPT" --verify --output "$TMP/output/installed_cron_inventory.json"

cat > "$TMP/fake-bin/crontab-error" <<'EOF'
#!/usr/bin/env bash
printf 'crontab: permission denied\n' >&2
exit 1
EOF
chmod +x "$TMP/fake-bin/crontab-error"
set +e
K2B_INVENTORY_CRONTAB_BIN="$TMP/fake-bin/crontab-error" \
K2B_INVENTORY_PM2_DIR="$TMP/secret/pm2" \
K2B_INVENTORY_LAUNCHD_DIR="$TMP/secret/launchd" \
K2B_INVENTORY_STATE_DIR="$TMP/secret/state" \
K2B_INVENTORY_MCP_FILE="$TMP/secret/nope" \
"$SCRIPT" --host local --output "$TMP/output/crontab-error-inventory.json" 2>"$TMP/crontab-error.err"
crontab_error_rc=$?
set -e
[ "$crontab_error_rc" = "2" ] || { echo "FAIL: crontab rc1 error should fail inventory"; exit 1; }
[ ! -e "$TMP/output/crontab-error-inventory.json" ] || { echo "FAIL: crontab rc1 error wrote inventory"; exit 1; }

set +e
K2B_INVENTORY_CRONTAB_BIN="$TMP/missing-crontab" \
K2B_INVENTORY_PM2_DIR="$TMP/secret/pm2" \
K2B_INVENTORY_LAUNCHD_DIR="$TMP/secret/launchd" \
K2B_INVENTORY_STATE_DIR="$TMP/secret/state" \
K2B_INVENTORY_MCP_FILE="$TMP/secret/nope" \
"$SCRIPT" --host local --output "$TMP/output/crontab-missing-inventory.json" 2>"$TMP/crontab-missing.err"
crontab_missing_rc=$?
set -e
[ "$crontab_missing_rc" = "2" ] || { echo "FAIL: unavailable crontab should fail inventory"; exit 1; }
[ ! -e "$TMP/output/crontab-missing-inventory.json" ] || { echo "FAIL: unavailable crontab wrote inventory"; exit 1; }

set +e
K2B_INVENTORY_CRONTAB_BIN="$TMP/fake-bin/crontab-error" \
"$SCRIPT" --verify --output "$TMP/output/installed_cron_inventory.json" 2>"$TMP/crontab-verify-error.err"
crontab_verify_error_rc=$?
set -e
[ "$crontab_verify_error_rc" = "2" ] || { echo "FAIL: crontab rc1 error should fail verification"; exit 1; }

jq '.cron.hash = null' "$TMP/output/installed_cron_inventory.json" > "$TMP/output/installed_cron_null_hash.json"
set +e
PATH="$TMP/fake-bin:$PATH" "$SCRIPT" --verify --output "$TMP/output/installed_cron_null_hash.json" 2>"$TMP/crontab-null-hash.err"
crontab_null_hash_rc=$?
set -e
[ "$crontab_null_hash_rc" = "2" ] || { echo "FAIL: virtual crontab source without hash should fail verification"; exit 1; }

cat > "$TMP/fake-bin/crontab-none" <<'EOF'
#!/usr/bin/env bash
printf 'crontab: no crontab for fixture-user\n' >&2
exit 1
EOF
chmod +x "$TMP/fake-bin/crontab-none"
K2B_INVENTORY_CRONTAB_BIN="$TMP/fake-bin/crontab-none" \
K2B_INVENTORY_PM2_DIR="$TMP/secret/pm2" \
K2B_INVENTORY_LAUNCHD_DIR="$TMP/secret/launchd" \
K2B_INVENTORY_STATE_DIR="$TMP/secret/state" \
K2B_INVENTORY_MCP_FILE="$TMP/secret/nope" \
"$SCRIPT" --host local --output "$TMP/output/no-crontab-inventory.json"
jq -e '.cron.path == "crontab://current-user" and (.cron.hash | length == 64) and .cron.commands == []' \
  "$TMP/output/no-crontab-inventory.json" >/dev/null \
  || { echo "FAIL: explicit no-crontab result should be recorded with an empty-source hash"; exit 1; }
K2B_INVENTORY_CRONTAB_BIN="$TMP/fake-bin/crontab-none" \
"$SCRIPT" --verify --output "$TMP/output/no-crontab-inventory.json"

# The SJM source-only host must never place inventory state in the synced vault.
mkdir -p "$TMP/sjm-vault"
set +e
K2B_VAULT_PATH="$TMP/sjm-vault" \
K2B_INVENTORY_PM2_DIR="$TMP/secret/pm2" \
K2B_INVENTORY_LAUNCHD_DIR="$TMP/secret/launchd" \
K2B_INVENTORY_CRON_FILE="$TMP/secret/empty_cron" \
K2B_INVENTORY_STATE_DIR="$TMP/secret/state" \
K2B_INVENTORY_MCP_FILE="$TMP/secret/nope" \
"$SCRIPT" --host sjm --output "$TMP/sjm-vault/.staging/inventory.json" 2>"$TMP/sjm-output.err"
sjm_output_rc=$?
set -e
[ "$sjm_output_rc" = "2" ] || { echo "FAIL: SJM vault output should be rejected"; exit 1; }
[ ! -e "$TMP/sjm-vault/.staging/inventory.json" ] || { echo "FAIL: rejected SJM inventory wrote to vault"; exit 1; }
grep -q 'outside K2B_VAULT_PATH' "$TMP/sjm-output.err" \
  || { echo "FAIL: SJM vault-output rejection missing"; cat "$TMP/sjm-output.err"; exit 1; }

# Malformed active cron/MCP definitions must fail closed and produce no manifest.
printf 'this is not a crontab entry\n' > "$TMP/secret/malformed_cron"
set +e
K2B_INVENTORY_PM2_DIR="$TMP/secret/pm2" \
K2B_INVENTORY_LAUNCHD_DIR="$TMP/secret/launchd" \
K2B_INVENTORY_CRON_FILE="$TMP/secret/malformed_cron" \
K2B_INVENTORY_STATE_DIR="$TMP/secret/state" \
K2B_INVENTORY_MCP_FILE="$TMP/secret/nope" \
"$SCRIPT" --host local --output "$TMP/output/malformed_cron_inventory.json" 2>"$TMP/malformed-cron.err"
malformed_cron_rc=$?
set -e
[ "$malformed_cron_rc" -ne 0 ] || { echo "FAIL: malformed cron should fail closed"; exit 1; }
[ ! -e "$TMP/output/malformed_cron_inventory.json" ] || { echo "FAIL: malformed cron wrote inventory"; exit 1; }

printf '{not json\n' > "$TMP/secret/malformed_mcp.json"
set +e
K2B_INVENTORY_PM2_DIR="$TMP/secret/pm2" \
K2B_INVENTORY_LAUNCHD_DIR="$TMP/secret/launchd" \
K2B_INVENTORY_CRON_FILE="$TMP/secret/empty_cron" \
K2B_INVENTORY_STATE_DIR="$TMP/secret/state" \
K2B_INVENTORY_MCP_FILE="$TMP/secret/malformed_mcp.json" \
"$SCRIPT" --host local --output "$TMP/output/malformed_mcp_inventory.json" 2>"$TMP/malformed-mcp.err"
malformed_mcp_rc=$?
set -e
[ "$malformed_mcp_rc" -ne 0 ] || { echo "FAIL: malformed MCP should fail closed"; exit 1; }
[ ! -e "$TMP/output/malformed_mcp_inventory.json" ] || { echo "FAIL: malformed MCP wrote inventory"; exit 1; }

echo "PASS: inventory-retired-runtime.test.sh"
