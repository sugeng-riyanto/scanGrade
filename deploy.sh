#!/usr/bin/env bash
set -euo pipefail

cd /opt/scangrade
git pull
source .venv/bin/activate
pip install -r requirements.txt
sudo systemctl restart scangrade
