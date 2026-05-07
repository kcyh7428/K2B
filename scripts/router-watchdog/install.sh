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
  com.k2b.router-leaf-optimizer.plist
  com.k2b.router-digest.plist
)

APP_DIR="${K2B_ROUTER_WATCHDOG_APP_DIR:-$HOME/Library/Application Support/k2b-router-watchdog}"
INSTALL_BIN="$APP_DIR/bin"
LOG_DIR="${K2B_ROUTER_WATCHDOG_LOG_DIR:-$HOME/Library/Logs/k2b-router-watchdog}"
LAUNCH_AGENTS_DIR="${K2B_ROUTER_WATCHDOG_LAUNCH_AGENTS_DIR:-$HOME/Library/LaunchAgents}"
ENV_FILE="${K2B_ROUTER_WATCHDOG_ENV_FILE:-$HOME/.k2b-router-watchdog.env}"
LEAFOPT_SENTINEL="${K2B_ROUTER_LEAFOPT_SENTINEL:-$HOME/.k2b-router-leafopt-enabled}"
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

if [[ -e "$LEAFOPT_SENTINEL" ]]; then
  log "WARNING: $LEAFOPT_SENTINEL exists; router leaf optimizer will be allowed to mutate manual selectors after install"
  log "WARNING: remove it before install if you have not inspected optimize-leaves.sh --dry-run output"
fi

# Mac Mini has no `.git` directory (deleted 2026-05-07; see L-2026-05-07-001).
# Source-of-truth for code is MacBook + GitHub; Mini receives files via rsync
# only. When git is genuinely missing, skip the dirty-tree check and trust
# the rsync copy as the install snapshot.
#
# IMPORTANT: only the specific "not a git repository" condition gets the
# pass. Other git failures (corrupt repo, permission errors, missing git
# binary, safe.directory rejection) MUST fail closed -- otherwise the
# installer could quietly snapshot modified tracked source instead of the
# pristine HEAD state. Capture stderr and match the well-known fatal
# message; any other failure mode propagates as a hard error.
have_git=true
git_probe_stderr="$(
  git -C "$REPO_ROOT" rev-parse --is-inside-work-tree 2>&1 >/dev/null
)" || git_probe_rc=$?
git_probe_rc="${git_probe_rc:-0}"
if [[ "$git_probe_rc" -ne 0 ]]; then
  if [[ "$git_probe_stderr" == *"not a git repository"* ]]; then
    have_git=false
  else
    printf '%s\n' "$git_probe_stderr" >&2
    fail "git probe failed at $REPO_ROOT (rc=$git_probe_rc); refusing to install. Fix git state or set up the repo before retrying."
  fi
fi

if $have_git; then
  dirty="$(git -C "$REPO_ROOT" status --porcelain -- scripts/router-watchdog launchd)"
  blocking_dirty="$(printf '%s\n' "$dirty" | grep -vE '^(\?\? .*)?$' || true)"
  if [[ -n "$blocking_dirty" ]]; then
    printf '%s\n' "$blocking_dirty" >&2
    fail "source tree has modified tracked files for scripts/router-watchdog or launchd"
  fi
  if [[ -n "$dirty" ]]; then
    log "source paths are untracked in git; treating current rsync copy as the install snapshot"
  fi
else
  log "no git repo at $REPO_ROOT (deploy target with .git removed); trusting rsync copy as install snapshot"
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

if $have_git; then
  git_sha="$(git -C "$REPO_ROOT" rev-parse HEAD)"
else
  # No git on Mini -- record the install-time placeholder. The MacBook-side
  # commit SHA is logged by deploy-to-mini.sh and reachable via DEVLOG.md.
  git_sha="no-git-on-mini"
fi
{
  printf '%s source=%s %s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$git_sha" "$change_note"
  printf '%s\n' "$after_manifest"
} >> "$INSTALL_LOG"

# IMPORTANT: do NOT write $MANIFEST yet. The manifest reflects on-disk state
# but the next run uses it to gate launchctl reload. If we write it here and
# launchctl fails below (set -e aborts the script), next run sees:
#   - on-disk manifest matches sources (since files were copied)
#   - $MANIFEST file matches the on-disk manifest (we wrote it)
#   - jobs probably still loaded (with OLD plists/binaries)
# ...and skips the reload that would recover the stale launchd state.
#
# Fix: defer the $MANIFEST write to AFTER launchctl succeeds. If launchctl
# fails, $MANIFEST stays at the OLD value, so next run's
# "current_on_disk != applied_manifest" check fires and forces a reload.
# We do NOT skip the install-log line above -- that line records the
# attempt regardless of launchctl outcome (audit trail).

# Re-derive change_note from the *applied* manifest, not just disk state.
# If $MANIFEST exists and content matches after_manifest, the previous run
# successfully completed launchctl (because we only update $MANIFEST after
# successful launchctl). Otherwise, treat as changed even if disk content
# matches the previous before-launchctl write.
if [[ -f "$MANIFEST" ]]; then
  applied_manifest="$(<"$MANIFEST")"
else
  applied_manifest=""
fi
if [[ "$applied_manifest" != "$after_manifest" ]]; then
  change_note="changed"
fi

if [[ "$SKIP_LAUNCHCTL" != "1" ]]; then
  # Only restart launchd jobs when the applied manifest does not match
  # current disk state OR a job is missing from launchctl. Avoids
  # interrupting in-flight router-watchdog runs (a probe in progress, an
  # optimizer mid-decision) when /sync runs with no router-watchdog deltas.
  # Pre-2026-05-07 the bootout/bootstrap loop ran unconditionally on every
  # install.
  uid="$(id -u)"
  needs_launchctl=false
  if [[ "$change_note" != "no changes" ]]; then
    needs_launchctl=true
  else
    # Even on no-change, verify every plist is currently loaded. If any
    # is missing, launchctl bootstrap to recover. Catches "user manually
    # bootout'd a job" or "jobs lost across reboot" recovery paths.
    for plist in "${PLISTS[@]}"; do
      label="${plist%.plist}"
      if ! launchctl print "gui/$uid/$label" >/dev/null 2>&1; then
        needs_launchctl=true
        break
      fi
    done
  fi
  if $needs_launchctl; then
    for plist in "${PLISTS[@]}"; do
      dest="$LAUNCH_AGENTS_DIR/$plist"
      launchctl bootout "gui/$uid" "$dest" >/dev/null 2>&1 || true
      launchctl bootstrap "gui/$uid" "$dest"
    done
  else
    log "no-change install: skipping launchctl bootout/bootstrap (jobs already loaded, applied manifest matches disk)"
  fi
else
  log "skipping launchctl because K2B_ROUTER_WATCHDOG_SKIP_LAUNCHCTL=1"
fi

# Persist the applied manifest only after launchctl actually ran (and
# succeeded -- set -e would have aborted the script on failure). If
# SKIP_LAUNCHCTL=1, the on-disk bin/plists may be newer than what launchd
# is actively running; advancing $MANIFEST in that case would mask the
# disk-vs-applied drift on the next non-skip run. Keep the OLD $MANIFEST
# value so the next normal install detects the mismatch and forces a
# reload. SKIP_LAUNCHCTL is a CI/test convenience flag, not a production
# install path.
if [[ "$SKIP_LAUNCHCTL" != "1" ]]; then
  printf '%s\n' "$after_manifest" > "$MANIFEST"
else
  log "SKIP_LAUNCHCTL=1: leaving $MANIFEST unchanged so next non-skip install reloads launchd"
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
