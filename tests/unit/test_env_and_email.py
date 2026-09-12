"""Config/notification regression tests.

Two real incidents are pinned here:

1. `.env` values carrying inline comments. ``FLASK_ENV=production  # deploy``
   made naive readers fall through to DevelopmentConfig (debug on), and a
   comment glued to a Midtrans key (``...abc# Payment gateway``) made the key
   fail authentication with 401.
2. ``notification_service.send_email`` read ``SMTP_USER``/``SMTP_PASS`` while the
   project stores ``SMTP_EMAIL``/``SMTP_PASSWORD`` — approval emails were
   silently never delivered.
"""

from unittest.mock import patch

import pytest


# ── .env value parsing ────────────────────────────────────────

class TestEnvParsing:
    def test_inline_comment_is_stripped(self, monkeypatch):
        from app.config import env_str
        monkeypatch.setenv("SG_TEST_VALUE", "production  # untuk deploy")
        assert env_str("SG_TEST_VALUE") == "production"

    def test_surrounding_whitespace_is_trimmed(self, monkeypatch):
        from app.config import env_str
        monkeypatch.setenv("SG_TEST_VALUE", "  padded-value  ")
        assert env_str("SG_TEST_VALUE") == "padded-value"

    def test_hash_inside_a_secret_is_preserved(self, monkeypatch):
        """A password may legitimately contain '#' — only ' #' starts a comment."""
        from app.config import env_str
        monkeypatch.setenv("SG_TEST_VALUE", "p#ss w0rd")
        assert env_str("SG_TEST_VALUE") == "p#ss w0rd"

    def test_env_key_cuts_a_glued_comment(self, monkeypatch):
        from app.config import env_key
        monkeypatch.setenv("SG_TEST_KEY", "Mid-server-abc123# Payment gateway")
        assert env_key("SG_TEST_KEY") == "Mid-server-abc123"

    def test_env_key_still_handles_clean_values(self, monkeypatch):
        from app.config import env_key
        monkeypatch.setenv("SG_TEST_KEY", "  Mid-server-abc123  ")
        assert env_key("SG_TEST_KEY") == "Mid-server-abc123"

    def test_commented_flask_env_does_not_fall_back_to_development(self, monkeypatch):
        from app.config import ProductionConfig, get_config
        monkeypatch.setenv("FLASK_ENV", "production  # production untuk deploy")
        assert get_config() is ProductionConfig

    def test_dotted_config_path_resolves(self):
        from app.config import TestingConfig, get_config
        assert get_config("app.config.TestingConfig") is TestingConfig


# ── Email transport ───────────────────────────────────────────

@pytest.fixture
def app():
    from app import create_app
    return create_app("app.config.TestingConfig")


class TestEmailTransport:
    def test_send_email_uses_configured_smtp_account_on_465(self, app):
        from app.services.notification_service import send_email

        app.config["SMTP_PASSWORD"] = "app-password"
        with app.app_context():
            with patch("app.services.notification_service.smtplib.SMTP_SSL") as ssl_mock:
                ok = send_email("kepsek@example.com", "Aktivasi", "<p>kode</p>")

        assert ok is True
        host, port = ssl_mock.call_args[0][:2]
        assert (host, port) == ("smtp.gmail.com", 465)
        server = ssl_mock.return_value.__enter__.return_value
        server.login.assert_called_once_with(app.config["SMTP_EMAIL"], "app-password")
        server.sendmail.assert_called_once()
        sender, recipients = server.sendmail.call_args[0][:2]
        assert recipients == ["kepsek@example.com"]
        assert app.config["SMTP_EMAIL"] in sender

    def test_send_email_uses_starttls_on_587(self, app):
        from app.services.notification_service import send_email

        app.config["SMTP_PASSWORD"] = "app-password"
        app.config["SMTP_PORT"] = 587
        with app.app_context():
            with patch("app.services.notification_service.smtplib.SMTP") as smtp_mock:
                ok = send_email("guru@example.com", "Halo", "<p>hi</p>")

        assert ok is True
        smtp_mock.return_value.__enter__.return_value.starttls.assert_called_once()

    def test_send_email_reports_failure_without_credentials(self, app, monkeypatch):
        from app.services.notification_service import send_email

        app.config["SMTP_PASSWORD"] = ""
        app.config["SMTP_EMAIL"] = ""
        monkeypatch.delenv("SMTP_PASSWORD", raising=False)
        monkeypatch.delenv("SMTP_EMAIL", raising=False)
        with app.app_context():
            with patch("app.services.notification_service.smtplib.SMTP_SSL") as ssl_mock:
                ok = send_email("guru@example.com", "Halo", "<p>hi</p>")

        assert ok is False
        ssl_mock.assert_not_called()


# ── WhatsApp is optional ──────────────────────────────────────

class TestWhatsappIsOptional:
    def test_whatsapp_is_a_noop_without_api_key(self, monkeypatch):
        from app.services.notification_service import send_whatsapp

        monkeypatch.delenv("FONNTE_API_KEY", raising=False)
        with patch("app.services.notification_service.requests.post") as post:
            assert send_whatsapp("081234567890", "halo") is False
        post.assert_not_called()

    def test_approval_email_does_not_need_whatsapp(self, app, monkeypatch):
        from app.services import notification_service

        monkeypatch.delenv("FONNTE_API_KEY", raising=False)
        app.config["SMTP_PASSWORD"] = "app-password"
        with app.app_context():
            with patch.object(notification_service, "send_email", return_value=True) as mail:
                notification_service.notify_approval(
                    "admin@example.com", "081234567890", "SMA Test", "123456", "2026-01-01",
                )
        # Email alone must be enough for the approval to go out.
        mail.assert_called_once()
