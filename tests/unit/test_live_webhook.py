"""Offline verification of the signed LiveKit webhook boundary.

The three rejection paths run against the real route with no SDK installed. The
authorisation paths need a *verified* body, which is the SDK's job, so those drive
it through a stand-in `livekit.api` whose `WebhookReceiver` returns a chosen event:
the route's contract with the SDK is two calls (`TokenVerifier`, `receive`), and the
question here is what the route does with the result, not whether LiveKit can sign.

The delivery itself used to answer **500** on every request: `request.max_content_length
= …` is a read-only property on Werkzeug 3, so the assignment raised before the
signature was ever checked. `test_webhook_rejects_missing_signature` was red for
that reason; these keep the whole boundary covered.
"""
import logging
import sys
import types

import pytest
from flask import Flask

from app.routes import live_webhook as hook
from tests.unit.live_fixtures import FakeDB


class _Event:
    def __init__(self, kind="participant_joined", room="sg_room", identity="p_abc",
                 event_id="evt-1"):
        self.event = kind
        self.id = event_id
        self.room = types.SimpleNamespace(name=room) if room is not None else None
        self.participant = (types.SimpleNamespace(identity=identity)
                            if identity is not None else None)


def _fake_sdk(monkeypatch, event, *, accept="Bearer good"):
    """A stand-in for `livekit.api`: signature check is one string comparison."""
    api = types.ModuleType("livekit.api")

    class TokenVerifier:
        def __init__(self, key, secret):
            self.key, self.secret = key, secret

    class WebhookReceiver:
        def __init__(self, verifier):
            self.verifier = verifier

        def receive(self, body, auth):
            if auth != accept:
                raise ValueError("invalid signature")
            return event

    api.TokenVerifier, api.WebhookReceiver = TokenVerifier, WebhookReceiver
    package = types.ModuleType("livekit")
    package.api = api
    monkeypatch.setitem(sys.modules, "livekit", package)
    monkeypatch.setitem(sys.modules, "livekit.api", api)


def _app():
    app = Flask(__name__)
    app.register_blueprint(hook.live_webhook_bp)
    return app


def _signed(monkeypatch, db=None, *, event=None, **kwargs):
    """The route, switched on, with a verified body and a fake database."""
    monkeypatch.setattr(hook, "feature_enabled", lambda *_: True)
    monkeypatch.setattr(hook, "client_ready", lambda *_: True)
    _fake_sdk(monkeypatch, event or _Event(), **kwargs)
    database = db if db is not None else FakeDB({"scan_live_sessions": [],
                                                 "scan_live_events": []})
    monkeypatch.setattr(hook, "get_supabase", lambda: database)
    return database


def _deliver(body=b'{"event":"participant_joined"}', auth="Bearer good"):
    return _app().test_client().post("/webhook/livekit", data=body,
                                     headers={"Authorization": auth})


def test_webhook_disabled_before_sdk_or_database(monkeypatch):
    monkeypatch.setattr(hook, "feature_enabled", lambda *_: False)
    response = _app().test_client().post("/webhook/livekit", data=b'{}')
    assert response.status_code == 404


def test_webhook_rejects_missing_signature(monkeypatch):
    monkeypatch.setattr(hook, "feature_enabled", lambda *_: True)
    monkeypatch.setattr(hook, "client_ready", lambda *_: True)
    response = _app().test_client().post("/webhook/livekit", data=b'{}')
    assert response.status_code == 401


def test_webhook_rejects_large_request(monkeypatch):
    monkeypatch.setattr(hook, "feature_enabled", lambda *_: True)
    monkeypatch.setattr(hook, "client_ready", lambda *_: True)
    response = _app().test_client().post("/webhook/livekit", data=b'x' * 65537,
                                          headers={"Authorization": "signed"})
    assert response.status_code == 413


def test_webhook_rejects_a_wrong_signature(monkeypatch):
    _signed(monkeypatch)
    response = _app().test_client().post("/webhook/livekit", data=b'{}',
                                          headers={"Authorization": "Bearer nope"})
    assert response.status_code == 401


def test_a_known_event_for_an_unknown_room_is_a_quiet_no_content(monkeypatch):
    _signed(monkeypatch)                       # no session carries sg_room
    assert _deliver().status_code == 204


def test_an_unknown_event_type_is_ignored_without_a_database_read(monkeypatch):
    db = _signed(monkeypatch, event=_Event(kind="something_new"))
    assert _deliver().status_code == 204
    assert "scan_live_events" not in db.rows or db.rows["scan_live_events"] == []


def test_an_event_for_a_known_room_is_recorded_once(monkeypatch):
    db = _signed(monkeypatch, FakeDB({
        "scan_live_sessions": [{"id": "room-id", "school_id": "school-1",
                                "room_name": "sg_room"}],
        "scan_live_events": []}))

    response = _deliver()

    assert response.status_code == 200
    assert response.get_json() == {"status": "received"}
    assert len(db.rows["scan_live_events"]) == 1
    assert db.rows["scan_live_events"][0]["event_key"] == "evt-1"


def test_a_redelivery_is_a_no_op_rather_than_a_duplicate(monkeypatch):
    db = _signed(monkeypatch, FakeDB({
        "scan_live_sessions": [{"id": "room-id", "school_id": "school-1",
                                "room_name": "sg_room"}],
        "scan_live_events": [{"event_key": "evt-1"}]}))

    response = _deliver()

    assert response.status_code == 200
    assert response.get_json() == {"status": "already_processed"}
    assert len(db.rows["scan_live_events"]) == 1


def test_the_rejection_log_carries_no_payload_or_signature(monkeypatch, caplog):
    """A rejected delivery must not put a JWT or a body into the journal."""
    _signed(monkeypatch, accept="Bearer good")
    body = b'{"secret":"do-not-log-this"}'

    with caplog.at_level(logging.WARNING, logger=hook.log.name):
        response = _app().test_client().post(
            "/webhook/livekit", data=body,
            headers={"Authorization": "Bearer wrong-signature-value"})

    assert response.status_code == 401
    text = " ".join(record.getMessage() for record in caplog.records)
    assert "do-not-log-this" not in text
    assert "wrong-signature-value" not in text
    assert "rejected" in text


@pytest.mark.parametrize("length,status", [(0, 401), (hook.MAX_BODY_BYTES, 204)])
def test_the_boundary_of_the_body_cap(monkeypatch, length, status):
    """Exactly at the cap is read (then answered 204 as an unknown room); one byte
    over is 413, checked above. An empty body is a 401, not a signature check on
    nothing."""
    _signed(monkeypatch, FakeDB({"scan_live_sessions": [], "scan_live_events": []}))
    response = _app().test_client().post("/webhook/livekit", data=b"x" * length,
                                          headers={"Authorization": "Bearer good"})
    assert response.status_code == status
