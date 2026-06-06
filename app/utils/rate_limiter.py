"""Rate limiter with optional Redis backend for multi-worker support."""
import time
from collections import defaultdict
from flask import request, jsonify, current_app

_limits = {}

DEFAULT_LIMITS = {
    "default": (60, 60),
    "auth": (30, 60),
    "api": (30, 60),
    "register": (3, 3600),
    "upload": (10, 300),
    "reset_password": (3, 300),
}

_exempt_paths = {"/health", "/static/"}


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

        ip = request.remote_addr or "unknown"
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
        key = f"rl:{group}:{ip}" if redis else f"{group}:{ip}"
        allowed, retry_after = _check_limit(key, max_req, window, redis)

        if not allowed:
            if request.is_json or request.headers.get("Accept", "").startswith("application/json"):
                return jsonify({
                    "error": "Too many requests",
                    "retry_after": retry_after,
                    "message": f"Terlalu banyak permintaan. Coba lagi dalam {retry_after} detik.",
                }), 429
            return jsonify({
                "error": "Too many requests",
                "retry_after": retry_after,
                "message": "Terlalu banyak permintaan. Silakan coba lagi nanti.",
            }), 429
        return None
