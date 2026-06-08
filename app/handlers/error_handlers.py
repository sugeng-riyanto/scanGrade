import traceback

from flask import jsonify, request, redirect
from app.errors import ScanGradeException
from app.utils.logger import get_logger

logger = get_logger("error_handlers")


def register_error_handlers(app):
    @app.errorhandler(ScanGradeException)
    def handle_scangarde_error(error):
        app.logger.warning(
            "%s: %s", error.error_code, error.message,
            extra={"error_code": error.error_code, **(error.details or {})},
        )
        try:
            import sentry_sdk
            level = "error" if error.status_code >= 500 else "warning"
            sentry_sdk.capture_exception(error, level=level)
        except ImportError:
            pass
        resp = {"success": False, "error": error.error_code, "message": error.user_message}
        if app.debug and error.details:
            resp["details"] = error.details
        return jsonify(resp), error.status_code

    @app.errorhandler(400)
    def handle_bad_request(e):
        if _wants_json():
            return jsonify({"success": False, "error": "BAD_REQUEST", "message": "Permintaan tidak valid"}), 400
        return redirect("/")

    @app.errorhandler(401)
    def unauthorized(e):
        if _wants_json():
            return jsonify({"success": False, "error": "UNAUTHORIZED", "message": "Silakan login terlebih dahulu"}), 401
        return redirect("/auth/login")

    @app.errorhandler(403)
    def forbidden(e):
        return jsonify({"success": False, "error": "FORBIDDEN", "message": "Akses ditolak"}), 403

    @app.errorhandler(404)
    def not_found(e):
        if _wants_json():
            return jsonify({"success": False, "error": "NOT_FOUND", "message": "Halaman tidak ditemukan"}), 404
        return redirect("/")

    @app.errorhandler(413)
    def request_entity_too_large(e):
        return jsonify({"success": False, "error": "FILE_TOO_LARGE", "message": "File terlalu besar. Maksimal 50MB"}), 413

    @app.errorhandler(429)
    def too_many_requests(e):
        return jsonify({"success": False, "error": "RATE_LIMITED", "message": "Terlalu banyak permintaan. Coba lagi nanti."}), 429

    @app.errorhandler(500)
    def server_error(e):
        app.logger.error("500: %s", str(e), exc_info=True)
        try:
            import sentry_sdk
            sentry_sdk.capture_exception(e)
        except ImportError:
            pass
        if app.debug:
            return jsonify({
                "success": False, "error": "SERVER_ERROR",
                "message": "Internal server error",
                "detail": str(e), "traceback": traceback.format_exc(),
            }), 500
        return jsonify({"success": False, "error": "SERVER_ERROR", "message": "Terjadi kesalahan server. Tim kami sedang menanganinya."}), 500


def _wants_json():
    accept = request.headers.get("Accept", "")
    return "application/json" in accept or request.path.startswith("/api/")
