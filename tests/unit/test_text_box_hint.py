"""Pressing Teks places a text box, so the row under it is live on the press.

The "Teks:" row is the rich-text toolbar for a box on the answer canvas, and every
control in it is `:disabled="activeBox[i] == null"`. It used to be that a box only
existed after a click on the page, so selecting the **Teks** tool showed a grey
toolbar with no visible reason — a teacher reading the preview called it "belum
aktif ya?" ("not active yet?"), which is the reading a pupil would arrive at too.

The first fix was a sentence in the row ("click the canvas to place a text box"),
and it explained the state without removing it: the pupil still had to discover a
second gesture after pressing a button that looks like it should do something.
The fix here is behavioural. Pressing **Teks** now places the page's first box —
or, when the page already has one, opens its last — and the new box takes the
caret, so the toolbar is live and the pupil types straight away.

What that leaves the sentence for is the one grey state a delete or a page turn can
reach (a page with no box at all), and there it names the action that resolves it:
the tool press. It stays bound to the **same condition** the controls are disabled
on — a hint beside working controls, or absent beside dead ones, is worse than no
hint — and it stays bilingual like the rest of the page.

The placement itself is not restated here: the shipped `addTextBoxAt` and
`_openTextBox` are lifted out of the template by brace matching and executed in
node, so what is measured is the code a pupil presses.
"""
from __future__ import annotations

import json
import pathlib
import re
import shutil
import subprocess
import tempfile

import pytest

PAGE = pathlib.Path(__file__).resolve().parents[2] / "app" / "templates" / "student" / "take_exam.html"

ROW_OPEN = '<div class="sg-box-toolbar" x-show="essayCanvasMode[i] === \'text\'" x-cloak>'
LABEL = "t('Teks:','Text:')"
HINT = "tekan Teks untuk kotak baru"
HINT_EN = "press Text for a new box"
CONDITION = "activeBox[i] == null"


def page() -> str:
    return PAGE.read_text(encoding="utf-8")


def method_source(text: str, name: str) -> str:
    """One method from the page's component, by counting its braces.

    Copied from `tests/unit/test_canvas_box_graph.py` rather than imported: a test
    module is not a library, and this keeps the two suites independent. The slice
    includes the signature so it drops into an object literal and runs.
    """
    start = text.index("        %s(" % name)
    i = text.index("{", start)
    depth = 0
    for j in range(i, len(text)):
        if text[j] == "{":
            depth += 1
        elif text[j] == "}":
            depth -= 1
            if depth == 0:
                return text[start:j + 1]
    raise AssertionError("unclosed method %s" % name)


def row() -> str:
    """The row the hint belongs to: from its open tag to the first control.

    The first control is the anchor rather than the closing tag because the row
    contains nested <details> blocks, and a brace-matching helper for HTML is a
    second parser to get wrong. If the label and the first button are here and the
    hint is between them, it is in the row the reader is looking at.
    """
    text = page()
    start = text.index(ROW_OPEN)
    end = text.index("boxCmd(i, 'bold')", start)
    return text[start:end]


# ── the press, not the sentence ──────────────────────────────────────────────


class TestTheToolPressPlacestheBox:
    def test_selecting_the_tool_starts_the_box(self):
        source = method_source(page(), "setEssayCanvasMode")
        assert "this._openTextBox(i)" in source, (
            "selecting Teks is still only a mode change, so the row under it stays "
            "grey until the pupil works out that the paper has to be clicked")
        assert "if (mode === 'text')" in source, (
            "every tool press would place a box, including Pen and Eraser")

    def test_the_press_opens_the_box_that_is_already_there(self):
        source = method_source(page(), "_openTextBox")
        assert "if (boxes.length)" in source, (
            "a page that already has a box would grow another empty one on every press")
        assert "this.focusTextBox(i, boxes.length - 1)" in source, (
            "an existing box is selected but never opened, so the caret lands nowhere")
        assert "this.activeBox[i] = boxes.length - 1" in source, (
            "the toolbar acts on the active index and it is left pointing nowhere")

    def test_an_empty_page_gets_one_at_the_top_left_margin(self):
        source = method_source(page(), "_openTextBox")
        fallback = re.search(r"return this\.addTextBoxAt\(i,\s*([\d.]+),\s*([\d.]+)\)", source)
        assert fallback, "the empty-page branch places nothing"
        x, y = float(fallback.group(1)), float(fallback.group(2))
        assert 0 < x < 1 and 0 < y < 1, (
            f"the first box is placed at ({x}, {y}), which is off the sheet")
        assert x <= 0.25 and y <= 0.25, (
            f"the first box is placed at ({x}, {y}): an answer starts at the top left")

    def test_the_button_does_not_take_the_caret_back(self):
        """A button focuses on mousedown, which lands after the tick that places the
        box and calls `focus()` — so without `@mousedown.prevent` the caret ends up
        on the button the pupil just pressed and they click the box anyway."""
        text = page()
        start = text.index("@click=\"setEssayCanvasMode(i, 'text')\"")
        tag = text[text.rindex("<button", 0, start):text.index(">", start)]
        assert "@mousedown.prevent" in tag, (
            "pressing Teks hands the focus back to the button, so the box it places "
            "is not ready to type in")

    def test_the_first_press_waits_for_the_box_to_be_rendered(self):
        """Alpine inserts the box's element on its own tick, which can land after the
        tick that created it. Without a bounded retry the caret is asked for while
        there is nothing to type in, and the pupil has to click the box the tool
        just made — the second gesture this change exists to remove."""
        source = method_source(page(), "focusTextBox")
        assert "if (el)" in source and "el.focus()" in source
        assert "tries < 2" in source and "setTimeout(" in source, (
            "the focus is requested once and never retried against the render")
        assert "while (" not in source, "the retry polls instead of stopping"

    def test_a_placement_is_not_an_answer(self):
        """A press that ticks the question on the rail would let a pupil answer an
        essay by pressing Teks and never typing."""
        # The *call*, not the word: the method's own comment says why it does not
        # call it, and a guard that reads prose refuses its own explanation.
        for name in ("addTextBoxAt", "_openTextBox"):
            assert "this.markAnswered(" not in method_source(page(), name), (
                f"{name} marks the question answered before anything is written in it")


# ── one creator ──────────────────────────────────────────────────────────────


class TestThereIsOneWayToMakeABox:
    def test_the_box_literal_is_built_once(self):
        assert page().count("text: '', html: '', align: 'left'") == 1, (
            "a second copy of the box literal is where a new field goes missing from "
            "one of the two placement paths — the defect this helper exists to close")

    def test_every_placement_path_goes_through_it(self):
        text = page()
        click = method_source(text, "onPdfClick")
        assert "this.addTextBoxAt(i, x_pct, y_pct)" in click, (
            "the click on the paper builds its own box again")
        # The canvas pointer-down branch lives inside an event closure rather than a
        # component method, so it is read where it sits: the text mode branch, up to
        # its `return`.
        start = text.index("if (mode === 'text') {", text.index("const onStart = e =>"))
        branch = text[start:text.index("return;", start)]
        assert "this.addTextBoxAt(i, p.x / canvas.width, p.y / canvas.height)" in branch, (
            "a tap on the canvas places a box by a second route")

    def test_the_box_carries_the_defaults_the_toolbar_reads(self):
        source = method_source(page(), "addTextBoxAt")
        for field in ("text:", "html:", "align:", "fontSize:", "fontFamily:", "orient:"):
            assert field in source, f"a placed box has no {field} and the toolbar reads it"
        assert "textFontSize[i] || 16" in source and "textFontFamily[i] ||" in source, (
            "the box ignores the size and font chosen in the toolbar")

    def test_a_placement_inside_the_margin_is_clamped_to_the_sheet(self):
        source = method_source(page(), "addTextBoxAt")
        assert "Math.max(0, Math.min(1, xPct))" in source
        assert "Math.max(0, Math.min(1, yPct))" in source, (
            "a press at the edge can put the box outside the paper, where it cannot "
            "be reached to drag back")


class TestDeletingLeavesTheRowAlive:
    def test_a_remaining_box_takes_the_selection(self):
        source = method_source(page(), "deleteActiveBox")
        assert "const left = ((this.textBoxes[i] || {})[this.page - 1] || [])" in source
        assert "this.activeBox[i] = left.length ? left.length - 1 : null" in source, (
            "deleting one of two boxes greys the whole toolbar for a pupil who meant "
            "to carry on in the other")
        assert "this.focusTextBox(i, left.length - 1)" in source, (
            "the remaining box is selected but the caret is not put in it")


# ── the one grey state that is left ──────────────────────────────────────────


class TestTheRowSaysWhyItIsGrey:
    def test_the_hint_is_between_the_label_and_the_first_control(self):
        block = row()
        assert LABEL in block, "the row moved or was renamed — this test anchors on it"
        assert HINT in block, (
            "the row is disabled when a page has no box and says nothing about it")
        assert block.index(LABEL) < block.index(HINT), (
            "the hint is above the label it explains, which reads as page copy")

    def test_it_names_the_action_that_makes_the_controls_usable(self):
        """The sentence has to be the *action*, and after this change the action is
        the tool press the pupil just used — not a second gesture they have to
        discover. 'Not available' would leave them where they were."""
        assert "tekan Teks" in row()
        assert "press Text" in row()

    def test_the_sentence_is_short_enough_to_share_the_row(self):
        """On a phone `sg-box-toolbar` stops wrapping and scrolls sideways, so a
        sentence that fills the width would push the controls out of sight — the
        same failure it was added to fix, one layer down."""
        assert len(HINT) <= 45, f"the hint is {len(HINT)} characters: {HINT!r}"

    def test_it_speaks_both_languages_like_the_page(self):
        block = row()
        assert HINT in block and HINT_EN in block, "the hint is Indonesian-only"
        assert re.search(r"t\('" + re.escape(HINT) + r"','" + re.escape(HINT_EN) + r"'\)",
                         block), "the hint is not a t() pair, so the toggle cannot reach it"

    def test_it_is_bound_to_the_condition_the_controls_are_disabled_on(self):
        block = row()
        # The sentence is the `x-text` argument, so it lives *inside* the opening
        # tag rather than between the tags; the tag is matched first and read after.
        tag = re.search(r"<span\b[^>]*" + re.escape(HINT) + r"[^>]*>", block)
        assert tag, "the hint is not a <span> in the row"
        hint = re.search(r'x-show="([^"]+)"', tag.group(0))
        assert hint, "the hint has no x-show, so it would sit beside working controls"
        assert hint.group(1) == CONDITION, (
            f"the hint is shown on {hint.group(1)!r} while the controls are disabled on "
            f"{CONDITION!r} — the sentence and the state can disagree")
        assert f':disabled="{CONDITION}"' in block, (
            "the controls are no longer disabled on that condition, so the hint is now "
            "the thing that is wrong")

    def test_the_row_is_still_only_there_in_text_mode(self):
        assert ROW_OPEN in page()
        assert "x-cloak" in ROW_OPEN, (
            "without x-cloak the row paints before Alpine hides it")


# ── the shipped methods, executed ────────────────────────────────────────────


@pytest.mark.skipif(shutil.which("node") is None, reason="node is needed to run the placement")
class TestThePlacementItself:
    """`addTextBoxAt` and `_openTextBox` from the template, run in node.

    A test that restated the placement would prove the restatement. These lift the
    real methods, stand in a component with the fields they read, and press.
    """

    @pytest.fixture(scope="class")
    def results(self):
        text = page()
        script = (
            "const api = {\n"
            "  page: 1, textBoxes: {}, activeBox: {}, focus: [], answered: [],\n"
            "  textFontSize: {}, textFontFamily: {},\n"
            "  $nextTick(fn) { fn(); },\n"
            "  focusTextBox(i, bi) { this.focus.push([i, bi]); },\n"
            "  markAnswered(i, v) { this.answered.push([i, v]); },\n"
            + method_source(text, "addTextBoxAt") + ",\n"
            + method_source(text, "_openTextBox") + "\n"
            "};\n"
            "const box = (i, p) => ((api.textBoxes[i] || {})[p] || []).slice();\n"
            "api._openTextBox(0);\n"
            "const first = {n: box(0, 0).length, active: api.activeBox[0],"
            " focus: api.focus.slice(), box: box(0, 0)[0]};\n"
            "api.focus.length = 0;\n"
            "api._openTextBox(0);\n"
            "const second = {n: box(0, 0).length, active: api.activeBox[0],"
            " focus: api.focus.slice()};\n"
            "api.focus.length = 0;\n"
            # Boxes only on page 2: the press must place on the page being read.
            "api.textBoxes = {0: {1: [{x_pct: .5, y_pct: .5, text: 'p2'}]}};\n"
            "api.page = 1; api.activeBox = {};\n"
            "api._openTextBox(0);\n"
            "const otherPage = {n: box(0, 0).length, onP2: box(0, 1).length};\n"
            "api.focus.length = 0;\n"
            # Two boxes on the page being read: open the last, add nothing.
            "api.textBoxes = {0: {1: [{x_pct: .1, y_pct: .1}, {x_pct: .2, y_pct: .2}]}};\n"
            "api.page = 2; api.activeBox = {}; api.focus.length = 0;\n"
            "api._openTextBox(0);\n"
            "const existing = {n: box(0, 1).length, active: api.activeBox[0],"
            " focus: api.focus.slice()};\n"
            # The clamp.
            "api.textBoxes = {}; api.page = 1;\n"
            "api.addTextBoxAt(0, 1.4, -0.2);\n"
            "const clamped = box(0, 0)[0];\n"
            "console.log(JSON.stringify({first, second, otherPage, existing, clamped,"
            " answered: api.answered}));\n"
        )
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as fh:
            fh.write(script)
            name = fh.name
        proc = subprocess.run(["node", name], capture_output=True, text=True, timeout=60)
        assert proc.returncode == 0, proc.stderr
        return json.loads(proc.stdout)

    def test_the_first_press_places_exactly_one_box(self, results):
        assert results["first"]["n"] == 1, "the Teks press placed nothing"
        assert results["first"]["active"] == 0, (
            "the placed box is not the active one, so the toolbar acts on nothing")
        assert results["first"]["focus"] == [[0, 0]], (
            "the box is placed but the caret is not in it, so the pupil still has to "
            "click before typing")

    def test_the_box_is_ready_to_type_in(self, results):
        box = results["first"]["box"]
        assert box["text"] == "" and box["html"] == ""
        assert box["align"] == "left" and box["orient"] == "horizontal"
        assert box["fontSize"] == 16
        assert box["fontFamily"].startswith("Times New Roman")
        assert 0 < box["x_pct"] < 1 and 0 < box["y_pct"] < 1

    def test_a_second_press_opens_the_same_box_instead_of_stacking_one(self, results):
        assert results["second"]["n"] == 1, "every press leaves another empty box to tidy up"
        assert results["second"]["active"] == 0
        assert results["second"]["focus"] == [[0, 0]], "the existing box is not opened"

    def test_it_places_on_the_page_being_read(self, results):
        assert results["otherPage"]["n"] == 1, (
            "a box on another page made the press do nothing on this one")
        assert results["otherPage"]["onP2"] == 1, "the other page was written to instead"

    def test_a_page_with_boxes_opens_its_last_one(self, results):
        existing = results["existing"]
        assert existing["n"] == 2, "an existing page grew another box on the press"
        assert existing["active"] == 1
        assert existing["focus"] == [[0, 1]], "the last box is selected but not opened"

    def test_a_point_outside_the_sheet_lands_on_its_edge(self, results):
        assert results["clamped"]["x_pct"] == 1
        assert results["clamped"]["y_pct"] == 0

    def test_nothing_is_marked_answered(self, results):
        assert results["answered"] == [], (
            "placing a box ticked the question on the rail before a word was written")
