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
if ! command -v redis-server &>/dev/null; then echo "⚠️  redis-server not found — installing..."; sudo apt install -y redis-server; fi

# Ensure Redis is running
if systemctl is-active --quiet redis-server 2>/dev/null; then
    echo "✅ Redis is running"
else
    echo "🔄 Starting Redis..."
    sudo systemctl enable redis-server --now
    echo "✅ Redis started"
fi

# Create log directory
sudo mkdir -p /var/log/scangrade
sudo chown scangrade:scangrade /var/log/scangrade 2>/dev/null || true

# Create scangrade user if not exists
if ! id scangrade &>/dev/null; then
    echo "👤 Creating scangrade user..."
    sudo useradd -r -s /bin/false scangrade 2>/dev/null || true
    sudo chown -R scangrade:scangrade "$REPO_DIR"
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

# ── Ensure production env vars ──
echo ""
echo "🔧 Setting production env vars..."
sed -i 's/^FLASK_DEBUG=1/FLASK_DEBUG=0/' .env 2>/dev/null || true
sed -i 's/^FLASK_ENV=development/FLASK_ENV=production/' .env 2>/dev/null || true

# Ensure REDIS_URL is set
if ! grep -q '^REDIS_URL=' .env 2>/dev/null; then
    echo 'REDIS_URL=redis://localhost:6379/0' >> .env
    echo "✅ Added REDIS_URL to .env"
fi

# Ensure SMTP vars exist
if ! grep -q '^SMTP_EMAIL=' .env 2>/dev/null; then
    echo 'SMTP_EMAIL=scangrade9@gmail.com' >> .env
    echo 'SMTP_PASSWORD=' >> .env
    echo "⚠️  SMTP vars added — set SMTP_PASSWORD in .env"
fi

# ── Build Tailwind CSS ──
echo ""
echo "🎨 Building Tailwind CSS..."
npm install --silent 2>/dev/null
npm run css:build
echo "✅ Tailwind CSS built"

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

# ── Install Celery service ──
if [ -f "$REPO_DIR/deploy/celery.service" ]; then
    echo ""
    echo "⚙️  Installing Celery worker service..."
    sudo cp "$REPO_DIR/deploy/celery.service" "/etc/systemd/system/scangrade-celery.service"
    sudo systemctl daemon-reload
    sudo systemctl enable scangrade-celery.service 2>/dev/null || true
fi

# ── Restart Flask service ──
echo ""
echo "🔄 Restarting $SERVICE_NAME..."
sudo systemctl restart "$SERVICE_NAME"

# ── Restart Celery worker ──
echo "🔄 Restarting Celery worker..."
sudo systemctl restart scangrade-celery.service 2>/dev/null || echo "⚠️  Celery service not installed yet — run: sudo cp deploy/celery.service /etc/systemd/system/ && sudo systemctl daemon-reload && sudo systemctl enable scangrade-celery && sudo systemctl start scangrade-celery"

# ── Verify services ──
sleep 3
for svc in "$SERVICE_NAME" "scangrade-celery"; do
    if systemctl is-active --quiet "$svc" 2>/dev/null; then
        echo "✅ $svc is running (PID: $(systemctl show -p MainPID "$svc" | cut -d= -f2))"
    else
        echo "⚠️  $svc is not running (may not be installed)"
    fi
done
echo ""
echo "📋 Flask last 10 log lines:"
sudo journalctl -u "$SERVICE_NAME" --no-pager -n 10

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
