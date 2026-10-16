"""What the guard reads, and the route that asked it for something narrower.

`/teacher/export/bubble-sheet/<exam_id>` prints the LJK — the blank bubble sheet a
teacher photocopies. It asked ``_guard_exam`` for
``total_questions,question_types,subject`` and nothing else, and the *rule*
(``can_manage_exam``) is made of two columns that were therefore absent:
``teacher_id`` and ``school_id``. To PostgREST a column left out of a select is not an
error, it is an absent key — so the owner branch compared ``"" != "<user id>"`` and the
admin branch compared ``"" != "<school id>"``, and the route refused every caller except
a ``super_admin``. The whole report was one line of JSON, with nothing in any log:

    {"error":"Ujian ini tidak tersedia untuk akun Anda. …"}

``managed_exam`` now adds the rule's own fields to whatever the caller asks for
(``_select_with_rule_columns``), which is what makes a narrow ``columns`` safe instead of
a silent refusal. Both halves are pinned here: the union, and the route driven for the
three callers that decide it.
"""
import re
from pathlib import Path

import pytest

from app.utils import denials, exam_access

ROOT = Path(__file__).resolve().parents[2]
TEACHER = ROOT / "app" / "routes" / "teacher.py"
EXAM_ACCESS = ROOT / "app" / "utils" / "exam_access.py"
URL = "/teacher/export/bubble-sheet/e-1"

#: The paper the report is about, with every column any of these callers may read.
EXAM = {
    "id": "e-1",
    "teacher_id": "tea-1",
    "school_id": "sch-1",
    "total_questions": 4,
    "question_types": {"0": "mcq"},
    "subject": "Fisika",
    "title": "My Physics",
    "status": "active",
}


class _Resp:
    def __init__(self, data=None):
        self.data = data


class _Query:
    """PostgREST-shaped: the select list decides which keys the row comes back with.

    That is the whole point of the fake. One that answered every select with the full
    row would let the defect pass — the bug was never the *row*, it was the columns the
    guard was handed.
    """

    def __init__(self, store):
        self.store = store
        self.columns = "*"
        self.exam_id = None

    def select(self, columns="*"):
        self.columns = columns
        self.store.selects.append(columns)
        return self

    def eq(self, column, value):
        if column == "id":
            self.exam_id = value
        return self

    def maybe_single(self):
        return self

    def execute(self):
        if self.store.raises:
            raise RuntimeError("no connection")
        row = self.store.row
        if not row or str(row.get("id")) != str(self.exam_id):
            return _Resp(None)
        if self.columns.strip() == "*":
            return _Resp(dict(row))
        wanted = [column.strip() for column in self.columns.split(",")]
        return _Resp({key: value for key, value in row.items() if key in wanted})


class _Fake:
    def __init__(self, row=None, raises=False):
        self.row = row if row is not None else dict(EXAM)
        self.raises = raises
        self.selects = []

    def table(self, name):
        self.name = name
        return _Query(self)


def _managed(columns="id,teacher_id,school_id", user="tea-1", role="guru",
             school="sch-1", fake=None):
    fake = fake or _Fake()
    exam, verdict = exam_access.managed_exam(fake, "e-1", user, role, school, columns)
    return exam, verdict, fake


# ── the union ────────────────────────────────────────────────────────────────

class TestTheRuleColumnsAreAlwaysFetched:
    @pytest.mark.parametrize("asked", [
        "total_questions,question_types,subject",
        "title",
        "id",
        "id,teacher_id,school_id",          # already complete: nothing is duplicated
    ])
    def test_the_rule_columns_are_added_to_what_the_caller_asked_for(self, asked):
        selected = exam_access._select_with_rule_columns(asked)
        columns = [column.strip() for column in selected.split(",")]
        assert len(columns) == len(set(columns)), f"a column is repeated: {selected}"
        for column in exam_access.RULE_COLUMNS:
            assert column in columns, f"{column} is missing from {selected}"
        for column in asked.split(","):
            assert column in columns, f"the caller lost {column}: {selected}"

    def test_a_star_stays_a_star(self):
        assert exam_access._select_with_rule_columns("*") == "*"

    @pytest.mark.parametrize("nothing", [None, "", "  "])
    def test_a_caller_that_asks_for_no_columns_still_gets_the_rules(self, nothing):
        assert exam_access._select_with_rule_columns(nothing) == \
            ",".join(exam_access.RULE_COLUMNS)

    def test_the_owner_passes_on_the_column_list_the_reported_route_used(self):
        """The route that failed asked for three columns and got refused for two."""
        exam, verdict, fake = _managed(
            columns="total_questions,question_types,subject")
        assert verdict == exam_access.EXAM_OK, (
            "the owner was refused because the rule's own columns were not fetched")
        assert exam["total_questions"] == 4, "the route lost a column it prints"
        assert exam["teacher_id"] == "tea-1" and exam["school_id"] == "sch-1"

    def test_the_schools_admin_passes_on_the_same_narrow_list(self):
        _exam, verdict, _fake = _managed(
            columns="total_questions,question_types,subject",
            user="adm-2", role="admin_sekolah")
        assert verdict == exam_access.EXAM_OK

    def test_a_colleague_is_still_refused(self):
        _exam, verdict, _fake = _managed(
            columns="total_questions,question_types,subject", user="tea-9")
        assert verdict == exam_access.EXAM_DENIED

    def test_the_database_is_asked_for_the_rule_columns(self):
        _exam, _verdict, fake = _managed(columns="subject")
        assert "teacher_id" in fake.selects[-1] and "school_id" in fake.selects[-1], (
            f"the query never asked for them: {fake.selects[-1]}")

    def test_the_other_two_answers_are_unchanged(self):
        missing, verdict_missing, _ = _managed(fake=_Fake(row={"id": "other"}))
        assert missing is None and verdict_missing == exam_access.EXAM_MISSING
        unreadable, verdict_unreadable, _ = _managed(fake=_Fake(raises=True))
        assert unreadable is None and verdict_unreadable == exam_access.EXAM_UNVERIFIABLE


class TestTheUnionCannotBeBypassed:
    def test_the_reader_goes_through_the_collection(self):
        src = EXAM_ACCESS.read_text(encoding="utf-8")
        assert "select(_select_with_rule_columns(columns))" in src, (
            "managed_exam selects the caller's columns directly again")

    def test_the_guard_builds_no_query_of_its_own(self):
        """One reader: a route cannot hand the rule a row of its own choosing."""
        body = TEACHER.read_text(encoding="utf-8")
        guard = body.split("def _guard_exam(")[1].split("\ndef ")[0]
        assert "managed_exam(" in guard
        assert 'table("exams")' not in guard, (
            "_guard_exam queries exams itself, so a caller's columns decide the rule")


# ── the route the report came from, driven ───────────────────────────────────

def _raw(view):
    while hasattr(view, "__wrapped__"):
        view = view.__wrapped__
    return view


def _answered(response):
    """`(body, status)` for both shapes a view returns here.

    A successful export hands back a Response; `_deny` hands back `(jsonify(...), 403)`
    and the route returns it unchanged — the tuple Flask unpacks for a real request.
    """
    if isinstance(response, tuple):
        return response[0], response[1]
    return response, response.status_code


class TestTheBubbleSheetRoute:
    def _call(self, app, monkeypatch, *, user="tea-1", role="guru", school="sch-1"):
        from flask import g

        from app.routes import teacher

        fake = _Fake()
        monkeypatch.setattr(teacher, "get_supabase", lambda: fake)
        with app.test_request_context(URL):
            g.user_id = user
            g.user_role = role
            g.user_school_id = school
            g.tz_offset = 7
            g.show = {}
            response = _raw(teacher.bubble_sheet)("e-1")
        return response, fake

    def test_the_papers_own_teacher_gets_the_sheet(self, app, monkeypatch):
        body, status = _answered(self._call(app, monkeypatch)[0])
        assert status == 200, (
            "the teacher who wrote the paper cannot print its answer sheet")
        assert body.mimetype == "application/pdf"
        assert "LJK_" in body.headers.get("Content-Disposition", "")

    def test_the_schools_admin_gets_the_sheet(self, app, monkeypatch):
        _body, status = _answered(
            self._call(app, monkeypatch, user="adm-2", role="admin_sekolah")[0])
        assert status == 200

    def test_another_teacher_gets_the_sentence_and_nothing_else(self, app, monkeypatch):
        body, status = _answered(self._call(app, monkeypatch, user="tea-9")[0])
        assert status == 403
        assert body.mimetype != "application/pdf"
        assert body.get_json()["error"] == denials.NO_EXAM_ACCESS

    def test_the_route_still_asks_the_guard_for_what_it_prints(self):
        """The narrow list is the point — the rule's fields come from one place."""
        body = TEACHER.read_text(encoding="utf-8")
        route = body.split("def bubble_sheet(")[1].split("\ndef ")[0]
        columns = re.search(r'columns="([^"]*)"', route)
        assert columns, "the route no longer names its columns"
        assert "total_questions" in columns.group(1)
        assert "teacher_id" not in columns.group(1), (
            "the route is compensating for the guard instead of relying on it")
