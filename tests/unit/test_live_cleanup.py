"""A failed room deletion is debt, and a vanished room is a mismatch — both tested.

Two P0 findings from the release review, and both were the same shape: the code
believed something it never checked again.

* `end_room()` was called once. Its failure produced a log line and an HTTP
  ``"media_cleanup": "pending"``, and nothing on the box ever looked at that room
  again. `live_cleanup` puts the deletion in a table, claims it with a lease and a
  compare-and-swap, retries it with an exponential backoff, treats an already
  absent room as cleaned, and escalates to `abandoned` rather than dropping it.
* A session could be `active` while LiveKit had no such room. `live_reconcile`
  only parks a session whose room it *saw* earlier (`media_state='live'`), never
  one nobody joined, and writes nothing at all when the probe itself fails.

The database is `tests/unit/live_fixtures.py`'s in-memory stand-in, which now
also does migration 073's `scan_live_one_cleanup_per_room` partial index, so a
second enqueue for one room fails the way it does in Postgres, and `is_()` so the
`missing_since IS NULL` condition can be expressed at all.

Three groups came out of the independent review of `421d0e1` and are tested here
rather than trusted: the pass must not consult the creation flags (the deadlock),
the debt must survive its parent (migration 074), and a claim's lease must be
re-asserted before the call (overlapping workers and restarts).
"""
import pytest
from flask import Flask, g

from app.routes import live
from app.services import live_cleanup, live_reconcile
from tests.unit import live_fixtures as fx
from tests.unit.live_fixtures import FakeDB, FakeQuery, door

NOW = "2026-10-25T09:00:00+00:00"
SESSION = {"id": "room-id", "school_id": "school-1", "exam_id": "exam-1",
           "room_name": "sg_room", "created_by": "teacher-a", "status": "active"}
EXAM = {"id": "exam-1", "teacher_id": "teacher-a", "school_id": "school-1",
        "scan_live_enabled": True}


def _db(*, sessions=None, jobs=None):
    return FakeDB({"exams": [dict(EXAM)],
                   "scan_live_sessions": [dict(r) for r in (sessions or [])],
                   "scan_live_cleanup_jobs": [dict(r) for r in (jobs or [])]})


# ── the backoff ──────────────────────────────────────────────────────────────
def test_the_wait_grows_geometrically_and_stops_at_an_hour():
    waits = [live_cleanup.backoff_seconds(n) for n in range(1, 10)]
    assert waits[:4] == [30, 60, 120, 240]
    assert waits == sorted(waits)
    assert max(waits) == live_cleanup.CAP_SECONDS == 3600
    # A zero or negative attempt is the first attempt, not a negative sleep.
    assert live_cleanup.backoff_seconds(0) == live_cleanup.backoff_seconds(1)


def test_the_budget_is_bounded():
    assert live_cleanup.MAX_ATTEMPTS >= 3
    # Eight failures span more than two hours of retries at the capped hour.
    total = sum(live_cleanup.backoff_seconds(n)
                for n in range(1, live_cleanup.MAX_ATTEMPTS + 1))
    assert total >= 3600 * 2


# ── enqueueing ───────────────────────────────────────────────────────────────
def test_a_failed_deletion_is_recorded_once_per_room():
    db = _db(sessions=[SESSION])

    first = live_cleanup.enqueue(db, SESSION, reason="session_ended",
                                 error=RuntimeError("LiveKit unreachable"), now=NOW)
    second = live_cleanup.enqueue(db, SESSION, reason="session_ended",
                                  error=RuntimeError("still unreachable"), now=NOW)

    assert len(db.jobs()) == 1
    assert second["id"] == first["id"]
    assert first["next_attempt_at"] == second["next_attempt_at"] == NOW
    # The newest reason wins, and a retry of Stop does not push the schedule out.
    assert "still unreachable" in second["last_error"]
    assert db.jobs()[0]["attempts"] == 0


def test_the_same_room_can_be_queued_again_after_it_was_cleaned():
    db = _db(sessions=[SESSION], jobs=[{
        "id": "job-1", "session_id": "room-id", "school_id": "school-1",
        "room_name": "sg_room", "status": "done", "attempts": 2,
        "claim_token": "", "next_attempt_at": NOW,
    }])

    live_cleanup.enqueue(db, SESSION, now=NOW)

    assert [job["status"] for job in db.jobs()] == ["done", "pending"]


def test_a_race_on_the_index_returns_the_job_that_won(monkeypatch):
    """Two requests enqueue at once; the loser must not raise into the route."""
    db = _db(sessions=[SESSION])
    real_open = live_cleanup._open_job
    calls = {"n": 0}

    def blind_once(database, session):
        calls["n"] += 1
        if calls["n"] == 1:                  # the pre-insert read sees nothing
            return None
        return real_open(database, session)

    monkeypatch.setattr(live_cleanup, "_open_job", blind_once)
    db.rows["scan_live_cleanup_jobs"] = [{
        "id": "winner", "session_id": "room-id", "school_id": "school-1",
        "room_name": "sg_room", "status": "pending", "attempts": 0,
        "claim_token": "", "next_attempt_at": NOW,
    }]

    job = live_cleanup.enqueue(db, SESSION, now=NOW)

    assert job["id"] == "winner"
    assert len(db.jobs()) == 1


def test_a_row_without_a_room_cannot_invent_a_deletion():
    db = _db(sessions=[SESSION])

    assert live_cleanup.enqueue(db, {"id": "room-id", "school_id": "school-1",
                                     "room_name": None}, now=NOW) is None
    assert db.jobs() == []


# ── claiming ─────────────────────────────────────────────────────────────────
def test_two_workers_cannot_claim_the_same_room():
    db = _db(sessions=[SESSION])
    live_cleanup.enqueue(db, SESSION, now=NOW)

    first = live_cleanup.claim_next(db, owner="worker-a", now=NOW)
    second = live_cleanup.claim_next(db, owner="worker-b", now=NOW)

    assert first is not None
    assert second is None, "a live claim must not be taken by a second worker"
    assert db.jobs()[0]["claimed_by"] == "worker-a"


def test_a_backed_off_job_is_not_claimed_early():
    db = _db(sessions=[SESSION])
    live_cleanup.enqueue(db, SESSION, now=NOW)
    job = live_cleanup.claim_next(db, owner="worker-a", now=NOW)
    live_cleanup.attempt(None, db, job, end_room=lambda room: (_ for _ in ()).throw(
        RuntimeError("LiveKit unreachable")), now=NOW)

    assert live_cleanup.claim_next(db, owner="worker-b", now=NOW) is None
    # …and it becomes claimable once its own wait is over.
    later = "2026-10-25T09:01:00+00:00"
    assert live_cleanup.claim_next(db, owner="worker-b", now=later) is not None


def test_a_crashed_claim_is_recovered_after_its_lease_without_burning_an_attempt():
    db = _db(sessions=[SESSION])
    live_cleanup.enqueue(db, SESSION, now=NOW)
    orphan = live_cleanup.claim_next(db, owner="worker-a", now=NOW)
    assert orphan is not None          # …and then the worker dies, mid-call.

    still_held = "2026-10-25T09:04:00+00:00"
    assert live_cleanup.claim_next(db, owner="worker-b", now=still_held) is None

    after_lease = "2026-10-25T09:06:00+00:00"
    recovered = live_cleanup.claim_next(db, owner="worker-b", now=after_lease)
    assert recovered is not None
    assert recovered["claimed_by"] == "worker-b"
    # A crash is a box problem, not a failed deletion: the budget is untouched.
    assert recovered["attempts"] == 0

    outcome = live_cleanup.attempt(None, db, recovered,
                                   end_room=lambda room: None, now=after_lease)
    assert outcome["state"] == "done"
    assert db.jobs()[0]["status"] == "done"


# ── one attempt ──────────────────────────────────────────────────────────────
def test_a_deletion_that_works_closes_the_job():
    db = _db(sessions=[SESSION])
    live_cleanup.enqueue(db, SESSION, now=NOW)
    job = live_cleanup.claim_next(db, owner="worker-a", now=NOW)
    deleted = []

    outcome = live_cleanup.attempt(None, db, job, end_room=deleted.append, now=NOW)

    assert outcome["state"] == "done"
    assert deleted == ["sg_room"]
    assert db.jobs()[0]["status"] == "done"
    assert db.jobs()[0]["last_error"] is None


def test_a_room_that_is_already_gone_counts_as_cleaned():
    """LiveKit answers a deletion of a missing room with an error."""
    db = _db(sessions=[SESSION])
    live_cleanup.enqueue(db, SESSION, now=NOW)
    job = live_cleanup.claim_next(db, owner="worker-a", now=NOW)

    def gone(room):
        raise RuntimeError("TwirpError: room not found")

    outcome = live_cleanup.attempt(None, db, job, end_room=gone, now=NOW)

    assert outcome["state"] == "done"
    assert db.jobs()[0]["status"] == "done"
    assert "already absent" in db.jobs()[0]["last_error"]
    # Not a failure, so no attempt was spent and nothing is scheduled.
    assert db.jobs()[0]["attempts"] == 0


def test_a_failed_deletion_is_rescheduled_with_the_error_on_the_row():
    db = _db(sessions=[SESSION])
    live_cleanup.enqueue(db, SESSION, now=NOW)
    job = live_cleanup.claim_next(db, owner="worker-a", now=NOW)

    def down(room):
        raise TimeoutError("LiveKit API timed out")

    outcome = live_cleanup.attempt(None, db, job, end_room=down, now=NOW)

    assert outcome["state"] == "retried"
    row = db.jobs()[0]
    assert row["status"] == "pending"
    assert row["attempts"] == 1
    assert "timed out" in row["last_error"]
    assert row["next_attempt_at"] > NOW, "the retry must wait, not spin"


def test_enough_failures_mark_the_room_as_needing_a_person():
    db = _db(sessions=[SESSION])
    live_cleanup.enqueue(db, SESSION, now=NOW)
    moment = NOW

    def down(room):
        raise RuntimeError("LiveKit unreachable")

    for _ in range(live_cleanup.MAX_ATTEMPTS):
        job = live_cleanup.claim_next(db, owner="worker-a", now=moment)
        assert job is not None, "the job must stay claimable until the budget runs out"
        live_cleanup.attempt(None, db, job, end_room=down, now=moment)
        moment = db.jobs()[0]["next_attempt_at"]

    row = db.jobs()[0]
    assert row["status"] == "abandoned"
    assert row["attempts"] == live_cleanup.MAX_ATTEMPTS
    assert "LiveKit unreachable" in row["last_error"]
    # Escalated, not retried for ever.
    assert live_cleanup.claim_next(db, owner="worker-b", now=moment) is None


def test_a_transition_that_lost_its_row_is_reported_not_assumed(monkeypatch):
    """The lease renewal answers, the recording write does not."""
    db = _db(sessions=[SESSION])
    live_cleanup.enqueue(db, SESSION, now=NOW)
    job = live_cleanup.claim_next(db, owner="worker-a", now=NOW)
    deleted = []

    original = FakeQuery.execute
    seen = {"updates": 0}

    def execute(self):
        if self.op == "update":
            seen["updates"] += 1
            if seen["updates"] > 1:
                return fx._Result([])      # another worker already closed it
        return original(self)

    monkeypatch.setattr(FakeQuery, "execute", execute)
    outcome = live_cleanup.attempt(None, db, job, end_room=deleted.append, now=NOW)

    # The deletion went out (the room is gone), the bookkeeping did not land.
    assert deleted == ["sg_room"]
    assert outcome["state"] == "lost"


# ── one pass ─────────────────────────────────────────────────────────────────
def test_one_pass_does_not_retry_its_own_failure():
    db = _db(sessions=[SESSION])
    live_cleanup.enqueue(db, SESSION, now=NOW)

    def down(room):
        raise RuntimeError("LiveKit unreachable")

    first = live_cleanup.run_pending(None, db, end_room=down, now=NOW)
    second = live_cleanup.run_pending(None, db, end_room=down, now=NOW)

    assert first == {"attempted": 1, "done": 0, "retried": 1,
                     "abandoned": 0, "lost": 0}
    assert second["attempted"] == 0, "the backoff must survive inside one tick"


def test_a_pass_drains_every_due_room():
    db = _db(sessions=[SESSION, dict(SESSION, id="room-2", room_name="sg_room2")])
    for session in (SESSION, dict(SESSION, id="room-2", room_name="sg_room2")):
        live_cleanup.enqueue(db, session, now=NOW)
    deleted = []

    summary = live_cleanup.run_pending(None, db, end_room=deleted.append, now=NOW)

    assert summary["done"] == 2
    assert sorted(deleted) == ["sg_room", "sg_room2"]
    assert {job["status"] for job in db.jobs()} == {"done"}


# ── reconcile: only a room we saw may be declared missing ────────────────────
def _session(**overrides):
    row = dict(SESSION, media_state="unknown")
    row.update(overrides)
    return row


def test_a_room_the_sfu_serves_is_recorded_as_live():
    db = _db(sessions=[_session()])

    result = live_reconcile.reconcile(None, db, room_names=lambda config: ["sg_room"],
                                      now=NOW)

    assert result == {"measured": True, "live": 1, "missing": 0,
                      "stale": 0, "waiting": 0}
    row = db.rows["scan_live_sessions"][0]
    assert row["status"] == "active"
    assert row["media_state"] == "live"
    assert row["reconciled_at"] == NOW
    assert row["missing_since"] is None


def test_a_session_nobody_joined_is_not_stale():
    """No LiveKit room exists until the first participant joins."""
    db = _db(sessions=[_session()])

    result = live_reconcile.reconcile(None, db, room_names=lambda config: [], now=NOW)

    assert result["waiting"] == 1 and result["stale"] == 0
    row = db.rows["scan_live_sessions"][0]
    assert row["status"] == "active"
    assert row["media_state"] == "unknown", "absence proves nothing before a sighting"
    assert row.get("missing_since") is None


def test_a_first_absence_only_starts_the_clock():
    """One empty room list is a snapshot, not a verdict on a lesson."""
    db = _db(sessions=[_session(media_state="live")])
    queued = []

    result = live_reconcile.reconcile(
        None, db, room_names=lambda config: [], now=NOW,
        enqueue=lambda database, session, **kwargs: queued.append(session["id"]))

    assert result["missing"] == 1 and result["stale"] == 0
    row = db.rows["scan_live_sessions"][0]
    assert row["status"] == "active", "a candidate must not close a live session"
    assert row["missing_since"] == NOW
    assert queued == [], "nothing is deleted before the absence is confirmed"


def test_the_park_needs_the_room_to_stay_gone_past_the_grace():
    db = _db(sessions=[_session(media_state="live")])
    queued = []
    absent = lambda config: []
    enqueue = lambda database, session, **kwargs: queued.append(session["id"])

    live_reconcile.reconcile(None, db, room_names=absent, now=NOW, enqueue=enqueue)
    early = "2026-10-25T09:00:30+00:00"       # 30 s < MISSING_GRACE
    second = live_reconcile.reconcile(None, db, room_names=absent, now=early,
                                      enqueue=enqueue)
    assert second["stale"] == 0
    assert db.rows["scan_live_sessions"][0]["status"] == "active"
    assert queued == []

    later = "2026-10-25T09:02:00+00:00"       # 2 min after the first sighting
    third = live_reconcile.reconcile(None, db, room_names=absent, now=later,
                                     enqueue=enqueue)
    assert third["stale"] == 1
    row = db.rows["scan_live_sessions"][0]
    assert row["status"] == "stale"
    assert row["media_state"] == "missing"
    assert row["stale_reason"] == live_reconcile.STALE_REASON
    assert queued == ["room-id"]


def test_a_room_that_comes_back_clears_the_candidate():
    """A short list during a LiveKit restart must cost nothing."""
    db = _db(sessions=[_session(media_state="live")])
    absent = lambda config: []

    live_reconcile.reconcile(None, db, room_names=absent, now=NOW)
    assert db.rows["scan_live_sessions"][0]["missing_since"] == NOW

    back = "2026-10-25T09:05:00+00:00"
    result = live_reconcile.reconcile(None, db, room_names=lambda c: ["sg_room"],
                                      now=back)

    assert result["live"] == 1 and result["stale"] == 0
    row = db.rows["scan_live_sessions"][0]
    assert row["status"] == "active"
    assert row["media_state"] == "live"
    assert row["missing_since"] is None, "the clock must not survive a sighting"


def test_a_probe_that_fails_writes_nothing():
    """LiveKit being briefly unreachable must not end anybody's lesson."""
    db = _db(sessions=[_session(media_state="live")])

    def broken(config):
        raise RuntimeError("LiveKit unreachable")

    result = live_reconcile.reconcile(None, db, room_names=broken, now=NOW)

    assert result["measured"] is False
    assert db.updates == 0
    assert db.rows["scan_live_sessions"][0]["status"] == "active"


def test_a_session_the_teacher_ended_is_not_touched_by_the_sweep():
    db = _db(sessions=[_session(status="ended", media_state="live")])

    live_reconcile.reconcile(None, db, room_names=lambda config: [], now=NOW)

    row = db.rows["scan_live_sessions"][0]
    assert row["status"] == "ended"
    assert "reconciled_at" not in row


def test_a_session_ended_while_the_sweep_ran_is_left_ended():
    """Every write is conditional on `active`, so a person's decision wins."""
    db = _db(sessions=[_session(media_state="live")])
    db.race = "ended"

    result = live_reconcile.reconcile(None, db, room_names=lambda config: [], now=NOW)

    assert result["stale"] == 0 and result["missing"] == 0
    row = db.rows["scan_live_sessions"][0]
    assert row["status"] == "ended"
    assert row.get("missing_since") is None


# ── the routes record the debt, and a stale session is a closed door ─────────
def _stage(monkeypatch, rows, *, end_error=None):
    db = FakeDB(rows)
    ended = []

    def fake_end(config, room):
        ended.append(room)
        if end_error is not None:
            raise end_error

    monkeypatch.setattr(live, "get_supabase", lambda: db)
    monkeypatch.setattr(live, "_exam", lambda *_: db.rows["exams"][0])
    monkeypatch.setattr(live, "exam_sitting_allowed", lambda *_: (True, ""))
    monkeypatch.setattr(live.live_policy, "feature_enabled", lambda *_: True)
    monkeypatch.setattr(live.live_policy, "client_ready", lambda *_: True)
    monkeypatch.setattr(live.livekit_gateway, "end_room", fake_end)
    return db, ended


def _rows(sessions=None):
    return {"exams": [dict(EXAM)],
            "scan_live_sessions": [dict(r) for r in (sessions or [dict(SESSION)])]}


def test_stop_that_cannot_delete_records_the_debt(monkeypatch):
    app = Flask(__name__)
    db, ended = _stage(monkeypatch, _rows(),
                       end_error=RuntimeError("LiveKit unreachable"))

    response = door(app, "end_session", "room-id")

    assert response.status_code == 202
    assert response.get_json() == {"status": "ended", "media_cleanup": "pending"}
    assert ended == ["sg_room"]
    # The row still gates new tokens, and the deletion is now somebody's job.
    assert db.rows["scan_live_sessions"][0]["status"] == "ended"
    assert [job["room_name"] for job in db.jobs()] == ["sg_room"]
    assert db.jobs()[0]["reason"] == "session_ended"


def test_switching_the_paper_off_records_the_debt(monkeypatch):
    app = Flask(__name__)
    db, _ = _stage(monkeypatch, _rows(),
                   end_error=RuntimeError("LiveKit unreachable"))

    view = live.set_exam_enabled
    while getattr(view, "__wrapped__", None) is not None:
        view = view.__wrapped__
    with app.test_request_context(json={"enabled": False}):
        g.user_school_id = "school-1"
        g.user_role = "guru"
        g.user_id = "teacher-a"
        response = app.make_response(view("exam-1"))

    assert response.status_code == 200
    assert response.get_json()["media_cleanup"] == "pending"
    assert db.rows["exams"][0]["scan_live_enabled"] is False
    assert [job["room_name"] for job in db.jobs()] == ["sg_room"]


def test_a_session_with_vanished_media_hands_out_no_token(monkeypatch):
    app = Flask(__name__)
    _stage(monkeypatch, _rows([dict(_session(status="stale", media_state="missing"))]))
    issued = []
    monkeypatch.setattr(live.livekit_gateway, "issue_join_token",
                        lambda *a, **k: issued.append(a) or {})

    response = door(app, "join_token", "room-id")

    assert response.status_code == 403
    assert issued == [], "a parked session must issue nothing, not even a stale token"


# ── maintenance is not creation (review finding 1) ──────────────────────────
#
# `run_once` used to return early while the two feature flags were off, and the
# scheduler refused to start, so the debt a failed deletion recorded could never be
# paid after a teacher revoked ScanMeet. These hold the separation: the flags gate
# creation (routes), the pass gates itself on credentials and schema.


def _maintenance(monkeypatch, db, *, config=None, end_room=None, room_names=None):
    """Wire `run_once` to a fake app, fake database and fake LiveKit."""
    from app.services import live_cleanup, livekit_gateway
    from app.utils import auth as auth_module

    calls = []

    def fake_end(config, room):
        calls.append(room)
        if end_room is not None:
            return end_room(room)
        return None

    monkeypatch.setattr(auth_module, "get_supabase", lambda: db)
    monkeypatch.setattr(livekit_gateway, "end_room", fake_end)
    monkeypatch.setattr(livekit_gateway, "room_names", room_names or (lambda config: []))
    app = type("App", (), {"config": config or {
        "LIVEKIT_URL": "wss://staging.livekit.invalid",
        "LIVEKIT_API_KEY": "key", "LIVEKIT_API_SECRET": "secret"}})()
    return live_cleanup.run_once(app, now=NOW), calls


def test_cleanup_runs_with_the_feature_flags_off(monkeypatch):
    """Revoking ScanMeet must not stop disposal of rooms already carrying video."""
    from app.services import live_policy

    db = _db(sessions=[SESSION])
    live_cleanup.enqueue(db, SESSION, now=NOW)

    def forbidden(*_a, **_k):
        raise AssertionError("run_once consulted a creation flag")

    monkeypatch.setattr(live_policy, "feature_enabled", forbidden)
    monkeypatch.setattr(live_policy, "client_ready", lambda config: True)

    result, calls = _maintenance(monkeypatch, db)

    assert result["ran"] is True
    assert "regardless of" in result["rules"]
    assert result["cleanup"]["done"] == 1
    assert calls == ["sg_room"]
    assert db.jobs()[0]["status"] == "done"


def test_maintenance_is_blocked_without_credentials(monkeypatch):
    """A pass that cannot run says so; it is never a pass."""
    from app.services import live_policy

    db = _db(sessions=[SESSION])
    live_cleanup.enqueue(db, SESSION, now=NOW)
    monkeypatch.setattr(live_policy, "client_ready", lambda config: False)

    result, calls = _maintenance(monkeypatch, db)

    assert result["ran"] is False
    assert "credentials" in result["blocked"]
    assert calls == []
    assert db.jobs()[0]["status"] == "pending", "nothing was drained, and it says so"


def test_maintenance_is_blocked_without_the_ledger_table(monkeypatch):
    """A box whose database is behind its code gets a reason, not a retry loop."""
    from app.services import live_policy

    db = _db(sessions=[SESSION])
    monkeypatch.setattr(live_policy, "client_ready", lambda config: True)
    original = FakeQuery.execute

    def unmigrated(self):
        if self.name == live_cleanup.TABLE and self.op == "select":
            raise RuntimeError('relation "scan_live_cleanup_jobs" does not exist')
        return original(self)

    monkeypatch.setattr(FakeQuery, "execute", unmigrated)
    result, calls = _maintenance(monkeypatch, db)

    assert result["ran"] is False
    assert "migration 073/074" in result["blocked"]
    assert calls == []


def test_schema_ready_reads_the_ledger():
    assert live_cleanup.schema_ready(_db()) is True

    class Unmigrated:
        def table(self, name):
            query = FakeQuery(self, name)
            query.execute = lambda: (_ for _ in ()).throw(
                RuntimeError("relation does not exist"))
            return query

    assert live_cleanup.schema_ready(Unmigrated()) is False


# ── the debt outlives its parent (review finding 2, migration 074) ──────────
def test_an_orphaned_job_is_still_deleted():
    """Migration 074 releases the reference; the worker only needs `room_name`."""
    db = _db()
    db.rows["scan_live_cleanup_jobs"] = [{
        "id": "orphan-1", "session_id": None, "school_id": None,
        "room_name": "sg_orphan", "status": "pending", "attempts": 0,
        "claim_token": "", "next_attempt_at": NOW, "orphaned_at": NOW,
    }]
    deleted = []

    summary = live_cleanup.run_pending(None, db, end_room=deleted.append, now=NOW)

    assert summary["done"] == 1
    assert deleted == ["sg_orphan"]
    row = db.jobs()[0]
    assert row["status"] == "done"
    assert row["orphaned_at"] == NOW, "the orphan stamp is the audit trail"


def test_a_pending_job_never_takes_its_parent_with_it():
    """The migration contract, read from the SQL: no CASCADE on either reference."""
    root = live_cleanup.__file__
    migrations = (__import__("pathlib").Path(root).parents[2] / "supabase" / "migrations")
    text = "".join(
        p.read_text(encoding="utf-8")
        for p in sorted(migrations.glob("07[34]_scan_live*.sql")))

    assert text, "migrations 073/074 are missing from the checkout"
    assert "scan_live_cleanup_jobs" in text
    for reference in ("scan_live_cleanup_jobs_session_id_fkey",
                      "scan_live_cleanup_jobs_school_id_fkey"):
        assert reference in text
    # The forward-only migration must replace CASCADE with SET NULL, and nothing
    # may put CASCADE back on this table.
    assert "ON DELETE SET NULL" in text
    cascade = [line for line in text.splitlines()
               if "CASCADE" in line.upper() and "cleanup_jobs" in line]
    assert cascade == [], f"a cleanup reference still cascades: {cascade}"
    assert "missing_since" in text, "the reconcile grace column has no migration"


# ── the claim under overlapping workers (review finding 3) ──────────────────
def test_a_worker_that_lost_its_lease_does_not_touch_the_room():
    db = _db(sessions=[SESSION])
    live_cleanup.enqueue(db, SESSION, now=NOW)
    first = live_cleanup.claim_next(db, owner="worker-a", now=NOW)
    assert first is not None

    # The lease runs out while worker A is still between the claim and the call;
    # worker B takes the row and deletes the room.
    after_lease = "2026-10-25T09:06:00+00:00"
    second = live_cleanup.claim_next(db, owner="worker-b", now=after_lease)
    assert second is not None
    deleted = []
    assert live_cleanup.attempt(None, db, second, end_room=deleted.append,
                                now=after_lease)["state"] == "done"

    outcome = live_cleanup.attempt(None, db, first, end_room=deleted.append,
                                   now="2026-10-25T09:07:00+00:00")

    assert outcome["state"] == "lost"
    assert deleted == ["sg_room"], "the stale worker must not delete a room it lost"
    assert db.jobs()[0]["status"] == "done", "B's outcome stands"


def test_a_renewal_without_the_token_is_refused():
    db = _db(sessions=[SESSION])
    live_cleanup.enqueue(db, SESSION, now=NOW)
    job = live_cleanup.claim_next(db, owner="worker-a", now=NOW)

    stale = dict(job, claim_token="someone-elses")
    assert live_cleanup._renew(db, stale, live_cleanup._now(NOW)) is False
    assert live_cleanup._renew(db, job, live_cleanup._now(NOW)) is True


def test_the_repair_does_not_revive_the_room_it_parked(monkeypatch):
    """A fresh start is a new row and a new room name — never the old room back."""
    app = Flask(__name__)
    db, _ = _stage(monkeypatch, _rows([
        dict(_session(status="stale", media_state="missing", room_name="sg_old"))]))

    response = door(app, "create_session", "exam-1")

    assert response.status_code == 201
    assert response.get_json()["existing"] is False
    sessions = db.rows["scan_live_sessions"]
    assert [row["status"] for row in sessions] == ["stale", "active"]
    assert sessions[1]["room_name"] != "sg_old"
