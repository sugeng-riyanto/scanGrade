import os

bind = f"0.0.0.0:{os.getenv('PORT', '8000')}"
workers = 4
worker_class = "sync"
timeout = 120
accesslog = "-"
errorlog = "-"
