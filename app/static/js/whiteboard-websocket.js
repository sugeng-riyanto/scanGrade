/* Whiteboard WebSocket Client */
class WhiteboardSocket {
    constructor(whiteboardId, options = {}) {
        this.whiteboardId = whiteboardId;
        this.options = options;
        this.socket = null;
        this.connected = false;
        this.reconnectAttempts = 0;
        this.maxReconnect = 10;
        this.reconnectDelay = 1000;
    }

    connect() {
        if (this.socket && this.socket.connected) return;
        this.socket = io("/whiteboard", {
            transports: ["websocket", "polling"],
        });

        this.socket.on("connect", () => {
            this.connected = true;
            this.reconnectAttempts = 0;
            this.socket.emit("join", { whiteboard_id: this.whiteboardId });
            if (this.options.onConnect) this.options.onConnect();
        });

        this.socket.on("disconnect", () => {
            this.connected = false;
            if (this.options.onDisconnect) this.options.onDisconnect();
            this._reconnect();
        });

        this.socket.on("draw_op", (data) => {
            if (this.options.onDrawOp) this.options.onDrawOp(data);
        });

        this.socket.on("cursor_move", (data) => {
            if (this.options.onCursorMove) this.options.onCursorMove(data);
        });

        this.socket.on("annotate_request", (data) => {
            if (this.options.onAnnotateRequest) this.options.onAnnotateRequest(data);
        });

        this.socket.on("annotate_approved", (data) => {
            if (this.options.onAnnotateApproved) this.options.onAnnotateApproved(data);
        });

        this.socket.on("annotate_revoked", (data) => {
            if (this.options.onAnnotateRevoked) this.options.onAnnotateRevoked(data);
        });

        this.socket.on("slide_changed", (data) => {
            if (this.options.onSlideChanged) this.options.onSlideChanged(data);
        });

        this.socket.on("timer_update", (data) => {
            if (this.options.onTimerUpdate) this.options.onTimerUpdate(data);
        });

        this.socket.on("tool_state_update", (data) => {
            if (this.options.onToolStateUpdate) this.options.onToolStateUpdate(data);
        });

        this.socket.on("display_settings_update", (data) => {
            if (this.options.onDisplaySettingsUpdate) this.options.onDisplaySettingsUpdate(data);
        });

        this.socket.on("reaction_broadcast", (data) => {
            if (this.options.onReaction) this.options.onReaction(data);
        });

        this.socket.on("user_joined", (data) => {
            if (this.options.onUserJoined) this.options.onUserJoined(data);
        });

        this.socket.on("user_left", (data) => {
            if (this.options.onUserLeft) this.options.onUserLeft(data);
        });
    }

    _reconnect() {
        if (this.reconnectAttempts >= this.maxReconnect) return;
        this.reconnectAttempts++;
        setTimeout(() => {
            if (!this.connected) this.connect();
        }, this.reconnectDelay * this.reconnectAttempts);
    }

    disconnect() {
        if (this.socket) {
            this.socket.emit("leave", { whiteboard_id: this.whiteboardId });
            this.socket.disconnect();
        }
    }

    sendDraw(op_type, data) {
        if (!this.socket || !this.connected) return;
        this.socket.emit("draw", {
            whiteboard_id: this.whiteboardId,
            op_type, data, timestamp: Date.now(),
        });
    }

    sendCursor(x, y) {
        if (!this.socket || !this.connected) return;
        this.socket.emit("cursor", { whiteboard_id: this.whiteboardId, x, y });
    }

    sendHeartbeat() {
        if (!this.socket || !this.connected) return;
        this.socket.emit("heartbeat", { whiteboard_id: this.whiteboardId });
    }

    sendFocusStatus(status) {
        if (!this.socket || !this.connected) return;
        this.socket.emit("focus_status", { whiteboard_id: this.whiteboardId, status });
    }

    sendReaction(emoji, userName) {
        if (!this.socket || !this.connected) return;
        this.socket.emit("reaction", { whiteboard_id: this.whiteboardId, emoji, user_name: userName });
    }

    requestAnnotate(studentId, studentName) {
        if (!this.socket || !this.connected) return;
        this.socket.emit("request_annotate", { whiteboard_id: this.whiteboardId, student_id: studentId, student_name: studentName });
    }

    approveAnnotate(studentId) {
        if (!this.socket || !this.connected) return;
        this.socket.emit("approve_annotate", { whiteboard_id: this.whiteboardId, student_id: studentId });
    }

    revokeAnnotate(studentId) {
        if (!this.socket || !this.connected) return;
        this.socket.emit("revoke_annotate", { whiteboard_id: this.whiteboardId, student_id: studentId });
    }

    sendSlideChange(slideNumber) {
        if (!this.socket || !this.connected) return;
        this.socket.emit("slide_change", { whiteboard_id: this.whiteboardId, slide_number: slideNumber });
    }

    sendTimerSync(seconds, running) {
        if (!this.socket || !this.connected) return;
        this.socket.emit("timer_sync", { whiteboard_id: this.whiteboardId, seconds, running });
    }

    sendToolState(type, state) {
        if (!this.socket || !this.connected) return;
        this.socket.emit("tool_state", { whiteboard_id: this.whiteboardId, tool: type, state });
    }

    sendDisplaySettings(settings) {
        if (!this.socket || !this.connected) return;
        this.socket.emit("display_settings", { whiteboard_id: this.whiteboardId, ...settings });
    }
}

/* Heartbeat: every 5 seconds */

/* Heartbeat: every 5 seconds */
function startHeartbeat(ws) {
    setInterval(() => ws.sendHeartbeat(), 5000);
}
