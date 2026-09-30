"""The exam builder on a tablet: a finger, a PDF, and the shape of a new question.

Three defects, all measured on the running page (a demo teacher against
`/teacher/exams/<id>`, driven in headless Chrome with `pointer: coarse`), not read
off the source:

1. **Every control is under the finger floor.** At 820x1180 (iPad Air 4 portrait),
   1024x768, 1112x834 and 768x1024 the builder painted **42 controls under 40px**:
   the per-question type and cognitive-level selects at **30px**, the mark-scheme
   rows at **16px**, the PGK category keys at **30px**, the media toggle at
   **32px**, the AI/Manual pills at **36-38px**. Nothing overflowed
   horizontally at any of those widths — the layout was never the problem; the
   hit areas were. `teacher-content` already tunes this page's type, so the page
   is already a named scope, and the raise belongs with the app's other
   `pointer: coarse` rules because a 768px tablet is a finger.
2. **"Apply to Form" wrote into inputs the form itself owns.** `applyToForm()`
   set `.value` on `input[name="question_types"]`, and that input carries
   `:value="getTypesJson()"` — Alpine re-derives it on the next reactive flush.
   Measured: written `{"0":"complex_multiple_choice","1":"true_false"}`, then one
   "Add Multiple choice" click, and the input read back
   `{"0":"mcq","1":"mcq","2":"mcq","3":"mcq","4":"true_false","5":"mcq"}`. So the
   applied paper was silently discarded, and the page's own step list ("Review the
   detected questions, click Apply to form") promised a review the builder never
   showed.
3. **A new question was an empty grid.** A complex-multiple-choice question seeded
   three blank statements with a blank-looking key, and a matching one seeded an
   empty pair, so the shape of an unfinished question had to be guessed.

What these tests cannot do is lay the page out — this suite has no browser. They
encode the structural rules, and the live measurements above are recorded in
AGENTS.md.
"""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
FORM = ROOT / "app" / "templates" / "teacher" / "exam_form.html"
THEME = ROOT / "app" / "static" / "css" / "theme.css"

#: The scope the stylesheet's finger rule names, carried by the page's own root
#: element. Named rather than inferred from `.teacher-content`, so the rule says
#: which page it is about.
SCOPE = "sg-exam-builder"


COMMENT = re.compile(r"<!--.*?-->", re.S)


def form() -> str:
    return FORM.read_text(encoding="utf-8")


def markup() -> str:
    """The template with its comments blanked out — a comment declares nothing.

    The measured history of this page is written into its own comments, and a
    naive scan reads those as markup: the count of `x-data="pdfUpload()"` was two
    after the duplicate was removed, because the note explaining the removal names
    the attribute.
    """
    return COMMENT.sub(lambda m: "\n" * m.group(0).count("\n"), form())


def method(text: str, name: str) -> str:
    """The body of one method inside the builder's object literal.

    Brace-counted, because every one of these methods contains nested blocks and
    a regex that stopped at the first `}` would read a fragment of one — which is
    how a guard passes while the half of the method it is about is gone.
    """
    m = re.search(r"\n\s*(?:async\s+)??" + re.escape(name) + r"\s*\([^)]*\)\s*\{", text)
    assert m, f"`{name}` is not defined in the exam builder"
    depth, start = 0, m.end() - 1
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    raise AssertionError(f"`{name}` never closes its brace")


def css_rule(css: str, header: str) -> str:
    """The whole rule that begins at `header`, with its braces balanced."""
    start = css.index(header)
    depth = 0
    for i, ch in enumerate(css[start:], start):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return css[start:i + 1]
    raise AssertionError(f"unclosed rule: {header}")


# ── 1. uploading a paper applies it, and the applied paper is the form's own ──


class TestTheParsedPaperIsAppliedByDefault:
    """The teacher uploads a PDF and the builder is already filled in.

    The button existed; the request is that it is not something a teacher has to
    know to press. Making it automatic is only meaningful because the second half
    of this class holds: an application that Alpine then discards is not an
    application.
    """

    def test_uploading_a_pdf_applies_it_without_a_click(self):
        body = method(form(), "uploadPDF")
        assert "applyToForm()" in body, (
            "uploadPDF does not apply the parsed paper, so the teacher still has to "
            "find and press the button")
        # and it must be the *success* path: an upload that failed has nothing
        # to apply, and applying it would write the previous paper's questions
        # over the form.
        assert body.index("applyToForm()") > body.index("d.error"), (
            "applyToForm() is called before the error branch, so a failed upload "
            "still writes its (empty) question set into the form")

    def test_the_page_no_longer_tells_the_teacher_to_press_it(self):
        text = form()
        for instruction in ('Klik “Terapkan ke Formulir”', 'Click “Apply to form”'):
            assert instruction not in text, (
                f"the step list still reads {instruction!r} — the page is describing "
                "a click that is no longer needed")

    def test_the_applied_paper_becomes_the_forms_own_question_list(self):
        """The one writer: the hidden inputs are derived from `questions`."""
        applied = method(form(), "applyToForm")
        assert "'pdf-applied'" in applied or '"pdf-applied"' in applied, (
            "applyToForm() does not hand the parsed paper to the question list, so "
            "the applied types are written into an input Alpine owns and re-derives")
        assert "pdf-applied" in form().split("function questionManager")[1], (
            "nothing in the builder listens for the applied paper, so the event it "
            "is sent is dropped")
        assert "adoptParsed" in form(), (
            "the builder has no method that turns a parsed paper into questions")
        adopt = method(form(), "adoptParsed")
        assert re.search(r"this\.questions\s*=", adopt), (
            "adoptParsed() does not make the parsed paper the form's own list, so the "
            "hidden inputs are still derived from an empty one")
        assert "blankQuestion(" in adopt, (
            "adoptParsed() does not build its questions through blankQuestion(), so an "
            "adopted question arrives without the defaults a new one gets")

    def test_nothing_assigns_a_value_to_an_input_the_form_binds(self):
        """The measured defect: `.value = …` on a `:value`-bound input loses.

        Alpine re-derives the binding on any reactive flush, so the write is not
        a write — it is a value that survives until the teacher's next click.
        """
        text = form()
        bound = re.findall(r'name="([a-z_]+)"[^>]*:value=', text)
        bound += re.findall(r':value="[^"]*"\s*name="([a-z_]+)"', text)
        assert bound, "the form no longer binds any hidden input by name — check this rule"
        offenders = []
        for name in set(bound):
            for m in re.finditer(r"querySelector\(\s*['\"]input\[name=[\"']" + re.escape(name), text):
                tail = text[m.end():m.end() + 400]
                if re.search(r"\.value\s*=", tail):
                    offenders.append(name)
        assert not offenders, (
            f"the template assigns `.value` to {sorted(set(offenders))}, which Alpine "
            "owns via `:value` — the write is re-derived away on the next flush")

    def test_replacing_a_paper_is_reported_and_not_silent(self):
        """The parsed set becomes the list; how much it displaced is said out loud.

        The alternative — keep whichever list is non-empty — has a sharp edge
        that is worse than the replacement: one question added by hand would
        silently drop the forty the PDF had just supplied.
        """
        adopt = method(form(), "adoptParsed")
        assert "replaced" in adopt, (
            "adoptParsed() does not report how many questions it displaced, so a "
            "paper that replaced a built one looks identical to a first upload")
        assert "detail.replaced" in form(), (
            "the report never reaches the badge, so the teacher is not told the list "
            "was replaced")

    def test_the_ai_toggle_actually_reaches_the_upload(self):
        """Measured: with AI mode switched on, the request still carried
        `ai_mode=false`.

        The uploader card was nested inside a *second* `pdfUpload()` card — the
        page's own comment calls the AI banner "outside pdfUpload scope" — so
        `this.$el.parentElement.closest('[x-data]')` resolved the outer uploader,
        whose data has no `aiMode` at all, and `pd = {}` fell back to `false`.
        The endpoint answers `questions: 0` for `ai_mode=false` and `questions: 5`
        for the same paper with `ai_mode=true`, so the AI path detected nothing.
        """
        text = markup()
        scopes = re.findall(r'x-data="pdfUpload\(\)"', text)
        assert len(scopes) == 1, (
            f"{len(scopes)} nested `pdfUpload()` scopes: the inner one's AI flags are "
            "read from the outer one, which does not have any")
        body = method(form(), "uploadPDF")
        assert "parentElement?.closest('[x-data]')" not in body, (
            "the AI mode is still read from the *nearest* `x-data`, which is how a "
            "scope that does not declare `aiMode` answered for one that does")
        assert "aiMode" in body and "aiLang" in body, (
            "the upload no longer reads the AI mode or language at all")

    def test_an_empty_parse_does_not_empty_the_form(self):
        """Measured: manual mode returns no classified questions at all.

        The same one-page paper returned `questions: 0` with `ai_mode=false` and
        `questions: 5` with `ai_mode=true`, from the same markdown — so "nothing
        detected" is the ordinary result of the builder's default mode. Acting on
        it left the edit page with `question_types` = `{}` and no question cards.
        An empty set is not an instruction to delete a paper.
        """
        body = method(form(), "onPdfApplied")
        assert re.search(r"if\s*\(\s*!\s*parsed\.length\s*\)", body), (
            "onPdfApplied() adopts an empty parse, so a manual upload — which "
            "detects nothing by design — wipes the questions the teacher has")
        guard = body[:body.index("adoptParsed")]
        assert "return 0" in guard, (
            "the empty-parse branch does not return before adoptParsed()")

    def test_the_confirmation_says_what_actually_happened(self):
        """An `applied` badge that is always green is not a confirmation."""
        text = form()
        assert "detail.adopted" in text, (
            "applyToForm() never learns how many questions were taken, so its badge "
            "cannot tell '10 applied' from 'nothing was detected'")
        assert "diterapkan otomatis" in text.lower() or "applied automatically" in text.lower(), (
            "the badge does not say the paper was applied automatically")


# ── 2. a new question arrives with a shape, and it is still editable ──────────


class TestTheStatementAndKeyDefaults:
    """``Kalimat 1``… and the affirmative judgement, pre-filled and editable.

    An empty grid is a question a teacher has to discover the shape of, and a
    complex-multiple-choice question whose statements are blank is one the grader
    cannot read at all: `keyFor` keeps statement text unfiltered so the key stays
    aligned, which means an unfilled statement makes the whole key unreadable.
    """

    def test_a_new_complex_multiple_choice_question_comes_with_numbered_statements(self):
        seed = method(form(), "seedPgk")
        assert seed.count("StatementLabel(") >= 3, (
            "seedPgk() still seeds blank statements, so the default statements are "
            "not 'Kalimat 1, Kalimat 2, Kalimat 3'")
        assert not re.search(r"pgkStatements\s*=\s*\[\s*''", seed), (
            "seedPgk() still writes an empty string as a statement")

    def test_each_added_statement_carries_the_next_number(self):
        body = method(form(), "pgkAddStatement")
        assert "push('')" not in body and 'push("")' not in body, (
            "pgkAddStatement() pushes an empty statement, so the fourth statement is "
            "blank where the first three are labelled")
        assert "StatementLabel(" in body, (
            "pgkAddStatement() does not number the statement it adds")

    def test_the_label_is_bilingual_and_one_place_writes_it(self):
        text = form()
        m = re.search(r"function\s+sgStatementLabel\s*\(\s*[a-z]+\s*\)\s*\{([^}]*)\}", text)
        assert m, "there is no single function that writes a statement label"
        pair = re.search(r"[Tt]\(\s*'([^']+)'\s*,\s*'([^']+)'\s*\)", m.group(1))
        assert pair, (
            "the label is not written as a `t('…','…')` / `sgT('…','…')` pair, so one "
            "language gets the other's word")
        assert pair.group(1) != pair.group(2), (
            f"the label is identical in both languages ({pair.group(1)!r}), which the "
            "i18n gate refuses and a reader learns nothing from")
        assert pair.group(1) == "Kalimat", (
            "the Indonesian half is the one the request names: 'Kalimat 1, Kalimat 2, …'")
        assert len(re.findall(r"function\s+sgStatementLabel", text)) == 1, (
            "the label is written in more than one place, so the two can disagree")

    def test_a_new_matching_question_comes_with_a_numbered_pair(self):
        blank = method(form(), "blankQuestion")
        assert re.search(r"kind === 'match'[^;]*newPair\(", blank), (
            "a new matching question seeds an empty pair, so its columns are blank")
        add = method(form(), "addPair")
        assert "newPair(" in add, (
            "addPair() pushes an empty pair, so the second row is blank where the "
            "first is labelled")
        onchange = method(form(), "onTypeChange")
        assert re.search(r"kind === 'match'[^;]*newPair\(", onchange), (
            "re-typing a question as Matching also has to give it the same default pair")
        pair = method(form(), "newPair")
        assert "StatementLabel(" in pair and "MatchLabel(" in pair, (
            "newPair() does not fill both columns — a pair with only a left-hand side "
            "is dropped on save, leaving the question with no key at all")

    def test_a_new_matching_pair_defaults_to_the_match_word(self):
        text = form()
        m = re.search(r"function\s+sgMatchLabel\s*\(\s*[a-z]+\s*\)\s*\{([^}]*)\}", text)
        assert m, "no single function writes the right-hand default of a pair"
        pair = re.search(r"[Tt]\(\s*'([^']+)'\s*,\s*'([^']+)'\s*\)", m.group(1))
        assert pair, "the match word is not a bilingual pair"
        assert "Matches" in pair.group(2), (
            "the right-hand default is not the requested 'Matches'")
        assert re.search(r"\+\s*'\s*'\s*\+|\+\s*n|\+\s*\+\s*[a-z]", m.group(1)), (
            "the match word carries no row number. The pupil's option list is "
            "deduplicated (`question_types.match_options`), so N pairs whose right-hand "
            "side all read the same word collapse into one choice and N-1 of them are "
            "marked wrong")

    def test_a_new_true_false_question_still_defaults_to_true(self):
        blank = method(form(), "blankQuestion")
        assert "q.tf = 'true'" in blank, "a new true/false question has no default key"
        onchange = method(form(), "onTypeChange")
        assert re.search(r"q\.tf\s*=\s*'true'", onchange), (
            "changing a question's type to True/False leaves its key unset")

    def test_the_default_key_is_the_affirmative_category(self):
        """True, Yes or Matches — the first entry of each preset, by construction."""
        seed = method(form(), "seedPgk")
        assert re.search(r"pgkKey\s*=\s*\[0, ?0, ?0\]", seed), (
            "seedPgk() no longer keys every statement to the first category, so the "
            "default judgement is not the affirmative one")
        preset = method(form(), "pgkSetPreset")
        assert re.search(r"return[^;]*\?\s*cur\s*:\s*0", preset) or "cur : 0" in preset, (
            "pgkSetPreset() does not clamp the key to 0, so switching to a narrower "
            "preset can leave the key naming a category that is gone")

    def test_the_defaults_are_still_the_teacher_s_to_edit(self):
        text = form()
        assert re.search(r'x-model="q\.pgkStatements\[si\]"', text), (
            "the seeded statements are not bound inputs any more, so they are not "
            "editable")
        assert re.search(r"x-model=\"p\.l\"", text) and re.search(r'x-model="p\.r"', text), (
            "the seeded matching pair is not two editable inputs")


# ── 3. a finger, not a width ─────────────────────────────────────────────────


class TestTheBuilderControlsReachTheFingerFloor:
    """Measured at 820/1024/1112/768: 42 controls under 40px, none of them an
    overflow. The raise is asked as `pointer: coarse`, like the app's other
    finger rules, because a 768px tablet is as much a finger as a 375px phone."""

    def test_the_page_names_the_scope_the_rule_is_written_against(self):
        text = form()
        m = re.search(r'<div class="([^"]*teacher-content[^"]*)"', text)
        assert m, "the page's root element is gone"
        assert SCOPE in m.group(1), (
            f"the page root does not carry `{SCOPE}`, so the stylesheet's finger rule "
            "names a class nothing uses — the same dead-CSS shape the OMR bench had")

    def test_the_rule_asks_about_the_finger_and_not_the_width(self):
        css = THEME.read_text(encoding="utf-8")
        block = css_rule(css, "@media (pointer: coarse)")
        # There is more than one coarse block in this stylesheet; find the one
        # that names this page's scope.
        assert f".{SCOPE}" in css, (
            f"nothing in theme.css names `.{SCOPE}`, so no finger rule reaches the "
            "builder's controls")
        m = re.search(r"@media\s*\(pointer:\s*coarse\)\s*\{(?:[^{}]|\{[^{}]*\})*?"
                      + re.escape(SCOPE) + r"[^{}]*\{[^}]*\}", css)
        assert m, (
            f"`.{SCOPE}` is not inside a `pointer: coarse` block, so the raise is "
            "either applied to a mouse desktop or to nothing")

    def test_a_width_query_is_not_what_raises_it(self):
        """A width cannot decide it: 768px portrait is a finger, 768px on a mouse
        desktop is not, and `max-width` gets both wrong in the same direction."""
        css = THEME.read_text(encoding="utf-8")
        hits = [m.group(0) for m in re.finditer(
            r"@media\s*\([^)]*width[^)]*\)\s*\{(?:[^{}]|\{[^{}]*\})*?"
            + re.escape(SCOPE), css)]
        assert not hits, (
            f"a width media query raises `.{SCOPE}`: {hits[:2]} — a tablet's finger "
            "would keep 30px selects on a 1180px iPad and a 768px desktop would get "
            "finger-sized controls it does not need")

    def test_the_floor_is_forty_four_pixels(self):
        css = THEME.read_text(encoding="utf-8")
        m = re.search(r"@media\s*\(pointer:\s*coarse\)\s*\{(?:[^{}]|\{[^{}]*\})*?"
                      + re.escape(SCOPE) + r"[^{}]*\{([^}]*)\}", css)
        assert m, f"`.{SCOPE}` has no `pointer: coarse` rule"
        body = m.group(1)
        assert re.search(r"min-height:\s*44px", body), (
            "the finger rule does not raise the height to the app's 44px floor")
        assert re.search(r"min-width:\s*44px", body), (
            "icon-only controls need the floor in the other axis too — the sidebar's "
            "12x24px close button is what that rule was written for")

    def test_every_control_kind_the_page_uses_is_named(self):
        css = THEME.read_text(encoding="utf-8")
        m = re.search(r"(@media\s*\(pointer:\s*coarse\)\s*\{(?:[^{}]|\{[^{}]*\})*?"
                      + re.escape(SCOPE) + r"[^{}]*\{[^}]*\})", css)
        assert m, f"`.{SCOPE}` has no `pointer: coarse` rule"
        rule = m.group(1)
        text = form()
        for kind in ("select", "input", "button"):
            assert kind in rule, (
                f"the rule does not name `{kind}`, and the page is full of them")
        # the input kinds the page actually uses, so the rule cannot be narrower
        # than the page and look complete
        for kind in ("text", "url", "number"):
            if re.search(r'type="' + kind + r'"', text):
                assert f'type="{kind}"' in rule or f"type='{kind}'" in rule, (
                    f"the rule does not raise `input[type={kind}]`, which this page uses")

    def test_the_page_does_not_stop_scrolling_sideways_where_it_did(self):
        """No width measured overflowed; a raise must not create one."""
        text = form()
        assert "overflow-x-hidden" not in text, (
            "an overflow is hidden rather than answered — the measurement showed none, "
            "and hiding one is how the next one goes unnoticed")
