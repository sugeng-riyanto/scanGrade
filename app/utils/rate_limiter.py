"""Rate limiter with optional Redis backend for multi-worker support."""
import time
from collections import defaultdict
from flask import request, jsonify, current_app

# Flask-Limiter instance — initialized in create_app(), imported by routes
limiter = None

_limits = {}

DEFAULT_LIMITS = {
    "default": (120, 60),
    "auth": (30, 60),
    "api": (120, 60),
    "api_student": (300, 60),
    "register": (10, 600),
    "upload": (10, 300),
    "reset_password": (3, 300),
}

_exempt_paths = {"/health", "/static/"}
_endpoint_self_limited = {"/api/student/sync-draft", "/api/violation/log", "/api/student/force-submit"}


def _get_redis():
    """Get Redis client if configured."""
    try:
        from redis import Redis
        url = current_app.config.get("REDIS_URL", "")
        if url:
            return Redis.from_url(url)
    except Exception:
        pass
    return None


def _check_limit(key, max_req, window, redis=None):
    """Check rate limit, returns (allowed, retry_after)."""
    now = time.time()
    if redis:
        try:
            pipe = redis.pipeline()
            pipe.zremrangebyscore(key, 0, now - window)
            pipe.zadd(key, {str(now): now})
            pipe.zcard(key)
            pipe.expire(key, int(window) + 1)
            _, _, count, _ = pipe.execute()
            if count > max_req:
                oldest = redis.zrange(key, 0, 0, withscores=True)
                retry = int(window - (now - oldest[0][1])) if oldest else 1
                return False, max(1, retry)
            return True, 0
        except Exception:
            pass
    # In-memory fallback
    entry = _limits.get(key)
    if entry is None or now - entry["start"] > window:
        _limits[key] = {"start": now, "count": 1}
        return True, 0
    entry["count"] += 1
    if entry["count"] > max_req:
        retry_after = int(window - (now - entry["start"]))
        return False, max(1, retry_after)
    return True, 0


def get_rate_limiter(app):
    redis = _get_redis()

    @app.before_request
    def check_rate_limit():
        path = request.path
        for ex in _exempt_paths:
            if path.startswith(ex):
                return None
        # Endpoints with their own per-user rate limiter
        for ex in _endpoint_self_limited:
            if path.startswith(ex):
                return None

        ip = request.remote_addr or "unknown"
        # Use user_id as key for authenticated users (school NAT friendly)
        user_id = g.get("user_id") if hasattr(g, "user_id") else None
        identity = user_id or ip

        if path.startswith(("/auth/register",)):
            group = "register"
        elif path.startswith(("/auth/reset-password", "/auth/forgot-password")):
            group = "reset_password"
        elif path.startswith(("/auth/", "/login", "/login-user")):
            group = "auth"
        elif path.startswith(("/api/",)):
            group = "api"
        elif path.endswith(("/upload-pdf", "/upload")):
            group = "upload"
        else:
            group = "default"

        max_req, window = DEFAULT_LIMITS.get(group, DEFAULT_LIMITS["default"])
        key = f"rl:{group}:{identity}" if redis else f"{group}:{identity}"
        allowed, retry_after = _check_limit(key, max_req, window, redis)

        if not allowed:
            if request.is_json or request.headers.get("Accept", "").startswith("application/json"):
                return jsonify({"error": "Too many requests", "retry_after": retry_after}), 429
            # Return a simple HTML page for regular requests
            from flask import render_template_string
            from urllib.parse import quote
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
