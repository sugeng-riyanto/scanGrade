#!/bin/bash
# ─── ScanGrade Production Deploy ─────────────────────────────────
# Run this on the VPS via Biznet GIO web console
# Usage: bash deploy-production.sh
# ──────────────────────────────────────────────────────────────────

set -e

REPO_DIR="/opt/scangrade"
SERVICE_NAME="scangrade"
BRANCH="main"

echo "┌──────────────────────────────────────────────┐"
echo "│  ScanGrade Deploy — $(date)  │"
echo "└──────────────────────────────────────────────┘"

# 1. Navigate to repo
cd "$REPO_DIR" || { echo "❌ $REPO_DIR not found"; exit 1; }

# 2. Pull latest code
echo "→ Pulling latest code from $BRANCH..."
git fetch origin
git reset --hard "origin/$BRANCH"
git pull origin "$BRANCH"
echo "✅ Code updated: $(git log --oneline -1)"

# 3. Activate virtualenv and install deps
echo "→ Installing dependencies..."
source venv/bin/activate 2>/dev/null || source .venv/bin/activate 2>/dev/null
pip install -q -r requirements.txt 2>&1 | tail -3

# 4. Rebuild Tailwind CSS
echo "→ Rebuilding Tailwind CSS..."
npm run css:build 2>/dev/null || echo "⚠️  npm run css:build failed — CSS may be stale"

# 5. Set production env vars
echo "→ Configuring environment..."
sed -i 's/FLASK_DEBUG=1/FLASK_DEBUG=0/' .env 2>/dev/null || true
sed -i 's/FLASK_ENV=development/FLASK_ENV=production/' .env 2>/dev/null || true

# 6. Ensure SMTP vars exist
if ! grep -q "^SMTP_PASSWORD=" .env 2>/dev/null; then
    echo "⚠️  SMTP_PASSWORD not set in .env — email will not work"
fi

# 7. Restart service
echo "→ Restarting service..."
sudo systemctl restart "$SERVICE_NAME" 2>/dev/null && echo "✅ Service restarted" || echo "⚠️  systemctl restart failed — try manually"

# 8. Verify health
sleep 2
echo "→ Checking health..."
HEALTH=$(curl -s http://localhost:8000/health 2>/dev/null || curl -s http://localhost:5000/health 2>/dev/null)
if echo "$HEALTH" | grep -q '"status"'; then
    echo "✅ App is healthy: $HEALTH"
else
    echo "⚠️  Health check failed. Check logs: journalctl -u $SERVICE_NAME -n 20"
fi

# 9. Check if nginx is running
if systemctl is-active --quiet nginx 2>/dev/null; then
    echo "✅ Nginx is running"
else
    echo "⚠️  Nginx not running — start with: sudo systemctl start nginx"
fi

echo ""
echo "┌──────────────────────────────────────────────┐"
echo "│  Deploy complete!                             │"
echo "│  Check: curl http://103.93.133.193:8000/health"
echo "│  Logs:  journalctl -u scangrade -f           │"
echo "└──────────────────────────────────────────────┘"
