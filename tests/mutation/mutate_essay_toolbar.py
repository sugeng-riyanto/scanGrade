"""Mutation check for the essay toolbar — "no tool changes the stored answer".

The toolbar's whole justification is one sentence: a written answer may be
*bulleted, centred, coloured, tabbed, tabled and annotated*, but no button may add
a character the pupil did not type or remove one they did. `tests/unit/
test_essay_toolbar.py` asserts that per tool, and `tests/unit/
test_essay_answer_integrity.py` asserts the page's half of it (the font is applied
to the sheet, never written into `getAnswersLight`).

A guard is only worth its green tick if it fails when the rule is broken, and the
rule here is unusually easy to break *almost* correctly: an editor that inserts a
zero-width space with a colour, a reader that drops the newline between two lines,
or a table whose cells run together all look right on screen and are wrong in the
answer the teacher marks.
So each mutation below removes exactly one of those clauses and asks the guards to
go red:

* the tab that becomes spaces, and the shift-tab that stops cutting;
* the alignment that writes a character onto the block it was meant only to style;
* the coloured caret that leaves an invisible character behind, and the colour
  palette that stops validating its own input;
* the answer reader that loses the newline a block contributes, joins table cells,
  or joins list items — the three shapes that arrive at the marking page as one
  run-on string;
* the symbol that arrives padded, the Tab that types a character inside a cell
  instead of moving on, and the font that is written into the stored text.

Every anchor is translated to the target file's own line-ending style by the shared
harness, and the baseline is checked first, so "caught" cannot mean "the guards were
already failing".

Run: python deploy/mutation_check.py --run mutate_essay_toolbar
"""
from __future__ import annotations

from tests.mutation.harness import main

GUARDS = [
    "tests/unit/test_essay_toolbar.py",
    "tests/unit/test_essay_answer_integrity.py",
]

EDITOR = "app/static/js/essay-editor.js"
PAGE = "app/templates/student/take_exam.html"

MUTATIONS = [

    # ── typing tools: a tab is a character, a shift-tab takes one back ──────
    ('a tab is typed as four spaces',
     EDITOR,
     "        if (!back) return insertText(editor, '\\t');",
     "        if (!back) return insertText(editor, '    ');"),

    ('shift-tab no longer takes the tab back',
     EDITOR,
     "        var cut = value.charAt(offset - 1) === '\\t' ? 1 : 0;",
     "        var cut = value.charAt(offset - 1) === '\\t' ? 0 : 0;"),

    # ── alignment is a property of the block, not a character ───────────────
    ('align writes a space into the answer',
     EDITOR,
     "        editor.style.textAlign = value;\n"
     "        remember(editor);",
     "        editor.style.textAlign = value;\n"
     "        editor.appendChild(docOf(editor).createTextNode(' '));\n"
     "        remember(editor);"),

    # ── colour wraps a run; a caret is a span, not an invisible character ───
    ('a coloured caret leaves a zero-width space',
     EDITOR,
     "            if (range.collapsed) {\n"
     "                range.insertNode(span);",
     "            if (range.collapsed) {\n"
     "                range.insertNode(span);\n"
     "                span.appendChild(doc.createTextNode('\\u200b'));"),

    ('the colour palette stops validating its own input',
     EDITOR,
     "        if (!editor || !/^#[0-9a-fA-F]{6}$/.test(hex || '')) return false;",
     "        if (!editor) return false;"),

    # ── what the teacher reads: the three shapes that run together ──────────
    ('the reader loses the line break after a block',
     EDITOR,
     "                    if (out[out.length - 1] !== '\\n') out.push('\\n');",
     "                    if (false) out.push('\\n');"),

    ('table cells are read as one string',
     EDITOR,
     "                if (tag === 'TD' || tag === 'TH') {\n"
     "                    if (out.length && out[out.length - 1] !== '\\n' && out[out.length - 1] !== '\\t') out.push('\\t');\n"
     "                    walk(child);\n"
     "                    if (out.length && out[out.length - 1] !== '\\t') out.push('\\t');\n"
     "                    continue;\n"
     "                }",
     "                if (tag === 'TD' || tag === 'TH') {\n"
     "                    walk(child);\n"
     "                    continue;\n"
     "                }"),

    ('list items are read as one line',
     EDITOR,
     "                var block = /^(DIV|P|LI|UL|OL|H1|H2|H3|BLOCKQUOTE|TABLE|TR|THEAD|TBODY|TFOOT)$/.test(tag);",
     "                var block = /^(DIV|P|UL|OL|H1|H2|H3|BLOCKQUOTE|TABLE|TR|THEAD|TBODY|TFOOT)$/.test(tag);"),

    # ── the two insertions a pupil presses ─────────────────────────────────
    ('an inserted symbol is padded with a space',
     EDITOR,
     "        var node = doc.createTextNode(text);",
     "        var node = doc.createTextNode(text + ' ');"),

    ('tab inside a cell types a character instead of moving on',
     EDITOR,
     "        if (cell && !back) {",
     "        if (false) {"),

    # ── the page: a font is a style, never part of the answer ──────────────
    # Read through a guard rather than through `this.essayFont(i)` on purpose: an
    # unguarded call also crashes the serialisation section of the integrity suite
    # (which builds `getAnswersLight` with a smaller state object), and a red suite
    # from a TypeError would report this as caught without the font assertion -- the
    # claim this mutation exists for -- ever being asked.
    ('the chosen font is written into the stored text',
     PAGE,
     "                    const textAnswer = (this.answersText[i] || '').trim();",
     "                    const textAnswer = ((this.answersText[i] || '').trim()\n"
     "                        + ((this.textFontFamily && this.textFontFamily[i]) ? ' [' + this.textFontFamily[i] + ']' : '')).trim();"),
]

if __name__ == "__main__":
    raise SystemExit(main("mutate_essay_toolbar", GUARDS, MUTATIONS))
