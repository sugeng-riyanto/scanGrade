"""A vice principal or head of school may teach a subject — within the school's own rule.

Requested: "Penugasan VP dan principals harusnya memungkinkan sebagai guru mapel
tertentu, mengajar lebih dari satu mapel dan bisa hanya di kelas tertentu."

The matrix could already hold many subjects and class-specific subsets, but only
for `guru`. Officials live solely in `profiles` (`school_officials.py` says so on
purpose: no `teachers` row, no attribute of their own), so three separate gates
kept them out:

* `teacher_assignments.teacher_in_school` demanded `role='guru'`, so the matrix
  refused to open for them even if the id were reachable;
* the roster at `/admin-sekolah/teachers` read only the `teachers` table, so an
  official never appeared to be assigned;
* `teacher_or_admin_required` and `can_manage_exam` did not name the two roles,
  so even a stored assignment could not be acted upon.

The chosen boundary is *scoped like a teacher*: an assigned official reaches the
teacher workspace, but writes are limited to the (class, subject) pairs they
hold and to their OWN papers — never a colleague's. These guards pin all four
properties, so one of them cannot drift back to "read-only" or widen to "the
whole school" without a red test.
"""
from __future__ import annotations

import pathlib
from types import SimpleNamespace

from app.services import assignments, teacher_assignments as ta
from app.utils.exam_access import can_manage_exam

ROOT = pathlib.Path(__file__).resolve().parents[2]
ADMIN_SEKOLAH = (ROOT / "app" / "routes" / "admin_sekolah.py").read_text(encoding="utf-8")
ROSTER_HTML = (ROOT / "app" / "templates" / "admin_sekolah" / "teachers.html").read_text(encoding="utf-8")
AUTH_PY = (ROOT / "app" / "utils" / "auth.py").read_text(encoding="utf-8")

OFFICIALS = ("principal", "vice_principal")
SCHOOL = "school-1"
OTHER_SCHOOL = "school-2"


class _Query:
    def __init__(self, rows):
        self.rows = list(rows)
        self._in = None

    def select(self, *a, **k):
        return self

    def eq(self, col, val):
        self.rows = [r for r in self.rows if str(r.get(col)) == str(val)]
        return self

    def in_(self, col, values):
        wanted = {str(v) for v in values}
        self.rows = [r for r in self.rows if str(r.get(col)) in wanted]
        return self

    def limit(self, *a, **k):
        return self

    def execute(self):
        return SimpleNamespace(data=self.rows)


class _Sb:
    def __init__(self, profiles):
        self.profiles = profiles

    def table(self, name):
        assert name == "profiles", name
        return _Query(self.profiles)


def _profile(role, *, school=SCHOOL, uid="u-1"):
    return {"id": uid, "role": role, "school_id": school}


# ── the matrix must open for an official ─────────────────────────────────────

def test_a_guru_still_belongs():
    assert ta.teacher_in_school(_Sb([_profile("guru")]), SCHOOL, "u-1") is True


def test_a_vice_principal_belongs():
    assert ta.teacher_in_school(_Sb([_profile("vice_principal")]), SCHOOL, "u-1") is True


def test_a_head_of_school_belongs():
    assert ta.teacher_in_school(_Sb([_profile("principal")]), SCHOOL, "u-1") is True


def test_a_pupil_does_not_belong():
    assert ta.teacher_in_school(_Sb([_profile("murid")]), SCHOOL, "u-1") is False


def test_an_official_of_another_school_does_not_belong():
    sb = _Sb([_profile("principal", school=OTHER_SCHOOL)])
    assert ta.teacher_in_school(sb, SCHOOL, "u-1") is False


# ── and the assignment feature must scope them exactly like a teacher ────────

def test_an_official_is_a_scoped_role():
    """Otherwise the exam builder would offer them every class in the school."""
    for role in OFFICIALS:
        assert assignments.is_scoped_role(role), role


def test_a_guru_is_still_scoped():
    for role in ("guru", "teacher"):
        assert assignments.is_scoped_role(role), role


def test_the_admins_are_still_not_scoped():
    for role in ("admin_sekolah", "super_admin", "admin"):
        assert not assignments.is_scoped_role(role), role


def test_the_same_class_under_a_foreign_subject_still_refuses_an_official():
    """`unassigned_class_ids` reads the role nowhere — it takes the id — so this
    holds the pair rule for whoever the school assigned, official or guru."""
    row = {"class_id": "c-1", "subject_id": "s-physics", "school_id": SCHOOL,
           "teacher_id": "u-1", "status": "active"}
    sb = _Sb([])
    sb_assign = SimpleNamespace(table=lambda name: _Query([row]))
    assert assignments.unassigned_class_ids(sb_assign, "u-1", SCHOOL, "s-maths", ["c-1"]) == ["c-1"]


# ── the workspace: own papers only ───────────────────────────────────────────

OWN = {"id": "exam-1", "school_id": SCHOOL, "teacher_id": "u-1"}
COLLEAGUE = {"id": "exam-2", "school_id": SCHOOL, "teacher_id": "someone-else"}


def test_an_official_manages_their_own_paper():
    for role in OFFICIALS:
        assert can_manage_exam("u-1", role, SCHOOL, OWN) is True, role


def test_an_official_does_not_manage_a_colleagues_paper():
    """The whole reason the boundary is *scoped like a teacher*: oversight must not
    become the power to unpublish or overwrite someone else's marks."""
    for role in OFFICIALS:
        assert can_manage_exam("u-1", role, SCHOOL, COLLEAGUE) is False, role


def test_an_official_does_not_manage_another_schools_paper():
    for role in OFFICIALS:
        assert can_manage_exam("u-1", role, OTHER_SCHOOL, OWN) is False, role


def test_an_official_with_no_school_manages_nothing():
    for role in OFFICIALS:
        assert can_manage_exam("u-1", role, None, OWN) is False, role


def test_the_admin_still_manages_the_whole_school():
    assert can_manage_exam("admin-1", "admin_sekolah", SCHOOL, COLLEAGUE) is True


def test_a_guru_still_manages_only_their_own():
    assert can_manage_exam("u-1", "guru", SCHOOL, OWN) is True
    assert can_manage_exam("u-1", "guru", SCHOOL, COLLEAGUE) is False


# ── the workspace gate names them ────────────────────────────────────────────

def test_the_teacher_workspace_admits_officials():
    block = AUTH_PY.split("def teacher_or_admin_required")[1].split("\ndef ")[0]
    for role in OFFICIALS:
        assert role in block, (
            f"`teacher_or_admin_required` does not name {role}: an assigned official "
            "reaches the dashboard only to be bounced home")


# ── the roster lists them, so they can be assigned at all ────────────────────

def test_the_roster_reads_the_officials():
    assert "officials_service.list_officials" in ADMIN_SEKOLAH, (
        "the roster builds its rows from `teachers` only, so a head of school can "
        "never be seen — let alone assigned — on /admin-sekolah/teachers")


def test_the_roster_marks_which_rows_are_officials():
    assert "is_official" in ADMIN_SEKOLAH, (
        "officials are merged into the roster without a flag, so the template cannot "
        "tell a head of school from a guru and offers the wrong actions")


def test_the_roster_template_names_the_official_roles():
    assert "principal" in ROSTER_HTML and "vice_principal" in ROSTER_HTML, (
        "an official row is drawn with no word for what they are")
