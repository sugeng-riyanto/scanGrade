from datetime import datetime, timezone
import logging
import os
import threading
import time
from flask import Blueprint, request, jsonify, g, session, render_template, redirect, url_for, make_response, current_app, flash
from app.utils.auth import (login_required, get_supabase, get_auth_client, get_auth_admin,
                            find_auth_user_by_email, invalidate_session, set_auth_cookie,
                            login_door_for, session_role, _extract_token,
                            USER_ROLES, ALL_ROLES, SIGN_IN_TABS, dashboard_for,
                            sign_in_tab, password_change_record)
from app.utils.helpers import row_or_none
from app.services.audit_service import log_activity
from app.utils.security import sanitize_input
from app.utils.rate_limiter import limiter, check_account_limit
from app.utils.auth_messages import auth_error, rate_limit_error

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

#: What the position `<select>` submits when the school's role is not on the list,
#: so it can type its own. The sentinel is never stored and never shown: an operator
#: reading `requester_position` on the approval screen must see the school's own
#: words, not this.
POSITION_OTHER = "other"

#: Longest position this field will carry into the approval queue and the profile.
#: A free-typed field is a *label*, and an operator-facing one: a paragraph pasted
#: into it would follow the registration through every screen that names the person.
POSITION_MAX_LENGTH = 80


def _resolve_position(form) -> str:
    """The position the school actually named: the list's value, or its own typing.

    One function so the stored value, the profile's `full_name` and the email's
    greeting cannot disagree — they were three separate reads of the raw field, and
    the form is about to gain a second field that can carry the real answer.
    """
    chosen = (form.get("position") or "").strip()
    if chosen == POSITION_OTHER:
        chosen = (form.get("position_other") or "").strip()
    return chosen[:POSITION_MAX_LENGTH]


@auth_bp.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "GET":
        return render_template("auth/register.html")

    npsn = request.form.get("npsn", "").strip()
    school_name = request.form.get("school_name", "").strip()
    wa = request.form.get("wa", "").strip()
    email = request.form.get("email", "").strip().lower()
    password = request.form.get("password", "")
    position = _resolve_position(request.form)

    if not all([npsn, school_name, wa, email, password, position]):
        return render_template("auth/register.html", error=auth_error("all_required"))

    if len(password) < 6:
        return render_template("auth/register.html", error=auth_error("password_short"))

    # ── Consent check (UU PDP) ──
    consent = request.form.get("consent")
    if not consent:
        return render_template("auth/register.html", error=auth_error("tos_required"))

    # Per-ACCOUNT throttle on the school (NPSN), not the client IP: several
    # schools can share one NAT'd address, so an IP-keyed limit would let one
    # school's retries block another's first attempt.
    allowed, retry = check_account_limit("register", npsn)
    if not allowed:
        return render_template("auth/register.html", error=rate_limit_error("registration", retry))

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
                return render_template("auth/register.html", error=auth_error("npsn_pending"))
            return render_template("auth/register.html",
                                   error=auth_error("npsn_taken", school=dup.get("school_name", "")))
    except Exception:
        pass
    try:
        existing_school = supabase.table("schools").select("id", "name").eq("npsn", npsn).execute()
        if existing_school.data:
            return render_template("auth/register.html",
                                   error=auth_error("npsn_taken_plain",
                                                    school=existing_school.data[0].get("name", "")))
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
            return render_template("auth/register.html", error=auth_error("email_taken"))
        current_app.logger.error(f"Register step 1 (create_user) failed: {err}")
        return render_template("auth/register.html",
                               error=auth_error("account_create_failed", reason=err[:200]))

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
        return render_template("auth/register.html", error=auth_error("profile_save_failed"))

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
        return render_template("auth/register.html", error=auth_error("request_create_failed"))

    try:
        log_activity("register", "user", uid, new_data={"email": email, "school_name": school_name, "role": "admin_sekolah", "status": "pending"})
    except Exception:
        pass

    # ── The acknowledgement, best-effort ──
    # The account, the profile and the request row already exist, so this mail is a
    # courtesy *after* the registration and never a condition of it. A relay that
    # refuses — or a body that cannot be built — must not turn a registration that
    # happened into an error page: the school would retry, and the retry would be
    # refused as a duplicate account for a request already on file. The sender is the
    # one place every mail in this app goes through (`smtp_settings.send`), so it is
    # also the one place the outcome is written down for the operator (mail_ledger).
    try:
        from app.services import email_bodies

        mail = email_bodies.registration_received(name=position, school_name=school_name,
                                                  npsn=npsn, position=position)
        _send_email(email, mail["subject"], mail["html"], html=True, text=mail["text"])
    except Exception as e:
        logger.warning("Registration acknowledgement to %s not sent: %s", email, e)

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
        return render_template("auth/activate.html", error=auth_error("activate_required"), email=email)

    if len(code) != 12 or not code.isalnum():
        return render_template("auth/activate.html", error=auth_error("activate_code_shape"), email=email)

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
            return render_template("auth/activate.html", error=auth_error("activate_code_used"), email=email)

        # Check expiry
        expires_at = req.get("expires_at")
        if expires_at:
            expires_dt = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
            if expires_dt < datetime.now(timezone.utc):
                return render_template("auth/activate.html", error=auth_error("activate_code_expired_contact"), email=email)

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
        return render_template("auth/activate.html", error=auth_error("activate_code_expired"), email=email)


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
        return True, auth_error("login_bad_credentials")
    text = str(exc).lower()
    if "rate limit" in text or getattr(exc, "status", None) == 429:
        return False, auth_error("login_auth_busy")
    return False, auth_error("login_transient")


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


# ─── SIGN IN (one page, every role) ──────────────────
#
# There used to be two doors: one for admins, one for teachers, students and the
# two school officials. Which door a reader belonged on was a property of their
# role, so every path that answered "you are not signed in" had to name one — and
# naming the wrong one was a dead end they could only escape by spotting the small
# link to the other page. Now one page signs everyone in, and the role comes from
# the account rather than from the page the reader chose.
#
# Both old URLs still answer, because they are published: `/tutorial/admin-sekolah`
# and four cards on `/demo` link to them, schools have them bookmarked, and a
# printed login card names one. A GET forwards here carrying whatever `?role=` and
# `?next=` it was given; a POST is signed in by this same code, because a 302 on a
# POST throws the credentials away and hands the reader an empty form.

#: Query arguments the old doors forward. Both stay *text*: they are carried for
#: the reader's sake and are never turned into a redirect target, so neither can
#: become an open redirect however it is spelled.
_FORWARDED_ARGS = ("role", "next")


def _sign_in_page(**context):
    """The one sign-in page, uncached.

    `no-store` is not decoration: this page is where `login_required` sends an
    expired session, and a browser that cached it would replay that notice on the
    next visit, reading as that page's own error.

    The page context is filled in here rather than left to the caller, because the
    caller that forgets it is the *refusal* path: a failed password check rendering
    the page without `initial_tab` took the whole request down with a 500 (the tab
    strip is built from it, and `tojson` cannot serialise `Undefined`). A form that
    answers a wrong password with an empty page is worse than one that says nothing,
    and the fix is that no call site can omit it.
    """
    ctx = _sign_in_context()
    ctx.update(context)
    resp = make_response(render_template("auth/login.html", **ctx))
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    return resp


def _forward_to_sign_in():
    """A GET on an old door: hand the reader to the one page, keeping its hints."""
    forwarded = {key: request.args[key] for key in _FORWARDED_ARGS if request.args.get(key)}
    return redirect(url_for("auth.sign_in", **forwarded))


def _sign_in_context():
    """What the page needs to open on the right tab.

    `role` is validated rather than echoed: `sign_in_tab` answers `""` for
    anything it does not recognise, so `?role=<anything>` preselects nothing. The
    tab is a hint for the reader — the placeholder and the helper line — and never
    a claim the server acts on. `next` is kept only when it is a path on this site,
    so a link from elsewhere cannot smuggle an absolute URL into the form.
    """
    role = request.args.get("role", "")
    tab = sign_in_tab(role)
    next_url = request.args.get("next", "")
    return {
        "role_hint": role if tab else "",
        "initial_tab": tab,
        "tabs": SIGN_IN_TABS,
        "next_url": next_url if next_url.startswith("/") and not next_url.startswith("//") else "",
    }


def _role_and_status(supabase, res, login_input):
    """`(role, status)` for the account, or `(None, "active")` if the row cannot be read.

    `None` and `""` are deliberately different answers. An empty role is an account
    this app has no home for — a refusal the reader must not be able to tell apart
    from a wrong password. `None` is a profile row the server could not read *and*
    no role in the account's own metadata: an infrastructure failure, which the
    caller answers with the transient sentence rather than a credential one, and
    which must not consume the account's attempt budget.
    """
    try:
        profile = supabase.table("profiles") \
            .select("role, status, school_id") \
            .eq("id", res.user.id) \
            .single() \
            .execute()
        pdata = profile.data or {}
        role = pdata.get("role") or ""
        if role:
            return role, pdata.get("status", "active")
    except Exception as e:
        logger.warning("Sign-in profile lookup failed for %s: %s", login_input, e)

    metadata_role = res.user.user_metadata.get("role") or ""
    return (metadata_role or None), "active"


def _identifier_email(supabase, login_input):
    """The email behind an identifier: an address, a NISN, or an employee id.

    A pupil's card carries a NISN and a teacher's carries a NIP, so the field takes
    all three — the door a reader came through used to decide which of them was
    even looked for, and now it does not. When nothing matches, the text is
    returned unchanged and sign-in fails exactly as a wrong password does, which is
    the point: being told "no such NISN" is being told which NISNs exist.
    """
    if "@" in login_input:
        return login_input

    email = login_input
    found_id = None

    # A pupil, by NISN. `profiles.nisn` is probed with a raw filter because the
    # column is not guaranteed on every school's schema, and the account's own
    # metadata is the fallback for the same reason.
    try:
        prof = supabase.table("profiles").select("id").filter("nisn", "eq", login_input).limit(1).execute()
        if prof.data:
            found_id = prof.data[0]["id"]
    except Exception:
        try:
            for u in supabase.auth.admin.list_users():
                if (getattr(u, "user_metadata", {}) or {}).get("nisn") == login_input:
                    found_id = u.id
                    break
        except Exception:
            pass

    # A teacher or another member of staff, by employee id.
    if not found_id:
        try:
            t = supabase.table("teachers").select("id").eq("employee_id", login_input).limit(1).execute()
            if t.data:
                found_id = t.data[0]["id"]
        except Exception:
            pass

    if found_id:
        try:
            email = supabase.auth.admin.get_user_by_id(found_id).user.email
        except Exception:
            pass
    return email


def _sign_in():
    """Match an identifier and a password, then send the account to its own home.

    The role is matched against **every** role this app has, whatever tab the page
    was showing — the tab is a placeholder the reader chose, and trusting it would
    let a pupil's card be validated as a teacher's. There is no "wrong door" left
    to refuse, so the two sentences that used to name the other page are gone: an
    unknown identifier, a wrong password and an account whose role this app does not
    have all produce the same one, and nothing in it says which of the three it was.
    """
    login_input = request.form.get("email", "").strip().lower()
    password = request.form.get("password", "")

    if not login_input or not password:
        return _sign_in_page(error=auth_error("login_required_fields"))

    supabase_auth = get_auth_client()
    supabase = get_supabase()
    email = _identifier_email(supabase, login_input)

    try:
        res = _sign_in_with_retry(supabase_auth, email, password)

        role, status = _role_and_status(supabase, res, login_input)
        if role not in ALL_ROLES:
            # `None` is a row nobody could read (the server's problem); `""` is an
            # account with no role this app has (the reader's business, and the one
            # the brief wants answered with the generic credential sentence).
            return _sign_in_page(
                error=auth_error("login_transient" if role is None else "login_bad_credentials"))

        if status == "pending":
            return redirect(f"/auth/activate?email={email}&pending=1")

        # Their own home, through the one mapping that owns that question.
        redirect_url = dashboard_for(role)
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
            # Failed attempts are counted per ACCOUNT, keyed on what the reader
            # typed rather than on the address it resolved to — a NISN and an
            # email are the same account, and one of them is what they will try
            # again. Keying this on the IP would lock out every colleague behind
            # the same school NAT.
            allowed, retry = check_account_limit("login_failed", login_input, ip=request.remote_addr)
            if not allowed:
                return _sign_in_page(error=rate_limit_error("login", retry))
        else:
            # A transient failure must NOT consume the account's budget: doing so
            # let a rate-limit spike ban a school from logging in for 15 minutes.
            logger.warning("Login transient failure for %s: %s", login_input, e)
        return _sign_in_page(error=message)
    # log_activity outside try/except so audit failures don't block login
    try:
        log_activity("login", "user", res.user.id, new_data={"role": role, "ip": request.remote_addr})
    except Exception as e:
        current_app.logger.warning("Login audit log failed: %s", e)
    return resp


@auth_bp.route("/sign-in", methods=["GET", "POST"])
# The same per-IP flood backstop the admin door has always carried. It is a
# backstop and not the brute-force defence — that is the per-ACCOUNT counter below,
# because a school shares one NAT'd address and a per-IP attempt limit throttled
# whole classes.
@_rate_limit("300 per minute")
def sign_in():
    """The sign-in page, and the only place a password is checked."""
    if request.method == "GET":
        return _sign_in_page(**_sign_in_context())
    return _sign_in()


# ─── LOGIN (the admin door, now an alias) ────────────

@auth_bp.route("/login", methods=["GET", "POST"])
@_rate_limit("300 per minute")
def login():
    """The admin door, kept answering for the links and cards that still name it.

    `/tutorial/admin-sekolah` and the super-admin and school-admin cards on `/demo`
    link here, and a school's printed cards may too. Both methods are accepted: a
    POST is signed in by the one handler, a GET is forwarded to the one page.
    """
    if request.method == "POST":
        return _sign_in()
    return _forward_to_sign_in()


# ─── LOGIN USER (the teacher/student door, now an alias) ───

@auth_bp.route("/login-user", methods=["GET", "POST"])
# This door used to carry no per-IP limit at all, so a script could POST it as fast
# as the network allowed while only the per-account counter — which needs a correct
# identifier to key on — stood in the way. It has the same backstop as the other.
@_rate_limit("300 per minute")
def login_user():
    """The teacher/student door, kept answering — see `login`."""
    if request.method == "POST":
        return _sign_in()
    return _forward_to_sign_in()


# ─── FORGOT PASSWORD — 6-digit code flow ────────────

_RESET_CODES = {}  # fallback: in-memory (single worker)

#: How long a reset code lives. The mail tells the reader this in minutes
#: (`email_bodies.reset_code`), derived from this one value — a sentence that says ten
#: minutes over a fifteen-minute expiry is a lie the reader cannot check, and the two
#: numbers drifting is exactly what a second constant would cause.
RESET_CODE_TTL_SECONDS = 600


def _store_reset_code(email: str, code: str, ttl: int = RESET_CODE_TTL_SECONDS):
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


def _send_email(to_email: str, subject: str, body: str, html: bool = False,
                text: str | None = None, important: bool = False) -> bool:
    """Send email via SMTP (scangrade9@gmail.com). ``True`` when it went out.

    It used to return ``None`` — and, on a box with no ``SMTP_PASSWORD``, log
    `SMTP not configured — email not sent` and return from *inside* the caller's
    `try`, which no `except` can see. The visitor was then shown the "enter your
    6-character code" page for a code that was never sent, and the only place
    that knew otherwise was a log line on a server they cannot read. A relay that
    answers 200 while sending nothing is the one failure a visitor cannot tell
    from success, so the answer is a bool the caller has to look at.
    """
    from app.services import smtp_settings

    # One resolver, so a credential set in the admin panel actually reaches this
    # path. When this function read `config.SMTP_PASSWORD` directly it saw only the
    # environment, which on a hosted box is an empty `SMTP_PASSWORD=` line — the
    # reset email then failed no matter what the operator had configured.
    ok, error = smtp_settings.send(to_email, subject, body, html=html, text=text,
                                   important=important)
    if not ok:
        current_app.logger.warning(
            "Could not send to %s: %s", to_email, error or "not configured")
    return ok


@auth_bp.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    if request.method == "GET":
        return render_template("auth/forgot_password.html")

    email = request.form.get("email", "").strip().lower()
    if not email:
        return render_template("auth/forgot_password.html", error=auth_error("forgot_required"))

    # Per-ACCOUNT throttle (the email/NISN itself). This endpoint sends mail, so
    # it needs a real limit — but keyed to the account, so a class of students
    # behind one school IP can each request their own code.
    allowed, retry = check_account_limit("forgot_password", email)
    if not allowed:
        return render_template("auth/forgot_password.html", error=rate_limit_error("code_request", retry))

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
            au = get_auth_admin().get_user_by_id(prof["id"])
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
                    au = get_auth_admin().get_user_by_id(rec["id"])
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

    # 3. Search the profile mirror by the address itself. `profiles.email`
    # (migration 040) is a derived copy of the auth address readable in one scoped
    # query, and it is the only lookup that covers *every* role: a principal or a
    # vice principal has no `students`/`teachers` row, so before this the two
    # oversight roles could reset only by typing their auth address — which the
    # school hands out on a card and nobody memorises.
    if not user_data:
        try:
            p = row_or_none(
                supabase.table("profiles").select("id, phone, full_name, role")
                .eq("email", email).maybe_single().execute()
            )
            if p:
                target_email = email
                user_data = {
                    "auth_email": email,
                    "recovery_email": email,
                    "user_id": p["id"],
                    "role": p.get("role", "murid"),
                    "full_name": p.get("full_name", ""),
                }
        except Exception:
            pass

    # 4. Search by auth email directly (the paged walk — kept as the last resort)
    if not user_data:
        u = find_auth_user_by_email(email)
        if u:
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

    if not user_data:
        return render_template("auth/forgot_password.html", error=auth_error("forgot_not_found"))

    # Generate 6-digit code (uppercase + digits)
    import random, string
    code = "".join(random.choices(string.ascii_uppercase + string.digits, k=6))
    _store_reset_code(target_email, code)

    # Send code via SMTP. The stored code is left to expire on its own (10 minutes)
    # when this fails, so a retry is a normal new request rather than a recovery.
    name = user_data.get("full_name", "Pengguna")
    sent = False
    try:
        # One body, from `app/services/email_bodies.py`: bilingual, escaped, and a
        # plain-text part beside the HTML one. The reset mail used to be a bare string
        # with the code framed in box-drawing characters — legible in a terminal and
        # monospaced garbage in every mail client.
        from app.services import email_bodies

        mail = email_bodies.reset_code(name=name, code=code,
                                       minutes=RESET_CODE_TTL_SECONDS // 60)
        # `important=True`: a one-time credential is urgent to its reader, and the
        # header is the half of deliverability a body cannot carry.
        sent = _send_email(target_email, mail["subject"], mail["html"],
                           html=True, text=mail["text"], important=True)
    except Exception as e:
        current_app.logger.error(f"Failed to send reset code: {e}")
        # Recorded here because this attempt never reached the sender: the body could
        # not be built, so `smtp_settings.send` was never called and the ledger would
        # otherwise show the *previous* outcome — or nothing at all — while the visitor
        # is told their code could not be emailed. This is the one failure an operator
        # could not see anywhere, and it matters most on a fresh box with no history.
        from app.services import mail_ledger
        mail_ledger.record(state=mail_ledger.STATE_FAILED, to=target_email,
                           subject="password reset code",
                           detail=f"the message could not be built: {e}")
        sent = False
    if not sent:
        return render_template("auth/forgot_password.html", error=auth_error("forgot_email_failed"))

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
        return render_template("auth/verify_code.html", email=email, error=auth_error("code_required"))

    # Per-ACCOUNT throttle (not per-IP): caps brute-forcing one account's reset
    # code while letting a whole class verify their own codes from a shared IP.
    allowed, retry = check_account_limit("verify_code", email)
    if not allowed:
        return render_template("auth/verify_code.html", email=email,
                               error=rate_limit_error("code_trial", retry))

    stored = _get_reset_code(email)
    if not stored:
        return render_template("auth/verify_code.html", email=email, error=auth_error("code_invalid"))

    if stored != code:
        return render_template("auth/verify_code.html", email=email, error=auth_error("code_wrong"))

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
        return render_template("auth/set_new_password.html", email=email, error=auth_error("all_required"))
    if password != confirm:
        return render_template("auth/set_new_password.html", email=email, error=auth_error("password_mismatch"))
    if len(password) < 6:
        return render_template("auth/set_new_password.html", email=email, error=auth_error("password_short"))

    # Verify session marker (code was consumed during verification)
    session_email = session.get("reset_email", "")
    if not session_email or session_email != email:
        return render_template("auth/set_new_password.html", email=email, error=auth_error("reset_session_expired"))

    # Find user and update password
    supabase = get_supabase()
    user_id = None
    role = "murid"

    try:
        # Find by recovery email (phone)
        prof = row_or_none(
            supabase.table("profiles").select("id, role")
            .eq("phone", email).maybe_single().execute()
        )
        if not prof:
            # Find by auth email. Scoped to an id here — a full search needs the
            # service key and the paged walk, not a single page of the listing.
            u = find_auth_user_by_email(email)
            if u:
                p2 = row_or_none(
                    supabase.table("profiles").select("role")
                    .eq("id", u.id).maybe_single().execute()
                )
                user_id = u.id
                role = p2.get("role", "murid") if p2 else "murid"
        else:
            user_id = prof["id"]
            role = prof.get("role", "murid")
    except Exception:
        pass

    if not user_id:
        return render_template("auth/set_new_password.html", email=email, error=auth_error("reset_user_missing"))

    try:
        get_auth_admin().update_user_by_id(user_id, {"password": password})
        session.pop("reset_email", None)

        # A reset **is** a password change, so it is recorded as one. Without this the
        # account stays marked as carrying a password the school printed, and the gate
        # sends the reader to /auth/change-password the moment they sign in with the
        # password they have just chosen on this page — the one-time rule firing on the
        # wrong sentence — while `password_changed_at` stays NULL for exactly the
        # accounts whose owners replaced their own. Best-effort and logged: the password
        # is the reset, and bookkeeping must never report a completed reset as failed.
        _fields, record_change = password_change_record(user_id)
        try:
            record_change(get_supabase())
        except Exception as e:
            current_app.logger.error(
                f"reset-password record not written for {user_id}: {e}")

        # One mapping for every role, including the two officials — a third copy of
        # these URLs is a third place a new role is forgotten. An unknown role gets
        # the admin door, which is what `dashboard_for` answers with no match and
        # the same page `login_door_for` names when it has no role to go on.
        redirect_url = dashboard_for(role)
        return render_template("auth/reset_success.html", redirect_url=redirect_url,
                               login_door=login_door_for(role), role=role)
    except Exception as e:
        current_app.logger.error(f"Reset password error: {e}")
        return render_template("auth/set_new_password.html", email=email, error=auth_error("reset_failed"))


# ─── RESET PASSWORD ──────────────────────────────────

@auth_bp.route("/reset-password", methods=["GET", "POST"])
def reset_password():
    if request.method == "GET":
        return render_template("auth/reset_password.html")

    password = request.form.get("password", "")
    confirm = request.form.get("confirm_password", "")

    if not password or not confirm:
        return render_template("auth/reset_password.html", error=auth_error("all_required"))

    if password != confirm:
        return render_template("auth/reset_password.html", error=auth_error("password_mismatch"))

    if len(password) < 6:
        return render_template("auth/reset_password.html", error=auth_error("password_short"))

    access_token = request.form.get("access_token", "") or request.args.get("access_token", "")

    if not access_token:
        return render_template("auth/reset_password.html", error=auth_error("reset_token_missing"))

    supabase = get_auth_client()
    try:
        supabase.auth.set_session(access_token, "")
        supabase.auth.update_user({"password": password})
        return render_template("auth/reset_password_success.html")
    except Exception as e:
        current_app.logger.error(f"Reset password error: {e}")
        return render_template("auth/reset_password.html", error=auth_error("reset_token_expired"))


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
    token = _extract_token()
    # Read the door from the token's own session, *before* the session is dropped.
    # Not from `g.user_role`: no hook fills it on this route (only
    # `login_required` does, and logout must keep working for a session that has
    # already expired), so reading it here answered None for everyone and every
    # role was sent to the admin door. See `session_role` in app/utils/auth.py.
    door = login_door_for(session_role(token), request.path)
    # Drop the cached session before anything else, so the token stops working
    # right away instead of remaining valid for the remainder of the cache TTL.
    invalidate_session(token)
    try:
        supabase = get_auth_client()
        supabase.auth.sign_out()
    except Exception:
        pass
    if uid:
        log_activity("logout", "user", uid)
    resp = make_response(redirect(door))
    resp.delete_cookie("access_token", path="/")
    resp.delete_cookie("refresh_token", path="/")
    return resp


# ─── CHANGE THE PASSWORD A SCHOOL PRINTED ─────────────

@auth_bp.route("/change-password", methods=["GET", "POST"])
@login_required
def change_password():
    """The page a login card's one-time password lands on.

    `login_required` lets this path through while the flag is set (it is on the
    exempt list), which is what keeps the redirect from looping. The two things
    that must not be skippable are both here: the **current** password has to be
    proven, because a session left open on a shared laptop is otherwise enough to
    take the account over; and the new password is written by the admin API, the
    only door this app has ever had to a credential.

    On success the session is deliberately **destroyed** and the reader is sent to
    their own login door: the whole point of a one-time password is that the next
    sign-in uses the new one, and nothing else in this app can prove the new
    password works. The cached session is invalidated first, so the old token stops
    working immediately rather than for the remainder of its TTL.

    The two writes are deliberately **not** one try block. The admin API writes the
    password; the profile record says the password changed. A database that cannot
    hold the record (migration 040 not yet applied) must not turn a password that has
    already been replaced into an error page — see `password_change_record` in
    `app/utils/auth.py`, which both this page and the by-code reset share.
    """
    from app.services import password_change

    if request.method == "GET":
        return render_template("auth/change_password.html")

    current = request.form.get("current_password", "")
    new = request.form.get("new_password", "")
    confirm = request.form.get("confirm_password", "")

    problem = password_change.change_problem(current, new, confirm)
    if problem:
        return render_template("auth/change_password.html",
                               error=auth_error(problem))

    # Prove the current password rather than trusting the session. A wrong one is
    # reported as such, not as a server fault: the reader can act on the first and
    # cannot act on the second.
    try:
        get_auth_client().auth.sign_in_with_password(
            {"email": g.user_email, "password": current})
    except Exception:
        log_activity("change_password_failed", "user", g.user_id,
                     new_data={"reason": "current_password"})
        return render_template("auth/change_password.html",
                               error=auth_error("change_current_wrong"))

    try:
        get_supabase().auth.admin.update_user_by_id(g.user_id, {"password": new})
    except Exception as e:
        current_app.logger.error(f"change-password failed for {g.user_id}: {e}")
        log_activity("change_password_failed", "user", g.user_id)
        return render_template("auth/change_password.html",
                               error=auth_error("change_not_saved"))

    # The password is the change; the record only *says* it happened, so a profile write
    # that cannot be made must not report the change as lost. Reported live: on a
    # database where migration 040 is not applied, PostgREST refuses the update
    # (``PGRST204``) naming a column that does not exist — the refusal happened *after*
    # the admin API had already replaced the password, and the reader was shown "not
    # saved" for a change that had landed. ``password_change_record`` holds both the
    # fields and that reasoning; this page is the reader's only door, so here a real
    # failure to record is still reported.
    _fields, record_change = password_change_record(g.user_id)
    try:
        record_change(get_supabase())
    except Exception as e:
        current_app.logger.error(f"change-password flag not cleared for {g.user_id}: {e}")
        log_activity("change_password_failed", "user", g.user_id)
        return render_template("auth/change_password.html",
                               error=auth_error("change_not_saved"))

    door = login_door_for(g.user_role, request.path)
    log_activity("change_password", "user", g.user_id)
    flash(auth_error("changed_sign_in_again"), "success")
    # Same teardown as /auth/logout, in the same order, for the same reason: the
    # cached session goes first so the old token cannot ride its TTL.
    invalidate_session(_extract_token())
    try:
        get_auth_client().auth.sign_out()
    except Exception:
        pass
    resp = make_response(redirect(door))
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
