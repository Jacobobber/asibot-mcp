#!/usr/bin/env bash
# Asibot automated backup script
# Dumps PostgreSQL + copies master encryption key, compresses, rotates.
# Run via cron: 0 2 * * * /home/jacob/asibot/deploy/backup.sh >> /var/log/asibot-backup.log 2>&1

set -euo pipefail

# --- Configuration ---
BACKUP_DIR="${ASIBOT_BACKUP_DIR:-/backups/asibot}"
COMPOSE_DIR="${ASIBOT_COMPOSE_DIR:-/home/jacob/asibot}"
MASTER_KEY="${ASIBOT_DATA_DIR:-$HOME/.asibot}/master.key"
RETENTION_DAYS=30
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
WORK_DIR=$(mktemp -d)

trap 'rm -rf "$WORK_DIR"' EXIT

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }

# --- Preflight checks ---
if [ ! -f "$MASTER_KEY" ]; then
    log "ERROR: Master key not found at $MASTER_KEY"
    exit 1
fi

if ! docker compose -f "$COMPOSE_DIR/docker-compose.yml" ps postgres --status running -q >/dev/null 2>&1; then
    log "ERROR: postgres container is not running"
    exit 1
fi

mkdir -p "$BACKUP_DIR"

# --- 1. PostgreSQL dump ---
log "Starting pg_dump..."
docker compose -f "$COMPOSE_DIR/docker-compose.yml" exec -T postgres \
    pg_dump -U asibot -Fc asibot > "$WORK_DIR/asibot.dump"

DUMP_SIZE=$(stat -c%s "$WORK_DIR/asibot.dump" 2>/dev/null || stat -f%z "$WORK_DIR/asibot.dump")
if [ "$DUMP_SIZE" -lt 100 ]; then
    log "ERROR: pg_dump produced suspiciously small file (${DUMP_SIZE} bytes)"
    exit 1
fi
log "pg_dump complete (${DUMP_SIZE} bytes)"

# --- 2. Copy master key ---
cp "$MASTER_KEY" "$WORK_DIR/master.key"
log "Master key copied"

# --- 3. Compress into archive ---
ARCHIVE="$BACKUP_DIR/asibot_backup_${TIMESTAMP}.tar.gz"
tar czf "$ARCHIVE" -C "$WORK_DIR" asibot.dump master.key
ARCHIVE_SIZE=$(stat -c%s "$ARCHIVE" 2>/dev/null || stat -f%z "$ARCHIVE")
log "Archive created: $ARCHIVE (${ARCHIVE_SIZE} bytes)"

# --- 4. Rotate old backups ---
DELETED=$(find "$BACKUP_DIR" -name "asibot_backup_*.tar.gz" -mtime +"$RETENTION_DAYS" -delete -print | wc -l)
if [ "$DELETED" -gt 0 ]; then
    log "Rotated $DELETED backup(s) older than ${RETENTION_DAYS} days"
fi

log "Backup complete: $ARCHIVE"
