#!/usr/bin/env bash
# ScanGrade VPS Backup — backup config files to /opt/scangrade/backups/
# Jalankan via cron: 0 3 * * * /opt/scangrade/scripts/backup.sh
set -euo pipefail

BACKUP_DIR="/opt/scangrade/backups"
RETENTION_DAYS=7
TIMESTAMP=$(date '+%Y%m%d_%H%M%S')
BACKUP_FILE="$BACKUP_DIR/scangrade-vps-$TIMESTAMP.tar.gz"
LOG="/var/log/scangrade-backup.log"

mkdir -p "$BACKUP_DIR"

echo "[$(date '+%Y-%m-%d %H:%M:%S')] Starting VPS backup..." >> "$LOG"

# Files to backup
tar -czf "$BACKUP_FILE" \
    /opt/scangrade/.env \
    /opt/scangrade/gunicorn.conf.py \
    /opt/scangrade/deploy/scangrade.service \
    /etc/nginx/sites-available/scangrade \
    2>/dev/null || true

# Try to include SSL certs (may fail if not exists)
if [ -d /etc/letsencrypt/live/scangrade.web.id ]; then
    tar -rf "$BACKUP_FILE" -C /etc/letsencrypt live/scangrade.web.id 2>/dev/null || true
fi

SIZE=$(du -h "$BACKUP_FILE" | cut -f1)
echo "[$(date '+%Y-%m-%d %H:%M:%S')] ✅ Backup created: $BACKUP_FILE ($SIZE)" >> "$LOG"

# Upload to Supabase Storage if configured
if command -v curl &>/dev/null; then
    SUPABASE_URL="${SUPABASE_URL:-}"
    SERVICE_KEY="${SUPABASE_SERVICE_KEY:-}"
    # Try to read from .env if not set
    if [ -z "$SUPABASE_URL" ] && [ -f /opt/scangrade/.env ]; then
        source /opt/scangrade/.env 2>/dev/null || true
    fi
    if [ -n "$SUPABASE_URL" ] && [ -n "$SUPABASE_SERVICE_KEY" ]; then
        BUCKET="scangrade-backups"
        REMOTE_PATH="vps/$TIMESTAMP.tar.gz"
        curl -s -X POST "$SUPABASE_URL/storage/v1/object/$BUCKET/$REMOTE_PATH" \
            -H "Authorization: Bearer $SERVICE_KEY" \
            -H "Content-Type: application/gzip" \
            --data-binary @"$BACKUP_FILE" \
            -o /dev/null && \
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] ✅ Uploaded to Supabase Storage" >> "$LOG" || \
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] ⚠️  Upload to Supabase failed" >> "$LOG"
    fi
fi

# Clean old backups
find "$BACKUP_DIR" -name "scangrade-vps-*.tar.gz" -mtime +$RETENTION_DAYS -delete 2>/dev/null || true
echo "[$(date '+%Y-%m-%d %H:%M:%S')] ✅ Backup complete (retention: $RETENTION_DAYS days)" >> "$LOG"
