/* Whiteboard Slide Navigator — horizontal thumbnail bar */
class WhiteboardSlides {
    constructor(containerId, options = {}) {
        this.container = document.getElementById(containerId);
        this.options = options;
        this.slides = [];
        this.current = 0;
    }

    setSlides(slides) {
        this.slides = slides;
        this.render();
    }

    render() {
        if (!this.container) return;
        this.container.innerHTML = "";

        this.slides.forEach((slide, i) => {
            const thumb = document.createElement("div");
            thumb.className = `slide-thumb ${i === this.current ? "active" : ""}`;
            thumb.style.cssText = `
                width: 80px; height: 60px; border-radius: 8px; overflow: hidden;
                cursor: pointer; flex-shrink: 0; border: 3px solid ${i === this.current ? "#3b82f6" : "#e2e8f0"};
                transition: all 0.15s; position: relative;
            `;

            if (slide.background_url) {
                const img = document.createElement("img");
                img.src = slide.background_url;
                img.style.cssText = "width:100%;height:100%;object-fit:cover;";
                thumb.appendChild(img);
            } else {
                const label = document.createElement("div");
                label.style.cssText = "width:100%;height:100%;display:flex;align-items:center;justify-content:center;font-size:11px;font-weight:600;color:#94a3b8;background:#f1f5f9;";
                label.textContent = `${i + 1}`;
                thumb.appendChild(label);
            }

            // Delete button
            if (this.options.onDelete) {
                const del = document.createElement("button");
                del.innerHTML = "&times;";
                del.style.cssText = "position:absolute;top:-6px;right:-6px;width:18px;height:18px;border-radius:50%;background:#ef4444;color:#fff;font-size:12px;border:none;cursor:pointer;display:flex;align-items:center;justify-content:center;";
                del.addEventListener("click", (e) => { e.stopPropagation(); this.options.onDelete(i); });
                thumb.appendChild(del);
            }

            thumb.addEventListener("click", () => this.goTo(i));
            this.container.appendChild(thumb);
        });
    }

    goTo(index) {
        if (index < 0 || index >= this.slides.length) return;
        this.current = index;
        this.render();
        if (this.options.onChange) this.options.onChange(index, this.slides[index]);
    }

    next() { this.goTo(this.current + 1); }
    prev() { this.goTo(this.current - 1); }
    addSlide(slide) { this.slides.push(slide); this.render(); }
    removeSlide(index) { this.slides.splice(index, 1); if (this.current >= this.slides.length) this.current = this.slides.length - 1; this.render(); }
}
