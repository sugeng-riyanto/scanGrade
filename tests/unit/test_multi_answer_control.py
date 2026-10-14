"""One picker entry, one toggle, and the four places the answer has to agree.

The teacher's side of this feature was built first, and it is complete: one
"Pilihan Ganda" entry in the picker, a per-question toggle under it, a key area that
turns from radio to checkbox live, and a `mcq_multi` type written instead of a second
picker entry. What a page *offers* is not what a pupil *gets*, and the halves that
did not exist are the ones this file guards:

1. **The pupil's control.** The exam page classified a question by *kind* — and both
   names are kind `choice` — so a question stored as `mcq_multi` was drawn with the
   single-answer row of bubbles, where each tap replaces the last. The pupil could
   never record the second tick the question asks for, and the grader (which compares
   the set of ticks against the key) marked it wrong. Nothing raised, nothing logged:
   the paper was simply unanswerable. The control is now switched on the *stored
   type*, and this file runs the page's own `toggleOption` / `isTicked` / `qIsMulti`
   in node and grades what they record with the server's own grader.

2. **The marks a paper is scored by.** `mark_scheme.SCHEME_ORDER` has one row per
   type and no row for the exception, and both readers of it folded an unknown name
   into the *legacy-essay* bucket: `type_counts` counted a multi-answer choice
   question as an essay, and `build_weights` priced it at the multiple-choice
   *default* rather than the marks the teacher set for multiple choice. Folding it
   into the multiple-choice row (`SCHEME_OF`) is the fix, and the same fold the
   builder's own table already draws.

3. **The shape an answer is allowed to hold.** `normalize_answer` was written with
   the feature and had **no caller at all**, so the rule it states — one letter for a
   single-answer question, the set for the exception, the other shape *read* rather
   than refused — was documentation. Both writers (the autosave door and the submit
   door) go through it now.

4. **The vocabulary.** The picker offers one entry for letters, the exception is not
   a second one, and the AKM grid keeps a name of its own so the two can never be
   confused for each other.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

from app.services import mark_scheme
from app.services import question_types as qt

ROOT = Path(__file__).resolve().parents[2]
EXAM_PAGE = ROOT / "app" / "templates" / "student" / "take_exam.html"
FORM = ROOT / "app" / "templates" / "teacher" / "exam_form.html"
API = ROOT / "app" / "routes" / "api.py"
STUDENT = ROOT / "app" / "routes" / "student.py"

NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="needs node to run the page's own JS")


# ── the pieces taken out of the page, by their real source ────────────────────

def _page() -> str:
    return EXAM_PAGE.read_text(encoding="utf-8")


def _one(pattern: str, what: str) -> str:
    match = re.search(pattern, _page(), re.S)
    assert match, f"could not find {what} in the exam page — renamed or removed?"
    return match.group(0)


def _word_free_cue() -> str:
    """The cue's own element, as the page writes it — tag, classes and content."""
    match = re.search(r'<span x-show="multiHintOn\(i\)" x-cloak.*?</span>', _page(), re.S)
    assert match, (
        "the word-free cue is gone from the exam page — a pupil who cannot read the "
        "badge has nothing left telling them the question can hold more than one. The "
        "`x-cloak` is part of the element on purpose: this cue starts hidden, and "
        "without it a question answering one letter shows a cue about several until "
        "Alpine takes over — the page's own convention for every block it draws "
        "conditionally.")
    return match.group(0)


#: The client's kind map, so `sgKind` is the page's own answer rather than a guess.
SG_KIND = r"function sgKind\(type\) \{[^\n]*\}"
#: One branch of the paper's choice renderer, read as a region rather than as a
#: whole-file search — a `fa-square` anywhere else must not satisfy a rule about
#: this branch. Each runs to the comment that introduces the next branch, which is
#: the marker that survives a change inside either one.
#: The plain row's branch. `!qIsMulti(i)` is load-bearing rather than decoration:
#: `qKind` is 'choice' for the exception as well, so without it both this branch and
#: the one below match an exception question and the pupil is shown the question
#: **twice** — a row that replaces a letter above a row that adds one. That shipped,
#: and was measured in a real Chrome on the deployed page by
#: `tests/unit/test_exam_render_gate.py`, which counts the rows the page laid out;
#: this is the same fact read cheaply.
SINGLE_BRANCH = r"<template x-if=\"qKind\(i\) === 'choice' && !qIsMulti\(i\)\">(.*?)<!-- The exception:"
MULTI_BRANCH = r"<template x-if=\"qKind\(i\) === 'choice' && qIsMulti\(i\)\">(.*?)<!-- True / False"
#: The three methods the tick control is made of, and the two they lean on.
Q_IS_MULTI = r"qIsMulti\(i\) \{[^\n]*\}"
IS_TICKED = r"isTicked\(i, opt\) \{.*?\n        \}(?=,)"
#: The word-free cue's own reader, and the trigger inside `toggleOption`.
MULTI_HINT_ON = r"multiHintOn\(i\) \{.*?\n        \}(?=,)"
TOGGLE_OPTION = r"toggleOption\(i, opt\) \{.*?\n        \}(?=,)"
SET_STRUCTURED = r"_setStructured\(i, value, filled\) \{.*?\n        \}(?=,)"
MARK_ANSWERED = r"markAnswered\(i, val, overwrite\) \{.*?\n        \}(?=,)"


def _node(script: str) -> dict:
    """Run a script in node and read back the JSON it prints."""
    proc = subprocess.run(
        [NODE, "-e", script], capture_output=True, text=True, timeout=60,
        cwd=str(ROOT), check=False,
    )
    assert proc.returncode == 0, f"node failed:\n{proc.stdout}\n{proc.stderr}"
    return json.loads(proc.stdout.strip().splitlines()[-1])


@needs_node
def _observe() -> dict:
    """Every scenario, driven by the template's own methods."""
    methods = ",\n".join([
        _one(Q_IS_MULTI, "the qIsMulti method"),
        _one(IS_TICKED, "the isTicked method"),
        _one(MULTI_HINT_ON, "the multiHintOn method"),
        _one(TOGGLE_OPTION, "the toggleOption method"),
        _one(SET_STRUCTURED, "the _setStructured method"),
        _one(MARK_ANSWERED, "the markAnswered method"),
    ])
    script = "\n".join([
        f"const SG_QT = {json.dumps(qt.vocabulary())};",
        _one(SG_KIND, "the sgKind helper"),
        "function make(questions) {",
        "  return { questions: questions, answered: {}, _answerTimestamps: {},",
        "           multiHinted: {},",
        "           markDirty() {}, _ev() {},",
        methods,
        "  };",
        "}",
        textwrap.dedent("""
        const S = {};
        // 1. the single-answer row is still a radio: a tap replaces the last letter
        let a = make([{type: 'mcq', origIdx: 0, answer: null}]);
        a.markAnswered(0, 'A'); a.markAnswered(0, 'C');
        S.single_answer = a.questions[0].answer;
        S.single_answered = !!a.answered[0];

        // 2. the exception accumulates, in the paper's own option order
        let b = make([{type: 'mcq_multi', origIdx: 0, answer: null}]);
        b.toggleOption(0, 'C'); b.toggleOption(0, 'A'); b.toggleOption(0, 'B');
        S.multi_ticks = b.questions[0].answer;
        S.multi_answered = !!b.answered[0];
        S.multi_is_multi = b.qIsMulti(0);
        S.multi_ticked_flags = [b.isTicked(0, 'B'), b.isTicked(0, 'E')];

        // 3. tapping a tick again removes it
        b.toggleOption(0, 'A');
        S.after_untick = b.questions[0].answer;

        // 4. unticking the last tick is *no answer*, not an empty set
        let c = make([{type: 'mcq_multi', origIdx: 0, answer: null}]);
        c.toggleOption(0, 'A'); c.toggleOption(0, 'A');
        S.emptied_answer = c.questions[0].answer === null ? 'null'
            : JSON.stringify(c.questions[0].answer);
        S.emptied_answered = !!c.answered[0];

        // 5. a draft written while the question was a single-answer one reads as
        //    one tick, and a new tick is *added* to it rather than replacing it
        let d = make([{type: 'mcq_multi', origIdx: 0, answer: 'B'}]);
        S.narrow_is_ticked = d.isTicked(0, 'B');
        d.toggleOption(0, 'D');
        S.narrow_after_add = d.questions[0].answer;

        // 6. the `true` a drawing overlay writes must not wipe the letters
        let e = make([{type: 'mcq_multi', origIdx: 0, answer: null}]);
        e.toggleOption(0, 'A'); e.toggleOption(0, 'C'); e.markAnswered(0, true);
        S.drawing_keeps_ticks = e.questions[0].answer;
        let f = make([{type: 'mcq', origIdx: 0, answer: null}]);
        f.markAnswered(0, 'B'); f.markAnswered(0, true);
        S.drawing_keeps_letter = f.questions[0].answer;

        // 7. the two controls are told apart by the stored type, never by a flag
        S.mcq_is_not_multi = a.qIsMulti(0);

        // 8. the word-free cue is *earned*: it appears at the second tick and not
        //    before, and it stays for the rest of the question afterwards
        let g = make([{type: 'mcq_multi', origIdx: 0, answer: null}]);
        S.hint_at_none = g.multiHintOn(0);
        g.toggleOption(0, 'A');
        S.hint_at_one_tick = g.multiHintOn(0);
        g.toggleOption(0, 'C');
        S.hint_at_two_ticks = g.multiHintOn(0);
        g.toggleOption(0, 'C');
        S.hint_after_a_tick_was_taken_back = g.multiHintOn(0);
        S.hint_question_kept_its_other_tick = g.isTicked(0, 'A');

        // 9. a draft the server restored with two ticks already in it shows the cue
        //    without waiting for another tap
        let h = make([{type: 'mcq_multi', origIdx: 0, answer: ['B','D']}]);
        S.hint_on_a_restored_draft = h.multiHintOn(0);

        // 10. and a single-answer question never wears it, however many taps it
        //     took to end up with one letter
        let j = make([{type: 'mcq', origIdx: 0, answer: null}]);
        j.markAnswered(0, 'A'); j.markAnswered(0, 'D');
        S.hint_on_a_single_answer_question = j.multiHintOn(0);

        console.log(JSON.stringify(S));
        """),
    ])
    return _node(script)


@pytest.fixture(scope="module")
def seen() -> dict:
    return _observe()


# ── 1. the pupil's control ───────────────────────────────────────────────────

class TestThePupilGetsTheControlTheQuestionAsksFor:
    def test_a_single_answer_question_still_replaces_the_letter(self, seen):
        """The pre-existing radio rule, kept — this is a regression guard."""
        assert seen["single_answer"] == "C", (
            "a single-answer question kept more than the last letter tapped")

    def test_the_exception_takes_two_ticks(self, seen):
        assert seen["multi_ticks"] == ["A", "B", "C"], (
            "ticking two options on a multi-answer question did not record both")

    def test_the_ticks_are_stored_in_the_papers_own_order(self, seen):
        """Tapped C, A, B — stored A, B, C, which is the order every reader uses."""
        assert seen["multi_ticks"] == ["A", "B", "C"]

    def test_a_tick_tapped_again_is_removed(self, seen):
        assert seen["after_untick"] == ["B", "C"]

    def test_unticking_the_last_tick_is_no_answer_not_an_empty_set(self, seen):
        assert seen["emptied_answer"] == "null", (
            "an empty list would travel as a truthy value and read as answered")
        assert seen["emptied_answered"] is False, (
            "the question is still flagged answered after the last tick went")

    def test_a_draft_that_names_one_letter_reads_as_one_tick(self, seen):
        """A key set before the toggle was switched on is a valid set of one."""
        assert seen["narrow_is_ticked"] is True
        assert seen["narrow_after_add"] == ["B", "D"], (
            "a second tick replaced the letter instead of joining it")

    def test_a_drawing_does_not_wipe_the_ticks(self, seen):
        assert seen["drawing_keeps_ticks"] == ["A", "C"], (
            "the drawing overlay's `true` replaced the pupil's ticks")

    def test_a_drawing_still_does_not_wipe_a_single_letter(self, seen):
        assert seen["drawing_keeps_letter"] == "B"

    def test_the_control_is_chosen_by_the_stored_type(self, seen):
        assert seen["mcq_is_not_multi"] is False, "an mcq was drawn as a tick control"
        assert seen["multi_is_multi"] is True, (
            "an mcq_multi question was not recognised as the exception")
        assert seen["multi_ticked_flags"] == [True, False]


class TestTheControlIsWiredToTheTypeNotToAFlag:
    def test_the_page_asks_the_stored_type(self):
        page = _page()
        assert "qIsMulti(i) { return ((this.questions[i] || {}).type) === SG_QT.multi; }" \
            in page, "the tick control no longer asks the stored type"
        assert "SG_QT.multi" in page, (
            "the exception's name is spelled out in the page instead of coming from "
            "the server's own vocabulary")

    def test_the_tick_row_is_its_own_branch_beside_the_radio_row(self):
        page = _page()
        assert '<template x-if="qKind(i) === \'choice\' && qIsMulti(i)">' in page, (
            "there is no branch for the exception, so a multi-answer question is "
            "drawn by whichever branch comes first")
        assert '<template x-if="qKind(i) === \'choice\' && !qIsMulti(i)">' in page, (
            "the plain row's branch does not exclude the exception, so an exception "
            "question matches both branches and is drawn twice — one question, two "
            "answer rows, which is what the deployed page did")
        assert "@click=\"toggleOption(i, opt)\"" in page
        assert "@click=\"markAnswered(i, opt)\"" in page, (
            "the single-answer row no longer records a tap the way it always did")

    def test_the_tick_control_is_told_apart_by_its_behaviour_not_its_looks(self):
        """The contract **changed on purpose**, and this is the rewrite of the
        guard that encoded the opposite one.

        The exception used to draw a checkbox affordance (`fa-square` /
        `fa-square-check`) and a helper line. A pupil answered a question that
        looked and read as a second kind of question, when what the teacher asked
        for was *the same single-choice row, able to hold more than one letter*.
        The row is deliberately identical now — the same control, the same letter,
        no icon and no helper paragraph — so the cue that has to survive is the
        accessible one, not a drawing. These assertions are the *stricter* half of
        that swap: they fail if a checkbox affordance comes back, if the guidance
        paragraph comes back, or if the marker stops being the exception's alone.
        """
        branch = _one(MULTI_BRANCH, "the exception's branch")
        single = _one(SINGLE_BRANCH, "the single-answer branch")
        # No checkbox affordance, and no second kind of button: the letter is the
        # whole content of the control.
        assert "fa-square" not in branch, (
            "the exception draws a checkbox affordance again — the row was meant to "
            "stop looking like a second kind of question")
        assert '<i class="fas mr-1"' not in branch, (
            "an icon rides inside the option button, so the row no longer reads as "
            "the single-choice row above it")
        # The helper paragraph is gone: parity is the point.
        assert "<p " not in branch, (
            "a paragraph under the row is the visual difference the change removed")
        # The cue that survives, and it belongs to this branch alone.
        assert ':aria-pressed="isTicked(i, opt)' in branch, (
            "nothing tells a screen reader this control can hold more than one "
            "letter, and the row looks the same so nothing else can")
        assert "aria-pressed" not in single, (
            "the single-answer row claims to be a toggle too, so the marker no "
            "longer distinguishes the exception")

    def test_the_rule_is_named_by_a_badge_not_by_a_second_control(self):
        """The cue that replaced the checkbox glyph, and how it is allowed to look.

        A pupil has to be able to *see* that this question takes several letters —
        the row looking the same is the point, not the reason to say nothing. The
        cue is a badge beside the question number that borrows the true/false
        badge's classes verbatim: one place in the page to learn "this question
        works differently", and no way for the two to drift. It must stay a
        *label* — a `<span>`, never a button — or it becomes the second control the
        change removed.
        """
        page = _page()
        branch = _one(MULTI_BRANCH, "the exception's branch")
        single = _one(SINGLE_BRANCH, "the single-answer branch")
        BADGE = ('class="inline-flex items-center gap-1 text-[10px] font-bold '
                 'text-primary-600 bg-primary-50 px-2 py-0.5 rounded-full border '
                 'border-primary-200"')
        assert BADGE in branch, (
            "nothing tells the pupil this question allows more than one letter, "
            "and the row is deliberately identical to a single-choice one")
        assert page.count(BADGE) == 2, (
            "the exception's badge and the true/false badge are meant to be the same "
            "class string, so one of them was restyled and the two can now drift")
        assert BADGE not in single, (
            "the single-answer row wears the badge too, so it no longer marks the "
            "exception")
        assert 't(\'Boleh lebih dari satu\',\'More than one allowed\')' in branch, (
            "the badge is a bilingual pair like every other label on this page")
        badge = re.search(r'<span ' + re.escape(BADGE) + r'[^>]*>', branch)
        assert badge and '<button' not in badge.group(0), (
            "the cue became a control of its own rather than a label beside one")

    def test_both_rows_are_the_one_shared_option_control(self):
        """Fase 5, and the parity half of the change: the same 44px control, twice."""
        page = _page()
        assert ".opt-btn { min-width: 44px; min-height: 44px; }" in page
        assert page.count(
            'class="opt-btn px-5 py-2 border-2 text-sm font-extrabold cursor-pointer"') == 2, (
            "the exception and the single-answer row no longer share one control, "
            "which is how their tap targets drift apart")
        single = _one(SINGLE_BRANCH, "the single-answer branch")
        branch = _one(MULTI_BRANCH, "the exception's branch")
        assert '<span x-text="opt"></span>' in single and \
            '<span x-text="opt"></span>' in branch, (
            "the two rows no longer paint the option letter the same way")


# ── 1b. the word-free half of the same cue ──────────────────────────────────


class TestThePupilIsShownMoreThanOneWithoutBeingTold:
    """The badge says the rule in words; this says it without any.

    A pupil who cannot read the badge — a young one, one who reads slowly, one whose
    first language is neither of the page's — still has to be able to *see* that this
    question takes several letters, and a badge is a sentence. The cue is a double
    tick, drawn beside it, and it is **earned**: a single-answer question is what one
    tap means, so it appears only once a second letter has actually been ticked, and
    then stays for that question (it names the rule, not the count, so taking a tick
    back does not make it a lie).

    Being wordless is not only about the pupil: a cue with no words on it adds no
    bilingual pair and no leftover, so the page's own coverage floor does not move
    for it — the only reason a release would have to re-record
    `deploy/i18n_baseline.json` is a *word*, and this one deliberately has none.

    The half that is easy to get wrong is where it sits. It has to sit **outside**
    the option row: the row is measured button-by-button against the single-answer
    row by the deploy's render gate (`deploy/exam_render_gate.py`), so a cue drawn
    among the buttons would either move a pupil's control or put an `<i>` inside the
    button the row's parity is claimed of. It is a label, never a control.
    """

    # ── what the pupil sees, driven by the page's own methods ────────────────

    def test_it_appears_at_the_second_tick_and_never_at_the_first(self, seen):
        assert seen["hint_at_none"] is False
        assert seen["hint_at_one_tick"] is False, (
            "the cue was on screen after one tap — one tap is what a single-answer "
            "question expects, so a hint there is wallpaper, not a hint")
        assert seen["hint_at_two_ticks"] is True, (
            "a second tick landed and the pupil was told nothing wordless about it")

    def test_it_stays_after_a_tick_is_taken_back(self, seen):
        """Persistent means the rule, not the count: one letter left, and the cue is
        still true — the question does still take more than one."""
        assert seen["hint_after_a_tick_was_taken_back"] is True, (
            "the cue blinked out the moment the count dropped to one, which makes it "
            "a badge for the number rather than for the rule")
        assert seen["hint_question_kept_its_other_tick"] is True

    def test_a_restored_draft_with_two_ticks_already_shows_it(self, seen):
        """The reading is of the answer, not of this sitting's taps: a paper resumed
        mid-question must not look like a question that takes one letter."""
        assert seen["hint_on_a_restored_draft"] is True

    def test_a_single_answer_question_never_wears_it(self, seen):
        assert seen["hint_on_a_single_answer_question"] is False, (
            "a question whose data is one letter wore a cue about several")

    # ── the wiring, and the two mistakes this change can make ────────────────

    def test_the_flag_is_set_in_the_tick_control_and_nowhere_else(self):
        page = _page()
        assert page.count("multiHinted[i] = true;") == 1, (
            "the cue's flag has more than one writer, so something other than a "
            "second tick can raise it")
        assert "multiHinted[i] = true;" in \
            _one(TOGGLE_OPTION, "the toggleOption method"), (
            "the flag is set outside the tick control — the plain row writes through "
            "the same funnel, so a single-answer question can reach it")

    def test_the_cue_says_nothing_in_any_language(self):
        marker = _word_free_cue()
        assert re.search(r"[A-Za-z0-9]", re.sub(r"<[^>]+>", "", marker)) is None, (
            "the cue carries a letter or a digit, so it is copy: the pupil has to be "
            "able to read it and the language toggle owes it a translation")
        assert "x-text" not in marker and "t(" not in marker, (
            "the cue is rendered from a string rather than drawn")
        assert 'aria-hidden="true"' in marker, (
            "the cue announces itself to a screen reader, which already has the rule "
            "through `aria-pressed` on the buttons — twice-said is not more accessible")

    def test_it_is_drawn_by_the_exception_and_by_nothing_else(self):
        page = _page()
        branch = _one(MULTI_BRANCH, "the exception's branch")
        single = _one(SINGLE_BRANCH, "the single-answer branch")
        assert 'x-show="multiHintOn(i)"' in branch, (
            "the exception's row is the one place the cue belongs and it is not there")
        assert "multiHintOn" not in single, (
            "the single-answer row shows a cue about holding more than one answer")
        assert page.count('x-show="multiHintOn(i)"') == 1, (
            "the cue is drawn in more than one place, so it no longer marks the "
            "exception")

    def test_it_sits_outside_the_row_whose_parity_it_may_not_move(self):
        branch = _one(MULTI_BRANCH, "the exception's branch")
        # The *element*, not its name: the comment above it explains the rule and
        # names the reader, so a search for the name would be satisfied by prose and
        # would pass with the cue sitting **inside** the row it may not disturb.
        cue = _word_free_cue()
        assert cue in branch, "the cue is drawn outside the exception's own block"
        assert branch.index(cue) < branch.index('class="opt-row'), (
            "the cue is drawn inside the option row — the deploy's render gate measures "
            "that row button-by-button against the single-answer row, so the cue "
            "either moves a pupil's control or becomes a difference it finds")
        button = re.search(r'<button [^>]*toggleOption.*?</button>', branch, re.S)
        assert button and "<i" not in button.group(0), (
            "an element rides inside the option button, so the row the pupil taps is no "
            "longer the plain row's — the cue is the only thing allowed to say the "
            "rule, and it may not be a second control")


# ── 2. the marks the paper is scored by ──────────────────────────────────────

class TestTheRowTheExceptionIsPricedIn:
    def test_it_is_priced_in_the_multiple_choice_row(self):
        assert mark_scheme.scheme_type(qt.MCQ_MULTI) == qt.MCQ

    def test_it_is_not_counted_as_an_essay(self):
        """The bug: `type_counts` folded an unknown name into the essay bucket."""
        counts = mark_scheme.type_counts({"0": qt.MCQ_MULTI}, 1)
        assert counts[qt.MCQ] == 1, "a multi-answer question was not counted as a choice"
        assert counts[qt.ESSAY_CANVAS] == 0, (
            "a multi-answer choice question was counted as an essay")

    def test_it_earns_the_marks_the_teacher_set_for_multiple_choice(self):
        """The second half: the exception was priced at the *default*, not the scheme."""
        types = {"0": qt.MCQ_MULTI, "1": qt.MCQ}
        weights = mark_scheme.build_weights(types, 2, {"mcq": 3})
        assert weights["0"] == pytest.approx(50.0)
        assert weights["1"] == pytest.approx(50.0), (
            "the exception question was priced differently from the mcq beside it")

    def test_the_table_shows_one_row_and_totals_the_paper(self):
        table = mark_scheme.describe({"0": qt.MCQ_MULTI, "1": qt.MCQ}, 2)
        rows = {r["type"]: r for r in table["rows"]}
        assert qt.MCQ in rows and rows[qt.MCQ]["count"] == 2, (
            "the scheme table does not show the exception in the choice row")
        assert qt.ESSAY_CANVAS not in rows, "the table grew an essay row for it"
        assert sum(r["scaled_subtotal"] for r in table["rows"]) == pytest.approx(100.0)

    def test_the_builder_folds_it_the_same_way(self):
        """The page's table and the stored scheme must agree, or the total moves."""
        form = FORM.read_text(encoding="utf-8")
        assert "SG_SCHEME_OF[SG_QT.multi] = SG_QT.default;" in form, (
            "the builder no longer folds the exception into the choice row, so the "
            "table a teacher sees and the scheme the server stores disagree")

    def test_no_row_of_its_own_exists(self):
        assert qt.MCQ_MULTI not in mark_scheme.SCHEME_ORDER, (
            "the exception has a row of its own, which puts it on the paper's face")


# ── 3. the shape an answer is allowed to hold ────────────────────────────────

class TestTheShapeAnAnswerMayHold:
    def test_a_list_on_a_single_answer_question_narrows_to_one_letter(self):
        assert qt.normalize_answer_map({"0": qt.MCQ}, {"0": ["C", "A"]})["0"] == "A", (
            "the paper's own order decides which letter a single-answer question keeps")

    def test_the_exception_keeps_the_set_in_the_papers_order(self):
        got = qt.normalize_answer_map({"0": qt.MCQ_MULTI}, {"0": ["C", "A"]})["0"]
        assert got == ["A", "C"]

    def test_one_letter_on_the_exception_is_still_a_set_of_one(self):
        assert qt.normalize_answer_map({"0": qt.MCQ_MULTI}, {"0": "B"})["0"] == ["B"]

    def test_the_bookkeeping_keys_are_left_alone(self):
        answers = {"0": "A", "_device_info": {"ip_address": "10.0.0.1"},
                   "_timestamps": {"0": 123}, "_rev": 4, "_flags": []}
        got = qt.normalize_answer_map({"0": qt.MCQ}, answers)
        assert got["_device_info"] == answers["_device_info"]
        assert got["_timestamps"] == {"0": 123}
        assert got["_rev"] == 4

    def test_a_question_the_page_knows_nothing_about_is_left_alone(self):
        answers = {"0": {"anything": True}}
        assert qt.normalize_answer_map({"0": "some_future_type"}, answers) == answers

    def test_a_non_mapping_passes_through(self):
        assert qt.normalize_answer_map({"0": qt.MCQ}, ["A"]) == ["A"]

    def test_it_is_the_one_rule_and_not_a_second_copy(self):
        """The map must not restate `normalize_answer`; it must call it."""
        import inspect

        source = inspect.getsource(qt.normalize_answer_map)
        assert "normalize_answer(" in source, (
            "a second copy of the rule is how the two shapes drift apart")
        body = source.split('"""')[2]
        assert "answer_letters" not in body, "the rule was re-implemented here"

    def test_both_writers_go_through_it(self):
        """Written and never called is what this was — both doors call it now."""
        api = API.read_text(encoding="utf-8")
        student = STUDENT.read_text(encoding="utf-8")
        assert "answers = normalize_answer_map(" in api, (
            "the autosave door writes whatever shape the client posted")
        assert "answers = normalize_answer_map(" in student, (
            "the submit door grades whatever shape the client posted")
        assert "normalize_answer_map," in api and "normalize_answer_map," in student, (
            "a door imports the rule under another name, or not at all")


# ── 4. the vocabulary a teacher chooses from ─────────────────────────────────

class TestOneEntryForALettersQuestion:
    def test_the_picker_offers_the_multi_type_nowhere(self):
        assert qt.MCQ_MULTI not in qt.PICKER_TYPES, (
            "the exception is offered as a second picker entry, which is exactly what "
            "the toggle replaced")
        assert qt.PICKER_TYPES.count(qt.MCQ) == 1

    def test_exactly_one_picker_entry_is_a_choice_question(self):
        choosable = [t for t in qt.PICKER_TYPES if qt.question_kind(t) == qt.KIND_CHOICE]
        assert choosable == [qt.MCQ], (
            "a teacher is offered more than one way to pick a question with letters")

    def test_the_entry_says_only_what_it_is(self):
        assert qt._TYPE_LABELS[qt.MCQ] == ("Pilihan Ganda", "Multiple choice"), (
            "the entry names the single-answer rule that the toggle decides")
        assert qt.vocabulary()["labels"][qt.KIND_CHOICE] == list(qt._TYPE_LABELS[qt.MCQ]), (
            "the builder's own label and the picker's entry disagree")

    def test_the_akm_grid_keeps_a_name_of_its_own(self):
        """It is a statement grid, not a second way to answer with letters."""
        assert qt.question_kind(qt.COMPLEX_MULTIPLE_CHOICE) == qt.KIND_PGK
        assert qt.question_kind(qt.MCQ_MULTI) == qt.KIND_CHOICE
        assert qt._TYPE_LABELS[qt.COMPLEX_MULTIPLE_CHOICE][0] != qt._TYPE_LABELS[qt.MCQ][0]

    def test_the_builders_own_words_match_the_servers(self):
        """A hand-written label in the page is how the two drift apart."""
        form = FORM.read_text(encoding="utf-8")
        assert "{id:'Pilihan Ganda', en:'Multiple choice'," in form, (
            "the builder's copy of the label no longer matches `_TYPE_LABELS`")


# ── 5. the teacher is told when the toggle changes an answer they set ────────

class TestTheTeacherIsToldWhatTheToggleChanges:
    def test_turning_it_off_says_which_letters_went(self):
        form = FORM.read_text(encoding="utf-8")
        assert "multiTrimmed[i] = true;" in form
        assert "Kunci disisakan pada huruf pertama" in form, (
            "the key shrank and nothing told the teacher")

    def test_turning_it_on_names_the_key_that_is_still_one_letter(self):
        form = FORM.read_text(encoding="utf-8")
        assert "multiWidened[i] = true;" in form, (
            "switching the exception on leaves a one-letter key silently")
        assert "kuncinya masih menyebut satu huruf" in form

    def test_the_notice_is_only_for_a_question_that_already_exists(self):
        form = FORM.read_text(encoding="utf-8")
        assert "q._stored && (q.answers || []).length === 1" in form, (
            "every fresh question would raise the notice, which is wallpaper")
        assert "_stored: true," in form, (
            "nothing marks a question as one loaded from storage")

    def test_the_key_area_reads_the_exception_live(self):
        form = FORM.read_text(encoding="utf-8")
        assert ":data-key-mode=\"isMulti(q) ? 'checkbox' : 'radio'\"" in form, (
            "the teacher's key area no longer shows the mode the pupil will get")
        assert "Kunci Jawaban (boleh lebih dari satu):" in form
