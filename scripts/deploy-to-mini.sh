#!/bin/bash
# deploy-to-mini.sh -- Sync K2B project files from MacBook to Mac Mini
#
# Usage:
#   deploy-to-mini.sh              # auto-detect what changed, sync it
#   deploy-to-mini.sh skills       # sync skills + AGENTS.md/CLAUDE.md + architecture
#   deploy-to-mini.sh code         # sync k2b-remote + rebuild + restart
#   deploy-to-mini.sh dashboard    # sync k2b-dashboard + rebuild + restart
#   deploy-to-mini.sh scripts      # sync scripts/
#   deploy-to-mini.sh all          # sync everything
#   deploy-to-mini.sh --dry-run    # show what would sync without doing it
#
# Change detection (auto mode) compares local content against the remote via
# `rsync -acn` (dry-run + checksum). This is authoritative regardless of git
# commit structure, so a multi-commit ship (e.g. code commit + follow-up
# devlog commit) never hides a category's files from the detector.
#
# Test hooks (do not set in prod):
#   K2B_LOCAL_BASE             override LOCAL_BASE (source tree)
#   K2B_MINI                   override remote host
#   K2B_REMOTE_BASE            override remote project path
#   K2B_RSYNC_TARGET_PREFIX    override "$MINI:$REMOTE_BASE" wholesale (e.g.
#                              a local path to bypass SSH in tests)
#   K2B_DETECT_ONLY=true       print detected categories and exit 0 before
#                              the Mini reachability check runs
#   K2B_DEPLOY_SELFTEST        internal test hook; do not set in prod

set -euo pipefail

MINI="${K2B_MINI:-macmini}"
LOCAL_BASE="${K2B_LOCAL_BASE:-$HOME/Projects/K2B}"
REMOTE_BASE="${K2B_REMOTE_BASE:-~/Projects/K2B}"
RSYNC_TARGET="${K2B_RSYNC_TARGET_PREFIX:-${MINI}:${REMOTE_BASE}}"
DETECT_ONLY="${K2B_DETECT_ONLY:-false}"
DRY_RUN=false
MODE="${1:-auto}"

# Parse host + path from RSYNC_TARGET ONCE here, so reachability checks and
# follow-up SSH (install.sh trigger) all route to the SAME machine that
# rsync is shipping files to. Without this, K2B_RSYNC_TARGET_PREFIX
# overrides could rsync to one host while reachability/install hit another.
# For local-shaped RSYNC_TARGET (no colon, test mode), MINI_HOST stays
# empty and is_remote_target() / maybe_run_remote_install() bypass.
if [[ "$RSYNC_TARGET" == *":"* ]]; then
    MINI_HOST="${RSYNC_TARGET%%:*}"
    MINI_PATH="${RSYNC_TARGET#*:}"
else
    MINI_HOST=""
    MINI_PATH="$RSYNC_TARGET"
fi

if [[ "$MODE" == "--dry-run" ]]; then
    DRY_RUN=true
    MODE="${2:-auto}"
fi

if [[ "${2:-}" == "--dry-run" ]]; then
    DRY_RUN=true
fi

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

log()  { echo -e "${GREEN}[sync]${NC} $1"; }
warn() { echo -e "${YELLOW}[sync]${NC} $1"; }
err()  { echo -e "${RED}[sync]${NC} $1"; }

# RSYNC_TARGET is "host:path" in prod and a local path in tests. We need to
# know which so we can skip the SSH reachability check when tests drive the
# script against a local fixture tree.
is_remote_target() {
    [[ "$RSYNC_TARGET" == *":"* ]]
}

remote_shell_quote() {
    local path="$1"
    local rest prefix
    if [[ "$path" == *[[:cntrl:]]* || "$path" == *"\\"* || "$path" == *'"'* ]]; then
        err "remote path contains unsupported character: $path"
        exit 1
    fi
    if [[ "$path" == "~" ]]; then
        printf '"$HOME"'
        return 0
    fi
    if [[ "$path" == "~/"* ]]; then
        printf '"$HOME"/'
        rest="${path#\~/}"
    else
        rest="$path"
    fi
    printf "'"
    while [[ "$rest" == *"'"* ]]; do
        prefix="${rest%%\'*}"
        printf "%s'\\\\''" "$prefix"
        rest="${rest#*\'}"
    done
    printf "%s'" "$rest"
}

ensure_target_dirs() {
    $DRY_RUN && return 0

    if is_remote_target; then
        local cmd="mkdir -p $(remote_shell_quote "$MINI_PATH")"
        local dir
        for dir in "$@"; do
            cmd+=" $(remote_shell_quote "$MINI_PATH/$dir")"
        done
        ssh "$MINI_HOST" "$cmd"
    else
        mkdir -p "$MINI_PATH"
        local dir
        for dir in "$@"; do
            mkdir -p "$MINI_PATH/$dir"
        done
    fi
}

if [[ "${K2B_DEPLOY_SELFTEST:-}" == "remote-shell-quote" ]]; then
    remote_shell_quote "${1:?path required}"
    exit 0
fi
if [[ "${K2B_DEPLOY_SELFTEST:-}" == "ensure-target-dirs" ]]; then
    ensure_target_dirs "$@"
    exit 0
fi
if [[ "${K2B_DEPLOY_SELFTEST:-}" == "ensure-target-dirs-dry-run" ]]; then
    DRY_RUN=true
    ensure_target_dirs "$@"
    exit 0
fi

# rsync_has_changes <source> <target> [extra rsync flags...]
# Returns 0 if a dry-run rsync with --checksum would transfer or delete
# anything, 1 if source and target are byte-identical under the given flags.
# Aborts the whole script (exit 1) on rsync error -- a swallowed error would
# otherwise look identical to "no changes" and let auto mode silently ship
# without deploying.
# The flags passed in MUST mirror the flags the real sync function uses
# (exclude lists, --delete, etc.) so the dry-run is authoritative.
rsync_has_changes() {
    local src="$1" dst="$2"
    shift 2
    local output stderr_file rc=0
    stderr_file="$(mktemp)"
    output=$(rsync -acn --itemize-changes "$@" "$src" "$dst" 2>"$stderr_file") || rc=$?
    if [[ $rc -ne 0 ]]; then
        err "rsync dry-run failed ($src -> $dst, exit $rc):"
        cat "$stderr_file" >&2
        rm -f "$stderr_file"
        # Common cause on a freshly provisioned Mini: the remote project dir
        # doesn't exist, so the top-level single-file doc rsyncs can't cd into
        # it. sync_skills's real sync hits the same error; manual bootstrap is
        # required.
        err "If the remote project tree is missing, SSH to the Mini, mkdir ~/Projects/K2B, then re-run."
        exit 1
    fi
    rm -f "$stderr_file"
    if [[ -z "$output" ]]; then
        return 1
    fi
    # itemize-changes lines begin with transfer indicators:
    #   >f... / <f...   file transfer
    #   >d... / cd...   dir transfer / dir create
    #   *deleting       deletion (only with --delete)
    if echo "$output" | grep -qE '^([><c*][fd]|\*deleting)'; then
        return 0
    fi
    return 1
}

needs_skills=false
needs_code=false
needs_dashboard=false
needs_scripts=false

# Populate the four needs_* flags by diffing local content against
# $RSYNC_TARGET via rsync --checksum --dry-run, once per category, using the
# same include/exclude rules each category's real sync function uses. This
# is commit-structure-independent: the detector sees the full local vs
# remote drift regardless of how many commits produced it.
detect_changes() {
    local doc
    for doc in AGENTS.md CLAUDE.md README.md K2B_ARCHITECTURE.md .mcp.json DEVLOG.md; do
        if [[ -f "$LOCAL_BASE/$doc" ]]; then
            if rsync_has_changes "$LOCAL_BASE/$doc" "$RSYNC_TARGET/$doc"; then
                needs_skills=true
            fi
        fi
    done
    if [[ -f "$LOCAL_BASE/.codex/hooks.json" ]]; then
        if is_remote_target; then
            if ! ssh "$MINI_HOST" "test -d $(remote_shell_quote "$MINI_PATH/.codex")" >/dev/null 2>&1; then
                needs_skills=true
            elif rsync_has_changes "$LOCAL_BASE/.codex/hooks.json" "$RSYNC_TARGET/.codex/hooks.json"; then
                needs_skills=true
            fi
        elif [[ ! -d "$MINI_PATH/.codex" ]]; then
            needs_skills=true
        elif rsync_has_changes "$LOCAL_BASE/.codex/hooks.json" "$RSYNC_TARGET/.codex/hooks.json"; then
            needs_skills=true
        fi
    fi
    if [[ -d "$LOCAL_BASE/.claude/skills" ]]; then
        if rsync_has_changes "$LOCAL_BASE/.claude/skills/" "$RSYNC_TARGET/.claude/skills/" --delete; then
            needs_skills=true
        fi
    fi
    if [[ -d "$LOCAL_BASE/.agents/skills" ]]; then
        if rsync_has_changes "$LOCAL_BASE/.agents/skills/" "$RSYNC_TARGET/.agents/skills/" --delete; then
            needs_skills=true
        fi
    fi
    # k2b-remote/workspace/ is the bot's runtime scratch tree: uploads/ holds
    # incoming Telegram attachments, telegram-outbox/ holds pending replies. The
    # bot creates both at startup (mkdirSync recursive in media.ts and
    # telegram-outbox.ts), so they are never source and must not sync. The
    # `/workspace/` pattern is anchored: the leading slash pins it to the
    # k2b-remote transfer root (a nested src/.../workspace/ still syncs) and the
    # trailing slash matches a directory only (a file named "workspace" still
    # syncs and would correctly flag code). This stops runtime files (and any
    # future runtime subdir) from faking "code" drift that would needlessly
    # rebuild + restart the live bot. Source lives in k2b-remote/src/ -- never
    # place source under workspace/. This exclude list MUST stay identical to
    # sync_code's (see the line ~143 mirror invariant); the test suite asserts it.
    if [[ -d "$LOCAL_BASE/k2b-remote" ]]; then
        if rsync_has_changes "$LOCAL_BASE/k2b-remote/" "$RSYNC_TARGET/k2b-remote/" \
            --exclude node_modules --exclude dist --exclude store --exclude .env --exclude /workspace/; then
            needs_code=true
        fi
    fi
    if [[ -d "$LOCAL_BASE/k2b-dashboard" ]]; then
        if rsync_has_changes "$LOCAL_BASE/k2b-dashboard/" "$RSYNC_TARGET/k2b-dashboard/" \
            --exclude node_modules --exclude dist --exclude legacy-v2 --exclude '.env*'; then
            needs_dashboard=true
        fi
    fi
    if [[ -d "$LOCAL_BASE/scripts" ]]; then
        if rsync_has_changes "$LOCAL_BASE/scripts/" "$RSYNC_TARGET/scripts/" \
            --exclude '__pycache__/' --exclude '*.pyc'; then
            needs_scripts=true
        fi
    fi
    if [[ -d "$LOCAL_BASE/launchd" ]]; then
        if rsync_has_changes "$LOCAL_BASE/launchd/" "$RSYNC_TARGET/launchd/" --delete; then
            needs_scripts=true
        fi
    fi
    if [[ -f "$LOCAL_BASE/README-router-watchdog.md" ]]; then
        if rsync_has_changes "$LOCAL_BASE/README-router-watchdog.md" "$RSYNC_TARGET/README-router-watchdog.md"; then
            needs_scripts=true
        fi
    fi
}

sync_skills() {
    log "Syncing skills + top-level docs..."
    local rsync_flag=""
    $DRY_RUN && rsync_flag="--dry-run"
    if ! $DRY_RUN; then
        ensure_target_dirs ".claude/skills" ".agents/skills" ".codex"
    fi

    # Top-level docs: sync any that exist. K2B_ARCHITECTURE.md was removed 2026-04
    # but README.md is user-facing documentation worth keeping in sync.
    # .mcp.json added 2026-04-19 after silent drift let MiniMax MCP BASE_PATH
    # diverge between machines (MacBook user `keithmbpm2` vs Mini user `fastshower`),
    # breaking all bot-initiated image generation.
    # DEVLOG.md added 2026-05-07 alongside .git removal on Mini -- Mini-side
    # processes (e.g. the Telegram bot) now read DEVLOG.md to introspect
    # "what shipped recently" instead of `git log`, since git no longer exists
    # on Mini. See L-2026-05-07-001.
    for doc in AGENTS.md CLAUDE.md README.md K2B_ARCHITECTURE.md .mcp.json DEVLOG.md; do
        if [[ -f "$LOCAL_BASE/$doc" ]]; then
            rsync -av $rsync_flag "$LOCAL_BASE/$doc" "$RSYNC_TARGET/$doc"
        fi
    done

    rsync -av $rsync_flag --delete "$LOCAL_BASE/.claude/skills/" "$RSYNC_TARGET/.claude/skills/"
    if [[ -d "$LOCAL_BASE/.agents/skills" ]]; then
        rsync -av $rsync_flag --delete "$LOCAL_BASE/.agents/skills/" "$RSYNC_TARGET/.agents/skills/"
    fi
    if [[ -f "$LOCAL_BASE/.codex/hooks.json" ]]; then
        rsync -av $rsync_flag "$LOCAL_BASE/.codex/hooks.json" "$RSYNC_TARGET/.codex/hooks.json"
    fi

    if ! $DRY_RUN && is_remote_target; then
        log "Verifying skills on Mini..."
        local remote_count local_count
        local_count=$(find "$LOCAL_BASE/.claude/skills" -maxdepth 1 -type d -name 'k2b-*' 2>/dev/null | wc -l | tr -d ' ')
        if [[ "$local_count" == "0" ]]; then
            err "No local Claude skills found under $LOCAL_BASE/.claude/skills"
            exit 1
        fi
        remote_count=$(ssh "$MINI_HOST" "test -d $(remote_shell_quote "$MINI_PATH/.claude/skills") && find $(remote_shell_quote "$MINI_PATH/.claude/skills") -maxdepth 1 -type d -name 'k2b-*' 2>/dev/null | wc -l" | tr -d ' ') || {
            err "Unable to verify Claude skills on Mini"
            exit 1
        }
        if [[ "$remote_count" == "$local_count" ]]; then
            log "Skills verified: $remote_count skill folders on both machines"
        else
            err "Claude skill count mismatch: local=$local_count remote=$remote_count"
            exit 1
        fi
        if [[ -d "$LOCAL_BASE/.agents/skills" ]]; then
            local remote_agents_count local_agents_count
            local_agents_count=$(find "$LOCAL_BASE/.agents/skills" -maxdepth 1 -type d -name 'k2b-*' 2>/dev/null | wc -l | tr -d ' ')
            if [[ "$local_agents_count" == "0" ]]; then
                err "No local Codex skills found under $LOCAL_BASE/.agents/skills"
                exit 1
            fi
            remote_agents_count=$(ssh "$MINI_HOST" "test -d $(remote_shell_quote "$MINI_PATH/.agents/skills") && find $(remote_shell_quote "$MINI_PATH/.agents/skills") -maxdepth 1 -type d -name 'k2b-*' 2>/dev/null | wc -l" | tr -d ' ') || {
                err "Unable to verify Codex skills on Mini"
                exit 1
            }
            if [[ "$remote_agents_count" != "$local_agents_count" ]]; then
                err "Codex skill count mismatch: local=$local_agents_count remote=$remote_agents_count"
                exit 1
            fi
            log "Codex skills verified: $remote_agents_count skill folders on both machines"
        fi
        if [[ -f "$LOCAL_BASE/.codex/hooks.json" ]]; then
            if ! ssh "$MINI_HOST" "test -f $(remote_shell_quote "$MINI_PATH/.codex/hooks.json")"; then
                err "Codex hooks missing on Mini: $MINI_PATH/.codex/hooks.json"
                exit 1
            fi
            log "Codex hooks verified on Mini"
        fi
    fi
}

sync_code() {
    log "Syncing k2b-remote code..."
    local rsync_flag=""
    $DRY_RUN && rsync_flag="--dry-run"

    # The exclude list MUST mirror detect_changes (see its note + line ~143).
    # /workspace/ = bot runtime scratch (anchored root, directory-only), never source.
    rsync -av $rsync_flag \
        --exclude node_modules \
        --exclude dist \
        --exclude store \
        --exclude .env \
        --exclude /workspace/ \
        "$LOCAL_BASE/k2b-remote/" "$RSYNC_TARGET/k2b-remote/"

    if ! $DRY_RUN; then
        log "Building and restarting k2b-remote on Mini..."
        ssh "$MINI_HOST" "cd $MINI_PATH/k2b-remote && npm run build && pm2 restart k2b-remote"

        log "Verifying k2b-remote health..."
        sleep 2
        local status
        status=$(ssh "$MINI_HOST" "pm2 jlist" 2>/dev/null | python3 -c "
import sys, json
procs = json.load(sys.stdin)
for p in procs:
    if p['name'] == 'k2b-remote':
        print(p['pm2_env']['status'])
        break
" 2>/dev/null || echo "unknown")

        if [[ "$status" == "online" ]]; then
            log "k2b-remote is online"
        else
            err "k2b-remote status: $status -- check with: ssh macmini 'pm2 logs k2b-remote --lines 20 --nostream'"
        fi
    fi
}

sync_dashboard() {
    log "Syncing k2b-dashboard..."
    local rsync_flag=""
    $DRY_RUN && rsync_flag="--dry-run"

    rsync -av $rsync_flag \
        --exclude node_modules \
        --exclude dist \
        --exclude legacy-v2 \
        --exclude '.env*' \
        "$LOCAL_BASE/k2b-dashboard/" "$RSYNC_TARGET/k2b-dashboard/"

    if ! $DRY_RUN; then
        log "Building and restarting k2b-dashboard on Mini..."
        ssh "$MINI_HOST" "cd $MINI_PATH/k2b-dashboard && npm run build && pm2 restart k2b-dashboard"

        log "Verifying k2b-dashboard health..."
        sleep 2
        local status
        status=$(ssh "$MINI_HOST" "pm2 jlist" 2>/dev/null | python3 -c "
import sys, json
procs = json.load(sys.stdin)
for p in procs:
    if p['name'] == 'k2b-dashboard':
        print(p['pm2_env']['status'])
        break
" 2>/dev/null || echo "unknown")

        if [[ "$status" == "online" ]]; then
            log "k2b-dashboard is online"
        else
            err "k2b-dashboard status: $status -- check with: ssh macmini 'pm2 logs k2b-dashboard --lines 20 --nostream'"
        fi
    fi
}

sync_scripts() {
    log "Syncing scripts/ + launchd router-watchdog files..."
    local rsync_flag=""
    $DRY_RUN && rsync_flag="--dry-run"

    rsync -av $rsync_flag \
        --exclude '__pycache__/' --exclude '*.pyc' \
        "$LOCAL_BASE/scripts/" "$RSYNC_TARGET/scripts/"
    if [[ -d "$LOCAL_BASE/launchd" ]]; then
        rsync -av $rsync_flag --delete "$LOCAL_BASE/launchd/" "$RSYNC_TARGET/launchd/"
    fi
    if [[ -f "$LOCAL_BASE/README-router-watchdog.md" ]]; then
        rsync -av $rsync_flag "$LOCAL_BASE/README-router-watchdog.md" "$RSYNC_TARGET/README-router-watchdog.md"
    fi
}

# Run router-watchdog/install.sh on the Mini so the launchd-served `bin/`
# snapshot stays in sync with the rsynced source. Without this, /sync
# silently produces install/source drift: source files update, but the
# binary launchd actually invokes from `~/Library/Application Support/
# k2b-router-watchdog/bin/` keeps running an older snapshot.
# 2026-05-07 dry-run inspection caught exactly this -- the new
# leaf-optimizer formula was on Mini's source path while launchd kept
# firing the pre-fix installed snapshot.
#
# Runs UNCONDITIONALLY whenever target is remote (not in test mode) and
# install.sh exists locally, regardless of whether sync_scripts() detected
# changes. This closes the "auto mode skips when source matches but
# installed is stale" gap that Codex flagged as the HIGH finding on the
# 2026-05-07 review of this very fix. install.sh is idempotent (sha256
# manifest match -> skips file copy AND launchctl bootout/bootstrap), so
# always-running it costs only the SSH round-trip + a quick hash check
# when nothing has changed.
#
# Host derivation: parse the host component from RSYNC_TARGET rather than
# trusting $MINI alone, so a K2B_RSYNC_TARGET_PREFIX override (e.g.
# user@otherhost:/path for a different deploy target) routes the install
# to the SAME machine that received the rsync. If RSYNC_TARGET is purely
# local (no colon -- test mode) the install step is skipped via
# is_remote_target.
maybe_run_remote_install() {
    is_remote_target || return 0
    $DRY_RUN && return 0
    [[ -f "$LOCAL_BASE/scripts/router-watchdog/install.sh" ]] || return 0
    log "Running router-watchdog install.sh on $MINI_HOST..."
    ssh "$MINI_HOST" "bash ${MINI_PATH}/scripts/router-watchdog/install.sh"
}

# Main -- mode validation first so invalid modes exit fast (no SSH wait).
case "$MODE" in
    skills)
        needs_skills=true
        ;;
    code)
        needs_code=true
        ;;
    dashboard)
        needs_dashboard=true
        ;;
    scripts)
        needs_scripts=true
        ;;
    all)
        needs_skills=true
        needs_code=true
        needs_dashboard=true
        needs_scripts=true
        ;;
    auto)
        ;;  # detection happens after the reachability check below
    *)
        err "Unknown mode: $MODE"
        echo "Usage: deploy-to-mini.sh [auto|skills|code|dashboard|scripts|all] [--dry-run]"
        exit 1
        ;;
esac

# Reachability check must happen BEFORE detect_changes: rsync's dry-run runs
# over SSH for a remote target, so an unreachable Mini would silently report
# "no changes" instead of failing loud. Skipped for local RSYNC_TARGET (tests).
# Uses MINI_HOST parsed from RSYNC_TARGET, not the raw $MINI default, so
# K2B_RSYNC_TARGET_PREFIX overrides probe the SAME host that rsync targets.
if is_remote_target; then
    if ! ssh -o ConnectTimeout=5 "$MINI_HOST" "echo ok" &>/dev/null; then
        err "Cannot reach $MINI_HOST. Is it on?"
        exit 1
    fi
fi

if [[ "$MODE" == "auto" ]]; then
    detect_changes
    if ! $needs_skills && ! $needs_code && ! $needs_dashboard && ! $needs_scripts; then
        if [[ "$DETECT_ONLY" != "true" ]]; then
            warn "No changes detected. Use 'all' to force full sync."
            # Still reconcile router-watchdog installed-vs-source drift before
            # exiting -- source might match between machines while the launchd-
            # served install snapshot is stale. install.sh is sha256-manifest-
            # idempotent, so this is a cheap no-op when there is nothing to do.
            maybe_run_remote_install
        fi
        exit 0
    fi
fi

if [[ "$DETECT_ONLY" == "true" ]]; then
    $needs_skills && echo "skills"
    $needs_code && echo "code"
    $needs_dashboard && echo "dashboard"
    $needs_scripts && echo "scripts"
    exit 0
fi

$DRY_RUN && warn "DRY RUN -- no files will be changed"

# Summary
echo ""
log "Sync plan:"
$needs_skills && log "  - Skills + AGENTS.md/CLAUDE.md + .agents/skills + .codex/hooks.json + top-level docs"
$needs_code && log "  - k2b-remote code (+ build + restart)"
$needs_dashboard && log "  - k2b-dashboard code (+ build + restart)"
$needs_scripts && log "  - scripts/ + launchd router-watchdog files"
echo ""

# Execute
$needs_skills && sync_skills
$needs_code && sync_code
$needs_dashboard && sync_dashboard
$needs_scripts && sync_scripts

# Always reconcile router-watchdog install snapshot, regardless of which
# categories synced. Closes the install/source-drift class:
# - sync_scripts() runs -> install picks up new bin/launchd
# - sync_scripts() did NOT run (e.g. /sync skills only) -> install still
#   reconciles any pre-existing drift left over from a previous run
# install.sh is sha256-manifest-idempotent so the no-change case is cheap.
maybe_run_remote_install

echo ""
if $DRY_RUN; then
    log "Dry run complete. Run without --dry-run to sync."
else
    log "Sync complete."
fi
