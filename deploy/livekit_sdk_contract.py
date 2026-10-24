"""Check the real optional LiveKit Python SDK's JWT and API constructors offline.

No LiveKit credentials or network calls. Run only on an isolated CI/staging host
with requirements-live.txt installed: python -m deploy.livekit_sdk_contract
"""
from __future__ import annotations

import asyncio
import base64
import json

from app.services import livekit_gateway, live_policy


def claims(jwt: str) -> dict:
    body = jwt.split(".")[1]
    body += "=" * (-len(body) % 4)
    return json.loads(base64.urlsafe_b64decode(body))


async def check_server_constructor(cfg: dict) -> None:
    from livekit import api

    client = api.LiveKitAPI(
        url=live_policy.server_api_url(cfg),
        api_key=cfg["LIVEKIT_API_KEY"],
        api_secret=cfg["LIVEKIT_API_SECRET"],
    )
    await client.aclose()


def main() -> int:
    cfg = {
        "LIVEKIT_URL": "wss://example.invalid",
        "LIVEKIT_API_KEY": "synthetic-local-key",
        "LIVEKIT_API_SECRET": "synthetic-local-secret-not-for-production",
    }
    from livekit import api
    teacher = livekit_gateway.issue_join_token(
        cfg, "fake-session", "fake-room", "teacher-1", "teacher"
    )
    student = livekit_gateway.issue_join_token(
        cfg, "fake-session", "fake-room", "student-1", "student",
        supervisor_id="teacher-1",
    )
    for result in (teacher, student):
        assert result["url"] == cfg["LIVEKIT_URL"]
        assert claims(result["token"])["video"]["room"] == "fake-room"
    video = claims(student["token"])["video"]
    assert video["canPublishSources"] == ["camera"], video.keys()
    assert video["canPublishData"] is False
    assert not video.get("roomAdmin", False)
    assert not video.get("roomRecord", False)
    assert student["supervisor_identity"] == live_policy.participant_identity(
        cfg, "fake-session", "teacher-1"
    )
    asyncio.run(check_server_constructor(cfg))
    print("PASS: pinned LiveKit SDK token grants and RoomService API constructor (offline)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
