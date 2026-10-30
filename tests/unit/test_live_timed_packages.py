"""Pinned LiveKit SDK boundary checks; all network calls are replaced."""
import base64
from datetime import datetime, timedelta, timezone
import json
from unittest.mock import AsyncMock

import pytest

from app.services import livekit_gateway as gateway

api = pytest.importorskip('livekit.api', reason='optional LiveKit SDK is exercised by the SDK workflow')

CONFIG = {'LIVEKIT_URL': 'wss://demo.livekit.cloud', 'LIVEKIT_API_KEY': 'test-key',
          'LIVEKIT_API_SECRET': 'x' * 40, 'SECRET_KEY': 'local-only-secret'}


def test_join_jwt_cannot_outlive_session_deadline():
    deadline = datetime.now(timezone.utc) + timedelta(seconds=45)
    reply = gateway.issue_join_token(CONFIG, 'session', 'room', 'teacher', 'teacher', expires_at=deadline.isoformat())
    segment = reply['token'].split('.')[1]
    claims = json.loads(base64.urlsafe_b64decode(segment + '=' * (-len(segment) % 4)))
    assert 0 < claims['exp'] - claims['nbf'] <= 45
    assert claims['exp'] <= deadline.timestamp()


@pytest.mark.parametrize('deadline', ['broken', (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()])
def test_invalid_or_expired_deadline_cannot_issue_token(deadline):
    with pytest.raises(ValueError):
        gateway.issue_join_token(CONFIG, 'session', 'room', 'teacher', 'teacher', expires_at=deadline)


def test_room_capacity_uses_sdk_and_closes_client_on_failure(monkeypatch):
    class Client:
        room = type('Room', (), {'create_room': AsyncMock(side_effect=RuntimeError('unavailable'))})()
        aclose = AsyncMock()
    client = Client()
    monkeypatch.setattr(api, 'LiveKitAPI', lambda **kwargs: client)
    with pytest.raises(RuntimeError):
        gateway.ensure_capacity(CONFIG, 'room', 35)
    request = client.room.create_room.call_args.args[0]
    assert request.name == 'room' and request.max_participants == 35
    client.aclose.assert_awaited_once()


@pytest.mark.parametrize('cap', [True, 0, 1, '35', None])
def test_invalid_room_capacity_fails_before_connecting(monkeypatch, cap):
    factory = AsyncMock()
    monkeypatch.setattr(api, 'LiveKitAPI', factory)
    with pytest.raises(ValueError):
        gateway.ensure_capacity(CONFIG, 'room', cap)
    factory.assert_not_called()
