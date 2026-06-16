class WhiteboardCanvas {
    constructor(canvasId, options = {}) {
        this.canvas = document.getElementById(canvasId);
        this.ctx = this.canvas.getContext("2d");
        this.options = options;
        this.dpr = window.devicePixelRatio || 1;

        this.tool = "pen";
        this.color = "#000000";
        this.width = 0.75;
        this.opacity = 1;
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
        this.currentBg = null;      // Image object
        this.bgBounds = null;       // {x, y, w, h} for aspect-ratio rendering
        this.remoteOps = [];

        // Display settings
        this.boardMode = "white";
        this.gridEnabled = false;
        this.gridSpacing = 50;
        this.gridLogarithmic = false;

        // Zoom & Pan
        this.zoom = 1;
        this.panX = 0;
        this.panY = 0;
        this._isPanning = false;
        this._panStart = { x: 0, y: 0 };

        // Compass
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
        const rect = this.canvas.parentElement.getBoundingClientRect();
        const w = rect.width, h = rect.height;
        this.canvas.width = w * this.dpr;
        this.canvas.height = h * this.dpr;
        this.canvas.style.width = w + "px";
        this.canvas.style.height = h + "px";
        this._render();
    }

    _beginFrame() {
        const ed = this._edpr();
        const z = this.zoom, px = this.panX, py = this.panY;
        this.ctx.setTransform(ed * z, 0, 0, ed * z, ed * px, ed * py);
    }

    // Convert screen coords to canvas-logical coords (accounting for zoom/pan)
    _pos(e) {
        // Use offsetX/offsetY for MouseEvent (exact position in target)
        // Use clientX - rect.left for Touch (TouchEvent lacks offsetX)
        if (e.offsetX !== undefined && e.offsetY !== undefined) {
            // MouseEvent: offsetX/Y is relative to the canvas element
            const ed = this._edpr();
            return {
                x: (e.offsetX * ed - this.panX * ed) / (this.zoom * ed),
                y: (e.offsetY * ed - this.panY * ed) / (this.zoom * ed),
            };
        }
        // TouchEvent: compute from clientX - rect.left
        const r = this.canvas.getBoundingClientRect();
        const ed = this._edpr();
        const px = (e.clientX - r.left) * ed;
        const py = (e.clientY - r.top) * ed;
        return {
            x: (px - this.panX * ed) / (this.zoom * ed),
            y: (py - this.panY * ed) / (this.zoom * ed),
        };
    }

    _edpr() {
        const r = this.canvas.getBoundingClientRect();
        return r.width > 0 ? this.canvas.width / r.width : this.dpr;
    }

    _bindEvents() {
        const c = this.canvas;
        c.addEventListener("mousedown", (e) => {
            if (e.button === 1 || (e.button === 0 && e.shiftKey)) { this._startPan(e); return; }
            this._down(e);
        });
        c.addEventListener("mousemove", (e) => {
            if (this._isPanning) { this._doPan(e); return; }
            // Update pen preview cursor position
            this._cursorX = e.clientX; this._cursorY = e.clientY;
            this._move(e);
        });
        c.addEventListener("mouseup", (e) => { if (this._isPanning) { this._endPan(e); return; } this._up(e); });
        c.addEventListener("mouseleave", (e) => { if (this._isPanning) { this._endPan(e); return; } this._up(e); });
        // Track cursor for pen preview
        this._cursorX = 0; this._cursorY = 0;
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
            if (this._isPanning) { this._endPan(e); return; }
            this._up(e);
        }, { passive: false });

        // Keyboard shortcuts
        document.addEventListener("keydown", (e) => {
            if (e.ctrlKey && e.key === "0") { e.preventDefault(); this.zoomReset(); }
            if (e.ctrlKey && e.key === "=") { e.preventDefault(); this.zoomIn(); }
            if (e.ctrlKey && e.key === "-") { e.preventDefault(); this.zoomOut(); }
        });
    }

    // ─── Zoom & Pan ───
    _onWheel(e) {
        const delta = -e.deltaY * 0.001;
        const factor = 1 + delta;
        const newZoom = Math.max(0.1, Math.min(10, this.zoom * factor));
        // Zoom toward mouse position
        const rect = this.canvas.getBoundingClientRect();
        const mx = e.clientX - rect.left, my = e.clientY - rect.top;
        // Pan keeps the point under the cursor stationary
        this.panX = mx - (mx - this.panX) * (newZoom / this.zoom);
        this.panY = my - (my - this.panY) * (newZoom / this.zoom);
        this.zoom = newZoom;
        this._render();
        if (this.options.onZoom) this.options.onZoom(this.zoom);
    }

    zoomIn() { this._onWheel({ deltaY: -120, clientX: 0, clientY: 0 }); }
    zoomOut() { this._onWheel({ deltaY: 120, clientX: 0, clientY: 0 }); }
    zoomReset() { this.zoom = 1; this.panX = 0; this.panY = 0; this._render(); if (this.options.onZoom) this.options.onZoom(1); }
    setZoom(v) { this.zoom = Math.max(0.1, Math.min(10, v)); this._render(); if (this.options.onZoom) this.options.onZoom(this.zoom); }

    _startPan(e) {
        this._isPanning = true;
        this._panStart = { x: e.clientX - this.panX, y: e.clientY - this.panY };
        this.canvas.style.cursor = "grabbing";
    }
    _doPan(e) {
        if (!this._isPanning) return;
        this.panX = e.clientX - this._panStart.x;
        this.panY = e.clientY - this._panStart.y;
        this._render();
    }
    _endPan(e) {
        this._isPanning = false;
        this.canvas.style.cursor = "crosshair";
    }

    _pinchStart(e) {
        const t = e.touches;
        this._pinchDist = Math.hypot(t[0].clientX - t[1].clientX, t[0].clientY - t[1].clientY);
        this._pinchZoom = this.zoom;
        this._pinchCX = (t[0].clientX + t[1].clientX) / 2;
        this._pinchCY = (t[0].clientY + t[1].clientY) / 2;
    }
    _pinchMove(e) {
        const t = e.touches;
        const dist = Math.hypot(t[0].clientX - t[1].clientX, t[0].clientY - t[1].clientY);
        const scale = dist / this._pinchDist;
        const newZoom = Math.max(0.1, Math.min(10, this._pinchZoom * scale));
        const rect = this.canvas.getBoundingClientRect();
        const mx = this._pinchCX - rect.left, my = this._pinchCY - rect.top;
        this.panX = mx - (mx - this.panX) * (newZoom / this.zoom);
        this.panY = my - (my - this.panY) * (newZoom / this.zoom);
        this.zoom = newZoom;
        this._render();
        if (this.options.onZoom) this.options.onZoom(this.zoom);
    }

    // ─── Fullscreen ───
    toggleFullscreen() {
        const el = this.canvas.parentElement;
        if (!document.fullscreenElement) {
            el.requestFullscreen?.() || el.webkitRequestFullscreen?.();
        } else {
            document.exitFullscreen?.() || document.webkitExitFullscreen?.();
        }
    }

    // ─── Display Settings ───
    setBoardMode(m) { this.boardMode = m; this._render(); }
    toggleGrid() { this.gridEnabled = !this.gridEnabled; this._render(); }
    setGridSpacing(v) { this.gridSpacing = Math.max(10, Math.min(500, v)); this._render(); }
    setGridLogarithmic(v) { this.gridLogarithmic = !!v; this._render(); }

    // ─── Background + Grid ───
    _drawBackground() {
        this._beginFrame();
        const vw = this.canvas.width / this.dpr;  // CSS viewport width
        const vh = this.canvas.height / this.dpr; // CSS viewport height
        // Fill full visible area in drawing coords
        const left = -this.panX / this.zoom;
        const top = -this.panY / this.zoom;
        const right = left + vw / this.zoom;
        const bottom = top + vh / this.zoom;
        this.ctx.fillStyle = this.boardMode === "white" ? "#FFFFFF" : "#1e293b";
        this.ctx.fillRect(left - 1000, top - 1000, right - left + 2000, bottom - top + 2000);

        // Background image with proper aspect ratio
        if (this.currentBg && this.bgBounds) {
            this.ctx.drawImage(this.currentBg, this.bgBounds.x, this.bgBounds.y, this.bgBounds.w, this.bgBounds.h);
        }

        if (!this.gridEnabled) return;
        this.ctx.strokeStyle = this.boardMode === "white" ? "rgba(0,0,0,0.07)" : "rgba(255,255,255,0.07)";
        this.ctx.lineWidth = 0.5;
        if (this.gridLogarithmic) {
            const maxD = Math.ceil(Math.log10(Math.max(right - left, bottom - top)));
            const ppd = Math.max(right - left, bottom - top) / maxD;
            for (let d = 0; d < maxD; d++) {
                const b = d * ppd + left;
                this.ctx.lineWidth = 1; this.ctx.beginPath(); this.ctx.moveTo(b, top); this.ctx.lineTo(b, bottom); this.ctx.stroke();
                for (let n = 2; n <= 9; n++) {
                    const x = b + Math.log10(n) * ppd;
                    this.ctx.lineWidth = 0.3; this.ctx.beginPath(); this.ctx.moveTo(x, top); this.ctx.lineTo(x, bottom); this.ctx.stroke();
                }
            }
        } else {
            const startX = Math.floor(left / this.gridSpacing) * this.gridSpacing;
            const startY = Math.floor(top / this.gridSpacing) * this.gridSpacing;
            for (let x = startX; x <= right; x += this.gridSpacing) { this.ctx.beginPath(); this.ctx.moveTo(x, top); this.ctx.lineTo(x, bottom); this.ctx.stroke(); }
            for (let y = startY; y <= bottom; y += this.gridSpacing) { this.ctx.beginPath(); this.ctx.moveTo(left, y); this.ctx.lineTo(right, y); this.ctx.stroke(); }
        }
    }

    // ─── Pointer ───
    _down(e) {
        if (this.textMode) return;
        if (this.tool === "compass") { this._compDown(e); return; }
        const pos = this._pos(e);
        this.isDrawing = true;
        this.lastX = pos.x; this.lastY = pos.y;
        this.points = [[pos.x, pos.y]];
        if (this.tool === "laser") { this.laserVisible = true; this._render(); return; }
        this._snap();
    }

    _move(e) {
        const pos = this._pos(e);

        if (this.tool === "laser" && this.laserVisible) {
            this.lastX = pos.x; this.lastY = pos.y; this._render();
            clearTimeout(this.laserTimeout);
            this.laserTimeout = setTimeout(() => { this.laserVisible = false; this._render(); }, 2000);
            this._emitCursor(pos); return;
        }
        if (this.tool === "compass" && this.isDrawing) { this._compMove(e); return; }
        if (!this.isDrawing) { this._emitCursor(pos); return; }

        this._beginFrame();

        if (this.tool === "eraser") {
            const erSize = (this.width + 5) / this.zoom;
            this.ctx.globalCompositeOperation = "destination-out";
            this.ctx.beginPath(); this.ctx.arc(pos.x, pos.y, erSize, 0, Math.PI * 2); this.ctx.fill();
            this.ctx.globalCompositeOperation = "source-over";
            this.points.push([pos.x, pos.y]);
            this._emitDraw("erase", { points: [[pos.x, pos.y]], width: this.width + 5 });
            return;
        }

        if (this.tool === "highlight") this.ctx.globalAlpha = 0.3;

        // Scale line width inversely by zoom so it stays consistent in screen pixels
        const screenWidth = this.width / this.zoom;
        this.ctx.beginPath();
        this.ctx.moveTo(this.lastX, this.lastY);
        this.ctx.lineTo(pos.x, pos.y);
        this.ctx.strokeStyle = this.color;
        this.ctx.lineWidth = Math.max(0.05, screenWidth);
        this.ctx.setLineDash(this.dash);
        this.ctx.lineCap = "round";
        this.ctx.lineJoin = "round";
        this.ctx.stroke();
        this.ctx.setLineDash([]);
        this.ctx.globalAlpha = 1;

        this.points.push([pos.x, pos.y]);
        this.lastX = pos.x; this.lastY = pos.y;
        this._emitDraw("line", { points: this.points, color: this.color, width: parseFloat(this.width) || 0.75, dash: this.dash });
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
                points: this.points, color: this.color, width: parseFloat(this.width) || 0.75, dash: this.dash,
            });
        }
    }

    // ─── Compass ───
    _compDown(e) {
        const p = this._pos(e);
        this.isDrawing = true;
        this.compassCenter = { x: p.x, y: p.y };
        this.compassSnapshot = this.canvas.toDataURL();
        this.compassRadius = 0;
    }

    _compMove(e) {
        if (!this.isDrawing) return;
        const p = this._pos(e);
        this.compassRadius = Math.hypot(p.x - this.compassCenter.x, p.y - this.compassCenter.y);
        const img = new Image();
        img.onload = () => {
            const w = this.canvas.width / this.dpr, h = this.canvas.height / this.dpr;
            this.ctx.clearRect(0, 0, w, h);
            this._drawBackground();
            this.ctx.drawImage(img, 0, 0);
            this.ctx.strokeStyle = this.color; this.ctx.lineWidth = this.width;
            this.ctx.beginPath(); this.ctx.arc(this.compassCenter.x, this.compassCenter.y, this.compassRadius, 0, Math.PI * 2); this.ctx.stroke();
            this.ctx.strokeStyle = this.color; this.ctx.lineWidth = 1;
            this.ctx.beginPath(); this.ctx.moveTo(this.compassCenter.x - 6, this.compassCenter.y); this.ctx.lineTo(this.compassCenter.x + 6, this.compassCenter.y); this.ctx.stroke();
            this.ctx.beginPath(); this.ctx.moveTo(this.compassCenter.x, this.compassCenter.y - 6); this.ctx.lineTo(this.compassCenter.x, this.compassCenter.y + 6); this.ctx.stroke();
            this.ctx.setLineDash([4, 4]); this.ctx.beginPath(); this.ctx.moveTo(this.compassCenter.x, this.compassCenter.y); this.ctx.lineTo(p.x, p.y); this.ctx.stroke(); this.ctx.setLineDash([]);
            this.ctx.fillStyle = this.color; this.ctx.font = "12px Inter, sans-serif";
            this.ctx.fillText(`r=${Math.round(this.compassRadius)}px`, this.compassCenter.x + (p.x - this.compassCenter.x) / 2 + 5, this.compassCenter.y + (p.y - this.compassCenter.y) / 2 - 5);
        };
        img.src = this.compassSnapshot;
    }

    _compUp(e) {
        if (!this.isDrawing) return;
        this.isDrawing = false;
        if (this.compassRadius > 5) {
            this._emitDraw("circle", { cx: this.compassCenter.x, cy: this.compassCenter.y, r: this.compassRadius, color: this.color, width: this.width });
        }
    }

    // ─── Calculator ───
    toggleCalculator() {
        if (this.calcEl && this.calcEl.style.display !== "none") { this.calcEl.style.display = "none"; return; }
        if (!this.calcEl) {
            if (typeof ScanGradeTools !== "undefined" && ScanGradeTools.createCalculator) {
                this.calcEl = ScanGradeTools.createCalculator();
                document.body.appendChild(this.calcEl);
            } else { alert("Kalkulator tidak tersedia"); return; }
        }
        this.calcEl.style.display = "block";
    }

    // ─── Undo / Redo ───
    _snap() {
        this.undoStack.push(this.canvas.toDataURL());
        if (this.undoStack.length > 50) this.undoStack.shift();
        this.redoStack = [];
    }

    _restoreBg() {
        this.ctx.setTransform(1, 0, 0, 1, 0, 0);
        this.ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);
        this._drawBackground();
    }

    undo() {
        if (this.undoStack.length === 0) return;
        this.redoStack.push(this.canvas.toDataURL());
        this.ctx.setTransform(1, 0, 0, 1, 0, 0);
        this._restoreBg();
        const img = new Image();
        img.onload = () => { this.ctx.setTransform(1, 0, 0, 1, 0, 0); this.ctx.drawImage(img, 0, 0); };
        img.src = this.undoStack.pop();
    }

    redo() {
        if (this.redoStack.length === 0) return;
        this.undoStack.push(this.canvas.toDataURL());
        this.ctx.setTransform(1, 0, 0, 1, 0, 0);
        this._restoreBg();
        const img = new Image();
        img.onload = () => { this.ctx.setTransform(1, 0, 0, 1, 0, 0); this.ctx.drawImage(img, 0, 0); };
        img.src = this.redoStack.pop();
    }

    clearCanvas() {
        this._snap();
        this.ctx.setTransform(1, 0, 0, 1, 0, 0);
        this._restoreBg();
        if (this.currentBg && this.bgBounds) {
            this.ctx.setTransform(1, 0, 0, 1, 0, 0);
            this.ctx.drawImage(this.currentBg, this.bgBounds.x, this.bgBounds.y, this.bgBounds.w, this.bgBounds.h);
        }
    }

    setBackground(url) {
        if (!url) { this.currentBg = null; this.bgBounds = null; this._render(); return; }
        const img = new Image();
        img.crossOrigin = "anonymous";
        img.onload = () => {
            this.currentBg = img;
            this._calcBgBounds();
            this._render();
        };
        img.src = url;
    }

    _calcBgBounds() {
        if (!this.currentBg) { this.bgBounds = null; return; }
        const cw = this.canvas.width / this.dpr;
        const ch = this.canvas.height / this.dpr;
        const iw = this.currentBg.naturalWidth || this.currentBg.width;
        const ih = this.currentBg.naturalHeight || this.currentBg.height;
        if (!iw || !ih) { this.bgBounds = { x: 0, y: 0, w: cw, h: ch }; return; }

        const scale = Math.min(cw / iw, ch / ih);
        const bw = iw * scale;
        const bh = ih * scale;
        this.bgBounds = { x: (cw - bw) / 2, y: (ch - bh) / 2, w: bw, h: bh };
    }

    setTool(t) {
        this.tool = t; this.textMode = false;
        const cursors = { pen: "default", eraser: "default", text: "text", highlight: "default", laser: "crosshair", compass: "crosshair" };
        this.canvas.style.cursor = cursors[t] || "default";
    }

    setColor(c) { this.color = c; }
    setWidth(w) { this.width = w; }
    setOpacity(o) { this.opacity = o; }
    setFontSize(s) { this.fontSize = s; }
    setDash(d) { this.dash = d; }
    clearLaser() { this.laserVisible = false; this._render(); }

    enableTextMode() {
        this.textMode = true;
        this.tool = "text";
        this.canvas.addEventListener("click", (e) => this._placeText(e), { once: true });
    }

    _placeText(e) {
        if (!this.textMode) return;
        const pos = this._pos(e);
        const input = document.createElement("input");
        input.type = "text";
        input.style.position = "fixed";
        input.style.left = (e.clientX) + "px";
        input.style.top = (e.clientY) + "px";
        input.style.fontSize = this.fontSize + "px";
        input.style.fontFamily = "Inter, sans-serif";
        input.style.border = "2px solid " + this.color;
        input.style.padding = "4px 8px";
        input.style.borderRadius = "6px";
        input.style.background = "#fff";
        input.style.zIndex = "99999";
        document.body.appendChild(input);
        input.focus();
        const done = () => {
            const text = input.value.trim();
            if (text) {
                this._snap();
                this.ctx.font = `${this.fontSize}px Inter, sans-serif`;
                this.ctx.fillStyle = this.color;
                this.ctx.fillText(text, pos.x, pos.y);
                this._emitDraw("text", { text, x: pos.x, y: pos.y, color: this.color, fontSize: this.fontSize });
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
        this.ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);
        this._drawBackground();
        for (const op of this.remoteOps) this.replayOp(op);
        if (this.laserVisible) {
            this.ctx.strokeStyle = "rgba(255,0,0,0.6)"; this.ctx.lineWidth = 20 / (this.dpr * this.zoom);
            this.ctx.beginPath(); this.ctx.arc(this.lastX, this.lastY, 10 / (this.dpr * this.zoom), 0, Math.PI * 2); this.ctx.stroke();
            this.ctx.fillStyle = "rgba(255,0,0,0.9)";
            this.ctx.beginPath(); this.ctx.arc(this.lastX, this.lastY, 4 / (this.dpr * this.zoom), 0, Math.PI * 2); this.ctx.fill();
        }
    }

    // ─── Replay ───
    replayOp(op) {
        this._beginFrame();
        const d = op.data || {};
        const ctx = this.ctx;
        if (op.op_type === "line") {
            const pts = d.points || [];
            if (pts.length < 2) return;
            ctx.beginPath(); ctx.moveTo(pts[0][0], pts[0][1]);
            for (let i = 1; i < pts.length; i++) ctx.lineTo(pts[i][0], pts[i][1]);
            ctx.strokeStyle = d.color || "#000";
            const w = (d.width || 3) / this.zoom;
            ctx.lineWidth = Math.max(0.05, w);
            ctx.setLineDash(d.dash || []); ctx.lineCap = "round"; ctx.lineJoin = "round"; ctx.stroke(); ctx.setLineDash([]);
        } else if (op.op_type === "text") {
            ctx.font = `${d.fontSize || 16}px Inter, sans-serif`;
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
        this._beginFrame();
        const w = this.canvas.width / this.dpr, h = this.canvas.height / this.dpr;
        this.ctx.clearRect(0, 0, w, h);
        this._drawBackground();
        if (this.currentBg && this.bgBounds) {
            this.ctx.drawImage(this.currentBg, this.bgBounds.x, this.bgBounds.y, this.bgBounds.w, this.bgBounds.h);
        }
        for (const op of ops) this.replayOp(op);
    }

    _emitDraw(op_type, data) { if (this.options.onDraw) this.options.onDraw({ op_type, data, timestamp: Date.now() }); }
    _emitCursor(pos) { if (this.options.onCursor) this.options.onCursor({ x: pos.x, y: pos.y }); }
    toDataURL() { return this.canvas.toDataURL("image/png"); }
}
