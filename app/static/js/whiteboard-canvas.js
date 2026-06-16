/**
 * Whiteboard Canvas Engine v2
 * Architecture inspired by OpenBoard (Qt/C++ → JS)
 *
 * Layers (render order):
 *   1. Board background (white/black)
 *   2. Grid (linear/log)
 *   3. Background image (PDF)
 *   4. Drawing ops replay
 *   5. Compass live preview
 *   6. Laser pointer
 *
 * Tool system (per OpenBoard):
 *   - Each tool is an SVG overlay with proper local→scene transform
 *   - Hit-testing via inverse-transform of mouse position to local coords
 *   - Rotation center per tool (ruler: top-left, protractor: arc center, triangle: right-angle)
 *   - StartLine/DrawLine pattern for edge-constrained drawing
 */
class WhiteboardCanvas {
    constructor(canvasId, options = {}) {
        this.canvas = document.getElementById(canvasId);
        this.ctx = this.canvas.getContext("2d");
        this.options = options;
        this.dpr = window.devicePixelRatio || 1;

        // Drawing state
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
        this.laserVisible = false;
        this.laserTimeout = null;
        this.undoStack = [];
        this.redoStack = [];
        this.currentBg = null;
        this.remoteOps = [];

        // Display settings
        this.boardMode = "white";
        this.gridEnabled = false;
        this.gridSpacing = 50;
        this.gridLogarithmic = false;

        // Overlay container for tool SVGs
        this.overlayContainer = null;

        // Tool state (one per tool type)
        this.toolState = {
            ruler: { visible: false, x: 0, y: 0, angle: 0, w: 500, h: 70 },
            protractor: { visible: false, x: 0, y: 0, angle: 0, size: 350, curAngle: 0 },
            triangle: { visible: false, x: 0, y: 0, angle: 0, size: 300, orient: "bottomLeft" },
        };
        this.toolSvg = {};      // cached SVG elements
        this.toolDrag = null;   // { type, zone, startMX, startMY, startState, startDist, startAngle }

        // Compass (circle drawing)
        this.compassCenter = null;
        this.compassSnapshot = null;
        this.compassRadius = 0;

        // Calculator
        this.calcEl = null;

        // Active draw constraint tool
        this.activeConstraint = null; // 'ruler' | 'triangle' | null

        this._init();
    }

    // ─── Init ───
    _init() {
        this._makeOverlay();
        this._resize();
        window.addEventListener("resize", () => this._resize());
        this._bindEvents();
        this._render();
    }

    _makeOverlay() {
        if (!this.overlayContainer) {
            this.overlayContainer = document.createElement("div");
            this.overlayContainer.style.cssText = "position:absolute;inset:0;pointer-events:none;overflow:hidden;z-index:5;";
            this.canvas.parentElement.style.position = "relative";
            this.canvas.parentElement.appendChild(this.overlayContainer);
        }
    }

    _resize() {
        const rect = this.canvas.parentElement.getBoundingClientRect();
        this.canvas.width = rect.width * this.dpr;
        this.canvas.height = rect.height * this.dpr;
        this.canvas.style.width = rect.width + "px";
        this.canvas.style.height = rect.height + "px";
        this.ctx.scale(this.dpr, this.dpr);
        this._render();
    }

    _bindEvents() {
        const c = this.canvas;
        c.addEventListener("mousedown", (e) => this._onPointerDown(e));
        c.addEventListener("mousemove", (e) => this._onPointerMove(e));
        c.addEventListener("mouseup", (e) => this._onPointerUp(e));
        c.addEventListener("mouseleave", (e) => this._onPointerUp(e));
        c.addEventListener("touchstart", (e) => { e.preventDefault(); this._onPointerDown(e.touches[0]); }, { passive: false });
        c.addEventListener("touchmove", (e) => { e.preventDefault(); this._onPointerMove(e.touches[0]); }, { passive: false });
        c.addEventListener("touchend", (e) => this._onPointerUp(e), { passive: false });

        // Tool drag (document-level for smooth moves)
        document.addEventListener("mousemove", (e) => this._onToolDragMove(e));
        document.addEventListener("mouseup", (e) => this._onToolDragEnd(e));
        document.addEventListener("touchmove", (e) => { if (this.toolDrag) { e.preventDefault(); this._onToolDragMove(e.touches[0]); } }, { passive: false });
        document.addEventListener("touchend", (e) => this._onToolDragEnd(e));
    }

    _pos(e) {
        const rect = this.canvas.getBoundingClientRect();
        return { x: e.clientX - rect.left, y: e.clientY - rect.top };
    }

    // ─── Display Settings ───
    setBoardMode(m) { this.boardMode = m; this._render(); }
    toggleGrid() { this.gridEnabled = !this.gridEnabled; this._render(); }
    setGridSpacing(v) { this.gridSpacing = Math.max(10, Math.min(500, v)); this._render(); }
    setGridLogarithmic(v) { this.gridLogarithmic = !!v; this._render(); }

    // ─── Layer 1-2: Background + Grid ───
    _drawBackground() {
        const w = this.canvas.width / this.dpr;
        const h = this.canvas.height / this.dpr;

        // Board color
        this.ctx.fillStyle = this.boardMode === "white" ? "#FFFFFF" : "#1e293b";
        this.ctx.fillRect(0, 0, w, h);

        // Grid
        if (!this.gridEnabled) return;
        this.ctx.strokeStyle = this.boardMode === "white" ? "rgba(0,0,0,0.07)" : "rgba(255,255,255,0.07)";
        this.ctx.lineWidth = 0.5;

        if (this.gridLogarithmic) {
            this._drawLogGrid(w, h);
            return;
        }

        // Linear grid
        for (let x = 0; x <= w; x += this.gridSpacing) {
            this.ctx.beginPath(); this.ctx.moveTo(x, 0); this.ctx.lineTo(x, h); this.ctx.stroke();
        }
        for (let y = 0; y <= h; y += this.gridSpacing) {
            this.ctx.beginPath(); this.ctx.moveTo(0, y); this.ctx.lineTo(w, y); this.ctx.stroke();
        }
    }

    _drawLogGrid(w, h) {
        const maxDec = Math.ceil(Math.log10(Math.max(w, h)));
        const pxPerDec = Math.max(w, h) / maxDec;

        for (let d = 0; d < maxDec; d++) {
            const base = d * pxPerDec;
            this.ctx.lineWidth = 1;
            this.ctx.beginPath(); this.ctx.moveTo(base, 0); this.ctx.lineTo(base, h); this.ctx.stroke();
            for (let n = 2; n <= 9; n++) {
                const x = base + Math.log10(n) * pxPerDec;
                this.ctx.lineWidth = 0.3;
                this.ctx.beginPath(); this.ctx.moveTo(x, 0); this.ctx.lineTo(x, h); this.ctx.stroke();
            }
        }
    }

    // ─── Layer 3: Background image ───
    setBackground(url) {
        if (!url) { this.currentBg = null; this._render(); return; }
        const img = new Image();
        img.crossOrigin = "anonymous";
        img.onload = () => { this.currentBg = img; this._render(); };
        img.src = url;
    }

    // ─── Layer 4: Drawing ops ───
    replayOp(op) {
        const d = op.data || {};
        const ctx = this.ctx;

        if (op.op_type === "line") {
            const pts = d.points || [];
            if (pts.length < 2) return;
            ctx.beginPath();
            ctx.moveTo(pts[0][0], pts[0][1]);
            for (let i = 1; i < pts.length; i++) {
                ctx.lineTo(pts[i][0], pts[i][1]);
            }
            ctx.strokeStyle = d.color || "#000";
            ctx.lineWidth = d.width || 3;
            ctx.setLineDash(d.dash || []);
            ctx.lineCap = "round";
            ctx.lineJoin = "round";
            ctx.stroke();
            ctx.setLineDash([]);
        } else if (op.op_type === "text") {
            ctx.font = `${d.fontSize || 16}px ${d.fontFamily || "Inter, sans-serif"}`;
            ctx.fillStyle = d.color || "#000";
            ctx.fillText(d.text || "", d.x || 0, d.y || 0);
        } else if (op.op_type === "erase") {
            ctx.globalCompositeOperation = "destination-out";
            ctx.beginPath();
            ctx.arc(d.x || 0, d.y || 0, (d.width || 8) + 5, 0, Math.PI * 2);
            ctx.fill();
            ctx.globalCompositeOperation = "source-over";
        } else if (op.op_type === "clear") {
            // handled in loadOps
        } else if (op.op_type === "circle") {
            ctx.strokeStyle = d.color || "#000";
            ctx.lineWidth = d.width || 3;
            ctx.beginPath();
            ctx.arc(d.cx || 0, d.cy || 0, d.r || 0, 0, Math.PI * 2);
            ctx.stroke();
        }
    }

    loadOps(ops) {
        this._snapState();
        this.ctx.clearRect(0, 0, this.canvas.width / this.dpr, this.canvas.height / this.dpr);
        this._drawBackground();
        if (this.currentBg) {
            this.ctx.drawImage(this.currentBg, 0, 0, this.canvas.width / this.dpr, this.canvas.height / this.dpr);
        }
        for (const op of ops) this.replayOp(op);
    }

    // ─── Main render ───
    _render() {
        const w = this.canvas.width / this.dpr;
        const h = this.canvas.height / this.dpr;
        this.ctx.clearRect(0, 0, w, h);
        this._drawBackground();
        if (this.currentBg) {
            this.ctx.drawImage(this.currentBg, 0, 0, w, h);
        }
        for (const op of this.remoteOps) this.replayOp(op);

        if (this.laserVisible) {
            this.ctx.beginPath();
            this.ctx.arc(this.lastX, this.lastY, 10, 0, Math.PI * 2);
            this.ctx.fillStyle = "rgba(255,0,0,0.6)";
            this.ctx.fill();
            this.ctx.beginPath();
            this.ctx.arc(this.lastX, this.lastY, 4, 0, Math.PI * 2);
            this.ctx.fillStyle = "rgba(255,0,0,0.9)";
            this.ctx.fill();
        }
    }

    // ─── Pointer events ───
    _onPointerDown(e) {
        if (this.textMode) return;
        if (this.tool === "compass") { this._compassDown(e); return; }
        if (this.toolDrag) return;
        const pos = this._pos(e);

        // Check if clicking on a tool overlay
        if (this._toolHitTest(pos)) return;

        this.isDrawing = true;
        this.lastX = pos.x;
        this.lastY = pos.y;
        this.points = [[pos.x, pos.y]];

        if (this.tool === "laser") { this.laserVisible = true; this._render(); return; }
        this._snapState();
    }

    _onPointerMove(e) {
        const pos = this._pos(e);

        if (this.tool === "laser" && this.laserVisible) {
            this.lastX = pos.x; this.lastY = pos.y; this._render();
            clearTimeout(this.laserTimeout);
            this.laserTimeout = setTimeout(() => { this.laserVisible = false; this._render(); }, 2000);
            this._emitCursor(pos); return;
        }
        if (this.tool === "compass" && this.isDrawing) { this._compassMove(e); return; }
        if (!this.isDrawing) { this._emitCursor(pos); return; }

        let drawPos = this._constrainDrawPos(pos);
        const ctx = this.ctx;

        if (this.tool === "eraser") {
            ctx.globalCompositeOperation = "destination-out";
            ctx.beginPath();
            ctx.arc(drawPos.x, drawPos.y, this.width + 5, 0, Math.PI * 2);
            ctx.fill();
            ctx.globalCompositeOperation = "source-over";
            this.points.push([drawPos.x, drawPos.y]);
            this._emitDraw("erase", { points: [[drawPos.x, drawPos.y]], width: this.width + 5 });
            return;
        }

        if (this.tool === "highlight") ctx.globalAlpha = 0.3;

        ctx.beginPath();
        ctx.moveTo(this.lastX, this.lastY);
        ctx.lineTo(drawPos.x, drawPos.y);
        ctx.strokeStyle = this.color;
        ctx.lineWidth = this.width;
        ctx.setLineDash(this.dash);
        ctx.lineCap = "round";
        ctx.lineJoin = "round";
        ctx.stroke();
        ctx.setLineDash([]);
        ctx.globalAlpha = 1;

        this.points.push([drawPos.x, drawPos.y]);
        this.lastX = drawPos.x;
        this.lastY = drawPos.y;
        this._emitDraw("line", { points: this.points, color: this.color, width: this.width, dash: this.dash });
    }

    _onPointerUp(e) {
        if (this.tool === "compass") { this._compassUp(e); return; }
        if (!this.isDrawing || this.tool === "laser") {
            this.isDrawing = false;
            if (this.tool === "laser") { this.laserVisible = false; this._render(); }
            return;
        }
        this.isDrawing = false;
        if (this.points.length > 0) {
            this._emitDraw(this.tool === "eraser" ? "erase" : "line", {
                points: this.points, color: this.color, width: this.width, dash: this.dash,
            });
        }
    }

    // ─── Draw constraint (StartLine/DrawLine – OpenBoard pattern) ───
    _constrainDrawPos(pos) {
        // Ruler: lock Y to tool edge
        const rs = this.toolState.ruler;
        if (rs.visible) {
            const angleRad = rs.angle * Math.PI / 180;
            const cx = rs.x + rs.w / 2, cy = rs.y + rs.h / 2;
            const dx = pos.x - cx, dy = pos.y - cy;
            const localY = dx * Math.sin(-angleRad) + dy * Math.cos(-angleRad);
            if (Math.abs(localY) < rs.h * 1.2) {
                // Constrain to nearest edge (top or bottom in local space)
                const edgeLocalY = localY > 0 ? rs.h / 2 + 1 : -(rs.h / 2 + 1);
                const worldEdgeY = cy + (edgeLocalY) * Math.cos(angleRad);
                return { x: pos.x, y: worldEdgeY };
            }
        }
        return pos;
    }

    // ─── Tool SVG Overlays (OpenBoard-inspired) ───
    // Each tool is an SVG element positioned via left/top + transform:rotate(deg)
    // Hit-testing uses inverse-transform to local coordinates

    toggleTool(type) {
        const st = this.toolState[type];
        if (!st) return;
        st.visible = !st.visible;
        if (st.visible) {
            // Place tool in center of canvas on first show
            if (st.x === 0 && st.y === 0) {
                const w = this.canvas.width / this.dpr;
                const h = this.canvas.height / this.dpr;
                st.x = (w - (st.w || st.size)) / 2;
                st.y = (h - (st.h || st.size)) / 2;
            }
            this._buildToolSvg(type);
        } else {
            this._destroyToolSvg(type);
        }
    }

    _buildToolSvg(type) {
        this._destroyToolSvg(type);
        const st = this.toolState[type];
        const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
        svg.dataset.tool = type;
        svg.style.cssText = "position:absolute;pointer-events:auto;cursor:grab;overflow:visible;user-select:none;";

        let vbW, vbH;
        if (type === "ruler") {
            vbW = st.w; vbH = st.h;
            svg.setAttribute("viewBox", `0 0 ${vbW} ${vbH}`);
            svg.style.width = vbW + "px";
            svg.style.height = vbH + "px";
            svg.style.left = st.x + "px";
            svg.style.top = st.y + "px";
            svg.style.transform = `rotate(${st.angle}deg)`;
            svg.style.transformOrigin = `${st.w * 0.02}px ${st.h * 0.5}px`; // rotation at left edge center

            const marks = ScanGradeTools.rulerSvg(vbW, vbH);
            svg.innerHTML = `
                <rect x="0" y="0" width="${vbW}" height="${vbH}" rx="6" fill="rgba(235,242,255,0.4)" stroke="rgba(0,80,200,0.5)" stroke-width="1.5"/>
                ${marks}
                <g id="controls">
                    <rect x="0" y="0" width="${vbW}" height="${vbH}" fill="transparent" style="pointer-events:auto"/>
                    <circle cx="20" cy="${vbH/2}" r="10" fill="rgba(0,80,200,0.75)" stroke="white" stroke-width="1.5" style="cursor:move" data-zone="move"/>
                    <text x="14" y="${vbH/2+4}" font-size="10" fill="white" font-weight="bold" style="pointer-events:none">⣿</text>
                    <circle cx="${vbW-20}" cy="14" r="9" fill="rgba(200,50,0,0.8)" stroke="white" stroke-width="1.5" style="cursor:alias" data-zone="rotate"/>
                    <text x="${vbW-24}" y="18" font-size="9" fill="white" font-weight="bold" style="pointer-events:none">↻</text>
                    <circle cx="${vbW-10}" cy="${vbH/2}" r="6" fill="rgba(0,40,120,0.6)" stroke="white" stroke-width="1" style="cursor:ew-resize" data-zone="resize"/>
                    <rect x="0" y="0" width="22" height="22" rx="4" fill="rgba(200,50,50,0.7)" stroke="white" stroke-width="1" style="cursor:pointer" data-zone="close"/>
                    <text x="7" y="15" font-size="12" fill="white" font-weight="bold" style="pointer-events:none">✕</text>
                </g>`;
        } else if (type === "protractor") {
            const s = st.size;
            vbW = s; vbH = s;
            const cx = s / 2, cy = s * 0.88;
            svg.setAttribute("viewBox", `0 0 ${s} ${s}`);
            svg.style.width = s + "px";
            svg.style.height = s + "px";
            svg.style.left = st.x + "px";
            svg.style.top = st.y + "px";
            svg.style.transform = `rotate(${st.angle}deg)`;
            svg.style.transformOrigin = `${cx}px ${cy}px`;

            const marks = ScanGradeTools.protractorSvg(cx, cy, s * 0.42);
            svg.innerHTML = `
                <rect x="0" y="0" width="${s}" height="${s}" rx="4" fill="rgba(235,242,255,0.4)" stroke="rgba(0,80,200,0.4)" stroke-width="1"/>
                ${marks}
                <g id="controls">
                    <rect x="0" y="0" width="${s}" height="${s}" fill="transparent" style="pointer-events:auto"/>
                    <circle cx="${cx}" cy="${cy}" r="10" fill="rgba(0,80,200,0.75)" stroke="white" stroke-width="1.5" style="cursor:move" data-zone="move"/>
                    <text x="${cx-5}" y="${cy+4}" font-size="11" fill="white" font-weight="bold" style="pointer-events:none">+</text>
                    <circle cx="${cx}" cy="${s*0.08}" r="9" fill="rgba(200,50,0,0.8)" stroke="white" stroke-width="1.5" style="cursor:alias" data-zone="rotate"/>
                    <circle cx="${s-20}" cy="${s*0.4}" r="7" fill="rgba(0,40,120,0.6)" stroke="white" stroke-width="1" style="cursor:ew-resize" data-zone="resize"/>
                    <rect x="0" y="0" width="22" height="22" rx="4" fill="rgba(200,50,50,0.7)" stroke="white" stroke-width="1" style="cursor:pointer" data-zone="close"/>
                    <text x="7" y="15" font-size="12" fill="white" font-weight="bold" style="pointer-events:none">✕</text>
                </g>`;
        } else if (type === "triangle") {
            const s = st.size;
            const pts = this._triVerts(st);
            const minX = Math.min(pts[0].x, pts[1].x, pts[2].x) - 5;
            const minY = Math.min(pts[0].y, pts[1].y, pts[2].y) - 5;
            const maxX = Math.max(pts[0].x, pts[1].x, pts[2].x) + 5;
            const maxY = Math.max(pts[0].y, pts[1].y, pts[2].y) + 5;
            const bbW = maxX - minX, bbH = maxY - minY;
            vbW = bbW; vbH = bbH;

            svg.setAttribute("viewBox", `${minX} ${minY} ${bbW} ${bbH}`);
            svg.style.width = bbW + "px";
            svg.style.height = bbH + "px";
            svg.style.left = st.x + "px";
            svg.style.top = st.y + "px";
            svg.style.transform = `rotate(${st.angle}deg)`;
            svg.style.transformOrigin = `${pts[1].x - minX}px ${pts[1].y - minY}px`; // rotation at right-angle corner

            const poly = pts.map(p => `${p.x},${p.y}`).join(" ");
            svg.innerHTML = `
                <polygon points="${poly}" fill="rgba(235,242,255,0.4)" stroke="rgba(0,80,200,0.5)" stroke-width="1.5"/>
                <g id="controls">
                    <polygon points="${poly}" fill="transparent" style="pointer-events:auto"/>
                    <circle cx="${pts[2].x}" cy="${pts[2].y}" r="10" fill="rgba(0,80,200,0.75)" stroke="white" stroke-width="1.5" style="cursor:move" data-zone="move"/>
                    <circle cx="${pts[1].x}" cy="${pts[1].y}" r="9" fill="rgba(200,50,0,0.8)" stroke="white" stroke-width="1.5" style="cursor:alias" data-zone="rotate"/>
                    <rect x="${pts[0].x-11}" y="${pts[0].y-11}" width="22" height="22" rx="4" fill="rgba(200,50,50,0.7)" stroke="white" stroke-width="1" data-zone="close"/>
                    <text x="${pts[0].x-4}" y="${pts[0].y+4}" font-size="12" fill="white" font-weight="bold" style="pointer-events:none">✕</text>
                </g>`;
        }

        this.toolSvg[type] = svg;
        this.overlayContainer.appendChild(svg);

        // SVG event delegation via pointer-events on <g> and data-zone attributes
        svg.addEventListener("mousedown", (e) => this._onToolSvgDown(type, e));
        svg.addEventListener("touchstart", (e) => { e.preventDefault(); this._onToolSvgDown(type, e.touches[0]); }, { passive: false });
    }

    _destroyToolSvg(type) {
        if (this.toolSvg[type]) { this.toolSvg[type].remove(); this.toolSvg[type] = null; }
    }

    _triVerts(st) {
        const s = st.size;
        switch (st.orient || "bottomLeft") {
            case "bottomLeft": return [{ x: 10, y: s }, { x: 10, y: 10 }, { x: s, y: s }];
            case "bottomRight": return [{ x: s - 10, y: s }, { x: s - 10, y: 10 }, { x: 10, y: s }];
            case "topLeft": return [{ x: 10, y: 10 }, { x: 10, y: s }, { x: s, y: 10 }];
            case "topRight": return [{ x: s - 10, y: 10 }, { x: s - 10, y: s }, { x: 10, y: 10 }];
        }
    }

    flipTriangle() {
        const order = ["bottomLeft", "bottomRight", "topRight", "topLeft"];
        const idx = order.indexOf(this.toolState.triangle.orient);
        this.toolState.triangle.orient = order[(idx + 1) % 4];
        if (this.toolState.triangle.visible) this._buildToolSvg("triangle");
    }

    // ─── Tool Hit Test (OpenBoard: use local coordinates via inverse transform) ───
    _toolHitTest(pos) {
        for (const [type, st] of Object.entries(this.toolState)) {
            if (!st.visible || !this.toolSvg[type]) continue;
            const svg = this.toolSvg[type];
            const rect = svg.getBoundingClientRect();
            const canvasRect = this.canvas.getBoundingClientRect();

            // Hit test via SVG element from event — handled by SVG's own events
            // This method is for canvas-level pointerDown interception
        }
        return false;
    }

    _onToolSvgDown(type, e) {
        e.stopPropagation();
        const target = e.target;
        const zone = target?.dataset?.zone;
        if (!zone) return;

        const st = this.toolState[type];

        if (zone === "close") {
            this.toggleTool(type);
            if (this.options.onToolToggle) this.options.onToolToggle(type, false);
            return;
        }

        // Start drag/rotate/resize
        this.toolDrag = {
            type, zone,
            startX: e.clientX,
            startY: e.clientY,
            startState: { ...st, x: st.x, y: st.y, angle: st.angle, size: st.size },
        };

        // For rotate: compute initial angle from center
        if (zone === "rotate" || zone === "move") {
            const svg = this.toolSvg[type];
            const sRect = svg.getBoundingClientRect();
            const ox = sRect.left + sRect.width / 2;
            const oy = sRect.top + sRect.height / 2;
            this.toolDrag.originX = ox;
            this.toolDrag.originY = oy;
            this.toolDrag.startAngle = Math.atan2(e.clientY - oy, e.clientX - ox) * 180 / Math.PI;
            this.toolDrag.startItemAngle = st.angle;
        }

        svg.style.cursor = zone === "rotate" ? "alias" : zone === "resize" ? "ew-resize" : "grabbing";
    }

    _onToolDragMove(e) {
        if (!this.toolDrag) return;
        const { type, zone, startX, startY, startState } = this.toolDrag;
        const st = this.toolState[type];
        const dx = e.clientX - startX;
        const dy = e.clientY - startY;

        if (zone === "move") {
            st.x = startState.x + dx;
            st.y = startState.y + dy;
        } else if (zone === "rotate") {
            const angle = Math.atan2(e.clientY - this.toolDrag.originY, e.clientX - this.toolDrag.originX) * 180 / Math.PI;
            st.angle = this.toolDrag.startItemAngle + (angle - this.toolDrag.startAngle);
        } else if (zone === "resize") {
            if (type === "ruler") st.w = Math.max(150, startState.w + dx);
            else if (type === "protractor") st.size = Math.max(150, startState.size + dx);
        }

        this._updateToolSvgPos(type);
        if (this.options.onToolState) this.options.onToolState(type, { x: st.x, y: st.y, angle: st.angle });
    }

    _onToolDragEnd(e) {
        if (!this.toolDrag) return;
        const { type } = this.toolDrag;
        if (this.toolSvg[type]) this.toolSvg[type].style.cursor = "grab";
        if (this.options.onToolState) {
            const st = this.toolState[type];
            this.options.onToolState(type, { x: st.x, y: st.y, angle: st.angle, size: st.size, orient: st.orient });
        }
        this.toolDrag = null;
    }

    _updateToolSvgPos(type) {
        const svg = this.toolSvg[type];
        if (!svg) return;
        const st = this.toolState[type];
        svg.style.left = st.x + "px";
        svg.style.top = st.y + "px";
        svg.style.transform = `rotate(${st.angle}deg)`;
    }

    // ─── Compass (Circle Drawing – OpenBoard pencil-on-needle pattern) ───
    _compassDown(e) {
        const pos = this._pos(e);
        this.isDrawing = true;
        this.compassCenter = { x: pos.x, y: pos.y };
        this.compassSnapshot = this.canvas.toDataURL();
        this.compassRadius = 0;
    }

    _compassMove(e) {
        if (!this.isDrawing) return;
        const pos = this._pos(e);
        const dx = pos.x - this.compassCenter.x;
        const dy = pos.y - this.compassCenter.y;
        this.compassRadius = Math.sqrt(dx * dx + dy * dy);

        const img = new Image();
        img.onload = () => {
            const w = this.canvas.width / this.dpr;
            const h = this.canvas.height / this.dpr;
            this.ctx.clearRect(0, 0, w, h);
            this._drawBackground();
            if (this.currentBg) this.ctx.drawImage(this.currentBg, 0, 0, w, h);
            this.ctx.drawImage(img, 0, 0);
            // Draw circle
            this.ctx.strokeStyle = this.color;
            this.ctx.lineWidth = this.width;
            this.ctx.beginPath();
            this.ctx.arc(this.compassCenter.x, this.compassCenter.y, this.compassRadius, 0, Math.PI * 2);
            this.ctx.stroke();
            // Center crosshair (OpenBoard: paintCenterCross)
            this.ctx.strokeStyle = this.color;
            this.ctx.lineWidth = 1;
            this.ctx.beginPath();
            this.ctx.moveTo(this.compassCenter.x - 6, this.compassCenter.y);
            this.ctx.lineTo(this.compassCenter.x + 6, this.compassCenter.y);
            this.ctx.moveTo(this.compassCenter.x, this.compassCenter.y - 6);
            this.ctx.lineTo(this.compassCenter.x, this.compassCenter.y + 6);
            this.ctx.stroke();
            // Radius line
            this.ctx.setLineDash([4, 4]);
            this.ctx.beginPath();
            this.ctx.moveTo(this.compassCenter.x, this.compassCenter.y);
            this.ctx.lineTo(pos.x, pos.y);
            this.ctx.stroke();
            this.ctx.setLineDash([]);
            // Label
            this.ctx.fillStyle = this.color;
            this.ctx.font = "12px Inter, sans-serif";
            this.ctx.fillText(`r = ${Math.round(this.compassRadius)}px`, this.compassCenter.x + dx / 2 + 5, this.compassCenter.y + dy / 2 - 5);
        };
        img.src = this.compassSnapshot;
    }

    _compassUp(e) {
        if (!this.isDrawing) return;
        this.isDrawing = false;
        if (this.compassRadius > 5) {
            this._emitDraw("circle", {
                cx: this.compassCenter.x, cy: this.compassCenter.y, r: this.compassRadius,
                color: this.color, width: this.width,
            });
        }
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

    // ─── Undo / Redo / Clear ───
    _snapState() {
        this.undoStack.push(this.canvas.toDataURL());
        if (this.undoStack.length > 50) this.undoStack.shift();
        this.redoStack = [];
    }

    undo() {
        if (this.undoStack.length === 0) return;
        this.redoStack.push(this.canvas.toDataURL());
        const prev = this.undoStack.pop();
        const img = new Image();
        img.onload = () => {
            const w = this.canvas.width / this.dpr;
            const h = this.canvas.height / this.dpr;
            this.ctx.clearRect(0, 0, w, h);
            this._drawBackground();
            if (this.currentBg) this.ctx.drawImage(this.currentBg, 0, 0, w, h);
            this.ctx.drawImage(img, 0, 0);
        };
        img.src = prev;
    }

    redo() {
        if (this.redoStack.length === 0) return;
        this.undoStack.push(this.canvas.toDataURL());
        const next = this.redoStack.pop();
        const img = new Image();
        img.onload = () => {
            const w = this.canvas.width / this.dpr;
            const h = this.canvas.height / this.dpr;
            this.ctx.clearRect(0, 0, w, h);
            this._drawBackground();
            if (this.currentBg) this.ctx.drawImage(this.currentBg, 0, 0, w, h);
            this.ctx.drawImage(img, 0, 0);
        };
        img.src = next;
    }

    clearCanvas() {
        this._snapState();
        this.ctx.clearRect(0, 0, this.canvas.width / this.dpr, this.canvas.height / this.dpr);
        this._drawBackground();
        if (this.currentBg) {
            this.ctx.drawImage(this.currentBg, 0, 0, this.canvas.width / this.dpr, this.canvas.height / this.dpr);
        }
    }

    // ─── Tool Setters ───
    setTool(t) { this.tool = t; this.textMode = false; this.canvas.style.cursor = "crosshair"; }
    setColor(c) { this.color = c; }
    setWidth(w) { this.width = w; }
    setOpacity(o) { this.opacity = o; }
    setFontSize(s) { this.fontSize = s; }
    setDash(d) { this.dash = d; }
    clearLaser() { this.laserVisible = false; this._render(); }

    enableTextMode() {
        this.textMode = true;
        this.tool = "text";
        this.canvas.style.cursor = "text";
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
        input.style.fontFamily = this.fontFamily;
        input.style.border = "2px solid " + this.color;
        input.style.padding = "4px 8px";
        input.style.borderRadius = "6px";
        input.style.background = "#fff";
        input.style.zIndex = "99999";
        document.body.appendChild(input);
        input.focus();

        const onEnd = () => {
            const text = input.value.trim();
            if (text) {
                this._snapState();
                this.ctx.font = `${this.fontSize}px ${this.fontFamily}`;
                this.ctx.fillStyle = this.color;
                this.ctx.fillText(text, pos.x, pos.y);
                this._emitDraw("text", { text, x: pos.x, y: pos.y, color: this.color, fontSize: this.fontSize, fontFamily: this.fontFamily });
            }
            document.body.removeChild(input);
            this.textMode = false;
        };
        input.addEventListener("blur", onEnd);
        input.addEventListener("keydown", (ev) => { if (ev.key === "Enter") { ev.preventDefault(); onEnd(); } });
    }

    // ─── Callbacks ───
    _emitDraw(op_type, data) {
        if (this.options.onDraw) this.options.onDraw({ op_type, data, timestamp: Date.now() });
    }
    _emitCursor(pos) {
        if (this.options.onCursor) this.options.onCursor({ x: pos.x, y: pos.y });
    }
    toDataURL() { return this.canvas.toDataURL("image/png"); }
}
