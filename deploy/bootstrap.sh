#!/usr/bin/env bash
set -euo pipefail
# ScanGrade VPS Bootstrap - run this on fresh Ubuntu 22.04 VPS
# Usage: curl -sL https://raw.githubusercontent.com/sugeng-riyanto/scanGrade/main/scripts/bootstrap.sh | bash

echo "============================================"
echo "  ScanGrade VPS Bootstrap"
echo "============================================"

# 1. System deps
sudo apt update
sudo apt install -y python3-pip python3.10-venv redis-server nginx git curl build-essential \
    libgl1-mesa-glx libglib2.0-0 libsm6 libxext6 libxrender-dev libmagic1 poppler-utils

# 2. Enable Redis
sudo systemctl enable redis-server --now

# 3. Clone repo
cd /opt
sudo rm -rf scangrade 2>/dev/null || true
sudo git clone https://github.com/sugeng-riyanto/scanGrade.git scangrade
sudo chown -R $(whoami):$(whoami) scangrade
cd scangrade

# 4. .env file
#
# These are the box's secrets, and they are demanded from the environment
# rather than written here. This script once spelled out a **service-role** key
# — the one credential that bypasses every row-level-security policy — and a
# Flask secret key in a repository anyone can read; anything reading that file
# could then act as the backend, or forge a session. Export them first:
#
#   export SUPABASE_URL=https://<ref>.supabase.co
#   export SUPABASE_ANON_KEY=...      # public: it ships in every page's HTML
#   export SUPABASE_SERVICE_KEY=...   # secret: it bypasses RLS, keep it off the repo
#   export FLASK_SECRET_KEY=$(python3 -c 'import secrets; print(secrets.token_hex(32))')
: "${SUPABASE_URL:?export SUPABASE_URL before running this bootstrap}"
: "${SUPABASE_ANON_KEY:?export SUPABASE_ANON_KEY before running this bootstrap}"
: "${SUPABASE_SERVICE_KEY:?export SUPABASE_SERVICE_KEY before running this bootstrap}"
: "${FLASK_SECRET_KEY:?export FLASK_SECRET_KEY before running this bootstrap}"
cat > .env <<EOF
SUPABASE_URL=$SUPABASE_URL
SUPABASE_ANON_KEY=$SUPABASE_ANON_KEY
SUPABASE_SERVICE_KEY=$SUPABASE_SERVICE_KEY
FLASK_SECRET_KEY=$FLASK_SECRET_KEY
FLASK_ENV=production
FLASK_DEBUG=0
REDIS_URL=redis://localhost:6379/0
LOG_LEVEL=INFO
EOF
chmod 600 .env

# 5. Virtual env
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

# 6. Build Tailwind CSS
npm install --silent
npm run css:build

# 7. Systemd service
sudo cp deploy/scangrade.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable scangrade --now

# 8. NGINX
sudo cp deploy/nginx.conf /etc/nginx/sites-available/scangrade
sudo ln -sf /etc/nginx/sites-available/scangrade /etc/nginx/sites-enabled/
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t && sudo systemctl restart nginx

echo ""
echo "============================================"
echo "  ✅ ScanGrade deployed!"
echo "  Akses: http://$(curl -s ifconfig.me)"
echo "============================================"
