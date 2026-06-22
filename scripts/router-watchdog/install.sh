#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
SOURCE_BIN="$SCRIPT_DIR/bin"
SOURCE_LAUNCHD="$REPO_ROOT/launchd"
PLISTS=(
  com.k2b.router-watchdog.plist
  com.k2b.router-private-vpn-watchdog.plist
  com.k2b.router-daily-rollup.plist
  com.k2b.router-node-score.plist
  com.k2b.router-digest.plist
  com.k2b.router-r5c-autorecovery.plist
)
RETIRED_PLISTS=(
  com.k2b.router-leaf-optimizer.plist
)
# Backup snapshot includes only active plists; retired plists are removed,
# never restored.
BACKUP_PLISTS=("${PLISTS[@]}")

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
    key="${key#export }"
    key="${key//[[:space:]]/}"
    case "$key" in
      TELEGRAM_BOT_TOKEN|KEITH_CHAT_ID|MIHOMO_*|K2B_PRIVATE_VPN_*)
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

validate_optional_ssh_target() {
  local name="$1" value="${!1:-}"
  [[ -z "$value" ]] && return 0
  [[ "$value" != -* ]] || fail "$name must not start with '-'"
  [[ "$value" != *[[:space:]]* ]] || fail "$name must not contain whitespace"
}

validate_optional_ssh_key() {
  local name="$1" value="${!1:-}"
  [[ -z "$value" ]] && return 0
  python3 - "$value" <<'PY' || fail "$name is not a usable SSH key path"
import os, pwd, stat, sys
raw = sys.argv[1]
if ".." in raw.split(os.sep):
    raise SystemExit(1)
path = os.path.abspath(os.path.expanduser(raw))
home = os.path.abspath(os.path.expanduser("~"))
real_home = os.path.abspath(pwd.getpwuid(os.getuid()).pw_dir)
if home != real_home:
    raise SystemExit(1)
ssh_dir = os.path.join(home, ".ssh")
if not (path == ssh_dir or path.startswith(ssh_dir + os.sep)):
    raise SystemExit(1)
if os.path.islink(path) or not os.path.isfile(path):
    raise SystemExit(1)
mode = os.stat(path, follow_symlinks=False).st_mode
if mode & 0o077:
    raise SystemExit(1)
current = os.path.dirname(path) or "."
while True:
    if os.path.islink(current):
        raise SystemExit(1)
    mode = os.stat(current, follow_symlinks=False).st_mode
    if mode & 0o022:
        raise SystemExit(1)
    if os.path.abspath(current) == ssh_dir:
        break
    parent = os.path.dirname(current)
    if parent == current:
        raise SystemExit(1)
    current = parent
PY
}

warn_missing_private_env() {
  local name="$1" fallback="$2"
  [[ -n "${!name:-}" ]] && return 0
  log "WARNING: env file is missing $name; private VPN incident traces will use ${fallback}"
}

remove_retired_plists() {
  local uid="$1" plist dest label attempt
  for plist in "${RETIRED_PLISTS[@]}"; do
    dest="$LAUNCH_AGENTS_DIR/$plist"
    label="${plist%.plist}"
    if [[ "$SKIP_LAUNCHCTL" != "1" ]]; then
      # Boot out by path when the plist file still exists, then boot out by
      # label in case the file is already gone but launchd still has the job.
      if [[ -f "$dest" ]]; then
        launchctl bootout "gui/$uid" "$dest" >/dev/null 2>&1 || true
      fi
      launchctl bootout "gui/$uid/$label" >/dev/null 2>&1 || true
      for attempt in 1 2 3 4 5; do
        if ! launchctl print "gui/$uid/$label" >/dev/null 2>&1; then
          break
        fi
        sleep 1
      done
      if launchctl print "gui/$uid/$label" >/dev/null 2>&1; then
        launchctl bootout "gui/$uid/$label" >/dev/null 2>&1 || true
        for attempt in 1 2 3 4 5; do
          if ! launchctl print "gui/$uid/$label" >/dev/null 2>&1; then
            break
          fi
          sleep 1
        done
      fi
      if launchctl print "gui/$uid/$label" >/dev/null 2>&1; then
        fail "retired launchd job $label is still loaded after bootout; refusing to remove plist"
      fi
    fi
    if [[ -f "$dest" ]]; then
      rm -f "$dest"
      log "retired launchd job removed: $plist"
    fi
  done
}

remove_retired_state() {
  rm -f "$APP_DIR/leaf-optimizer-state.json"
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
validate_optional_ssh_target K2B_PRIVATE_VPN_ROUTER_SSH_TARGET
validate_optional_ssh_target K2B_PRIVATE_VPN_HK_SSH_TARGET
validate_optional_ssh_key K2B_PRIVATE_VPN_HK_SSH_KEY
warn_missing_private_env K2B_PRIVATE_VPN_HK_SSH_TARGET "server trace=unknown"
warn_missing_private_env K2B_PRIVATE_VPN_AWS_PROFILE "default k2b-aws-signhubdev-hk"
warn_missing_private_env K2B_PRIVATE_VPN_AWS_REGION "default ap-east-1"
warn_missing_private_env K2B_PRIVATE_VPN_AWS_INSTANCE "default Ubuntu-1"

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
uid="$(id -u)"

# === HIGH-3: transactional install backup ===
# Snapshot the current installed state (bin tree + each plist file) BEFORE
# any modification. If the launchctl bootout/bootstrap loop later fails
# partway through, rollback_install() restores from this snapshot so the
# fleet ends in either fully-old or fully-new state, never half-upgraded.
INSTALL_BACKUP_DIR="$(mktemp -d "$APP_DIR/.install-backup.XXXXXX")"
mkdir -p "$INSTALL_BACKUP_DIR/bin" "$INSTALL_BACKUP_DIR/plists"
if [[ -d "$INSTALL_BIN" ]]; then
  # cp -a "$INSTALL_BIN/." preserves attributes and copies contents
  # without nesting. Empty install dir is fine -- nothing to copy. We
  # FAIL CLOSED if the copy errors out: a partial backup would later
  # cause the rollback rsync --delete to wipe INSTALL_BIN to whatever
  # subset cp managed to capture, leaving the fleet worse than before
  # the install attempt.
  if compgen -G "$INSTALL_BIN/*" >/dev/null 2>&1 || compgen -G "$INSTALL_BIN/.*" >/dev/null 2>&1; then
    if ! cp -a "$INSTALL_BIN/." "$INSTALL_BACKUP_DIR/bin/"; then
      fail "could not snapshot $INSTALL_BIN to $INSTALL_BACKUP_DIR/bin -- refusing to install (rollback would be unsafe)"
    fi
  fi
fi
for plist in "${BACKUP_PLISTS[@]}"; do
  if [[ -f "$LAUNCH_AGENTS_DIR/$plist" ]]; then
    if ! cp -a "$LAUNCH_AGENTS_DIR/$plist" "$INSTALL_BACKUP_DIR/plists/$plist"; then
      fail "could not snapshot $LAUNCH_AGENTS_DIR/$plist -- refusing to install (rollback would be unsafe)"
    fi
  fi
done
remove_retired_plists "$uid"

ROLLBACK_HAD_DEGRADATION=false

cleanup_install_backup() {
  # If rollback ran with any degradation (failed restore or failed
  # re-bootstrap), keep the backup directory around for manual recovery
  # rather than silently removing the only known-good copy of the prior
  # install state. Happy-path installs always clean up.
  if $ROLLBACK_HAD_DEGRADATION; then
    log "leaving backup directory in place for manual recovery: $INSTALL_BACKUP_DIR"
    return 0
  fi
  rm -rf "$INSTALL_BACKUP_DIR" 2>/dev/null || true
}
trap cleanup_install_backup EXIT

rollback_install() {
  local reason="$1"
  log "ERROR: $reason"
  log "rolling back to pre-install snapshot at $INSTALL_BACKUP_DIR"

  # Restore bin tree. --checksum is REQUIRED here: rsync's default size+mtime
  # comparison can falsely skip files that the just-failed install rewrote
  # in place (same name, often same size, mtimes only seconds apart).
  # --delete brings INSTALL_BIN back to backup state by removing extras.
  if [[ -d "$INSTALL_BACKUP_DIR/bin" ]]; then
    if ! rsync -a --checksum --delete "$INSTALL_BACKUP_DIR/bin/" "$INSTALL_BIN/" 2>/dev/null; then
      log "WARNING: bin tree restore failed; install bin may be inconsistent"
      ROLLBACK_HAD_DEGRADATION=true
    fi
  fi

  # Restore each active plist file (or remove it if it was new this install).
  # Retired LaunchAgents are intentionally never restored: cleanup already
  # removed them, and a failed install must not reintroduce retired mutation
  # lanes into the LaunchAgents directory.
  for plist in "${PLISTS[@]}"; do
    bak="$INSTALL_BACKUP_DIR/plists/$plist"
    dest="$LAUNCH_AGENTS_DIR/$plist"
    if [[ -f "$bak" ]]; then
      if ! cp -a "$bak" "$dest" 2>/dev/null; then
        log "WARNING: failed to restore $plist from backup"
        ROLLBACK_HAD_DEGRADATION=true
      fi
    else
      # Plist did not exist before this install; remove the new one so
      # launchctl state matches pre-install reality.
      rm -f "$dest" 2>/dev/null || true
    fi
  done

  # Best-effort re-bootstrap of the restored active fleet. Retired plists are
  # never restored to disk; old mutation lanes stay retired.
  # Each active plist is bootout'd (tolerating "not loaded") and re-bootstrapped
  # from its restored backup. Failures here are logged but do not abort the
  # rollback -- rollback is already a degraded path; the goal is to leave
  # launchd consistent with the on-disk plist files we just restored.
  for plist in "${PLISTS[@]}"; do
    dest="$LAUNCH_AGENTS_DIR/$plist"
    [[ -f "$dest" ]] || continue
    launchctl bootout "gui/$uid" "$dest" >/dev/null 2>&1 || true
    if ! launchctl bootstrap "gui/$uid" "$dest" >/dev/null 2>&1; then
      log "WARNING: re-bootstrap of $plist after rollback failed; manual recovery may be needed"
      ROLLBACK_HAD_DEGRADATION=true
    fi
  done

  # MANIFEST stays at PRIOR value (we never wrote a new one), so the next
  # /sync's deploy-to-mini.sh -> install.sh run will detect the mismatch
  # and retry. cleanup_install_backup runs via the EXIT trap (preserves
  # the backup if ROLLBACK_HAD_DEGRADATION).
  fail "install rolled back to previous state ($reason); MANIFEST unchanged; next deploy will retry"
}

before_manifest="$(
  {
    sha_manifest "$INSTALL_BIN"
    for plist in "${PLISTS[@]}"; do
      [[ -f "$LAUNCH_AGENTS_DIR/$plist" ]] && LC_ALL=C LANG=C shasum -a 256 "$LAUNCH_AGENTS_DIR/$plist"
    done
    true
  } | LC_ALL=C sort
)"

rsync -a --checksum --delete --delete-excluded \
  --exclude 'leaf-optimizer-profiles.json' \
  "$SOURCE_BIN/" "$INSTALL_BIN/"
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
      # HIGH-3: on bootstrap failure, restore previous install + revert
      # manifest by calling rollback_install (which calls fail -> exit 1).
      # Without this, set -e would abort with the fleet in a half-upgraded
      # state (some plists already running new versions, others bootout'd
      # with new files on disk but no live job).
      if ! launchctl bootstrap "gui/$uid" "$dest"; then
        rollback_install "launchctl bootstrap failed for $plist"
      fi
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

remove_retired_state

log "$change_note; installed to $APP_DIR"
