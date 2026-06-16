#!/usr/bin/env bash
# ScanGrade — Setup cron jobs
# Usage: bash deploy/setup-cron.sh
set -euo pipefail

echo "=== Setting up ScanGrade cron jobs ==="

# Remove old scangrade cron entries
crontab -l 2>/dev/null | grep -v "scangrade" | grep -v "health-check" | grep -v "backup.sh" | grep -v "monitor.sh" > /tmp/cron_new 2>/dev/null || true

# Add health check (every 5 min)
echo "*/5 * * * * /opt/scangrade/deploy/health-check.sh >> /var/log/scangrade-health.log 2>&1" >> /tmp/cron_new

# Add monitoring (every 5 min)
echo "*/5 * * * * /opt/scangrade/deploy/monitor.sh" >> /tmp/cron_new

# Add backup (daily 3 AM)
echo "0 3 * * * /opt/scangrade/deploy/backup.sh" >> /tmp/cron_new

crontab /tmp/cron_new
rm -f /tmp/cron_new

echo ""
echo "=== Cron jobs installed ==="
crontab -l
