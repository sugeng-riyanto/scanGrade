"""Gunicorn configuration for ScanGrade — 1000 concurrent users.

Workers = (2 × CPU cores) + 1
For a 4-core VPS: 4 workers = ~4000 worker threads total (with gevent)
Each gevent worker handles ~250 concurrent greenlets (connections).
"""
import os
import multiprocessing

# ─── Server Socket ────────────────────────────────────────────────
bind = "127.0.0.1:8000"  # Nginx proxies to this
backlog = 2048

# ─── Workers ──────────────────────────────────────────────────────
# gevent handles 1000+ concurrent connections per worker
workers = min(multiprocessing.cpu_count() * 2 + 1, 8)  # cap at 8
worker_class = "gevent"
worker_connections = 1000  # max concurrent per worker
threads = 1  # gevent is single-threaded (greenlets replace threads)

# ─── Timeouts ─────────────────────────────────────────────────────
timeout = 120          # worker killed if request takes > 2 min
graceful_timeout = 30
keepalive = 5          # keep-alive for HTTP keep-alive connections
worker_tmp_dir = "/dev/shm"  # use RAM for worker heartbeat (faster than disk)

# ─── Memory ───────────────────────────────────────────────────────
max_requests = 2000           # recycle worker after 2000 requests (prevent leaks)
max_requests_jitter = 200     # ±200 jitter to avoid thundering herd
preload_app = True            # load app once, fork workers (saves RAM)

# ─── Logging ──────────────────────────────────────────────────────
accesslog = "/var/log/scangrade/access.log"
errorlog = "/var/log/scangrade/error.log"
loglevel = "info"
access_log_format = '%(h)s %(l)s %(u)s %(t)s "%(r)s" %(s)s %(b)s "%(f)s" "%(a)s" %(D)s'

# ─── Security ─────────────────────────────────────────────────────
limit_request_line = 8190
limit_request_fields = 100
limit_request_field_size = 8190

# ─── Hooks ────────────────────────────────────────────────────────
def on_starting(server):
    """Create log directory and set worker count for health endpoint."""
    os.makedirs("/var/log/scangrade", exist_ok=True)
    os.environ["GUNICORN_WORKERS"] = str(workers)
    server.log.info("Gunicorn starting with %d workers (gevent, %d connections each)",
                    workers, worker_connections)

def post_fork(server, worker):
    """Log worker spawn."""
    server.log.info("Worker spawned (pid: %s)", worker.pid)

def pre_exec(server):
    """Master process — log config."""
    server.log.info("Master process (pid: %s) binding to %s", os.getpid(), bind)
