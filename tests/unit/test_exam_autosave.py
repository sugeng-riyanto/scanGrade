"""The exam builder's draft, written while the teacher is still writing it.

`/teacher/exams/new` is the largest form in the app and, until this, none of it
was kept until **Publish & Send to Classes** was pressed — a closed tab, a shut
lid or a school connection that dropped for a minute took the whole paper with
it. `app/static/js/exam-autosave.js` now saves it a couple of seconds after the
teacher stops changing it.

Two halves, and both are needed:

* **The loop** (`test_exam_autosave.py` drives it in **node**) — the debounce, the
  "nothing changed, write nothing" rule, the three states it paints, and the one
  save that must never be lost: an edit made *while* a request is open.
* **The two doors it writes through** (`teacher.exam_form` for a paper that does
  not exist yet, `teacher.exam_detail` once it does) — a create page's first save
  *mints* the draft, later saves write that row, and the id the page carries is
  untrusted input that must not become a write to somebody else's paper.

The load-bearing decision is the create-then-update loop. Without it the explicit
Publish would post to `/exams/new` a second time and a school would end up with
two papers for one exam — so the adoption is pinned from both ends: the page
rewrites the form action when it is handed an id, and the route refuses to write a
draft that is not this teacher's.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from app.routes import teacher as teacher_routes
from tests.unit.test_invigilation import _DB

ROOT = Path(__file__).resolve().parents[2]
MODULE = ROOT / "app" / "static" / "js" / "exam-autosave.js"
FORM = ROOT / "app" / "templates" / "teacher" / "exam_form.html"
ROUTES = ROOT / "app" / "routes" / "teacher.py"

NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="needs node to run the module")

#: Evaluate the module and drive it. `window` is the module's own argument (its
#: IIFE ends ``})(typeof window !== 'undefined' ? window : this)``), so a plain
#: object stands in for the browser and nothing global leaks between cases. Every
#: timer is injected: `setTimeout(fn, 2500)` in the module is the behaviour under
#: test, and waiting for a real one would only measure node's clock.
HARNESS = r"""
const fs = require('fs');
const src = fs.readFileSync(process.env.SG_EXAM_AUTOSAVE_MODULE, 'utf8');
const win = {};
new Function('window', 'setTimeout', 'clearTimeout', src)(win, setTimeout, clearTimeout);
const M = win.SGExamAutosave;

const results = [];
function record(name, got, want) { results.push([name, got, want]); }

/* A clock with a handle: `run` fires whatever is pending, earliest first, which is
   how the debounce is stepped through without a real wait. */
function clock() {
    let n = 0;
    const pending = new Map();
    return {
        setTimer(fn, ms) { const id = ++n; pending.set(id, { fn: fn, ms: ms }); return id; },
        clearTimer(id) { pending.delete(id); },
        waits() { return Array.from(pending.values()).map(function (p) { return p.ms; }); },
        size() { return pending.size; },
        run() {
            const all = Array.from(pending.entries());
            pending.clear();
            all.sort(function (a, b) { return a[1].ms - b[1].ms; });
            all.forEach(function (entry) { entry[1].fn(); });
        }
    };
}

function field(name, value, type, checked) {
    return { name: name, value: value, type: type || 'text', checked: !!checked };
}

/* The form stub: `elements` is what `collect` walks, `querySelector` answers the
   one selector the module asks for, and the listeners are fired by hand. */
function formStub(elements, getId) {
    const listeners = {};
    return {
        elements: elements,
        addEventListener(type, fn) { (listeners[type] = listeners[type] || []).push(fn); },
        fire(type) { (listeners[type] || []).slice().forEach(function (fn) { fn({}); }); },
        has(type) { return !!listeners[type]; },
        querySelector(sel) { return sel === '[name="draft_id"]' ? { value: getId() } : null; }
    };
}

const flush = async function () { for (let i = 0; i < 6; i++) await Promise.resolve(); };

function build(opts) {
    opts = opts || {};
    const c = clock();
    let id = opts.id || '';
    const elements = opts.elements || [field('title', 'Ulangan Harian')];
    const form = formStub(elements, function () { return id; });
    const indicator = { dataset: {}, textContent: '' };
    const states = [];
    const sends = [];
    const adopted = [];
    const replies = (opts.replies || []).slice();
    let wakeOnline = null;
    const wired = M.wire({
        form: form,
        indicator: indicator,
        read: function () { return M.collect(form); },
        send: function (state) {
            sends.push(state);
            const next = replies.length ? replies.shift() : { ok: true };
            if (next === 'reject') return Promise.reject(new Error('offline'));
            return Promise.resolve(next);
        },
        adopt: function (examId) { adopted.push(examId); id = examId; },
        label: function (state) { states.push(state); return state; },
        setTimer: c.setTimer,
        clearTimer: c.clearTimer,
        listenOnline: function (fn) { wakeOnline = fn; }
    });
    return { c: c, form: form, indicator: indicator, states: states, sends: sends,
             adopted: adopted, wired: wired, elements: elements,
             wake: function () { if (wakeOnline) wakeOnline(); },
             hasWake: function () { return !!wakeOnline; } };
}

/* Typing: the value moves *and* the event fires, because an unchanged form is
   deliberately not written (see the dedupe rule). */
function type(f, value) {
    f.elements[0].value = value;
    f.form.fire('input');
}

(async function () {
    // ── the debounce ─────────────────────────────────────────────────────────
    let a = build({});
    record('a complete page wires', a.wired, true);
    record('it listens for typing', a.form.has('input'), true);
    record('it listens for a select or a checkbox', a.form.has('change'), true);
    record('it listens for the question-list controls', a.form.has('click'), true);
    type(a, 'Ulangan Harian 1');
    record('a change is not saved at once', a.sends.length, 0);
    record('a change waits the debounce', a.c.waits()[0], M.DEBOUNCE_MS);
    a.c.run();
    await flush();
    record('the debounce then writes once', a.sends.length, 1);
    record('the states it paints', a.states.join('>'), 'saving>saved');
    record('the whole form travels to the door', a.sends[0].body.length, 1);

    let b = build({});
    type(b, 'U1'); type(b, 'U2'); type(b, 'U3');
    record('a burst of typing queues one timer', b.c.size(), 1);
    record('and writes nothing yet', b.sends.length, 0);
    b.c.run();
    await flush();
    record('a burst of typing is one write', b.sends.length, 1);

    // ── it writes only what changed ──────────────────────────────────────────
    let c3 = build({});
    type(c3, 'Changed');
    c3.c.run(); await flush();
    c3.form.fire('input');           // the same body again: a click that did nothing
    c3.c.run(); await flush();
    record('an unchanged form is not written again', c3.sends.length, 1);

    let d = build({});
    type(d, 'Ulangan 1');
    d.c.run(); await flush();
    type(d, 'Ulangan 2');
    d.c.run(); await flush();
    record('a changed field is written again', d.sends.length, 2);
    record('the second save carries the new value', d.sends[1].body[0][1], 'Ulangan 2');

    // ── a refusal is shown, and not retried on its own ───────────────────────
    let e = build({ replies: [{ ok: false, error: 'Minimal 1 soal' }] });
    type(e, 'Ulangan 1');
    e.c.run(); await flush();
    record('a refusal is shown, not swallowed', e.indicator.dataset.sgAutosave, 'error');
    record('a refusal is not retried on its own', e.sends.length, 1);
    type(e, 'Ulangan 1');           // the same body offered again after a change
    e.c.run(); await flush();
    record('the next change offers the body again', e.sends.length, 2);
    record('and a save that works says so', e.indicator.dataset.sgAutosave, 'saved');

    // ── a save that never arrived is waiting, not failed ─────────────────────
    let q = build({ replies: [{ queued: true }] });
    record('the page hands the module a way to hear the link return', q.hasWake(), true);
    type(q, 'Ulangan 1');
    q.c.run(); await flush();
    record('a request that never arrived is not painted as failed',
           q.indicator.dataset.sgAutosave, 'waiting');
    record('the states a dropped save paints', q.states.join('>'), 'saving>waiting');
    record('a queued draft waits before offering itself again', q.c.waits()[0], M.RETRY_MS);
    q.wake();
    record('the link returning pulls the wait forward', q.c.waits()[0], 0);
    q.c.run(); await flush();
    record('and the draft is then written', q.sends.length, 2);
    record('and the teacher is told it is safe', q.indicator.dataset.sgAutosave, 'saved');
    record('the retry carries the whole body', q.sends[1].body[0][1], 'Ulangan 1');

    // a transport rejection is the same queue
    let s = build({ replies: ['reject', { ok: true }] });
    type(s, 'C'); s.c.run(); await flush();
    record('a rejected request is queued, not failed', s.indicator.dataset.sgAutosave, 'waiting');
    s.wake(); s.c.run(); await flush();
    record('and written when the link is back', s.sends.length, 2);
    record('a dropped save never paints the failure state', s.states.indexOf('error'), -1);

    // typing while queued replaces the backstop with a normal save
    let r = build({ replies: [{ queued: true }, { ok: true }] });
    type(r, 'A'); r.c.run(); await flush();
    type(r, 'B'); r.c.run(); await flush();
    record('the second offer carries the newer text', r.sends[1].body[0][1], 'B');
    record('and a save that then works says so', r.indicator.dataset.sgAutosave, 'saved');

    // a reconnect with nothing waiting writes nothing
    let t = build({});
    type(t, 'D'); t.c.run(); await flush();
    const beforeWake = t.sends.length;
    t.wake();
    record('a reconnect with nothing waiting offers nothing', t.sends.length, beforeWake);

    // a refusal is still not queued and arms no retry
    let u = build({ replies: [{ ok: false, error: 'nope' }] });
    type(u, 'E'); u.c.run(); await flush();
    record('a refusal stays a refusal', u.indicator.dataset.sgAutosave, 'error');
    record('and a refusal arms no retry', u.c.size(), 0);

    // ── the id it is handed back ─────────────────────────────────────────────
    let f = build({ replies: [{ ok: true, exam_id: 'E1' }, { ok: true, exam_id: 'E1' }] });
    type(f, 'Ulangan 1');
    f.c.run(); await flush();
    record('the minted draft is adopted', f.adopted.join(','), 'E1');
    type(f, 'Ulangan 2');
    f.c.run(); await flush();
    record('and adopted only once', f.adopted.length, 1);

    // ── the save that must never be lost ─────────────────────────────────────
    let g = build({});
    type(g, 'Ulangan 1');
    g.c.run();                       // the first save is open
    type(g, 'Ulangan 2');            // the teacher types again
    g.c.run();                       // that timer fires while the request is open
    record('a save while one is open does not stack', g.sends.length, 1);
    await flush();                   // the first save settles
    record('the waiting edit is rescheduled at once', g.c.waits()[0], 0);
    g.c.run();
    await flush();
    record('the waiting edit is then written', g.sends.length, 2);
    record('and it carries the edit made mid-request', g.sends[1].body[0][1], 'Ulangan 2');

    // ── a page that did not hand over the collaborators ──────────────────────
    const noSend = { form: formStub([], function () { return ''; }) };
    record('no read or send is a refusal', M.wire(noSend), false);
    record('no form is a refusal', M.wire({ read: function () {}, send: function () {} }), false);

    // ── collect: what travels, and what must not ─────────────────────────────
    const els = [
        field('title', 'Ulangan Harian'),
        field('pdf', 'C:\\rahasia.pdf', 'file'),
        field('action', 'publish', 'submit'),
        field('draft_id', 'E1'),
        field('class_ids', 'c1', 'checkbox', true),
        field('class_ids', 'c2', 'checkbox', false),
        field('question_types', '{}')
    ];
    const got = M.collect({ elements: els, querySelector: function () { return { value: 'E1' }; } });
    record('a file input never travels with an autosave', got.body.some(function (p) { return p[0] === 'pdf'; }), false);
    record('the submit verb never travels either', got.body.some(function (p) { return p[0] === 'action'; }), false);
    record('the draft id is not part of the snapshot', got.snapshot.indexOf('draft_id'), -1);
    record('an unchecked box is absent', got.body.some(function (p) { return p[0] === 'class_ids' && p[1] === 'c2'; }), false);
    record('a checked box is present', got.body.some(function (p) { return p[0] === 'class_ids' && p[1] === 'c1'; }), true);
    record('the current id is reported separately', got.id, 'E1');

    const one = M.collect({ elements: [field('a', '1'), field('b', '2')], querySelector: function () { return { value: '' }; } });
    const two = M.collect({ elements: [field('b', '2'), field('a', '1')], querySelector: function () { return { value: '' }; } });
    record('the same page in another DOM order is the same snapshot', one.snapshot === two.snapshot, true);

    record('the debounce is in the band the brief asks for',
           M.DEBOUNCE_MS >= 2000 && M.DEBOUNCE_MS <= 3000, true);

    console.log(JSON.stringify(results));
})();
"""


@pytest.fixture(scope="module")
def driven() -> dict:
    """Every node case, run once, as ``{name: got}`` plus the expected values."""
    env = {**os.environ, "SG_EXAM_AUTOSAVE_MODULE": str(MODULE)}
    proc = subprocess.run(
        [NODE, "-e", HARNESS],
        capture_output=True, text=True, timeout=60, env=env, encoding="utf-8",
    )
    assert proc.returncode == 0, f"the module did not run:\n{proc.stderr}"
    rows = json.loads(proc.stdout.strip().splitlines()[-1])
    return {"got": {name: got for name, got, _ in rows},
            "want": {name: want for name, _, want in rows}}


@needs_node
def test_the_form_wires_the_loop_and_the_three_ways_it_changes(driven):
    """`click` is not decoration: Alpine writes the hidden question inputs through
    `:value`, which fires no event, so the question list is only noticed because a
    button was pressed."""
    for name in ("a complete page wires", "it listens for typing",
                 "it listens for a select or a checkbox",
                 "it listens for the question-list controls"):
        assert driven["got"][name] == driven["want"][name], name


@needs_node
def test_a_change_waits_the_debounce_and_is_then_written_once(driven):
    for name in ("a change is not saved at once", "a change waits the debounce",
                 "the debounce then writes once", "the whole form travels to the door"):
        assert driven["got"][name] == driven["want"][name], name


@needs_node
def test_the_debounce_is_in_the_band_the_brief_asks_for(driven):
    assert driven["got"]["the debounce is in the band the brief asks for"] is True


@needs_node
def test_typing_is_collapsed_into_one_save(driven):
    assert driven["got"]["a burst of typing queues one timer"] == 1
    assert driven["got"]["and writes nothing yet"] == 0
    assert driven["got"]["a burst of typing is one write"] == 1


@needs_node
def test_an_unchanged_form_is_never_written_again(driven):
    """The whole reason a background loop is affordable on a 1 vCPU box."""
    assert driven["got"]["an unchanged form is not written again"] == 1


@needs_node
def test_a_changed_field_is_written_again_with_its_new_value(driven):
    assert driven["got"]["a changed field is written again"] == 2
    assert driven["got"]["the second save carries the new value"] == "Ulangan 2"


@needs_node
def test_it_paints_writing_then_written(driven):
    assert driven["got"]["the states it paints"] == "saving>saved"


@needs_node
def test_a_refusal_is_shown_and_waits_for_the_teacher(driven):
    """A body the server keeps refusing must not become a request every few
    seconds; the teacher's next change is what tries again."""
    assert driven["got"]["a refusal is shown, not swallowed"] == "error"
    assert driven["got"]["a refusal is not retried on its own"] == 1
    assert driven["got"]["the next change offers the body again"] == 2
    assert driven["got"]["and a save that works says so"] == "saved"


@needs_node
def test_a_dropped_connection_is_waiting_and_written_when_the_link_returns(driven):
    """The save that never arrived is the one a school connection eats. It is not
    a failure — the body is still in the form — so it is queued, and the two ways
    out (the browser's `online` event, and the backstop timer) both write it."""
    assert driven["got"]["the page hands the module a way to hear the link return"] is True
    assert driven["got"]["a request that never arrived is not painted as failed"] == "waiting"
    assert driven["got"]["the states a dropped save paints"] == "saving>waiting"
    assert driven["got"]["a queued draft waits before offering itself again"] == driven["want"][
        "a queued draft waits before offering itself again"]
    assert driven["got"]["the link returning pulls the wait forward"] == 0
    assert driven["got"]["and the draft is then written"] == 2
    assert driven["got"]["and the teacher is told it is safe"] == "saved"
    assert driven["got"]["the retry carries the whole body"] == "Ulangan 1"


@needs_node
def test_a_rejected_request_queues_and_never_paints_the_failure_state(driven):
    """`fetch` rejecting is a connection that fell over, not a verdict on the
    draft; painting it as "Not saved" is what sends a teacher reloading."""
    assert driven["got"]["a rejected request is queued, not failed"] == "waiting"
    assert driven["got"]["and written when the link is back"] == 2
    assert driven["got"]["a dropped save never paints the failure state"] == -1


@needs_node
def test_typing_while_queued_writes_the_newer_body(driven):
    assert driven["got"]["the second offer carries the newer text"] == "B"
    assert driven["got"]["and a save that then works says so"] == "saved"


@needs_node
def test_a_reconnect_with_nothing_waiting_writes_nothing(driven):
    """An idle page that merely regained a link has nothing to offer."""
    assert driven["got"]["a reconnect with nothing waiting offers nothing"] == 1


@needs_node
def test_a_refusal_is_still_never_queued_and_arms_no_retry(driven):
    """The queue must not swallow the refusal rule: a body the server keeps
    refusing is the teacher's to fix, not something to offer every 15 seconds."""
    assert driven["got"]["a refusal stays a refusal"] == "error"
    assert driven["got"]["and a refusal arms no retry"] == 0


@needs_node
def test_the_minted_draft_is_adopted_exactly_once(driven):
    """Adopted twice is harmless; *never* adopted is two papers for one exam."""
    assert driven["got"]["the minted draft is adopted"] == "E1"
    assert driven["got"]["and adopted only once"] == 1


@needs_node
def test_an_edit_made_while_a_save_is_open_is_not_lost(driven):
    """The one race that would quietly drop a sentence the teacher typed."""
    assert driven["got"]["a save while one is open does not stack"] == 1
    assert driven["got"]["the waiting edit is rescheduled at once"] == 0
    assert driven["got"]["the waiting edit is then written"] == 2
    assert driven["got"]["and it carries the edit made mid-request"] == "Ulangan 2", (
        "the rescheduled save must carry the newer body, not the one just written")


@needs_node
def test_a_page_that_did_not_hand_over_the_collaborators_is_refused(driven):
    assert driven["got"]["no read or send is a refusal"] is False
    assert driven["got"]["no form is a refusal"] is False


@needs_node
def test_collect_keeps_the_file_and_the_submit_verb_out_of_the_body(driven):
    """A PDF re-uploaded every couple of seconds is how an autosave becomes a
    problem; the builder uploads it once through its own AJAX path."""
    assert driven["got"]["a file input never travels with an autosave"] is False
    assert driven["got"]["the submit verb never travels either"] is False


@needs_node
def test_collect_keeps_the_draft_id_out_of_the_snapshot(driven):
    """The module writes the id *after* a reply, so counting it would make every
    save look like the page changed underneath it."""
    assert driven["got"]["the draft id is not part of the snapshot"] == -1
    assert driven["got"]["the current id is reported separately"] == "E1"


@needs_node
def test_collect_reads_checkboxes_and_a_dom_order_independent_snapshot(driven):
    assert driven["got"]["an unchecked box is absent"] is False
    assert driven["got"]["a checked box is present"] is True
    assert driven["got"]["the same page in another DOM order is the same snapshot"] is True


# ── the create-then-update loop, behaviourally ───────────────────────────────
#
# `_owned_draft` is the guard that decides whether an id the page carried is a
# draft this teacher may keep writing. It is a *write* door's precondition, so it
# is checked against the same fake database the invigilation tests use rather than
# asserted from the source text.

def _draft_row(**over):
    row = {"id": "e1", "teacher_id": "u1", "school_id": "s1",
           "status": "draft", "is_published": False}
    row.update(over)
    return row


def _owned(**over):
    return teacher_routes._owned_draft(_DB({"exams": [_draft_row(**over)]}),
                                       "e1", "s1", "u1")


def test_the_teachers_own_draft_is_the_row_the_next_save_writes():
    row = _owned()
    assert row and row["id"] == "e1"


def test_another_schools_draft_is_never_written():
    assert _owned(school_id="s2") is None


def test_another_teachers_draft_is_never_written():
    """The id travels in the page, so it is input, not authority."""
    assert _owned(teacher_id="u2") is None


def test_a_published_paper_is_not_a_draft_to_keep_writing():
    assert _owned(is_published=True) is None


def test_a_paper_that_already_left_draft_is_not_written_as_a_draft():
    assert _owned(status="active") is None


def test_an_id_that_names_no_row_makes_the_caller_mint_one():
    """A draft deleted in another tab must not turn the next autosave into a 500."""
    assert teacher_routes._owned_draft(_DB({"exams": []}), "gone", "s1", "u1") is None


def test_no_id_at_all_makes_the_caller_mint_one():
    assert teacher_routes._owned_draft(_DB({"exams": []}), None, "s1", "u1") is None


def test_a_row_with_no_status_is_still_a_draft():
    """The column is nullable in older rows, and the app reads a missing status as
    `draft` everywhere else (`_publication_state`)."""
    row = teacher_routes._owned_draft(
        _DB({"exams": [{"id": "e1", "teacher_id": "u1", "school_id": "s1"}]}),
        "e1", "s1", "u1")
    assert row and row["id"] == "e1"


# ── the two save doors ───────────────────────────────────────────────────────

def _routes() -> str:
    return ROUTES.read_text(encoding="utf-8")


def test_both_doors_read_the_autosave_verb_and_the_draft_id():
    src = _routes()
    assert src.count('    autosave = action == "autosave"') == 2, (
        "the create door and the edit door both have to know a save is an autosave")


def test_one_refusal_helper_answers_both_a_json_and_a_browser_save():
    src = _routes()
    assert src.count("return _exam_save_refused(autosave,") == 6, (
        "each door has three refusals — the question count, the window order and "
        "the assessment calendar — and every one of them has to answer an autosave "
        "with a sentence rather than a redirect")
    assert 'return jsonify({"ok": False, "error": message}), 400' in src, (
        "an autosave must never navigate")
    assert 'flash(message, "error")' in src and 'return redirect(request.referrer or "/teacher/exams")' in src, (
        "a browser save keeps the flash and the redirect it has always had")


def test_the_autosave_stops_before_anything_that_is_not_a_draft_write():
    """The weight-gap flash, the PDF re-upload, the publish side effects and the
    per-pupil roster sync all belong to a *reported* save."""
    src = _routes()
    marker = ('    if autosave:\n'
              '        return jsonify({"ok": True, "exam_id": exam_id})\n'
              '    _sync_exam_targets(supabase, exam_id, class_ids, request.form)')
    assert src.count(marker) == 2, (
        "both doors must answer an autosave before the roster sync and the flash")


def test_the_create_door_writes_the_draft_the_page_holds_instead_of_a_second_row():
    src = _routes()
    assert 'draft_row = _owned_draft(supabase, draft_id, g.get("user_school_id"), g.user_id)' in src
    assert src.count("        if draft_row:\n") == 2, (
        "the insert and its fallback both have to prefer the draft row")


def test_the_create_door_ages_the_paper_only_when_it_mints_the_row():
    """A "created" audit entry every few seconds is not an audit trail."""
    assert '    if not draft_row:\n        log_activity("create", "exam"' in _routes()


def test_owned_draft_refuses_the_three_rows_that_are_not_this_teachers_draft():
    src = _routes()
    assert 'if str(row.get("school_id") or "") != str(school_id or ""):' in src
    assert 'if str(row.get("teacher_id") or "") != str(user_id or ""):' in src
    assert 'if row.get("is_published") or (row.get("status") or "draft") != "draft":' in src


# ── the page ─────────────────────────────────────────────────────────────────

def _form() -> str:
    return FORM.read_text(encoding="utf-8")


def test_the_form_loads_the_autosave_module():
    assert '<script src="/static/js/exam-autosave.js"></script>' in _form(), (
        "the loop is not on the page, so nothing is saved until Publish is pressed")


def test_the_form_carries_the_draft_id_and_the_edit_address_it_will_need():
    form = _form()
    assert 'name="draft_id"' in form, "the draft the page is writing is not in the form"
    assert 'data-save-url="{{ url_for(' in form, (
        "the edit address has to come from the server's own url_for, not a path "
        "spelled out in JavaScript")


def test_the_status_line_exists_and_starts_quiet():
    form = _form()
    tag = re.search(r'<span id="autosave-status"[^>]*>', form)
    assert tag, "there is no autosave status line"
    assert 'data-sg-autosave="idle"' in tag.group(0)
    assert 'aria-live="polite"' in tag.group(0), (
        "a status a screen reader never announces is a status half the teachers do not have")


def test_the_page_hands_the_module_its_read_its_send_and_its_adopt():
    form = _form()
    tail = form[form.index("SGExamAutosave.wire("):]
    for hook in ("read:", "send:", "adopt:", "label:"):
        assert hook in tail, f"the wiring does not supply {hook}"


def test_the_send_posts_the_verb_the_draft_id_and_asks_for_json():
    """The JSON accept header is not cosmetic: `_wants_json` is what makes a
    refusal arrive as a sentence instead of a redirect the fetch follows."""
    form = _form()
    tail = form[form.index("SGExamAutosave.wire("):]
    assert "body.append('action', 'autosave')" in tail
    assert "body.append('draft_id', state.id || '')" in tail
    assert "headers: { 'Accept': 'application/json' }" in tail


def test_adopting_the_id_rewrites_the_form_action():
    """The duplicate-paper guard: after the first save, the explicit Publish must
    post to `/teacher/exams/<id>`, not to `/exams/new` again."""
    form = _form()
    tail = form[form.index("SGExamAutosave.wire("):]
    assert "idField.value = examId" in tail
    assert "form.setAttribute('action'," in tail
    assert "form.getAttribute('data-save-url').replace('SG_DRAFT_ID', examId)" in tail


def test_the_status_words_are_bilingual_pairs():
    """A literal in the module would be the one sentence on the page the language
    toggle could not reach."""
    form = _form()
    tail = form[form.index("SGExamAutosave.wire("):]
    for pair in ("sgT('Menyimpan…', 'Saving…')",
                 "sgT('Tersimpan otomatis', 'Saved automatically')",
                 "sgT('Gagal menyimpan', 'Not saved')"):
        assert pair in tail, f"the status {pair!r} is not a bilingual pair"


def test_the_waiting_words_say_waiting_and_say_it_in_both_languages():
    """The whole point of the state: a teacher reading "Not saved" reloads the
    page to chase a draft they never lost. Waiting says where the paper is."""
    form = _form()
    tail = form[form.index("SGExamAutosave.wire("):]
    assert "if (state === 'waiting')" in tail, "no state for a save that is queued"
    assert "Menunggu koneksi" in tail and "Waiting for connection" in tail, (
        "the waiting words are not a bilingual pair")


def test_the_send_queues_a_request_that_never_reached_the_server():
    """A dropped link, a 5xx and an unreadable body are all the same thing from the
    teacher's side or the draft's: the server never judged it, so it waits rather
    than being painted as failed. A 4xx is left to the refusal path."""
    form = _form()
    tail = form[form.index("SGExamAutosave.wire("):]
    assert "if (reply.status >= 500) return { queued: true };" in tail, (
        "a server that could not answer is not a verdict on the draft")
    assert "if (data === null) return { queued: true };" in tail, (
        "a body we could not read must not be reported as saved")
    assert ".catch(function () { return { queued: true }; })" in tail, (
        "a fetch that rejects (offline, DNS, reset) must queue, not fail the draft")
