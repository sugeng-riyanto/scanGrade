"""An expired session told the user to "log in first" — and said it on the wrong page.

Reported as: opening ``/super-admin/activation-codes`` answered
"Silakan login terlebih dahulu" while the user was fully logged in. Three defects
composed into that:

1. ``_unauthorized()`` flashed one blanket sentence for every cause, so a
   15-minute idle timeout (or the 4-hour absolute limit) read as a permissions
   problem on whatever URL the user was opening.
2. Neither login page rendered flashes — ``get_flashed_messages`` was absent from
   ``auth/login.html``, ``auth/login_user.html`` and ``base.html``. The redirect
   target silently swallowed the notice, so it stayed queued in the session.
3. A successful login did not clear the queue, so the stale notice was finally
   printed by the next page that *does* render flashes — the page the user had
   just opened, which then looked like the thing rejecting them.

The tests below pin each one. Reproduced against production before fixing: trip
the idle timeout, log in again, open the page — the message was there.
"""
import time
from types import SimpleNamespace

import pytest

from app.utils import auth as authmod
from app.routes import auth as routes_auth


# ── fakes ────────────────────────────────────────────────────────

class FakeQuery:
    """``select(...).eq(...).single().execute()`` for the profiles lookup."""

    def __init__(self, row):
        self.row = row

    def select(self, *a, **k):
        return self

    def eq(self, *a, **k):
        return self

    def single(self):
        return self

    def execute(self):
        return SimpleNamespace(data=self.row)


class FakeSupabase:
    def __init__(self, row):
        self.row = row

    def table(self, name):
        return FakeQuery(self.row)


def _fake_login_result():
    user = SimpleNamespace(
        id="sa-1",
        email="superadmin@scan-grade.app",
        user_metadata={"full_name": "Super Admin", "role": "super_admin"},
    )
    session = SimpleNamespace(access_token="tok", refresh_token="rtok")
    return SimpleNamespace(user=user, session=session)


@pytest.fixture
def app():
    from app import create_app

    application = create_app("app.config.TestingConfig")
    application.config["RATELIMIT_ENABLED"] = False

    # A route that is protected by nothing but ``login_required``, so the session
    # timeout checks are exercised without any role/school noise.
    def _probe():
        return "ok"

    application.add_url_rule(
        "/_probe_protected", "_probe_protected", authmod.login_required(_probe)
    )
    return application


def _set_session(client, **values):
    with client.session_transaction() as sess:
        sess.update(values)


def _read_session(client):
    with client.session_transaction() as sess:
        return dict(sess)


# ── 1. the message must say why ──────────────────────────────────

def test_unauthorized_without_a_reason_keeps_the_generic_message(app):
    """Callers that don't know the cause keep the old wording."""
    client = app.test_client()

    resp = client.get("/_probe_protected")   # no access_token cookie at all

    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/auth/login")
    assert "Silakan login terlebih dahulu" in client.get("/auth/login").get_data(as_text=True)


def test_unauthorized_reason_reaches_the_json_caller(app):
    with app.test_request_context("/api/thing", headers={"Accept": "application/json"}):
        resp, status = authmod._unauthorized("Sesi Anda berakhir")

    assert status == 401
    assert resp.get_json()["error"] == "Sesi Anda berakhir"


def test_idle_timeout_names_the_idle_window(app, monkeypatch):
    monkeypatch.setattr(authmod, "_session_for", lambda token: {
        "user_id": "sa-1", "email": "sa@x", "name": "SA", "role": "super_admin",
        "school_id": None, "status": "active",
    })

    client = app.test_client()
    stale = str(time.time() - (15 * 60 + 60))  # one minute past super_admin idle
    client.set_cookie("access_token", "tok")
    client.set_cookie("last_activity", stale)

    resp = client.get("/_probe_protected")

    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/auth/login")

    # The notice is now carried into the login page, not left unread.
    page = client.get("/auth/login")
    body = page.get_data(as_text=True)
    assert "tidak ada aktivitas" in body
    assert "15 menit" in body


def test_absolute_limit_names_the_hour_budget(app, monkeypatch):
    monkeypatch.setattr(authmod, "_session_for", lambda token: {
        "user_id": "sa-1", "email": "sa@x", "name": "SA", "role": "super_admin",
        "school_id": None, "status": "active",
    })

    client = app.test_client()
    client.set_cookie("access_token", "tok")
    client.set_cookie("last_activity", str(time.time()))
    client.set_cookie("session_start", str(time.time() - (4 * 3600 + 60)))

    resp = client.get("/_probe_protected")

    assert resp.status_code == 302
    page = client.get("/auth/login")
    assert "4 jam" in page.get_data(as_text=True)


# ── 1b. a new login starts a new idle window ─────────────────────

def _guru_session(monkeypatch):
    monkeypatch.setattr(authmod, "_session_for", lambda token: {
        "user_id": "g-1", "email": "guru@x", "name": "Guru", "role": "guru",
        "school_id": None, "status": "active",
    })


def test_a_stale_idle_cookie_cannot_lock_out_a_fresh_login(app, monkeypatch):
    """``last_activity`` is refreshed only by an authenticated response, and the
    login redirect does not go through one — so a browser that kept the cookie
    from an earlier session arrives with a stale value. Logging in issues a fresh
    ``session_start``, so the idle window has to restart with it; otherwise the
    first request after *every* re-login is refused and the account stays locked
    out until the cookie expires 24 hours later, with no way for the user out.
    Reproduced against a live server before fixing: re-login, then
    ``GET /teacher/dashboard`` answered 302 with "tidak ada aktivitas".
    """
    _guru_session(monkeypatch)

    client = app.test_client()
    client.set_cookie("access_token", "tok")
    client.set_cookie("last_activity", str(time.time() - 2 * 3600))  # earlier session
    client.set_cookie("session_start", str(time.time()))            # this login

    resp = client.get("/_probe_protected")

    assert resp.status_code == 200, resp.get_data(as_text=True)


def test_the_clamp_does_not_weaken_the_timeout(app, monkeypatch):
    """If the session itself is idle, both signals are old and it is refused —
    the fix must restart the clock at login, not ignore the clock."""
    _guru_session(monkeypatch)

    client = app.test_client()
    client.set_cookie("access_token", "tok")
    client.set_cookie("last_activity", str(time.time() - 2 * 3600))
    client.set_cookie("session_start", str(time.time() - 2 * 3600))

    resp = client.get("/_probe_protected")

    assert resp.status_code == 302
    # A guru is refused, and lands on the door that says so: the admin page
    # heading "Masuk Admin" is not where a teacher should be told to log in
    # again. See tests/unit/test_login_door.py.
    assert resp.headers["Location"].endswith("/auth/login-user")
    assert "60 menit" in client.get("/auth/login-user").get_data(as_text=True)


def test_a_login_with_no_idle_cookie_still_works(app, monkeypatch):
    """A browser that has ``session_start`` but no ``last_activity`` — a session
    created before that cookie existed — must not be refused for the absence."""
    _guru_session(monkeypatch)

    client = app.test_client()
    client.set_cookie("access_token", "tok")
    client.set_cookie("session_start", str(time.time()))

    assert client.get("/_probe_protected").status_code == 200


# ── 2. the login page must render what it was told ───────────────

def test_login_page_renders_a_flashed_notice(app):
    """The regression: the redirect target dropped the notice on the floor."""
    client = app.test_client()
    _set_session(client, _flashes=[("error", "Silakan login terlebih dahulu")])

    page = client.get("/auth/login")

    assert page.status_code == 200
    assert "Silakan login terlebih dahulu" in page.get_data(as_text=True)


def test_login_user_page_renders_a_flashed_notice(app):
    client = app.test_client()
    _set_session(client, _flashes=[("error", "Sesi Anda berakhir")])

    page = client.get("/auth/login-user")

    assert page.status_code == 200
    assert "Sesi Anda berakhir" in page.get_data(as_text=True)


def test_a_flash_is_shown_once(app):
    """Consumed on render — it must not be printable again on a later page."""
    client = app.test_client()
    _set_session(client, _flashes=[("error", "sekali saja")])

    assert "sekali saja" in client.get("/auth/login").get_data(as_text=True)
    assert "sekali saja" not in client.get("/auth/login").get_data(as_text=True)


# ── 3. a finished session's notice must not outlive the login ────

def test_successful_login_drops_a_stale_expiry_notice(app, monkeypatch):
    monkeypatch.setattr(routes_auth, "_sign_in_with_retry",
                        lambda client, email, pw: _fake_login_result())
    monkeypatch.setattr(routes_auth, "log_activity", lambda *a, **k: None)
    app.extensions["supabase_auth"] = FakeSupabase({"role": "super_admin", "status": "active"})
    app.extensions["supabase"] = FakeSupabase({"role": "super_admin", "status": "active"})

    client = app.test_client()
    _set_session(client, _csrf_token="tok", _flashes=[("error", "Silakan login terlebih dahulu")])

    resp = client.post("/auth/login", data={
        "email": "superadmin@scan-grade.app",
        "password": "superadmin123",
        "_csrf_token": "tok",
    })

    assert resp.status_code == 302, resp.get_data(as_text=True)
    # The notice described a session that has just ended; it must not survive into
    # the new one and reappear on the page the user opens next.
    assert "_flashes" not in _read_session(client)


def test_successful_user_login_drops_a_stale_expiry_notice(app, monkeypatch):
    monkeypatch.setattr(routes_auth, "_sign_in_with_retry",
                        lambda client, email, pw: _fake_login_result())
    monkeypatch.setattr(routes_auth, "log_activity", lambda *a, **k: None)
    app.extensions["supabase_auth"] = FakeSupabase({"role": "murid", "status": "active"})
    app.extensions["supabase"] = FakeSupabase({"role": "murid", "status": "active"})

    client = app.test_client()
    _set_session(client, _csrf_token="tok", _flashes=[("error", "Silakan login terlebih dahulu")])

    resp = client.post("/auth/login-user", data={
        "email": "murid@scan-grade.app",
        "password": "demo123",
        "_csrf_token": "tok",
    })

    assert resp.status_code == 302, resp.get_data(as_text=True)
    assert "_flashes" not in _read_session(client)
