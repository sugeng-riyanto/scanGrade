/* Whiteboard Canvas Engine — drawing, tools, undo/redo, laser, background */
class WhiteboardCanvas {
    constructor(canvasId, options = {}) {
        this.canvas = document.getElementById(canvasId);
        this.ctx = this.canvas.getContext("2d");
        this.options = options;
        this.tool = "pen";       // pen | eraser | text | highlight | laser
        this.color = "#000000";
        this.width = 3;
        this.opacity = 1;
        this.fontSize = 16;
        this.fontFamily = "Inter, sans-serif";
        this.dash = [];          // line dash style
        this.isDrawing = false;
        this.lastX = 0;
        this.lastY = 0;
        this.points = [];
        this.textMode = false;
        this.textInput = null;
        this.laserVisible = false;
        this.laserTimeout = null;
        this.undoStack = [];
        this.redoStack = [];
        this.currentBg = null;   // Image object for background
        this.remoteOps = [];     // ops from other users (for replay)
        this.remoteCursors = {}; // other users' cursor positions

        this._init();
    }

    _init() {
        this._resize();
        window.addEventListener("resize", () => this._resize());
        this._bindEvents();
        this.clearCanvas();
    }

    _resize() {
        const rect = this.canvas.parentElement.getBoundingClientRect();
        this.canvas.width = rect.width;
        this.canvas.height = rect.height;
        this._render();
    }

    _bindEvents() {
        this.canvas.addEventListener("mousedown", (e) => this._pointerDown(e));
        this.canvas.addEventListener("mousemove", (e) => this._pointerMove(e));
        this.canvas.addEventListener("mouseup", (e) => this._pointerUp(e));
        this.canvas.addEventListener("mouseleave", (e) => this._pointerUp(e));
        this.canvas.addEventListener("touchstart", (e) => this._pointerDown(e.touches[0]), { passive: false });
        this.canvas.addEventListener("touchmove", (e) => { e.preventDefault(); this._pointerMove(e.touches[0]); }, { passive: false });
        this.canvas.addEventListener("touchend", (e) => this._pointerUp(e), { passive: false });
    }

    _getPos(e) {
        const rect = this.canvas.getBoundingClientRect();
        return { x: e.clientX - rect.left, y: e.clientY - rect.top };
    }

    _pointerDown(e) {
        if (this.textMode) return;
        const pos = this._getPos(e);
        this.isDrawing = true;
        this.lastX = pos.x;
        this.lastY = pos.y;
        this.points = [[pos.x, pos.y]];

        if (this.tool === "laser") {
            this.laserVisible = true;
            this._render();
            return;
        }

        this._saveState();
    }

    _pointerMove(e) {
        const pos = this._getPos(e);

        if (this.tool === "laser" && this.laserVisible) {
            this.lastX = pos.x;
            this.lastY = pos.y;
            this._render();
            if (this.laserTimeout) clearTimeout(this.laserTimeout);
            this.laserTimeout = setTimeout(() => { this.laserVisible = false; this._render(); }, 2000);
            this._emitCursor(pos);
            return;
        }

        if (!this.isDrawing) {
            this._emitCursor(pos);
            return;
        }

        if (this.tool === "eraser") {
            this.ctx.globalCompositeOperation = "destination-out";
            this.ctx.beginPath();
            this.ctx.arc(pos.x, pos.y, this.width + 5, 0, Math.PI * 2);
            this.ctx.fill();
            this.ctx.globalCompositeOperation = "source-over";
            this.points.push([pos.x, pos.y]);
            this._emitDraw("erase", { points: [[pos.x, pos.y]], width: this.width + 5 });
            return;
        }

        if (this.tool === "highlight") {
            this.ctx.globalAlpha = 0.3;
        }

        this.ctx.beginPath();
        this.ctx.moveTo(this.lastX, this.lastY);
        this.ctx.lineTo(pos.x, pos.y);
        this.ctx.strokeStyle = this.color;
        this.ctx.lineWidth = this.width;
        this.ctx.setLineDash(this.dash);
        this.ctx.lineCap = "round";
        this.ctx.lineJoin = "round";
        this.ctx.stroke();
        this.ctx.setLineDash([]);
        this.ctx.globalAlpha = 1;

        this.points.push([pos.x, pos.y]);
        this.lastX = pos.x;
        this.lastY = pos.y;
        this._emitDraw("line", { points: this.points, color: this.color, width: this.width, dash: this.dash });
    }

    _pointerUp(e) {
        if (!this.isDrawing || this.tool === "laser") {
            this.isDrawing = false;
            if (this.tool === "laser") { this.laserVisible = false; this._render(); }
            return;
        }
        this.isDrawing = false;
        if (this.points.length > 0) {
            this._emitDraw(this.tool === "eraser" ? "erase" : "line", {
                points: this.points, color: this.color, width: this.width, dash: this.dash
            });
        }
    }

    _saveState() {
        this.undoStack.push(this.canvas.toDataURL());
        if (this.undoStack.length > 50) this.undoStack.shift();
        this.redoStack = [];
    }

    undo() {
        if (this.undoStack.length === 0) return;
        this.redoStack.push(this.canvas.toDataURL());
        const prev = this.undoStack.pop();
        const img = new Image();
        img.onload = () => { this.ctx.clearRect(0, 0, this.canvas.width, this.canvas.height); this.ctx.drawImage(img, 0, 0); };
        img.src = prev;
    }

    redo() {
        if (this.redoStack.length === 0) return;
        this.undoStack.push(this.canvas.toDataURL());
        const next = this.redoStack.pop();
        const img = new Image();
        img.onload = () => { this.ctx.clearRect(0, 0, this.canvas.width, this.canvas.height); this.ctx.drawImage(img, 0, 0); };
        img.src = next;
    }

    clearCanvas() {
        this._saveState();
        this.ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);
        if (this.currentBg) {
            this.ctx.drawImage(this.currentBg, 0, 0, this.canvas.width, this.canvas.height);
        }
    }

    setBackground(url) {
        if (!url) { this.currentBg = null; this._render(); return; }
        const img = new Image();
        img.crossOrigin = "anonymous";
        img.onload = () => {
            this.currentBg = img;
            this._render();
        };
        img.src = url;
    }

    setTool(tool) { this.tool = tool; this.textMode = false; this.canvas.style.cursor = tool === "text" ? "text" : "crosshair"; }
    setColor(color) { this.color = color; }
    setWidth(width) { this.width = width; }
    setOpacity(opacity) { this.opacity = opacity; }
    setFontSize(size) { this.fontSize = size; }
    setDash(dash) { this.dash = dash; }

    clearLaser() { this.laserVisible = false; this._render(); }

    enableTextMode() {
        this.textMode = true;
        this.tool = "text";
        this.canvas.style.cursor = "text";
        this.canvas.addEventListener("click", (e) => this._placeText(e), { once: true });
    }

    _placeText(e) {
        if (!this.textMode) return;
        const pos = this._getPos(e);
        const input = document.createElement("input");
        input.type = "text";
        input.style.position = "fixed";
        input.style.left = (e.clientX) + "px";
        input.style.top = (e.clientY) + "px";
        input.style.fontSize = this.fontSize + "px";
        input.style.fontFamily = this.fontFamily;
        input.style.border = "2px solid " + this.color;
        input.style.padding = "4px 8px";
        input.style.borderRadius = "6px";
        input.style.background = "#fff";
        input.style.zIndex = "9999";
        document.body.appendChild(input);
        input.focus();

        const submitText = () => {
            const text = input.value.trim();
            if (text) {
                this._saveState();
                this.ctx.font = `${this.fontSize}px ${this.fontFamily}`;
                this.ctx.fillStyle = this.color;
                this.ctx.fillText(text, pos.x, pos.y);
                this._emitDraw("text", { text, x: pos.x, y: pos.y, color: this.color, fontSize: this.fontSize, fontFamily: this.fontFamily });
            }
            document.body.removeChild(input);
            this.textMode = false;
        };

        input.addEventListener("blur", submitText);
        input.addEventListener("keydown", (ev) => { if (ev.key === "Enter") { ev.preventDefault(); submitText(); } });
    }

    /* Replay a remote drawing op on the local canvas */
    replayOp(op) {
        const d = op.data || {};
        if (op.op_type === "line") {
            const pts = d.points || [];
            if (pts.length < 2) return;
            this.ctx.beginPath();
            this.ctx.moveTo(pts[0][0], pts[0][1]);
            for (let i = 1; i < pts.length; i++) {
                this.ctx.lineTo(pts[i][0], pts[i][1]);
            }
            this.ctx.strokeStyle = d.color || "#000";
            this.ctx.lineWidth = d.width || 3;
            this.ctx.setLineDash(d.dash || []);
            this.ctx.lineCap = "round";
            this.ctx.lineJoin = "round";
            this.ctx.stroke();
            this.ctx.setLineDash([]);
        } else if (op.op_type === "text") {
            this.ctx.font = `${d.fontSize || 16}px ${d.fontFamily || "Inter, sans-serif"}`;
            this.ctx.fillStyle = d.color || "#000";
            this.ctx.fillText(d.text || "", d.x || 0, d.y || 0);
        } else if (op.op_type === "erase") {
            this.ctx.globalCompositeOperation = "destination-out";
            this.ctx.beginPath();
            this.ctx.arc(d.x || 0, d.y || 0, (d.width || 8) + 5, 0, Math.PI * 2);
            this.ctx.fill();
            this.ctx.globalCompositeOperation = "source-over";
        } else if (op.op_type === "clear") {
            this._saveState();
            this.ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);
            if (this.currentBg) {
                this.ctx.drawImage(this.currentBg, 0, 0, this.canvas.width, this.canvas.height);
            }
        }
    }

    /* Load all saved ops for a slide */
    loadOps(ops) {
        this._saveState();
        this.ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);
        if (this.currentBg) {
            this.ctx.drawImage(this.currentBg, 0, 0, this.canvas.width, this.canvas.height);
        }
        for (const op of ops) {
            this.replayOp(op);
        }
    }

    /* Render background + all remote ops */
    _render() {
        this.ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);
        if (this.currentBg) {
            this.ctx.drawImage(this.currentBg, 0, 0, this.canvas.width, this.canvas.height);
        }
        for (const op of this.remoteOps) {
            this.replayOp(op);
        }
        if (this.laserVisible) {
            this.ctx.beginPath();
            this.ctx.arc(this.lastX, this.lastY, 10, 0, Math.PI * 2);
            this.ctx.fillStyle = "rgba(255, 0, 0, 0.6)";
            this.ctx.fill();
            this.ctx.beginPath();
            this.ctx.arc(this.lastX, this.lastY, 4, 0, Math.PI * 2);
            this.ctx.fillStyle = "rgba(255, 0, 0, 0.9)";
            this.ctx.fill();
        }
    }

    /* Factory methods for upstream callbacks */
    _emitDraw(op_type, data) {
        if (this.options.onDraw) {
            this.options.onDraw({ op_type, data, timestamp: Date.now() });
        }
    }

    _emitCursor(pos) {
        if (this.options.onCursor) {
            this.options.onCursor({ x: pos.x, y: pos.y });
        }
    }

    /* Export canvas as data URL */
    toDataURL() {
        return this.canvas.toDataURL("image/png");
    }
}
