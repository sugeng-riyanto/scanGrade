"""A teacher's assignments must read the same everywhere: dashboard, classes, students.

Requested: "Pastikan penugasan guru terhadap subject-subject masing-masing match
dengan dashboard masing-masing guru dan match dengan kelas serta match dengan
murid sesuai kelasnya."

Three readers of the same table had drifted from the rule the rest of the app
already applies (`teacher_assignments.current_pairs` / `school_pairs_detail`:
`status='active'` **and** the active school year):

* `req_cache.teacher_assignments_for` — the reader the dashboard uses — took every
  row, so a pair the admin had **removed** (soft-closed with `status='inactive'`,
  the whole point of migration 045) still showed on the teacher's dashboard;
* `/teacher/classes` ran its own query with no `status`/year filter at all;
* `/teacher/students` listed **every pupil in the school**, not the pupils in the
  classes the teacher is actually assigned to.

These guards pin the shared filter and the two pages to it, so the three cannot
answer the same question differently again.
"""
from __future__ import annotations

import pathlib
from types import SimpleNamespace

from app.services import teacher_assignments as ta

ROOT = pathlib.Path(__file__).resolve().parents[2]
TEACHER_PY = (ROOT / "app" / "routes" / "teacher.py").read_text(encoding="utf-8")
REQ_CACHE = (ROOT / "app" / "utils" / "req_cache.py").read_text(encoding="utf-8")

SCHOOL = "school-1"
TEACHER = "teacher-1"
YEAR = "2026/2027"


def _row(class_id="c1", subject_id="s1", *, status="active", school_year=YEAR,
         teacher=TEACHER, school=SCHOOL):
    row = {"class_id": class_id, "subject_id": subject_id, "teacher_id": teacher,
           "school_id": school}
    if status is not None:
        row["status"] = status
    if school_year is not None:
        row["school_year"] = school_year
    return row


class _Q:
    def __init__(self, sb, table):
        self.sb = sb
        self.table = table
        self.filters = []

    def select(self, *a, **k):
        return self

    def eq(self, col, val):
        self.filters.append(("eq", col, val))
        return self

    def in_(self, col, values):
        self.filters.append(("in", col, {str(v) for v in values}))
        return self

    def limit(self, *a, **k):
        return self

    def order(self, *a, **k):
        return self

    def execute(self):
        rows = [r for r in self.sb.data.get(self.table, []) if self._match(r)]
        return SimpleNamespace(data=rows)

    def _match(self, row):
        for op, col, val in self.filters:
            if op == "eq" and str(row.get(col)) != str(val):
                return False
            if op == "in" and str(row.get(col)) not in val:
                return False
        return True


class _Sb:
    def __init__(self, data=None):
        self.data = data or {}

    def table(self, name):
        return _Q(self, name)


# ── the shared rule ──────────────────────────────────────────────────────────

class TestTheActiveRowRule:
    def test_an_active_row_in_the_year_counts(self):
        assert ta.row_is_active(_row(), YEAR) is True

    def test_a_soft_closed_row_does_not(self):
        """Migration 045 closes a removed pair with `status='inactive'`; a reader
        that ignores it shows a pair the admin took away."""
        assert ta.row_is_active(_row(status="inactive"), YEAR) is False

    def test_a_row_from_another_year_does_not(self):
        assert ta.row_is_active(_row(school_year="2025/2026"), YEAR) is False

    def test_a_row_without_a_status_is_active(self):
        """A row written before 045 carries no status and is a real assignment."""
        assert ta.row_is_active(_row(status=None), YEAR) is True

    def test_a_row_without_a_year_belongs_to_the_active_year(self):
        assert ta.row_is_active(_row(school_year=None), YEAR) is True


# ── the classes a teacher actually holds ─────────────────────────────────────

class TestAssignedClassIds:
    def test_only_active_classes_are_returned(self):
        sb = _Sb({"teacher_assignments": [
            _row("c1"), _row("c2"), _row("c3", status="inactive")]})
        assert ta.assigned_class_ids(sb, SCHOOL, TEACHER, YEAR) == {"c1", "c2"}

    def test_classes_from_another_year_are_dropped(self):
        sb = _Sb({"teacher_assignments": [
            _row("c1"), _row("c9", school_year="2025/2026")]})
        assert ta.assigned_class_ids(sb, SCHOOL, TEACHER, YEAR) == {"c1"}

    def test_a_teacher_with_nothing_assigned_has_no_classes(self):
        sb = _Sb({"teacher_assignments": []})
        assert ta.assigned_class_ids(sb, SCHOOL, TEACHER, YEAR) == set()


# ── the pupils in those classes ──────────────────────────────────────────────

class TestStudentsInClasses:
    def test_only_pupils_in_the_given_classes_come_back(self):
        sb = _Sb({"profiles": [
            {"id": "p1", "role": "murid", "school_id": SCHOOL, "class_id": "c1"},
            {"id": "p2", "role": "murid", "school_id": SCHOOL, "class_id": "c2"},
            {"id": "p3", "role": "murid", "school_id": SCHOOL, "class_id": "c9"},
            {"id": "t1", "role": "guru", "school_id": SCHOOL, "class_id": "c1"},
        ]})
        got = ta.students_in_classes(sb, SCHOOL, {"c1", "c2"})
        assert {s["id"] for s in got} == {"p1", "p2"}

    def test_no_assigned_classes_returns_nobody_rather_than_the_school(self):
        """The failure this replaces: an unassigned teacher saw every pupil."""
        sb = _Sb({"profiles": [
            {"id": "p1", "role": "murid", "school_id": SCHOOL, "class_id": "c1"}]})
        assert ta.students_in_classes(sb, SCHOOL, set()) == []

    def test_another_schools_pupil_never_appears(self):
        sb = _Sb({"profiles": [
            {"id": "p1", "role": "murid", "school_id": "other", "class_id": "c1"},
            {"id": "p2", "role": "murid", "school_id": SCHOOL, "class_id": "c1"},
        ]})
        got = ta.students_in_classes(sb, SCHOOL, {"c1"})
        assert {s["id"] for s in got} == {"p2"}


# ── the wiring: all three readers use the one rule ───────────────────────────

class TestTheReadersAgree:
    def test_the_dashboard_reader_filters_like_the_rule(self):
        body = REQ_CACHE.split("def teacher_assignments_for", 1)[1].split("\ndef ", 1)[0]
        assert "row_is_active" in body, (
            "the dashboard's assignment reader takes soft-closed or out-of-year "
            "rows, so a removed pair keeps showing on the teacher dashboard")
        assert "active_school_year" in body, (
            "the dashboard reader never resolves the active year, so it cannot "
            "drop last year's pairs")

    def test_the_classes_page_uses_the_filtered_reader(self):
        body = TEACHER_PY.split("def teacher_classes(", 1)[1].split("\n@teacher_bp", 1)[0]
        assert "teacher_assignments_for(" in body, (
            "/teacher/classes runs its own unfiltered query instead of the "
            "reader the dashboard uses")

    def test_the_students_page_scopes_to_assigned_classes(self):
        body = TEACHER_PY.split("def students(", 1)[1].split("\n@teacher_bp", 1)[0]
        assert "assigned_class_ids(" in body, (
            "/teacher/students must resolve the teacher's assigned classes")
        assert "students_in_classes(" in body, (
            "/teacher/students lists the whole school because it never narrows "
            "the pupils to the teacher's classes")
