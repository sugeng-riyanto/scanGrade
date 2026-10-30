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
LIVE_RECORDER = "recorder"  # SFU-only receive-only viewer; never a database role.
# NOTE(schema_contract): compare participant kinds via these constants, never via
# a quoted literal — writing the name in quotes reads as a database role the CHECK
# constraint will never contain, and role_mismatches() rightly refuses it.

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

#: Mode pembelajaran (kelas online). Sengaja terpisah dari EXAM_POLICY:
#: ujian mengunci media demi integritas, kelas membuka interaksi demi belajar.
#: Nilai default OFF sama seperti ujian — diaktifkan eksplisit per sesi.
CLASS_POLICY = {
    "mode": "class",
    "camera": "optional",
    "microphone": "open",
    "chat": True,
    "raise_hand": True,
    "recording": "local_only",
    "screen_share": "teacher_only",
    "layout": "grid",
    "disconnect": "rejoin_keep_presence",
    "attendance": True,
}

MODES = ("exam", "class")

#: Aturan perekaman: server tidak pernah menyimpan blob media.
#: - exam: dilarang total (token room_record=False, tidak ada tombol rekam).
#: - class: hanya lokal di browser (MediaRecorder) / OBS via clean-feed
#:   receive-only; upload ke Drive milik masing-masing user via OAuth.
RECORDING_RULE = {
    "exam": "disabled",
    "class": "local_only_with_consent",
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


def policy_for(mode: str) -> dict:
    """Kembalikan policy sesuai mode; exam tetap default yang dikunci."""
    if mode == "class":
        return dict(CLASS_POLICY)
    if mode == "exam":
        return dict(EXAM_POLICY)
    raise ValueError("Unrecognized ScanMeet mode")


def allowed_sources_for(mode: str, role: str, microphone_approved: bool = False) -> list[str]:
    """Sumber publish per mode.

    - exam: murid hanya kamera sampai guru approve (tetap dikunci).
    - class: murid langsung kamera+mic (interaksi belajar terbuka).
    - recorder (kedua mode): receive-only, tidak publish apa pun.
    """
    if role == LIVE_RECORDER:
        return []
    if mode == "class":
        if role in (LIVE_TEACHER, LIVE_STUDENT):
            return ["camera", "microphone"]
        raise ValueError("Unrecognized Live participant role")
    return allowed_sources(role, microphone_approved)


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
