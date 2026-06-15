#!/usr/bin/env bash
# ScanGrade Health Check — jalankan via cron setiap 5 menit
# CRON: */5 * * * * /opt/scangrade/scripts/health-check.sh >> /var/log/scangrade-health.log 2>&1

set -euo pipefail

TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')
APP_URL="http://127.0.0.1:8000"
SERVICE="scangrade"

# 1. Check systemd
if ! systemctl is-active --quiet "$SERVICE"; then
    echo "[$TIMESTAMP] ❌ $SERVICE is DOWN — restarting..."
    systemctl restart "$SERVICE"
    sleep 3
    if systemctl is-active --quiet "$SERVICE"; then
        echo "[$TIMESTAMP] ✅ $SERVICE restarted successfully"
    else
        echo "[$TIMESTAMP] ❌ $SERVICE failed to restart — manual intervention needed"
        exit 1
    fi
fi

# 2. Check HTTP health
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" "$APP_URL/health" 2>/dev/null || echo "000")
if [ "$HTTP_CODE" != "200" ]; then
    echo "[$TIMESTAMP] ❌ Health check failed (HTTP $HTTP_CODE) — restarting $SERVICE..."
    systemctl restart "$SERVICE"
fi

# 3. Check NGINX
if command -v nginx &>/dev/null; then
    if ! nginx -t 2>/dev/null; then
        echo "[$TIMESTAMP] ❌ NGINX config error"
    fi
fi

# 4. Check Redis
if systemctl is-active --quiet redis-server 2>/dev/null; then
    REDIS_PONG=$(redis-cli ping 2>/dev/null || echo "FAIL")
    if [ "$REDIS_PONG" != "PONG" ]; then
        echo "[$TIMESTAMP] ❌ Redis not responding — restarting..."
        systemctl restart redis-server
    fi
fi

# 5. Disk usage
DISK_USAGE=$(df / | awk 'NR==2 {print $5}' | sed 's/%//')
if [ "$DISK_USAGE" -gt 90 ]; then
    echo "[$TIMESTAMP] ⚠️  Disk usage critical: ${DISK_USAGE}%"
fi

echo "[$TIMESTAMP] ✅ Health check passed"
