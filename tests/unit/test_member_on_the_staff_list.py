"""A teacher the school accepted is on its staff list — in every picker and write.

`tests/unit/test_member_teaches_here.py` pins the roster, the assignment matrix and
the membership doors. The invigilation side was left behind, and it failed in the same
two halves the roster did:

* every picker read the `teachers` table alone — `invigilation.form_options`,
  `invigilation_matrix.teachers_of` — so the school that accepted a guru from another
  NPSN could not offer them a duty (the grid, the auto-fill and the importer all draw
  from that list);
* the writes validated the same table — `assign_invigilator`, and the click grid's
  `assign`/`toggle`/`apply` — so even a hand-made POST was refused as
  `teacher_not_in_school`.

Both halves ask one list now (`teacher_assignments.staff_directory` / `is_staff`), and
these tests pin the three properties that matter: the member is on it, their own staff
still is, and a stranger is still refused. The last one is the reason this is a
widening rather than a new rule — the door opened for one more answer, not for any id
a request names.
"""
from __future__ import annotations

from tests.unit.membership_fixtures import FakeDb, membership, person, school

SCHOOL_A = "sc-a"
SCHOOL_B = "sc-b"
STRANGER_SCHOOL = "sc-z"

OWNER = "teacher-own"        # this school's own employee
MEMBER = "teacher-member"    # accepted from another NPSN
STRANGER = "teacher-stranger"


def _db() -> FakeDb:
    """Two schools, one member, one stranger — and the member has no `teachers` row."""
    return FakeDb(
        schools=[school(SCHOOL_A, "SMP A", "11111111"),
                 school(SCHOOL_B, "SMA B", "22222222"),
                 school(STRANGER_SCHOOL, "SMK Z", "99999999")],
        # Only the school's OWN employee has an employment row, which is the whole
        # point: the member teaches here without one (migration 044's model).
        teachers=[{"id": OWNER, "school_id": SCHOOL_A,
                   "profiles": {"full_name": "Bu Sari"}}],
        profiles=[person(OWNER, "Bu Sari", "sari@a.test"),
                  person(MEMBER, "Pak Budi", "budi@b.test"),
                  person(STRANGER, "Orang Luar", "luar@z.test")],
        teacher_school_membership=[membership(MEMBER, SCHOOL_A, "active")],
    )


# ── the list itself ─────────────────────────────────────────────────────────

def test_the_schools_own_staff_and_its_members_are_one_list():
    from app.services import teacher_assignments as ta

    staff = ta.staff_directory(_db(), SCHOOL_A)

    assert [t["id"] for t in staff] == [OWNER, MEMBER], staff
    assert [t["name"] for t in staff] == ["Bu Sari", "Pak Budi"], "listed by name"
    assert [t["member"] for t in staff] == [False, True], "the two ways in, labelled"


def test_the_same_person_is_not_listed_twice():
    """A member with an employment row too is one person, and one staff entry."""
    from app.services import teacher_assignments as ta

    db = _db()
    db.rows("teachers").append({"id": MEMBER, "school_id": SCHOOL_A,
                                "profiles": {"full_name": "Pak Budi"}})

    staff = ta.staff_directory(db, SCHOOL_A)

    assert [t["id"] for t in staff] == [OWNER, MEMBER], staff
    assert staff[1]["member"] is False, "the employment row is the stronger fact"


def test_another_schools_teacher_is_not_on_it():
    from app.services import teacher_assignments as ta

    assert ta.staff_directory(_db(), STRANGER_SCHOOL) == []
    assert ta.staff_directory(_db(), "") == []


def test_is_staff_answers_the_write_side_of_the_same_question():
    """A picker that offers somebody the write refuses is a form that lies."""
    from app.services import teacher_assignments as ta

    db = _db()
    assert ta.is_staff(db, SCHOOL_A, OWNER) is True
    assert ta.is_staff(db, SCHOOL_A, MEMBER) is True, "the write must accept what the picker offers"
    assert ta.is_staff(db, SCHOOL_A, STRANGER) is False
    assert ta.is_staff(db, SCHOOL_A, None) is False


def test_a_read_that_fails_keeps_the_schools_own_staff_and_adds_nobody():
    """`active_members` answers `[]` when it cannot read — a picker, not an outage."""
    from app.services import teacher_assignments as ta

    db = _db()
    db.fail.add("teacher_school_membership")

    staff = ta.staff_directory(db, SCHOOL_A)

    assert [t["id"] for t in staff] == [OWNER]
    assert ta.is_staff(db, SCHOOL_A, MEMBER) is False


# ── the pickers ─────────────────────────────────────────────────────────────

def test_the_schedule_form_offers_the_member():
    from app.services import invigilation

    options = invigilation.form_options(_db(), SCHOOL_A)

    assert [t["id"] for t in options["teachers"]] == [OWNER, MEMBER]
    assert [t["name"] for t in options["teachers"]] == ["Bu Sari", "Pak Budi"]


def test_the_matrix_picker_offers_the_member():
    from app.services import invigilation_matrix

    assert [t["id"] for t in invigilation_matrix.teachers_of(_db(), SCHOOL_A)] == [OWNER, MEMBER]


def test_the_matrix_picker_and_the_matrix_are_one_list():
    """The grid's name map is the same read, so a duty cannot print as an empty cell."""
    from app.services import invigilation_matrix

    db = _db()
    db.tables.setdefault("exam_period", []).append(
        {"id": "p1", "school_id": SCHOOL_A, "name": "Sesi 1", "is_active": True})
    db.tables.setdefault("exam_room", []).append(
        {"id": "r1", "school_id": SCHOOL_A, "name": "Ruang 1", "is_active": True})
    db.tables.setdefault("invigilation_duty", []).append(
        {"id": "d1", "school_id": SCHOOL_A, "exam_date": "2026-06-01",
         "period_id": "p1", "room_id": "r1", "teacher_id": MEMBER, "source": "manual"})

    grid = invigilation_matrix.matrix(db, SCHOOL_A, "2026-06-01")

    assert grid["cells"]["p1"]["r1"][0]["teacher_name"] == "Pak Budi", (
        "a duty held by a member printed without a name")


# ── the writes ──────────────────────────────────────────────────────────────

def test_assigning_a_member_to_a_sitting_is_accepted():
    from app.services import invigilation

    db = _db()
    db.tables.setdefault("invigilation_schedules", []).append(
        {"id": "sch1", "school_id": SCHOOL_A, "exam_id": "e1", "class_id": "c1"})

    out = invigilation.assign_invigilator(db, SCHOOL_A, schedule_id="sch1",
                                          teacher_id=MEMBER, actor_id="admin-1")

    assert out["ok"] is True and out["created"] is True, out
    written = db.write_payloads("invigilator_assignments", "insert")
    assert written and written[0]["teacher_id"] == MEMBER
    assert written[0]["school_id"] == SCHOOL_A


def test_assigning_a_stranger_is_still_refused():
    """Widened for one answer, not for whatever id a request carries."""
    from app.services import invigilation

    db = _db()
    db.tables.setdefault("invigilation_schedules", []).append(
        {"id": "sch1", "school_id": SCHOOL_A, "exam_id": "e1", "class_id": "c1"})

    out = invigilation.assign_invigilator(db, SCHOOL_A, schedule_id="sch1",
                                          teacher_id=STRANGER, actor_id="admin-1")

    assert out["ok"] is False and out["reason"] == "teacher_not_in_school"
    assert db.write_payloads("invigilator_assignments", "insert") == []


def test_the_click_grids_teacher_check_is_the_same_predicate():
    """`invigilation_matrix`'s three writes share one helper, and it is the merged list."""
    from app.services import invigilation_matrix

    db = _db()
    assert invigilation_matrix._teacher_in_school(db, SCHOOL_A, MEMBER) is True
    assert invigilation_matrix._teacher_in_school(db, SCHOOL_A, OWNER) is True
    assert invigilation_matrix._teacher_in_school(db, SCHOOL_A, STRANGER) is False


def test_no_picker_or_write_reads_the_employment_table_for_the_whole_answer():
    """The three call sites that used to be `_row(…, "teachers", …)`, pinned.

    A future edit that reaches for the employment table again would compile, pass a
    smoke test on a single-school box, and quietly refuse every cross-NPSN teacher a
    duty — the exact defect this file exists to keep closed.
    """
    from pathlib import Path

    from app.services import invigilation, invigilation_matrix

    for module in (invigilation, invigilation_matrix):
        src = Path(module.__file__).read_text(encoding="utf-8")
        assert '_row(supabase, "teachers"' not in src, module.__name__
        assert '_row_in_school(supabase, "teachers"' not in src, module.__name__
        assert "_teacher_in_school" in src, module.__name__
