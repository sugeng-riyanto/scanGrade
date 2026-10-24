"""A microphone grant interrupted mid-moderation must be repairable by the teacher.

``approve`` and ``close`` park a hand-raise row in ``approving``/``closing`` before
they call LiveKit, and only their own error path steps it back. A worker that dies
between the two statements — or whose own reset also fails, which the route logs as
"needs operator reconciliation" — leaves a row that ``hand_raise`` keeps handing
back to the pupil and that ``moderate`` refuses with 409. Nothing in the console
could move it. These tests drive the repair without a database or the SDK.
"""
import pytest
from flask import Flask, g

from app.routes import live


class _Query:
    """Minimal supabase-py shape: table().select|update().eq()…execute()."""

    def __init__(self, owner, name):
        self.owner = owner
        self.store = owner.rows
        self.name = name
        self.op = "select"
        self.payload = None
        self.filters = []
        self.limit_n = None
        self.broken = False

    def select(self, *_):
        self.op = "select"
        return self

    def update(self, payload):
        self.op = "update"
        self.payload = dict(payload)
        return self

    def eq(self, key, value):
        self.filters.append((key, value))
        return self

    def limit(self, n):
        self.limit_n = n
        return self

    def execute(self):
        rows = self.store.setdefault(self.name, [])
        if self.op == "update" and self.owner.race is not None:
            # Another writer decided the row between the read and the write.
            for row in rows:
                row["status"] = self.owner.race
            self.owner.race = None
        matched = [r for r in rows
                   if all(str(r.get(k)) == str(v) for k, v in self.filters)]
        if self.op == "select":
            if self.limit_n is not None:
                matched = matched[: self.limit_n]
            return type("R", (), {"data": [dict(r) for r in matched]})()
        if self.broken:
            raise RuntimeError("database unreachable")
        for row in matched:
            row.update(self.payload)
        return type("R", (), {"data": [dict(r) for r in matched]})()


class _DB:
    def __init__(self, rows):
        self.rows = {name: [dict(r) for r in items] for name, items in rows.items()}
        self.last_table = None
        self.race = None

    def table(self, name):
        self.last_table = name
        return _Query(self, name)


SESSION = {
    "id": "room-id", "status": "active", "exam_id": "exam-1",
    "created_by": "teacher-a", "school_id": "school-1", "room_name": "sg_room",
}
EXAM = {"id": "exam-1", "teacher_id": "teacher-a", "school_id": "school-1"}
RAISE = {"id": "raise-1", "session_id": "room-id", "school_id": "school-1",
         "student_id": "student-x", "status": "approving", "created_at": "t0"}

# The undecorated view: login_required is a wrapper, and the authorization this
# test exercises lives inside the function.
view = live.reconcile.__wrapped__


def _setup(monkeypatch, db, mute=None):
    monkeypatch.setattr(live, "get_supabase", lambda: db)
    monkeypatch.setattr(live, "_exam", lambda *_: EXAM)
    # A pupil is a *valid* room participant here, so the only thing that can
    # keep them out is the staff-only gate under test.
    monkeypatch.setattr(live, "exam_sitting_allowed", lambda *_: (True, ""))
    calls = []

    def fake_mute(config, session_id, room, student_id, enabled):
        calls.append((session_id, room, student_id, enabled))
        if isinstance(mute, Exception):
            raise mute

    monkeypatch.setattr(live.livekit_gateway, "set_student_mic", fake_mute)
    # Enabled by default here; the disabled case patches it off itself.
    monkeypatch.setattr(live.live_policy, "feature_enabled", lambda *_: True)
    app = Flask(__name__)
    return app, calls


def _call(app, role="guru", user="teacher-a"):
    """Run the view and present its ``(body, status)`` tuple as a response."""
    with app.test_request_context():
        g.user_school_id = "school-1"
        g.user_role = role
        g.user_id = user
        return app.make_response(view("room-id", "raise-1"))


def test_interrupted_approval_is_muted_and_returned_to_the_teacher(monkeypatch):
    db = _DB({"scan_live_sessions": [SESSION], "scan_live_hand_raises": [RAISE]})
    app, calls = _setup(monkeypatch, db)
    response = _call(app)
    assert response.status_code == 200
    assert response.get_json() == {"status": "pending"}
    # Fail closed: the microphone goes off before the row moves.
    assert calls == [("room-id", "sg_room", "student-x", False)]
    assert db.rows["scan_live_hand_raises"][0]["status"] == "pending"


def test_interrupted_close_finishes_the_close(monkeypatch):
    db = _DB({"scan_live_sessions": [SESSION],
              "scan_live_hand_raises": [dict(RAISE, status="closing")]})
    app, calls = _setup(monkeypatch, db)
    response = _call(app)
    assert response.get_json() == {"status": "closed"}
    assert calls[-1][3] is False
    assert db.rows["scan_live_hand_raises"][0]["status"] == "closed"


def test_a_failed_mute_leaves_the_row_repairable(monkeypatch):
    db = _DB({"scan_live_sessions": [SESSION], "scan_live_hand_raises": [RAISE]})
    app, _ = _setup(monkeypatch, db, mute=RuntimeError("LiveKit down"))
    response = _call(app)
    assert response.status_code == 503
    # Still parked, so the teacher can try again — never silently "approved".
    assert db.rows["scan_live_hand_raises"][0]["status"] == "approving"


def test_a_concurrent_decision_wins_over_the_repair(monkeypatch):
    """The write is conditional, so a row decided meanwhile is left alone."""
    db = _DB({"scan_live_sessions": [SESSION], "scan_live_hand_raises": [RAISE]})
    app, calls = _setup(monkeypatch, db)
    db.race = "approved"
    response = _call(app)
    assert response.status_code == 409
    assert db.rows["scan_live_hand_raises"][0]["status"] == "approved"
    assert calls, "a repair that lost the race still leaves the microphone closed"


def test_a_lost_state_transition_is_reported_rather_than_assumed(monkeypatch):
    db = _DB({"scan_live_sessions": [SESSION], "scan_live_hand_raises": [RAISE]})
    app, calls = _setup(monkeypatch, db)
    original = _Query.execute

    def execute(self):
        if self.op == "update":
            return type("R", (), {"data": []})()
        return original(self)

    monkeypatch.setattr(_Query, "execute", execute)
    response = _call(app)
    assert response.status_code == 409
    assert calls, "the microphone must be closed before the state is reported lost"


def test_pupil_cannot_clear_their_own_parked_grant(monkeypatch):
    db = _DB({"scan_live_sessions": [SESSION], "scan_live_hand_raises": [RAISE]})
    app, calls = _setup(monkeypatch, db)
    response = _call(app, role="murid", user="student-x")
    assert response.status_code == 403
    assert calls == []
    assert db.rows["scan_live_hand_raises"][0]["status"] == "approving"


def test_a_second_teacher_cannot_repair_another_room(monkeypatch):
    db = _DB({"scan_live_sessions": [SESSION], "scan_live_hand_raises": [RAISE]})
    app, calls = _setup(monkeypatch, db)
    response = _call(app, user="teacher-b")
    assert response.status_code == 403
    assert calls == []


def test_a_decision_that_needs_no_repair_is_refused(monkeypatch):
    db = _DB({"scan_live_sessions": [SESSION],
              "scan_live_hand_raises": [dict(RAISE, status="pending")]})
    app, calls = _setup(monkeypatch, db)
    response = _call(app)
    assert response.status_code == 409
    assert calls == [], "a pending request must not be muted behind the teacher"


def test_another_school_s_request_is_not_visible(monkeypatch):
    db = _DB({"scan_live_sessions": [dict(SESSION, school_id="school-2")],
              "scan_live_hand_raises": [RAISE]})
    app, calls = _setup(monkeypatch, db)
    response = _call(app)
    assert response.status_code == 403
    assert calls == []


def test_disabled_feature_answers_before_any_database_read(monkeypatch):
    db = _DB({"scan_live_sessions": [SESSION], "scan_live_hand_raises": [RAISE]})
    app, calls = _setup(monkeypatch, db)
    monkeypatch.setattr(live.live_policy, "feature_enabled", lambda *_: False)
    response = _call(app)
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
    db = _DB({"scan_live_sessions": [SESSION],
              "scan_live_hand_raises": [dict(RAISE, status=status)]})
    app, calls = _setup(monkeypatch, db)
    assert _call(app).status_code == 200
    assert db.rows["scan_live_hand_raises"][0]["status"] in ("pending", "closed")
    assert calls[-1][3] is False
