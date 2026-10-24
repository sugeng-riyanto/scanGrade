"""Offline verification of the signed LiveKit webhook boundary."""
from flask import Flask
from app.routes import live_webhook as hook


def _app():
    app = Flask(__name__)
    app.register_blueprint(hook.live_webhook_bp)
    return app


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
