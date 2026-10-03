"""One source of truth for a sitting's state — and a heartbeat that cannot lock.

Requested: the six professional-exam principles, of which these are the load-bearing
ones —

* **#6 single source of truth** — `attempt_status.get_attempt_status` answers status,
  deadline and seconds-left once, so the page, the dashboard, the sweep and the lock
  gate cannot disagree;
* **#3 idempotent resume** — the status read writes nothing and creates nothing, so it
  is safe as the resume door;
* **#1 server-authoritative clock** — the payload carries `server_now` and an absolute
  `deadline`, both from `exam_window`, never the device clock;
* **#2 heartbeat/liveness** — a ping records contact, and the module can prove it
  **cannot lock**: nothing in `heartbeat` writes `status`;
* **#4 granular audit** — a gap is a `connection_gap` + `connection_resumed` event,
  not a column overwrite.

The fake PostgREST below *applies* writes rather than recording them, so an assertion
about "it did not write" is a fact about the row, not about a log line that a real
write could also produce.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.services import attempt_status
from app.services.submission_service import LOCKED_STATUS

ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "supabase" / "migrations" / "056_attempt_liveness.sql"
SERVICE = ROOT / "app" / "services" / "attempt_status.py"
ROUTES = ROOT / "app" / "routes" / "student.py"
TEMPLATE = ROOT / "app" / "templates" / "student" / "take_exam.html"


# ── a PostgREST stand-in that applies its writes ────────────────────────────


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
        self._single = False
        self._desc = False

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

    def order(self, column, desc=False, **k):
        self._desc = bool(desc)
        self._order = column
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
            rows = [dict(r) for r in self.db.tables.get(self.table, []) if self._match(r)]
            if getattr(self, "_order", None) and not self._single:
                rows.sort(key=lambda r: r.get(self._order), reverse=self._desc)
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

    def writes(self):
        return [op for op, _t, _p in self.log if op in ("update", "insert")]


def _now():
    return datetime.now(timezone.utc)


def _exam(**kw):
    base = {
        "id": "exam-1",
        "duration_minutes": 60,
        "start_at": None,
        "end_at": None,
        "auto_submit_on_window_end": False,
        "lock_pending_resume": True,
        "resume_code_limit": 2,
        "publish_mode": "auto",
        "school_id": "sch-1",
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
        "last_ping_at": None,
        "ping_count": 0,
    }
    base.update(kw)
    return base


def _db(started_at, *, status="draft", exam=None, now=None, **row_kw):
    return _DB(
        submissions=[_row(started_at, status=status, **row_kw)],
        exams=[exam or _exam()],
    )


def _status(db, **kw):
    return attempt_status.get_attempt_status(db, "exam-1", "stu-1", **kw)


# ── the schema ──────────────────────────────────────────────────────────────


class TestTheLivenessColumns:
    def test_the_migration_declares_both_columns(self):
        sql = MIGRATION.read_text(encoding="utf-8")
        assert "last_ping_at" in sql and "ping_count" in sql

    def test_it_is_idempotent_and_non_destructive(self):
        sql = MIGRATION.read_text(encoding="utf-8").upper()
        assert "IF NOT EXISTS" in sql
        assert "DROP TABLE" not in sql and "DROP COLUMN" not in sql
        assert "DELETE FROM" not in sql


# ── the single source of truth ──────────────────────────────────────────────


class TestTheStatusVocabulary:
    def test_no_row_is_missing_and_writes_nothing(self):
        db = _DB(submissions=[], exams=[_exam()])
        out = _status(db)
        assert out["status"] == attempt_status.MISSING and out["ok"] is False
        assert db.writes() == [], "the status read must be a read"

    def test_a_live_draft_is_active_with_the_servers_clock(self):
        started = _now() - timedelta(minutes=5)
        db = _db(started)
        out = _status(db, now=_now())
        assert out["status"] == attempt_status.ACTIVE
        assert out["ok"] is True and out["seconds_left"] is not None
        # 60-minute exam, five minutes in: the seconds left are the server's own.
        assert 54 * 60 < out["seconds_left"] <= 56 * 60
        assert out["server_now"] and out["deadline"]

    def test_a_draft_past_the_grace_reads_expired(self):
        started = _now() - timedelta(minutes=70)
        db = _db(started)
        assert _status(db, now=_now())["status"] == attempt_status.EXPIRED

    def test_a_locked_row_before_the_deadline_stays_locked(self):
        started = _now() - timedelta(minutes=5)
        db = _db(started, status=LOCKED_STATUS)
        assert _status(db, now=_now())["status"] == attempt_status.LOCKED

    def test_a_locked_row_past_the_deadline_reads_expired(self):
        started = _now() - timedelta(minutes=61)
        db = _db(started, status=LOCKED_STATUS)
        assert _status(db, now=_now())["status"] == attempt_status.EXPIRED

    def test_a_submitted_row_is_finished(self):
        started = _now() - timedelta(minutes=5)
        db = _db(started, status="submitted")
        assert _status(db, now=_now())["status"] == attempt_status.FINISHED

    def test_the_seconds_left_is_exam_window_own_number(self):
        from app.utils import exam_window
        started = _now() - timedelta(minutes=17)
        exam = _exam()
        db = _db(started, exam=exam)
        now = _now()
        out = _status(db, now=now)
        assert out["seconds_left"] == exam_window.seconds_left(exam, started.isoformat(), now)

    def test_it_is_idempotent(self):
        started = _now() - timedelta(minutes=5)
        db = _db(started)
        now = _now()
        first = _status(db, now=now)
        second = _status(db, now=now)
        assert first == second
        assert db.writes() == []

    def test_it_can_include_the_pupils_own_answers(self):
        started = _now() - timedelta(minutes=1)
        db = _db(started)
        out = _status(db, now=_now(), include_answers=True)
        assert out["answers"] == {"1": "A"}

    def test_answers_are_not_included_by_default(self):
        started = _now() - timedelta(minutes=1)
        db = _db(started)
        assert "answers" not in _status(db, now=_now())


# ── the deadline is the only clock, so the duration bug cannot hide ─────────


class TestTheDeadlineFollowsTheDuration:
    @pytest.mark.parametrize("minutes", [30, 90, 120, 45])
    def test_a_custom_duration_sets_the_deadline(self, minutes):
        from app.utils import exam_window
        started = _now() - timedelta(minutes=1)
        exam = _exam(duration_minutes=minutes)
        db = _db(started, exam=exam)
        now = _now()
        out = _status(db, now=now)
        expected = exam_window.deadline(exam, started.isoformat())
        assert out["deadline"] == expected.isoformat()
        assert out["seconds_left"] == int((expected - now).total_seconds())


# ── the heartbeat: liveness, and never a lock ───────────────────────────────


class TestTheHeartbeat:
    def test_it_records_contact_and_returns_the_clock(self):
        started = _now() - timedelta(minutes=5)
        db = _db(started)
        now = _now()
        out = attempt_status.heartbeat(db, "exam-1", "stu-1", now=now)
        row = db.tables["submissions"][0]
        assert row["last_ping_at"] == now.isoformat()
        assert row["ping_count"] == 1
        assert out["status"] == attempt_status.ACTIVE
        assert out["seconds_left"] is not None

    def test_it_never_writes_the_status(self):
        """The heart of the contract: a ping cannot end or lock a paper."""
        started = _now() - timedelta(minutes=5)
        db = _db(started)
        for _ in range(5):
            attempt_status.heartbeat(db, "exam-1", "stu-1", now=_now())
        assert db.tables["submissions"][0]["status"] == "draft"
        for op, table, payload in db.log:
            if op == "update" and table == "submissions":
                assert "status" not in payload, "the heartbeat wrote a status"

    def test_a_finished_sitting_is_not_pinged(self):
        started = _now() - timedelta(minutes=5)
        db = _db(started, status="submitted")
        out = attempt_status.heartbeat(db, "exam-1", "stu-1", now=_now())
        assert out["status"] == attempt_status.FINISHED
        assert db.tables["submissions"][0]["ping_count"] == 0

    def test_a_gap_is_recorded_as_two_events(self):
        started = _now() - timedelta(minutes=10)
        stale = _now() - timedelta(seconds=attempt_status.GAP_SECONDS + 40)
        db = _db(started, last_ping_at=stale.isoformat(), ping_count=3)
        attempt_status.heartbeat(db, "exam-1", "stu-1", now=_now())
        kinds = [e["kind"] for e in db.tables.get("attempt_session_events", [])]
        assert kinds == ["connection_gap", "connection_resumed"]
        assert [e["seq"] for e in db.tables["attempt_session_events"]] == [1, 2]

    def test_a_short_gap_is_not_an_event(self):
        started = _now() - timedelta(minutes=10)
        recent = _now() - timedelta(seconds=10)
        db = _db(started, last_ping_at=recent.isoformat(), ping_count=1)
        attempt_status.heartbeat(db, "exam-1", "stu-1", now=_now())
        assert db.tables.get("attempt_session_events", []) == []


# ── the audit trail ─────────────────────────────────────────────────────────


class TestTheTimeline:
    def test_a_new_event_takes_the_next_sequence(self):
        db = _DB(attempt_session_events=[
            {"id": "e1", "attempt_id": "sub-1", "seq": 1, "kind": "attempt_started"},
            {"id": "e2", "attempt_id": "sub-1", "seq": 2, "kind": "connection_gap"},
        ])
        assert attempt_status.record_event(db, "sub-1", "connection_resumed") is True
        seqs = sorted(e["seq"] for e in db.tables["attempt_session_events"])
        assert seqs == [1, 2, 3]

    def test_a_failed_write_is_swallowed(self):
        class _Boom(_DB):
            def table(self, name):
                raise RuntimeError("down")

        assert attempt_status.record_event(_Boom(), "sub-1", "attempt_locked") is False

    def test_the_timeline_reads_in_sequence(self):
        db = _DB(attempt_session_events=[
            {"id": "e2", "attempt_id": "sub-1", "seq": 2, "kind": "attempt_resumed"},
            {"id": "e1", "attempt_id": "sub-1", "seq": 1, "kind": "attempt_locked"},
        ])
        assert [e["kind"] for e in attempt_status.timeline(db, "sub-1")] == [
            "attempt_locked", "attempt_resumed"]


# ── it is wired: the doors, and the page ────────────────────────────────────


class TestItIsWired:
    def test_the_student_blueprint_serves_status_and_heartbeat(self):
        src = ROUTES.read_text(encoding="utf-8")
        assert '/attempt-status/<exam_id>' in src
        assert '/heartbeat/<exam_id>' in src
        assert "attempt_status" in src

    def test_the_heartbeat_route_scopes_to_the_session_pupil(self):
        src = ROUTES.read_text(encoding="utf-8")
        block = src[src.index('/heartbeat/<exam_id>'):]
        block = block[:block.index("@student_bp.route")]
        assert "g.user_id" in block, (
            "the heartbeat must record the session's own pupil, never an id from the body"
        )

    def test_the_page_pings_its_heartbeat(self):
        src = TEMPLATE.read_text(encoding="utf-8")
        assert "/student/heartbeat/" in src, "the page never sends a heartbeat"

    def test_the_page_resyncs_from_the_status_endpoint(self):
        src = TEMPLATE.read_text(encoding="utf-8")
        assert "/student/attempt-status/" in src, (
            "the page never re-reads the server clock, so a device clock could stand in"
        )
