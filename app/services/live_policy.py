"""Locked-down ScanGrade Live Exam Mode defaults.

The exam system is authoritative. Media failures never alter an exam attempt.
Teacher customization for ordinary class sessions is deliberately a separate mode.
"""
from __future__ import annotations

import hashlib
import hmac
import os
from datetime import timedelta

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


def allowed_sources(role: str, microphone_approved: bool = False) -> list[str]:
    if role == "teacher":
        return ["camera", "microphone"]
    if role == "student":
        return ["camera", "microphone"] if microphone_approved else ["camera"]
    raise ValueError("Unrecognized Live participant role")


TOKEN_TTL = timedelta(minutes=5)
