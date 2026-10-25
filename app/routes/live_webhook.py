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

#: How much of an oversized delivery is drained before answering 413. The reverse
#: proxy has already buffered it (`client_max_body_size 1m` on the vhost), so this
#: costs no extra memory; it only lets the client *hear* the answer. Measured on
#: staging: without it gunicorn resets a connection that still has unread bytes and
#: nginx reports **502** for a route that correctly refused the body, which the
#: signed-webhook probe reads as a failure of the cap.
MAX_DRAIN_BYTES = 4 * 1024 * 1024
DRAIN_CHUNK = 64 * 1024


def _discard(stream, limit):
    """Read and drop at most `limit` bytes. Returns how many were dropped.

    Bounded on purpose: the whole point of the cap above is that a body is never
    buffered past it, so this discards rather than collects, and stops at the
    ceiling even if the client keeps sending.
    """
    dropped = 0
    while dropped < limit:
        chunk = stream.read(min(DRAIN_CHUNK, limit - dropped))
        if not chunk:
            break
        dropped += len(chunk)
    return dropped


@live_webhook_bp.route("/webhook/livekit", methods=["POST"])
def receive_livekit():
    # Flask CSRF middleware exempts /webhook/*; the signed raw body is mandatory.
    if not feature_enabled(current_app.config):
        return jsonify({"error": "Live disabled"}), 404
    if not client_ready(current_app.config):
        return jsonify({"error": "Live not configured"}), 503
    if request.content_length and request.content_length > MAX_BODY_BYTES:
        _discard(request.stream, min(request.content_length, MAX_DRAIN_BYTES))
        return jsonify({"error": "Payload too large"}), 413
    # A chunked delivery carries no Content-Length, so the header check above
    # cannot see it. Cap the read itself: one byte past the limit is enough to
    # know it was exceeded, and it is never buffered beyond that.
    #
    # Not by assigning `request.max_content_length`: on Werkzeug 3 that property
    # has no setter, so the assignment raised `AttributeError` and *every*
    # delivery — signed or not — answered 500. The bounded read is what actually
    # caps memory here.
    body_bytes = request.stream.read(MAX_BODY_BYTES + 1)
    if len(body_bytes) > MAX_BODY_BYTES:
        return jsonify({"error": "Payload too large"}), 413
    auth_header = request.headers.get("Authorization", "")
    if not auth_header or not body_bytes:
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
