/**
 * ScanGrade Tools v2 — Ruler, Protractor, Triangle, Compass Calculator
 * Ported from OpenBoard (Qt/C++) architecture to JavaScript Canvas2D
 *
 * Key OpenBoard UX patterns implemented:
 * - State machines per tool (idle, resize, rotate, move, draw)
 * - Drawing constraint along tool edges (ruler/triangle)
 * - Zone-based cursor switching
 * - Real-time angle measurement (protractor)
 * - 4 orientations + flip (triangle)
 * - Adaptive scale markings
 * - Glass/3D gradient effects
 */

const ScanGradeTools = (function() {

  // ─── Geometry utils (port of UBGeometryUtils) ───
  const GEOM = {
    cm: 10,              // pixels per cm (dynamic, updated from context)
    mm: 1,
    millimetersPerCentimeter: 10,
    centimetersGraduationHeight: 0.25,    // fraction of tool height
    halfCentimeterGraduationHeight: 0.18,
    millimeterGraduationHeight: 0.10,
  };

  // ─── Ruler (UBGraphicsRuler port) ───
  class Ruler {
    constructor() {
      this.x = 30; this.y = 80; this.width = 500; this.height = 70;
      this.angle = 0; this.scale = 1;
      this.state = 'idle'; // idle, move, resize, rotate, draw
      this.snap = false;
      this.pixelsPerCm = 40; // default
      this.drawLineDirection = 0; // 0=bottom, 1=top
      this.strokeWidth = 2.5;
    }

    /** Generate SVG content for ruler markings */
    /** Generate SVG ruler markings in viewBox coords (30cm / 12in) */
    svgContent(vbW, vbH) {
      const w = vbW || 400, h = vbH || 65;
      const mmMax = 300; // 30cm
      const ppmm = w / mmMax;
      let s = '';
      for (let mm = 0; mm <= mmMax; mm++) {
        const x = mm * ppmm;
        const tenth = mm % 10 === 0, half = mm % 5 === 0;
        const hi = tenth ? h*0.32 : half ? h*0.22 : h*0.12;
        s += `<line x1="${x}" y1="2" x2="${x}" y2="${hi}" stroke="rgba(0,0,120,0.7)" stroke-width="${tenth?1.5:half?0.9:0.5}"/>`;
      }
      for (let cm = 0; cm <= 30; cm++) {
        s += `<text x="${cm*10*ppmm}" y="${h*0.32+18}" font-size="11" fill="rgba(0,0,120,1)" font-weight="bold" text-anchor="middle">${cm}</text>`;
      }
      // Inches (1in = 25.4mm)
      const totalIn = Math.floor(mmMax / 25.4);
      const ppin = w / totalIn;
      for (let si = 0; si <= totalIn * 8; si++) {
        const x = si * ppin / 8;
        const ei = si % 8 === 0, q = si % 4 === 0;
        s += `<line x1="${x}" y1="${h*0.5}" x2="${x}" y2="${ei?h*0.8:q?h*0.72:h*0.62}" stroke="rgba(180,50,0,0.7)" stroke-width="${ei?1.5:0.5}"/>`;
      }
      for (let i = 0; i <= totalIn; i++) {
        s += `<text x="${i*ppin}" y="${h*0.82}" font-size="11" fill="rgba(180,50,0,0.95)" font-weight="bold" text-anchor="middle">${i}″</text>`;
      }
      return s;
    }

    /** Constrain a scene point to the ruler edge (StartLine/DrawLine port) */
    constrainToEdge(scenePos, canvas, toScene) {
      // Convert scene pos to tool-local
      const angleRad = this.angle * Math.PI / 180;
      const cosA = Math.cos(-angleRad), sinA = Math.sin(-angleRad);
      const cx = this.x + this.width / 2, cy = this.y + this.height / 2;
      const dx = scenePos.x - cx, dy = scenePos.y - cy;
      const localX = dx * cosA - dy * sinA;
      const localY = dx * sinA + dy * cosA;

      // Determine which edge (top or bottom half in local coords)
      if (localY > this.height / 2) {
        this.drawLineDirection = 0; // bottom
        return { x: scenePos.x, y: cy + (this.height/2 + this.strokeWidth/2) };
      } else {
        this.drawLineDirection = 1; // top
        return { x: scenePos.x, y: cy - (this.height/2 + this.strokeWidth/2) };
      }
    }

    /** Get control zone at a given local mouse position */
    getZone(localX, localY) {
      const hw = this.width, hh = this.height;
      // Close button: top-left
      if (localX < 30 && localY < 25) return 'close';
      // Rotate button: top-right area
      if (localX > hw - 40 && localY < 25) return 'rotate';
      // Resize handle: right edge
      if (localX > hw - 15) return 'resize';
      // Body = move
      if (localX > 0 && localX < hw && localY > 0 && localY < hh) return 'move';
      return 'none';
    }

    getCursor(zone) {
      const cursors = { move: 'grab', resize: 'ew-resize', rotate: 'alias', close: 'pointer' };
      return cursors[zone] || 'default';
    }
  }

  // ─── Protractor (UBGraphicsProtractor port) ───
  class Protractor {
    constructor() {
      this.x = 60; this.y = 50; this.size = 350;
      this.angle = 0; this.scale = 1;
      this.state = 'idle';
      this.snap = false;
      this.currentAngle = 0; // marker angle
      this.startAngle = 0;   // rotation offset
      this.itemRotationAngle = 0;
      this.cursorRotationAngle = 0;
      this.radius = 170;
      this.pixelsPerCm = 40;
    }

    svgContent() {
      const cx = this.radius + 30, cy = this.radius + 30;
      const r = this.radius;
      let s = '';
      for (let d = 0; d <= 180; d++) {
        const rad = -d * Math.PI / 180; // 0° at right, 180° at left (standard)
        const cos = Math.cos(rad), sin = Math.sin(rad);
        const rOuter = r, rInner = d % 10 === 0 ? r - 25 : d % 5 === 0 ? r - 18 : r - 5;
        const sw = d % 10 === 0 ? 1.8 : d % 5 === 0 ? 1.0 : 0.5;
        s += `<line x1="${cx+cos*rOuter}" y1="${cy+sin*rOuter}" x2="${cx+cos*rInner}" y2="${cy+sin*rInner}" stroke="rgba(0,0,120,0.7)" stroke-width="${sw}"/>`;
        // Inner scale (reversed)
        const rInnerArc = r * 0.55, rInnerEnd = d % 10 === 0 ? r * 0.65 : r * 0.6;
        s += `<line x1="${cx+cos*rInnerArc}" y1="${cy+sin*rInnerArc}" x2="${cx+cos*rInnerEnd}" y2="${cy+sin*rInnerEnd}" stroke="rgba(180,50,0,0.55)" stroke-width="${d%10===0?1.2:0.4}"/>`;
      }
      // Labels (standard protractor: outer=0-180 clockwise, inner=0-180 counter-clockwise)
      for (let t = 0; t <= 180; t += 10) {
        const rad = -t * Math.PI / 180;
        const cos = Math.cos(rad), sin = Math.sin(rad);
        const lr = r * 0.75, lra = r * 0.4;
        s += `<text x="${cx+cos*(r-25)-7}" y="${cy+sin*(r-25)+4}" font-size="8" fill="rgba(0,0,120,0.9)" font-weight="bold" text-anchor="middle">${t}</text>`;
        s += `<text x="${cx+cos*lr-5}" y="${cy+sin*lr+3}" font-size="7" fill="rgba(180,50,0,0.8)" font-weight="bold" text-anchor="middle">${t}</text>`;
      }
      return s;
    }

    /** Get the angle marker line endpoint */
    getMarkerEnd() {
      const rad = -this.currentAngle * Math.PI / 180;
      const cx = this.x + this.size / 2;
      const cy = this.y + this.size * 0.92;
      return {
        x: cx + (this.radius + 20) * Math.cos(rad),
        y: cy + (this.radius + 20) * Math.sin(rad)
      };
    }

    /** Draw angle measurement on a canvas context */
    drawAngleDisplay(ctx) {
      if (this.state === 'idle') return;
      const cx = this.x + this.size / 2;
      const cy = this.y + this.size * 0.92;
      const r = this.radius * 0.45;

      // Draw marker line
      const end = this.getMarkerEnd();
      ctx.save();
      ctx.strokeStyle = 'rgba(200,50,0,0.8)';
      ctx.lineWidth = 2;
      ctx.setLineDash([5, 3]);
      ctx.beginPath();
      ctx.moveTo(cx, cy);
      ctx.lineTo(end.x, end.y);
      ctx.stroke();
      ctx.setLineDash([]);

      // Draw angle arc
      const angleRad = this.currentAngle * Math.PI / 180;
      ctx.strokeStyle = 'rgba(200,50,0,0.5)';
      ctx.lineWidth = 1.5;
      ctx.beginPath();
      ctx.arc(cx, cy, r * 0.3, -Math.PI/2, -Math.PI/2 + angleRad);
      ctx.stroke();

      // Draw angle label
      ctx.fillStyle = 'rgba(200,50,0,0.9)';
      ctx.font = 'bold 13px Inter, sans-serif';
      const labelAngle = (-Math.PI/2 + angleRad / 2);
      const lx = cx + (r * 0.4) * Math.cos(labelAngle);
      const ly = cy + (r * 0.4) * Math.sin(labelAngle);
      ctx.fillText(Math.round(this.currentAngle) + '°', lx - 12, ly + 5);
      ctx.restore();
    }

    getZone(localX, localY) {
      const cx = this.size / 2, cy = this.size * 0.92;
      const dx = localX - cx, dy = localY - cy;
      const dist = Math.sqrt(dx * dx + dy * dy);
      if (dist > this.radius + 10) return 'none';
      // Close: top-left
      if (localX < 40 && localY < 30) return 'close';
      // Resize: right edge
      if (localX > this.size - 30 && localY < this.size * 0.5) return 'resize';
      // Rotate: top-right
      if (localX > this.size - 60 && localY < 30) return 'rotate';
      // Marker zone: near the arc edge
      if (dist > this.radius * 0.7) return 'marker';
      // Body
      return 'move';
    }

    getCursor(zone) {
      const cursors = { move: 'grab', resize: 'ew-resize', rotate: 'alias', marker: 'crosshair', close: 'pointer' };
      return cursors[zone] || 'default';
    }
  }

  // ─── Triangle (UBGraphicsTriangle port) ───
  class Triangle {
    constructor() {
      this.x = 160; this.y = 120; this.size = 300;
      this.angle = 0; this.scale = 1;
      this.state = 'idle';
      this.snap = false;
      this.orientation = 'bottomLeft'; // bottomLeft, bottomRight, topLeft, topRight
      this.pixelsPerCm = 40;
      this.width = 270; this.height = 270; // triangle bounding box
    }

    /** Get triangle vertices based on orientation */
    getVertices() {
      const w = this.size, h = this.size;
      switch (this.orientation) {
        case 'bottomLeft':  return [{x:10,y:h}, {x:10,y:10}, {x:w,y:h}];
        case 'bottomRight': return [{x:w-10,y:h}, {x:w-10,y:10}, {x:10,y:h}];
        case 'topLeft':     return [{x:10,y:10}, {x:10,y:h}, {x:w,y:10}];
        case 'topRight':    return [{x:w-10,y:10}, {x:w-10,y:h}, {x:10,y:10}];
      }
    }

    svgContent() {
      const verts = this.getVertices();
      const [A, B, C] = [verts[0], verts[1], verts[2]];
      let s = '';

      // Grid lines parallel to base
      const steps = Math.floor(this.size / 12);
      for (let t = 1; t < steps; t++) {
        const frac = t / steps;
        const x1 = A.x + (C.x - A.x) * frac;
        const y1 = A.y + (C.y - A.y) * frac;
        const x2 = B.x + (C.x - B.x) * frac;
        const y2 = B.y + (C.y - B.y) * frac;
        s += `<line x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}" stroke="rgba(0,0,120,0.12)" stroke-width="0.5"/>`;
      }

      // Scale markings along edges
      const markEdge = (p1, p2, isVert) => {
        const d = Math.sqrt(Math.pow(p2.x-p1.x,2)+Math.pow(p2.y-p1.y,2));
        const numMarks = Math.floor(d / 12);
        for (let i = 0; i <= numMarks; i++) {
          const frac = i / numMarks;
          const mx = p1.x + (p2.x - p1.x) * frac;
          const my = p1.y + (p2.y - p1.y) * frac;
          const h = i % 5 === 0 ? 10 : 5;
          const perp = isVert ? 1 : -1;
          const nx = isVert ? -1 : 0;
          const ny = isVert ? 0 : 1;
          s += `<line x1="${mx}" y1="${my}" x2="${mx + nx * h}" y2="${my + ny * h}" stroke="rgba(0,0,120,0.5)" stroke-width="${i%5===0?1:0.4}"/>`;
          if (i % 5 === 0) {
            s += `<text x="${mx-4}" y="${my-3}" font-size="6" fill="rgba(0,0,120,0.65)" font-weight="bold">${Math.round(i/5*2.5)}</text>`;
          }
        }
      };

      markEdge(verts[0], verts[1], true);  // vertical side
      markEdge(verts[0], verts[2], false); // bottom side

      // 90° corner marker
      const minX = Math.min(A.x, B.x, C.x);
      const maxX = Math.max(A.x, B.x, C.x);
      const maxY = Math.max(A.y, B.y, C.y);
      if (this.orientation === 'bottomLeft' || this.orientation === 'bottomRight') {
        s += `<path d="M ${minX+12},${maxY} L ${minX+12},${maxY-12} L ${minX},${maxY-12}" fill="none" stroke="rgba(200,50,0,0.5)" stroke-width="1.5"/>`;
      }

      // 45° label
      s += `<text x="${this.size*0.45}" y="${this.size*0.55}" font-size="8" fill="rgba(0,0,120,0.5)" font-weight="bold" transform="rotate(-45,${this.size*0.45},${this.size*0.55})">45°</text>`;

      return s;
    }

    /** Constrain point to triangle edge for drawing (StartLine port) */
    constrainToEdge(scenePos, canvas, toScene) {
      // Simplified: snap to nearest edge
      const verts = this.getVertices();
      const edges = [[verts[0], verts[1]],[verts[1], verts[2]],[verts[0], verts[2]]];
      let minDist = Infinity, bestPt = null;
      for (const [p1, p2] of edges) {
        const dx = p2.x - p1.x, dy = p2.y - p1.y;
        const len = Math.sqrt(dx*dx+dy*dy);
        const t = Math.max(0, Math.min(1, ((scenePos.x-p1.x)*dx + (scenePos.y-p1.y)*dy) / (len*len)));
        const px = p1.x + t * dx, py = p1.y + t * dy;
        const d = Math.sqrt(Math.pow(scenePos.x-px,2)+Math.pow(scenePos.y-py,2));
        if (d < minDist) { minDist = d; bestPt = {x:px, y:py}; }
      }
      return bestPt || scenePos;
    }

    /** Get triangle bounding box */
    getBBox() {
      const verts = this.getVertices();
      return {
        x: Math.min(verts[0].x, verts[1].x, verts[2].x),
        y: Math.min(verts[0].y, verts[1].y, verts[2].y),
        w: Math.max(verts[0].x, verts[1].x, verts[2].x) - Math.min(verts[0].x, verts[1].x, verts[2].x),
        h: Math.max(verts[0].y, verts[1].y, verts[2].y) - Math.min(verts[0].y, verts[1].y, verts[2].y),
      };
    }

    getZone(localX, localY) {
      const bb = this.getBBox();
      // Close: top-left
      if (localX < bb.x + 25 && localY < bb.y + 25) return 'close';
      // Resize: at acute corner
      if (Math.abs(localX - (bb.x + bb.w)) < 15 && Math.abs(localY - (bb.y + bb.h)) < 35) return 'resize';
      // Rotate: near right-angle corner
      const verts = this.getVertices();
      const raCorner = this.orientation === 'bottomLeft' || this.orientation === 'topLeft' ? verts[1] : verts[0];
      if (Math.abs(localX - raCorner.x) < 20 && Math.abs(localY - raCorner.y) < 20) return 'rotate';
      // Flip: bottom area
      if (localY > bb.y + bb.h - 25 && localX > bb.x && localX < bb.x + bb.w) return 'flip';
      // Body
      if (localX >= bb.x && localX <= bb.x+bb.w && localY >= bb.y && localY <= bb.y+bb.h) return 'move';
      return 'none';
    }

    getCursor(zone) {
      const cursors = { move: 'grab', resize: 'nwse-resize', rotate: 'alias', flip: 'pointer', close: 'pointer' };
      return cursors[zone] || 'default';
    }

    flip() {
      const order = ['bottomLeft', 'bottomRight', 'topRight', 'topLeft'];
      const idx = order.indexOf(this.orientation);
      this.orientation = order[(idx + 1) % 4];
    }
  }

  // ─── Calculator (standalone) ───
  function createCalculator(state = {}) {
    const s = Object.assign({
      visible: false, input: '0', result: 0, op: null, prev: 0,
      newEntry: true, expression: '', angleMode: 'DEG', memVal: 0, hasMem: false
    }, state);

    const el = document.createElement('div');
    el.className = 'sg-calculator';
    el.innerHTML = `
      <div class="calc-body">
        <div class="calc-header"><span class="calc-title">📐 Scientific</span>
          <button class="calc-angle-btn" data-angle>${s.angleMode}</button>
          <button class="calc-close" data-close>&times;</button>
        </div>
        <div class="calc-display"><div class="calc-expr">${s.expression || ' '}</div><div class="calc-val">${s.input}</div></div>
        <div class="calc-grid">
          <button data-sci="sin">sin</button><button data-sci="cos">cos</button><button data-sci="tan">tan</button>
          <button data-sci="log">log</button><button data-sci="ln">ln</button>
          <button data-sci="sq">x²</button><button data-sci="cube">x³</button><button data-op="pow">x^y</button>
          <button data-const="pi">π</button><button data-const="e">e</button>
          <button data-mem="MC">MC</button><button data-mem="MR">MR</button>
          <button data-mem="M+">M+</button><button data-mem="M-">M-</button><button data-paren="(">(</button>
          <button data-clear="all">C</button><button data-clear="entry">CE</button><button data-paren=")">)</button>
          <button data-op="÷">÷</button>
          <button data-num="7">7</button><button data-num="8">8</button><button data-num="9">9</button><button data-op="×">×</button>
          <button data-num="4">4</button><button data-num="5">5</button><button data-num="6">6</button><button data-op="-">−</button>
          <button data-num="1">1</button><button data-num="2">2</button><button data-num="3">3</button><button data-op="+">+</button>
          <button data-num="0">0</button><button data-num=".">.</button><button data-op="pm">+/−</button>
          <button data-equals class="calc-eq">=</button>
        </div>
      </div>`;

    const updateDisplay = () => {
      el.querySelector('.calc-expr').textContent = s.expression || ' ';
      el.querySelector('.calc-val').textContent = s.input;
    };
    const degRad = d => d * Math.PI / 180;
    const getVal = () => parseFloat(s.input) || 0;
    const setVal = v => { s.input = String(v); s.result = v; updateDisplay(); };
    const calcEquals = () => {
      const cur = getVal();
      let r = 0;
      s.expression = (s.expression || s.prev) + ' ' + cur + ' =';
      if (s.op === '+') r = s.prev + cur;
      else if (s.op === '-') r = s.prev - cur;
      else if (s.op === '×') r = s.prev * cur;
      else if (s.op === '÷') r = cur !== 0 ? s.prev / cur : 0;
      else if (s.op === 'pow') r = Math.pow(s.prev, cur);
      else r = cur;
      s.input = String(r); s.result = r; s.op = null; s.newEntry = true;
    };

    el.addEventListener('click', e => {
      const t = e.target;
      if (t.matches('[data-close]')) { s.visible = false; el.style.display = 'none'; return; }
      if (t.matches('[data-angle]')) {
        s.angleMode = s.angleMode === 'DEG' ? 'RAD' : 'DEG';
        t.textContent = s.angleMode; return;
      }
      if (t.matches('[data-num]')) {
        const v = t.dataset.num;
        if (s.newEntry) { s.input = ''; s.newEntry = false; s.expression = ''; }
        if (v === '.' && s.input.includes('.')) return;
        s.input += v; s.result = getVal(); updateDisplay(); return;
      }
      if (t.matches('[data-op]')) {
        const op = t.dataset.op;
        if (op === 'pm') { setVal(-getVal()); return; }
        if (op === 'pow') { s.prev = getVal(); s.op = 'pow'; s.newEntry = true; s.expression = s.prev + ' ^ '; updateDisplay(); return; }
        if (s.op) calcEquals();
        s.prev = getVal(); s.op = op; s.newEntry = true;
        const sym = { '+':' + ', '-':' − ', '×':' × ', '÷':' ÷ ' };
        s.expression = s.prev + (sym[op] || ' ' + op + ' '); updateDisplay(); return;
      }
      if (t.matches('[data-equals]')) { calcEquals(); updateDisplay(); return; }
      if (t.matches('[data-sci]')) {
        const name = t.dataset.sci, v = getVal();
        let r = 0;
        if (name === 'sin') r = Math.sin(s.angleMode === 'DEG' ? degRad(v) : v);
        else if (name === 'cos') r = Math.cos(s.angleMode === 'DEG' ? degRad(v) : v);
        else if (name === 'tan') r = Math.tan(s.angleMode === 'DEG' ? degRad(v) : v);
        else if (name === 'log') r = v > 0 ? Math.log10(v) : 0;
        else if (name === 'ln') r = v > 0 ? Math.log(v) : 0;
        else if (name === 'sq') r = v * v;
        else if (name === 'cube') r = v * v * v;
        s.expression = name + '(' + v + ') ='; setVal(r); s.newEntry = true; updateDisplay(); return;
      }
      if (t.matches('[data-const]')) { setVal(t.dataset.const === 'pi' ? Math.PI : Math.E); s.expression = t.dataset.const === 'pi' ? 'π' : 'e'; s.newEntry = true; updateDisplay(); return; }
      if (t.matches('[data-paren]')) { s.expression += ' ' + t.dataset.paren + ' '; updateDisplay(); return; }
      if (t.matches('[data-mem]')) {
        const opm = t.dataset.mem, cur = getVal();
        if (opm === 'MC') { s.memVal = 0; s.hasMem = false; }
        else if (opm === 'MR') { if (s.hasMem) setVal(s.memVal); }
        else if (opm === 'M+') { s.memVal += cur; s.hasMem = true; }
        else if (opm === 'M-') { s.memVal -= cur; s.hasMem = true; }
        return;
      }
      if (t.matches('[data-clear]')) {
        s.input = '0'; s.result = 0;
        if (t.dataset.clear === 'all') { s.op = null; s.prev = 0; s.expression = ''; }
        updateDisplay();
      }
    });
    return el;
  }

  // ─── Public API ───
  return {
    Ruler,
    Protractor,
    Triangle,
    createCalculator,
    // Static SVG generators for backward compat
    rulerSvg(vbW, vbH) { return new Ruler().svgContent(vbW, vbH); },
    protractorSvg(cx, cy, outerR) {
      // Generate protractor SVG with explicit center/radius (bypass class coords)
      const r = outerR || 175;
      let s = '';
      for (let d = 0; d <= 180; d++) {
        const rad = -d * Math.PI / 180;
        const cos = Math.cos(rad), sin = Math.sin(rad);
        const r1 = r, r2 = d % 10 === 0 ? r - 25 : d % 5 === 0 ? r - 18 : r - 5;
        s += `<line x1="${cx+cos*r1}" y1="${cy+sin*r1}" x2="${cx+cos*r2}" y2="${cy+sin*r2}" stroke="rgba(0,0,120,0.7)" stroke-width="${d%10===0?1.8:1}"/>`;
        const r3 = r*0.52, r4 = d % 10 === 0 ? r*0.62 : r*0.55;
        s += `<line x1="${cx+cos*r3}" y1="${cy+sin*r3}" x2="${cx+cos*r4}" y2="${cy+sin*r4}" stroke="rgba(180,50,0,0.55)" stroke-width="${d%10===0?1.2:0.5}"/>`;
      }
      for (let t = 0; t <= 180; t += 10) {
        const rad = -t * Math.PI / 180;
        const cos = Math.cos(rad), sin = Math.sin(rad);
        s += `<text x="${cx+cos*(r-28)}" y="${cy+sin*(r-28)+6}" font-size="14" fill="rgba(0,0,120,1)" font-weight="bold" text-anchor="middle">${t}°</text>`;
        s += `<text x="${cx+cos*(r*0.5)}" y="${cy+sin*(r*0.5)+5}" font-size="11" fill="rgba(180,50,0,0.85)" font-weight="bold" text-anchor="middle">${180-t}°</text>`;
      }
      return s;
    },
    triangleSvg45(origin, size) { return new Triangle().svgContent(); },
    rulerFromState(st) { const r = new Ruler(); Object.assign(r, st); return r; },
    protractorFromState(st) { const p = new Protractor(); Object.assign(p, st); return p; },
    triangleFromState(st) { const t = new Triangle(); Object.assign(t, st); return t; },
  };
})();

// ─── Calculator styles (injected once) ───
(function() {
  if (document.getElementById('sg-calc-style')) return;
  const style = document.createElement('style');
  style.id = 'sg-calc-style';
  style.textContent = `
.sg-calculator { position:fixed; bottom:80px; right:16px; z-index:9999; width:280px; background:#1e293b; border-radius:16px; box-shadow:0 8px 32px rgba(0,0,0,0.5); border:1px solid #334155; overflow:hidden; user-select:none; }
.sg-calculator .calc-header { display:flex; align-items:center; justify-content:space-between; padding:6px 12px; background:#0f172a; border-bottom:1px solid #334155; }
.sg-calculator .calc-title { font-size:11px; font-weight:800; color:#e2e8f0; }
.sg-calculator .calc-angle-btn { font-size:9px; font-weight:800; padding:2px 8px; border-radius:4px; border:none; cursor:pointer; background:#334155; color:#94a3b8; }
.sg-calculator .calc-angle-btn:hover { background:#475569; }
.sg-calculator .calc-close { font-size:14px; color:#64748b; cursor:pointer; background:none; border:none; padding:0 4px; }
.sg-calculator .calc-close:hover { color:#ef4444; }
.sg-calculator .calc-display { background:#0f172a; padding:8px 12px 6px; text-align:right; min-height:48px; }
.sg-calculator .calc-expr { font-size:10px; color:#64748b; font-family:monospace; min-height:14px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.sg-calculator .calc-val { font-size:20px; font-weight:700; color:#f8fafc; font-family:monospace; letter-spacing:1px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.sg-calculator .calc-grid { display:grid; grid-template-columns:repeat(5,1fr); gap:1px; padding:4px; background:#1e293b; }
.sg-calculator .calc-grid button { padding:8px 2px; font-size:11px; font-weight:700; border:none; border-radius:4px; cursor:pointer; background:#334155; color:#e2e8f0; transition:background 0.1s; }
.sg-calculator .calc-grid button:hover { background:#475569; }
.sg-calculator .calc-grid button.calc-eq { background:#f97316; color:white; grid-column:span 2; }
.sg-calculator .calc-grid button.calc-eq:hover { background:#ea580c; }
.sg-calculator .calc-grid button[data-sci] { background:#1e3a5f; color:#38bdf8; font-size:10px; }
.sg-calculator .calc-grid button[data-const] { background:#1e293b; color:#fbbf24; }
.sg-calculator .calc-grid button[data-op] { background:#451a03; color:#fb923c; }
.sg-calculator .calc-grid button[data-clear] { background:#450a0a; color:#fca5a5; }
.sg-calculator .calc-grid button[data-mem] { background:#1e293b; color:#94a3b8; font-size:9px; }
.sg-calculator .calc-grid button[data-paren] { background:#1e293b; color:#cbd5e1; }
  `;
  document.head.appendChild(style);
})();
