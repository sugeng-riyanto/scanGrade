/**
 * Whiteboard Canvas v6 — foolproof 1:1 coordinate mapping
 * canvas.width = canvas.clientWidth (CSS pixel size)
 * _pos() uses offsetX/offsetY for mouse, clientX-rect.left for touch
 */
class WhiteboardCanvas {
    constructor(canvasId, options = {}) {
        this.canvas = document.getElementById(canvasId);
        this.ctx = this.canvas.getContext("2d");
        this.options = options;
        this._wbId = options.whiteboardId || '';
        this.tool = "pen";
        this.color = "#000000";
        this._width = 0.75;
        // Line tool state (straight line drawing)
        this.lineStart = null;
        this.lineSnapshot = null;
        this.isLineDrawing = false;
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
        this.gridRowOnly = false;
        this.gridSpacing = 50;
        this.gridLogarithmic = false;

        this.compassCenter = null;
        this.compassSnapshot = null;
        this.compassRadius = 0;
        this.calcEl = null;

        this._zoom = 1;
        this._ready = false;
        this._init();
    }

    _init() {
        this._bindEvents();
        this._waitForSize();
        window.addEventListener("resize", () => this._resize());
    }

    // ─── Zoom (track base size dynamically) ───
    get _baseW() {
        // Base width is the stage width before zoom (from bg or 2000 default)
        const s = this.canvas.parentElement?.style;
        if (s && s.width && s.width !== '2000px') {
            return parseFloat(s.width) / this._zoom;
        }
        return 2000;
    }
    setZoom(v) {
        const z = Math.max(0.25, Math.min(5, parseFloat(v) || 1));
        const stage = this.canvas.parentElement;
        if (!stage) return;
        // Get the current base width (actual content size before zoom)
        const bw = this._baseW;
        const bh = this.canvas.parentElement ? parseFloat(stage.style.height) / (this._zoom || 1) : bw;
        stage.style.width = (bw * z) + "px";
        stage.style.height = (bh * z) + "px";
        this._zoom = z;
        this._resize();
        if (this.options.onZoom) this.options.onZoom(z);
    }
    zoomIn() { this.setZoom((this._zoom || 1) * 1.25); }
    zoomOut() { this.setZoom((this._zoom || 1) / 1.25); }
    zoomReset() { this.setZoom(1); }

    _waitForSize() {
        if (this.canvas.clientWidth > 10 && this.canvas.clientHeight > 10) {
            this._resize();
        } else {
            setTimeout(() => this._waitForSize(), 50);
        }
    }

    _resize() {
        const r = this.canvas.getBoundingClientRect();
        if (r.width < 10 || r.height < 10) {
            setTimeout(() => this._resize(), 50);
            return;
        }
        this.canvas.width = r.width;
        this.canvas.height = r.height;
        this._ready = true;
        if (this.currentBg) this._calcBgBounds();
        this._render();
    }

    // ─── Coordinate: exam canvas formula (take_exam.html:1187-1191) ───
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
        document.addEventListener("keydown", (e) => {
            if ((e.ctrlKey || e.metaKey) && e.key === "z") { e.preventDefault(); this.undo(); }
            if ((e.ctrlKey || e.metaKey) && e.key === "y") { e.preventDefault(); this.redo(); }
        });
    }

    // ─── Drawing ───
    _down(e) {
        if (this.textMode || this.isDrawing) return;
        if (this.tool === "compass") { this._compDown(e); return; }
        if (this.tool === "line") { this._lineDown(e); return; }
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
        if (this.tool === "line" && this.isLineDrawing) { this._lineMove(e); return; }
        if (!this.isDrawing) return;

        if (this.tool === "eraser") {
            this.ctx.globalCompositeOperation = "destination-out";
            this.ctx.beginPath();
            this.ctx.arc(p.x, p.y, Math.max(3, this._width + 2), 0, Math.PI * 2);
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
        this.ctx.lineWidth = Math.max(0.1, this._width);
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
        if (this.tool === "line") { this._lineUp(e); return; }
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
            this.ctx.drawImage(this.currentBg, this.bgBounds.x, this.bgBounds.y, this.bgBounds.w, this.bgBounds.h);
        }
        if (!this.gridEnabled) return;
        this.ctx.strokeStyle = this.boardMode === "white" ? "rgba(0,0,0,0.07)" : "rgba(255,255,255,0.07)";
        this.ctx.lineWidth = 0.5;
        if (this.gridRowOnly) {
            // Only horizontal lines (row mode)
            for (let y = 0; y <= h; y += this.gridSpacing) { this.ctx.beginPath(); this.ctx.moveTo(0, y); this.ctx.lineTo(w, y); this.ctx.stroke(); }
        } else {
            for (let x = 0; x <= w; x += this.gridSpacing) { this.ctx.beginPath(); this.ctx.moveTo(x, 0); this.ctx.lineTo(x, h); this.ctx.stroke(); }
            for (let y = 0; y <= h; y += this.gridSpacing) { this.ctx.beginPath(); this.ctx.moveTo(0, y); this.ctx.lineTo(w, y); this.ctx.stroke(); }
        }
    }

    setBackground(url) {
        if (!url) { this.currentBg = null; this.bgBounds = null; this._render(); return; }
        const img = new Image();
        img.crossOrigin = "anonymous";
        img.onload = () => {
            this.currentBg = img;
            const iw = img.naturalWidth || img.width || 0;
            const ih = img.naturalHeight || img.height || 0;
            if (!iw || !ih) { this._render(); return; }

            // PDF at 150 DPI → screen ~96 DPI → actual paper size
            const actualW = iw * 96 / 150;
            const actualH = ih * 96 / 150;

            // Reset zoom to 1x, set stage to actual paper size
            this._zoom = 1;
            const stage = this.canvas.parentElement;
            if (stage) {
                stage.style.width = actualW + "px";
                stage.style.height = actualH + "px";
            }
            this._resize();

            // Image fills the canvas 1:1
            this.bgBounds = { x: 0, y: 0, w: this.canvas.width, h: this.canvas.height };

            // Auto-zoom to fit viewport (90% margin)
            if (stage && this.canvas.width > 0) {
                const zoom = Math.min(
                    (this.canvas.width * 0.9) / actualW,
                    (this.canvas.height * 0.9) / actualH
                );
                this.setZoom(Math.max(0.25, Math.min(5, zoom)));
            }

            this._render();
        };
        img.src = url;
    }

    // ─── Compass ───
    _compDown(e) { const p = this._pos(e); this.isDrawing = true; this.compassCenter = { x: p.x, y: p.y }; this.compassSnapshot = new Image(); this.compassSnapshot.src = this.canvas.toDataURL(); this.compassRadius = 0; }
    _compMove(e) {
        if (!this.isDrawing) return;
        const p = this._pos(e);
        const cx = this.compassCenter.x, cy = this.compassCenter.y;
        this.compassRadius = Math.hypot(p.x - cx, p.y - cy);
        this.ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);
        this._drawBackground();
        if (this.compassSnapshot.complete) this.ctx.drawImage(this.compassSnapshot, 0, 0);
        this.ctx.strokeStyle = this.color; this.ctx.lineWidth = Math.max(0.1, this._width);
        this.ctx.beginPath(); this.ctx.arc(cx, cy, this.compassRadius, 0, Math.PI * 2); this.ctx.stroke();
        this.ctx.beginPath(); this.ctx.moveTo(cx - 6, cy); this.ctx.lineTo(cx + 6, cy); this.ctx.stroke();
        this.ctx.beginPath(); this.ctx.moveTo(cx, cy - 6); this.ctx.lineTo(cx, cy + 6); this.ctx.stroke();
        this.ctx.setLineDash([4, 4]); this.ctx.beginPath(); this.ctx.moveTo(cx, cy); this.ctx.lineTo(p.x, p.y); this.ctx.stroke(); this.ctx.setLineDash([]);
        this.ctx.fillStyle = this.color; this.ctx.font = "12px Inter, sans-serif";
        this.ctx.fillText(`r=${Math.round(this.compassRadius)}px`, cx + (p.x - cx) / 2 + 5, cy + (p.y - cy) / 2 - 5);
    }
    _compUp(e) { if (!this.isDrawing) return; this.isDrawing = false; if (this.compassRadius > 5) { this._emitDraw("circle", { cx: this.compassCenter.x, cy: this.compassCenter.y, r: this.compassRadius, color: this.color, width: this._width }); } }

    // ─── Line tool (straight lines) ───
    _lineDown(e) {
        const p = this._pos(e);
        this.isLineDrawing = true;
        this.lineStart = { x: p.x, y: p.y };
        this.lineSnapshot = new Image();
        this.lineSnapshot.src = this.canvas.toDataURL();
        this._snap();
    }
    _lineMove(e) {
        if (!this.isLineDrawing) return;
        const p = this._pos(e);
        const s = this.lineStart;
        this.ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);
        this._drawBackground();
        if (this.lineSnapshot.complete) this.ctx.drawImage(this.lineSnapshot, 0, 0);
        // Draw preview line
        this.ctx.beginPath();
        this.ctx.moveTo(s.x, s.y);
        this.ctx.lineTo(p.x, p.y);
        this.ctx.strokeStyle = this.color;
        this.ctx.lineWidth = Math.max(0.1, this._width);
        this.ctx.lineCap = "round";
        this.ctx.stroke();
        // Show distance label
        const dist = Math.hypot(p.x - s.x, p.y - s.y);
        this.ctx.fillStyle = this.color;
        this.ctx.font = "11px Inter, sans-serif";
        this.ctx.fillText(`${Math.round(dist)}px`, (s.x + p.x) / 2 + 5, (s.y + p.y) / 2 - 5);
    }
    _lineUp(e) {
        if (!this.isLineDrawing) return;
        this.isLineDrawing = false;
        const p = this._pos(e);
        const s = this.lineStart;
        // Finalize line on snapshot
        this._snap();
        this.ctx.beginPath();
        this.ctx.moveTo(s.x, s.y);
        this.ctx.lineTo(p.x, p.y);
        this.ctx.strokeStyle = this.color;
        this.ctx.lineWidth = Math.max(0.1, this._width);
        this.ctx.lineCap = "round";
        this.ctx.stroke();
        this._emitDraw("line", { points: [[s.x, s.y], [p.x, p.y]], color: this.color, width: this._width, dash: this.dash });
    }

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
        input.style.position = "fixed"; input.style.left = e.clientX + "px"; input.style.top = e.clientY + "px";
        input.style.fontSize = this.fontSize + "px"; input.style.fontFamily = "Inter, sans-serif";
        input.style.border = "2px solid " + this.color; input.style.padding = "4px 8px";
        input.style.borderRadius = "6px"; input.style.background = "#fff"; input.style.zIndex = "99999";
        document.body.appendChild(input); input.focus();
        const done = () => {
            const text = input.value.trim();
            if (text) {
                this._snap();
                this.ctx.font = this.fontSize + "px Inter, sans-serif";
                this.ctx.fillStyle = this.color;
                this.ctx.fillText(text, p.x, p.y);
                this._emitDraw("text", { text, x: p.x, y: p.y, color: this.color, fontSize: this.fontSize });
            }
            document.body.removeChild(input); this.textMode = false;
        };
        input.addEventListener("blur", done);
        input.addEventListener("keydown", (ev) => { if (ev.key === "Enter") { ev.preventDefault(); done(); } });
    }

    _render() {
        if (!this._ready) return;
        this.ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);
        this._drawBackground();
        for (const op of this.remoteOps) this._replay(op);
    }

    replayOp(op) { this._replay(op); }
    _replay(op) {
        const d = op.data || {};
        const ctx = this.ctx;
        if (op.op_type === "line") {
            const pts = d.points || [];
            if (pts.length < 2) return;
            ctx.beginPath(); ctx.moveTo(pts[0][0], pts[0][1]);
            for (let i = 1; i < pts.length; i++) ctx.lineTo(pts[i][0], pts[i][1]);
            ctx.strokeStyle = d.color || "#000";
            ctx.lineWidth = Math.max(0.1, d.width || 3);
            ctx.lineCap = "round"; ctx.lineJoin = "round"; ctx.stroke();
        } else if (op.op_type === "text") {
            ctx.font = (d.fontSize || 16) + "px Inter, sans-serif";
            ctx.fillStyle = d.color || "#000"; ctx.fillText(d.text || "", d.x || 0, d.y || 0);
        } else if (op.op_type === "erase") {
            ctx.globalCompositeOperation = "destination-out";
            ctx.beginPath(); ctx.arc(d.x || 0, d.y || 0, (d.width || 8) + 5, 0, Math.PI * 2); ctx.fill();
            ctx.globalCompositeOperation = "source-over";
        } else if (op.op_type === "circle") {
            ctx.strokeStyle = d.color || "#000"; ctx.lineWidth = d.width || 3;
            ctx.beginPath(); ctx.arc(d.cx || 0, d.cy || 0, d.r || 0, 0, Math.PI * 2); ctx.stroke();
        }
    }

    loadOps(ops) {
        this._snap();
        this.ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);
        this._drawBackground();
        if (this.currentBg && this.bgBounds) this.ctx.drawImage(this.currentBg, this.bgBounds.x, this.bgBounds.y, this.bgBounds.w, this.bgBounds.h);
        for (const op of ops) this._replay(op);
    }

    setBoardMode(m) {
        this.boardMode = m;
        // Auto-contrast pen color: dark board → white pen, white board → black pen
        if (m === 'black' && (this.color === '#000000' || this.color === '#000' || this.color === 'black')) {
            this.color = '#ffffff';
            if (this.options.onColorChange) this.options.onColorChange('#ffffff');
        } else if (m === 'white' && (this.color === '#ffffff' || this.color === '#fff' || this.color === 'white')) {
            this.color = '#000000';
            if (this.options.onColorChange) this.options.onColorChange('#000000');
        }
        this._render();
    }
    toggleGrid() { this.gridEnabled = !this.gridEnabled; this._render(); }
    setGridSpacing(v) { this.gridSpacing = Math.max(10, Math.min(500, v)); this._render(); }
    setGridLogarithmic(v) { this.gridLogarithmic = !!v; this._render(); }

    _emitDraw(op_type, data) {
        const op = { op_type, data, timestamp: Date.now() };
        if (this.options.onDraw) this.options.onDraw(op);
        // Offline queue: save to localStorage if no WebSocket
        if (!navigator.onLine) {
            try {
                const q = JSON.parse(localStorage.getItem('wb_offline_' + (this.options.whiteboardId || '')) || '[]');
                q.push(op);
                localStorage.setItem('wb_offline_' + (this.options.whiteboardId || ''), JSON.stringify(q.slice(-200)));
            } catch(e) {}
        }
    }
    flushOfflineQueue() {
        try {
            const key = 'wb_offline_' + (this.options.whiteboardId || '');
            const q = JSON.parse(localStorage.getItem(key) || '[]');
            if (q.length === 0) return;
            localStorage.removeItem(key);
            q.forEach(op => { if (this.options.onDraw) this.options.onDraw(op); });
        } catch(e) {}
    }
    toDataURL() { return this.canvas.toDataURL("image/png"); }
}
