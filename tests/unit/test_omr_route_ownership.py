"""A scanned sheet, a bulk ZIP and a published result must be *your* paper's.

The OMR routes took an `exam_id` straight from the request and checked only the
caller's role, and the publish POST checked only the school. So a teacher could

* read a sheet into a colleague's paper (even another school's) by posting its id
  to `/api/scan/process` or `/api/scan/bulk`,
* write a whole class of marks into it via `/api/scan/bulk-save`,
* publish another teacher's marks with `POST /teacher/publish/<exam_id>`, and
* poll any OMR task by its id — a bearer token for a pupil's scanned answers.

The rule is the one the teacher UI already assumes: the paper's owner, or the
admin of the paper's school (`exam_access.can_manage_exam`). These tests pin that
each of the five doors now applies it, and that the task-polling door asks it
about the task's paper rather than trusting the task id.
"""
from __future__ import annotations

import pathlib
import re

import pytest
from flask import g

from app.routes import api
from app.utils import exam_access as xa

ROOT = pathlib.Path(__file__).resolve().parents[2]
API = (ROOT / "app" / "routes" / "api.py").read_text(encoding="utf-8-sig")
TEACHER = (ROOT / "app" / "routes" / "teacher.py").read_text(encoding="utf-8-sig")
_COMMENT = re.compile(r"\{#.*?#\}|<!--.*?-->|(?:^|\s)#[^\n]*", re.S)


def code(text: str) -> str:
    """Source with its comments removed, so a guard never fires on prose."""
    return _COMMENT.sub("", text)


def _route_source(text: str, name: str) -> str:
    start = text.index(f"def {name}(")
    rest = text[start:]
    ends = [m.start() for m in re.finditer(r"\n(?:def |@\w+_bp\.route)", rest)]
    return rest[:min(ends)] if ends else rest


# ── a fake exams table ───────────────────────────────────────────────────────

SCHOOL_A, SCHOOL_B = "school-A", "school-B"
OWNER = "teacher-owner"


def exam_row(**over):
    row = {"id": "exam-1", "teacher_id": OWNER, "school_id": SCHOOL_A}
    row.update(over)
    return row


class _Result:
    def __init__(self, data):
        self.data = data


class _FakeTable:
    def __init__(self, row, boom=False):
        self.row, self.boom = row, boom

    def select(self, *a, **k):
        return self

    def eq(self, *a, **k):
        return self

    def maybe_single(self):
        return self

    def single(self):
        return self

    def execute(self):
        if self.boom:
            raise RuntimeError("supabase down")
        return _Result(self.row)


class _FakeSupabase:
    def __init__(self, row=None, boom=False):
        self.row, self.boom = row, boom

    def table(self, name):
        assert name == "exams"
        return _FakeTable(self.row, self.boom)


def _manage(row, *, role="guru", user_id=OWNER, school=SCHOOL_A):
    return xa.can_manage_exam(user_id, role, school, row)


# ── 1. the predicate, on the scan cases ──────────────────────────────────────

class TestTheRuleTheScanDoorsNeed:
    def test_the_owner_may_scan_their_own_paper(self):
        assert _manage(exam_row()) is True

    def test_a_colleague_in_the_same_school_may_not(self):
        assert _manage(exam_row(), user_id="teacher-other") is False

    def test_a_teacher_from_another_school_may_not(self):
        assert _manage(exam_row(), user_id="teacher-other", school=SCHOOL_B) is False

    def test_the_school_admin_may(self):
        assert _manage(exam_row(), role="admin_sekolah", user_id="admin-1",
                       school=SCHOOL_A) is True

    def test_another_schools_admin_may_not(self):
        assert _manage(exam_row(), role="admin_sekolah", user_id="admin-2",
                       school=SCHOOL_B) is False

    def test_a_super_admin_may(self):
        assert _manage(exam_row(), role="super_admin", user_id="sa-1", school=None) is True


# ── 2. the lookup + decision, and its four distinct answers ──────────────────

class TestTheSharedResolution:
    def test_the_owner_is_ok(self):
        exam, verdict = xa.managed_exam(_FakeSupabase(exam_row()), "exam-1",
                                        OWNER, "guru", SCHOOL_A)
        assert verdict == xa.EXAM_OK and exam["id"] == "exam-1"

    def test_a_stranger_is_denied(self):
        exam, verdict = xa.managed_exam(_FakeSupabase(exam_row()), "exam-1",
                                        "teacher-other", "guru", SCHOOL_A)
        assert exam is None and verdict == xa.EXAM_DENIED

    def test_a_missing_paper_is_missing_not_denied(self):
        exam, verdict = xa.managed_exam(_FakeSupabase(None), "exam-1",
                                        OWNER, "guru", SCHOOL_A)
        assert exam is None and verdict == xa.EXAM_MISSING

    def test_a_failed_lookup_denies_rather_than_passing(self):
        exam, verdict = xa.managed_exam(_FakeSupabase(boom=True), "exam-1",
                                        OWNER, "guru", SCHOOL_A)
        assert exam is None and verdict == xa.EXAM_UNVERIFIABLE


@pytest.fixture
def as_owner(app, monkeypatch):
    def run(row, *, role="guru", user_id=OWNER, school=SCHOOL_A):
        monkeypatch.setattr(api, "get_supabase", lambda: _FakeSupabase(row))
        with app.test_request_context("/api/scan/bulk-save"):
            g.user_id, g.user_role, g.user_school_id = user_id, role, school
            g.tz_offset, g.show = 7, {}
            return api._guard_managed_exam("exam-1")
    return run


class TestTheJsonGuard:
    def test_the_owner_passes(self, as_owner):
        exam, err = as_owner(exam_row())
        assert err is None and exam["teacher_id"] == OWNER

    def test_a_stranger_is_refused_with_403(self, as_owner):
        exam, err = as_owner(exam_row(), user_id="teacher-other")
        assert exam is None and err[1] == 403

    def test_a_missing_paper_is_404(self, as_owner):
        exam, err = as_owner(None)
        assert exam is None and err[1] == 404


# ── 3. the task id is not a licence ─────────────────────────────────────────

@pytest.fixture
def as_poller(app, monkeypatch):
    def run(record, *, role="guru", user_id=OWNER, school=SCHOOL_A, row=None):
        monkeypatch.setattr(api, "get_supabase", lambda: _FakeSupabase(row if row is not None else exam_row()))
        with app.test_request_context("/api/scan/task/abc"):
            g.user_id, g.user_role, g.user_school_id = user_id, role, school
            g.tz_offset, g.show = 7, {}
            return api._omr_task_caller_allowed(record)
    return run


class TestWhoMayPollATask:
    def test_an_unknown_task_is_refused(self, as_poller):
        assert as_poller(None) is False

    def test_the_caller_that_started_it_may_poll(self, as_poller):
        assert as_poller({"user_id": OWNER, "exam_id": "exam-1"}) is True

    def test_a_colleague_may_not_poll_your_task(self, as_poller):
        assert as_poller({"user_id": OWNER, "exam_id": "exam-1"},
                         user_id="teacher-other") is False

    def test_the_school_admin_may_poll_a_task_on_their_schools_paper(self, as_poller):
        assert as_poller({"user_id": "teacher-other", "exam_id": "exam-1"},
                         role="admin_sekolah", user_id="admin-1") is True

    def test_a_task_with_no_paper_is_only_its_starters(self, as_poller):
        assert as_poller({"user_id": "teacher-other", "exam_id": ""},
                         user_id="someone-else") is False


# ── 4. every door actually calls the guard ──────────────────────────────────

class TestEachDoorIsBound:
    def test_scan_process_guards_and_registers(self):
        src = code(_route_source(API, "scan_process"))
        assert "_guard_managed_exam(exam_id)" in src, "scan/process takes any exam_id"
        assert "_register_omr_task(task.id, exam_id)" in src, "its task is unowned"

    def test_scan_bulk_guards_and_registers(self):
        src = code(_route_source(API, "scan_bulk"))
        assert "_guard_managed_exam(exam_id)" in src, "scan/bulk takes any exam_id"
        assert "_register_omr_task(task.id, exam_id)" in src, "its task is unowned"

    def test_scan_bulk_save_guards_the_write(self):
        src = code(_route_source(API, "scan_bulk_save"))
        assert "_guard_managed_exam(exam_id)" in src, "scan/bulk-save writes anyone's paper"

    def test_scan_task_status_checks_the_record(self):
        src = code(_route_source(API, "scan_task_status"))
        assert "_omr_task_caller_allowed(cache_get(" in src, (
            "scan/task polls any task id without asking whose it is")

    def test_publish_scores_guards_the_paper(self):
        src = code(_route_source(TEACHER, "publish_scores"))
        assert "_guard_exam(supabase, exam_id" in src, (
            "POST /publish only checked the school, not the paper's owner")

    def test_the_teacher_guard_uses_the_same_rule_as_the_api(self):
        """Both doors resolve through `managed_exam`, so they cannot drift."""
        assert "managed_exam(" in code(_route_source(TEACHER, "_guard_exam"))
        assert "managed_exam(" in code(_route_source(API, "_guard_managed_exam"))
