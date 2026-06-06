import logging
import math as _math
import time
from datetime import datetime, timedelta, timezone
from flask import Flask, g, request, jsonify, redirect, render_template
from flask_cors import CORS
from supabase import create_client, Client

from app.config import get_config

DEFAULT_TZ_OFFSET = 7

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

    @app.route("/health")
    def health():
        return jsonify({
            "status": "ok",
            "supabase": "connected",
            "cache_size": len(_lru_cache),
            "uptime_ms": int((time.time() - app._start_time) * 1000) if hasattr(app, '_start_time') else 0,
        })

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

    app.register_blueprint(super_bp)
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


def _register_error_handlers(app):
    @app.errorhandler(401)
    def unauthorized(e):
        if "application/json" in request.headers.get("Accept", ""):
            return jsonify({"error": "Unauthorized"}), 401
        return redirect("/auth/login")

    @app.errorhandler(403)
    def forbidden(e):
        return jsonify({"error": "Forbidden"}), 403

    @app.errorhandler(404)
    def not_found(e):
        if "application/json" in request.headers.get("Accept", ""):
            return jsonify({"error": "Not found"}), 404
        return redirect("/auth/login")

    @app.errorhandler(500)
    def server_error(e):
        app.logger.error("500 error: %s", str(e), exc_info=True)
        if app.debug:
            import traceback
            return jsonify({"error": "Internal server error", "detail": str(e), "traceback": traceback.format_exc()}), 500
        return jsonify({"error": "Internal server error"}), 500


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

    if app.debug:
        @app.after_request
        def log_request(response):
            if hasattr(g, "start"):
                duration = time.time() - g.start
                app.logger.info(
                    "%s %s %s %.3fs user=%s",
                    request.method,
                    request.path,
                    response.status_code,
                    duration,
                    g.get("user_id"),
                )
            return response
