"""A teacher who works at two NPSNs: what they may do *here*, and never *there*.

`school_membership.resolve_for_request` puts the school a request acts in into
`g.user_school_id`, and `app/utils/auth.py` keeps the profile's own school beside
it in `g.user_home_school_id`. The teacher-side read surfaces are already pinned
(`tests/unit/test_member_teacher_works_in_the_active_npsn.py`), and so is the
destination school's roster and assignment matrix
(`tests/unit/test_member_teaches_here.py`).

What this file pins is the **write and gate half of the same loop**, executed one
door at a time against a database that holds *both* schools' rows, fully
populated and distinguishable:

* the destination school's admin can map the member (`teacher_assignments.save`
  writes into the school that asked, and only for classes that school owns);
* an assignment made in one NPSN grants **nothing** in the other — the class the
  member teaches at work is theirs to draw pupils from there, and is not theirs at
  home, which is the property that makes the mapping authoritative per tenant
  rather than a permission that follows the person;
* the door that hands a class roster to the exam builder refuses a class the
  member is not assigned to in that NPSN, before it reads a single pupil;
* the weight component a paper is filed under is this NPSN's — another school's
  component id is dropped, so the final mark a paper contributes to is weighted by
  the policy of the school the paper belongs to and no other.

A fixture that held only one school could not tell "reads the active NPSN" from
"reads nothing", which is why every test here is answered by *both* schools.
"""
from __future__ import annotations

import inspect

from flask import g

from tests.unit.membership_fixtures import FakeDb

HOME = "sc-home"
WORK = "sc-work"
YEAR_WORK = "2026/2027"
YEAR_HOME = "2025/2026"


def _db(*, mapped=True, extra=None):
    """Two schools' rows, with the member's own mapping written by WORK's admin.

    `mapped=False` is the state before the destination school has done anything —
    the member exists, the class does not teach itself.
    """
    tables = {
        "schools": [{"id": HOME, "name": "SMP Rumah", "npsn": "11111111"},
                    {"id": WORK, "name": "SMA Tempat Kerja", "npsn": "22222222"}],
        "school_years": [
            {"id": "y-home", "name": YEAR_HOME, "is_active": True, "school_id": HOME},
            {"id": "y-work", "name": YEAR_WORK, "is_active": True, "school_id": WORK},
        ],
        "classes": [
            {"id": "c-home", "name": "Kelas Rumah", "grade_level": "8", "school_id": HOME},
            {"id": "c-work", "name": "Kelas Kerja", "grade_level": "11", "school_id": WORK},
            {"id": "c-work2", "name": "Kelas Lain", "grade_level": "11", "school_id": WORK},
        ],
        "subjects": [
            {"id": "s-home", "name": "Mapel Rumah", "school_id": HOME, "is_active": True},
            {"id": "s-work", "name": "Mapel Kerja", "school_id": WORK, "is_active": True},
        ],
        # A component per school, each carrying the whole default, so both
        # schools can answer "this is how we weight" — with different ids.
        "grade_component_type": [
            {"id": "comp-home", "school_id": HOME, "name": "Komponen Rumah",
             "sort_order": 1, "is_active": True, "default_weight": 100},
            {"id": "comp-work", "school_id": WORK, "name": "Komponen Kerja",
             "sort_order": 1, "is_active": True, "default_weight": 100},
        ],
        "grade_component_config": [],
        "profiles": [
            {"id": "u-1", "full_name": "Guru Lintas", "role": "guru",
             "school_id": HOME, "status": "active"},
            {"id": "m-home", "full_name": "Murid Rumah", "role": "murid",
             "school_id": HOME, "class_id": "c-home", "nisn": "1", "nis": "1"},
            {"id": "m-work", "full_name": "Murid Kerja", "role": "murid",
             "school_id": WORK, "class_id": "c-work", "nisn": "2", "nis": "2"},
            {"id": "m-work2", "full_name": "Murid Lain", "role": "murid",
             "school_id": WORK, "class_id": "c-work2", "nisn": "3", "nis": "3"},
        ],
        # The membership row that makes this teacher WORK's, written by WORK.
        "teacher_school_membership": [
            {"id": "m-u1-work", "user_id": "u-1", "school_id": WORK,
             "school_role": "guru", "status": "active"},
        ],
        "teacher_assignments": [
            {"id": "a-work", "teacher_id": "u-1", "class_id": "c-work",
             "subject_id": "s-work", "school_id": WORK, "status": "active",
             "school_year": YEAR_WORK},
        ] if mapped else [],
    }
    if extra:
        tables.update(extra)
    return FakeDb(**tables)


def _answer(result):
    """``(payload, status)`` for a view that returns either a Response or a tuple."""
    body, status = result if isinstance(result, tuple) else (result, None)
    return body.get_json(), (status if status is not None else body.status_code)


def _acting_as_member(app, url="/teacher/classes"):
    ctx = app.test_request_context(url)
    ctx.push()
    g.user_id, g.user_role = "u-1", "guru"
    g.user_home_school_id, g.user_school_id = HOME, WORK
    return ctx


# ── the record the destination school's admin writes ─────────────────────────

def test_the_admin_maps_the_member_into_its_own_school():
    """The write the mapping page makes: this school, this teacher, this pair."""
    from app.services import teacher_assignments as ta

    db = _db(mapped=False)
    ok, payload = ta.save(db, WORK, "u-1", [("c-work", "s-work")], year_name=YEAR_WORK)

    assert ok, payload
    assert payload["added"] == [{"class_id": "c-work", "subject_id": "s-work"}]
    written = db.write_payloads("teacher_assignments")
    assert written, "the mapping the admin made was not written"
    row = written[0]
    assert row["teacher_id"] == "u-1"
    assert row["class_id"] == "c-work"
    assert row["school_id"] == WORK, "the assignment was filed under another NPSN"
    assert row["school_year"] == YEAR_WORK


def test_the_mapping_refuses_a_class_from_another_npsn_and_writes_nothing():
    """A manipulated form — or a stale one — cannot file the member across tenants."""
    from app.services import teacher_assignments as ta

    db = _db(mapped=False)
    ok, payload = ta.save(db, WORK, "u-1", [("c-home", "s-work")], year_name=YEAR_WORK)

    assert not ok
    assert payload["status"] == 403
    assert payload["reason"] == "Ada kelas yang bukan milik sekolah ini"
    assert db.writes == [], "a refused mapping left a row behind"


def test_the_mapping_refuses_a_subject_from_another_npsn_and_writes_nothing():
    from app.services import teacher_assignments as ta

    db = _db(mapped=False)
    ok, payload = ta.save(db, WORK, "u-1", [("c-work", "s-home")], year_name=YEAR_WORK)

    assert not ok
    assert payload["status"] == 403
    assert db.writes == []


# ── the mapping is per NPSN: it grants work there, and nothing at home ───────

def test_the_class_the_member_teaches_at_work_is_not_theirs_at_home():
    """One row, two questions, opposite answers — the heart of the active school.

    `teacher_assignments` is filtered by school **and** teacher, and this is the
    assertion that says so from the outside: the same person, the same class id,
    asked about the two schools that person is known to.
    """
    from app.services import teacher_assignments as ta

    db = _db()

    assert ta.assigned_class_ids(db, WORK, "u-1", YEAR_WORK) == {"c-work"}
    assert ta.assigned_class_ids(db, HOME, "u-1", YEAR_HOME) == set(), (
        "an assignment made by one NPSN granted the same teacher work in another")


def test_the_pupils_drawn_for_that_class_are_the_destinations():
    """The roster read is school **and** class, so a foreign class name reads none."""
    from app.services import teacher_assignments as ta

    db = _db()

    at_work = ta.students_in_classes(db, WORK, ["c-work"])
    assert [p["full_name"] for p in at_work] == ["Murid Kerja"]
    assert [p["school_id"] for p in at_work] == [WORK]
    # The same class id, asked about the school that does not own it: nobody.
    assert ta.students_in_classes(db, HOME, ["c-work"]) == []


# ── the write that a scoped teacher's paper goes through ─────────────────────

def test_a_paper_may_only_take_the_class_the_member_teaches_in_that_npsn():
    """The exam builder's write-side scope, asked the question twice.

    `unassigned_class_ids` is what `exam_form`/`exam_detail` call before saving a
    paper. For a guru it must accept exactly the pair the destination school
    mapped, refuse another class of the same school, and refuse everything at a
    school that never mapped them.
    """
    from app.services import assignments

    db = _db()

    assert assignments.unassigned_class_ids(db, "u-1", WORK, "s-work", ["c-work"]) == []
    assert assignments.unassigned_class_ids(db, "u-1", WORK, "s-work", ["c-work2"]) == ["c-work2"]
    # The same pair, asked of the home NPSN: the mapping does not travel.
    assert assignments.unassigned_class_ids(db, "u-1", HOME, "s-work", ["c-work"]) == ["c-work"]


def test_the_scoped_roles_are_the_roles_that_teach():
    """A role that is not scoped writes for the whole school, so the list is named."""
    from app.services import assignments

    assert assignments.SCOPED_ROLES == ("guru", "teacher", "principal", "vice_principal")
    assert assignments.is_scoped_role("guru")
    assert not assignments.is_scoped_role("admin_sekolah")
    assert not assignments.is_scoped_role("super_admin")


# ── the door that hands out a class roster ───────────────────────────────────

def test_the_roster_door_refuses_a_class_the_member_does_not_teach_here(app, monkeypatch):
    """`/teacher/api/exam-roster` gates before it reads, and against this NPSN.

    The refusal has to happen *before* the pupils are read, or a refusal that
    answers 403 has still handed nothing but has still asked a database for a
    class it may not see. Asserted on the read log, not on the status alone.
    """
    from app.routes import teacher as teacher_routes

    db = _db()
    monkeypatch.setattr(teacher_routes, "get_supabase", lambda: db)

    # `inspect.unwrap` steps past `teacher_or_admin_required`: this test is about
    # which school the door answers for, and the decorator's own refusals have
    # their guards (`tests/unit/test_teacher_exam_authority.py` and friends).
    view = inspect.unwrap(teacher_routes.exam_roster)
    ctx = _acting_as_member(app, "/teacher/api/exam-roster?class_id=c-work2")
    try:
        payload, status = _answer(view())
    finally:
        ctx.pop()

    assert status == 403
    assert payload["error"] == "Anda tidak ditugaskan untuk kelas terpilih"
    assert "profiles" not in db.reads, "the door read pupils for a class it refuses"


def test_the_roster_door_answers_for_the_class_the_member_does_teach(app, monkeypatch):
    """The positive half: the assigned pair is not refused, and it is asked of WORK."""
    from app.routes import teacher as teacher_routes

    db = _db()
    asked = []

    def _roster(supabase, school_id, class_ids, year_id):
        asked.append((school_id, list(class_ids)))
        return [{"class_id": "c-work", "student_id": "m-work", "full_name": "Murid Kerja"}]

    monkeypatch.setattr(teacher_routes, "get_supabase", lambda: db)
    monkeypatch.setattr(teacher_routes.exam_targets, "roster_for_classes", _roster)

    view = inspect.unwrap(teacher_routes.exam_roster)
    ctx = _acting_as_member(app, "/teacher/api/exam-roster?class_id=c-work")
    try:
        payload, status = _answer(view())
    finally:
        ctx.pop()

    assert status == 200
    assert asked == [(WORK, ["c-work"])], "the roster was drawn for another NPSN"
    assert [c["class_id"] for c in payload["classes"]] == ["c-work"]
    assert payload["totals"] == {"selected": 1, "total": 1}


# ── the weights a paper is filed under are this NPSN's ──────────────────────

def test_a_weight_component_from_another_npsn_is_dropped():
    """Where a mark is weighted: the paper's school decides, and only it.

    `_resolve_grade_component` turns a posted component id into one this school
    owns, or `None` (an uncategorised paper, which falls out of the weighted sum).
    Asked in both directions, so an implementation that returned whatever it was
    handed fails one of the four.
    """
    from app.routes import teacher as teacher_routes

    db = _db()
    resolve = teacher_routes._resolve_grade_component

    assert resolve(db, WORK, "comp-work") == "comp-work"
    assert resolve(db, WORK, "comp-home") is None, "another NPSN's component was accepted"
    assert resolve(db, HOME, "comp-home") == "comp-home"
    assert resolve(db, HOME, "comp-work") is None


def test_the_component_picker_offers_the_school_the_teacher_is_working_in():
    """The builder's picker, both schools, different names — nothing shared."""
    from app.routes import teacher as teacher_routes

    db = _db()
    by_subject = teacher_routes._grade_components_by_subject(db, WORK)

    assert list(by_subject) == ["s-work"], "the picker listed another NPSN's subjects"
    assert [c["id"] for c in by_subject["s-work"]] == ["comp-work"]
    assert [c["name"] for c in by_subject["s-work"]] == ["Komponen Kerja"]

    # The negative control: the same read aimed at home answers with home's rows,
    # which is what makes the assertion above mean "WORK", not "nothing".
    home = teacher_routes._grade_components_by_subject(db, HOME)
    assert list(home) == ["s-home"]
    assert [c["name"] for c in home["s-home"]] == ["Komponen Rumah"]


def test_a_weight_component_is_asked_for_with_the_school_it_belongs_to(monkeypatch):
    """The rule is one question to `grade_weighting`, carrying the acting school."""
    from app.routes import teacher as teacher_routes

    db = _db()
    seen = []

    def _ids(supabase, school_id):
        seen.append(school_id)
        return {"comp-work"}

    monkeypatch.setattr(teacher_routes.grade_weighting, "component_ids", _ids)
    assert teacher_routes._resolve_grade_component(db, WORK, "comp-work") == "comp-work"
    assert seen == [WORK]


def test_the_two_schools_are_two_different_schools():
    """A guard on the fixture itself, because every assertion above leans on it.

    If `HOME` and `WORK` were the same string — or the fixture held only one
    school's rows — then "asked about the active school" and "asked about the home
    school" would be the same question and this file would prove nothing.
    """
    db = _db()

    assert HOME != WORK
    assert {r["school_id"] for r in db.tables["classes"]} == {HOME, WORK}
    assert {c["id"] for c in db.tables["grade_component_type"]} == {"comp-home", "comp-work"}
    assert {r["full_name"] for r in db.tables["profiles"] if r["role"] == "murid"} == {
        "Murid Rumah", "Murid Kerja", "Murid Lain"}
