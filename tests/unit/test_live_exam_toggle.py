"""The per-examination ScanMeet switch: persisted, authorized, and revocable.

Migration 070 made a room possible; migration 071 records the teacher's decision
about the paper, and everything else follows from three claims that are tested
here rather than assumed:

* **OFF is a revocation.** It ends an active session in the database — the row
  every join token checks — before asking LiveKit to delete anything, and a
  media failure is reported as debt (`media_cleanup: pending`) instead of
  leaving the paper usable or failing the toggle.
* **ON is permission, not a room.** Toggling on never inserts a session, so two
  simultaneous ON requests cannot create two rooms, and a teacher who switches
  the paper on early publishes nothing.
* **The server decides.** A student, a teacher from another school, and a worker
  whose kill flags are off all get a refusal; the button's own text is never the
  authority.

The database is `tests/unit/live_fixtures.py`'s in-memory stand-in, which does
the conditional updates and the partial unique index of migration 070 for real.
"""
import pytest

from app.routes import live
from app.services import live_exam_toggle
from tests.unit.live_fixtures import FakeDB, FakeQuery, SESSION, door

EXAM = {
    "id": "exam-1", "teacher_id": "teacher-a", "school_id": "school-1",
    "scan_live_enabled": False,
}


def _exam_row(**overrides):
    row = dict(EXAM)
    row.update(overrides)
    return row


def _rows(exam=None, sessions=None):
    return {"exams": [exam or _exam_row()], "scan_live_sessions": sessions or []}


def _session(status="active", **overrides):
    row = {"id": "room-id", "school_id": "school-1", "exam_id": "exam-1",
           "room_name": "sg_room", "created_by": "teacher-a", "status": status}
    row.update(overrides)
    return row


def _set(db, value, calls=None, exam=None):
    """Write the switch the way the route does, with `exam` as ``_exam`` read it."""
    return live_exam_toggle.set_enabled(
        db, exam or dict(db.rows["exams"][0]), value=value,
        end_room=lambda room: (calls if calls is not None else []).append(room))


# ── the service ──────────────────────────────────────────────────────────────
def test_off_ends_the_active_room_and_names_it():
    db = FakeDB(_rows(sessions=[_session()]))
    calls = []
    result = _set(db, False, calls)

    assert result == {"enabled": False, "ended_session": "room-id",
                      "media_cleanup": None}
    assert db.rows["scan_live_sessions"][0]["status"] == "ended"
    assert db.rows["exams"][0]["scan_live_enabled"] is False
    assert calls == ["sg_room"]


def test_off_survives_a_media_cleanup_failure_because_the_row_is_the_gate():
    db = FakeDB(_rows(sessions=[_session()]))
    exam = db.rows["exams"][0]

    def unreachable(room):
        raise RuntimeError("LiveKit unreachable")

    result = live_exam_toggle.set_enabled(db, exam, value=False, end_room=unreachable)

    assert result["enabled"] is False
    assert result["ended_session"] == "room-id"
    assert result["media_cleanup"] == "pending"
    # The database is what every join token reads, so the revocation stands.
    assert db.rows["scan_live_sessions"][0]["status"] == "ended"
    assert db.rows["exams"][0]["scan_live_enabled"] is False


def test_off_with_no_room_is_only_the_switch():
    db = FakeDB(_rows())
    calls = []
    result = _set(db, False, calls)

    assert result == {"enabled": False, "ended_session": None, "media_cleanup": None}
    assert calls == []
    assert db.rows["exams"][0]["scan_live_enabled"] is False


def test_on_never_creates_a_room_even_when_asked_twice():
    db = FakeDB(_rows())
    calls = []

    _set(db, True, calls)
    _set(db, True, calls)

    assert db.rows["scan_live_sessions"] == []
    assert db.rows["exams"][0]["scan_live_enabled"] is True
    assert calls == []


def test_on_leaves_a_running_room_to_its_own_supervisor():
    db = FakeDB(_rows(sessions=[_session()]))
    calls = []
    result = _set(db, True, calls)

    assert result == {"enabled": True, "ended_session": None, "media_cleanup": None}
    assert db.rows["scan_live_sessions"][0]["status"] == "active"
    assert calls == []


def test_a_paper_in_another_school_is_not_ours_to_switch():
    """The route's school check is first; this one holds even if it drifts."""
    db = FakeDB(_rows(exam=_exam_row(school_id="school-2")))
    with pytest.raises(live_exam_toggle.Refused):
        _set(db, True, exam=_exam_row())  # claims school-1, row says school-2
    assert db.rows["exams"][0]["scan_live_enabled"] is False


def test_a_box_without_the_migration_refuses_instead_of_pretending():
    exam = _exam_row()

    class Unmigrated:
        def table(self, name):
            query = FakeQuery(self, name)
            query.execute = lambda: (_ for _ in ()).throw(
                RuntimeError('column "scan_live_enabled" does not exist'))
            return query

    with pytest.raises(live_exam_toggle.Refused):
        live_exam_toggle.set_enabled(Unmigrated(), exam, value=True,
                                     end_room=lambda room: None)


def test_only_the_boolean_true_counts_as_on():
    assert live_exam_toggle.enabled({"scan_live_enabled": True}) is True
    for value in (False, "true", 1, None):
        assert live_exam_toggle.enabled({"scan_live_enabled": value}) is False
    assert live_exam_toggle.enabled({}) is False
    assert live_exam_toggle.enabled(None) is False


# ── the route ────────────────────────────────────────────────────────────────
def _stage(monkeypatch, rows):
    """Route wired to the fake, with the kill flags on and LiveKit recorded."""
    db = FakeDB(rows)
    ended = []

    def fake_exam(_db, exam_id):
        for row in db.rows.get("exams", []):
            if str(row.get("id")) == str(exam_id):
                return dict(row)
        return None

    monkeypatch.setattr(live, "get_supabase", lambda: db)
    monkeypatch.setattr(live, "_exam", fake_exam)
    monkeypatch.setattr(live, "exam_sitting_allowed", lambda *_: (True, ""))
    monkeypatch.setattr(live.live_policy, "feature_enabled", lambda *_: True)
    monkeypatch.setattr(live.live_policy, "client_ready", lambda *_: True)
    monkeypatch.setattr(live.livekit_gateway, "end_room",
                        lambda config, room: ended.append(room))
    return db, ended


def _toggle(app, payload, **who):
    view = live.set_exam_enabled.__wrapped__
    from flask import g

    with app.test_request_context(json=payload):
        for key, value in who.items():
            setattr(g, key, value)
        return app.make_response(view("exam-1"))


def test_a_student_cannot_toggle_the_paper(app, monkeypatch):
    db, ended = _stage(monkeypatch, _rows())
    response = _toggle(app, {"enabled": True}, user_school_id="school-1",
                       user_role="murid", user_id="student-x")

    assert response.status_code == 403
    assert db.rows["exams"][0]["scan_live_enabled"] is False
    assert ended == []


def test_a_teacher_from_another_school_cannot_toggle_the_paper(app, monkeypatch):
    db, _ = _stage(monkeypatch, _rows())
    response = _toggle(app, {"enabled": True}, user_school_id="school-2",
                       user_role="guru", user_id="teacher-a")

    assert response.status_code == 403
    assert db.rows["exams"][0]["scan_live_enabled"] is False


def test_another_teacher_at_the_same_school_cannot_toggle_somebody_else_s_paper(
        app, monkeypatch):
    db, _ = _stage(monkeypatch, _rows())
    response = _toggle(app, {"enabled": True}, user_school_id="school-1",
                       user_role="guru", user_id="teacher-b")

    assert response.status_code == 403
    assert db.rows["exams"][0]["scan_live_enabled"] is False


@pytest.mark.parametrize("payload", [{}, {"enabled": "yes"}, {"enabled": 1},
                                     {"enabled": None}])
def test_the_body_must_be_a_real_boolean(app, monkeypatch, payload):
    db, _ = _stage(monkeypatch, _rows())
    response = _toggle(app, payload, user_school_id="school-1",
                       user_role="guru", user_id="teacher-a")

    assert response.status_code == 400
    assert db.rows["exams"][0]["scan_live_enabled"] is False


def test_teacher_toggles_on_then_off_and_the_room_ends(app, monkeypatch):
    db, ended = _stage(monkeypatch, _rows(sessions=[_session()]))

    on = _toggle(app, {"enabled": True}, user_school_id="school-1",
                 user_role="guru", user_id="teacher-a")
    assert on.status_code == 200
    assert on.get_json() == {"enabled": True, "ended_session": None,
                             "media_cleanup": None}
    assert db.rows["exams"][0]["scan_live_enabled"] is True
    assert db.rows["scan_live_sessions"][0]["status"] == "active"

    off = _toggle(app, {"enabled": False}, user_school_id="school-1",
                  user_role="guru", user_id="teacher-a")
    assert off.status_code == 200
    assert off.get_json()["ended_session"] == "room-id"
    assert db.rows["exams"][0]["scan_live_enabled"] is False
    assert db.rows["scan_live_sessions"][0]["status"] == "ended"
    assert ended == ["sg_room"]


def test_disabling_reports_pending_cleanup_without_refusing(app, monkeypatch):
    db, _ = _stage(monkeypatch, _rows(sessions=[_session()]))
    monkeypatch.setattr(live.livekit_gateway, "end_room",
                        lambda config, room: (_ for _ in ()).throw(
                            RuntimeError("LiveKit unreachable")))

    response = _toggle(app, {"enabled": False}, user_school_id="school-1",
                       user_role="guru", user_id="teacher-a")

    assert response.status_code == 200
    assert response.get_json()["media_cleanup"] == "pending"
    assert db.rows["scan_live_sessions"][0]["status"] == "ended"


def test_the_deployment_kill_switches_still_win(app, monkeypatch):
    db, _ = _stage(monkeypatch, _rows())
    monkeypatch.setattr(live.live_policy, "feature_enabled", lambda *_: False)

    response = _toggle(app, {"enabled": True}, user_school_id="school-1",
                       user_role="guru", user_id="teacher-a")

    assert response.status_code == 404
    assert db.rows["exams"][0]["scan_live_enabled"] is False


def test_status_reports_the_stored_switch_not_a_guess(app, monkeypatch):
    db, _ = _stage(monkeypatch, _rows(exam=_exam_row(scan_live_enabled=True)))
    response = door(app, "status", "exam-1")
    assert response.status_code == 200
    assert response.get_json()["enabled"] is True

    db.rows["exams"][0]["scan_live_enabled"] = False
    assert door(app, "status", "exam-1").get_json()["enabled"] is False


def test_a_pupil_cannot_join_a_paper_that_was_switched_off(app, monkeypatch):
    _stage(monkeypatch, _rows(sessions=[_session()]))

    response = door(app, "join_token", "room-id", role="murid",
                    user="student-x", school="school-1")

    assert response.status_code == 409
    assert response.get_json()["error"] == "ScanMeet is off for this exam"


def test_a_teacher_cannot_start_a_room_for_a_paper_that_is_off(app, monkeypatch):
    db, _ = _stage(monkeypatch, _rows())

    response = door(app, "create_session", "exam-1")

    assert response.status_code == 409
    assert response.get_json()["error"] == "ScanMeet is off for this exam"
    assert db.rows["scan_live_sessions"] == []


def test_switching_back_on_starts_a_fresh_room_not_the_ended_one(app, monkeypatch):
    """The acceptance matrix's "never revives revoked grants" row.

    A re-enabled paper must reach `POST /sessions`, which inserts a new room
    with a new `room_name`; the ended row stays ended and its tokens stay dead.
    """
    db, _ = _stage(monkeypatch, _rows())
    teacher = dict(user_school_id="school-1", user_role="guru", user_id="teacher-a")

    _toggle(app, {"enabled": True}, **teacher)
    first = door(app, "create_session", "exam-1")
    assert first.status_code == 201
    assert first.get_json()["existing"] is False

    _toggle(app, {"enabled": False}, **teacher)
    _toggle(app, {"enabled": True}, **teacher)
    second = door(app, "create_session", "exam-1")

    assert second.status_code == 201
    assert second.get_json()["existing"] is False
    sessions = db.rows["scan_live_sessions"]
    assert [row["status"] for row in sessions] == ["ended", "active"]
    assert sessions[0]["room_name"] != sessions[1]["room_name"]


def test_the_switch_touches_no_exam_state(app, monkeypatch):
    """Answers, timer and submissions are not columns of this feature."""
    db, _ = _stage(monkeypatch, _rows(sessions=[_session()]))
    before = {key: len(value) for key, value in db.rows.items()}

    _toggle(app, {"enabled": True}, user_school_id="school-1",
            user_role="guru", user_id="teacher-a")
    _toggle(app, {"enabled": False}, user_school_id="school-1",
            user_role="guru", user_id="teacher-a")

    assert {key: len(value) for key, value in db.rows.items()} == before
    assert set(db.rows) == {"exams", "scan_live_sessions"}


def test_a_box_without_the_migration_reads_the_paper_as_off(monkeypatch):
    """The fallback is the feature's default, not a silent pass.

    PostgREST answers a select naming a missing column with an error, so a box
    that has not applied migration 071 must be told the paper is OFF instead of
    failing every page — and the fallback must actually drop the column, or
    `enabled()` would read a value nobody selected.
    """
    db = FakeDB(_rows(exam=_exam_row(scan_live_enabled=True)))
    real_select = FakeQuery.select

    def guarded(self, *columns):
        if columns and "scan_live_enabled" in columns[0]:
            raise RuntimeError('column exams.scan_live_enabled does not exist')
        return real_select(self, *columns)

    monkeypatch.setattr(FakeQuery, "select", guarded)
    monkeypatch.setattr(live, "get_supabase", lambda: db)

    exam = live._exam(db, "exam-1")

    assert "scan_live_enabled" not in exam
    assert live_exam_toggle.enabled(exam) is False


def test_a_database_error_that_is_not_the_missing_column_still_raises(monkeypatch):
    """The fallback above must not become a blanket swallow."""
    db = FakeDB(_rows())
    real_select = FakeQuery.select

    def broken(self, *columns):
        if columns and "scan_live_enabled" in columns[0]:
            raise RuntimeError("connection reset by peer")
        return real_select(self, *columns)

    monkeypatch.setattr(FakeQuery, "select", broken)
    monkeypatch.setattr(live, "get_supabase", lambda: db)

    with pytest.raises(RuntimeError, match="connection reset"):
        live._exam(db, "exam-1")


def test_the_switch_is_checked_before_any_media_call(app, monkeypatch):
    """OFF must refuse before LiveKit is reached, not after a token exists."""
    _stage(monkeypatch, _rows(sessions=[_session()]))
    issued = []
    monkeypatch.setattr(live.livekit_gateway, "issue_join_token",
                        lambda *a, **k: issued.append(a) or {})

    response = door(app, "join_token", "room-id")

    assert response.status_code == 409
    assert issued == []


def test_an_active_session_from_before_the_switch_is_refused_after_it(app, monkeypatch):
    """The room row could not have been created while off — but if it exists."""
    db, _ = _stage(monkeypatch, _rows(sessions=[_session()]))
    assert SESSION["status"] == "active"

    response = _toggle(app, {"enabled": False}, user_school_id="school-1",
                       user_role="guru", user_id="teacher-a")

    assert response.status_code == 200
    already_ended = door(app, "join_token", "room-id", role="guru",
                         user="teacher-a", school="school-1")
    assert already_ended.status_code == 403
    assert db.rows["scan_live_sessions"][0]["status"] == "ended"
