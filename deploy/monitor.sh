#!/bin/bash
# ============================================================
# ScanGrade — Lightweight System Monitor
# Logs CPU, RAM, Disk, response time every 5 minutes
# Log file: /var/log/scangrade-monitor.log
# ============================================================
# Dipanggil via cron: */5 * * * * /opt/scangrade/deploy/monitor.sh
# ============================================================

LOG="/var/log/scangrade-monitor.log"
NOW=$(date '+%Y-%m-%d %H:%M:%S')

# CPU usage (percent)
CPU=$(top -bn1 | grep "Cpu(s)" | awk '{print $2 + $4}' | cut -d. -f1)

# RAM usage (percent)
RAM=$(free | grep Mem | awk '{printf "%.0f", $3/$2 * 100}')

# Disk usage (percent)
DISK=$(df / | tail -1 | awk '{print $5}' | tr -d '%')

# Response time from health endpoint (ms) — no bc needed
RT=$(curl -s -o /dev/null -w "%{time_total}" https://scangrade.web.id/health 2>/dev/null)
RT_MS=$(awk "BEGIN {printf \"%d\", $RT * 1000}" 2>/dev/null || echo "-1")
[ "$RT_MS" = "0" ] && RT_MS="-1"

# Uptime (days)
UPTIME=$(uptime -p | sed 's/up //')

echo "$NOW | CPU:${CPU}% | RAM:${RAM}% | DISK:${DISK}% | RT:${RT_MS}ms | UP:${UPTIME}" >> "$LOG"

# Alert if thresholds exceeded
if [ "$CPU" -gt 80 ] || [ "$RAM" -gt 90 ] || [ "$DISK" -gt 90 ]; then
    echo "$NOW | ⚠️  ALERT: CPU=$CPU RAM=$RAM DISK=$DISK" >> "$LOG"
fi

# Keep last 10000 lines
tail -n 10000 "$LOG" > "${LOG}.tmp" && mv "${LOG}.tmp" "$LOG"
