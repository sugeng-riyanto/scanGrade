import base64
import functools
import hashlib
import json
import time
from flask import g, request, jsonify, current_app, redirect, flash
from supabase import Client

# Role-based session timeout (OWASP + UU PDP standard)
SESSION_TIMEOUTS = {
    "super_admin":    {"idle_minutes": 15,  "absolute_hours": 4},
    "admin_sekolah":  {"idle_minutes": 30,  "absolute_hours": 8},
    "guru":           {"idle_minutes": 60,  "absolute_hours": 12},
    "murid":          {"idle_minutes": 120, "absolute_hours": 24},
}
DEFAULT_SESSION_TIMEOUT = {"idle_minutes": 30, "absolute_hours": 8}

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


# ── Session resolution cache ─────────────────────────────────────
# Validating a token and reading the profile costs two Supabase round-trips
# (~290 ms measured from this deployment) and used to be paid on *every*
# authenticated request. Caching the resolved session takes that off the hot
# path. The TTL is deliberately short so a role or status change still
# propagates within seconds, and logout invalidates explicitly so a revoked
# token can't ride the cache. Set AUTH_SESSION_CACHE_TTL=0 to disable.
AUTH_SESSION_TTL_DEFAULT = 30


def _session_ttl():
    try:
        return int(current_app.config.get("AUTH_SESSION_CACHE_TTL", AUTH_SESSION_TTL_DEFAULT) or 0)
    except Exception:
        return AUTH_SESSION_TTL_DEFAULT


def _session_key(token):
    return "authsess:" + hashlib.sha256(token.encode("utf-8")).hexdigest()


def _jwt_expired(token):
    """Read `exp` from the JWT payload.

    The signature was already verified by Supabase when the session was cached,
    so this local read is only used to stop an expired token from riding the
    cache for the remainder of its TTL.
    """
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        exp = json.loads(base64.urlsafe_b64decode(payload).decode("utf-8")).get("exp")
        return bool(exp) and time.time() >= float(exp)
    except Exception:
        return False


def _fetch_session(token):
    """The two Supabase round-trips: validate the token, then read the profile."""
    user = get_auth_client().auth.get_user(token)
    meta = user.user.user_metadata or {}
    try:
        pd = (
            get_supabase()
            .table("profiles")
            .select("role, school_id, status")
            .eq("id", user.user.id)
            .single()
            .execute()
            .data
            or {}
        )
    except Exception:
        pd = {}

    school_id = pd.get("school_id") or meta.get("school_id")
    if school_id == "None":
        school_id = None
    return {
        "user_id": user.user.id,
        "email": user.user.email,
        "name": meta.get("full_name", ""),
        "role": _normalize_role(pd.get("role") or meta.get("role", "murid")),
        "school_id": school_id,
        "status": pd.get("status", "active"),
    }


def _session_for(token):
    """Resolve a token to session data, via the cache when possible."""
    if _jwt_expired(token):
        # Same outcome as a rejected token: let the caller try a refresh.
        raise ValueError("access token expired")

    from app.utils.kv_cache import cache_get, cache_set

    cached = cache_get(_session_key(token))
    if cached:
        return cached

    data = _fetch_session(token)
    cache_set(_session_key(token), data, _session_ttl())
    return data


def _apply_session(data, token):
    g.user_id = data["user_id"]
    g.user_token = token
    g.user_email = data.get("email", "")
    g.user_name = data.get("name", "")
    g.user_role = data.get("role", "murid")
    g.user_school_id = data.get("school_id")
    g.user_status = data.get("status", "active")


def invalidate_session(token):
    """Drop a cached session so the token stops working immediately (logout)."""
    if not token:
        return
    from app.utils.kv_cache import cache_delete
    cache_delete(_session_key(token))


def login_required(f):
    @functools.wraps(f)
    def wrapper(*args, **kwargs):
        token = _extract_token()
        if not token:
            return _unauthorized()
        try:
            _apply_session(_session_for(token), token)

            # Block pending users from accessing protected routes
            if g.get("user_status") == "pending":
                return redirect("/auth/activate?email={}&pending=1".format(g.user_email))

            # Session timeout check (idle + absolute)
            role = g.get("user_role")
            to = SESSION_TIMEOUTS.get(role, DEFAULT_SESSION_TIMEOUT)
            now = time.time()
            try:
                last_act = float(request.cookies.get("last_activity", "0"))
                if last_act > 0 and now - last_act > to["idle_minutes"] * 60:
                    return _unauthorized()
            except Exception:
                pass
            try:
                sess_start = float(request.cookies.get("session_start", "0"))
                if sess_start > 0 and now - sess_start > to["absolute_hours"] * 3600:
                    return _unauthorized()
            except Exception:
                pass
        except Exception:
            # Token expired — try refresh using refresh_token cookie
            token = _refresh_token()
            if not token:
                return _unauthorized()
            g.user_token = token
        return f(*args, **kwargs)

    return wrapper


def _refresh_token():
    """Try to refresh Supabase session. Returns new access_token or None."""
    rt = request.cookies.get("refresh_token")
    if not rt:
        return None
    try:
        supabase = get_auth_client()
        res = supabase.auth.refresh_session(rt)
        if not res or not res.session or not res.session.access_token:
            return None
        token = res.session.access_token
        g.user_id = res.user.id
        g.user_email = res.user.email
        g.user_name = res.user.user_metadata.get("full_name", "")
        db = get_supabase()
        try:
            pd = db.table("profiles").select("*").eq("id", g.user_id).single().execute().data or {}
        except Exception:
            pd = {}
        if pd:
            g.user_role = _normalize_role(pd.get("role", "murid"))
            g.user_school_id = pd.get("school_id") or res.user.user_metadata.get("school_id")
            if g.user_school_id == "None": g.user_school_id = None
            g.user_status = pd.get("status", "active")
        else:
            meta = res.user.user_metadata
            g.user_role = _normalize_role(meta.get("role", "murid"))
            g.user_school_id = meta.get("school_id")
            if g.user_school_id == "None": g.user_school_id = None
            g.user_status = "active"
        g._new_access_token = token
        return token
    except Exception:
        return None


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
                    "admin_sekolah": "/admin-sekolah/dashboard",
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
        return jsonify({"error": "Silakan login terlebih dahulu"}), 401
    flash("Silakan login terlebih dahulu", "error")
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
        if request.method in ("GET", "HEAD", "OPTIONS"):
            return f(*args, **kwargs)
        allowed, msg = check_subscription_write()
        if not allowed:
            if _wants_json():
                return jsonify({"error": msg}), 403
            flash(msg, "error")
            ref = request.referrer or "/"
            return redirect(ref)
        return f(*args, **kwargs)
    return wrapper
