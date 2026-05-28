import logging
import time
from flask import Flask, g, request, jsonify, redirect
from flask_cors import CORS
from supabase import create_client, Client

from app.config import get_config


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

    _register_blueprints(app)
    _register_error_handlers(app)
    _register_request_logging(app)

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

    @app.route("/")
    def index():
        return redirect("/auth/login")

    @app.route("/health")
    def health():
        return jsonify({"status": "ok", "supabase": "connected"})

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

    app.register_blueprint(auth_bp, url_prefix="/auth")
    app.register_blueprint(exam_bp, url_prefix="/exam")
    app.register_blueprint(admin_bp, url_prefix="/admin")
    app.register_blueprint(teacher_bp, url_prefix="/teacher")
    app.register_blueprint(student_bp, url_prefix="/student")
    app.register_blueprint(api_bp, url_prefix="/api")
    app.register_blueprint(publish_bp, url_prefix="/publish")
    app.register_blueprint(webhook_bp, url_prefix="/webhook")


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


def _register_request_logging(app):
    @app.before_request
    def init_request():
        g.start = time.time()
        g.user_id = None

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
                    g.get("user_id"),
                )
            return response
