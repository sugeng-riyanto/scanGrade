"""The exam edit page: its actions at the top, and the PDF status that told a lie.

Why this file exists
--------------------
Two things about `/teacher/exams/<id>` (edit mode) were wrong from the reader's side,
and neither could be seen by a test that only checked a link *exists*:

* **The Actions card sat in the right-hand column, which in this grid wraps below the
  full-width form.** The page's own navigation was therefore the *last* thing on it —
  behind a builder that runs to three thousand lines. A teacher opening a paper to
  check it before publishing had to scroll the whole form to reach Preview, Results
  or Print LJK, which is exactly the moment they need them.
* **The PDF status bar opened in `empty` on every load**, including a paper that had a
  PDF: beside a drop zone the teacher had used days earlier it said *"No PDF uploaded
  yet"*, and the only way to discover the file was there was to open the pupil's
  preview. The state now comes from the row the route read (`exam.pdf_url`), so a
  stored file arrives as the green checklist it is.

What is asserted here is the *rendered* page, at the position the claim is about: the
bar before the form, the stored file as `data-pdf-status="uploaded"` with the name the
server read off the stored path, and — for the status bar — the Alpine seed itself,
because "the element exists" was already true while the state it opened in was wrong.
"""
from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
FORM = ROOT / "app" / "templates" / "teacher" / "exam_form.html"

STORED_PDF = "schools/sc-1/exams/ex-1/ujian-matematika-2026.pdf"


def _exam(*, pdf_url=None, status="draft", is_published=False) -> dict:
    """An `exam` row complete enough that the builder's embedded JSON renders."""
    return {
        "id": "e1", "title": "UH Matematika", "subject": "MTK",
        "class_ids": ["c1"], "pdf_url": pdf_url, "status": status,
        "is_published": is_published, "duration_minutes": 60, "total_questions": 5,
        "start_at": None, "end_at": None, "question_types": {},
        "question_weights": {}, "answer_key": {}, "question_pages": {},
        "question_audio": {}, "question_cognitive": {},
    }


def _render(app, exam) -> str:
    from flask import g

    with app.test_request_context("/teacher/exams/e1"):
        g.user_id, g.user_name, g.user_role = "u-1", "Uji", "guru"
        g.user_email, g.tz_offset, g.show = "u@example.test", 7, {}
        g.user_school_id, g.user_class_id = "sch-1", "cls-1"
        return app.jinja_env.get_template("teacher/exam_form.html").render(
            exam=exam, subjects=[], classes=[],
            builder_defaults={"subject_id": None, "class_ids": [],
                              "duration_minutes": 60})


@pytest.fixture()
def with_pdf(app) -> str:
    return _render(app, _exam(pdf_url=STORED_PDF, status="active",
                              is_published=True))


@pytest.fixture()
def without_pdf(app) -> str:
    return _render(app, _exam())


# ── 1. the actions are at the top, and the column that hid them is gone ──────

def test_the_actions_bar_is_above_the_form_it_belongs_to(with_pdf):
    """The whole point of the move: the bar comes *before* the paper's own fields."""
    bar = with_pdf.index("data-exam-actions")
    form = with_pdf.index('name="action" value="publish"')
    upload = with_pdf.index('id="pdf-upload"')
    assert bar < form, "the actions are still below the form's publish button"
    assert bar < upload, "the actions are still below the PDF drop zone"


def test_the_top_bar_carries_every_door_the_bottom_card_had(with_pdf):
    """A move that drops one of them is a door removed, not relocated."""
    for href in ("/teacher/preview/e1",
                 "/teacher/export/bubble-sheet/e1",
                 "/teacher/results?exam_id=e1",
                 "/teacher/exams/e1/sessions#room",
                 "/teacher/exams/e1/sessions#observations",
                 "/teacher/exams/e1/upload-pdf"):
        assert f'href="{href}"' in with_pdf, f"the top bar lost {href}"
    assert "deleteExam('e1')" in with_pdf, "the top bar lost the delete action"


def test_the_actions_card_no_longer_sits_in_a_wrapping_column(with_pdf):
    """The column wrapped below the form, which is how the doors ended up last."""
    source = FORM.read_text(encoding="utf-8")
    assert "lg:col-span-1" not in source, (
        "the right-hand column is back; in this grid it wraps below the form")
    assert "sticky top-20 sg-layer-sticky" not in source, (
        "the actions are sticky in a column again")


def test_the_bar_names_the_page_and_the_paper_it_is_editing(with_pdf):
    """It is the *Edit Exam* heading, beside the paper's own state."""
    assert "Edit Ujian" in with_pdf and "Edit Exam" in with_pdf
    assert "UH Matematika" in with_pdf
    assert "active" in with_pdf, "the paper's status is not shown"
    assert "Terlihat murid" in with_pdf and "Visible to pupils" in with_pdf


def test_the_create_page_shows_no_bar_at_all(app):
    """There is no paper yet: no state to report and nothing to navigate to."""
    html = _render(app, None)
    assert "data-exam-actions" not in html
    assert 'data-pdf-status' not in html


# ── 2. the PDF the pupils will read, as the server knows it ─────────────────

def test_a_stored_pdf_is_the_green_checklist_on_arrival(with_pdf):
    assert 'data-pdf-status="uploaded"' in with_pdf
    assert f'data-pdf-name="{STORED_PDF.split("/")[-1]}"' in with_pdf
    assert "ujian-matematika-2026.pdf" in with_pdf, (
        "the uploaded file is not named on the page")
    assert "Sudah diunggah" in with_pdf and "Uploaded" in with_pdf
    assert "fa-check-circle" in with_pdf


def test_the_pdf_line_says_what_it_is(without_pdf):
    """The one sentence that explains the row, in both languages."""
    for html in (without_pdf,):
        assert "PDF yang dilihat murid saat ujian" in html
        assert "The PDF students see during the exam" in html


def test_a_paper_without_a_pdf_shows_the_drop_zone_sentence(without_pdf):
    assert 'data-pdf-status="empty"' in without_pdf
    assert "Seret PDF ke sini atau klik untuk upload" in without_pdf
    assert "Drag a PDF here or click to upload" in without_pdf
    assert "PDF soal ujian — maksimal 20MB" in without_pdf
    assert "Exam PDF — 20MB maximum" in without_pdf
    assert 'data-pdf-status="uploaded"' not in without_pdf


def test_the_empty_states_sentence_lands_on_the_drop_zone(without_pdf):
    """A link to an anchor nothing carries scrolls nowhere."""
    assert 'href="#pdf-upload"' in without_pdf
    assert 'id="pdf-upload"' in without_pdf
    assert without_pdf.index('id="pdf-upload"') > without_pdf.index(
        'data-pdf-status="empty"'), "the drop zone is above the link that points at it"


def test_the_upload_action_wears_the_verb_the_paper_needs(with_pdf, without_pdf):
    """Replace for a paper that has one, upload for a paper that does not."""
    assert "Ganti PDF" in with_pdf and "Replace PDF" in with_pdf
    assert "Unggah PDF" in without_pdf and "Upload PDF" in without_pdf


# ── 3. the builder's own status bar opens in the state the row is in ────────

def test_the_status_bar_is_seeded_from_the_stored_row(with_pdf):
    """The defect: it opened `empty` for a paper whose file was already there."""
    assert 'data-pdf-stored="1"' in with_pdf
    assert "pdfStatus: 'success'" in with_pdf
    assert f"pdfName: '{STORED_PDF.split('/')[-1]}'" in with_pdf


def test_the_status_bar_still_opens_empty_when_there_is_no_file(without_pdf):
    assert 'data-pdf-stored="0"' in without_pdf
    assert "pdfStatus: 'empty'" in without_pdf


def test_the_status_bar_keeps_its_upload_events(with_pdf):
    """Seeding the initial state must not unhook the live upload."""
    for marker in ("pdf-uploaded", "pdf-reset", "pdf-error"):
        assert marker in with_pdf, f"the status bar no longer listens for {marker}"
    assert "pdfUrl: '" in with_pdf, "the seeded pdfUrl is gone"
