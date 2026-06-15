#!/usr/bin/env bash
# ScanGrade — Check all configurations
# Usage: bash deploy/check-config.sh
set -euo pipefail

echo "┌─────────────────────────────────────────────┐"
echo "│  ScanGrade — Configuration Check            │"
echo "└─────────────────────────────────────────────┘"
echo ""

# 1. Midtrans
MIDTRANS_URL="https://scangrade.web.id/super-admin/midtrans"
echo "🔸 Midtrans:     $MIDTRANS_URL"
echo "   Login super admin → isi Merchant ID, Client Key, Server Key"
echo ""

# 2. WhatsApp
WA_URL="https://scangrade.web.id/super-admin/whatsapp-settings"
echo "🔸 WhatsApp:     $WA_URL"
echo "   Login super admin → isi nomor WA untuk notifikasi"
echo ""

# 3. Backup VPS
if [ -f /opt/scangrade/backups/scangrade-vps-*.tar.gz ] 2>/dev/null; then
    echo "✅ Backup VPS:    Ada"
else
    echo "🔸 Backup VPS:    Belum ada backup (coba: sudo bash /opt/scangrade/backup.sh)"
fi

# 4. Health check
if [ -f /var/log/scangrade-health.log ]; then
    echo "✅ Health Check:  Aktif (log: /var/log/scangrade-health.log)"
else
    echo "🔸 Health Check:  Belum aktif"
fi

# 5. Cron
echo "📋 Cron jobs:"
crontab -l 2>/dev/null | grep -E "health|backup" || echo "   (none)"
echo ""

echo "┌─────────────────────────────────────────────┐"
echo "│  Buka browser → https://scangrade.web.id     │"
echo "│  Login: superadmin@scan-grade.app            │"
echo "│  Password: superadmin123                     │"
echo "│  Isi Midtrans + WA settings                  │"
echo "└─────────────────────────────────────────────┘"
