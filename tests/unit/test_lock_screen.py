"""A locked pupil can get back in — and staff can let them in when the code runs out.

`app/services/resume_code.py` computes the whole rule — lock, resume, finalise — and
its own guards pin the arithmetic. What those guards could not see is that **nothing
called `unlock`**: the server could lock a sitting, and then held no door to reopen it.
A pupil who crossed the threshold was stuck on a page whose only exit was a teacher
with database access, and the "resume code" the exam screen has shown since the first
minute led nowhere.

This file pins the three doors that were missing:

* `resume_code.manual_unlock` — a staff member's authority reopens a sitting **without
  a code** (the allowance is spent, or the pupil's device is dead).
* `POST /student/exams/<id>/resume` — the pupil's own door, scoped to `g.user_id` and
  never to an id the request carries.
* the lock screen on the exam page — a status the server sent (`message_key`) turned
  into a sentence, a code field, and a clock that keeps counting.

Every guard here is source *or* behaviour, and the reason is the same one the lock
gate's own file gives: "did the deadline move" and "is the pupil actually scoped" are
questions about where a decision lives, and a mocked call cannot show that a second
answer was written beside the first.
"""
from __future__ import annotations

import pathlib
import re
from datetime import datetime, timedelta, timezone

import pytest

from app.services import resume_code as rc

ROOT = pathlib.Path(__file__).resolve().parents[2]
STUDENT = ROOT / "app" / "routes" / "student.py"
TEACHER = ROOT / "app" / "routes" / "teacher.py"
PRINCIPAL = ROOT / "app" / "routes" / "principal.py"
ADMIN = ROOT / "app" / "routes" / "admin_sekolah.py"
TEMPLATE = ROOT / "app" / "templates" / "student" / "take_exam.html"
OFFICIAL_PAGE = ROOT / "app" / "templates" / "principal" / "invigilation.html"
SERVICE = ROOT / "app" / "services" / "resume_code.py"


# ── a PostgREST stand-in that applies its writes ────────────────────────────

class _Res:
    def __init__(self, data):
        self.data = data
        self.count = len(data or [])


class _Query:
    def __init__(self, db, table):
        self.db = db
        self.table = table
        self._filters = []
        self._op = "select"
        self._payload = None
        self._single = False

    def select(self, *cols, **kw):
        self._op = "select"
        return self

    def insert(self, payload):
        self._op = "insert"
        self._payload = dict(payload)
        return self

    def update(self, payload):
        self._op = "update"
        self._payload = dict(payload)
        return self

    def eq(self, column, value):
        self._filters.append((column, value))
        return self

    def in_(self, column, values):
        self._filters.append((column, ("__in__", list(values))))
        return self

    def order(self, *a, **k):
        return self

    def limit(self, *a, **k):
        return self

    def single(self):
        self._single = True
        return self

    def maybe_single(self):
        self._single = True
        return self

    def _match(self, row) -> bool:
        for column, value in self._filters:
            if isinstance(value, tuple) and value and value[0] == "__in__":
                if str(row.get(column)) not in {str(v) for v in value[1]}:
                    return False
            elif str(row.get(column)) != str(value):
                return False
        return True

    def execute(self):
        self.db.log.append((self._op, self.table, dict(self._payload or {})))
        if self._op == "select":
            rows = [dict(r) for r in self.db.tables.get(self.table, [])
                    if self._match(r)]
            if self._single:
                return _Res(rows[0] if rows else None)
            return _Res(rows)
        if self._op == "update":
            hits = [r for r in self.db.tables.get(self.table, []) if self._match(r)]
            for row in hits:
                row.update(self._payload)
            return _Res([dict(r) for r in hits])
        if self._op == "insert":
            self.db._counter += 1
            row = dict(self._payload)
            row.setdefault("id", f"{self.table}-{self.db._counter}")
            self.db.tables.setdefault(self.table, []).append(row)
            return _Res([dict(row)])
        raise AssertionError(self._op)


class _DB:
    def __init__(self, **tables):
        self.tables = {name: [dict(r) for r in rows] for name, rows in tables.items()}
        self.log = []
        self._counter = 0

    def table(self, name):
        return _Query(self, name)


def _now():
    return datetime.now(timezone.utc)


def _exam(**kw):
    base = {
        "id": "exam-1",
        "anti_cheat_enabled": True,
        "max_violations": 5,
        "penalty_per_violation": 5,
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
        "id": "sub-1",
        "exam_id": "exam-1",
        "student_id": "stu-1",
        "status": "locked_pending_resume",
        "started_at": started_at.isoformat(),
        "answers": {"1": "A"},
        "resume_limit": 2,
        "resume_count_used": 0,
        "locked_at": None,
        "last_resumed_at": None,
    }
    base.update(kw)
    return base


def _written(db, table="submissions"):
    return [payload for op, name, payload in db.log
            if name == table and op == "update"]


# ── the staff door: no code, the sitting's own row ──────────────────────────

class TestManualUnlock:
    """The allowance is spent or the phone is dead — the code is not the way in.

    A school that sets `resume_code_limit` to 0 hands every locked pupil to the
    teacher on purpose, so this route is not a back door: it is the documented
    second half of the policy, and it must obey the same one clock.
    """

    def test_the_module_offers_a_manual_door(self):
        assert hasattr(rc, "manual_unlock"), (
            "`resume_code` can only reopen a sitting with the pupil's own code, so a "
            "spent allowance (`resume_code_limit: 0`) strands the pupil with no door "
            "at all")

    def test_it_reopens_a_locked_sitting_without_a_code(self):
        started = _now() - timedelta(minutes=10)
        db = _DB(submissions=[_row(started)])
        out = rc.manual_unlock(db, db.tables["submissions"][0], _exam())
        assert out["ok"] and out["action"] == rc.RESUMED
        assert db.tables["submissions"][0]["status"] == "draft"

    def test_it_works_when_the_allowance_is_spent(self):
        """The whole reason it exists: the code path would have refused here."""
        db = _DB(submissions=[_row(_now() - timedelta(minutes=5),
                                   resume_count_used=2, resume_limit=2)])
        out = rc.manual_unlock(db, db.tables["submissions"][0], _exam())
        assert out["ok"], (
            "the manual door inherited the code's allowance check, so a pupil whose "
            "allowance is spent still cannot be let back in")

    def test_it_moves_no_clock_column(self):
        db = _DB(submissions=[_row(_now() - timedelta(minutes=5))])
        rc.manual_unlock(db, db.tables["submissions"][0], _exam())
        payload = _written(db)[-1]
        for column in ("started_at", "submitted_at", "end_at", "locked_at"):
            assert column not in payload, (
                f"the manual door wrote `{column}`; unlocking is never a way to buy "
                f"time, and the deadline is derived from `started_at`")

    def test_it_refuses_a_paper_that_is_not_locked(self):
        db = _DB(submissions=[_row(_now() - timedelta(minutes=5), status="draft")])
        out = rc.manual_unlock(db, db.tables["submissions"][0], _exam())
        assert out["reason"] == rc.NOT_LOCKED
        assert not _written(db)

    def test_it_refuses_after_the_deadline(self):
        """Past the clock the honest offer is finalise, not unlock."""
        db = _DB(submissions=[_row(_now() - timedelta(minutes=61))])
        out = rc.manual_unlock(db, db.tables["submissions"][0], _exam())
        assert out["ok"] is False
        assert out["reason"] == rc.DEADLINE_PASSED
        assert out["action"] == rc.FINALIZE
        assert db.tables["submissions"][0]["status"] == "locked_pending_resume", (
            "a manual unlock past the deadline reopened a paper that is already over")
        assert not _written(db)

    def test_it_counts_against_the_allowance(self):
        """A manual reopen is still a reopen, and the count is the audit trail."""
        db = _DB(submissions=[_row(_now() - timedelta(minutes=5))])
        rc.manual_unlock(db, db.tables["submissions"][0], _exam())
        assert db.tables["submissions"][0]["resume_count_used"] == 1
        assert db.tables["submissions"][0]["last_resumed_at"]


# ── the reason vocabulary, bilingual in the browser ─────────────────────────

class TestTheReasonVocabulary:
    def test_every_refusal_reason_has_a_key(self):
        for reason in (rc.WRONG_CODE, rc.NOT_LOCKED, rc.DEADLINE_PASSED,
                       rc.LIMIT_REACHED):
            key = rc.reason_key(reason)
            assert key and key != reason, (
                f"`{reason}` reaches the page as raw server text, so the pupil reads "
                f"an English identifier instead of a sentence in their language")

    def test_success_and_the_write_failure_have_keys_too(self):
        assert rc.reason_key("") == "resume_ok"
        assert rc.reason_key("write_failed") == "resume_write_failed"

    def test_an_unknown_reason_still_has_somewhere_to_go(self):
        assert rc.reason_key("something-new") == "resume_failed"


# ── the pupil's own door ────────────────────────────────────────────────────

def _block(source: str, needle: str) -> str:
    """The function body starting at `needle`, up to the next route decorator."""
    assert needle in source, f"{needle!r} is not in the file at all"
    body = source.split(needle, 1)[1]
    return body.split("\n@student_bp.route", 1)[0].split("\n@teacher_bp.route", 1)[0]


class TestThePupilResumeRoute:
    def _src(self) -> str:
        return STUDENT.read_text(encoding="utf-8")

    def test_the_route_exists(self):
        assert re.search(r'@student_bp\.route\(\s*"/exams/<exam_id>/resume"\s*,\s*'
                         r'methods=\["POST"\]', self._src()), (
            "there is no resume door on the pupil side, so a locked pupil's typed "
            "code is sent nowhere")

    def test_it_is_only_for_a_logged_in_pupil(self):
        block = _block(self._src(), '"/exams/<exam_id>/resume"')
        assert "@login_required" in block

    def test_the_pupil_is_read_from_the_session_not_the_request(self):
        """The one thing that must never come off the wire for this door."""
        block = _block(self._src(), '"/exams/<exam_id>/resume"')
        assert "g.user_id" in block, (
            "the route does not use the logged-in pupil's own id")
        for bad in ('request.form.get("student_id")', 'request.args.get("student_id")',
                    'request.json.get("student_id")',
                    "data.get(\"student_id\")", 'payload.get("student_id")'):
            assert bad not in block, (
                f"the resume door reads a pupil id from the request ({bad}), so one "
                f"pupil could reopen another pupil's paper")

    def test_it_calls_the_one_unlock_gate(self):
        block = _block(self._src(), '"/exams/<exam_id>/resume"')
        assert "unlock(" in block, (
            "the route re-implements the gate instead of asking `resume_code`")

    def test_it_finalises_when_the_gate_says_the_paper_is_over(self):
        """A correct code after the deadline must close the paper, not strand it.

        `finalize_expired` is the sweep's own on-demand door and the module names
        the lock screen as one of its two callers, so it is the one writer for this.
        """
        block = _block(self._src(), '"/exams/<exam_id>/resume"')
        assert "finalize_expired(" in block, (
            "a code typed after the deadline leaves the paper locked forever")

    def test_it_answers_with_the_key_not_a_sentence(self):
        block = _block(self._src(), '"/exams/<exam_id>/resume"')
        assert "reason_key(" in block, (
            "the refusal travels as a raw reason, so the page has to know the "
            "sever-side vocabulary")


def _route_block(source: str, needle: str) -> str:
    """A whole route: the decorators above the `def` through the next decorator.

    `_block` starts *at* a needle, which for a function name skips the decorators
    that carry the role guard — the very thing these guards are about. So this
    helper walks back to the route decorator the function belongs to.
    """
    at = source.index(needle)
    start = source.rindex("@", 0, at)
    end = source.find("\n@", at)
    return source[start:end if end != -1 else len(source)]


class TestTheStaffUnlockRoute:
    def _src(self) -> str:
        return TEACHER.read_text(encoding="utf-8")

    def _name(self) -> str:
        match = re.search(r"def (\w*unlock\w*)", self._src())
        assert match, "no manual unlock function exists in the teacher module"
        return match.group(1)

    def _route(self) -> str:
        return _route_block(self._src(), f"def {self._name()}(")

    def test_the_route_exists(self):
        assert re.search(r'@teacher_bp\.route\([^)]*unlock[^)]*'
                         r'methods=\["POST"\]', self._src()), (
            "a teacher has no way to let a locked pupil back in when the code is spent")

    def test_it_requires_the_teacher_role(self):
        assert "@teacher_required" in self._route()

    def test_its_authority_is_the_exams_the_teacher_holds(self):
        """Being a teacher is not authority over a colleague's paper."""
        assert "_teacher_code_exam_ids" in self._route(), (
            "the manual unlock is not gated on the invigilated-or-owned exam set, so "
            "any teacher could reopen any paper in the school")

    def test_it_calls_the_shared_gate(self):
        """`manual_unlock` still owns the rule; the route reaches it through the one
        service every door shares, so the officials' doors cannot drift from this
        one."""
        assert "unlock_sitting(" in self._route()


# ── the lock screen, driven by the server's own key ─────────────────────────

class TestTheLockScreen:
    def _src(self) -> str:
        return TEMPLATE.read_text(encoding="utf-8")

    def test_the_page_has_a_lock_screen_at_all(self):
        src = self._src()
        assert 'x-show="locked' in src, (
            "the exam page has no state for a locked sitting, so a locked pupil sees "
            "an ordinary paper that silently refuses every save")

    def test_the_screen_has_a_code_field(self):
        src = self._src()
        assert "resumeCodeInput" in src and "sg-resume-code" in src, (
            "the lock screen has nowhere to type the code the pupil was told to keep")

    def test_the_screen_posts_to_the_resume_door(self):
        src = self._src()
        assert "/resume'" in src or "/resume`" in src or "/resume\"" in src, (
            "the lock screen never calls the resume endpoint")

    def test_the_message_comes_from_the_servers_key(self):
        """`message_key` was produced by `attempt_status` and read by nothing."""
        src = self._src()
        assert "MESSAGE_TEXT" in src, (
            "the page does not translate the server's `message_key`, so the status "
            "the server computed is reduced to a raw identifier")
        for key in ("attempt_active", "attempt_locked", "attempt_finished",
                    "attempt_expired", "attempt_missing"):
            assert key in src, f"`{key}` has no sentence on the page"

    def test_the_keys_are_bilingual(self):
        block = self._src().split("MESSAGE_TEXT", 1)[1][:1500]
        assert re.search(r"\bid\s*:", block) and re.search(r"\ben\s*:", block), (
            "the message panel is written in one language only")

    def test_the_clock_keeps_counting_on_the_lock_screen(self):
        """Locking is not a pause, and the screen has to say so out loud."""
        src = self._src()
        assert "Waktu ujian tetap berjalan" in src, (
            "the lock screen does not tell the pupil their clock is still running, "
            "which is exactly the illusion the timing work removed")

    def test_the_status_response_is_applied(self):
        src = self._src()
        assert "_applyServerStatus" in src, (
            "nothing folds the status half of the heartbeat/status payload into the "
            "page, so a server-side lock is invisible until some other write fails")


# ── the staff page has to know which papers are locked ──────────────────────

class TestTheStaffPageKnowsWhichPapersAreLocked:
    """A button that offers to reopen a paper must know which papers are locked.

    Otherwise it is drawn on every pupil, and the invigilator is left guessing —
    and a paper that is merely a draft is not "locked", so saying it is would send
    a teacher looking for a lock that is not there.
    """

    def _db(self, status):
        return _DB(
            exam_access_codes=[{"id": "c1", "exam_id": "exam-1",
                                "student_id": "stu-1", "code": "123456"}],
            exams=[{"id": "exam-1", "title": "T1"}],
            students=[{"id": "stu-1", "school_id": "sch-1",
                       "profiles": {"full_name": "Pupil"}}],
            submissions=[{"exam_id": "exam-1", "student_id": "stu-1",
                          "status": status}],
        )

    def test_a_locked_sitting_is_marked_locked(self):
        from app.services import exam_codes
        rows = exam_codes.codes_for_exams(self._db("locked_pending_resume"),
                                          "sch-1", ["exam-1"])
        assert rows and rows[0]["is_locked"] is True
        assert rows[0]["sitting_status"] == "locked_pending_resume"

    def test_a_draft_sitting_is_not_offered_as_locked(self):
        from app.services import exam_codes
        rows = exam_codes.codes_for_exams(self._db("draft"), "sch-1", ["exam-1"])
        assert rows and rows[0]["is_locked"] is False

    def test_the_read_still_writes_nothing(self):
        from app.services import exam_codes
        db = self._db("locked_pending_resume")
        exam_codes.codes_for_exams(db, "sch-1", ["exam-1"])
        assert all(op == "select" for op, _t, _p in db.log), (
            "a read grew a write")

    def test_an_empty_authority_reads_nothing(self):
        from app.services import exam_codes
        db = self._db("locked_pending_resume")
        assert exam_codes.codes_for_exams(db, "sch-1", []) == []

    def test_the_invigilation_page_draws_the_unlock_button(self):
        block = (ROOT / "app" / "templates" / "teacher" / "invigilation.html"
                 ).read_text(encoding="utf-8")
        assert "/locked/" in block and "unlock" in block, (
            "the codes table has no way to act on a locked sitting")
        assert "c.is_locked" in block, (
            "the unlock button is not gated on the sitting actually being locked")

    def test_the_page_has_sentences_for_the_new_refusals(self):
        notes = (ROOT / "app" / "templates" / "shared" /
                 "_invigilation_reasons.html").read_text(encoding="utf-8")
        for key in ("exam_not_yours", "unlock_ok", "submission_finalized",
                    "not_locked", "deadline_passed"):
            assert f"'{key}'" in notes, (
                f"`{key}` is flashed but the page has no sentence for it, so the "
                f"invigilator reads a raw identifier")


# ── the officials' door: the same unlock, when no teacher is on duty ────────

def _handler(source: str, route_decorator: str) -> str:
    """A whole handler from its route decorator through the next one.

    ``_route_block`` walks back only to the nearest ``@`` before the ``def``, which
    for a route with a second decorator (`@open_year_required`) stops *after* the
    role guard these guards are about. This starts at the route line instead, so
    every decorator on the handler is inside the slice.
    """
    at = source.index(route_decorator)
    nxt = source.find("bp.route(", at + len(route_decorator))
    end = source.rfind("\n@", at, nxt) if nxt != -1 else len(source)
    return source[at:end if end != -1 else len(source)]


class TestTheOfficialUnlockDoor:
    """A locked pupil's way back cannot depend on which official is on duty.

    The teacher's door is bounded to the exams they hold. A vice principal and the
    school admin answer for the whole school, so a school with nobody invigilating
    that paper still needs the same door — and it has to be the *same* door
    underneath, or one would finalise a paper past its deadline while the other
    quietly reopened it.
    """

    def test_the_vice_principal_door_exists(self):
        src = PRINCIPAL.read_text(encoding="utf-8")
        assert re.search(
            r'@principal_bp\.route\(\s*"/vice-principal/locked/'
            r'<exam_id>/<student_id>/unlock",\s*methods=\["POST"\]\)', src), (
            "a vice principal cannot reopen a locked sitting")

    def test_it_requires_the_vice_principal_role(self):
        block = _handler(PRINCIPAL.read_text(encoding="utf-8"),
                         '@principal_bp.route("/vice-principal/locked/')
        assert "@vice_principal_required" in block, (
            "the officials' unlock is guarded by the wrong role")

    def test_the_admin_door_exists(self):
        src = ADMIN.read_text(encoding="utf-8")
        assert re.search(
            r'@admin_sekolah_bp\.route\(\s*"/locked/<exam_id>/<student_id>/unlock",'
            r'\s*methods=\["POST"\]\)', src), (
            "a school admin cannot reopen a locked sitting")

    def test_the_admin_door_requires_the_admin_role(self):
        block = _handler(ADMIN.read_text(encoding="utf-8"),
                         '@admin_sekolah_bp.route("/locked/<exam_id>/<student_id>/unlock"')
        assert "@admin_sekolah_required" in block

    def test_both_officials_call_the_same_service(self):
        for path, marker in (
                (PRINCIPAL, '@principal_bp.route("/vice-principal/locked/'),
                (ADMIN, '@admin_sekolah_bp.route("/locked/<exam_id>/<student_id>/unlock"')):
            block = _handler(path.read_text(encoding="utf-8"), marker)
            assert "unlock_sitting(" in block, (
                f"{path.name}: the officials' unlock does not call the shared "
                f"service, so its rules can drift from the teacher's")

    def test_the_school_is_the_sessions_never_the_requests(self):
        block = _handler(ADMIN.read_text(encoding="utf-8"),
                         '@admin_sekolah_bp.route("/locked/<exam_id>/<student_id>/unlock"')
        assert "_school_id()" in block, (
            "the admin unlock does not take the school from the session")

    def test_the_official_page_draws_the_unlock_button(self):
        src = OFFICIAL_PAGE.read_text(encoding="utf-8")
        assert "{{ invigilation_base }}/locked/" in src and "unlock" in src, (
            "the officials' codes table has no way to act on a locked sitting")
        assert "c.is_locked" in src, (
            "the officials' unlock button is not gated on the sitting being locked")
        assert "can_write" in src, (
            "the read-only head-of-school page would draw a button it cannot post")


class TestTheSharedManualUnlock:
    """One implementation behind every door — proven against the fake PostgREST.

    What these pin is the part that must not differ between callers: the school
    check, the "not locked is not a recovery" rule, the clock that never moves, and
    a paper whose clock ended being finalised rather than reopened.
    """

    def _db(self, *, exam_school="sch-1", status="locked_pending_resume",
            started_at=None):
        started = started_at or _now()
        return _DB(
            exams=[_exam(id="exam-1", school_id=exam_school, duration_minutes=60)],
            submissions=[_row(started, id="sub-1", status=status)],
        )

    def _quiet(self, monkeypatch):
        """Silence the audit trail: it needs an app context the fake db has not."""
        from app.services import sitting_unlock
        monkeypatch.setattr(sitting_unlock, "log_activity",
                            lambda *a, **k: None, raising=False)

    def test_it_reopens_a_locked_sitting(self, monkeypatch):
        self._quiet(monkeypatch)
        from app.services import sitting_unlock
        db = self._db()
        out = sitting_unlock.unlock_sitting(db, "sch-1", "exam-1", "stu-1", "staff-1")
        assert out["ok"] and out["action"] == "unlocked"
        assert db.tables["submissions"][0]["status"] == "draft"

    def test_it_moves_no_clock_column(self, monkeypatch):
        self._quiet(monkeypatch)
        from app.services import sitting_unlock
        db = self._db()
        sitting_unlock.unlock_sitting(db, "sch-1", "exam-1", "stu-1", "staff-1")
        for column in ("started_at", "end_at", "submitted_at"):
            assert all(column not in payload for payload in _written(db)), (
                f"the officials' unlock wrote `{column}`; reopening is never a way "
                f"to buy time")

    def test_it_refuses_another_schools_exam(self, monkeypatch):
        self._quiet(monkeypatch)
        from app.services import sitting_unlock
        db = self._db(exam_school="sch-2")
        out = sitting_unlock.unlock_sitting(db, "sch-1", "exam-1", "stu-1", "staff-1")
        assert out["ok"] is False and out["reason"] == "exam_not_in_school"
        assert _written(db) == [], "a foreign exam was reopened anyway"

    def test_a_sitting_that_is_not_locked_is_not_a_recovery(self, monkeypatch):
        self._quiet(monkeypatch)
        from app.services import sitting_unlock
        db = self._db(status="draft")
        out = sitting_unlock.unlock_sitting(db, "sch-1", "exam-1", "stu-1", "staff-1")
        assert out["ok"] is False and out["reason"] == rc.NOT_LOCKED

    def test_past_the_deadline_it_finalises_not_reopens(self, monkeypatch):
        self._quiet(monkeypatch)
        from app.services import sitting_unlock
        db = self._db(started_at=_now() - timedelta(minutes=90))
        out = sitting_unlock.unlock_sitting(db, "sch-1", "exam-1", "stu-1", "staff-1")
        assert out["action"] == "finalize" and out["reason"] == "submission_finalized"
        assert db.tables["submissions"][0]["status"] != "draft", (
            "a paper whose clock ended was reopened instead of finalised")
