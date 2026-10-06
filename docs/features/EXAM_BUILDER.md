# The exam builder (`/teacher/exams/new`, `/teacher/exams/<id>`)

The page a teacher writes a paper on. Three things about it are easy to get wrong
from the outside, and each one was measured on the running page (a demo teacher in
headless Chrome with touch emulation) rather than read off the source.

## Uploading a PDF applies it

Uploading a paper from the builder's PDF card fills the form in. There is no second
step: the questions the parser detected become the builder's own question list, so
the hidden fields the save route reads — `total_questions`, `question_types`,
`question_pages` — are **derived** from that list.

The button above the question card is *Terapkan ulang dari PDF* ("apply again from
the PDF"), not "apply": it exists so a teacher who edited or deleted the detected
questions can put the parsed set back, and it replaces the list when it does.

What the badge says afterwards is what happened, and it is the half a teacher needs
before saving:

| the parse | the badge |
|---|---|
| detected N questions | `N question(s) applied automatically` |
| ...and the form already had M | `... — M earlier question(s) replaced` |
| detected nothing | `No questions were detected in this PDF — add them by hand` |

Two behaviours are deliberate and load-bearing:

* **An empty parse changes nothing.** Manual mode does not classify questions at
  all — the same one-page paper returns `questions: 0` with `ai_mode=false` and
  `questions: 5` with `ai_mode=true`, from the same markdown — so "nothing detected"
  is the ordinary outcome of the default mode. Treating it as an instruction would
  empty the form.
* **The applied paper survives the next click.** `applyToForm()` used to write
  `.value` straight into `input[name="question_types"]`, which carries
  `:value="getTypesJson()"`, and Alpine re-derived it on the teacher's next
  reactive change. Measured: written `{"0":"complex_multiple_choice","1":"true_false"}`,
  one "Add Multiple choice" click, read back `{"0":"mcq",…,"4":"true_false","5":"mcq"}`.
  Nothing may set `.value` on an input the form binds (guarded in
  `tests/unit/test_exam_builder_tablet.py`).

**The AI toggle is read by key, not by depth.** The AI mode and the language picker
live on the page's own Alpine scope, three levels above the uploader; the uploader
card was nested inside a second `pdfUpload()` card, so `parentElement.closest('[x-data]')`
answered a scope that has no `aiMode` at all and an AI upload posted `ai_mode=false`.
`sgNearestScope(el, key)` walks up until it finds a scope that *declares* the key.

**Not applied:** the AI-generated answer key. Keys decide marks, so they stay the
teacher's to check (the download button and the AI badge are how they get them).

## What a new question arrives with

An empty grid is a question a teacher has to discover the shape of, and for complex
multiple choice a blank statement is worse than ugly: `keyFor` keeps statement text
unfiltered so the key stays aligned with it, which makes one unfilled statement
render the whole key unreadable. Every default below is a value inside an editable
input — `x-model`-bound, overwritten by typing — never an answer.

| type | statements / columns | key |
|---|---|---|
| Complex multiple choice | `Statement 1`, `Statement 2`, `Statement 3` (the AKM minimum) | the first category, which is the affirmative one in all three presets: **True**, **Yes**, **Matches** |
| Matching | left `Statement n`, right `Matches n` | that pair |
| True / False | — (the statement is printed on the paper; the builder stores only the key) | **True** |

Labels are written in the page's language at the moment the question is created
(`Kalimat 1` in Indonesian, `Statement 1` in English), because a statement is content
a pupil reads, not chrome — the same rule the PGK categories follow.

The right-hand column of a matching pair is numbered on purpose: a pupil's option
list is deduplicated (`question_types.match_options`), so three pairs whose right
side all read `Matches` would collapse into one choice and two of the three would be
marked wrong. A pair with only one side filled is dropped on save (`keyFor` filters
on both), so both sides are seeded.

## On a tablet

The page was measured as a demo teacher at 820x1180 (iPad Air 4 portrait), 1180x820,
800x1280 (Galaxy Tab portrait), 1280x800, 1024x768, 1112x834 and 768x1024.

* **Nothing overflowed horizontally** at any of them — the layout was never the
  problem.
* **42 controls were under 40px** at every one of them: the mark-scheme rows at
  16px, the per-question type and cognitive-level selects at 30px, the PGK category
  keys at 30px, the media toggle at 32px, the Manual/AI pills at 36-38px.

`app/static/css/theme.css` raises every control inside `.sg-exam-builder` — a class
on the page's own root element — to 44x44 under `@media (pointer: coarse)`. Measured
after: **42 -> 0** at all four tablet widths, with no overflow, and the rule does
*not* apply to a mouse desktop (measured with touch emulation off: 699x36 inputs).

`pointer: coarse` rather than a width, for the reason the app's other finger rules
give: a 768px tablet is as much a finger as a 375px phone, while a 768px window on a
mouse desktop is not — and `max-width` is wrong about both in the same direction.

## The two clocks

The form collects a **Durasi** ("counted from the moment *this* student starts")
and an **Assignment window end** ("the last instant a student may *begin*"). They
are different clocks, and nothing tied them together, so a 60-minute paper was
routinely given a two-day window — and the pupil who began at the far end of it met
the window's own deadline a few minutes into a paper they were entitled to sit for
the full hour.

With **Ikuti durasi otomatis** ticked (the default) the window end is
`start + duration`: the paper stays open just long enough for one full sitting. It is
a default and not a rule — untick it and the field is the teacher's again, which is
what a school rotating five classes through one afternoon needs. Two rules the
arithmetic obeys, both load-bearing:

* **`0` is Unlimited, not zero minutes.** A stored 0 already means "no limit"
  everywhere else, so the field is left alone rather than set to the start.
* **A saved window is never rewritten by a page load.** Only an input the teacher
  actually touches recomputes it; a *new* paper has no window yet, so its one pass
  fills the field rather than changing one.

**Auto-submit saat jendela berakhir** now opens **ticked for a new paper**. Off was
the safe-sounding choice and the wrong one: a paper whose window ended with a sitting
still running left that sitting open, and the teacher had to know to tick a box they
were never shown a reason for. An existing paper keeps whatever it stored.

## The draft saves itself

Nothing on this page used to be kept until **Publish & Send to Classes** was pressed.
A closed tab, a shut lid, or a school connection that dropped for a minute took the
whole paper — title, classes, every question, the marks — with it. The form is now
written by itself a couple of seconds after the teacher stops changing it, and the
status line beside the Publish button says which of the three things is true:
*Menyimpan…* / *Tersimpan otomatis* / *Gagal menyimpan*.

Three rules make the loop safe, and each one is a decision rather than a detail:

* **It never navigates.** The save is a `fetch` answered with JSON, so the teacher
  keeps typing in the same page. Both save doors know the difference
  (`_exam_save_refused` in `app/routes/teacher.py`): a browser post gets the flash and
  the redirect it always had, an autosave gets a sentence and a `400`.
* **It writes nothing that did not change.** The snapshot is the whole body minus the
  two fields the loop owns (`action`, `draft_id`), so opening the page, clicking
  around it, or letting Alpine touch a hidden input does not write. On a 1 vCPU box a
  background loop that wrote on every click would be the problem it was meant to
  solve.
* **It mints the draft once.** The create page has no `exam_id` until its first save;
  that save inserts the row, returns the id, and the page adopts it — rewriting the
  form action to `/teacher/exams/<id>`. Without that rewrite the explicit **Publish**
  would post to `/exams/new` a second time and the school would end up with two
  papers for one exam. `_owned_draft` guards the other end: an id that names another
  school's, another teacher's, or an already-published paper is ignored and a fresh
  draft is minted instead of writing somebody else's.

What an autosave deliberately does **not** do is everything else an explicit save
reaches: no per-pupil roster sync, no PDF re-upload, no weight-gap flash, no publish
side effects, no `log_activity("create")` for a draft that already exists. A PDF is
excluded from the body too — the builder uploads it once through its own AJAX path,
and re-sending the bytes every few seconds is not a draft write.

A refusal is shown and *not* retried on its own: a body the server keeps refusing
(the window end before its start, no questions at all, a window outside the running
assessment period) must not become a request every few seconds. The teacher's next
change tries again.

The loop is `app/static/js/exam-autosave.js`, driven in node by
`tests/unit/test_exam_autosave.py`. The page supplies the three things only it can
know — how to read the form, how to post it, and where to put the id it is handed
back — and the status words as `sgT` pairs, so the language toggle reaches them too.

The shared top bar in `app/static/js/sg-ux.js` still carries the *network* signal for
a slow save (its own delay keeps a quick one invisible); this status line only adds
which of the three states is true.

## Reading size on the pupil's page

The exam page offers **A- / A+** and a magnifier on the paper image. Neither is the
browser's zoom and neither changes the viewport: the scale is one CSS custom
property (`--sg-exam-scale`, read by `.sg-exam-zoom`) and the magnified diagram is an
overlay inside the page. That restraint is the point — the anti-cheat ladder watches
`resize`, `fullscreenchange` and `visibilitychange`, and a zoom that produced one
would charge a pupil for enlarging the text.

Pinch-zoom is left working on top of it (WCAG 1.4.4 / 1.4.10 forbid switching it
off). The chosen size is stored as the `text_scale` preference, so it follows the
pupil to their next device. See `docs/DESIGN_SYSTEM.md`.

## Where the code lives

* `app/templates/teacher/exam_form.html` — the whole page. The PDF uploader
  (`pdfUpload()`), the question list (`questionManager()`), the defaults
  (`sgStatementLabel` / `sgMatchLabel` / `seedPgk` / `newPair`), and the scope walk
  (`sgNearestScope`).
* `app/static/css/theme.css` — the `pointer: coarse` block for `.sg-exam-builder`.
* `app/static/js/exam-window.js` — the duration-to-window arithmetic, run in node by
  `tests/unit/test_exam_window_auto.py`.
* `app/static/js/exam-autosave.js` — the debounced draft save, run in node by
  `tests/unit/test_exam_autosave.py`.
* `app/static/js/exam-view.js` — the pupil page's reading size and lightbox, run in
  node by `tests/unit/test_exam_view_zoom.py`.
* `tests/unit/test_exam_builder_tablet.py` — the guards, and the measurements they
  encode.
