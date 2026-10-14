"""The switch between schools, where the answer is visible.

`app/utils/auth.py` resolves every request's school from the verified active
membership, and the only *writer* of that choice was `/teacher/membership` — a page
a teacher has no reason to open again after the day their request was accepted. So
the choice sits in the header now, and this file holds the four things that make
that safe rather than merely convenient:

* **the payload is the membership's own rows**, read through the same memoised read
  `resolve_for_request` already made for this request — asserted by *counting the
  reads*, because a second copy of the rule is how a header and a page start
  disagreeing about which schools a teacher may switch between;
* **it is hidden when there is nothing to switch to** (one membership, currently in
  it), and free for every role that cannot hold two memberships at all;
* **the return path is local** — the switcher posts the page it was used from, and a
  protocol-relative `//host` would make a school choice an open redirect;
* **the chrome carries the control**, guarded, with the way back to the account's
  own school offered only while a choice is actually in force;
* **and so does the teacher dashboard**, at every width — the header's copy is
  `hidden sm:block`, which is the phone a teacher holds when they walk into the
  second school, and the dashboard is the page whose every number belongs to the
  active NPSN. That page is cached per teacher **per school** for the same reason:
  keyed by the teacher alone, the switcher was confirmed by a page still showing
  the school they had just left.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
from flask import g, render_template, render_template_string

from app.services import school_membership as membership
from app.services import school_membership
from app.utils import req_cache
from app.routes import membership_routes
from tests.unit.membership_fixtures import FakeDb, membership as mrow

ROOT = Path(__file__).resolve().parents[2]
BASE = (ROOT / "app" / "templates" / "base.html").read_text(encoding="utf-8")
ROUTES = (ROOT / "app" / "routes" / "membership_routes.py").read_text(encoding="utf-8")

TEACHER = "u-1"
HOME = "sc-home"
OTHER = "sc-other"


def db_with(*rows) -> FakeDb:
    return FakeDb(teacher_school_membership=list(rows))


@pytest.fixture(autouse=True)
def _no_school_reads(monkeypatch):
    """The name comes from the per-school cache; stand it in so nothing dials out."""
    monkeypatch.setattr(req_cache, "school_row",
                        lambda school_id, columns="": {"name": f"School {school_id}"})


# ── the payload ──────────────────────────────────────────────────────────────

class TestTheSchoolsATeacherMaySwitchBetween:
    def test_two_memberships_list_both_with_the_active_one_marked(self):
        db = db_with(mrow(TEACHER, HOME), mrow(TEACHER, OTHER))
        out = school_membership.switchable_schools(
            TEACHER, "tok-1", active_school_id=OTHER, supabase=db)
        assert [s["school_id"] for s in out["schools"]] == sorted([HOME, OTHER])
        assert [s["active"] for s in out["schools"]] == [OTHER == s["school_id"]
                                                        for s in out["schools"]]
        assert out["away"] is False

    def test_a_second_school_while_at_home_is_still_offered(self):
        """The whole point: an accepted teacher can move *to* it, not only back."""
        db = db_with(mrow(TEACHER, HOME), mrow(TEACHER, OTHER))
        out = school_membership.switchable_schools(
            TEACHER, "tok-1", active_school_id=HOME, supabase=db)
        assert {s["school_id"] for s in out["schools"]} == {HOME, OTHER}

    def test_one_membership_already_in_force_hides_the_control(self):
        db = db_with(mrow(TEACHER, HOME))
        out = school_membership.switchable_schools(
            TEACHER, "tok-1", active_school_id=HOME, supabase=db)
        assert out == {"schools": [], "away": False}

    def test_a_choice_in_force_offers_the_way_home(self):
        membership.clear_active_school("tok-2")
        membership.set_active_school("tok-2", OTHER)
        db = db_with(mrow(TEACHER, OTHER))
        out = school_membership.switchable_schools(
            TEACHER, "tok-2", active_school_id=OTHER, supabase=db)
        assert out["away"] is True, "the home option is what `away` turns on"
        membership.clear_active_school("tok-2")

    def test_a_closed_membership_is_not_offered(self):
        """A closure is not a school to switch into — the membership page agrees."""
        db = db_with(mrow(TEACHER, HOME), mrow(TEACHER, OTHER),
                     mrow(TEACHER, "sc-closed", status="closed"))
        out = school_membership.switchable_schools(
            TEACHER, "tok-1", active_school_id=HOME, supabase=db)
        offered = {s["school_id"] for s in out["schools"]}
        assert offered == {HOME, OTHER}
        assert "sc-closed" not in offered

    def test_a_failed_read_hides_the_control_rather_than_raising(self, monkeypatch):
        monkeypatch.setattr(school_membership, "membership_rows",
                            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down")))
        out = school_membership.switchable_schools(TEACHER, "tok-1",
                                                   active_school_id=HOME, supabase=FakeDb())
        assert out == {"schools": [], "away": False}


class TestTheHeaderDoesNotReadTheMembershipsTwice:
    def test_it_reuses_the_memo_key_the_request_path_uses(self, monkeypatch, app):
        """One request, one read: the header reads what the resolution already did."""
        calls: list = []
        real = school_membership.membership_rows

        def counting(supabase, user_id, *a, **k):
            calls.append(user_id)
            return real(supabase, user_id, *a, **k)

        monkeypatch.setattr(school_membership, "membership_rows", counting)
        db = db_with(mrow(TEACHER, HOME), mrow(TEACHER, OTHER))
        with app.test_request_context("/teacher/dashboard"):
            g.user_id = TEACHER
            g.user_token = "tok-3"
            g.user_school_id = HOME
            g.user_role = "guru"
            school_membership.switchable_schools(TEACHER, "tok-3",
                                                 active_school_id=HOME, supabase=db)
            before = len(calls)
            school_membership.switchable_schools(TEACHER, "tok-3",
                                                 active_school_id=HOME, supabase=db)
        assert before == 1, "the rows are read once per request, not once per asker"
        assert len(calls) == before, "the second ask hit the memo, not the database"
        assert TEACHER in calls


# ── the context processor: who gets the control, and who pays nothing ────────

def _render_switcher(app, role, schools=(), away=False, monkeypatch=None):
    def fake(user_id, token, active_school_id=None, supabase=None):
        return {"schools": list(schools), "away": away}

    monkeypatch.setattr(school_membership, "switchable_schools", fake)
    with app.test_request_context("/teacher/dashboard"):
        g.user_id = TEACHER
        g.user_token = "tok-4"
        g.user_role = role
        g.user_school_id = HOME
        return render_template_string(
            "{{ school_switcher.schools|length }}|{{ school_switcher.away }}")


class TestTheHeaderControlReachesTheChrome:
    def test_a_teacher_with_two_schools_gets_them(self, monkeypatch, app):
        html = _render_switcher(app, "guru",
                                schools=[{"school_id": HOME, "name": "A", "active": True},
                                         {"school_id": OTHER, "name": "B", "active": False}],
                                monkeypatch=monkeypatch)
        assert html == "2|False"

    def test_a_pupil_never_gets_it(self, monkeypatch, app):
        assert _render_switcher(app, "murid", monkeypatch=monkeypatch) == "0|False"

    def test_an_anonymous_page_never_gets_it(self, monkeypatch, app):
        monkeypatch.setattr(school_membership, "switchable_schools",
                            lambda *a, **k: {"schools": [{"school_id": OTHER}], "away": False})
        with app.test_request_context("/"):
            html = render_template_string("{{ school_switcher.schools|length }}")
        assert html == "0"


# ── the return path must stay local ──────────────────────────────────────────

class TestTheWayBackIsLocal:
    @pytest.mark.parametrize("raw,kept", [
        ("/teacher/exams", "/teacher/exams"),
        ("/teacher/exams?page=2", "/teacher/exams?page=2"),
        ("//evil.example/x", ""),
        ("https://evil.example", ""),
        ("", ""),
        (None, ""),
        ("/teacher\\..\\x", ""),
        # A browser strips these three out of a URL *before* parsing it, so this shape
        # reaches the parser as `//evil.example`: protocol-relative and off-site, past
        # a check that only ever saw one leading slash.
        ("/\t/evil.example", ""),
        ("/\n/evil.example", ""),
        ("/\r/evil.example", ""),
    ])
    def test_only_a_single_slash_local_path_survives(self, raw, kept):
        assert membership_routes._safe_next(raw) == kept

    def test_the_route_returns_to_the_page_it_was_used_from(self):
        assert "back or url_for(" in ROUTES, (
            "the switch route must fall back to the membership page, not the header")
        assert "_safe_next(request.form.get(\"next\"))" in ROUTES


# ── the markup ───────────────────────────────────────────────────────────────

class TestTheControlIsInTheChrome:
    def test_it_posts_to_the_switch_route_with_the_page_it_is_on(self):
        assert 'action="/teacher/membership/switch"' in BASE
        assert 'name="next"' in BASE and "request.full_path" in BASE
        assert 'name="school_id"' in BASE and "sgSchoolSwitcher" in BASE

    def test_the_form_carries_the_token_the_global_check_requires(self):
        """Without it the control submits and the school never changes.

        `csrf_protect` in `app/__init__.py` refuses every POST that arrives without a
        token, and this control is a POST from a form — so a missing field is not a
        hardening gap, it is a switch that answers 403 on every use, which on screen
        looks exactly like nothing having happened.
        """
        assert 'name="_csrf_token"' in BASE, (
            "the switch form must post the token the app's CSRF check demands")

    def test_it_is_rendered_only_when_there_is_a_choice_to_make(self):
        assert "{% if _switch_schools|length > 1 or _switcher.get('away') %}" in BASE

    def test_the_chrome_tolerates_a_page_rendered_without_the_processor(self):
        """`base.html` is rendered directly by several guards and by the print
        documents' harness; a header that needs a context variable the renderer
        never passed is a header that breaks them, not a switcher."""
        assert "school_switcher|default({})" in BASE, (
            "the payload must have a fallback, or every bare render of the chrome fails")
        assert "school_switcher.schools" not in BASE

    def test_the_way_home_is_offered_only_while_a_choice_is_in_force(self):
        block = BASE.split("sgSchoolSwitcher", 1)[1].split("</form>", 1)[0]
        assert 'value="home"' in block
        assert "{% if _switcher.get('away') %}" in block, (
            "a `home` option with no choice in force would clear nothing")

    def test_a_teacher_page_actually_renders_the_switcher(self, monkeypatch, app):
        """End to end through the chrome: the payload reaches a real `<select>`."""
        monkeypatch.setattr(school_membership, "switchable_schools",
                            lambda *a, **k: {"schools": [
                                {"school_id": HOME, "name": "SMP Satu", "active": False},
                                {"school_id": OTHER, "name": "SMP Dua", "active": True}],
                                "away": True})
        with app.test_request_context("/teacher/dashboard"):
            g.user_id = TEACHER
            g.user_role = "guru"
            g.user_school_id = OTHER
            g.user_name = "Budi"
            # Set by a `before_request` hook that does not run in a bare context, and
            # read by the chrome's clock control.
            g.tz_offset = 7
            # `render_template_string`, not `app.jinja_env.from_string`: only the
            # former runs the context processors, and the processor is half of what
            # this test is about.
            html = render_template_string(
                "{% extends 'base.html' %}{% block content %}x{% endblock %}")
        assert 'id="sgSchoolSwitcher"' in html
        assert "SMP Satu" in html and "SMP Dua" in html
        assert html.count('value="home"') == 1, "the way home, once"
        assert 'name="next" value="/teacher/dashboard"' in html
        # The token has to be *rendered*, not merely present in the template: an empty
        # value is refused exactly like a missing field.
        token = re.search(r'name="_csrf_token" value="([^"]*)"', html)
        assert token and token.group(1), (
            "a tokenless POST is refused by `csrf_protect`, so the switch would look "
            "like it did nothing")
    def test_a_row_with_no_id_renders_an_empty_value_not_the_word_none(self, monkeypatch, app):
        """The template's own contract, whatever the payload holds.

        `switchable_schools` skips a row with no id today — which is exactly why a
        fixture of real rows and a sweep of the source both miss this, and why the
        template has to promise it itself: an empty payload is not the same promise
        as a form that can never post `None` as a uuid.
        """
        monkeypatch.setattr(school_membership, "switchable_schools",
                            lambda *a, **k: {"schools": [
                                {"school_id": None, "name": "Tanpa id", "active": False},
                                {"school_id": OTHER, "name": "SMP Dua", "active": True}],
                                "away": False})
        with app.test_request_context("/teacher/dashboard"):
            g.user_id = TEACHER
            g.user_role = "guru"
            g.user_school_id = OTHER
            g.user_name = "Budi"
            g.tz_offset = 7
            html = render_template_string(
                "{% extends 'base.html' %}{% block content %}x{% endblock %}")
        select = html.split('id="sgSchoolSwitcher"', 1)[1].split("</select>", 1)[0]
        assert 'value=""' in select, "a row with no id posts an empty value"
        assert 'value="None"' not in select, (
            "a bare interpolation renders the word None, and Postgres refuses it as a "
            "uuid")
        assert f'value="{OTHER}"' in select, "the real id is unaffected"

    def test_the_region_declares_its_own_language_like_the_rest_of_the_header(self):
        """The chrome follows the toggle, so its strings are pairs, not literals."""
        block = BASE.split("sgSchoolSwitcher", 1)[0].rsplit("<header", 1)[-1]
        assert ':lang="lang"' in block, "the switcher is inside the marked header region"
        assert "t('Sekolah aktif'" in BASE and "t('Sekolah asal saya'" in BASE


# ── the dashboard: the same choice, where its answer is visible ───────────────
#
# The header's copy is `hidden sm:block` — hidden on the narrowest screens, where
# the topbar is already carrying the crumb, the language and the menu. That is
# exactly the device a teacher holds when they walk into the second school, and the
# page where "which NPSN am I in?" is answered by every number on it is the
# dashboard. So the same form is on that page too, at every width.

DASH = (ROOT / "app" / "templates" / "teacher" / "dashboard.html").read_text(encoding="utf-8")
CACHE = (ROOT / "app" / "utils" / "req_cache.py").read_text(encoding="utf-8")
TEACHER_ROUTES = (ROOT / "app" / "routes" / "teacher.py").read_text(encoding="utf-8")


def _dashboard_context(**over) -> dict:
    """The dashboard's own context at its emptiest: a teacher with no exams yet."""
    data = dict(exams=[], total_students=0, avg_score="-", all_scores=[],
                user_name="Budi", assignments=[], classes=[], subjects=[],
                exams_no_key=[], exams_partial_key=[], partial_key_ceiling=0,
                exams_unassigned=[], exams_ambiguous_key=[], pending_grading=0,
                upcoming_exams=[], grading_progress={}, exam_stats=[],
                student_improvement=[], invigilation_tasks=[],
                invigilation_requests=[])
    data.update(over)
    return data


def _dashboard_html(monkeypatch, schools=(), away=False):
    app = create_app("app.config.TestingConfig")
    monkeypatch.setattr(school_membership, "switchable_schools",
                        lambda *a, **k: {"schools": list(schools), "away": away})
    with app.test_request_context("/teacher/dashboard"):
        g.user_id = TEACHER
        g.user_role = "guru"
        g.user_school_id = OTHER
        g.user_name, g.user_email = "Budi", "budi@example.test"
        g.tz_offset = 7
        return render_template("teacher/dashboard.html", **_dashboard_context())


class TestTheDashboardOffersTheSwitch:
    def test_the_card_is_the_headers_form_pinned_to_this_page(self):
        assert 'action="/teacher/membership/switch"' in DASH
        assert 'name="next" value="/teacher/dashboard"' in DASH
        assert 'name="school_id"' in DASH and "sgDashboardSwitcher" in DASH

    def test_the_form_carries_the_token_the_global_check_requires(self):
        assert 'name="_csrf_token"' in DASH and "csrf_token()" in DASH, (
            "`csrf_protect` refuses a tokenless POST, so the switch would 403 on "
            "every use and look exactly like nothing happening")

    def test_it_is_not_hidden_on_a_phone(self):
        """The opposite of the header's copy, on purpose: this card is the door the
        header's `hidden sm:block` closes, so it must render at every width."""
        block = DASH.split("sgDashboardSwitcher", 1)[0].rsplit("<form", 1)[-1]
        assert "hidden sm:block" not in block
        assert 'class="tap-44 w-full sm:w-64' in DASH, (
            "the control has to be full width (and 44px tall) on the device this "
            "card exists for")

    def test_every_string_on_it_is_a_pair(self):
        for indonesian, english in (("Sekolah aktif", "Active school"),
                                    ("Sekolah asal saya", "My own school"),
                                    ("Pindah sekolah atau NPSN", "Switch school or NPSN"),
                                    ("Kelas, murid dan penilaian mengikuti NPSN ini",
                                     "Classes, pupils and marks follow this NPSN")):
            assert indonesian in DASH and english in DASH, (
                f"{indonesian!r}/{english!r} is not a bilingual pair, so the card "
                "ignores the language toggle")

    def test_it_is_rendered_only_when_there_is_a_choice_to_make(self):
        assert "{% if _switch_schools|length > 1 or _switcher.get('away') %}" in DASH
        assert "school_switcher|default({})" in DASH, (
            "the payload needs a fallback: a bare render of this template without "
            "the context processor must not fail")

    def test_a_two_school_teacher_sees_both_with_their_npsn(self, monkeypatch):
        html = _dashboard_html(monkeypatch, away=True, schools=[
            {"school_id": HOME, "name": "SMP Satu", "npsn": "11111111", "active": False},
            {"school_id": OTHER, "name": "SMA Dua", "npsn": "22222222", "active": True}])
        assert "SMP Satu" in html and "SMA Dua" in html
        assert "NPSN 11111111" in html and "NPSN 22222222" in html, (
            "two schools in one district often share a short name; the number is "
            "what tells them apart")
        # Scoped to this card's own `<select>`: `base.html` renders its own copy of the
        # switch in the header (hidden below `sm`), and counting the whole document
        # would be counting two controls, not one option rendered twice.
        card = html.split("sgDashboardSwitcher", 1)[1].split("</select>", 1)[0]
        assert card.count('value="home"') == 1, "the way home, once, in this control"
        token = re.search(r'name="_csrf_token" value="([^"]*)"', html)
        assert token and token.group(1), "a tokenless POST is refused by `csrf_protect`"

    def test_a_single_school_teacher_never_sees_the_card(self, monkeypatch):
        html = _dashboard_html(monkeypatch, schools=[
            {"school_id": HOME, "name": "SMP Satu", "npsn": "11111111", "active": True}])
        assert "sgDashboardSwitcher" not in html, (
            "one school, currently in it: there is nothing to switch to")
        assert 'value="home"' not in html, (
            "nothing to go back to either: no choice is in force")

    def test_an_option_with_no_id_renders_an_empty_value(self, monkeypatch):
        html = _dashboard_html(monkeypatch, schools=[
            {"school_id": None, "name": "Tanpa id", "npsn": "", "active": False},
            {"school_id": OTHER, "name": "SMA Dua", "npsn": "22222222", "active": True}])
        select = html.split("sgDashboardSwitcher", 1)[1].split("</select>", 1)[0]
        assert 'value=""' in select and 'value="None"' not in select


class TestTheSchoolRowIsReadWhole:
    """`school_row` caches one row per school under one key, whatever columns the
    caller asked for — so a narrower read is not narrow, it is what every other
    reader in the TTL window gets. The switcher used to ask for `name` alone, which
    left the logo, the whiteboard feature flag and `npsn` missing elsewhere."""

    def test_the_switcher_asks_for_the_default_set(self, monkeypatch):
        asked: list = []
        # `switchable_schools` imports `school_row` inside itself at call time, so the
        # attribute that decides the columns is the one on `req_cache`.
        monkeypatch.setattr(req_cache, "school_row",
                            lambda school_id, *a, **k: (asked.append((school_id, a, k)),
                                                        {"name": "SMP", "npsn": "1"})[1])
        db = db_with(mrow(TEACHER, HOME), mrow(TEACHER, OTHER))
        out = school_membership.switchable_schools(TEACHER, "tok-9",
                                                   active_school_id=HOME, supabase=db)
        assert asked == [(HOME, (), {}), (OTHER, (), {})], (
            "the switcher narrowed the shared school row, which decides what every "
            "other reader of that school gets for the rest of the TTL")

    def test_the_payload_carries_the_npsn(self, monkeypatch):
        monkeypatch.setattr(req_cache, "school_row",
                            lambda school_id, *a, **k: {"name": f"Sekolah {school_id}",
                                                        "npsn": f"99{school_id}"})
        db = db_with(mrow(TEACHER, HOME), mrow(TEACHER, OTHER))
        out = school_membership.switchable_schools(TEACHER, "tok-9",
                                                   active_school_id=HOME, supabase=db)
        assert [s["npsn"] for s in out["schools"]] == [f"99{HOME}", f"99{OTHER}"]

    def test_a_school_row_that_cannot_be_read_still_lists_the_school(self, monkeypatch):
        monkeypatch.setattr(req_cache, "school_row",
                            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down")))
        db = db_with(mrow(TEACHER, HOME), mrow(TEACHER, OTHER))
        out = school_membership.switchable_schools(TEACHER, "tok-9",
                                                   active_school_id=HOME, supabase=db)
        assert {s["school_id"] for s in out["schools"]} == {HOME, OTHER}
        assert [s["name"] for s in out["schools"]] == ["", ""]

    def test_no_caller_in_the_app_narrows_the_shared_row(self):
        """The parameter exists for a column the default set does not know about;
        a narrower list is a cache-poisoning bug waiting on call order."""
        offenders = []
        for path in (ROOT / "app").rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            # `\b` and not a bare substring: `admin_sekolah._school_row` is a
            # different function on a different object, and a sweep that counted it
            # would fail on the second word of its own name.
            for call in re.finditer(r"(?<!def )\bschool_row\(([^)]*)\)", text):
                args = [a.strip() for a in call.group(1).split(",")]
                if len(args) > 1 and not args[1].startswith("columns"):
                    offenders.append(f"{path.name}: {call.group(0)}")
        assert not offenders, (
            "a caller asks `school_row` for less than SCHOOL_ROW_COLUMNS, and the row "
            "is cached under one key per school:\n  " + "\n  ".join(offenders))

    def test_the_default_set_is_named_once(self):
        assert 'SCHOOL_ROW_COLUMNS = "name, npsn, logo_url, features, status"' in CACHE
        assert "def school_row(school_id, columns=SCHOOL_ROW_COLUMNS)" in CACHE


# ── the page is cached per teacher *per school* ───────────────────────────────

class TestTheDashboardIsCachedPerSchool:
    def test_the_key_carries_the_school(self):
        assert req_cache.teacher_dashboard_key(TEACHER, HOME) != \
            req_cache.teacher_dashboard_key(TEACHER, OTHER)

    def test_the_key_is_built_in_one_place(self):
        assert 'f"t_dash:' not in TEACHER_ROUTES, (
            "the dashboard's cache key is spelled out in the route again; use "
            "req_cache.teacher_dashboard_key so the reader and every invalidation "
            "site cannot disagree about its shape")

    def test_the_route_reads_the_school_scoped_key(self):
        block = TEACHER_ROUTES.split('@teacher_bp.route("/dashboard")', 1)[1]
        block = block.split("@teacher_bp.route(", 1)[0]
        assert 'cache_key = teacher_dashboard_key(g.user_id, g.get("user_school_id"))' \
            in block, (
                "the dashboard read the per-teacher key, so the classes, subjects, "
                "assignments and duties of the previous school were served for the "
                "rest of the TTL after a switch")

    def test_the_invalidation_drops_the_same_key(self, monkeypatch):
        from app.routes import teacher as teacher_routes

        app = create_app("app.config.TestingConfig")
        dropped: list = []
        monkeypatch.setattr(teacher_routes, "cache_delete", dropped.append)
        with app.test_request_context("/teacher/dashboard"):
            g.user_id, g.user_school_id = TEACHER, OTHER
            teacher_routes._invalidate_teacher_dashboard()
        assert dropped == [req_cache.teacher_dashboard_key(TEACHER, OTHER)], (
            "a writer must drop the page it dirtied — the one for the school it is "
            "acting in")

    def test_switching_drops_the_destination_schools_page(self, monkeypatch):
        app = create_app("app.config.TestingConfig")
        dropped: list = []
        monkeypatch.setattr(req_cache, "invalidate",
                            lambda *keys: dropped.extend(keys))
        with app.test_request_context("/teacher/membership/switch"):
            g.user_id = TEACHER
            membership_routes._forget_cached_dashboard(OTHER)
            membership_routes._forget_cached_dashboard(None)
        assert dropped == [req_cache.teacher_dashboard_key(TEACHER, OTHER)], (
            "the page waiting in the destination must not outlive the switch; and a "
            "cleared choice names no school, so there is nothing to drop")

    def test_the_route_drops_the_school_it_switches_into(self):
        assert "_forget_cached_dashboard(target)" in ROUTES
        assert '_forget_cached_dashboard(g.get("user_home_school_id"))' in ROUTES, (
            "clearing back to the profile's own school lands on that school's page")
