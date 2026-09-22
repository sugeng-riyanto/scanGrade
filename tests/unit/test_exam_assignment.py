"""An exam reaches the classes the teacher assigned it to, and nobody else.

The rule now lives in ``exam_access.class_assignment_allows`` and both halves of
the app use it: the lists that *offer* an exam and the guard that *opens* it.

Two ways that can break, both measured against the real database before this test
existed:

1. **The list offered what the door refused.** ``/student/exams`` filtered its
   rows on ``class_ids`` and read the column off a ``select(...)`` that never asked
   for it, so every exam looked unassigned. A pupil in X-B was shown the papers
   for X-A and XI-A — three of them — and every one answered
   "Ujian ini tidak ditugaskan untuk kelas Anda." one click later.
2. **An exam assigned to no class reached the whole school.** That was the
   documented reading of an empty ``class_ids`` ("open to the whole school"), so
   the one exam a teacher forgot to assign sat in every class's list.

The route tests stub ``render_template``: they are about which rows the route
decides to hand the page, not about markup. ``test_exam_integrity`` covers the
guard's own truth table.

The fake postgrest applies ``eq``/``in_`` for real, because the school filter is
half of what these tests are about — a fake that ignores it would report the
other school's exam as a leak the app does not have.
"""
import inspect
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.routes import student as studentmod
from app.routes import teacher as teachermod
from app.utils.exam_access import exam_sitting_allowed

SCHOOL = "school-A"
CLASS_A, CLASS_B = "class-X-A", "class-X-B"
PUPIL_A, PUPIL_B = "stu-a", "stu-b"
TEACHER = "teacher-1"

EXAMS = [
    {"id": "exam-a", "title": "Untuk X-A", "school_id": SCHOOL, "class_ids": [CLASS_A],
     "teacher_id": TEACHER,
     "is_published": True, "status": "active", "question_types": {}, "total_questions": 0,
     "subject": "Fisika", "start_at": None},
    {"id": "exam-b", "title": "Untuk X-B", "school_id": SCHOOL,
     "class_ids": json.dumps([CLASS_B]),          # jsonb written with json.dumps
     "is_published": True, "status": "active", "question_types": {}, "total_questions": 0,
     "subject": "Fisika", "start_at": None},
    {"id": "exam-none", "title": "Tanpa kelas", "school_id": SCHOOL, "class_ids": [],
     "teacher_id": TEACHER,
     "is_published": True, "status": "active", "question_types": {}, "total_questions": 0,
     "subject": "Fisika", "start_at": None},
    {"id": "exam-other-school", "title": "Sekolah lain", "school_id": "school-B",
     "class_ids": [CLASS_A], "is_published": True, "status": "active",
     "question_types": {}, "total_questions": 0, "subject": "Fisika", "start_at": None},
]

PROFILES = [
    {"id": PUPIL_A, "full_name": "Murid X-A", "class_id": CLASS_A, "school_id": SCHOOL,
     "role": "murid", "status": "active"},
    {"id": PUPIL_B, "full_name": "Murid X-B", "class_id": CLASS_B, "school_id": SCHOOL,
     "role": "murid", "status": "active"},
    {"id": "stu-none", "full_name": "Murid tanpa kelas", "class_id": None,
     "school_id": SCHOOL, "role": "murid", "status": "active"},
]


class FakeQuery:
    """Chainable postgrest stand-in: filters rows, and records every select."""

    def __init__(self, rows, log):
        self._rows, self._log = rows, log
        self._filters = []
        self._one = False

    def select(self, *columns, **kwargs):
        self._log.append(columns[0] if columns else "")
        return self

    def eq(self, column, value):
        self._filters.append((column, value))
        return self

    def in_(self, column, values):
        self._filters.append((column, list(values)))
        return self

    def neq(self, *a, **k):
        return self

    def order(self, *a, **k):
        return self

    def limit(self, *a, **k):
        return self

    def single(self):
        self._one = True
        return self

    def maybe_single(self):
        # postgrest answers `maybe_single()` with a row (or None), not a list:
        # `row_or_none()` and every guard that reads `.get()` depend on that.
        self._one = True
        return self

    def execute(self):
        found = []
        for row in self._rows:
            if all(row.get(col) in want if isinstance(want, list) else row.get(col) == want
                   for col, want in self._filters):
                found.append(dict(row))
        if self._one:
            return SimpleNamespace(data=found[0] if found else None)
        return SimpleNamespace(data=found)


class FakeSupabase:
    def __init__(self, tables):
        self._tables = tables
        self.selects = {}

    def table(self, name):
        log = self.selects.setdefault(name, [])
        return FakeQuery(self._tables.get(name, []), log)


def _base_tables():
    return {
        "exams": EXAMS,
        "profiles": PROFILES,
        "submissions": [],
        "classes": [{"id": CLASS_A, "name": "X-A", "school_id": SCHOOL},
                    {"id": CLASS_B, "name": "X-B", "school_id": SCHOOL}],
    }


def _render(app, monkeypatch, route_func, pupil, supa=None, path="/student/exams"):
    """Run a student route with the pupil's session, capturing the template context."""
    from flask import g

    captured = {}

    def _capture(name, **kw):
        captured["template"], captured["ctx"] = name, kw
        return ""

    monkeypatch.setattr(studentmod, "render_template", _capture)
    supa = supa if supa is not None else FakeSupabase(_base_tables())
    app.extensions["supabase"] = supa

    with app.test_request_context(path):
        g.user_id = pupil["id"]
        g.user_role = "murid"
        g.user_name = pupil["full_name"]
        g.user_email = ""
        g.user_school_id = pupil["school_id"]
        g.user_class_id = pupil["class_id"]
        g.user_status = "active"
        route_func.__wrapped__()

    return captured["ctx"]


def _titles(exams):
    return sorted(e["title"] for e in exams)


# ── the exam list ────────────────────────────────────────────────

class TestTheExamList:
    def test_the_query_asks_for_class_ids(self, app, monkeypatch):
        """The defect, pinned at its source: the column has to be selected.

        Reading `class_ids` off a row that was never asked for it makes every
        exam look unassigned, and `not cids` then matches every exam in the
        school. A filter is only as good as the column it reads.
        """
        supa = FakeSupabase(_base_tables())
        _render(app, monkeypatch, studentmod.exam_list, PROFILES[0], supa=supa)

        exam_selects = [c for c in supa.selects.get("exams", []) if c]
        assert exam_selects, "the route never read the exams table"
        assert any("class_ids" in cols for cols in exam_selects), (
            "the exam list reads class_ids off a column list that does not include it"
        )

    def test_a_pupil_sees_their_own_class_exam_only(self, app, monkeypatch):
        ctx = _render(app, monkeypatch, studentmod.exam_list, PROFILES[0])
        assert _titles(ctx["exams"]) == ["Untuk X-A"]

    def test_another_class_exam_is_not_offered(self, app, monkeypatch):
        ctx = _render(app, monkeypatch, studentmod.exam_list, PROFILES[1])
        assert _titles(ctx["exams"]) == ["Untuk X-B"]

    def test_an_unassigned_exam_is_offered_to_no_class(self, app, monkeypatch):
        """`class_ids == []` means nobody, not the whole school."""
        for pupil in PROFILES[:2]:
            ctx = _render(app, monkeypatch, studentmod.exam_list, pupil)
            assert "Tanpa kelas" not in _titles(ctx["exams"]), pupil["full_name"]

    def test_a_pupil_with_no_class_on_file_sees_nothing(self, app, monkeypatch):
        """Never a wildcard: no class is not every class."""
        ctx = _render(app, monkeypatch, studentmod.exam_list, PROFILES[2])
        assert ctx["exams"] == []

    def test_another_school_never_appears(self, app, monkeypatch):
        for pupil in PROFILES:
            ctx = _render(app, monkeypatch, studentmod.exam_list, pupil)
            assert "Sekolah lain" not in _titles(ctx["exams"])

    def test_the_list_offers_nothing_the_guard_refuses(self, app, monkeypatch):
        """The contract between the two halves, checked as a relation.

        A list that is merely *stricter* is fine; a list that offers an exam the
        guard then refuses is the dead end pupils reported. This asserts the
        second cannot happen, whatever the rule becomes.
        """
        supa = FakeSupabase(_base_tables())
        for pupil in PROFILES:
            ctx = _render(app, monkeypatch, studentmod.exam_list, pupil, supa=supa)
            for exam in ctx["exams"]:
                allowed, reason = exam_sitting_allowed(
                    supa, exam, exam["id"], pupil["id"])
                assert allowed, (pupil["full_name"], exam["title"], reason)

    def test_json_string_class_ids_still_match(self, app, monkeypatch):
        """`exam-b` stores its classes as a JSON string; X-B must still see it."""
        ctx = _render(app, monkeypatch, studentmod.exam_list, PROFILES[1])
        assert "Untuk X-B" in _titles(ctx["exams"])


# ── the dashboard, which filters the same rows its own way ───────

class TestTheStudentDashboard:
    def test_the_dashboard_shows_only_the_own_class_exam(self, app, monkeypatch):
        ctx = _render(app, monkeypatch, studentmod.dashboard, PROFILES[0],
                      path="/student/dashboard")
        assert _titles(ctx["available_exams"]) == ["Untuk X-A"]

    def test_the_dashboard_hides_the_unassigned_exam(self, app, monkeypatch):
        for pupil in PROFILES[:2]:
            ctx = _render(app, monkeypatch, studentmod.dashboard, pupil,
                          path="/student/dashboard")
            assert "Tanpa kelas" not in _titles(ctx["available_exams"])

    def test_the_student_pages_select_the_column_they_filter_on(self):
        """The same trap as the list, pinned statically for both pages.

        `class_ids` has to be in the column list of every query whose result is
        filtered by it. Reading it off a row that never carried it is silent:
        `.get()` returns None, and None means "unassigned" — which is how a
        filter becomes a no-op.
        """
        for func in (studentmod.dashboard, studentmod.exam_list):
            queries = [line for line in inspect.getsource(func).splitlines()
                       if 'table("exams").select(' in line]
            assert queries, f"{func.__name__} reads exams"
            for line in queries:
                assert "class_ids" in line, line.strip()


# ── the teacher has to be able to see the void ───────────────────

class TestTheTeacherWarning:
    """An exam that reaches no pupil must not be silent.

    Once assignment is required, forgetting to tick a class means the paper is
    invisible — the teacher sees it in their own list and the class sees nothing.
    That is a worse failure than the leak it replaces unless the teacher is told.
    """

    def test_a_live_unassigned_exam_is_flagged(self):
        assert teachermod._needs_class_assignment(
            {"is_published": True, "status": "active", "class_ids": []}) is True

    def test_a_json_string_empty_list_is_flagged_too(self):
        """`"[]"` is a truthy string — the reason this is computed in Python."""
        assert teachermod._needs_class_assignment(
            {"is_published": True, "status": "active", "class_ids": "[]"}) is True

    def test_an_assigned_exam_is_not_flagged(self):
        assert teachermod._needs_class_assignment(
            {"is_published": True, "status": "active", "class_ids": [CLASS_A]}) is False
        assert teachermod._needs_class_assignment(
            {"is_published": True, "status": "active",
             "class_ids": json.dumps([CLASS_A])}) is False

    def test_a_draft_is_not_news(self):
        """A draft nobody can reach is expected; only a live exam is a surprise."""
        assert teachermod._needs_class_assignment(
            {"is_published": False, "status": "draft", "class_ids": []}) is False

    def test_the_teacher_dashboard_selects_the_column_it_warns_about(self):
        """The lesson `answer_key` already taught: a warning computed from a
        column the route never selected is a warning that can never be cleared."""
        dashboard = inspect.getsource(teachermod.dashboard)
        select = [line for line in dashboard.splitlines() if 'table("exams").select(' in line]
        assert select, "the teacher dashboard reads exams"
        assert "class_ids" in select[0], "the dashboard does not select the column it warns about"

    def test_the_dashboard_hands_the_warning_to_the_page(self, app, monkeypatch):
        """Run the route and read the context it renders with.

        Asserting the source contains the name is not enough: the list can be
        computed and then never passed, and the card silently disappears.
        """
        from flask import g

        captured = {}
        monkeypatch.setattr(teachermod, "render_template",
                            lambda name, **kw: captured.update(kw) or "")
        app.extensions["supabase"] = FakeSupabase({
            **_base_tables(), "profiles": [], "submissions": [],
        })

        with app.test_request_context("/teacher/dashboard"):
            g.user_id = TEACHER
            g.user_role = "guru"
            g.user_name = "Guru Uji"
            g.user_email = "guru@example.test"
            g.user_school_id = SCHOOL
            g.user_class_id = None
            g.user_status = "active"
            g.tz_offset = 0
            teachermod.dashboard.__wrapped__()

        assert "exams_unassigned" in captured, "the template has no list to warn about"
        assert [e["title"] for e in captured["exams_unassigned"]] == ["Tanpa kelas"]

    def test_the_exam_card_badges_only_the_unassigned_exam(self, app):
        """Render the card list twice: the badge is the page's only signal that a
        live exam reaches nobody, so it has to appear on exactly those cards."""
        from flask import g

        with app.test_request_context("/teacher/exams"):
            g.user_id = TEACHER
            g.user_role = "guru"
            g.user_name = "Guru Uji"
            g.user_email = "guru@example.test"
            g.user_school_id = SCHOOL
            g.user_class_id = None
            g.user_status = "active"
            g.tz_offset = 0
            template = app.jinja_env.get_template("teacher/exams.html")
            flagged = template.render(exams=EXAMS, unassigned_ids={"exam-none"})
            unflagged = template.render(exams=EXAMS, unassigned_ids=set())

        marker = "Tanpa kelas','No class"
        assert flagged.count(marker) == 1, "the unassigned exam is not badged"
        assert unflagged.count(marker) == 0, "an assigned exam was badged"

    def test_the_dashboard_retries_the_two_reads_that_500d(self):
        """Measured on the running server: `/teacher/dashboard` answered its error
        page on 2 of 5 loads because the exams read let
        `httpx.RemoteProtocolError: Server disconnected` out of the view.

        A dropped keep-alive is what `read_with_retry` is for, and this is the
        page a teacher opens first — so the two reads that threw go through it.
        """
        dashboard = inspect.getsource(teachermod.dashboard)
        assert 'read_with_retry(lambda: supabase.table("exams")' in dashboard
        assert 'read_with_retry(lambda: supabase.table("submissions")' in dashboard

    def test_the_teacher_copy_is_bilingual(self):
        """Both cards are new copy, so both must answer the EN/ID toggle."""
        dashboard = Path("app/templates/teacher/dashboard.html").read_text(encoding="utf-8")
        cards = Path("app/templates/teacher/exams.html").read_text(encoding="utf-8")

        assert "exams_unassigned" in dashboard
        assert "x-text=\"t('Ujian tanpa kelas','Exam with no class')\"" in dashboard
        assert "x-text=\"t('Tanpa kelas','No class')\"" in cards
