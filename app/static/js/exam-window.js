/* The two ends of a paper's window, kept in step with its duration.
 *
 * The form asks for a **Duration** ("counted from the moment the student
 * starts") and an **Assignment window end** ("the last instant a student may
 * begin"). They are two different clocks, and the builder left the teacher to
 * work the second one out from the first by hand — so a 60-minute paper was
 * routinely given a two-day window, and a pupil beginning at the far end of it
 * met the window's own deadline minutes into a paper they were entitled to sit
 * for an hour.
 *
 * With the follow toggle on, the window end is `start + duration`: the paper
 * stays open exactly long enough for one full sitting, so no pupil who is
 * admitted can be cut off before their own clock runs out. It is a default and
 * not a rule — untick it and the field is the teacher's again, which is what a
 * school rotating five classes through one afternoon needs (a longer window,
 * unchanged).
 *
 * Two rules the arithmetic obeys, and both are load-bearing:
 *
 *   * **`0` is Unlimited, not zero minutes.** `duration_facts` and the exam's
 *     own `deadline()` already read a stored 0 that way, so adding nothing and
 *     presenting the start back to the teacher would set a deadline the rest of
 *     the app does not believe in. No duration, no suggestion.
 *   * **It never runs on its own for a saved paper.** The window a teacher
 *     saved is not rewritten because the page happened to load — only an input
 *     the teacher actually touches recomputes it. A *new* paper has no window
 *     yet, so the one pass on load fills the field rather than changing it.
 *
 * Everything here is plain DOM and a pure function, so `tests/unit/test_exam_window_auto.py`
 * can run it in node against a two-line element stub and check the numbers
 * rather than the shape of the source.
 */
(function (root) {
    'use strict';

    // The same ceiling the duration input carries; a number past it cannot have
    // come from the field, so nothing is computed from one.
    var MAX_MINUTES = 600;
    var MS_PER_MINUTE = 60000;

    function pad(n) {
        return (n < 10 ? '0' : '') + n;
    }

    // `datetime-local` values are wall-clock time in the teacher's own zone, so
    // they are parsed and formatted by hand: `new Date('2026-10-06T07:30')` is
    // read as local by modern engines but not by every one of them, and an
    // ISO-string round trip would move the value by the UTC offset.
    function parseLocal(value) {
        if (typeof value !== 'string') return null;
        var m = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/.exec(value.trim());
        if (!m) return null;
        var d = new Date(+m[1], +m[2] - 1, +m[3], +m[4], +m[5], 0, 0);
        if (isNaN(d.getTime())) return null;
        // A date the constructor silently rolled over (31 February) is not the
        // date that was asked for; refuse rather than write a different one.
        if (d.getFullYear() !== +m[1] || d.getMonth() !== +m[2] - 1 || d.getDate() !== +m[3]) {
            return null;
        }
        return d;
    }

    function localValue(date) {
        return date.getFullYear() + '-' + pad(date.getMonth() + 1) + '-' + pad(date.getDate())
            + 'T' + pad(date.getHours()) + ':' + pad(date.getMinutes());
    }

    /* The window end a start and a duration imply, as a `datetime-local` string.
     * `null` when there is no answer to give — which the caller reads as "leave
     * the field alone", never as "write an empty one". */
    function endFrom(startValue, minutes) {
        var m = Number(minutes);
        if (!isFinite(m) || m <= 0 || m > MAX_MINUTES) return null;   // 0 = Unlimited
        var start = parseLocal(startValue);
        if (!start) return null;
        return localValue(new Date(start.getTime() + Math.round(m) * MS_PER_MINUTE));
    }

    /* Attach the three fields. Returns false when it cannot, so the page can
     * report a reason instead of throwing — the form still works without this,
     * it just asks the teacher for the second date themselves.
     *
     * `onLoad` runs the one pass a brand-new paper gets. It is passed by the
     * template as `{{ 'true' if (not exam or not exam.end_at) else 'false' }}`,
     * which is the whole difference between filling an empty field and
     * overwriting a deadline somebody chose. */
    function wire(fields) {
        fields = fields || {};
        var start = fields.start, end = fields.end,
            duration = fields.duration, follow = fields.follow;
        if (!start || !end || !duration || !follow) return false;
        if (typeof start.addEventListener !== 'function') return false;

        function recompute() {
            // Unticking leaves whatever is in the field: the toggle says who
            // owns the value, and switching it off hands it back untouched.
            if (!follow.checked) return;
            var next = endFrom(start.value, duration.value);
            if (next !== null) end.value = next;
        }

        start.addEventListener('change', recompute);
        duration.addEventListener('change', recompute);
        duration.addEventListener('input', recompute);
        follow.addEventListener('change', recompute);

        // The page sets `start_at` to "now" from an inline script further up, so
        // the first pass waits a tick rather than reading an empty field. A
        // saved window is left exactly as it was found: `recompute` only writes
        // when the toggle is on *and* the duration says something.
        if (fields.onLoad) setTimeout(recompute, 0);
        return true;
    }

    root.SGExamWindow = {
        MAX_MINUTES: MAX_MINUTES,
        parseLocal: parseLocal,
        localValue: localValue,
        endFrom: endFrom,
        wire: wire
    };
})(typeof window !== 'undefined' ? window : this);
