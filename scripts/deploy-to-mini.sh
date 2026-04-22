#!/bin/bash
# deploy-to-mini.sh -- Sync K2B project files from MacBook to Mac Mini
#
# Usage:
#   deploy-to-mini.sh              # auto-detect what changed, sync it
#   deploy-to-mini.sh skills       # sync skills + CLAUDE.md + architecture
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

set -euo pipefail

MINI="${K2B_MINI:-macmini}"
LOCAL_BASE="${K2B_LOCAL_BASE:-$HOME/Projects/K2B}"
REMOTE_BASE="${K2B_REMOTE_BASE:-~/Projects/K2B}"
RSYNC_TARGET="${K2B_RSYNC_TARGET_PREFIX:-${MINI}:${REMOTE_BASE}}"
DETECT_ONLY="${K2B_DETECT_ONLY:-false}"
DRY_RUN=false
MODE="${1:-auto}"

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
    for doc in CLAUDE.md README.md K2B_ARCHITECTURE.md .mcp.json; do
        if [[ -f "$LOCAL_BASE/$doc" ]]; then
            if rsync_has_changes "$LOCAL_BASE/$doc" "$RSYNC_TARGET/$doc"; then
                needs_skills=true
            fi
        fi
    done
    if [[ -d "$LOCAL_BASE/.claude/skills" ]]; then
        if rsync_has_changes "$LOCAL_BASE/.claude/skills/" "$RSYNC_TARGET/.claude/skills/" --delete; then
            needs_skills=true
        fi
    fi
    if [[ -d "$LOCAL_BASE/k2b-remote" ]]; then
        if rsync_has_changes "$LOCAL_BASE/k2b-remote/" "$RSYNC_TARGET/k2b-remote/" \
            --exclude node_modules --exclude dist --exclude store --exclude .env; then
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
        if rsync_has_changes "$LOCAL_BASE/scripts/" "$RSYNC_TARGET/scripts/"; then
            needs_scripts=true
        fi
    fi
}

sync_skills() {
    log "Syncing skills + top-level docs..."
    local rsync_flag=""
    $DRY_RUN && rsync_flag="--dry-run"

    # Top-level docs: sync any that exist. K2B_ARCHITECTURE.md was removed 2026-04
    # but README.md is user-facing documentation worth keeping in sync.
    # .mcp.json added 2026-04-19 after silent drift let MiniMax MCP BASE_PATH
    # diverge between machines (MacBook user `keithmbpm2` vs Mini user `fastshower`),
    # breaking all bot-initiated image generation.
    for doc in CLAUDE.md README.md K2B_ARCHITECTURE.md .mcp.json; do
        if [[ -f "$LOCAL_BASE/$doc" ]]; then
            rsync -av $rsync_flag "$LOCAL_BASE/$doc" "$RSYNC_TARGET/$doc"
        fi
    done

    rsync -av $rsync_flag --delete "$LOCAL_BASE/.claude/skills/" "$RSYNC_TARGET/.claude/skills/"

    if ! $DRY_RUN; then
        log "Verifying skills on Mini..."
        local remote_count
        remote_count=$(ssh "$MINI" "ls -d $REMOTE_BASE/.claude/skills/k2b-*/ 2>/dev/null | wc -l" | tr -d ' ')
        local local_count
        local_count=$(ls -d "$LOCAL_BASE/.claude/skills/k2b-"*/ 2>/dev/null | wc -l | tr -d ' ')
        if [[ "$remote_count" == "$local_count" ]]; then
            log "Skills verified: $remote_count skill folders on both machines"
        else
            warn "Skill count mismatch: local=$local_count remote=$remote_count"
        fi
    fi
}

sync_code() {
    log "Syncing k2b-remote code..."
    local rsync_flag=""
    $DRY_RUN && rsync_flag="--dry-run"

    rsync -av $rsync_flag \
        --exclude node_modules \
        --exclude dist \
        --exclude store \
        --exclude .env \
        "$LOCAL_BASE/k2b-remote/" "$RSYNC_TARGET/k2b-remote/"

    if ! $DRY_RUN; then
        log "Building and restarting k2b-remote on Mini..."
        ssh "$MINI" "cd $REMOTE_BASE/k2b-remote && npm run build && pm2 restart k2b-remote"

        log "Verifying k2b-remote health..."
        sleep 2
        local status
        status=$(ssh "$MINI" "pm2 jlist" 2>/dev/null | python3 -c "
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
        ssh "$MINI" "cd $REMOTE_BASE/k2b-dashboard && npm run build && pm2 restart k2b-dashboard"

        log "Verifying k2b-dashboard health..."
        sleep 2
        local status
        status=$(ssh "$MINI" "pm2 jlist" 2>/dev/null | python3 -c "
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
    log "Syncing scripts/..."
    local rsync_flag=""
    $DRY_RUN && rsync_flag="--dry-run"

    rsync -av $rsync_flag "$LOCAL_BASE/scripts/" "$RSYNC_TARGET/scripts/"
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
if is_remote_target; then
    if ! ssh -o ConnectTimeout=5 "$MINI" "echo ok" &>/dev/null; then
        err "Cannot reach Mac Mini (ssh macmini). Is it on?"
        exit 1
    fi
fi

if [[ "$MODE" == "auto" ]]; then
    detect_changes
    if ! $needs_skills && ! $needs_code && ! $needs_dashboard && ! $needs_scripts; then
        [[ "$DETECT_ONLY" != "true" ]] && warn "No changes detected. Use 'all' to force full sync."
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
$needs_skills && log "  - Skills + CLAUDE.md + README.md + K2B_ARCHITECTURE.md + .mcp.json"
$needs_code && log "  - k2b-remote code (+ build + restart)"
$needs_dashboard && log "  - k2b-dashboard code (+ build + restart)"
$needs_scripts && log "  - scripts/"
echo ""

# Execute
$needs_skills && sync_skills
$needs_code && sync_code
$needs_dashboard && sync_dashboard
$needs_scripts && sync_scripts

echo ""
if $DRY_RUN; then
    log "Dry run complete. Run without --dry-run to sync."
else
    log "Sync complete."
fi
