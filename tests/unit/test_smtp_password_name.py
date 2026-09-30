"""The app password is the same secret under either name a `.env` carries it in.

Measured on this checkout while wiring forgot-password end to end: `.env` had

    SMTP_EMAIL=...
    app_password_gmail=...
    #SMTP_PASSWORD=...

and the app read **neither** — the whole mail path resolves `SMTP_PASSWORD`
(`app/services/smtp_settings.py` → `_env`), so a box holding the right secret under
the name Google's own page produces reported ``configured: False`` and every reset
email was skipped with a warning. That is the same failure this module's docstring
already records once (an app password rejected as BadCredentials), wearing a
different name: silent, and only visible to somebody reading the journal.

Two spellings, then, and one of them needs a second thing handled: Google shows a
16-character app password in four groups (`abcd efgh ijkl mnop`), and that grouped
string authenticates as garbage. The *Gmail* names are squeezed; `SMTP_PASSWORD` is
left exactly as written, because an ordinary SMTP password may legitimately contain
a space and this helper must not be the reason a working box stops sending.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402

from app import config  # noqa: E402
from app.services import smtp_settings  # noqa: E402

#: Every name the password may arrive under, cleared before each case so the
#: checkout's own `.env` (loaded at import) cannot decide one of these tests.
ALL_NAMES = config.SMTP_PASSWORD_NAMES


@pytest.fixture(autouse=True)
def _no_ambient_password(monkeypatch):
    for name in (*ALL_NAMES, "SMTP_EMAIL", "SMTP_HOST", "SMTP_PORT", "SMTP_FROM"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(smtp_settings, "load", lambda: {})
    yield


class TestTheNames:
    def test_the_canonical_name_wins(self, monkeypatch):
        monkeypatch.setenv("SMTP_PASSWORD", "canonical")
        monkeypatch.setenv("app_password_gmail", "ignored")
        assert config.env_password() == "canonical"

    @pytest.mark.parametrize("name", ["APP_PASSWORD_GMAIL", "app_password_gmail"])
    def test_either_gmail_spelling_is_read(self, monkeypatch, name):
        monkeypatch.setenv(name, "gmail-secret")
        assert config.env_password() == "gmail-secret"

    def test_no_password_anywhere_is_empty_not_an_error(self):
        assert config.env_password() == ""

    def test_the_config_makes_the_same_choice(self, monkeypatch):
        """The class attribute is what the app, the alerts and the panel read."""
        monkeypatch.setenv("app_password_gmail", "gmail-secret")
        assert config.Config.SMTP_PASSWORD or config.env_password() == "gmail-secret", (
            "`Config.SMTP_PASSWORD` is not the value this helper would resolve")


class TestTheGroupedPaste:
    def test_a_gmail_password_pasted_in_groups_is_usable(self, monkeypatch):
        monkeypatch.setenv("app_password_gmail", "abcd efgh ijkl mnop")
        assert config.env_password() == "abcdefghijklmnop"

    def test_an_ordinary_password_keeps_its_spaces(self, monkeypatch):
        """A space in a real SMTP password is a character, not formatting."""
        monkeypatch.setenv("SMTP_PASSWORD", "two words")
        assert config.env_password() == "two words"


class TestTheMailPath:
    def test_the_mail_path_resolves_the_grouped_gmail_name(self, monkeypatch):
        monkeypatch.setenv("SMTP_EMAIL", "scangrade9@gmail.com")
        monkeypatch.setenv("app_password_gmail", "abcd efgh ijkl mnop")
        resolved = smtp_settings.resolve()
        assert resolved["password"] == "abcdefghijklmnop"
        assert resolved["configured"] is True, (
            "a box holding the app password under Gmail's own name still reports "
            "itself unable to send mail")

    def test_a_stored_password_still_wins_over_both(self, monkeypatch):
        monkeypatch.setenv("SMTP_EMAIL", "scangrade9@gmail.com")
        monkeypatch.setenv("app_password_gmail", "gmail-secret")
        monkeypatch.setattr(smtp_settings, "load", lambda: {"smtp_password": "stored"})
        resolved = smtp_settings.resolve()
        assert resolved["password"] == "stored"
        assert resolved["source"] == "database"

    def test_an_unconfigured_box_is_still_unconfigured(self):
        assert smtp_settings.resolve()["configured"] is False


if __name__ == "__main__":                                   # pragma: no cover
    sys.exit(pytest.main([__file__, "-q"]))
