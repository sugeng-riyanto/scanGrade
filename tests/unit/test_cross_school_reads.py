"""Reading your own paper is not a school's data being touched. Writing it still is.

A teacher who works at two schools writes papers in both, and a session is only ever in
one of them. `can_manage_exam` requires a paper's own school to be the school the session
is acting in, which is right for a *write* — the weighting, the pupils and the marks
follow the paper's school — and wrong for a report: a teacher who switched to their other
school could no longer print the LJK, open the results or read the analysis of a paper
**they had written**, while `/teacher/exams` went on offering all of it, because that list
is scoped by `teacher_id` alone. The offer and the rule disagreed, and the teacher whose
paper it was is the one who saw it.

So the guard now asks which question it is asking:

* `ACCESS_READ` — a report, a print, an export, a preview, an analysis — is answered for
  the paper's own author from any of their schools (`can_read_own_paper`);
* `ACCESS_MANAGE`, the default, is unchanged, and a write attempted from another of the
  author's schools is refused with **both schools named** (`EXAM_DENIED_ELSEWHERE`), since
  that refusal is the one that has a next step: move back to the paper's school.
"""
import ast
import re
from pathlib import Path

import pytest

from app.utils import denials, exam_access

ROOT = Path(__file__).resolve().parents[2]
TEACHER = ROOT / "app" / "routes" / "teacher.py"
LJK_URL = "/teacher/export/bubble-sheet/e-1"

PAPER = {
    "id": "e-1",
    "teacher_id": "tea-1",
    "school_id": "sch-A",
    "total_questions": 4,
    "question_types": {"0": "mcq"},
    "subject": "Fisika",
    "title": "My Physics",
    "status": "active",
    "is_published": True,
    "class_ids": [],
}

#: The one shape this door *edits*: the un-published draft the page itself minted. A
#: published paper is edited through `/teacher/exams/<id>` — the form's own action — and
#: `_owned_draft` deliberately refuses it here, so a save carrying its id mints a second
#: paper instead (measured: `.freebuff/probe_edit_save.py`). A test about this door's edit
#: path has to hand it a draft or it is testing the create path twice.
DRAFT = dict(PAPER, id="e-draft", status="draft", is_published=False)

SCHOOLS = [{"id": "sch-A", "name": "SMA Harapan Bangsa"},
           {"id": "sch-B", "name": "SMP Harapan Bangsa"}]

#: Every route that asks for a read, by function name. Pinned so that adding or removing
#: one is a deliberate act with a reason, the way a route list should be: each of these is
#: a report or a print of a paper, and each is GET-only (asserted below).
READ_ROUTES = {
    "bubble_sheet",            # the LJK
    "cheat_analysis",          # the pattern view (a 301 to the sessions page)
    "exam_preview",            # what a pupil will see
    "exam_preview_paper",      # the paper itself
    "exam_proctoring",         # the live room, bookmarked
    "exam_sessions",           # who is sitting
    "exam_sessions_data",      # the same, as JSON
    "export_pdf",              # results as a PDF
    "export_xlsx",             # results as a workbook
    "results",                 # the results page, which offers all of the above
    "results_print",           # the printed roster
    "results_report_cards",    # the class report cards
}

#: The one helper the analysis pages share: every caller of it is a report.
ANALYSIS_HELPER = "_analysis_of"


# ── the predicates ───────────────────────────────────────────────────────────

class TestThePredicates:
    def test_the_author_reads_from_another_of_their_schools(self):
        assert exam_access.can_read_own_paper("tea-1", "guru", "sch-B", PAPER) is True

    def test_the_author_still_cannot_write_from_another_school(self):
        assert exam_access.can_manage_exam("tea-1", "guru", "sch-B", PAPER) is False

    def test_a_colleague_is_refused_the_read_too(self):
        assert exam_access.can_read_own_paper("tea-9", "guru", "sch-B", PAPER) is False

    def test_the_schools_admin_reads_only_inside_their_own_school(self):
        assert exam_access.can_read_own_paper("adm", "admin_sekolah", "sch-A", PAPER) is True
        assert exam_access.can_read_own_paper("adm", "admin_sekolah", "sch-B", PAPER) is False

    def test_a_session_with_no_school_reads_nothing(self):
        assert exam_access.can_read_own_paper("tea-1", "guru", None, PAPER) is False
        assert exam_access.can_read_own_paper("tea-1", "guru", "", PAPER) is False

    def test_a_paper_with_no_school_is_still_its_authors_to_manage(self):
        """A row whose school was deleted or never recorded keeps its owner."""
        homeless = dict(PAPER, school_id=None)
        assert exam_access.can_manage_exam("tea-1", "guru", "sch-B", homeless) is True
        assert exam_access.can_read_own_paper("tea-1", "guru", "sch-B", homeless) is True


class TestTheVerdict:
    def _managed(self, access, user="tea-1", role="guru", school="sch-B", fake=None):
        fake = fake or _Fake()
        return exam_access.managed_exam(fake, "e-1", user, role, school,
                                        "id,teacher_id,school_id", access=access)

    def test_a_read_in_another_school_is_ok(self):
        exam, verdict = self._managed(exam_access.ACCESS_READ)
        assert verdict == exam_access.EXAM_OK and exam["id"] == "e-1"

    def test_a_write_in_another_school_has_its_own_verdict(self):
        """Not `EXAM_DENIED`: this refusal has a next step, and it needs the paper."""
        exam, verdict = self._managed(exam_access.ACCESS_MANAGE)
        assert verdict == exam_access.EXAM_DENIED_ELSEWHERE
        assert exam.get("school_id") == "sch-A", (
            "the refusal that names two schools was given the paper's row to name it with")

    def test_a_colleague_in_another_school_is_plain_denied(self):
        for access in (exam_access.ACCESS_READ, exam_access.ACCESS_MANAGE):
            exam, verdict = self._managed(access, user="tea-9")
            assert verdict == exam_access.EXAM_DENIED, access
            assert exam is None, "a plain denial must not hand the row back"


# ── fakes ────────────────────────────────────────────────────────────────────

class _Resp:
    def __init__(self, data=None):
        self.data = data


class _Query:
    def __init__(self, store, table):
        self.store, self.table = store, table
        self.filters, self.mode, self.payload = [], "select", None
        self.columns = "*"
        self.single = False

    def select(self, columns="*", **kwargs):
        self.columns = columns
        return self

    def eq(self, column, value):
        self.filters.append((column, value))
        return self

    def in_(self, column, values):
        self.filters.append((column, list(values)))
        return self

    def limit(self, *a, **k):
        return self

    def order(self, *a, **k):
        return self

    def insert(self, payload, *, returning="representation"):
        self.mode, self.payload = "insert", payload
        return self

    def update(self, payload, *, returning="representation"):
        self.mode, self.payload = "update", payload
        return self

    def maybe_single(self):
        self.single = True
        return self

    def _matches(self, row):
        for column, value in self.filters:
            if isinstance(value, list):
                if str(row.get(column)) not in [str(v) for v in value]:
                    return False
            elif str(row.get(column)) != str(value):
                return False
        return True

    def execute(self):
        # Scoped to the table when the caller named one: a lookup that fails on the
        # *late* read is a different answer from a lookup that fails on the paper.
        if self.store.raises is True or self.store.raises == self.table:
            raise RuntimeError("down")
        self.store.calls.append((self.table, self.mode, self.payload))
        if self.mode == "insert":
            # The row the database hands back, `id` and all: the create door names the
            # paper it just minted off this very field.
            fresh = dict(self.payload or {})
            fresh.setdefault("id", "new-1")
            return _Resp([fresh])
        if self.mode == "update":
            return _Resp([dict(self.payload or {})])
        rows = [row for row in self.store.rows.get(self.table, []) if self._matches(row)]
        if self.columns.strip() != "*":
            wanted = [c.strip() for c in self.columns.split(",")]
            rows = [{k: v for k, v in row.items() if k in wanted} for row in rows]
        if self.single:
            return _Resp(dict(rows[0]) if rows else None)
        return _Resp([dict(row) for row in rows])


class _Fake:
    def __init__(self, rows=None, raises=False):
        self.rows = rows if rows is not None else {"exams": [dict(PAPER), dict(DRAFT)],
                                                   "schools": [dict(s) for s in SCHOOLS]}
        self.raises = raises
        self.calls = []

    def table(self, name):
        return _Query(self, name)

    def writes(self):
        return [(table, mode, payload) for table, mode, payload in self.calls
                if mode in ("insert", "update")]


def _raw(view):
    while hasattr(view, "__wrapped__"):
        view = view.__wrapped__
    return view


def _answered(response):
    if isinstance(response, tuple):
        return response[0], response[1]
    return response, response.status_code


def _session(app, path, *, method="GET", user="tea-1", role="guru", school="sch-B",
             data=None, json_accept=False):
    """A request context carrying everything these routes read off `g`."""
    from flask import g

    headers = {"Accept": "application/json"} if json_accept else {}
    return app.test_request_context(path, method=method, data=data, headers=headers), g


# ── the route the report came from, from the author's *other* school ─────────

class TestTheLjkFromAnotherSchool:
    def _call(self, app, monkeypatch, *, user="tea-1", school="sch-B", fake=None):
        from app.routes import teacher

        fake = fake or _Fake()
        monkeypatch.setattr(teacher, "get_supabase", lambda: fake)
        ctx, g = _session(app, LJK_URL, user=user, school=school)
        with ctx:
            g.user_id = user
            g.user_role = "guru"
            g.user_school_id = school
            g.tz_offset = 7
            g.show = {}
            return _answered(_raw(teacher.bubble_sheet)("e-1"))

    def test_the_author_prints_their_own_sheet_from_another_school(self, app, monkeypatch):
        body, status = self._call(app, monkeypatch)
        assert status == 200, (
            "the paper's author was refused the LJK because the session was in another "
            "of their schools")
        assert body.mimetype == "application/pdf"

    def test_the_same_school_still_works(self, app, monkeypatch):
        _body, status = self._call(app, monkeypatch, school="sch-A")
        assert status == 200

    def test_a_colleague_in_another_school_gets_nothing(self, app, monkeypatch):
        body, status = self._call(app, monkeypatch, user="tea-9")
        assert status == 403
        assert body.get_json()["error"] == denials.NO_EXAM_ACCESS


# ── a write from another school says which school to move to ─────────────────

class TestTheWriteRefusal:
    def _toggle(self, app, monkeypatch, fake=None):
        from app.routes import teacher

        fake = fake or _Fake()
        monkeypatch.setattr(teacher, "get_supabase", lambda: fake)
        ctx, g = _session(app, "/teacher/exams/e-1/toggle-status", method="POST",
                          json_accept=True)
        with ctx:
            g.user_id = "tea-1"
            g.user_role = "guru"
            g.user_school_id = "sch-B"
            g.tz_offset = 7
            g.show = {}
            return _answered(_raw(teacher.toggle_exam_status)("e-1"))

    def test_it_names_both_schools(self, app, monkeypatch):
        body, status = self._toggle(app, monkeypatch)
        assert status == 403
        message = body.get_json()["error"]
        assert "SMA Harapan Bangsa" in message and "SMP Harapan Bangsa" in message, (
            f"the refusal does not say where to move to: {message}")
        assert "Pindah ke SMA Harapan Bangsa" in message

    def test_a_name_that_cannot_be_read_shortens_the_sentence(self, app, monkeypatch):
        """A refusal may not become a 500 because a lookup it did not need failed.

        The paper reads fine and only the *schools* lookup fails: this is a refusal
        already decided, whose sentence simply gets shorter. (A failure on the paper
        itself is the other answer — "access could not be verified" — and is asserted
        elsewhere.)
        """
        body, status = self._toggle(app, monkeypatch, fake=_Fake(raises="schools"))
        assert status == 403
        message = body.get_json()["error"]
        assert "SMA Harapan Bangsa" not in message
        assert message == denials.OTHER_SCHOOL_PAPER_UNNAMED

    def test_a_paper_that_is_not_theirs_is_still_the_plain_refusal(self, app, monkeypatch):
        from app.routes import teacher

        fake = _Fake()
        monkeypatch.setattr(teacher, "get_supabase", lambda: fake)
        ctx, g = _session(app, "/teacher/exams/e-1/toggle-status", method="POST",
                          user="tea-9", json_accept=True)
        with ctx:
            g.user_id = "tea-9"
            g.user_role = "guru"
            g.user_school_id = "sch-B"
            g.tz_offset = 7
            g.show = {}
            body, status = _answered(_raw(teacher.toggle_exam_status)("e-1"))
        assert status == 403
        assert body.get_json()["error"] == denials.NO_EXAM_ACCESS


# ── the split, as source ─────────────────────────────────────────────────────

def _routes():
    """`(path, methods, function name, asks_for_a_read)` for every teacher route."""
    src = TEACHER.read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        for decorator in node.decorator_list:
            call = decorator if isinstance(decorator, ast.Call) else None
            if not (call and isinstance(call.func, ast.Attribute)
                    and call.func.attr == "route"):
                continue
            path = ast.literal_eval(call.args[0]) if call.args else ""
            methods = ["GET"]
            for keyword in call.keywords:
                if keyword.arg == "methods":
                    methods = [element.value for element in keyword.value.elts]
            body = ast.get_source_segment(src, node) or ""
            yield path, methods, node.name, "access=ACCESS_READ" in body


class TestTheSplitIsDeclared:
    def test_the_default_is_manage(self):
        """A route that forgets the argument stays strict — the safe direction."""
        body = TEACHER.read_text(encoding="utf-8")
        guard = body.split("def _guard_exam(")[1].split("\ndef ")[0]
        assert "access=ACCESS_MANAGE" in guard.split("):", 1)[0], (
            "_guard_exam no longer defaults to the write question")

    def test_every_route_that_asks_for_a_read_is_get_only(self):
        for path, methods, name, asks_for_read in _routes():
            if asks_for_read:
                assert methods == ["GET"], (
                    f"{name} ({path}) asks for the read question but accepts {methods}: "
                    "a read may not be reachable by a write")

    def test_the_read_routes_are_exactly_these(self):
        found = {name for _path, _methods, name, asks in _routes() if asks}
        assert found == READ_ROUTES, (
            f"the read set moved: added {sorted(found - READ_ROUTES)}, "
            f"removed {sorted(READ_ROUTES - found)}")

    def test_every_analysis_caller_is_a_get_route(self):
        """`_analysis_of` asks for a read; whoever calls it must be a report."""
        src = TEACHER.read_text(encoding="utf-8")
        tree = ast.parse(src)
        callers = set()
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            for inner in ast.walk(node):
                if (isinstance(inner, ast.Call) and isinstance(inner.func, ast.Name)
                        and inner.func.id == ANALYSIS_HELPER):
                    callers.add(node.name)
        assert callers, f"nothing calls {ANALYSIS_HELPER} any more"
        routes = {name: methods for _path, methods, name, _r in _routes()}
        for name in callers:
            assert routes.get(name) == ["GET"], (
                f"{name} calls {ANALYSIS_HELPER} but is not a GET route")
        helper = src.split("def " + ANALYSIS_HELPER + "(")[1].split("\ndef ")[0]
        assert "access=ACCESS_READ" in helper, (
            f"{ANALYSIS_HELPER} does not ask for a read, so analysis stays closed")


# ── a save never moves a paper ───────────────────────────────────────────────

class TestASaveNeverMovesAPaper:
    """`/teacher/exams/new` wrote the *acting* school on every save.

    `super_admin` is the caller that reaches it from another school (`can_manage_exam`
    allows them anywhere), and before this the paper's own school was overwritten with the
    session's — taking its weighting, its pupils and its marks with it.
    """

    def _post(self, app, monkeypatch, *, draft_id, session_school="sch-A",
              form_school="sch-A"):
        """A save the door can actually reach, with the form naming another school.

        `session_school` is the paper's own (`_owned_draft` finds a draft only there —
        a cross-school POST is ignored and a fresh paper is minted, which is a
        different door). `form_school` defaults to it, because the field is what lands
        in `school_id` today and the authority refuses a name that disagrees.
        """
        from app.routes import teacher

        fake = _Fake()
        monkeypatch.setattr(teacher, "get_supabase", lambda: fake)
        data = {"title": "UTS", "subject": "MTK", "total_questions": "4",
                "start_at": "2026-10-20T09:00", "end_at": "2026-10-20T11:00",
                "action": "save_draft"}
        if form_school:
            data["school_id"] = form_school
        if draft_id:
            data["draft_id"] = draft_id
        ctx, g = _session(app, "/teacher/exams/new", method="POST", data=data,
                          role="super_admin", school=session_school)
        with ctx:
            g.user_id = "tea-1"
            g.user_role = "super_admin"
            g.user_school_id = session_school
            g.tz_offset = 7
            g.show = {}
            _answered(_raw(teacher.exam_form)())
        return fake

    def test_an_edit_writes_the_papers_own_school(self, app, monkeypatch):
        fake = self._post(app, monkeypatch, draft_id=DRAFT["id"],
                          form_school=PAPER["school_id"])
        updates = [payload for table, mode, payload in fake.writes()
                   if table == "exams" and mode == "update"]
        assert updates, "the draft was never written — the save minted a duplicate"
        assert updates[0].get("school_id", PAPER["school_id"]) == PAPER["school_id"], (
            "the save restated the paper's school, re-filing it under the school the "
            "request named instead of the school that owns it")
        assert updates[0]["teacher_id"] == "tea-1"

    def test_a_new_paper_is_filed_under_the_school_this_request_is_verified_in(
            self, app, monkeypatch):
        fake = self._post(app, monkeypatch, draft_id=None)
        inserts = [payload for table, mode, payload in fake.writes()
                   if table == "exams" and mode == "insert"]
        assert inserts, "the new paper was never written"
        assert inserts[0]["school_id"] == "sch-A", (
            "a create stopped filing under the school the request is verified in")

    def test_a_form_that_names_another_school_writes_nothing(self, app, monkeypatch):
        """The re-filing route, closed by the filing authority, not by a pop.

        This is the shape a `school_id` in the *form* would have to take to move a
        paper, and the answer is a refusal: the school a row is filed under is the one
        the request is verified in, and a name that disagrees is not an authority.
        """
        fake = self._post(app, monkeypatch, draft_id="e-1", form_school="sch-D")
        assert fake.writes() == [], (
            "a save honoured a form field naming a school the session is not in")
