"""The strip a student sits in front of: their own name, and the controls the room needs.

Reported as: during an exam the paper names the exam but never the person sitting
it, and the switches a student reaches for — light or dark, Indonesian or English,
is the connection up, how loud is this page, is the microphone live, how much
battery is left — are either missing or drawn in a bar that cannot be touched.

Two of the requested controls had nothing to control, and that is as much what
this file is about as the markup is:

* the exam page played **no sound at all** (no `Audio`, no `AudioContext`, no
  sound file), so a volume slider would have been a control over nothing. It now
  makes two alerts — the deadline warnings and the connection changes — through
  one gain node, and the slider *is* that gain. A web page cannot change the
  system volume, and this one does not claim to.
* the exam page **never opens the microphone** (the only `getUserMedia` in the
  repository is the teacher OMR scanner). The microphone is therefore drawn as a
  *status*, not a switch: a switch would have to ask for microphone permission in
  the middle of a paper, so that two students could hold a stream nothing uses.

What is asserted, and why each assertion is here:

1. The route hands the page a name and a class, and the name costs the *measured*
   page nothing. `/student/exams/<id>` is one of the pages the load harness
   drives (`loadtest_concurrent.py`), so an unconditional extra round-trip here
   is a capacity change, not a detail.
2. The alert gain is real: the peak the page schedules scales with the slider,
   and a muted page schedules nothing at all.
3. Each deadline warning fires once, at its own mark, and only while the paper is
   the thing in front of the student.
4. Losing and regaining the connection is audible, and the indicator follows.
5. The microphone is never requested, and the chip has no handler that could ask.
6. Every word the strip adds is bilingual, and none of it freezes its language by
   being built with `sgT()` — a label in the markup follows the toggle, a label
   stored in component state does not.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
EXAM_PAGE = ROOT / "app" / "templates" / "student" / "take_exam.html"
STUDENT_ROUTE = ROOT / "app" / "routes" / "student.py"

NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="needs node to run the component")


def source(path):
    return path.read_text(encoding="utf-8")


#: The strip's own markers, so a test reads the element and not the whole file.
STRIP_START = "<!-- sit strip -->"
STRIP_END = "<!-- /sit strip -->"


def strip() -> str:
    text = source(EXAM_PAGE)
    start = text.index(STRIP_START)
    return text[start:text.index(STRIP_END, start)]


# ── running the component's own methods ──────────────────────────────────────
#
# The methods are read out of the template and evaluated, rather than asserted
# against by pattern: a beep that schedules zero on every tick passes any
# source-level check, and the defect this strip exists for is a control that
# looks wired and is not.

def _method(name: str) -> str:
    """The component method ``name`` as the template writes it, brace included.

    The closing brace is part of the method: sliced to the comma that follows it,
    the extracted text is missing it, and every node run then fails on a syntax
    error that looks like the method is broken rather than the harness.
    """
    text = source(EXAM_PAGE)
    start = text.index(f"        {name}(")
    end = text.index("\n        },", start) + len("\n        }")
    return text[start:end]


def strip_without_comments() -> str:
    """The strip's markup with its own prose taken out.

    The comments explain the controls and are not rendered, so a rule about what
    the *page* says has to read what the page actually carries — otherwise an
    apostrophe in a sentence about the defect reads as the defect.
    """
    return re.sub(r"<!--.*?-->", "", strip(), flags=re.S)


def _node(script: str):
    done = subprocess.run([NODE, "-e", script], capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout.strip())


DEADLINE_TICKS = """
globalThis.document = {{ hidden: {hidden} }};
const calls = [];
const obj = {{
  _alerted: {{ five: false, one: false }},
  timeLeft: 0,
  submitted: {submitted},
  showExamAgreement: {agreement},
  beep(n, f) {{ calls.push([n, f]); }},
{method},
}};
for (const t of {ticks}) {{ obj.timeLeft = t; obj._deadlineAlert(); }}
console.log(JSON.stringify(calls));
"""


def _deadline_ticks(ticks, *, hidden=False, submitted=False, agreement=False):
    return _node(DEADLINE_TICKS.format(
        hidden=str(bool(hidden)).lower(),
        submitted=str(bool(submitted)).lower(),
        agreement=str(bool(agreement)).lower(),
        method=_method("_deadlineAlert"),
        ticks=json.dumps(list(ticks)),
    ))


BEEP = """
const peaks = [];
let oscillators = 0;
const ctx = {{
  currentTime: 0, state: 'running', destination: {{}}, resume() {{}},
  createOscillator() {{
    oscillators += 1;
    return {{ type: '', frequency: {{ value: 0 }}, connect() {{}}, start() {{}}, stop() {{}} }};
  }},
  createGain() {{
    return {{ gain: {{ setValueAtTime() {{}},
                       linearRampToValueAtTime(v) {{ peaks.push(v); }} }},
              connect() {{}} }};
  }},
}};
const obj = {{
  alertVolume: {volume},
  alertMuted: {muted},
  _audioCtx: null,
{methods},
}};
obj._audioCtx = {ctx};
obj.beep({count}, 880);
console.log(JSON.stringify({{ peaks, oscillators, volume: obj.alertVolume,
                             muted: obj.alertMuted }}));
"""


def _beep(volume=0.5, muted=False, count=2):
    return _node(BEEP.format(
        volume=json.dumps(volume),
        muted=str(bool(muted)).lower(),
        methods=",\n".join([_method("_alertGain"), _method("beep")]),
        ctx="ctx",
        count=count,
    ))


VOLUME = """
const store = {{}};
const beeps = [];
const obj = {{
  alertVolume: {volume},
  alertMuted: {muted},
  _remember(k, v) {{ store[k] = v; }},
  beep(n, f) {{ beeps.push([n, f]); }},
{methods},
}};
obj.setAlertVolume({percent});
const afterSet = [obj.alertVolume, obj.alertMuted];
{toggle}
console.log(JSON.stringify({{ afterSet, afterToggle: [obj.alertVolume, obj.alertMuted],
                             store, beeps }}));
"""


def _volume(percent, volume=0.5, muted=False, *, toggle=True):
    return _node(VOLUME.format(
        volume=json.dumps(volume),
        muted=str(bool(muted)).lower(),
        methods=",\n".join([_method("setAlertVolume"), _method("toggleAlertMute")]),
        percent=json.dumps(str(percent)),
        toggle="obj.toggleAlertMute();" if toggle else "",
    ))


CONNECTION = """
const calls = [];
const obj = {{
  syncStatus: '', isOnline: {online}, canvasDirty: {{}},
  dirty: false, pendingSync: false,
  setSyncLabel() {{}},
  saveToLocal() {{}},
  syncToServer() {{}},
  syncCanvasToServer() {{}},
  $nextTick(fn) {{ if (fn) fn(); }},
  beep(n, f) {{ calls.push([n, f]); }},
{method},
}};
obj.{name}();
console.log(JSON.stringify({{ online: obj.isOnline, calls }}));
"""


def _connection(name, *, online):
    return _node(CONNECTION.format(
        online=str(bool(online)).lower(),
        method=_method(name),
        name=name,
    ))


# ── 1. who is sitting the paper ──────────────────────────────────────────────

def test_the_page_names_the_student_and_their_class():
    template = source(EXAM_PAGE)
    assert "sg-sit-who" in strip(), "the strip does not carry the student"
    assert "{{ student_name" in template, "the name the route resolves is never printed"
    assert "{{ student_class_label" in template, "the class is never printed"


def test_the_route_hands_the_page_a_name_and_a_class():
    route = source(STUDENT_ROUTE)
    assert "student_name=" in route and "student_class_label=" in route, (
        "the exam route does not pass an identity to the page it renders")
    assert "class_row(" in route, (
        "the class label is not read through the cached class row, so every "
        "student of a class pays for the same query")


def test_the_name_costs_the_measured_exam_page_nothing():
    """`/student/exams/<id>` is one of the pages the load harness drives.

    The session already carries the student's name, so the common path has to be
    that value; only a blank one may cost a query, and that query has to be a
    per-request memo rather than a second read of the same row.
    """
    route = source(STUDENT_ROUTE)
    body = route[route.index("def take_exam("):route.index(
        'render_template("student/take_exam.html"')]

    assert 'g.get("user_name")' in body, (
        "the name no longer comes from the session, so the measured page pays a "
        "round-trip it does not need")
    assert "if not student_name:" in body, (
        "there is no guard, so the profile is read on every load of the exam page")
    assert body.index("if not student_name:") < body.index('table("profiles").select("full_name")'), (
        "the profile read happens before the blank check, which is the same as "
        "having no check at all")
    assert body.count('table("profiles").select("full_name")') == 1, (
        "the fallback runs more than once per page")


# ── 2. the six controls, on one row ──────────────────────────────────────────

def test_the_strip_carries_the_identity_and_the_six_controls():
    block = strip()
    for hook in ("sg-sit-who", "sg-sit-net", "sg-sit-vol", "sg-sit-mic", "sg-sit-bat"):
        assert hook in block, f"the strip has no {hook}"

    assert "toggleDark()" in block, "the strip cannot switch the theme"
    assert "setLang(lang === 'id' ? 'en' : 'id')" in block, (
        "the strip cannot switch the language, which is the moment the terms "
        "modal and the fullscreen blocker make the navbar unreachable")
    assert "setAlertVolume(" in block, "the volume slider is not wired to anything"
    assert ":value=\"alertVolume * 100\"" in block, (
        "the slider does not show the stored level, so it snaps back on every load")


def test_the_strip_is_a_control_strip_and_not_another_read_only_bar():
    """The bar this replaces was `pointer-events-none select-none`: it listed the
    battery, the network and the audio devices, and a student could not touch one
    of them."""
    text = source(EXAM_PAGE)
    assert "pointer-events-none select-none" not in text, (
        "the read-only device bar is still beside the strip, so the page says the "
        "same things twice and only one of the two rows can be touched")
    assert "<!-- Device Status Bar -->" not in text, (
        "the bar the strip replaces is still in the markup")


# ── 3. the volume is a real gain ─────────────────────────────────────────────

@needs_node
def test_the_alert_peak_scales_with_the_slider():
    half = _beep(volume=0.5)
    quiet = _beep(volume=0.25)

    assert half["oscillators"] == 2, "the alert is not two tones"
    assert half["peaks"], "nothing was scheduled, so the slider governs silence"
    assert max(half["peaks"]) == pytest.approx(0.28 * 0.5, abs=1e-9)
    assert max(quiet["peaks"]) == pytest.approx(0.28 * 0.25, abs=1e-9)
    assert max(quiet["peaks"]) < max(half["peaks"]), (
        "the slider does not change the level, so it is decoration")


@needs_node
def test_a_muted_page_schedules_nothing():
    assert _beep(muted=True)["peaks"] == [], (
        "a muted page still schedules a tone, so the mute button does not mute")
    assert _beep(volume=0.0)["peaks"] == [], (
        "a slider at zero still makes a sound")


@needs_node
def test_a_device_without_audio_is_a_courtesy_that_is_skipped_not_a_crash():
    """No `AudioContext` (an older browser, a locked-down school device) must cost
    the sound and nothing else."""
    done = subprocess.run([NODE, "-e", BEEP.format(
        volume="0.5", muted="false",
        methods=",\n".join([_method("_alertGain"), _method("beep")]),
        ctx="null", count=2)], capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    assert json.loads(done.stdout.strip())["peaks"] == []


@needs_node
def test_the_level_is_clamped_and_remembered():
    loud = _volume(150)
    assert loud["afterSet"][0] == 1.0, "the slider can store a level above full"
    assert float(loud["store"]["sg_exam_alert_volume"]) == 1.0, (
        "the level is not remembered, so the next exam starts somewhere else")


@needs_node
def test_a_slider_left_at_zero_stays_silent_on_the_next_paper():
    off = _volume(0, toggle=False)
    assert off["afterSet"] == [0.0, False]
    assert float(off["store"]["sg_exam_alert_volume"]) == 0.0, (
        "a level of zero is not remembered, so the next paper starts audible")
    assert off["store"]["sg_exam_alert_muted"] == "0", (
        "the stored mute flag disagrees with the live one, so a reload lands "
        "somewhere the student did not leave it")


@needs_node
def test_raising_the_slider_while_muted_unmutes_it():
    """Otherwise a student pushes the level up, hears nothing, and concludes the
    slider is broken."""
    up = _volume(50, muted=True, toggle=False)
    assert up["afterSet"] == [0.5, False]
    assert up["store"]["sg_exam_alert_muted"] == "0", (
        "the page unmuted but the stored flag still says muted")


@needs_node
def test_unmuting_something_that_is_already_silent_gives_it_a_level_back():
    """Otherwise the unmute button restores a zero and the student presses it
    twice, once to see nothing happen."""
    out = _volume(0, volume=0.0, muted=True)
    assert out["afterToggle"] == [0.5, False], (
        "unmuting a muted page whose level is zero leaves it silent")
    assert out["beeps"], "the unmute is not announced"


# ── 4. the alerts themselves ─────────────────────────────────────────────────

@needs_node
def test_each_deadline_warning_fires_once_at_its_own_mark():
    calls = _deadline_ticks([600, 301, 300, 299, 120, 61, 60, 59, 10, 1])
    assert calls == [[2, 880], [3, 990]], (
        "the deadline warnings do not fire once each at the five-minute and "
        f"one-minute marks (saw {calls})")


@needs_node
def test_a_warning_is_not_heard_from_a_tab_nobody_is_watching():
    """A beep from a background tab tells nobody anything, and the anti-cheat is
    already counting that tab change as something else."""
    assert _deadline_ticks([300, 60], hidden=True) == []


@needs_node
def test_a_finished_or_unstarted_paper_does_not_beep():
    assert _deadline_ticks([300, 60], submitted=True) == []
    assert _deadline_ticks([300, 60], agreement=True) == [], (
        "the terms modal is not the paper; a warning under it is a warning about "
        "a clock the student has not started")


@needs_node
def test_a_jump_past_a_mark_warns_about_the_mark_that_is_still_ahead():
    """The server reconciles the clock and the number can leap. Warning about a
    five-minute mark that is already behind would be a warning about nothing."""
    assert _deadline_ticks([400, 30, 29, 1]) == [[3, 990]]


@needs_node
def test_losing_and_regaining_the_connection_is_audible():
    gone = _connection("onGoOffline", online=True)
    assert gone["online"] is False, "the offline indicator does not follow"
    assert gone["calls"] == [[2, 330]], (
        "losing the connection is silent, and it is the moment a student needs to know")

    back = _connection("onGoOnline", online=False)
    assert back["online"] is True, "the online indicator does not follow"
    assert back["calls"] == [[2, 660]]


def test_the_indicator_is_read_from_state_not_from_the_browser_each_render():
    """`navigator.onLine` is not reactive: bound straight into the markup it is
    read once when Alpine walks the tree, so the pill keeps saying Online long
    after the connection has gone."""
    block = strip_without_comments()
    assert "sg-sit-net" in block
    assert "isOnline" in block, "the connectivity pill is not bound to reactive state"
    assert "navigator.onLine" not in block, (
        "the pill reads the browser property directly, which never re-renders")
    assert "isOnline: navigator.onLine" in source(EXAM_PAGE), (
        "the state is never seeded, so the pill starts on a guess")


# ── 5. the microphone is a status, not a switch ──────────────────────────────

def test_the_microphone_is_never_requested_on_the_exam_page():
    text = source(EXAM_PAGE)
    assert "getUserMedia" not in text, (
        "the exam page asks for the microphone, which puts a permission dialog in "
        "the middle of a paper")
    assert "MediaRecorder" not in text
    assert "AudioWorklet" not in text


def test_the_microphone_chip_says_why_it_does_nothing_and_has_no_handler():
    block = strip()
    start = block.index("sg-sit-mic")
    chip = block[start:block.index("</span>", start)]

    assert "@click" not in chip, (
        "the microphone chip looks like a button; pressing it cannot do anything "
        "the page does not already do")
    assert "mikrofon" in chip.lower(), "the chip does not explain itself"
    assert "audioInputs" in chip or "audioInputLabel" in chip, (
        "the chip no longer reports whether a device is there at all")


# ── 6. every word is a pair, and none of them freezes ────────────────────────

REQUIRED_PAIRS = (
    ("Mode Terang", "Light mode"),
    ("Mode Gelap", "Dark mode"),
    ("Bisukan suara peringatan", "Mute warning sounds"),
    ("Nyalakan suara peringatan", "Unmute warning sounds"),
    ("Baterai", "Battery"),
    ("Jaringan", "Network"),
)


def test_every_label_the_strip_adds_is_bilingual():
    block = strip()
    pairs = set(re.findall(r"t\('([^']*)','([^']*)'\)", block))
    for pair in REQUIRED_PAIRS:
        assert pair in pairs, f"{pair} is missing, so the strip is half-translated"


def test_no_strip_label_is_frozen_in_one_language():
    """A markup pair re-renders with the toggle; `sgT()` picks a language at the
    moment it is called and keeps it. The strip sits on screen for the whole
    sitting, so a frozen label is a label that never switches."""
    block = strip_without_comments()
    assert "sgT(" not in block, (
        "a strip label is built with sgT(), so it keeps the language it was "
        "written in across a toggle")


def test_no_apostrophe_sits_inside_a_pair():
    """`t('id','en')` lives in an HTML attribute, so the parser decodes the entity
    before Alpine runs and the string ends early — the label renders empty in both
    languages and nothing else fails."""
    block = strip_without_comments()
    assert not re.search(r"[A-Za-z]'[A-Za-z]", block)
    assert "&#39;" not in block and "&apos;" not in block


def test_the_markers_the_tests_read_are_the_markers_the_template_writes():
    text = source(EXAM_PAGE)
    assert text.count(STRIP_START) == 1 and text.count(STRIP_END) == 1, (
        "the strip markers are not a single pair, so a test could read the wrong "
        "region")


# ── 7. reachable while the paper is blocked ──────────────────────────────────
#
# The blocker and the away-blur are `fixed inset-0`, both on the scale's `scrim`
# line, so they cover the strip with the rest of the page — and the exam bar they
# also cover is where the
# theme and language controls normally live. A student who has lost fullscreen is
# then asked to fix it in a language they may not read, with a page that will not
# say who they are, and no way to quiet the alerts. The strip is the one row that
# has to survive that, which is what these four assertions pin down.
#
# The terms agreement is the same problem one screen earlier: it is the first thing
# shown, it covers the exam bar too, and its own copy starts out in Indonesian — so
# the language control has to be reachable *before* the student agrees, not only
# after. The strip therefore has a second layer, above that modal and still below
# the submit gate.

PINNED_CLASS = "sg-sit-strip-pinned"


def _style_block() -> str:
    """The page's own `<style>` text, which is where the pinning rule lives."""
    text = source(EXAM_PAGE)
    start = text.index("<style>")
    return text[start:text.index("</style>", start)]


def _pinned_rule() -> str:
    match = re.search(r"\.sg-sit-strip\." + PINNED_CLASS + r"\s*\{([^}]*)\}",
                      _style_block())
    assert match, (
        f"no `.{PINNED_CLASS}` rule, so nothing lifts the strip above the panel "
        "that covers it")
    return match.group(1)


def _scale() -> dict[str, int]:
    """The one ordered list of layer heights, read from the page's `:root`.

    This is the single place a height is written on this page — the map returns in
    declaration order, so a caller can ask not only what a layer is but whether the
    list is still a list. A rule naming a raw number, or a panel choosing one by
    eye, is the drift the scale exists to make impossible.
    """
    root = re.search(r":root\s*\{([^}]*)\}", _style_block())
    assert root, "the page has no `:root` block, so there is no layer scale"
    pairs = re.findall(r"--sg-layer-([a-z0-9-]+):\s*(\d+)\s*;", root.group(1))
    assert pairs, "the `:root` block declares no `--sg-layer-*` heights"
    return {name: int(value) for name, value in pairs}


def _layer(rule: str) -> int:
    """A rule's height, resolved through the scale by the *name* it takes.

    A rule that hardcodes a number, or names a layer that is not a line of the
    scale, fails here — which is the point: a height is chosen by name, never by
    eye.
    """
    scale = _scale()
    match = re.search(r"z-index:\s*var\(--sg-layer-([a-z0-9-]+)\)", rule)
    assert match, (
        f"the rule does not take its height from a named layer: {rule!r}")
    name = match.group(1)
    assert name in scale, (
        f"the rule takes layer `{name}`, which is not a line of the scale "
        f"({', '.join(scale)})")
    return scale[name]


def _layer_named(name: str) -> int:
    scale = _scale()
    assert name in scale, f"`{name}` is not a line of the layer scale"
    return scale[name]


#: A full-screen panel: the tag that carries `x-show="…"`, the state that raises
#: it, and one `sg-layer-*` name from the scale — never a number picked inline.
PANEL_TAG_RE = re.compile(r'x-show="([^"]*)"([^>]*)>', re.S)


def _panel_matches():
    out = []
    for match in PANEL_TAG_RE.finditer(source(EXAM_PAGE)):
        show, attrs = match.group(1), match.group(2)
        if "fixed inset-0" not in attrs:
            continue
        named = re.search(r"sg-layer-([a-z0-9-]+)", attrs)
        if named:
            out.append((match, show, named.group(1)))
    return out


def _panels() -> list[tuple[str, str]]:
    panels = [(show, name) for _m, show, name in _panel_matches()]
    assert len(panels) >= 4, (
        "the page's full-screen panels are not recognisable any more, so this test "
        "would be comparing against nothing")
    return panels


def _blocker_layers() -> list[str]:
    """Every panel that stands *between the student and the paper*.

    Separated from the modals by the state that raises it rather than by a number:
    a modal covers the paper for a reason and is meant to be on top, while the
    blocker and the away-blur are the two the strip has to outrank.
    """
    found = [name for show, name in _panels()
             if "fullscreenBlocked" in show or "awayBlurred" in show]
    assert len(found) >= 2, (
        "the two blocking panels are not recognisable any more, so this test would "
        "be comparing against nothing")
    return found


def test_the_strip_outranks_both_panels_that_cover_the_paper():
    pinned = _layer(_pinned_rule())
    blockers = [_layer_named(name) for name in _blocker_layers()]
    assert pinned > max(blockers), (
        f"the strip sits at {pinned}, under a blocker at {max(blockers)}: it stays "
        "covered, which is the defect this exists to remove")


def test_the_scale_is_one_ordered_list_with_no_shared_heights():
    """The scale is the only place a height is written, so it has to behave like a
    list: strictly increasing in the order it is declared — read top-down, each
    line paints over the one before it — and no two layers handed the same number
    by accident, which is how the terms agreement and the anti-cheat watermark both
    came to read `9999`.

    The canvas entries are a stacking context of their own inside a
    `.full-canvas-wrap`, so they are held to the same rule separately rather than
    compared with the page-level layers they never compete with.
    """
    scale = _scale()
    groups = {
        "canvas": {k: v for k, v in scale.items() if k.startswith("canvas-")},
        "page": {k: v for k, v in scale.items() if not k.startswith("canvas-")},
    }
    for label, group in groups.items():
        assert group, f"the {label} scale is empty"
        values = list(group.values())
        assert len(set(values)) == len(values), (
            f"two {label} layers share a height, so one silently paints over the "
            f"other: {group}")
        assert values == sorted(values), (
            f"the {label} scale is declared out of order, so 'the line above' no "
            f"longer means 'paints over': {group}")


def test_no_rule_on_this_page_chooses_a_height_by_eye():
    """Every height on the page is a `var(--sg-layer-*)` name — never a number. A
    raw `9999`, or a Tailwind `z-[90]`, is a height kept in step by hand, which is
    the drift the scale was built to remove."""
    raw = re.findall(r"z-index:\s*(\d+)", _style_block())
    assert not raw, (
        f"a rule hardcodes a z-index instead of naming a layer: {raw}")
    tailwind = re.findall(r"\bz-\[(\d+)\]", source(EXAM_PAGE))
    assert not tailwind, (
        f"a panel picks a height by eye with a Tailwind arbitrary value: {tailwind}")


def test_every_panel_layer_has_a_rule_that_takes_its_own_name():
    """A panel names a layer in its markup, and that name has to have a rule that
    takes the *same* line of the scale. A class pointing at another line, or at a
    name the scale never declares, is a panel stacked by a typo — a height that
    reads correct in the markup and does nothing in the browser."""
    block = _style_block()
    for name in sorted({name for _show, name in _panels()}):
        match = re.search(r"\.sg-layer-" + re.escape(name) + r"\s*\{([^}]*)\}",
                          block)
        assert match, (
            f"no `.sg-layer-{name}` rule, so its panels carry a class with no "
            "height at all")
        assert f"var(--sg-layer-{name})" in match.group(1), (
            f"`.sg-layer-{name}` does not take its own line of the scale: "
            f"{match.group(1)!r}")


def _panel_blocks():
    """Each full-screen panel's markup, from its opening tag to the next panel.

    Bounded by the next panel rather than by a closing tag because the panels nest
    divs: a block read to the wrong `</div>` would silently span two of them and
    find a control that belongs to the other one. A nested `x-show` is not a panel
    — `_panel_matches` only yields a tag that also carries `fixed inset-0` and a
    named layer — so the split lands on the real ones.
    """
    text = source(EXAM_PAGE)
    matches = _panel_matches()
    for i, (match, show, _name) in enumerate(matches):
        end = matches[i + 1][0].start() if i + 1 < len(matches) else len(text)
        yield show, text[match.start():end]


def test_the_blocking_panels_leave_the_language_to_the_strip():
    """Both blockers are `fixed inset-0`, so each used to draw its own EN/ID
    button to keep a control inside the panel that covers the exam bar. Once the
    strip outranks them (see the test above) that button is a second control for
    one choice, and each one needed a comment to explain why it was there — the
    strip is now the one place the language lives while the paper is blocked."""
    blockers = [(show, block) for show, block in _panel_blocks()
                if "fullscreenBlocked" in show or "awayBlurred" in show]
    assert len(blockers) >= 2, (
        "the two blocking panels are not recognisable any more, so this test would "
        "be checking nothing")
    for show, block in blockers:
        assert "setLang(lang === 'id' ? 'en' : 'id')" not in block, (
            f"the panel raised by `{show}` draws its own language button, so the "
            "same choice has two controls instead of the strip above it")


FOREMOST_CLASS = "sg-sit-strip-foremost"


def _foremost_rule() -> str:
    match = re.search(r"\.sg-sit-strip\." + FOREMOST_CLASS + r"\s*\{([^}]*)\}",
                      _style_block())
    assert match, (
        f"no `.{FOREMOST_CLASS}` rule, so nothing lifts the strip above the "
        "agreement a student has to read before the paper opens")
    return match.group(1)


def _modals_by_state():
    """The two modals, split by the state that raises them.

    They were one layer and are not any more. The strip has to be reachable
    *through* the agreement — its language control is how the terms get read — and
    has to stop short of the submit gate, which ends the paper rather than being a
    panel to reach past. Separated by the state and not by a number: a layer moved
    by hand should fail here rather than still look like a layer.
    """
    panels = _panels()
    agreement = [name for show, name in panels if "showExamAgreement" in show]
    submit = [name for show, name in panels if "showSubmitConfirm" in show]
    assert agreement and submit, (
        "the agreement and the submit gate are not recognisable any more, so this "
        "test would be comparing against nothing")
    return agreement, submit


def test_the_strip_is_reachable_through_the_terms_agreement():
    """The terms are the first screen of the sitting and they start out in
    Indonesian, so a student who reads English has to be able to switch them
    before agreeing."""
    agreement, _submit = _modals_by_state()
    foremost = _layer(_foremost_rule())
    agreement_z = max(_layer_named(name) for name in agreement)
    assert foremost > agreement_z, (
        f"the strip sits at {foremost}, under the agreement at {agreement_z}: "
        "the terms cannot be read in a language the strip could have switched to")
    assert _layer(_pinned_rule()) < min(_layer_named(name) for name in agreement), (
        "the ordinary pinned strip outranks the agreement, so it comes up there "
        "without having been asked to")


def test_the_strip_still_stops_short_of_the_submit_gate():
    """Ending the paper is a decision, not a moment to be reaching over: whichever
    layer lifts the strip, it must not come up through the confirmation."""
    _agreement, submit = _modals_by_state()
    gate = min(_layer_named(name) for name in submit)
    for name, rule in (("pinned", _pinned_rule()), ("foremost", _foremost_rule())):
        z = _layer(rule)
        assert z < gate, (
            f"the {name} strip at {z} would sit above the submit gate at "
            f"{gate}, so a student could poke the strip while confirming")


def test_the_strip_is_pinned_to_the_viewport_rather_than_merely_raised():
    """Raising a static element's z-index is not enough: a panel is `fixed inset-0`
    and therefore always on screen, while the strip moves with the page, so a
    student on a paper that scrolls would have an uncovered but *offscreen* strip —
    which is the same experience.

    And `fixed` rather than `sticky`, which is what this was first and did not
    work. `sticky` offsets a box inside its own *scroll container*; the strip's is
    `main.flex-1.overflow-y-auto`, which is sized to the viewport (1048 of 1048 on
    this box) while the overflow the page actually scrolls sits on the document —
    so scrolling the document carried the strip off the top while a `sticky`
    rule sat there looking correct. `fixed` is viewport-relative whichever ancestor
    scrolls, which is the only thing that makes it reachable on this layout.
    """
    rule = _pinned_rule()
    assert "position: fixed" in rule, (
        f"the strip is not pinned to the viewport: {rule!r}")
    assert re.search(r"top:\s*0\b", rule), f"the strip does not pin to the top: {rule!r}"
    assert not re.search(r"\boverflow:\s*(auto|scroll)", rule), (
        "a scroll container on the strip becomes its own containing block and "
        "breaks the viewport pinning it depends on")


def test_the_pinned_strip_keeps_the_page_gutter_so_it_does_not_jump():
    """Measured: it left the flow horizontally shifted before the insets, and a row
    that jumps sideways at the moment a blocker appears is read as a second defect.
    The insets track the page's own `p-4 lg:p-6`."""
    block = _style_block()
    pinned = re.search(r"\.sg-sit-strip\." + PINNED_CLASS + r"\s*\{([^}]*)\}", block)
    assert pinned, "the pinned rule disappeared"
    rule = pinned.group(1)
    assert re.search(r"left:\s*0\b", rule) and re.search(r"right:\s*0\b", rule), (
        f"the pinned strip does not span the viewport: {rule!r}")
    assert "margin-left: 1rem" in rule and "margin-right: 1rem" in rule, (
        f"the pinned strip has no gutter to match the page's p-4: {rule!r}")
    assert re.search(r"@media \(min-width: 1024px\)[^}]*\{[^}]*margin-left: 1.5rem", block), (
        "the gutter does not widen to the page's lg:p-6, so the strip shifts at "
        "the lg breakpoint")


def test_the_pinning_follows_the_panels_and_not_the_layout():
    block = strip()
    assert ":class=" in block and PINNED_CLASS in block, (
        "the strip never takes the pinned class, so the rule above is dead CSS")
    assert "stripLifted" in block, (
        "the pinning is bound to something other than the state that raises a "
        "panel, so the strip could stay lifted over a paper that is being answered")
    assert FOREMOST_CLASS in block and "stripForemost" in block, (
        "the strip never takes the foremost class, so the rule that lifts it over "
        "the terms agreement is dead CSS and its language control is unreachable")
    assert "paperBlocked" not in block, (
        "the strip binds to `paperBlocked`, which has no foremost layer, so the "
        "terms agreement would leave it covered")


def test_the_strip_is_not_lifted_while_the_paper_is_answerable():
    """In the ordinary sitting the strip is a card in the flow above the paper and
    must stay one: a permanently sticky strip would eat a phone's screen and would
    fight the exam bar, which is sticky at the same `top: 0`."""
    head = strip().split("class=\"", 1)[1].split('"', 1)[0]
    for token in ("sticky", "fixed", "z-[", PINNED_CLASS):
        assert token not in head, (
            f"the strip's static class list carries {token!r}, so it is lifted "
            "for the whole paper and not only while the paper is blocked")


def test_the_blockers_cover_the_exam_bar_these_controls_normally_live_in():
    """Why the strip has to carry them at all. If a blocker ever stopped covering
    the whole page, the honest answer would be to drop the workaround rather than
    keep a second copy of every control."""
    assert _blocker_layers(), "a blocker no longer covers the page"
    assert all("!submitted" in show for show, _ in _panels()
               if "fullscreenBlocked" in show or "awayBlurred" in show), (
        "a blocker stopped standing down once the paper is submitted")


BLOCKED_STATE = """
const obj = {{
  submitted: {submitted},
  fullscreenBlocked: {blocked},
  awayBlurred: {away},
{getter},
}};
console.log(JSON.stringify({{ blocked: obj.paperBlocked }}));
"""


def _paper_blocked(*, submitted=False, blocked=False, away=False):
    assert "        get paperBlocked()" in source(EXAM_PAGE), (
        "the page has no `paperBlocked`, so no markup can ask whether the paper "
        "is standing behind a panel")
    return _node(BLOCKED_STATE.format(
        submitted=str(bool(submitted)).lower(),
        blocked=str(bool(blocked)).lower(),
        away=str(bool(away)).lower(),
        getter=_method("get paperBlocked"),
    ))["blocked"]


@needs_node
def test_either_panel_blocks_and_submitting_stops_blocking():
    assert _paper_blocked(blocked=True) is True, "the fullscreen blocker does not pin the strip"
    assert _paper_blocked(away=True) is True, (
        "the away-blur is the same blocked paper and the strip stays reachable there too")
    assert _paper_blocked() is False, "an answerable paper lifts the strip"
    assert _paper_blocked(submitted=True, blocked=True) is False, (
        "a submitted paper keeps a lifted strip over the result")


LIFTED_STATE = """
const obj = {{
  submitted: {submitted},
  fullscreenBlocked: {blocked},
  awayBlurred: {away},
  showExamAgreement: {agreement},
{blocked_getter},
{lifted},
{foremost},
}};
console.log(JSON.stringify({{ lifted: obj.stripLifted, foremost: obj.stripForemost }}));
"""


def _strip_lifted(*, submitted=False, blocked=False, away=False, agreement=False):
    for name in ("get stripLifted()", "get stripForemost()"):
        assert "        " + name in source(EXAM_PAGE), f"the page has no `{name}`"
    # `stripLifted` is `paperBlocked || stripForemost`, so the object needs the
    # blocker getter as well — otherwise `this.paperBlocked` is `undefined` and a
    # blocker would appear to lift nothing at all.
    return _node(LIFTED_STATE.format(
        submitted=str(bool(submitted)).lower(),
        blocked=str(bool(blocked)).lower(),
        away=str(bool(away)).lower(),
        agreement=str(bool(agreement)).lower(),
        blocked_getter=_method("get paperBlocked"),
        lifted=_method("get stripLifted"),
        foremost=_method("get stripForemost"),
    ))


@needs_node
def test_the_agreement_lifts_the_strip_at_its_own_layer():
    assert _strip_lifted(agreement=True) == {"lifted": True, "foremost": True}, (
        "the strip stays under the terms agreement, so its language control cannot "
        "be reached while the terms are being read")
    assert _strip_lifted() == {"lifted": False, "foremost": False}, (
        "an answerable paper lifts the strip")
    assert _strip_lifted(submitted=True, agreement=True) == {"lifted": False, "foremost": False}, (
        "a submitted paper keeps a lifted strip over the result")
    assert _strip_lifted(blocked=True) == {"lifted": True, "foremost": False}, (
        "a blocker lifts the strip at its own layer, not at the agreement's")
