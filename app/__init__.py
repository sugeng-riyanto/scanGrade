import logging
import math as _math
import time
from datetime import datetime, timedelta, timezone
from flask import Flask, g, request, jsonify, redirect, render_template
from flask_cors import CORS
from flask_socketio import SocketIO
from supabase import create_client, Client

from app.config import get_config

DEFAULT_TZ_OFFSET = 7

# SocketIO instance (module-level, init_app called in create_app)
socketio = SocketIO(async_mode='eventlet')

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

    # Flask-Limiter (rate limiting — uses memory:// by default, Redis if explicitly configured)
    try:
        from flask_limiter import Limiter
        from flask_limiter.util import get_remote_address
        from app.utils import rate_limiter as rl_module

        storage_uri = "memory://"
        if cfg.REDIS_URL:
            try:
                from redis import Redis
                r = Redis.from_url(cfg.REDIS_URL)
                r.ping()
                storage_uri = cfg.REDIS_URL
                r.close()
            except Exception:
                app.logger.warning("Redis not available, falling back to memory:// rate limiting")

        limiter = Limiter(
            app=app,
            key_func=get_remote_address,
            storage_uri=storage_uri,
            default_limits=[],
        )
        app.extensions["limiter"] = limiter
        rl_module.limiter = limiter

        app.config["RATELIMIT_ENABLED"] = True
        app.logger.info("Flask-Limiter initialized (storage: %s)", storage_uri)
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
    app.extensions["supabase"] = supabase
    app.extensions["supabase_auth"] = create_client(cfg.SUPABASE_URL, cfg.SUPABASE_ANON_KEY)

    # SocketIO initialization
    socketio.init_app(app, cors_allowed_origins="*")
    from app.routes.whiteboard_socket import register_socket_events
    register_socket_events(socketio)
    app.extensions["socketio"] = socketio

    _register_blueprints(app)
    _register_error_handlers(app)
    _register_request_logging(app)
    _register_performance_middleware(app)
    _register_rate_limiter(app)

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
    app.jinja_env.globals["csrf_token"] = generate_csrf_token

    # Demo settings global — readable from any template
    _demo_cache = {}
    def get_demo_settings():
        """Return demo settings dict, cached per request."""
        req_key = f"demo_{id(request)}"
        if req_key in _demo_cache:
            return _demo_cache[req_key]
        try:
            supabase = app.extensions["supabase"]
            data = supabase.table("school_settings").select("demo_settings").eq("id", 1).single().execute().data or {}
            result = data.get("demo_settings") or {}
        except Exception:
            result = {}
        _demo_cache[req_key] = result
        return result
    app.jinja_env.globals["get_demo_settings"] = get_demo_settings

    def get_whatsapp_number():
        try:
            supabase = app.extensions["supabase"]
            data = supabase.table("school_settings").select("whatsapp_number").eq("id", 1).single().execute().data or {}
            return data.get("whatsapp_number", "")
        except Exception:
            return ""
    app.jinja_env.globals["get_whatsapp_number"] = get_whatsapp_number

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
                redirect_map = {"super_admin": "/super-admin/dashboard", "admin_sekolah": "/admin/dashboard", "guru": "/teacher/dashboard", "murid": "/student/dashboard"}
                return redirect(redirect_map.get(role, "/student/dashboard"))
            except Exception:
                pass
        return render_template("landing.html")

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
        return jsonify({
            "status": "ok",
            "supabase": "connected",
            "cache_size": len(_lru_cache),
            "uptime_ms": int((time.time() - app._start_time) * 1000) if hasattr(app, '_start_time') else 0,
        })

    @app.route("/debug/exam/<exam_id>")
    def debug_exam(exam_id):
        try:
            supabase = app.extensions["supabase"]
            exam = supabase.table("exams").select("id,title,status,is_published,school_id,teacher_id,class_ids,start_at").eq("id", exam_id).single().execute().data
            if not exam:
                return jsonify({"error": "not found"}), 404
            return jsonify({k: str(v) if not isinstance(v, (bool, int, float, list, dict)) and v is not None else v for k, v in exam.items()})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    app._start_time = time.time()

    return app


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
    from app.routes.whiteboard_teacher import whiteboard_teacher_bp
    from app.routes.whiteboard_student import whiteboard_student_bp
    from app.routes.students import student_bp as students_bp

    app.register_blueprint(super_bp)
    app.register_blueprint(public_bp)
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
    app.register_blueprint(whiteboard_teacher_bp, url_prefix="/teacher")
    app.register_blueprint(whiteboard_student_bp, url_prefix="/student")


def _register_error_handlers(app):
    from app.handlers.error_handlers import register_error_handlers
    register_error_handlers(app)


def _register_rate_limiter(app):
    from app.utils.rate_limiter import get_rate_limiter
    get_rate_limiter(app)


def _register_performance_middleware(app):
    @app.after_request
    def add_performance_headers(response):
        if hasattr(g, "start"):
            duration_ms = int((time.time() - g.start) * 1000)
            response.headers["X-Response-Time-ms"] = str(duration_ms)
        if request.path.startswith("/static/"):
            response.cache_control.max_age = 86400
            response.cache_control.public = True
        elif request.path.startswith("/api/") or request.path.startswith("/health"):
            response.cache_control.no_cache = True
        return response


def _register_request_logging(app):
    @app.before_request
    def init_request():
        g.start = time.time()
        g.user_id = None
        try:
            g.tz_offset = int(request.cookies.get("tz_offset", str(DEFAULT_TZ_OFFSET)))
        except (ValueError, TypeError):
            g.tz_offset = DEFAULT_TZ_OFFSET

    @app.after_request
    def log_request(response):
        if hasattr(g, "start"):
            duration = time.time() - g.start
            extra = {"user_id": g.get("user_id"), "duration": f"{duration:.3f}s"}
            app.logger.info("%s %s %s", request.method, request.path, response.status_code, extra=extra)
        return response
