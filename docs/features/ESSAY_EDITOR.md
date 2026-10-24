# Essay editor — the answer sheet a pupil writes on

The text-answer surface on `/student/exams/<id>`, and the path its words take to
the teacher who marks them. Two production defects were found and fixed before
any visual work was done; this page records both, because "the toolbar looks
better" is not the part that matters.

## The two data defects (fixed)

### 1. A fresh essay's first paragraphs were never captured

`paragraphs[i]` was created only by **Enter** or **+ Paragraf**. The first
paragraph was drawn from a Jinja fallback, and `onParagraphInput` began with

```js
if (!el || !this.paragraphs[qIdx] || !this.paragraphs[qIdx][pIdx]) return;
```

so a pupil who opened the question, typed one long answer and pressed Submit had
`answersText[i] === ''`. `periodicSync` only writes when `dirty` is set (it is set
by `onParagraphUpdate`, which never ran), and `getAnswersLight()` serialised
`text: ''` with no `paragraphs`. The page kept showing the words until it was
reloaded; the server had nothing.

Fixed by `seedParagraphs()` (one empty paragraph per essay question before the
first keystroke) **and** by making the input handler create the row if it is
still missing. Both, because a fallback that only exists in the template is the
same trap for the next editor.

### 2. The marking surfaces printed a prefix

| surface | before | after |
|---|---|---|
| `/teacher/grade/<id>` | `student_text[:300]` + `...` | the whole answer, `whitespace-pre-wrap` |
| `/teacher/api/grading-queue/<exam>` per question | `text[:100]` | the whole answer |
| the same route's sidebar summary | `text[:150]` | `text[:150]` — a summary on purpose |

Both used to read the stored dict themselves (`.text` or `.answer`), so an
answer stored only as `paragraphs` or as text boxes on the paper showed nothing.
One reader now: `app/services/essay_text.py:essay_answer_text()`, used by the
marking page, the queue **and** the AI prompt (`answer_text_of` delegates to it).

## What is stored

```json
{"type": "essay_text",
 "text": "paragraph one\n\nparagraph two\n\nbullet one\nbullet two",
 "paragraphs": [{"id": "p1", "text": "...", "html": "<div>...</div>", "order": 0}],
 "pages": {"0": {"canvas": "data:image/png;base64,…", "textBoxes": [{"text": "…"}]}}}
```

* `text` is what the pupil typed, joined with a blank line between paragraphs —
  the string the character counter, the AI prompt and the teacher all read.
* `html` is only for redisplay in the editor. No teacher surface and no AI
  prompt reads it.
* `pages[*].canvas` is a drawing, not text. `essay_answer_text` ignores it, and
  OCR ("Baca Canvas" on the marking page) is what turns it into an answer.

## The editor

* **Width.** An essay question is never drawn in the 34 rem side column, even
  when the pupil chose the side-by-side layout: `isEssayCurrent` removes the
  `exam-stage--left` class for an essay, so the editor spans the stage under the
  paper. Choice questions keep their column.
* **Lists.** `document.execCommand('insertUnorderedList')` is gone. Two reasons
  it did nothing: the toolbar button took focus on `mousedown` (so the command had
  no selection), and Tailwind's preflight sets `ul, ol { list-style: none }`, so
  even a list that was created drew no bullets. `app/static/js/essay-editor.js`
  now builds the `<ul>`/`<ol>` itself from the paragraph's lines, toggles it back
  to plain lines on a second press, and `theme.css` restores markers inside
  `.sg-essay-para` only.
* **Enter.** Inside a list item the browser's own Enter makes the next bullet
  (and an empty item leaves the list); outside a list the page adds a paragraph,
  as before. Shift+Enter is a soft line break.
* **Paste.** Plain text only. A pasted page cannot bring fonts, colours or links
  into an answer a teacher reads.
* **Font and size.** A toolbar select, applied to the sheet (`font-family`,
  `font-size`), stored in the same `textFontFamily[i]` / `textFontSize[i]` fields
  the canvas text boxes already used, and remembered across sits in
  `localStorage` (`sg_essay_font`, `sg_essay_size`). **No part of it is written
  into the answer**: a test runs `getAnswersLight()` before and after choosing
  Times New Roman at 20px and asserts the serialised answer is byte-identical.
* **Writing tools.** The toolbar carries what a written answer needs, and every
  one of them keeps the rule above: the *text* `textOf` returns is the answer, so a
  tool may draw it differently but may not add or remove a character. **Symbols**
  (12: `× ÷ ± ≠ ≤ ≥ ≈ √ π ∑ ∫ ∞`) and **equations** (six templates, e.g.
  `x = (−b ± √(b² − 4ac)) / 2a`) are plain text inserted at the caret. **Tab**
  inserts a `\t` (`Shift+Tab` takes it back; inside a table `Tab` moves to the next
  cell instead), and the sheet already sets `tab-size: 4`. **Alignment**
  (left/centre/right/justify) is written on the block the pupil is typing in, so
  `textOf` cannot see it. **Colour** wraps the selected run in a span — six named
  colours, not a free picker, because white on white makes an answer unmarkable —
  and a colour chosen with nothing selected is an *empty* span, never a zero-width
  space. **Table** inserts a real `<table>` (2×2, 3×3, 2×4, 4×3), and `textOf`
  reads cells apart by a tab and rows by a line: without that, a 2×2 table of
  `a b / c d` reached the teacher as `abcd`. Each palette is a native `<details>`,
  so it needs no JavaScript state to duplicate in the preview; its panel is
  positioned against the **toolbar**, not the button, because a panel anchored to
  a button near the right edge overflowed the screen at 390px.
* **No maths renderer.** An equation is a template, not a rendered formula. A
  vendored KaTeX is hundreds of kilobytes of fonts these users would fetch on
  every exam page over spotty WiFi, and a CDN breaks the offline rule; the teacher
  and the AI read exactly the characters the pupil inserted.
* **Colour theme (not text colour).** A separate "exam focus mode" palette was
  considered and **not** added: the app already carries one token set remapped for dark mode, and a third
  set for a single page is a palette to keep in step forever. The comfort the mode
  was for is met by drawing the sheet from `--bg-subtle` instead of pure white, in
  both themes. The sheet is `--bg-subtle` with `--text` (light: `#f8fafc` on
  `#1e293b`, ≈14:1; dark: `#202436` on `#e6e9f0`, ≈11:1). No new palette, no pure
  white page for a two-hour sitting, and the focus rule uses the theme's accent
  token. The exam page's existing dark-mode remap applies.
* **Autosave.** The exam bar already showed the sync state; the same label now
  sits directly under the editor ("Tersimpan otomatis di perangkat ini"), so a
  pupil does not have to hunt for it. The loop itself is unchanged and already
  offline-first: `localStorage` on every tick, a write to `/api/student/sync-draft`
  every 60 s when dirty, an `online` listener that syncs at once, and a pending
  submit kept locally until the link is back.

## Tests

`tests/unit/test_essay_answer_integrity.py` (54 cases):

* the reader, for every stored shape (text, paragraphs, boxes, drawing, legacy
  string), and the AI reading the identical text;
* the marking page rendered with a 1 200-character answer — the last sentence is
  asserted present, and the template is asserted not to slice;
* the grading queue route driven with a fake Supabase: per-question preview equal
  to the whole answer, sidebar summary still 150 characters;
* the editor in **jsdom** — bullets, numbering, toggling off, switching kind, an
  empty paragraph, the remembered range when focus has left, Enter-in-list
  detection, plain-text paste (a `<script>` in the clipboard never becomes an
  element), and the font that leaves the stored answer untouched;
* wiring guards: `@mousedown.prevent` on all four list buttons, no `execCommand`
  list command anywhere, the module loaded before the component, `seedParagraphs()`
  called before `loadDraft()`, and the list markers present in `theme.css`.

`tests/unit/test_essay_toolbar.py` holds the writing tools (29 cases in jsdom plus
the rendered page): a symbol lands at the caret and is in the stored text, a tab and
`Shift+Tab`, alignment written and read back with the text unchanged, a colour that
adds no character (including a collapsed caret), a table of the requested shape,
bounded at 6×6, with the caret in its first cell, cells read apart by a tab, `Tab`
inside a cell moving on rather than inserting — and, on the page as its route
renders it, every control **twice** (the text answer and the canvas-mode text tab),
the bilingual pairs, the bounded palette, the templates and `@mousedown.prevent` on
each. The suite is also in `deploy/theme_gate.sh`, so the gate proves the editor's
tools rather than trusting them.

The jsdom half needs the dev-only `jsdom` package (`npm install`); without it those
cases skip and the Python half still runs — but only outside the release gate. Inside
it, a skipped test is a refusal: `deploy/theme_gate.sh` reads pytest's own summary and
exits 3 rather than report an editor proven by a run that never executed it, so the
half that touches a pupil's answer cannot be the half nothing read.

## Limits

* jsdom is not a browser: it proves the DOM operations and the wiring, not layout
  or paint. The visual checks (width at 375/768/1366, markers, dark mode) were
  made by reading the rules and the rendered HTML, not by a screenshot; a real
  device pass is still worth doing.
* `innerText` is what a browser gives for line breaks between `<li>`s; the
  fallback used where there is no layout walks blocks itself, and that fallback is
  what the jsdom tests exercise.
* The nested-list rules (circle/square/lower-alpha) are still drawn by the
  stylesheet when a list nests, but the toolbar's `Tab` is an indent *character*,
  not a list-indent command: pressing it inside a bullet inserts a tab in the item.
  A list that nests one level is drawn correctly; there is no button that nests it.
* The toolbar wraps to three rows at 390px (fifteen controls and a label); the
  popovers stay inside the screen at that width, measured, and the sheet below is
  unaffected. A visual pass on a real phone is still worth doing — jsdom proves the
  operations and the screenshots here were of the toolbar alone.

## Canvas text boxes: the same tools on the paper

An essay answered on the PDF got a `<textarea>` per box, so a pupil could set a
font and move the box and nothing else — no bullets, no numbering, no symbols, no
table, no colour, while the essay field beside it had all of them. A box is a
`contenteditable` now (`app/static/js/essay-editor.js` does the work, as it does
for the paragraphs), and one toolbar above the paper acts on whichever box has
the caret (`activeBox[i]`). What it offers:

| group | controls |
|---|---|
| inline | bold, italic, underline |
| structure | bulleted list, numbered list, tab/indent, 2×2/3×3/2×4/4×3 table |
| block | alignment (left, centre, right, justify) |
| type | font family, 12–32 pt, colour (six-swatch palette) |
| maths | sixteen symbols, six equation templates |
| box | vertical orientation, delete |

Three properties are deliberate:

* **The stored answer is still text.** A box carries `text` (what
  `SGEssayEditor.textOf` reads, table cells separated by a tab and rows by a
  line) and `html` (only ever painted back into the box being edited). The
  teacher, the print report and the AI read `text`; nothing server-side renders
  the HTML.
* **The HTML is sanitised on the way in.** `sanitizeBoxHtml` removes scripts,
  frames, media, form controls and every `on*` attribute, so a paste — or a
  crafted draft restored from another device — cannot carry executable content
  into a page.* **Paging cannot lose a box.** `_saveCurrentPage` used to rebuild the array from
  `.text-box textarea` elements, which threw away `orient`, `align` and now the
  HTML every time the pupil changed page. It captures from the editors into the
existing objects instead (`_captureBoxes`), and the `x-for` key includes the
  page so a reused element cannot paint the previous page's HTML.
* **Pressing Teks places a box.** Selecting the text tool used to set a mode and
  nothing else, so the rich-text row above the paper arrived grey and disabled
  until the pupil worked out that the page had to be clicked — a teacher reading
  the preview called it "belum aktif ya?". The press now places the page's first
  box (6% in, 8% down: the top-left margin, clear of a printed header) and puts
  the caret in it, so the row is live and typing starts on the same gesture. A
  page that already has a box opens its **last** one rather than growing another
  empty box on every press, a second box is still one click on the paper away, and
  deleting the active box selects the one that remains. Two details make the caret
  arrive at all: the tool button carries `@mousedown.prevent` (a button takes focus
  on mouse-down, *after* the tick that focuses the box, so the caret would be
  handed back), and `focusTextBox` retries twice and then once on a short timer,
  because Alpine inserts the box's element on its own tick. `addTextBoxAt` is the
  single place a box is built — the paper click and the canvas pointer-down both
  call it, where before each had its own copy of the literal.

`essay_answer_text` reads **both** halves: a paragraph *and* a labelled diagram
are one answer. It used to fall back to the boxes only when there was no
paragraph, so a pupil who wrote an introduction and labelled a graph had the
labels dropped from the marking page and from the AI prompt.

Limits: `execCommand` is still the platform's inline toggle for bold/italic/
underline and colour (the lists and tables are built by the module); the box is
absolutely positioned, so a very wide table scrolls inside it rather than
widening the paper; and jsdom proves the DOM operations, not the paint.

## The graph panel

`app/static/js/exam-graph.js` mounts Desmos in the exam page's graph panel,
behind the **same `allow_calculator` switch** as the scientific calculator — the
teacher decides once whether a paper may use a calculator, and a graph tool is
one. It is not vendored, unlike Snap.js and Inter, and the reason is stated in
the module: Desmos' API authenticates from its own `<script>` URL, has no offline
build, and is licensed as a hosted product. What the page controls is *when* it
is fetched: on the first press, never on page load, so a paper opened on a dead
link still opens and its autosave is unaffected.

**The panel does not need Desmos to draw a graph.** Desmos issues API keys to an
account holder, and its free tier covers school or personal projects rather than
a commercial service, so a deployment without a key had a panel that said so and
nothing else. `app/static/js/exam-graph-local.js` is the second engine: one
expression in `x`, read by a small recursive-descent parser and drawn on **the
Chart.js this app already vendors and every page already carries**, so the tool
costs no extra download on the connection a school actually has. It is dragged
to pan and has its own zoom and reset buttons.

Two things in that module are deliberate rather than incidental:

* **No `eval`.** The formula is typed by a pupil, inside a page that holds their
  answers; it is parsed into closures instead.
* **A curve is never drawn through an asymptote.** A sample far outside the
  window, or one that jumps across most of it with the sign flipped, breaks the
  line the way `tan(x)` and `1/x` need it to be broken.

Which engine runs is a state, not a guess:

* **desmos** — `DESMOS_API_KEY` is set and the script loaded. The key is a
  **public client key**, interpolated into the page, and it is read from
  configuration (`config.DESMOS_API_KEY`) rather than written into the template.
* **local** — there is no key, or desmos.com could not be fetched (offline, a
  captive portal, the 15 s timeout, a key the API refuses). The panel draws the
  curve itself, so a school whose link cannot reach Desmos still sits the paper.
* **unconfigured** — neither engine could start, which means the page's own
  module did not load: a broken deployment, and the panel says exactly that.

The local plot is deliberately smaller than Desmos: no sliders, tables,
regressions or geometry, one curve at a time. That is what an exam graph question
needs, and the limit is written here rather than implied by a panel that looks
richer than it is.

Responsiveness is the panel's: `.sg-graph` is `min(92vw, 620px)` by
`min(70vh, 540px)` (and full width under 640 px), and each module resizes its
calculator from a `ResizeObserver` on the host plus `resize` and
`orientationchange`, because neither engine watches the box it was handed.

## The scientific calculator

The old keypad looked scientific and evaluated four functions: `(` and `)` only
decorated the expression line, `x^y` applied to the last typed number, and
`sin(30)` could not be entered at all. It now holds **one expression** and
evaluates it with a parser (`calcEval`), so brackets change the arithmetic and a
wrong key is backspaced. The key set follows what Cambridge International's
papers expect:

* trig and inverses (sin, cos, tan, sin⁻¹, cos⁻¹, tan⁻¹) in DEG/RAD;
* log, ln, 10^x, e^x, √, ∛;
* x², x³, x^y, x!, 1/x, |x|;
* π, e, nCr, nPr, Ans, %;
* a memory row (MC/MR/M+/M−), brackets, DEL, C/CE.

Implicit multiplication is supported (`2π`, `3(4+1)`), a leading sign is a value
not an error, and a malformed expression evaluates to *nothing shown* rather than
`NaN` on the display. `Ans` is the last `=` result, and memory is `MR` inserted
as a number.

`tests/unit/test_canvas_box_graph.py` lifts `calcEval` out of the template by
brace matching and executes it in node — 2×(3+4)² = 98, sin 30 = 0.5,
nCr(5,2) = 10, 5! = 120 — because a test that restates the grammar proves the
test, not the calculator the pupil presses.
