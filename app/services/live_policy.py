"""Locked-down ScanGrade Live Exam Mode defaults.

The exam system is authoritative. Media failures never alter an exam attempt.
Teacher customization for ordinary class sessions is deliberately a separate mode.
"""
from __future__ import annotations

import hashlib
import hmac
import os
from datetime import timedelta

LIVE_TEACHER = "teacher"  # SFU participant type, never a database account role
LIVE_STUDENT = "student"  # DB roles remain guru/murid/etc.

EXAM_POLICY = {
    "mode": "exam",
    "camera": "required",
    "microphone": "ask",
    "chat": False,
    "raise_hand": True,
    "recording": False,
    "screen_share": False,
    "layout": "grid",
    "disconnect": "alert_teacher_continue_exam",
}


def feature_enabled(config) -> bool:
    """Both switches must be on; default OFF, including on deployed servers."""
    def on(key):
        value = config.get(key, os.environ.get(key, "false"))
        return str(value).strip().lower() in ("true", "1", "yes", "on")
    return on("SCANGRADE_LIVE_ENABLED") and on("SCANGRADE_LIVE_EXAM_ENABLED")


def client_ready(config) -> bool:
    return all(config.get(k) or os.environ.get(k) for k in (
        "LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET"))


def setting(config, key) -> str:
    return str(config.get(key) or os.environ.get(key, "")).strip()


def participant_identity(config, session_id: str, user_id: str) -> str:
    """Opaque and deterministic within a room; never leak pupil identifiers."""
    secret = setting(config, "LIVEKIT_API_SECRET")
    if not secret:
        raise RuntimeError("LiveKit not configured")
    digest = hmac.new(secret.encode(), f"{session_id}:{user_id}".encode(),
                      hashlib.sha256).hexdigest()[:32]
    return "p_" + digest


def server_api_url(config) -> str:
    """Convert secure browser signaling WSS to LiveKit's HTTPS RoomService URL.

    LiveKit JS needs wss://; the Python admin API sends HTTP/Twirp requests.
    Fail closed for insecure or unrecognized endpoints in staging/production.
    """
    url = setting(config, "LIVEKIT_URL")
    if not url.startswith("wss://") or not url[6:]:
        raise ValueError("LIVEKIT_URL must be a secure wss:// endpoint")
    return "https://" + url[6:]


def allowed_sources(role: str, microphone_approved: bool = False) -> list[str]:
    if role == LIVE_TEACHER:
        return ["camera", "microphone"]
    if role == LIVE_STUDENT:
        return ["camera", "microphone"] if microphone_approved else ["camera"]
    raise ValueError("Unrecognized Live participant role")


#: The statuses migration 070's `scan_live_one_open_request` index counts as one
#: open request per pupil. `closing` belongs here because a close in flight still
#: owns the microphone: a pupil whose row is parked in `closing` has an open
#: request, and the route must show them that row instead of inserting a second
#: one the partial index will refuse.
OPEN_GRANTS = ("pending", "approving", "approved", "closing")

TOKEN_TTL = timedelta(minutes=5)
