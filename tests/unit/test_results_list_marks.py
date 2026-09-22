"""Unreleased marks must not reach the student's own pages.

Both ``/student/results`` and ``/student/dashboard`` render a score, a penalty and
derived statistics for every row. The templates hide the score behind an Alpine
``x-show`` toggle, which still leaves the value in the DOM — and the dashboard
computed its average, mastery level, subject averages, trend and weak areas from
the marks directly, so blanking the display alone would not have been enough.

``render_template`` is stubbed: these tests are about the data handed to the view.
``test_exam_integrity`` covers the markup.
"""
from types import SimpleNamespace

import pytest

from app.routes import student as studentmod


class FakeQuery:
    """Chainable postgrest stand-in returning a fixed row set."""

    def __init__(self, rows):
        self._rows = rows

    def select(self, *a, **k):
        return self

    def eq(self, *a, **k):
        return self

    def neq(self, *a, **k):
        return self

    def in_(self, *a, **k):
        return self

    def order(self, *a, **k):
        return self

    def limit(self, *a, **k):
        return self

    def execute(self):
        return SimpleNamespace(data=list(self._rows))


class FakeSupabase:
    """Table-aware so each lookup gets rows of the right shape."""

    def __init__(self, tables):
        self._tables = tables

    def table(self, name):
        return FakeQuery(self._tables.get(name, []))


def _submission(sid, exam_id, status, published, score, penalty=0.0, subject="Matematika"):
    return {
        "id": sid, "exam_id": exam_id, "student_id": "stu-1",
        "status": status, "is_published": published,
        "score": score, "final_score": score, "penalty": penalty,
        "max_score": 100, "violations": 0,
        "submitted_at": "2026-09-01T02:00:00+00:00",
        "graded_at": "2026-09-02T02:00:00+00:00",
        "answers": {},
        "exams": {"id": exam_id, "title": f"Ujian {exam_id}", "subject": subject,
                  "question_types": {}, "total_questions": 0},
    }


# One unreleased (still being marked) and one released result.
SUBS = [
    _submission("sub-unreleased", "exam-1", "graded", False, 77, penalty=5.5),
    _submission("sub-released", "exam-2", "published", True, 88, subject="Fisika"),
]


def _run(app, monkeypatch, route_func, tables, path):
    from flask import g

    captured = {}

    def _capture(name, **kw):
        captured["ctx"] = kw
        return ""

    monkeypatch.setattr(studentmod, "render_template", _capture)
    app.extensions["supabase"] = FakeSupabase(tables)

    with app.test_request_context(path):
        g.user_id = "stu-1"
        g.user_role = "murid"
        g.user_name = "Murid Uji"
        route_func.__wrapped__()

    return captured["ctx"]


# ── /student/results ─────────────────────────────────────────────

def test_results_blanks_unreleased_marks(app, monkeypatch):
    ctx = _run(app, monkeypatch, studentmod.results,
               {"submissions": SUBS}, "/student/results")
    by_id = {s["id"]: s for s in ctx["submissions"]}

    assert by_id["sub-unreleased"]["score"] is None
    assert by_id["sub-unreleased"]["final_score"] is None
    assert by_id["sub-unreleased"]["penalty"] is None


def test_results_keeps_released_marks(app, monkeypatch):
    ctx = _run(app, monkeypatch, studentmod.results,
               {"submissions": SUBS}, "/student/results")
    by_id = {s["id"]: s for s in ctx["submissions"]}

    assert by_id["sub-released"]["score"] == 88
    assert by_id["sub-released"]["penalty"] == 0


def test_results_subject_totals_ignore_unreleased(app, monkeypatch):
    """An average must not be built from marks the student cannot see."""
    ctx = _run(app, monkeypatch, studentmod.results,
               {"submissions": SUBS}, "/student/results")
    totals = {t["name"]: t for t in ctx["subject_totals"]}

    assert totals["Matematika"]["avg"] == 0      # unreleased only
    assert totals["Fisika"]["avg"] == 88


def test_results_survive_an_exam_with_no_subject(app, monkeypatch):
    """A NULL subject is a row, not a missing key.

    `.get("subject", "Lainnya")` does not catch it — the key is there and its
    value is None — so the grouping key became None and the sort below compared it
    with a real subject's name: `TypeError: '<' not supported between instances of
    'NoneType' and 'str'`, and the student's whole results page was a 500. It
    needs one paper with no subject *beside* one with a subject, which is exactly
    what a school whose builder left the field empty has.
    """
    rows = [
        _submission("sub-no-subject", "exam-3", "published", True, 60, subject=None),
        _submission("sub-released", "exam-2", "published", True, 88, subject="Fisika"),
    ]

    ctx = _run(app, monkeypatch, studentmod.results,
               {"submissions": rows}, "/student/results")

    assert [t["name"] for t in ctx["subject_totals"]] == ["Fisika", "Lainnya"]


# ── /student/dashboard ───────────────────────────────────────────

def test_dashboard_hides_unreleased_marks(app, monkeypatch):
    ctx = _run(app, monkeypatch, studentmod.dashboard,
               {"submissions": SUBS, "profiles": [], "exams": []},
               "/student/dashboard")
    by_id = {s["id"]: s for s in ctx["completed_exams"]}

    assert by_id["sub-unreleased"]["final_score"] is None
    assert by_id["sub-unreleased"]["score"] is None
    assert by_id["sub-unreleased"]["penalty"] is None


def test_dashboard_metrics_skip_unreleased_marks(app, monkeypatch):
    """The average, mastery level and trend must reflect released marks only.

    The dashboard used to average every completed submission, so it published an
    unreleased score as the student's progress — the screen the student lands on
    first.
    """
    ctx = _run(app, monkeypatch, studentmod.dashboard,
               {"submissions": SUBS, "profiles": [], "exams": []},
               "/student/dashboard")

    assert ctx["avg_score"] == 88.0, "the unreleased 77 must not be averaged in"
    assert ctx["mastery_level"] == "Baik"          # 88 → Baik, 82.5 would too
    assert ctx["subject_averages"] == {"Fisika": 88.0}
    assert [t["score"] for t in ctx["score_trend"]] == [88.0]
    assert [w["score"] for w in ctx["weak_areas"]] == []
