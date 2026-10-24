"""The essay toolbar's writing tools, executed — and the one rule they all keep.

The toolbar grew from bold/italic/underline and two list buttons to what a written
answer actually needs: alignment, a tab, a table, a colour, a symbol palette and a
set of equation templates. Two things about that are worth a suite of their own.

**A graded answer must not gain or lose a character from a button.** Every tool here
therefore has a *text* assertion as well as a markup one, taken from
`SGEssayEditor.textOf` — the same reader the page stores, syncs and sends to the
teacher and the AI. A colour wraps a run and adds nothing; an alignment is written
on the block; a table's cells are read apart by a tab and its rows by a line (without
that, a 2×2 table of `a b / c d` arrived as `abcd`); and an empty coloured caret is a
span, not a zero-width space.

**The controls are only as good as their wiring.** The module is executed in jsdom
against the real thing a pupil types into, and the page is *rendered* — not grepped —
so a control that appears twice (the text mode and the canvas-mode text tab), a
missing `t()` pair or a button that takes the caret on `mousedown` all fail here.

One honest limit, stated where the feature is: there is no maths renderer. An
equation is a text template the pupil edits, because a vendored KaTeX is hundreds of
kilobytes of fonts this app's users would pay for on every exam page over a spotty
connection, and a CDN would break the offline rule. The teacher reads exactly the
characters the pupil inserted.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from flask import g

ROOT = Path(__file__).resolve().parents[2]
TAKE = ROOT / "app" / "templates" / "student" / "take_exam.html"
MODULE = ROOT / "app" / "static" / "js" / "essay-editor.js"
THEME = ROOT / "app" / "static" / "css" / "theme.css"

NODE = shutil.which("node")

HARNESS = r"""
const { JSDOM } = require('jsdom');
const fs = require('fs');

const dom = new JSDOM('<!doctype html><html><body></body></html>',
                      { pretendToBeVisual: true });
const { window } = dom;
window.eval(fs.readFileSync(process.env.SG_ESSAY_EDITOR, 'utf8'));
const api = window.SGEssayEditor;
const doc = window.document;

const results = [];
function record(name, got, want) { results.push([name, got, want]); }

function editor(html) {
    const el = doc.createElement('div');
    el.setAttribute('contenteditable', 'true');
    el.innerHTML = html === undefined ? '<div><br></div>' : html;
    doc.body.appendChild(el);
    return el;
}

function caret(node, offset) {
    const sel = window.getSelection();
    const range = doc.createRange();
    range.setStart(node, offset);
    range.collapse(true);
    sel.removeAllRanges();
    sel.addRange(range);
}

function select(node, start, end) {
    const sel = window.getSelection();
    const range = doc.createRange();
    range.setStart(node, start);
    range.setEnd(node, end);
    sel.removeAllRanges();
    sel.addRange(range);
}

/* ── a symbol is a character ─────────────────────────────────────────────── */
{
    const el = editor('abc');
    caret(el.firstChild, 2);
    api.insertText(el, '×');
    record('a symbol lands at the caret', el.textContent, 'ab×c');
    record('a symbol is text, so the answer carries it', api.textOf(el).trim(), 'ab×c');
}
{
    /* The palette an equation button uses: the whole template, as one run. */
    const el = editor('');
    api.insertText(el, 'x = (−b ± √(b² − 4ac)) / 2a');
    record('an equation is the template as typed', api.textOf(el).trim(),
           'x = (−b ± √(b² − 4ac)) / 2a');
}

/* ── a tab is a character too ────────────────────────────────────────────── */
{
    const el = editor('ab');
    caret(el.firstChild, 2);
    api.insertTab(el, false);
    record('a tab is inserted at the caret', el.textContent, 'ab\t');
    record('the tab is in the stored text', api.textOf(el).replace(/\n/g, ''), 'ab\t');
}
{
    const el = editor('a\tb');
    caret(el.firstChild, 2);
    api.insertTab(el, true);
    record('shift-tab takes the tab back', el.textContent, 'ab');
}
{
    const el = editor('a     b');
    caret(el.firstChild, 6);
    api.insertTab(el, true);
    record('shift-tab takes up to four spaces', el.textContent, 'a b');
}

/* ── alignment is on the block, and invisible to the text ────────────────── */
{
    const el = editor('abc');
    const before = api.textOf(el);
    api.align(el, 'center');
    record('align writes the block', el.style.textAlign, 'center');
    record('align reads back', api.alignOf(el), 'center');
    record('align does not touch the answer', api.textOf(el), before);
    api.align(el, 'sideways');
    record('an unknown alignment is refused', el.style.textAlign, 'center');
}
{
    const el = editor('abc');
    record('an unaligned paragraph reads as left', api.alignOf(el), 'left');
}

/* ── colour is a wrapper, never a character ──────────────────────────────── */
{
    const el = editor('abcdef');
    select(el.firstChild, 2, 5);
    const before = api.textOf(el);
    api.foreColor(el, '#b91c1c');
    record('colour wrapped the selection',
           el.innerHTML.indexOf('<span style="color: rgb(185, 28, 28);">cde</span>') !== -1
           || el.innerHTML.indexOf('<span style="color: #b91c1c;">cde</span>') !== -1, true);
    record('colour adds no character', api.textOf(el), before);
    record('colour keeps the text', el.textContent, 'abcdef');
}
{
    /* A caret with a colour and nothing selected: the span must be empty, because a
       zero-width space would be a character in the answer the pupil never typed. */
    const el = editor('ab');
    caret(el.firstChild, 1);
    api.foreColor(el, '#1d4ed8');
    record('a coloured caret adds no text', api.textOf(el).trim(), 'ab');
    record('a coloured caret is a span with no text',
           el.querySelectorAll('span').length >= 1
           && (el.querySelector('span').textContent || ''), '');
}
{
    const el = editor('abc');
    record('a colour that is not a colour is refused', api.foreColor(el, 'red'), false);
}

/* ── a table is a table, and reads as one ────────────────────────────────── */
{
    const el = editor('');
    api.insertTable(el, 3, 2);
    const table = el.querySelector('table');
    record('the table exists', !!table, true);
    record('it has three rows', table.querySelectorAll('tr').length, 3);
    record('and two cells per row', table.querySelectorAll('tr')[0].children.length, 2);
    record('the first cell holds the caret',
           table.querySelector('td').contains(window.getSelection().anchorNode), true);
}
{
    const el = editor('');
    api.insertTable(el, 99, 99);
    record('a table is bounded', el.querySelectorAll('tr').length, 6);
    record('in both directions', el.querySelector('tr').children.length, 6);
}
{
    const el = editor('');
    el.innerHTML = '<table><tbody>'
        + '<tr><td>a</td><td>b</td></tr><tr><td>c</td><td>d</td></tr>'
        + '</tbody></table>';
    record('cells and rows are read apart', api.textOf(el), 'a\tb\nc\td\n');
}
{
    /* Inside a cell, Tab moves on rather than inserting a tab in the answer. */
    const el = editor('');
    el.innerHTML = '<table><tbody><tr><td>a</td><td>b</td></tr></tbody></table>';
    const cells = el.querySelectorAll('td');
    caret(cells[0].firstChild, 0);
    api.insertTab(el, false);
    record('tab inside a table moves to the next cell',
           cells[1].contains(window.getSelection().anchorNode), true);
    record('and adds no tab to the answer', api.textOf(el), 'a\tb\n');
}
{
    /* A list is still a list: the marker is the browser's, the text is the answer. */
    const el = editor('<div>satu</div><div>dua</div>');
    api.toggleList(el, 'ul');
    record('the list still toggles', el.querySelectorAll('li').length, 2);
    record('and its text is unchanged', api.textOf(el).replace(/\n+/g, ' ').trim(), 'satu dua');
}

process.stdout.write(JSON.stringify(results));
"""


def _run_harness():
    env = dict(os.environ)
    env["SG_ESSAY_EDITOR"] = str(MODULE)
    proc = subprocess.run([NODE, "-e", HARNESS], cwd=str(ROOT), env=env,
                          capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr
    return {name: (got, want) for name, got, want in json.loads(proc.stdout)}


needs_node = pytest.mark.skipif(NODE is None, reason="needs node to execute the editor")
needs_jsdom = pytest.mark.skipif(
    NODE is None or subprocess.run(
        [NODE, "-e", "require.resolve('jsdom')"], cwd=str(ROOT),
        capture_output=True).returncode != 0,
    reason="needs the jsdom dev dependency (`npm install`)",
)

#: The page's own toolbar resolver, lifted out and executed.
#:
#: The Alpine component cannot be instantiated without the whole page, and the
#: behaviour worth proving is the *caret* — a toolbar pressed before the pupil has
#: clicked anything. So the two methods are cut out of the template by brace
#: counting (the same way `test_essay_answer_integrity` reads the serialiser) and
#: driven against a paragraph in jsdom, with `currentPara` stubbed and
#: `execCommand` recorded rather than performed.
TARGET_HARNESS = r"""
const { JSDOM } = require('jsdom');
const fs = require('fs');
const src = fs.readFileSync(process.env.SG_TAKE_EXAM, 'utf8');

function method(name) {
    const start = src.indexOf('        ' + name + '(');
    if (start < 0) throw new Error('no method ' + name);
    const open = src.indexOf('{', start);
    let depth = 0;
    for (let i = open; i < src.length; i += 1) {
        if (src[i] === '{') depth += 1;
        else if (src[i] === '}') {
            depth -= 1;
            if (depth === 0) return src.slice(start, i + 1);
        }
    }
    throw new Error('unterminated ' + name);
}

const dom = new JSDOM('<!doctype html><html><body><div class="sg-essay-para" ' +
                      'data-para="0:p0" contenteditable="true"><p>siap</p></div></body></html>');
global.window = dom.window;
const doc = dom.window.document;
global.document = doc;              // the page's methods read the bare global

const el = doc.querySelector('.sg-essay-para');
/* jsdom implements `contenteditable` as an attribute and not the property the
   browser computes from it, so the resolver's "the caret is already here" test
   would never be true and every call would look like a fresh sheet. */
Object.defineProperty(el, 'isContentEditable', { get: function () { return true; } });
const results = [];
function record(name, got, want) { results.push([name, got, want]); }

const saved = [];
const ctx = {
    currentPara: function () { return { el: el, pIdx: 0 }; },
    onParagraphInput: function (qIdx, pIdx, target) {
        saved.push([qIdx, pIdx, target === el]);
    },
    refreshListState: function () {}
};
const api = new Function('ctx', 'with (ctx) { return {' +
    method('essayTarget') + ',' + method('execCmd') + '}; }')(ctx);
ctx.essayTarget = api.essayTarget;

let commanded = null;
doc.execCommand = function (cmd) { commanded = cmd; return true; };
dom.window.SGEssayEditor = { remember: function () {}, restore: function () { return true; } };

doc.body.focus();
record('nothing was focused to begin with', doc.activeElement === doc.body, true);

const target = api.essayTarget.call(ctx, 0);
record('the paragraph is the one the page named', target.el === el, true);
record('it takes the focus', doc.activeElement === el, true);
const sel = dom.window.getSelection();
record('the caret is inside it', el.contains(sel.anchorNode), true);
record('the caret is at the end of the paragraph',
       sel.anchorNode === el && sel.anchorOffset === el.childNodes.length, true);

const bold = api.execCmd.call(ctx, 0, 'bold');
record('the command reached the browser', commanded, 'bold');
record('the page was told to save the paragraph',
       saved.length === 1 && saved[0][0] === 0 && saved[0][1] === 0 && saved[0][2] === true, true);

/* A selection the pupil made is theirs: the resolver must not move it. */
const range = doc.createRange();
range.selectNodeContents(el.firstChild);
sel.removeAllRanges();
sel.addRange(range);
api.essayTarget.call(ctx, 0);
record('a selection the pupil made is kept', String(sel), 'siap');

process.stdout.write(JSON.stringify(results));
"""


def _run_target_harness():
    env = dict(os.environ)
    env["SG_TAKE_EXAM"] = str(TAKE)
    proc = subprocess.run([NODE, "-e", TARGET_HARNESS], cwd=str(ROOT), env=env,
                          capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


@pytest.fixture(scope="module")
def executed():
    """The page's toolbar resolver, run — or a skip that names what is missing."""
    if NODE is None:
        pytest.skip("needs node to execute the page's resolver")
    resolved = subprocess.run([NODE, "-e", "require.resolve('jsdom')"], cwd=str(ROOT),
                              capture_output=True).returncode == 0
    if not resolved:
        pytest.skip("needs the jsdom dev dependency (`npm install`)")
    return _run_target_harness()


@needs_node
@needs_jsdom
class TestTheToolsInARealDom:
    @pytest.fixture(scope="class")
    def results(self):
        return _run_harness()

    @pytest.mark.parametrize("name", [
        "a symbol lands at the caret",
        "a symbol is text, so the answer carries it",
        "an equation is the template as typed",
        "a tab is inserted at the caret",
        "the tab is in the stored text",
        "shift-tab takes the tab back",
        "shift-tab takes up to four spaces",
        "align writes the block",
        "align reads back",
        "align does not touch the answer",
        "an unknown alignment is refused",
        "an unaligned paragraph reads as left",
        "colour wrapped the selection",
        "colour adds no character",
        "colour keeps the text",
        "a coloured caret adds no text",
        "a coloured caret is a span with no text",
        "a colour that is not a colour is refused",
        "the table exists",
        "it has three rows",
        "and two cells per row",
        "the first cell holds the caret",
        "a table is bounded",
        "in both directions",
        "cells and rows are read apart",
        "tab inside a table moves to the next cell",
        "and adds no tab to the answer",
        "the list still toggles",
        "and its text is unchanged",
    ])
    def test_case(self, results, name):
        got, want = results[name]
        assert got == want, f"{name}: {got!r} != {want!r}"


def _render_exam(app):
    """The page as its route renders it — a Jinja fault or a dead Alpine attribute
    never reaches a grep, so the controls are checked in the HTML a pupil gets."""
    from datetime import datetime, timezone
    ctx = {
        "exam": {"id": "e1", "title": "T", "total_questions": 1,
                 "duration_minutes": 30, "question_types": {}, "pdf_page_urls": []},
        "anti_cheat_config": "{\"anti_cheat_enabled\": true}",
        "exam_started_at": datetime(2026, 9, 28, 7, 0, tzinfo=timezone.utc).isoformat(),
        "recovery_code": "", "question_options": {},
        "deadline": datetime(2026, 9, 28, 7, 30, tzinfo=timezone.utc).isoformat(),
        "deadline_reason": "duration", "seconds_left": 1800, "window_end": None,
        "away_grace_seconds": 15, "away_grace_chances": 2,
        "student_name": "Ahmad", "student_class_label": "7A",
    }
    with app.test_request_context("/student/exams/e1"):
        g.user_id, g.user_name, g.user_role = "stu-1", "Murid Uji", "murid"
        g.tz_offset, g.show = 7, {}
        return app.jinja_env.get_template("student/take_exam.html").render(**ctx)


class TestTheRenderedToolbar:
    @pytest.fixture(scope="class")
    def page(self, app):
        return _render_exam(app)

    def test_every_tool_is_in_both_essay_modes(self, page):
        """Two blocks render this toolbar: the text answer and the canvas-mode text
        tab. A control in one and not the other is a pupil's tool going missing when
        the teacher set the paper to canvas."""
        assert page.count("@mousedown.prevent=\"applyTab(i)\"") == 2
        assert page.count("@mousedown.prevent=\"insertSymbol(i,") == 2 * (12 + 6)
        assert page.count("@mousedown.prevent=\"insertTable(i,") == 2 * 4
        assert page.count("@mousedown.prevent=\"applyColor(i,") == 2 * 6
        assert page.count("setEssayAlign(i, $event.target.value)") == 2
        assert page.count("@keydown.tab.prevent=\"onParagraphTab(i, pIdx, $event)\"") == 2

    def test_each_palette_is_a_native_disclosure(self, page):
        """A `<details>` is open/closed with no JavaScript state to duplicate, and it
        is still a disclosure for a keyboard and a screen reader."""
        assert page.count('<details class="sg-tool-pop">') == 2 * 4
        assert page.count("<summary") >= 8
        # The panels are positioned against the toolbar, not the button: a popover
        # anchored to the button overflows the screen on a phone when the button is
        # near the right edge (measured at 390px), and the toolbar is one element.
        assert page.count("rounded-t-xl relative") == 2

    def test_the_controls_say_what_they_do_in_both_languages(self, page):
        for pair in ("t('Tebal','Bold')", "t('Perataan paragraf','Paragraph alignment')",
                     "t('Tab / indentasi','Tab / indent')",
                     "t('Warna huruf','Text colour')", "t('Simbol','Symbols')",
                     "t('Sisipkan rumus','Insert equation')",
                     "t('Sisipkan tabel','Insert table')",
                     "t('Kiri','Left')", "t('Tengah','Center')",
                     "t('Kanan','Right')", "t('Rata kiri-kanan','Justify')"):
            # Two toolbars, and the alignment/colour/tab controls carry both a
            # `title` (a mouse) and an `aria-label` (a screen reader).
            assert page.count(pair) >= 2, pair

    def test_the_colour_palette_is_bounded(self, page):
        """Six named colours, not a free picker: white on white is a way to make an
        answer unmarkable, and the sheet is the thing the teacher reads."""
        for hexcode in ("#111827", "#b91c1c", "#1d4ed8", "#15803d", "#c2410c", "#7e22ce"):
            assert page.count(f"applyColor(i, '{hexcode}')") == 2, hexcode

    def test_the_equation_palette_is_text_templates(self, page):
        """No renderer is added: the templates are what the pupil and the teacher read."""
        assert page.count("a² + b² = c²") >= 2
        assert page.count("x = (−b ± √(b² − 4ac)) / 2a") >= 2
        assert "katex" not in page.lower()
        assert "mathjax" not in page.lower()

    def test_no_control_steals_the_caret(self, page):
        """A button takes focus on `mousedown`; a command with no selection in the
        paragraph is a command that does nothing. Every new control prevents it."""
        for call in ("applyTab(i)", "insertTable(i, 2, 2)", "applyColor(i, '#111827')",
                     "insertSymbol(i, '×')"):
            assert page.count(f"@mousedown.prevent=\"{call}\"") == 2, call

    def test_the_format_buttons_name_the_question_they_act_on(self, page):
        """Bold, Italic and Underline have to resolve their paragraph like every
        other tool does. They used to read a stored `_activeEditor`, which is null
        until a paragraph has been clicked — so the first press on a fresh answer
        ran the platform command against nothing, and the button looked dead."""
        for cmd in ("bold", "italic", "underline"):
            assert page.count(f"@mousedown.prevent=\"execCmd(i, '{cmd}')\"") == 2, cmd
            bare = page.replace(f"execCmd(i, '{cmd}')", "")
            assert f"execCmd('{cmd}')" not in bare, cmd


class TestTheWiring:

    def test_the_module_exports_every_tool_the_page_calls(self):
        src = MODULE.read_text(encoding="utf-8")
        for name in ("insertText", "insertTab", "align", "alignOf", "foreColor",
                     "insertTable"):
            assert f"        {name}: {name}" in src, name

    def test_every_page_method_exists(self):
        src = TAKE.read_text(encoding="utf-8")
        for method in ("paraEl(qIdx)", "currentPara(qIdx)", "essayTarget(qIdx)",
                       "runEssayCommand(qIdx, fn)",
                       "essayAlign(i)", "setEssayAlign(i, value)", "applyTab(i)",
                       "insertSymbol(i, text)", "insertTable(i, rows, cols)",
                       "applyColor(i, hex)", "execCmd(qIdx, cmd)",
                       "onParagraphTab(qIdx, pIdx, ev)"):
            assert f"        {method} {{" in src, method

    def test_every_writing_tool_resolves_the_same_target(self):
        """One resolver, so a tool cannot be the one control that still needs the
        pupil to have clicked first."""
        src = TAKE.read_text(encoding="utf-8")
        for method in ("runEssayCommand", "applyList", "execCmd"):
            start = src.index(f"        {method}(")
            end = src.index("\n        },", start)
            assert "this.essayTarget(qIdx)" in src[start:end], method

    def test_the_target_puts_the_caret_in_the_paragraph(self, executed):
        """Executed, not grepped: the toolbar's resolver is lifted out of the page
        and driven against a paragraph nobody has clicked, which is the state a
        fresh answer is in."""
        results = {name: got for name, got, _ in executed}
        assert results["nothing was focused to begin with"] is True
        assert results["the paragraph is the one the page named"] is True
        assert results["it takes the focus"] is True
        assert results["the caret is inside it"] is True
        assert results["the caret is at the end of the paragraph"] is True
        assert results["a selection the pupil made is kept"] == "siap"
        assert results["the command reached the browser"] == "bold"
        assert results["the page was told to save the paragraph"] is True


        """The sheet follows the theme, so a border fixed to slate is a light line
        on a dark sheet."""
        css = THEME.read_text(encoding="utf-8")
        assert ".sg-essay-para table {" in css
        assert "border: 1px solid var(--border-strong);" in css

    def test_the_module_names_the_suites_that_execute_it(self):
        """The docstring pointed at a file that does not exist; a reader followed it."""
        src = MODULE.read_text(encoding="utf-8")
        assert "test_essay_editor_dom.py" not in src
        assert "tests/unit/test_essay_toolbar.py" in src
