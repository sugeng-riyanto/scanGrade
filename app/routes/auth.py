from datetime import datetime, timezone
import logging
import os
import threading
import time
from flask import Blueprint, request, jsonify, g, session, render_template, redirect, url_for, make_response, current_app
from app.utils.auth import login_required, get_supabase, get_auth_client, invalidate_session, set_auth_cookie
from app.utils.helpers import row_or_none
from app.services.audit_service import log_activity
from app.utils.security import sanitize_input
from app.utils.rate_limiter import limiter, check_account_limit, rate_limit_message

# Module-level logger: the login helpers below can be exercised outside a request
# context, and a logging call must never be the thing that breaks a login.
logger = logging.getLogger(__name__)

# Safe rate-limit decorator — no-op if Flask-Limiter not available or LOAD_TEST mode
def _rate_limit(n):
    import os
    if os.environ.get("LOAD_TEST") == "true":
        return lambda f: f
    return limiter.limit(n) if limiter else (lambda f: f)

auth_bp = Blueprint("auth", __name__)


# ─── REGISTER (Admin sekolah) ────────────────────────

@auth_bp.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "GET":
        return render_template("auth/register.html")

    npsn = request.form.get("npsn", "").strip()
    school_name = request.form.get("school_name", "").strip()
    wa = request.form.get("wa", "").strip()
    email = request.form.get("email", "").strip().lower()
    password = request.form.get("password", "")
    position = request.form.get("position", "")

    if not all([npsn, school_name, wa, email, password, position]):
        return render_template("auth/register.html", error="Semua field wajib diisi")

    if len(password) < 6:
        return render_template("auth/register.html", error="Password minimal 6 karakter")

    # ── Consent check (UU PDP) ──
    consent = request.form.get("consent")
    if not consent:
        return render_template("auth/register.html", error="Anda harus menyetujui Syarat & Ketentuan dan Kebijakan Privasi")

    # Per-ACCOUNT throttle on the school (NPSN), not the client IP: several
    # schools can share one NAT'd address, so an IP-keyed limit would let one
    # school's retries block another's first attempt.
    allowed, retry = check_account_limit("register", npsn)
    if not allowed:
        return render_template("auth/register.html", error=rate_limit_message(retry, "percobaan pendaftaran"))

    supabase = get_supabase()

    # ── Check NPSN duplicate ──
    try:
        existing_npsn = supabase.table("school_registration_requests") \
            .select("id", "status", "school_name") \
            .eq("npsn", npsn) \
            .in_("status", ["pending", "approved"]) \
            .execute()
        if existing_npsn.data:
            dup = existing_npsn.data[0]
            if dup.get("status") == "pending":
                return render_template("auth/register.html", error="NPSN ini sudah memiliki permohonan pendaftaran yang menunggu verifikasi")
            return render_template("auth/register.html", error=f"NPSN ini sudah terdaftar untuk sekolah '{dup.get('school_name', '')}'. Hubungi Super Admin.")
    except Exception:
        pass
    try:
        existing_school = supabase.table("schools").select("id", "name").eq("npsn", npsn).execute()
        if existing_school.data:
            return render_template("auth/register.html", error=f"NPSN ini sudah terdaftar untuk sekolah '{existing_school.data[0].get('name', '')}'")
    except Exception:
        pass

    # ── Step 1: Create Auth user ──
    try:
        from supabase import Client
        admin_client: Client = current_app.extensions["supabase"]
        res = admin_client.auth.admin.create_user({
            "email": email,
            "password": password,
            "user_metadata": {"role": "admin_sekolah", "full_name": position},
            "email_confirm": True,
        })
        uid = res.user.id
    except Exception as e:
        err = str(e)
        if "already exists" in err.lower() or "duplicate" in err.lower():
            return render_template("auth/register.html", error="Email sudah terdaftar")
        current_app.logger.error(f"Register step 1 (create_user) failed: {err}")
        return render_template("auth/register.html", error=f"Gagal membuat akun: {err[:200]}")

    # ── Step 2: Create/update profile ──
    try:
        supabase.table("profiles").upsert({
            "id": uid,
            "full_name": position,
            "phone": wa,
            "role": "admin_sekolah",
        }).execute()
        # Try optional columns that may not exist yet
        try:
            supabase.table("profiles").update({"status": "pending"}).eq("id", uid).execute()
        except Exception:
            pass
        try:
            supabase.table("profiles").update({"consent_at": datetime.now(timezone.utc).isoformat()}).eq("id", uid).execute()
        except Exception:
            pass
    except Exception as e:
        current_app.logger.error(f"Register step 2 (profile) failed: {e}, cleaning up user {uid}")
        # Rollback: delete the auth user
        try:
            admin_client.auth.admin.delete_user(uid)
        except Exception:
            pass
        return render_template("auth/register.html", error="Gagal menyimpan data profil")

    # ── Step 3: Create registration request ──
    try:
        # Build payload with only columns we know exist
        req_data = {
            "school_name": school_name,
            "npsn": npsn,
            "requester_name": position,
            "requester_email": email,
            "requester_phone": wa,
            "requester_position": position,
            "status": "pending",
            "profile_id": uid,
        }
        # Try adding optional columns (they may not exist in older schema)
        for opt_col in ["is_activated"]:
            req_data[opt_col] = False
        supabase.table("school_registration_requests").insert(req_data).execute()
    except Exception as e:
        current_app.logger.error(f"Register step 3 (reg request) failed: {e}")
        # Don't rollback — profile already created
        return render_template("auth/register.html", error="Gagal membuat permohonan registrasi. Silakan hubungi admin.")

    try:
        log_activity("register", "user", uid, new_data={"email": email, "school_name": school_name, "role": "admin_sekolah", "status": "pending"})
    except Exception:
        pass

    return render_template("auth/register_success.html", email=email)


# ─── ACTIVATE ────────────────────────────────────────

@auth_bp.route("/activate", methods=["GET", "POST"])
def activate():
    if request.method == "GET":
        prefill_email = request.args.get("email", "")
        return render_template("auth/activate.html", email=prefill_email)

    email = request.form.get("email", "").strip().lower()
    code = request.form.get("code", "").strip().replace(" ", "").upper()

    if not email or not code:
        return render_template("auth/activate.html", error="Email dan kode aktivasi wajib diisi", email=email)

    if len(code) != 12 or not code.isalnum():
        return render_template("auth/activate.html", error="Kode aktivasi harus 12 karakter alfanumerik", email=email)

    supabase = get_supabase()

    try:
        now = "now()"
        from datetime import datetime, timezone

        req_res = supabase.table("school_registration_requests") \
            .select("*") \
            .eq("requester_email", email) \
            .eq("activation_code", code) \
            .eq("is_activated", False) \
            .eq("status", "approved") \
            .single() \
            .execute()

        req = req_res.data
        if not req:
            return render_template("auth/activate.html", error="Kode aktivasi tidak valid atau sudah digunakan", email=email)

        # Check expiry
        expires_at = req.get("expires_at")
        if expires_at:
            expires_dt = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
            if expires_dt < datetime.now(timezone.utc):
                return render_template("auth/activate.html", error="Kode aktivasi sudah kedaluwarsa. Silakan hubungi admin.", email=email)

        # Update request
        supabase.table("school_registration_requests") \
            .update({"is_activated": True}) \
            .eq("id", req["id"]) \
            .execute()

        # Update profile status to active
        profile_id = req.get("profile_id")
        if profile_id:
            supabase.table("profiles") \
                .update({"status": "active"}) \
                .eq("id", profile_id) \
                .execute()

        log_activity("activate", "user", profile_id, new_data={"status": "active", "code": code[:4] + "****"})
        return render_template("auth/activate_success.html")
    except Exception as e:
        current_app.logger.error(f"Activation error: {e}")
        return render_template("auth/activate.html", error="Kode aktivasi tidak valid atau sudah kedaluwarsa", email=email)


# ─── Login failure handling ──────────────────────────
# Supabase Auth rate-limits sign-in *per IP*, and every login this app performs
# is made from the server, so all schools share a single bucket. Measured on the
# VPS: 15/15 sequential sign-ins succeed, but a simultaneous burst of 15 drops
# to 5/15 with `AuthApiError: Request rate limit reached`.
#
# The handlers used to catch that in a bare `except` and render "Email atau
# password salah" — telling students their correct password was wrong, which
# pushed them to retry and deepened the very rate limit that caused it, while
# making the real failure impossible to diagnose.
_LOGIN_RETRY_BASE = 0.4  # seconds before retrying a rate-limited attempt

# Pace outbound sign-ins BELOW Supabase's refill rate instead of stampeding it.
#
# Supabase limits POST /auth/v1/token per IP with a token bucket whose BURST
# CAPACITY IS FIXED AT 30 — only the refill rate is configurable in the
# dashboard. All logins leave from this one server IP, so every school shares a
# single bucket. Measured on this project:
#
#   before raising the limit : 0.45 sign-ins/s -> 330 simultaneous logins needed
#                              ~10 minutes of refill; 286 of 330 failed
#   after setting it to 1000 : ~9.7/s, confirmed by driving the endpoint past the
#                              ceiling (215 accepted, 13 rejected at 10.2/s demand)
#
# So the useful lever is patience, not concurrency: a school-wide login is a
# queue. Pacing ourselves under the ceiling is what turns 286 failures into none.
# Raise LOGIN_SIGNIN_RATE after raising the Supabase limit again.
_LOGIN_SIGNIN_RATE = float(os.environ.get("LOGIN_SIGNIN_RATE", "8") or 8)  # per second
_LOGIN_WAIT_BUDGET = float(os.environ.get("LOGIN_WAIT_BUDGET", "60") or 60)  # seconds

# Pacing state. Under gunicorn's gevent worker, threading and time.sleep are
# monkey-patched, so waiting here yields to other greenlets instead of blocking
# the worker.
_pace_lock = threading.Lock()
_next_slot = [0.0]

# The slot queue is kept in Redis so the rate is AGGREGATE across gunicorn
# workers. Pacing in-process alone multiplies the rate by the worker count
# (3 workers x 8/s = 24/s), which is the very stampede this exists to prevent.
# GET and SET are atomic inside the script, so two workers can never claim the
# same slot; the key expires on its own so a stale slot can't stall a restart.
_PACE_LUA = """
local key = KEYS[1]
local now = tonumber(ARGV[1])
local interval = tonumber(ARGV[2])
local ttl = tonumber(ARGV[3])
local slot = tonumber(redis.call('GET', key))
if not slot or slot < now then slot = now end
redis.call('SET', key, slot + interval, 'EX', ttl)
return tostring(slot)
"""
_PACE_REDIS_KEY = "rl:login:pace"


def _pace_signin_redis(interval):
    """Reserve the next sign-in slot in Redis and wait for it.

    Returns False if Redis is unusable, so the caller can fall back to local
    pacing instead of silently dropping the throttle. Uses wall-clock time
    because the queue is shared between processes.
    """
    try:
        from app.utils.rate_limiter import _get_redis_conn
        conn = _get_redis_conn()
        if conn is None:
            return False
        slot = float(conn.eval(_PACE_LUA, 1, _PACE_REDIS_KEY,
                               time.time(), interval, 120))
    except Exception as e:
        logger.debug("Redis login pacing unavailable (%s) — pacing locally", e)
        return False
    delay = slot - time.time()
    if delay > 0:
        time.sleep(delay)
    return True


def _pace_signin():
    """Claim the next outbound sign-in slot, keeping us under the ceiling."""
    if _LOGIN_SIGNIN_RATE <= 0:
        return
    interval = 1.0 / _LOGIN_SIGNIN_RATE
    if _pace_signin_redis(interval):
        return
    with _pace_lock:
        now = time.monotonic()
        slot = max(now, _next_slot[0])
        _next_slot[0] = slot + interval
    delay = slot - time.monotonic()
    if delay > 0:
        time.sleep(delay)

_BAD_CREDENTIAL_CODES = {"invalid_credentials", "invalid_grant"}


def _is_transient_auth_error(exc) -> bool:
    """True for conditions worth retrying: rate limiting or a server-side fault.

    A wrong password is never retried.
    """
    status = getattr(exc, "status", None)
    code = (getattr(exc, "code", None) or "").lower()
    text = str(exc).lower()
    if code in _BAD_CREDENTIAL_CODES or "invalid login credentials" in text:
        return False
    return status == 429 or status is None or (status and status >= 500) \
        or "rate limit" in text or "too many" in text


def _classify_login_error(exc):
    """Return (credentials_are_wrong, message_shown_to_the_user)."""
    if not _is_transient_auth_error(exc):
        return True, "Email atau password salah"
    text = str(exc).lower()
    if "rate limit" in text or getattr(exc, "status", None) == 429:
        return False, ("Server autentikasi sedang sibuk (batas permintaan). "
                       "Tunggu beberapa detik, lalu coba lagi.")
    return False, "Gagal masuk karena gangguan sementara. Silakan coba lagi sebentar lagi."


def _sign_in_with_retry(supabase_auth, email, password):
    """`sign_in_with_password` that queues behind the rate limit, not fails.

    Each attempt claims a paced slot, and a rate-limited attempt waits for
    another slot until the wait budget runs out. Waiting is cooperative under
    gevent, so a queued login does not block the worker's other greenlets.

    A wrong password is neither retried nor waited on — it raises immediately.
    """
    import random

    deadline = time.monotonic() + _LOGIN_WAIT_BUDGET
    while True:
        _pace_signin()
        try:
            return supabase_auth.auth.sign_in_with_password(
                {"email": email, "password": password})
        except Exception as e:
            if not _is_transient_auth_error(e):
                raise
            if time.monotonic() >= deadline:
                raise
            logger.warning("Login rate-limited; waiting for another slot: %s", e)
            time.sleep(_LOGIN_RETRY_BASE * (0.5 + random.random()))


# ─── LOGIN (Admin & Super Admin) ─────────────────────

@auth_bp.route("/login", methods=["GET", "POST"])
# Per-IP only as a flood backstop; brute force is bounded per ACCOUNT below,
# because a school shares one NAT'd address and 30/min throttled whole classes.
@_rate_limit("300 per minute")
def login():
    if request.method == "GET":
        resp = make_response(render_template("auth/login.html"))
        resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        return resp

    email = request.form.get("email", "").strip().lower()
    password = request.form.get("password", "")

    if not email or not password:
        return render_template("auth/login.html", error="Email dan password wajib diisi")

    supabase_auth = get_auth_client()
    supabase = get_supabase()

    try:
        res = _sign_in_with_retry(supabase_auth, email, password)

        # Check profile status
        try:
            profile = supabase.table("profiles") \
                .select("role, status, school_id") \
                .eq("id", res.user.id) \
                .single() \
                .execute()
            pdata = profile.data or {}
            role = pdata.get("role", "admin_sekolah")
            status = pdata.get("status", "active")

            if status == "pending":
                return redirect(f"/auth/activate?email={email}&pending=1")

            if role not in ("super_admin", "admin_sekolah"):
                return render_template("auth/login.html", error="Halaman ini untuk Admin. Guru/Murid silakan masuk di halaman login terpisah.")

        except Exception:
            role = res.user.user_metadata.get("role", "admin_sekolah")
            status = "active"

        redirect_map = {
            "super_admin": "/admin/dashboard",
            "admin_sekolah": "/admin/dashboard",
            "guru": "/teacher/dashboard",
            "murid": "/student/dashboard",
        }
        redirect_url = redirect_map.get(role, "/admin/dashboard")
        resp = make_response(redirect(redirect_url))
        # A flash left over from a session that has just ended describes a state
        # the user is no longer in. The login page is where it belongs, and it is
        # rendered there; dropping it here keeps it from being replayed on the
        # next page that renders flashes (e.g. "Silakan login terlebih dahulu"
        # appearing on a page the user opens while fully logged in).
        session.pop("_flashes", None)
        set_auth_cookie(resp, "access_token", res.session.access_token, max_age=86400)
        set_auth_cookie(resp, "refresh_token", res.session.refresh_token, max_age=86400 * 7)
        set_auth_cookie(resp, "session_start", str(time.time()), max_age=86400 * 7)
    except Exception as e:
        wrong_password, message = _classify_login_error(e)
        if wrong_password:
            # Failed attempts are counted per ACCOUNT. Keying this on the IP
            # would lock out every colleague behind the same school NAT.
            allowed, retry = check_account_limit("login_failed", email, ip=request.remote_addr)
            if not allowed:
                return render_template("auth/login.html", error=rate_limit_message(retry, "percobaan login"))
        else:
            # A transient failure must NOT consume the account's budget: doing so
            # let a rate-limit spike ban a school from logging in for 15 minutes.
            logger.warning("Login transient failure for %s: %s", email, e)
        return render_template("auth/login.html", error=message)
    # log_activity outside try/except so audit failures don't block login
    try:
        log_activity("login", "user", res.user.id, new_data={"role": role, "ip": request.remote_addr})
    except Exception as e:
        current_app.logger.warning("Login audit log failed: %s", e)
    return resp


# ─── LOGIN USER (Guru & Murid) ───────────────────────

@auth_bp.route("/login-user", methods=["GET", "POST"])
def login_user():
    if request.method == "GET":
        role_hint = request.args.get("role", "")
        resp = make_response(render_template("auth/login_user.html", role_hint=role_hint))
        resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        return resp

    login_input = request.form.get("email", "").strip().lower()
    password = request.form.get("password", "")

    if not login_input or not password:
        return render_template("auth/login_user.html", error="Email/NISN dan password wajib diisi")

    supabase_auth = get_auth_client()
    supabase = get_supabase()

    # Support NISN login for students / NIP login for teachers
    email = login_input
    if "@" not in login_input:
        found_id = None
        # Try NISN (students) — use raw query since profiles.nisn may not exist as column
        try:
            prof = supabase.table("profiles").select("id").filter("nisn", "eq", login_input).limit(1).execute()
            if prof.data:
                found_id = prof.data[0]["id"]
        except:
            # Fallback: search auth user_metadata for NISN
            try:
                users = supabase.auth.admin.list_users()
                for u in users:
                    meta = getattr(u, 'user_metadata', {}) or {}
                    if meta.get("nisn") == login_input:
                        found_id = u.id
                        break
            except:
                pass
        # Try NIP (teachers)
        if not found_id:
            try:
                t = supabase.table("teachers").select("id").eq("employee_id", login_input).limit(1).execute()
                if t.data:
                    found_id = t.data[0]["id"]
            except:
                pass
        if found_id:
            try:
                user_info = supabase.auth.admin.get_user_by_id(found_id)
                email = user_info.user.email
            except:
                pass

    try:
        res = _sign_in_with_retry(supabase_auth, email, password)

        try:
            profile = supabase.table("profiles") \
                .select("role, status") \
                .eq("id", res.user.id) \
                .single() \
                .execute()
            pdata = profile.data or {}
            role = pdata.get("role", "murid")
            status = pdata.get("status", "active")

            if status == "pending":
                return redirect(f"/auth/activate?email={email}&pending=1")

            if role not in ("guru", "murid"):
                return render_template("auth/login_user.html",
                                       error="Halaman ini untuk Guru/Murid. Admin silakan masuk di halaman login utama.")

        except Exception:
            role = res.user.user_metadata.get("role", "murid")

        redirect_map = {
            "guru": "/teacher/dashboard",
            "murid": "/student/dashboard",
        }
        redirect_url = redirect_map.get(role, "/student/dashboard")
        resp = make_response(redirect(redirect_url))
        # A flash left over from a session that has just ended describes a state
        # the user is no longer in. The login page is where it belongs, and it is
        # rendered there; dropping it here keeps it from being replayed on the
        # next page that renders flashes (e.g. "Silakan login terlebih dahulu"
        # appearing on a page the user opens while fully logged in).
        session.pop("_flashes", None)
        set_auth_cookie(resp, "access_token", res.session.access_token, max_age=86400)
        set_auth_cookie(resp, "refresh_token", res.session.refresh_token, max_age=86400 * 7)
        set_auth_cookie(resp, "session_start", str(time.time()), max_age=86400 * 7)
        log_activity("login", "user", res.user.id, new_data={"role": role, "ip": request.remote_addr})
        return resp
    except Exception as e:
        # Same reasoning as /login: throttle the account, not the school's IP,
        # and only for genuine credential failures.
        wrong_password, message = _classify_login_error(e)
        if wrong_password:
            allowed, retry = check_account_limit("login_failed", login_input, ip=request.remote_addr)
            if not allowed:
                return render_template("auth/login_user.html", error=rate_limit_message(retry, "percobaan login"))
        else:
            logger.warning("Login transient failure for %s: %s", login_input, e)
        return render_template("auth/login_user.html", error=message)


# ─── FORGOT PASSWORD — 6-digit code flow ────────────

_RESET_CODES = {}  # fallback: in-memory (single worker)

def _store_reset_code(email: str, code: str, ttl: int = 600):
    """Store reset code in Redis (or memory fallback)."""
    try:
        from redis import Redis
        r = Redis.from_url(current_app.config.get("REDIS_URL", "redis://localhost:6379/0"))
        r.setex(f"reset_code:{email}", ttl, code)
        return
    except Exception:
        pass
    _RESET_CODES[email] = {"code": code, "expires": time.time() + ttl}


def _get_reset_code(email: str) -> str | None:
    """Retrieve stored reset code."""
    try:
        from redis import Redis
        r = Redis.from_url(current_app.config.get("REDIS_URL", "redis://localhost:6379/0"))
        code = r.get(f"reset_code:{email}")
        if code:
            return code.decode() if isinstance(code, bytes) else code
        return None
    except Exception:
        pass
    entry = _RESET_CODES.get(email)
    if entry and entry["expires"] > time.time():
        return entry["code"]
    return None


def _delete_reset_code(email: str):
    try:
        from redis import Redis
        r = Redis.from_url(current_app.config.get("REDIS_URL", "redis://localhost:6379/0"))
        r.delete(f"reset_code:{email}")
        return
    except Exception:
        pass
    _RESET_CODES.pop(email, None)


def _send_email(to_email: str, subject: str, body: str):
    """Send email via SMTP (scangrade9@gmail.com)."""
    import smtplib, ssl
    from email.mime.text import MIMEText
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = "ScanGrade <scangrade9@gmail.com>"
    msg["To"] = to_email
    context = ssl.create_default_context()
    smtp_email = current_app.config.get("SMTP_EMAIL", "")
    smtp_pass = current_app.config.get("SMTP_PASSWORD", "")
    if not smtp_email or not smtp_pass:
        current_app.logger.warning("SMTP not configured — email not sent")
        return
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=context) as server:
        server.login(smtp_email, smtp_pass)
        server.sendmail(smtp_email, to_email, msg.as_string())


@auth_bp.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    if request.method == "GET":
        return render_template("auth/forgot_password.html")

    email = request.form.get("email", "").strip().lower()
    if not email:
        return render_template("auth/forgot_password.html", error="Email aktif atau NISN wajib diisi")

    # Per-ACCOUNT throttle (the email/NISN itself). This endpoint sends mail, so
    # it needs a real limit — but keyed to the account, so a class of students
    # behind one school IP can each request their own code.
    allowed, retry = check_account_limit("forgot_password", email)
    if not allowed:
        return render_template("auth/forgot_password.html", error=rate_limit_message(retry, "permintaan kode"))

    supabase = get_supabase()

    # Find user by: recovery email (phone), auth email, or NISN
    user_data = None  # {auth_email, recovery_email, user_id}
    target_email = email  # where to send the code

    # 1. Search profiles by phone (recovery email)
    try:
        prof = row_or_none(
            supabase.table("profiles").select("id, phone, full_name, role")
            .eq("phone", email).maybe_single().execute()
        )
        if prof:
            auth_client = get_auth_client()
            au = auth_client.admin.get_user_by_id(prof["id"])
            user_data = {
                "auth_email": au.user.email,
                "recovery_email": email,
                "user_id": prof["id"],
                "role": prof.get("role", "murid"),
                "full_name": prof.get("full_name", ""),
            }
            target_email = email  # send to the recovery email they entered
    except Exception:
        pass

    # 2. Search by NISN
    if not user_data:
        for table_name in ("students", "teachers"):
            try:
                rec = row_or_none(
                    supabase.table(table_name).select("id, profiles!inner(phone, full_name, role)").eq(
                        "nisn" if table_name == "students" else "employee_id", email
                    ).maybe_single().execute()
                )
                if rec:
                    prof = rec.get("profiles") or {}
                    auth_client = get_auth_client()
                    au = auth_client.admin.get_user_by_id(rec["id"])
                    recovery = prof.get("phone", "")
                    target_email = recovery if "@" in recovery else au.user.email
                    user_data = {
                        "auth_email": au.user.email,
                        "recovery_email": recovery if "@" in recovery else "",
                        "user_id": rec["id"],
                        "role": prof.get("role", "murid"),
                        "full_name": prof.get("full_name", ""),
                    }
                    break
            except Exception:
                pass

    # 3. Search by auth email directly
    if not user_data:
        try:
            auth_client = get_auth_client()
            users = auth_client.admin.list_users()
            for u in users:
                if u.email and u.email.lower() == email:
                    prof = row_or_none(
                        supabase.table("profiles").select("phone, full_name, role")
                        .eq("id", u.id).maybe_single().execute()
                    )
                    p = prof or {}
                    recovery = p.get("phone", "")
                    target_email = recovery if "@" in recovery else u.email
                    user_data = {
                        "auth_email": u.email,
                        "recovery_email": recovery if "@" in recovery else "",
                        "user_id": u.id,
                        "role": p.get("role", "murid"),
                        "full_name": p.get("full_name", ""),
                    }
                    break
        except Exception:
            pass

    if not user_data:
        return render_template("auth/forgot_password.html", error="Email atau NISN tidak ditemukan. Hubungi admin sekolah.")

    # Generate 6-digit code (uppercase + digits)
    import random, string
    code = "".join(random.choices(string.ascii_uppercase + string.digits, k=6))
    _store_reset_code(target_email, code)

    # Send code via SMTP
    name = user_data.get("full_name", "Pengguna")
    try:
        _send_email(
            target_email,
            "🔐 ScanGrade — Kode Verifikasi Reset Password",
            f"""Yth. {name},

Kami menerima permintaan reset password untuk akun ScanGrade Anda.

Kode verifikasi Anda (6 digit):
┌─────────────────────┐
│     {code}     │
└─────────────────────┘

Kode ini berlaku selama 10 menit.

Masukkan kode di atas pada halaman verifikasi untuk membuat password baru.

Jika Anda tidak merasa melakukan permintaan ini, abaikan email ini.

Hormat kami,
Tim ScanGrade
https://scangrade.web.id"""
        )
    except Exception as e:
        current_app.logger.error(f"Failed to send reset code: {e}")
        return render_template("auth/forgot_password.html", error="Gagal mengirim email. Coba lagi nanti.")

    return render_template("auth/verify_code.html", email=target_email, auth_email=user_data["auth_email"])


@auth_bp.route("/verify-reset-code", methods=["GET", "POST"])
def verify_reset_code():
    if request.method == "GET":
        email = request.args.get("email", "")
        if not email:
            return redirect(url_for("auth.forgot_password"))
        return render_template("auth/verify_code.html", email=email)

    email = request.form.get("email", "").strip().lower()
    code = request.form.get("code", "").strip().upper()

    if not email or not code:
        return render_template("auth/verify_code.html", email=email, error="Kode wajib diisi")

    # Per-ACCOUNT throttle (not per-IP): caps brute-forcing one account's reset
    # code while letting a whole class verify their own codes from a shared IP.
    allowed, retry = check_account_limit("verify_code", email)
    if not allowed:
        return render_template("auth/verify_code.html", email=email,
                               error=rate_limit_message(retry, "percobaan kode"))

    stored = _get_reset_code(email)
    if not stored:
        return render_template("auth/verify_code.html", email=email, error="Kode tidak valid atau sudah kedaluwarsa. Minta kode baru.")

    if stored != code:
        return render_template("auth/verify_code.html", email=email, error="Kode salah. Coba lagi.")

    # Code OK — consume it to prevent replay, and set session marker
    _delete_reset_code(email)
    session["reset_email"] = email
    return render_template("auth/set_new_password.html", email=email)


@auth_bp.route("/set-new-password", methods=["POST"])
def set_new_password():
    email = request.form.get("email", "").strip().lower()
    password = request.form.get("password", "")
    confirm = request.form.get("confirm_password", "")

    if not password or not confirm:
        return render_template("auth/set_new_password.html", email=email, error="Semua field wajib diisi")
    if password != confirm:
        return render_template("auth/set_new_password.html", email=email, error="Password tidak cocok")
    if len(password) < 6:
        return render_template("auth/set_new_password.html", email=email, error="Password minimal 6 karakter")

    # Verify session marker (code was consumed during verification)
    session_email = session.get("reset_email", "")
    if not session_email or session_email != email:
        return render_template("auth/set_new_password.html", email=email, error="Sesi kedaluwarsa. Ulangi proses reset.")

    # Find user and update password
    supabase = get_supabase()
    auth_client = get_auth_client()
    user_id = None
    role = "murid"

    try:
        # Find by recovery email (phone)
        prof = row_or_none(
            supabase.table("profiles").select("id, role")
            .eq("phone", email).maybe_single().execute()
        )
        if not prof:
            # Find by auth email
            users = auth_client.admin.list_users()
            for u in users:
                if u.email and u.email.lower() == email:
                    user_id = u.id
                    p2 = row_or_none(
                        supabase.table("profiles").select("role")
                        .eq("id", u.id).maybe_single().execute()
                    )
                    role = p2.get("role", "murid") if p2 else "murid"
                    break
        else:
            user_id = prof["id"]
            role = prof.get("role", "murid")
    except Exception:
        pass

    if not user_id:
        return render_template("auth/set_new_password.html", email=email, error="User tidak ditemukan")

    try:
        auth_client.admin.update_user_by_id(user_id, {"password": password})
        session.pop("reset_email", None)

        # Role-based redirect
        role_redirects = {
            "super_admin": "/super-admin/dashboard",
            "admin_sekolah": "/admin-sekolah/dashboard",
            "guru": "/teacher/dashboard",
            "murid": "/student/dashboard",
        }
        redirect_url = role_redirects.get(role, "/auth/login-user")
        return render_template("auth/reset_success.html", redirect_url=redirect_url, role=role)
    except Exception as e:
        current_app.logger.error(f"Reset password error: {e}")
        return render_template("auth/set_new_password.html", email=email, error="Gagal mereset password. Coba lagi.")


# ─── RESET PASSWORD ──────────────────────────────────

@auth_bp.route("/reset-password", methods=["GET", "POST"])
def reset_password():
    if request.method == "GET":
        return render_template("auth/reset_password.html")

    password = request.form.get("password", "")
    confirm = request.form.get("confirm_password", "")

    if not password or not confirm:
        return render_template("auth/reset_password.html", error="Semua field wajib diisi")

    if password != confirm:
        return render_template("auth/reset_password.html", error="Password tidak cocok")

    if len(password) < 6:
        return render_template("auth/reset_password.html", error="Password minimal 6 karakter")

    access_token = request.form.get("access_token", "") or request.args.get("access_token", "")

    if not access_token:
        return render_template("auth/reset_password.html", error="Token reset tidak ditemukan. Silakan ulangi proses reset password.")

    supabase = get_auth_client()
    try:
        supabase.auth.set_session(access_token, "")
        supabase.auth.update_user({"password": password})
        return render_template("auth/reset_password_success.html")
    except Exception as e:
        current_app.logger.error(f"Reset password error: {e}")
        return render_template("auth/reset_password.html", error="Gagal mereset password. Token mungkin kedaluwarsa.")


# ─── RESET PASSWORD (client-side token exchange) ─────

@auth_bp.route("/reset-password-exchange", methods=["POST"])
def reset_password_exchange():
    """Accepts access_token from URL fragment (sent by client JS) + new password."""
    data = request.get_json(silent=True) or {}
    access_token = data.get("access_token", "")
    refresh_token = data.get("refresh_token", "")
    password = data.get("password", "")

    if not access_token or not password:
        return jsonify({"error": "access_token and password required"}), 400
    if len(password) < 6:
        return jsonify({"error": "Password minimal 6 karakter"}), 400

    supabase = get_auth_client()
    try:
        if refresh_token:
            supabase.auth.set_session(access_token, refresh_token)
        else:
            supabase.auth.set_session(access_token, "")
        supabase.auth.update_user({"password": password})
        return jsonify({"ok": True})
    except Exception as e:
        current_app.logger.error(f"Password exchange error: {e}")
        return jsonify({"error": str(e)}), 400


# ─── LOGOUT ──────────────────────────────────────────

@auth_bp.route("/logout")
def logout():
    uid = getattr(g, "user_id", None)
    # Drop the cached session first, so the token stops working right away
    # instead of remaining valid for the remainder of the cache TTL.
    invalidate_session(request.cookies.get("access_token"))
    try:
        supabase = get_auth_client()
        supabase.auth.sign_out()
    except Exception:
        pass
    if uid:
        log_activity("logout", "user", uid)
    resp = make_response(redirect("/auth/login-user" if g.get("user_role") in ("guru", "murid") else "/auth/login"))
    resp.delete_cookie("access_token", path="/")
    resp.delete_cookie("refresh_token", path="/")
    return resp


# ─── ME ──────────────────────────────────────────────

@auth_bp.route("/me", methods=["GET"])
@login_required
def me():
    return jsonify({
        "user_id": g.user_id,
        "role": g.user_role,
        "school_id": str(g.user_school_id) if g.user_school_id else None,
        "status": g.get("user_status", "active"),
    })


# ─── SET TIMEZONE ────────────────────────────────────

@auth_bp.route("/set-timezone", methods=["POST"])
@login_required
def set_timezone():
    from flask import make_response
    offset = request.form.get("tz_offset") or (request.get_json(silent=True, force=True).get("tz_offset", 7) if request.is_json else request.form.get("tz_offset", 7))
    try:
        offset = int(offset)
        if offset < -12 or offset > 14:
            offset = 7
    except (ValueError, TypeError):
        offset = 7
    resp = make_response(jsonify({"ok": True, "tz_offset": offset}))
    resp.set_cookie("tz_offset", str(offset), httponly=False, samesite="Lax", path="/", max_age=365 * 86400)
    return resp
