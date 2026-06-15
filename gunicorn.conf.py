import os

bind = f"0.0.0.0:{os.getenv('PORT', '8000')}"
workers = 8
worker_class = "eventlet"
timeout = 120
max_requests = 5000
max_requests_jitter = 500
preload_app = True
keepalive = 5
accesslog = "-"
errorlog = "-"

# ── Production tuning for 600 concurrent users ──
worker_connections = 1000
graceful_timeout = 30
