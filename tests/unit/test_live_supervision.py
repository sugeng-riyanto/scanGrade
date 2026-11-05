"""Exercise authorization and cleanup ordering; SQL checks use real Postgres."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from flask import g
from app.routes import live
from app.services import live_cleanup, live_supervision
from tests.unit.live_fixtures import EXAM, SESSION, FakeDB, door, stage


def expiry(hours=1):
    return (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat()


def assignment(**kw):
    return {"exam_id": EXAM["id"], "school_id": EXAM["school_id"],
            "supervisor_id": "substitute", "valid_until": expiry(), "revoked_at": None, **kw}


@pytest.mark.parametrize("value,expected", [(None, False), (False, False), ("true", True), ("1", True), ("off", False)])
def test_flag_defaults_off(monkeypatch, value, expected):
    monkeypatch.delenv(live_supervision.FLAG, raising=False)
    assert live_supervision.enabled({} if value is None else {live_supervision.FLAG: value}) is expected


@pytest.mark.parametrize("change", [{}, {"valid_until": expiry(-1)}, {"revoked_at": expiry()},
                                  {"school_id": "other"}, {"valid_until": "bad"}, {"valid_until": "2028-01-01"}])
def test_assignment_expiry_school_and_revocation(change):
    db = FakeDB({"scanmeet_exam_supervisors": [assignment(**change)]})
    assert live_supervision.assigned(db, EXAM, "substitute") is (not change)


def test_assignee_has_media_access_only(monkeypatch):
    app, db, _ = stage(monkeypatch, {"scanmeet_exam_supervisors": [assignment()]})
    app.config[live_supervision.FLAG] = True
    with app.test_request_context():
        g.user_id, g.user_role, g.user_school_id = "substitute", "guru", "school-1"
        assert live._access(db, EXAM, staff_only=True) == "teacher"
        assert not live.can_manage_exam(g.user_id, g.user_role, g.user_school_id, EXAM)
        g.user_school_id = "other"
        assert live._access(db, EXAM, staff_only=True) is None
    assert door(app, "set_exam_enabled", EXAM["id"], user="substitute").status_code == 403
    assert door(app, "supervisors", EXAM["id"], user="substitute").status_code == 403


def test_pending_transfer_stops_tokens_without_leaking_room(monkeypatch):
    app, db, _ = stage(monkeypatch, {"scan_live_sessions": [{**SESSION, "transfer_state": "closing", "media_version": 1}]})
    assert door(app, "join_token", SESSION["id"]).status_code == 403
    data = door(app, "status", EXAM["id"]).get_json()
    assert data["handover_pending"] is True and data["session"] is None
    assert "sg_room" not in str(data)


def test_public_status_has_version_but_no_room_or_policy(monkeypatch):
    app, db, _ = stage(monkeypatch, {"scan_live_sessions": [{**SESSION, "media_version": 3, "secret_future_column": "private"}]})
    data = door(app, "status", EXAM["id"]).get_json()["session"]
    assert data["media_version"] == 3
    assert "room_name" not in data and "secret_future_column" not in data


def test_former_supervisor_cannot_end_rotated_session(monkeypatch):
    app, _, _ = stage(monkeypatch, {"scan_live_sessions": [{**SESSION, "created_by": "substitute", "media_version": 2}]})
    assert door(app, "end_session", SESSION["id"]).status_code == 403


def test_end_is_conditional_on_read_media_version(monkeypatch):
    app, db, _ = stage(monkeypatch, {"scan_live_sessions": [{**SESSION, "media_version": 1, "transfer_state": "ready"}]})
    monkeypatch.setattr(live, "_check", lambda *args, **kw: db.rows["scan_live_sessions"][0].update(media_version=2, created_by="substitute") or "teacher")
    assert door(app, "end_session", SESSION["id"]).status_code == 409
    assert db.rows["scan_live_sessions"][0]["status"] == "active"


@pytest.mark.parametrize("response", [None, [], {}, {"state": "pending"}])
def test_no_empty_or_pending_finish_can_close_debt(response):
    db = SimpleNamespace(rpc=lambda *args: SimpleNamespace(execute=lambda: SimpleNamespace(data=response)))
    with pytest.raises(RuntimeError):
        live_supervision.finish(db, {"handover_id": "handover", "claim_token": "lease"})


@pytest.mark.parametrize("deletion_error,callback_error,expected", [(None, None, "done"),
    (RuntimeError("not found"), None, "done"), (RuntimeError("offline"), None, "retried"),
    (None, RuntimeError("database unavailable"), "retried")])
def test_delete_before_callback_and_retry_both(monkeypatch, deletion_error, callback_error, expected):
    now = datetime.now(timezone.utc)
    db = FakeDB({live_cleanup.TABLE: [{"id": "job", "status": "pending", "room_name": "old", "handover_id": "handover", "claim_token": "", "attempts": 0}]})
    job = live_cleanup.claim_next(db, owner="test", now=now)
    calls = []
    def delete(room):
        calls.append("delete")
        if deletion_error:
            raise deletion_error
    def finish(db, job):
        calls.append("finish")
        assert db.jobs()[0]["status"] == "pending"
        if callback_error:
            raise callback_error
        return {"state": "completed"}
    monkeypatch.setattr(live_supervision, "finish", finish)
    assert live_cleanup.attempt({}, db, job, end_room=delete, now=now)["state"] == expected
    assert calls == (["delete"] if deletion_error and str(deletion_error) == "offline" else ["delete", "finish"])
    assert db.jobs()[0]["status"] == ("done" if expected == "done" else "pending")


def test_immediate_attempt_cannot_claim_another_schools_job():
    db = FakeDB({live_cleanup.TABLE: [{"id": "wrong", "school_id": "other", "status": "pending", "handover_id": "h"}]})
    assert live_supervision.attempt_handover({}, db, "h", "school-1", end_room=lambda _: pytest.fail("cross-school deletion")) == "pending"
    assert db.jobs()[0].get("claim_token") is None


@pytest.mark.parametrize("method", ["_record", "_mark_missing", "_park"])
def test_old_room_observation_cannot_overwrite_new_generation(method):
    from app.services import live_reconcile
    old = {**SESSION, "media_version": 1, "transfer_state": "ready", "media_state": "live"}
    new = {**old, "media_version": 2, "room_name": "sg_new", "missing_since": None}
    db = FakeDB({"scan_live_sessions": [new]})
    args = {"media_state": "missing"} if method == "_record" else {}
    assert not getattr(live_reconcile, method)(db, old, datetime.now(timezone.utc), **args)
    assert db.rows["scan_live_sessions"][0] == new
