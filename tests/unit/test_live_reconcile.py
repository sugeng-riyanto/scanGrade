"""A microphone grant interrupted mid-moderation must be repairable by the teacher.

``approve`` and ``close`` park a hand-raise row in ``approving``/``closing`` before
they call LiveKit, and only their own error path steps it back. A worker that dies
between the two statements — or whose own reset also fails, which the route logs as
"needs operator reconciliation" — leaves a row that ``hand_raise`` keeps handing
back to the pupil and that ``moderate`` refuses with 409. Nothing in the console
could move it. These tests drive the repair without a database or the SDK.

The exhaustive from→action→to matrix lives in `test_live_mic_state_machine.py`;
this file is the repair itself, including the ways it can fail.
"""
import pytest
from flask import Flask

from app.routes import live
from tests.unit import live_fixtures as fx
from tests.unit.live_fixtures import RAISE, SESSION, door, stage


def _rows(status="approving", session=None):
    return {
        "scan_live_sessions": [session or SESSION],
        "scan_live_hand_raises": [dict(RAISE, status=status)],
    }


def test_interrupted_approval_is_muted_and_returned_to_the_teacher(monkeypatch):
    app, db, calls = stage(monkeypatch, _rows())
    response = door(app, "reconcile", "room-id", "raise-1")
    assert response.status_code == 200
    assert response.get_json() == {"status": "pending"}
    # Fail closed: the microphone goes off before the row moves.
    assert calls == [("room-id", "sg_room", "student-x", False)]
    assert db.statuses() == ["pending"]


def test_interrupted_close_finishes_the_close(monkeypatch):
    app, db, calls = stage(monkeypatch, _rows("closing"))
    response = door(app, "reconcile", "room-id", "raise-1")
    assert response.get_json() == {"status": "closed"}
    assert calls[-1][3] is False
    assert db.statuses() == ["closed"]


def test_a_failed_mute_leaves_the_row_repairable(monkeypatch):
    app, db, _ = stage(monkeypatch, _rows(), mic_errors=[RuntimeError("LiveKit down")])
    response = door(app, "reconcile", "room-id", "raise-1")
    assert response.status_code == 503
    # Still parked, so the teacher can try again — never silently "approved".
    assert db.statuses() == ["approving"]


def test_a_concurrent_decision_wins_over_the_repair(monkeypatch):
    """The write is conditional, so a row decided meanwhile is left alone."""
    app, db, calls = stage(monkeypatch, _rows())
    db.race = "approved"
    response = door(app, "reconcile", "room-id", "raise-1")
    assert response.status_code == 409
    assert db.statuses() == ["approved"]
    assert calls, "a repair that lost the race still leaves the microphone closed"


def test_a_lost_state_transition_is_reported_rather_than_assumed(monkeypatch):
    app, _db, calls = stage(monkeypatch, _rows())
    original = fx.FakeQuery.execute

    def execute(self):
        if self.op == "update":
            return fx._Result([])
        return original(self)

    monkeypatch.setattr(fx.FakeQuery, "execute", execute)
    response = door(app, "reconcile", "room-id", "raise-1")
    assert response.status_code == 409
    assert calls, "the microphone must be closed before the state is reported lost"


def test_pupil_cannot_clear_their_own_parked_grant(monkeypatch):
    app, db, calls = stage(monkeypatch, _rows())
    response = door(app, "reconcile", "room-id", "raise-1",
                    role="murid", user="student-x")
    assert response.status_code == 403
    assert calls == []
    assert db.statuses() == ["approving"]


def test_a_second_teacher_cannot_repair_another_room(monkeypatch):
    app, _db, calls = stage(monkeypatch, _rows())
    response = door(app, "reconcile", "room-id", "raise-1", user="teacher-b")
    assert response.status_code == 403
    assert calls == []


def test_a_decision_that_needs_no_repair_is_refused(monkeypatch):
    app, _db, calls = stage(monkeypatch, _rows("pending"))
    response = door(app, "reconcile", "room-id", "raise-1")
    assert response.status_code == 409
    assert calls == [], "a pending request must not be muted behind the teacher"


def test_another_school_s_request_is_not_visible(monkeypatch):
    app, _db, calls = stage(
        monkeypatch, _rows("approving", session=dict(SESSION, school_id="school-2")))
    response = door(app, "reconcile", "room-id", "raise-1")
    assert response.status_code == 403
    assert calls == []


def test_disabled_feature_answers_before_any_database_read(monkeypatch):
    app, _db, calls = stage(monkeypatch, _rows(), live_on=False)
    response = door(app, "reconcile", "room-id", "raise-1")
    assert response.status_code == 404
    assert calls == []


def test_the_repair_route_is_not_swallowed_by_the_action_route():
    """``/<action>`` also matches the literal ``reconcile``; the static rule has
    to win, or this endpoint answers 400 "Invalid action" from ``moderate``."""
    app = Flask(__name__)
    app.register_blueprint(live.live_bp)
    endpoint, _ = app.url_map.bind("localhost").match(
        "/api/live/sessions/room-1/hand-raises/raise-1/reconcile", method="POST")
    assert endpoint == "live.reconcile", endpoint


@pytest.mark.parametrize("status", ["approving", "closing"])
def test_only_transitional_states_are_repairable(monkeypatch, status):
    app, db, calls = stage(monkeypatch, _rows(status))
    assert door(app, "reconcile", "room-id", "raise-1").status_code == 200
    assert db.statuses()[0] in ("pending", "closed")
    assert calls[-1][3] is False
