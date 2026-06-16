/* Whiteboard Canvas Engine — drawing, tools, undo/redo, laser, background */
class WhiteboardCanvas {
    constructor(canvasId, options = {}) {
        this.canvas = document.getElementById(canvasId);
        this.ctx = this.canvas.getContext("2d");
        this.options = options;
        this.tool = "pen";       // pen | eraser | text | highlight | laser | compass
        this.color = "#000000";
        this.width = 3;
        this.opacity = 1;
        this.fontSize = 16;
        this.fontFamily = "Inter, sans-serif";
        this.dash = [];
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
        this.currentBg = null;
        this.remoteOps = [];
        this.remoteCursors = {};

        // Display settings
        this.boardMode = "white";  // white | black
        this.gridEnabled = false;
        this.gridSpacing = 50;
        this.gridLogarithmic = false;

        // Tool overlays
        this.overlayContainer = null;
        this.toolSvg = { ruler: null, protractor: null, triangle: null };
        this.toolState = {
            ruler: { visible: false, x: 30, y: 80, angle: 0, width: 500, height: 70, scale: 1 },
            protractor: { visible: false, x: 60, y: 50, size: 350, angle: 0, scale: 1, currentAngle: 0, state: "idle" },
            triangle: { visible: false, x: 160, y: 120, size: 300, angle: 0, orientation: "bottomLeft", scale: 1 },
        };
        this.toolDrag = { active: false, type: null, zone: null, startX: 0, startY: 0, origState: null };

        // Compass (circle drawing mode)
        this.circleCenterX = 0;
        this.circleCenterY = 0;
        this.circleSnapshot = null;
        this.circleRadius = 0;

        // Calculator
        this.calcEl = null;

        this._init();
    }

    _init() {
        this._resize();
        window.addEventListener("resize", () => this._resize());
        this._bindEvents();
        this.clearCanvas();
        this._createOverlayContainer();
    }

    _createOverlayContainer() {
        this.overlayContainer = document.createElement("div");
        this.overlayContainer.style.cssText = "position:absolute;inset:0;pointer-events:none;overflow:hidden;z-index:5;";
        this.canvas.parentElement.style.position = "relative";
        this.canvas.parentElement.appendChild(this.overlayContainer);
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

        // Tool SVG events (delegated via overlay container)
        document.addEventListener("mousemove", (e) => this._onToolDrag(e));
        document.addEventListener("mouseup", (e) => this._onToolDragEnd(e));
        document.addEventListener("touchmove", (e) => { const t = e.touches[0]; if (this.toolDrag.active) { e.preventDefault(); this._onToolDrag(t); } }, { passive: false });
        document.addEventListener("touchend", (e) => this._onToolDragEnd(e));
    }

    _getPos(e) {
        const rect = this.canvas.getBoundingClientRect();
        return { x: e.clientX - rect.left, y: e.clientY - rect.top };
    }

    // ─── Display Settings ───
    setBoardMode(mode) { this.boardMode = mode; this._render(); }
    toggleGrid() { this.gridEnabled = !this.gridEnabled; this._render(); }
    setGridSpacing(px) { this.gridSpacing = Math.max(10, Math.min(200, px)); this._render(); }
    setGridLogarithmic(val) { this.gridLogarithmic = !!val; this._render(); }

    // ─── Drawing ───
    _pointerDown(e) {
        if (this.textMode) return;
        if (this.tool === "compass") { this._compassDown(e); return; }
        if (this.toolDrag.active) return;
        const pos = this._getPos(e);
        this.isDrawing = true;
        this.lastX = pos.x;
        this.lastY = pos.y;
        this.points = [[pos.x, pos.y]];
        if (this.tool === "laser") { this.laserVisible = true; this._render(); return; }
        this._saveState();
    }

    _pointerMove(e) {
        const pos = this._getPos(e);
        if (this.tool === "laser" && this.laserVisible) {
            this.lastX = pos.x; this.lastY = pos.y; this._render();
            clearTimeout(this.laserTimeout);
            this.laserTimeout = setTimeout(() => { this.laserVisible = false; this._render(); }, 2000);
            this._emitCursor(pos); return;
        }
        if (this.tool === "compass" && this.isDrawing) { this._compassMove(e); return; }
        if (!this.isDrawing) { this._emitCursor(pos); return; }

        // Constrain to ruler/triangle edge if visible
        let drawPos = pos;
        if (this.tool === "pen" && this.toolState.ruler.visible) {
            const r = this.toolState.ruler;
            const angleRad = r.angle * Math.PI / 180;
            const cx = r.x + r.width / 2, cy = r.y + r.height / 2;
            const dx = pos.x - cx, dy = pos.y - cy;
            const localX = dx * Math.cos(-angleRad) - dy * Math.sin(-angleRad);
            const localY = dx * Math.sin(-angleRad) + dy * Math.cos(-angleRad);
            if (localY > r.height / 2) drawPos = { x: pos.x, y: cy + (r.height / 2 + 1) };
            else drawPos = { x: pos.x, y: cy - (r.height / 2 + 1) };
        }

        if (this.tool === "eraser") {
            this.ctx.globalCompositeOperation = "destination-out";
            this.ctx.beginPath();
            this.ctx.arc(drawPos.x, drawPos.y, this.width + 5, 0, Math.PI * 2);
            this.ctx.fill();
            this.ctx.globalCompositeOperation = "source-over";
            this.points.push([drawPos.x, drawPos.y]);
            this._emitDraw("erase", { points: [[drawPos.x, drawPos.y]], width: this.width + 5 });
            return;
        }

        if (this.tool === "highlight") this.ctx.globalAlpha = 0.3;
        this.ctx.beginPath();
        this.ctx.moveTo(this.lastX, this.lastY);
        this.ctx.lineTo(drawPos.x, drawPos.y);
        this.ctx.strokeStyle = this.color;
        this.ctx.lineWidth = this.width;
        this.ctx.setLineDash(this.dash);
        this.ctx.lineCap = "round";
        this.ctx.lineJoin = "round";
        this.ctx.stroke();
        this.ctx.setLineDash([]);
        this.ctx.globalAlpha = 1;
        this.points.push([drawPos.x, drawPos.y]);
        this.lastX = drawPos.x;
        this.lastY = drawPos.y;
        this._emitDraw("line", { points: this.points, color: this.color, width: this.width, dash: this.dash });
    }

    _pointerUp(e) {
        if (this.tool === "compass") { this._compassUp(e); return; }
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

    // ─── Compass (Circle Drawing) ───
    _compassDown(e) {
        const pos = this._getPos(e);
        this.isDrawing = true;
        this.circleCenterX = pos.x;
        this.circleCenterY = pos.y;
        this.circleSnapshot = this.canvas.toDataURL();
    }

    _compassMove(e) {
        if (!this.isDrawing) return;
        const pos = this._getPos(e);
        const dx = pos.x - this.circleCenterX;
        const dy = pos.y - this.circleCenterY;
        this.circleRadius = Math.sqrt(dx * dx + dy * dy);
        const img = new Image();
        img.onload = () => {
            this.ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);
            this.ctx.drawImage(img, 0, 0);
            this._drawBackground();
            this.ctx.strokeStyle = this.color;
            this.ctx.lineWidth = this.width;
            this.ctx.beginPath();
            this.ctx.arc(this.circleCenterX, this.circleCenterY, this.circleRadius, 0, Math.PI * 2);
            this.ctx.stroke();
            // Dashed center line
            this.ctx.setLineDash([4, 4]);
            this.ctx.beginPath();
            this.ctx.moveTo(this.circleCenterX, this.circleCenterY);
            this.ctx.lineTo(pos.x, pos.y);
            this.ctx.stroke();
            this.ctx.setLineDash([]);
            // Radius label
            this.ctx.fillStyle = this.color;
            this.ctx.font = "12px Inter, sans-serif";
            this.ctx.fillText(`r=${Math.round(this.circleRadius)}px`, this.circleCenterX + dx / 2 + 5, this.circleCenterY + dy / 2 - 5);
        };
        img.src = this.circleSnapshot;
    }

    _compassUp(e) {
        if (!this.isDrawing) return;
        this.isDrawing = false;
        if (this.circleRadius > 5) {
            this._emitDraw("circle", {
                cx: this.circleCenterX, cy: this.circleCenterY, r: this.circleRadius,
                color: this.color, width: this.width,
            });
        }
    }

    // ─── Tool SVG Overlays ───
    toggleTool(type) {
        const st = this.toolState[type];
        if (!st) return;
        st.visible = !st.visible;
        if (st.visible) this._createToolSvg(type);
        else this._removeToolSvg(type);
    }

    _createToolSvg(type) {
        this._removeToolSvg(type);
        const st = this.toolState[type];
        const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
        svg.dataset.tool = type;
        svg.style.cssText = "position:absolute;pointer-events:auto;cursor:grab;overflow:visible;";

        const updatePos = () => {
            const w = type === "ruler" ? st.width * st.scale : type === "protractor" ? st.size : st.size;
            const h = type === "ruler" ? st.height * st.scale : type === "protractor" ? st.size : st.size;
            svg.setAttribute("viewBox", `0 0 ${w} ${h}`);
            svg.style.width = w + "px";
            svg.style.height = h + "px";
            svg.style.left = st.x + "px";
            svg.style.top = st.y + "px";
            svg.style.transform = `rotate(${st.angle}deg)`;
            svg.style.transformOrigin = `${w / 2}px ${h / 2}px`;
        };

        let content = "";
        if (type === "ruler") {
            content = `<rect width="100%" height="100%" fill="rgba(245,248,255,0.35)" stroke="rgba(0,80,200,0.6)" stroke-width="2" rx="4"/>
                ${ScanGradeTools.rulerSvg(st.width * st.scale, st.height * st.scale)}
                <circle cx="25" cy="20" r="8" fill="rgba(0,80,200,0.8)" stroke="white" stroke-width="1.5" style="cursor:move"/>
                <text x="18" y="24" font-size="10" fill="white" font-weight="bold" style="pointer-events:none">⣿</text>
                <circle cx="${st.width * st.scale - 25}" cy="20" r="8" fill="rgba(200,50,0,0.85)" stroke="white" stroke-width="1.5" style="cursor:alias"/>
                <text x="${st.width * st.scale - 30}" y="24" font-size="9" fill="white" font-weight="bold" style="pointer-events:none">↻</text>`;
        } else if (type === "protractor") {
            const cx = st.size / 2, cy = st.size * 0.92;
            content = `<rect width="100%" height="100%" fill="rgba(245,248,255,0.45)" stroke="rgba(0,80,200,0.5)" stroke-width="1.5" rx="4"/>
                ${ScanGradeTools.protractorSvg(cx, cy, st.size * 0.45)}
                <circle cx="${cx}" cy="${cy}" r="8" fill="rgba(0,80,200,0.8)" stroke="white" stroke-width="1.5" style="cursor:move"/>
                <text x="${cx - 4}" y="${cy + 4}" font-size="9" fill="white" font-weight="bold" style="pointer-events:none">+</text>
                <circle cx="${cx}" cy="${cy - st.size * 0.37}" r="7" fill="rgba(200,50,0,0.85)" stroke="white" stroke-width="1.5" style="cursor:alias"/>
                <circle cx="${st.size - 20}" cy="${st.size * 0.4}" r="6" fill="rgba(0,40,120,0.7)" stroke="white" stroke-width="1" style="cursor:ew-resize"/>`;
        } else if (type === "triangle") {
            const verts = this._getTriangleVerts(st);
            const minX = Math.min(verts[0].x, verts[1].x, verts[2].x);
            const minY = Math.min(verts[0].y, verts[1].y, verts[2].y);
            const maxX = Math.max(verts[0].x, verts[1].x, verts[2].x);
            const maxY = Math.max(verts[0].y, verts[1].y, verts[2].y);
            const bbW = maxX - minX, bbH = maxY - minY;
            svg.setAttribute("viewBox", `${minX - 5} ${minY - 5} ${bbW + 10} ${bbH + 10}`);
            svg.style.width = (bbW + 10) + "px";
            svg.style.height = (bbH + 10) + "px";
            st.x = st.x || 160; st.y = st.y || 120;
            svg.style.left = st.x + "px";
            svg.style.top = st.y + "px";
            svg.style.transform = `rotate(${st.angle}deg)`;
            svg.style.transformOrigin = `${(bbW + 10) / 2}px ${(bbH + 10) / 2}px`;
            const pts = verts.map(p => `${p.x},${p.y}`).join(" ");
            content = `<polygon points="${pts}" fill="rgba(245,248,255,0.45)" stroke="rgba(0,80,200,0.6)" stroke-width="2"/>
                ${ScanGradeTools.triangleSvg45(st.size)}
                <circle cx="${verts[2].x}" cy="${verts[2].y}" r="8" fill="rgba(0,80,200,0.8)" stroke="white" stroke-width="1.5" style="cursor:move"/>
                <circle cx="${verts[1].x}" cy="${verts[1].y}" r="7" fill="rgba(200,50,0,0.85)" stroke="white" stroke-width="1.5" style="cursor:alias"/>`;
            svg.dataset.skipTransform = "1";
        }

        if (!svg.dataset.skipTransform) updatePos();
        svg.innerHTML = content;
        svg.addEventListener("mousedown", (e) => this._onToolSvgMouseDown(type, e));
        svg.addEventListener("touchstart", (e) => this._onToolSvgMouseDown(type, e.touches[0]), { passive: false });
        this.overlayContainer.appendChild(svg);
        this.toolSvg[type] = svg;
    }

    _removeToolSvg(type) {
        if (this.toolSvg[type]) { this.toolSvg[type].remove(); this.toolSvg[type] = null; }
    }

    _getTriangleVerts(st) {
        const w = st.size, h = st.size;
        switch (st.orientation || "bottomLeft") {
            case "bottomLeft": return [{ x: 10, y: h }, { x: 10, y: 10 }, { x: w, y: h }];
            case "bottomRight": return [{ x: w - 10, y: h }, { x: w - 10, y: 10 }, { x: 10, y: h }];
            case "topLeft": return [{ x: 10, y: 10 }, { x: 10, y: h }, { x: w, y: 10 }];
            case "topRight": return [{ x: w - 10, y: 10 }, { x: w - 10, y: h }, { x: 10, y: 10 }];
        }
    }

    flipTriangle() {
        const order = ["bottomLeft", "bottomRight", "topRight", "topLeft"];
        const idx = order.indexOf(this.toolState.triangle.orientation);
        this.toolState.triangle.orientation = order[(idx + 1) % 4];
        if (this.toolState.triangle.visible) {
            this._createToolSvg("triangle");
        }
    }

    _onToolSvgMouseDown(type, e) {
        e.preventDefault();
        const svg = this.toolSvg[type];
        if (!svg) return;
        const rect = svg.getBoundingClientRect();
        const localX = e.clientX - rect.left, localY = e.clientY - rect.top;
        const st = this.toolState[type];

        // Determine zone (close, move, rotate, resize)
        let zone = "move";
        if (localX < 35 && localY < 28) zone = "close";
        else if (type === "ruler" && localX > (st.width * st.scale) - 35 && localY < 28) zone = "rotate";
        else if (type === "protractor") {
            const cx = st.size / 2, cy = st.size * 0.92;
            const dist = Math.sqrt(Math.pow(localX - cx, 2) + Math.pow(localY - cy, 2));
            if (localX > st.size - 30 && localY < st.size * 0.5) zone = "resize";
            else if (dist > st.size * 0.38 && localY < st.size * 0.5) zone = "rotate";
            else if (dist > st.size * 0.32 && localY > st.size * 0.4) zone = "marker";
        } else if (type === "triangle") {
            const verts = this._getTriangleVerts(st);
            const ra = st.orientation === "bottomLeft" || st.orientation === "topLeft" ? verts[1] : verts[0];
            if (Math.abs(localX - ra.x) < 15 && Math.abs(localY - ra.y) < 15) zone = "rotate";
            else if (localY > st.size - 25 && localX > 10 && localX < st.size - 10) zone = "flip";
        }

        if (zone === "close") { this.toggleTool(type); if (this.options.onToolToggle) this.options.onToolToggle(type, false); return; }
        if (zone === "flip") { this.flipTriangle(); return; }

        this.toolDrag = { active: true, type, zone, startX: e.clientX, startY: e.clientY, origState: Object.assign({}, st) };
        svg.style.cursor = zone === "rotate" ? "alias" : zone === "resize" ? "ew-resize" : "grabbing";
    }

    _onToolDrag(e) {
        if (!this.toolDrag.active) return;
        const { type, zone, startX, startY, origState } = this.toolDrag;
        const st = this.toolState[type];
        const dx = (e.clientX - startX) / (this.canvas.parentElement ? 1 : 1);
        const dy = (e.clientY - startY) / (this.canvas.parentElement ? 1 : 1);

        if (zone === "move") {
            st.x = origState.x + dx;
            st.y = origState.y + dy;
        } else if (zone === "rotate") {
            const svg = this.toolSvg[type];
            if (!svg) return;
            const rect = svg.getBoundingClientRect();
            const cx = rect.left + rect.width / 2, cy = rect.top + rect.height / 2;
            st.angle = origState.angle + (Math.atan2(e.clientY - cy, e.clientX - cx) - Math.atan2(startY - cy, startX - cx)) * 180 / Math.PI;
        } else if (zone === "resize" && type === "protractor") {
            st.size = Math.max(150, origState.size + dx);
        }

        if (st.visible) this._createToolSvg(type);
        if (this.options.onToolState) this.options.onToolState(type, { x: st.x, y: st.y, angle: st.angle, size: st.size, visible: st.visible });
    }

    _onToolDragEnd(e) {
        if (!this.toolDrag.active) return;
        const { type, zone } = this.toolDrag;
        if (this.toolSvg[type]) this.toolSvg[type].style.cursor = "grab";
        this.toolDrag.active = false;
    }

    // ─── Calculator ───
    toggleCalculator() {
        if (this.calcEl && this.calcEl.style.display !== "none") {
            this.calcEl.style.display = "none";
            return;
        }
        if (!this.calcEl) {
            if (typeof ScanGradeTools !== "undefined" && ScanGradeTools.createCalculator) {
                this.calcEl = ScanGradeTools.createCalculator();
                document.body.appendChild(this.calcEl);
            } else {
                alert("Kalkulator tidak tersedia");
                return;
            }
        }
        this.calcEl.style.display = "block";
    }

    // ─── Undo/Redo ───
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
        img.onload = () => { this.ctx.clearRect(0, 0, this.canvas.width, this.canvas.height); this._drawBackground(); this.ctx.drawImage(img, 0, 0); };
        img.src = prev;
    }

    redo() {
        if (this.redoStack.length === 0) return;
        this.undoStack.push(this.canvas.toDataURL());
        const next = this.redoStack.pop();
        const img = new Image();
        img.onload = () => { this.ctx.clearRect(0, 0, this.canvas.width, this.canvas.height); this._drawBackground(); this.ctx.drawImage(img, 0, 0); };
        img.src = next;
    }

    clearCanvas() {
        this._saveState();
        this.ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);
        this._drawBackground();
    }

    setBackground(url) {
        if (!url) { this.currentBg = null; this._render(); return; }
        const img = new Image();
        img.crossOrigin = "anonymous";
        img.onload = () => { this.currentBg = img; this._render(); };
        img.src = url;
    }

    setTool(tool) { this.tool = tool; this.textMode = false; this.canvas.style.cursor = tool === "text" ? "text" : tool === "compass" ? "crosshair" : "crosshair"; }

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

    // ─── Background + Grid ───
    _drawBackground() {
        // Board background
        this.ctx.fillStyle = this.boardMode === "white" ? "#FFFFFF" : "#1e293b";
        this.ctx.fillRect(0, 0, this.canvas.width, this.canvas.height);

        // Grid
        if (this.gridEnabled) {
            this.ctx.strokeStyle = this.boardMode === "white" ? "rgba(0,0,0,0.08)" : "rgba(255,255,255,0.08)";
            this.ctx.lineWidth = 0.5;
            if (this.gridLogarithmic) {
                this._drawLogGrid();
            } else {
                for (let x = 0; x <= this.canvas.width; x += this.gridSpacing) {
                    this.ctx.beginPath(); this.ctx.moveTo(x, 0); this.ctx.lineTo(x, this.canvas.height); this.ctx.stroke();
                }
                for (let y = 0; y <= this.canvas.height; y += this.gridSpacing) {
                    this.ctx.beginPath(); this.ctx.moveTo(0, y); this.ctx.lineTo(this.canvas.width, y); this.ctx.stroke();
                }
            }
        }

        // Background image
        if (this.currentBg) {
            this.ctx.drawImage(this.currentBg, 0, 0, this.canvas.width, this.canvas.height);
        }
    }

    _drawLogGrid() {
        // Logarithmic grid (base 10): major lines at 1, 10, 100, 1000... minor at 2-9 between each decade
        const maxVal = Math.max(this.canvas.width, this.canvas.height);
        const maxDecade = Math.ceil(Math.log10(maxVal));
        const pxPerDecade = maxVal / maxDecade;

        for (let decade = 0; decade < maxDecade; decade++) {
            const baseX = decade * pxPerDecade;
            // Major line at start of decade
            this.ctx.lineWidth = 1;
            this.ctx.beginPath(); this.ctx.moveTo(baseX, 0); this.ctx.lineTo(baseX, this.canvas.height); this.ctx.stroke();

            // Minor lines at 2-9 within this decade
            for (let n = 2; n <= 9; n++) {
                const pos = baseX + Math.log10(n) * pxPerDecade;
                this.ctx.lineWidth = 0.3;
                this.ctx.beginPath(); this.ctx.moveTo(pos, 0); this.ctx.lineTo(pos, this.canvas.height); this.ctx.stroke();
            }
        }
    }

    // ─── Render ───
    _render() {
        this.ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);
        this._drawBackground();
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

    // ─── Replay Ops ───
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
            this._drawBackground();
        } else if (op.op_type === "circle") {
            this.ctx.strokeStyle = d.color || "#000";
            this.ctx.lineWidth = d.width || 3;
            this.ctx.beginPath();
            this.ctx.arc(d.cx || 0, d.cy || 0, d.r || 0, 0, Math.PI * 2);
            this.ctx.stroke();
        }
    }

    loadOps(ops) {
        this._saveState();
        this.ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);
        this._drawBackground();
        for (const op of ops) {
            this.replayOp(op);
        }
    }

    // ─── Callbacks ───
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

    toDataURL() {
        return this.canvas.toDataURL("image/png");
    }
}
