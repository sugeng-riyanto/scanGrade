"""Whiteboard initialization — called lazily on first whiteboard request.
This keeps heavy imports (fitz, PIL, flask_socketio) out of the main app startup."""

_init_done = False


def init_whiteboard(app):
    """Register whiteboard blueprints and SocketIO. Safe to call multiple times."""
    global _init_done
    if _init_done:
        return
    _init_done = True

    try:
        from app.routes.whiteboard_teacher import whiteboard_teacher_bp
        from app.routes.whiteboard_student import whiteboard_student_bp
        app.register_blueprint(whiteboard_teacher_bp, url_prefix="/wb/teacher")
        app.register_blueprint(whiteboard_student_bp, url_prefix="/wb/student")

        from flask_socketio import SocketIO
        sio = SocketIO(async_mode='threading')
        sio.init_app(app, cors_allowed_origins="*")
        from app.routes.whiteboard_socket import register_socket_events
        register_socket_events(sio)
        app.extensions["socketio"] = sio
    except Exception as e:
        app.logger.error("Whiteboard init error: %s", str(e)[:120])
        app.extensions["socketio"] = None
