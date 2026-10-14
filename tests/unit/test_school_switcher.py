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
  own school offered only while a choice is actually in force.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
from flask import g, render_template_string

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
