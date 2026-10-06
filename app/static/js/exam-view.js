/* Reading comfort on the pupil exam page: a text-size control and an image
 * lightbox, both of which restyle or overlay what is already on the page.
 *
 * The two things this file deliberately does **not** do are the point of it:
 *
 *   * It never touches the browser's own zoom, the viewport, or the `<meta
 *     name="viewport">` tag. WCAG 1.4.4 and 1.4.10 forbid switching pinch-zoom
 *     off, and a pupil who needs to magnify is exactly the pupil least able to
 *     fix a control that broke it. Pressing A+ here resizes the paper; a pinch
 *     still zooms the browser on top of that.
 *   * It raises **no event the anti-cheat ladder watches**. The ladder treats a
 *     window `resize`, a `fullscreenchange` and a `visibilitychange` as things
 *     that happened *to the exam* — so a content zoom that produced one would
 *     charge a pupil for enlarging the text. A CSS custom property produces none
 *     of them, which is why the scale is a property and not a `transform` or a
 *     re-layout of the window. See `tests/unit/test_exam_view_zoom.py`.
 *
 * The scale is one of a closed set of steps, not a free number, because the
 * control is two buttons rather than a slider: a value between two steps could
 * not be reached by pressing either one. `app/services/user_preferences.py`
 * carries the same list.
 *
 * Everything the node harness drives is a pure function on plain arguments, so
 * what `tests/unit/test_exam_view_zoom.py` checks is the number the property is
 * set to and the fact that no other surface was touched — not the shape of the
 * source.
 */
(function (root) {
    'use strict';

    //: The steps A-/A+ move through. 1.0 is the paper as the teacher drew it.
    var STEPS = [0.85, 1.0, 1.15, 1.3, 1.5];
    var DEFAULT = 1.0;

    //: The custom property the exam stage reads. One property, so a scale is a
    //: restyle of elements that already exist.
    var PROPERTY = '--sg-exam-scale';

    //: The per-device cache. The profile copy (see `user_preferences`) wins when
    //: there is one; this is what an anonymous or offline page falls back to.
    var STORAGE_KEY = 'sg_exam_text_scale';

    //: The key this choice is stored under on the profile.
    var PREF_KEY = 'text_scale';

    /* The index of an exact step, or -1. Compared with a tolerance because the
     * value may have round-tripped through JSON, `localStorage` (always a string)
     * and float formatting on the way here. */
    function indexOf(scale) {
        var n = Number(scale);
        if (!isFinite(n)) return -1;
        for (var i = 0; i < STEPS.length; i++) {
            if (Math.abs(n - STEPS[i]) < 1e-9) return i;
        }
        return -1;
    }

    /* A step the control can reach, or the default.
     *
     * A stored or server value that is not one of the steps is *not* adopted:
     * the page would then show a scale neither button could have produced, and
     * the first press would jump somewhere the pupil did not ask for. */
    function normalize(scale) {
        var i = indexOf(scale);
        return i === -1 ? DEFAULT : STEPS[i];
    }

    /* One press, clamped at both ends. At the last step it returns the same value
     * it was given, which is how the caller knows the button is already spent and
     * should stay disabled. */
    function next(scale, direction) {
        var i = indexOf(scale);
        if (i === -1) i = indexOf(DEFAULT);
        var d = Number(direction) < 0 ? -1 : 1;
        var j = i + d;
        if (j < 0) j = 0;
        if (j > STEPS.length - 1) j = STEPS.length - 1;
        return STEPS[j];
    }

    /* Set the scale on one element, by setting the property that element reads.
     *
     * It writes to the element it is handed and to nothing else — no viewport
     * edit, no `window.dispatchEvent`, no fullscreen call, no `outerWidth`
     * read. That restraint is the contract the anti-cheat ladder depends on, so
     * it is asserted rather than assumed. */
    function apply(element, scale) {
        if (!element || !element.style || typeof element.style.setProperty !== 'function') {
            return false;
        }
        element.style.setProperty(PROPERTY, String(normalize(scale)));
        return true;
    }

    /* The image lightbox: an overlay inside this page.
     *
     * A magnified diagram is read here. No new tab is opened and the viewport is
     * not zoomed, so the paper behind the overlay is untouched and the ladder
     * sees nothing. */
    function openImage(overlay, image, src) {
        if (!overlay || !image || !src) return false;
        image.src = src;
        overlay.classList.remove('hidden');
        overlay.setAttribute('aria-hidden', 'false');
        return true;
    }

    function closeImage(overlay) {
        if (!overlay) return false;
        overlay.classList.add('hidden');
        overlay.setAttribute('aria-hidden', 'true');
        return true;
    }

    /* Open the overlay for any element carrying `attribute` (default
     * `data-lightbox`), by delegation, so questions drawn after this call are
     * covered too. The close button, a click on the backdrop and Escape all
     * close it; nothing else is bound. */
    function wireLightbox(parts) {
        parts = parts || {};
        var overlay = parts.overlay, image = parts.image, close = parts.close,
            container = parts.container, attr = parts.attribute || 'data-lightbox';
        if (!overlay || !image || !container) return false;
        if (typeof container.addEventListener !== 'function') return false;
        if (typeof document === 'undefined') return false;

        container.addEventListener('click', function (event) {
            var node = event.target;
            while (node && node !== container) {
                if (node.getAttribute && node.getAttribute(attr) !== null) {
                    var src = node.getAttribute(attr) || node.getAttribute('src');
                    if (src) openImage(overlay, image, src);
                    return;
                }
                node = node.parentNode;
            }
        });
        if (close && typeof close.addEventListener === 'function') {
            close.addEventListener('click', function () { closeImage(overlay); });
        }
        overlay.addEventListener('click', function (event) {
            if (event.target === overlay) closeImage(overlay);
        });
        document.addEventListener('keydown', function (event) {
            if (event.key === 'Escape' || event.keyCode === 27) closeImage(overlay);
        });
        return true;
    }

    root.SGExamView = {
        STEPS: STEPS,
        DEFAULT: DEFAULT,
        PROPERTY: PROPERTY,
        STORAGE_KEY: STORAGE_KEY,
        PREF_KEY: PREF_KEY,
        indexOf: indexOf,
        normalize: normalize,
        next: next,
        apply: apply,
        openImage: openImage,
        closeImage: closeImage,
        wireLightbox: wireLightbox
    };
})(typeof window !== 'undefined' ? window : this);
