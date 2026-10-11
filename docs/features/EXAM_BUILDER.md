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
| Statement grid (AKM) — `complex_multiple_choice` | `Statement 1`, `Statement 2`, `Statement 3` (the AKM minimum) | the first category, which is the affirmative one in all three presets: **True**, **Yes**, **Matches** |
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

A save that never **arrived** is a different thing from a save the server refused,
and the loop treats it that way. A school connection that drops mid-sentence (or a
5xx, or a body that could not be read back) leaves the draft unjudged rather than
rejected, so it is **queued**: the status line says *waiting*, never *not saved* —
"Gagal menyimpan" is what sends a teacher reloading to chase a paper they still have
— and the body is offered again the moment the browser says the link is back, with a
15-second backstop for the server that restarts without the link ever dropping. Only
a save the server actually ruled on is a failure; only a queued one retries.

The loop is `app/static/js/exam-autosave.js`, driven in node by
`tests/unit/test_exam_autosave.py`. The page supplies the three things only it can
know — how to read the form, how to post it, and where to put the id it is handed
back — and the status words as `sgT` pairs, so the language toggle reaches them too.

The shared top bar in `app/static/js/sg-ux.js` still carries the *network* signal for
a slow save (its own delay keeps a quick one invisible); this status line only adds
which of the four states is true.

## The answer-key page reads the builder's paper

`/teacher/exams/<id>/answer-keys` edits the choice keys for the paper the builder
wrote. Two things it must not do, and both are now guarded:

* **It draws the option letters from the server.** `question_types.CHOICE_OPTIONS`
is the one definition of what an option is; `vocabulary()["options"]` serves it, the
builder reads it as `optionLetters`, and this page receives it as `option_letters`.
A hand-written `['A','B','C','D','E']` on either page is how one of them starts
offering a button the printed sheet has no bubble for. `item_analysis.CHOICE_OPTIONS`
aliases the same tuple rather than listing it again.
* **It knows whether a stored key is still a key.** `question_types.key_state(qtype,
key)` reports `set` (the app can mark the question from it), `empty` (nothing stored,
or this question's own blank shape), or `stale` — something *is* stored that the
question can no longer read. That last one is what a structural edit leaves behind:
the `"B"` a question kept from when it was multiple choice, on the question that has
since become a matching one. The page warns (naming the old value), seeds the editor
empty for that question, and **does not delete the old key** — a page load must never
destroy a mark somebody set on purpose.

A save posts **only the questions the teacher actually touched** (`getJson()` skips
anything not in `dirty`), and the route writes only when the merged key differs from
what is stored. Opening this page and pressing Save is therefore a read, not a
rewrite-and-re-grade of every submission — the load a 1 vCPU box feels most during a
run of papers. `tests/unit/test_answer_key_source.py` pins all of it.

### One answer, or several — and the keys that were written before the choice existed

A question the builder offers as **Pilihan Ganda** is one question, and the name says
only that: whether *one* letter or several are right is a small toggle beneath the type
picker (*Izinkan pengecualian: lebih dari satu jawaban benar untuk soal ini*, off by
default), not a second type a teacher chooses up front. Behind the scenes the toggle
decides whether the question is stored as `mcq` (off) or `mcq_multi` (on) — a detail
neither the teacher nor the pupil has to know.

The picker carries **one** entry per kind, and the exception is deliberately not a
second one: it has no entry of its own in `PICKER_TYPES`, and `mcq_multi` is not a
picker value at all. The type the teacher *picks* therefore never changes with the
toggle, which is what keeps a paper readable — and it is why the entry is named
"Pilihan Ganda" rather than after the single-answer rule the toggle decides.

**The rule the exception must not be confused with.** The PGK type the picker calls
**Tabel Pernyataan (AKM)** (`complex_multiple_choice`) is a different question
entirely: the AKM statement-and-category grid, with its own editor, its own grader and
its own key shape (see `PGK_QUESTION_TYPE.md`). Storing the letters exception as a
`complex_multiple_choice` would show the pupil a grid and hand an unreadable key to
that grader. The two are separate types with separate names in the picker for exactly
that reason — and the entry was renamed off "Pilihan Ganda Kompleks" because that
phrase is also what "more than one correct answer" is called in ordinary use, which
made a second kind of ordinary multiple choice appear to sit beside the real one.

**What the pupil is shown follows the stored type too, and that is a control rather
than a label.** An `mcq` question is a row of bubbles where a tap replaces the last
one; an `mcq_multi` question is the same row drawn as ticks, where a tap adds or removes
a letter and the ticks are stored in the paper's own option order. The page asks the
question's type (`SG_QT.multi`) rather than keeping a flag of its own, because a second
source of truth fails *silently* here: the pupil gets the wrong control and simply
cannot record the answer the question asks for, while the grader — comparing the set of
ticks against the key — marks it wrong. `tests/unit/test_multi_answer_control.py` runs
the page's own `toggleOption` / `isTicked` / `markAnswered` in node and grades what they
record with the app's own grader, so both shapes are observed rather than described.

**Which mark-scheme row the exception is priced in** is decided once, in
`mark_scheme.SCHEME_OF`: it is a multiple-choice question the app marks, so it shares
the `mcq` row — the builder's own table draws it there (`SG_SCHEME_OF`) and the stored
scheme folds it the same way. It is deliberately not a row of its own (that would put
the exception on the paper's face), and it is deliberately not left unfolded either:
the fallback bucket in `type_counts` is the *legacy essay* one, so an unfolded
exception was counted as a written question and priced at the type's *default* marks
rather than the marks the teacher set for multiple choice.

**An answer that arrives in the other shape is read, not refused.**
`question_types.normalize_answer_map` is applied at both doors that store an answer —
`/api/student/sync-draft` on every autosave, and the submit route before anything is
graded or stored — so a page loaded before the teacher flipped the toggle (or a
hand-made request) cannot put a list on a single-answer question, or a bare letter on
the exception. A pupil must not lose a sitting over a control they cannot reach from
their side.

What the exception means for scoring is the part that matters when a key is changed,
and the two types are not interchangeable on the same key:

| stored type | a list key reads as | a pupil ticking one of two |
|---|---|---|
| `mcq` | *any of these* (`value in key`) | **right** |
| `mcq_multi` | *exactly these* (set equality) | **wrong** |

So switching a question to the exception is not a no-op on marks already recorded.
That is why nothing here changes a type or a key silently.

**The answer-key page asks the type, not the stored value.** Its control is a radio
for a `mcq` question and a set of checkboxes for `mcq_multi` (`choice_mode(qtype)`),
and it says which in words above the buttons. It used to be a set of toggles for any
choice question, so clicking `B` after `A` wrote `["A","B"]` — on a question whose
name is *single answer*. That is precisely how the ambiguous keys in the database got
there, and a page that keeps offering it keeps making more. A save now **refuses** a
multi-letter key on a single-answer question, names the question in the flash, and
points at the two honest ways out rather than trimming a letter the teacher did not
ask to drop. The page also warns when a stored key disagrees with its own question,
in both directions: `lost` (a single-answer question whose key marks several, which
cannot be read as the one answer the question now asks for) and `narrow` (the
exception was switched on after a single letter was set, so more may be correct).

**`/teacher/answer-key-review` is where the old data is settled.** It lists the
teacher's own papers (the school's, for an `admin_sekolah`) that hold a `mcq`
question whose key names more than one letter — selected by
`question_types.ambiguous_choice_keys`, the same function the teacher dashboard's
card counts with, so a paper cannot be flagged on one page and invisible on the
other. Neither resolution is taken automatically: the stored key does not say whether
the paper meant *all of these* or *one of them*, and only the owner knows.

For each question the panel prices every resolution **before** it is offered, with
`exam_scoring.key_change_impact` — the same arithmetic the recalculation writes with,
so the number the teacher agrees to is the number the pupil gets:

| resolution | what it writes | what it means |
|---|---|---|
| **Jadikan pengecualian** | `question_types[n] = mcq_multi`, key kept | every stored letter stays correct; a pupil who ticked only some of them now loses the question |
| **Jadikan kunci tunggal** | `answer_key[n] = <one letter>` | one letter stays correct; pupils who ticked another lose it |

Each row shows how many sittings would go **up**, **down**, and **stay**, plus the
largest single move, and the panel states how many already-marked sittings are in
play. Applying a resolution writes the type or the key **and nothing else**:
rewriting marks is a separate tick (*Hitung ulang skor murid*), offered only when
marks would actually move, never taken on the teacher's behalf. Both acts are audit
logged (`resolve_ambiguous_key`, and `recompute` when it is taken), and the route
refuses to apply a resolution to a question another tab has already resolved.

`tests/unit/test_multi_answer_review.py` pins the control, the refusal, the selection
and the two-decisions rule.

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

## The teacher's preview of the pupil's page

Every card on `/teacher/exams` offers **Pratinjau Tampilan Murid**
(`/teacher/exams/<id>/preview`). The page it opens shows the paper in three frames —
**Laptop** (1366×768, one shape), **Tablet** (768×1024, and the same frame flipped to
1024×768) and **HP** (375×812 / 812×375) — because a tablet is not a narrow laptop: it
is the width at which the layout switches to its wide form, and squeezing a layout to
judge it is how a teacher approves something no pupil will ever see. The frame is a
real frame at the real width (`:width="frameW"`, never a scaled image of one), and
the device table is `PREVIEW_DEVICES` in `app/routes/teacher.py`.

The paper inside the frame is the pupil's own template, not a mock-up: same
`student/take_exam.html`, same question types, same media, same navigation. It is the
same page because a mock-up stops being evidence the day the real page changes.

What it must never be is a **sitting**. `/teacher/exams/<id>/preview/paper` renders
with `preview=True`, and that one flag is the whole contract:

* the route calls none of the four things the pupil's door does on the way in — no
  `open_sitting` (so **no attempt row**, and a preview cannot count against
  `max_attempts`), no `exam_target_student` write (nobody is enrolled), no recovery
  code, no `ensure_page_thumbs` — and it writes nothing at all;
* the page starts no watch: no anti-cheat ladder, no tab watch, no liveness ping, no
  countdown, no draft beacon, and the agreement card dismisses without asking for
  fullscreen. The exam bar, the question rail and the media render; the machinery
  behind them does not;
* the clock shows the paper's own duration and **does not run**. A stopped clock has
to say so, or it reads as a fault, so the page carries a banner saying this is a
preview — nothing is saved, nothing is scored, no anti-cheat runs;
* the answer key never reaches the page, for the same reason it never reaches a
  pupil's: the key is stripped and the public option list comes from the one place
  (`public_options`) the pupil's door asks.

The gate is the same one every other teacher door on a paper uses (`_guard_exam` →
`can_manage_exam`): the author, or the school's own admin. `tests/unit/test_exam_preview.py`
drives the door against a supabase that records every call and **renders** the page
rather than grepping it, so "nothing was created" and "nothing was armed" are
asserted against what happened, not what the source looks like.

## Where the code lives

* `app/routes/teacher.py` — `PREVIEW_DEVICES` and the two preview doors
  (`exam_preview`, `exam_preview_paper`).
* `app/templates/teacher/exam_preview.html` — the frames and the device/orientation
  switches.
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
* `app/services/question_types.py` — `CHOICE_OPTIONS`, `vocabulary()` and `key_state()`,
  the one source the builder and the answer-key page share.
* `tests/unit/test_exam_builder_tablet.py` — the guards, and the measurements they
  encode.
* `tests/unit/test_answer_key_source.py` — one option definition, `key_state`, and the
  write-nothing save.
* `tests/unit/test_exam_preview.py` — the preview creates nothing, arms nothing, and
  the frames are the sizes they claim.
