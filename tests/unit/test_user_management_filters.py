"""The super-admin user roster is a list you have to be able to *narrow*.

Measured against the running route before this test existed: ``/super-admin/users/manage``
rendered up to fifty accounts with one free-text box and nothing else. There was no way to
see only the teachers, or only the suspended accounts, and no way to order the list at all —
so "find the one suspended guru" meant scrolling and reading. That is the whole reason the
page exists.

What is asserted, against a fake that answers the two reads the route makes
(``auth.admin.list_users()`` and ``profiles.in_``):

* a role filter keeps only that role, a status filter only that status, and the two
  compose;
* a column can be ordered ascending or descending, and the direction actually reverses;
* an unknown or empty ``sort`` is ignored rather than used as a field name — the sort
  arrives in the query string, so it must be checked against the route's own table;
* the filter menus are filled from the *unfiltered* set, so choosing a role cannot remove
  the other roles from the list you would use to undo it;
* the template renders the controls and keeps the active filters in the sort and
  pagination links, so clicking a header never silently drops the filter you set.
"""
from types import SimpleNamespace

import pytest
from flask import g

from tests.conftest import app_instance
from app.routes import super_admin as mod
from app.utils import auth as auth_utils

ROUTE = "/super-admin/users/manage"

#: (id, email, full_name, role, status, school) — deliberately unsorted, so every
#: ordering assertion below is about the route and not about the fixture's own luck.
PEOPLE = [
    ("u-1", "zeta@scan-grade.app", "Zeta", "guru", "active", "sch-1"),
    ("u-2", "alpha@scan-grade.app", "Alpha", "murid", "active", "sch-1"),
    ("u-3", "mike@scan-grade.app", "Mike", "guru", "suspended", "sch-2"),
    ("u-4", "beta@scan-grade.app", "Beta", "admin_sekolah", "active", "sch-2"),
]

SCHOOLS = [{"id": "sch-1", "name": "SMP Satu"}, {"id": "sch-2", "name": "SMP Dua"}]


class _Resp:
    def __init__(self, data):
        self.data = data


class _Table:
    """A PostgREST table read: ``select()``, an optional ``range()``, ``execute()``."""

    def __init__(self, rows):
        self._rows = list(rows)
        self._start = 0
        self._end = None

    def select(self, *_a, **_k):
        return self

    def range(self, start, end):
        self._start, self._end = start, end
        return self

    def execute(self):
        if self._end is None:
            return _Resp([dict(r) for r in self._rows])
        return _Resp([dict(r) for r in self._rows[self._start:self._end + 1]])


class _Admin:
    """The GoTrue admin listing: **paged**, and the *server* sets the page size.

    ``server_cap`` is the point. Asking for a thousand users does not promise a
    thousand back — GoTrue answers 50 by default — so "call ``list_users()`` once
    and call it the listing" is the defect this fixture is built to expose.
    """

    def __init__(self, users, *, server_cap=50):
        self._users = list(users)
        self._server_cap = server_cap
        self.pages_asked = []

    def list_users(self, page=1, per_page=None):
        per = min(per_page or 50, self._server_cap)
        self.pages_asked.append((page, per))
        start = (page - 1) * per
        return self._users[start:start + per]


class _FakeSupabase:
    def __init__(self, *, server_cap=50, users=None, profiles=None):
        self.admin = _Admin(users if users is not None else self._all_users(),
                            server_cap=server_cap)
        self._tables = {
            "profiles": profiles if profiles is not None else [
                {"id": pid, "full_name": name, "role": role, "phone": "",
                 "status": status, "school_id": school}
                for pid, _email, name, role, status, school in PEOPLE
            ],
            "schools": [dict(s) for s in SCHOOLS],
        }

    @staticmethod
    def _all_users():
        return [SimpleNamespace(id=pid, email=email,
                                created_at="2026-01-01T00:00:00+00:00")
                for pid, email, _n, _r, _s, _sch in PEOPLE]

    @property
    def auth(self):
        return SimpleNamespace(admin=self.admin)

    def table(self, name):
        return _Table(self._tables.get(name, []))


@pytest.fixture()
def fake(monkeypatch):
    supabase = _FakeSupabase()
    monkeypatch.setattr(mod, "get_supabase", lambda: supabase)
    # `list_all_auth_users` reads the admin interface off the shared client.
    monkeypatch.setattr(auth_utils, "get_auth_admin", lambda: supabase.admin)
    return supabase


def _render(query: str = "") -> str:
    """The route's own view, driven for real, with the super-admin role signed in."""
    app = app_instance()
    with app.test_request_context(f"{ROUTE}{query}"):
        g.user_id = "sa-1"
        g.user_role = "super_admin"
        g.user_email = "super@scan-grade.app"
        g.user_name = "Super Admin"
        g.user_school_id = ""
        g.tz_offset = 7
        g.show = {}
        return mod.user_management.__wrapped__()


def _emails(html: str) -> list[str]:
    """The roster's emails, in the order the table lists them.

    Scoped to the table body on purpose: the chrome renders the signed-in super
    admin's own address too, and an ordering assertion that silently included it
    would be measuring the header rather than the list.
    """
    import re
    body = html.split("<tbody", 1)[1].split("</tbody>", 1)[0] if "<tbody" in html else ""
    # One email per row: the email appears twice in each row (the display span and
    # the inline-edit form's value), so the *first* match in a `<tr` is the roster
    # position and the second is the same account's edit control.
    emails: list[str] = []
    for row in body.split("<tr")[1:]:
        found = re.search(r"([a-z]+)@scan-grade\.app", row)
        if found:
            emails.append(found.group(1))
    return emails


# ── filters ──────────────────────────────────────────────────────────────────

def test_the_role_filter_keeps_only_that_role(fake):
    assert _emails(_render("?role=guru")) == ["zeta", "mike"]


def test_the_status_filter_keeps_only_that_status(fake):
    html = _render("?status=suspended")
    assert "mike@scan-grade.app" in html
    for other in ("zeta@scan-grade.app", "alpha@scan-grade.app", "beta@scan-grade.app"):
        assert other not in html


def test_the_two_filters_compose(fake):
    html = _render("?role=guru&status=active")
    assert "zeta@scan-grade.app" in html
    assert "mike@scan-grade.app" not in html, "a suspended account slipped past a status filter"
    assert "alpha@scan-grade.app" not in html


def test_without_filters_every_user_is_listed(fake):
    html = _render()
    for _pid, email, *_ in PEOPLE:
        assert email in html


# ── sorting ──────────────────────────────────────────────────────────────────

def test_sorting_by_name_ascending_and_descending_are_opposites(fake):
    asc = _emails(_render("?sort=name&dir=asc"))
    desc = _emails(_render("?sort=name&dir=desc"))
    assert asc == ["alpha", "beta", "mike", "zeta"]
    assert desc == list(reversed(asc)), (
        "descending is not the reverse of ascending — the direction is ignored")


def test_sorting_by_role_and_email(fake):
    assert _emails(_render("?sort=role&dir=asc")) == [
        "beta", "zeta", "mike", "alpha"]
    assert _emails(_render("?sort=email&dir=asc")) == [
        "alpha", "beta", "mike", "zeta"]


def test_an_unknown_sort_is_ignored_not_used_as_a_field_name(fake):
    """The sort arrives in the query string, so it is a name from a known table."""
    # The fixture's own order, unchanged.
    assert _emails(_render("?sort=password")) == ["zeta", "alpha", "mike", "beta"]
    assert _emails(_render("?sort=")) == ["zeta", "alpha", "mike", "beta"]


def test_an_unknown_direction_falls_back_to_ascending(fake):
    assert _emails(_render("?sort=name&dir=sideways")) == [
        "alpha", "beta", "mike", "zeta"]


# ── the menus are not self-erasing ───────────────────────────────────────────

def test_the_role_menu_lists_every_role_even_while_one_is_selected(fake):
    html = _render("?role=guru")
    for role in ("guru", "murid", "admin_sekolah"):
        assert f'value="{role}"' in html, (
            "filtering by a role removed the other roles from the list you would "
            "use to undo it")


# ── the template ─────────────────────────────────────────────────────────────

def test_the_page_offers_the_controls_and_keeps_the_filters_in_its_links(fake):
    html = _render("?role=guru&status=active&sort=name&dir=desc")
    assert 'name="role"' in html, "no role filter control"
    assert 'name="status"' in html, "no status filter control"
    # The active filters survive a header click and a page change.
    assert "role=guru&status=active" in html
    assert "sort=name&dir=asc" in html or "sort=email" in html, (
        "the sortable headers do not link to another ordering")
    assert 'name="sort" value="name"' in html
    assert 'name="dir" value="desc"' in html


def test_the_sortable_headers_link_to_a_real_ordering(fake):
    html = _render()
    for field in ("name", "email", "role", "status", "school"):
        assert f"sort={field}" in html, f"the {field} column cannot be ordered"


# ── the whole listing, not its first page ────────────────────────────────────

def test_the_roster_walks_past_the_first_page(monkeypatch):
    """The live page showed 50 of 806 users — every school but the first was missing.

    ``admin.list_users()`` returns a *page* (50 by default), and the route read it
    once as if it were the listing. With 120 accounts and a 50-wide page, the
    last twenty can only appear if the route keeps asking until a page is empty.
    """
    users = [SimpleNamespace(id=f"u-{i}", email=f"user{i:03d}@scan-grade.app",
                             created_at="2026-01-01T00:00:00+00:00")
             for i in range(120)]
    supabase = _FakeSupabase(users=users)
    monkeypatch.setattr(mod, "get_supabase", lambda: supabase)
    monkeypatch.setattr(auth_utils, "get_auth_admin", lambda: supabase.admin)

    html = _render()

    assert "user000@scan-grade.app" in html
    assert "dari 120 user" in html, (
        "the roster counted only one page of the auth listing")
    assert [p for p, _ in supabase.admin.pages_asked] == [1, 2, 3, 4], (
        "the route did not walk the listing; it read one page and treated it as "
        "the whole set")
    # The accounts the old page could never reach are on the later display pages.
    assert "user119@scan-grade.app" in _render("?page=3"), (
        "an account past the first page of 50 was invisible")


def test_a_short_page_is_not_taken_for_the_end_of_the_listing(monkeypatch):
    """The page size is the server's to decide, so a short page proves nothing."""
    users = [SimpleNamespace(id=f"u-{i}", email=f"user{i:03d}@scan-grade.app",
                             created_at="2026-01-01T00:00:00+00:00")
             for i in range(60)]
    # Page 1 comes back short (40) only because the *server* capped it there.
    supabase = _FakeSupabase(users=users, server_cap=40)
    monkeypatch.setattr(mod, "get_supabase", lambda: supabase)
    monkeypatch.setattr(auth_utils, "get_auth_admin", lambda: supabase.admin)

    html = _render()

    assert "dari 60 user" in html and [p for p, _ in supabase.admin.pages_asked] == [1, 2, 3], (
        "a page shorter than the requested size was mistaken for the end of "
        "the listing")
    assert "user059@scan-grade.app" in _render("?page=2")


def test_the_profiles_are_read_past_their_first_chunk(monkeypatch):
    """Profiles arrive in chunks too, so a listing past 1000 must not be truncated.

    A roster that lists an account but cannot say its role or school is the same
    defect one layer down: the account is visible and unusable. The 1100th profile
    only exists if the read walks past its first chunk of 1000.
    """
    count = 1100
    users = [SimpleNamespace(id=f"u-{i}", email=f"u{i:04d}@scan-grade.app",
                             created_at="2026-01-01T00:00:00+00:00")
             for i in range(count)]
    profiles = [{"id": f"u-{i}", "full_name": f"P{i}", "role": "murid",
                 "phone": "", "status": "active",
                 "school_id": "sch-2" if i >= 1050 else "sch-1"}
                for i in range(count)]
    supabase = _FakeSupabase(users=users, profiles=profiles)
    monkeypatch.setattr(mod, "get_supabase", lambda: supabase)
    monkeypatch.setattr(auth_utils, "get_auth_admin", lambda: supabase.admin)

    html = _render("?school=sch-2")

    assert "dari 50 user" in html, (
        "the profiles past the first chunk were lost, so the accounts beyond it "
        "have no school to filter on")


# ── school: a filter and a column ────────────────────────────────────────────

def test_the_school_filter_keeps_only_that_school(fake):
    html = _render("?school=sch-2")
    assert "mike@scan-grade.app" in html
    assert "beta@scan-grade.app" in html
    assert "zeta@scan-grade.app" not in html
    assert "alpha@scan-grade.app" not in html


def test_the_school_menu_lists_every_school_even_while_one_is_selected(fake):
    html = _render("?school=sch-1")
    assert 'value="sch-1"' in html
    assert 'value="sch-2"' in html, (
        "filtering by a school removed the others from the list you would use "
        "to undo it")


def test_every_row_names_its_school(fake):
    html = _render()
    assert "SMP Satu" in html and "SMP Dua" in html, (
        "a super admin could not see which school an account belongs to")


def test_the_school_column_can_be_ordered(fake):
    assert _emails(_render("?sort=school&dir=asc")) == [
        "mike", "beta", "zeta", "alpha"]


def test_the_school_filter_survives_a_header_click(fake):
    html = _render("?school=sch-1&sort=name&dir=asc")
    assert "school=sch-1" in html, "a header click silently dropped the school"
