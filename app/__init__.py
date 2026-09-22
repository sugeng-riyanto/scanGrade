import os
import logging
import math as _math
import time
from datetime import datetime, timedelta, timezone
import click
from flask import Flask, g, request, jsonify, redirect, render_template, make_response
from flask_cors import CORS
from supabase import create_client, Client
from app.utils import query_meter
from app.utils.auth import login_required, super_admin_required
from app.utils.supabase_retry import RetryingClient

from app.config import get_config

DEFAULT_TZ_OFFSET = 7

# Session timeout by role (OWASP + UU PDP standard)
SESSION_TIMEOUTS = {
    "super_admin":    {"idle_minutes": 15,  "absolute_hours": 4},
    "admin_sekolah":  {"idle_minutes": 30,  "absolute_hours": 8},
    "guru":           {"idle_minutes": 60,  "absolute_hours": 12},
    "murid":          {"idle_minutes": 120, "absolute_hours": 24},
}
DEFAULT_SESSION_TIMEOUT = {"idle_minutes": 30, "absolute_hours": 8}

_lru_cache = {}
_lru_cache_ttl = {}
_lru_max = 256


def cache_get(key, ttl=60):
    now = time.time()
    if key in _lru_cache and now - _lru_cache_ttl.get(key, 0) < ttl:
        return _lru_cache[key]
    return None


def cache_set(key, value):
    if len(_lru_cache) >= _lru_max:
        oldest = min(_lru_cache_ttl, key=_lru_cache_ttl.get)
        _lru_cache.pop(oldest, None)
        _lru_cache_ttl.pop(oldest, None)
    _lru_cache[key] = value
    _lru_cache_ttl[key] = time.time()


def create_app(env=None):
    app = Flask(__name__)
    cfg = get_config(env)
    app.config.from_object(cfg)
    cfg.validate()

    # Behind nginx every request arrives from the proxy's address, so without
    # this the client IP is 127.0.0.1 for everyone. That silently breaks per-IP
    # rate limiting (all users share one bucket), audit logging, and anti-cheat
    # device-mismatch detection. Only enabled when the config declares a trusted
    # proxy, so a directly-reachable instance can't have X-Forwarded-For spoofed.
    _proxy_hops = int(app.config.get("TRUSTED_PROXY_HOPS", 0) or 0)
    if _proxy_hops > 0:
        from werkzeug.middleware.proxy_fix import ProxyFix
        app.wsgi_app = ProxyFix(
            app.wsgi_app,
            x_for=_proxy_hops,
            x_proto=_proxy_hops,
            x_host=_proxy_hops,
            x_port=_proxy_hops,
        )
        app.logger.info("ProxyFix enabled (trusting %d proxy hop(s) of X-Forwarded-For)", _proxy_hops)

    # Structured logging
    from app.utils.logger import setup_logging
    setup_logging(app)

    # Sentry initialization
    if cfg.SENTRY_DSN:
        try:
            import sentry_sdk
            from sentry_sdk.integrations.flask import FlaskIntegration
            sentry_sdk.init(
                dsn=cfg.SENTRY_DSN,
                environment=cfg.SENTRY_ENVIRONMENT,
                integrations=[FlaskIntegration()],
                traces_sample_rate=0.1,
            )
            sentry_sdk.set_tag("app", "scangrade")
            sentry_sdk.set_tag("version", cfg.APP_VERSION)
            app.logger.info("Sentry initialized for %s", cfg.SENTRY_ENVIRONMENT)
        except Exception as e:
            app.logger.warning("Sentry init failed: %s", e)

    # Flask-Limiter (rate limiting ΓÇö uses memory:// by default, Redis if explicitly configured)
    try:
        from flask_limiter import Limiter
        from flask_limiter.util import get_remote_address
        from app.utils import rate_limiter as rl_module

        # The storage, decided in this order: a config that *names* it (a test
        # config names "memory://") is obeyed outright and the environment is not
        # consulted — otherwise `REDIS_URL` from the checkout's own `.env` overrode
        # the test config's explicit "" and every construct spent four seconds
        # failing to reach a Redis the tests never use. Otherwise the configured
        # Redis is used when it answers, and memory:// when it does not.
        named = getattr(cfg, "RATELIMIT_STORAGE_URI", "")
        redis_url = ""
        if named:
            storage_uri = named
        else:
            redis_url = cfg.REDIS_URL or os.environ.get("REDIS_URL", "")
            storage_uri = redis_url or "memory://"
            if redis_url:
                try:
                    from redis import Redis
                    r = Redis.from_url(redis_url, socket_connect_timeout=3, socket_timeout=5)
                    r.ping()
                    storage_uri = redis_url
                    r.close()
                    app.logger.info("Redis connected for Flask-Limiter")
                except Exception as e:
                    storage_uri = "memory://"
                    app.logger.warning("Redis not available for Flask-Limiter (%s) — falling back to memory://", e)

        limiter = Limiter(
            app=app,
            key_func=get_remote_address,
            storage_uri=storage_uri,
            default_limits=[],
        )
        app.extensions["limiter"] = limiter
        # Adopted rather than assigned: the views decorated at import time hold only
        # a weak proxy to a limiter, so replacing this without keeping the old one
        # alive leaves every one of them dead on the next request (see
        # rate_limiter._limiters).
        rl_module.remember_limiter(limiter)

        app.config["RATELIMIT_ENABLED"] = True
        app.config["REDIS_URL"] = redis_url
        app.logger.info("Flask-Limiter initialized (storage: %s)", storage_uri.split("@")[-1] if "@" in storage_uri else storage_uri)
    except ImportError:
        app.logger.info("Flask-Limiter not installed")
    except Exception as e:
        app.logger.warning("Flask-Limiter init failed: %s", e)

    CORS(
        app,
        origins=[
            "http://localhost:5000",
            "http://localhost:3000",
            f"https://{cfg.NGROK_DOMAIN}" if cfg.NGROK_DOMAIN else "",
        ],
        supports_credentials=True,
    )

    supabase: Client = create_client(cfg.SUPABASE_URL, cfg.SUPABASE_SERVICE_KEY)
    # Wrapped, so a keep-alive connection the server has already closed is retried
    # instead of reaching a view as `Server disconnected` — see app/utils/supabase_retry.py.
    app.extensions["supabase"] = RetryingClient(supabase)
    app.extensions["supabase_auth"] = create_client(cfg.SUPABASE_URL, cfg.SUPABASE_ANON_KEY)

    _register_blueprints(app)
    _register_legacy_admin_urls(app)
    _register_error_handlers(app)
    _register_request_logging(app)
    _register_performance_middleware(app)
    _register_rate_limiter(app)
    _register_csrf_protection(app)

    import json as _json

    @app.template_filter("from_json")
    def from_json_filter(val):
        if val is None:
            return None
        if isinstance(val, (dict, list)):
            return val
        if isinstance(val, str):
            try:
                return _json.loads(val)
            except (TypeError, ValueError):
                return None
        return val

    @app.template_filter("tz")
    def tz_format_filter(val, fmt="%d %b %Y %H:%M"):
        if not val:
            return "-"
        try:
            if isinstance(val, str):
                for fmt_in in ("%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
                    try:
                        dt = datetime.strptime(val, fmt_in)
                        break
                    except ValueError:
                        continue
                else:
                    return val[:16].replace("T", " ")
            elif isinstance(val, datetime):
                dt = val
            else:
                return str(val)
            offset = getattr(g, "tz_offset", DEFAULT_TZ_OFFSET)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            dt = dt.astimezone(timezone(timedelta(hours=offset)))
            return dt.strftime(fmt)
        except Exception:
            return str(val)[:16] if val else "-"

    @app.template_filter("tz_short")
    def tz_short_filter(val):
        offset = getattr(g, "tz_offset", DEFAULT_TZ_OFFSET)
        sign = "+" if offset >= 0 else ""
        return f"UTC{sign}{offset}"

    app.jinja_env.globals["cos"] = _math.cos
    app.jinja_env.globals["sin"] = _math.sin

    from app.utils.csrf import generate_csrf_token, csrf_required
    from app.utils.auth import login_required
    app.jinja_env.globals["csrf_token"] = generate_csrf_token

    def get_demo_settings():
        """Return demo settings dict, cached per request via g."""
        from flask import g as flask_g
        if hasattr(flask_g, '_demo_settings'):
            return flask_g._demo_settings
        try:
            supabase = app.extensions["supabase"]
            data = supabase.table("school_settings").select("demo_settings").eq("id", 1).single().execute().data or {}
            result = data.get("demo_settings") or {}
        except Exception:
            result = {}
        flask_g._demo_settings = result
        return result
    app.jinja_env.globals["get_demo_settings"] = get_demo_settings

    # Which demo items a page renders, in the order the super admin arranged
    # them. Exposed as one global so `/demo` and the landing page cannot read the
    # blob differently — see `app/services/demo_settings.py` for why that matters.
    from app.services.demo_settings import (
        demo_items as _demo_items,
        demo_link_on as _demo_link_on,
        demo_order as _demo_order,
    )
    app.jinja_env.globals["demo_items"] = _demo_items
    # The settings page needs the *unfiltered* order, because it is where a
    # switched-off item is switched back on — `demo_items` would hide its own row.
    app.jinja_env.globals["demo_order"] = _demo_order
    # The `Demo` link has no row of its own; it follows the master toggle.
    app.jinja_env.globals["demo_link_on"] = _demo_link_on

    def get_whatsapp_number():
        from flask import g as flask_g
        if hasattr(flask_g, '_whatsapp_number'):
            return flask_g._whatsapp_number
        try:
            supabase = app.extensions["supabase"]
            data = supabase.table("school_settings").select("whatsapp_number").eq("id", 1).single().execute().data or {}
            result = data.get("whatsapp_number", "")
        except Exception:
            result = ""
        flask_g._whatsapp_number = result
        return result
    app.jinja_env.globals["get_whatsapp_number"] = get_whatsapp_number

    def get_school_features(school_id=None):
        """Feature flags for a school (whiteboard_enabled, …).

        A shared row, not a per-request one: it is identical for every student
        and teacher in the school, and a student page reads it on every load, so
        a school was paying one round-trip per page view for the same JSON.

        The dict this replaced was keyed by `id(request)` and never cleared — it
        grew for the life of the worker, and CPython reusing a request object's
        address could have served a request another request's features.
        """
        from app.utils.req_cache import school_features
        sid = school_id or getattr(g, "user_school_id", None)
        return school_features(sid) if sid else {}
    app.jinja_env.globals["get_school_features"] = get_school_features

    # Stylesheet URLs carry a content hash. nginx serves /static/ with
    # `immutable, max-age=31536000`, which is right for a phone on a weak
    # signal — but a stylesheet that keeps its URL is then never re-fetched, so
    # an edit to it would reach only browsers that had never loaded it. See
    # app/utils/asset_version.py.
    from app.utils.asset_version import asset_v
    app.jinja_env.globals["asset_v"] = asset_v

    # Question types, so a template branches on the *family* of a question rather
    # than on a comparison with the one type it was written for. That comparison is
    # how a page decides a new objective type is an essay — it is not the type the
    # page knows, so it must be an essay — and then renders a canvas for a
    # true/false question and shows the pupil no way to answer it. The names live in
    # app/services/question_types.py; these are the four doors a page needs (see the
    # module docstring).
    from app.services.question_types import (
        describe_answer, grade_answer, is_essay, is_objective, part_factor,
        partial_credit, public_options, question_kind, vocabulary,
    )
    # A report used to decide "is this answer right" with its own comparison of
    # letters, which cannot express a matching answer at all and marks every one
    # of them wrong. The grader is the same object the score comes from.
    app.jinja_env.globals["q_correct"] = grade_answer
    app.jinja_env.globals["q_is_objective"] = is_objective
    app.jinja_env.globals["q_is_essay"] = is_essay
    app.jinja_env.globals["q_kind"] = question_kind
    app.jinja_env.globals["q_answer_text"] = describe_answer
    app.jinja_env.globals["q_public_options"] = public_options
    # The mark scheme, for a page that shows what a question earned. These are the
    # same functions the score is computed with, so a report cannot show a
    # different number from the one it was added up from — which is exactly what a
    # second implementation here would eventually do.
    app.jinja_env.globals["q_part_factor"] = part_factor
    app.jinja_env.globals["q_partial_credit"] = partial_credit
    # The pages that classify a question in JavaScript get the names from here
    # rather than writing their own list, which is how a Python change and a
    # JavaScript change stop meaning the same thing.
    app.jinja_env.globals["q_vocabulary"] = vocabulary
    # The measurement vocabulary — the four education frameworks and the cognitive
    # levels a kisi-kisi is written in. A page that offers "which analysis?" and a
    # page that lets a teacher label a question must offer the same four and the
    # same six, so both read them from app/services/analysis_frameworks.py rather
    # than each writing a list that drifts from the other.
    from app.services.analysis_frameworks import (
        bands_payload, catalogue, levels_payload,
    )
    app.jinja_env.globals["frameworks_catalogue"] = catalogue
    app.jinja_env.globals["cognitive_levels"] = levels_payload
    app.jinja_env.globals["cognitive_bands"] = bands_payload

    def q_any_essay(types):
        """Does this exam contain a question a teacher has to mark by hand?

        A page asking "does this exam have essays" used to ask it with a list of
        the three essay names, which is a list that stops being right the moment a
        fourth exists — and reads as an essay icon for a true/false question.
        """
        return any(is_essay(value) for value in (types or []))

    app.jinja_env.globals["q_any_essay"] = q_any_essay

    @app.template_global()
    def school_favicon(school_info=None):
        """Generate a simple SVG favicon from school initials or default."""
        if school_info and school_info.get("logo_url"):
            return school_info["logo_url"]
        name = (school_info or {}).get("name", "SG")
        initials = "".join(w[0] for w in name.split()[:2]).upper()[:2] if len(name.split()) > 1 else name[:2].upper()
        color = "#4338CA"
        return f"data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'><rect width='32' height='32' rx='6' fill='{color}'/><text x='16' y='22' text-anchor='middle' font-size='16' font-weight='bold' fill='white'>{initials}</text></svg>"

    @app.template_global()
    def greeting():
        offset = getattr(g, "tz_offset", DEFAULT_TZ_OFFSET)
        from datetime import timedelta, timezone as _tz
        now = datetime.now(_tz(timedelta(hours=offset)))
        h = now.hour
        if 5 <= h < 11:
            return "pagi"
        elif 11 <= h < 15:
            return "siang"
        elif 15 <= h < 18:
            return "sore"
        else:
            return "malam"

    @app.template_global()
    def greeting_en():
        offset = getattr(g, "tz_offset", DEFAULT_TZ_OFFSET)
        from datetime import timedelta, timezone as _tz
        now = datetime.now(_tz(timedelta(hours=offset)))
        h = now.hour
        if 5 <= h < 11:
            return "morning"
        elif 11 <= h < 15:
            return "afternoon"
        elif 15 <= h < 18:
            return "evening"
        else:
            return "evening"

    @app.route("/")
    def index():
        token = request.cookies.get("access_token")
        if token:
            try:
                user = app.extensions["supabase_auth"].auth.get_user(token)
                from app.utils.auth import get_supabase
                db = get_supabase()
                try:
                    profile = db.table("profiles").select("role").eq("id", user.user.id).single().execute()
                    role = profile.data.get("role", "murid")
                except Exception:
                    role = user.user.user_metadata.get("role", "murid")
                redirect_map = {"super_admin": "/super-admin/dashboard", "admin_sekolah": "/admin-sekolah/dashboard", "guru": "/teacher/dashboard", "murid": "/student/dashboard"}
                return redirect(redirect_map.get(role, "/student/dashboard"))
            except Exception:
                pass
        # No language argument: English is base.html's default for every page, so
        # asking for it here would be a second source of truth — and the one that
        # could silently disagree. The toggle writes `sg_lang`, so a visitor who
        # picks Indonesian keeps it across the whole app.
        resp = make_response(render_template("landing.html"))
        resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        resp.headers["Pragma"] = "no-cache"
        return resp

    @app.route("/demo")
    def demo_page():
        return render_template("demo.html")

    @app.route("/tutorial/guru")
    def tutorial_guru():
        return render_template("tutorial_guru.html")

    @app.route("/tutorial/admin-sekolah")
    def tutorial_admin():
        return render_template("tutorial_admin_sekolah.html")

    @app.route("/tutorial/murid")
    def tutorial_murid():
        # Default anti-cheat values (dapat diubah admin di pengaturan sekolah)
        ac = {
            "penalty_per_violation": 5,
            "max_violations": 5,
            "auto_submit_on_max": True,
        }
        try:
            supabase = app.extensions["supabase"]
            exam_sample = supabase.table("exams").select("penalty_per_violation,max_violations,auto_submit_on_max").limit(1).execute()
            if exam_sample.data:
                ac.update({k: v for k, v in exam_sample.data[0].items() if v is not None})
        except Exception:
            pass
        return render_template("tutorial_murid.html", ac=ac)

    @app.route("/health")
    def health():
        from app.utils.rate_limiter import get_redis_status
        redis_info = get_redis_status()
        return jsonify({
            "status": "ok",
            "supabase": "connected",
            "redis": redis_info,
            "workers": os.environ.get("GUNICORN_WORKERS", "1"),
            "cache_size": len(_lru_cache),
            "uptime_ms": int((time.time() - app._start_time) * 1000) if hasattr(app, '_start_time') else 0,
        })

    @app.route("/monitor")
    @login_required
    def monitor_page():
        """Simple server monitoring page ΓÇö reads /var/log/scangrade-monitor.log."""
        log_path = "/var/log/scangrade-monitor.log"
        lines = []
        if os.path.exists(log_path):
            try:
                with open(log_path, "r") as f:
                    raw = f.readlines()
                lines = [l.strip() for l in raw if l.strip() and "ALERT" not in l][-200:]
            except Exception:
                pass
        return render_template("monitor.html", log_lines=lines)

    @app.route("/debug/exam/<exam_id>")
    @login_required
    def debug_exam(exam_id):
        try:
            supabase = app.extensions["supabase"]
            exam = supabase.table("exams").select("id,title,status,is_published,school_id,teacher_id,class_ids,start_at").eq("id", exam_id).single().execute().data
            if not exam:
                return jsonify({"error": "not found"}), 404
            teacher_id = exam.get("teacher_id", "")
            school_id = exam.get("school_id", "")
            if g.user_id != teacher_id and g.get("user_school_id") != school_id and g.get("user_role") not in ("admin_sekolah", "super_admin"):
                return jsonify({"error": "forbidden"}), 403
            return jsonify({k: str(v) if not isinstance(v, (bool, int, float, list, dict)) and v is not None else v for k, v in exam.items()})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    app._start_time = time.time()

    # Both loops run a pass as soon as they start, and the retention one purges.
    # Constructing the app must not mutate data, so callers that only need an app
    # object (the test suite, the deploy smoke test) turn this off.
    if app.config.get("START_BACKGROUND_SCHEDULERS", True):
        # Start background cleanup scheduler
        try:
            from app.services.cleanup_service import start_cleanup_scheduler
            start_cleanup_scheduler(interval=1800)
        except Exception as e:
            app.logger.warning("Failed to start cleanup scheduler: %s", e)

        # Start data retention scheduler (daily purge)
        try:
            from app.services.data_retention_service import start_retention_scheduler
            # Pass the app so the purge loop can push an application context.
            start_retention_scheduler(interval=86400, app=app)
        except Exception as e:
            app.logger.warning("Failed to start retention scheduler: %s", e)

    # CLI commands
    @app.cli.command("purge-data")
    def purge_data_command():
        """Manual trigger for data retention purge (soft-delete old records)."""
        from app.services.data_retention_service import purge_all
        result = purge_all()
        click.echo(f"Purge complete: {result}")

    @app.cli.command("export-user")
    @click.argument("user_id")
    def export_user_command(user_id):
        """Export all data for a given user_id."""
        from app.services.data_retention_service import export_user_data
        import json
        data = export_user_data(user_id)
        click.echo(json.dumps(data, indent=2, default=str))

    return app


def _register_legacy_admin_urls(app):
    """One rule per moved admin URL: a 308 to the page that owns it now.

    Deliberately unguarded. Where a URL lives is not a permission, and the page
    it lands on carries its own guard — which also makes the redirect correct for
    whichever role follows it: a school admin sent to `/super-admin/dashboard` is
    bounced on to `/admin-sekolah/dashboard` by `role_required`, the same place
    their own bookmark would have taken them.

    The query string is carried over. A permanent redirect that silently drops
    `?page=3` lands the reader on a page that looks fine and is the wrong one.
    """
    from app.utils.legacy_urls import LEGACY_ADMIN_PAGES, legacy_endpoint

    def _mover(target):
        def move():
            query = request.query_string.decode()
            return redirect(target + ("?" + query if query else ""), code=308)
        return move

    for old, new in LEGACY_ADMIN_PAGES.items():
        app.add_url_rule(old, legacy_endpoint(old), _mover(new), methods=["GET"])


def _register_blueprints(app):
    from app.routes.auth import auth_bp
    from app.routes.exam import exam_bp
    from app.routes.admin import admin_bp
    from app.routes.teacher import teacher_bp
    from app.routes.student import student_bp
    from app.routes.api import api_bp
    from app.routes.publish import publish_bp
    from app.routes.webhook import webhook_bp
    from app.routes.admin_sekolah import admin_sekolah_bp
    from app.routes.tools import tools_bp
    from app.routes.super_admin import super_bp
    from app.routes.public import public_bp
    from app.routes.guide import guide_bp
    from app.routes.students import student_bp as students_bp

    app.register_blueprint(super_bp)
    app.register_blueprint(public_bp)
    app.register_blueprint(guide_bp, url_prefix="/guide")
    app.register_blueprint(students_bp)
    app.register_blueprint(auth_bp, url_prefix="/auth")
    app.register_blueprint(exam_bp, url_prefix="/exam")
    app.register_blueprint(admin_bp, url_prefix="/admin")
    app.register_blueprint(teacher_bp, url_prefix="/teacher")
    app.register_blueprint(student_bp, url_prefix="/student")
    app.register_blueprint(api_bp, url_prefix="/api")
    app.register_blueprint(publish_bp, url_prefix="/publish")
    app.register_blueprint(webhook_bp, url_prefix="/webhook")
    app.register_blueprint(admin_sekolah_bp, url_prefix="/admin-sekolah")
    app.register_blueprint(tools_bp, url_prefix="/tools")

    # Whiteboard blueprints (registered but heavy imports deferred)
    try:
        from app.routes.whiteboard_teacher import whiteboard_teacher_bp
        from app.routes.whiteboard_student import whiteboard_student_bp
        app.register_blueprint(whiteboard_teacher_bp, url_prefix="/wb/teacher")
        app.register_blueprint(whiteboard_student_bp, url_prefix="/wb/student")
    except Exception:
        pass


def _register_error_handlers(app):
    from app.handlers.error_handlers import register_error_handlers
    register_error_handlers(app)


def _register_rate_limiter(app):
    from app.utils.rate_limiter import get_rate_limiter
    get_rate_limiter(app)


def _register_csrf_protection(app):
    """Global CSRF protection — validates token on all state-changing requests.

    Exemptions:
    - Bearer token auth (Authorization: Bearer ...) — API keys are CSRF-safe
    - Webhook endpoints — external callbacks with their own auth
    - Public read-only endpoints
    - LOAD_TEST mode (existing bypass)
    """
    from app.utils.csrf import validate_csrf
    from flask import session as _session

    @app.before_request
    def csrf_protect():
        if request.method not in ('POST', 'PUT', 'DELETE', 'PATCH'):
            return None
        # Bearer token auth is CSRF-safe (requires preflight)
        auth = request.headers.get('Authorization', '')
        if auth.startswith('Bearer '):
            return None
        # Webhook endpoints have their own signature verification
        if request.path.startswith('/webhook/'):
            return None
        # Public endpoints (no auth, no state change)
        if request.path == '/api/public/privacy-info':
            return None
        # Validate CSRF token
        if not validate_csrf():
            # Logged loudly on purpose: a rejected write used to look exactly like a
            # silent no-op on the client. The anti-cheat event log went 403 on every
            # send for who knows how long and nothing surfaced it.
            app.logger.warning(
                "CSRF rejected: %s %s (accept=%r)",
                request.method, request.path, (request.headers.get("Accept") or "")[:40],
            )
            return jsonify({"error": "CSRF token invalid"}), 403
        return None

    @app.after_request
    def ensure_session_cookie(response):
        """Persist session cookie on every response so the CSRF token is available."""
        if _session.modified:
            response = app.make_response(response)
            app.session_interface.save_session(app, _session, response)
        return response


def _register_performance_middleware(app):
    @app.after_request
    def add_performance_headers(response):
        if hasattr(g, "start"):
            duration_ms = int((time.time() - g.start) * 1000)
            response.headers["X-Response-Time-ms"] = str(duration_ms)
        # How many Supabase round-trips this render spent. It rides beside the
        # response time because the two answer the same question from opposite
        # ends: response time is the symptom a student feels, round-trips are the
        # cause a release changes. The load harness reads this header per page and
        # deploy/perf_gate.py compares it with the last release that passed.
        roundtrips = query_meter.header_value()
        if roundtrips is not None:
            response.headers[query_meter.HEADER] = roundtrips
        if request.path.startswith("/static/"):
            # 7 days for static assets (CSS/JS/images/fonts)
            response.cache_control.max_age = 604800
            response.cache_control.public = True
            response.cache_control.immutable = True
        elif request.path.startswith("/api/") or request.path.startswith("/health"):
            response.cache_control.no_cache = True
        else:
            response.cache_control.no_cache = True
            response.cache_control.no_store = True
            response.cache_control.must_revalidate = True
        # Add security headers (PSE Kominfo & UU PDP compliance)
        if not response.headers.get("Content-Security-Policy"):
            csp = (
                "frame-ancestors 'self';"
            )
            response.headers["Content-Security-Policy"] = csp
        if not response.headers.get("X-Content-Type-Options"):
            response.headers["X-Content-Type-Options"] = "nosniff"
        if not response.headers.get("X-Frame-Options"):
            response.headers["X-Frame-Options"] = "SAMEORIGIN"
        return response


def _register_request_logging(app):
    # Metrics counters
    _metrics = {"requests": 0, "errors": 0, "response_times": []}
    _max_times = 1000  # keep last 1000 for stats

    @app.before_request
    def init_request():
        g.start = time.time()
        g.user_id = None
        # Armed here, before every other hook, so the count a page reports covers
        # the whole render. See app/utils/query_meter.py.
        query_meter.begin()
        try:
            g.tz_offset = int(request.cookies.get("tz_offset", str(DEFAULT_TZ_OFFSET)))
        except (ValueError, TypeError):
            g.tz_offset = DEFAULT_TZ_OFFSET

    @app.context_processor
    def inject_globals():
        return {"tz_offset": g.get("tz_offset", DEFAULT_TZ_OFFSET)}

    @app.after_request
    def log_request(response):
        if hasattr(g, "start"):
            duration = time.time() - g.start
            extra = {"user_id": g.get("user_id"), "duration": f"{duration:.3f}s"}
            app.logger.info("%s %s %s", request.method, request.path, response.status_code, extra=extra)
            _metrics["requests"] += 1
            if response.status_code >= 500:
                _metrics["errors"] += 1
            _metrics["response_times"].append(duration * 1000)
            if len(_metrics["response_times"]) > _max_times:
                _metrics["response_times"] = _metrics["response_times"][-500:]
        # Set refreshed access_token cookie if token was refreshed. Through the
        # shared helper so it carries Secure on HTTPS like the session cookie.
        new_token = getattr(g, "_new_access_token", None)
        if new_token:
            from app.utils.auth import set_auth_cookie
            set_auth_cookie(response, "access_token", new_token, max_age=86400)
        # Update last_activity timestamp for session timeout tracking
        if g.get("user_id"):
            from app.utils.auth import set_auth_cookie
            set_auth_cookie(response, "last_activity", str(time.time()), max_age=86400)
        return response

    @app.route("/metrics")
    @login_required
    def metrics():
        import psutil
        _metrics["active_users"] = len(_metrics.get("response_times", [])) or 0
        rt = _metrics["response_times"]
        p50 = sorted(rt)[len(rt)//2] if rt else 0
        p95 = sorted(rt)[int(len(rt)*0.95)] if rt else 0
        cpu = psutil.cpu_percent(interval=0.1)
        mem = psutil.virtual_memory()
        disk = psutil.disk_usage("/")
        return f"""# HELP scangrade_requests_total Total requests
# TYPE scangrade_requests_total counter
scangrade_requests_total {_metrics["requests"]}
# HELP scangrade_errors_total Total errors (5xx)
# TYPE scangrade_errors_total counter
scangrade_errors_total {_metrics["errors"]}
# HELP scangrade_response_time_ms Response time in ms
# TYPE scangrade_response_time_ms gauge
scangrade_response_time_p50_ms {p50}
scangrade_response_time_p95_ms {p95}
# HELP scangrade_cpu_percent CPU usage percent
# TYPE scangrade_cpu_percent gauge
scangrade_cpu_percent {cpu}
# HELP scangrade_memory_usage_bytes Memory usage
# TYPE scangrade_memory_usage_bytes gauge
scangrade_memory_used_bytes {mem.used}
scangrade_memory_total_bytes {mem.total}
scangrade_memory_percent {mem.percent}
# HELP scangrade_disk_usage_bytes Disk usage
# TYPE scangrade_disk_usage_bytes gauge
scangrade_disk_free_bytes {disk.free}
scangrade_disk_total_bytes {disk.total}
""", 200, {"Content-Type": "text/plain; charset=utf-8"}

    @app.route("/metrics/processes")
    @super_admin_required
    def metrics_processes():
        """Cumulative per-process CPU, so a box with no shell can still be attributed.

        `/metrics` answers the app's own questions; this answers the box's. It is a
        super-admin read rather than one every signed-in user may make, because a
        process inventory is not something a student needs.
        """
        from app.services.process_sampler import sample_text
        return sample_text(), 200, {"Content-Type": "text/plain; charset=utf-8"}

