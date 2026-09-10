#!/usr/bin/env bash
# Inventory retired runtime state for K2B clean-slate removal.
# Records metadata, hashes, and sanitized config only. Never records secrets,
# credentials, env values, cookies, OAuth material, or raw logs.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

HOST="local"
OUTPUT=""
FIXTURES=0
VERIFY=0

usage() {
  cat <<'EOF'
Usage: scripts/inventory-retired-runtime.sh [--host local|home|sjm] [--output PATH] [--fixtures] [--verify]

Env:
  K2B_INVENTORY_OUTPUT      output JSON path
  K2B_INVENTORY_COMMIT      source commit hash (default: current HEAD)
  K2B_INVENTORY_PM2_DIR     PM2 dump directory
  K2B_INVENTORY_LAUNCHD_DIR launchd plist directory
  K2B_INVENTORY_CRON_FILE   crontab file path
  K2B_INVENTORY_MCP_FILE    MCP config file path
  K2B_INVENTORY_STATE_DIR   state directory to inventory
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --host)
      HOST="${2:-local}"
      shift 2
      ;;
    --output)
      OUTPUT="${2:-}"
      shift 2
      ;;
    --fixtures) FIXTURES=1; shift ;;
    --verify) VERIFY=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "inventory-retired-runtime: unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done

if [ -z "$OUTPUT" ]; then
  OUTPUT="${K2B_INVENTORY_OUTPUT:-}"
fi
if [ -z "$OUTPUT" ]; then
  echo "inventory-retired-runtime: --output or K2B_INVENTORY_OUTPUT is required" >&2
  exit 2
fi

if [ "$VERIFY" -eq 1 ]; then
  if [ ! -f "$OUTPUT" ]; then
    echo "inventory-retired-runtime: missing inventory: $OUTPUT" >&2
    exit 2
  fi
  python3 - "$OUTPUT" <<'PY'
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys

path = sys.argv[1]
try:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
except (OSError, json.JSONDecodeError) as exc:
    print(f"inventory-retired-runtime: cannot parse inventory: {exc}", file=sys.stderr)
    sys.exit(2)

status = 0


def fail(msg: str):
    global status
    print(f"inventory-retired-runtime: {msg}", file=sys.stderr)
    status = 2


def require_string(obj, key, context):
    val = obj.get(key)
    if not isinstance(val, str) or not val:
        fail(f"{context} missing or invalid {key}")
        return None
    return val


def require_hex_hash(value, context):
    if not isinstance(value, str) or len(value) != 64:
        fail(f"{context} invalid hash length")
        return False
    try:
        int(value, 16)
        return True
    except ValueError:
        fail(f"{context} hash is not hexadecimal")
        return False


# Top-level schema
if not isinstance(data, dict):
    fail("manifest is not a JSON object")
    sys.exit(2)

if data.get("schema_version") != "v1":
    fail("schema_version must be 'v1'")

for key in ("host", "host_kind", "created_at", "source_commit"):
    require_string(data, key, "manifest")

if data.get("ownership_unknown_disposition") != "decision-required":
    fail("ownership_unknown_disposition must be 'decision-required'")


def require_list(key):
    val = data.get(key)
    if not isinstance(val, list):
        fail(f"{key} must be a list")
        return []
    return val


def require_dict(key):
    val = data.get(key)
    if not isinstance(val, dict):
        fail(f"{key} must be an object")
        return {}
    return val


# PM2 section
pm2 = require_list("pm2")
for idx, entry in enumerate(pm2):
    ctx = f"pm2[{idx}]"
    if not isinstance(entry, dict):
        fail(f"{ctx} is not an object")
        continue
    for key in ("name", "path", "command", "hash", "owner", "disposition", "env_var_names"):
        if key not in entry:
            fail(f"{ctx} missing {key}")
    if entry.get("owner") != "unknown":
        fail(f"{ctx} owner must be 'unknown'")
    if entry.get("disposition") != "decision-required":
        fail(f"{ctx} disposition must be 'decision-required'")
    require_hex_hash(entry.get("hash"), ctx)
    env_names = entry.get("env_var_names")
    if not isinstance(env_names, list):
        fail(f"{ctx} env_var_names must be a list")
    else:
        for vidx, vname in enumerate(env_names):
            if not isinstance(vname, str):
                fail(f"{ctx} env_var_names[{vidx}] is not a string")

# Launchd section
launchd = require_list("launchd")
for idx, entry in enumerate(launchd):
    ctx = f"launchd[{idx}]"
    if not isinstance(entry, dict):
        fail(f"{ctx} is not an object")
        continue
    for key in ("label", "path", "plist_hash", "owner", "disposition"):
        if key not in entry:
            fail(f"{ctx} missing {key}")
    if entry.get("owner") != "unknown":
        fail(f"{ctx} owner must be 'unknown'")
    if entry.get("disposition") != "decision-required":
        fail(f"{ctx} disposition must be 'decision-required'")
    require_hex_hash(entry.get("plist_hash"), ctx)

# Cron section
cron = require_dict("cron")
for key in ("path", "hash", "owner", "disposition", "commands"):
    if key not in cron:
        fail(f"cron missing {key}")
if cron.get("owner") != "unknown":
    fail("cron owner must be 'unknown'")
if cron.get("disposition") != "decision-required":
    fail("cron disposition must be 'decision-required'")
require_string(cron, "path", "cron")
if cron.get("hash") is not None:
    require_hex_hash(cron.get("hash"), "cron")
if "commands" not in cron:
    fail("cron missing commands")
elif not isinstance(cron.get("commands"), list):
    fail("cron.commands must be a list")
else:
    for cidx, cmd in enumerate(cron["commands"]):
        if not isinstance(cmd, str) or not cmd:
            fail(f"cron.commands[{cidx}] is not a non-empty string")

# MCP section
mcp = require_dict("mcp")
for key in ("path", "hash", "owner", "disposition", "servers", "env_var_names"):
    if key not in mcp:
        fail(f"mcp missing {key}")
if mcp.get("owner") != "unknown":
    fail("mcp owner must be 'unknown'")
if mcp.get("disposition") != "decision-required":
    fail("mcp disposition must be 'decision-required'")
require_string(mcp, "path", "mcp")
if mcp.get("hash") is not None:
    require_hex_hash(mcp.get("hash"), "mcp")
for key in ("servers", "env_var_names"):
    if key not in mcp:
        fail(f"mcp missing {key}")
    elif not isinstance(mcp.get(key), list):
        fail(f"mcp.{key} must be a list")
    else:
        for vidx, value in enumerate(mcp[key]):
            if key == "env_var_names":
                if not isinstance(value, str):
                    fail(f"mcp.{key}[{vidx}] is not a string")
                continue
            ctx = f"mcp.servers[{vidx}]"
            if not isinstance(value, dict):
                fail(f"{ctx} is not an object")
                continue
            for field in ("name", "command", "env_var_names"):
                if field not in value:
                    fail(f"{ctx} missing {field}")
            require_string(value, "name", ctx)
            require_string(value, "command", ctx)
            names = value.get("env_var_names")
            if not isinstance(names, list) or any(
                not isinstance(name, str) for name in names
            ):
                fail(f"{ctx} env_var_names must be a list of strings")

# State section
state = require_list("state")
for idx, entry in enumerate(state):
    ctx = f"state[{idx}]"
    if not isinstance(entry, dict):
        fail(f"{ctx} is not an object")
        continue
    for key in ("path", "hash", "owner", "disposition"):
        if key not in entry:
            fail(f"{ctx} missing {key}")
    if entry.get("owner") != "unknown":
        fail(f"{ctx} owner must be 'unknown'")
    if entry.get("disposition") != "decision-required":
        fail(f"{ctx} disposition must be 'decision-required'")
    require_hex_hash(entry.get("hash"), ctx)


def verify(section: str, key: str):
    global status
    for idx, entry in enumerate(data.get(section, [])):
        if not isinstance(entry, dict):
            continue
        file_path = entry.get("path", "")
        expected = entry.get(key, "")
        if not file_path or not expected:
            continue
        try:
            with open(file_path, "rb") as f:
                actual = hashlib.sha256(f.read()).hexdigest()
        except OSError as exc:
            fail(f"cannot read {file_path}: {exc}")
            continue
        if actual != expected:
            fail(f"hash mismatch for {file_path}")


def verify_source(section: str):
    entry = data.get(section)
    if not isinstance(entry, dict):
        return
    file_path = entry.get("path")
    expected = entry.get("hash")
    if not isinstance(file_path, str) or not file_path:
        return
    if section == "cron" and file_path == "crontab://current-user":
        crontab_bin = os.environ.get("K2B_INVENTORY_CRONTAB_BIN") or shutil.which("crontab")
        if not crontab_bin:
            fail("cannot verify installed crontab: crontab executable unavailable")
            return
        try:
            proc = subprocess.run(
                [crontab_bin, "-l"], capture_output=True, check=False
            )
        except OSError as exc:
            fail(f"cannot read installed crontab: {exc}")
            return
        no_crontab = (
            proc.returncode == 1
            and not proc.stdout
            and re.fullmatch(
                rb"\s*(?:crontab:\s*)?no crontab for [^\r\n]+\s*",
                proc.stderr,
                re.IGNORECASE,
            )
            is not None
        )
        if proc.returncode != 0 and not no_crontab:
            fail(f"cannot read installed crontab: exit {proc.returncode}")
            return
        actual = hashlib.sha256(proc.stdout).hexdigest()
        if expected is None:
            fail("installed crontab manifest must carry a source hash")
            return
    else:
        if expected is None:
            if os.path.exists(file_path):
                fail(f"{section} source exists but manifest has no hash: {file_path}")
            return
        if not isinstance(expected, str) or not require_hex_hash(expected, section):
            return
        try:
            with open(file_path, "rb") as f:
                actual = hashlib.sha256(f.read()).hexdigest()
        except OSError as exc:
            fail(f"cannot read {file_path}: {exc}")
            return
    if actual != expected:
        fail(f"hash mismatch for {file_path}")


if status != 0:
    sys.exit(2)

verify("pm2", "hash")
verify("launchd", "plist_hash")
verify("state", "hash")
verify_source("cron")
verify_source("mcp")

if status != 0:
    sys.exit(2)
print(f"inventory-retired-runtime: verified {path}")
PY
  exit $?
fi

now_utc() {
  python3 - <<'PY'
from datetime import datetime, timezone
print(datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z"))
PY
}

sha256_file() {
  python3 -c 'import hashlib,sys; print(hashlib.sha256(open(sys.argv[1],"rb").read()).hexdigest())' "$1"
}

json_escape() {
  python3 -c 'import json,sys; print(json.dumps(sys.stdin.read()), end="")'
}

sanitize_command() {
  python3 -c '
import re
import sys

text = sys.stdin.read()
text = re.sub(r"([A-Za-z][A-Za-z0-9+.-]*://)[^/@\s]+@", r"\1[REDACTED]@", text)
text = re.sub(
    r"(?i)\b((?:[A-Z0-9_]*(?:API_?KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL|AUTH|COOKIE|OAUTH)[A-Z0-9_]*)=)([^\s]+)",
    r"\1[REDACTED]",
    text,
)
text = re.sub(
    r"(?i)(--(?:api[-_]?key|token|secret|password|credential|auth|cookie|oauth)(?:=|\s+))([^\s]+)",
    r"\1[REDACTED]",
    text,
)
text = re.sub(
    r"(?i)(\b(?:authorization|authentication|x-api-key|api-key|cookie|set-cookie)"
    r"\s*:\s*)(?:bearer\s+)?([^\s]+)",
    r"\1[REDACTED]",
    text,
)
text = re.sub(
    r"\b(?:sk-[A-Za-z0-9_-]{8,}|ghp_[A-Za-z0-9]{36}|glpat-[A-Za-z0-9-]{20,}|AKIA[0-9A-Z]{16}|(?:tk|tok)_[A-Za-z0-9_-]{6,}|[A-Za-z0-9+/]{40,}={0,2})\b",
    "[REDACTED]",
    text,
)
sys.stdout.write(text)
'
}

case "$HOST" in
  local|home|sjm) ;;
  *) echo "inventory-retired-runtime: unsupported host kind: $HOST" >&2; exit 2 ;;
esac
host="$(hostname -s)"
created_at="$(now_utc)"
commit="${K2B_INVENTORY_COMMIT:-$(git -C "$REPO_ROOT" rev-parse HEAD 2>/dev/null || echo "unknown")}"

# Directories / files (env overrides retained for tests)
PM2_DIR="${K2B_INVENTORY_PM2_DIR:-${PM2_HOME:-$HOME/.pm2}}"
LAUNCHD_DIR="${K2B_INVENTORY_LAUNCHD_DIR:-$HOME/Library/LaunchAgents}"
CRON_FILE="${K2B_INVENTORY_CRON_FILE:-}"
CRON_SOURCE_LABEL="$CRON_FILE"
CRONTAB_BIN="${K2B_INVENTORY_CRONTAB_BIN:-}"
MCP_FILE="${K2B_INVENTORY_MCP_FILE:-$HOME/.config/k2b/mcp.json}"
STATE_DIR="${K2B_INVENTORY_STATE_DIR:-$REPO_ROOT/.retired-state}"

if [ "$HOST" = "sjm" ]; then
  K2B_VAULT_PATH_EFFECTIVE="${K2B_VAULT_PATH:-$HOME/Projects/K2B-Vault}"
  if ! python3 - "$OUTPUT" "$K2B_VAULT_PATH_EFFECTIVE" <<'PY'
import os
import sys

output = os.path.realpath(os.path.abspath(os.path.expanduser(sys.argv[1])))
vault = os.path.realpath(os.path.abspath(os.path.expanduser(sys.argv[2])))
if output == vault or output.startswith(vault.rstrip(os.sep) + os.sep):
    print(
        "inventory-retired-runtime: SJM output must remain outside K2B_VAULT_PATH",
        file=sys.stderr,
    )
    raise SystemExit(1)
PY
  then
    exit 2
  fi
fi

out_dir="$(dirname "$OUTPUT")"
mkdir -p "$out_dir"

tmp_out="$(mktemp "$out_dir/.inventory-XXXXXX.json")"
cron_capture=""
cron_error=""
cleanup() {
  rm -f "$tmp_out"
  if [ -n "$cron_capture" ]; then
    rm -f "$cron_capture"
  fi
  if [ -n "$cron_error" ]; then
    rm -f "$cron_error"
  fi
}
trap cleanup EXIT

if [ -z "$CRON_FILE" ]; then
  CRON_SOURCE_LABEL="crontab://current-user"
  if [ -z "$CRONTAB_BIN" ]; then
    CRONTAB_BIN="$(command -v crontab || true)"
  fi
  if [ -z "$CRONTAB_BIN" ] || [ ! -x "$CRONTAB_BIN" ]; then
    echo "inventory-retired-runtime: crontab executable unavailable" >&2
    exit 2
  fi
  cron_capture="$(mktemp "${TMPDIR:-/tmp}/k2b-crontab.XXXXXX")"
  cron_error="$(mktemp "${TMPDIR:-/tmp}/k2b-crontab-error.XXXXXX")"
  chmod 600 "$cron_capture" "$cron_error"
  set +e
  "$CRONTAB_BIN" -l >"$cron_capture" 2>"$cron_error"
  cron_rc=$?
  set -e
  if [ "$cron_rc" -eq 1 ]; then
    if [ -s "$cron_capture" ] || ! python3 - "$cron_error" <<'PY'
import re
import sys

stderr = open(sys.argv[1], "rb").read()
if re.fullmatch(
    rb"\s*(?:crontab:\s*)?no crontab for [^\r\n]+\s*",
    stderr,
    re.IGNORECASE,
) is None:
    raise SystemExit(1)
PY
    then
      echo "inventory-retired-runtime: cannot read installed crontab" >&2
      exit 2
    fi
  elif [ "$cron_rc" -ne 0 ]; then
    echo "inventory-retired-runtime: cannot read installed crontab" >&2
    exit 2
  fi
  CRON_FILE="$cron_capture"
fi

{
  printf '{\n'
  printf '  "schema_version": "v1",\n'
  printf '  "host": %s,\n' "$(printf '%s' "$host" | json_escape)"
  printf '  "host_kind": %s,\n' "$(printf '%s' "$HOST" | json_escape)"
  printf '  "created_at": %s,\n' "$(printf '%s' "$created_at" | json_escape)"
  printf '  "source_commit": %s,\n' "$(printf '%s' "$commit" | json_escape)"
  printf '  "ownership_unknown_disposition": "decision-required",\n'

  # PM2
  printf '  "pm2": [\n'
  first=1
  if [ -d "$PM2_DIR" ]; then
    for f in "$PM2_DIR"/*.pm2; do
      [ -e "$f" ] || continue
      hash="$(sha256_file "$f")"
      if ! jq -e 'if type == "array" then all(.[]; type == "object") else type == "object" end' "$f" >/dev/null 2>&1; then
        echo "inventory-retired-runtime: malformed PM2 dump: $f" >&2
        exit 2
      fi
      while IFS= read -r pm2_entry; do
        [ -n "$pm2_entry" ] || continue
        json_name="$(printf '%s' "$pm2_entry" | jq -r '.name // ""')"
        name="${json_name:-$(basename "$f" .pm2)}"
        cmd_raw="$(printf '%s' "$pm2_entry" | jq -er '(.cmd // .pm_exec_path // "unknown") | tostring | .[0:200]')"
        cmd="$(printf '%s' "$cmd_raw" | sanitize_command | json_escape)"
        env_keys="$(printf '%s' "$pm2_entry" | jq -e '(.env // {}) | if type == "object" then (keys | sort) else error("PM2 env must be an object") end')"
        if [ "$first" -eq 0 ]; then printf ',\n'; fi
        first=0
        printf '    {\n'
        printf '      "name": %s,\n' "$(printf '%s' "$name" | json_escape)"
        printf '      "path": %s,\n' "$(printf '%s' "$f" | json_escape)"
        printf '      "command": %s,\n' "$cmd"
        printf '      "hash": %s,\n' "$(printf '%s' "$hash" | json_escape)"
        printf '      "owner": "unknown",\n'
        printf '      "disposition": "decision-required",\n'
        printf '      "env_var_names": %s\n' "$env_keys"
        printf '    }'
      done < <(jq -c 'if type == "array" then .[] else . end' "$f")
    done
  fi
  printf '\n  ],\n'

  # Launchd
  printf '  "launchd": [\n'
  first=1
  if [ -d "$LAUNCHD_DIR" ]; then
    for f in "$LAUNCHD_DIR"/*.plist; do
      [ -e "$f" ] || continue
      label="$(basename "$f" .plist)"
      hash="$(sha256_file "$f")"
      if [ "$first" -eq 0 ]; then printf ',\n'; fi
      first=0
      printf '    {\n'
      printf '      "label": %s,\n' "$(printf '%s' "$label" | json_escape)"
      printf '      "path": %s,\n' "$(printf '%s' "$f" | json_escape)"
      printf '      "plist_hash": %s,\n' "$(printf '%s' "$hash" | json_escape)"
      printf '      "owner": "unknown",\n'
      printf '      "disposition": "decision-required"\n'
      printf '    }'
    done
  fi
  printf '\n  ],\n'

  # Cron
  printf '  "cron": {\n'
  cron_commands='[]'
  cron_hash='null'
  if [ -f "$CRON_FILE" ]; then
    cron_hash="$(printf '%s' "$(sha256_file "$CRON_FILE")" | json_escape)"
    cron_commands="$(python3 - "$CRON_FILE" <<'PY'
import json, re, sys

# Sensitive keywords for keys, option names, and header names.
_SECRET_KEY_RE = re.compile(
    r"API_?KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL|AUTH|COOKIE|OAUTH|BEARER|ACCESS_?KEY",
    re.I,
)
_ASSIGN_RE = re.compile(r'^([A-Za-z_][A-Za-z0-9_]*)=(.*)$')
_CRON_ENV_RE = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*\s*=')
_URL_RE = re.compile(r'^[a-zA-Z][a-zA-Z0-9+.-]*://')
_HEADER_RE = re.compile(
    r'^(Authorization|Authentication|X-Api-Key|Api-Key|Cookie|Set-Cookie|Bearer|Token)([\s:]|$)',
    re.I,
)
_SECRET_VALUE_RE = re.compile(
    r'\b(?:'
    r'sk-[a-zA-Z0-9]{8,}|'
    r'ghp_[A-Za-z0-9]{36}|'
    r'glpat-[A-Za-z0-9\-]{20}|'
    r'AKIA[0-9A-Z]{16}|'
    r'tk_[A-Za-z0-9]{6,}|'
    r'tok_[A-Za-z0-9]{6,}|'
    r'[A-Za-z0-9+/]{40,}={0,2}'
    r')\b'
)
_TOKEN_OPTION_NAMES = {
    "token", "api-key", "apikey", "api_token", "auth", "secret",
    "password", "credential", "key", "bearer", "access-key", "access_token",
    "access-token", "api_key",
}


def _option_name(tok: str) -> str:
    """Normalize an option token to a lookup key."""
    if tok.startswith("--"):
        name = tok[2:]
    elif tok.startswith("-"):
        name = tok[1:]
    else:
        return ""
    return name.replace("_", "-").lower()


def _is_token_option(tok: str) -> bool:
    name = _option_name(tok)
    if name in _TOKEN_OPTION_NAMES:
        return True
    # Also treat options whose names contain secret keywords as token-like.
    if name and _SECRET_KEY_RE.search(name):
        return True
    return False


def sanitize(cmd: str) -> str:
    # Split while preserving quoted tokens.
    tokens = []
    for m in re.finditer(r"\"([^\"]*)\"|\x27([^\x27]*)\x27|(\S+)", cmd):
        tokens.append(m.group(1) or m.group(2) or m.group(3))

    out = []
    skip_next = False
    for tok in tokens:
        if skip_next:
            skip_next = False
            out.append("[REDACTED]")
            continue

        # URLs with credentials: redact userinfo while keeping scheme and host.
        if _URL_RE.match(tok):
            tok = re.sub(r'//[^@]+@', '//[REDACTED]@', tok, count=1)
            out.append(tok)
            continue

        # Authorization-style headers and bearer tokens: redact the value.
        # This catches bare names and quoted header strings like
        # "Authorization: Bearer <token>".
        if _HEADER_RE.match(tok) or _HEADER_RE.search(tok):
            out.append("[REDACTED]")
            continue

        # Secret-bearing assignments (KEY=VALUE), including "export KEY=VALUE".
        m = _ASSIGN_RE.match(tok)
        if m:
            key, value = m.groups()
            if _SECRET_KEY_RE.search(key) or _SECRET_VALUE_RE.search(value):
                out.append(f"{key}=[REDACTED]")
                continue

        # Options with attached values (--token=secret, --api_key=secret).
        if (tok.startswith("-") and "=" in tok
                and _is_token_option(tok.split("=", 1)[0])):
            opt_part = tok.split("=", 1)[0]
            out.append(f"{opt_part}=[REDACTED]")
            continue

        # Options with separate values (--token secret, -t secret).
        if tok.startswith("-") and _is_token_option(tok):
            skip_next = True
            out.append(tok)
            continue

        # Redact bare values that look like secrets.
        if _SECRET_VALUE_RE.search(tok):
            out.append("[REDACTED]")
            continue

        out.append(tok)

    return " ".join(out)


def extract_command(line: str):
    """Extract the command portion of a crontab line (skip schedule fields)."""
    line = line.strip()
    if not line or line.startswith("#"):
        return None
    if _CRON_ENV_RE.match(line):
        return None
    if line.startswith("@"):
        parts = line.split(maxsplit=1)
        if len(parts) != 2 or parts[0].lower() not in {
            "@reboot", "@yearly", "@annually", "@monthly", "@weekly",
            "@daily", "@midnight", "@hourly",
        }:
            raise ValueError(f"malformed crontab entry: {line}")
        return parts[1]
    parts = line.split()
    if len(parts) < 6:
        raise ValueError(f"malformed crontab entry: {line}")
    numeric_field = re.compile(r"^[0-9*/?,\-]+$")
    named_month = re.compile(
        r"^(?:[0-9*/?,\-]|JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)+$",
        re.I,
    )
    named_day = re.compile(
        r"^(?:[0-9*/?,\-]|SUN|MON|TUE|WED|THU|FRI|SAT)+$",
        re.I,
    )
    validators = (numeric_field, numeric_field, numeric_field, named_month, named_day)
    if any(not validator.fullmatch(field) for validator, field in zip(validators, parts[:5])):
        raise ValueError(f"malformed crontab schedule: {line}")
    return " ".join(parts[5:])


with open(sys.argv[1], encoding="utf-8") as f:
    commands = [sanitize(cmd) for line in f if (cmd := extract_command(line)) is not None]
print(json.dumps(commands))
PY
)" || {
      echo "inventory-retired-runtime: malformed cron configuration: $CRON_FILE" >&2
      exit 2
    }
  fi
  printf '    "path": %s,\n' "$(printf '%s' "$CRON_SOURCE_LABEL" | json_escape)"
  printf '    "hash": %s,\n' "$cron_hash"
  printf '    "owner": "unknown",\n'
  printf '    "disposition": "decision-required",\n'
  printf '    "commands": %s\n' "$cron_commands"
  printf '  },\n'

  # MCP
  printf '  "mcp": {\n'
  mcp_hash='null'
  mcp_data='{"servers":[],"env_var_names":[]}'
  if [ -f "$MCP_FILE" ]; then
    mcp_hash="$(printf '%s' "$(sha256_file "$MCP_FILE")" | json_escape)"
    mcp_data="$(python3 - "$MCP_FILE" <<'PY'
import json
import re
import sys

try:
    with open(sys.argv[1], encoding="utf-8") as f:
        data = json.load(f)
except (OSError, json.JSONDecodeError) as exc:
    print(f"inventory-retired-runtime: malformed MCP config: {exc}", file=sys.stderr)
    raise SystemExit(2)

if not isinstance(data, dict):
    print("inventory-retired-runtime: MCP config must be an object", file=sys.stderr)
    raise SystemExit(2)
servers = data.get("mcpServers", {})
if not isinstance(servers, dict):
    print("inventory-retired-runtime: mcpServers must be an object", file=sys.stderr)
    raise SystemExit(2)

def sanitize(text):
    text = re.sub(r"([A-Za-z][A-Za-z0-9+.-]*://)[^/@\s]+@", r"\1[REDACTED]@", text)
    text = re.sub(
        r"(?i)\b((?:[A-Z0-9_]*(?:API_?KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL|AUTH|COOKIE|OAUTH)[A-Z0-9_]*)=)([^\s]+)",
        r"\1[REDACTED]",
        text,
    )
    text = re.sub(
        r"(?i)(--(?:api[-_]?key|token|secret|password|credential|auth|cookie|oauth)(?:=|\s+))([^\s]+)",
        r"\1[REDACTED]",
        text,
    )
    return text

records = []
all_env_names = set()
for name in sorted(servers):
    config = servers[name]
    if not isinstance(name, str) or not name or not isinstance(config, dict):
        print("inventory-retired-runtime: malformed MCP server entry", file=sys.stderr)
        raise SystemExit(2)
    command = config.get("command", "unknown")
    env = config.get("env", {})
    if not isinstance(command, str) or not command or not isinstance(env, dict):
        print(f"inventory-retired-runtime: malformed MCP server: {name}", file=sys.stderr)
        raise SystemExit(2)
    if any(not isinstance(key, str) for key in env):
        print(f"inventory-retired-runtime: invalid MCP env name: {name}", file=sys.stderr)
        raise SystemExit(2)
    env_names = sorted(env)
    all_env_names.update(env_names)
    records.append(
        {
            "name": name,
            "command": sanitize(command),
            "env_var_names": env_names,
        }
    )
print(json.dumps({"servers": records, "env_var_names": sorted(all_env_names)}))
PY
)" || {
      echo "inventory-retired-runtime: malformed MCP configuration: $MCP_FILE" >&2
      exit 2
    }
  fi
  printf '    "path": %s,\n' "$(printf '%s' "$MCP_FILE" | json_escape)"
  printf '    "hash": %s,\n' "$mcp_hash"
  printf '    "owner": "unknown",\n'
  printf '    "disposition": "decision-required",\n'
  printf '    "servers": %s,\n' "$(printf '%s' "$mcp_data" | jq -c '.servers')"
  printf '    "env_var_names": %s\n' "$(printf '%s' "$mcp_data" | jq -c '.env_var_names')"
  printf '  },\n'

  # State
  printf '  "state": [\n'
  first=1
  if [ -d "$STATE_DIR" ]; then
    for f in "$STATE_DIR"/*; do
      [ -e "$f" ] || continue
      [ -f "$f" ] || continue
      hash="$(sha256_file "$f")"
      if [ "$first" -eq 0 ]; then printf ',\n'; fi
      first=0
      printf '    {\n'
      printf '      "path": %s,\n' "$(printf '%s' "$f" | json_escape)"
      printf '      "hash": %s,\n' "$(printf '%s' "$hash" | json_escape)"
      printf '      "owner": "unknown",\n'
      printf '      "disposition": "decision-required"\n'
      printf '    }'
    done
  fi
  printf '\n  ]\n'

  printf '}\n'
} > "$tmp_out"

mv "$tmp_out" "$OUTPUT"
echo "inventory-retired-runtime: wrote $OUTPUT"
