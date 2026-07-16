#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

echo "┌─────────────────────────────────────────────┐"
echo "│  ScanGrade Deploy — $(date)  │"
echo "└─────────────────────────────────────────────┘"

if [ ! -d .git ]; then
    echo "❌ Bukan repo ScanGrade. Jalankan dari folder /opt/scangrade"
    exit 1
fi

echo "📥 Pull kode terbaru..."
git fetch origin
git reset --hard origin/main

echo "📦 Install dependencies..."
source .venv/bin/activate 2>/dev/null || python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt --quiet

echo "🔄 Restart service..."
sudo systemctl daemon-reload
sudo systemctl restart scangrade

sleep 2
if systemctl is-active --quiet scangrade; then
    echo "✅ ScanGrade running (PID: $(systemctl show -p MainPID scangrade | cut -d= -f2))"
else
    echo "❌ Gagal start — cek: journalctl -u scangrade -n 50"
    exit 1
fi

echo "✅ Done — $(date)"
