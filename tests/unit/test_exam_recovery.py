"""Recovery codes: getting back into an exam session without weakening the rules.

``/student/recover`` was linked from the exam list and shipped a template, but no
route existed and nothing ever issued a code — the page answered 404 and the code
input called an endpoint that did not exist. The table ``exam_access_codes`` was
referenced only by deletes.

The tests below pin the two properties that matter: the code is idempotent and
never blocks an exam, and redeeming one cannot reach another student's session or
an exam the rules would refuse.
"""
from types import SimpleNamespace

import pytest

from app.routes import student as studentmod
from app.utils.exam_recovery import issue_code, redeem_code


# ── fakes ────────────────────────────────────────────────────────

class FakeTable:
    """Chainable stand-in for one PostgREST table."""

    def __init__(self, rows=None, fail_writes=False):
        self.rows = list(rows or [])
        self.fail_writes = fail_writes
        self._filters = []
        self._mode = "select"
        self._payload = None
        self._single = False
        self._desc = False
        self._limit = None

    # query building
    def select(self, *a, **k):
        self._mode = "select"
        return self

    def eq(self, col, val):
        self._filters.append((col, val))
        return self

    def order(self, col, desc=False):
        self._desc = bool(desc)
        return self

    def limit(self, n):
        self._limit = n
        return self

    def maybe_single(self):
        self._single = True
        return self

    def insert(self, data):
        self._mode, self._payload = "insert", data
        return self

    def update(self, data):
        self._mode, self._payload = "update", data
        return self

    def execute(self):
        rows = [r for r in self.rows if all(r.get(c) == v for c, v in self._filters)]
        if self._desc:
            rows = sorted(rows, key=lambda r: r.get("created_at") or "", reverse=True)
        if self._limit:
            rows = rows[: self._limit]

        if self._mode in ("insert", "update") and self.fail_writes:
            raise RuntimeError("write failed")

        if self._mode == "insert":
            row = dict(self._payload)
            row.setdefault("id", f"code-{len(self.rows) + 1}")
            row.setdefault("created_at", f"2026-09-12T00:0{len(self.rows)}:00+00:00")
            self.rows.append(row)
            return SimpleNamespace(data=[row])

        if self._mode == "update":
            for r in rows:
                r.update(self._payload)
            return SimpleNamespace(data=rows)

        if self._single:
            # maybe_single(): None — not a response carrying data=None — on no match
            return SimpleNamespace(data=rows[0] if rows else None)
        return SimpleNamespace(data=rows)


class FakeSupabase:
    def __init__(self, tables=None):
        self._tables = {}
        for name, rows in (tables or {}).items():
            self._tables[name] = rows if isinstance(rows, FakeTable) else FakeTable(rows)

    def table(self, name):
        return self._tables.setdefault(name, FakeTable())


EXAM = {
    "id": "exam-1", "title": "Matematika", "subject": "Matematika",
    "is_published": True, "status": "active",
    "school_id": "school-a", "class_ids": ["class-a"],
}
PROFILE = {"id": "stu-1", "school_id": "school-a", "class_id": "class-a"}


def db(codes=None, submissions=None, exam=None, profile=None, **kw):
    return FakeSupabase({
        "exam_access_codes": FakeTable(codes or []),
        "submissions": FakeTable(submissions or []),
        "exams": FakeTable([exam or EXAM]),
        "profiles": FakeTable([profile or PROFILE]),
    }, **kw)


# ── issuing ──────────────────────────────────────────────────────

def test_issue_creates_a_six_digit_code():
    supa = db()
    code = issue_code(supa, "stu-1", "exam-1")

    assert code and len(code) == 6 and code.isdigit()
    assert supa.table("exam_access_codes").rows[0]["code"] == code


def test_issue_is_idempotent_across_reloads():
    """Reloading the exam must not invalidate the number the student wrote down."""
    supa = db()

    first = issue_code(supa, "stu-1", "exam-1")
    second = issue_code(supa, "stu-1", "exam-1")
    third = issue_code(supa, "stu-1", "exam-1")

    assert first == second == third
    assert len(supa.table("exam_access_codes").rows) == 1


def test_issue_never_blocks_the_exam_when_storage_fails():
    """The exam must open even if this feature is broken."""
    supa = db()
    supa.table("exam_access_codes").fail_writes = True

    assert issue_code(supa, "stu-1", "exam-1") is None


def test_issue_avoids_codes_the_same_student_already_has():
    supa = db(codes=[{"id": "c1", "student_id": "stu-1", "exam_id": "exam-0",
                      "code": "123456", "created_at": "2026-09-11T00:00:00+00:00"}])

    code = issue_code(supa, "stu-1", "exam-1")

    assert code != "123456"


# ── redeeming ────────────────────────────────────────────────────

def test_redeem_returns_the_exam_and_stamps_use():
    supa = db(codes=[{"id": "c1", "student_id": "stu-1", "exam_id": "exam-1",
                      "code": "234567", "created_at": "2026-09-12T00:00:00+00:00"}])

    assert redeem_code(supa, "stu-1", "234567") == "exam-1"
    assert supa.table("exam_access_codes").rows[0]["is_used"] is True
    assert supa.table("exam_access_codes").rows[0]["used_at"]


def test_redeem_still_works_after_the_first_use():
    """A second dead device must not lock the student out of their own session.

    ``is_used``/``used_at`` record the first redemption for the audit trail; they
    are deliberately not an access control, because the redemption anyway only
    leads to an exam this same student is already allowed to sit.
    """
    supa = db(codes=[{"id": "c1", "student_id": "stu-1", "exam_id": "exam-1",
                      "code": "234567", "is_used": True,
                      "created_at": "2026-09-12T00:00:00+00:00"}])

    assert redeem_code(supa, "stu-1", "234567") == "exam-1"


def test_redeem_is_scoped_to_the_calling_student():
    """Another student's code is never even searched for."""
    supa = db(codes=[{"id": "c1", "student_id": "stu-2", "exam_id": "exam-1",
                      "code": "234567", "created_at": "2026-09-12T00:00:00+00:00"}])

    assert redeem_code(supa, "stu-1", "234567") is None


def test_redeem_rejects_bad_input_without_a_lookup():
    supa = db()
    for bad in ("", "12345", "1234567", "abcdef", "12 456", None):
        assert redeem_code(supa, "stu-1", bad) is None


def test_redeem_returns_none_when_unknown():
    assert redeem_code(db(), "stu-1", "999999") is None


def test_issue_reuses_one_code_per_exam():
    """Two exams in the same day must not share a code when the old one is "used"."""
    supa = db(codes=[{"id": "c1", "student_id": "stu-1", "exam_id": "exam-0",
                      "code": "111111", "is_used": True,
                      "created_at": "2026-09-11T00:00:00+00:00"}])

    first = issue_code(supa, "stu-1", "exam-1")
    again = issue_code(supa, "stu-1", "exam-1")

    assert first == again
    assert len(supa.table("exam_access_codes").rows) == 2


def test_redeem_survives_a_failing_stamp():
    """Stamping the audit trail must not stop a student from resuming."""
    supa = db(codes=[{"id": "c1", "student_id": "stu-1", "exam_id": "exam-1",
                      "code": "234567", "created_at": "2026-09-12T00:00:00+00:00"}])
    codes = supa.table("exam_access_codes")
    codes.fail_writes = True

    assert redeem_code(supa, "stu-1", "234567") == "exam-1"


# ── the API ──────────────────────────────────────────────────────

def _call_recover(supa, payload, monkeypatch, app):
    from flask import g

    app.extensions["supabase"] = supa
    with app.test_request_context("/student/api/recover-exam", method="POST", json=payload):
        g.user_id, g.user_role = "stu-1", "murid"
        raw = studentmod.api_recover_exam.__wrapped__()
    return raw if isinstance(raw, tuple) else (raw, 200)


@pytest.fixture
def app():
    from app import create_app
    return create_app("app.config.TestingConfig")


CODE_ROW = [{"id": "c1", "student_id": "stu-1", "exam_id": "exam-1",
             "code": "234567", "created_at": "2026-09-12T00:00:00+00:00"}]


def test_api_redirects_to_the_exam(app, monkeypatch):
    supa = db(codes=CODE_ROW, submissions=[{"id": "s1", "student_id": "stu-1",
                                            "exam_id": "exam-1", "status": "draft",
                                            "created_at": "2026-09-12T01:00:00+00:00"}])
    resp, status = _call_recover(supa, {"code": "234567"}, monkeypatch, app)

    assert status == 200
    assert resp.get_json()["redirect"] == "/student/exams/exam-1"


def test_api_rejects_an_unknown_code(app, monkeypatch):
    resp, status = _call_recover(db(), {"code": "999999"}, monkeypatch, app)

    assert status == 404
    assert "tidak ditemukan" in resp.get_json()["error"].lower()


def test_api_rejects_a_malformed_code(app, monkeypatch):
    resp, status = _call_recover(db(), {"code": "12"}, monkeypatch, app)

    assert status == 400
    assert "6 angka" in resp.get_json()["error"]


def test_api_refuses_another_schools_exam(app, monkeypatch):
    """A valid code still cannot open an exam the student may not sit."""
    supa = db(codes=CODE_ROW, exam=dict(EXAM, school_id="school-b"))
    resp, status = _call_recover(supa, {"code": "234567"}, monkeypatch, app)

    assert status == 403
    assert resp.get_json()["error"]
    assert "redirect" not in resp.get_json()


def test_api_refuses_an_unpublished_exam(app, monkeypatch):
    supa = db(codes=CODE_ROW, exam=dict(EXAM, is_published=False))
    resp, status = _call_recover(supa, {"code": "234567"}, monkeypatch, app)

    assert status == 403


def test_api_points_a_collected_exam_at_the_results(app, monkeypatch):
    supa = db(codes=CODE_ROW, submissions=[{"id": "s1", "student_id": "stu-1",
                                            "exam_id": "exam-1", "status": "submitted",
                                            "created_at": "2026-09-12T01:00:00+00:00"}])
    resp, status = _call_recover(supa, {"code": "234567"}, monkeypatch, app)

    assert status == 409
    assert resp.get_json()["redirect"] == "/student/results"


# ── the page ─────────────────────────────────────────────────────

def test_page_lists_only_sessions_that_may_be_opened(app, monkeypatch):
    captured = {}

    def _capture(name, **kw):
        captured["template"] = name
        captured["ctx"] = kw
        return "<page>"

    monkeypatch.setattr(studentmod, "render_template", _capture)
    supa = db(submissions=[
        {"id": "s1", "student_id": "stu-1", "exam_id": "exam-1", "status": "draft",
         "started_at": "2026-09-12T01:00:00+00:00", "exams": EXAM},
        # Same student, same school — but the teacher has not published it yet.
        {"id": "s2", "student_id": "stu-1", "exam_id": "exam-2", "status": "draft",
         "started_at": "2026-09-12T02:00:00+00:00",
         "exams": dict(EXAM, id="exam-2", is_published=False)},
    ])
    app.extensions["supabase"] = supa

    from flask import g
    with app.test_request_context("/student/recover"):
        g.user_id, g.user_role = "stu-1", "murid"
        studentmod.recover_exam_page.__wrapped__()

    assert captured["template"] == "student/recover.html"
    assert [s["exam_id"] for s in captured["ctx"]["open_sessions"]] == ["exam-1"]


def test_page_renders_when_the_lookup_fails(app, monkeypatch):
    """A failing query must not leave the student staring at a 500."""
    captured = {}

    def _capture(name, **kw):
        captured["ctx"] = kw
        return "<page>"

    monkeypatch.setattr(studentmod, "render_template", _capture)
    supa = FakeSupabase()

    def _boom(name):
        raise RuntimeError("db down")

    supa.table = _boom
    app.extensions["supabase"] = supa

    from flask import g
    with app.test_request_context("/student/recover"):
        g.user_id, g.user_role = "stu-1", "murid"
        studentmod.recover_exam_page.__wrapped__()

    assert captured["ctx"]["open_sessions"] == []
