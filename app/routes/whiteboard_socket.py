"""Whiteboard WebSocket events — real-time collaboration."""
from flask import g, request


def _safe(fn):
    """Decorator to prevent socket handler crashes from killing the worker."""
    import functools
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception:
            pass
    return wrapper


def register_socket_events(socketio):
    """Register all whiteboard SocketIO event handlers."""

    @socketio.on("connect", namespace="/whiteboard")
    @_safe
    def on_connect():
        pass

    @socketio.on("disconnect", namespace="/whiteboard")
    @_safe
    def on_disconnect():
        pass

    @socketio.on("join", namespace="/whiteboard")
    @_safe
    def on_join(data):
        whiteboard_id = data.get("whiteboard_id") if isinstance(data, dict) else None
        if whiteboard_id:
            socketio.enter_room(request.sid, whiteboard_id, namespace="/whiteboard")
            socketio.emit("user_joined", {"sid": request.sid}, room=whiteboard_id, namespace="/whiteboard")

    @socketio.on("leave", namespace="/whiteboard")
    @_safe
    def on_leave(data):
        whiteboard_id = data.get("whiteboard_id") if isinstance(data, dict) else None
        if whiteboard_id:
            socketio.leave_room(request.sid, whiteboard_id, namespace="/whiteboard")
            socketio.emit("user_left", {"sid": request.sid}, room=whiteboard_id, namespace="/whiteboard")

    @socketio.on("draw", namespace="/whiteboard")
    @_safe
    def on_draw(data):
        whiteboard_id = data.get("whiteboard_id") if isinstance(data, dict) else None
        if whiteboard_id:
            socketio.emit("draw_op", data, room=whiteboard_id, skip_sid=request.sid, namespace="/whiteboard")

    @socketio.on("cursor", namespace="/whiteboard")
    @_safe
    def on_cursor(data):
        whiteboard_id = data.get("whiteboard_id") if isinstance(data, dict) else None
        if whiteboard_id:
            socketio.emit("cursor_move", data, room=whiteboard_id, skip_sid=request.sid, namespace="/whiteboard")

    @socketio.on("request_annotate", namespace="/whiteboard")
    @_safe
    def on_request_annotate(data):
        whiteboard_id = data.get("whiteboard_id") if isinstance(data, dict) else None
        if whiteboard_id:
            socketio.emit("annotate_request", {
                "student_id": data.get("student_id"),
                "student_name": data.get("student_name", ""),
                "sid": request.sid,
            }, room=whiteboard_id, namespace="/whiteboard")

    @socketio.on("approve_annotate", namespace="/whiteboard")
    @_safe
    def on_approve_annotate(data):
        whiteboard_id = data.get("whiteboard_id") if isinstance(data, dict) else None
        student_id = data.get("student_id") if isinstance(data, dict) else None
        if whiteboard_id and student_id:
            set_permission(whiteboard_id, student_id, True)
            socketio.emit("annotate_approved", {"student_id": student_id}, room=whiteboard_id, namespace="/whiteboard")

    @socketio.on("revoke_annotate", namespace="/whiteboard")
    @_safe
    def on_revoke_annotate(data):
        whiteboard_id = data.get("whiteboard_id") if isinstance(data, dict) else None
        student_id = data.get("student_id") if isinstance(data, dict) else None
        if whiteboard_id and student_id:
            set_permission(whiteboard_id, student_id, False)
            socketio.emit("annotate_revoked", {"student_id": student_id}, room=whiteboard_id, namespace="/whiteboard")

    @socketio.on("heartbeat", namespace="/whiteboard")
    @_safe
    def on_heartbeat(data):
        whiteboard_id = data.get("whiteboard_id") if isinstance(data, dict) else None
        if whiteboard_id:
            socketio.emit("heartbeat_ack", {"sid": request.sid}, room=whiteboard_id, namespace="/whiteboard")

    @socketio.on("focus_status", namespace="/whiteboard")
    @_safe
    def on_focus_status(data):
        whiteboard_id = data.get("whiteboard_id") if isinstance(data, dict) else None
        if whiteboard_id:
            socketio.emit("focus_update", {
                "sid": request.sid,
                "status": data.get("status", "focused"),
            }, room=whiteboard_id, namespace="/whiteboard")

    @socketio.on("reaction", namespace="/whiteboard")
    @_safe
    def on_reaction(data):
        whiteboard_id = data.get("whiteboard_id") if isinstance(data, dict) else None
        if whiteboard_id:
            socketio.emit("reaction_broadcast", {
                "emoji": data.get("emoji"),
                "user_name": data.get("user_name", ""),
            }, room=whiteboard_id, skip_sid=request.sid, namespace="/whiteboard")

    @socketio.on("slide_change", namespace="/whiteboard")
    @_safe
    def on_slide_change(data):
        whiteboard_id = data.get("whiteboard_id") if isinstance(data, dict) else None
        if whiteboard_id:
            socketio.emit("slide_changed", {"slide_number": data.get("slide_number", 1)}, room=whiteboard_id, namespace="/whiteboard")

    @socketio.on("timer_sync", namespace="/whiteboard")
    @_safe
    def on_timer_sync(data):
        whiteboard_id = data.get("whiteboard_id") if isinstance(data, dict) else None
        if whiteboard_id:
            socketio.emit("timer_update", {
                "seconds": data.get("seconds", 0),
                "running": data.get("running", True),
            }, room=whiteboard_id, namespace="/whiteboard")

    @socketio.on("tool_state", namespace="/whiteboard")
    @_safe
    def on_tool_state(data):
        whiteboard_id = data.get("whiteboard_id") if isinstance(data, dict) else None
        if whiteboard_id:
            socketio.emit("tool_state_update", {
                "tool": data.get("tool"),
                "state": data.get("state"),
            }, room=whiteboard_id, skip_sid=request.sid, namespace="/whiteboard")

    @socketio.on("display_settings", namespace="/whiteboard")
    @_safe
    def on_display_settings(data):
        whiteboard_id = data.get("whiteboard_id") if isinstance(data, dict) else None
        if whiteboard_id:
            socketio.emit("display_settings_update", {
                "board_mode": data.get("board_mode"),
                "grid_enabled": data.get("grid_enabled"),
                "grid_spacing": data.get("grid_spacing"),
                "grid_logarithmic": data.get("grid_logarithmic"),
            }, room=whiteboard_id, skip_sid=request.sid, namespace="/whiteboard")
