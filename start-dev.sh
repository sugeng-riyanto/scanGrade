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
echo "NGROK_URL=$NGROK_URL" > .env.ngrok

echo "Starting Flask..."
gunicorn wsgi:app --bind 0.0.0.0:5000 --reload
