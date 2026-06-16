#!/bin/bash
# ============================================================
# ScanGrade — Toggle LOAD_TEST mode for load testing
# ============================================================
# Usage:
#   bash deploy/set-load-test.sh on   → Enable LOAD_TEST + restart
#   bash deploy/set-load-test.sh off  → Disable LOAD_TEST + restart
# ============================================================

SERVICE_FILE="/etc/systemd/system/scangrade.service"

case "${1:-}" in
  on)
    echo "=== Enabling LOAD_TEST mode ==="
    # Remove any existing LOAD_TEST line
    sudo sed -i '/^Environment=LOAD_TEST/d' "$SERVICE_FILE"
    # Add after [Service]
    sudo sed -i '/^\[Service\]/a Environment=LOAD_TEST=true' "$SERVICE_FILE"
    sudo systemctl daemon-reload
    sudo systemctl restart scangrade
    echo "✅ LOAD_TEST=true — scangrade restarted"
    ;;
  off)
    echo "=== Disabling LOAD_TEST mode ==="
    sudo sed -i '/^Environment=LOAD_TEST/d' "$SERVICE_FILE"
    sudo systemctl daemon-reload
    sudo systemctl restart scangrade
    echo "✅ LOAD_TEST=disabled — scangrade restarted"
    ;;
  *)
    echo "Usage: bash deploy/set-load-test.sh on|off"
    echo ""
    CURRENT=$(sudo grep LOAD_TEST "$SERVICE_FILE" 2>/dev/null || echo "not set")
    echo "Current: $CURRENT"
    exit 1
    ;;
esac
