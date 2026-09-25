"""The exam's end is the server's decision, not the browser's.

Today the only thing that ends a sitting is the exam page's own countdown:
`if (this.timeLeft <= 0) this.submitExam(true)` (`take_exam.html`). That is a
picture of the deadline, not the deadline. A student who closes the laptop at
minute 59, whose phone dies, whose tab is discarded by the browser, or who simply
never presses Send leaves the row in `draft` — and a `draft` is not a submission:
it is absent from the teacher's results list, absent from the analysis, and absent
from the exam's statistics, forever. Nobody chose that; the clock ran out in a
browser that was no longer running.

So the deadline gets one enforcement point on the server, and this file pins what
it has to be:

1. **The arithmetic is the app's, not a second copy of it.** `exam_window.deadline`
   already answers "when does this sitting end", from both clocks — the duration
   counted from the student's own `started_at` and the assignment window end when
   the exam is set to stop there. A sweep that recomputes the arithmetic is a
   second answer to the same question, and the two drift the first time a teacher
   edits a window.
2. **The closing write is the app's one writer.** `submission_service.finish_sitting`
   owns the `submissions` row — the unique constraint counts every status, so
   deciding between PATCH and POST is exactly the thing that used to 500 a student
   who tried to submit. A sweep that writes the row itself reintroduces that.
3. **The grace is one number.** A paper arriving within
   `exam_window.LATE_GRACE_SECONDS` of the deadline is *accepted* — that is what
   the grace means — so the sweep must not close that sitting yet, or a student
   whose countdown hits zero would race the box for their own answers. The two
   halves of "accepted" and "not yet closed" read the same constant.
4. **A paper the clock closed was not late.** "Late" means the answers arrived
   after the deadline (plus the grace). Here they arrived before it and the sitting
   was ended *by* it, so `submitted_late` stays false — and `submitted_at` is the
   deadline itself, not the moment the sweep noticed, so the record does not move
   with the tick interval.
5. **Nothing that enforces no end is ever closed.** An exam with no duration and
   no window end has no deadline, and a sitting under it stays open indefinitely —
   that is what the teacher asked for, not a bug to sweep away.

The tests below therefore fail on the shipped code for one reason: there is no
server-side enforcement at all. `close_expired` does not exist.
"""
from __future__ import annotations

import pathlib
import re
from datetime import datetime, timedelta, timezone

import pytest

from app.services import deadline_service as D
from app.services.anti_cheat_service import calculate_graduated_penalty
from app.utils import exam_window

ROOT = pathlib.Path(__file__).resolve().parents[2]

#: A fixed instant, so nothing here depends on when the suite runs.
NOW = datetime(2026, 10, 1, 12, 0, 0, tzinfo=timezone.utc)

EXAM_ID = "e-1"
STUDENT_ID = "u-1"
SUB_ID = "sub-1"


def iso(when: datetime) -> str:
    return when.isoformat()


def exam(**over):
    """One `exams` row with a one-question key, so a score is computable."""
    base = {
        "id": EXAM_ID,
        "duration_minutes": 60,
        "start_at": None,
        "end_at": None,
        "auto_submit_on_window_end": False,
        "publish_mode": "manual",
        "total_questions": 1,
        "answer_key": {"0": "A"},
        "question_types": {"0": "mcq"},
        "question_weights": {"0": 100.0},
    }
    base.update(over)
    return base


def draft(started: datetime, **over):
    """A `submissions` row as the sweep reads one — with its exam embedded."""
    row = {
        "id": SUB_ID,
        "exam_id": EXAM_ID,
        "student_id": STUDENT_ID,
        "status": "draft",
        "started_at": iso(started),
        "answers": {"0": "A"},
    }
    row.update(over)
    return row


# ── a fake PostgREST, in memory ──────────────────────────────────────────────

class _Result:
    def __init__(self, data, count=None):
        self.data = data
        self.count = len(data) if count is None else count


class _Query:
    def __init__(self, client, table):
        self.client = client
        self.table = table
        self.filters = []
        self._op = "select"
        self._count = None
        self._payload = None
        self._on_conflict = None
        self._limit = None

    def select(self, *cols, count=None, **kw):
        self._op, self._count = "select", count
        return self

    def update(self, payload):
        self._op, self._payload = "update", payload
        return self

    def insert(self, payload):
        self._op, self._payload = "insert", payload
        return self

    def upsert(self, payload, on_conflict=None):
        self._op, self._payload, self._on_conflict = "upsert", payload, on_conflict
        return self

    def eq(self, column, value):
        self.filters.append(("eq", column, value))
        return self

    def lt(self, column, value):
        self.filters.append(("lt", column, value))
        return self

    def in_(self, column, values):
        self.filters.append(("in", column, list(values)))
        return self

    def order(self, *a, **k):
        return self

    def limit(self, n):
        self._limit = n
        return self

    def _matches(self, rows):
        out = []
        for row in rows:
            keep = True
            for op, column, value in self.filters:
                actual = row.get(column)
                if op == "eq" and actual != value:
                    keep = False
                elif op == "lt" and not (str(actual or "") < str(value)):
                    keep = False
                elif op == "in" and actual not in value:
                    keep = False
            if keep:
                out.append(row)
        if self._limit is not None:
            out = out[:self._limit]
        return out

    def execute(self):
        self.client.calls.append((self.table, self._op))
        rows = self.client.rows.setdefault(self.table, [])
        if self._op == "select":
            return _Result(list(self._matches(rows)))
        if self._op == "update":
            changed = self._matches(rows)
            for row in changed:
                row.update(self._payload)
            return _Result([dict(r) for r in changed])
        payload = self._payload if isinstance(self._payload, list) else [self._payload]
        for item in payload:
            rows.append(dict(item))
        return _Result(list(payload))


class FakeClient:
    def __init__(self, rows=None):
        self.rows = rows or {}
        self.calls = []

    def table(self, name):
        return _Query(self, name)


def sweep_client(started, **over):
    """One draft on one exam, ready to be swept."""
    stored = draft(started, exams=exam(**over.pop("exam", {})))
    return FakeClient({"submissions": [stored]}), stored


# ── 1. when a sitting ends ───────────────────────────────────────────────────

class TestWhenASittingEnds:
    def test_a_sitting_past_its_duration_is_closed(self):
        client, stored = sweep_client(NOW - timedelta(hours=2))
        outcome = D.close_expired(client, now=NOW)

        assert outcome["closed"] == 1
        assert stored["status"] == "submitted"
        # The instant the sitting ended — not the moment the sweep noticed.
        assert stored["submitted_at"] == iso(NOW - timedelta(hours=1))
        assert stored["submitted_late"] is False

    def test_a_sitting_inside_its_duration_is_left_alone(self):
        client, stored = sweep_client(NOW - timedelta(minutes=30))
        outcome = D.close_expired(client, now=NOW)

        assert outcome["closed"] == 0
        assert stored["status"] == "draft"

    def test_an_exam_with_no_end_is_never_closed(self):
        """No duration and no window end: nothing enforces an end, so nothing ends."""
        client, stored = sweep_client(NOW - timedelta(days=30),
                                      exam={"duration_minutes": 0, "end_at": None})
        assert D.close_expired(client, now=NOW)["closed"] == 0
        assert stored["status"] == "draft"

    def test_the_assignment_window_end_cuts_a_sitting_short(self):
        """`auto_submit_on_window_end`: the earlier of the two clocks is the end."""
        window_end = NOW - timedelta(hours=2)
        client, stored = sweep_client(
            NOW - timedelta(hours=3),
            exam={"duration_minutes": 600, "end_at": iso(window_end),
                  "auto_submit_on_window_end": True},
        )
        D.close_expired(client, now=NOW)

        assert stored["status"] == "submitted"
        assert stored["submitted_at"] == iso(window_end), (
            "the sitting ran on the duration although the exam says the window ends it"
        )

    def test_the_window_end_does_not_end_a_sitting_that_is_not_set_to_stop_there(self):
        """The window governs *beginning*; a late starter keeps their duration."""
        client, stored = sweep_client(
            NOW - timedelta(hours=3),
            exam={"duration_minutes": 0, "end_at": iso(NOW - timedelta(hours=2)),
                  "auto_submit_on_window_end": False},
        )
        assert D.close_expired(client, now=NOW)["closed"] == 0
        assert stored["status"] == "draft"

    def test_a_paper_still_within_the_grace_has_not_expired(self):
        """The grace is where a paper may still arrive and be accepted."""
        started = NOW - timedelta(hours=2)                      # deadline 11:00
        deadline = started + timedelta(minutes=60)
        grace = timedelta(seconds=exam_window.LATE_GRACE_SECONDS)
        client, stored = sweep_client(started)

        # One second inside the grace, and the exact boundary: not yet closed.
        assert D.close_expired(client, now=deadline + grace - timedelta(seconds=1))["closed"] == 0
        assert D.close_expired(client, now=deadline + grace)["closed"] == 0, (
            "the sweep closed a sitting during the grace the submit route accepts"
        )
        assert stored["status"] == "draft"

        assert D.close_expired(client, now=deadline + grace + timedelta(seconds=1))["closed"] == 1
        assert stored["status"] == "submitted"

    def test_a_whole_exam_of_sittings_is_closed_in_one_pass(self):
        rows = [draft(NOW - timedelta(hours=2), id=f"s-{i}", student_id=f"u-{i}",
                      exams=exam()) for i in range(12)]
        client = FakeClient({"submissions": rows})
        outcome = D.close_expired(client, now=NOW)

        assert outcome["closed"] == 12
        assert all(r["status"] == "submitted" for r in rows)

    def test_closing_twice_closes_once(self):
        client, stored = sweep_client(NOW - timedelta(hours=2))
        assert D.close_expired(client, now=NOW)["closed"] == 1
        assert D.close_expired(client, now=NOW)["closed"] == 0, (
            "the second pass closed the same sitting again"
        )
        assert stored["status"] == "submitted"


# ── 2. what the record says ──────────────────────────────────────────────────

class TestTheRecord:
    def test_a_closed_paper_is_not_marked_late(self):
        """Late means the answers arrived after the deadline. Here they did not."""
        client, stored = sweep_client(NOW - timedelta(hours=2))
        D.close_expired(client, now=NOW)
        assert stored["submitted_late"] is False

    def test_the_score_is_the_apps_own_rule(self):
        client, stored = sweep_client(NOW - timedelta(hours=2))
        D.close_expired(client, now=NOW)
        assert stored["score"] == 100.0, "a keyed-correct answer was not scored as one"
        assert stored["max_score"] == 100.0
        assert stored["status"] == "submitted"

    def test_the_penalty_is_the_same_ladder_a_submitted_paper_climbs(self):
        client, stored = sweep_client(NOW - timedelta(hours=2))
        client.rows["violation_logs"] = [
            {"user_id": STUDENT_ID, "exam_id": EXAM_ID, "violation_type": "tab_switch"},
            {"user_id": STUDENT_ID, "exam_id": EXAM_ID, "violation_type": "focus_lost"},
        ]
        D.close_expired(client, now=NOW)

        expected = calculate_graduated_penalty(2, exam())["penalty"]
        assert stored["violations"] == 2
        assert stored["penalty"] == pytest.approx(expected), (
            "a clock-closed paper skips the penalty ladder a submitted one climbs"
        )

    def test_it_is_published_exactly_when_the_exam_says_so(self):
        client, stored = sweep_client(NOW - timedelta(hours=2), exam={"publish_mode": "auto"})
        D.close_expired(client, now=NOW)
        assert stored["is_published"] is True

        client, stored = sweep_client(NOW - timedelta(hours=2), exam={"publish_mode": "manual"})
        D.close_expired(client, now=NOW)
        assert stored["is_published"] is False


# ── 3. one point, not two ────────────────────────────────────────────────────

class TestOnePointOwnsTheEnd:
    SOURCE = (ROOT / "app" / "services" / "deadline_service.py").read_text(encoding="utf-8")

    def test_the_deadline_arithmetic_is_the_modules_own(self):
        """`exam_window` already answers this; a second answer is a second truth."""
        assert "exam_window.deadline(" in self.SOURCE, (
            "the sweep does not ask exam_window when the sitting ends"
        )
        assert not re.search(r"timedelta\s*\(\s*minutes\s*=", self.SOURCE), (
            "the sweep recomputes the duration arithmetic instead of asking for it"
        )

    def test_the_grace_is_the_number_the_submit_route_accepts_within(self):
        assert "LATE_GRACE_SECONDS" in self.SOURCE, (
            "the grace is not the one the submit route accepts papers within, so "
            "'accepted' and 'not yet closed' can drift apart"
        )

    def test_the_row_is_written_by_the_one_writer(self):
        """`finish_sitting` owns the submissions row, PATCH-vs-POST included.

        The *call* is what has to be there, not the import: a mutation that
        replaced the call with a direct PATCH kept the import and survived this
        test until the assertion was tightened.
        """
        assert "finish_sitting(" in self.SOURCE, (
            "the sweep writes the submissions row itself, which is how a re-sit "
            "collided on submissions_student_exam_unique before"
        )
        assert not re.search(r'table\(\s*["\']submissions["\']\s*\)\s*\.\s*update\(',
                             self.SOURCE), (
            "the sweep patches the submissions row directly instead of through "
            "submission_service.finish_sitting"
        )

    def test_the_sweep_does_not_decide_lateness_again(self):
        """`submitted_late` is written as a constant here, not recomputed.

        The hours a sweep might have been down for must not turn a paper into a
        late one — that is a statement about the box, not about the student.
        """
        assert '"submitted_late": False' in self.SOURCE

    def test_the_online_submit_route_keeps_its_own_verdict(self):
        """Its two ends are observable, so it keeps asking `is_late` directly.

        It is deliberately *not* routed through the sweep: a teacher's stated mark
        must not be able to override a session that was actually timed.
        """
        student = (ROOT / "app" / "routes" / "student.py").read_text(encoding="utf-8")
        assert "exam_window.is_late(" in student
        assert '"submitted_late": submitted_late' in student


# ── 4. the paper is accepted before it is ever closed ────────────────────────

class TestLateAnswersAreStillAccepted:
    """The other half of the same deadline, and the reason the grace exists."""

    def test_a_paper_inside_the_grace_is_not_late(self):
        started = NOW - timedelta(hours=1)
        arrived = started + timedelta(minutes=60) + timedelta(seconds=30)
        assert exam_window.is_late(exam(), iso(started), iso(arrived)) is False

    def test_a_paper_after_the_grace_is_late_rather_than_refused(self):
        started = NOW - timedelta(hours=1)
        arrived = started + timedelta(minutes=60) + timedelta(minutes=3)
        assert exam_window.is_late(exam(), iso(started), iso(arrived)) is True

    def test_an_exam_with_no_deadline_never_marks_anything_late(self):
        started = NOW - timedelta(days=1)
        assert exam_window.is_late(
            exam(duration_minutes=0, end_at=None), iso(started), iso(NOW)) is False


# ── 5. it runs without a browser ─────────────────────────────────────────────

class TestItRunsWithoutABrowser:
    def test_the_loop_is_started_with_the_other_schedulers(self):
        init = (ROOT / "app" / "__init__.py").read_text(encoding="utf-8")
        assert "start_deadline_scheduler" in init, (
            "nothing calls the sweep, so the deadline is still only enforced in a browser"
        )
        block = init[init.index("START_BACKGROUND_SCHEDULERS"):]
        assert "start_deadline_scheduler" in block, (
            "the sweep is started outside the background-scheduler switch, which is "
            "what lets the test suite and the deploy gate build an app without side effects"
        )

    def test_the_interval_is_configurable(self):
        config = (ROOT / "app" / "config.py").read_text(encoding="utf-8")
        assert "DEADLINE_SWEEP_INTERVAL_SECONDS" in config

    def test_a_paper_that_was_already_graded_is_not_touched(self):
        """The sweep closes drafts. A graded paper is a result, not a sitting.

        Overwriting one would replace a teacher's own marks with the auto-scored
        objective ones, hours after they were entered.
        """
        row = draft(NOW - timedelta(hours=2), status="graded", score=55.0,
                    final_score=50.0, exams=exam())
        client = FakeClient({"submissions": [row]})

        assert D.close_expired(client, now=NOW)["closed"] == 0
        assert row["status"] == "graded"
        assert row["score"] == 55.0
        assert row["final_score"] == 50.0
