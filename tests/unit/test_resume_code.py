"""A locked pupil gets their paper back — and never gets their clock back.

Requested: an automatic way back for a pupil locked out by exceeding the
fullscreen/tab-switch threshold, "DENGAN WAKTU UJIAN YANG TIDAK PERNAH RESET ATAU
BERTAMBAH akibat penguncian/resume".

That last clause is the whole test file. Everything else here is machinery around
one invariant, and the invariant is arithmetic rather than policy:

    `started_at` is the only input to the deadline, so any transition that writes
    `started_at` moves the deadline. Locking must not write it. Reopening must not
    write it. Finalising must not write it.

`app/utils/exam_window.deadline()` already derives the end of a sitting from
`started_at` plus the exam's own duration (and the window end, when the exam is set
to stop there). So this code never computes a deadline at all — it asks, and then it
must be able to *prove* it left it alone: the guards below assert the exact payload of
each transition, because "did not change the deadline" is only believable if the
column it is derived from is absent from every write.

One code, and it already existed
--------------------------------
The first version of this feature minted `submissions.resume_code` — a *second* code
on a screen that already shows the recovery code (`exam_access_codes`, issued by
`app/utils/exam_recovery.py`). Two codes meaning "a way back into this paper" is the
parallel-subsystem shape this repository keeps closing, and no pupil can answer
"which one do I type?". So the lock now borrows the code that is already there, and
the guards below pin that: **the migration adds no code column**, and the unlock path
resolves the code through `exam_recovery`.

Three further decisions, each pinned with its reason:

* **Locking is opt-in.** `exams.max_violations` already ends a sitting — the graduated
  ladder's `auto_submit_on_max` (migration 011) — and that is a production behaviour.
  Turning it into a *lock* for every existing exam would silently change what happens
  to a pupil mid-paper. The flag defaults false.
* **The threshold is the exam's own `max_violations`**, not a second number.
* **At or past the deadline, a lock is refused**, and so is a correct code typed after
  it. One helper answers both, so the two doors cannot disagree about the boundary.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "supabase" / "migrations" / "054_resume_code.sql"
SERVICE = ROOT / "app" / "services" / "resume_code.py"
RECOVERY = ROOT / "app" / "utils" / "exam_recovery.py"
SUBMISSION_SERVICE = ROOT / "app" / "services" / "submission_service.py"


# ── a PostgREST stand-in that applies its writes ────────────────────────────
#
# Applying rather than recording matters here: the assertions are about what a
# transition *wrote*, so a fake that only logged payloads would let a service that
# never actually updated the row pass.

class _Res:
    def __init__(self, data):
        self.data = data
        self.count = len(data or [])


class _Query:
    def __init__(self, db, table):
        self.db = db
        self.table = table
        self._filters: list[tuple[str, object]] = []
        self._op = "select"
        self._payload = None
        #: `maybe_single()`/`single()` answer with ONE object, not a list —
        #: modelling that faithfully matters, because `row_or_none` unwraps
        #: `.data` and a fake that hands back a list makes the real caller call
        #: `.get` on a list.
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
        return all(str(row.get(c)) == str(v) for c, v in self._filters)

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
        self.log: list[tuple] = []
        self._counter = 0

    def table(self, name):
        return _Query(self, name)


class _SingleDB(_DB):
    """Kept as a name the tests read as intent; single-ness is per query now."""


def _now():
    return datetime.now(timezone.utc)


def _exam(**kw):
    """An exam that has chosen to lock. Duration 60, so the deadline is knowable."""
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
        "status": "draft",
        "started_at": started_at.isoformat(),
        "answers": {"1": "A"},
        "resume_limit": 2,
        "resume_count_used": 0,
        "locked_at": None,
        "last_resumed_at": None,
    }
    base.update(kw)
    return base


def _code(code="123456", *, student_id="stu-1", exam_id="exam-1"):
    """The pupil's own recovery code, as `exam_access_codes` holds it."""
    return {"id": "code-1", "student_id": student_id, "exam_id": exam_id,
            "code": code, "created_at": _now().isoformat()}


def _locked_db(started_at, *, codes=None, **row_kw):
    return _SingleDB(
        submissions=[_row(started_at, status="locked_pending_resume", **row_kw)],
        exam_access_codes=codes if codes is not None else [_code()],
    )


# ── the schema ──────────────────────────────────────────────────────────────

def _sql() -> str:
    return MIGRATION.read_text(encoding="utf-8")


class TestTheSchema:
    def test_the_migration_exists_at_all(self):
        assert MIGRATION.exists(), (
            "the lock gate has no migration, so the columns it writes cannot be "
            "saved on any deployment")

    def test_it_adds_every_column_the_service_writes(self):
        sql = _sql()
        for column in ("resume_limit", "resume_count_used", "locked_at",
                       "last_resumed_at"):
            assert re.search(rf"ADD COLUMN IF NOT EXISTS\s+{column}\b", sql), (
                f"`submissions.{column}` is written by the service but never "
                f"created here, which is a 500 on the first lock")

    def test_it_adds_no_second_code(self):
        """The reconciliation, pinned: one code, and it is the recovery code."""
        sql = _sql()
        assert not re.search(r"ADD COLUMN IF NOT EXISTS\s+resume_code\b", sql), (
            "the migration adds a second code column beside the recovery code that "
            "is already on the exam screen — two codes, one screen, and the pupil "
            "cannot tell which to type")
        assert not re.search(r"idx_submissions_resume_code|"
                             r"idx_submissions_locked_resume_code", sql), (
            "there is still an index over a code column this table does not have")

    def test_the_unlock_key_is_the_recovery_code_table(self):
        """Named where it lives, so a reader knows which code this is."""
        assert "exam_access_codes" in _sql(), (
            "the migration does not say which code the lock borrows, so the next "
            "reader has to go looking for a second one")
        assert "idx_access_codes_code" in _sql()

    def test_it_extends_the_status_vocabulary_without_dropping_the_old_one(self):
        """`003` set the vocabulary; a lock is a status, so it has to join it."""
        sql = _sql()
        assert "DROP CONSTRAINT IF EXISTS submissions_status_check" in sql
        assert "submissions_status_check" in sql
        assert "locked_pending_resume" in sql
        for status in ("draft", "submitted", "graded", "published", "retracted"):
            assert f"'{status}'" in sql, (
                f"the new vocabulary forgot `{status}`, so a row already carrying "
                f"it becomes invalid the moment this migration runs")

    def test_it_introduces_no_second_source_of_time(self):
        """The one rule the whole feature rests on: nothing here can extend time.

        A column called `extra_time`, `frozen_until` or `paused_at` would be a
        second answer to "when does this end", and the first code that read it
        would be a deadline that moves. The migration must not offer one.
        """
        sql = _sql()
        added = re.findall(r"ADD COLUMN IF NOT EXISTS\s+([a-z_]+)", sql)
        forbidden = ("extra", "frozen", "freeze", "pause", "extend", "extension",
                     "bonus", "additional", "compensat", "resume_until",
                     "resume_expires", "extra_minutes")
        offenders = [c for c in added if any(f in c for f in forbidden)]
        assert not offenders, (
            "a time-giving column would be a second deadline: " + ", ".join(offenders))

    def test_it_touches_none_of_the_deadline_columns(self):
        sql = _sql()
        for column in ("started_at", "duration_minutes", "end_at",
                       "auto_submit_on_window_end"):
            assert not re.search(
                rf"ALTER TABLE\s+(public\.)?(submissions|exams)[^;]*\b"
                rf"(DROP COLUMN|ALTER COLUMN)\s+{column}\b", sql, re.S), (
                f"this migration alters `{column}`, which is an input to the "
                f"deadline — the one thing it must never do")

    def test_it_is_idempotent_and_non_destructive(self):
        sql = _sql()
        assert "DROP TABLE" not in sql.upper()
        assert "DROP COLUMN" not in sql.upper()
        assert not re.search(r"\bDELETE\s+FROM\b", sql, re.I)
        adds = re.findall(r"ALTER TABLE[^;]*ADD COLUMN\s+(IF NOT EXISTS)", sql, re.I)
        assert len(adds) == sql.lower().count("add column"), (
            "an ADD COLUMN is unguarded, so running this migration twice fails")

    def test_the_locking_flag_is_opt_in(self):
        """Default false: no exam that exists today may change what it does."""
        sql = _sql()
        flag = re.search(r"ADD COLUMN IF NOT EXISTS\s+lock_pending_resume\s+"
                         r"[A-Za-z]+\s+NOT NULL\s+DEFAULT\s+(true|false)", sql, re.I)
        assert flag, "`exams.lock_pending_resume` is missing or not defaulted"
        assert flag.group(1).lower() == "false", (
            "locking on by default would silently turn the ladder's auto-submit "
            "into a lock-out for every exam already running")

    def test_the_locked_rows_are_the_ones_indexed(self):
        """Uniqueness/ordering is only load-bearing while a paper is locked."""
        sql = _sql()
        assert re.search(
            r"CREATE INDEX[^;]*ON submissions\(status\)[^;]*"
            r"WHERE[^;]*status\s*=\s*'locked_pending_resume'", sql, re.S | re.I), (
            "nothing indexes the locked rows, so the queue a teacher or invigilator "
            "reads is a full scan of every sitting ever taken")


# ── the service, and the one code it borrows ────────────────────────────────

def test_the_module_exists():
    assert SERVICE.exists(), "the resume-code service was never written"


class TestItMintsNothing:
    def test_the_service_has_no_code_generator(self):
        source = SERVICE.read_text(encoding="utf-8")
        for gone in ("def generate_code", "ALPHABET", "def issue("):
            assert gone not in source, (
                f"`{gone}` is back, which means a second code exists beside the "
                f"recovery code that is already on the pupil's screen")

    def test_the_code_is_resolved_through_the_recovery_module(self):
        source = SERVICE.read_text(encoding="utf-8")
        assert "exam_recovery" in source and "matches" in source, (
            "the unlock path does not ask the recovery module, so it must be "
            "comparing the code itself — a second answer to \"is this code theirs\"")

    def test_the_recovery_module_answers_without_redeeming(self):
        """A failed unlock must not stamp the code as used: `redeem_code` records
        first use, and answering \"is this theirs\" must not have that side effect."""
        source = RECOVERY.read_text(encoding="utf-8")
        helper = source.split("def matches(", 1)[1].split("\ndef ", 1)[0]
        assert "is_used" not in helper and "update(" not in helper, (
            "the non-redeeming lookup writes to the code row")
        assert "student_id" in helper, (
            "the lookup is not scoped to the caller, so a guess would search every "
            "pupil's codes")


class TestTheThreshold:
    def test_it_reads_the_exams_own_max_violations(self):
        from app.services import resume_code as rc
        assert rc.threshold(_exam(max_violations=3)) == 3
        assert rc.threshold(_exam(max_violations=7)) == 7

    def test_unlimited_violations_never_locks(self):
        from app.services import resume_code as rc
        assert rc.decide(_exam(max_violations=0), _row(_now()), 99) == rc.NONE

    def test_below_the_threshold_does_nothing(self):
        from app.services import resume_code as rc
        assert rc.decide(_exam(max_violations=5), _row(_now()), 4) == rc.NONE

    def test_at_the_threshold_before_the_deadline_it_locks(self):
        from app.services import resume_code as rc
        assert rc.decide(_exam(), _row(_now()), 5) == rc.LOCK

    def test_at_or_after_the_deadline_it_finalises_instead_of_locking(self):
        from app.services import resume_code as rc
        started = _now() - timedelta(minutes=61)
        assert rc.decide(_exam(), _row(started), 5) == rc.FINALIZE
        # And exactly at the deadline: the prompt's "TEPAT atau SETELAH".
        started = _now() - timedelta(minutes=60)
        assert rc.decide(_exam(), _row(started), 5) == rc.FINALIZE

    def test_an_exam_that_has_not_opted_in_is_never_locked(self):
        from app.services import resume_code as rc
        assert rc.decide(_exam(lock_pending_resume=False), _row(_now()), 99) == rc.NONE

    def test_anti_cheat_switched_off_means_nothing_is_counted_either(self):
        from app.services import resume_code as rc
        assert rc.decide(_exam(anti_cheat_enabled=False), _row(_now()), 99) == rc.NONE

    def test_an_unlimited_duration_exam_has_no_deadline_to_pass(self):
        from app.services import resume_code as rc
        assert rc.decide(_exam(duration_minutes=0), _row(_now() - timedelta(days=3)),
                         5) == rc.LOCK


# ── the transitions, and the clock they must not move ──────────────────────

def _written(db, table="submissions") -> list[dict]:
    return [payload for op, name, payload in db.log
            if name == table and op == "update"]


class TestLockingDoesNotMoveTheClock:
    def test_the_lock_payload_contains_no_clock_column(self):
        from app.services import resume_code as rc
        started = _now() - timedelta(minutes=10)
        db = _DB(submissions=[_row(started)])
        out = rc.lock(db, db.tables["submissions"][0], _exam(), violations=5)
        assert out["ok"] and out["action"] == rc.LOCK
        payload = _written(db)[-1]
        assert payload["status"] == rc.LOCKED
        for column in ("started_at", "submitted_at", "end_at"):
            assert column not in payload, (
                f"the lock wrote `{column}`, which moves the deadline — the one "
                f"thing locking may never do")

    def test_the_row_keeps_its_started_at_after_locking(self):
        from app.services import resume_code as rc
        started = _now() - timedelta(minutes=10)
        db = _DB(submissions=[_row(started)])
        before = db.tables["submissions"][0]["started_at"]
        rc.lock(db, db.tables["submissions"][0], _exam(), violations=5)
        assert db.tables["submissions"][0]["started_at"] == before
        assert db.tables["submissions"][0]["locked_at"]

    def test_a_lock_after_the_deadline_is_refused_rather_than_written(self):
        from app.services import resume_code as rc
        db = _DB(submissions=[_row(_now() - timedelta(minutes=90))])
        out = rc.lock(db, db.tables["submissions"][0], _exam(), violations=5)
        assert out["ok"] is False and out["action"] == rc.FINALIZE
        assert db.tables["submissions"][0]["status"] == "draft", (
            "a lock was written past the deadline, holding a pupil out of a paper "
            "that is already over")


class TestReopeningDoesNotMoveTheClock:
    def test_the_right_code_before_the_deadline_returns_the_paper(self):
        from app.services import resume_code as rc
        started = _now() - timedelta(minutes=10)
        db = _locked_db(started)
        out = rc.unlock(db, db.tables["submissions"][0], _exam(),
                        student_id="stu-1", code="123456")
        assert out["ok"] and out["action"] == rc.RESUMED
        row = db.tables["submissions"][0]
        assert row["status"] == "draft"
        assert row["resume_count_used"] == 1
        assert row["started_at"] == started.isoformat()

    def test_the_deadline_after_reopening_is_the_deadline_before(self):
        """The single most important assertion in the feature.

        The elapsed time is not returned, the deadline is not shifted, and the
        sitting is not paused: a pupil locked for three minutes loses those three
        minutes, exactly as if they had stared at the wall for three.
        """
        from app.utils import exam_window
        from app.services import resume_code as rc
        exam = _exam(duration_minutes=60)
        started = _now() - timedelta(minutes=30)
        db = _locked_db(started)
        before = exam_window.deadline(exam, db.tables["submissions"][0]["started_at"])
        rc.unlock(db, db.tables["submissions"][0], exam,
                  student_id="stu-1", code="123456")
        after = exam_window.deadline(exam, db.tables["submissions"][0]["started_at"])
        assert before == after, "reopening moved the deadline"
        assert before == started + timedelta(minutes=60)

    def test_the_payload_contains_no_clock_column(self):
        from app.services import resume_code as rc
        db = _locked_db(_now() - timedelta(minutes=5))
        rc.unlock(db, db.tables["submissions"][0], _exam(),
                  student_id="stu-1", code="123456")
        payload = _written(db)[-1]
        for column in ("started_at", "submitted_at", "end_at", "locked_at"):
            assert column not in payload, (
                f"reopening wrote `{column}`; a resume that clears the lock stamp "
                f"or moves the start is a resume that changes the arithmetic")

    def test_a_code_entered_after_the_deadline_is_refused_and_finalised(self):
        from app.services import resume_code as rc
        db = _locked_db(_now() - timedelta(minutes=61))
        out = rc.unlock(db, db.tables["submissions"][0], _exam(),
                        student_id="stu-1", code="123456")
        assert out["ok"] is False
        assert out["reason"] == rc.DEADLINE_PASSED
        assert out["action"] == rc.FINALIZE
        assert db.tables["submissions"][0]["status"] == rc.LOCKED, (
            "the paper was reopened past the deadline, letting the pupil answer "
            "outside the time the sitting was given")
        assert not _written(db), "a refused resume still wrote to the row"

    def test_the_wrong_code_is_refused(self):
        from app.services import resume_code as rc
        db = _locked_db(_now() - timedelta(minutes=5))
        out = rc.unlock(db, db.tables["submissions"][0], _exam(),
                        student_id="stu-1", code="999999")
        assert out["ok"] is False and out["reason"] == rc.WRONG_CODE
        assert not _written(db), "a refused resume still wrote to the row"

    def test_another_pupils_code_is_refused(self):
        """The lookup is scoped to the caller, so a code that is real — for somebody
        else — is still not this pupil's."""
        from app.services import resume_code as rc
        db = _locked_db(_now() - timedelta(minutes=5),
                        codes=[_code("123456", student_id="someone-else")])
        out = rc.unlock(db, db.tables["submissions"][0], _exam(),
                        student_id="stu-1", code="123456")
        assert out["reason"] == rc.WRONG_CODE

    def test_a_code_for_another_exam_is_refused(self):
        from app.services import resume_code as rc
        db = _locked_db(_now() - timedelta(minutes=5),
                        codes=[_code("123456", exam_id="a-different-exam")])
        out = rc.unlock(db, db.tables["submissions"][0], _exam(),
                        student_id="stu-1", code="123456")
        assert out["reason"] == rc.WRONG_CODE

    def test_an_empty_or_malformed_code_is_refused(self):
        from app.services import resume_code as rc
        for bad in ("", "   ", "12345", "abcdef", "1234567"):
            db = _locked_db(_now() - timedelta(minutes=5))
            out = rc.unlock(db, db.tables["submissions"][0], _exam(),
                            student_id="stu-1", code=bad)
            assert out["reason"] == rc.WRONG_CODE, bad
            assert not _written(db)

    def test_a_paper_that_is_not_locked_cannot_be_reopened(self):
        from app.services import resume_code as rc
        db = _SingleDB(submissions=[_row(_now() - timedelta(minutes=5))],
                       exam_access_codes=[_code()])
        out = rc.unlock(db, db.tables["submissions"][0], _exam(),
                        student_id="stu-1", code="123456")
        assert out["reason"] == rc.NOT_LOCKED

    def test_the_limit_points_at_the_manual_route(self):
        from app.services import resume_code as rc
        db = _locked_db(_now() - timedelta(minutes=5),
                        resume_count_used=2, resume_limit=2)
        out = rc.unlock(db, db.tables["submissions"][0], _exam(),
                        student_id="stu-1", code="123456")
        assert out["ok"] is False and out["reason"] == rc.LIMIT_REACHED
        assert not _written(db)

    def test_the_limit_is_read_from_the_sitting_when_it_was_snapshotted(self):
        """A school that lowers the policy mid-exam must not retroactively change
        the allowance of a pupil already sitting."""
        from app.services import resume_code as rc
        row = _row(_now(), resume_limit=5)
        assert rc.effective_limit(_exam(resume_code_limit=1), row) == 5
        row_none = _row(_now(), resume_limit=None)
        assert rc.effective_limit(_exam(resume_code_limit=3), row_none) == 3
        assert rc.effective_limit(_exam(resume_code_limit=None), row_none) == rc.DEFAULT_LIMIT


# ── the sitting is no longer wired for a code of its own ────────────────────

class TestTheSittingMintsNothing:
    def test_open_sitting_does_not_issue_a_code(self):
        """It used to. The code comes from `exam_recovery` when the exam page opens,
        so a second mint here is the duplicate this reconciliation removed."""
        src = SUBMISSION_SERVICE.read_text(encoding="utf-8")
        assert "_issue_resume_code" not in src, (
            "`open_sitting` still mints a code of its own")
        assert "resume_code" not in src, (
            "`submission_service` still names a `resume_code` column that no longer "
            "exists")
