/* The builder's draft, written while the teacher is still writing it.
 *
 * `/teacher/exams/new` is the densest form in the app — title, subject, classes,
 * question list, media, anti-cheat — and until this file existed none of it was
 * saved until the teacher pressed **Publish & Send to Classes**. A tab closed, a
 * laptop lid shut, a school connection that drops for a minute: the paper was
 * gone, and the only warning was the browser's own "leave site?" prompt. So the
 * form is saved by itself, a couple of seconds after the teacher stops changing
 * it.
 *
 * Three rules make the loop safe, and each of them is here rather than in the
 * page because a second copy would drift from this one.
 *
 *   1. **It never navigates.** The save is a `fetch` answered with JSON, so the
 *      teacher keeps typing in the same page. The server's own save doors know
 *      the difference (see `_exam_save_refused` in app/routes/teacher.py): an
 *      autosave that cannot be written answers a sentence instead of a redirect.
 *
 *   2. **It saves nothing that has not changed.** The snapshot is the whole body
 *      minus the two fields this file owns, so opening the page, clicking around
 *      it or letting another script touch a field does not write. That is what
 *      keeps a background loop from becoming load on a 1 vCPU box.
 *
 *   3. **It mints the draft once.** The first save returns the new exam's id, the
 *      page adopts it, and every later save writes *that* row. Without the
 *      adoption the explicit Publish would post to `/exams/new` a second time and
 *      leave the school with two papers — so the adoption is the page's, but the
 *      decision is this file's (`adopt`, below, and only while the row is still
 *      new).
 *
 * Loaded as a plain `<script src>` and driven from the page's own script, like
 * `exam-window.js` beside it. `tests/unit/test_exam_autosave.py` runs it in node
 * against injected collaborators — the page's three functions and a stubbed
 * timer — so what is checked is the debounce, the dedupe and the states rather
 * than the shape of the source.
 */
(function (root) {
    'use strict';

    /* How long the page waits after the teacher's last change. Long enough that a
     * paragraph being typed is one save rather than forty, short enough that a
     * closed tab costs a sentence instead of a paper. */
    var DEBOUNCE_MS = 2500;

    /* The two fields this file owns, and the reason `draft_id` cannot appear in the
     * snapshot: the module writes it *after* a reply arrives, so counting it as
     * teacher input would make every save look like the page changed underneath
     * it and start a loop.
     *
     * The submit buttons are dropped for a different reason: they carry
     * `name="action"`, and the explicit Publish button is in the form. Its value
     * belongs to the submit that pressed it, not to a background save. */
    var OWNED = { action: true, draft_id: true };

    /* Every field the form would post, as (name, value) pairs, minus the ones this
     * file owns and minus any file input.
     *
     * A file input is skipped because `new FormData(form)` would re-upload the PDF
     * on every tick — the builder uploads it through its own AJAX path
     * (`pdf_preview_url`) precisely so the bytes travel once. The order is the
     * form's; `snapshot` sorts, so the two readings of the same page agree however
     * the DOM happens to be ordered. */
    function collect(form) {
        var pairs = [];
        var els = (form && form.elements) || [];
        for (var i = 0; i < els.length; i++) {
            var el = els[i];
            var name = el && el.name;
            if (!name || OWNED[name]) continue;
            var type = el.type;
            if (type === 'file' || type === 'submit' || type === 'button' ||
                type === 'reset' || type === 'image') continue;
            if ((type === 'checkbox' || type === 'radio') && !el.checked) continue;
            pairs.push([name, el.value === undefined || el.value === null ? '' : String(el.value)]);
        }
        var idField = (form && form.querySelector) ? form.querySelector('[name="draft_id"]') : null;
        return {
            body: pairs,
            snapshot: pairs.map(function (p) { return p[0] + '=' + p[1]; })
                           .sort().join('\u0001'),
            id: idField ? (idField.value || '') : ''
        };
    }

    /* Attach the loop. Returns false when the page has not handed over what the loop
     * needs, so a page can report a reason instead of throwing — the form still
     * works without this, it just asks the teacher to press Publish to keep it.
     *
     * `opts`:
     *   form, indicator  elements (the indicator may be absent: the save still runs)
     *   read()           -> the shape `collect` returns; the page's own reading
     *   send(state)      -> a promise for `{ok, exam_id, error}`
     *   adopt(examId)    where the page writes the id it has just been handed
     *   label(state)     the words, from the page, so the language toggle reaches them
     *   setTimer/clearTimer, debounceMs   injected by the tests, real timers here
     */
    function wire(opts) {
        opts = opts || {};
        var form = opts.form, indicator = opts.indicator,
            read = opts.read, send = opts.send,
            adopt = opts.adopt, label = opts.label,
            debounceMs = opts.debounceMs || DEBOUNCE_MS,
            setT = opts.setTimer || setTimeout,
            clearT = opts.clearTimer || clearTimeout;

        if (!form || typeof form.addEventListener !== 'function') return false;
        if (typeof read !== 'function' || typeof send !== 'function') return false;

        var timer = null;       // the pending debounce
        var inFlight = false;   // a save is open
        var rev = 0;            // bumped by every change, so an edit made *during*
                                // a request is not mistaken for the body just saved
        var last = read().snapshot;

        function paint(state, detail) {
            if (!indicator) return;
            indicator.dataset.sgAutosave = state;
            var text = label ? (label(state, detail) || '') : '';
            if (indicator.textContent !== text) indicator.textContent = text;
        }

        function settle(reply, sent, sentRev) {
            var ok = !(reply && reply.ok === false);
            inFlight = false;
            if (!ok) {
                /* The refusal is the server's own sentence (a window end before its
                 * start, no questions at all) — shown, not swallowed. `last` is left
                 * alone, so the same body is offered again once the teacher fixes the
                 * field, and *nothing is retried on its own*: a body the server keeps
                 * refusing must not become a request every few seconds. */
                paint('error', (reply && reply.error) || '');
            } else {
                last = sent.snapshot;
                if (reply.exam_id && !read().id && typeof adopt === 'function') {
                    adopt(reply.exam_id);
                }
                paint('saved');
            }
            /* An edit that landed while the request was open: one more pass, now
             * rather than after another full debounce. `rev` is the test, not the
             * snapshot — the edit is by definition not in the body that was just
             * written. And only after a save that worked: a refusal waits for the
             * teacher to change something, which is what the `ok` guard says. */
            if (ok && rev !== sentRev && timer === null) arm(0);
        }

        function fire() {
            timer = null;
            if (inFlight) return;                    // the open request re-checks
            var state = read();
            if (state.snapshot === last) return;     // nothing new to write
            var sentRev = rev;
            inFlight = true;
            paint('saving');
            var reply;
            try {
                reply = send(state);
            } catch (e) {
                settle({ ok: false }, state, sentRev);
                return;
            }
            Promise.resolve(reply).then(function (r) { settle(r || {}, state, sentRev); },
                                        function () { settle({ ok: false }, state, sentRev); });
        }

        function arm(wait) {
            if (timer !== null) clearT(timer);
            timer = setT(fire, wait === undefined ? debounceMs : wait);
        }

        function onChange() {
            rev += 1;
            arm();
        }

        /* Three listeners, because the form changes in three ways. `input` is typing,
         * `change` is a select or a checkbox, and `click` is the one that matters for
         * the question list: Alpine writes the hidden `question_types` / `answer_key`
         * inputs through `:value`, which fires no event at all, so adding or removing
         * a question would otherwise wait for the next keystroke. Each only *starts*
         * the debounce — `fire` then compares the snapshot, so a click that changed
         * nothing writes nothing. */
        ['input', 'change', 'click'].forEach(function (type) {
            form.addEventListener(type, onChange, true);
        });

        return true;
    }

    root.SGExamAutosave = {
        DEBOUNCE_MS: DEBOUNCE_MS,
        collect: collect,
        wire: wire
    };
})(typeof window !== 'undefined' ? window : this);
