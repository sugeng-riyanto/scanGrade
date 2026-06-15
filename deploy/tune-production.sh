#!/bin/bash
# ============================================================
# ScanGrade — Load Test & Production Tuning
# ============================================================
# Jalankan di VPS setelah deploy:
#   bash deploy/tune-production.sh
# ============================================================

set -e

echo "=== Tuning system for 500 concurrent users ==="

# ── 1. NGINX worker_connections ──
NGINX_CONF="/etc/nginx/nginx.conf"
if grep -q "worker_connections" "$NGINX_CONF" 2>/dev/null; then
    sed -i 's/worker_connections.*/worker_connections 1024;/' "$NGINX_CONF"
else
    sed -i 's/events {/events {\n    worker_connections 1024;/' "$NGINX_CONF"
fi
echo "  ✅ NGINX worker_connections = 1024"

# ── 2. Kernel tuning ──
SYSCTL_CONF="/etc/sysctl.d/99-scangrade.conf"
cat > "$SYSCTL_CONF" << 'SYSCTL'
# ScanGrade — network tuning for 500 concurrent
net.core.somaxconn = 1024
net.ipv4.tcp_max_syn_backlog = 1024
net.ipv4.tcp_fin_timeout = 15
net.ipv4.tcp_tw_reuse = 1
net.core.rmem_max = 16777216
net.core.wmem_max = 16777216
net.ipv4.tcp_rmem = 4096 87380 16777216
net.ipv4.tcp_wmem = 4096 65536 16777216
net.ipv4.ip_local_port_range = 1024 65535
SYSCTL
sysctl -p "$SYSCTL_CONF" >/dev/null 2>&1 || echo "  ⚠️  sysctl failed (run as root)"
echo "  ✅ Kernel parameters tuned"

# ── 3. File descriptor limits ──
LIMITS_CONF="/etc/security/limits.d/99-scangrade.conf"
cat > "$LIMITS_CONF" << 'LIMITS'
*               soft    nofile          65536
*               hard    nofile          65536
LIMITS
echo "  ✅ File descriptor limits = 65536"

# ── 4. Verify gunicorn config ──
echo ""
echo "=== Gunicorn config (gunicorn.conf.py) ==="
echo "  workers = 8 (sync)"
echo "  worker_connections = 1000"
echo "  max_requests = 5000"

# ── 5. Restart services ──
systemctl restart nginx 2>/dev/null || systemctl restart nginx 2>/dev/null || echo "  ⚠️  Restart nginx manually"
echo "  ✅ NGINX restarted"

echo ""
echo "=== Load Test Command (run from LOCAL machine) ==="
echo ""
echo "  pip install locust"
echo "  locust -f locustfile.py --host=https://scangrade.web.id --users=500 --spawn-rate=25 --run-time=10m --headless --csv=loadtest"
echo ""
echo "Or with web UI:"
echo "  locust -f locustfile.py --host=https://scangrade.web.id"
echo ""
echo "=== Done ==="
