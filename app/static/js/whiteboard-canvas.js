/**
 * Whiteboard Canvas v4 — direct pixel drawing (like exam canvas)
 * No context transform. Drawing in CSS pixel coords, multiplied by dpr.
 * Zoom/pan handled via CSS transform on wrapper element.
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
        this.lastX = 0;
        this.lastY = 0;
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
        this.gridLogarithmic = false;

        // Zoom/Pan: applied via CSS transform on parentElement
        this._zoom = 1;
        this._panX = 0;
        this._panY = 0;
        this._isPanning = false;
        this._panStart = { x: 0, y: 0 };

        this.compassCenter = null;
        this.compassSnapshot = null;
        this.compassRadius = 0;
        this.calcEl = null;
        this._cursorX = 0;
        this._cursorY = 0;

        this._init();
    }

    get width() { return this._width; }
    set width(v) { this._width = parseFloat(v) || 0.75; }
    get zoom() { return this._zoom; }
    set zoom(v) { this._zoom = Math.max(0.1, Math.min(10, v)); this._applyCssTransform(); }
    get panX() { return this._panX; }
    set panX(v) { this._panX = v; this._applyCssTransform(); }
    get panY() { return this._panY; }
    set panY(v) { this._panY = v; this._applyCssTransform(); }

    _init() {
        this._resize();
        window.addEventListener("resize", () => this._resize());
        this._bindEvents();
        this._render();
    }

    _resize() {
        const rect = this.canvas.parentElement.getBoundingClientRect();
        this.canvas.width = rect.width * this.dpr;
        this.canvas.height = rect.height * this.dpr;
        this.canvas.style.width = rect.width + "px";
        this.canvas.style.height = rect.height + "px";
        this._applyCssTransform();
        this._render();
    }

    _applyCssTransform() {
        const el = this.canvas.parentElement;
        const z = this._zoom, px = this._panX, py = this._panY;
        if (z === 1 && px === 0 && py === 0) {
            el.style.transform = "";
            el.style.transformOrigin = "";
        } else {
            el.style.transform = `scale(${z}) translate(${px}px, ${py}px)`;
            el.style.transformOrigin = "0 0";
        }
    }

    _pos(e) {
        // Returns CSS-pixel position relative to the canvas element
        if (e.offsetX !== undefined) {
            return { x: e.offsetX, y: e.offsetY };
        }
        const r = this.canvas.getBoundingClientRect();
        return { x: e.clientX - r.left, y: e.clientY - r.top };
    }

    _bindEvents() {
        const c = this.canvas;
        c.addEventListener("mousedown", (e) => {
            if (e.button === 1 || (e.button === 0 && e.shiftKey)) { this._startPan(e); return; }
            this._down(e);
        });
        c.addEventListener("mousemove", (e) => {
            if (this._isPanning) { this._doPan(e); return; }
            this._cursorX = e.clientX; this._cursorY = e.clientY;
            this._move(e);
        });
        c.addEventListener("mouseup", (e) => { if (this._isPanning) { this._endPan(); return; } this._up(e); });
        c.addEventListener("mouseleave", (e) => { if (this._isPanning) { this._endPan(); return; } this._up(e); });
        c.addEventListener("wheel", (e) => { e.preventDefault(); this._onWheel(e); }, { passive: false });

        c.addEventListener("touchstart", (e) => {
            e.preventDefault();
            if (e.touches.length === 2) { this._pinchStart(e); return; }
            this._down(e.touches[0]);
        }, { passive: false });
        c.addEventListener("touchmove", (e) => {
            e.preventDefault();
            if (e.touches.length === 2) { this._pinchMove(e); return; }
            if (this._isPanning) { this._doPan(e.touches[0]); return; }
            this._move(e.touches[0]);
        }, { passive: false });
        c.addEventListener("touchend", (e) => {
            if (this._isPanning) { this._endPan(); return; }
            this._up(e);
        }, { passive: false });

        document.addEventListener("keydown", (e) => {
            if (e.ctrlKey && e.key === "0") { e.preventDefault(); this._resetView(); }
            if (e.ctrlKey && e.key === "=") { e.preventDefault(); this.zoomIn(); }
            if (e.ctrlKey && e.key === "-") { e.preventDefault(); this.zoomOut(); }
        });
    }

    _px(cos) { return cos * this.dpr; }

    // ─── Zoom/Pan ───
    _onWheel(e) {
        const oldZ = this._zoom;
        this._zoom = Math.max(0.1, Math.min(10, this._zoom * (1 - e.deltaY * 0.001)));
        const rect = this.canvas.parentElement.getBoundingClientRect();
        const mx = e.clientX - rect.left, my = e.clientY - rect.top;
        this._panX = mx - (mx - this._panX) * (this._zoom / oldZ);
        this._panY = my - (my - this._panY) * (this._zoom / oldZ);
        this._applyCssTransform();
        this._render();
        if (this.options.onZoom) this.options.onZoom(this._zoom);
    }
    zoomIn() { this._onWheel({ deltaY: -120, clientX: window.innerWidth / 2, clientY: window.innerHeight / 2 }); }
    zoomOut() { this._onWheel({ deltaY: 120, clientX: window.innerWidth / 2, clientY: window.innerHeight / 2 }); }
    _resetView() { this._zoom = 1; this._panX = 0; this._panY = 0; this._applyCssTransform(); this._render(); if (this.options.onZoom) this.options.onZoom(1); }

    _startPan(e) { this._isPanning = true; this._panStart = { x: e.clientX - this._panX, y: e.clientY - this._panY }; }
    _doPan(e) { if (!this._isPanning) return; this._panX = e.clientX - this._panStart.x; this._panY = e.clientY - this._panStart.y; this._applyCssTransform(); }
    _endPan() { this._isPanning = false; }
    _pinchStart(e) {
        const t = e.touches;
        this._pinchDist = Math.hypot(t[0].clientX - t[1].clientX, t[0].clientY - t[1].clientY);
        this._pinchZoom = this._zoom;
        this._pinchCX = (t[0].clientX + t[1].clientX) / 2;
        this._pinchCY = (t[0].clientY + t[1].clientY) / 2;
    }
    _pinchMove(e) {
        const t = e.touches;
        const dist = Math.hypot(t[0].clientX - t[1].clientX, t[0].clientY - t[1].clientY);
        const s = dist / this._pinchDist;
        const oldZ = this._zoom;
        this._zoom = Math.max(0.1, Math.min(10, this._pinchZoom * s));
        const rect = this.canvas.parentElement.getBoundingClientRect();
        const mx = this._pinchCX - rect.left, my = this._pinchCY - rect.top;
        this._panX = mx - (mx - this._panX) * (this._zoom / oldZ);
        this._panY = my - (my - this._panY) * (this._zoom / oldZ);
        this._applyCssTransform();
        this._render();
        if (this.options.onZoom) this.options.onZoom(this._zoom);
    }
    toggleFullscreen() {
        const el = this.canvas.parentElement;
        if (!document.fullscreenElement) { el.requestFullscreen?.() || el.webkitRequestFullscreen?.(); }
        else { document.exitFullscreen?.() || document.webkitExitFullscreen?.(); }
    }

    // ─── Drawing ───
    _down(e) {
        if (this.textMode) return;
        if (this.tool === "compass") { this._compDown(e); return; }
        const p = this._pos(e);
        this.isDrawing = true;
        this.lastX = this._px(p.x); this.lastY = this._px(p.y);
        this.points = [[this._px(p.x), this._px(p.y)]];
        if (this.tool === "laser") { this.laserVisible = true; this._render(); return; }
        this._snap();
    }

    _move(e) {
        const p = this._pos(e);
        const px = this._px(p.x), py = this._px(p.y);
        if (this.tool === "laser" && this.laserVisible) {
            this.lastX = px; this.lastY = py; this._render();
            clearTimeout(this.laserTimeout);
            this.laserTimeout = setTimeout(() => { this.laserVisible = false; this._render(); }, 2000);
            this._emitCursor(p); return;
        }
        if (this.tool === "compass" && this.isDrawing) { this._compMove(e); return; }
        if (!this.isDrawing) { this._emitCursor(p); return; }

        if (this.tool === "eraser") {
            const erSize = this._px(this._width + 5);
            this.ctx.globalCompositeOperation = "destination-out";
            this.ctx.beginPath(); this.ctx.arc(px, py, erSize, 0, Math.PI * 2); this.ctx.fill();
            this.ctx.globalCompositeOperation = "source-over";
            this.points.push([px, py]);
            this._emitDraw("erase", { points: [[px, py]], width: this._width + 5 });
            return;
        }

        if (this.tool === "highlight") this.ctx.globalAlpha = 0.3;

        this.ctx.beginPath();
        this.ctx.moveTo(this.lastX, this.lastY);
        this.ctx.lineTo(px, py);
        this.ctx.strokeStyle = this.color;
        this.ctx.lineWidth = Math.max(0.05, this._px(this._width));
        this.ctx.setLineDash(this._pxArr(this.dash));
        this.ctx.lineCap = "round";
        this.ctx.lineJoin = "round";
        this.ctx.stroke();
        this.ctx.setLineDash([]);
        this.ctx.globalAlpha = 1;

        this.points.push([px, py]);
        this.lastX = px; this.lastY = py;
        this._emitDraw("line", { points: this.points, color: this.color, width: this._width, dash: this.dash });
    }

    _pxArr(arr) { return Array.isArray(arr) ? arr.map(v => v * this.dpr) : arr; }

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
    _drawBackground() {
        const w = this.canvas.width, h = this.canvas.height;
        this.ctx.fillStyle = this.boardMode === "white" ? "#FFFFFF" : "#1e293b";
        this.ctx.fillRect(0, 0, w, h);
        if (this.currentBg && this.bgBounds) {
            this.ctx.drawImage(this.currentBg, this._px(this.bgBounds.x), this._px(this.bgBounds.y), this._px(this.bgBounds.w), this._px(this.bgBounds.h));
        }
        if (!this.gridEnabled) return;
        this.ctx.strokeStyle = this.boardMode === "white" ? "rgba(0,0,0,0.07)" : "rgba(255,255,255,0.07)";
        this.ctx.lineWidth = 0.5;
        if (this.gridLogarithmic) {
            const maxD = Math.ceil(Math.log10(Math.max(w, h) / this.dpr));
            const ppd = Math.max(w, h) / maxD;
            for (let d = 0; d < maxD; d++) {
                const b = d * ppd;
                this.ctx.lineWidth = 1; this.ctx.beginPath(); this.ctx.moveTo(b, 0); this.ctx.lineTo(b, h); this.ctx.stroke();
                for (let n = 2; n <= 9; n++) {
                    const x = b + Math.log10(n) * ppd;
                    this.ctx.lineWidth = 0.3; this.ctx.beginPath(); this.ctx.moveTo(x, 0); this.ctx.lineTo(x, h); this.ctx.stroke();
                }
            }
        } else {
            const sp = this._px(this.gridSpacing);
            for (let x = 0; x <= w; x += sp) { this.ctx.beginPath(); this.ctx.moveTo(x, 0); this.ctx.lineTo(x, h); this.ctx.stroke(); }
            for (let y = 0; y <= h; y += sp) { this.ctx.beginPath(); this.ctx.moveTo(0, y); this.ctx.lineTo(w, y); this.ctx.stroke(); }
        }
    }

    setBackground(url) {
        if (!url) { this.currentBg = null; this.bgBounds = null; this._render(); return; }
        const img = new Image();
        img.crossOrigin = "anonymous";
        img.onload = () => {
            this.currentBg = img;
            const cw = this.canvas.width, ch = this.canvas.height;
            const iw = img.naturalWidth || img.width, ih = img.naturalHeight || img.height;
            const sc = Math.min(cw / iw, ch / ih);
            const bw = iw * sc, bh = ih * sc;
            this.bgBounds = { x: (cw - bw) / 2 / this.dpr, y: (ch - bh) / 2 / this.dpr, w: bw / this.dpr, h: bh / this.dpr };
            this._render();
        };
        img.src = url;
    }

    // ─── Compass ───
    _compDown(e) {
        const p = this._pos(e);
        this.isDrawing = true;
        this.compassCenter = { x: this._px(p.x), y: this._px(p.y) };
        this.compassSnapshot = new Image();
        this.compassSnapshot.src = this.canvas.toDataURL();
        this.compassRadius = 0;
    }
    _compMove(e) {
        if (!this.isDrawing) return;
        const p = this._pos(e);
        const cx = this.compassCenter.x, cy = this.compassCenter.y;
        const px = this._px(p.x), py = this._px(p.y);
        this.compassRadius = Math.hypot(px - cx, py - cy);
        this.ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);
        this._drawBackground();
        if (this.compassSnapshot.complete) this.ctx.drawImage(this.compassSnapshot, 0, 0);
        this.ctx.strokeStyle = this.color; this.ctx.lineWidth = this._px(this._width);
        this.ctx.beginPath(); this.ctx.arc(cx, cy, this.compassRadius, 0, Math.PI * 2); this.ctx.stroke();
        this.ctx.strokeStyle = this.color; this.ctx.lineWidth = 1;
        this.ctx.beginPath(); this.ctx.moveTo(cx - 6 * this.dpr, cy); this.ctx.lineTo(cx + 6 * this.dpr, cy); this.ctx.stroke();
        this.ctx.beginPath(); this.ctx.moveTo(cx, cy - 6 * this.dpr); this.ctx.lineTo(cx, cy + 6 * this.dpr); this.ctx.stroke();
        this.ctx.setLineDash([4 * this.dpr, 4 * this.dpr]);
        this.ctx.beginPath(); this.ctx.moveTo(cx, cy); this.ctx.lineTo(px, py); this.ctx.stroke(); this.ctx.setLineDash([]);
        this.ctx.fillStyle = this.color; this.ctx.font = `${12 * this.dpr}px Inter, sans-serif`;
        this.ctx.fillText(`r=${Math.round(this.compassRadius / this.dpr)}px`, cx + (px - cx) / 2 + 5 * this.dpr, cy + (py - cy) / 2 - 5 * this.dpr);
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

    // ─── Undo / Redo ───
    _snap() { this.undoStack.push(this.canvas.toDataURL()); if (this.undoStack.length > 50) this.undoStack.shift(); this.redoStack = []; }
    _restoreBg() { this.ctx.clearRect(0, 0, this.canvas.width, this.canvas.height); this._drawBackground(); }

    undo() {
        if (this.undoStack.length === 0) return;
        this.redoStack.push(this.canvas.toDataURL());
        const img = new Image();
        img.onload = () => { this.ctx.clearRect(0, 0, this.canvas.width, this.canvas.height); this._drawBackground(); this.ctx.drawImage(img, 0, 0); };
        img.src = this.undoStack.pop();
    }
    redo() {
        if (this.redoStack.length === 0) return;
        this.undoStack.push(this.canvas.toDataURL());
        const img = new Image();
        img.onload = () => { this.ctx.clearRect(0, 0, this.canvas.width, this.canvas.height); this._drawBackground(); this.ctx.drawImage(img, 0, 0); };
        img.src = this.redoStack.pop();
    }
    clearCanvas() {
        this._snap();
        this.ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);
        this._drawBackground();
        if (this.currentBg && this.bgBounds) {
            this.ctx.drawImage(this.currentBg, this._px(this.bgBounds.x), this._px(this.bgBounds.y), this._px(this.bgBounds.w), this._px(this.bgBounds.h));
        }
    }

    setTool(t) { this.tool = t; this.textMode = false; this.canvas.style.cursor = { pen: "default", eraser: "default", text: "text", highlight: "default", laser: "crosshair", compass: "crosshair" }[t] || "default"; }
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
                this.ctx.fillText(text, this._px(p.x), this._px(p.y));
                this._emitDraw("text", { text, x: p.x, y: p.y, color: this.color, fontSize: this.fontSize });
            }
            document.body.removeChild(input);
            this.textMode = false;
        };
        input.addEventListener("blur", done);
        input.addEventListener("keydown", (ev) => { if (ev.key === "Enter") { ev.preventDefault(); done(); } });
    }

    _render() {
        this.ctx.setTransform(1, 0, 0, 1, 0, 0);
        this.ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);
        this._drawBackground();
        for (const op of this.remoteOps) this._replay(op);
    }

    _replay(op) {
        const d = op.data || {};
        const ctx = this.ctx;
        if (op.op_type === "line") {
            const pts = d.points || [];
            if (pts.length < 2) return;
            ctx.beginPath(); ctx.moveTo(pts[0][0], pts[0][1]);
            for (let i = 1; i < pts.length; i++) ctx.lineTo(pts[i][0], pts[i][1]);
            ctx.strokeStyle = d.color || "#000";
            ctx.lineWidth = Math.max(0.05, (d.width || 3) * this.dpr);
            ctx.setLineDash(Array.isArray(d.dash) ? d.dash.map(v => v * this.dpr) : []);
            ctx.lineCap = "round"; ctx.lineJoin = "round"; ctx.stroke(); ctx.setLineDash([]);
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
        this.ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);
        this._drawBackground();
        if (this.currentBg && this.bgBounds) {
            this.ctx.drawImage(this.currentBg, this._px(this.bgBounds.x), this._px(this.bgBounds.y), this._px(this.bgBounds.w), this._px(this.bgBounds.h));
        }
        for (const op of ops) this._replay(op);
    }

    setBoardMode(m) { this.boardMode = m; this._render(); }
    toggleGrid() { this.gridEnabled = !this.gridEnabled; this._render(); }
    setGridSpacing(v) { this.gridSpacing = Math.max(10, Math.min(500, v)); this._render(); }
    setGridLogarithmic(v) { this.gridLogarithmic = !!v; this._render(); }

    _emitDraw(op_type, data) { if (this.options.onDraw) this.options.onDraw({ op_type, data, timestamp: Date.now() }); }
    _emitCursor(p) { if (this.options.onCursor) this.options.onCursor({ x: p.x, y: p.y }); }
    toDataURL() { return this.canvas.toDataURL("image/png"); }
}
