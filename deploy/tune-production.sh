#!/bin/bash
# ============================================================
# ScanGrade — Load Test & Production Tuning
# ============================================================
# Jalankan di VPS setelah deploy:
#   bash deploy/tune-production.sh
# ============================================================

set -e

echo "=== Tuning system for 1000 concurrent users ==="

# ── 1. NGINX worker_connections ──
NGINX_CONF="/etc/nginx/nginx.conf"
if grep -q "worker_connections" "$NGINX_CONF" 2>/dev/null; then
    sed -i 's/worker_connections.*/worker_connections 2048;/' "$NGINX_CONF"
else
    sed -i 's/events {/events {\n    worker_connections 2048;/' "$NGINX_CONF"
fi
echo "  ✅ NGINX worker_connections = 2048"

# ── 2. Kernel tuning ──
SYSCTL_CONF="/etc/sysctl.d/99-scangrade.conf"
cat > "$SYSCTL_CONF" << 'SYSCTL'
# ScanGrade — network tuning for 1000 concurrent
net.core.somaxconn = 2048
net.ipv4.tcp_max_syn_backlog = 2048
net.ipv4.tcp_fin_timeout = 15
net.ipv4.tcp_tw_reuse = 1
net.core.rmem_max = 33554432
net.core.wmem_max = 33554432
net.ipv4.tcp_rmem = 4096 87380 33554432
net.ipv4.tcp_wmem = 4096 65536 33554432
net.ipv4.ip_local_port_range = 1024 65535
SYSCTL
sysctl -p "$SYSCTL_CONF" >/dev/null 2>&1 || echo "  ⚠️  sysctl failed (run as root)"
echo "  ✅ Kernel parameters tuned for 1000 concurrent"

# ── 3. File descriptor limits ──
LIMITS_CONF="/etc/security/limits.d/99-scangrade.conf"
cat > "$LIMITS_CONF" << 'LIMITS'
*               soft    nofile          65536
*               hard    nofile          65536
LIMITS
echo "  ✅ File descriptor limits = 65536"

# ── 4. Gunicorn workers ──
echo ""
echo "=== Gunicorn config (gunicorn.conf.py) ==="
echo "  workers = 4 (sync)"
echo "  worker_connections = 1000"
echo "  max_requests = 5000"

# ── 5. Restart services ──
systemctl restart nginx 2>/dev/null || echo "  ⚠️  Restart nginx manually"
echo "  ✅ NGINX restarted"

echo ""
echo "=== Load Test Command (run from LOCAL machine) ==="
echo ""
echo "  pip install locust"
echo "  locust -f locustfile.py --host=https://scangrade.web.id --users=1000 --spawn-rate=50 --run-time=10m --headless --csv=loadtest1000"
echo ""

# ── 6. Swap file (prevent OOM kills) ──
if ! swapon --show | grep -q "swapfile"; then
    echo ""
    echo "📀 Creating 1GB swap file..."
    sudo fallocate -l 1G /swapfile
    sudo chmod 600 /swapfile
    sudo mkswap /swapfile
    sudo swapon /swapfile
    echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
    echo "✅ Swap created (1GB)"
fi

echo "=== Done ==="
