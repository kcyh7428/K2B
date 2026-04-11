#!/bin/bash
# deploy-to-mini.sh -- Sync K2B project files from MacBook to Mac Mini
#
# Usage:
#   deploy-to-mini.sh              # auto-detect what changed, sync it
#   deploy-to-mini.sh skills       # sync skills + CLAUDE.md + architecture
#   deploy-to-mini.sh code         # sync k2b-remote + rebuild + restart
#   deploy-to-mini.sh scripts      # sync scripts/
#   deploy-to-mini.sh all          # sync everything
#   deploy-to-mini.sh --dry-run    # show what would sync without doing it

set -euo pipefail

MINI="macmini"
LOCAL_BASE="$HOME/Projects/K2B"
REMOTE_BASE="~/Projects/K2B"
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

# Detect what changed
detect_changes() {
    local changes
    cd "$LOCAL_BASE"

    # Try uncommitted changes first
    changes=$(git diff --name-only HEAD 2>/dev/null || true)

    # If nothing uncommitted, check last commit
    if [[ -z "$changes" ]]; then
        changes=$(git diff --name-only HEAD~1 HEAD 2>/dev/null || true)
    fi

    # Also check untracked files in key directories
    local untracked
    untracked=$(git ls-files --others --exclude-standard .claude/skills/ scripts/ k2b-remote/ 2>/dev/null || true)
    changes="$changes"$'\n'"$untracked"

    echo "$changes"
}

needs_skills=false
needs_code=false
needs_scripts=false

categorize() {
    local changes="$1"
    if echo "$changes" | grep -qE '\.claude/|CLAUDE\.md|K2B_ARCHITECTURE\.md'; then
        needs_skills=true
    fi
    if echo "$changes" | grep -qE '^k2b-remote/'; then
        needs_code=true
    fi
    if echo "$changes" | grep -qE '^scripts/'; then
        needs_scripts=true
    fi
}

sync_skills() {
    log "Syncing skills + top-level docs..."
    local rsync_flag=""
    $DRY_RUN && rsync_flag="--dry-run"

    # Top-level docs: sync any that exist. K2B_ARCHITECTURE.md was removed 2026-04
    # but README.md is user-facing documentation worth keeping in sync.
    for doc in CLAUDE.md README.md K2B_ARCHITECTURE.md; do
        if [[ -f "$LOCAL_BASE/$doc" ]]; then
            rsync -av $rsync_flag "$LOCAL_BASE/$doc" "$MINI:$REMOTE_BASE/$doc"
        fi
    done

    rsync -av $rsync_flag --delete "$LOCAL_BASE/.claude/skills/" "$MINI:$REMOTE_BASE/.claude/skills/"

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
        "$LOCAL_BASE/k2b-remote/" "$MINI:$REMOTE_BASE/k2b-remote/"

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

sync_scripts() {
    log "Syncing scripts/..."
    local rsync_flag=""
    $DRY_RUN && rsync_flag="--dry-run"

    rsync -av $rsync_flag "$LOCAL_BASE/scripts/" "$MINI:$REMOTE_BASE/scripts/"
}

# Main
case "$MODE" in
    skills)
        needs_skills=true
        ;;
    code)
        needs_code=true
        ;;
    scripts)
        needs_scripts=true
        ;;
    all)
        needs_skills=true
        needs_code=true
        needs_scripts=true
        ;;
    auto)
        changes=$(detect_changes)
        if [[ -z "$changes" || "$changes" == $'\n' ]]; then
            warn "No changes detected. Use 'all' to force full sync."
            exit 0
        fi
        categorize "$changes"
        if ! $needs_skills && ! $needs_code && ! $needs_scripts; then
            warn "Changes detected but none in syncable categories."
            echo "$changes"
            exit 0
        fi
        ;;
    *)
        err "Unknown mode: $MODE"
        echo "Usage: deploy-to-mini.sh [auto|skills|code|scripts|all] [--dry-run]"
        exit 1
        ;;
esac

# Check Mini is reachable
if ! ssh -o ConnectTimeout=5 "$MINI" "echo ok" &>/dev/null; then
    err "Cannot reach Mac Mini (ssh macmini). Is it on?"
    exit 1
fi

$DRY_RUN && warn "DRY RUN -- no files will be changed"

# Summary
echo ""
log "Sync plan:"
$needs_skills && log "  - Skills + CLAUDE.md + K2B_ARCHITECTURE.md"
$needs_code && log "  - k2b-remote code (+ build + restart)"
$needs_scripts && log "  - scripts/"
echo ""

# Execute
$needs_skills && sync_skills
$needs_code && sync_code
$needs_scripts && sync_scripts

echo ""
if $DRY_RUN; then
    log "Dry run complete. Run without --dry-run to sync."
else
    log "Sync complete."
fi
