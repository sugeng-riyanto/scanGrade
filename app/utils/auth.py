import functools
from flask import g, request, jsonify, current_app, redirect
from supabase import Client


def get_supabase() -> Client:
    return current_app.extensions["supabase"]


def _wants_json():
    accept = request.headers.get("Accept", "")
    return "application/json" in accept or request.path.startswith("/api/")


def login_required(f):
    @functools.wraps(f)
    def wrapper(*args, **kwargs):
        token = _extract_token()
        if not token:
            return _unauthorized()
        try:
            supabase = get_supabase()
            user = supabase.auth.get_user(token)
            g.user_id = user.user.id
            g.user_role = user.user.user_metadata.get("role", "student")
            g.user_token = token
        except Exception:
            return _unauthorized()
        return f(*args, **kwargs)
    return wrapper


def teacher_required(f):
    @functools.wraps(f)
    @login_required
    def wrapper(*args, **kwargs):
        if g.get("user_role") != "teacher":
            if _wants_json():
                return jsonify({"error": "Forbidden"}), 403
            return redirect("/auth/login")
        return f(*args, **kwargs)
    return wrapper


def admin_required(f):
    @functools.wraps(f)
    @login_required
    def wrapper(*args, **kwargs):
        if g.get("user_role") != "admin":
            if _wants_json():
                return jsonify({"error": "Forbidden"}), 403
            return redirect("/auth/login")
        return f(*args, **kwargs)
    return wrapper


def teacher_or_admin_required(f):
    """Decorator that allows both teachers and admins to access the route."""
    @functools.wraps(f)
    @login_required
    def wrapper(*args, **kwargs):
        if g.get("user_role") not in ("teacher", "admin"):
            if _wants_json():
                return jsonify({"error": "Forbidden"}), 403
            return redirect("/auth/login")
        return f(*args, **kwargs)
    return wrapper


def _unauthorized():
    if _wants_json():
        return jsonify({"error": "Unauthorized"}), 401
    return redirect("/auth/login")


def _extract_token():
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        return auth_header[7:]
    return request.cookies.get("access_token")
