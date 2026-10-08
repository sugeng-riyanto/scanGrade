"""Config/notification regression tests.

Three real incidents are pinned here:

1. An unreadable `.env`. ``load_dotenv()`` was called bare, and python-dotenv lets
   a failed ``open()`` out — so the box's `.env` at mode ``600`` (which
   ``docs/SECRET_ROTATION.md`` tells an operator to set) was a ``PermissionError``
   at *import* of ``app.config``: gunicorn could not boot its workers, the Celery
   worker crash-looped under ``Restart=always`` with nothing in the journal saying
   why, and the suite or a ``manage.py`` command died before printing anything
   useful. The read is now tolerant and its reason is published on
   ``config.DOTENV_ERROR``; the worker's unit also takes the file as
   ``EnvironmentFile=``, which systemd reads as root.

2. `.env` values carrying inline comments. ``FLASK_ENV=production  # deploy``
   made naive readers fall through to DevelopmentConfig (debug on), and a
   comment glued to a Midtrans key (``...abc# Payment gateway``) made the key
   fail authentication with 401.
3. ``notification_service.send_email`` read ``SMTP_USER``/``SMTP_PASS`` while the
   project stores ``SMTP_EMAIL``/``SMTP_PASSWORD`` — approval emails were
   silently never delivered.

Incident 1 is asserted by ``TestAnUnreadableEnvFile`` below; 2 and 3 by the rest
of the file. The transport tests patch ``smtp_settings.smtplib``, not the notification
service's: there is now exactly **one** sender, and `notification_service.send_email`
delegates to it. Patching the old address would prove nothing about the connection
that actually opens.
"""

import builtins
import os
import re
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[2]


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


# ── An unreadable .env is a missing file, not a fatal one ─────

class TestAnUnreadableEnvFile:
    """The failure this pins is a *process* that never starts.

    `deploy/deploy.sh` writes the box's `.env` as root and the mode is `600`
    (`docs/SECRET_ROTATION.md`), so the `.env` that both the app and the worker
    read through python-dotenv is one the account they run as could not
    necessarily open. When that read raised, the traceback came out of a *file
    reader* at import: no page, no task, no clue in the journal beyond the same
    four lines every five seconds (`Restart=always`).

    What is asserted is deliberately two-sided: the reason is *recorded* rather
    than swallowed, and the environment is *still* the environment — because the
    thing this must not do is turn a genuinely missing configuration into a
    silent one. `Config.validate()` is what refuses an incomplete environment, and
    it does so with the names of the variables.
    """

    def test_a_readable_file_sets_its_variables(self, tmp_path, monkeypatch):
        from app.config import load_env_file
        env = tmp_path / "readable.env"
        env.write_text("SG_DOTENV_PROBE=loaded\n", encoding="utf-8")
        monkeypatch.delenv("SG_DOTENV_PROBE", raising=False)
        assert load_env_file(str(env)) == ""
        assert os.environ["SG_DOTENV_PROBE"] == "loaded"
        monkeypatch.delenv("SG_DOTENV_PROBE", raising=False)

    def test_a_file_that_is_not_there_is_not_an_error(self, tmp_path):
        """A box with no `.env` is a postgres box with systemd variables — normal.

        The two states must stay distinguishable, which is the only reason
        `DOTENV_ERROR` exists: "no file here" answers `""` exactly like a file that
        was read.
        """
        from app.config import load_env_file
        assert load_env_file(str(tmp_path / "absent.env")) == ""

    def test_an_unreadable_file_is_reported_rather_than_raised(self, tmp_path, monkeypatch):
        from app.config import load_env_file
        env = tmp_path / "denied.env"
        env.write_text("SG_DOTENV_PROBE=never\n", encoding="utf-8")
        real_open = builtins.open

        def deny(path, *args, **kwargs):
            if str(path) == str(env):
                raise PermissionError(13, "Permission denied")
            return real_open(path, *args, **kwargs)

        monkeypatch.setattr(builtins, "open", deny)
        monkeypatch.delenv("SG_DOTENV_PROBE", raising=False)
        reason = load_env_file(str(env))
        assert "PermissionError" in reason, (
            "an unreadable file must answer with the reason, not with silence: a "
            f"page or a probe has to tell it from a box with no .env (got {reason!r})")
        assert "SG_DOTENV_PROBE" not in os.environ, (
            "the denied file's variables were loaded anyway")

    def test_a_directory_in_place_of_the_file_is_not_an_error_either(self, tmp_path):
        """python-dotenv skips a path that is not a file; the rule must not rely on that.

        This is the shape of a half-created `.env` (a `mkdir .env` typo, a broken
        symlink target), and it is asserted here so the tolerance is pinned to the
        file reader's contract rather than to one library version's behaviour.
        """
        from app.config import load_env_file
        (tmp_path / "directory.env").mkdir()
        assert load_env_file(str(tmp_path / "directory.env")) == ""

    def test_a_process_with_an_unreadable_env_file_still_imports_the_config(self):
        """The incident itself, end to end: a fresh interpreter, denied at `open`.

        Red at HEAD — the traceback came out of `import app.config` — and asserted
        in a subprocess on purpose: the failure was a process that never started,
        so a test that imports the already-loaded module proves nothing about the
        import, and `wsgi.py` and `manage.py` need the same guarantee as this file.
        """
        script = (
            "import builtins\n"
            "real = builtins.open\n"
            "def deny(path, *args, **kwargs):\n"
            "    if str(path).endswith('.env'):\n"
            "        raise PermissionError(13, 'Permission denied')\n"
            "    return real(path, *args, **kwargs)\n"
            "builtins.open = deny\n"
            "import app.config as c\n"
            "print('DOTENV_ERROR=' + (c.DOTENV_ERROR or 'none'))\n"
            "print('CONFIG_OK=' + c.TestingConfig.SECRET_KEY[:4])\n"
        )
        env = {**os.environ, "PYTHONPATH": str(ROOT), "PYTHONIOENCODING": "utf-8"}
        done = subprocess.run([sys.executable, "-c", script], cwd=str(ROOT),
                              capture_output=True, text=True, timeout=300, env=env)
        assert done.returncode == 0, (
            "an unreadable .env still takes the process down at import, which is "
            "exactly how the worker and every gunicorn worker died:\n"
            + (done.stderr or "")[-2000:])
        assert "DOTENV_ERROR=PermissionError" in done.stdout, (
            "the import survived but the reason was not recorded: " + done.stdout)
        assert "CONFIG_OK=" in done.stdout, (
            "the config classes were not built, so the import was survived but not "
            "completed: " + done.stdout)

    def test_no_module_the_box_runs_reads_the_env_file_itself(self):
        """Every additional reader is another process that can die at import.

        `wsgi.py` had one: it called `load_dotenv()` before importing the app, so
        tolerating the read in `app/config.py` would have saved the app and left
        gunicorn's worker boot exactly as fatal as before. The sweep is over the
        modules a **deployed process** imports — `app/**` and `wsgi.py`, the two
        things gunicorn and `celery -A app.celery_app` load.

        Root-level tooling is outside it on purpose: `manage.py` and the load-test
        scripts read a `.env` a developer put in a checkout, not the box's `600`
        file, and a crash there is a loud command rather than a silent service.
        """
        runtime = [*(ROOT / "app").rglob("*.py"), ROOT / "wsgi.py"]
        importers = sorted(
            path.relative_to(ROOT).as_posix() for path in runtime
            if re.search(r"^\s*(?:from dotenv import|import dotenv)",
                         path.read_text(encoding="utf-8", errors="replace"), re.M))
        assert importers == ["app/config.py"], (
            "a runtime module reads an environment file on its own: " + repr(importers)
            + " — `app.config.load_env_file` is the one reader whose failure is not "
            "fatal, and a second reader is a process that can still die at import")


# ── Email transport ───────────────────────────────────────────

class TestEmailTransport:
    def test_send_email_uses_configured_smtp_account_on_465(self, app):
        from app.services.notification_service import send_email

        app.config["SMTP_PASSWORD"] = "app-password"
        with app.app_context():
            with patch("app.services.smtp_settings.smtplib.SMTP_SSL") as ssl_mock:
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
            with patch("app.services.smtp_settings.smtplib.SMTP") as smtp_mock:
                ok = send_email("guru@example.com", "Halo", "<p>hi</p>")

        assert ok is True
        smtp_mock.return_value.__enter__.return_value.starttls.assert_called_once()

    def test_send_email_reports_failure_without_credentials(self, app, monkeypatch):
        from app.services.notification_service import send_email

        app.config["SMTP_PASSWORD"] = ""
        app.config["SMTP_EMAIL"] = ""
        from app.config import SMTP_PASSWORD_NAMES

        for name in (*SMTP_PASSWORD_NAMES, "SMTP_EMAIL"):
            monkeypatch.delenv(name, raising=False)
        with app.app_context():
            with patch("app.services.smtp_settings.smtplib.SMTP_SSL") as ssl_mock:
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
