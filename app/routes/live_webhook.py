"""Signed LiveKit webhooks: only isolated metadata, never exam submissions."""
from __future__ import annotations

import hashlib
import logging

from flask import Blueprint, current_app, request, jsonify
from app.services.live_policy import feature_enabled, client_ready, setting
from app.utils.auth import get_supabase

live_webhook_bp = Blueprint("live_webhook", __name__)
log = logging.getLogger(__name__)
EVENTS = {
    "room_started", "room_finished", "participant_joined", "participant_left",
    "participant_connection_aborted", "track_published", "track_unpublished",
    "egress_started", "egress_updated", "egress_ended",
}
MAX_BODY_BYTES = 65536


@live_webhook_bp.route("/webhook/livekit", methods=["POST"])
def receive_livekit():
    # Flask CSRF middleware exempts /webhook/*; the signed raw body is mandatory.
    if not feature_enabled(current_app.config):
        return jsonify({"error": "Live disabled"}), 404
    if not client_ready(current_app.config):
        return jsonify({"error": "Live not configured"}), 503
    if request.content_length and request.content_length > MAX_BODY_BYTES:
        return jsonify({"error": "Payload too large"}), 413
    auth_header = request.headers.get("Authorization", "")
    body_bytes = request.get_data(cache=False)
    if not auth_header or not body_bytes or len(body_bytes) > MAX_BODY_BYTES:
        return jsonify({"error": "Missing or oversized signed payload"}), 401
    try:
        from livekit import api
        validator = api.WebhookReceiver(api.TokenVerifier(
            setting(current_app.config, "LIVEKIT_API_KEY"),
            setting(current_app.config, "LIVEKIT_API_SECRET"),
        ))
        body = body_bytes.decode("utf-8")
        event = validator.receive(body, auth_header)
    except Exception:
        # Signature mismatches must NOT trigger any database writes.
        log.warning("LiveKit webhook rejected: bad signature or payload")
        return jsonify({"error": "Invalid signature"}), 401
    kind = event.event
    room = getattr(getattr(event, "room", None), "name", "")
    if kind not in EVENTS or not room:
        return "", 204
    db = get_supabase()
    try:
        sessions = (db.table("scan_live_sessions").select("id,school_id")
                    .eq("room_name", room).limit(1).execute().data or [])
        if not sessions:
            return "", 204
        session = sessions[0]
        who = getattr(getattr(event, "participant", None), "identity", "") or ""
        event_id = str(getattr(event, "id", "") or "")
        event_key = event_id if event_id else hashlib.sha256(body_bytes).hexdigest()
        db.table("scan_live_events").insert({
            "school_id": session["school_id"],
            "session_id": session["id"],
            "event_key": event_key,
            "event_type": kind,
            "participant_identity": who[:80],
        }).execute()
    except Exception as exc:
        if "23505" in str(exc) or "duplicate key" in str(exc).lower():
            return jsonify({"status": "already_processed"})
        log.exception("LiveKit webhook could not persist audit event")
        # Let LiveKit retry; exam state is still unaffected.
        return jsonify({"error": "Live audit unavailable"}), 503
    return jsonify({"status": "received"})
