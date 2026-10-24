/* The graph panel's own calculator — no key, no network, no CDN.
 *
 * The panel is Desmos when the deployment has a `DESMOS_API_KEY`, and this when
 * it does not. That is not a fallback bolted on for tidiness: Desmos issues keys
 * to an account holder and its free tier is for school/personal projects, so a
 * school that has not signed up (or whose network cannot reach desmos.com on
 * exam morning) had a panel that said "the key is not configured" and no graph.
 * A paper should not lose a tool because a subscription is missing.
 *
 * What it is: one expression in `x`, plotted on an axis pair the pupil can drag
 * and zoom. What it is not: Desmos. There are no sliders, tables, regressions or
 * geometry, and that limit is stated where the feature is documented rather than
 * implied by a panel that looks richer than it is.
 *
 * Two decisions worth keeping:
 *
 *   * **No `eval`.** A formula is parsed into a tree of closures by the small
 *     recursive-descent parser below. A pupil types this text during an exam, and
 *     `eval` on it would be a way to run anything in the page that holds their
 *     answers.
 *   * **The drawing is Chart.js**, which this app already vendors and every page
 *     already carries (`base.html`), so the tool costs no extra download on the
 *     connection a school actually has.
 *
 * Exercised by `tests/unit/test_exam_graph_local.py` — the parser as arithmetic
 * and the mount in jsdom with Chart stubbed.
 */
(function (root) {
    'use strict';

    var FUNCS = {
        sin: Math.sin, cos: Math.cos, tan: Math.tan,
        asin: Math.asin, acos: Math.acos, atan: Math.atan,
        sqrt: Math.sqrt, abs: Math.abs, exp: Math.exp,
        ln: Math.log,
        log: function (v) { return Math.log(v) / Math.LN10; },
        floor: Math.floor, ceil: Math.ceil, round: Math.round
    };

    function failure(code) {
        var err = new Error(code);
        err.code = code;
        return err;
    }

    /* ── Reading the formula ─────────────────────────────────────────────────
     * Numbers (with an exponent), names, and the six operators a paper uses.
     * Anything else is a syntax error rather than something to guess at: a pupil
     * who typed a character this does not know needs to hear it. */
    function tokenize(src) {
        var out = [];
        var i = 0;
        while (i < src.length) {
            var c = src.charAt(i);
            if (/\s/.test(c)) { i += 1; continue; }
            if (/[0-9.]/.test(c)) {
                var j = i;
                var dots = 0;
                while (j < src.length && /[0-9.]/.test(src.charAt(j))) {
                    if (src.charAt(j) === '.') {
                        dots += 1;
                        if (dots > 1) throw failure('syntax');
                    }
                    j += 1;
                }
                /* `1e-3` is a number; `2e` is two times e, so the exponent only
                 * counts when digits follow it. */
                if (src.charAt(j) === 'e' || src.charAt(j) === 'E') {
                    var k = j + 1;
                    if (src.charAt(k) === '+' || src.charAt(k) === '-') k += 1;
                    if (/[0-9]/.test(src.charAt(k))) {
                        while (k < src.length && /[0-9]/.test(src.charAt(k))) k += 1;
                        j = k;
                    }
                }
                var value = Number(src.slice(i, j));
                if (!isFinite(value)) throw failure('syntax');
                out.push({ t: 'num', v: value });
                i = j;
                continue;
            }
            if (/[a-zA-Z]/.test(c)) {
                var end = i;
                while (end < src.length && /[a-zA-Z]/.test(src.charAt(end))) end += 1;
                out.push({ t: 'name', v: src.slice(i, end).toLowerCase() });
                i = end;
                continue;
            }
            if ('+-*/^(),'.indexOf(c) >= 0) {
                out.push({ t: 'op', v: c });
                i += 1;
                continue;
            }
            throw failure('syntax');
        }
        return out;
    }

    /* Recursive descent, returning a function of x for each node.
     *
     *   expr   := term (('+' | '-') term)*
     *   term   := unary (('*' | '/') unary | unary)*      // the second form is
     *   unary  := ('-' | '+') unary | power               // implicit multiply
     *   power  := atom ('^' unary)?                       // right-assoc
     *   atom   := number | 'x' | 'pi' | 'e' | func '(' expr ')' | '(' expr ')'
     */
    function parse(src) {
        var tokens = tokenize(src);
        if (!tokens.length) throw failure('empty');
        var pos = 0;

        function peek() { return tokens[pos]; }
        function take() { return tokens[pos++]; }
        function isOp(ch) {
            var tok = peek();
            return !!tok && tok.t === 'op' && tok.v === ch;
        }

        function parseExpr() {
            var left = parseTerm();
            while (isOp('+') || isOp('-')) {
                var op = take().v;
                var right = parseTerm();
                left = op === '+'
                    ? (function (a, b) { return function (x) { return a(x) + b(x); }; })(left, right)
                    : (function (a, b) { return function (x) { return a(x) - b(x); }; })(left, right);
            }
            return left;
        }

        function parseTerm() {
            var left = parseUnary();
            for (;;) {
                if (isOp('*') || isOp('/')) {
                    var op = take().v;
                    var right = parseUnary();
                    left = op === '*'
                        ? (function (a, b) { return function (x) { return a(x) * b(x); }; })(left, right)
                        : (function (a, b) { return function (x) { return a(x) / b(x); }; })(left, right);
                    continue;
                }
                /* `2x`, `3(x+1)`, `x sin(x)`, `(x+1)(x-1)`: a term that starts with
                 * a number, a name or a bracket multiplies what came before it. */
                var next = peek();
                if (next && (next.t === 'num' || next.t === 'name' || (next.t === 'op' && next.v === '('))) {
                    var other = parseUnary();
                    left = (function (a, b) { return function (x) { return a(x) * b(x); }; })(left, other);
                    continue;
                }
                return left;
            }
        }

        function parseUnary() {
            if (isOp('-')) {
                take();
                var inner = parseUnary();
                return function (x) { return -inner(x); };
            }
            if (isOp('+')) {
                take();
                return parseUnary();
            }
            return parsePower();
        }

        function parsePower() {
            var base = parseAtom();
            if (isOp('^')) {
                take();
                var exponent = parseUnary();
                return function (x) { return Math.pow(base(x), exponent(x)); };
            }
            return base;
        }

        function parseAtom() {
            var tok = peek();
            if (!tok) throw failure('syntax');
            if (tok.t === 'num') {
                take();
                var value = tok.v;
                return function () { return value; };
            }
            if (tok.t === 'op' && tok.v === '(') {
                take();
                var inner = parseExpr();
                if (!isOp(')')) throw failure('syntax');
                take();
                return inner;
            }
            if (tok.t === 'name') {
                take();
                var name = tok.v;
                if (name === 'x') return function (x) { return x; };
                if (name === 'pi') return function () { return Math.PI; };
                if (name === 'e') return function () { return Math.E; };
                if (Object.prototype.hasOwnProperty.call(FUNCS, name)) {
                    if (!isOp('(')) throw failure('syntax');
                    take();
                    var arg = parseExpr();
                    if (!isOp(')')) throw failure('syntax');
                    take();
                    var fn = FUNCS[name];
                    return function (x) { return fn(arg(x)); };
                }
                /* A name this reader does not know is a typo, not a variable. */
                throw failure('syntax');
            }
            throw failure('syntax');
        }

        var fn = parseExpr();
        if (pos < tokens.length) throw failure('syntax');
        return fn;
    }

    /* `y = …` is how a pupil writes the label on the axes; the function is what
     * follows it. The prefix is stripped rather than taught, because a formula
     * that reads as it is written on the paper is the point. */
    function compile(src) {
        var text = String(src === null || src === undefined ? '' : src).trim();
        text = text.replace(/^y\s*=/i, '').trim();
        if (!text) throw failure('empty');
        return { fn: parse(text), text: text };
    }

    function formatTick(value) {
        if (!isFinite(value)) return '';
        if (Math.abs(value) < 1e-9) return '0';
        var magnitude = Math.abs(value);
        if (magnitude >= 1e6 || magnitude < 1e-3) return value.toExponential(0);
        return String(Math.round(value * 1000) / 1000);
    }

    function axisOptions() {
        return {
            type: 'linear',
            grid: { color: '#e5e7eb', lineWidth: 1 },
            border: { color: '#cbd5e1' },
            ticks: {
                color: '#475569',
                font: { size: 10 },
                maxTicksLimit: 9,
                maxRotation: 0,
                callback: function (value) { return formatTick(value); }
            }
        };
    }

    /* Put a plot in `host` and keep it the size of `host`.
     *
     * Returns the same shape the Desmos module does (`resize`, `destroy`) plus the
     * three things this one needs from the page: `setExpression`, `zoom`, `reset`.
     */
    function mount(host, options) {
        if (!host) return null;
        var Chart = root.Chart;
        if (!Chart) throw failure('unconfigured');
        var doc = root.document;

        var wrap = doc.createElement('div');
        wrap.className = 'sg-graph-local-canvas';
        wrap.style.cssText = 'position:absolute;inset:0;';
        var canvas = doc.createElement('canvas');
        canvas.style.cssText = 'display:block;width:100%;height:100%;touch-action:none;cursor:grab;';
        wrap.appendChild(canvas);
        host.appendChild(wrap);

        var view = { x0: -10, x1: 10, y0: -10, y1: 10 };
        var expr = null;

        var chart = new Chart(canvas.getContext('2d'), {
            type: 'scatter',
            data: {
                datasets: [{
                    data: [],
                    showLine: true,
                    borderColor: '#0d9488',
                    borderWidth: 2,
                    pointRadius: 0,
                    pointHitRadius: 0,
                    hitRadius: 0,
                    tension: 0,
                    fill: false,
                    spanGaps: false
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                animation: false,
                parsing: false,
                normalized: true,
                /* Nothing is hovered on purpose: a tooltip over a paper is a
                 * distraction, and every pointer event here is a pan. */
                events: [],
                layout: { padding: 2 },
                plugins: {
                    legend: { display: false },
                    tooltip: { enabled: false }
                },
                scales: { x: axisOptions(), y: axisOptions() }
            }
        });

        function points() {
            if (!expr) return [];
            var width = canvas.clientWidth || 600;
            var count = Math.max(240, Math.min(1400, Math.round(width * 1.6)));
            var step = (view.x1 - view.x0) / count;
            var span = view.y1 - view.y0;
            var out = [];
            var previous = null;
            for (var i = 0; i <= count; i += 1) {
                var x = view.x0 + i * step;
                var y;
                try {
                    y = expr(x);
                } catch (err) {
                    y = NaN;
                }
                if (typeof y !== 'number' || !isFinite(y)) {
                    out.push(null);
                    previous = null;
                    continue;
                }
                /* Two tells that a sample is on the far side of an asymptote, and
                 * joining it to its neighbour would draw a vertical line that is
                 * not in the function: a value far outside the window, and a jump
                 * across most of it with the sign flipped (tan(pi/2), 1/x). */
                if (Math.abs(y) > span * 40) {
                    out.push(null);
                    previous = null;
                    continue;
                }
                if (previous !== null && Math.abs(y - previous) > span * 20) {
                    out.push(null);
                    previous = y;
                    continue;
                }
                out.push({ x: x, y: y });
                previous = y;
            }
            return out;
        }

        function applyView() {
            chart.options.scales.x.min = view.x0;
            chart.options.scales.x.max = view.x1;
            chart.options.scales.y.min = view.y0;
            chart.options.scales.y.max = view.y1;
        }

        function redraw() {
            chart.data.datasets[0].data = points();
            applyView();
            chart.update('none');
        }

        var drag = null;

        function onDown(event) {
            if (event.pointerType === 'mouse' && event.button !== 0) return;
            drag = {
                x: event.clientX,
                y: event.clientY,
                from: { x0: view.x0, x1: view.x1, y0: view.y0, y1: view.y1 }
            };
            canvas.style.cursor = 'grabbing';
            if (canvas.setPointerCapture) {
                try { canvas.setPointerCapture(event.pointerId); } catch (err) { /* not capturable */ }
            }
            if (event.preventDefault) event.preventDefault();
        }

        function onMove(event) {
            if (!drag) return;
            var box = canvas.getBoundingClientRect ? canvas.getBoundingClientRect() : null;
            if (!box || !box.width || !box.height) return;
            var perX = (drag.from.x1 - drag.from.x0) / box.width;
            var perY = (drag.from.y1 - drag.from.y0) / box.height;
            var dx = (event.clientX - drag.x) * perX;
            var dy = (event.clientY - drag.y) * perY;
            view.x0 = drag.from.x0 - dx;
            view.x1 = drag.from.x1 - dx;
            view.y0 = drag.from.y0 + dy;
            view.y1 = drag.from.y1 + dy;
            redraw();
            if (event.preventDefault) event.preventDefault();
        }

        function onUp() {
            drag = null;
            canvas.style.cursor = 'grab';
        }

        canvas.addEventListener('pointerdown', onDown);
        canvas.addEventListener('pointermove', onMove);
        canvas.addEventListener('pointerup', onUp);
        canvas.addEventListener('pointercancel', onUp);

        function setExpression(src) {
            try {
                var compiled = compile(src);
                expr = compiled.fn;
                redraw();
                return { ok: true, text: compiled.text };
            } catch (err) {
                /* The last curve that read stays on the axes: a half-typed formula
                 * should not blank the graph the pupil is looking at. */
                return { ok: false, code: (err && err.code) || 'syntax' };
            }
        }

        function zoom(factor) {
            var step = typeof factor === 'number' && factor > 0 ? factor : 1;
            var cx = (view.x0 + view.x1) / 2;
            var cy = (view.y0 + view.y1) / 2;
            var halfX = (view.x1 - view.x0) / 2 / step;
            var halfY = (view.y1 - view.y0) / 2 / step;
            view = { x0: cx - halfX, x1: cx + halfX, y0: cy - halfY, y1: cy + halfY };
            redraw();
        }

        function reset() {
            view = { x0: -10, x1: 10, y0: -10, y1: 10 };
            redraw();
        }

        var observer = null;
        function fit() {
            /* Chart.js sizes itself from its parent, and this plot is mounted on
             * the press that shows the panel — in the same tick the browser still
             * has it `display:none`, so that measurement is 0 and a no-argument
             * `resize()` is a no-op that leaves a 0×0 canvas for ever. Measured
             * sizes are handed over explicitly; the observer then fires on the
             * tick the panel becomes visible. Verified in a browser: without this
             * the panel opened, said "Graph", and drew nothing. */
            var width = host.clientWidth;
            var height = host.clientHeight;
            try {
                if (width > 0 && height > 0) {
                    if (chart.width !== width || chart.height !== height) {
                        chart.resize(width, height);
                    }
                } else {
                    chart.resize();
                }
            } catch (err) { /* a hidden host has no box */ }
        }
        if (typeof root.ResizeObserver === 'function') {
            observer = new root.ResizeObserver(fit);
            observer.observe(host);
        }
        root.addEventListener('resize', fit);
        root.addEventListener('orientationchange', fit);
        fit();

        return {
            chart: chart,
            setExpression: setExpression,
            zoom: zoom,
            reset: reset,
            resize: fit,
            view: function () { return { x0: view.x0, x1: view.x1, y0: view.y0, y1: view.y1 }; },
            destroy: function () {
                if (observer) observer.disconnect();
                root.removeEventListener('resize', fit);
                root.removeEventListener('orientationchange', fit);
                canvas.removeEventListener('pointerdown', onDown);
                canvas.removeEventListener('pointermove', onMove);
                canvas.removeEventListener('pointerup', onUp);
                canvas.removeEventListener('pointercancel', onUp);
                try { chart.destroy(); } catch (err) { /* already gone */ }
                if (wrap.parentNode) wrap.parentNode.removeChild(wrap);
            }
        };
    }

    root.SGGraphLocal = { compile: compile, mount: mount, formatTick: formatTick };
})(typeof window !== 'undefined' ? window : this);
