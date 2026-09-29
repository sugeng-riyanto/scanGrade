"""A duration of zero is *Tak terbatas* — and every page now writes it that way.

``duration_minutes = 0`` is the teacher form's "Tak terbatas / Unlimited", and
``exam_window.deadline()`` has always read it as "nothing enforces an end". Nothing
else agreed, and each page was wrong in its own direction — all four measured on the
running code before this file existed:

* the exam list wrote ``{{ e.duration_minutes or 60 }}``, so a paper a teacher
  deliberately left open-ended was advertised to the class as **60 menit**;
* the student dashboard wrote the raw column, so the same paper read **"0 min"** one
  page away from a screen that said *Tak terbatas*;
* the builder's own form selected the 60-minute option for it
  (``(exam.duration_minutes or 60) == m``) — one conditional render away from
  *saving* 60 and quietly converting an unlimited paper into an hour;
* and the exam paper had the rule right, but in a JavaScript ternary of its own.

One row, four answers. ``exam_window.duration_facts()`` is the single place that now
decides which branch a duration falls in; each template writes the copy, because the
copy is a ``t()`` pair and a helper that returned a finished sentence would have to
pick a language on the server — the one thing no page here does.

The pages are **rendered**, not grepped: "does the card say 60" is not a question a
search of the source can answer.
"""
from __future__ import annotations

import contextlib
import re
from pathlib import Path

import pytest

from app.utils import exam_window

ROOT = Path(__file__).resolve().parents[2]
TEMPLATES = {
    "exam_list": ROOT / "app" / "templates" / "student" / "exam_list.html",
    "dashboard": ROOT / "app" / "templates" / "student" / "dashboard.html",
    "exam_form": ROOT / "app" / "templates" / "teacher" / "exam_form.html",
    "take_exam": ROOT / "app" / "templates" / "student" / "take_exam.html",
}

#: The fabrication itself, in the one shape it shipped in: a fallback of minutes
#: where the value is *unlimited*. `or 0` is not this — it says the same thing the
#: arithmetic says.
FABRICATED_FALLBACK = re.compile(r"duration_minutes\s*\bor\b\s*(?!0\b)\d+")


def _exam(**over):
    """A real exam row: the builder's own form reads sixteen columns off it, and a
    missing one is `Undefined` rather than `None` — which breaks the page at
    `tojson` rather than at the assertion this suite is about."""
    row = {
        "id": "e1", "title": "Latihan Akhir", "subject": "Fisika",
        "duration_minutes": 45, "total_questions": 10, "question_types": {},
        "end_at": None, "auto_submit_on_window_end": False, "sg_in_progress": False,
        "answer_key": {}, "class_ids": [], "description": "", "max_attempts": 1,
        "pdf_url": None, "question_audio": {}, "question_cognitive": {},
        "question_pages": {}, "question_weights": {}, "start_at": None,
        "subject_id": "subj-1",
    }
    row.update(over)
    return row


@contextlib.contextmanager
def _signed_in(app, path, role="murid"):
    """A request context carrying what base.html reads for this role."""
    from flask import g
    with app.test_request_context(path):
        g.user_id, g.user_name, g.user_role = "u-1", "Pengguna Uji", role
        g.user_email, g.tz_offset, g.show = "u@example.test", 7, {}
        g.user_school_id, g.user_class_id = "sch-1", "cls-1"
        yield


def _render_exam_list(app, exams):
    with _signed_in(app, "/student/exams"):
        return app.jinja_env.get_template("student/exam_list.html").render(exams=exams)


def _render_dashboard(app, exams):
    with _signed_in(app, "/student/dashboard"):
        return app.jinja_env.get_template("student/dashboard.html").render(
            available_exams=exams, completed_exams=[], avg_score=0,
            user_name="Pengguna Uji", student_class={"name": "7A"},
            subject_count=0, active_whiteboards=[], subject_averages={},
            chart_points=[], weak_areas=[], mastery_level=("Belum Ada Data", "No data yet"))


def _render_form(app, exam):
    with _signed_in(app, "/teacher/exams/new", role="guru"):
        return app.jinja_env.get_template("teacher/exam_form.html").render(
            exam=exam, subjects=[], classes=[])


def _render_paper(app, minutes):
    """The exam page with only the context its own copy needs (as its route sends)."""
    from flask import g
    ctx = {
        "exam": {"id": "e1", "title": "T", "total_questions": 1,
                 "duration_minutes": minutes, "question_types": {},
                 "pdf_page_urls": []},
        "anti_cheat_config": "{\"anti_cheat_enabled\": true}",
        "exam_started_at": None, "recovery_code": "", "question_options": {},
        "deadline": None, "deadline_reason": "duration", "seconds_left": None,
        "window_end": None, "away_grace_seconds": 15, "away_grace_chances": 2,
        "student_name": "Ahmad", "student_class_label": "7A",
    }
    with app.test_request_context("/student/exams/e1"):
        g.user_id, g.user_name, g.user_role = "stu-1", "Murid Uji", "murid"
        g.tz_offset, g.show = 7, {}
        return app.jinja_env.get_template("student/take_exam.html").render(**ctx)


DURATION_SPAN = re.compile(r'<span data-duration="([^"]+)"')
TEACHER_DURATION = re.compile(r'id="duration-value"[^>]*>([^<]*)<')


def _duration_branch(html):
    """What the list and the dashboard say this paper's duration is."""
    found = DURATION_SPAN.findall(html)
    assert len(found) == 1, f"expected one duration, found {found}"
    return found[0]


SELECT = re.compile(r"<select name=\"duration_minutes\".*?</select>", re.S)
OPTION = re.compile(r"<option value=\"(\d+)\"([^>]*)>")


def _selected_minutes(html):
    select = SELECT.search(html)
    assert select, "the builder no longer has a duration select"
    return [v for v, attrs in OPTION.findall(select.group(0)) if "selected" in attrs]


# ── 1. the rule itself ──────────────────────────────────────────────────────

class TestTheRule:
    def test_zero_is_unlimited_and_a_number_of_minutes_is_not(self):
        assert exam_window.duration_facts(0)["unlimited"] is True
        assert exam_window.duration_facts(45) == {"unlimited": False, "minutes": 45}
        assert exam_window.duration_facts(120)["minutes"] == 120

    def test_a_missing_value_reads_as_unlimited_like_the_arithmetic(self):
        """A row whose column is NULL must not become some default number of
        minutes: `deadline()` reads it as "nothing enforces an end", because
        `exam.get("duration_minutes") or 0` is 0."""
        for missing in (None, "", "bukan angka"):
            assert exam_window.duration_facts(missing)["unlimited"] is True, missing

    def test_it_agrees_with_the_deadline_arithmetic(self):
        """The two answers about one column, asserted as a relation rather than as
        two lists that happen to match today — over the values the arithmetic itself
        is defined on (`deadline()` adds the number to a `timedelta`, so a malformed
        one raises there instead of answering)."""
        from datetime import datetime, timezone
        started = datetime(2026, 9, 29, 7, 0, tzinfo=timezone.utc)
        for value in (0, None, 45):
            exam = {"duration_minutes": value, "end_at": None,
                    "auto_submit_on_window_end": False}
            unlimited = exam_window.duration_facts(value)["unlimited"]
            assert unlimited == (exam_window.deadline(exam, started) is None), value

    def test_a_numeric_string_from_a_form_is_read_as_its_number(self):
        """Deliberately more tolerant than the arithmetic, and not a second answer:
        a form value arrives as text, and a page must not read \"60\" as unlimited
        because `deadline()` would have raised on it."""
        assert exam_window.duration_facts("60") == {"unlimited": False, "minutes": 60}
        assert exam_window.duration_facts("0")["unlimited"] is True

    def test_the_pages_can_reach_it(self, app):
        """It has to be a template global, or a page would have to import it —
        which is how a second copy gets written."""
        assert "duration_facts" in app.jinja_env.globals


# ── 2. the exam list ────────────────────────────────────────────────────────

class TestTheExamList:
    def test_an_unlimited_paper_is_not_advertised_as_an_hour(self, app):
        html = _render_exam_list(app, [_exam(duration_minutes=0)])
        assert _duration_branch(html) == "unlimited", (
            "the list still turns the teacher's Unlimited into some number of minutes"
        )
        assert "t('Tak terbatas','Unlimited')" in html, (
            "the card cannot say 'Unlimited' in either language"
        )
        assert "60 menit" not in html and "60 min" not in html

    def test_a_timed_paper_still_states_its_own_number(self, app):
        html = _render_exam_list(app, [_exam(duration_minutes=45)])
        assert _duration_branch(html) == "45"
        assert "t('45 menit','45 min')" in html, "the card lost its own figure"


# ── 3. the student dashboard ────────────────────────────────────────────────

class TestTheDashboard:
    def test_the_same_paper_does_not_read_zero_minutes(self, app):
        html = _render_dashboard(app, [_exam(duration_minutes=0)])
        assert _duration_branch(html) == "unlimited", (
            "the dashboard prints the raw column, so an unlimited paper reads 0 min"
        )
        assert ">0 min" not in html and "0 min<" not in html

    def test_a_timed_paper_reads_its_own_number(self, app):
        html = _render_dashboard(app, [_exam(duration_minutes=90)])
        assert _duration_branch(html) == "90"
        assert "t('90 menit','90 min')" in html


# ── 4. the builder's own form ───────────────────────────────────────────────

class TestTheBuilderForm:
    def test_an_unlimited_paper_selects_unlimited_not_sixty(self, app):
        html = _render_form(app, _exam(duration_minutes=0))
        assert _selected_minutes(html) == ["0"], (
            "the form marks the 60-minute option selected on a paper the teacher "
            "left unlimited, so the next save silently converts it"
        )

    def test_a_stored_duration_selects_its_own_option(self, app):
        assert _selected_minutes(_render_form(app, _exam(duration_minutes=45))) == ["45"]

    def test_a_paper_stored_without_a_duration_selects_unlimited(self, app):
        """A NULL column reads as unlimited in the arithmetic (`or 0`), so the form
        has to agree: a select with nothing chosen falls back to its first option —
        10 minutes — and the next save writes a duration onto a paper the teacher
        left open-ended."""
        assert _selected_minutes(_render_form(app, _exam(duration_minutes=None))) == ["0"]

    def test_a_new_paper_still_opens_on_the_forms_default_hour(self, app):
        """The fabricated 60 was wrong about a *stored* zero, not about a new paper:
        a teacher opening the form for a new exam still finds 60 minutes chosen."""
        assert _selected_minutes(_render_form(app, None)) == ["60"]


# ── 5. the exam paper consumes the same answer ──────────────────────────────

class TestTheExamPaper:
    def test_the_page_is_told_which_branch_it_is_in(self, app):
        assert "durationUnlimited: true" in _render_paper(app, 0)
        assert "durationUnlimited: false" in _render_paper(app, 45)

    def test_the_terms_line_reads_the_servers_answer(self, app):
        source = TEMPLATES["take_exam"].read_text(encoding="utf-8")
        assert "durationUnlimited ? t('Tak terbatas','Unlimited')" in source, (
            "the duration line still decides for itself what a zero means"
        )
        assert "durationMinutes > 0" not in source, (
            "the rule is re-derived in JavaScript, so it can drift from the server's"
        )


# ── 6. one rule, and no second copy waiting to be written ───────────────────

class TestThereIsOnlyOneRule:
    @pytest.mark.parametrize("name", sorted(TEMPLATES))
    def test_every_reader_consults_the_rule(self, name):
        source = TEMPLATES[name].read_text(encoding="utf-8")
        assert "duration_facts(" in source, (
            f"{name} decides for itself what a duration of zero means"
        )

    @pytest.mark.parametrize("name", sorted(TEMPLATES))
    def test_no_template_fabricates_a_duration_again(self, name):
        source = TEMPLATES[name].read_text(encoding="utf-8")
        found = FABRICATED_FALLBACK.search(source)
        assert not found, (
            f"{name} grew a fallback of minutes again: {found.group(0)!r}"
        )
