"""Does the pupil's essay actually arrive in front of the teacher?

This file exists because it did not. `/teacher/api/grade-question/<exam>/<q>` — the
data behind the per-question marking queue — was bound to `_review_flags_for`, a
helper defined immediately under the decorators that took
`(supabase, submission_ids)`. Flask called it with `(exam_id, question_index)`, so
the first statement that touched the argument (`'…'.table(...)`) raised, and the
queue answered 500 for every question of every paper. A teacher opening
`/teacher/grading/<exam_id>` saw a list with no answers on it, which is reported as
"the answer never arrived".

A route bound to a view that cannot accept its own URL is the class of defect, and
the first test here is the general one: every rule in the app is checked, so the next
edit that strands a decorator fails in the suite rather than in a marking session.
"""
from __future__ import annotations

import inspect
import json
from types import SimpleNamespace

import pytest

EXAM = "11111111-2222-3333-4444-555555555555"
SUB = "aaaaaaaa-0000-0000-0000-000000000001"
TEACHER = "teacher-1"

#: What the exam page actually stores for an essay: `getAnswersLight()` in
#: `student/take_exam.html` writes `{type, text, paragraphs}` under the question's
#: index, and `text` is the paragraphs joined by a blank line.
ESSAY = ("Fotosintesis terjadi di kloroplas.\n\n"
         "Faktor yang mempengaruhi:\n"
         "- intensitas cahaya\n"
         "- kadar CO2\n"
         "- suhu\n\n"
         "Kesimpulan: ketiganya membatasi laju reaksi.")


# ── the general guard ───────────────────────────────────────────────────────

def test_every_route_view_can_accept_its_own_url_arguments(app):
    """A decorator attached to the wrong `def` shows up here.

    `static` is Flask's own endpoint and takes its path through a different
    mechanism, so it is the one exclusion — and it is named, not pattern-matched.
    """
    broken = []
    for rule in app.url_map.iter_rules():
        if rule.endpoint == "static":
            continue
        view = app.view_functions.get(rule.endpoint)
        if view is None:
            continue
        params = {p.name for p in inspect.signature(view).parameters.values()
                  if p.kind in (p.POSITIONAL_OR_KEYWORD, p.KEYWORD_ONLY)}
        missing = set(rule.arguments or ()) - params
        if missing:
            broken.append(f"{rule.rule} -> {rule.endpoint} (takes {sorted(params)})")
    assert not broken, "route(s) bound to a view that cannot accept their URL:\n  " + \
        "\n  ".join(broken)


def test_the_grade_question_api_is_the_view_on_its_url(app):
    """Named explicitly, because the general guard would pass if the URL vanished."""
    assert app.view_functions["teacher.grade_question_api"].__name__ == "grade_question_api"
    rules = [r for r in app.url_map.iter_rules()
             if str(r) == "/teacher/api/grade-question/<exam_id>/<int:question_index>"]
    assert rules and rules[0].endpoint == "teacher.grade_question_api"


# ── the text itself ─────────────────────────────────────────────────────────

class _Query:
    def __init__(self, rows):
        self._rows = rows

    def select(self, *a, **k):
        return self

    def eq(self, *a, **k):
        return self

    def in_(self, *a, **k):
        return self

    def order(self, *a, **k):
        return self

    def limit(self, *a, **k):
        return self

    def single(self):
        return self

    def execute(self):
        return SimpleNamespace(data=self._rows)


class _FakeSupabase:
    def __init__(self, subs):
        self._subs = subs

    def table(self, name):
        if name == "submissions":
            return _Query(self._subs)
        return _Query([])


def _teacher_client(monkeypatch, app, subs):
    from app.routes import teacher as tmod
    from app.utils import auth as authmod

    monkeypatch.setattr(authmod, "_session_for", lambda token: {
        "user_id": TEACHER, "email": "t@x", "name": "t", "role": "guru",
        "school_id": "school-1", "status": "active",
    })
    monkeypatch.setattr(tmod, "get_supabase", lambda: _FakeSupabase(subs))
    monkeypatch.setattr(tmod, "_guard_exam", lambda *a, **k: (
        {"id": EXAM, "teacher_id": TEACHER, "school_id": "school-1"}, None))
    monkeypatch.setattr(tmod, "_review_flags_for", lambda *a, **k: [])
    monkeypatch.setattr(tmod, "_annotations_for", lambda *a, **k: [])

    client = app.test_client()
    client.set_cookie("access_token", "tok")
    return client


def _submission(answers):
    return {
        "id": SUB,
        "student_id": "student-1",
        "answers": answers,
        "score": None,
        "final_score": None,
        "status": "submitted",
        "submitted_at": "2026-10-01T09:00:00",
        "profiles": {"full_name": "Ayu"},
    }


def test_the_queue_returns_the_whole_essay(monkeypatch, app):
    """Every character, including the list markers the pupil typed."""
    answers = {"2": {"type": "essay_text", "text": ESSAY,
                     "paragraphs": [{"id": "p0", "text": ESSAY, "html": "<p>…</p>",
                                     "order": 0}]}}
    client = _teacher_client(monkeypatch, app, [_submission(answers)])

    resp = client.get(f"/teacher/api/grade-question/{EXAM}/2")

    assert resp.status_code == 200, resp.data[:400]
    body = resp.get_json()
    assert len(body["students"]) == 1
    got = body["students"][0]["essayText"]
    assert got == ESSAY.strip()
    # The specific things that go missing first: a list marker, a blank line
    # between paragraphs, and the final paragraph.
    assert "- kadar CO2" in got
    assert "\n\n" in got
    assert got.endswith("ketiganya membatasi laju reaksi.")


def test_an_answer_stored_as_a_plain_string_still_arrives(monkeypatch, app):
    """Older papers hold a bare string; the queue must not show them empty."""
    client = _teacher_client(monkeypatch, app, [_submission({"2": ESSAY})])

    body = client.get(f"/teacher/api/grade-question/{EXAM}/2").get_json()

    assert body["students"][0]["essayText"] == ESSAY.strip()


def test_an_answer_with_no_text_is_empty_rather_than_missing(monkeypatch, app):
    client = _teacher_client(monkeypatch, app, [_submission({"2": {"type": "essay"}})])

    body = client.get(f"/teacher/api/grade-question/{EXAM}/2").get_json()

    assert body["students"][0]["essayText"] == ""
    assert body["students"][0]["name"] == "Ayu"
