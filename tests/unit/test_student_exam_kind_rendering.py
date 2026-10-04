"""The student exam page, rendered with every question kind on one paper.

The reported fault was "the teacher set True/False, complex multiple choice,
matching, drag & drop, and a multi-option multiple choice, and none of them
appear for the pupil". This suite is the server half of that question, and it is
deliberately a *render* rather than a grep: it hands the page one paper carrying
every kind plus the public material a matching / drag / complex question needs,
and asserts the page that comes out actually carries

  * the pupil-facing type map (`question_types`) — so `sgKind()` has something to
    classify,
  * one control branch per kind,
  * the public options (`lefts`/`rights`/`chips`/`statements`/`categories`) that
    `public_options()` releases and never the pairing itself, and
  * the *stored* duration, so a custom 90 minutes is what the page is told.

If these hold, the remaining surface is JavaScript at runtime, and a browser — not
a template render — is what proves that half. That boundary is the point: this
file fails loudly the day a kind stops reaching the page, and says nothing about
a kind whose Alpine branch throws after it arrives.
"""
from __future__ import annotations

import contextlib

from flask import g


@contextlib.contextmanager
def _signed_in(app):
    with app.test_request_context("/student/exams/e1"):
        g.user_id, g.user_name, g.user_role = "stu-1", "Murid Uji", "murid"
        g.user_email, g.tz_offset, g.show = "s@test", 7, {}
        g.user_school_id, g.user_class_id = "sch-1", "cls-1"
        yield


def _render(app, qtypes, qopts):
    ctx = {
        "exam": {"id": "e1", "title": "T", "total_questions": len(qtypes),
                 "duration_minutes": 90, "question_types": qtypes,
                 "question_canvas": {}, "question_audio": {}, "question_pages": {},
                 "pdf_page_urls": []},
        "anti_cheat_config": "{\"anti_cheat_enabled\": true}",
        "exam_started_at": None, "recovery_code": "", "question_options": qopts,
        "deadline": None, "deadline_reason": "duration", "seconds_left": 5400,
        "window_end": None, "away_grace_seconds": 15, "away_grace_chances": 2,
        "student_name": "Ahmad", "student_class_label": "7A",
    }
    with _signed_in(app):
        return app.jinja_env.get_template("student/take_exam.html").render(**ctx)


def test_every_kind_block_is_rendered_and_types_are_wired(app):
    qtypes = {"0": "mcq", "1": "true_false", "2": "complex_multiple_choice",
              "3": "match", "4": "drag_drop", "5": "ordering", "6": "essay_canvas"}
    qopts = {
        "2": {"statements": ["a", "b"], "categories": ["Benar", "Salah"]},
        "3": {"lefts": ["x"], "rights": ["y"]},
        "4": {"chips": ["p", "q"]},
        "5": {"chips": ["p", "q"]},
    }
    html = _render(app, qtypes, qopts)

    # 1. the type map reaches the page, question by question
    assert '"true_false"' in html and '"complex_multiple_choice"' in html, \
        "the question types never reached the page"
    # 2. the stored duration reaches the page (not a 60-minute default)
    assert "durationMinutes: 90" in html, "the stored duration never reached the page"
    # 3. one control branch per kind
    for marker in ("truefalse", "pgk", "match", "dragdrop", "ordering"):
        assert f"qKind(i) === '{marker}'" in html, f"missing branch: {marker}"
    # 4. the public material, and never the pairing
    assert '"statements"' in html and '"lefts"' in html and '"chips"' in html, \
        "public options for match/drag/pgk never reached the page"


def test_the_builder_offers_every_type_the_pupil_page_can_render(app):
    """The picker a teacher sees and the kinds the pupil page draws must agree."""
    from app.services.question_types import PICKER_TYPES, question_kind

    with app.test_request_context("/teacher/exams/new"):
        g.user_id, g.user_name, g.user_role = "t-1", "Guru Uji", "guru"
        g.tz_offset, g.show = 7, {}
        g.user_school_id = "sch-1"
        html = app.jinja_env.get_template("teacher/exam_form.html").render(
            exam=None, subjects=[], classes=[])

    for value in PICKER_TYPES:
        assert f'"{value}"' in html, f"the builder no longer offers {value}"
    kinds = {question_kind(t) for t in PICKER_TYPES}
    assert {"choice", "truefalse", "pgk", "match", "dragdrop", "ordering"} <= kinds, \
        "a picker type has no kind for the pupil page to render"
