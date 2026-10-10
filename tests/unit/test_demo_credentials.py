"""Every credential `/demo` prints must be one the seed actually made.

The page's job is to hand a stranger a working login. It drifted from the seed
twice, and both drifts produced the same sentence on the page — *"Email atau
password salah"* — for an account the page had just invited them to use:

* the **super-admin** card printed the blanket `demo123`, but `manage.py` seeds
  that account with its own password, so the one card offering "highest access"
  offered a login that could not work;
* a whole **SMP teacher** cohort had drifted (their passwords had been changed
  after seeding) while every SMA/SMK account still worked — a data problem, but
  the page had no way to notice it and neither did the repair button, which
  walked only the first page of the auth listing.

This suite closes the *page* half of that, because it is the half a later edit
can silently reintroduce. It reads the seed out of `manage.py` (as a literal,
without importing the app) and holds two things against it:

* every `@scan-grade.app` address printed on `/demo` belongs to a seeded
  account — a card for an account the seed never made is a demo of a login that
  cannot work;
* every `cred_row(...)` password equals that account's seeded password, so the
  super-admin card cannot fall back to the school default.

The data half is `reset_demo_passwords`, guarded in `test_super_admin_crud.py`.
"""
from __future__ import annotations

import ast
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[2]
MANAGE = ROOT / "manage.py"
DEMO_HTML = ROOT / "app" / "templates" / "demo.html"

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@scan-grade\.app")
#: `cred_row('email', 'accent')` or `cred_row('email', 'accent', 'password')`.
CRED_RE = re.compile(
    r"cred_row\(\s*'(?P<email>[^']+)'\s*,\s*'(?P<accent>[^']+)'\s*"
    r"(?:,\s*'(?P<password>[^']+)'\s*)?\)")


def _seed() -> dict[str, str]:
    """email -> password, read from `manage.py` as literals.

    `ast` rather than `import manage`: importing that module builds the app and
    runs the armament check, which is not a thing a unit test should trigger.
    """
    tree = ast.parse(MANAGE.read_text(encoding="utf-8"))

    def literal(name: str):
        for node in tree.body:
            if isinstance(node, ast.Assign) and any(
                    getattr(t, "id", None) == name for t in node.targets):
                return ast.literal_eval(node.value)
        raise AssertionError(f"{name} is no longer a top-level literal in manage.py")

    users = {"demo_users": literal("DEMO_USERS"), "demo_schools": literal("DEMO_SCHOOLS")}
    out: dict[str, str] = {}
    out[users["demo_users"]["super_admin"]["email"]] = \
        users["demo_users"]["super_admin"]["password"]
    for school in users["demo_schools"]:
        for key in ("admin",):
            out[school[key]["email"]] = school[key]["password"]
        for key in ("officials", "teachers", "students"):
            for u in school.get(key, []):
                out[u["email"]] = u["password"]
    return out


def test_the_seed_has_the_two_distinct_passwords_this_guard_is_about():
    """If this ever becomes one password, this file stops testing anything."""
    seed = _seed()
    assert seed, "the seed parsed to nothing"
    assert seed["superadmin@scan-grade.app"] != "demo123", (
        "the super-admin password is now the school default, so the page's blanket "
        "line would be true — delete this guard rather than leave it vacuous")


def test_every_address_the_page_prints_is_a_seeded_account():
    seed = _seed()
    page = DEMO_HTML.read_text(encoding="utf-8")
    printed = {e for e in EMAIL_RE.findall(page)}
    assert printed, "the demo page prints no addresses — did the cards move?"
    unknown = sorted(printed - set(seed))
    assert not unknown, (
        f"/demo offers {unknown}, which manage.py never seeds: a card for an "
        f"account that does not exist is a demo of a login that cannot work")


def test_every_card_prints_that_account_s_own_password():
    seed = _seed()
    page = DEMO_HTML.read_text(encoding="utf-8")
    rows = list(CRED_RE.finditer(page))
    assert rows, "no cred_row(...) calls found — the macro moved or changed shape"
    wrong = []
    for m in rows:
        email = m.group("email")
        password = m.group("password") or "demo123"
        if email in seed and password != seed[email]:
            wrong.append((email, password, seed[email]))
    assert not wrong, (
        "these cards promise a password the seed does not use (email, printed, "
        f"seeded): {wrong}")
    # And the guard must actually have seen the one card where they differ, or it
    # is only proving that a page of `demo123`s contains `demo123`s.
    distinct = [m for m in rows if (m.group("password") or "demo123") != "demo123"]
    assert distinct, (
        "no card carries its own password — the super-admin card has fallen back "
        "to the school default, which is the drift this suite exists for")


def test_the_blanket_password_line_is_scoped_to_school_accounts():
    """The page may not promise one password for an account that has another."""
    page = DEMO_HTML.read_text(encoding="utf-8")
    assert "Password semua akun" not in page and "password for all of them" not in page, (
        "the page claims one password for every account again; the super-admin "
        "account is seeded with a different one")
    assert "Password akun sekolah" in page or "Password for school accounts" in page, (
        "the blanket line was removed without being replaced by a scoped one, so "
        "the school accounts no longer say what their password is")


#: `login_link('<url>', …)` — the card's button.
LOGIN_LINK_RE = re.compile(r"login_link\(\s*'(?P<url>[^']+)'")
#: The role a card is for, when it names one (`/auth/sign-in?role=guru`). The value
#: is read as anything URL-ish rather than as `[a-z_]+`, so a misspelling that a
#: narrower pattern would simply not see — `role=guru2`, `role=Guru` — is *reported*
#: instead of passing the guard by being invisible to it.
DOOR_ROLE_RE = re.compile(r"[?&]role=(?P<role>[A-Za-z0-9_]+)")


def _wrong_doors(page: str):
    """Every card whose button does not open the one sign-in page for its role."""
    from app.utils.auth import ALL_ROLES, LOGIN_URL

    wrong = []
    for match in LOGIN_LINK_RE.finditer(page):
        url = match.group("url")
        path = url.split("?", 1)[0]
        if path != LOGIN_URL:
            wrong.append((url, f"the one sign-in page, {LOGIN_URL}"))
            continue
        role_match = DOOR_ROLE_RE.search(url)
        if role_match and role_match.group("role") not in ALL_ROLES:
            # A hint the page cannot place: it opens with no tab, so the card's
            # promise ("this is the pupil door") is quietly not kept.
            wrong.append((url, f"a role this app has, {ALL_ROLES}"))
    return wrong


def test_every_card_opens_the_one_sign_in_page_for_its_role():
    """A card is a promise about which page the login works on, and for whom.

    Two cards used to be the trap: an admin card pointing at the teacher/student
    door was refused *by design* — the page turned `super_admin` and
    `admin_sekolah` away — so a trainee typed a correct password and was told it
    was wrong. It also cost a live measurement an afternoon: every seeded account
    was probed at one door and four correct logins were reported as failures.

    There is one page now, so the failure mode moved rather than vanished: what a
    card has to get right is the **role hint**, because that is what opens the page
    on the right tab. A card naming a group the page cannot place opens a form that
    explains nothing about the reader it was written for, and a card pointing at an
    old door leaves the reader one redirect away from the page for no reason.
    """
    page = DEMO_HTML.read_text(encoding="utf-8")
    assert LOGIN_LINK_RE.search(page), "no login_link(...) calls found"
    wrong = _wrong_doors(page)
    assert not wrong, (
        "these demo cards do not open the one sign-in page for the role they "
        f"advertise (url, what it needs): {wrong}")


def test_the_card_rule_would_catch_the_defect_it_describes():
    """Pointed at the real text, so the guard above cannot be vacuous."""
    page = DEMO_HTML.read_text(encoding="utf-8")

    moved_back = page.replace(
        "{{ login_link('/auth/sign-in?role=guru'",
        "{{ login_link('/auth/login-user?role=guru'")
    assert moved_back != page, "the guru card moved — update this rule, do not delete it"
    assert _wrong_doors(moved_back), (
        "a guru card moved back to an old door and the rule did not notice")

    bogus_role = page.replace(
        "{{ login_link('/auth/sign-in?role=guru'",
        "{{ login_link('/auth/sign-in?role=guru2'")
    assert bogus_role != page
    assert _wrong_doors(bogus_role), (
        "a card named a group the page cannot place and the rule did not notice")
