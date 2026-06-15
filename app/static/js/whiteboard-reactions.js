/* Whiteboard Quick Reactions — emoji bubbles */
class WhiteboardReactions {
    constructor(containerId, options = {}) {
        this.container = document.getElementById(containerId);
        this.options = options;
        this.emojis = ["\u{1F44D}", "\u2753", "\u{1F680}"]; // thumbs up, question, rocket
        this.activeBubbles = [];
        this._render();
    }

    _render() {
        if (!this.container) return;
        this.container.innerHTML = "";
        this.emojis.forEach((emoji) => {
            const btn = document.createElement("button");
            btn.textContent = emoji;
            btn.style.cssText = `
                width: 40px; height: 40px; border-radius: 50%; border: none;
                background: var(--bg-card, #fff); font-size: 20px; cursor: pointer;
                box-shadow: 0 2px 8px rgba(0,0,0,0.1); transition: transform 0.15s;
            `;
            btn.addEventListener("mouseenter", () => { btn.style.transform = "scale(1.2)"; });
            btn.addEventListener("mouseleave", () => { btn.style.transform = "scale(1)"; });
            btn.addEventListener("click", () => {
                if (this.options.onReaction) this.options.onReaction(emoji);
                this._showBubble(emoji, "Anda");
            });
            this.container.appendChild(btn);
        });
    }

    showRemoteReaction(emoji, userName) {
        this._showBubble(emoji, userName);
    }

    _showBubble(emoji, userName) {
        const bubble = document.createElement("div");
        bubble.style.cssText = `
            position: fixed; bottom: 100px; right: 20px; z-index: 9999;
            display: flex; align-items: center; gap: 8px; padding: 8px 16px;
            background: var(--bg-card, #fff); border-radius: 20px;
            box-shadow: 0 4px 12px rgba(0,0,0,0.15);
            font-size: 14px; font-weight: 600; animation: slideUp 0.25s ease-out;
        `;
        bubble.innerHTML = `<span style="font-size:24px">${emoji}</span> ${userName}`;
        document.body.appendChild(bubble);
        this.activeBubbles.push(bubble);
        setTimeout(() => {
            bubble.style.opacity = "0";
            bubble.style.transition = "opacity 0.3s";
            setTimeout(() => { document.body.removeChild(bubble); }, 300);
        }, 2500);
    }
}
