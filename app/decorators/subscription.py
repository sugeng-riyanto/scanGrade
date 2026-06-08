"""Subscription feature enforcement decorator."""

import functools
from flask import g, request, jsonify, flash, redirect
from app.services.subscription_service import check_feature_limit


def _wants_json():
    accept = request.headers.get("Accept", "")
    return "application/json" in accept or request.path.startswith("/api/")


def require_subscription(feature):
    """Decorator that checks if the user's school plan allows the given feature.

    Usage:
        @require_subscription("create_exam")
        def create_exam():
            ...

    Features: "create_exam", "ai_grading", "add_student"
    """
    def decorator(f):
        @functools.wraps(f)
        def wrapper(*args, **kwargs):
            if request.method in ("GET", "HEAD", "OPTIONS"):
                return f(*args, **kwargs)

            school_id = g.get("user_school_id")
            if not school_id:
                return f(*args, **kwargs)

            allowed, message = check_feature_limit(school_id, feature)
            if not allowed:
                if _wants_json():
                    return jsonify({"success": False, "error": "FEATURE_LIMIT", "message": message}), 403
                flash(message, "error")
                return redirect(request.referrer or "/")

            return f(*args, **kwargs)
        return wrapper
    return decorator
