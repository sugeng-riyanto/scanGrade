/* The essay editor's own editing behaviour: lists, plain-text paste, and the
 * reading of what was typed.
 *
 * The toolbar already existed and the two list buttons did nothing a pupil could
 * see. Three causes, and this file is the first two:
 *
 *   1. **The button stole the selection.** A `<button>` outside the editable takes
 *      focus on `mousedown`; by the time `@click` runs, the browser's selection is
 *      the button, so `document.execCommand('insertUnorderedList')` had nothing to
 *      apply itself to. The template now calls `@mousedown.prevent`, and this file
 *      also keeps the last range inside the editor and restores it — two belts,
 *      because a range is what both the old API and the new one need.
 *
 *   2. **The list was created but nothing drew a marker.** Tailwind's preflight
 *      sets `ul, ol { list-style: none; margin: 0; padding: 0 }` on every page, so
 *      a `<ul><li>` produced by the deprecated API (or by this file) rendered as
 *      indented lines with no bullets. The markers are restored for the editor's
 *      own sheet in `theme.css` (`.sg-essay-sheet ul/ol`), scoped there so the
 *      rest of the app's lists stay as the design system draws them.
 *
 *   3. **`execCommand` is deprecated and its list behaviour differs by browser.**
 *      Rather than depend on it for the one thing a pupil reported broken, the
 *      toggle below builds and unwraps the `<ul>`/`<ol>` itself: select the
 *      paragraph's lines (the editor holds one paragraph), wrap them in `<li>`,
 *      or — pressing the same button again — take the items back out as plain
 *      lines. Bold/italic/underline still go through the platform (they are
 *      inline toggles with no structural equivalent and work today).
 *
 * The text that is *stored* is unchanged either way. `onParagraphInput` reads
 * `text` from the DOM (`textOf`, below) and the markup only travels beside it for
 * redisplay; a font is applied to the sheet, never written into the answer, so
 * choosing a font cannot change what a teacher or the AI reads.
 *
 * ## The writing tools, and the one rule they all keep
 *
 * The toolbar now carries what a written answer actually needs: bullets and
 * numbers (above), paragraph alignment, a tab, a table, a colour for a run, a
 * palette of symbols, and a set of equation templates. Each is here rather than in
 * the page so both the pupil's page and the teacher's preview of it — the same
 * template, rendered read-only — run one implementation.
 *
 * **None of them may add a character the pupil did not type, or remove one they
 * did.** That is what makes these safe to put in front of a graded answer:
 *
 *   * a symbol and an equation are plain text inserted at the caret, so the
 *     stored answer contains `√` or `x²` rather than an image or an element;
 *   * a tab is a `\t` in the text, so a line an indented answer survives the
 *     round trip;
 *   * alignment is written on the *block* the pupil is typing in, which is why
 *     `textOf` cannot see it at all;
 *   * a colour wraps the selected run (or a caret with a colour) and adds no
 *     character — an empty span is a caret, not a zero-width space;
 *   * a table's cells are read apart by a tab and its rows by a line, so a 2×2
 *     table is not `abcd` on the teacher's page.
 *
 * There is no maths renderer and none is added: a vendored KaTeX is hundreds of
 * kilobytes and a font set this app's spotty-WiFi users would pay for on every
 * exam page, and a CDN would break the offline rule. An equation here is a text
 * template the pupil edits — honest about what it is, and readable by the teacher
 * and the AI exactly as written.
 *
 * Loaded as a classic script before the page's Alpine component, like
 * `exam-view.js` beside it, and exercised in a real DOM by
 * `tests/unit/test_essay_answer_integrity.py` and `tests/unit/test_essay_toolbar.py`
 * (jsdom) — not by calling its functions in isolation, which is what lets a broken
 * wiring still pass.
 */
(function (root) {
    'use strict';

    /* The last selection inside an editor, per element. `mousedown` on a toolbar
     * button clears the browser's selection in some browsers even with
     * `preventDefault`, so the range a command should act on is kept here. */
    var saved = null;

    function docOf(editor) {
        return (editor && editor.ownerDocument) || (root.document || null);
    }

    function selection() {
        return typeof root.getSelection === 'function' ? root.getSelection() : null;
    }

    function inside(editor, node) {
        while (node) {
            if (node === editor) return true;
            node = node.parentNode;
        }
        return false;
    }

    /* Remember the range while it is still in the editable. Called on keyup,
     * mouseup and focus, which is every way a pupil's caret moves. */
    function remember(editor) {
        if (!editor) return;
        var sel = selection();
        if (!sel || !sel.rangeCount) return;
        var range = sel.getRangeAt(0);
        if (!inside(editor, range.commonAncestorContainer)) return;
        saved = { editor: editor, range: range.cloneRange() };
    }

    function caretToEnd(editor) {
        var doc = docOf(editor);
        if (!doc) return;
        var range = doc.createRange();
        range.selectNodeContents(editor);
        range.collapse(false);
        var sel = selection();
        if (!sel) return;
        sel.removeAllRanges();
        sel.addRange(range);
    }

    /* Put the caret back where it was, and say whether that worked.
     *
     * The live selection wins when it is already inside the paragraph, and that
     * order is load-bearing: `focus()` moves the caret to the start of the element
     * in a DOM without a real text input behind it (measured in jsdom, and the
     * reason a symbol first landed at position 0), so focusing before deciding
     * would replace a caret the pupil can see with one they cannot. Only a caret
     * that has actually left the paragraph is restored from `saved`.
     */
    function restore(editor) {
        if (!editor) return false;
        var sel = selection();
        if (sel && sel.rangeCount && inside(editor, sel.getRangeAt(0).commonAncestorContainer)) {
            return true;
        }
        if (typeof editor.focus === 'function') editor.focus({ preventScroll: true });
        sel = selection();
        if (!sel) return false;
        if (saved && saved.editor === editor) {
            sel.removeAllRanges();
            sel.addRange(saved.range);
            return true;
        }
        if (sel.rangeCount && inside(editor, sel.getRangeAt(0).commonAncestorContainer)) return true;
        return false;
    }

    /* Whether the caret sits inside a list item — the test that decides whether
     * Enter should make a new bullet (native) or a new paragraph (the page). */
    function inListItem(editor) {
        var sel = selection();
        if (!sel || !sel.rangeCount) return false;
        var node = sel.getRangeAt(0).startContainer;
        var stop = editor || null;
        while (node) {
            if (node.nodeType === 1 && node.tagName === 'LI') return true;
            if (stop && node === stop) return false;
            node = node.parentNode;
        }
        return false;
    }

    /* The editor's direct list, if it holds one. One paragraph holds at most one
     * list — the toggle below never nests — so the first is the answer. */
    function directList(editor) {
        for (var i = 0; i < editor.childNodes.length; i++) {
            var node = editor.childNodes[i];
            if (node.nodeType === 1 && (node.tagName === 'UL' || node.tagName === 'OL')) return node;
        }
        return null;
    }

    function listItems(list) {
        var items = [];
        for (var i = 0; i < list.childNodes.length; i++) {
            var node = list.childNodes[i];
            if (node.nodeType === 1 && node.tagName === 'LI') items.push(node);
        }
        return items;
    }

    /* The paragraph as lines, before they are list items.
     *
     * The editor is a contenteditable div holding what the browser typed into it:
     * `<div>`/`<p>` per visual line, `<br>` where a line was split, or a bare text
     * node on an untouched paragraph. Each of those becomes one line node, and a
     * run of inline nodes is gathered into one. `<p><br></p>` (what an empty
     * paragraph arrives as) is one empty line, not zero. */
    function lineNodes(editor) {
        var doc = docOf(editor);
        var lines = [];
        var run = null;
        function open() {
            run = doc.createElement('div');
            lines.push(run);
        }
        for (var i = 0; i < editor.childNodes.length; i++) {
            var node = editor.childNodes[i];
            if (node.nodeType === 1 && node.tagName === 'BR') {
                run = null;
                continue;
            }
            if (node.nodeType === 1 && /^(DIV|P|LI)$/.test(node.tagName)) {
                lines.push(node);
                run = null;
                continue;
            }
            if (!run) open();
            run.appendChild(node.cloneNode(true));
        }
        var real = lines.filter(function (line) {
            return (line.textContent || '').replace(/\u200b/g, '').trim() !== '';
        });
        if (real.length) return real;
        return [doc.createElement('div')];
    }

    /* Tell the page the DOM changed, so its own handler stores the new text and
     * markup and marks the answer dirty. A synthetic event is used rather than a
     * callback so the page keeps exactly one writer. */
    function announce(editor) {
        var doc = docOf(editor);
        if (!doc || typeof root.Event !== 'function') return;
        editor.dispatchEvent(new root.Event('input', { bubbles: true }));
    }

    function caretIntoList(list) {
        var items = listItems(list);
        var target = items.length ? items[items.length - 1] : null;
        if (!target) return;
        var doc = docOf(list);
        var range = doc.createRange();
        range.selectNodeContents(target);
        range.collapse(false);
        var sel = selection();
        if (!sel) return;
        sel.removeAllRanges();
        sel.addRange(range);
    }

    /* Turn this paragraph's lines into a list of `kind`, or back into plain lines
     * when it is already that kind. `kind` is 'ul' or 'ol'.
     *
     * Returns true when the editor was changed, so the caller can decide whether
     * to do anything else; false when there is no editor at all.
     */
    function toggleList(editor, kind) {
        if (!editor) return false;
        var doc = docOf(editor);
        var tag = kind === 'ol' ? 'OL' : 'UL';
        if (!restore(editor)) caretToEnd(editor);

        var list = directList(editor);
        if (list) {
            var items = listItems(list);
            if (list.tagName === tag) {
                /* Same button again: unwrap. Each item becomes a plain line, and an
                 * empty item keeps a <br> so the line still exists to type on. */
                var frag = doc.createDocumentFragment();
                items.forEach(function (li) {
                    var line = doc.createElement('div');
                    while (li.firstChild) line.appendChild(li.firstChild);
                    if (!line.textContent.trim() && !line.querySelector('br')) line.innerHTML = '<br>';
                    frag.appendChild(line);
                });
                editor.replaceChild(frag, list);
            } else {
                /* The other list: keep every item, change the wrapper. Moving the
                 * nodes keeps their text, their nesting and their undo-safe DOM
                 * identity instead of re-parsing innerHTML. */
                var replacement = doc.createElement(tag);
                items.forEach(function (li) { replacement.appendChild(li); });
                editor.replaceChild(replacement, list);
            }
        } else {
            var lines = lineNodes(editor);
            var fresh = doc.createElement(tag);
            lines.forEach(function (line) {
                var li = doc.createElement('li');
                while (line.firstChild) li.appendChild(line.firstChild);
                if (!li.textContent.trim() && !li.querySelector('br')) li.innerHTML = '<br>';
                fresh.appendChild(li);
            });
            editor.innerHTML = '';
            editor.appendChild(fresh);
        }
        if (list && list.tagName === tag) {
            /* unwrapped: put the caret at the end of what is now the last line */
            caretToEnd(editor);
        } else {
            caretIntoList(directList(editor) || editor);
        }
        announce(editor);
        return true;
    }

    /* Which list this paragraph is, for the toolbar's active state: 'ul', 'ol' or
     * null. Read from the DOM rather than remembered, so a paragraph restored from
     * a draft shows the right button as active. */
    function listKind(editor) {
        var list = editor ? directList(editor) : null;
        if (!list) return null;
        return list.tagName === 'OL' ? 'ol' : 'ul';
    }

    /* What the pupil typed, as text. `innerText` is what the browser's layout says
     * (it turns a `<li>` into its own line); `textContent` is the fallback for a
     * DOM without layout, which is what the tests drive. */
    function textOf(editor) {
        if (!editor) return '';
        if (typeof editor.innerText === 'string') return editor.innerText;
        /* No layout (a DOM without a renderer, or a browser that does not expose
         * `innerText`): walk the tree and put the line breaks where the browser
         * would — between blocks, list items and at every `<br>`. Falling back to
         * `textContent` alone ran two list items together (`alphabeta`), which is
         * the *answer* as far as the server is concerned. */
        var out = [];
        function trimTabs() {
            while (out.length && out[out.length - 1] === '\t') out.pop();
        }
        (function walk(node) {
            for (var i = 0; i < node.childNodes.length; i++) {
                var child = node.childNodes[i];
                if (child.nodeType === 3) { out.push(child.nodeValue); continue; }
                if (child.nodeType !== 1) continue;
                var tag = child.tagName;
                if (tag === 'BR') { out.push('\n'); continue; }
                /* A cell is its own field of the answer: cells are separated by a
                 * tab and rows by a line, which is how the same table reads on the
                 * teacher's page. Without this a 2×2 table of "a b / c d" arrived as
                 * "abcd", which is not the answer the pupil wrote. */
                if (tag === 'TD' || tag === 'TH') {
                    if (out.length && out[out.length - 1] !== '\n' && out[out.length - 1] !== '\t') out.push('\t');
                    walk(child);
                    if (out.length && out[out.length - 1] !== '\t') out.push('\t');
                    continue;
                }
                var block = /^(DIV|P|LI|UL|OL|H1|H2|H3|BLOCKQUOTE|TABLE|TR|THEAD|TBODY|TFOOT)$/.test(tag);
                if (block) {
                    trimTabs();
                    if (out.length && out[out.length - 1] !== '\n') out.push('\n');
                }
                walk(child);
                if (block) {
                    trimTabs();
                    if (out[out.length - 1] !== '\n') out.push('\n');
                }
            }
        })(editor);
        trimTabs();
        return out.join('');
    }

    /* Insert a run of plain text at the caret — a symbol, an equation template, a
     * tab. The answer is *text*: what is stored, synced, read by the teacher and
     * read by the AI is `textOf`, so a symbol is a character rather than an image
     * or an element. One insertion, one `input` event, one save.
     */
    function insertText(editor, text) {
        if (!editor || text === undefined || text === null) return false;
        var doc = docOf(editor);
        if (!restore(editor)) caretToEnd(editor);
        var sel = selection();
        if (!sel || !sel.rangeCount) return false;
        var range = sel.getRangeAt(0);
        range.deleteContents();
        var node = doc.createTextNode(text);
        range.insertNode(node);
        range.setStartAfter(node);
        range.collapse(true);
        sel.removeAllRanges();
        sel.addRange(range);
        remember(editor);
        announce(editor);
        return true;
    }

    /* The cell the caret is in, or null. A table is the one block where Tab has a
     * meaning of its own: moving to the next cell is what every sheet does, and a
     * tab character inside a cell would be invisible. */
    function cellOf(editor) {
        var sel = selection();
        if (!sel || !sel.rangeCount) return null;
        var node = sel.getRangeAt(0).startContainer;
        while (node) {
            if (node.nodeType === 1 && (node.tagName === 'TD' || node.tagName === 'TH')) return node;
            if (node === editor) return null;
            node = node.parentNode;
        }
        return null;
    }

    function caretInto(node, atEnd) {
        var doc = docOf(node);
        if (!doc) return false;
        var range = doc.createRange();
        range.selectNodeContents(node);
        range.collapse(!atEnd);
        var sel = selection();
        if (!sel) return false;
        sel.removeAllRanges();
        sel.addRange(range);
        return true;
    }

    function nextCell(cell) {
        var row = cell.parentNode;
        var cells = row ? row.children : [];
        for (var i = 0; i < cells.length - 1; i++) {
            if (cells[i] === cell) return cells[i + 1];
        }
        var nextRow = row && row.nextElementSibling;
        if (nextRow && nextRow.cells && nextRow.cells.length) return nextRow.cells[0];
        return null;
    }

    /* Tab inserts a tab, Shift+Tab takes one back — except inside a table cell,
     * where Tab moves to the next cell (and the last cell keeps the caret rather
     * than leaving the answer). A tab is a character in the stored text, so an
     * indented line survives the round trip to the teacher.
     */
    function insertTab(editor, back) {
        if (!editor) return false;
        var cell = cellOf(editor);
        if (cell && !back) {
            var target = nextCell(cell);
            if (!target) return false;
            cell = target;
            caretInto(cell, false);
            remember(editor);
            return true;
        }
        if (!restore(editor)) return false;
        if (!back) return insertText(editor, '\t');
        var sel = selection();
        if (!sel || !sel.rangeCount) return false;
        var range = sel.getRangeAt(0);
        var node = range.startContainer;
        var offset = range.startOffset;
        if (!node || node.nodeType !== 3 || offset <= 0) return false;
        var value = node.nodeValue || '';
        var cut = value.charAt(offset - 1) === '\t' ? 1 : 0;
        if (!cut) {
            var spaces = / {1,4}$/.exec(value.slice(0, offset));
            cut = spaces ? spaces[0].length : 0;
        }
        if (!cut) return false;
        node.nodeValue = value.slice(0, offset - cut) + value.slice(offset);
        range.setStart(node, offset - cut);
        range.collapse(true);
        sel.removeAllRanges();
        sel.addRange(range);
        remember(editor);
        announce(editor);
        return true;
    }

    /* Paragraph alignment. It is a property of the *block*, not of a run, which is
     * why it is written on the paragraph element the pupil is typing in rather
     * than wrapped around the selection: half a line centred is not a thing a
     * pupil asked for. `textOf` reads the same either way. */
    var ALIGNMENTS = { left: 1, center: 1, right: 1, justify: 1 };

    function align(editor, value) {
        if (!editor || !editor.style || !ALIGNMENTS[value]) return false;
        editor.style.textAlign = value;
        remember(editor);
        announce(editor);
        return true;
    }

    function alignOf(editor) {
        var value = (editor && editor.style && editor.style.textAlign) || 'left';
        return ALIGNMENTS[value] ? value : 'left';
    }

    /* Colour for the selected run — or for what is typed next, when nothing is
     * selected. The platform command is used where it exists (it is an inline
     * toggle, like bold, and keeps the undo stack intact) and a `<span>` where it
     * does not; either way the *text* is untouched, which is the rule this editor
     * already holds for its font. An empty span at a collapsed caret is a caret
     * with a colour, not an invisible character in the answer.
     */
    function foreColor(editor, hex) {
        if (!editor || !/^#[0-9a-fA-F]{6}$/.test(hex || '')) return false;
        if (!restore(editor)) caretToEnd(editor);
        var doc = docOf(editor);
        var done = false;
        if (doc && typeof doc.execCommand === 'function') {
            try {
                done = doc.execCommand('foreColor', false, hex);
            } catch (err) {
                done = false;
            }
        }
        if (!done) {
            var sel = selection();
            if (!sel || !sel.rangeCount) return false;
            var range = sel.getRangeAt(0);
            var span = doc.createElement('span');
            span.style.color = hex;
            if (range.collapsed) {
                range.insertNode(span);
                var caret = doc.createRange();
                caret.setStart(span, 0);
                caret.collapse(true);
                sel.removeAllRanges();
                sel.addRange(caret);
            } else {
                span.appendChild(range.extractContents());
                range.insertNode(span);
                var after = doc.createRange();
                after.selectNodeContents(span);
                sel.removeAllRanges();
                sel.addRange(after);
            }
        }
        remember(editor);
        announce(editor);
        return true;
    }

    /* A table, as a table. Cells are `contenteditable` by inheritance (the whole
     * paragraph is), so a pupil types in them like anywhere else; `textOf` reads
     * cells apart by a tab and rows by a line, so the stored answer is a readable
     * table rather than every cell run together. Bounded at 6×6: a table is a
     * small part of an essay, and an unbounded one is a way to fill a page with
     * borders instead of an answer.
     */
    function insertTable(editor, rows, cols) {
        if (!editor) return false;
        rows = Math.min(Math.max(parseInt(rows, 10) || 2, 1), 6);
        cols = Math.min(Math.max(parseInt(cols, 10) || 2, 1), 6);
        var doc = docOf(editor);
        var table = doc.createElement('table');
        table.className = 'sg-essay-table';
        var body = doc.createElement('tbody');
        for (var r = 0; r < rows; r++) {
            var tr = doc.createElement('tr');
            for (var c = 0; c < cols; c++) {
                var td = doc.createElement('td');
                td.appendChild(doc.createElement('br'));
                tr.appendChild(td);
            }
            body.appendChild(tr);
        }
        table.appendChild(body);
        if (!restore(editor)) caretToEnd(editor);
        var sel = selection();
        if (!sel || !sel.rangeCount) {
            editor.appendChild(table);
        } else {
            var range = sel.getRangeAt(0);
            range.deleteContents();
            range.insertNode(table);
        }
        var first = body.querySelector('td');
        if (first) caretInto(first, false);
        remember(editor);
        announce(editor);
        return true;
    }

    /* Paste as plain text. A copied web page would otherwise bring its own fonts,
     * colours and links into the answer, and the teacher would receive styling the
     * pupil cannot see the source of. The text is inserted at the caret, so one
     * paste is one undo step like typing. */
    function pastePlain(event) {
        if (!event || !event.clipboardData) return false;
        var text = event.clipboardData.getData('text/plain');
        if (text === null || text === undefined) return false;
        if (event.preventDefault) event.preventDefault();
        var doc = docOf(event.target);
        if (doc && typeof doc.execCommand === 'function') {
            if (doc.execCommand('insertText', false, text)) return true;
        }
        var sel = selection();
        if (!sel || !sel.rangeCount) return false;
        var range = sel.getRangeAt(0);
        range.deleteContents();
        var node = doc.createTextNode(text);
        range.insertNode(node);
        range.setStartAfter(node);
        range.collapse(true);
        sel.removeAllRanges();
        sel.addRange(range);
        announce(event.target);
        return true;
    }

    root.SGEssayEditor = {
        remember: remember,
        restore: restore,
        toggleList: toggleList,
        listKind: listKind,
        inListItem: inListItem,
        textOf: textOf,
        pastePlain: pastePlain,
        insertText: insertText,
        insertTab: insertTab,
        align: align,
        alignOf: alignOf,
        foreColor: foreColor,
        insertTable: insertTable
    };
})(typeof window !== 'undefined' ? window : this);
