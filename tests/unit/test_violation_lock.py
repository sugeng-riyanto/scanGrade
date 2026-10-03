"""Crossing the violation threshold must *lock* the sitting, not merely end it.

Requested: "Wire resume_code.decide() into the moment a violation is charged so
crossing max_violations actually locks the sitting server-side."

The threshold is not new. `exams.max_violations` has ended a sitting since
migration 011 — the ladder's `auto_submit_on_max` — and `/api/violation/log` is
where that decision is already made: it counts the charged violations, reads the
exam, and answers the page with `auto_submit`. So this is not a second counter
and not a second threshold; it is one more branch at the one place that already
charges.

Two rules make the branch safe, and both are guards below:

* **The lock replaces the instruction, never adds to it.** Telling the page both
  "you are locked" and "submit now" is a race between two terminal states, and
  the page would pick one arbitrarily. When the lock is written, `auto_submit`
  leaves the response false.
* **Opt-in, and never past the deadline.** An exam that has not asked for locking
  keeps exactly today's behaviour, and one whose deadline has passed is *not*
  locked — that branch stays with the ladder, because a paper waiting on a resume
  that can never be granted is a pupil held out of an exam that is already over.
"""
from __future__ import annotations

import pathlib
import re
from datetime import datetime, timedelta, timezone

import pytest

from app.routes import api as api_module
from app.services import resume_code as rc

ROOT = pathlib.Path(__file__).resolve().parents[2]
API = ROOT / "app" / "routes" / "api.py"


# ── a fake that applies its writes ───────────────────────────────────────────

class _Resp:
    def __init__(self, data):
        self.data = data


class _Query:
    def __init__(self, db, table):
        self.db = db
        self.table = table
        self.payload = None
        self.filters = []

    def update(self, payload):
        self.payload = dict(payload)
        return self

    def eq(self, column, value):
        self.filters.append((column, value))
        return self

    def execute(self):
        rows = self.db.tables.setdefault(self.table, [])
        hits = [r for r in rows
                if all(str(r.get(c)) == str(v) for c, v in self.filters)]
        for row in hits:
            row.update(self.payload)
        self.db.writes.append((self.table, dict(self.payload or {})))
        return _Resp([dict(r) for r in hits])


class _DB:
    def __init__(self, **tables):
        self.tables = {k: [dict(r) for r in v] for k, v in tables.items()}
        self.writes = []

    def table(self, name):
        return _Query(self, name)


def _now():
    return datetime.now(timezone.utc)


def _exam(**kw):
    base = {
        "id": "exam-1",
        "anti_cheat_enabled": True,
        "penalty_per_violation": 5,
        "max_violations": 5,
        "auto_submit_on_max": True,
        "duration_minutes": 60,
        "start_at": None,
        "end_at": None,
        "auto_submit_on_window_end": False,
        "lock_pending_resume": True,
        "resume_code_limit": 2,
    }
    base.update(kw)
    return base


def _row(started_at, **kw):
    base = {
        "id": "sub-1", "exam_id": "exam-1", "student_id": "stu-1",
        "status": "draft", "started_at": started_at.isoformat(),
        "resume_limit": 2, "resume_count_used": 0,
    }
    base.update(kw)
    return base


def _penalty(auto_submit=True):
    """What `calculate_graduated_penalty` answers at the threshold."""
    return {"penalty": 15.0, "warning": False, "auto_submit": auto_submit,
            "current_penalty_this_violation": 10.0}


def _outcome(db, exam, row, charged, penalty=None):
    return api_module._violation_outcome(
        db, exam, row, charged, penalty if penalty is not None else _penalty())


# ── the branch ───────────────────────────────────────────────────────────────

class TestTheThresholdLocks:
    def test_at_the_threshold_before_the_deadline_the_sitting_is_locked(self):
        db = _DB(submissions=[_row(_now() - timedelta(minutes=10))])
        out = _outcome(db, _exam(), db.tables["submissions"][0], 5)

        assert out["locked"] is True
        # No code is echoed: the pupil's code is the recovery code already on their
        # own screen (`app/services/resume_code.py` explains why there is one code
        # and not two).
        assert "resume_code" not in out, (
            "a second code travelled in the response, which is the duplicate this "
            "reconciliation removed")
        assert db.tables["submissions"][0]["status"] == rc.LOCKED
        assert db.tables["submissions"][0]["locked_at"]

    def test_the_response_stops_telling_the_page_to_submit(self):
        """Both instructions at once is a race between two terminal states."""
        db = _DB(submissions=[_row(_now() - timedelta(minutes=10))])
        out = _outcome(db, _exam(), db.tables["submissions"][0], 5,
                       penalty=_penalty(auto_submit=True))

        assert out["locked"] is True
        assert out["auto_submit"] is False, (
            "the page was told to submit *and* that it is locked, so whichever "
            "handler ran first would win")

    def test_the_lock_never_moves_the_clock(self):
        db = _DB(submissions=[_row(_now() - timedelta(minutes=10))])
        before = db.tables["submissions"][0]["started_at"]
        _outcome(db, _exam(), db.tables["submissions"][0], 5)

        assert db.tables["submissions"][0]["started_at"] == before
        payload = db.writes[-1][1]
        for column in ("started_at", "submitted_at", "end_at"):
            assert column not in payload

    def test_below_the_threshold_nothing_happens(self):
        db = _DB(submissions=[_row(_now() - timedelta(minutes=10))])
        out = _outcome(db, _exam(), db.tables["submissions"][0], 4)

        assert out["locked"] is False
        assert db.tables["submissions"][0]["status"] == "draft"
        assert not db.writes


class TestWhenItMustNotLock:
    def test_an_exam_that_has_not_opted_in_keeps_todays_behaviour(self):
        db = _DB(submissions=[_row(_now() - timedelta(minutes=10))])
        out = _outcome(db, _exam(lock_pending_resume=False),
                       db.tables["submissions"][0], 99)

        assert out["locked"] is False
        assert db.tables["submissions"][0]["status"] == "draft"
        assert not db.writes

    def test_at_or_after_the_deadline_the_ladder_still_ends_it(self):
        """Locking here would hold a pupil out of a paper that is already over,
        waiting on a resume that can never be granted."""
        db = _DB(submissions=[_row(_now() - timedelta(minutes=61))])
        out = _outcome(db, _exam(), db.tables["submissions"][0], 5)

        assert out["locked"] is False
        assert db.tables["submissions"][0]["status"] == "draft"
        assert not db.writes, "a lock was written past the deadline"

    def test_a_sitting_that_is_already_locked_is_left_alone(self):
        db = _DB(submissions=[_row(_now() - timedelta(minutes=5),
                                   status=rc.LOCKED)])
        out = _outcome(db, _exam(), db.tables["submissions"][0], 7)

        assert out["locked"] is False
        assert not db.writes

    def test_a_student_with_no_sitting_is_not_a_crash(self):
        db = _DB(submissions=[])
        out = _outcome(db, _exam(), None, 5)

        assert out["locked"] is False
        assert db.writes == []

    def test_anti_cheat_switched_off_locks_nothing(self):
        db = _DB(submissions=[_row(_now() - timedelta(minutes=5))])
        out = _outcome(db, _exam(anti_cheat_enabled=False),
                       db.tables["submissions"][0], 99)

        assert out["locked"] is False
        assert not db.writes


# ── the wiring, which is what makes the branch reachable ─────────────────────

class TestTheRouteActuallyAsks:
    def _source(self) -> str:
        return API.read_text(encoding="utf-8-sig")

    def _charge_block(self) -> str:
        source = self._source()
        assert "def log_violation" in source
        return source.split("def log_violation", 1)[1].split("\n@api_bp.route", 1)[0]

    def test_the_exam_read_carries_the_locking_flag(self):
        """The branch cannot decide anything about a policy it did not read."""
        block = self._charge_block()
        # The select is split across adjacent string literals, so read the whole
        # call — from `select(` to the first `.eq(` — rather than one literal.
        select = re.search(r"select\((.*?)\.eq\(", block, re.S)
        assert select, "the exam is no longer read in the charging block"
        assert "lock_pending_resume" in select.group(1), (
            "the exam is read without `lock_pending_resume`, so `decide()` would "
            "always answer NONE and the lock would never be written: "
            + select.group(1))

    def test_the_submission_read_carries_the_fields_the_lock_needs(self):
        block = self._charge_block()
        assert "started_at" in block, (
            "the submission is read without `started_at`, so the deadline cannot be "
            "asked and a lock could be written past it")
        assert "resume_limit" in block, (
            "the submission is read without its allowance, so the lock would "
            "re-snapshot one on every charge")
        # A word-boundary match, not a substring: `resume_code_limit` is a real
        # column (the exam's policy) and is not the second code this feature
        # removed. Only a column named exactly `resume_code` is the offence.
        assert not re.search(r"\bresume_code\b", block), (
            "the charging block still reads a `resume_code` column that no longer "
            "exists — the lock borrows the recovery code instead")

    def test_the_branch_is_called_with_the_charged_count(self):
        block = self._charge_block()
        assert "_violation_outcome(" in block, (
            "the charging block never asks whether the threshold was crossed, so "
            "`max_violations` still only ends the paper")
        assert "total_count" in block.split("_violation_outcome(", 1)[1][:200] or (
            "total_count" in block), "the outcome is not given the charged count"

    def test_the_outcome_is_merged_into_the_response(self):
        block = self._charge_block()
        assert "**outcome" in block or "**outcome," in block, (
            "the outcome is computed and then dropped, so the page is never told")
