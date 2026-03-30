#!/bin/bash
# vault-sync.sh — Sync Obsidian vault to NAS via SSH, then trigger RAG reindex
#
# Setup:
#   1. Configure SSH alias "nas" in ~/.ssh/config pointing to your NAS
#   2. Set RAG_API_KEY env var (or export it in your shell profile):
#      export RAG_API_KEY="your-api-key-here"
#   3. Add to crontab for automatic sync (recommended over LaunchAgent on macOS):
#      */10 * * * * RAG_API_KEY=your-key /path/to/vault-sync.sh >> /tmp/vault-sync-cron.log 2>&1
#
# Note: On macOS, use crontab instead of LaunchAgent — crontab inherits user
# Full Disk Access permissions while LaunchAgent is blocked by TCC.

SOURCE="${VAULT_SOURCE:-$HOME/Documents/obsidian/vault/}"
DEST_PRIMARY="${NAS_SSH_ALIAS:-nas}:/mnt/user/appdata/vault-mirror/"
DEST_FALLBACK="${NAS_FALLBACK_IP:-}:/mnt/user/appdata/vault-mirror/"
RAG_ENDPOINT="${RAG_ENDPOINT:-http://localhost:8900}"
LOGFILE="/tmp/vault-sync.log"
TIMESTAMP=$(date "+%Y-%m-%d %H:%M:%S")

log() {
    echo "[$TIMESTAMP] $1" >> "$LOGFILE"
}

# Test SSH connectivity via configured alias
check_nas_alias() {
    ssh -o ConnectTimeout=5 -o BatchMode=yes -o StrictHostKeyChecking=no \
        "${NAS_SSH_ALIAS:-nas}" "echo ok" 2>/dev/null
    return $?
}

# Test SSH connectivity by raw IP (fallback)
check_host_ip() {
    local ip="${1%%:*}"
    [ -z "$ip" ] && return 1
    ssh -o ConnectTimeout=5 -o BatchMode=yes -o StrictHostKeyChecking=no \
        "$ip" "exit" 2>/dev/null
    return $?
}

DEST=""
DEST_LABEL=""

if check_nas_alias; then
    DEST="$DEST_PRIMARY"
    DEST_LABEL="NAS (ssh alias)"
elif [ -n "$DEST_FALLBACK" ] && check_host_ip "$DEST_FALLBACK"; then
    DEST="$DEST_FALLBACK"
    DEST_LABEL="NAS (fallback IP)"
fi

if [ -z "$DEST" ]; then
    log "WARNING: NAS unreachable via both ssh alias and fallback IP, skipping sync"
    echo "Vault sync skipped: NAS unreachable"
    exit 0
fi

# Run rsync and capture stats
RSYNC_OUTPUT=$(rsync -az --delete \
    --exclude=".obsidian/" \
    --exclude=".claude/" \
    --exclude=".backups/" \
    --exclude="*.DS_Store" \
    --stats \
    "$SOURCE" "$DEST" 2>&1)

RSYNC_EXIT=$?

if [ $RSYNC_EXIT -ne 0 ]; then
    log "ERROR: rsync failed (exit $RSYNC_EXIT): $RSYNC_OUTPUT"
    echo "Vault sync failed: rsync error (exit $RSYNC_EXIT)"
    exit $RSYNC_EXIT
fi

# Parse number of transferred files from rsync --stats output
FILES_TRANSFERRED=$(echo "$RSYNC_OUTPUT" | grep "Number of regular files transferred" | awk '{print $NF}')
FILES_TRANSFERRED="${FILES_TRANSFERRED:-0}"

log "OK: Synced to $DEST_LABEL, $FILES_TRANSFERRED files transferred"
echo "Vault synced -> $DEST_LABEL ($FILES_TRANSFERRED files)"

# Trigger RAG re-indexing on NAS if any files were transferred
if echo "$FILES_TRANSFERRED" | grep -qE '^[1-9][0-9]*$'; then
    if [ -n "$RAG_API_KEY" ]; then
        ssh "${NAS_SSH_ALIAS:-nas}" "curl -s -X POST -H 'X-API-Key: $RAG_API_KEY' http://localhost:8900/index > /dev/null 2>&1 &" 2>/dev/null
    else
        ssh "${NAS_SSH_ALIAS:-nas}" "curl -s -X POST http://localhost:8900/index > /dev/null 2>&1 &" 2>/dev/null
    fi
    log "RAG reindex triggered ($FILES_TRANSFERRED files changed)"
    echo "RAG reindex triggered ($FILES_TRANSFERRED files changed)"
else
    log "RAG reindex skipped (no changes)"
    echo "RAG reindex skipped (no changes)"
fi
