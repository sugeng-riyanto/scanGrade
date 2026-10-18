"""The canvas answer: rich text in a box, a graph beside it, and an answer that
is read whole.

Three things are asserted here, and each one is a defect this change exists to
close rather than a property that was already true:

* **A box is a text field with the editor's tools.** It was a `<textarea>`, so a
  pupil could not bullet a list, number a set of steps, insert a symbol or a
  table, or colour a run on the paper — while the essay beside it could. The
  toolbar and the box are asserted together, because a toolbar whose buttons
  target a `<textarea>` is a toolbar of dead buttons.

* **Both halves of an answer reach the teacher.** `essay_answer_text` used the
  boxes only as a fallback, so a paragraph *and* a labelled diagram arrived as
  the paragraph alone: the marking page was missing half the answer and the AI
  was asked to mark it.

* **The calculator evaluates what is on its display.** Brackets used to be
  decoration; `sin(30)` could not be typed at all, and `x^y` applied to the last
  number rather than the expression. The parser is not restated here — the real
  method is lifted out of the template by brace matching and executed, which is
  what makes 2×(3+4)^2 = 98 a measurement of the shipped code.

Run: pytest tests/unit/test_canvas_box_graph.py
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from app.services.essay_text import essay_answer_text

ROOT = Path(__file__).resolve().parents[2]
PAGE = ROOT / "app" / "templates" / "student" / "take_exam.html"
GRAPH = ROOT / "app" / "static" / "js" / "exam-graph.js"


def page_text() -> str:
    return PAGE.read_text(encoding="utf-8")


def method_source(text: str, name: str) -> str:
    """One method from the page's component, by counting its braces.

    A regex cannot find the end of a method that contains braces, and the
    calculator's parser is mostly braces. The slice returned includes the
    signature so it can be dropped into an object literal and called.
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


class TestTheBoxIsRichText:
    def test_the_textarea_is_gone_in_favour_of_an_editable(self):
        text = page_text()
        assert 'x-model="box.text"' not in text, (
            "a textarea cannot carry bullets, symbols or colour, and it is what the "
            "box used to be"
        )
        assert 'class="box-rich" contenteditable="true"' in text

    def test_the_toolbar_and_the_box_are_wired_together(self):
        text = page_text()
        for command in ("bold", "italic", "underline", "ul", "ol"):
            assert "@mousedown.prevent=\"boxCmd(i, '%s')\"" % command in text, command
        assert 'boxAlign(i, $event.target.value)' in text
        assert 'boxTab(i, null, false)' in text
        assert 'boxColor(i,' in text
        assert 'boxSymbol(i,' in text
        assert 'boxTable(i,' in text
        assert 'boxFontSize(i,' in text and 'boxFontFamily(i,' in text
        assert 'boxOrient(i)' in text

    def test_every_control_is_prevented_so_the_selection_survives(self):
        text = page_text()
        # Every command button must keep mousedown from moving focus out of the
        # box; that is the defect the essay toolbar already paid for once, and a
        # command with no selection in the editable does nothing at all.
        for handler in ('boxCmd(', 'boxColor(', 'boxSymbol(', 'boxTable(',
                        'boxTab(', 'boxOrient('):
            pattern = r'@mousedown\.prevent="[^"]*' + re.escape(handler)
            assert re.search(pattern, text), handler
        assert re.search(r'@change="boxFontSize\(', text)
        assert re.search(r'@change="boxFontFamily\(', text)

    def test_a_box_stores_its_html_and_its_plain_text(self):
        text = page_text()
        assert "box.html = this.sanitizeBoxHtml(el.innerHTML)" in text
        assert "box.text = window.SGEssayEditor.textOf(el)" in text
        # The HTML is what is painted back; the text is what the teacher reads.
        assert "x-init=\"$el.innerHTML = sanitizeBoxHtml(box.html || '')\"" in text

    def test_a_pasted_box_cannot_carry_script(self):
        source = method_source(page_text(), "sanitizeBoxHtml")
        assert "script,style,iframe,object,embed" in source
        assert "indexOf('on') === 0" in source
        assert "javascript:" in source

    def test_the_page_key_includes_the_page(self):
        text = page_text()
        assert ":key=\"'tb-' + page + '-' + bi\"" in text, (
            "the old key was the index alone, so paging reused the same element "
            "and `x-init` never re-read the new page's HTML"
        )

    def test_dragging_is_the_handles_job(self):
        text = page_text()
        assert '@mousedown.stop="startTextBoxDrag(i, bi, $event)"' in text
        # The box itself must not start a drag: it would swallow the caret.
        assert 'class="text-box"' in text and '@mousedown="startTextBoxDrag' not in text


class TestPagingKeepsWhatWasTyped:
    def test_saving_captures_state_instead_of_rebuilding_it(self):
        body = method_source(page_text(), "_saveCurrentPage")
        assert "this._captureBoxes(i)" in body
        assert "querySelectorAll('.text-box textarea')" not in body, (
            "rebuilding the array from the DOM throws away orient, align and the "
            "HTML on every page change"
        )
        assert "this.activeBox[this.currentQ] = null" in page_text()

    def test_a_saved_box_keeps_its_orientation_and_alignment(self):
        capture = method_source(page_text(), "_captureBoxes")
        assert "box.html =" in capture and "box.text =" in capture
        # It must not replace the box object, which is what carries the rest.
        assert "boxes[bi] =" not in capture


class TestTheAnswerIsWhole:
    def test_a_paragraph_and_a_labelled_diagram_are_both_read(self):
        answer = {
            "type": "essay",
            "text": "The gradient is 3, so the line rises.",
            "pages": {"0": {"textBoxes": [{"text": "y = 3x + 2"}]}},
        }
        read = essay_answer_text(answer)
        assert "The gradient is 3" in read
        assert "y = 3x + 2" in read, (
            "the boxes were a fallback: the moment a paragraph existed the labels "
            "were dropped from the teacher's page and the AI's prompt"
        )

    def test_boxes_alone_still_read_as_before(self):
        answer = {"pages": {"1": {"textBoxes": [{"text": "halaman dua"}]},
                            "0": {"textBoxes": [{"text": "halaman satu"}]}}}
        assert essay_answer_text(answer) == "halaman satu\nhalaman dua"

    def test_a_bare_string_answer_is_unchanged(self):
        assert essay_answer_text("plain") == "plain"
        assert essay_answer_text({"answer": "legacy"}) == "legacy"


class TestTheGraph:
    def test_the_key_is_configuration_not_a_literal(self):
        text = page_text()
        assert "graphKey: {{ (config.get('DESMOS_API_KEY') or '')|tojson }}" in text
        assert "DESMOS_API_KEY" in (ROOT / "app" / "config.py").read_text(encoding="utf-8")

    def test_the_panel_is_behind_the_calculator_switch(self):
        text = page_text()
        start = text.index('<template x-if="antiCheat.allow_calculator">')
        end = text.index("</template>", start)
        assert "toggleGraph()" in text[start:end], (
            "the graph is a calculator: the teacher's one switch decides both"
        )
        assert 'x-show="showGraph && !submitted && !showExamAgreement"' in text

    def test_the_script_is_fetched_on_the_first_press_only(self):
        text = page_text()
        assert '<script src="/static/js/exam-graph.js"></script>' in text
        assert "await window.SGGraph.load(this.graphKey)" in text
        assert "window.SGGraph.mount(host)" in text

    def test_the_three_failures_are_named(self):
        text = page_text()
        assert "graphState = 'unconfigured'" in text
        assert "graphState = 'loading'" in text
        assert "this.graphState = (err && err.code) || 'offline'" in text
        assert "t('Kunci API Desmos belum diatur di server ini.','The Desmos API key is not configured on this server.')" in text

    def test_the_api_key_travels_on_the_request_not_in_the_url_of_the_page(self):
        js = GRAPH.read_text(encoding="utf-8")
        assert "?apiKey=" in js
        assert "encodeURIComponent(key)" in js
        assert "if (!key) return Promise.reject(failure('unconfigured'))" in js

    def test_the_calculator_follows_its_host(self):
        js = GRAPH.read_text(encoding="utf-8")
        assert "new root.ResizeObserver(fit)" in js
        assert "calculator.resize()" in js
        assert "orientationchange" in js

    def test_the_panel_is_responsive_in_css(self):
        text = page_text()
        assert ".sg-graph { width: min(92vw, 620px); height: min(70vh, 540px); }" in text
        assert ".sg-graph-host { width: 100%; height: 100%; }" in text
        assert "@media (max-width: 640px) { .sg-graph" in text

    def test_the_keypad_has_the_cambridge_functions(self):
        text = page_text()
        for key in ("sin", "cos", "tan", "asin", "acos", "atan", "log", "ln",
                    "nCr", "nPr", "sqrt", "cbrt", "abs"):
            assert "calcSciFunc('%s')" % key in text, key
        for literal in ("calcInsert('10^')", "calcInsert('e^')", "calcInsert('^2')",
                        "calcInsert('!')", "calcInsert('%')", "calcConstant('ans')"):
            assert literal in text, literal
        assert "calcDel()" in text

    def test_the_label_only_bracket_counter_is_gone(self):
        assert "calcParenLevel" not in page_text()


@pytest.mark.skipif(shutil.which("node") is None, reason="node is needed to run the parser")
class TestTheCalculatorEvaluates:
    """The real `calcEval` from the template, executed in node.

    Lifting the method out is deliberate: a test that restates the grammar proves
    the grammar in the test, not the calculator the pupil presses.
    """

    @pytest.fixture(scope="class")
    def api(self, tmp_path_factory):
        text = page_text()
        script = (
            "const api = { calcAngleMode: 'DEG', calcAns: 0,"
            + method_source(text, "calcEval") + ","
            + method_source(text, "calcFormat")
            + "};\n"
            "const src = process.argv[2];\n"
            "const cases = JSON.parse(require('fs').readFileSync(src, 'utf8'));\n"
            "const out = cases.map(c => api.calcEval(c));\n"
            "console.log(JSON.stringify(out));\n"
        )
        path = tmp_path_factory.mktemp("calc") / "calc.js"
        path.write_text(script, encoding="utf-8")
        return path

    def run_cases(self, api, cases):
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as fh:
            json.dump(cases, fh)
            name = fh.name
        proc = subprocess.run(["node", str(api), name], capture_output=True, text=True, timeout=30)
        assert proc.returncode == 0, proc.stderr
        return json.loads(proc.stdout)

    def test_brackets_change_the_arithmetic(self, api):
        values = self.run_cases(api, ["2×(3+4)^2", "2×3+4^2", "6÷(1+2)"])
        assert values[0] == pytest.approx(98)
        assert values[1] == pytest.approx(22)
        assert values[2] == pytest.approx(2)

    def test_trigonometry_follows_the_angle_mode(self, api):
        values = self.run_cases(api, ["sin(30)", "cos(60)", "tan(45)", "asin(0.5)"])
        assert values[0] == pytest.approx(0.5)
        assert values[1] == pytest.approx(0.5)
        assert values[2] == pytest.approx(1)
        assert values[3] == pytest.approx(30)

    def test_the_keys_that_were_missing(self, api):
        values = self.run_cases(api, ["log(100)", "ln(e)", "5!", "nCr(5,2)", "nPr(5,2)",
                                      "√(16)", "∛(27)", "10^2", "e^0", "50%", "3^2"])
        assert values == pytest.approx([2, 1, 120, 10, 20, 4, 3, 100, 1, 0.5, 9])

    def test_ans_is_the_last_answer(self, api):
        text = page_text()
        script = (
            "const api = { calcAngleMode: 'DEG', calcAns: 0,"
            + method_source(text, "calcEval") + ","
            + method_source(text, "calcFormat")
            + "};\n"
            "api.calcAns = api.calcEval('2+3');\n"
            "console.log(JSON.stringify([api.calcAns, api.calcEval('Ans×2')]));\n"
        )
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as fh:
            fh.write(script)
            name = fh.name
        proc = subprocess.run(["node", name], capture_output=True, text=True, timeout=30)
        assert proc.returncode == 0, proc.stderr
        assert json.loads(proc.stdout) == [5, 10]

    def test_a_malformed_expression_is_null_not_a_crash(self, api):
        values = self.run_cases(api, ["2+", "(3", "2++3", "sin()", "2×"])
        assert values[0] is None
        assert values[1] is None
        # 2++3 is read as 2 + (+3): a leading sign is a value, not an error.
        assert values[2] == pytest.approx(5)
        assert values[3] is None
        assert values[4] is None
