"""Tests for error handling — custom exceptions, response helpers, error handlers.

Run with: pytest tests/test_error_handling.py -v
"""

import json
import pytest
from flask import Flask


# ── Custom Exception Tests ────────────────────────────────────

class TestCustomExceptions:
    def test_file_too_large_error(self):
        from app.errors import FileTooLargeError
        err = FileTooLargeError(100_000_000, 50_000_000)
        assert err.error_code == "FILE_TOO_LARGE"
        assert err.status_code == 413
        assert "50MB" in err.user_message
        assert err.details["file_size"] == 100_000_000

    def test_file_too_large_error_uses_configured_limit(self):
        """The message must quote the same limit MAX_CONTENT_LENGTH enforces."""
        from app.config import Config
        from app.errors import FileTooLargeError
        err = FileTooLargeError(Config.MAX_CONTENT_LENGTH + 1, Config.MAX_CONTENT_LENGTH)
        assert "50MB" in err.user_message
        assert "50MB" in err.message

    def test_invalid_pdf_error(self):
        from app.errors import InvalidPDFError
        err = InvalidPDFError("File is corrupt")
        assert err.error_code == "INVALID_PDF"
        assert err.status_code == 422
        assert "tidak valid" in err.user_message

    def test_validation_error(self):
        from app.errors import ValidationError
        err = ValidationError("email", "Format email salah")
        assert err.error_code == "VALIDATION_ERROR"
        assert err.status_code == 422
        assert err.details["field"] == "email"

    def test_not_found_error(self):
        from app.errors import NotFoundError
        err = NotFoundError("Ujian", "exam-123")
        assert err.error_code == "NOT_FOUND"
        assert err.status_code == 404
        assert "Ujian" in err.user_message

    def test_forbidden_error(self):
        from app.errors import ForbiddenError
        err = ForbiddenError()
        assert err.error_code == "FORBIDDEN"
        assert err.status_code == 403

    def test_ai_processing_error(self):
        from app.errors import AIProcessingError
        err = AIProcessingError("gemini", "Rate limit exceeded")
        assert err.error_code == "AI_PROCESSING_ERROR"
        assert err.details["provider"] == "gemini"

    def test_grading_error(self):
        from app.errors import GradingError
        err = GradingError("sub-1", "Division by zero")
        assert err.error_code == "GRADING_ERROR"
        assert err.details["submission_id"] == "sub-1"

    def test_payment_error(self):
        from app.errors import PaymentError
        err = PaymentError("Insufficient funds", order_id="ORD-001")
        assert err.error_code == "PAYMENT_ERROR"
        assert err.details["order_id"] == "ORD-001"

    def test_subscription_error(self):
        from app.errors import SubscriptionError
        err = SubscriptionError()
        assert err.error_code == "SUBSCRIPTION_ERROR"
        assert err.status_code == 403


# ── Response Helper Tests ─────────────────────────────────────

class TestResponseHelpers:
    def test_success_response(self):
        from app.utils.responses import success_response
        with Flask(__name__).app_context():
            resp, code = success_response(data={"name": "Test"}, message="OK")
            assert code == 200
            assert resp.json["success"] is True
            assert resp.json["data"]["name"] == "Test"
            assert "timestamp" in resp.json

    def test_error_response(self):
        from app.utils.responses import error_response
        with Flask(__name__).app_context():
            resp, code = error_response("TEST_ERROR", "Ada masalah", status_code=422)
            assert code == 422
            assert resp.json["success"] is False
            assert resp.json["error"] == "TEST_ERROR"
            assert "timestamp" in resp.json


# ── Error Handler Tests ───────────────────────────────────────

class TestErrorHandlers:
    @pytest.fixture
    def app(self):
        app = Flask(__name__)
        app.config["DEBUG"] = False
        from app.handlers.error_handlers import register_error_handlers
        register_error_handlers(app)
        return app

    @pytest.fixture
    def client(self, app):
        return app.test_client()

    @pytest.fixture
    def debug_app(self):
        app = Flask(__name__)
        app.config["DEBUG"] = True
        # Flask turns PROPAGATE_EXCEPTIONS on when DEBUG is on, which would let
        # the exception escape instead of reaching the 500 handler we're testing.
        app.config["PROPAGATE_EXCEPTIONS"] = False
        from app.handlers.error_handlers import register_error_handlers
        register_error_handlers(app)
        return app

    @pytest.fixture
    def debug_client(self, debug_app):
        return debug_app.test_client()

    # The error handlers only emit JSON when the caller asks for it
    # (Accept: application/json or a /api/ path) — see _wants_json().
    JSON = {"Accept": "application/json"}

    def test_404_handler(self, client):
        resp = client.get("/nonexistent", headers=self.JSON)
        assert resp.status_code == 404
        data = resp.get_json()
        assert data["error"] == "NOT_FOUND"
        assert data["success"] is False

    def test_413_handler(self, app):
        """Oversized uploads must produce a JSON FILE_TOO_LARGE error."""
        app.config["MAX_CONTENT_LENGTH"] = 16

        @app.route("/upload", methods=["POST"])
        def upload():
            from flask import request as _r
            _r.get_data()  # forces Werkzeug to enforce MAX_CONTENT_LENGTH
            return "ok"

        client = app.test_client()
        resp = client.post("/upload", data=b"x" * 64, headers=self.JSON)
        assert resp.status_code == 413
        data = resp.get_json()
        assert data["error"] == "FILE_TOO_LARGE"
        assert data["success"] is False

    def test_500_handler_returns_generic_message_in_production(self, client):
        @client.application.route("/trigger-500")
        def trigger():
            raise RuntimeError("Test error")

        resp = client.get("/trigger-500", headers=self.JSON)
        assert resp.status_code == 500
        data = resp.get_json()
        assert data["error"] == "SERVER_ERROR"
        assert "Tim kami" in data["message"]
        # Production responses must never leak internals.
        assert "traceback" not in data
        assert "Test error" not in data["message"]

    def test_500_handler_returns_traceback_in_debug(self, debug_client):
        @debug_client.application.route("/trigger-500-debug")
        def trigger():
            raise RuntimeError("Debug traceback")

        resp = debug_client.get("/trigger-500-debug", headers=self.JSON)
        assert resp.status_code == 500
        data = resp.get_json()
        assert data["error"] == "SERVER_ERROR"
        assert "traceback" in data

    def test_scan_grade_exception_handler(self, client):
        from app.errors import NotFoundError

        @client.application.route("/trigger-not-found")
        def trigger():
            raise NotFoundError("Item", "123")

        resp = client.get("/trigger-not-found")
        assert resp.status_code == 404
        data = resp.get_json()
        assert data["error"] == "NOT_FOUND"
        assert data["success"] is False
