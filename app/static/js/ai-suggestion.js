/*
 * The pause between "the model answered" and "a teacher marked".
 *
 * Two things this file exists to stop, both of which the old synchronous call did:
 *
 *  - **A pending request that looks like a finished mark.** The provider call now
 *    happens in a worker, so the page gets an id and has to wait. `onPending` is
 *    called with how long it has been waiting, so the marking page can say so
 *    instead of showing a disabled button and no reason.
 *  - **A number arriving in a score field on its own.** `suggest()` resolves with a
 *    *proposal*; nothing is written anywhere until a person calls `apply()` and the
 *    form is saved. `apply()` records the decision and hands the number back — it
 *    does not touch the submission, which is why a teacher who then edits the field
 *    gets their own number.
 *
 * Polling rather than a websocket: the backend is a 1-vCPU box with a Celery worker,
 * a marking page is open for minutes at a time, and one request every 1.5s per open
 * question costs less than the connection it would replace.
 */
(function (global) {
    'use strict';

    var POLL_MS = 1500;
    //: Longer than any single provider call we have seen, short enough that a dead
    //: worker becomes a sentence rather than a spinner that never stops.
    var GIVE_UP_MS = 90000;
    var TERMINAL = {ready: 1, failed: 1, applied: 1, rejected: 1};

    function sleep(ms) {
        return new Promise(function (resolve) { setTimeout(resolve, ms); });
    }

    function readJson(response) {
        return response.json().catch(function () { return {}; }).then(function (body) {
            if (!response.ok) {
                var message = body && body.error ? body.error : ('HTTP ' + response.status);
                var error = new Error(message);
                error.status = response.status;
                throw error;
            }
            return body;
        });
    }

    /*
     * Ask for a suggestion and wait for the worker.
     *
     * `onPending(elapsedMs, body)` is called on every poll; a page that shows nothing
     * still works, because the promise is what carries the answer.
     */
    function suggest(opts) {
        var options = opts || {};
        var started = Date.now();

        return fetch('/api/ai/suggest-essay', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                submission_id: options.submission_id,
                question_index: options.questionIndex,
                // The teacher's own reading of a canvas answer. Optional and empty
                // for a typed essay, in which case the server uses the stored
                // answer — never a text the browser supplied.
                ocr_text: options.ocrText || ''
            })
        }).then(readJson).then(function (queued) {
            var id = queued.suggestion_id;
            if (!id) { throw new Error('Server tidak mengembalikan id saran AI'); }
            if (options.onPending) { options.onPending(0, queued); }
            return waitFor(id, started, options);
        });
    }

    function waitFor(id, started, options) {
        return fetch('/api/ai/suggestion/' + encodeURIComponent(id), {method: 'GET'})
            .then(readJson)
            .then(function (body) {
                if (body.status === 'ready') {
                    return Promise.resolve(body);
                }
                if (body.status === 'failed') {
                    // A failed suggestion is a sentence, not a zero. The teacher must
                    // never see 0 in a score field because a provider was down.
                    var error = new Error(body.error || 'Saran AI gagal dibuat');
                    error.suggestion = body;
                    throw error;
                }
                var elapsed = Date.now() - started;
                if (elapsed > GIVE_UP_MS) {
                    var timeout = new Error('Saran AI belum selesai setelah ' +
                        Math.round(elapsed / 1000) + ' detik. Coba lagi.');
                    timeout.suggestion = body;
                    throw timeout;
                }
                if (options.onPending) { options.onPending(elapsed, body); }
                return sleep(POLL_MS).then(function () {
                    return waitFor(id, started, options);
                });
            });
    }

    function decide(id, verb) {
        return fetch('/api/ai/suggestion/' + encodeURIComponent(id) + '/' + verb, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: '{}'
        }).then(readJson);
    }

    /* Accepted. Writes no mark — the caller fills its own field and saves. */
    function apply(id) {
        return decide(id, 'apply');
    }

    /* Looked at and not used. Kept as a record, not deleted. */
    function reject(id) {
        return decide(id, 'reject');
    }

    /* "12 dari 30" for the exam overview, without a second endpoint. */
    function bulk(examId, submission_ids) {
        return fetch('/api/ai/grade-bulk', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({exam_id: examId, submission_ids: submission_ids || null})
        }).then(readJson);
    }

    global.SGAi = {
        suggest: suggest,
        apply: apply,
        reject: reject,
        bulk: bulk,
        TERMINAL: TERMINAL
    };
})(window);
