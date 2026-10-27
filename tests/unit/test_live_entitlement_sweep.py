"""A lapsed add-on must stop the camera that is *already* running.

The release review's P0 question was "with pupil video flowing, switch the
school's licence off — does the video stop, or does only the *next* token get
refused?" Every gate in the feature answered the second question: `POST
/sessions`, the per-exam switch and `POST /sessions/<id>/token` all consult
`live_school_entitlement.active()`, and nothing asked again once the room was
open. So the answer at the time was "the next token is refused and the current
room keeps publishing", which is not a revocation.

These tests are behavioural: they run the sweep against the in-memory database
and a recording `end_room`, and they assert what a participant would experience
(the session is no longer `active`, the room delete was issued, a failure became
durable debt) rather than that some function is called somewhere in a route file.

The rule that took the most care is the negative one: an entitlement read that
*fails* is not a licence decision. A sweep that treated transport failure as
"lapsed" would end every running examination on the box during a Supabase blip,
which is the worst outcome this product has — worse than a camera that outlives
its licence by one maintenance interval.
"""
import ast
import inspect

import pytest
from flask import Flask

from app.services import (live_cleanup, live_entitlement_sweep,
                          live_school_entitlement as entitlement)
from tests.unit.live_fixtures import FakeDB

NOW = "2026-10-25T09:00:00+00:00"

PAID = {"school_id": "school-1", "enabled": True, "status": "paid",
        "valid_until": "2026-12-31T00:00:00Z"}
EXPIRED = {"school_id": "school-1", "enabled": True, "status": "paid",
           "valid_until": "2026-10-24T00:00:00Z"}
DISABLED = {"school_id": "school-1", "enabled": False, "status": "disabled",
            "valid_until": "2026-12-31T00:00:00Z"}
PAST_DUE = {"school_id": "school-1", "enabled": True, "status": "expired",
            "valid_until": "2026-12-31T00:00:00Z"}

SESSION_1 = {"id": "sess-1", "school_id": "school-1", "exam_id": "exam-1",
             "room_name": "sg_one", "status": "active", "media_state": "live"}
SESSION_2 = {"id": "sess-2", "school_id": "school-2", "exam_id": "exam-2",
             "room_name": "sg_two", "status": "active", "media_state": "live"}


def _db(*, licences=None, sessions=None, jobs=None):
    return FakeDB({"scanmeet_school_entitlements": [dict(r) for r in (licences or [])],
                   "scan_live_sessions": [dict(r) for r in (sessions or [])],
                   "scan_live_cleanup_jobs": [dict(r) for r in (jobs or [])]})


def _sweep(db, *, rooms=None, state=None, enqueue=None):
    """Run one pass with a recording `end_room`; return (summary, deleted rooms)."""
    deleted = [] if rooms is None else rooms

    def end_room(room):
        deleted.append(room)

    return (live_entitlement_sweep.revoke_lapsed(
        db, end_room=end_room, entitlement_state=state, enqueue=enqueue, now=NOW),
        deleted)


# ── the gap: a room that is already running ──────────────────────────────────
@pytest.mark.parametrize("licence", [EXPIRED, DISABLED, PAST_DUE],
                         ids=["expired", "disabled", "past-due"])
def test_a_lapsed_licence_ends_the_room_that_is_already_running(licence):
    db = _db(licences=[licence], sessions=[SESSION_1])

    summary, deleted = _sweep(db)

    assert deleted == ["sg_one"], "the room LiveKit must delete"
    assert summary["revoked"] == 1 and summary["deleted"] == 1
    session = db.rows["scan_live_sessions"][0]
    assert session["status"] == "stale", "not 'active': every token route reads that"
    assert session["stale_reason"] == live_entitlement_sweep.REASON
    assert session["media_state"] == "missing", "LiveKit confirmed the deletion"
    assert session["reconciled_at"] == NOW


def test_a_licence_that_is_still_valid_leaves_the_lesson_alone():
    db = _db(licences=[PAID], sessions=[SESSION_1])

    summary, deleted = _sweep(db)

    assert deleted == []
    assert summary["kept"] == 1 and summary["revoked"] == 0
    assert db.rows["scan_live_sessions"][0]["status"] == "active"


def test_an_expiring_at_midnight_licence_revokes_at_midnight():
    """The boundary is the deadline, not the day before it."""
    just_before = {"school_id": "school-1", "enabled": True, "status": "paid",
                   "valid_until": "2026-10-25T09:00:01Z"}
    exactly_now = dict(just_before, valid_until="2026-10-25T09:00:00Z")

    kept, _ = _sweep(_db(licences=[just_before], sessions=[SESSION_1]))
    ended, deleted = _sweep(_db(licences=[exactly_now], sessions=[SESSION_1]))

    assert kept["kept"] == 1 and ended["revoked"] == 1
    assert deleted == ["sg_one"]


# ── the negative rule: an unreadable licence is not a lapsed one ─────────────
def test_an_unreadable_licence_parks_nothing():
    db = _db(licences=[PAID], sessions=[SESSION_1])

    summary, deleted = _sweep(
        db, state=lambda *a, **k: entitlement.UNKNOWN)

    assert deleted == []
    assert summary["unread"] == 1 and summary["revoked"] == 0
    assert summary["unknown_schools"] == ["school-1"]
    assert db.rows["scan_live_sessions"][0]["status"] == "active"


def test_a_missing_entitlement_table_is_unreadable_not_lapsed():
    """Migration 075 absent is a box state, not a school that stopped paying."""

    class NoLicenceTable(FakeDB):
        def table(self, name):
            if name == "scanmeet_school_entitlements":
                raise RuntimeError('relation "public.scanmeet_school_entitlements" '
                                   'does not exist')
            return super().table(name)

    db = NoLicenceTable({"scan_live_sessions": [dict(SESSION_1)]})

    summary, deleted = _sweep(db)

    assert deleted == []
    assert summary["unknown_schools"] == ["school-1"]
    assert db.rows["scan_live_sessions"][0]["status"] == "active"


def test_a_boundary_that_raises_is_not_a_decision():
    def explode(*_args, **_kwargs):
        raise RuntimeError("entitlement service unavailable")

    db = _db(licences=[EXPIRED], sessions=[SESSION_1])
    summary, deleted = _sweep(db, state=explode)

    assert deleted == [] and summary["unread"] == 1


# ── cost, idempotency and the race with a renewal ────────────────────────────
def test_the_licence_is_read_once_per_school_not_once_per_room():
    db = _db(licences=[EXPIRED],
             sessions=[SESSION_1, dict(SESSION_1, id="sess-3", room_name="sg_three")])
    reads = []

    def spy(database, school, *, now=None):
        reads.append(school)
        return entitlement.INACTIVE

    summary, deleted = _sweep(db, state=spy)

    assert reads == ["school-1"], "one licence, however many rooms it covers"
    assert summary["revoked"] == 2 and sorted(deleted) == ["sg_one", "sg_three"]


def test_a_second_pass_finds_nothing_active_and_deletes_nothing_again():
    db = _db(licences=[EXPIRED], sessions=[SESSION_1])
    rooms = []

    first, _ = _sweep(db, rooms=rooms)
    second, _ = _sweep(db, rooms=rooms)

    assert first["revoked"] == 1
    assert second["revoked"] == 0 and second["sessions"] == 0
    assert rooms == ["sg_one"], "the room is deleted once, not once per interval"


def test_a_renewed_licence_before_the_pass_keeps_the_room():
    """The race the operator asked about: renewal lands first."""
    db = _db(licences=[PAID], sessions=[SESSION_1])

    summary, deleted = _sweep(db)

    assert deleted == [] and summary["kept"] == 1
    assert db.rows["scan_live_sessions"][0]["status"] == "active"


def test_a_session_somebody_else_already_parked_is_not_deleted_twice():
    """A teacher's Stop landing between the read and the write."""

    class Racing(FakeDB):
        def table(self, name):
            query = super().table(name)
            if name == "scan_live_sessions":
                # The sweep read `active`; by the time it writes, the teacher's
                # Stop has already moved the row to `ended`.
                self.rows["scan_live_sessions"][0]["status"] = "ended"
            return query

    db = Racing({"scanmeet_school_entitlements": [dict(EXPIRED)],
                 "scan_live_sessions": [dict(SESSION_1)]})
    summary, deleted = _sweep(db)

    assert deleted == [], "the compare half of the swap refused the write"
    assert summary["revoked"] == 0


# ── a deletion that failed is debt the box retries ───────────────────────────
def test_a_failed_deletion_becomes_durable_debt():
    db = _db(licences=[EXPIRED], sessions=[SESSION_1])

    def boom(room):
        raise RuntimeError("LiveKit unreachable")

    summary = live_entitlement_sweep.revoke_lapsed(
        db, end_room=boom, enqueue=live_cleanup.enqueue, now=NOW)

    assert summary["revoked"] == 1
    assert summary["deferred"] == 1 and summary["deleted"] == 0
    # The row already refuses new tokens; the deletion is now somebody's job.
    assert db.rows["scan_live_sessions"][0]["status"] == "stale"
    jobs = db.rows["scan_live_cleanup_jobs"]
    assert len(jobs) == 1
    assert jobs[0]["room_name"] == "sg_one"
    assert jobs[0]["reason"] == live_entitlement_sweep.CLEANUP_REASON
    assert jobs[0]["status"] == "pending"
    assert "LiveKit unreachable" in jobs[0]["last_error"]


def test_the_debt_is_recorded_even_without_a_ledger_to_record_it():
    """An enqueue that raises must not undo a revocation that already happened."""
    db = _db(licences=[EXPIRED], sessions=[SESSION_1])

    summary = live_entitlement_sweep.revoke_lapsed(
        db, end_room=lambda room: (_ for _ in ()).throw(RuntimeError("down")),
        enqueue=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no table")),
        now=NOW)

    assert summary["deferred"] == 1
    assert db.rows["scan_live_sessions"][0]["status"] == "stale"


def test_a_session_without_a_room_is_not_handed_to_livekit():
    db = _db(licences=[EXPIRED],
             sessions=[dict(SESSION_1, room_name=None)])

    summary, deleted = _sweep(db)

    assert deleted == [] and summary["revoked"] == 0


# ── the rule the ledger already documents, applied here too ──────────────────
def test_the_sweep_cannot_consult_the_creation_flags():
    """Disposal must not be gated on the switch that stops creating rooms.

    Measured rather than asserted by grep: `revoke_lapsed` takes no `config`, so
    there is nothing to read a flag from, and every mention of a flag name in the
    module is *inside a string literal* (the prose that explains why they are not
    read, and the `FEATURE_INDEPENDENCE` note). A line that actually read one
    would have to name it as code, and that is what this counts.
    """
    parameters = inspect.signature(live_entitlement_sweep.revoke_lapsed).parameters
    assert "config" not in parameters

    source = inspect.getsource(live_entitlement_sweep)
    tree = ast.parse(source)
    in_literals = "\n".join(
        node.value for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str))
    for flag in ("SCANGRADE_LIVE_ENABLED", "SCANGRADE_LIVE_EXAM_ENABLED"):
        assert source.count(flag) > 0, "the rule stopped being stated"
        assert source.count(flag) == in_literals.count(flag), (
            f"{flag} appears outside a string literal in the sweep: it is read there")


def test_the_maintenance_pass_runs_the_sweep(monkeypatch):
    """The wiring: a sweep nobody calls is a module, not a revocation."""
    import app.utils.auth as auth
    from app.services import live_policy, live_reconcile, livekit_gateway

    db = _db(licences=[EXPIRED], sessions=[SESSION_1])
    deleted = []
    monkeypatch.setattr(live_policy, "client_ready", lambda *_: True)
    monkeypatch.setattr(auth, "get_supabase", lambda: db)
    monkeypatch.setattr(livekit_gateway, "end_room",
                        lambda _config, room: deleted.append(room))
    monkeypatch.setattr(livekit_gateway, "room_names", lambda *_: [])
    monkeypatch.setattr(live_reconcile, "reconcile",
                        lambda *a, **k: {"measured": True})

    app = Flask(__name__)
    result = live_cleanup.run_once(app, now=NOW)

    assert result["ran"] is True
    assert result["entitlement"]["revoked"] == 1
    assert deleted == ["sg_one"]


# ── the tri-state read itself, and that `active()` did not change ────────────
def test_state_separates_a_refusal_from_a_failure():
    assert entitlement.state(_db(licences=[PAID]), "school-1",
                             now=entitlement.datetime(2026, 10, 25,
                                                      tzinfo=entitlement.timezone.utc)) == entitlement.ACTIVE
    assert entitlement.state(_db(licences=[EXPIRED]), "school-1") == entitlement.INACTIVE
    assert entitlement.state(_db(), "school-1") == entitlement.INACTIVE
    assert entitlement.state(_db(), "") == entitlement.INACTIVE

    class Broken(FakeDB):
        def table(self, name):
            raise RuntimeError("database unavailable")

    assert entitlement.state(Broken(), "school-1") == entitlement.UNKNOWN


def test_active_is_still_default_deny():
    """Every existing caller keeps the answer it had: only ACTIVE is a yes."""
    now = entitlement.datetime(2026, 10, 25, tzinfo=entitlement.timezone.utc)
    assert entitlement.active(_db(licences=[PAID]), "school-1", now=now) is True

    class Broken(FakeDB):
        def table(self, name):
            raise RuntimeError("database unavailable")

    for db, school in ((_db(), "school-1"), (Broken(), "school-1"),
                       (_db(licences=[EXPIRED]), "school-1"),
                       (_db(licences=[PAID]), "")):
        assert entitlement.active(db, school, now=now) is False
