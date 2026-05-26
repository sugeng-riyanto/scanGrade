import functools
from flask import g, request, jsonify, current_app
from supabase import Client


def get_supabase() -> Client:
    return current_app.extensions["supabase"]


def login_required(f):
    @functools.wraps(f)
    def wrapper(*args, **kwargs):
        token = _extract_token()
        if not token:
            return jsonify({"error": "Unauthorized"}), 401
        try:
            supabase = get_supabase()
            user = supabase.auth.get_user(token)
            g.user_id = user.user.id
            g.user_role = user.user.user_metadata.get("role", "student")
            g.user_token = token
        except Exception:
            return jsonify({"error": "Invalid token"}), 401
        return f(*args, **kwargs)
    return wrapper


def teacher_required(f):
    @functools.wraps(f)
    @login_required
    def wrapper(*args, **kwargs):
        if g.get("user_role") != "teacher":
            return jsonify({"error": "Forbidden"}), 403
        return f(*args, **kwargs)
    return wrapper


def admin_required(f):
    @functools.wraps(f)
    @login_required
    def wrapper(*args, **kwargs):
        if g.get("user_role") != "admin":
            return jsonify({"error": "Forbidden"}), 403
        return f(*args, **kwargs)
    return wrapper


def _extract_token():
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        return auth_header[7:]
    return request.cookies.get("access_token")
