"""A scanned sheet can be late too — and the teacher can correct what the clock says.

The online submit route has both ends of a submission in hand: the student's own
`started_at` and the moment the answers arrived are the same session. A scan has
only one of them. `app/utils/exam_window.py:late_arrival` answers it with whichever
clock the paper actually ran on, in this order —

1. the student's own sitting, when the app saw one;
2. failing that, the exam's own `start_at` (for a paper exam, when the papers went
   out);
3. with neither, no deadline at all, so a scan is never late *by itself*.

— and the teacher's own answer wins outright when the request carries one. That
last part is the whole reason this is honest rather than a guess: the recorded
arrival of a scanned paper is the *scan*, so a pile scanned the next morning marks
every sheet in it late — a true statement about the scan and a false one about the
students. Only a person who watched the papers come in can tell those apart, so the
mark is correctable from the results list, in both halves of the table, and the
correction writes one boolean without re-grading anything.

What this file is built to catch, in order of how quietly each fails:

* **a window column missing from the bulk select.** PostgREST reads an absent
  column as absent, not as an error, so a forgotten `end_at` would mean "never
  late" for a whole class with nothing in any log;
* **a later page clearing the flag.** Page 2 of a sheet arriving on time does not
  un-late page 1;
* **the clock overruling the teacher**, in either direction;
* **the mark existing in only one half of the results table**, which is how a
  correction reaches desktop users and not phones.
"""
from __future__ import annotations

import contextlib
import re
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.utils import exam_window

ROOT = Path(__file__).resolve().parents[2]
TEMPLATES = ROOT / "app" / "templates"
API = (ROOT / "app" / "routes" / "api.py").read_text(encoding="utf-8")
TEACHER = (ROOT / "app" / "routes" / "teacher.py").read_text(encoding="utf-8")
SCAN = (TEMPLATES / "teacher" / "scan.html").read_text(encoding="utf-8")
TABLE = (TEMPLATES / "teacher" / "_results_table.html").read_text(encoding="utf-8")

UTC = timezone.utc
_COMMENT = re.compile(r"\{#.*?#\}|<!--.*?-->|(?:^|\s)#[^\n]*", re.S)


def code(text: str) -> str:
    """Source with its comments removed, so a guard never fires on prose."""
    return _COMMENT.sub("", text)


def _src_route(text: str, name: str) -> str:
    """One route's source: from its `def` to the next top-level `def`/`@route`."""
    start = text.index(f"def {name}(")
    rest = text[start:]
    ends = [m.start() for m in re.finditer(r"\n(?:def |@\w+_bp\.route)", rest)]
    return rest[:min(ends)] if ends else rest


def at(hour: int, minute: int = 0, second: int = 0) -> datetime:
    return datetime(2026, 9, 19, hour, minute, second, tzinfo=UTC)


#: A paper exam: the papers went out at 08:00, the sitting is 60 minutes, and the
#: duration clock is the one that applies (no hard window end).
PAPER = {"start_at": "2026-09-19T08:00:00+00:00", "end_at": None,
         "duration_minutes": 60, "auto_submit_on_window_end": False}

#: A window-ended exam: everyone stops at 10:00, whenever they began.
WINDOW = {"start_at": "2026-09-19T08:00:00+00:00", "end_at": "2026-09-19T10:00:00+00:00",
          "duration_minutes": 0, "auto_submit_on_window_end": True}

#: Nothing enforces an end: unlimited, no window.
OPEN_ENDED = {"start_at": None, "end_at": None, "duration_minutes": 0,
              "auto_submit_on_window_end": False}


# ── the rule ─────────────────────────────────────────────────────────────────

class TestTheClockAStringOfPapersRunsOn:
    def test_a_scan_before_the_clock_runs_out_is_not_late(self):
        assert exam_window.late_arrival(PAPER, None, at(8, 50)) is False

    def test_the_grace_holds_for_a_scan_at_the_bell(self):
        """The 2-minute grace is shared with the online route, not re-invented."""
        assert exam_window.late_arrival(PAPER, None, at(9, 0, 30)) is False
        assert exam_window.late_arrival(PAPER, None, at(9, 2, 30)) is True

    def test_a_pile_scanned_after_the_paper_is_over_is_late(self):
        assert exam_window.late_arrival(PAPER, None, at(9, 30)) is True

    def test_the_students_own_sitting_beats_the_exams_start(self):
        """A pupil who began at 09:30 (late in the window) has until 10:30, and the
        class clock must not overwrite that: `started_at`, when it exists, is the
        only one of the two that is about this student."""
        started = "2026-09-19T09:30:00+00:00"
        assert exam_window.late_arrival(PAPER, started, at(9, 45)) is False
        assert exam_window.late_arrival(PAPER, started, at(10, 40)) is True

    def test_a_hard_window_end_applies_whatever_the_arrival_is(self):
        assert exam_window.late_arrival(WINDOW, None, at(9, 55)) is False
        assert exam_window.late_arrival(WINDOW, None, at(10, 5)) is True
        # Even for a pupil who began on the app: the shared window is the rule.
        assert exam_window.late_arrival(WINDOW, "2026-09-19T09:50:00+00:00",
                                        at(10, 5)) is True

    def test_an_exam_with_no_clock_never_marks_a_scan_late(self):
        """Nothing enforces an end, so nothing can be past it. A guess here would be
        the app inventing a deadline the school never set."""
        assert exam_window.late_arrival(OPEN_ENDED, None, at(23, 59)) is False


class TestTheTeachersWordWins:
    def test_a_tick_marks_a_late_arrival_the_clock_cannot_see(self):
        """Untimed exam, no sitting, nothing to compare — and the teacher watched
        the sheet come in after time was called."""
        assert exam_window.late_arrival(OPEN_ENDED, None, at(10, 0), True) is True

    def test_an_explicit_no_clears_a_clock_that_says_yes(self):
        """The correction direction: the pile was scanned the next morning, so the
        recorded time is the scan and not the hand-in."""
        assert exam_window.late_arrival(PAPER, None, at(9, 30), False) is False
        assert exam_window.late_arrival(WINDOW, None, at(10, 30), False) is False

    def test_the_payload_can_say_local_time_and_mean_it(self):
        """The form's own conversion is unchanged, and the scan route must not
        grow a second one: `late_arrival` reads what the exam row already holds."""
        assert exam_window.to_utc_iso("2026-09-19T10:00") == "2026-09-19T03:00:00"


# ── the two scan routes ──────────────────────────────────────────────────────

class TestBothScanRoutesRecordIt:
    def test_the_single_sheet_save_computes_and_stores_the_flag(self):
        route = code(_src_route(API, "scan_save"))
        assert "exam_window.late_arrival(" in route, (
            "the single-sheet save does not ask the one module that decides this")
        assert '"submitted_late": late' in route, (
            "the computed mark is not written to the submission")

    def test_the_single_sheet_save_reads_the_rows_own_sitting_and_mark(self):
        """`late_arrival(exam, None, …)` is syntactically identical and silently
        answers off the *class* clock, so the argument has to be named, not implied:
        the stored row is the only place the pupil's own sitting exists."""
        route = code(_src_route(API, "scan_save"))
        assert "late_arrival(exam, sitting_started, arrived_at, stated_late)" in route, (
            "the single-sheet mark is computed without the paper's own sitting")
        assert 'sitting_started = existing[0].get("started_at")' in route, (
            "the sitting does not come from the row that is being saved")
        assert 'stored_late = bool(existing[0].get("submitted_late"))' in route, (
            "the existing mark is not read from the row, so page 2 clears page 1")

    def test_the_bulk_save_computes_and_stores_the_flag(self):
        route = code(_src_route(API, "scan_bulk_save"))
        assert "exam_window.late_arrival(" in route
        assert '"submitted_late": late' in route

    @pytest.mark.parametrize("column", ["start_at", "end_at", "duration_minutes",
                                        "auto_submit_on_window_end"])
    def test_the_bulk_select_names_every_window_column(self, column):
        """A column left out of a select list reads as *absent*, never as an error,
        so a forgotten one means a whole class is silently never late."""
        route = code(_src_route(API, "scan_bulk_save"))
        select = route[route.index('table("exams")'):route.index(".single()")]
        assert column in select, f"{column} is not in the exams select of bulk-save"

    def test_the_bulk_select_names_the_stored_sitting_and_flag(self):
        route = code(_src_route(API, "scan_bulk_save"))
        i = route.index('table("submissions")')
        select = route[i:i + 400]
        assert "started_at" in select and "submitted_late" in select, (
            "the existing row is read without the two columns this decision needs")

    def test_a_second_page_never_clears_the_mark(self):
        """Page 2 arriving on time does not un-late page 1: the paper's answers
        arrived late once, and only a teacher can say otherwise."""
        for name in ("scan_save", "scan_bulk_save"):
            route = code(_src_route(API, name))
            assert "stated_late is None and stored_late" in route, (
                f"{name} lets a later page's arrival time clear the mark")

    def test_the_teacher_can_still_correct_an_already_graded_sheet(self):
        """The answers of an already-graded sheet are refused as a rewrite — but a
        late mark the teacher states is a correction to the record, not a rewrite of
        the paper, so it must go through."""
        route = code(_src_route(API, "scan_save"))
        blocked = route.index('if current_status in ("graded", "published") and not adds_new')
        branch = route[blocked:blocked + 700]
        assert "late_updated" in branch and '"submitted_late": bool(stated_late)' in branch, (
            "an already-graded sheet cannot have its mark corrected from the scan screen")
        # …and the branch has to be *reachable*: the two strings above survive
        # inside a body that nothing can enter, which is exactly how a correction
        # that was written stops working.
        assert "if stated_late is not None and bool(stated_late) != stored_late:" in branch, (
            "the correction branch is never taken")

    def test_only_a_stated_value_overwrites_a_stored_one(self):
        for name in ("scan_save", "scan_bulk_save"):
            route = code(_src_route(API, name))
            assert "sub.get(\"late\")" in route or "data.get(\"late\")" in route, (
                f"{name} never reads the teacher's own answer from the request")

    def test_the_online_route_is_untouched(self):
        """It has both ends of its own submission, so it keeps asking `is_late`
        directly. Routing it through `late_arrival` would let a teacher's mark
        override a session that was actually timed."""
        student = (ROOT / "app" / "routes" / "student.py").read_text(encoding="utf-8")
        assert "exam_window.is_late(" in student
        assert "late_arrival(" not in student


# ── the correction surface ───────────────────────────────────────────────────

@contextlib.contextmanager
def _signed_in(app, path):
    from flask import g
    with app.test_request_context(path):
        g.user_id = "tea-1"
        g.user_name = "Guru Uji"
        g.user_email = "guru@example.test"
        g.user_role = "guru"
        g.tz_offset = 7
        g.show = {}
        yield


def _single_payload() -> str:
    """The body of the single-sheet save's `fetch`, and nothing after it."""
    return SCAN[SCAN.index("await fetch('/api/scan/save'"):
                SCAN.index("// Check if redirected to login page")]


def _bulk_payload() -> str:
    """The rows the bulk save assembles, and nothing after them."""
    return SCAN[SCAN.index("// Bulk save all"):
                SCAN.index("function drawDetectionOverlay")]


def _row(name, late):
    return {
        "id": f"sub-{name}", "student_id": f"stu-{name}", "student_name": name,
        "status": "graded", "score": 80, "final_score": 76, "penalty": 0,
        "submitted_at": "2026-09-19T02:00:00+00:00",
        "started_at": "2026-09-19T01:00:00+00:00",
        "submitted_late": late, "answers": {"_nisn": "1234"},
    }


def _render_table(app, rows):
    with _signed_in(app, "/teacher/results"):
        return app.jinja_env.get_template("teacher/_results_table.html").render(
            sub_list=rows, is_scan_section=False)


class TestTheResultsListCorrectsIt:
    def test_the_correction_posts_the_opposite_of_what_is_stored(self, app):
        """One button that marks and unmarks, in both halves of the table — the
        count is the assertion, because a control added to the desktop rows alone is
        a control phones do not have."""
        html = _render_table(app, [_row("Ani", True), _row("Budi", False)])
        mark = re.findall(r'action="/teacher/submission/([^/]+)/late"[^>]*>\s*'
                          r'<input type="hidden" name="late" value="(\d)"', html)
        # One control per row *per half*: the mobile card and the desktop row are
        # separate markup, so a control added to one of them is one half the
        # teachers never get — the same equality the late badge itself is held to.
        assert len(mark) == 4, f"expected two controls per row, found {mark}"
        assert mark.count(("sub-Ani", "0")) == 2, mark
        assert mark.count(("sub-Budi", "1")) == 2, mark

    def test_the_pages_own_language_still_switches(self, app):
        """A literal here would be the one string on the row that does not follow
        the reader, and the i18n gate counts it as debt."""
        html = _render_table(app, [_row("Ani", True)])
        assert "t('Hapus tanda terlambat" in html or "Hapus tanda terlambat" in html
        assert "&#39;" not in html, (
            "an apostrophe entity inside a t() pair does not survive HTML decoding")

    def test_the_route_guards_the_exam_rather_than_trusting_the_id(self):
        route = code(_src_route(TEACHER, "submission_late"))
        assert "_guard_exam(" in route, (
            "a submission id says nothing about who may write to it")
        assert 'update({"submitted_late": late})' in route, (
            "the correction must write the one column, not the whole row")

    def test_the_correction_does_not_touch_the_answers_or_the_status(self):
        route = code(_src_route(TEACHER, "submission_late"))
        body = route[route.index("update({"):]
        for column in ("answers", "score", "final_score", "status"):
            assert column not in body.split("execute()")[0], (
                f"correcting a late mark rewrites {column}")


class TestTheScanScreenAsks:
    def test_the_box_exists_in_both_flows(self):
        assert 'id="late-check"' in SCAN, "the single-sheet flow has no box to tick"
        bulk_render = SCAN[SCAN.index("function renderBulkResults"):
                           SCAN.index("// Bulk save all")]
        assert "late-check" in bulk_render, "the bulk table has no per-row box"

    def test_both_payloads_carry_the_teachers_answer(self):
        # Sliced around each *request*, which is where the payload is built: the
        # single save's body is what its `fetch` sends, and the bulk save assembles
        # its rows before it. Sliced wider this passes for the wrong reason — the
        # single save's slice used to run to the *bulk* request and pick up the
        # response-handling `data.late` on the way.
        assert "late: document.getElementById('late-check')" in _single_payload(), (
            "the single-sheet save never sends the tick")
        assert "late: sel.closest('tr')?.querySelector('.late-check')" in _bulk_payload(), (
            "the bulk save never sends the ticked rows")

    def test_an_untouched_box_sends_nothing(self):
        """`undefined` is dropped by JSON.stringify, and absent means "let the
        clock decide" — a box that always posted false would be an assertion that
        every paper was on time.

        Both payloads, counted: one site fixed and the other left asserting would
        still find `? true : undefined` somewhere in the file.
        """
        for name, body in (("single", _single_payload()), ("bulk", _bulk_payload())):
            assert body.count("? true : undefined") == 1, (
                f"the {name} save does not send an untouched box as absent")

    def test_the_tick_is_cleared_where_the_save_appears(self):
        """The box is shown with the save button, so it has to be unticked there
        too: a tick left over from the last paper rides on the next pupil's save,
        and that mark is not one anybody can explain afterwards."""
        shown = SCAN[SCAN.index("renderAnswersTable(lastAnswers"):
                     SCAN.index("sessionStorage.setItem('sg_scan_result',")]
        assert "late-check').checked = false" in shown, (
            "the tick survives from the previous sheet")

    def test_the_box_is_cleared_for_the_next_sheet(self):
        reset = SCAN[SCAN.index("function resetScan()"):]
        reset = reset[:reset.index("\n}")]
        assert "late-check').checked = false" in reset, (
            "the tick follows the teacher to the next student's paper")
