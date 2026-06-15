/* Whiteboard Timer Overlay — set by teacher, shown to all */
class WhiteboardTimer {
    constructor(containerId, options = {}) {
        this.container = document.getElementById(containerId);
        this.options = options;
        this.seconds = 0;
        this.running = false;
        this.interval = null;
        this._render();
    }

    _render() {
        if (!this.container) return;
        this.container.innerHTML = "";
        this.container.style.cssText = `
            display: flex; align-items: center; gap: 8px;
            font-size: 14px; font-weight: 700; font-variant-numeric: tabular-nums;
        `;
    }

    setTime(seconds) {
        this.seconds = Math.max(0, seconds);
        this._update();
    }

    start() {
        if (this.running) return;
        this.running = true;
        this.interval = setInterval(() => {
            if (this.seconds > 0) {
                this.seconds--;
                this._update();
                if (this.seconds === 0) {
                    this.stop();
                    if (this.options.onTimeUp) this.options.onTimeUp();
                }
            }
        }, 1000);
        if (this.options.onSync) this.options.onSync(this.seconds, true);
    }

    stop() {
        this.running = false;
        if (this.interval) {
            clearInterval(this.interval);
            this.interval = null;
        }
        if (this.options.onSync) this.options.onSync(this.seconds, false);
    }

    reset(seconds) {
        this.stop();
        this.seconds = Math.max(0, seconds || 0);
        this._update();
    }

    _update() {
        if (!this.container) return;
        const m = String(Math.floor(this.seconds / 60)).padStart(2, "0");
        const s = String(this.seconds % 60).padStart(2, "0");
        this.container.innerHTML = `<span style="color:${this.seconds <= 60 ? "#ef4444" : "#64748b"}">${m}:${s}</span>`;

        if (this.options.onTick) this.options.onTick(this.seconds);
    }

    syncRemote(seconds, running) {
        this.seconds = Math.max(0, seconds);
        this._update();
        if (running && !this.running) {
            this.start();
        } else if (!running && this.running) {
            this.stop();
        }
    }
}
