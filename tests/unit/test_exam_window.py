"""The assignment window, the duration, and which clock ends a sitting.

A teacher setting an exam has two time settings that look alike and are not:

* **Duration** — counted from the moment *this* student starts, and allowed to run
  past the assignment window end (the form says so on the field).
* **Assignment window end** — the last instant a student may *begin*, and, when
  "Auto-Submit on Assignment Window End" is on, also the instant the sitting ends
  whatever time it started.

`app/utils/exam_window.py` answers both, so the countdown a student sees, the door
that admits them, the sync API that enforces the timer and the submit route that
records a late paper cannot disagree. These tests pin the arithmetic and the two
places it is applied — the student's list and the teacher's form.

The form half is here because of a defect it found: the "Question settings" card
opened its own Alpine scope with both shuffle toggles hard-coded `true`, which
shadowed the exam's stored values. An exam saved with shuffling OFF rendered ON,
and the next save wrote `true` back. One of the tests below fails on that shape: a
checkbox the page posts must be a field the routes read, or the teacher's toggle
does nothing at all.
"""
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.utils import exam_window

ROOT = Path(__file__).resolve().parents[2]
FORM = (ROOT / "app" / "templates" / "teacher" / "exam_form.html").read_text(encoding="utf-8")
TEACHER = (ROOT / "app" / "routes" / "teacher.py").read_text(encoding="utf-8")

NOW = datetime(2026, 9, 19, 12, 0, tzinfo=timezone.utc)


def exam(**kw):
    row = {"duration_minutes": 60, "auto_submit_on_window_end": False}
    row.update(kw)
    return row


# ── the window: when a student may begin ────────────────────────────────────

class TestTheWindowGovernsBeginning:
    def test_an_exam_with_no_dates_is_open(self):
        assert exam_window.window_state(exam()) == exam_window.OPEN

    def test_before_the_start_is_not_open_and_not_late(self):
        row = exam(start_at=(NOW + timedelta(hours=1)).isoformat())
        assert exam_window.window_state(row, NOW) == exam_window.BEFORE
        assert exam_window.may_begin(row, NOW) == (False, "before_start")

    def test_after_the_end_is_closed(self):
        row = exam(end_at=(NOW - timedelta(minutes=1)).isoformat())
        assert exam_window.window_state(row, NOW) == exam_window.CLOSED
        assert exam_window.may_begin(row, NOW) == (False, "window_closed")

    def test_the_two_ends_are_a_window_not_two_switches(self):
        row = exam(start_at=(NOW - timedelta(hours=1)).isoformat(),
                   end_at=(NOW + timedelta(hours=1)).isoformat())
        assert exam_window.window_state(row, NOW) == exam_window.OPEN
        assert exam_window.may_begin(row, NOW) == (True, "open")

    def test_a_sitting_started_inside_the_window_survives_its_close(self):
        """The rule the student door implements: closed refuses a *new* start, and
        admits a student who already began, because the sitting they hold is what
        the deadline clock governs."""
        row = exam(start_at=(NOW - timedelta(hours=3)).isoformat(),
                   end_at=(NOW - timedelta(minutes=5)).isoformat())
        assert exam_window.window_state(row, NOW) == exam_window.CLOSED
        assert exam_window.window_state(row, NOW - timedelta(minutes=10)) != exam_window.CLOSED


# ── the duration: how long that student has ─────────────────────────────────

class TestTheDeadline:
    started = NOW

    def test_without_the_switch_the_duration_runs_from_the_students_own_start(self):
        row = exam(duration_minutes=90, end_at=(NOW + timedelta(minutes=30)).isoformat())
        assert exam_window.deadline(row, self.started) == NOW + timedelta(minutes=90)

    def test_without_the_switch_the_duration_may_extend_past_the_window_end(self):
        """What the Duration field has always said in as many words."""
        row = exam(duration_minutes=120, end_at=(NOW + timedelta(minutes=10)).isoformat())
        assert exam_window.deadline(row, self.started) > exam_window.parse_dt(row["end_at"])

    def test_with_the_switch_the_window_end_caps_a_long_duration(self):
        row = exam(duration_minutes=120, auto_submit_on_window_end=True,
                   end_at=(NOW + timedelta(minutes=10)).isoformat())
        assert exam_window.deadline(row, self.started) == NOW + timedelta(minutes=10)
        assert exam_window.deadline_reason(row, self.started) == exam_window.DEADLINE_WINDOW_END

    def test_with_the_switch_a_short_duration_still_wins(self):
        """\"Regardless of when they start\" cannot mean \"longer than the duration\":
        the earlier of the two clocks is the one that ends a paper."""
        row = exam(duration_minutes=15, auto_submit_on_window_end=True,
                   end_at=(NOW + timedelta(hours=4)).isoformat())
        assert exam_window.deadline(row, self.started) == NOW + timedelta(minutes=15)
        assert exam_window.deadline_reason(row, self.started) == exam_window.DEADLINE_DURATION

    def test_unlimited_plus_the_switch_is_still_bounded_by_the_window(self):
        row = exam(duration_minutes=0, auto_submit_on_window_end=True,
                   end_at=(NOW + timedelta(hours=2)).isoformat())
        assert exam_window.deadline(row, self.started) == NOW + timedelta(hours=2)

    def test_unlimited_without_the_switch_has_no_deadline(self):
        assert exam_window.deadline(exam(duration_minutes=0), self.started) is None

    def test_a_sitting_with_no_start_has_no_deadline(self):
        assert exam_window.deadline(exam(duration_minutes=60), None) is None

    def test_seconds_left_counts_down_and_stops_at_zero(self):
        row = exam(duration_minutes=30)
        assert exam_window.seconds_left(row, self.started, NOW) == 1800
        assert exam_window.seconds_left(row, self.started, NOW + timedelta(minutes=31)) == 0

    def test_late_is_measured_against_the_deadline_with_the_grace(self):
        row = exam(duration_minutes=30)
        inside = NOW + timedelta(minutes=30, seconds=exam_window.LATE_GRACE_SECONDS)
        beyond = inside + timedelta(seconds=1)
        assert exam_window.is_late(row, self.started, inside) is False
        assert exam_window.is_late(row, self.started, beyond) is True

    def test_a_paper_with_no_deadline_can_never_be_late(self):
        assert exam_window.is_late(exam(duration_minutes=0), self.started, NOW) is False


# ── the form value, on the right clock ──────────────────────────────────────

class TestTheFormTimestamp:
    def test_a_jakarta_datetime_becomes_utc(self):
        """The form has no timezone: a teacher types 09:00 and means 09:00 there.
        Both ends of the window go through this one function, because the one
        mistake here moves a deadline by seven hours without looking wrong."""
        assert exam_window.to_utc_iso("2026-09-19T09:00", 7) == "2026-09-19T02:00:00"

    def test_an_empty_field_is_no_boundary(self):
        assert exam_window.to_utc_iso("", 7) is None
        assert exam_window.to_utc_iso(None, 7) is None

    def test_the_two_ends_land_on_the_same_clock(self):
        start = exam_window.to_utc_iso("2026-09-19T08:00", 7)
        end = exam_window.to_utc_iso("2026-09-19T09:30", 7)
        assert exam_window.parse_dt(end) - exam_window.parse_dt(start) == timedelta(minutes=90)


class TestAFactsObjectForThePage:
    def test_it_carries_the_deadline_the_reason_and_the_window(self):
        row = exam(duration_minutes=45, auto_submit_on_window_end=True,
                   end_at=(NOW + timedelta(minutes=90)).isoformat())
        facts = exam_window.page_facts(row, NOW, NOW)
        assert facts["reason"] == exam_window.DEADLINE_DURATION
        assert facts["seconds_left"] == 2700
        assert facts["deadline_iso"] == (NOW + timedelta(minutes=45)).isoformat()
        assert facts["window_end_iso"] == row["end_at"]

    def test_no_deadline_is_reported_as_no_deadline(self):
        facts = exam_window.page_facts(exam(duration_minutes=0), NOW, NOW)
        assert facts["deadline_iso"] is None and facts["seconds_left"] is None


# ── where the rules are applied ─────────────────────────────────────────────

class TestWhichExamsTheStudentIsOffered:
    @staticmethod
    def offerable(row, class_id="X-A", drafts=()):
        from app.routes.student import _offerable
        return _offerable(row, class_id, set(drafts), NOW)

    def test_an_open_assigned_exam_is_offered(self):
        assert self.offerable(exam(id="e1", class_ids=["X-A"])) is True

    def test_an_exam_whose_window_has_not_opened_is_not_offered(self):
        row = exam(id="e1", class_ids=["X-A"], start_at=(NOW + timedelta(hours=1)).isoformat())
        assert self.offerable(row) is False

    def test_an_exam_whose_window_has_closed_is_not_offered(self):
        row = exam(id="e1", class_ids=["X-A"], end_at=(NOW - timedelta(minutes=1)).isoformat())
        assert self.offerable(row) is False

    def test_but_a_sitting_already_begun_stays_on_the_list_after_it_closes(self):
        row = exam(id="e1", class_ids=["X-A"], end_at=(NOW - timedelta(minutes=1)).isoformat())
        assert self.offerable(row, drafts={"e1"}) is True

    def test_an_exam_assigned_to_another_class_is_still_refused(self):
        row = exam(id="e1", class_ids=["X-B"])
        assert self.offerable(row) is False


# ── the teacher's side: the switches have to be read ────────────────────────

class TestTheFormAndTheRoutesAgree:
    def posted_toggles(self) -> set:
        """Every checkbox the form can post, by `name`."""
        return set(re.findall(r'<input type="checkbox"[^>]*name="([a-z_]+)"', FORM))

    def test_the_window_end_field_exists_on_the_form(self):
        assert 'name="end_at"' in FORM
        assert 'name="auto_submit_on_window_end"' in FORM

    def test_every_checkbox_the_form_posts_is_a_field_the_routes_read(self):
        """"Checklist and unchecklist work" is exactly this: a box the page posts and
        the handler never reads is a toggle that does nothing. Unchecked boxes post
        nothing at all, so the route's default decides the off state."""
        missing = sorted(n for n in self.posted_toggles()
                         if f'request.form.get("{n}"' not in TEACHER
                         and f'request.form.getlist("{n}"' not in TEACHER)
        assert not missing, (
            f"the form posts {missing}, which no teacher route reads — the toggle "
            f"would have no effect")

    def test_the_question_settings_card_no_longer_hard_codes_its_toggles(self):
        """The bug this file was written for: a nested `x-data` with `true` shadowed
        the stored values, so an exam saved with shuffling OFF rendered ON and the
        next save wrote `true` back."""
        assert 'x-data="{ randomize_questions: true, randomize_options: true }"' not in FORM

    def test_and_seeds_both_from_the_exam_row(self):
        assert "randomize_questions: {{ 'true' if" in FORM
        assert "randomize_options: {{ 'true' if" in FORM

    def test_both_ends_of_the_window_are_required_to_be_ordered(self):
        assert "Batas akhir ujian harus setelah waktu mulai" in TEACHER

    def test_the_new_fields_are_written_by_both_the_create_and_the_update_route(self):
        assert TEACHER.count('"auto_submit_on_window_end": auto_submit_on_window_end') == 2, (
            "one of the create/update handlers does not store the window switch")
        assert TEACHER.count('"end_at": end_at,') == 2
