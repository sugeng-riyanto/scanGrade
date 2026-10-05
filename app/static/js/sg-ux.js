/* ScanGrade — the shared "the system is working" feedback.
 *
 * The complaint this answers is not that the site is slow; it is that the site
 * *looks* stopped. A form submit, an upload or a scan can take seconds on a
 * school connection, and until this file existed the only sign anything was
 * happening was the browser's own tab spinner — which is outside the page, off
 * to the side, and easy to miss on a phone. A teacher who cannot see that their
 * paper is being saved clicks again.
 *
 * So every async action in the app gets one of two signals, both defined here
 * rather than page by page:
 *
 *   1. **A top progress bar** — the thin line that fills across the top of the
 *      window (the pattern YouTube, GitHub and Instagram use). It appears while
 *      any tracked request is in flight and finishes when the last one settles.
 *      Nothing in a page has to know about it.
 *
 *   2. **A busy button** — the control that started the work keeps its label,
 *      gains a spinner and stops accepting clicks, so the same form cannot be
 *      submitted twice. This is opt-in: mark the control (or the form that owns
 *      it) with `data-sg-busy`, because a page that already manages its own
 *      button state — the exam page's submit does — must not have this file
 *      overwrite it.
 *
 * Loaded `defer` from base.html, same as Chart.js, so it never blocks paint and
 * runs before DOMContentLoaded.
 *
 * Deliberately dependency-free and defensive: nothing here may be able to throw
 * into a page's own script, because this file's job is to help a page that is
 * already on a slow connection. Every hook is wrapped, and the fetch/XHR
 * instrumentation only wraps them when they exist.
 */
(function () {
    'use strict';

    if (window.sgBusy) return;   // idempotent, however this file gets included

    var ACCENT = 'var(--sb-thumb-active, #2563eb)';
    //: A request can finish faster than a bar can be perceived. Hiding it
    //: instantly makes the page flicker; holding it briefly reads as one
    //: deliberate action instead of two.
    var MIN_VISIBLE_MS = 300;
    //: How long a request may run before the bar appears at all. This is the
    //: difference between feedback and noise: the exam page syncs a draft every
    //: ~12 seconds while a pupil writes, and a bar that flashed on each of those
    //: would be worse than none. Anything still running after this is the thing
    //: the reader is actually waiting for.
    var DELAY_MS = 350;

    var bar = null;
    var depth = 0;              // in-flight tracked requests
    var visible = false;        // the bar is on screen
    var shownAt = 0;
    var showTimer = null;
    var growTimer = null;
    var hideTimer = null;

    function body() {
        return document.body || document.documentElement;
    }

    function makeBar() {
        if (bar && bar.isConnected) return bar;
        bar = document.createElement('div');
        bar.id = 'sg-progress';
        bar.setAttribute('role', 'progressbar');
        bar.setAttribute('aria-hidden', 'true');
        var s = bar.style;
        s.position = 'fixed';
        s.top = '0';
        s.left = '0';
        s.height = '3px';
        s.width = '0%';
        s.background = ACCENT;
        // The layer, from the same scale every other panel names (see
        // app/static/css/theme.css): above the header and the drawers, below a
        // dialog. Written as the literal token rather than a constant so the
        // stacking height is readable here, beside the `position: fixed` it
        // applies to — `tests/unit/test_layer_scale.py` reads this statement, and
        // a height hidden behind a variable name is a height a reader cannot see.
        s.zIndex = 'var(--sg-layer-notice, 200)';
        s.pointerEvents = 'none';
        s.opacity = '0';
        s.transition = 'width 200ms ease-out, opacity 250ms ease-out';
        s.borderRadius = '0 2px 2px 0';
        body().appendChild(bar);
        return bar;
    }

    function grow() {
        // Creep toward 90% and stop there: the bar must never claim to be
        // finished while a request is still open. The last 10% is the response
        // arriving, which is the only thing that can honestly fill it.
        if (growTimer) return;
        growTimer = setInterval(function () {
            if (!bar) return;
            var now = parseFloat(bar.style.width) || 0;
            if (now >= 90) return;
            var step = now < 40 ? 4 : (now < 70 ? 1.5 : 0.6);
            bar.style.width = (now + step) + '%';
        }, 250);
    }

    function reveal() {
        showTimer = null;
        if (depth <= 0 || visible) return;
        var el = makeBar();
        visible = true;
        shownAt = Date.now();
        // A frame's delay, so the browser does not coalesce the transition away.
        requestAnimationFrame(function () {
            el.style.width = '8%';
            el.style.opacity = '1';
        });
        grow();
    }

    function start() {
        depth++;
        if (depth > 1 || visible) return;    // already counting or already shown
        if (hideTimer) { clearTimeout(hideTimer); hideTimer = null; }
        // Delayed on purpose — see DELAY_MS. A request that finishes first never
        // draws anything, which is what keeps an autosave loop quiet.
        if (!showTimer) showTimer = setTimeout(reveal, DELAY_MS);
    }

    function finish() {
        if (depth > 0) depth--;
        if (depth > 0) return;
        if (showTimer) { clearTimeout(showTimer); showTimer = null; }
        if (growTimer) { clearInterval(growTimer); growTimer = null; }
        if (!visible || !bar) return;        // it never appeared; nothing to hide
        var el = bar;
        var wait = Math.max(0, MIN_VISIBLE_MS - (Date.now() - shownAt));
        hideTimer = setTimeout(function () {
            hideTimer = null;
            if (depth > 0) return;      // something started again while we waited
            visible = false;
            el.style.width = '100%';
            el.style.opacity = '0';
            setTimeout(function () {
                if (visible) return;
                el.style.width = '0%';
            }, 260);
        }, wait);
    }

    // ── the busy button ──────────────────────────────────────────────────────
    //
    // The original markup is kept on the element rather than replaced by a
    // message, so restoring is exact even when the label contains a Font
    // Awesome <i> and an Alpine-bound <span>.

    // Styles are set inline rather than through a class, on purpose: the dimmed,
    // non-clickable state has to be there even on a page whose stylesheet did
    // not happen to compile the utility that would express it, and the exact
    // previous `style` attribute is restored when the work settles.
    function markBusy(button) {
        if (!button || button.dataset.sgBusyOn === '1') return;
        button.dataset.sgBusyOn = '1';
        button.dataset.sgBusyAt = String(Date.now());
        button.dataset.sgBusyHtml = button.innerHTML;
        button.dataset.sgBusyStyle = button.getAttribute('style') || '';
        button.setAttribute('aria-busy', 'true');
        button.disabled = true;
        button.style.opacity = '0.7';
        button.style.cursor = 'wait';
        var spin = document.createElement('i');
        spin.className = 'fas fa-spinner fa-spin';
        spin.setAttribute('aria-hidden', 'true');
        button.innerHTML = '';
        button.appendChild(spin);
        button.appendChild(document.createTextNode('\u00a0'));
        var label = document.createElement('span');
        // Bilingual in one element, using the same `lang` attribute the rest of
        // the app switches on (see setLang() in base.html): a hard-coded
        // sentence here would be the one untranslatable string on the page.
        var en = (document.documentElement.lang || 'id').toLowerCase().indexOf('en') === 0;
        label.textContent = en ? 'Working…' : 'Memproses…';
        button.appendChild(label);
    }

    function clearBusy(button) {
        if (!button || button.dataset.sgBusyOn !== '1') return;
        button.innerHTML = button.dataset.sgBusyHtml || '';
        if (button.dataset.sgBusyStyle) {
            button.setAttribute('style', button.dataset.sgBusyStyle);
        } else {
            button.removeAttribute('style');
        }
        button.disabled = false;
        button.removeAttribute('aria-busy');
        delete button.dataset.sgBusyOn;
        delete button.dataset.sgBusyAt;
        delete button.dataset.sgBusyHtml;
        delete button.dataset.sgBusyStyle;
    }

    function clearAllBusy() {
        var marked = document.querySelectorAll('[data-sg-busy-on="1"]');
        Array.prototype.forEach.call(marked, clearBusy);
    }

    function busyControl(form, submitter) {
        if (submitter && submitter.matches && submitter.matches('[data-sg-busy]')) {
            return submitter;
        }
        var want = (submitter && submitter.matches && submitter.matches('button,[type=submit]'))
            ? submitter
            : (form.querySelector('[data-sg-busy]')
               || form.querySelector('button[type=submit], input[type=submit]'));
        if (!want) return null;
        // Opt-in means opt-in: a control the page did not mark is left alone
        // *unless* the whole form asked for it, because a form that asks is
        // saying "this submit is the slow one".
        var owner = form.matches && form.matches('[data-sg-busy]');
        if (!owner && !(want.matches && want.matches('[data-sg-busy]'))) return null;
        return want;
    }

    window.sgBusy = {
        start: start,
        finish: finish,
        mark: markBusy,
        clear: clearBusy,
        clearAll: clearAllBusy,
        //: A page whose async work is not a form submit or a fetch — a canvas
        //: loop, a WebSocket round-trip — drives the bar with these two.
        begin: start,
        end: finish
    };

    // ── tying it to what actually happens ────────────────────────────────────

    // Capture phase, so this runs even for a submit handler that stops
    // propagation. A form that fails its own validation never fires `submit` at
    // all, so nothing can be left marked busy by a submit that did not happen.
    document.addEventListener('submit', function (event) {
        try {
            var form = event.target;
            if (!form || form.nodeName !== 'FORM') return;
            var button = busyControl(form, event.submitter);
            if (button) markBusy(button);
            start();
        } catch (e) { /* never let feedback break a submit */ }
    }, true);

    if (typeof window.fetch === 'function') {
        var realFetch = window.fetch;
        window.fetch = function () {
            start();
            var p;
            try {
                p = realFetch.apply(this, arguments);
            } catch (e) {
                finish();
                throw e;
            }
            if (p && typeof p.then === 'function') {
                return p.then(function (r) { finish(); return r; },
                              function (e) { finish(); throw e; });
            }
            finish();
            return p;
        };
    }

    if (typeof window.XMLHttpRequest === 'function') {
        var open = XMLHttpRequest.prototype.open;
        var send = XMLHttpRequest.prototype.send;
        XMLHttpRequest.prototype.open = function () {
            this.__sgTracked = false;
            return open.apply(this, arguments);
        };
        XMLHttpRequest.prototype.send = function () {
            var self = this;
            try {
                if (!self.__sgTracked) {
                    self.__sgTracked = true;
                    start();
                    self.addEventListener('loadend', finish);
                }
            } catch (e) { /* fall through to the real send */ }
            return send.apply(this, arguments);
        };
    }

    // Anything still marked when the page comes back from the bfcache is stale:
    // the navigation it was waiting for already finished, or the browser went
    // back instead of forward. Without this the button stays dead on return.
    window.addEventListener('pageshow', function (event) {
        if (event.persisted) {
            depth = 0;
            visible = false;
            clearAllBusy();
            if (showTimer) { clearTimeout(showTimer); showTimer = null; }
            if (growTimer) { clearInterval(growTimer); growTimer = null; }
            if (bar) { bar.style.opacity = '0'; bar.style.width = '0%'; }
        }
    });

    // A last-resort release: a submit that never navigates (a handler that
    // cancelled it, a dead connection that never settles) must not leave a
    // button disabled forever. Generous, because a real save on a school link
    // can take a while — and harmless, because a successful submit has already
    // replaced the page by then.
    setInterval(function () {
        var marked = document.querySelectorAll('[data-sg-busy-on="1"]');
        Array.prototype.forEach.call(marked, function (b) {
            var t = parseInt(b.dataset.sgBusyAt || '0', 10);
            if (t && Date.now() - t > 120000) clearBusy(b);
        });
    }, 15000);
})();
