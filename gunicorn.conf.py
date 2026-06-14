import os

bind = f"0.0.0.0:{os.getenv('PORT', '8000')}"
workers = 8
worker_class = "sync"
timeout = 120
max_requests = 1000
max_requests_jitter = 200
preload_app = True
keepalive = 5
accesslog = "-"
errorlog = "-"
