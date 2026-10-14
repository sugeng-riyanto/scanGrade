"""A pupil belongs to a class of *their own* school — and four doors assumed it.

`app/services/school_integrity.py` has reported this row for a while: a
`profiles.class_id` that names a class of another school is `class_pupil_mismatch`,
a row that is well-formed and wrong, which an operator untangles by hand through
the quarantine and restore doors. What was never checked is the other end — the
*write* doors that create it. `profiles.class_id` and `students.class_id` are both
written from a form body or a CSV column, and no door asked whether the class was
the acting school's:

* `create_student_account` (the single writer behind every pupil account — the
  guru's CSV page, the API import, and the admin's "add pupil" form) filed whatever
  class id it was handed;
* `/admin-sekolah/students/<id>/edit` wrote the same column straight from the form,
  so a pupil could be moved onto another NPSN's roster.

The actor with a *reason* to hold another school's class ids is the teacher who
works at two of them — `/students/import` is a guru's page, and a member acting in
their second NPSN can read that school's ids off their own pages. So the rule is
the same sentence the integrity sweep already states, moved to the door:
**the class this school writes must be one this school owns.**

This file drives the doors (not a paraphrase of them) against a database holding
both schools, and it pins the refusal *and* its cost: a refused import or edit must
leave nothing behind, because a pupil on a foreign roster is invisible to the school
that now counts them and still counted by the school that lost them.
"""
from __future__ import annotations

import io
from types import SimpleNamespace

import pytest
from flask import g

from tests.conftest import app_instance
from tests.unit.membership_fixtures import FakeDb

from app.routes import admin_sekolah as admin_mod
from app.routes import students as student_routes
from app.services import student_import
from app.services.student_import import (CLASS_NOT_OURS, CLASS_UNVERIFIABLE,
                                         create_student_account, owned_class_id)

SCHOOL = "aaaaaaaa-1111-4a2b-8c3d-000000000001"
OTHER = "bbbbbbbb-1111-4a2b-8c3d-000000000002"
CLASS_A = "cccccccc-1111-4a2b-8c3d-000000000003"      # this school's
CLASS_B = "dddddddd-1111-4a2b-8c3d-000000000004"      # another school's
PUPIL = "eeeeeeee-1111-4a2b-8c3d-000000000005"
ADMIN = "ffffffff-1111-4a2b-8c3d-000000000006"

CSV = b"nama,nisn\nBudi Santoso,12345678\n"


def db_with(*, class_of_a=SCHOOL, fail_classes=False) -> FakeDb:
    """Two schools, one class each, and one pupil already filed by this school."""
    fake = FakeDb(
        schools=[{"id": SCHOOL, "name": "SMP Satu", "npsn": "11111111"},
                 {"id": OTHER, "name": "SMP Dua", "npsn": "22222222"}],
        classes=[{"id": CLASS_A, "school_id": class_of_a, "name": "VII A",
                  "grade_level": "7"},
                 {"id": CLASS_B, "school_id": OTHER, "name": "VIII B",
                  "grade_level": "8"}],
        profiles=[{"id": ADMIN, "full_name": "Admin Satu", "role": "admin_sekolah",
                   "school_id": SCHOOL, "status": "active"},
                  {"id": PUPIL, "full_name": "Murid Satu", "role": "murid",
                   "school_id": SCHOOL, "class_id": CLASS_A, "nisn": "99999999"}],
        students=[{"id": PUPIL, "school_id": SCHOOL, "nisn": "99999999",
                   "class_id": CLASS_A, "status": "active"}],
        school_years=[{"id": "y-1", "school_id": SCHOOL, "name": "2026/2027",
                       "is_active": True}],
    )
    if fail_classes:
        fake.fail = {"classes"}
    return fake


def answer(result):
    """``(payload, status)`` for a view returning either a Response or a tuple."""
    body, status = result if isinstance(result, tuple) else (result, None)
    return (body.get_json() if body.is_json else body), (
        status if status is not None else body.status_code)


def peel(view):
    """The undecorated body — the repo's idiom for driving a view by hand."""
    while hasattr(view, "__wrapped__"):
        view = view.__wrapped__
    return view


def run(view, monkeypatch, fake, *, module, method="POST", data=None, path="/",
        school=SCHOOL, role="admin_sekolah", kwargs=None):
    monkeypatch.setattr(module, "get_supabase", lambda: fake)
    if hasattr(module, "log_activity"):
        monkeypatch.setattr(module, "log_activity", lambda *a, **k: None)
    app = app_instance()
    with app.test_request_context(path, method=method, data=data or {}):
        g.user_id = ADMIN
        g.user_role = role
        g.user_school_id = school
        g.user_email = "admin@scan-face.app"
        g.user_name = "Admin Satu"
        g.tz_offset = 7
        return peel(view)(**(kwargs or {}))


# ── the rule itself, one class at a time ─────────────────────────────────────

class TestTheRule:
    def test_a_class_of_this_school_is_kept(self):
        assert owned_class_id(db_with(), SCHOOL, CLASS_A) == (CLASS_A, None)

    def test_a_class_of_another_school_is_refused_with_a_reason(self):
        assert owned_class_id(db_with(), SCHOOL, CLASS_B) == (None, CLASS_NOT_OURS)

    def test_no_class_chosen_is_not_a_refusal(self):
        """"No class" is a real state — a school files a pupil before placing them."""
        assert owned_class_id(db_with(), SCHOOL, "") == (None, None)
        assert owned_class_id(db_with(), SCHOOL, None) == (None, None)

    def test_a_read_that_fails_refuses(self):
        """"Could not ask" is not "ours" — the write it guards is a roster entry."""
        assert owned_class_id(db_with(fail_classes=True), SCHOOL, CLASS_A) == (
            None, CLASS_UNVERIFIABLE)

    def test_a_class_whose_row_names_another_school_is_refused(self):
        """The id is this school's *spelling* but the row disagrees — still refused.

        A class id can be reachable in two schools' page state (a copied form, a
        roster that has since moved); what decides is the row's own `school_id`,
        which is the same column the integrity sweep reads.
        """
        assert owned_class_id(db_with(class_of_a=OTHER), SCHOOL, CLASS_A) == (
            None, CLASS_NOT_OURS)


# ── the single writer: every pupil account passes through it ─────────────────

class TestTheSingleWriter:
    def test_it_refuses_a_foreign_class_before_minting_anything(self, monkeypatch):
        created = []
        monkeypatch.setattr(student_import.account_creation, "create_account",
                            lambda *a, **k: created.append(k) or SimpleNamespace(id="x"))
        fake = db_with()

        with pytest.raises(student_import.ValidationError):
            create_student_account(fake, school_id=SCHOOL, nisn="12345678",
                                   full_name="Budi", email="budi@example.test",
                                   password="rahasia", class_id=CLASS_B)

        assert created == [], "an account was minted for a class this school does not own"
        assert fake.writes == [], "a refused pupil account left a row behind"

    def test_it_files_its_own_class_on_both_rows(self, monkeypatch):
        """`profiles.class_id` and `students.class_id` are the same answer, once."""
        captured = {}
        monkeypatch.setattr(student_import.account_creation, "create_account",
                            lambda *a, **k: captured.update(k) or SimpleNamespace(id="x"))

        create_student_account(db_with(), school_id=SCHOOL, nisn="12345679",
                               full_name="Budi", email="budi2@example.test",
                               password="rahasia", class_id=CLASS_A)

        assert captured["profile_fields"]["class_id"] == CLASS_A
        assert captured["role_fields"]["class_id"] == CLASS_A
        assert captured["school_id"] == SCHOOL

    def test_no_class_still_creates_the_pupil(self, monkeypatch):
        """A pupil with no class is allowed; only a *foreign* class is not."""
        captured = {}
        monkeypatch.setattr(student_import.account_creation, "create_account",
                            lambda *a, **k: captured.update(k) or SimpleNamespace(id="x"))

        create_student_account(db_with(), school_id=SCHOOL, nisn="12345680",
                               full_name="Siti", email="siti@example.test",
                               password="rahasia", class_id=None)

        assert "class_id" not in captured["profile_fields"]
        assert captured["role_fields"]["class_id"] is None


# ── the guru's CSV door ──────────────────────────────────────────────────────

class TestTheGuruImportDoor:
    def _post(self, monkeypatch, fake, class_id):
        called = []
        monkeypatch.setattr(student_routes, "import_students_from_csv",
                            lambda *a, **k: called.append(a) or
                            {"success": 1, "failed": 0, "errors": [], "total": 1})
        data = {"class_id": class_id,
                "csv_file": (io.BytesIO(CSV), "murid.csv")}
        out = run(student_routes.import_csv, monkeypatch, fake, module=student_routes,
                  data=data, path="/students/import/csv", role="guru")
        return answer(out), called

    def test_a_foreign_class_answers_422_and_imports_nothing(self, monkeypatch):
        (payload, status), called = self._post(monkeypatch, db_with(), CLASS_B)

        assert status == 422
        assert payload["error"] == "BAD_CLASS"
        assert payload["message"] == CLASS_NOT_OURS
        assert called == [], "the import ran with a class this school does not own"

    def test_its_own_class_reaches_the_import(self, monkeypatch):
        (payload, status), called = self._post(monkeypatch, db_with(), CLASS_A)

        assert status == 200
        assert payload["success"] is True
        assert called, "a valid class did not reach the import service"
        assert called[0][2] == CLASS_A, "the import ran with a different class"


# ── the admin's two doors ────────────────────────────────────────────────────

class TestTheAdminDoors:
    def test_adding_a_pupil_into_another_schools_class_is_refused(self, monkeypatch):
        created = []
        monkeypatch.setattr(admin_mod, "create_student_account",
                            lambda *a, **k: created.append(k) or "uid-1")
        out = run(admin_mod.create_student, monkeypatch, db_with(),
                  module=admin_mod, data={"name": "Budi", "nisn": "12345681",
                                          "class_id": CLASS_B},
                  path="/admin-sekolah/students/create")
        assert created == [], "a pupil was created in another school's class"
        assert out.status_code == 302

    def test_adding_a_pupil_into_its_own_class_still_works(self, monkeypatch):
        created = []
        monkeypatch.setattr(admin_mod, "create_student_account",
                            lambda *a, **k: created.append(k) or "uid-1")
        run(admin_mod.create_student, monkeypatch, db_with(),
            module=admin_mod, data={"name": "Budi", "nisn": "12345682",
                                    "class_id": CLASS_A},
            path="/admin-sekolah/students/create")
        assert created and created[0]["class_id"] == CLASS_A

    def test_moving_a_pupil_into_another_schools_class_is_refused(self, monkeypatch):
        fake = db_with()
        out = run(admin_mod.edit_student, monkeypatch, fake, module=admin_mod,
                  data={"class_id": CLASS_B, "nisn": "99999999"},
                  path=f"/admin-sekolah/students/{PUPIL}/edit",
                  kwargs={"student_id": PUPIL})

        assert out.status_code == 302
        assert fake.writes == [], "a refused move still wrote the pupil"
        assert fake.row("profiles", id=PUPIL)["class_id"] == CLASS_A

    def test_moving_a_pupil_into_its_own_class_is_written(self, monkeypatch):
        fake = db_with()
        fake.tables["classes"].append({"id": "aaaa1111-1111-4a2b-8c3d-000000000007",
                                       "school_id": SCHOOL, "name": "VII B",
                                       "grade_level": "7"})
        run(admin_mod.edit_student, monkeypatch, fake, module=admin_mod,
            data={"class_id": "aaaa1111-1111-4a2b-8c3d-000000000007"},
            path=f"/admin-sekolah/students/{PUPIL}/edit",
            kwargs={"student_id": PUPIL})

        moved = [p for _v, t, p in fake.writes if t == "students"]
        assert moved and moved[0]["class_id"] == "aaaa1111-1111-4a2b-8c3d-000000000007"

    def test_clearing_the_class_is_still_allowed(self, monkeypatch):
        """Empty means "no class" — the refusal is for a *wrong* one."""
        fake = db_with()
        run(admin_mod.edit_student, monkeypatch, fake, module=admin_mod,
            data={"class_id": ""}, path=f"/admin-sekolah/students/{PUPIL}/edit",
            kwargs={"student_id": PUPIL})

        moved = [p for _v, t, p in fake.writes if t == "students"]
        assert moved and moved[0]["class_id"] is None


# ── one rule, two house spellings ────────────────────────────────────────────

class TestTheTwoSpellingsAgree:
    """`admin_sekolah._class_in_school` guards the promote form; this rule guards pupils.

    Two spellings of one question is exactly how a boundary starts leaking (the row
    that is refused by one door and accepted by the next), so the two are asked
    the same four questions here.
    """

    def test_both_refuse_a_foreign_class_and_accept_this_schools(self):
        fake = db_with()

        assert admin_mod._class_in_school(fake, CLASS_A, SCHOOL)["id"] == CLASS_A
        assert admin_mod._class_in_school(fake, CLASS_B, SCHOOL) is None
        assert owned_class_id(fake, SCHOOL, CLASS_A) == (CLASS_A, None)
        assert owned_class_id(fake, SCHOOL, CLASS_B) == (None, CLASS_NOT_OURS)

    def test_neither_answers_when_no_class_was_chosen(self):
        fake = db_with()

        assert admin_mod._class_in_school(fake, "", SCHOOL) is None
        assert admin_mod._class_in_school(fake, "None", SCHOOL) is None
        assert owned_class_id(fake, SCHOOL, "") == (None, None)
