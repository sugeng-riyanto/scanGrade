/* The graphing calculator, and the three states it can be opened in.
 *
 * The exam page offers Desmos beside the scientific calculator, behind the same
 * `allow_calculator` switch: the teacher who decides a paper may use a
 * calculator decides once, and a graph tool is a calculator. It is not vendored
 * the way Snap.js and Inter are, and that is deliberate rather than an oversight:
 * Desmos' API authenticates by reading its own `<script>` tag's URL, has no
 * offline build, and its licence is for the hosted product. What the page can
 * control is *when* it is fetched — on the first press, never on page load — so a
 * pupil whose WiFi is gone loses one panel and keeps the paper, and the paper
 * itself (questions, PDF, drawing, autosave) never waits on desmos.com.
 *
 * Three outcomes, each named, because "the graph did not open" is not a message a
 * pupil can act on:
 *
 *   * **unconfigured** — there is no `DESMOS_API_KEY` in this deployment. A
 *     teacher's preview shows the same words on purpose: the feature is missing
 *     from the box, not from the paper.
 *   * **offline** — the script could not be fetched. The panel says so and the
 *     pupil can retry without reloading the exam.
 *   * **ready** — the calculator exists in the element it was given.
 *
 * The size is the panel's, not the calculator's: Desmos draws to whatever box it
 * is handed and does not watch that box, so a rotation, a browser resize or the
 * side column folding away all need `resize()` called explicitly. One
 * `ResizeObserver` on the host does that, which is also what makes the panel
 * responsive without a media query per device.
 *
 * Loaded as a classic script before the page's Alpine component, like
 * `essay-editor.js`, and exercised by `tests/unit/test_exam_graph.py` (the file's
 * states and the page's wiring in jsdom).
 */
(function (root) {
    'use strict';

    var SCRIPT_BASE = 'https://www.desmos.com/api/v1.10/calculator.js';
    var loading = null;

    function failure(code, message) {
        var err = new Error(message || code);
        err.code = code;
        return err;
    }

    /* Fetch Desmos' script once per page. A second press reuses the same promise,
     * so a slow link produces one download rather than one per press. */
    function load(key) {
        if (!key) return Promise.reject(failure('unconfigured'));
        if (root.Desmos) return Promise.resolve(root.Desmos);
        if (loading) return loading;
        var doc = root.document;
        loading = new Promise(function (resolve, reject) {
            var script = doc.createElement('script');
            script.src = SCRIPT_BASE + '?apiKey=' + encodeURIComponent(key);
            script.async = true;
            script.onload = function () {
                if (root.Desmos) resolve(root.Desmos);
                else reject(failure('offline', 'Desmos loaded without an API'));
            };
            script.onerror = function () {
                /* The script stays out of the DOM after a failure so a retry can
                 * insert a fresh one; a `src` that already failed is cached by
                 * some browsers and would never fire `load` again. */
                if (script.parentNode) script.parentNode.removeChild(script);
                loading = null;
                reject(failure('offline'));
            };
            doc.head.appendChild(script);
            /* A captive portal accepts the connection and never answers, so a
             * load event is not the only way this ends. */
            root.setTimeout(function () {
                if (!root.Desmos) {
                    if (script.parentNode) script.parentNode.removeChild(script);
                    loading = null;
                    reject(failure('timeout'));
                }
            }, 15000);
        });
        return loading;
    }

    /* Put a calculator in `host` and keep it the size of `host`.
     *
     * Returns `{ calculator, destroy }` synchronously for a caller that already
     * has the script; the page's `openGraph()` awaits `load()` first. */
    function mount(host, options) {
        if (!host) return null;
        if (!root.Desmos) {
            throw failure('unconfigured', 'Desmos is not loaded');
        }
        var calculator = root.Desmos.GraphingCalculator(host, Object.assign({
            expressions: true,
            settingsMenu: false,
            zoomButtons: true,
            border: false,
            keypad: true,
            projectorMode: false,
            /* A paper is not a graph: the axes labels are useful, the degree-mode
             * menu is a distraction in a two-hour sitting. It stays reachable from
             * the wrench, which is the convention an external board's papers
             * assume — the setting exists without spending screen on it. */
            administerSecretFolders: false
        }, options || {}));

        var observer = null;
        function fit() {
            try { calculator.resize(); } catch (err) { /* a hidden host has no box */ }
        }
        if (typeof root.ResizeObserver === 'function') {
            observer = new root.ResizeObserver(fit);
            observer.observe(host);
        }
        root.addEventListener('resize', fit);
        root.addEventListener('orientationchange', fit);
        fit();

        return {
            calculator: calculator,
            resize: fit,
            destroy: function () {
                if (observer) observer.disconnect();
                root.removeEventListener('resize', fit);
                root.removeEventListener('orientationchange', fit);
                try { calculator.destroy(); } catch (err) { /* already gone */ }
            }
        };
    }

    root.SGGraph = { load: load, mount: mount };
})(typeof window !== 'undefined' ? window : this);
