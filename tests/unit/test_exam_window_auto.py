"""The duration and the assignment window end, kept in step instead of guessed.

Two clocks sit side by side on `/teacher/exams/new` and mean different things:
**Duration** is counted from the moment *this* student starts, while the
**Assignment window end** is the last instant anyone may *begin*. Nothing tied
them together, so a 60-minute paper was routinely given a two-day window — and
the pupil who began at the far end of it met the window's own deadline a few
minutes into a paper they were entitled to sit for the full hour.

`app/static/js/exam-window.js` now computes the second from the first, behind a
toggle that is on by default and can be switched off when a school wants a long
window (five classes rotating through one afternoon). These tests run the module
in **node** against a two-line element stub, so what is checked is the number it
writes and the moment it writes it — not the shape of the source.

The two rules the arithmetic has to keep, both of which a naive
``start + minutes`` gets wrong:

* **`0` is Unlimited.** `duration_facts` and the exam's own `deadline()` already
  read a stored 0 that way; a module that added nothing and wrote the start back
  would set a deadline the rest of the app does not believe in.
* **A saved window is never rewritten by a page load.** Only an input the teacher
  actually touches recomputes it. A *new* paper has no window yet, so its one
  pass fills an empty field rather than changing a chosen one.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
MODULE = ROOT / "app" / "static" / "js" / "exam-window.js"
FORM = ROOT / "app" / "templates" / "teacher" / "exam_form.html"

NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="needs node to run the module")

#: Evaluate the module and drive it. `window` is the module's own argument (its
#: IIFE ends ``})(typeof window !== 'undefined' ? window : this)``), so a plain
#: object stands in for the browser and nothing global leaks between tests.
#: `setTimeout` runs its callback at once: the module defers its on-load pass by
#: a tick on purpose, and waiting for a real one would only test node's timer.
HARNESS = r"""
const fs = require('fs');
// The path rides in an environment variable: with `node -e` there is no argv
// slot for one, and a temp file would only add a second thing to clean up.
const src = fs.readFileSync(process.env.SG_EXAM_WINDOW_MODULE, 'utf8');
const win = {};
new Function('window', 'setTimeout', src)(win, (fn) => fn());
const M = win.SGExamWindow;

function el(value) {
    return {
        value: value === undefined ? '' : value,
        checked: false,
        handlers: {},
        addEventListener(type, fn) { (this.handlers[type] = this.handlers[type] || []).push(fn); },
        fire(type) { (this.handlers[type] || []).forEach((fn) => fn()); }
    };
}

const results = [];
function record(name, got, want) { results.push([name, got, want]); }

// ── the arithmetic ────────────────────────────────────────────────────────────
record('an hour from the start', M.endFrom('2026-10-06T07:30', 60), '2026-10-06T08:30');
record('the five-minute step', M.endFrom('2026-10-06T07:30', 5), '2026-10-06T07:35');
record('a window that crosses midnight', M.endFrom('2026-10-06T23:40', 45), '2026-10-07T00:25');
record('seconds in the value are tolerated', M.endFrom('2026-10-06T07:30:00', 60), '2026-10-06T08:30');
record('zero is Unlimited, not zero minutes', M.endFrom('2026-10-06T07:30', 0), null);
record('an empty duration has no answer', M.endFrom('2026-10-06T07:30', ''), null);
record('a duration past the field ceiling is refused', M.endFrom('2026-10-06T07:30', 601), null);
record('a missing start has no answer', M.endFrom('', 60), null);
record('a start that is not a date has no answer', M.endFrom('half past seven', 60), null);
record('a rolled-over date is refused, not silently moved',
       M.endFrom('2026-02-30T09:00', 60), null);
record('the value round-trips',
       M.localValue(M.parseLocal('2026-10-06T07:30')), '2026-10-06T07:30');

// ── the wiring ────────────────────────────────────────────────────────────────
function fields(start, end, minutes, follow, onLoad) {
    const s = el(start), e = el(end), d = el(minutes), f = el('');
    f.checked = follow;
    const ok = M.wire({ start: s, end: e, duration: d, follow: f, onLoad: onLoad });
    return { s, e, d, f, ok };
}

let f = fields('2026-10-06T07:30', '', '60', true, false);
f.d.fire('change');
record('a duration the teacher types moves the window end', f.e.value, '2026-10-06T08:30');

f = fields('2026-10-06T07:30', '', '45', true, false);
f.d.fire('input');
record('the same holds while typing', f.e.value, '2026-10-06T08:15');

f = fields('2026-10-06T09:00', '2026-10-20T12:00', '60', false, false);
f.d.fire('change');
record('unticked, the window field is the teacher\u2019s', f.e.value, '2026-10-20T12:00');

f = fields('2026-10-06T07:30', '', '60', true, true);
record('a paper with no window yet is filled on load', f.e.value, '2026-10-06T08:30');

f = fields('2026-10-06T07:30', '2026-10-20T12:00', '60', true, false);
record('a saved window is left alone by a page load', f.e.value, '2026-10-20T12:00');

f = fields('2026-10-06T07:30', '2026-10-20T12:00', '60', false, false);
f.f.checked = true;
f.f.fire('change');
record('re-ticking hands the field back to the arithmetic', f.e.value, '2026-10-06T08:30');

f = fields('2026-10-06T07:30', '', '60', true, false);
f.s.value = '2026-10-06T10:00';
f.s.fire('change');
record('moving the start moves the end', f.e.value, '2026-10-06T11:00');

f = fields('2026-10-06T07:30', '2026-10-20T12:00', '0', true, false);
f.d.fire('change');
record('Unlimited leaves the window as the teacher set it', f.e.value, '2026-10-20T12:00');

record('a missing field is a refusal, not a crash',
       M.wire({ start: el('x'), end: null, duration: el('60'), follow: el('') }), false);
record('a start that cannot listen is a refusal, not a crash',
       M.wire({ start: { value: 'x' }, end: el(''), duration: el('60'), follow: el('') }), false);

console.log(JSON.stringify(results));
"""


@pytest.fixture(scope="module")
def driven() -> dict:
    """Every case, run once in node, as ``{name: got}`` plus the expected values."""
    env = {**os.environ, "SG_EXAM_WINDOW_MODULE": str(MODULE)}
    proc = subprocess.run(
        [NODE, "-e", HARNESS],
        capture_output=True, text=True, timeout=60, env=env, encoding="utf-8",
    )
    assert proc.returncode == 0, f"the module did not run:\n{proc.stderr}"
    rows = json.loads(proc.stdout.strip().splitlines()[-1])
    return {"got": {name: got for name, got, _ in rows},
            "want": {name: want for name, _, want in rows}}


@needs_node
@pytest.mark.parametrize("name", [
    "an hour from the start",
    "the five-minute step",
    "a window that crosses midnight",
    "seconds in the value are tolerated",
])
def test_the_window_end_is_the_start_plus_the_duration(driven, name):
    assert driven["got"][name] == driven["want"][name]


@needs_node
@pytest.mark.parametrize("name", [
    "zero is Unlimited, not zero minutes",
    "an empty duration has no answer",
    "a duration past the field ceiling is refused",
    "a missing start has no answer",
    "a start that is not a date has no answer",
    "a rolled-over date is refused, not silently moved",
])
def test_it_refuses_rather_than_inventing_a_deadline(driven, name):
    """A wrong answer is worse than no answer: the caller leaves the field alone."""
    assert driven["got"][name] is None, (
        f"{name!r} produced {driven['got'][name]!r} where the field must be left as it was")


@needs_node
def test_the_value_it_writes_is_one_the_field_can_read(driven):
    assert driven["got"]["the value round-trips"] == "2026-10-06T07:30"


@needs_node
@pytest.mark.parametrize("name,case", [
    ("a duration the teacher types moves the window end", "change"),
    ("the same holds while typing", "input"),
    ("re-ticking hands the field back to the arithmetic", "follow"),
    ("moving the start moves the end", "start"),
])
def test_the_teacher_s_own_change_recomputes_the_window(driven, name, case):
    assert driven["got"][name] == driven["want"][name], f"({case}: {name})"


@needs_node
@pytest.mark.parametrize("name", [
    "unticked, the window field is the teacher’s",
    "Unlimited leaves the window as the teacher set it",
])
def test_it_never_writes_what_it_was_not_asked_to(driven, name):
    assert driven["got"][name] == driven["want"][name]


@needs_node
def test_a_page_load_fills_an_empty_window_but_never_rewrites_a_saved_one(driven):
    """The whole difference between the two passes, in one assertion."""
    assert driven["got"]["a paper with no window yet is filled on load"] == "2026-10-06T08:30"
    assert driven["got"]["a saved window is left alone by a page load"] == "2026-10-20T12:00"


@needs_node
@pytest.mark.parametrize("name", [
    "a missing field is a refusal, not a crash",
    "a start that cannot listen is a refusal, not a crash",
])
def test_an_incomplete_page_is_refused_rather_than_thrown_at(driven, name):
    assert driven["got"][name] is False


# ── the form half ─────────────────────────────────────────────────────────────

def _form() -> str:
    return FORM.read_text(encoding="utf-8")


def test_the_form_loads_the_module():
    assert '<script src="/static/js/exam-window.js"></script>' in _form(), (
        "the arithmetic is not on the page, so the window is still the teacher's to work out")


def test_the_toggle_is_on_by_default_and_can_be_switched_off():
    """Default ON is the ask; the untick is what a rotating school needs."""
    form = _form()
    tag = re.search(r"<input type=\"checkbox\" id=\"window_follow_duration\"[^>]*>", form)
    assert tag, "the window-end field has no follow toggle"
    assert "checked" in tag.group(0), "the follow toggle opens off, which is not the default asked for"


def test_the_toggle_is_named_for_a_screen_reader_in_both_languages():
    form = _form()
    block = form[form.index("window_follow_duration"): form.index("window_follow_duration") + 1200]
    assert re.search(r"t\('[^']+','[^']+'\)", block), (
        "the toggle carries no bilingual pair, so the language toggle cannot reach it")


def test_the_page_wires_the_three_fields_by_the_names_the_route_posts():
    """The names are the contract with `exam_form`'s POST branch."""
    form = _form()
    tail = form[form.index("SGExamWindow.wire("):]
    for field in ('input[name="duration_minutes"]', "start_at_input", "end_at_input"):
        assert field in tail, f"the wiring does not read {field}, so it cannot be kept in step"


def test_o_load_is_decided_by_whether_a_window_was_saved():
    """`not exam.end_at` — the one condition that separates fill from overwrite."""
    form = _form()
    tail = form[form.index("SGExamWindow.wire("):]
    assert re.search(r"onLoad:\s*\{\{\s*'true' if \(not exam or not exam\.end_at\)", tail), (
        "the on-load pass is not gated on the paper having no window yet")


def test_auto_submit_on_the_window_end_opens_ticked_for_a_new_paper():
    """It was off, and the comment on the card said the wrong thing about it.

    A paper whose window ends while a sitting is still running left that sitting
    open, and the teacher had to know to tick a box they were never given a
    reason for. The checkbox is the only thing that decides what the route reads,
    so the default lives here.
    """
    form = _form()
    block = form[form.index("auto_submit_window_end:"): form.index("auto_submit_window_end:") + 120]
    assert "if exam else True" in block, (
        "a new paper still opens with auto-submit off:\n" + block)


def test_an_existing_paper_keeps_the_value_it_stored():
    """The default is for a new paper only — a saved choice is not a default."""
    form = _form()
    block = form[form.index("auto_submit_window_end:"): form.index("auto_submit_window_end:") + 120]
    assert "exam.get('auto_submit_on_window_end')" in block, (
        "the stored value is no longer read, so every saved paper would be re-defaulted")
