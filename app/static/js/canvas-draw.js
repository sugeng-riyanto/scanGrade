class CanvasDraw {
    constructor(canvasId, opts = {}) {
        this.canvas = document.getElementById(canvasId);
        if (!this.canvas) return;
        this.ctx = this.canvas.getContext('2d');
        this.drawing = false;
        this.tool = 'pen';
        this.color = '#000000';
        this.lineWidth = 2;
        this.eraserWidth = 24;
        this.history = [];
        this.historyIndex = -1;
        this.maxHistory = 30;
        this._resize(opts);
        this.ctx.lineCap = 'round';
        this.ctx.lineJoin = 'round';
        this._bindEvents();
        this._saveState();
    }

    _resize(opts) {
        const parent = this.canvas.parentElement;
        const w = parent ? parent.clientWidth : 400;
        if (w <= 0) { requestAnimationFrame(() => this._resize(opts)); return; }
        this.canvas.width = Math.max(w - 4, 100);
        this.canvas.height = opts.height || 120;
    }

    _bindEvents() {
        const c = this.canvas;
        const opts = { passive: false };
        c.addEventListener('mousedown', e => this._startDraw(e));
        c.addEventListener('mousemove', e => this._draw(e));
        c.addEventListener('mouseup', () => this._stopDraw());
        c.addEventListener('mouseleave', () => this._stopDraw());
        c.addEventListener('touchstart', e => { e.preventDefault(); this._startDraw(e.touches[0]); }, opts);
        c.addEventListener('touchmove', e => { e.preventDefault(); this._draw(e.touches[0]); }, opts);
        c.addEventListener('touchend', e => { e.preventDefault(); this._stopDraw(); }, opts);
    }

    _getPos(e) {
        const rect = this.canvas.getBoundingClientRect();
        return {
            x: (e.clientX - rect.left) * (this.canvas.width / rect.width),
            y: (e.clientY - rect.top) * (this.canvas.height / rect.height),
        };
    }

    _startDraw(e) {
        this.drawing = true;
        const p = this._getPos(e);
        this.lastX = p.x;
        this.lastY = p.y;
    }

    _draw(e) {
        if (!this.drawing) return;
        const p = this._getPos(e);
        if (this.tool === 'eraser') {
            this.ctx.globalCompositeOperation = 'destination-out';
            this.ctx.beginPath();
            this.ctx.arc(p.x, p.y, this.eraserWidth / 2, 0, Math.PI * 2);
            this.ctx.fill();
            this.ctx.globalCompositeOperation = 'source-over';
        } else {
            this.ctx.beginPath();
            this.ctx.moveTo(this.lastX, this.lastY);
            this.ctx.lineTo(p.x, p.y);
            this.ctx.strokeStyle = this.color;
            this.ctx.lineWidth = this.lineWidth;
            this.ctx.stroke();
        }
        this.lastX = p.x;
        this.lastY = p.y;
    }

    _stopDraw() {
        if (this.drawing) {
            this.drawing = false;
            setTimeout(() => this._saveState(), 0);
        }
    }

    _saveState() {
        this.history = this.history.slice(0, this.historyIndex + 1);
        this.history.push(this.canvas.toDataURL());
        if (this.history.length > this.maxHistory) this.history.shift();
        this.historyIndex = this.history.length - 1;
    }

    setTool(tool) { this.tool = tool; }
    setColor(color) { this.color = color; }
    setLineWidth(w) { this.lineWidth = w; }

    undo() {
        if (this.historyIndex > 0) {
            this.historyIndex--;
            this._loadState(this.history[this.historyIndex]);
        }
    }

    clear() {
        this.ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);
        this._saveState();
    }

    _loadState(dataUrl) {
        const img = new Image();
        img.onload = () => {
            this.ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);
            this.ctx.drawImage(img, 0, 0);
        };
        img.src = dataUrl;
    }

    toDataURL() { return this.canvas.toDataURL(); }

    isEmpty() {
        const d = this.ctx.getImageData(0, 0, this.canvas.width, this.canvas.height).data;
        for (let i = 3; i < d.length; i += 4) { if (d[i] !== 0) return false; }
        return true;
    }
}
