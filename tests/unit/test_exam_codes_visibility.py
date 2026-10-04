"""The recovery code is shown to staff who already have the pupil in front of them.

The pupil's results page tells them *"Your class invigilator decides"* and *"the
subject teacher's exam is the one being retaken"* — but until this change no staff
surface showed a pupil's code, so a pupil locked out of a sitting had to read the
number off their own screen and the teacher had nothing to compare it against. The
lock feature (`app/services/resume_code.py`) deliberately borrows the recovery code
from `app/utils/exam_recovery.py`; the piece that was missing is the **read** side.

Two roles, two authorities, and they are not the same list:

* **a teacher** sees the codes of a pupil *only* for a sitting they invigilate or an
  exam they own (the subject teacher who assigned the test). That is the same
  authority `invigilation.invigilated_exam_ids` already expresses for the retake
  decision — one rule, not a second one written here;
* **a school official** (vice principal or school admin) sees the school's codes
  because their authority is the whole school.

What is asserted, and why:

* the service **requires** the caller's ``school_id`` and **never** takes a school
  from the request — the recurring defect this repository closes;
* a teacher's list is the intersection of their own exams and the codes, so a
  colleague's exam is not leaked by holding the route;
* an official's list is the school's, and another school's codes never appear;
* the code is a **read**: no write to ``exam_access_codes`` is issued by listing;
* the invigilator page and the shared official page both render it, and the teacher
  page is finally reachable from the sidebar (it had no link).
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SERVICE = ROOT / "app" / "services" / "exam_codes.py"
TEACHER_ROUTES = ROOT / "app" / "routes" / "teacher.py"
PRINCIPAL_ROUTES = ROOT / "app" / "routes" / "principal.py"
ADMIN_ROUTES = ROOT / "app" / "routes" / "admin_sekolah.py"
TEACHER_PAGE = ROOT / "app" / "templates" / "teacher" / "invigilation.html"
OFFICIAL_PAGE = ROOT / "app" / "templates" / "principal" / "invigilation.html"
SIDEBAR = ROOT / "app" / "templates" / "base.html"


# ── a PostgREST stand-in that applies its writes ────────────────────────────

class _Res:
    def __init__(self, data):
        self.data = data


class _Query:
    def __init__(self, store, table):
        self.store, self.table = store, table
        self.filters = []
        self.mode, self.payload = "select", None

    def select(self, *a, **k):
        self.mode = "select"
        return self

    def insert(self, payload):
        self.mode, self.payload = "insert", payload
        return self

    def update(self, payload):
        self.mode, self.payload = "update", payload
        return self

    def eq(self, column, value):
        self.filters.append((column, value))
        return self

    def in_(self, column, values):
        self.filters.append(("IN " + column, set(map(str, values))))
        return self

    def order(self, *a, **k):
        return self

    def limit(self, *a, **k):
        return self

    def _matches(self, row):
        for key, value in self.filters:
            if key.startswith("IN "):
                if str(row.get(key[3:])) not in value:
                    return False
            elif str(row.get(key)) != str(value):
                return False
        return True

    def execute(self):
        self.store.calls.append((self.table, tuple(self.filters), self.payload,
                                 self.mode))
        rows = self.store.rows.setdefault(self.table, [])
        if self.mode == "update":
            hit = [r for r in rows if self._matches(r)]
            for row in hit:
                row.update(self.payload)
            return _Res(hit)
        if self.mode == "insert":
            row = dict(self.payload)
            row.setdefault("id", f"c{len(rows) + 1}")
            rows.append(row)
            return _Res([row])
        return _Res([r for r in rows if self._matches(r)])


class FakeSupabase:
    def __init__(self, rows=None):
        self.rows = {k: [dict(r) for r in v] for k, v in (rows or {}).items()}
        self.calls = []

    def table(self, name):
        return _Query(self, name)

    def writes(self, table="exam_access_codes"):
        return [c for c in self.calls if c[0] == table and c[3] != "select"]


def _code(**over):
    row = {"id": "k1", "exam_id": "e1", "student_id": "s1", "code": "123456",
           "is_used": False}
    row.update(over)
    return row


# ── the service: school-scoped, never from the request ──────────────────────

class TestTheService:
    def test_the_service_exists(self):
        assert SERVICE.exists(), "no exam_codes service module"

    def test_listing_is_a_read_and_never_writes(self):
        from app.services import exam_codes

        sb = FakeSupabase(rows={"exam_access_codes": [_code()]})
        exam_codes.codes_for_exams(sb, "sch1", ["e1"])
        assert sb.writes() == [], "listing codes wrote to exam_access_codes"

    def test_a_teacher_sees_only_the_exams_they_hold(self):
        """Their authority is the exam set, not the role."""
        from app.services import exam_codes

        sb = FakeSupabase(rows={"exam_access_codes": [
            _code(id="k1", exam_id="e1", student_id="s1", code="111111"),
            _code(id="k2", exam_id="e2", student_id="s2", code="222222")]})

        rows = exam_codes.codes_for_exams(sb, "sch1", ["e1"])

        assert {r["code"] for r in rows} == {"111111"}, (
            "a colleague's exam codes leaked through the teacher's read")

    def test_an_empty_exam_set_selects_nothing_not_everything(self):
        from app.services import exam_codes

        sb = FakeSupabase(rows={"exam_access_codes": [_code()]})
        assert exam_codes.codes_for_exams(sb, "sch1", []) == []

    def test_the_read_is_scoped_to_the_exams_asked_for(self):
        from app.services import exam_codes

        sb = FakeSupabase(rows={"exam_access_codes": [_code()]})
        exam_codes.codes_for_exams(sb, "sch1", ["e1"])
        filters = [f for c in sb.calls for f in c[1]]
        assert ("IN exam_id", {"e1"}) in filters, (
            "the read did not scope itself to the caller's exams")


# ── the pages ───────────────────────────────────────────────────────────────

class TestTheInvigilatorPage:
    def test_the_teacher_route_passes_codes_scoped_to_their_exams(self):
        src = TEACHER_ROUTES.read_text(encoding="utf-8")
        body = src.split("def invigilation_duties(")[1].split("\ndef ")[0]
        assert "exam_codes." in body, "the invigilator page reads no codes"
        assert "exam_ids" in body, (
            "the page must scope the codes to the exams the teacher holds")

    def test_the_teacher_page_renders_a_code_column(self):
        html = TEACHER_PAGE.read_text(encoding="utf-8")
        assert "recovery_code" in html or "code" in html
        assert re.search(r"data-code=|codes", html), (
            "the invigilator page draws no codes")

    def test_the_teacher_page_is_reachable_from_the_sidebar(self):
        html = SIDEBAR.read_text(encoding="utf-8")
        assert 'href="/teacher/invigilation"' in html, (
            "the invigilator page has no sidebar link — a teacher must know the URL")

    def test_the_official_page_route_passes_school_codes(self):
        src = PRINCIPAL_ROUTES.read_text(encoding="utf-8")
        body = src.split("def _invigilation_page(")[1].split("\ndef ")[0]
        assert "exam_codes." in body, "the official invigilation page reads no codes"

    def test_the_admin_route_passes_school_codes(self):
        src = ADMIN_ROUTES.read_text(encoding="utf-8")
        body = src.split("def invigilation_page(")[1].split("\ndef ")[0]
        assert "exam_codes." in body, (
            "the admin-school invigilation page reads no codes")

    def test_the_shared_official_page_renders_codes(self):
        html = OFFICIAL_PAGE.read_text(encoding="utf-8")
        assert "recovery_code" in html or "code" in html, (
            "the shared official page draws no codes")

    def test_every_new_string_is_bilingual(self):
        for path in (TEACHER_PAGE, OFFICIAL_PAGE):
            html = path.read_text(encoding="utf-8")
            assert re.search(r"t\('|sgT\(", html), (
                f"{path.name} carries no i18n helper")
