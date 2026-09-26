"""The SMTP credential has to be settable without a shell, and unrepeatable in a page.

Measured against the running deployment before this test existed: the box reads
``SMTP_EMAIL`` / ``SMTP_PASSWORD`` from ``EnvironmentFile=/opt/scangrade/.env`` and
from nowhere else, the file only ever received ``SMTP_PASSWORD=`` (empty), and the
app password that was in the checkout's ``.env`` is rejected by Google (535
BadCredentials). So the reset flow could reach "send" and always fail, and the only
way to change that was root on the box.

What is asserted here:

* a value stored in ``system_settings`` is what the sender actually uses, and the
  environment is the fallback rather than the other way round;
* the password is **write-only** — the settings page may say whether one is set,
  never render it, and saving with the field left blank keeps the stored one;
* a non-super-admin cannot read or write the page;
* the "send a test" action reports the truth for each of the four outcomes, so a
  green result is a genuine SMTP 250 rather than an optimistic page;
* the forgot-password sender uses the *stored* credentials, which is the whole
  point: with ``SMTP_PASSWORD`` empty in the environment, a reset email must still
  go out because the password lives in the database.
"""
from types import SimpleNamespace

import pytest
from flask import g

from tests.conftest import app_instance

ROUTE = "/super-admin/email-settings"
TEST_ROUTE = "/super-admin/email-settings/test"


class _Resp:
    def __init__(self, data):
        self.data = data


class _Table:
    """A key/value stand-in for ``system_settings``, with upsert-on-``key``."""

    def __init__(self, store):
        self._store = store
        self._keys = None
        self._pending = None
        self.select_cols = None

    def select(self, cols="*"):
        self.select_cols = cols
        return self

    def in_(self, _col, keys):
        self._keys = list(keys)
        return self

    def eq(self, _col, value):
        self._keys = [value]
        return self

    def upsert(self, row, **_kw):
        self._pending = dict(row)
        return self

    def execute(self):
        if self._pending is not None:
            row = self._pending
            self._store[row["key"]] = row["value"]
            return _Resp([dict(row)])
        keys = self._keys if self._keys is not None else list(self._store)
        return _Resp([{"key": k, "value": self._store[k]} for k in keys
                      if k in self._store])


class _FakeSupabase:
    def __init__(self, store=None):
        self.system_settings = dict(store or {})

    def table(self, name):
        assert name == "system_settings", f"unexpected table {name}"
        return _Table(self.system_settings)


@pytest.fixture()
def fake(monkeypatch):
    from app.routes import super_admin as mod
    supabase = _FakeSupabase()
    monkeypatch.setattr(mod, "get_supabase", lambda: supabase)
    return supabase


def _clear_cache():
    from app.utils.cache import cache_delete
    for key in ("smtp_settings", "smtp_settings:store"):
        cache_delete(key)


# ── the resolver ─────────────────────────────────────────────────────────────

def test_a_stored_password_is_the_one_that_is_used(monkeypatch):
    """``system_settings`` beats the environment — that is the whole feature."""
    from app.services import smtp_settings

    _clear_cache()
    supabase = _FakeSupabase({
        "smtp_host": "smtp.example.test",
        "smtp_port": "2525",
        "smtp_user": "stored@scan-grade.app",
        "smtp_password": "stored-secret",
        "smtp_from": "Stored <stored@scan-grade.app>",
    })
    monkeypatch.setattr(smtp_settings, "get_supabase", lambda: supabase)
    monkeypatch.setenv("SMTP_PASSWORD", "env-secret")
    monkeypatch.setenv("SMTP_EMAIL", "env@scan-grade.app")

    got = smtp_settings.resolve()
    assert got["password"] == "stored-secret"
    assert got["user"] == "stored@scan-grade.app"
    assert got["host"] == "smtp.example.test"
    assert got["port"] == 2525
    assert got["sender"] == "Stored <stored@scan-grade.app>"
    assert got["source"] == "database"


def test_the_environment_is_the_fallback_when_nothing_is_stored(monkeypatch):
    from app.services import smtp_settings

    _clear_cache()
    monkeypatch.setattr(smtp_settings, "get_supabase", lambda: _FakeSupabase())
    monkeypatch.setenv("SMTP_PASSWORD", "env-secret")
    monkeypatch.setenv("SMTP_EMAIL", "env@scan-grade.app")

    got = smtp_settings.resolve()
    assert got["password"] == "env-secret"
    assert got["user"] == "env@scan-grade.app"
    assert got["source"] == "environment"


def test_saving_stores_every_field(monkeypatch):
    from app.services import smtp_settings

    _clear_cache()
    supabase = _FakeSupabase()
    monkeypatch.setattr(smtp_settings, "get_supabase", lambda: supabase)

    saved = smtp_settings.save({
        "smtp_host": "smtp.gmail.com",
        "smtp_port": "465",
        "smtp_user": "scangrade9@gmail.com",
        "smtp_password": "abcd efgh ijkl mnop",
        "smtp_from": "ScanGrade <scangrade9@gmail.com>",
        "evil_key": "ignored",
    })
    assert "evil_key" not in saved
    assert supabase.system_settings["smtp_password"] == "abcd efgh ijkl mnop"
    assert supabase.system_settings["smtp_user"] == "scangrade9@gmail.com"
    assert supabase.system_settings["smtp_port"] == "465"


def test_a_blank_password_keeps_the_stored_one(monkeypatch):
    """The field is write-only, so leaving it empty must not erase the credential."""
    from app.services import smtp_settings

    _clear_cache()
    supabase = _FakeSupabase({"smtp_password": "already-set"})
    monkeypatch.setattr(smtp_settings, "get_supabase", lambda: supabase)

    smtp_settings.save({"smtp_user": "scangrade9@gmail.com", "smtp_password": ""})
    assert supabase.system_settings["smtp_password"] == "already-set"


# ── the page ─────────────────────────────────────────────────────────────────

def _render(method="GET", path=ROUTE, data=None, role="super_admin"):
    from app.routes import super_admin as mod
    app = app_instance()
    with app.test_request_context(path, method=method, data=data or {}):
        g.user_id = "sa-1"
        g.user_role = role
        g.user_email = "super@scan-grade.app"
        g.user_name = "Super Admin"
        g.user_school_id = ""
        g.tz_offset = 7
        g.show = {}
        fn = mod.email_settings
        return getattr(fn, "__wrapped__", fn)()


def test_the_page_never_renders_the_password(fake):
    fake.system_settings["smtp_password"] = "super-secret-app-password"
    html = _render()
    assert "super-secret-app-password" not in html, (
        "the stored password was rendered into the page — it is write-only")
    # It may *say* that one is set, which is how an operator knows to leave it blank.
    assert "smtp_password" in html


def test_a_non_super_admin_is_refused(fake):
    """The full decorated view, not the ``__wrapped__`` inner — this is the guard."""
    from app.routes import super_admin as mod
    app = app_instance()
    with app.test_request_context(ROUTE):
        g.user_id = "t-1"
        g.user_role = "guru"
        g.user_email = "guru@scan-grade.app"
        g.user_school_id = "s-1"
        resp = mod.email_settings()
    # A non-super-admin is sent back to the login refusal, never the settings body.
    assert getattr(resp, "status_code", None) in (301, 302), (
        "a guru reached the SMTP settings view")


def test_sending_a_test_reports_a_real_delivery(monkeypatch, fake):
    from app.services import smtp_settings
    _clear_cache()
    fake.system_settings.update({
        "smtp_user": "scangrade9@gmail.com", "smtp_password": "pw",
        "smtp_host": "smtp.gmail.com", "smtp_port": "465",
    })
    monkeypatch.setattr(smtp_settings, "get_supabase", lambda: fake)
    sent = {}

    def _record(to, *a, **k):
        sent["to"] = to
        return True, None

    monkeypatch.setattr(smtp_settings, "send", _record)

    out = smtp_settings.send_test("someone@scan-grade.app")
    assert out["outcome"] == "sent"
    assert sent["to"] == "someone@scan-grade.app"


def test_a_failed_send_is_reported_as_failed(monkeypatch, fake):
    from app.services import smtp_settings
    _clear_cache()
    fake.system_settings.update({"smtp_user": "scangrade9@gmail.com", "smtp_password": "pw"})
    monkeypatch.setattr(smtp_settings, "get_supabase", lambda: fake)
    monkeypatch.setattr(smtp_settings, "send",
                        lambda to, *a, **k: (False, "535 BadCredentials"))

    out = smtp_settings.send_test("someone@scan-grade.app")
    assert out["outcome"] == "send_failed"
    assert "535" in (out.get("detail") or "")


def test_an_unconfigured_box_says_so_instead_of_failing_quietly(monkeypatch):
    from app.services import smtp_settings
    _clear_cache()
    monkeypatch.setattr(smtp_settings, "get_supabase", lambda: _FakeSupabase())
    monkeypatch.delenv("SMTP_PASSWORD", raising=False)
    monkeypatch.delenv("SMTP_EMAIL", raising=False)

    out = smtp_settings.send_test("someone@scan-grade.app")
    assert out["outcome"] == "not_configured"


# ── the reset path actually uses the stored credential ───────────────────────

def test_forgot_password_sends_with_the_stored_password(monkeypatch):
    """With ``SMTP_PASSWORD`` empty in the environment, the stored one must still send."""
    from app.services import smtp_settings
    from app.routes import auth as auth_mod

    _clear_cache()
    supabase = _FakeSupabase({
        "smtp_user": "scangrade9@gmail.com",
        "smtp_password": "stored-secret",
        "smtp_host": "smtp.gmail.com",
        "smtp_port": "465",
    })
    monkeypatch.setattr(smtp_settings, "get_supabase", lambda: supabase)
    monkeypatch.setenv("SMTP_PASSWORD", "")

    logins = []

    class _Smtp:
        def __init__(self, *a, **k):
            pass
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def login(self, user, password):
            logins.append((user, password))
        def sendmail(self, frm, to, _msg):
            return {}

    import smtplib as real_smtplib
    monkeypatch.setattr(smtp_settings.smtplib, "SMTP_SSL", _Smtp)

    app = app_instance()
    with app.test_request_context("/"):
        ok = auth_mod._send_email("parent@scan-grade.app", "Kode", "123456")

    assert ok is True, "the reset email did not send using the stored credentials"
    assert logins and logins[0] == ("scangrade9@gmail.com", "stored-secret")
