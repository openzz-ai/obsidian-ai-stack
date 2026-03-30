#!/bin/bash
# claude-config-backup.sh — Daily backup of Claude Code config to remote servers
#
# Excludes sessions/, file-history/, telemetry/ and other account-specific data
# so the backup is portable across accounts and machines.
# After restore: rsync back + claude login = full environment restored.
#
# Configuration:
#   BACKUP_PRIMARY    SSH alias or IP of primary backup target (e.g., "nas")
#   BACKUP_FALLBACK   SSH alias or IP of fallback target (e.g., "backup-server")
#   BACKUP_PATH       Remote path for the backup (default: /mnt/user/appdata/claude-backup/)

set -euo pipefail

SRC="$HOME/.claude/"
BACKUP_PRIMARY="${BACKUP_PRIMARY:-nas}"
BACKUP_FALLBACK="${BACKUP_FALLBACK:-}"
BACKUP_PATH="${BACKUP_PATH:-/mnt/user/appdata/claude-backup/}"
DEST_PRIMARY="${BACKUP_PRIMARY}:${BACKUP_PATH}"
LOG_FILE="$HOME/.claude/logs/backup.log"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'

ok()   { echo -e "${GREEN}✅ $*${NC}"; }
warn() { echo -e "${YELLOW}⚠️  $*${NC}"; }
info() { echo -e "${CYAN}   $*${NC}"; }
err()  { echo -e "${RED}❌ $*${NC}"; }

mkdir -p "$(dirname "$LOG_FILE")"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG_FILE"
}

# ── Exclude list ──────────────────────────────────────────────────────────────
# sessions/        — account-specific session history, not portable
# file-history/    — file edit snapshots, rebuildable from git
# shell-snapshots/ — machine-specific shell state
# telemetry/       — telemetry data
# cache/           — local cache, rebuildable
RSYNC_EXCLUDES=(
    "--exclude=sessions/"
    "--exclude=file-history/"
    "--exclude=shell-snapshots/"
    "--exclude=telemetry/"
    "--exclude=cache/"
    "--exclude=*.log"
    "--exclude=.DS_Store"
)

check_dest() {
    local host="$1"
    ssh -o ConnectTimeout=3 -o BatchMode=yes "$host" "echo ok" > /dev/null 2>&1
}

do_backup() {
    local dest="$1"
    local label="$2"

    log "Starting backup to $label ($dest)..."

    rsync -az --delete \
        "${RSYNC_EXCLUDES[@]}" \
        "$SRC" \
        "$dest" 2>&1 | tee -a "$LOG_FILE"

    local exit_code=${PIPESTATUS[0]}
    if [ "$exit_code" -eq 0 ]; then
        log "Backup succeeded → $label"
        ok "Backup succeeded → $label"
        return 0
    else
        log "Backup failed → $label (exit $exit_code)"
        err "Backup failed → $label"
        return 1
    fi
}

# ── Main ──────────────────────────────────────────────────────────────────────

echo ""
echo "═══════════════════════════════════════"
echo "  Claude Code Config Backup"
echo "  $(date '+%Y-%m-%d %H:%M:%S')"
echo "═══════════════════════════════════════"
echo ""

log "=== Claude Config Backup Started ==="

SUCCESS=false

if check_dest "$BACKUP_PRIMARY"; then
    info "$BACKUP_PRIMARY reachable, starting backup..."
    if do_backup "$DEST_PRIMARY" "$BACKUP_PRIMARY"; then
        SUCCESS=true
    fi
elif [ -n "$BACKUP_FALLBACK" ] && check_dest "$BACKUP_FALLBACK"; then
    info "Primary unreachable, trying fallback $BACKUP_FALLBACK..."
    DEST_FALLBACK="${BACKUP_FALLBACK}:${BACKUP_PATH}"
    if do_backup "$DEST_FALLBACK" "$BACKUP_FALLBACK"; then
        SUCCESS=true
    fi
else
    err "All backup targets unreachable"
    err "Check VPN/Tailscale: tailscale status"
    log "ERROR: all backup targets unreachable"
    exit 1
fi

SIZE=$(du -sh "$SRC" --exclude=sessions --exclude=file-history --exclude=shell-snapshots 2>/dev/null | cut -f1 || echo "?")
info "Source size (excluding non-portable data): ~$SIZE"

echo ""
if $SUCCESS; then
    log "=== Backup Completed Successfully ==="
    ok "Backup complete"
else
    log "=== Backup Failed ==="
    err "Backup failed — check log: $LOG_FILE"
    exit 1
fi
echo ""
