import time
from collections import defaultdict
from flask import request, jsonify, current_app

_limits = {}

DEFAULT_LIMITS = {
    "default": (60, 60),        # 60 requests per minute
    "auth": (30, 60),           # 30 auth requests per minute
    "api": (30, 60),            # 30 API requests per minute
    "register": (3, 3600),      # 3 registrations per hour
    "upload": (10, 300),        # 10 uploads per 5 minutes
    "reset_password": (3, 300), # 3 reset attempts per 5 minutes
}

_exempt_paths = {"/health", "/static/"}


def get_rate_limiter(app):
    @app.before_request
    def check_rate_limit():
        path = request.path
        for ex in _exempt_paths:
            if path.startswith(ex):
                return None

        ip = request.remote_addr or "unknown"
        now = time.time()

        # Determine group
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
        key = f"{group}:{ip}"
        entry = _limits.get(key)

        if entry is None or now - entry["start"] > window:
            _limits[key] = {"start": now, "count": 1}
            return None

        entry["count"] += 1
        if entry["count"] > max_req:
            retry_after = int(window - (now - entry["start"]))
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
