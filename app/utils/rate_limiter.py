"""Rate limiter with Redis backend for multi-worker support.

Architecture:
- Redis is the PRIMARY backend — shared across all gunicorn workers
- In-memory fallback only when Redis is unavailable (dev/single-worker)
- Connection pooling via redis.ConnectionPool (reuses TCP connections)
- Lazy init: Redis tried on first request, not at app startup
"""
import os
import time
import threading
import logging
from flask import g, request, jsonify, current_app

logger = logging.getLogger(__name__)

# Flask-Limiter instance — initialized in create_app(), imported by routes
limiter = None

# ── Redis connection pool (singleton, thread-safe) ──────────────
_redis_pool = None
_redis_lock = threading.Lock()
_redis_init_attempted = False
_redis_available = False


def _get_redis_pool():
    """Get or create a Redis connection pool (singleton per process)."""
    global _redis_pool, _redis_init_attempted, _redis_available

    if _redis_init_attempted and not _redis_available:
        return None

    if _redis_pool is not None:
        return _redis_pool

    with _redis_lock:
        if _redis_pool is not None:
            return _redis_pool

        _redis_init_attempted = True
        try:
            from redis import Redis
            from redis.connection import ConnectionPool

            url = os.environ.get("REDIS_URL", "") or ""
            if not url:
                try:
                    url = current_app.config.get("REDIS_URL", "")
                except RuntimeError:
                    pass

            if not url:
                logger.warning("REDIS_URL not configured — using in-memory rate limiting (not shared across workers)")
                return None

            _redis_pool = ConnectionPool.from_url(
                url,
                max_connections=20,
                decode_responses=True,
                socket_connect_timeout=3,
                socket_timeout=5,
                retry_on_timeout=True,
            )
            # Quick connectivity check
            test_conn = Redis(connection_pool=_redis_pool)
            test_conn.ping()
            _redis_available = True
            logger.info("Redis rate limiter connected: %s", url.split("@")[-1] if "@" in url else url)
            return _redis_pool
        except Exception as e:
            logger.warning("Redis unavailable (%s) — falling back to in-memory rate limiting", e)
            _redis_pool = None
            _redis_available = False
            return None


def _get_redis_conn():
    """Get a Redis connection from the pool."""
    pool = _get_redis_pool()
    if pool is None:
        return None
    try:
        from redis import Redis
        conn = Redis(connection_pool=pool)
        conn.ping()
        return conn
    except Exception:
        # Pool might be stale — reset and try on next request
        global _redis_pool, _redis_available
        _redis_pool = None
        _redis_available = False
        return None


# ── In-memory fallback (single-worker only) ─────────────────────
_limits = {}
_limits_lock = threading.Lock()

DEFAULT_LIMITS = {
    "default": (120, 60),
    "auth": (30, 60),
    "api": (120, 60),
    "api_student": (300, 60),
    "upload": (10, 300),
}

# ── Per-ACCOUNT throttles ─────────────────────────────────────────
# Keyed on the account the caller names (email / NISN / NPSN) instead of the
# client IP. A school reaches the internet through ONE NAT'd public IP, so an
# IP-keyed limit lets a few users lock out everybody else on that network — the
# exact opposite of what these endpoints are for.
#
# Each scope also carries a deliberately loose per-IP backstop. That one only
# exists so a single host can't cycle through thousands of accounts; it is
# wide enough that a whole class sharing an IP never trips it.
ACCOUNT_LIMITS = {
    "register": (5, 3600),        # 5 signups per school (NPSN) per hour
    "forgot_password": (3, 900),  # 3 reset codes per account per 15 min
    "verify_code": (10, 900),     # 10 code attempts per account per 15 min
    "login_failed": (8, 900),     # 8 failed logins per account per 15 min
}

ACCOUNT_IP_BACKSTOP = {
    "register": (60, 3600),
    "forgot_password": (60, 900),
    "verify_code": (60, 900),
    # Wide on purpose: 330 users behind one school IP must be able to sign in,
    # so a hundred failed attempts is a signal, not a school-wide lockout.
    "login_failed": (300, 900),
}

_exempt_paths = {"/health", "/static/"}
_exact_exempt = {"/", "/pricing", "/demo", "/auth/login-user", "/auth/login"}
# Routes that throttle themselves inside the handler (via check_account_limit).
# The hook must skip them or the strict per-IP group is applied on top — which
# is what locked a whole school out of a single NAT'd address.
_endpoint_self_limited = {
    "/api/student/sync-draft",
    "/api/violation/log",
    "/api/student/force-submit",
    "/auth/register",
    "/auth/forgot-password",
    "/auth/verify-reset-code",
}


def _check_limit_redis(conn, key, max_req, window):
    """Sliding window rate limit via Redis sorted sets."""
    try:
        now = time.time()
        pipe = conn.pipeline()
        pipe.zremrangebyscore(key, 0, now - window)
        pipe.zadd(key, {f"{now}:{os.getpid()}": now})
        pipe.zcard(key)
        pipe.expire(key, int(window) + 1)
        _, _, count, _ = pipe.execute()

        if count > max_req:
            oldest = conn.zrange(key, 0, 0, withscores=True)
            retry = int(window - (now - oldest[0][1])) if oldest else 1
            return False, max(1, retry)
        return True, 0
    except Exception as e:
        logger.debug("Redis rate limit check failed: %s — allowing request", e)
        return True, 0  # Fail open — don't block users if Redis hiccups


def _check_limit_memory(key, max_req, window):
    """In-memory sliding window — only works in single-worker mode."""
    now = time.time()
    with _limits_lock:
        entry = _limits.get(key)
        if entry is None or now - entry["start"] > window:
            _limits[key] = {"start": now, "count": 1}
            return True, 0
        entry["count"] += 1
        if entry["count"] > max_req:
            retry_after = int(window - (now - entry["start"]))
            return False, max(1, retry_after)
        return True, 0


def _count(conn, key, max_req, window):
    """One rate-limit check against Redis, or the in-memory fallback."""
    if conn:
        return _check_limit_redis(conn, key, max_req, window)
    return _check_limit_memory(key, max_req, window)


def check_account_limit(scope, account, ip=None):
    """Throttle a *named account* rather than the caller's IP.

    Returns ``(allowed, retry_after_seconds)``. Calls fail open when Redis is
    unavailable, matching the rest of this module, so a cache outage can never
    lock users out.

    ``scope`` selects the thresholds from ACCOUNT_LIMITS; ``account`` is the
    identifier the caller supplied (email / NISN / NPSN). An empty account is
    allowed through — the handler validates the input and reports it.
    """
    if os.environ.get("LOAD_TEST") == "true":
        return True, 0

    account = (account or "").strip().lower()
    if not account:
        return True, 0

    conn = _get_redis_conn()

    acct_max, acct_window = ACCOUNT_LIMITS.get(scope, (10, 600))
    allowed, retry = _count(conn, f"rl:acct:{scope}:{account}", acct_max, acct_window)
    if not allowed:
        return False, retry

    if ip is None:
        try:
            ip = request.remote_addr or ""
        except RuntimeError:  # no request context (e.g. a background job)
            ip = ""
    if ip:
        ip_max, ip_window = ACCOUNT_IP_BACKSTOP.get(scope, (60, 600))
        allowed, retry = _count(conn, f"rl:acctip:{scope}:{ip}", ip_max, ip_window)
        if not allowed:
            return False, retry

    return True, 0


def rate_limit_message(retry_after, what="permintaan"):
    """User-facing message mirroring the hook's wording, in minutes."""
    minutes = max(1, (int(retry_after) + 59) // 60)
    return f"Terlalu banyak {what}. Coba lagi dalam {minutes} menit."


def _cleanup_memory_limits():
    """Periodic cleanup of stale in-memory entries."""
    now = time.time()
    with _limits_lock:
        stale = [k for k, v in _limits.items() if now - v["start"] > 3600]
        for k in stale:
            del _limits[k]


_last_cleanup = time.time()


def _reset_redis():
    """Force Redis reconnection on next request."""
    global _redis_pool, _redis_available, _redis_init_attempted
    _redis_pool = None
    _redis_available = False
    _redis_init_attempted = False


# Expose for health checks and tests
def get_redis_status():
    """Return Redis connection status for monitoring."""
    conn = _get_redis_conn()
    if conn:
        try:
            info = conn.info("server")
            return {"connected": True, "version": info.get("redis_version", "unknown")}
        except Exception:
            return {"connected": False, "error": "ping_failed"}
    return {"connected": False, "error": "not_configured"}


def get_rate_limiter(app):
    """Register the before_request rate limit hook."""
    global _last_cleanup

    @app.before_request
    def check_rate_limit():
        global _last_cleanup

        # LOAD_TEST mode — bypass all rate limiting
        if os.environ.get("LOAD_TEST") == "true":
            return None

        path = request.path

        # Exempt paths
        for ex in _exempt_paths:
            if path.startswith(ex):
                return None
        if path in _exact_exempt:
            return None
        # Endpoints with their own per-user rate limiter
        for ex in _endpoint_self_limited:
            if path.startswith(ex):
                return None

        # Determine rate limit group
        if path.startswith(("/auth/", "/login", "/login-user")):
            group = "auth"
        elif path.startswith(("/api/",)):
            group = "api"
        elif path.endswith(("/upload-pdf", "/upload")):
            group = "upload"
        else:
            group = "default"

        max_req, window = DEFAULT_LIMITS.get(group, DEFAULT_LIMITS["default"])

        # Identity: user_id for authenticated (school-NAT friendly), IP for anon
        ip = request.remote_addr or "unknown"
        user_id = g.get("user_id") if hasattr(g, "user_id") else None
        identity = user_id or ip

        # Periodic memory cleanup (every 10 min)
        now = time.time()
        if now - _last_cleanup > 600:
            _last_cleanup = now
            _cleanup_memory_limits()

        # Try Redis first (shared across workers), fallback to in-memory
        conn = _get_redis_conn()
        if conn:
            key = f"rl:{group}:{identity}"
            allowed, retry_after = _check_limit_redis(conn, key, max_req, window)
        else:
            key = f"{group}:{identity}"
            allowed, retry_after = _check_limit_memory(key, max_req, window)

        if not allowed:
            if request.is_json or request.headers.get("Accept", "").startswith("application/json"):
                return jsonify({"error": "Too many requests", "retry_after": retry_after}), 429

            from flask import render_template_string
            msg = f"Terlalu banyak permintaan. Silakan coba lagi dalam {retry_after} detik."
            html = f'''<!DOCTYPE html><html><head><meta charset="utf-8"><title>Rate Limited</title>
<style>body{{font-family:sans-serif;display:flex;align-items:center;justify-content:center;min-height:100vh;background:#f8fafc;}}
.card{{max-width:400px;text-align:center;padding:40px;background:white;border-radius:16px;box-shadow:0 4px 24px rgba(0,0,0,0.06);}}
h1{{font-size:48px;color:#ef4444;margin:0;}}p{{color:#64748b;}}button{{margin-top:16px;padding:10px 24px;background:#3b82f6;color:white;border:none;border-radius:12px;font-weight:bold;cursor:pointer;}}
button:hover{{background:#2563eb;}}</style></head><body>
<div class="card"><h1>429</h1><p>{msg}</p>
<button onclick="location.reload()">Coba Lagi</button></div></body></html>'''
            return render_template_string(html), 429

        return None
