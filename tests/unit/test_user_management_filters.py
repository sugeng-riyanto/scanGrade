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

ROUTE = "/super-admin/users/manage"

#: (id, email, full_name, role, status) — deliberately unsorted, so every ordering
#: assertion below is about the route and not about the fixture's own luck.
PEOPLE = [
    ("u-1", "zeta@scan-grade.app", "Zeta", "guru", "active"),
    ("u-2", "alpha@scan-grade.app", "Alpha", "murid", "active"),
    ("u-3", "mike@scan-grade.app", "Mike", "guru", "suspended"),
    ("u-4", "beta@scan-grade.app", "Beta", "admin_sekolah", "active"),
]


class _Resp:
    def __init__(self, data):
        self.data = data


class _Profiles:
    def __init__(self, rows):
        self._rows = rows
        self._ids = None

    def select(self, *_a, **_k):
        return self

    def in_(self, _col, ids):
        self._ids = list(ids)
        return self

    def execute(self):
        want = set(self._ids or [])
        return _Resp([dict(r) for r in self._rows if r["id"] in want])


class _FakeSupabase:
    def __init__(self):
        self.auth = SimpleNamespace(admin=SimpleNamespace(list_users=self._users))
        self._profiles = [
            {"id": pid, "full_name": name, "role": role, "phone": "",
             "status": status, "school_id": None}
            for pid, _email, name, role, status in PEOPLE
        ]

    def _users(self):
        return [SimpleNamespace(id=pid, email=email,
                                created_at="2026-01-01T00:00:00+00:00")
                for pid, email, _n, _r, _s in PEOPLE]

    def table(self, _name):
        return _Profiles(self._profiles)


@pytest.fixture()
def fake(monkeypatch):
    supabase = _FakeSupabase()
    monkeypatch.setattr(mod, "get_supabase", lambda: supabase)
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
    for field in ("name", "email", "role", "status"):
        assert f"sort={field}" in html, f"the {field} column cannot be ordered"
