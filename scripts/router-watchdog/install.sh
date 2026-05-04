#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
SOURCE_BIN="$SCRIPT_DIR/bin"
SOURCE_LAUNCHD="$REPO_ROOT/launchd"
PLISTS=(
  com.k2b.router-watchdog.plist
  com.k2b.router-daily-rollup.plist
  com.k2b.router-node-score.plist
  com.k2b.router-digest.plist
)

APP_DIR="${K2B_ROUTER_WATCHDOG_APP_DIR:-$HOME/Library/Application Support/k2b-router-watchdog}"
INSTALL_BIN="$APP_DIR/bin"
LOG_DIR="${K2B_ROUTER_WATCHDOG_LOG_DIR:-$HOME/Library/Logs/k2b-router-watchdog}"
LAUNCH_AGENTS_DIR="${K2B_ROUTER_WATCHDOG_LAUNCH_AGENTS_DIR:-$HOME/Library/LaunchAgents}"
ENV_FILE="${K2B_ROUTER_WATCHDOG_ENV_FILE:-$HOME/.k2b-router-watchdog.env}"
MANIFEST="$APP_DIR/install-manifest.sha256"
INSTALL_LOG="$LOG_DIR/install.log"
SKIP_LAUNCHCTL="${K2B_ROUTER_WATCHDOG_SKIP_LAUNCHCTL:-0}"

log() { printf '[router-watchdog install] %s\n' "$*"; }
fail() { printf '[router-watchdog install] ERROR: %s\n' "$*" >&2; exit 1; }

load_watchdog_env() {
  local line key value first last
  while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line%$'\r'}"
    [[ -z "$line" || "${line:0:1}" == "#" || "$line" != *=* ]] && continue
    key="${line%%=*}"
    value="${line#*=}"
    if [[ ${#value} -ge 2 ]]; then
      first="${value:0:1}"
      last="${value: -1}"
      if { [[ "$first" == "'" && "$last" == "'" ]] || [[ "$first" == '"' && "$last" == '"' ]]; }; then
        value="${value:1:${#value}-2}"
      fi
    fi
    case "$key" in
      TELEGRAM_BOT_TOKEN|KEITH_CHAT_ID|MIHOMO_API_BASE|MIHOMO_API_SECRET|MIHOMO_OPENAI_GROUP)
        printf -v "$key" '%s' "$value"
        export "$key"
        ;;
    esac
  done < "$ENV_FILE"
}

stat_mode() {
  if stat -f %Lp "$1" >/dev/null 2>&1; then
    stat -f %Lp "$1"
  else
    stat -c %a "$1"
  fi
}

sha_manifest() {
  local root="$1"
  if [[ ! -d "$root" ]]; then
    return 0
  fi
  (
    cd "$root"
    find . -type f -print | LC_ALL=C sort | while IFS= read -r file; do
      LC_ALL=C LANG=C shasum -a 256 "$file"
    done
    true
  )
}

[[ -d "$SOURCE_BIN" ]] || fail "missing source bin dir: $SOURCE_BIN"
[[ -d "$SOURCE_LAUNCHD" ]] || fail "missing launchd dir: $SOURCE_LAUNCHD"
[[ -f "$ENV_FILE" ]] || fail "env file missing: $ENV_FILE"

mode="$(stat_mode "$ENV_FILE")"
[[ "$mode" == "600" ]] || fail "$ENV_FILE must be chmod 600, got $mode"

load_watchdog_env

: "${TELEGRAM_BOT_TOKEN:?env file is missing TELEGRAM_BOT_TOKEN}"
: "${KEITH_CHAT_ID:?env file is missing KEITH_CHAT_ID}"
: "${MIHOMO_API_BASE:?env file is missing MIHOMO_API_BASE}"
: "${MIHOMO_API_SECRET:?env file is missing MIHOMO_API_SECRET}"
: "${MIHOMO_OPENAI_GROUP:?env file is missing MIHOMO_OPENAI_GROUP}"

dirty="$(git -C "$REPO_ROOT" status --porcelain -- scripts/router-watchdog launchd)"
blocking_dirty="$(printf '%s\n' "$dirty" | grep -vE '^(\?\? .*)?$' || true)"
if [[ -n "$blocking_dirty" ]]; then
  printf '%s\n' "$blocking_dirty" >&2
  fail "source tree has modified tracked files for scripts/router-watchdog or launchd"
fi
if [[ -n "$dirty" ]]; then
  log "source paths are untracked in git; treating current rsync copy as the install snapshot"
fi

mkdir -p "$INSTALL_BIN" "$LOG_DIR" "$LAUNCH_AGENTS_DIR"

before_manifest="$(
  {
    sha_manifest "$INSTALL_BIN"
    for plist in "${PLISTS[@]}"; do
      [[ -f "$LAUNCH_AGENTS_DIR/$plist" ]] && LC_ALL=C LANG=C shasum -a 256 "$LAUNCH_AGENTS_DIR/$plist"
    done
    true
  } | LC_ALL=C sort
)"

rsync -a --checksum --delete "$SOURCE_BIN/" "$INSTALL_BIN/"
find "$INSTALL_BIN" -type f \( -name '*.sh' -o -name '*.py' \) -exec chmod +x {} +

for plist in "${PLISTS[@]}"; do
  src="$SOURCE_LAUNCHD/$plist"
  dest="$LAUNCH_AGENTS_DIR/$plist"
  [[ -f "$src" ]] || fail "missing plist source: $src"
  tmp="$dest.tmp"
  sed \
    -e "s#__HOME__#$HOME#g" \
    -e "s#__APP_DIR__#$APP_DIR#g" \
    -e "s#__LOG_DIR__#$LOG_DIR#g" \
    -e "s#__ENV_FILE__#$ENV_FILE#g" \
    -e "s#__VAULT_PATH__#${K2B_VAULT_PATH:-$HOME/Projects/K2B-Vault}#g" \
    "$src" > "$tmp"
  mv "$tmp" "$dest"
done

after_manifest="$(
  {
    sha_manifest "$INSTALL_BIN"
    for plist in "${PLISTS[@]}"; do
      [[ -f "$LAUNCH_AGENTS_DIR/$plist" ]] && LC_ALL=C LANG=C shasum -a 256 "$LAUNCH_AGENTS_DIR/$plist"
    done
    true
  } | LC_ALL=C sort
)"

change_note="changed"
if [[ "$before_manifest" == "$after_manifest" ]]; then
  change_note="no changes"
fi

git_sha="$(git -C "$REPO_ROOT" rev-parse HEAD)"
{
  printf '%s source=%s %s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$git_sha" "$change_note"
  printf '%s\n' "$after_manifest"
} >> "$INSTALL_LOG"
printf '%s\n' "$after_manifest" > "$MANIFEST"

if [[ "$SKIP_LAUNCHCTL" != "1" ]]; then
  uid="$(id -u)"
  for plist in "${PLISTS[@]}"; do
    dest="$LAUNCH_AGENTS_DIR/$plist"
    launchctl bootout "gui/$uid" "$dest" >/dev/null 2>&1 || true
    launchctl bootstrap "gui/$uid" "$dest"
  done
else
  log "skipping launchctl because K2B_ROUTER_WATCHDOG_SKIP_LAUNCHCTL=1"
fi

dry_env=()
if [[ "$SKIP_LAUNCHCTL" == "1" ]]; then
  dry_env+=(K2B_ROUTER_WATCHDOG_DRY_RUN_SKIP_CHECKS=1)
fi

if [[ ${#dry_env[@]} -gt 0 ]]; then
  env \
    K2B_ROUTER_WATCHDOG_APP_DIR="$APP_DIR" \
    K2B_ROUTER_WATCHDOG_LOG_DIR="$LOG_DIR" \
    K2B_ROUTER_WATCHDOG_ENV_FILE="$ENV_FILE" \
    "${dry_env[@]}" \
    bash "$INSTALL_BIN/check.sh" --dry-run >/dev/null
else
  env \
    K2B_ROUTER_WATCHDOG_APP_DIR="$APP_DIR" \
    K2B_ROUTER_WATCHDOG_LOG_DIR="$LOG_DIR" \
    K2B_ROUTER_WATCHDOG_ENV_FILE="$ENV_FILE" \
    bash "$INSTALL_BIN/check.sh" --dry-run >/dev/null
fi

log "$change_note; installed to $APP_DIR"
