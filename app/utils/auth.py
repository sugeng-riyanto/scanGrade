import functools
from flask import g, request, jsonify, current_app, redirect
from supabase import Client

# Role name mapping: old -> new (both accepted in decorators)
ROLE_ALIASES = {
    "admin": "super_admin",
    "teacher": "guru",
    "student": "murid",
}


def _normalize_role(role: str) -> str:
    """Map old role names to new hierarchy."""
    return ROLE_ALIASES.get(role, role)


def get_supabase() -> Client:
    return current_app.extensions["supabase"]


def get_auth_client() -> Client:
    return current_app.extensions["supabase_auth"]


def _wants_json():
    accept = request.headers.get("Accept", "")
    return "application/json" in accept or request.path.startswith("/api/")


def check_subscription_write(school_id=None):
    """Check if school subscription is active. Returns (allowed, error_msg).
    If not allowed, returns (False, 'message'). If allowed, returns (True, None)."""
    sid = school_id or g.get("user_school_id")
    if not sid:
        return True, None  # No school = super admin, always allowed

    # Import here to avoid circular imports
    from app.services.midtrans_service import is_school_active
    if is_school_active(sid):
        return True, None

    msg = "Masa langganan sekolah Anda telah berakhir. Fitur tulis dinonaktifkan. Hubungi Super Admin untuk perpanjangan."
    return False, msg


def login_required(f):
    @functools.wraps(f)
    def wrapper(*args, **kwargs):
        token = _extract_token()
        if not token:
            return _unauthorized()
        try:
            supabase = get_auth_client()
            user = supabase.auth.get_user(token)
            g.user_id = user.user.id
            g.user_token = token
            g.user_email = user.user.email
            g.user_name = user.user.user_metadata.get("full_name", "")

            # Fetch fresh profile data from profiles table
            db = get_supabase()
            try:
                profile = (
                    db.table("profiles")
                    .select("*")
                    .eq("id", g.user_id)
                    .single()
                    .execute()
                )
                pd = profile.data or {}
            except Exception:
                pd = {}

            if pd:
                g.user_role = _normalize_role(pd.get("role", "murid"))
                g.user_school_id = pd.get("school_id") or user.user.user_metadata.get("school_id")
                g.user_status = pd.get("status", "active")
            else:
                meta = user.user.user_metadata
                g.user_role = _normalize_role(meta.get("role", "murid"))
                g.user_school_id = meta.get("school_id")
                g.user_status = "active"

            # Block pending users from accessing protected routes
            if g.get("user_status") == "pending":
                return redirect("/auth/activate?email={}&pending=1".format(g.user_email))
        except Exception:
            return _unauthorized()
        return f(*args, **kwargs)

    return wrapper


def _check_roles(user_role: str, allowed_roles: tuple) -> bool:
    """Check user role against allowed roles (supports both old and new names)."""
    normalized_user = _normalize_role(user_role)
    return normalized_user in allowed_roles or any(
        _normalize_role(r) == normalized_user for r in allowed_roles
    )


def role_required(*roles):
    normalized_allowed = tuple(
        _normalize_role(r) for r in roles
    )

    def decorator(f):
        @functools.wraps(f)
        @login_required
        def wrapper(*args, **kwargs):
            if not _check_roles(g.get("user_role", ""), normalized_allowed):
                if _wants_json():
                    return jsonify({"error": "Forbidden"}), 403
                role_redirect = {
                    "super_admin": "/super-admin/dashboard",
                    "admin_sekolah": "/admin/dashboard",
                    "guru": "/teacher/dashboard",
                    "murid": "/student/dashboard",
                }
                return redirect(
                    role_redirect.get(g.get("user_role"), "/auth/login")
                )
            return f(*args, **kwargs)

        return wrapper

    return decorator


# ── Shortcuts (new role names) ──────────────────
def super_admin_required(f):
    return role_required("super_admin")(f)


def admin_sekolah_required(f):
    return role_required("admin_sekolah")(f)


def guru_required(f):
    return role_required("guru")(f)


def murid_required(f):
    return role_required("murid")(f)


# ── Shortcuts (backward-compatible with old names) ──
def teacher_required(f):
    return role_required("teacher", "guru")(f)


def admin_required(f):
    return role_required("admin", "super_admin", "admin_sekolah")(f)


def teacher_or_admin_required(f):
    return role_required("teacher", "admin", "guru", "admin_sekolah")(f)


def teacher_or_admin_sekolah_required(f):
    return role_required("guru", "admin_sekolah")(f)


def _unauthorized():
    if _wants_json():
        return jsonify({"error": "Unauthorized"}), 401
    return redirect("/auth/login")


def _extract_token():
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        return auth_header[7:]
    return request.cookies.get("access_token")


def subscription_write_required(f):
    """Decorator: blocks write access if school subscription is expired.
    Teachers/students/admins cannot create/update/delete data when expired.
    Read-only (GET) is always allowed."""
    @functools.wraps(f)
    @login_required
    def wrapper(*args, **kwargs):
        allowed, msg = check_subscription_write()
        if not allowed:
            if _wants_json():
                return jsonify({"error": msg}), 403
            flash(msg, "error")
            # Redirect back or to dashboard
            ref = request.referrer or "/"
            return redirect(ref)
        return f(*args, **kwargs)
    return wrapper
