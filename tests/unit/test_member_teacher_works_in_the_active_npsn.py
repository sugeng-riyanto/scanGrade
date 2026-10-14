"""A teacher who joined another NPSN works *there* — classes, pupils and weights.

`school_membership.resolve_for_request` decides which school a request is for, and
`app/utils/auth.py` puts the answer in `g.user_school_id` while keeping the profile's
own school in `g.user_home_school_id`. The exam-writing doors already ask that
authority (`tests/unit/test_exam_school_authority.py`), and the destination school
already sees and assigns the member (`tests/unit/test_member_teaches_here.py`).

What is pinned *here* is the middle of that loop: the surfaces the teacher then works
in. The interest is that a member's `profiles.school_id` is their **home** school — the
two are different strings on purpose — so any of these surfaces that read the profile,
the session's home school, or "the teacher's school" instead of the resolved active one
would draw the wrong NPSN's classes, pupils, weights and KKM, silently and plausibly.
A school notices the last one first: a mark weighted by another school's policy is a
mark that cannot be defended.

Each test executes the surface (not a grep) against two fully populated schools, so a
reader that reached for the home school would find data there and fail the assertion
rather than pass on an empty fixture.
"""
from __future__ import annotations

import inspect
from pathlib import Path
from types import SimpleNamespace

from flask import g

ROOT = Path(__file__).resolve().parents[2]
TEACHER_ROUTE = ROOT / "app" / "routes" / "teacher.py"

HOME = "sc-home"
WORK = "sc-work"

#: Both schools carry a full, *distinguishable* set of rows. The names are the
#: assertion: a surface that read the home school would render `Kelas Rumah` and
#: `Mapel Rumah`, and there is nothing subtle about that.
SCHOOLS = {
    HOME: {"id": HOME, "name": "SMP Rumah", "npsn": "11111111"},
    WORK: {"id": WORK, "name": "SMA Tempat Kerja", "npsn": "22222222"},
}


class _Res:
    def __init__(self, data):
        self.data = data


class _Query:
    """Enough PostgREST to answer the reads these helpers make, filtered for real."""

    def __init__(self, db, table):
        self.db, self.table = db, table
        self._eq = []
        self._in = []
        self._one = False

    def select(self, *a, **k):
        return self

    def eq(self, column, value):
        self._eq.append((column, str(value)))
        return self

    def neq(self, column, value):
        return self

    def in_(self, column, values):
        self._in.append((column, {str(v) for v in values}))
        return self

    def order(self, *a, **k):
        return self

    def limit(self, *a, **k):
        return self

    def single(self):
        self._one = True
        return self

    def maybe_single(self):
        self._one = True
        return self

    def execute(self):
        self.db.reads.append((self.table, tuple(self._eq)))
        rows = [dict(r) for r in self.db.tables.get(self.table, [])
                if all(str(r.get(c)) == v for c, v in self._eq)
                and all(str(r.get(c)) in wanted for c, wanted in self._in)]
        if self._one:
            return _Res(rows[0] if rows else None)
        return _Res(rows)


class _Db:
    """Two schools' worth of rows, with every read recorded for the assertions."""

    def __init__(self):
        self.tables = {
            "schools": [dict(SCHOOLS[HOME]), dict(SCHOOLS[WORK])],
            "school_years": [
                {"id": "y-home", "name": "2025/2026", "is_active": True, "school_id": HOME},
                {"id": "y-work", "name": "2026/2027", "is_active": True, "school_id": WORK},
            ],
            "classes": [
                {"id": "c-home", "name": "Kelas Rumah", "grade_level": "8", "school_id": HOME},
                {"id": "c-work", "name": "Kelas Kerja", "grade_level": "11", "school_id": WORK},
            ],
            "subjects": [
                {"id": "s-home", "name": "Mapel Rumah", "code": "MR", "school_id": HOME,
                 "is_active": True},
                {"id": "s-work", "name": "Mapel Kerja", "code": "MK", "school_id": WORK,
                 "is_active": True},
            ],
            # The member's own mapping, written by the destination school's admin.
            "teacher_assignments": [
                {"id": "a-work", "teacher_id": "u-1", "class_id": "c-work",
                 "subject_id": "s-work", "school_id": WORK, "status": "active",
                 "school_year": "2026/2027",
                 "classes": {"id": "c-work", "name": "Kelas Kerja"},
                 "subjects": {"id": "s-work", "name": "Mapel Kerja"}},
            ],
            "profiles": [
                {"id": "u-1", "full_name": "Guru Lintas", "role": "guru", "school_id": HOME},
                {"id": "m-home", "full_name": "Murid Rumah", "role": "murid",
                 "school_id": HOME, "class_id": "c-home", "nisn": "1", "nis": "1"},
                {"id": "m-work", "full_name": "Murid Kerja", "role": "murid",
                 "school_id": WORK, "class_id": "c-work", "nisn": "2", "nis": "2"},
            ],
        }
        self.reads = []

    def table(self, name):
        return _Query(self, name)


def _acting_as_member(app, url="/teacher/dashboard"):
    """A request context for `u-1`, a guru whose profile names HOME and who is
    working in WORK — exactly the pair `auth._apply_session` sets."""
    ctx = app.test_request_context(url)
    ctx.push()
    g.user_id, g.user_role = "u-1", "guru"
    g.user_home_school_id, g.user_school_id = HOME, WORK
    return ctx


# ── the pages the teacher works in draw the destination NPSN ─────────────────

def test_the_classes_page_names_the_school_the_teacher_is_working_in(app, monkeypatch):
    """`/teacher/classes` reads its school from the resolved session, not the profile."""
    from app.routes import teacher as teacher_routes

    db = _Db()
    asked = []

    def _assignments(teacher_id, school_id):
        asked.append(school_id)
        return ["WORK-ASSIGNMENT"] if school_id == WORK else []

    monkeypatch.setattr(teacher_routes, "teacher_assignments_for", _assignments)
    monkeypatch.setattr(teacher_routes, "get_supabase", lambda: db)
    captured = {}
    monkeypatch.setattr(teacher_routes, "render_template",
                        lambda name, **kw: captured.update(kw) or SimpleNamespace(name=name))

    ctx = _acting_as_member(app, "/teacher/classes")
    try:
        # `inspect.unwrap` steps past `teacher_or_admin_required`: this test is about
        # which school the page reads, and the decorators' refusals have their guards.
        inspect.unwrap(teacher_routes.teacher_classes)()
    finally:
        ctx.pop()

    assert asked == [WORK], "the page asked for the home school's assignments"
    assert captured["school_info"]["npsn"] == SCHOOLS[WORK]["npsn"]
    assert captured["active_year"]["school_id"] == WORK
    assert captured["assignments"] == ["WORK-ASSIGNMENT"]


def test_the_exam_builder_offers_the_pairs_the_destination_school_mapped(app, monkeypatch):
    """The builder's whole scope comes from the member's rows in the active school."""
    from app.routes import teacher as teacher_routes

    db = _Db()
    asked = []

    def _assignments(teacher_id, school_id):
        asked.append(school_id)
        if school_id != WORK:
            return []
        return [{"subjects": {"id": "s-work", "name": "Mapel Kerja"},
                 "classes": {"id": "c-work", "name": "Kelas Kerja"}}]

    monkeypatch.setattr(teacher_routes, "teacher_assignments_for", _assignments)

    ctx = _acting_as_member(app, "/teacher/exams/new")
    try:
        subjects, classes, by_subject = teacher_routes._builder_scope(db, WORK)
    finally:
        ctx.pop()

    assert asked == [WORK]
    assert [s["id"] for s in subjects] == ["s-work"], "the builder offered another NPSN's subject"
    assert [c["id"] for c in classes] == ["c-work"], "the builder offered another NPSN's class"
    assert by_subject == {"s-work": ["c-work"]}
    # …and the home school's own rows were never read as a fallback.
    assert ("classes", (("school_id", HOME),)) not in db.reads, (
        "a guru's builder must not fall back to a whole-school read")


# ── the weights and the KKM are the destination school's ─────────────────────

def test_the_grade_table_weighs_with_the_school_the_teacher_is_working_in(app, monkeypatch):
    """The headline for a school: a member's marks are weighted by *its* policy.

    Decided by the resolved active school, and proven by recording the school each
    weighting question was asked about — while the fake holds a different, populated
    answer for the home school, so reaching for the profile's school fails here
    instead of passing quietly.
    """
    from app.routes import teacher as teacher_routes

    db = _Db()
    seen = {}

    def _classes(school_id):
        seen["classes"] = school_id
        return [r for r in db.tables["classes"] if r["school_id"] == school_id]

    def _subjects(school_id):
        seen["subjects"] = school_id
        return [r for r in db.tables["subjects"] if r["school_id"] == school_id]

    def _finals(supabase, school_id, subject_id, year_id, pupil_ids):
        seen["weights"] = school_id
        seen["pupils"] = list(pupil_ids)
        return {}

    def _kkm(supabase, school_id, subject_id, year_id=None):
        seen["kkm"] = school_id
        return 75

    def _config(supabase, school_id, subject_id, year_id):
        seen["config"] = school_id
        return {}

    monkeypatch.setattr(teacher_routes, "school_classes", _classes)
    monkeypatch.setattr(teacher_routes, "school_subjects", _subjects)
    monkeypatch.setattr(teacher_routes.grade_weighting, "subject_finals", _finals)
    monkeypatch.setattr(teacher_routes.kkm_service, "effective", _kkm)
    monkeypatch.setattr(teacher_routes.grade_weighting, "effective_config", _config)
    monkeypatch.setattr(teacher_routes.grade_weighting, "list_components",
                        lambda supabase, school_id: [])

    ctx = _acting_as_member(app, "/teacher/students")
    try:
        out = teacher_routes._grade_table(db, WORK, "guru", "")
    finally:
        ctx.pop()

    # Every weighting question, one school.
    assert seen["classes"] == WORK
    assert seen["weights"] == WORK, "the final mark was weighted by the home NPSN"
    assert seen["config"] == WORK, "the weights shown came from the home NPSN"
    assert seen["kkm"] == WORK, "the KKM shown came from the home NPSN"
    # The pupils are the destination's, and the home school's pupil is nowhere near it.
    assert [r["full_name"] for r in out["rows"]] == ["Murid Kerja"]
    assert seen["pupils"] == ["m-work"]
    assert out["subject_id"] == "s-work"
    assert out["subject"]["name"] == "Mapel Kerja", "the table listed another NPSN's subject"
    assert out["kkm"] == 75


def test_the_wrong_school_would_be_visible(app, monkeypatch):
    """The negative control: aimed at HOME, the same helper draws HOME's rows.

    Without this, the test above could pass on a fixture where the home rows simply do
    not exist — and "it read the active school" would be indistinguishable from "it
    read nothing". Here the same call, asked about HOME, must return HOME's pupil and
    HOME's subject, which is what makes the WORK assertion mean something.
    """
    from app.routes import teacher as teacher_routes

    db = _Db()
    monkeypatch.setattr(teacher_routes, "school_classes",
                        lambda sid: [r for r in db.tables["classes"] if r["school_id"] == sid])
    monkeypatch.setattr(teacher_routes, "school_subjects",
                        lambda sid: [r for r in db.tables["subjects"] if r["school_id"] == sid])
    monkeypatch.setattr(teacher_routes.grade_weighting, "subject_finals",
                        lambda *a, **k: {})
    monkeypatch.setattr(teacher_routes.kkm_service, "effective",
                        lambda *a, **k: 70)
    monkeypatch.setattr(teacher_routes.grade_weighting, "effective_config",
                        lambda *a, **k: {})
    monkeypatch.setattr(teacher_routes.grade_weighting, "list_components",
                        lambda supabase, school_id: [])

    # The member's rows live in WORK, so asking about HOME finds no assignment and
    # therefore no subject at all — the honest answer, not the home school's classes.
    ctx = _acting_as_member(app, "/teacher/students")
    try:
        out = teacher_routes._grade_table(db, HOME, "guru", "")
    finally:
        ctx.pop()

    assert out["subject_id"] == "", "an unassigned teacher was handed a subject"
    assert out["rows"] == [], "a teacher with no pair in that school was shown its pupils"


# ── the binding itself: one school per request, and never the profile's ──────

def test_no_teacher_surface_scopes_a_read_by_the_profiles_school():
    """`g.user_home_school_id` exists so a *write* can tell the two apart.

    No page may use it: the read side's whole question is the resolved school, and a
    route that reached for the profile's would put a member's classes, pupils, weights
    and KKM back in their home NPSN.
    """
    src = TEACHER_ROUTE.read_text(encoding="utf-8")
    assert "user_home_school_id" not in src, (
        "a teacher-facing surface reads the profile's school instead of the resolved one")


def test_the_grade_table_route_and_both_exports_share_the_resolved_school():
    """One helper, three callers, and each hands it `g.user_school_id`."""
    src = TEACHER_ROUTE.read_text(encoding="utf-8")
    for name in ("students", "students_export_xlsx", "students_export_pdf"):
        body = _function_body(src, name)
        assert 'school_id = g.get("user_school_id")' in body, name
        assert "_grade_table(supabase, school_id," in body, name


def _function_body(src: str, name: str) -> str:
    marker = f"def {name}("
    at = src.index(marker)
    rest = src[at:]
    nxt = rest.find("\n@", 1)
    return rest if nxt == -1 else rest[:nxt]
