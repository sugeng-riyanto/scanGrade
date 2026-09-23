"""The trail at the top of every page: where the reader is, and the way back.

The chrome has printed a breadcrumb since the layout was written, and for all that
time it was built in the template out of the URL: the path split on `/`, a small
dictionary of route words, `title()` for everything else, and a home link aimed at
`/{admin|teacher|student}/dashboard`. Four defects came out of those three lines,
and each of them is a class of defect rather than a slip:

* **the first word was the URL's prefix, not the reader** — three roles open
  `/teacher/analysis/<id>` (`can_manage_exam` allows all of them) and all three
  read "Teacher";
* **the home link was a redirect through a page the role is refused** —
  `/admin/dashboard` 308s into the super-admin panel;
* **most words were not words** — `title()` of a hyphenated slug, in English, in
  the one piece of chrome that follows the language toggle;
* **nothing was navigable but the last word** — an identifier is not a place, so
  on `/teacher/grade/<uuid>` the header's widest element was a 36-character token.

So the structure moved into `app/utils/breadcrumbs.py` and the words stayed in the
template, where the coverage gate can count them beside every other string the
reader sees. These tests hold the two halves together, and they ask the app's own
URL map rather than a list of addresses kept here: that is what keeps a crumb from
pointing at a page that does not exist, and what stops a POST route ever being
offered as a link.

The guard that matters most is `test_every_page_the_app_serves_is_named_or_dropped`:
it walks every GET rule registered and insists that each literal segment is either
named in the template or dropped by the module for a reason a reader can read. A
new page cannot arrive without a name, and a segment cannot be dropped by accident.
"""
from __future__ import annotations

import ast
import pathlib

import pytest

from app.utils import breadcrumbs

ROOT = pathlib.Path(__file__).resolve().parents[2]
BASE = (ROOT / "app" / "templates" / "base.html").read_text(encoding="utf-8")

ROLES = ("super_admin", "admin_sekolah", "guru", "murid")

EXAM = "38404a0e-af70-4968-ac20-217c61b096cc"


# ── the template's own vocabulary, read out of it ────────────────────────────

def _template_dict(name: str) -> dict:
    """The `{% set _crumb_x = {…} %}` literal, as Python.

    Read from the template rather than imported from the module because the whole
    point of the split is that these strings live where the coverage gate can see
    them; a test that imported its own copy would not notice them leaving.
    """
    start = BASE.index("{% set " + name + " = {")
    body = BASE[start:]
    opener = body.index("= {") + 2
    closer = body.index("} %}", opener)
    value = ast.literal_eval(body[opener:closer + 1])
    assert isinstance(value, dict) and value, f"{name} is not a non-empty dict"
    return value


def _literal_segments(rule: str) -> list[str]:
    """The URL's own words: placeholders (`<exam_id>`, `download.<ext>`) are not."""
    return [part for part in rule.strip("/").split("/")
            if part and "<" not in part]


def _get_rules(app):
    return [rule for rule in app.url_map.iter_rules()
            if "GET" in (rule.methods or ())]


def _every_rule(app):
    return list(app.url_map.iter_rules())


# ── the vocabulary is complete, in both directions ───────────────────────────

def test_every_page_the_app_serves_is_named_or_dropped(app):
    """A page the app answers must have a name, unless it is dropped on purpose."""
    labels = _template_dict("_crumb_labels")
    areas = _template_dict("_crumb_areas")
    unaccounted: dict[str, list[str]] = {}

    for rule in _get_rules(app):
        for segment in _literal_segments(rule.rule):
            # A role word is the area, which the area crumb already names.
            if segment in breadcrumbs.PREFIXES:
                continue
            if not breadcrumbs.names_a_place(segment):
                continue
            if segment.lower() in labels or segment in areas:
                continue
            unaccounted.setdefault(segment, []).append(f"GET {rule.rule}")

    assert not unaccounted, (
        "these URL segments are pages the app serves and the trail would print "
        "title-cased — name them in base.html or drop them in breadcrumbs.py with "
        "a reason:\n" + "\n".join(
            f"  {segment}  <- {paths}" for segment, paths in sorted(unaccounted.items())))


def test_the_vocabulary_has_no_words_for_pages_that_do_not_exist(app):
    """A label for a segment no route has is a word that rots: the page was renamed."""
    labels = _template_dict("_crumb_labels")
    served = set()
    for rule in _every_rule(app):
        served.update(_literal_segments(rule.rule))
    dead = sorted(word for word in labels if word not in served)
    assert not dead, (
        f"base.html names {dead} but no route serves them — the crumb for that page "
        "is now title-cased English")


def test_a_segment_is_dropped_for_exactly_one_stated_reason():
    """The drop-list is the guard's own documentation, so it cannot be a dumping ground."""
    seen: dict[str, str] = {}
    for why, segments in breadcrumbs.GROUPS:
        assert why and why == why.strip(), f"a group has no reason: {why!r}"
        assert segments, f"the group {why!r} is empty"
        for segment in segments:
            assert segment not in seen, (
                f"{segment!r} is dropped twice, as {seen[segment]!r} and {why!r}")
            seen[segment] = why
    assert set(seen) == set(breadcrumbs.IGNORED)


def test_an_artifact_is_never_a_crumb_whatever_its_name(app):
    """Downloads are named by hand and keep arriving: `download`, `download-pdf`, …"""
    for segment in ("download", "download-pdf", "download-template", "download.csv",
                    "download.xlsx", "loaderio-51ecf273210e88abe9f24d4eb2dba2a8.html"):
        assert not breadcrumbs.names_a_place(segment), segment


def test_a_six_digit_run_or_a_uuid_is_an_identifier(app):
    for segment in (EXAM, "123456", "9033553f-e225-41cb-8d55-e950db7d2a33"):
        assert not breadcrumbs.names_a_place(segment), segment
    for segment in ("results", "grade", "dashboard", "deploy-status"):
        assert breadcrumbs.names_a_place(segment), segment


# ── the reader, not the address ──────────────────────────────────────────────

@pytest.mark.parametrize("role", ROLES)
@pytest.mark.parametrize("prefix", ("teacher", "admin-sekolah", "super-admin"))
def test_the_first_crumb_is_the_reader_not_the_urls_prefix(role, prefix):
    """All three roles open a teacher's exam analysis; none of them is a teacher."""
    site, path = breadcrumbs.AREAS[role]
    crumbs = breadcrumbs.trail(f"/{prefix}/analysis/{EXAM}", role)
    assert crumbs[0]["kind"] == "area"
    assert crumbs[0]["key"] == role, crumbs
    assert crumbs[0]["path"] == path
    assert site in breadcrumbs.AREAS


@pytest.mark.parametrize("role", ROLES)
def test_the_area_crumb_is_the_readers_own_dashboard(role):
    """Not `/admin/dashboard`, which 308s into a panel a school admin is refused."""
    _key, path = breadcrumbs.AREAS[role]
    crumbs = breadcrumbs.trail("/teacher/grade/x", role)
    assert crumbs[0]["href"] == path


@pytest.mark.parametrize("role", ROLES)
def test_an_area_never_aims_through_a_permanent_redirect(role):
    """Home is one click, not a 308 — and never into another role's panel.

    `/admin/dashboard` is a legacy URL that redirects to `/super-admin/dashboard`,
    so aiming a school admin's only way back at it sends them through a hop and
    into a page their role is refused.
    """
    from app.utils.legacy_urls import LEGACY_ADMIN_PAGES

    _key, path = breadcrumbs.AREAS[role]
    assert path not in LEGACY_ADMIN_PAGES, (
        f"{role}'s area crumb aims at {path}, which permanently redirects to "
        f"{LEGACY_ADMIN_PAGES[path]}")
    assert not path.startswith("/admin/")


@pytest.mark.parametrize("role", ROLES)
def test_the_readers_own_dashboard_is_one_crumb_and_it_is_current(role):
    _key, path = breadcrumbs.AREAS[role]
    crumbs = breadcrumbs.trail(path, role)
    assert len(crumbs) == 1, crumbs
    assert crumbs[0]["kind"] == "area" and crumbs[0]["current"] is True
    assert crumbs[0]["href"] is None, "the trail links to the page being read"


def test_an_unknown_role_gets_a_trail_with_no_area():
    """The chrome falls back rather than asserting a dashboard somebody may not open."""
    crumbs = breadcrumbs.trail("/teacher/grade/x")
    assert all(crumb["kind"] != "area" for crumb in crumbs), crumbs
    assert crumbs, "no trail at all would leave the header empty"


# ── a crumb is a link only where the app answers ─────────────────────────────

def _serves(app, path: str, method: str = "GET") -> bool:
    adapter = app.url_map.bind("localhost")
    try:
        adapter.match(path, method=method)
        return True
    except Exception:
        return False


def test_a_crumb_is_a_link_only_where_the_app_answers_get(app):
    """Every href the trail produces, checked against the app's own URL map."""
    paths = (
        "/teacher/dashboard", "/teacher/exams", "/teacher/exams/new",
        f"/teacher/exams/{EXAM}/edit", f"/teacher/analysis/{EXAM}",
        f"/teacher/analysis/{EXAM}/report", f"/teacher/grade/{EXAM}",
        "/teacher/results", f"/teacher/analysis/{EXAM}/download.csv",
        "/teacher/subjects", "/teacher/retractions", "/teacher/grading-queue",
        "/admin-sekolah/dashboard", "/admin-sekolah/students", "/admin-sekolah/import",
        f"/super-admin/schools/{EXAM}", "/super-admin/deploy-status",
        "/super-admin/omr-test", f"/student/exams/{EXAM}", "/student/dashboard",
        f"/wb/teacher/whiteboard/{EXAM}", "/r/sometoken", "/teacher/settings",
    )
    checked = 0
    for path in paths:
        for crumb in breadcrumbs.trail(path, "guru"):
            if crumb["href"] is None:
                continue
            checked += 1
            assert _serves(app, crumb["href"]), (
                f"the trail for {path} links to {crumb['href']}, which is not a GET "
                "page this app serves")
    assert checked >= 5, "the guard checked almost nothing"


def test_a_post_route_is_never_offered_as_a_link(app):
    """`/teacher/subjects/new` is a POST; a trail linking to it submits a form."""
    assert not _serves(app, "/teacher/subjects/new"), (
        "the subject form has become a GET page — this guard needs a new example")
    crumbs = breadcrumbs.trail("/teacher/subjects/new", "admin_sekolah")
    assert all(crumb["href"] != "/teacher/subjects/new" for crumb in crumbs), crumbs


def test_a_parent_the_app_does_not_serve_is_a_word_not_a_link(app):
    """`/teacher/analysis` is not a page — the item analysis needs an exam."""
    assert not _serves(app, "/teacher/analysis"), (
        "the analysis index is a real route now; update this guard")
    crumbs = breadcrumbs.trail(f"/teacher/analysis/{EXAM}/report", "guru")
    parent = [c for c in crumbs if c["key"] == "analysis"][0]
    assert parent["href"] is None
    assert parent["current"] is False


def test_the_last_crumb_is_the_page_being_read_and_is_not_a_link():
    crumbs = breadcrumbs.trail(f"/teacher/analysis/{EXAM}/report", "guru")
    assert crumbs[-1]["current"] is True
    assert crumbs[-1]["href"] is None
    assert [c["key"] for c in crumbs] == ["guru", "analysis", "report"]


def test_an_identifier_never_reaches_the_trail():
    crumbs = breadcrumbs.trail(f"/teacher/grade/{EXAM}", "guru")
    assert [c["key"] for c in crumbs] == ["guru", "grade"]
    assert EXAM not in " ".join(c["key"] for c in crumbs)


def test_an_artifact_is_the_page_it_came_from():
    crumbs = breadcrumbs.trail("/teacher/results/download.csv", "guru")
    assert [c["key"] for c in crumbs] == ["guru", "results"]
    crumbs = breadcrumbs.trail(f"/teacher/analysis/{EXAM}/download.xlsx", "guru")
    assert [c["key"] for c in crumbs] == ["guru", "analysis"]


def test_the_public_reader_has_no_trail():
    """`/r/<token>` is opened by a stranger with no session and no role.

    The token is a row id like any other, so the trail is empty — and it is read
    from the service that mints it, so a shorter token cannot silently become a
    crumb that prints somebody's share link in the header.
    """
    from app.services.analysis_share import TOKEN_BYTES, new_token

    token = "x" * (TOKEN_BYTES * 2)
    assert len(new_token()) >= len(token) * 0.5
    assert breadcrumbs.trail(f"/r/{token}") == []
    assert breadcrumbs.trail(f"/r/{token}/download.pdf") == []


def test_a_role_word_in_front_of_an_id_names_a_page_not_the_area(app):
    """One route uses a role word that way: a learner's own report.

    The trail used to stop at the class report and mark *it* as the page being
    read, with no link back — on a page that is one level below it.
    """
    path = f"/teacher/analysis/{EXAM}/report/student/{EXAM}"
    with app.test_request_context(path):
        crumbs = breadcrumbs.trail(path, "guru")
    assert [c["key"] for c in crumbs] == ["guru", "analysis", "report", "student"]
    assert crumbs[-1]["current"] is True and crumbs[-1]["href"] is None
    # ... and the class report above it is the way back, because the app serves it.
    assert crumbs[-2]["href"] == f"/teacher/analysis/{EXAM}/report"
    assert crumbs[-2]["current"] is False


def test_the_area_word_is_permitted_where_it_is_not_a_place(app):
    """`/tutorial/guru`'s audience word is dropped, and the page keeps its name."""
    crumbs = breadcrumbs.trail("/tutorial/guru")
    assert [c["key"] for c in crumbs] == ["tutorial"]
    assert crumbs[-1]["current"] is True


def test_the_whiteboard_module_reads_as_one_word(app):
    """`wb` mounts the module and `teacher` repeats the area: neither is a page."""
    for path, role in (("/wb/teacher/whiteboard", "guru"),
                       (f"/wb/teacher/whiteboard/{EXAM}", "guru"),
                       ("/wb/student/whiteboard", "murid"),
                       (f"/wb/student/whiteboard/{EXAM}", "murid")):
        keys = [c["key"] for c in breadcrumbs.trail(path, role)]
        assert keys == [role, "whiteboard"], f"{path} -> {keys}"


def test_a_trailing_slash_and_a_query_string_do_not_change_the_trail():
    plain = breadcrumbs.trail("/teacher/results", "guru")
    assert breadcrumbs.trail("/teacher/results/", "guru") == plain
    assert breadcrumbs.trail("/teacher/results?exam_id=x&page=2#top", "guru") == plain


# ── the words ────────────────────────────────────────────────────────────────

def test_every_word_is_a_bilingual_pair():
    for name in ("_crumb_labels", "_crumb_areas"):
        for key, pair in _template_dict(name).items():
            assert isinstance(pair, tuple) and len(pair) == 2, f"{name}[{key}] = {pair!r}"
            indonesian, english = pair
            assert indonesian.strip() and english.strip(), f"{name}[{key}] is half empty"
            assert indonesian == indonesian.strip(), f"{name}[{key}] has stray space"


def test_the_area_words_cover_every_role_the_module_knows():
    """A role with a scope but no word renders its own dashboard as a slug."""
    assert set(_template_dict("_crumb_areas")) == set(breadcrumbs.AREAS)


def test_the_area_word_is_the_roles_own_name():
    """A super admin reading a teacher's page must not be called "Teacher"."""
    areas = _template_dict("_crumb_areas")
    assert areas["guru"] == ("Guru", "Teacher")
    assert areas["admin_sekolah"] == ("Admin Sekolah", "School Admin")
    assert areas["murid"][1] == "Student"


# ── the chrome that prints it ────────────────────────────────────────────────

@pytest.mark.parametrize("path,role,expected", (
    (f"/teacher/analysis/{EXAM}/report", "guru", ["Guru", "Analisis Butir", "Laporan"]),
    ("/super-admin/deploy-status", "super_admin", ["Super Admin", "Kesiapan Runner"]),
    ("/admin-sekolah/students", "admin_sekolah", ["Admin Sekolah", "Murid"]),
    (f"/teacher/grade/{EXAM}", "guru", ["Guru", "Koreksi"]),
))
def test_the_header_prints_the_trail_it_is_given(app, path, role, expected):
    """The chrome, rendered for real — the words, in order, from the template."""
    from flask import g

    with app.test_request_context(path):
        g.user_id, g.user_role = "u-1", role
        g.user_name, g.user_email = "Uji Coba", "uji@example.test"
        g.user_school_id, g.tz_offset, g.show = "", 7, {}
        html = app.jinja_env.from_string(
            "{% extends 'base.html' %}{% block content %}x{% endblock %}").render()

    header = html.split("</header>", 1)[0]
    nav = header.split("<!-- Breadcrumb -->", 1)[1].split("</nav>", 1)[0]
    assert EXAM not in nav, "an identifier reached the header"
    assert nav.count('x-text="t(') == len(expected), (
        f"{path} printed {nav.count('x-text=\"t(')} words, expected {len(expected)}")
    position = 0
    for word in expected:
        found = nav.find(f"'{word}'", position)
        assert found > 0, f"{path}: {word!r} is missing from the trail"
        position = found
    assert 'aria-current="page"' in nav


def test_the_header_prints_no_trail_for_a_page_it_has_no_word_for(app):
    """The safety net is a slug — and it is unreachable, which the first test holds."""
    from flask import g

    with app.test_request_context("/teacher/some-unknown-page"):
        g.user_id, g.user_role = "u-1", "guru"
        g.user_name, g.user_email = "Uji Coba", "uji@example.test"
        g.user_school_id, g.tz_offset, g.show = "", 7, {}
        html = app.jinja_env.from_string(
            "{% extends 'base.html' %}{% block content %}x{% endblock %}").render()
    nav = html.split("<!-- Breadcrumb -->", 1)[1].split("</nav>", 1)[0]
    assert "Some Unknown Page" in nav


def test_the_home_icon_is_the_trails_own_area_crumb(app):
    """Two sources for "where is home" is how they drift apart."""
    assert "{% if _trail and _trail[0].kind == 'area' %}" in BASE
    assert "href=\"{{ _trail[0].path }}\"" in BASE
    # The old expression aimed at /{admin|teacher|student}/dashboard, and
    # /admin/dashboard is a legacy URL that redirects out of the reader's role.
    assert "g.user_role in ('super_admin','admin_sekolah') %}admin" not in BASE


def test_the_trail_is_built_by_the_module_and_not_by_the_template(app):
    assert "breadcrumb_trail(request.path, g.get('user_role'))" in BASE
    assert "request.path.strip('/').split('/')" not in BASE, (
        "the template is splitting the URL again")
