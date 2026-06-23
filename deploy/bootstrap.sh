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
cat > .env << 'EOF'
SUPABASE_URL=https://roshkbzgfzpfedowozfo.supabase.co
SUPABASE_ANON_KEY=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InJvc2hrYnpnZnpwZmVkb3dvemZvIiwicm9sZSI6ImFub24iLCJpYXQiOjE3Nzk0Mjg5MDYsImV4cCI6MjA5NTAwNDkwNn0.PChroG0l5LQ26kSgRXiL8_lHVT-tww0Rs24ucs4dZD0
SUPABASE_SERVICE_KEY=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InJvc2hrYnpnZnpwZmVkb3dvemZvIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc3OTQyODkwNiwiZXhwIjoyMDk1MDA0OTA2fQ.rKZCSie8a0WqxKxk3GrmRmRWteZbqUVjKq97BhXpNcA
FLASK_SECRET_KEY=scan-grade-prod-2024-32char-secret-key!!
FLASK_ENV=production
FLASK_DEBUG=0
REDIS_URL=redis://localhost:6379/0
LOG_LEVEL=INFO
EOF

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
