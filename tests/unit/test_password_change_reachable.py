"""Every role can change its own password, and the change lands in Supabase.

The page exists (`/auth/change-password`), the rule exists
(`app/services/password_change.py`), the write exists (Supabase's admin API plus the
`profiles` mirror) — and until now **nothing linked to it**. `grep` over the whole
tree found route, template and tests, and not one `href` in the chrome every role
shares: the only way in was to type the URL, which is how a page that works is
reported as a page that does not exist.

Two things are held here, and they are the two halves of the request:

* **the door is in the shared chrome**, so a super admin, a school admin, a head
  teacher, a deputy head, a teacher and a pupil all reach it in one click, in the
  language they are reading;
* **the write is about the session's own account** — one `update_user_by_id` for
  `g.user_id` through Supabase's admin API, then the profile record that says the
  password changed. A role-specific path is the shape that goes wrong here: a pupil
  and a teacher resolve to the same `profiles.id`, so a second code path (or a second
  `where` clause) is how one role's change writes another role's row.
"""

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

BASE = ROOT / "app" / "templates" / "base.html"
ROUTES = ROOT / "app" / "routes" / "auth.py"

PATH = "/auth/change-password"
ROLES = ("super_admin", "admin_sekolah", "principal", "vice_principal", "guru", "murid")
FIELDS = ('name="current_password"', 'name="new_password"', 'name="confirm_password"')


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# ── 1. the door, in the chrome every role shares ─────────────────────────────

class TestTheDoor:
    def test_the_sidebar_links_to_it(self):
        """The block under `<!-- User -->` is drawn for every authenticated role."""
        chrome = _text(BASE)
        assert "<!-- User -->" in chrome
        sidebar = chrome.split("<!-- User -->", 1)[1].split("</aside>", 1)[0]
        assert f'href="{PATH}"' in sidebar, (
            "the sidebar every role shares has no way to the change-password page")

    def test_the_user_menu_links_to_it(self):
        chrome = _text(BASE)
        menu = chrome.split("<!-- User dropdown -->", 1)[1].split("</header>", 1)[0]
        assert f'href="{PATH}"' in menu, (
            "the top-right account menu is where people look for it, and it offers "
            "only Logout")

    def test_the_label_switches_language(self):
        """The label is translated — whether it is written as text or as a tooltip."""
        chrome = _text(BASE)
        for anchor, stop in (("<!-- User -->", "</aside>"),
                             ("<!-- User dropdown -->", "</header>")):
            block = chrome.split(anchor, 1)[1].split(stop, 1)[0]
            at = block.find(f'href="{PATH}"')
            assert at != -1, f"no change-password door {anchor}"
            # The whole element, not only its opening tag: the sidebar labels it with a
            # tooltip and the menu with a span, so reading up to the first `>` would
            # find the translated label in one and miss it in the other.
            link = block[at:block.index("</a>", at) + len("</a>")]
            assert 't(' in link, (
                f"the link beside {anchor} is a hard-coded label, so the door is "
                f"legible in only one of the two languages")

    def test_the_route_is_open_to_every_role(self):
        source = _text(ROUTES)
        at = source.index('@auth_bp.route("/change-password"')
        decorators = source[at:source.index("\ndef change_password", at)]
        assert "@login_required" in decorators, "an anonymous visitor must not reach it"
        assert not re.search(r"@(?!login_required)\w+_required", decorators), (
            "the page is gated by a role decorator, so some role cannot change its "
            "own password")

    def test_the_forced_change_can_actually_reach_it(self):
        """`must_change_password` redirects everything else, so this path is exempt."""
        auth = _text(ROOT / "app" / "utils" / "auth.py")
        assert "change-password" in auth, (
            "the forced-change redirect does not exempt this path, so a user with a "
            "printed password is redirected in a loop")


# ── 2. every role can open it and submit it ──────────────────────────────────

@pytest.fixture
def probe(app, monkeypatch):
    """Call the real view with a session, and record what reached Supabase."""
    from app.routes import auth as auth_mod

    written: dict = {}

    class FakeAdmin:
        def update_user_by_id(self, user_id, payload):
            written.setdefault("password", []).append((user_id, payload))

    class FakeAuthApi:
        """`client.auth` — the call shape the route uses is `client.auth.<method>`.

        Missed once and the suite refused to notice: a fake that puts
        `sign_in_with_password` on the *client* raises `AttributeError` inside the
        route's own `try`, which the route answers with "that current password is
        wrong" — so a fake of the wrong shape reads as a wrong password, and the
        write that never happened looks like a rule that refused it.
        """

        def sign_in_with_password(self, payload):
            if payload.get("password") != "correct-horse":
                raise RuntimeError("Invalid login credentials")
            return type("S", (), {"user": type("U", (), {"id": "u-1"})()})

        def sign_out(self):
            written["signed_out"] = True

    class FakeAuth:
        def __init__(self):
            self.auth = FakeAuthApi()

    class FakeTable:
        def update(self, fields):
            written.setdefault("profile", []).append(fields)
            return self

        def eq(self, *a):
            return self

        def execute(self):
            return type("R", (), {"data": []})()

    class FakeSupabase:
        """The one client the route uses: the admin API *and* the profile table."""

        def __init__(self):
            self.auth = type("A", (), {"admin": FakeAdmin()})()

        def table(self, name):
            assert name == "profiles", f"the change wrote to {name}"
            return FakeTable()

    monkeypatch.setattr(auth_mod, "get_auth_client", lambda: FakeAuth())
    monkeypatch.setattr(auth_mod, "get_supabase", lambda: FakeSupabase())
    monkeypatch.setattr(auth_mod, "log_activity", lambda *a, **k: None)
    monkeypatch.setattr(auth_mod, "invalidate_session", lambda *a, **k: None)

    #: The view without `login_required`'s wrapper. The door itself is guarded by
    #: `TestTheDoor` and by the session suite; what is measured here is what the view
    #: does with a session it has been handed.
    view = auth_mod.change_password.__wrapped__

    def call(role, *, current="correct-horse", new="RahasiaBaru#1", confirm=None):
        from flask import g

        with app.test_request_context(PATH, method="POST", data={
                "current_password": current, "new_password": new,
                "confirm_password": new if confirm is None else confirm}):
            g.user_id, g.user_role = "u-1", role
            g.user_email, g.user_name = "someone@school.id", "Nama Uji"
            g.user_school_id, g.tz_offset = "sch-1", 7
            g.show = {}
            written.clear()
            return view(), written

    return call


class TestEveryRole:
    @pytest.mark.parametrize("role", ROLES)
    def test_the_page_renders_the_form_for_the_role(self, app, role):
        from flask import g

        with app.test_request_context(PATH):
            g.user_id, g.user_role = "u-1", role
            g.user_name, g.user_email = "Nama Uji", "someone@school.id"
            g.user_school_id, g.tz_offset, g.show = "sch-1", 7, {}
            body = app.jinja_env.get_template("auth/change_password.html").render()
        for field in FIELDS:
            assert field in body, f"{role} is shown a page with no {field}"

    @pytest.mark.parametrize("role", ROLES)
    def test_the_change_is_written_to_supabase_for_the_session_s_own_account(self, probe, role):
        response, written = probe(role)
        assert written.get("password"), f"{role}'s change never reached Supabase"
        user_id, payload = written["password"][0]
        assert user_id == "u-1", (
            "the write must target the session's own account, never a role-derived one")
        assert payload == {"password": "RahasiaBaru#1"}
        assert written.get("profile"), "the profile record was never written"
        assert written["profile"][0].get("must_change_password") is False
        assert response.status_code == 302, "the reader is sent to their own login door"

    @pytest.mark.parametrize("role", ROLES)
    def test_a_wrong_current_password_writes_nothing(self, probe, role):
        _, written = probe(role, current="not-it")
        assert "password" not in written and "profile" not in written, (
            "a session on a shared laptop is otherwise enough to take the account over")

    def test_a_mismatched_confirmation_writes_nothing(self, probe):
        _, written = probe("guru", confirm="beda-sekali")
        assert "password" not in written

    def test_the_known_default_is_refused_by_name(self, probe):
        _, written = probe("murid", new="siswa123", confirm="siswa123")
        assert "password" not in written, (
            "the password the whole school already has is not a change")


# ── 3. the page it lands on, and the record ──────────────────────────────────

class TestThePageAfterwards:
    def test_the_success_path_clears_the_session(self, probe):
        response, written = probe("murid")
        assert written.get("signed_out") is True, (
            "the whole point of a one-time password is that the next sign-in uses the "
            "new one — and nothing else here can prove the new password works")
        assert response.status_code == 302

    def test_the_page_is_not_the_admin_area_s(self):
        """A pupil's change must not depend on the admin seat being present."""
        source = _text(ROUTES)
        assert "login_door_for(g.user_role" in source, (
            "every role is sent back to its own door, so a pupil does not land on the "
            "admin login with credentials that do not work there")


if __name__ == "__main__":                                   # pragma: no cover
    sys.exit(pytest.main([__file__, "-q"]))
