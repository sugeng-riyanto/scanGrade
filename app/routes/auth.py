from datetime import datetime, timezone
import time
from flask import Blueprint, request, jsonify, g, render_template, redirect, url_for, make_response, current_app
from app.utils.auth import login_required, get_supabase, get_auth_client
from app.services.audit_service import log_activity
from app.utils.security import sanitize_input
from app.utils.rate_limiter import limiter

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
            "status": "pending",
        }).execute()
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


# ─── LOGIN (Admin & Super Admin) ─────────────────────

@auth_bp.route("/login", methods=["GET", "POST"])
@_rate_limit("30 per minute")
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
        res = supabase_auth.auth.sign_in_with_password({"email": email, "password": password})

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
        resp.set_cookie("access_token", res.session.access_token, httponly=True, samesite="Lax", path="/", max_age=86400)
        resp.set_cookie("refresh_token", res.session.refresh_token, httponly=True, samesite="Lax", path="/", max_age=86400*7)
        log_activity("login", "user", res.user.id, new_data={"role": role, "ip": request.remote_addr})
        return resp
    except Exception:
        return render_template("auth/login.html", error="Email atau password salah")


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
        # Try NISN (students)
        if not found_id:
            try:
                prof = supabase.table("profiles").select("id").eq("nisn", login_input).limit(1).execute()
                if prof.data:
                    found_id = prof.data[0]["id"]
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
        res = supabase_auth.auth.sign_in_with_password({"email": email, "password": password})

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
        resp.set_cookie("access_token", res.session.access_token, httponly=True, samesite="Lax", path="/", max_age=86400)
        resp.set_cookie("refresh_token", res.session.refresh_token, httponly=True, samesite="Lax", path="/", max_age=86400*7)
        log_activity("login", "user", res.user.id, new_data={"role": role, "ip": request.remote_addr})
        return resp
    except Exception:
        return render_template("auth/login_user.html", error="Email atau password salah")


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
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=context) as server:
        server.login("scangrade9@gmail.com", "tjyv mycd pznp fmqn")
        server.sendmail("scangrade9@gmail.com", to_email, msg.as_string())


@auth_bp.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    if request.method == "GET":
        return render_template("auth/forgot_password.html")

    email = request.form.get("email", "").strip().lower()
    if not email:
        return render_template("auth/forgot_password.html", error="Email aktif atau NISN wajib diisi")

    supabase = get_supabase()

    # Find user by: recovery email (phone), auth email, or NISN
    user_data = None  # {auth_email, recovery_email, user_id}
    target_email = email  # where to send the code

    # 1. Search profiles by phone (recovery email)
    try:
        prof = supabase.table("profiles").select("id, phone, full_name, role").eq("phone", email).maybe_single().execute()
        if prof.data:
            auth_client = get_auth_client()
            au = auth_client.admin.get_user_by_id(prof.data["id"])
            user_data = {
                "auth_email": au.user.email,
                "recovery_email": email,
                "user_id": prof.data["id"],
                "role": prof.data.get("role", "murid"),
                "full_name": prof.data.get("full_name", ""),
            }
            target_email = email  # send to the recovery email they entered
    except Exception:
        pass

    # 2. Search by NISN
    if not user_data:
        for table_name in ("students", "teachers"):
            try:
                rec = supabase.table(table_name).select("id, profiles!inner(phone, full_name, role)").eq(
                    "nisn" if table_name == "students" else "employee_id", email
                ).maybe_single().execute()
                if rec.data:
                    prof = rec.data.get("profiles") or {}
                    auth_client = get_auth_client()
                    au = auth_client.admin.get_user_by_id(rec.data["id"])
                    recovery = prof.get("phone", "")
                    target_email = recovery if "@" in recovery else au.user.email
                    user_data = {
                        "auth_email": au.user.email,
                        "recovery_email": recovery if "@" in recovery else "",
                        "user_id": rec.data["id"],
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
                    prof = supabase.table("profiles").select("phone, full_name, role").eq("id", u.id).maybe_single().execute()
                    p = prof.data or {}
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

    stored = _get_reset_code(email)
    if not stored:
        return render_template("auth/verify_code.html", email=email, error="Kode tidak valid atau sudah kedaluwarsa. Minta kode baru.")

    if stored != code:
        return render_template("auth/verify_code.html", email=email, error="Kode salah. Coba lagi.")

    # Code OK — show password reset form
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

    # Verify code still valid
    stored = _get_reset_code(email)
    if not stored:
        return render_template("auth/set_new_password.html", email=email, error="Sesi kedaluwarsa. Ulangi proses reset.")

    # Find user and update password
    supabase = get_supabase()
    auth_client = get_auth_client()
    user_id = None
    role = "murid"

    try:
        # Find by recovery email (phone)
        prof = supabase.table("profiles").select("id, role").eq("phone", email).maybe_single().execute()
        if not prof.data:
            # Find by auth email
            users = auth_client.admin.list_users()
            for u in users:
                if u.email and u.email.lower() == email:
                    user_id = u.id
                    p2 = supabase.table("profiles").select("role").eq("id", u.id).maybe_single().execute()
                    role = p2.data.get("role", "murid") if p2.data else "murid"
                    break
        else:
            user_id = prof.data["id"]
            role = prof.data.get("role", "murid")
    except Exception:
        pass

    if not user_id:
        return render_template("auth/set_new_password.html", email=email, error="User tidak ditemukan")

    try:
        auth_client.admin.update_user_by_id(user_id, {"password": password})
        _delete_reset_code(email)

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
