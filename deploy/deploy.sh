#!/usr/bin/env bash
set -euo pipefail

# ─── ScanGrade Deploy Script ─────────────────────────────────
# Usage: sudo ./deploy/deploy.sh
# Prerequisites: git, python3, venv, systemd
# ─────────────────────────────────────────────────────────────

REPO_DIR="/opt/scangrade"
SERVICE_NAME="scangrade"
GIT_BRANCH="main"

echo "🚀 Deploying ScanGrade..."

# 1. Pull latest code
echo "📥 Pulling from git ($GIT_BRANCH)..."
cd "$REPO_DIR"
git fetch origin
git reset --hard "origin/$GIT_BRANCH"

# 2. Activate venv & install requirements
echo "📦 Installing Python dependencies..."
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt --quiet

# 3. Restart service
echo "🔄 Restarting $SERVICE_NAME service..."
sudo systemctl daemon-reload
sudo systemctl restart "$SERVICE_NAME"

# 4. Verify
sleep 2
if systemctl is-active --quiet "$SERVICE_NAME"; then
    echo "✅ $SERVICE_NAME is running."
    sudo journalctl -u "$SERVICE_NAME" --no-pager -n 10
else
    echo "❌ $SERVICE_NAME failed to start. Checking logs..."
    sudo journalctl -u "$SERVICE_NAME" --no-pager -n 50
    exit 1
fi

echo "✅ Deploy complete!"
