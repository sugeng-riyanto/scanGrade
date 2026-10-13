"""The penalty is derived from per-violation decisions, and one writer derives it.

A teacher reading a paper that lost fifteen points could not tell *which* violations
cost them, and had no way to forgive one without erasing the rest: the penalty was a
single aggregate the ladder computed from a count, and the appeal flow moved it by a
delta. This suite pins the replacement:

* a decision is stored per violation, in the same `submissions.answers` container the
  appeal already uses (one container, so the two cannot disagree);
* the total is the ladder summed over the *applied* ones — never a stored delta — so
  it can always be re-derived from the list;
* **absence means applied**, which is why no historical mark moves;
* the writer is `penalty_service.recompute`, and the panel and the stored mark ask the
  same arithmetic;
* the write door is the owner or the school's admin (`can_manage_exam`), the read door
  is wider (`can_read_exam`) so the two oversight roles can read and not touch.
"""
from __future__ import annotations

import json
import pathlib

import pytest

from app.services import penalty_service
from app.services.anti_cheat_service import calculate_graduated_penalty
from app.utils.exam_access import can_manage_exam, can_read_exam

ROOT = pathlib.Path(__file__).resolve().parents[2]
TEACHER = (ROOT / "app" / "routes" / "teacher.py").read_text(encoding="utf-8")
API = (ROOT / "app" / "routes" / "api.py").read_text(encoding="utf-8")
PANEL = (ROOT / "app" / "templates" / "teacher" / "submission_violations.html").read_text(encoding="utf-8")
RESULT = (ROOT / "app" / "templates" / "student" / "result_detail.html").read_text(encoding="utf-8")

EXAM = {"id": "exam-1", "penalty_per_violation": 5, "anti_cheat_enabled": True,
        "question_types": {"0": "mcq"}, "answer_key": {"0": "A"},
        "total_questions": 1, "question_weights": {}}


def event(vid, kind="tab_switch", at="2026-10-13T01:00:00+00:00"):
    return {"id": vid, "violation_type": kind, "created_at": at, "metadata": {}}


def decided(vid, status, reason=None, kind="tab_switch"):
    return dict(event(vid, kind), status=status, reason=reason,
                decided_by="guru-1", decided_at="2026-10-13T02:00:00+00:00")


# ── pricing: applied only, positional, and never a re-levelled sum ───────────

class TestTheBreakdownIsTheLadderOverAppliedViolations:
    def test_all_applied_is_the_ordinary_ladder(self):
        rows = [decided("v1", "applied"), decided("v2", "applied"), decided("v3", "applied")]
        assert penalty_service.breakdown_for(EXAM, rows)["total"] == 15.0  # 0 + 5 + 10

    def test_ignoring_the_second_does_not_re_level_the_third(self):
        """The property the whole design rests on: forgiving one charge forgives
        exactly that charge, not the ones after it."""
        rows = [decided("v1", "applied"), decided("v2", "ignored"), decided("v3", "applied")]
        part = penalty_service.breakdown_for(EXAM, rows)
        assert part["total"] == 10.0  # 0 + 0 + 10, *not* 0 + 5
        assert part["applied"] == 2 and part["ignored"] == 1

    def test_ignoring_everything_charges_nothing(self):
        rows = [decided("v1", "ignored"), decided("v2", "ignored")]
        assert penalty_service.breakdown_for(EXAM, rows)["total"] == 0.0

    def test_an_unreadable_status_is_treated_as_applied(self):
        """A decision the app does not understand must not become a discount."""
        rows = [dict(event("v1"), status="banana")]
        assert penalty_service.with_decisions(rows, {})[0]["status"] == "applied"

    def test_a_non_charged_kind_is_recorded_never_priced(self):
        rows = [decided("v1", "applied", kind="orientation_shift"),
                decided("v2", "applied", kind="tab_switch")]
        part = penalty_service.breakdown_for(EXAM, rows)
        assert part["recorded"] == 1, "a rotation is not on the ladder to forgive"

    def test_the_switch_off_paper_charges_nothing(self):
        off = dict(EXAM, anti_cheat_enabled=False)
        rows = [decided("v1", "applied"), decided("v2", "applied")]
        assert penalty_service.breakdown_for(off, rows)["total"] == 0.0


class TestHistoricalMarksDoNotMove:
    def test_no_decisions_is_exactly_the_count_based_total(self):
        """`recompute` on an untouched paper must reproduce `calculate_graduated_penalty`
        to the cent, for every count — that is what keeps old marks identical through
        this change, with no backfill."""
        for n in range(0, 7):
            events = [decided(f"v{i}", "applied") for i in range(1, n + 1)]
            ladder = calculate_graduated_penalty(n, EXAM)["penalty"]
            assert penalty_service.breakdown_for(EXAM, events)["total"] == ladder, (
                f"count={n}: derived {penalty_service.breakdown_for(EXAM, events)['total']} "
                f"!= ladder {ladder}")

    def test_absence_of_a_decision_reads_as_applied(self):
        sub = {"answers": {}}
        assert penalty_service.decisions(sub) == {}
        rows = penalty_service.with_decisions([event("v1")], penalty_service.decisions(sub))
        assert rows[0]["status"] == "applied"


# ── a fake PostgREST that really stores, so the writer can be driven ─────────

class _Res:
    def __init__(self, data, count=None):
        self.data = data
        self.count = count if count is not None else len(data)


class _Q:
    def __init__(self, db, table):
        self.db, self.table = db, table
        self.filters, self.op, self.payload, self._single = [], "select", None, False

    def select(self, *a, **k):
        self.op = "select"
        return self

    def update(self, payload):
        self.op, self.payload = "update", dict(payload)
        return self

    def insert(self, payload):
        self.op, self.payload = "insert", dict(payload)
        return self

    def eq(self, column, value):
        self.filters.append((column, str(value)))
        return self

    def in_(self, column, values):
        self.filters.append((column, {str(v) for v in values}))
        return self

    def order(self, *a, **k):
        return self

    def limit(self, *a, **k):
        return self

    def maybe_single(self):
        self._single = True
        return self

    def single(self):
        self._single = True
        return self

    def _match(self, row):
        for column, want in self.filters:
            got = row.get(column)
            if isinstance(want, set):
                if str(got) not in want:
                    return False
            elif str(got) != want:
                return False
        return True

    def execute(self):
        rows = self.db.tables.setdefault(self.table, [])
        if self.op == "select":
            hits = [dict(r) for r in rows if self._match(r)]
            if self._single:
                return _Res(hits[0]) if hits else None
            return _Res(hits, count=len(hits))
        if self.op == "insert":
            row = dict(self.payload)
            row.setdefault("id", f"{self.table}-{len(rows) + 1}")
            rows.append(row)
            return _Res([dict(row)])
        hits = [r for r in rows if self._match(r)]
        for r in hits:
            r.update(self.payload)
        self.db.writes.append((self.op, self.table, dict(self.payload)))
        return _Res([dict(r) for r in hits])


class _Db:
    def __init__(self, submission=None, exam=None, logs=None):
        self.tables = {
            "submissions": [dict(submission)] if submission else [],
            "exams": [dict(exam)] if exam else [],
            "violation_logs": [dict(r) for r in (logs or [])],
        }
        self.writes = []

    def table(self, name):
        return _Q(self, name)


def _submission(answers=None, **kw):
    base = {"id": "sub-1", "exam_id": "exam-1", "student_id": "stu-1",
            "answers": json.dumps(answers or {}), "teacher_feedback": {},
            "penalty": 0, "final_score": 100, "violations": 0}
    base.update(kw)
    return base


def _logs(n=3, kind="tab_switch"):
    return [{"id": f"v{i}", "exam_id": "exam-1", "user_id": "stu-1",
             "violation_type": kind, "created_at": f"2026-10-13T0{i}:00:00+00:00",
             "metadata": {}} for i in range(1, n + 1)]


# ── the writer ───────────────────────────────────────────────────────────────

class TestTheWriterDerivesTheStoredMark:
    def test_recompute_writes_the_derived_penalty_and_keeps_the_raw(self):
        db = _Db(submission=_submission({"0": "A"}), exam=EXAM, logs=_logs(3))
        picture = penalty_service.recompute(db, "sub-1")
        assert picture["penalty"] == 15.0
        assert picture["applied"] == 3 and picture["ignored"] == 0
        assert picture["final"] == max(0.0, picture["raw"] - 15.0)
        stored = db.tables["submissions"][0]
        assert stored["penalty"] == 15.0 and stored["final_score"] == picture["final"]

    def test_set_decision_ignoring_one_lowers_the_total_without_re_levelling(self):
        db = _Db(submission=_submission({"0": "A"}), exam=EXAM, logs=_logs(3))
        _, facts = penalty_service.set_decision(db, "sub-1", "v2", "ignored", "salah")
        assert facts == {"violation_id": "v2", "old_status": "applied",
                         "new_status": "ignored", "reason": "salah"}
        assert db.tables["submissions"][0]["penalty"] == 10.0

    def test_a_decision_survives_a_recompute(self):
        db = _Db(submission=_submission({"0": "A"}), exam=EXAM, logs=_logs(3))
        penalty_service.set_decision(db, "sub-1", "v3", "ignored")
        picture = penalty_service.recompute(db, "sub-1")
        assert picture["ignored"] == 1 and picture["penalty"] == 5.0  # 0 + 5 + 0

    def test_set_all_ignores_every_charged_violation(self):
        db = _Db(submission=_submission({"0": "A"}), exam=EXAM, logs=_logs(3))
        picture, facts = penalty_service.set_all(db, "sub-1", "ignored", "banding")
        assert facts["changed"] == 3 and picture["penalty"] == 0.0
        assert picture["ignored"] == 3

    def test_set_all_never_touches_a_non_charged_kind(self):
        logs = _logs(2) + [{"id": "v9", "exam_id": "exam-1", "user_id": "stu-1",
                            "violation_type": "orientation_shift",
                            "created_at": "2026-10-13T09:00:00+00:00", "metadata": {}}]
        db = _Db(submission=_submission({"0": "A"}), exam=EXAM, logs=logs)
        _, facts = penalty_service.set_all(db, "sub-1", "ignored")
        assert facts["changed"] == 2, "a rotation was never on the ladder to forgive"

    def test_an_unknown_status_is_refused_and_writes_nothing(self):
        db = _Db(submission=_submission(), exam=EXAM, logs=_logs(1))
        picture, facts = penalty_service.set_decision(db, "sub-1", "v1", "maybe")
        assert picture is None and facts.get("error") == "status"
        assert db.writes == []

    def test_a_missing_submission_is_refused(self):
        db = _Db(submission=None, exam=EXAM, logs=_logs(1))
        picture, facts = penalty_service.set_decision(db, "nope", "v1", "ignored")
        assert picture is None and facts.get("error") == "missing"

    def test_inspect_does_not_write(self):
        db = _Db(submission=_submission({"0": "A"}), exam=EXAM, logs=_logs(2))
        picture = penalty_service.inspect(db, db.tables["submissions"][0], EXAM)
        assert picture["penalty"] == 5.0
        assert db.writes == [], "a reader must never move a mark"


# ── RBAC: who may write, who may only read ───────────────────────────────────

SCHOOL = "school-1"
EXAM_ROW = {"id": "exam-1", "teacher_id": "guru-OWNER", "school_id": SCHOOL}


class TestTheDoorsAreDifferent:
    def test_the_owner_may_manage(self):
        assert can_manage_exam("guru-OWNER", "guru", SCHOOL, EXAM_ROW) is True

    def test_another_teacher_in_the_same_school_may_not(self):
        assert can_manage_exam("guru-OTHER", "guru", SCHOOL, EXAM_ROW) is False

    def test_the_school_admin_may_manage(self):
        assert can_manage_exam("admin-1", "admin_sekolah", SCHOOL, EXAM_ROW) is True

    def test_an_admin_of_another_school_may_not(self):
        assert can_manage_exam("admin-2", "admin_sekolah", "school-9", EXAM_ROW) is False

    def test_the_two_officials_may_read_but_not_manage(self):
        """The line between supervising and teaching: oversight reads, it does not
        overwrite a colleague's marks."""
        for role in ("principal", "vice_principal"):
            assert can_manage_exam("off-1", role, SCHOOL, EXAM_ROW) is False, role
            assert can_read_exam("off-1", role, SCHOOL, EXAM_ROW) is True, role

    def test_a_pupil_may_do_neither(self):
        assert can_manage_exam("stu-1", "murid", SCHOOL, EXAM_ROW) is False
        assert can_read_exam("stu-1", "murid", SCHOOL, EXAM_ROW) is False


class TestTheRoutesUseThoseDoors:
    def test_the_write_routes_guard_with_can_manage(self):
        for name in ("api_submission_violation_set", "api_submission_violations_all"):
            body = TEACHER.split(f"def {name}(", 1)[1].split("\ndef ", 1)[0]
            assert "_guard_submission(" in body, f"{name} does not use the write guard"

    def test_the_read_panel_guards_with_can_read(self):
        body = TEACHER.split("def submission_violations(", 1)[1].split("\ndef ", 1)[0]
        assert "_guard_submission_read(" in body
        read_guard = TEACHER.split("def _guard_submission_read(", 1)[1].split("\ndef ", 1)[0]
        assert "can_read_exam(" in read_guard, "the panel must let officials read"
        assert "can_manage_exam(" not in read_guard

    def test_the_panel_only_offers_controls_a_manager_may_use(self):
        assert "can_edit" in PANEL and "can_manage_exam(" in TEACHER
        # The controls are behind `can_edit`, so a reader gets no switches.
        assert PANEL.count("{% if can_edit %}") >= 2

    def test_the_write_routes_are_guarded_by_the_teacher_decorator(self):
        for name in ("api_submission_violation_set", "api_submission_violations_all",
                     "submission_violations"):
            block = TEACHER.split(f"def {name}(", 1)[1].split("def ", 1)[0]
            before = TEACHER[max(0, TEACHER.index(f"def {name}(") - 400):
                             TEACHER.index(f"def {name}(")]
            assert "@teacher_or_admin_required" in before, f"{name} has no role guard"

    def test_an_admin_acting_on_a_colleagues_paper_is_recorded(self):
        for name in ("api_submission_violation_set", "api_submission_violations_all"):
            body = TEACHER.split(f"def {name}(", 1)[1].split("\ndef ", 1)[0]
            assert "on_behalf_of" in body, f"{name} does not record who it acted for"


# ── the appeal is the same decision, not a second number ─────────────────────

class TestTheAppealAndTheDecisionsCannotDisagree:
    def test_approving_an_appeal_ignores_the_violations(self):
        body = TEACHER.split("def api_penalty_appeal_handle(", 1)[1].split("\ndef ", 1)[0]
        assert 'penalty_service.set_all(' in body
        assert '"ignored"' in body
        assert "reduction_type" not in body, "a delta would be a second source of truth"
        assert "new_penalty = round(current_penalty - penalty_reduction" not in body

    def test_the_decision_and_the_appeal_share_one_container(self):
        assert penalty_service.DECISIONS_KEY == "_violation_decisions"
        # The appeal lives beside it in the same `answers` object.
        assert "_penalty_appeal" in TEACHER

    def test_the_new_charge_reads_the_decisions_back(self):
        assert "penalty_service.decisions(" in API
        assert "penalty_service.applied_penalty(" in API


# ── what the pupil is told ───────────────────────────────────────────────────

class TestThePupilSeesCountsNotTheLog:
    def test_the_result_page_summarises_applied_against_recorded(self):
        assert "penalty_summary" in RESULT
        assert "penalty_summary.applied" in RESULT and "penalty_summary.recorded" in RESULT

    def test_the_summary_never_prints_a_per_event_list(self):
        for leak in ("violation_rows", "decided_by", "away_seconds"):
            assert leak not in RESULT, f"the pupil's page leaks the teacher's log: {leak}"

    def test_the_route_computes_the_summary_from_the_service(self):
        student = (ROOT / "app" / "routes" / "student.py").read_text(encoding="utf-8")
        # The route gets raw, penalty, final and the counts from the one reader that
        # prices the ladder — the same `inspect` the teacher's review panel calls — and
        # hands that picture straight to the page. It replaced this route's own
        # `events` + `breakdown_for` pair, which answered the same question in a second
        # place; what this guard is for (the pupil's summary is the service's, never
        # assembled at the route) is unchanged.
        assert "penalty_service.inspect(" in student
        assert "parts=parts" in student


# ── the write route, driven for real: who gets in and who does not ───────────

class _RouteDb(_Db):
    """The writer's own tables: the submission and the paper it belongs to.

    The paper is embedded in the submission as `exams`, exactly the shape
    `_guard_submission` selects — the guard reads the paper off the row it already
    fetched, so the fake has to hand it the same way PostgREST does."""

    def __init__(self, submission, exam, logs, owner_name="Bu Sari"):
        submission = dict(submission)
        submission["exams"] = {"id": exam.get("id"), "teacher_id": exam.get("teacher_id"),
                               "school_id": exam.get("school_id")}
        super().__init__(submission=submission, exam=exam, logs=logs)
        self.tables["profiles"] = [{"id": exam.get("teacher_id"),
                                    "full_name": owner_name}]


def _raw(fn):
    """The route body with every decorator stripped — the guard inside is what is
    under test, not the login decorator above it."""
    while hasattr(fn, "__wrapped__"):
        fn = fn.__wrapped__
    return fn


def _set_violation(app, monkeypatch, db, *, role, uid, school, violation_id="v2",
                   status="ignored"):
    from app.routes import teacher as tmod
    monkeypatch.setattr(tmod, "get_supabase", lambda: db)
    monkeypatch.setitem(app.extensions, "supabase", db)
    monkeypatch.setattr(tmod, "log_activity", lambda *a, **k: None)
    with app.test_request_context(
            f"/teacher/api/submission/sub-1/violations", method="POST",
            json={"violation_id": violation_id, "status": status, "reason": "salah"}):
        from flask import g
        g.user_id, g.user_role, g.user_school_id = uid, role, school
        result = _raw(tmod.api_submission_violation_set)("sub-1")
        # A refusal is a `(response, 403)` tuple; a success is a bare response.
        if isinstance(result, tuple):
            return result[0], result[1]
        return result, result.status_code


class TestWhoMayChangeAPenalty:
    def _db(self):
        sub = _submission({"0": "A"})
        exam = dict(EXAM, teacher_id="guru-OWNER", school_id=SCHOOL)
        return _RouteDb(sub, exam, _logs(3))

    def test_the_owner_may_change_it(self, app, monkeypatch):
        db = self._db()
        resp = _set_violation(app, monkeypatch, db, role="guru",
                              uid="guru-OWNER", school=SCHOOL)
        response, status = resp
        assert status == 200 and response.get_json()["success"] is True
        assert db.tables["submissions"][0]["penalty"] == 10.0

    def test_another_teacher_is_refused(self, app, monkeypatch):
        db = self._db()
        resp = _set_violation(app, monkeypatch, db, role="guru",
                              uid="guru-OTHER", school=SCHOOL)
        _response, status = resp
        assert status == 403, "a colleague must not touch this paper"
        assert db.tables["submissions"][0]["penalty"] == 0, "nothing may be written"

    def test_the_school_admin_may_change_it(self, app, monkeypatch):
        db = self._db()
        resp = _set_violation(app, monkeypatch, db, role="admin_sekolah",
                              uid="admin-1", school=SCHOOL)
        _response, status = resp
        assert status == 200
        assert db.tables["submissions"][0]["penalty"] == 10.0

    def test_an_admin_of_another_school_is_refused(self, app, monkeypatch):
        db = self._db()
        resp = _set_violation(app, monkeypatch, db, role="admin_sekolah",
                              uid="admin-2", school="school-9")
        _response, status = resp
        assert status == 403

    @pytest.mark.parametrize("role", ["principal", "vice_principal"])
    def test_the_officials_are_refused_the_write(self, app, monkeypatch, role):
        """Oversight reads; it does not overwrite a colleague's mark."""
        db = self._db()
        resp = _set_violation(app, monkeypatch, db, role=role,
                              uid="off-1", school=SCHOOL)
        _response, status = resp
        assert status == 403, f"{role} was allowed to write a penalty"

    def test_an_unknown_status_is_refused_before_anything_is_written(self, app, monkeypatch):
        db = self._db()
        resp = _set_violation(app, monkeypatch, db, role="guru",
                              uid="guru-OWNER", school=SCHOOL, status="maybe")
        _response, status = resp
        assert status == 400
        assert db.tables["submissions"][0]["penalty"] == 0


def _set_all(app, monkeypatch, db, *, role, uid, school, status="ignored"):
    from app.routes import teacher as tmod
    monkeypatch.setattr(tmod, "get_supabase", lambda: db)
    monkeypatch.setitem(app.extensions, "supabase", db)
    monkeypatch.setattr(tmod, "log_activity", lambda *a, **k: None)
    with app.test_request_context("/teacher/api/submission/sub-1/violations/all",
                                  method="POST",
                                  json={"status": status, "reason": "banding"}):
        from flask import g
        g.user_id, g.user_role, g.user_school_id = uid, role, school
        result = _raw(tmod.api_submission_violations_all)("sub-1")
        if isinstance(result, tuple):
            return result[0], result[1]
        return result, result.status_code


class TestTheBulkControl:
    def _db(self):
        exam = dict(EXAM, teacher_id="guru-OWNER", school_id=SCHOOL)
        return _RouteDb(_submission({"0": "A"}), exam, _logs(3))

    def test_the_owner_may_ignore_everything_at_once(self, app, monkeypatch):
        db = self._db()
        response, status = _set_all(app, monkeypatch, db, role="guru",
                                    uid="guru-OWNER", school=SCHOOL)
        assert status == 200 and response.get_json()["changed"] == 3
        assert db.tables["submissions"][0]["penalty"] == 0.0

    def test_an_official_is_refused_the_bulk_write(self, app, monkeypatch):
        db = self._db()
        _response, status = _set_all(app, monkeypatch, db, role="principal",
                                     uid="off-1", school=SCHOOL)
        assert status == 403
        assert db.tables["submissions"][0]["penalty"] == 0, "nothing may be written"
