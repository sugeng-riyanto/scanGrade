/**
 * Whiteboard Canvas v5 — exact same drawing engine as exam canvas (take_exam.html)
 * No transforms, no DPR conversion, no CSS transform zoom.
 * Coordinates are always in canvas pixels, just like the exam.
 */
class WhiteboardCanvas {
    constructor(canvasId, options = {}) {
        this.canvas = document.getElementById(canvasId);
        this.ctx = this.canvas.getContext("2d");
        this.options = options;
        this.dpr = window.devicePixelRatio || 1;

        this.tool = "pen";
        this.color = "#000000";
        this._width = 0.75;
        this.fontSize = 16;
        this.dash = [];
        this.isDrawing = false;
        this.lastX = 0; this.lastY = 0;
        this.points = [];
        this.textMode = false;
        this.laserVisible = false;
        this.laserTimeout = null;
        this.undoStack = [];
        this.redoStack = [];
        this.currentBg = null;
        this.bgBounds = null;
        this.remoteOps = [];

        this.boardMode = "white";
        this.gridEnabled = false;
        this.gridSpacing = 50;

        this.compassCenter = null;
        this.compassSnapshot = null;
        this.compassRadius = 0;
        this.calcEl = null;

        this._init();
    }

    _init() {
        this._resize();
        window.addEventListener("resize", () => this._resize());
        this._bindEvents();
        this._render();
    }

    _resize() {
        const stage = document.getElementById("wb-stage");
        if (!stage) return;
        const sr = stage.getBoundingClientRect();
        if (sr.width <= 1 || sr.height <= 1) {
            setTimeout(() => this._resize(), 100);
            return;
        }
        this.canvas.width = sr.width * this.dpr;
        this.canvas.height = sr.height * this.dpr;
        this._render();
    }

    // ─── Coordinate: EXACT same as exam canvas (take_exam.html:1187-1191) ───
    _pos(e) {
        const src = e.touches ? e.touches[0] : e;
        const r = this.canvas.getBoundingClientRect();
        return {
            x: (src.clientX - r.left) * (this.canvas.width / r.width),
            y: (src.clientY - r.top) * (this.canvas.height / r.height),
        };
    }

    _bindEvents() {
        const c = this.canvas;
        c.addEventListener("mousedown", (e) => this._down(e));
        c.addEventListener("mousemove", (e) => this._move(e));
        c.addEventListener("mouseup", (e) => this._up(e));
        c.addEventListener("mouseleave", (e) => this._up(e));

        c.addEventListener("touchstart", (e) => { e.preventDefault(); this._down(e); }, { passive: false });
        c.addEventListener("touchmove", (e) => { e.preventDefault(); this._move(e); }, { passive: false });
        c.addEventListener("touchend", (e) => this._up(e), { passive: false });

        // Keyboard: support Undo (Ctrl+Z), Redo (Ctrl+Y), Clear
        document.addEventListener("keydown", (e) => {
            if ((e.ctrlKey || e.metaKey) && e.key === "z") { e.preventDefault(); this.undo(); }
            if ((e.ctrlKey || e.metaKey) && e.key === "y") { e.preventDefault(); this.redo(); }
        });
    }

    // ─── Drawing ───
    _down(e) {
        if (this.textMode || this.isDrawing) return;
        if (this.tool === "compass") { this._compDown(e); return; }
        const p = this._pos(e);
        this.isDrawing = true;
        this.lastX = p.x; this.lastY = p.y;
        this.points = [[p.x, p.y]];
        if (this.tool === "laser") { this.laserVisible = true; this._render(); return; }
        this._snap();
    }

    _move(e) {
        const p = this._pos(e);
        if (this.tool === "laser" && this.laserVisible) {
            this.lastX = p.x; this.lastY = p.y; this._render();
            clearTimeout(this.laserTimeout);
            this.laserTimeout = setTimeout(() => { this.laserVisible = false; this._render(); }, 2000);
            return;
        }
        if (this.tool === "compass" && this.isDrawing) { this._compMove(e); return; }
        if (!this.isDrawing) return;

        if (this.tool === "eraser") {
            this.ctx.globalCompositeOperation = "destination-out";
            this.ctx.beginPath();
            this.ctx.arc(p.x, p.y, (this._width + 5) * this.dpr, 0, Math.PI * 2);
            this.ctx.fill();
            this.ctx.globalCompositeOperation = "source-over";
            this.points.push([p.x, p.y]);
            this._emitDraw("erase", { points: [[p.x, p.y]], width: this._width + 5 });
            return;
        }

        if (this.tool === "highlight") this.ctx.globalAlpha = 0.3;

        this.ctx.beginPath();
        this.ctx.moveTo(this.lastX, this.lastY);
        this.ctx.lineTo(p.x, p.y);
        this.ctx.strokeStyle = this.color;
        this.ctx.lineWidth = Math.max(0.5, this._width * this.dpr);
        this.ctx.lineCap = "round";
        this.ctx.lineJoin = "round";
        this.ctx.stroke();
        this.ctx.globalAlpha = 1;

        this.points.push([p.x, p.y]);
        this.lastX = p.x; this.lastY = p.y;
        this._emitDraw("line", { points: this.points, color: this.color, width: this._width, dash: this.dash });
    }

    _up(e) {
        if (this.tool === "compass") { this._compUp(e); return; }
        if (!this.isDrawing || this.tool === "laser") {
            this.isDrawing = false;
            if (this.tool === "laser") { this.laserVisible = false; this._render(); }
            return;
        }
        this.isDrawing = false;
        if (this.points.length > 0) {
            this._emitDraw(this.tool === "eraser" ? "erase" : "line", {
                points: this.points, color: this.color, width: this._width, dash: this.dash,
            });
        }
    }

    // ─── Background ───
    get _w() { return this.canvas.width; }
    get _h() { return this.canvas.height; }

    _drawBackground() {
        this.ctx.fillStyle = this.boardMode === "white" ? "#FFFFFF" : "#1e293b";
        this.ctx.fillRect(0, 0, this._w, this._h);
        if (this.currentBg && this.bgBounds) {
            this.ctx.drawImage(this.currentBg, this.bgBounds.x, this.bgBounds.y, this.bgBounds.w, this.bgBounds.h);
        }
        if (!this.gridEnabled) return;
        this.ctx.strokeStyle = this.boardMode === "white" ? "rgba(0,0,0,0.07)" : "rgba(255,255,255,0.07)";
        this.ctx.lineWidth = 0.5;
        const sp = this.gridSpacing * this.dpr;
        for (let x = 0; x <= this._w; x += sp) { this.ctx.beginPath(); this.ctx.moveTo(x, 0); this.ctx.lineTo(x, this._h); this.ctx.stroke(); }
        for (let y = 0; y <= this._h; y += sp) { this.ctx.beginPath(); this.ctx.moveTo(0, y); this.ctx.lineTo(this._w, y); this.ctx.stroke(); }
    }

    setBackground(url) {
        if (!url) { this.currentBg = null; this.bgBounds = null; this._render(); return; }
        const img = new Image();
        img.crossOrigin = "anonymous";
        img.onload = () => {
            this.currentBg = img;
            const cw = this._w, ch = this._h;
            const iw = img.naturalWidth || img.width, ih = img.naturalHeight || img.height;
            const sc = Math.min(cw / iw, ch / ih);
            this.bgBounds = { x: (cw - iw * sc) / 2, y: (ch - ih * sc) / 2, w: iw * sc, h: ih * sc };
            this._render();
        };
        img.src = url;
    }

    // ─── Compass (circle drawing) ───
    _compDown(e) {
        const p = this._pos(e);
        this.isDrawing = true;
        this.compassCenter = { x: p.x, y: p.y };
        this.compassSnapshot = new Image();
        this.compassSnapshot.src = this.canvas.toDataURL();
        this.compassRadius = 0;
    }
    _compMove(e) {
        if (!this.isDrawing) return;
        const p = this._pos(e);
        const cx = this.compassCenter.x, cy = this.compassCenter.y;
        this.compassRadius = Math.hypot(p.x - cx, p.y - cy);
        this.ctx.clearRect(0, 0, this._w, this._h);
        this._drawBackground();
        if (this.compassSnapshot.complete) this.ctx.drawImage(this.compassSnapshot, 0, 0);
        this.ctx.strokeStyle = this.color; this.ctx.lineWidth = Math.max(0.5, this._width * this.dpr);
        this.ctx.beginPath(); this.ctx.arc(cx, cy, this.compassRadius, 0, Math.PI * 2); this.ctx.stroke();
        this.ctx.strokeStyle = this.color; this.ctx.lineWidth = 1;
        this.ctx.beginPath(); this.ctx.moveTo(cx - 6 * this.dpr, cy); this.ctx.lineTo(cx + 6 * this.dpr, cy); this.ctx.stroke();
        this.ctx.beginPath(); this.ctx.moveTo(cx, cy - 6 * this.dpr); this.ctx.lineTo(cx, cy + 6 * this.dpr); this.ctx.stroke();
        this.ctx.setLineDash([4 * this.dpr, 4 * this.dpr]);
        this.ctx.beginPath(); this.ctx.moveTo(cx, cy); this.ctx.lineTo(p.x, p.y); this.ctx.stroke(); this.ctx.setLineDash([]);
        this.ctx.fillStyle = this.color; this.ctx.font = `${12 * this.dpr}px Inter, sans-serif`;
        this.ctx.fillText(`r=${Math.round(this.compassRadius / this.dpr)}px`, cx + (p.x - cx) / 2 + 5 * this.dpr, cy + (p.y - cy) / 2 - 5 * this.dpr);
    }
    _compUp(e) {
        if (!this.isDrawing) return;
        this.isDrawing = false;
        if (this.compassRadius > 5 * this.dpr) {
            this._emitDraw("circle", { cx: this.compassCenter.x, cy: this.compassCenter.y, r: this.compassRadius, color: this.color, width: this._width });
        }
    }

    // ─── Calculator ───
    toggleCalculator() {
        if (this.calcEl && this.calcEl.style.display !== "none") { this.calcEl.style.display = "none"; return; }
        if (!this.calcEl) {
            if (typeof ScanGradeTools?.createCalculator === "function") {
                this.calcEl = ScanGradeTools.createCalculator();
                document.body.appendChild(this.calcEl);
            } else { alert("Kalkulator tidak tersedia"); return; }
        }
        this.calcEl.style.display = "block";
    }

    // ─── Undo / Redo / Clear ───
    _snap() { this.undoStack.push(this.canvas.toDataURL()); if (this.undoStack.length > 50) this.undoStack.shift(); this.redoStack = []; }

    undo() {
        if (this.undoStack.length === 0) return;
        this.redoStack.push(this.canvas.toDataURL());
        const img = new Image();
        img.onload = () => { this.ctx.clearRect(0, 0, this._w, this._h); this._drawBackground(); this.ctx.drawImage(img, 0, 0); };
        img.src = this.undoStack.pop();
    }
    redo() {
        if (this.redoStack.length === 0) return;
        this.undoStack.push(this.canvas.toDataURL());
        const img = new Image();
        img.onload = () => { this.ctx.clearRect(0, 0, this._w, this._h); this._drawBackground(); this.ctx.drawImage(img, 0, 0); };
        img.src = this.redoStack.pop();
    }
    clearCanvas() {
        this._snap();
        this.ctx.clearRect(0, 0, this._w, this._h);
        this._drawBackground();
        if (this.currentBg && this.bgBounds) this.ctx.drawImage(this.currentBg, this.bgBounds.x, this.bgBounds.y, this.bgBounds.w, this.bgBounds.h);
    }

    setTool(t) { this.tool = t; this.textMode = false; }
    setColor(c) { this.color = c; }
    setWidth(w) { this._width = Math.max(0.1, parseFloat(w) || 0.75); }
    setFontSize(s) { this.fontSize = s; }
    setDash(d) { this.dash = d; }
    clearLaser() { this.laserVisible = false; this._render(); }

    enableTextMode() {
        this.textMode = true; this.tool = "text";
        this.canvas.addEventListener("click", (e) => this._placeText(e), { once: true });
    }
    _placeText(e) {
        if (!this.textMode) return;
        const p = this._pos(e);
        const input = document.createElement("input");
        input.type = "text";
        input.style.position = "fixed";
        input.style.left = e.clientX + "px";
        input.style.top = e.clientY + "px";
        input.style.fontSize = this.fontSize + "px";
        input.style.fontFamily = "Inter, sans-serif";
        input.style.border = "2px solid " + this.color;
        input.style.padding = "4px 8px";
        input.style.borderRadius = "6px";
        input.style.background = "#fff";
        input.style.zIndex = "99999";
        document.body.appendChild(input); input.focus();
        const done = () => {
            const text = input.value.trim();
            if (text) {
                this._snap();
                this.ctx.font = `${this.fontSize * this.dpr}px Inter, sans-serif`;
                this.ctx.fillStyle = this.color;
                this.ctx.fillText(text, p.x, p.y);
                this._emitDraw("text", { text, x: p.x / this.dpr, y: p.y / this.dpr, color: this.color, fontSize: this.fontSize });
            }
            document.body.removeChild(input);
            this.textMode = false;
        };
        input.addEventListener("blur", done);
        input.addEventListener("keydown", (ev) => { if (ev.key === "Enter") { ev.preventDefault(); done(); } });
    }

    // ─── Render ───
    _render() {
        this.ctx.setTransform(1, 0, 0, 1, 0, 0);
        this.ctx.clearRect(0, 0, this._w, this._h);
        this._drawBackground();
        for (const op of this.remoteOps) this._replay(op);
    }

    // ─── Replay (receive remote ops in CSS coords, convert to canvas pixels) ───
    _replay(op) {
        const d = op.data || {};
        const ctx = this.ctx;
        if (op.op_type === "line") {
            const pts = d.points || [];
            if (pts.length < 2) return;
            ctx.beginPath(); ctx.moveTo(pts[0][0] * this.dpr, pts[0][1] * this.dpr);
            for (let i = 1; i < pts.length; i++) ctx.lineTo(pts[i][0] * this.dpr, pts[i][1] * this.dpr);
            ctx.strokeStyle = d.color || "#000";
            ctx.lineWidth = Math.max(0.5, (d.width || 3) * this.dpr);
            ctx.lineCap = "round"; ctx.lineJoin = "round"; ctx.stroke();
        } else if (op.op_type === "text") {
            ctx.font = `${(d.fontSize || 16) * this.dpr}px Inter, sans-serif`;
            ctx.fillStyle = d.color || "#000";
            ctx.fillText(d.text || "", (d.x || 0) * this.dpr, (d.y || 0) * this.dpr);
        } else if (op.op_type === "erase") {
            ctx.globalCompositeOperation = "destination-out";
            ctx.beginPath(); ctx.arc((d.x || 0) * this.dpr, (d.y || 0) * this.dpr, ((d.width || 8) + 5) * this.dpr, 0, Math.PI * 2); ctx.fill();
            ctx.globalCompositeOperation = "source-over";
        } else if (op.op_type === "circle") {
            ctx.strokeStyle = d.color || "#000"; ctx.lineWidth = (d.width || 3) * this.dpr;
            ctx.beginPath(); ctx.arc((d.cx || 0) * this.dpr, (d.cy || 0) * this.dpr, (d.r || 0) * this.dpr, 0, Math.PI * 2); ctx.stroke();
        }
    }

    loadOps(ops) {
        this._snap();
        this.ctx.clearRect(0, 0, this._w, this._h);
        this._drawBackground();
        if (this.currentBg && this.bgBounds) this.ctx.drawImage(this.currentBg, this.bgBounds.x, this.bgBounds.y, this.bgBounds.w, this.bgBounds.h);
        for (const op of ops) this._replay(op);
    }

    setBoardMode(m) { this.boardMode = m; this._render(); }
    toggleGrid() { this.gridEnabled = !this.gridEnabled; this._render(); }
    setGridSpacing(v) { this.gridSpacing = Math.max(10, Math.min(500, v)); this._render(); }

    _emitDraw(op_type, data) { if (this.options.onDraw) this.options.onDraw({ op_type, data, timestamp: Date.now() }); }
    toDataURL() { return this.canvas.toDataURL("image/png"); }
}
