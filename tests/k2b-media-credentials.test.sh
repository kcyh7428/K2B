#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SKILL="$REPO_ROOT/.agents/skills/k2b-media-generator/SKILL.md"
WHISPER="$REPO_ROOT/scripts/yt-transcribe-whisper.sh"
HOSTINGER="$REPO_ROOT/scripts/run-hostinger-mcp.sh"
PRIVATE_ENV="$REPO_ROOT/scripts/lib/private-env.sh"

TEST_ROOT="$(mktemp -d)"
trap 'rm -rf "$TEST_ROOT"' EXIT

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

if rg -q 'k2b-remote/\.env|Operations Console' "$SKILL"; then
  echo "media skill still names a retired runtime or console" >&2
  exit 1
fi

rg -q '~/.k2b-env' "$SKILL"
rg -q 'disabled unless Keith explicitly requests' "$SKILL"
rg -q 'scripts/lib/private-env\.sh' "$SKILL"
rg -q 'regular, non-symlink file owned by the current user with exact mode `0600`' "$SKILL"

if rg -q 'source "\$HOME/\.k2b-env"|\. "\$HOME/\.k2b-env"|source "\$HOME/\.zshenv"' \
  "$SKILL" "$WHISPER" "$HOSTINGER"; then
  fail "a live media path still sources a credential or startup file directly"
fi

mkdir -p "$TEST_ROOT/bin"
printf 'audio' > "$TEST_ROOT/input.mp3"

# Use controlled stand-ins so the credential checks are exercised without any
# provider call. The fake curl also confirms the secret travelled over stdin,
# not in its argument vector.
cat > "$TEST_ROOT/bin/ffprobe" <<'EOF'
#!/usr/bin/env bash
echo 1
EOF
cat > "$TEST_ROOT/bin/curl" <<'EOF'
#!/usr/bin/env bash
config="$(cat)"
[[ "$config" == *'Authorization: Bearer test-groq'* ]] || exit 91
[[ "$*" != *test-groq* ]] || exit 92
echo 'test transcript'
EOF
cat > "$TEST_ROOT/bin/npx" <<'EOF'
#!/usr/bin/env bash
[[ "${API_TOKEN:-}" == 'test-hostinger' ]] || exit 93
[[ -z "${HOSTINGER_API_TOKEN+x}" ]] || exit 94
echo 'hostinger started'
EOF
chmod +x "$TEST_ROOT/bin/ffprobe" "$TEST_ROOT/bin/curl" "$TEST_ROOT/bin/npx"

VALID_ENV="$TEST_ROOT/valid.env"
cat > "$VALID_ENV" <<'EOF'
GROQ_API_KEY='test-groq'
HOSTINGER_API_TOKEN='test-hostinger'
EOF
chmod 600 "$VALID_ENV"

output=$(env -u GROQ_API_KEY \
  HOME="$TEST_ROOT" K2B_ENV_FILE="$VALID_ENV" PATH="$TEST_ROOT/bin:$PATH" \
  bash "$WHISPER" "$TEST_ROOT/input.mp3")
[[ "$output" == 'test transcript' ]] || fail "Whisper did not load a valid private credential file"

output=$(env -u HOSTINGER_API_TOKEN \
  HOME="$TEST_ROOT" K2B_ENV_FILE="$VALID_ENV" PATH="$TEST_ROOT/bin:$PATH" \
  bash "$HOSTINGER")
[[ "$output" == 'hostinger started' ]] || fail "Hostinger did not load a valid private credential file"

INSECURE_ENV="$TEST_ROOT/insecure.env"
cp "$VALID_ENV" "$INSECURE_ENV"
chmod 644 "$INSECURE_ENV"
if env -u GROQ_API_KEY \
  HOME="$TEST_ROOT" K2B_ENV_FILE="$INSECURE_ENV" PATH="$TEST_ROOT/bin:$PATH" \
  bash "$WHISPER" "$TEST_ROOT/input.mp3" >"$TEST_ROOT/out" 2>"$TEST_ROOT/err"; then
  fail "Whisper accepted a mode-0644 credential file"
fi
rg -q 'regular, non-symlink file owned by the current user with mode 0600' "$TEST_ROOT/err" || \
  fail "Whisper did not explain the credential-file rejection"

LINK_ENV="$TEST_ROOT/link.env"
ln -s "$VALID_ENV" "$LINK_ENV"
if env -u HOSTINGER_API_TOKEN \
  HOME="$TEST_ROOT" K2B_ENV_FILE="$LINK_ENV" PATH="$TEST_ROOT/bin:$PATH" \
  bash "$HOSTINGER" >"$TEST_ROOT/out" 2>"$TEST_ROOT/err"; then
  fail "Hostinger accepted a symlink credential file"
fi
rg -q 'regular, non-symlink file owned by the current user with mode 0600' "$TEST_ROOT/err" || \
  fail "Hostinger did not explain the symlink rejection"

MISSING_TOKEN_ENV="$TEST_ROOT/missing-token.env"
echo "UNRELATED_VALUE='present'" > "$MISSING_TOKEN_ENV"
chmod 600 "$MISSING_TOKEN_ENV"
if env -u HOSTINGER_API_TOKEN \
  HOME="$TEST_ROOT" K2B_ENV_FILE="$MISSING_TOKEN_ENV" PATH="$TEST_ROOT/bin:$PATH" \
  bash "$HOSTINGER" >"$TEST_ROOT/out" 2>"$TEST_ROOT/err"; then
  fail "Hostinger started without HOSTINGER_API_TOKEN"
fi
rg -q 'HOSTINGER_API_TOKEN not set' "$TEST_ROOT/err" || fail "Hostinger missing-token error is unclear"

python3 - "$REPO_ROOT/.mcp.json" <<'PY'
import json
import sys

config = json.load(open(sys.argv[1], encoding="utf-8"))
hostinger = config["mcpServers"]["hostinger"]
assert hostinger["command"] == "bash"
command = hostinger["args"][-1]
assert "K2B_PROJECT_ROOT" in command
assert "$HOME/Projects/K2B" in command
assert "/Users/" not in command
PY

rg -q 'hostinger-api-mcp@1\.58\.0' "$HOSTINGER" || fail "Hostinger MCP package is not pinned"
bash -n "$PRIVATE_ENV" "$WHISPER" "$HOSTINGER"

echo "k2b media credential contract: PASS"
