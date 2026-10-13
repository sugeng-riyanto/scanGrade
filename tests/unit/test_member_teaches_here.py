"""A teacher a school accepted is a teacher *of that school* — on the page, and
through every door the school acts on.

Reported from production: a school approved a teacher's request to join its NPSN,
and the teacher did not appear on `/admin-sekolah/teachers`. The diagnosis, in the
code that was there: the roster read the `teachers` table alone, and
`teacher_assignments.teacher_in_school` compared `profiles.school_id` — so an
approved member existed in `teacher_school_membership` (migration 044) and was
invisible to the roster, to the assignment matrix (404) and to the school's own
controls, while `app.utils.auth` resolved their *session* school from that same
membership. Two halves of one feature disagreed about who teaches where.

What is pinned here, and why each is a property rather than a detail:

* **one predicate, two ways in** — `teacher_in_school` is true for a profile of
  this school *or* an active membership of it, and false the moment the membership
  is closed;
* **the account is not the membership** — `account_of_this_school` still answers
  the credential question, so the password door refuses a member. The roster hides
  the control for them; this is what makes the hidden button a fact;
* **the roster** carries a member with a badge that names the membership and never
  the other school (migration 063's privacy rule), the action that removes them
  from *this* school, and none of the account actions;
* **select-all never picks a member up** — the bulk routes delete an account and
  reset its password, and neither is this school's to do;
* **the switcher** is the writer that was missing entirely: `set_active_school`
  had no caller anywhere, so an accepted membership could never become the school
  whose classes, pupils, weighting and marks apply.

The views are driven for real with `g` populated, against the shared membership
fake — the same arrangement `test_promote_rbac.py` uses, so the route bodies (and
not a paraphrase of them) are what answer.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from flask import g

from tests.conftest import app_instance
from tests.unit.membership_fixtures import FakeDb, membership, person, school

from app.routes import admin_sekolah as mod
from app.routes import membership_routes as member_routes
from app.services import school_membership as membership_service
from app.services import teacher_assignments as ta

ROOT = Path(__file__).resolve().parents[2]
ROSTER_TEMPLATE = ROOT / "app" / "templates" / "admin_sekolah" / "teachers.html"
SWITCHER_TEMPLATE = ROOT / "app" / "templates" / "teacher" / "membership.html"

SCHOOL = "sc-target"          # the school that accepted the teacher
HOME = "sc-home"              # the teacher's own school
# Ids shaped like the column's: `admin_sekolah._teacher_in_school` refuses anything
# that is not a UUID before it reads, which is one of the checks this file keeps.
MEMBER = "3d0c0e5a-1111-4a2b-8c3d-000000000011"   # joined SCHOOL from elsewhere
LOCAL = "3d0c0e5a-1111-4a2b-8c3d-000000000012"    # SCHOOL's own account
ADMIN = "3d0c0e5a-1111-4a2b-8c3d-000000000013"
TEACHER_ROLE = "guru"

ROSTER_URL = "/admin-sekolah/teachers"
SWITCH_URL = "/teacher/membership/switch"


# ── the two students of this file ────────────────────────────────────────────

def db_with(*, status="active", fail_membership=False, extra=None) -> FakeDb:
    tables = {
        # The member's profile lives at their OWN school — that is the whole point
        # of the membership table (044): identity in one place, membership in many.
        "profiles": [
            {**person(LOCAL, "Lokal Guru", "lokal@example.test"),
             "role": "guru", "school_id": SCHOOL},
            {**person(MEMBER, "Anggota Guru", "anggota@example.test"),
             "role": "guru", "school_id": HOME},
            {**person(ADMIN, "Admin Sekolah", "admin@example.test"),
             "role": "admin_sekolah", "school_id": SCHOOL},
        ],
        "teacher_school_membership": [membership(MEMBER, SCHOOL, status)],
        "teachers": [{"id": LOCAL, "school_id": SCHOOL, "employee_id": "1980",
                      "subject_id": None}],
        "schools": [school(SCHOOL, "SMP Tujuan"), school(HOME, "SMP Asal")],
    }
    if extra:
        tables.update(extra)
    fake = FakeDb(**tables)
    if fail_membership:
        fake.fail = {"teacher_school_membership"}
    return fake


def peel(view):
    """The undecorated body, so `g` can be populated by hand (the repo's idiom)."""
    while hasattr(view, "__wrapped__"):
        view = view.__wrapped__
    return view


def run(view, monkeypatch, fake, *, method="GET", data=None, path=ROSTER_URL,
        school=SCHOOL, role="admin_sekolah", token=None, kwargs=None):
    monkeypatch.setattr(mod, "get_supabase", lambda: fake)
    monkeypatch.setattr(mod, "log_activity", lambda *a, **k: None)
    app = app_instance()
    with app.test_request_context(path, method=method, data=data or {}):
        g.user_id = ADMIN
        g.user_role = role
        g.user_school_id = school
        g.user_email = "admin@scan-grade.app"
        g.user_name = "Admin SMP"
        g.tz_offset = 7
        if token:
            g.user_token = token
        return peel(view)(**(kwargs or {}))


def run_switch(monkeypatch, fake, *, data, token="tok-1"):
    monkeypatch.setattr(member_routes, "get_supabase", lambda: fake)
    monkeypatch.setattr(member_routes, "log_activity", lambda *a, **k: None)
    app = app_instance()
    with app.test_request_context(SWITCH_URL, method="POST", data=data):
        g.user_id = MEMBER
        g.user_role = TEACHER_ROLE
        g.user_school_id = SCHOOL
        g.user_email = "anggota@example.test"
        g.user_name = "Anggota Guru"
        g.tz_offset = 7
        g.user_token = token
        return peel(member_routes.teacher_membership_switch)()


# ── one predicate, two ways in ───────────────────────────────────────────────

class TestWhoTeachesHere:
    def test_a_profile_of_this_school_is_a_teacher_of_it(self):
        assert ta.teacher_in_school(db_with(), SCHOOL, LOCAL) is True

    def test_an_active_member_is_a_teacher_of_it(self):
        """The bug, as an assertion: the membership *is* membership of the staff."""
        assert ta.teacher_in_school(db_with(), SCHOOL, MEMBER) is True

    def test_a_closed_membership_is_not(self):
        assert ta.teacher_in_school(db_with(status="closed"), SCHOOL, MEMBER) is False

    def test_a_stranger_is_not(self):
        assert ta.teacher_in_school(db_with(), SCHOOL, "u-nobody") is False
        assert mod._teacher_in_school(db_with(), "u-nobody", SCHOOL) is False

    def test_a_read_that_fails_grants_nothing(self):
        """Nothing is granted on a maybe — the closure rule's fail-closed half."""
        assert ta.teacher_in_school(db_with(fail_membership=True), SCHOOL, MEMBER) is False

    def test_the_account_question_is_narrower_than_the_teaching_one(self):
        """A member teaches here and is not this school's account — deliberately."""
        fake = db_with()
        assert ta.teacher_in_school(fake, SCHOOL, MEMBER) is True
        assert ta.account_of_this_school(fake, SCHOOL, MEMBER) is False
        assert ta.account_of_this_school(fake, SCHOOL, LOCAL) is True

    def test_a_member_may_be_a_homeroom_teacher(self):
        """`classes.teacher_id` was the third place that compared the profile's school."""
        assert mod._teacher_in_school(db_with(), MEMBER, SCHOOL) is True
        assert mod._teacher_in_school(db_with(status="closed"), MEMBER, SCHOOL) is False
        assert mod._teacher_in_school(db_with(), LOCAL, SCHOOL) is True


# ── the roster ───────────────────────────────────────────────────────────────

def roster(monkeypatch, **kw) -> str:
    html = run(mod.teachers, monkeypatch, db_with(**kw))
    assert isinstance(html, str), html
    return html


class TestTheRoster:
    def test_an_accepted_member_is_on_it(self, monkeypatch):
        html = roster(monkeypatch)
        assert "Anggota Guru" in html, (
            "the reported symptom: an approved teacher missing from the roster")

    def test_the_badge_names_membership_and_never_the_other_school(self, monkeypatch):
        html = roster(monkeypatch)
        assert "Anggota NPSN" in html, (
            "the admin has to be able to tell a join from an account they created")
        assert "SMP Asal" not in html, (
            "migration 063: the destination school never learns another membership")
        assert HOME not in html, "no school id of another membership may render"

    def test_a_member_is_offered_work_and_not_the_account(self, monkeypatch):
        html = roster(monkeypatch)
        assert f'"/admin-sekolah/teachers/{MEMBER}/remove-member"' in html or \
               f"/admin-sekolah/teachers/{MEMBER}/remove-member" in html
        assert f"/admin-sekolah/teachers/{MEMBER}/delete" not in html, (
            "deleting the account would take every school they teach in")
        assert f"/admin-sekolah/teachers/{MEMBER}/reset-password" not in html, (
            "their credentials belong to the school that created the account")

    def test_the_schools_own_teacher_keeps_the_account_controls(self, monkeypatch):
        html = roster(monkeypatch)
        assert f"/admin-sekolah/teachers/{LOCAL}/delete" in html
        assert f"/admin-sekolah/teachers/{LOCAL}/reset-password" in html
        assert html.count("Anggota NPSN") == 1, (
            "the badge is the member's, and exactly one member is on this roster")

    def test_select_all_cannot_reach_a_member(self, monkeypatch):
        html = roster(monkeypatch)
        blob = re.search(r'id="teacher-ids-json" value=\'([^\']*)\'', html)
        assert blob, "the hidden field is what the bulk buttons post"
        ids = json.loads(blob.group(1))
        assert MEMBER not in ids, (
            "a member in the bulk set is an account delete waiting for one click")
        assert LOCAL in ids

    def test_a_closed_membership_leaves_the_roster(self, monkeypatch):
        html = roster(monkeypatch, status="closed")
        assert "Anggota Guru" not in html, (
            "closing a membership is total — the roster is one of its read paths")

    def test_a_member_cannot_read_another_schools_roster(self, monkeypatch):
        """The union is scoped by the session's school, not by what is in the body."""
        outsider = "3d0c0e5a-1111-4a2b-8c3d-0000000000ff"
        fake = db_with(extra={"teacher_school_membership": [
            membership(MEMBER, SCHOOL, "active"), membership(outsider, "sc-x", "active")]})
        html = run(mod.teachers, monkeypatch, fake)
        assert outsider not in html, "a membership of another school is not ours"


# ── the doors the school actually acts through ───────────────────────────────

class TestTheDoors:
    def test_the_assignment_matrix_opens_for_a_member(self, monkeypatch):
        """The CRUD half of the report: a member can be given classes and subjects."""
        result = run(mod.teacher_assignments, monkeypatch, db_with(),
                     path=f"/admin-sekolah/teachers/{MEMBER}/assignments",
                     kwargs={"teacher_id": MEMBER})
        body = result[0].get_json() if isinstance(result, tuple) else result.get_json()
        assert body is not None and "classes" in body, body

    def test_the_assignment_matrix_still_closes_for_a_stranger(self, monkeypatch):
        result = run(mod.teacher_assignments, monkeypatch, db_with(),
                     path="/admin-sekolah/teachers/nobody/assignments",
                     kwargs={"teacher_id": "nobody"})
        assert isinstance(result, tuple) and result[1] == 404, result

    def test_the_password_door_refuses_a_member(self, monkeypatch):
        fake = db_with()
        fake.auth = SimpleNamespace(admin=SimpleNamespace(
            update_user_by_id=lambda *a, **k: None))
        result = run(mod.reset_teacher_password, monkeypatch, fake, method="POST",
                     path=f"/admin-sekolah/teachers/{MEMBER}/reset-password",
                     data={"password": "baru-123"}, kwargs={"teacher_id": MEMBER})
        assert isinstance(result, tuple) and result[1] == 404, result

    def test_the_password_door_opens_for_the_schools_own_account(self, monkeypatch):
        fake = db_with()
        fake.auth = SimpleNamespace(admin=SimpleNamespace(
            update_user_by_id=lambda *a, **k: None))
        result = run(mod.reset_teacher_password, monkeypatch, fake, method="POST",
                     path=f"/admin-sekolah/teachers/{LOCAL}/reset-password",
                     data={"password": "baru-123"}, kwargs={"teacher_id": LOCAL})
        body = result[0].get_json() if isinstance(result, tuple) else result.get_json()
        assert body.get("success") is True, body

    def test_removing_a_member_closes_the_membership_and_nothing_else(self, monkeypatch):
        fake = db_with()
        result = run(mod.remove_teacher_membership, monkeypatch, fake, method="POST",
                     path=f"/admin-sekolah/teachers/{MEMBER}/remove-member",
                     kwargs={"teacher_id": MEMBER})
        body = result.get_json()
        assert body.get("success") is True, body
        [payload] = fake.write_payloads("teacher_school_membership", "update")
        assert payload["status"] == "closed", payload
        assert payload["closed_by"] == ADMIN, "a closure with no author is not a record"
        assert fake.write_payloads("profiles", "delete") == [], (
            "the professor's identity is not this school's to delete")
        assert fake.write_payloads("teachers", "delete") == []

    def test_removing_somebody_who_is_not_a_member_writes_nothing(self, monkeypatch):
        fake = db_with()
        result = run(mod.remove_teacher_membership, monkeypatch, fake, method="POST",
                     path="/admin-sekolah/teachers/nobody/remove-member",
                     kwargs={"teacher_id": "nobody"})
        assert isinstance(result, tuple) and result[1] == 409, result
        assert fake.writes == [], "the closure is a transition, not an assignment"

    def test_a_second_removal_is_refused_rather_than_repeated(self, monkeypatch):
        fake = db_with(status="closed")
        result = run(mod.remove_teacher_membership, monkeypatch, fake, method="POST",
                     path=f"/admin-sekolah/teachers/{MEMBER}/remove-member",
                     kwargs={"teacher_id": MEMBER})
        assert isinstance(result, tuple) and result[1] == 409, result


# ── the switcher: the writer that was missing ────────────────────────────────

class TestTheSwitcher:
    def test_it_makes_the_accepted_school_the_active_one(self, monkeypatch):
        membership_service.clear_active_school("tok-1")
        run_switch(monkeypatch, db_with(), data={"school_id": SCHOOL})
        assert membership_service.get_active_school("tok-1") == SCHOOL

    def test_it_refuses_a_school_the_teacher_is_not_a_member_of(self, monkeypatch):
        membership_service.clear_active_school("tok-1")
        run_switch(monkeypatch, db_with(), data={"school_id": "sc-elsewhere"})
        assert membership_service.get_active_school("tok-1") is None, (
            "a school id in a form is a claim; the row is the answer")

    def test_a_closed_membership_cannot_be_switched_into(self, monkeypatch):
        membership_service.clear_active_school("tok-1")
        run_switch(monkeypatch, db_with(status="closed"), data={"school_id": SCHOOL})
        assert membership_service.get_active_school("tok-1") is None, (
            "switching must not be a way around a closure")

    def test_going_home_clears_the_choice(self, monkeypatch):
        membership_service.set_active_school("tok-1", SCHOOL)
        run_switch(monkeypatch, db_with(), data={"school_id": "home"})
        assert membership_service.get_active_school("tok-1") is None

    def test_the_resolution_follows_the_choice_and_drops_it_on_a_closure(self):
        """End to end, with the box's own resolution: the switch decides the school."""
        app = app_instance()
        fake = db_with()
        membership_service.set_active_school("tok-2", SCHOOL)
        with app.test_request_context(SWITCH_URL, method="POST"):
            chosen = membership_service.resolve_for_request(
                "tok-2", MEMBER, HOME, TEACHER_ROLE, supabase=fake)
        assert chosen == SCHOOL, "the NPSN the teacher chose is the one that applies"

        closed = db_with(status="closed")
        with app.test_request_context(SWITCH_URL, method="POST"):
            after = membership_service.resolve_for_request(
                "tok-2", MEMBER, HOME, TEACHER_ROLE, supabase=closed)
        assert after == HOME
        assert membership_service.get_active_school("tok-2") is None, (
            "a memo that outlived its row is dropped by the read path")


# ── the pages and routes name what they must ─────────────────────────────────

class TestTheWiring:
    def test_the_switcher_page_renders_the_control_and_its_pairs(self):
        text = SWITCHER_TEMPLATE.read_text(encoding="utf-8")
        assert "teacher_membership_switch" in text, "the control posts to the route"
        assert "Ajar di sini" in text and "Teach here" in text, (
            "this page is translated, so its copy is a pair like the rest of it")

    def test_the_switcher_route_is_a_post_a_teacher_may_make(self):
        src = (ROOT / "app" / "routes" / "membership_routes.py").read_text(encoding="utf-8")
        block = src.split("def teacher_membership_switch", 1)[1]
        decorators = src.split("def teacher_membership_switch", 1)[0].rsplit("@teacher_membership_bp", 1)[1]
        assert "methods=[\"POST\"]" in decorators, decorators
        assert "_MEMBERSHIP_ROLES" in decorators, (
            "only the cross-school roles have a school to switch to")
        assert "is_active_member(" in block, (
            "the row is the answer, checked before anything is written")

    def test_the_roster_route_asks_the_membership_for_its_members(self):
        src = (ROOT / "app" / "routes" / "admin_sekolah.py").read_text(encoding="utf-8")
        body = src.split("def teachers():", 1)[1].split("\ndef ", 1)[0]
        assert "membership_service.active_members(" in body
        assert "is_member" in body, "the template branches on the marker"

    def test_the_removal_route_is_the_membership_close_it_claims(self):
        src = (ROOT / "app" / "routes" / "admin_sekolah.py").read_text(encoding="utf-8")
        block = src.split("def remove_teacher_membership", 1)[0].rsplit("@admin_sekolah_bp", 1)[1]
        body = src.split("def remove_teacher_membership", 1)[1].split("\n@", 1)[0]
        assert "methods=[\"POST\"]" in block and "@admin_sekolah_required" in block
        assert "@subscription_write_required" in block
        assert "membership_service.close(" in body
        assert "delete_user" not in body and ".delete()" not in body, (
            "this school removes a teacher; it does not delete an identity")
