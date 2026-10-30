"""LiveKit API boundary; nothing imported or connected while Live is disabled."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone, timedelta
from app.services.live_policy import (
    TOKEN_TTL, LIVE_STUDENT, allowed_sources, client_ready, participant_identity, server_api_url, setting,
)


def issue_join_token(config, session_id: str, room: str, user_id: str,
                     role: str, microphone_approved: bool = False,
                     supervisor_id: str | None = None, expires_at=None) -> dict:
    if not client_ready(config):
        raise RuntimeError("LiveKit credentials are not configured")
    # Fail closed even without the optional SDK; validation must precede import.
    if role == LIVE_STUDENT and not supervisor_id:
        raise ValueError("Exam supervisor is required for student track isolation")
    from livekit import api

    identity = participant_identity(config, session_id, user_id)
    supervisor_identity = (participant_identity(config, session_id, supervisor_id)
                           if role == LIVE_STUDENT else None)
    grants = api.VideoGrants(
        room=room, room_join=True, room_admin=False, room_record=False,
        room_create=False, can_publish=True, can_subscribe=True,
        can_publish_data=False, can_update_own_metadata=False,
        can_publish_sources=allowed_sources(role, microphone_approved),
    )
    ttl = TOKEN_TTL
    if expires_at:
        from app.services.scanmeet_packages import parse_time
        deadline = parse_time(expires_at)
        if not deadline:
            raise ValueError("Invalid session deadline")
        remaining = (deadline - datetime.now(timezone.utc)).total_seconds()
        if remaining <= 0:
            raise ValueError("Session time has ended")
        ttl = min(ttl, timedelta(seconds=remaining))
    jwt = (api.AccessToken(setting(config, "LIVEKIT_API_KEY"),
                           setting(config, "LIVEKIT_API_SECRET"))
           .with_identity(identity).with_metadata('{"role":"' + role + '"}')
           .with_grants(grants).with_ttl(ttl).to_jwt())
    return {"url": setting(config, "LIVEKIT_URL"), "token": jwt,
            "room": room, "identity": identity, "role": role,
            "mic_approved": bool(microphone_approved),
            "supervisor_identity": supervisor_identity}


async def _ensure_capacity(config, room, limit):
    from livekit import api
    client = api.LiveKitAPI(url=server_api_url(config),
                            api_key=setting(config, "LIVEKIT_API_KEY"),
                            api_secret=setting(config, "LIVEKIT_API_SECRET"))
    try:
        await client.room.create_room(api.CreateRoomRequest(name=room, max_participants=int(limit)))
    finally:
        await client.aclose()


def ensure_capacity(config, room, limit):
    """Set the SFU participant cap before any limited-package token is issued."""
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 2:
        raise ValueError("Invalid participant limit")
    asyncio.run(_ensure_capacity(config, room, limit))


async def _set_student_mic(config, session_id, room, student_id, enabled):
    from livekit import api
    from livekit.protocol.models import ParticipantPermission, TrackSource
    from livekit.protocol.room import UpdateParticipantRequest

    identity = participant_identity(config, session_id, student_id)
    sources = [TrackSource.CAMERA]
    if enabled:
        sources.append(TrackSource.MICROPHONE)
    client = api.LiveKitAPI(
        url=server_api_url(config),
        api_key=setting(config, "LIVEKIT_API_KEY"),
        api_secret=setting(config, "LIVEKIT_API_SECRET"),
    )
    try:
        await client.room.update_participant(UpdateParticipantRequest(
            room=room, identity=identity,
            permission=ParticipantPermission(
                can_publish=True, can_subscribe=True, can_publish_data=False,
                can_publish_sources=sources,
            ),
        ))
    finally:
        await client.aclose()


def set_student_mic(config, session_id, room, student_id, enabled):
    # This API is invoked only by teacher-authorized moderation routes.
    asyncio.run(_set_student_mic(config, session_id, room, student_id, enabled))


async def _end_room(config, room):
    from livekit import api
    from livekit.protocol.room import DeleteRoomRequest
    client = api.LiveKitAPI(url=server_api_url(config),
                            api_key=setting(config, "LIVEKIT_API_KEY"),
                            api_secret=setting(config, "LIVEKIT_API_SECRET"))
    try:
        await client.room.delete_room(DeleteRoomRequest(room=room))
    finally:
        await client.aclose()


def end_room(config, room):
    asyncio.run(_end_room(config, room))


async def _room_names(config):
    from livekit import api
    from livekit.protocol.room import ListRoomsRequest
    client = api.LiveKitAPI(url=server_api_url(config),
                            api_key=setting(config, "LIVEKIT_API_KEY"),
                            api_secret=setting(config, "LIVEKIT_API_SECRET"))
    try:
        response = await client.room.list_rooms(ListRoomsRequest())
    finally:
        await client.aclose()
    return [room.name for room in response.rooms]


def room_names(config) -> list[str]:
    """The rooms the SFU currently serves, for `live_reconcile`.

    Raises rather than answering `[]` when the call fails: an empty list means
    "no rooms exist", and a sweep told that would park every live session because
    LiveKit was briefly unreachable.
    """
    return asyncio.run(_room_names(config))
