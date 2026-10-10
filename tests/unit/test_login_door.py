"""Every role is sent to the one sign-in page, opened on its own group.

The app had **two** login pages, each headed for its reader — "Masuk Admin" and
"Masuk Guru / Murid" — so landing a teacher on the admin one was a dead end they
could only escape by noticing the small link to the other. They are one page now
(``/auth/sign-in``), and the two URLs are aliases of it.

What the role still decides is the ``?role=`` hint. That hint is what opens the form
on the reader's own group — so a bookmark, a support script, or a redirect that has
just ended a session lands the reader *where they meant to go* instead of on a form
that explains nothing about them. It is also checkable without a browser, because
the page writes the tab it opened on into ``data-selected`` on the server.

That rule lives in ``login_door_for``, and every route that answers "you are not
signed in" — logout, ``_unauthorized``, the 401 handler — has to use it. None of
them may send a reader to an *alias*: a redirect is a hop the reader pays for
nothing, and the hint is exactly what a hop is apt to drop.

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
up*, and that the page they land on opens on their own group — because a redirect
that is merely role-*aware* can still be pointed at the wrong tab, or at a URL that
only forwards.
"""
import ast
import re
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.conftest import build_app
from app.utils import auth as authmod
from app.utils.auth import ALL_ROLES, LOGIN_URL, login_door_for, sign_in_tab
from app.routes import auth as routes_auth

ROOT = Path(__file__).resolve().parents[2]

#: The role list is the app's own (`DASHBOARD_FOR_ROLE`'s keys), not a copy: a seventh
#: role has to be covered by these tests the day it is given a home.
OLD_DOORS = ("/auth/login", "/auth/login-user")


def _hint(role: str) -> str:
    """The URL a reader of ``role`` must be sent to: the one page, on their tab."""
    return f"{LOGIN_URL}?role={role}"


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
    """Built once, and its own: the probe rule below has to be registered before
    this app has answered a request, which the shared app always has.

    The probe rule is added here rather than by a fixture of its own because a
    route cannot be registered after the app has handled its first request — and
    with a shared app, that first request belongs to whichever test runs first.
    """
    application = build_app("app.config.TestingConfig")
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
    @pytest.mark.parametrize("role", ALL_ROLES)
    def test_every_role_answers_the_one_page_with_its_own_hint(self, role):
        """All six, and the hint is the group — not the admin door for everything.

        The two doors made this a two-way question, so half the roles could only be
        tested by their *absence* from the other page. One page, one URL per role,
        and the hint is what is left of "their own door".
        """
        assert login_door_for(role) == _hint(role)

    @pytest.mark.parametrize("role", ALL_ROLES)
    def test_no_role_is_sent_to_an_alias(self, role):
        """An alias answers, and that is the trap: it *works*, one hop later.

        A reader whose session just ended is the one reader who has already spent
        their patience, and the hop is also where the role hint gets dropped — they
        arrive on a form that explains nothing about them.
        """
        url = login_door_for(role)
        assert url.split("?", 1)[0] not in OLD_DOORS, (
            f"{role} is sent to {url}, which only forwards to {LOGIN_URL}")

    @pytest.mark.parametrize("legacy,expected", [
        ("teacher", "guru"),
        ("student", "murid"),
        ("admin", "super_admin"),
    ])
    def test_the_old_role_names_still_map(self, legacy, expected):
        """``role_required`` accepts ``teacher``/``student``; so must this."""
        assert login_door_for(legacy) == _hint(expected)

    @pytest.mark.parametrize("path,role", [
        ("/student/dashboard", "murid"),
        ("/teacher/exams", "guru"),
        ("/principal/progress", "principal"),
        ("/vice-principal/dashboard", "vice_principal"),
        ("/admin-sekolah/teachers", "admin_sekolah"),
        ("/super-admin/dashboard", "super_admin"),
    ])
    def test_an_unknown_role_is_placed_by_the_path(self, path, role):
        """The URL being opened is the second-best guess, and it names a hint too."""
        assert login_door_for(path=path) == _hint(role)

    def test_a_known_role_beats_the_path(self):
        """The role is truth; the path is only a guess at who holds the URL."""
        assert login_door_for("murid", "/super-admin/schools") == _hint("murid")

    def test_the_hint_it_carries_is_one_the_page_can_place(self):
        """A hint the page cannot place opens on no tab at all, and says nothing.

        The page validates `?role=` instead of echoing it (`sign_in_tab` answers `""`
        for anything it does not know), so a redirect carrying a stale or misspelt
        role would land the reader on a form that explains nothing about them — which
        is the state the merge exists to remove. Asked through `sign_in_tab`, the
        function the template itself calls, so a role moved between groups cannot
        leave this test agreeing with a second opinion.
        """
        for role in ALL_ROLES:
            url = login_door_for(role)
            hinted = url.split("role=", 1)[1] if "role=" in url else ""
            assert sign_in_tab(hinted) != "", (
                f"{role} is sent to {url}, which the sign-in page cannot place")

    @pytest.mark.parametrize("called", [
        lambda: login_door_for(),
        lambda: login_door_for(path="/"),
        lambda: login_door_for(path="/auth/logout"),
        lambda: login_door_for("who_knows"),
    ])
    def test_nothing_known_means_no_hint_rather_than_a_guessed_one(self, called):
        """Same rule as `sign_in_tab`: an unplaceable reader is placed nowhere."""
        assert called() == LOGIN_URL


# ── 2. logout sends each role to its own door ─────────────────────

class TestLogoutSendsEachRoleHome:
    @pytest.mark.parametrize("role", ALL_ROLES)
    def test_each_role_lands_on_the_one_page_on_its_own_group(self, app, monkeypatch, role):
        client = _signed_in(app, monkeypatch, role)

        resp = client.get("/auth/logout")

        assert resp.status_code == 302
        assert resp.headers["Location"] == _hint(role)

    @pytest.mark.parametrize("role", ALL_ROLES)
    def test_the_door_it_lands_on_reaches_the_one_sign_in_page(self, app, monkeypatch, role):
        """The redirect is only right if its destination answers *and* opens on the
        reader's group.

        Two pages with two headings made "the page it lands on is headed for that
        role" a question about prose. One page answers it in structure: the tab the
        server marked `data-selected="true"` *is* the group. So a destination that
        answered but dropped the hint fails now — where reading the page for the word
        "Guru" would have passed no matter which tab was open, because every strip
        names every group. A door that 404s, or one that stops carrying the hint, is
        still the dead end this class was written for.
        """
        client = _signed_in(app, monkeypatch, role)

        location = client.get("/auth/logout").headers["Location"]
        page = client.get(location)

        assert page.status_code == 200, f"{role} landed on {location}, which does not answer"
        flat = re.sub(r"\s+", " ", page.get_data(as_text=True))
        assert f'data-tab="{sign_in_tab(role)}" data-selected="true"' in flat, (
            f"{role} landed on the sign-in page and it did not open on their group")

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
        assert resp.headers["Location"] == LOGIN_URL

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

        assert resp.headers["Location"] == _hint("guru")

    def test_no_token_at_all_still_logs_out(self, app, monkeypatch):
        monkeypatch.setattr(routes_auth, "log_activity", lambda *a, **k: None)

        resp = app.test_client().get("/auth/logout")

        assert resp.status_code == 302
        assert resp.headers["Location"] == LOGIN_URL

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
    @pytest.mark.parametrize("path,role", [
        ("/student/dashboard", "murid"),
        ("/teacher/exams", "guru"),
    ])
    def test_a_user_url_with_no_token_opens_the_page_on_that_groups_tab(self, app, path, role):
        resp = app.test_client().get(path)

        assert resp.status_code == 302
        assert resp.headers["Location"] == _hint(role)

    def test_an_idle_teacher_is_sent_to_the_teacher_tab(self, app, monkeypatch):
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
        assert resp.headers["Location"] == _hint("guru")
        # And the notice describing the expiry is readable where the reader lands.
        assert "60 menit" in client.get(_hint("guru")).get_data(as_text=True)

    def test_an_idle_admin_still_goes_to_the_admin_tab(self, app, monkeypatch):
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
        assert resp.headers["Location"] == _hint("super_admin")


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
    @pytest.mark.parametrize("path,role", [
        ("/student/results", "murid"),
        ("/teacher/exams", "guru"),
        ("/principal/progress", "principal"),
        ("/vice-principal/dashboard", "vice_principal"),
        ("/super-admin/schools", "super_admin"),
        ("/admin-sekolah/teachers", "admin_sekolah"),
    ])
    def test_the_url_decides_which_group_the_page_opens_on(self, app, path, role):
        """A 401 handler sees no session at all, so the path is all it has — and the
        path is already partitioned by role. Every prefix must be placed, officials
        included: left out, they would land on the admin tab, which is the
        misdirection this whole file exists for."""
        with app.test_request_context(path):
            resp = _handler_401(app)(SimpleNamespace())

        assert resp.status_code == 302
        assert resp.headers["Location"] == _hint(role)

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

        Prose is not a copy of a decision, so a docstring that *names* the page in
        order to explain it is not an offender — the door is spelled where it is
        chosen. Comments were already skipped for exactly that reason; docstrings
        were not, and two files that document which page a reader matches were
        therefore read as a second mapping. The prose is read out of the AST rather
        than guessed at with a triple-quote regex, which is the guess that would
        let one form of mention through while refusing another.
        """
        offenders = _app_py_door_lines(_LEARNER_DOOR)
        detail = "\n  ".join(f"{rel}:{line}  {text}" for rel, line, text in offenders)
        assert not offenders, (
            "the teacher/student door is spelled out outside app/utils/auth.py:\n  "
            + detail
        )

    def test_the_admin_door_is_spelled_out_in_one_place_only(self):
        """The other half of the same rule, and the half that was still open.

        ``login_door_for`` exists because every role belongs on the one page *with
        its own hint*; a route that writes ``/auth/login`` itself sends a teacher, a
        pupil or an official to a page that opens on the admin group. The reader is
        no longer turned away -- the aliases forward -- but they are told the wrong
        thing about themselves, which is the failure this whole file is about. The
        sweep is over every app module rather than the three that happened to hold
        copies (``admin_sekolah.py`` 22, ``teacher.py`` 3, ``student.py`` 1),
        because a list of files is how the next file is forgotten.
        """
        offenders = _app_py_door_lines(_ADMIN_DOOR)
        detail = "\n  ".join(f"{rel}:{line}  {text}" for rel, line, text in offenders)
        assert not offenders, (
            "the admin door is spelled out outside app/utils/auth.py -- hand the "
            "route login_door_for(g.get('user_role'), request.path) instead:\n  "
            + detail)

    def test_no_template_redirects_to_the_admin_door(self):
        """A page's own bounce is a door choice the Python sweep cannot see.

        ``teacher/scan.html`` sent a teacher whose session had expired to
        ``/auth/login``, and ``auth/reset_success.html`` spelled the door out from a
        role test that knew only ``guru`` -- so a pupil and both officials, whose
        passwords had just been reset, were handed the admin page. Both are the
        reader being told the wrong thing about themselves, in a template.
        """
        offenders = _template_door_lines(_ADMIN_DOOR)
        detail = "\n  ".join(f"{rel}:{line}  {text}" for rel, line, text in offenders)
        assert not offenders, (
            "a template hard-codes the admin door -- hand the page "
            "login_door_for(role, path) from its route instead:\n  " + detail)

    def test_a_docstring_about_the_door_is_not_read_as_a_second_copy(self):
        """The guard above asks where the door is *chosen*.

        Both halves are pinned here, because a skip that is too wide is worse than
        no skip at all: it would quietly stop reading the code it exists to read.
        """
        assert _docstring_lines('"""``/auth/login-user`` is the learner door."""\nX = 1\n') \
            == {1}
        assert _docstring_lines('def f():\n    """read /auth/login-user"""\n    pass\n') == {2}
        assert _docstring_lines('DOOR = "/auth/login-user"\n') == set()
        assert _docstring_lines('') == set()


#: The two doors, told apart by what follows. `/auth/login` is a *prefix* of
#: `/auth/login-user`, so a bare substring test for the admin door would report the
#: learner door too -- the lookahead is what keeps the two rules separate.
_ADMIN_DOOR = re.compile(r"/auth/login(?![\w-])")
_LEARNER_DOOR = re.compile(r"/auth/login-user")

#: Files that name a door for a reason that is not a door *choice*, listed so the
#: exception is a decision rather than an accident: the rate limiter exempts login
#: URLs from its flood bucket.
_DOOR_ALLOWED = ("app/utils/rate_limiter.py",)


def _app_py_door_lines(pattern):
    """[(relative path, line number, stripped line)] for app .py lines spelling a door.

    ``app/utils/auth.py`` is where the doors are *defined*, so it is not a reader of
    them. Prose is not a copy of a decision, so a docstring that names a page in
    order to explain it is skipped -- read out of the AST rather than guessed at with
    a triple-quote regex, which is the guess that would let one form of mention
    through while refusing another. Comments are skipped for the same reason.
    """
    offenders = []
    for path in sorted((ROOT / "app").rglob("*.py")):
        rel = str(path.relative_to(ROOT)).replace("\\", "/")
        if rel == "app/utils/auth.py" or rel in _DOOR_ALLOWED:
            continue
        text = path.read_text(encoding="utf-8")
        prose = _docstring_lines(text)
        for i, line in enumerate(text.splitlines(), 1):
            stripped = line.strip()
            if i in prose or stripped.startswith("#"):
                continue
            if pattern.search(line):
                offenders.append((rel, i, stripped[:90]))
    return offenders


def _template_door_lines(pattern):
    """[(relative path, line number, stripped line)] for templates spelling a door.

    A page's own bounce is a door *choice* too, and the Python sweep above cannot see
    it. Jinja's ``{# ... #}`` and HTML's ``<!-- ... -->`` are prose and skipped by the
    same rule: the door is spelled where it is chosen.
    """
    offenders = []
    for path in sorted((ROOT / "app" / "templates").rglob("*.html")):
        rel = str(path.relative_to(ROOT)).replace("\\", "/")
        text = path.read_text(encoding="utf-8")
        for i, code in _lines_outside_comments(text):
            if pattern.search(code):
                offenders.append((rel, i, code.strip()[:90]))
    return offenders


def _lines_outside_comments(text):
    """(line number, the part of the line that is not inside a comment).

    One comment can open and close on the same line, or open on one line and close
    several later; both shapes name the page without choosing it.
    """
    inside = None
    for number, line in enumerate(text.splitlines(), 1):
        kept = ""
        remainder = line
        while remainder:
            if inside is not None:
                if inside not in remainder:
                    remainder = ""
                    break
                remainder = remainder.split(inside, 1)[1]
                inside = None
                continue
            found = [(remainder.find(o), o, c)
                     for o, c in (("{#", "#}"), ("<!--", "-->"))
                     if o in remainder]
            if not found:
                break
            at, opener, closer = min(found)
            kept += remainder[:at]
            remainder = remainder[at + len(opener):]
            if closer in remainder:
                remainder = remainder.split(closer, 1)[1]
            else:
                inside = closer
                remainder = ""
        if (kept or remainder).strip():
            yield number, kept + remainder


def _docstring_lines(text):
    """The line numbers ``text`` spends on docstrings, the module's included.

    A docstring is an expression statement whose value is a string, and only in
    first position — a bare string anywhere else is code a reader has to see, so it
    stays in the sweep.
    """
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return set()
    lines = set()
    nodes = [tree] + [n for n in ast.walk(tree)
                      if isinstance(n, (ast.ClassDef, ast.FunctionDef,
                                        ast.AsyncFunctionDef))]
    for node in nodes:
        body = getattr(node, "body", None)
        if not body or not isinstance(body[0], ast.Expr):
            continue
        value = body[0].value
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            lines.update(range(value.lineno, (value.end_lineno or value.lineno) + 1))
    return lines


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
