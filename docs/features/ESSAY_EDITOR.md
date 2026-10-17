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
* **Colour.** A separate "exam focus mode" palette was considered and **not**
  added: the app already carries one token set remapped for dark mode, and a third
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
* The nested-list rules (circle/square/lower-alpha) are supported by the same
  toggle if a pupil indents with Tab in a browser; the toolbar itself does not
  offer an indent button.
