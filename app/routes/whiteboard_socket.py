"""Whiteboard WebSocket events — real-time collaboration."""
import json
from flask import g, request
from app.services.whiteboard_service import get_ops, set_permission, can_annotate


def register_socket_events(socketio):
    """Register all whiteboard SocketIO event handlers."""

    @socketio.on("connect", namespace="/whiteboard")
    def on_connect():
        pass

    @socketio.on("disconnect", namespace="/whiteboard")
    def on_disconnect():
        pass

    @socketio.on("join", namespace="/whiteboard")
    def on_join(data):
        """Join a whiteboard room."""
        whiteboard_id = data.get("whiteboard_id") if isinstance(data, dict) else None
        if whiteboard_id:
            socketio.enter_room(request.sid, whiteboard_id, namespace="/whiteboard")
            socketio.emit("user_joined", {"sid": request.sid}, room=whiteboard_id, namespace="/whiteboard")

    @socketio.on("leave", namespace="/whiteboard")
    def on_leave(data):
        whiteboard_id = data.get("whiteboard_id") if isinstance(data, dict) else None
        if whiteboard_id:
            socketio.leave_room(request.sid, whiteboard_id, namespace="/whiteboard")
            socketio.emit("user_left", {"sid": request.sid}, room=whiteboard_id, namespace="/whiteboard")

    @socketio.on("draw", namespace="/whiteboard")
    def on_draw(data):
        """Broadcast drawing operation to all in the room (except sender)."""
        if not isinstance(data, dict):
            return
        whiteboard_id = data.get("whiteboard_id")
        if whiteboard_id:
            socketio.emit("draw_op", data, room=whiteboard_id, skip_sid=request.sid, namespace="/whiteboard")

    @socketio.on("cursor", namespace="/whiteboard")
    def on_cursor(data):
        """Broadcast cursor/laser pointer position."""
        if not isinstance(data, dict):
            return
        whiteboard_id = data.get("whiteboard_id")
        if whiteboard_id:
            socketio.emit("cursor_move", data, room=whiteboard_id, skip_sid=request.sid, namespace="/whiteboard")

    @socketio.on("request_annotate", namespace="/whiteboard")
    def on_request_annotate(data):
        """Student requests annotation permission → notify teacher."""
        if not isinstance(data, dict):
            return
        whiteboard_id = data.get("whiteboard_id")
        student_name = data.get("student_name", "")
        if whiteboard_id:
            socketio.emit("annotate_request", {
                "student_id": data.get("student_id"),
                "student_name": student_name,
                "sid": request.sid,
            }, room=whiteboard_id, namespace="/whiteboard")

    @socketio.on("approve_annotate", namespace="/whiteboard")
    def on_approve_annotate(data):
        """Teacher approves student annotation permission."""
        if not isinstance(data, dict):
            return
        whiteboard_id = data.get("whiteboard_id")
        student_id = data.get("student_id")
        if whiteboard_id and student_id:
            set_permission(whiteboard_id, student_id, True)
            socketio.emit("annotate_approved", {"student_id": student_id}, room=whiteboard_id, namespace="/whiteboard")

    @socketio.on("revoke_annotate", namespace="/whiteboard")
    def on_revoke_annotate(data):
        """Teacher revokes student annotation permission."""
        if not isinstance(data, dict):
            return
        whiteboard_id = data.get("whiteboard_id")
        student_id = data.get("student_id")
        if whiteboard_id and student_id:
            set_permission(whiteboard_id, student_id, False)
            socketio.emit("annotate_revoked", {"student_id": student_id}, room=whiteboard_id, namespace="/whiteboard")

    @socketio.on("heartbeat", namespace="/whiteboard")
    def on_heartbeat(data):
        """Student heartbeat — track online status."""
        if not isinstance(data, dict):
            return
        whiteboard_id = data.get("whiteboard_id")
        if whiteboard_id:
            socketio.emit("heartbeat_ack", {"sid": request.sid}, room=whiteboard_id, namespace="/whiteboard")

    @socketio.on("focus_status", namespace="/whiteboard")
    def on_focus_status(data):
        """Broadcast focus status (focused/blurred/hidden)."""
        if not isinstance(data, dict):
            return
        whiteboard_id = data.get("whiteboard_id")
        if whiteboard_id:
            socketio.emit("focus_update", {
                "sid": request.sid,
                "status": data.get("status", "focused"),
            }, room=whiteboard_id, namespace="/whiteboard")

    @socketio.on("reaction", namespace="/whiteboard")
    def on_reaction(data):
        """Broadcast quick reaction emoji."""
        if not isinstance(data, dict):
            return
        whiteboard_id = data.get("whiteboard_id")
        if whiteboard_id:
            socketio.emit("reaction_broadcast", {
                "emoji": data.get("emoji"),
                "user_name": data.get("user_name", ""),
            }, room=whiteboard_id, skip_sid=request.sid, namespace="/whiteboard")

    @socketio.on("slide_change", namespace="/whiteboard")
    def on_slide_change(data):
        """Teacher changes slide → broadcast to all."""
        if not isinstance(data, dict):
            return
        whiteboard_id = data.get("whiteboard_id")
        if whiteboard_id:
            socketio.emit("slide_changed", {
                "slide_number": data.get("slide_number", 1),
            }, room=whiteboard_id, namespace="/whiteboard")

    @socketio.on("timer_sync", namespace="/whiteboard")
    def on_timer_sync(data):
        """Teacher sets/updates timer."""
        if not isinstance(data, dict):
            return
        whiteboard_id = data.get("whiteboard_id")
        if whiteboard_id:
            socketio.emit("timer_update", {
                "seconds": data.get("seconds", 0),
                "running": data.get("running", True),
            }, room=whiteboard_id, namespace="/whiteboard")

    @socketio.on("tool_state", namespace="/whiteboard")
    def on_tool_state(data):
        """Broadcast tool state (position, angle) to all."""
        if not isinstance(data, dict):
            return
        whiteboard_id = data.get("whiteboard_id")
        if whiteboard_id:
            socketio.emit("tool_state_update", {
                "tool": data.get("tool"),
                "state": data.get("state"),
            }, room=whiteboard_id, skip_sid=request.sid, namespace="/whiteboard")

    @socketio.on("display_settings", namespace="/whiteboard")
    def on_display_settings(data):
        """Broadcast display settings (board mode, grid) to all."""
        if not isinstance(data, dict):
            return
        whiteboard_id = data.get("whiteboard_id")
        if whiteboard_id:
            socketio.emit("display_settings_update", {
                "board_mode": data.get("board_mode"),
                "grid_enabled": data.get("grid_enabled"),
                "grid_spacing": data.get("grid_spacing"),
                "grid_logarithmic": data.get("grid_logarithmic"),
            }, room=whiteboard_id, skip_sid=request.sid, namespace="/whiteboard")
