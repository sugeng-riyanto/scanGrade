"""One page signs everyone in — and the published links have to keep working.

There were two login pages. Which one a reader belonged on was a property of their
role, so every path that answered "you are not signed in" had to name one, and
naming the wrong one was a dead end they could only escape by spotting the small
link to the other. The pages are one now, reached at `/auth/sign-in`, and the two
old URLs are aliases of it — because they are *published*: `/tutorial/guru`,
`/tutorial/murid`, `/tutorial/admin-sekolah` and five cards on `/demo` link to
them, schools have them bookmarked, and a printed login card names one.

Four things are asserted here, and each is a way this merge could be wrong rather
than a way it could look wrong:

* **Every link the public pages actually contain** still lands on the form — read
  out of the templates, not off a list, so a page that grows a link is covered by
  it. And it has to land *directly*: an alias is a redirect, which is a round trip
  a prospective customer pays for no reason.
* **The old doors forward their parameters.** `?role=` and `?next=` are what the
  tutorial pages and the demo cards carry; dropping either is how the merge breaks
  a link that was working.
* **`?role=` selects the tab, and nothing more.** It is server-rendered, so a test
  can see it without a browser, and an unrecognised value selects nothing rather
  than being echoed back.
* **There is one refusal and it says nothing.** An unknown identifier, a wrong
  password and an account whose role this app does not have are the same sentence,
  and neither of the two *old* sentences — each of which named the other page, and
  therefore the reader's role — survives.
"""
from __future__ import annotations

import inspect
import json
import re
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest

from app.routes import auth as routes_auth
from app.utils.auth import (ALL_ROLES, DASHBOARD_FOR_ROLE, LOGIN_URL,
                            SIGN_IN_TABS, SIGN_IN_TAB_FOR_ROLE)

ROOT = Path(__file__).resolve().parents[2]
TEMPLATES = ROOT / "app" / "templates"

NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="needs node to run the expression")

#: The pages that publish a link to the sign-in page. `/tutorial/*` are the three
#: walkthroughs, `/demo` is the page handed to prospective schools.
PUBLIC_PAGES = ("tutorial_guru.html", "tutorial_murid.html",
                "tutorial_admin_sekolah.html", "demo.html")

#: The same pages as a reader opens them, so the two halves of the contract — the
#: pages answer, and their links land on the form — are checked on the same four.
PUBLIC_ROUTES = {
    "tutorial_guru.html": "/tutorial/guru",
    "tutorial_murid.html": "/tutorial/murid",
    "tutorial_admin_sekolah.html": "/tutorial/admin-sekolah",
    "demo.html": "/demo",
}

#: An `href` or a `login_link('…')` on those pages, with its query string.
LOGIN_LINK = re.compile(r"""(?:href="|login_link\(\s*')(/auth/[^"'?]+)(\?[^"']*)?""")


def public_login_links() -> list[str]:
    """Every `/auth/...` link the four public pages contain, as written."""
    found = []
    for name in PUBLIC_PAGES:
        text = (TEMPLATES / name).read_text(encoding="utf-8")
        for path, query in LOGIN_LINK.findall(text):
            found.append(path + query)
    assert found, "no login links found on the public pages — has the markup moved?"
    return sorted(set(found))


def flatten(html: str) -> str:
    """The page with runs of whitespace collapsed, so an attribute pair can be
    looked for across a line break (the tabs are rendered one attribute per line)."""
    return re.sub(r"\s+", " ", html)


# ── fakes (the same shape test_session_expiry_notice drives the doors with) ──

class FakeQuery:
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


class FakeSupabase:
    def __init__(self, row=None):
        self.row = row
        self.auth = SimpleNamespace(admin=SimpleNamespace(
            list_users=lambda: [], get_user_by_id=lambda _id: None))

    def table(self, name):
        return FakeQuery(self.row)


def _login_result(role: str | None = "guru", user_id: str = "u-1"):
    """A token response. ``role=None`` leaves the account's metadata without one, which
    is the state that tells an unreadable profile row apart from an empty role."""
    metadata = {"full_name": "Who"}
    if role:
        metadata["role"] = role
    user = SimpleNamespace(id=user_id, email="who@example.org", user_metadata=metadata)
    return SimpleNamespace(user=user,
                           session=SimpleNamespace(access_token="tok", refresh_token="rtok"))


@pytest.fixture(scope="module")
def app():
    from tests.conftest import build_app

    application = build_app("app.config.TestingConfig")
    application.config["RATELIMIT_ENABLED"] = False
    return application


def _prepare(app, monkeypatch, role="guru", result=None, limit=(True, 0)):
    """A client ready to POST, with the profile row and the token response faked."""
    app.extensions["supabase"] = FakeSupabase({"role": role, "status": "active"})
    app.extensions["supabase_auth"] = FakeSupabase()
    monkeypatch.setattr(routes_auth, "_sign_in_with_retry",
                        lambda client, email, pw: result or _login_result(role))
    monkeypatch.setattr(routes_auth, "log_activity", lambda *a, **k: None)
    monkeypatch.setattr(routes_auth, "check_account_limit", lambda *a, **k: limit)
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["_csrf_token"] = "tok"
    return client


def _post(client, url=LOGIN_URL, identifier="who@example.org", password="pw"):
    return client.post(url, data={"email": identifier, "password": password,
                                  "_csrf_token": "tok"})


# ── 1. the published links, read from the pages themselves ───────────────────

def test_every_public_login_link_points_at_the_one_page_and_lands_on_it(app):
    """Both halves, because either alone would pass while the merge was half done.

    `link.split("?")[0] == LOGIN_URL` is the "no redirect hop" half — an alias
    works, but it costs a round trip that a visitor deciding whether to buy does
    not need to pay. The GET is the "not broken" half: a link that 404s or lands
    somewhere else is worse than a slow one.
    """
    for link in public_login_links():
        assert link.split("?")[0] == LOGIN_URL, (
            f"{link} still points at an old door, so the reader pays a redirect. "
            f"Point it at {LOGIN_URL} with the same `?role=`.")

        page = app.test_client().get(link)
        assert page.status_code == 200, f"{link} did not land on a page"
        assert 'action="/auth/sign-in"' in page.get_data(as_text=True), (
            f"{link} did not land on the one sign-in form")


def test_the_public_pages_themselves_still_load(app):
    """The other half of the same contract: the pages that carry the links.

    They are the sales surface — a prospective school reads the tutorials and the
    demo — so a page that stopped answering after the merge is the failure a reader
    meets first, before any link on it is ever clicked.
    """
    for name, route in PUBLIC_ROUTES.items():
        resp = app.test_client().get(route)
        assert resp.status_code == 200, f"{route} ({name}) answered {resp.status_code}"


def test_a_role_link_opens_the_page_on_that_roles_tab(app):
    """The hint is the whole reason the role is in the URL."""
    role_links = [link for link in public_login_links() if "role=" in link]
    assert role_links, "no role-carrying links found — the demo cards carry them"

    for link in role_links:
        role = re.search(r"[?&]role=([a-z_]+)", link).group(1)
        tab = SIGN_IN_TAB_FOR_ROLE[role]
        flat = flatten(app.test_client().get(link).get_data(as_text=True))

        assert f'data-tab="{tab}" data-selected="true"' in flat, (
            f"{link} landed without its group's tab selected")
        for other, _roles in SIGN_IN_TABS:
            if other != tab:
                assert f'data-tab="{other}" data-selected="false"' in flat, (
                    f"{link} selected {other} as well as {tab}")


# ── 2. the old doors forward, and they still sign people in ──────────────────

@pytest.mark.parametrize("door", ["/auth/login", "/auth/login-user"])
def test_an_old_door_forwards_to_the_one_page(app, door):
    resp = app.test_client().get(door)

    assert resp.status_code == 302, "an old door must not render a second form"
    assert resp.headers["Location"] == LOGIN_URL


@pytest.mark.parametrize("query,expected", [
    ("?role=guru", {"role": ["guru"]}),
    ("?role=murid", {"role": ["murid"]}),
    ("?role=principal", {"role": ["principal"]}),
    ("?role=vice_principal", {"role": ["vice_principal"]}),
    ("?next=%2Fstudent%2Fdashboard", {"next": ["/student/dashboard"]}),
    ("?role=murid&next=%2Fstudent%2Fresults",
     {"role": ["murid"], "next": ["/student/results"]}),
])
def test_an_old_door_carries_the_parameters_it_was_given(app, query, expected):
    """`/tutorial/guru` and four `/demo` cards publish exactly these.

    Dropping one is how a link that worked before the merge starts opening the page
    with no idea who the reader is. The *value* is asserted rather than its spelling:
    a browser percent-encodes `next` coming in, Flask does not going out, and a test
    that pinned the encoding would be asserting which library wrote the redirect.
    """
    location = app.test_client().get("/auth/login-user" + query).headers["Location"]
    assert location.startswith(LOGIN_URL)

    carried = parse_qs(urlsplit(location).query)
    assert carried == expected, f"{query} came through as {carried}"


def test_an_old_door_still_signs_a_reader_in(app, monkeypatch):
    """A POST may not be answered with a redirect: that throws the credentials away."""
    client = _prepare(app, monkeypatch, role="guru")

    resp = client.post("/auth/login-user", data={
        "email": "who@example.org", "password": "pw", "_csrf_token": "tok"})

    assert resp.status_code == 302
    assert resp.headers["Location"] == DASHBOARD_FOR_ROLE["guru"]


# ── 3. the role is matched against every role, not against the tab ───────────

@pytest.mark.parametrize("role", sorted(ALL_ROLES))
def test_every_role_signs_in_through_the_one_endpoint(app, monkeypatch, role):
    """All six, not the four the brief started from: the matrix is the code's."""
    client = _prepare(app, monkeypatch, role=role)

    resp = _post(client)

    assert resp.status_code == 302, resp.get_data(as_text=True)
    assert resp.headers["Location"] == DASHBOARD_FOR_ROLE[role]


@pytest.mark.parametrize("tab_role", sorted(ALL_ROLES))
def test_the_tab_a_reader_arrived_on_never_narrows_the_search(app, monkeypatch, tab_role):
    """A tab is a hint the reader chose; the account is what the server trusts.

    Driven the way the bug would happen: arrive on the *pupil* tab and sign in as an
    administrator. If the tab reached the backend as a claim, this is the request
    that would be refused.
    """
    client = _prepare(app, monkeypatch, role=tab_role)
    client.get(LOGIN_URL + "?role=murid")     # the page's own hint: pupil

    resp = _post(client)

    assert resp.status_code == 302, resp.get_data(as_text=True)
    assert resp.headers["Location"] == DASHBOARD_FOR_ROLE[tab_role], (
        f"a {tab_role} account was routed by the tab instead of by its role")


# ── 3b. the keyboard a group opens on, and the way out of it ────────────────

INPUTMODE = re.compile(r':inputmode="([^"]*)"')


def _inputmode_expression(app) -> str:
    """The identifier field's `:inputmode`, read out of the render.

    Read rather than written down here: the attribute is the contract, and a copy of
    the expression in the test would keep passing while the page lost it.
    """
    html = app.test_client().get(LOGIN_URL + "?role=murid").get_data(as_text=True)
    match = INPUTMODE.search(flatten(html))
    assert match, "the identifier field no longer says which keyboard to open"
    return match.group(1)


def _evaluate(expression: str, role: str, email_mode: bool) -> str:
    """The expression, run as the browser would run it.

    Alpine evaluates it with `role` and `emailMode` in scope, so a reader of the code
    cannot tell a branch that works from one that never fires — the same reason the
    repo runs the exam page's JavaScript instead of grepping for it.
    """
    script = (
        f"const expr = {json.dumps(expression)};\n"
        "function value(role, emailMode) { return eval(expr); }\n"
        f"console.log(JSON.stringify({{v: value({json.dumps(role)}, "
        f"{'true' if email_mode else 'false'})}}));\n"
    )
    done = subprocess.run([NODE, "-e", script], capture_output=True, text=True,
                          timeout=30)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)["v"]


@needs_node
@pytest.mark.parametrize("role,email_mode,expected", [
    ("murid", False, "numeric"),
    ("murid", True, "text"),
    ("guru", False, "text"),
    ("staff", False, "text"),
    ("admin", False, "email"),
])
def test_the_keyboard_a_group_opens_on(app, role, email_mode, expected):
    """A pupil's card carries a NISN, so their group opens on a digits keypad.

    The admin and teacher groups keep the full keyboard, because an address and a
    `NIP` both start with letters and a digits keypad would make them untypable.
    """
    assert _evaluate(_inputmode_expression(app), role, email_mode) == expected


def test_the_digits_keypad_is_never_a_dead_end(app):
    """Why the pupil group may be allowed a digits-only keyboard at all.

    Every login card this app prints carries an **email** beside the NISN
    (`login_cards.COLUMNS`), and `_identifier_email` reads an address first — so a
    keypad with no letters would leave a pupil unable to type the credential on
    their own card, which is the "the group you picked blocked you" failure this
    merge exists to remove. The escape is a visible tap, not a hidden one, and it is
    offered only while the keypad it escapes is up.
    """
    from app.services.login_cards import COLUMNS

    assert "email" in [key for key, _label in COLUMNS], (
        "the printed cards no longer carry an email, so the way out may be unnecessary "
        "— but check why before deleting it: `_identifier_email` reads '@' first")

    flat = flatten(app.test_client().get(LOGIN_URL + "?role=murid")
                   .get_data(as_text=True))
    assert "@click=\"emailMode = true\"" in flat, (
        "the pupil group's digits keypad has no way out for a pupil whose card "
        "carries an email")
    assert "x-show=\"role === 'murid' && !emailMode\"" in flat, (
        "the escape is offered to a reader whose keyboard already has letters")


# ── 4. one refusal, and it says nothing ─────────────────────────────────────

GENERIC = "Email atau password salah"
OLD_DOOR_SENTENCES = (
    "Halaman ini untuk Admin",             # login_wrong_page
    "Halaman ini untuk Guru/Murid",        # login_user_wrong_page
)


def _refusal_body(app, monkeypatch, identifier="nobody@example.org", role_for_profile=None,
                  raises=True):
    if raises:
        app.extensions["supabase"] = FakeSupabase({"role": "guru", "status": "active"})
        app.extensions["supabase_auth"] = FakeSupabase()
        # The wording matters to the *classifier*, not to the test: only a refusal
        # that reads as a credential one is counted against the account's budget.
        # `Exception("invalid login")` is classified transient, which is the case
        # `test_a_profile_row_the_server_cannot_read…` covers.
        monkeypatch.setattr(routes_auth, "_sign_in_with_retry",
                            lambda *a, **k: (_ for _ in ()).throw(
                                Exception("Invalid login credentials")))
        monkeypatch.setattr(routes_auth, "log_activity", lambda *a, **k: None)
        monkeypatch.setattr(routes_auth, "check_account_limit", lambda *a, **k: (True, 0))
        client = app.test_client()
        with client.session_transaction() as sess:
            sess["_csrf_token"] = "tok"
    else:
        client = _prepare(app, monkeypatch, role=role_for_profile)
    return _post(client, identifier=identifier).get_data(as_text=True)


def test_an_unknown_identifier_and_a_wrong_password_read_the_same(app, monkeypatch):
    """Two causes, one sentence — asserting both are true of the *same* string, so
    a later edit cannot give one of them its own wording back."""
    unknown = _refusal_body(app, monkeypatch, identifier="99999999")
    wrong = _refusal_body(app, monkeypatch, identifier="who@example.org")

    assert GENERIC in unknown and GENERIC in wrong
    for sentence in OLD_DOOR_SENTENCES:
        assert sentence not in unknown and sentence not in wrong, (
            "a refusal names the page (and therefore the role) again")


def test_an_account_with_no_role_this_app_has_is_refused_generically(app, monkeypatch):
    """The third cause the brief names, and the one that used to have a door of its
    own: a role belongs to exactly one of the two pages, so being told "wrong page"
    was being told who the account was."""
    body = _refusal_body(app, monkeypatch, raises=False, role_for_profile="wali_kelas")

    assert GENERIC in body
    for sentence in OLD_DOOR_SENTENCES:
        assert sentence not in body

    # Both halves of the pair: the page writes each into its own span, so this is not
    # asking which language the request rendered in — it is asking that the sentence
    # this role is refused with is the generic one, in every language the page has.
    from app.utils.auth_messages import auth_error
    for sentence in auth_error("login_bad_credentials"):
        assert sentence in body, (
            "a role this app has no home for is not refused with the generic "
            "credential sentence")


def test_a_profile_row_the_server_cannot_read_is_not_reported_as_bad_credentials(app):
    """An infrastructure failure is not a password the reader got wrong.

    Told apart on purpose: the credential sentence consumes the account's attempt
    budget (the caller counts it), and spending a school's budget on our own bad
    minute is how a rate-limit spike used to ban a whole school for fifteen minutes.
    """
    class Exploding(FakeSupabase):
        def table(self, name):
            raise RuntimeError("profiles is down")

    # The row cannot be read *and* the account's own metadata carries no role — which
    # is what an infrastructure failure looks like from here, as opposed to an account
    # this app has no home for (`""`), which is answered as a credential refusal.
    res = _login_result(role=None)
    role, status = routes_auth._role_and_status(Exploding(), res, "who@example.org")

    assert role is None, "an unreadable row must be distinguishable from an empty role"
    assert status == "active"


# ── 5. the limits: one backstop per IP, one budget per account ───────────────

@pytest.mark.parametrize("endpoint", ["sign_in", "login", "login_user"])
def test_every_url_that_accepts_a_password_keeps_the_per_ip_backstop(endpoint):
    """All three, because all three now check a password.

    The teacher/student door used to carry *no* per-IP rule at all, and it is the
    URL a script would have found first.
    """
    source = inspect.getsource(getattr(routes_auth, endpoint))

    assert '_rate_limit("300 per minute")' in source, (
        f"{endpoint} lost the per-IP flood backstop")


def test_the_merged_url_is_exempt_from_the_hook_the_way_its_siblings_are():
    """The hook's per-IP group is *added* to the view's own limit, not a substitute.

    All three URLs spend the view's `300 per minute` backstop and the per-account
    counter; the two that are exempt from the hook are limited once, so a third URL
    that is not is limited twice for the same act — and the one left out is the page
    every reader now uses. Asserted for all three so a later edit cannot exempt one
    of them and let the others drift.
    """
    from app.utils import rate_limiter

    for url in (LOGIN_URL, "/auth/login", "/auth/login-user"):
        assert url in rate_limiter._exact_exempt, (
            f"{url} is throttled by the hook's auth group *as well as* by the view, "
            f"while the other URLs that check a password are throttled once")


def test_the_attempt_budget_is_keyed_on_the_account_not_the_school_ip():
    """The reason the backstop is not the defence.

    A school shares one NAT'd address, so a per-IP attempt limit locks out a whole
    class; the counted thing has to be the identifier the reader typed.
    """
    source = inspect.getsource(routes_auth._sign_in)

    assert 'check_account_limit("login_failed", login_input' in source, (
        "the failed-attempt counter is no longer keyed on the identifier")


def test_failed_logins_for_other_accounts_do_not_lock_a_valid_one_on_the_same_ip():
    """The school-NAT case, executed against the real counter."""
    from app.utils.rate_limiter import check_account_limit

    ip = "203.0.113.7"
    for i in range(25):
        allowed, _retry = check_account_limit("login_failed", f"nat-probe-{i}@example.org", ip=ip)
        assert allowed, (
            "another account's failures locked this one out — that is the school "
            "behind one NAT losing its whole class to somebody else's typos")


def test_the_public_pages_do_not_publish_a_door_that_no_longer_renders(app):
    """The links are the contract; this is the sweep over the *text* of the pages,
    so a link added after this file was written is caught by the same rule."""
    for name in PUBLIC_PAGES:
        text = (TEMPLATES / name).read_text(encoding="utf-8")
        for match in re.finditer(r"/auth/(login|login-user)", text):
            line = text[:match.start()].count("\n") + 1
            assert False, (
                f"{name}:{line} still names an old door in a link. Those URLs answer "
                "(they are aliases), but a published link that pays a redirect for "
                f"nothing is the hop this task removed — use {LOGIN_URL}.")
