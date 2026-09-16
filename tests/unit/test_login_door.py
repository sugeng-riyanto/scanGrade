"""Every role gets its own door back — on logout and on an expired session.

The app has **two** login pages, and each says out loud who it is for:
``/auth/login`` is headed "Masuk Admin" and ``/auth/login-user`` is headed
"Masuk Guru / Murid". Landing a teacher or a student on the admin door is a dead
end they can only escape by noticing the small "Guru/Murid?" link.

``logout()`` already contained the intent —

    redirect("/auth/login-user" if g.get("user_role") in ("guru", "murid") else "/auth/login")

— and it never once fired. ``g.user_role`` is filled by ``login_required`` and by
nothing else, and logout deliberately sits outside that decorator (clearing the
cookies has to work for a session that has already ended). So ``g.get`` answered
``None`` for every request and all four roles were sent to the admin door.

The same misdirection applied to ``_unauthorized()``, which hardcoded
``/auth/login``: a teacher whose session timed out was told to log in again on a
page headed "Masuk Admin".

These tests drive the real routes as each role and assert *where the reader ends
up* — and that the page they land on is the one headed for their role — because a
redirect that is merely role-*aware* can still be pointed at the wrong page.
"""
import re
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import create_app
from app.utils import auth as authmod
from app.utils.auth import login_door_for
from app.routes import auth as routes_auth

ROOT = Path(__file__).resolve().parents[2]

ADMIN_HEADING = "Masuk Admin"
USER_HEADING = "Masuk Guru / Murid"

USER_ROLES = ("guru", "murid")
ADMIN_ROLES = ("super_admin", "admin_sekolah")
ALL_ROLES = ADMIN_ROLES + USER_ROLES


# ── fakes ─────────────────────────────────────────────────────────

class FakeQuery:
    """``select(...).eq(...).single().execute()`` for the profiles lookup."""

    def __init__(self, row):
        self.row = row

    def select(self, *a, **k):
        return self

    def eq(self, *a, **k):
        return self

    def filter(self, *a, **k):
        return self

    def limit(self, *a, **k):
        return self

    def single(self):
        return self

    def execute(self):
        return SimpleNamespace(data=self.row)


class _FakeAuth:
    def sign_out(self):
        return None


class FakeSupabase:
    def __init__(self, row=None):
        self.row = row
        self.auth = _FakeAuth()

    def table(self, name):
        return FakeQuery(self.row)


@pytest.fixture(scope="module")
def app():
    """Built once: ``create_app`` is the expensive part, and nothing here mutates it.

    The probe rule is added here rather than by a fixture of its own because a
    route cannot be registered after the app has handled its first request — and
    with a shared app, that first request belongs to whichever test runs first.
    """
    application = create_app("app.config.TestingConfig")
    application.config["RATELIMIT_ENABLED"] = False
    application.extensions["supabase_auth"] = FakeSupabase()
    application.extensions["supabase"] = FakeSupabase()

    # A route guarded by nothing but ``login_required``, so the session-timeout
    # checks are exercised without any role or school noise.
    def _probe():
        return "ok"

    application.add_url_rule(
        "/_probe_protected", "_probe_protected", authmod.login_required(_probe)
    )
    return application


def _signed_in(app, monkeypatch, role):
    """A client holding a token whose session resolves to ``role``."""
    monkeypatch.setattr(authmod, "_session_for", lambda token, _r=role: {
        "user_id": "u-1", "email": "u@x", "name": "U", "role": _r,
        "school_id": None, "class_id": None, "status": "active",
    })
    monkeypatch.setattr(routes_auth, "log_activity", lambda *a, **k: None)
    client = app.test_client()
    client.set_cookie("access_token", "tok")
    return client


# ── 1. one mapping, and it is the role that decides ───────────────

class TestTheMapping:
    @pytest.mark.parametrize("role", USER_ROLES)
    def test_a_user_role_gets_the_teacher_student_door(self, role):
        assert login_door_for(role) == "/auth/login-user"

    @pytest.mark.parametrize("role", ADMIN_ROLES)
    def test_an_admin_role_gets_the_admin_door(self, role):
        assert login_door_for(role) == "/auth/login"

    @pytest.mark.parametrize("legacy,expected", [
        ("teacher", "/auth/login-user"),
        ("student", "/auth/login-user"),
        ("admin", "/auth/login"),
    ])
    def test_the_old_role_names_still_map(self, legacy, expected):
        """``role_required`` accepts ``teacher``/``student``; so must this."""
        assert login_door_for(legacy) == expected

    @pytest.mark.parametrize("path,expected", [
        ("/student/dashboard", "/auth/login-user"),
        ("/teacher/exams", "/auth/login-user"),
        ("/admin-sekolah/teachers", "/auth/login"),
        ("/super-admin/dashboard", "/auth/login"),
    ])
    def test_an_unknown_role_falls_back_to_the_path(self, path, expected):
        assert login_door_for(path=path) == expected

    def test_a_known_role_beats_the_path(self):
        """The role is truth; the path is only a guess at who holds the URL."""
        assert login_door_for("murid", "/super-admin/schools") == "/auth/login-user"

    @pytest.mark.parametrize("called", [
        lambda: login_door_for(),
        lambda: login_door_for(path="/"),
        lambda: login_door_for(path="/auth/logout"),
        lambda: login_door_for("who_knows"),
    ])
    def test_nothing_known_means_the_door_everyone_can_reach(self, called):
        assert called() == "/auth/login"


# ── 2. logout sends each role to its own door ─────────────────────

class TestLogoutSendsEachRoleHome:
    @pytest.mark.parametrize("role", USER_ROLES)
    def test_a_user_role_lands_on_the_teacher_student_login(self, app, monkeypatch, role):
        client = _signed_in(app, monkeypatch, role)

        resp = client.get("/auth/logout")

        assert resp.status_code == 302
        assert resp.headers["Location"] == "/auth/login-user"

    @pytest.mark.parametrize("role", ADMIN_ROLES)
    def test_an_admin_role_lands_on_the_admin_login(self, app, monkeypatch, role):
        client = _signed_in(app, monkeypatch, role)

        resp = client.get("/auth/logout")

        assert resp.status_code == 302
        assert resp.headers["Location"] == "/auth/login"

    @pytest.mark.parametrize("role", ALL_ROLES)
    def test_the_page_it_lands_on_is_headed_for_that_role(self, app, monkeypatch, role):
        """The redirect is only right if the destination says so out loud."""
        client = _signed_in(app, monkeypatch, role)

        location = client.get("/auth/logout").headers["Location"]
        body = client.get(location).get_data(as_text=True)

        expected = USER_HEADING if role in USER_ROLES else ADMIN_HEADING
        assert expected in body, f"{role} landed on a page headed for someone else"

    def test_a_session_that_cannot_be_resolved_still_logs_out(self, app, monkeypatch):
        """An expired token is exactly when someone wants to log out."""
        def _boom(token):
            raise ValueError("expired")

        monkeypatch.setattr(authmod, "_session_for", _boom)
        monkeypatch.setattr(routes_auth, "log_activity", lambda *a, **k: None)
        client = app.test_client()
        client.set_cookie("access_token", "stale")

        resp = client.get("/auth/logout")

        assert resp.status_code == 302
        assert resp.headers["Location"] == "/auth/login"

    def test_an_expired_token_still_has_a_role_from_the_cache(self, app, monkeypatch):
        """The reason ``session_role`` reads the cache before resolving.

        A guru clicks logout after the access token has expired: the resolver
        refuses the token, but the cached session — written by the page they were
        just on — still says who they are. Reading only the resolver would lose
        that and send a teacher to the admin door at the exact moment it matters.
        """
        from app.utils.kv_cache import cache_delete, cache_set
        from app.utils.auth import _session_key

        def _boom(token):
            raise ValueError("access token expired")

        monkeypatch.setattr(authmod, "_session_for", _boom)
        monkeypatch.setattr(routes_auth, "log_activity", lambda *a, **k: None)
        cache_set(_session_key("stale"), {"user_id": "g-1", "role": "guru"}, 30)
        try:
            client = app.test_client()
            client.set_cookie("access_token", "stale")

            resp = client.get("/auth/logout")
        finally:
            cache_delete(_session_key("stale"))

        assert resp.headers["Location"] == "/auth/login-user"

    def test_no_token_at_all_still_logs_out(self, app, monkeypatch):
        monkeypatch.setattr(routes_auth, "log_activity", lambda *a, **k: None)

        resp = app.test_client().get("/auth/logout")

        assert resp.status_code == 302
        assert resp.headers["Location"] == "/auth/login"

    @pytest.mark.parametrize("role", ALL_ROLES)
    def test_logging_out_still_removes_the_session_cookies(self, app, monkeypatch, role):
        """Reading the role first must not turn logout into a no-op."""
        client = _signed_in(app, monkeypatch, role)

        resp = client.get("/auth/logout")
        cleared = " ".join(resp.headers.getlist("Set-Cookie")).replace("; ", ";")

        assert "access_token=;" in cleared
        assert "refresh_token=;" in cleared


# ── 3. the same rule on the "your session ended" path ─────────────

class TestAnExpiredSessionUsesTheSameDoor:
    @pytest.mark.parametrize("path,expected", [
        ("/student/dashboard", "/auth/login-user"),
        ("/teacher/exams", "/auth/login-user"),
    ])
    def test_a_user_url_with_no_token_shows_the_user_door(self, app, path, expected):
        resp = app.test_client().get(path)

        assert resp.status_code == 302
        assert resp.headers["Location"] == expected

    def test_an_idle_teacher_is_sent_to_the_teacher_door(self, app, monkeypatch):
        """The role is known by then — ``_apply_session`` runs before the check."""
        monkeypatch.setattr(authmod, "_session_for", lambda token: {
            "user_id": "g-1", "email": "g@x", "name": "Guru", "role": "guru",
            "school_id": None, "class_id": None, "status": "active",
        })
        client = app.test_client()
        client.set_cookie("access_token", "tok")
        client.set_cookie("last_activity", str(time.time() - 2 * 3600))
        client.set_cookie("session_start", str(time.time() - 2 * 3600))

        resp = client.get("/_probe_protected")

        assert resp.status_code == 302, "the session must still be refused"
        assert resp.headers["Location"] == "/auth/login-user"
        # And the notice describing the expiry is readable where the reader lands.
        assert "60 menit" in client.get("/auth/login-user").get_data(as_text=True)

    def test_an_idle_admin_still_goes_to_the_admin_door(self, app, monkeypatch):
        monkeypatch.setattr(authmod, "_session_for", lambda token: {
            "user_id": "sa-1", "email": "sa@x", "name": "SA", "role": "super_admin",
            "school_id": None, "class_id": None, "status": "active",
        })
        client = app.test_client()
        client.set_cookie("access_token", "tok")
        client.set_cookie("last_activity", str(time.time() - 3600))
        client.set_cookie("session_start", str(time.time() - 3600))

        resp = client.get("/_probe_protected")

        assert resp.status_code == 302, "the session must still be refused"
        assert resp.headers["Location"] == "/auth/login"


# ── 4. the 401 handler says the same thing ────────────────────────

def _handler_401(app):
    """The 401 view Flask has registered for this app.

    ``errorhandler(401)`` is registered by *code*, so Flask keys it under
    ``None`` as the exception class (a handler registered for an exception class
    instead would be keyed by that class).
    """
    for code, handlers in app.error_handler_spec[None].items():
        if code == 401:
            for view in handlers.values():
                return view
    raise AssertionError("no 401 handler registered")


class TestThe401Handler:
    @pytest.mark.parametrize("path,expected", [
        ("/student/results", "/auth/login-user"),
        ("/teacher/exams", "/auth/login-user"),
        ("/super-admin/schools", "/auth/login"),
        ("/admin-sekolah/teachers", "/auth/login"),
    ])
    def test_the_url_decides_which_door(self, app, path, expected):
        with app.test_request_context(path):
            resp = _handler_401(app)(SimpleNamespace())

        assert resp.status_code == 302
        assert resp.headers["Location"] == expected

    def test_a_json_caller_still_gets_a_json_401(self, app):
        with app.test_request_context("/student/results",
                                      headers={"Accept": "application/json"}):
            body, status = _handler_401(app)(SimpleNamespace())

        assert status == 401
        assert body.get_json()["error"] == "UNAUTHORIZED"


# ── 5. the rule lives in one place ────────────────────────────────

class TestOneMappingOwnsTheDoors:
    def test_logout_resolves_the_role_from_the_token_not_from_g(self):
        """The original defect, stated as a rule.

        ``g.user_role`` is empty on this route by construction, so a future edit
        that reads it here silently restores "always the admin door".
        """
        body = _function_source("logout")
        assert "session_role(" in body, "logout no longer resolves the role from the token"
        assert "login_door_for(" in body, "logout no longer uses the shared mapping"
        assert 'g.get("user_role")' not in body, (
            "logout reads g.user_role again — nothing fills it on this route"
        )

    def test_unauthorized_uses_the_mapping(self):
        body = _function_source("_unauthorized", module="app/utils/auth.py")
        assert "login_door_for(" in body
        assert '"/auth/login"' not in body

    def test_the_401_handler_uses_the_mapping(self):
        body = _function_source("unauthorized", module="app/handlers/error_handlers.py")
        assert "login_door_for(" in body
        assert '"/auth/login"' not in body

    def test_the_teacher_student_door_is_spelled_out_in_one_place_only(self):
        """A second copy is a second thing to keep in sync.

        Two files name both login pages for reasons that are not a door choice,
        and they are listed so the exception is a decision rather than an
        accident: the rate limiter exempts login *URLs* from its flood bucket,
        and the smoke test walks each role's page by URL.
        """
        allowed = {
            "app/utils/rate_limiter.py",   # an exempt-path set, not a door choice
        }
        offenders = []
        for path in sorted((ROOT / "app").rglob("*.py")):
            rel = str(path.relative_to(ROOT)).replace("\\", "/")
            if rel == "app/utils/auth.py" or rel in allowed:
                continue
            for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                stripped = line.strip()
                if stripped.startswith("#"):
                    continue
                if "/auth/login-user" in line:
                    offenders.append(f"{rel}:{i}  {stripped[:90]}")
        assert not offenders, (
            "the teacher/student door is spelled out outside app/utils/auth.py:\n  "
            + "\n  ".join(offenders)
        )


def _function_source(name, module="app/routes/auth.py"):
    """The source of ``def name(...)``, at whatever indentation it lives.

    Handles a nested definition too — ``unauthorized`` sits inside
    ``register_error_handlers`` — by carrying the indentation it was found at
    into the lookahead that ends the match.
    """
    source = (ROOT / module).read_text(encoding="utf-8")
    match = re.search(
        rf"^([ \t]*)def {name}\(.*?\n(?=\1(?:@|def )|\Z)", source, re.S | re.M
    )
    assert match, f"could not find {name} in {module}"
    return match.group(0)
