# Production Deployment Guide

## Prerequisites (Hostinger VPS / Any Linux)
- Ubuntu 22.04+ / Debian 12+
- Python 3.12+
- Git
- Systemd
- Nginx (recommended)

## Step-by-Step

### 1. Clone & Setup
```bash
sudo mkdir -p /opt/scangrade
sudo chown $USER:$USER /opt/scangrade
git clone https://github.com/sugeng-riyanto/scanGrade.git /opt/scangrade
cd /opt/scangrade

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure Environment
```bash
cp .env.example .env
nano .env
```

Required variables:
```
FLASK_SECRET_KEY=<random-64-char-string>
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_SERVICE_KEY=eyJ... (service_role key)
SUPABASE_ANON_KEY=eyJ... (anon key)
SENTRY_DSN=https://...@...ingest.sentry.io/... (optional)
```

### 3. Database Migration
Run these SQL files in Supabase SQL Editor:
1. `_COMPLETE_SETUP.sql`
2. `migrations/001_enable_rls_and_policies.sql`
3. `migrations/20260608_fix_rls_policies.sql`
4. `migrations/20260608_usage_tracking.sql`

### 4. Systemd Service
```bash
sudo cp deploy/scangrade.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable scangrade
sudo systemctl start scangrade
```

Verify: `sudo systemctl status scangrade` and `curl http://localhost:8000/health`

### 5. Nginx Reverse Proxy (Optional)
```nginx
server {
    listen 80;
    server_name scangrade.io;
    return 301 https://$server_name$request_uri;
}

server {
    listen 443 ssl;
    server_name scangrade.io;

    ssl_certificate /etc/letsencrypt/live/scangrade.io/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/scangrade.io/privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    location /static/ {
        alias /opt/scangrade/app/static/;
        expires 30d;
    }
}
```

### 6. Deploy Script
```bash
sudo ./deploy/deploy.sh
```

This pulls latest code, installs deps, restarts service.

## Health Check
```bash
curl http://localhost:8000/health
# {"status":"ok","supabase":"connected",...}
```

## Rollback
```bash
cd /opt/scangrade
git log --oneline -5
git reset --hard <previous-commit-hash>
sudo systemctl restart scangrade
```
