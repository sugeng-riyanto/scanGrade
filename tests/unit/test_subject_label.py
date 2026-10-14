"""`Mata Pelajaran: None` — the printed subject, and the reason it was a word.

The defect
----------
`exams.subject` is a legacy `TEXT` column. The current builder stores
`subject_id` (an FK to `subjects`) and never writes the old column, so a paper
created today carries a ``NULL`` subject. Five documents printed it with

    {{ exam.get('subject', '-') }}

and `dict.get`'s default applies only when the *key is absent* — the key is
present with a ``None`` value, so Python's ``None`` reached the page as the
literal word ``None`` beside "Mata Pelajaran". (The bug is reproduced first,
below, against the raw dict, so the fix is measured against the mechanism and
not against a description of it.)

One resolver now answers it — `app.services.subject_lookup` — so the exam sheet,
the report card, the pupil's screen result and the pupil's PDF cannot disagree.
It fills the real name from `subject_id` when it has to, and otherwise prints an
honest fallback rather than a dash or a null.
"""
from pathlib import Path

import pytest

from app.services import subject_lookup
from app.services.subject_lookup import FALLBACK_ID, annotate, display_name, label, names_for

ROOT = Path(__file__).resolve().parents[2]
TEMPLATES = ROOT / "app" / "templates"


# ── the mechanism, reproduced ────────────────────────────────────────────────

def test_the_old_idiom_is_what_printed_none():
    """`get(key, default)` does not cover a present-but-null value.

    This is the bug, not a stand-in for it: the default only fires on an absent
    key, and both halves of the answer are absent here — no text, no resolved
    name — so the old expression handed ``None`` to Jinja and Jinja painted it.
    """
    exam = {"subject": None}
    assert exam.get("subject", "-") is None, "the default does not cover a null value"
    assert str(exam.get("subject", "-")) == "None", "and Jinja renders that as the word"


# ── the resolver ─────────────────────────────────────────────────────────────

def test_display_name_never_returns_none():
    assert display_name({"subject": None}) == ""
    assert display_name({"subject": "  Matematika  "}) == "Matematika"
    assert display_name({"subject_name": "IPA", "subject": None}) == "IPA"
    assert display_name({}) == ""
    assert display_name(None) == ""


def test_label_falls_back_honestly_instead_of_a_dash():
    assert label({"subject": None}) == FALLBACK_ID
    assert label({}) == FALLBACK_ID
    assert label({"subject": "Biologi"}) == "Biologi"
    assert label({"subject": None}, fallback="Subject not set") == "Subject not set"


def test_names_for_resolves_subject_id_in_one_read():
    class _Resp:
        def __init__(self, data):
            self.data = data

    class _Query:
        def __init__(self, rows):
            self._rows = rows
        def select(self, *a, **k): return self
        def in_(self, column, values):
            self._values = {str(v) for v in values}
            return self
        def execute(self):
            return _Resp([r for r in self._rows if str(r["id"]) in self._values])

    class _Client:
        def __init__(self, rows):
            self._rows = rows
        def table(self, name):
            assert name == "subjects"
            return _Query(self._rows)

    exams = [{"id": "e1", "subject": None, "subject_id": "s1"},
             {"id": "e2", "subject": "Fisika", "subject_id": "s2"}]
    names = names_for(_Client([{"id": "s1", "name": "Kimia"}]), exams)
    assert names == {"e1": "Kimia", "e2": "Fisika"}


def test_annotate_fills_the_row_it_is_handed():
    class _Client:
        def table(self, name):
            class _Q:
                def select(self, *a, **k): return self
                def in_(self, *a, **k): return self
                def execute(self):
                    class _R: data = [{"id": "s9", "name": "Sejarah"}]
                    return _R()
            return _Q()

    exam = {"id": "e9", "subject": None, "subject_id": "s9"}
    annotate(_Client(), exam)
    assert exam["subject_name"] == "Sejarah"
    assert label(exam) == "Sejarah"


# ── the surfaces that printed it ─────────────────────────────────────────────

#: Every template that rendered the subject, and the one line each carried.
PRINTED = (
    "teacher/print_exam_report.html",
    "print/report_card.html",
    "student/result_detail.html",
    "student/result_detail_pdf.html",
)


def test_no_template_prints_the_buggy_idiom():
    for rel in PRINTED:
        source = (TEMPLATES / rel).read_text(encoding="utf-8")
        assert "exam.get('subject'" not in source, (
            f"{rel} still reads the legacy subject text column directly")
        assert "subject_label(exam" in source or "subject_name(exam" in source, (
            f"{rel} no longer prints the subject at all")


EXAM = {
    "id": "e1", "title": "Ujian Matematika", "subject": None, "subject_id": "s1",
    "answer_key": {}, "question_types": {}, "question_weights": {},
    "total_questions": 0, "pdf_page_urls": [], "question_scoring": {},
}


def test_the_exam_sheet_shows_the_name_or_an_honest_fallback(app):
    """Rendered, not grepped: a null subject must not become the word `None`."""

    def render(exam):
        return app.jinja_env.get_template("teacher/print_exam_report.html").render(
            exam=exam, roster=[], stats={"count": 0, "avg": 0.0, "max": 0.0,
                                         "min": 0.0, "passed": 0, "pass_rate": 0,
                                         "threshold": 70},
            class_names=[], school={}, teacher_name="", printed_on="now", pass_mark=70)

    blank = render(dict(EXAM))
    assert "None" not in blank, "the word `None` reached the sheet"
    assert FALLBACK_ID in blank, "no honest fallback where the subject would be"

    named = render({**EXAM, "subject_name": "Matematika"})
    assert "Matematika" in named and FALLBACK_ID not in named


def test_the_report_card_shows_the_name_or_an_honest_fallback(app):
    def render(exam):
        card = {"submission": {"answers": {}, "teacher_feedback": {}, "exam": exam,
                               "status": "published", "is_published": True,
                               "final_score": 55.5, "score": 60, "penalty": 4.5,
                               "violations": 2},
                "exam": exam, "released": True, "student_name": "Ahmad",
                "student_nisn": "1", "student_nis": "1", "teacher_name": "",
                "school": {"name": "SMP 1"}}
        return app.jinja_env.get_template("print/report_card.html").render(
            cards=[card], printed_on="now", show_key=False)

    blank = render(dict(EXAM))
    assert "None" not in blank
    assert FALLBACK_ID in blank

    named = render({**EXAM, "subject_name": "Kimia"})
    assert "Kimia" in named and FALLBACK_ID not in named


def _render_pupil_pdf(app, exam):
    """The pupil's own PDF sheet, as this file renders it.

    Module-level rather than nested, so `deploy/render_fixture_gate.py` measures the
    same call this file does — a second copy of the context is the drift that gate
    exists to catch, and a fixture it cannot reach is a document it cannot measure.
    """
    # `submitted_at` is what the sheet prints under `Dikumpulkan`; it is a column the
    # route's submission row carries, and a fixture without it printed `-` silently
    # (see `deploy/render_fixture_gate.py`).
    return app.jinja_env.get_template("student/result_detail_pdf.html").render(
        submission={"exam": exam, "answers": {}, "teacher_feedback": {},
                    "status": "published", "submitted_at": "2026-09-13T11:00:00+00:00",
                    "final_score": 70, "score": 75,
                    "penalty": 5, "violations": 1},
        student_name="Ahmad", teacher_name="", school_info={}, merged_pages={})


def test_the_pupil_pdf_shows_the_name_or_an_honest_fallback(app):
    def render(exam):
        return _render_pupil_pdf(app, exam)

    blank = render(dict(EXAM))
    assert "None" not in blank
    assert FALLBACK_ID in blank

    named = render({**EXAM, "subject_name": "Biologi"})
    assert "Biologi" in named and FALLBACK_ID not in named
