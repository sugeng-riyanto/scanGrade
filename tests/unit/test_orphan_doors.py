"""Pages that existed but nothing reached now have doors, and the doors match the merge.

When the live room (`proctoring`) and the co-occurrence patterns (`cheat-analysis`)
were merged into one session page, the two old URLs were kept as redirects —
"a bookmark is a promise" — and the app deliberately stopped pointing at them
(`tests/unit/test_session_page.py::test_nothing_points_at_the_dead_endpoints`).
What that left was the *survivor* itself with almost no way in: only the results
page linked `/sessions`, and the template marketplace (`/teacher/templates`) had
no inbound link anywhere at all — a page you could only find by knowing the URL.

So the doors are the two views of the one session page, reached where a teacher
already is:

* **the exam detail page** (`teacher/exam_form.html`, edit mode) offers both —
  the live room through `#room` and the patterns through `#observations`, landing
  each reader on the half they came for;
* **the exams list** (`teacher/exams.html`) offers the marketplace.

Both are bilingual pairs, and neither names a dead endpoint: the door is the
survivor, not the redirect.
"""
from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
TEMPLATES = ROOT / "app" / "templates" / "teacher"
EXAM_FORM = TEMPLATES / "exam_form.html"
EXAMS = TEMPLATES / "exams.html"
SESSION = TEMPLATES / "session_review.html"

#: The one surviving view, and the two halves a teacher may be looking for.
SESSION_PATH = "/teacher/exams/e1/sessions"
MARKETPLACE = "/teacher/templates"


def _exam() -> dict:
    """An `exam` row complete enough that the builder's embedded JSON renders."""
    return {
        "id": "e1", "title": "UH Matematika", "subject": "MTK",
        "class_ids": ["c1"], "pdf_url": None, "status": "draft",
        "duration_minutes": 60, "total_questions": 5, "start_at": None,
        "end_at": None, "question_types": {}, "question_weights": {},
        "answer_key": {}, "question_pages": {}, "question_audio": {},
        "question_cognitive": {},
    }


@pytest.fixture()
def rendered_exam_detail(app):
    from flask import g

    with app.test_request_context("/teacher/exams/e1"):
        g.user_id, g.user_name, g.user_role = "u-1", "Uji", "guru"
        g.user_email, g.tz_offset, g.show = "u@example.test", 7, {}
        g.user_school_id, g.user_class_id = "sch-1", "cls-1"
        return app.jinja_env.get_template("teacher/exam_form.html").render(
            exam=_exam(), subjects=[], classes=[],
            builder_defaults={"subject_id": None, "class_ids": [],
                              "duration_minutes": 60})


@pytest.fixture()
def rendered_exams(app):
    from flask import g

    with app.test_request_context("/teacher/exams"):
        g.user_id, g.user_name, g.user_role = "u-1", "Uji", "guru"
        g.user_email, g.tz_offset, g.show = "u@example.test", 7, {}
        g.user_school_id, g.user_class_id = "sch-1", "cls-1"
        return app.jinja_env.get_template("teacher/exams.html").render(
            exams=[], unassigned_ids=set())


# ── the exam detail page: both views of the one session page ──────────────────

def test_the_exam_detail_page_links_the_live_room(rendered_exam_detail):
    assert f'href="{SESSION_PATH}#room"' in rendered_exam_detail, (
        "the exam detail page has no door to the live room; a teacher has to "
        "remember /sessions to proctor a sitting")


def test_the_exam_detail_page_links_the_patterns(rendered_exam_detail):
    assert f'href="{SESSION_PATH}#observations"' in rendered_exam_detail, (
        "the exam detail page has no door to the pattern view")


def test_the_two_doors_are_bilingual_pairs(rendered_exam_detail):
    """Each door names its half in both languages, so it switches with the toggle."""
    for indonesian, english in (("Pengawasan Langsung", "Live proctoring"),
                                ("Analisis Pola", "Pattern analysis")):
        assert indonesian in rendered_exam_detail and english in rendered_exam_detail, (
            f"the door {indonesian!r}/{english!r} is not a bilingual pair")


def test_the_doors_reach_the_survivor_not_the_dead_endpoints(rendered_exam_detail):
    assert "/proctoring" not in rendered_exam_detail
    assert "/cheat-analysis" not in rendered_exam_detail


def test_the_two_doors_land_on_two_different_halves():
    """A fragment that names nothing scrolls nowhere, so the anchors must exist."""
    text = SESSION.read_text(encoding="utf-8")
    assert 'id="room"' in text, "the live room has no anchor for its door to reach"
    assert 'id="observations"' in text, "the pattern view has no anchor"


# ── the exams list: the marketplace ──────────────────────────────────────────

def test_the_exams_list_links_the_template_marketplace(rendered_exams):
    assert f'href="{MARKETPLACE}"' in rendered_exams, (
        "the template marketplace is orphaned: no page links to it")


def test_the_marketplace_door_is_bilingual(rendered_exams):
    assert "Template Ujian" in rendered_exams and "Exam templates" in rendered_exams, (
        "the marketplace door does not switch with the language toggle")


# ── and the marketplace is really the marketplace ────────────────────────────

def test_the_marketplace_route_still_serves_its_page(app):
    rules = {rule.rule for rule in app.url_map.iter_rules()}
    assert MARKETPLACE in rules, "the door points at a route that does not exist"
    page = (TEMPLATES / "templates.html").read_text(encoding="utf-8")
    assert "Template Ujian" in page, (
        "the door's landing page is not the marketplace")
