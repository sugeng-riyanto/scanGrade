"""The exams list was printing UTC to teachers who read their clock in WIB.

`start_at` is stored in UTC — every writer in the app is explicit about that
(`datetime.now(timezone.utc).isoformat()`) — and the list card rendered it with a
plain slice, `{{ exam.start_at[:16] }}`. So a paper set to open at 07:50 WIB was
shown to the teacher who set it as ``2026-10-06T00:50``: the seven-hour hole between
what the school's clock said and what the page said, on the list they re-read every
day. The builder form had already been formatting its two clocks through the `tz`
filter (`g.tz_offset`), which is why the bug lived on the *list* and not in the form.

Two guards, because they catch different regressions:

* a **render** guard — a reader at +7 sees 07:50, a reader at 0 sees 00:50, from the
  same row, which is the whole claim of the filter;
* a **source** guard — the raw slice is gone, so a later edit that "simplifies" the
  filter back to `[:16]` cannot quietly reintroduce the offset hole.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EXAMS = ROOT / "app" / "templates" / "teacher" / "exams.html"


def _timed_exam(**over) -> dict:
    """One exam card's worth of a row, with a start the two offsets disagree on."""
    row = {
        "id": "e1", "title": "UH Fisika", "subject": "FIS", "status": "active",
        "duration_minutes": 60, "total_questions": 5, "submission_count": 0,
        "is_published": True, "is_template": False, "class_ids": ["c1"],
        "start_at": "2026-10-06T00:50:00+00:00",
    }
    row.update(over)
    return row


def _render(app, offset: int) -> str:
    from flask import g

    with app.test_request_context("/teacher/exams"):
        g.user_id, g.user_name, g.user_role = "u-1", "Uji", "guru"
        g.user_email, g.tz_offset, g.show = "u@example.test", offset, {}
        g.user_school_id, g.user_class_id = "sch-1", "cls-1"
        return app.jinja_env.get_template("teacher/exams.html").render(
            exams=[_timed_exam()], unassigned_ids=set())


def test_a_wib_reader_sees_the_start_in_wib(app):
    """The exact complaint: 00:50 UTC is 07:50 in the school's own clock."""
    page = _render(app, 7)
    assert "2026-10-06 07:50" in page, "the card is not showing the WIB start"
    assert "2026-10-06T00:50" not in page and "2026-10-06 00:50" not in page, (
        "the card still reads as UTC — the seven-hour hole is back")


def test_a_utc_reader_still_sees_utc(app):
    """The filter converts to the *reader's* offset; it is not a hard-coded +7."""
    assert "2026-10-06 00:50" in _render(app, 0)


def test_the_card_does_not_slice_the_utc_timestamp_raw():
    """A guard against re-deriving the bug: `[:16]` on a UTC string *is* the bug."""
    text = EXAMS.read_text(encoding="utf-8")
    assert "exam.start_at[:16]" not in text, (
        "the start is sliced raw again, so it prints UTC to a WIB teacher")
    assert "exam.start_at | tz(" in text, (
        "the card no longer formats the start through the reader's own offset")
