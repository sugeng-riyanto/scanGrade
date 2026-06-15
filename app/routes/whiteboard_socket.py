"""Whiteboard WebSocket events."""


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
        whiteboard_id = data.get("whiteboard_id")
        if whiteboard_id:
            socketio.join_room(whiteboard_id, namespace="/whiteboard")

    @socketio.on("leave", namespace="/whiteboard")
    def on_leave(data):
        whiteboard_id = data.get("whiteboard_id")
        if whiteboard_id:
            socketio.leave_room(whiteboard_id, namespace="/whiteboard")
