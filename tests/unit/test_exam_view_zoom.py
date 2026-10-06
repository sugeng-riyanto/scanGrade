"""Reading the paper comfortably, without ever becoming an anti-cheat signal.

The pupil exam page now offers two things the accessibility and comfort work
asked for: a **text-size control** (`A-` / `A+`) and an **image lightbox** for the
paper's diagrams. Both are deliberately built to be invisible to the anti-cheat
ladder — the ladder watches a window `resize`, a `fullscreenchange` and a
`visibilitychange`, and either of these controls producing one would charge a
pupil for enlarging the text or reading a diagram.

The mechanism that buys that is a **CSS custom property** set on one element
(`--sg-exam-scale`, read by `.sg-exam-zoom`). It changes no window dimension, so
there is nothing for the ladder to see, and the browser's own pinch-zoom is left
untouched (WCAG 1.4.4 / 1.4.10 forbid switching it off).

`app/static/js/exam-view.js` is driven here in **node** against a two-line element
stub, so `apply()` is checked by what it *does* — one `setProperty` call on the
element it was handed — rather than by how the source reads. A `document` that
throws on any access turns "did it touch anything else?" into an assertion.

The scale is a closed set of steps, because the control is a pair of buttons: a
value between two steps could not be reached by pressing either one. The same list
lives in `app/services/user_preferences.py`, and the two are pinned together here.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from app.services import user_preferences

ROOT = Path(__file__).resolve().parents[2]
MODULE = ROOT / "app" / "static" / "js" / "exam-view.js"
EXAM = ROOT / "app" / "templates" / "student" / "take_exam.html"
BASE = ROOT / "app" / "templates" / "base.html"

NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="needs node to run the module")


def _exam() -> str:
    return EXAM.read_text(encoding="utf-8")


#: Evaluate the module and drive it. `window` is the module's own argument (its
#: IIFE ends ``})(typeof window !== 'undefined' ? window : this)``), so a plain
#: object stands in for the browser. `document` is a tripwire: any property read
#: throws and is recorded, which is how "apply touched nothing else" is proved
#: instead of asserted from the source.
HARNESS = r"""
const fs = require('fs');
const src = fs.readFileSync(process.env.SG_EXAM_VIEW_MODULE, 'utf8');

let documentTouched = false;
globalThis.document = new Proxy({}, {
    get() { documentTouched = true; throw new Error('the module reached for the document'); }
});

const win = {};
new Function('window', src)(win);
const M = win.SGExamView;
if (!M) { console.log(JSON.stringify([['the module did not export SGExamView', null, true]])); process.exit(0); }
// What the module put on the window at load time, so a later write is visible.
const windowKeys = Object.keys(win).sort().join(',');

const results = [];
function record(name, got, want) { results.push([name, got, want]); }

function el() {
    const calls = [];
    return { calls, style: { setProperty(k, v) { calls.push([k, v]); } } };
}

// ── the steps ────────────────────────────────────────────────────────────────
record('the steps it moves through', M.STEPS, null);
record('the default is the paper as drawn', M.normalize(undefined), 1.0);
record('a step is kept', M.normalize(1.15), 1.15);
record('a step that came back from storage as a string is kept', M.normalize('1.3'), 1.3);
record('a value between two steps is not adopted', M.normalize(1.2), 1.0);
record('a value past the top is not adopted', M.normalize(3), 1.0);
record('a value below the bottom is not adopted', M.normalize(0.1), 1.0);
record('a non-number is not adopted', M.normalize('big'), 1.0);
record('one press up', M.next(1.0, 1), 1.15);
record('one press down', M.next(1.0, -1), 0.85);
record('the top is a wall', M.next(1.5, 1), 1.5);
record('the bottom is a wall', M.next(0.85, -1), 0.85);
record('a press from an unreachable value starts from the paper', M.next(9, 1), 1.15);

// ── apply writes one property, on one element, and nothing more ──────────────
let e = el();
record('apply reports success', M.apply(e, 1.3), true);
record('apply sets exactly one property', e.calls.length, 1);
record('...the one the CSS reads', e.calls[0][0], '--sg-exam-scale');
record('...at the step it was given', e.calls[0][1], '1.3');
record('apply snaps an unreachable value to the paper', (function () {
    const x = el(); M.apply(x, 1.2); return x.calls[0][1];
})(), '1');
record('apply with no element is a refusal', M.apply(null, 1), false);
record('apply to a non-element is a refusal', M.apply({}, 1), false);
record('apply reached for the document', documentTouched, false);
record('apply reached for a global', Object.keys(win).sort().join(',') !== windowKeys, false);

console.log(JSON.stringify(results));
"""


@pytest.fixture(scope="module")
def driven() -> dict:
    env = {**os.environ, "SG_EXAM_VIEW_MODULE": str(MODULE)}
    proc = subprocess.run(
        [NODE, "-e", HARNESS],
        capture_output=True, text=True, timeout=60, env=env, encoding="utf-8",
    )
    assert proc.returncode == 0, f"the module did not run:\n{proc.stderr}"
    rows = json.loads(proc.stdout.strip().splitlines()[-1])
    return {"got": {name: got for name, got, _ in rows},
            "want": {name: want for name, _, want in rows}}


# ── the arithmetic ────────────────────────────────────────────────────────────

@needs_node
@pytest.mark.parametrize("name", [
    "the default is the paper as drawn",
    "a step is kept",
    "a step that came back from storage as a string is kept",
])
def test_a_reachable_step_is_kept(driven, name):
    assert driven["got"][name] == driven["want"][name]


@needs_node
@pytest.mark.parametrize("name", [
    "a value between two steps is not adopted",
    "a value past the top is not adopted",
    "a value below the bottom is not adopted",
    "a non-number is not adopted",
])
def test_an_unreachable_scale_falls_back_to_the_paper(driven, name):
    """The buttons could not have produced it, so the page must not show it."""
    assert driven["got"][name] == 1.0, (
        f"{name!r} produced {driven['got'][name]!r}; the page would then display a "
        "scale neither button can reach and jump on the next press")


@needs_node
@pytest.mark.parametrize("name", [
    "one press up",
    "one press down",
    "a press from an unreachable value starts from the paper",
])
def test_one_press_moves_one_step(driven, name):
    assert driven["got"][name] == driven["want"][name]


@needs_node
@pytest.mark.parametrize("name", ["the top is a wall", "the bottom is a wall"])
def test_the_ends_are_walls(driven, name):
    """A press at an end returns what it was given — how the caller knows to
    leave the button disabled rather than pressing into a no-op."""
    assert driven["got"][name] == driven["want"][name]


# ── the acceptance criterion: no anti-cheat signal ────────────────────────────

@needs_node
def test_applying_a_scale_writes_one_property_on_one_element(driven):
    """This is the whole reason the scale is a custom property: restyle, no event."""
    assert driven["got"]["apply reports success"] is True
    assert driven["got"]["apply sets exactly one property"] == 1
    assert driven["got"]["...the one the CSS reads"] == "--sg-exam-scale"
    assert driven["got"]["...at the step it was given"] == "1.3"


@needs_node
def test_applying_a_scale_touches_nothing_outside_its_own_element(driven):
    """A `document` tripwire and a global tripwire: one property, nothing else.

    The ladder watches the *window*, so a zoom that reached for it at all would be
    a zoom that could fire a signal. Reaching for nothing is the guarantee.
    """
    assert driven["got"]["apply reached for the document"] is False, (
        "the zoom touched the document; a viewport or fullscreen effect could follow")
    assert driven["got"]["apply reached for a global"] is False, (
        "the zoom touched a global; the anti-cheat ladder watches those")


def _code_only(src: str) -> str:
    """The module's code, with its prose removed.

    The comments are where the *reason* for this restraint is written down, so
    naming the very APIs the file must not call is expected there. The hazard is a
    call, and a call is code.
    """
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return re.sub(r"//[^\n]*", "", src)


def test_the_module_cannot_raise_a_resize_or_fullscreen_signal():
    """The code itself, because an unused listener is the same hazard as a used one."""
    src = _code_only(MODULE.read_text(encoding="utf-8"))
    for forbidden in ("resize", "orientationchange", "fullscreen", "Fullscreen",
                      "dispatchEvent", "visibilitychange", "innerWidth", "outerWidth",
                      "documentElement", "user-scalable", "userScalable", "viewport"):
        assert forbidden not in src, (
            f"exam-view.js calls {forbidden!r}; the zoom is supposed to produce no "
            "event the anti-cheat ladder can see")


def test_the_page_never_switches_the_browser_zoom_off():
    """WCAG 1.4.4 / 1.4.10: pinch-zoom belongs to the pupil, not to us."""
    for template in (EXAM, BASE):
        text = template.read_text(encoding="utf-8")
        assert "user-scalable=no" not in text
        assert "user-scalable=no," not in text
        assert "maximum-scale=1" not in text, (
            f"{template.name} caps the browser zoom, which is what the app-level "
            "zoom exists to avoid doing")


# ── the steps are one list, in two languages ─────────────────────────────────

@needs_node
def test_the_page_and_the_server_agree_about_the_steps(driven):
    """A step the server accepts must be a step a button can reach."""
    assert driven["got"]["the steps it moves through"] == list(user_preferences.TEXT_SCALES)


@pytest.mark.parametrize("step", list(getattr(user_preferences, "TEXT_SCALES", ())))
def test_the_profile_keeps_every_step_the_control_can_reach(step):
    assert user_preferences.normalize({"text_scale": step})["text_scale"] == step


@pytest.mark.parametrize("value", [1.2, 0.5, 2.0, "1.15", True, None, [1.15]])
def test_the_profile_refuses_a_scale_no_button_could_have_produced(value):
    assert "text_scale" not in user_preferences.normalize({"text_scale": value})


def test_text_scale_is_part_of_the_preference_vocabulary():
    assert user_preferences.TEXT_SCALE in user_preferences.KEYS


# ── the page half ─────────────────────────────────────────────────────────────

def test_the_page_loads_the_module_as_a_classic_script():
    """A deferred helper runs *after* Alpine, and `examApp` reads it as it is built."""
    exam = _exam()
    tags = re.findall(r"<script\b[^>]*>", exam, re.I)
    mine = [t for t in tags if "exam-view.js" in t]
    assert mine, "the zoom module is not on the page"
    assert not re.search(r"\bdefer\b", mine[0], re.I), (
        "exam-view.js is deferred; Alpine is deferred too and would start first, so "
        "`examApp` would read `window.SGExamView` as undefined")


def test_the_paper_carries_the_property_the_css_reads():
    exam = _exam()
    assert ".sg-exam-zoom { zoom: var(--sg-exam-scale, 1); }" in exam, (
        "nothing reads the scale, so pressing A+ would change a number and not the paper")
    stage = re.search(r'<div class="exam-stage sg-exam-zoom"[^>]*x-ref="examStage"', exam)
    assert stage, "the exam stage is not the box the scale is written to"


def test_the_text_size_control_exists_and_is_bilingual():
    exam = _exam()
    # From the control's comment to the last of its own labels, so both buttons and
    # every string they carry are inside the window.
    start = exam.index("stepTextScale(-1)") - 600
    end = exam.index("t('Perbesar teks','Increase text size')") + 60
    controls = exam[start:end]
    assert "A-" in controls and "A+" in controls, controls[-200:]
    for pair in ("t('Ukuran teks','Text size')",
                 "t('Perkecil teks','Decrease text size')",
                 "t('Perbesar teks','Increase text size')"):
        assert pair in controls, f"the size control has no translatable label: {pair}"


def test_the_size_buttons_stop_at_the_ends_rather_than_pressing_into_a_no_op():
    exam = _exam()
    assert "get isMinTextScale()" in exam and "get isMaxTextScale()" in exam, (
        "the ends of the scale are not exposed, so a spent button stays enabled")
    assert ':disabled="isMinTextScale"' in exam and ':disabled="isMaxTextScale"' in exam


def test_the_chosen_size_is_remembered_for_the_next_device():
    exam = _exam()
    body = exam[exam.index("stepTextScale(direction) {"):]
    body = body[:body.index("\n        openPageZoom")]
    assert "sgSavePrefs" in body, "the size is not written to the profile"
    assert "v.PREF_KEY" in body, "the write does not use the shared preference key"
    assert "STORAGE_KEY" in body, "the size is not cached on this device either"


def test_the_lightbox_is_an_overlay_inside_the_page():
    exam = _exam()
    for marker in ('id="sg-exam-lightbox"', 'id="sg-exam-lightbox-img"',
                   'id="sg-exam-lightbox-close"'):
        assert marker in exam, f"the lightbox is missing {marker}"
    assert "SGExamView.wireLightbox(" in exam, "the lightbox is never wired"
    assert "target=\"_blank\"" not in exam.split("sg-exam-lightbox")[0][-4000:], (
        "the lightbox is not allowed to open a new tab")


def _paper_image_block() -> str:
    """The `<img alt="PDF page">` element and the lines its attributes continue on."""
    lines = _exam().splitlines()
    start = next(i for i, ln in enumerate(lines) if 'alt="PDF page"' in ln)
    return "\n".join(lines[start:start + 4])


def test_the_lightbox_takes_a_named_layer_rather_than_a_hand_picked_height():
    """`test_layer_scale.py` sweeps templates for a height written as a number.

    The magnifier is a maximized viewer, so it names the layer the media player
    already established instead of inventing one.
    """
    exam = _exam()
    line = next(ln for ln in exam.splitlines() if 'id="sg-exam-lightbox"' in ln)
    assert "sg-layer-media" in line, f"the lightbox names no layer:\n{line}"
    assert not re.search(r"z-index\s*:\s*-?\d", line), (
        "the lightbox writes a stacking height by hand")


def test_losing_fullscreen_takes_the_magnifier_with_it():
    """The viewer layer is above the blocker's scrim, so the blocker closes it."""
    exam = _exam()
    block = exam[exam.index("this.fullscreenBlocked = true;"):][:400]
    assert "this.closePageZoom()" in block, (
        "the overlay survives the fullscreen blocker, so a blocked paper is still "
        "readable through the magnifier")


def test_the_paper_image_opens_the_lightbox_without_stealing_the_text_box_gesture():
    """Two gestures, one image: text mode places a box, anywhere else magnifies."""
    block = _paper_image_block()
    assert "essayCanvasMode[i]==='text' ? onPdfClick(i, $event) : openPageZoom" in block, (
        "the lightbox replaced the text-box gesture instead of coexisting with it:"
        f"\n{block}")


def test_the_paper_image_is_findable_by_the_delegated_handler():
    assert "data-lightbox" in _paper_image_block(), (
        "the delegated handler looks for data-lightbox, which the paper image does not carry")
