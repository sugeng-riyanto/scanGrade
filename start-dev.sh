#!/usr/bin/env bash
set -euo pipefail

if [ ! -f .env ]; then
  echo "Copy .env.example to .env first!"
  exit 1
fi

set -a; source .env; set +a

echo "Starting ngrok tunnel..."
ngrok http 5000 --log=stdout > /tmp/ngrok.log 2>&1 &
NGROK_PID=$!
sleep 3

NGROK_URL=$(curl -s http://localhost:4040/api/tunnels | python3 -c "import sys,json; print(json.load(sys.stdin)['tunnels'][0]['public_url'])")
NGROK_DOMAIN=$(echo "$NGROK_URL" | sed 's|https://||; s|http://||')
echo "NGROK_DOMAIN=$NGROK_DOMAIN" > .env.ngrok
# Source ngrok domain into current env so Flask picks it up
export NGROK_DOMAIN

echo "Starting Flask..."
gunicorn wsgi:app --bind 0.0.0.0:5000 --reload
