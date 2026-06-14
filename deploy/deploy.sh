#!/usr/bin/env bash
set -euo pipefail

# ─── ScanGrade Deploy Script ─────────────────────────────────
# Usage: sudo ./deploy/deploy.sh
# Prerequisites: git, python3, venv, systemd, nginx, redis-server
# ─────────────────────────────────────────────────────────────

REPO_DIR="/opt/scangrade"
SERVICE_NAME="scangrade"
NGINX_SITE="scangrade"
GIT_BRANCH="main"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)

echo "┌─────────────────────────────────────────────┐"
echo "│  ScanGrade Deployment — $(date)  │"
echo "└─────────────────────────────────────────────┘"

# ── Prerequisites check ──
echo ""
echo "📋 Checking prerequisites..."

if ! command -v python3 &>/dev/null; then echo "❌ python3 not found"; exit 1; fi
if ! command -v git &>/dev/null; then echo "❌ git not found"; exit 1; fi
if ! command -v nginx &>/dev/null; then echo "⚠️  nginx not found — skipping config reload"; NGINX_MISSING=1; else NGINX_MISSING=0; fi
if ! command -v redis-server &>/dev/null; then echo "⚠️  redis-server not found — rate limiter will use memory (single worker only)"; fi

if systemctl is-active --quiet redis-server 2>/dev/null; then
    echo "✅ Redis is running"
else
    echo "⚠️  Redis is not running — rate limiter falls back to in-memory"
fi

# ── Pull latest code ──
echo ""
echo "📥 Pulling latest code ($GIT_BRANCH)..."
cd "$REPO_DIR"
git fetch origin
git reset --hard "origin/$GIT_BRANCH"
echo "✅ Commit: $(git log --oneline -1)"

# ── Backup current .env ──
if [ -f .env ]; then
    cp .env ".env.backup.$TIMESTAMP"
    echo "💾 .env backed up to .env.backup.$TIMESTAMP"
fi

# ── Install Python dependencies ──
echo ""
echo "📦 Installing Python dependencies..."
source .venv/bin/activate
pip install --upgrade pip --quiet
pip install -r requirements.txt --quiet
echo "✅ Dependencies installed ($(pip list --format=columns | wc -l) packages)"

# ── Copy NGINX config ──
if [ "$NGINX_MISSING" -eq 0 ] && [ -f "$REPO_DIR/deploy/nginx.conf" ]; then
    echo ""
    echo "🔧 Installing NGINX config..."
    sudo cp "$REPO_DIR/deploy/nginx.conf" "/etc/nginx/sites-available/$NGINX_SITE"
    if [ ! -L "/etc/nginx/sites-enabled/$NGINX_SITE" ]; then
        sudo ln -s "/etc/nginx/sites-available/$NGINX_SITE" "/etc/nginx/sites-enabled/"
    fi
    # Test config
    if sudo nginx -t; then
        sudo systemctl reload nginx
        echo "✅ NGINX config loaded"
    else
        echo "❌ NGINX config test failed — manual check required"
        exit 1
    fi
fi

# ── Restart Flask service ──
echo ""
echo "🔄 Restarting $SERVICE_NAME..."
sudo systemctl daemon-reload
sudo systemctl restart "$SERVICE_NAME"

# ── Verify service ──
sleep 3
if systemctl is-active --quiet "$SERVICE_NAME"; then
    echo "✅ $SERVICE_NAME is running (PID: $(systemctl show -p MainPID "$SERVICE_NAME" | cut -d= -f2))"
    echo ""
    echo "📋 Last 10 log lines:"
    sudo journalctl -u "$SERVICE_NAME" --no-pager -n 10
else
    echo "❌ $SERVICE_NAME failed to start!"
    echo ""
    echo "📋 Last 50 log lines:"
    sudo journalctl -u "$SERVICE_NAME" --no-pager -n 50
    exit 1
fi

# ── Health check ──
echo ""
echo "🏥 Health check..."
sleep 1
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8000/health 2>/dev/null || echo "000")
if [ "$HTTP_CODE" = "200" ]; then
    echo "✅ Application health check passed (HTTP $HTTP_CODE)"
else
    echo "⚠️  Health check returned HTTP $HTTP_CODE — may still be starting"
fi

echo ""
echo "┌─────────────────────────────────────────────┐"
echo "│  ✅ Deploy complete: $(date)  │"
echo "└─────────────────────────────────────────────┘"
echo ""
echo "   Service:  systemctl status $SERVICE_NAME"
echo "   Logs:     journalctl -u $SERVICE_NAME -f"
echo "   NGINX:    sudo nginx -t"
echo "   Site:     https://scan-grade.app"
echo ""
