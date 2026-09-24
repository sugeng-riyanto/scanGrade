"""Does `randomize_options` make a right answer record as a wrong one?

The question, and why a reading of the code is not an answer
-----------------------------------------------------------
`randomize_options` promises to reorder a multiple-choice question's options per
student. On this exam the paper is a PDF and the *screen* draws five bubbles
labelled A–E with no option text — the content lives on the paper, in its own
printed order. So "reorder the options" has to mean one of two very different
things, and they have different consequences for a mark:

  * **reorder the letters' positions** — the labels move around the row. The tap
    still records the label that was tapped, so a student who answers *by letter*
    is recorded exactly as before; but the screen's position and the paper's
    position stop agreeing, and a student who answers *by position* records a
    letter the paper never printed there.
  * **reorder the option content** — the content moves, and the letters must move
    with it (or the answer must be mapped back) for the mark to survive.

Which one the released page does is not visible in a diff: the shuffle is six
tokens of JavaScript inside a 4,600-line template, and the answer is stored as
whatever string the button carried. So this file runs the template's **own**
functions — the real `_shuffle`, the real option builder, the real `markAnswered`
— in node, and grades the recorded answer with the server's **own** grader
(`question_types.grade_answer`) rather than a restatement of it.

The distinction that matters
----------------------------
A label tap and a positional tap are the same instruction only while the letters
sit in the paper's order. These tests separate them, because a feature that is
safe for one and unsafe for the other is not a feature anybody can decide on from
the word "randomize".
"""
from __future__ import annotations

import json
import pathlib
import re
import shutil
import subprocess

import pytest

from app.services.question_types import grade_answer

ROOT = pathlib.Path(__file__).resolve().parents[2]
EXAM_PAGE = ROOT / "app" / "templates" / "student" / "take_exam.html"

NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="needs node to run the template's own JS")


# ── the pieces taken from the template, by their real source ──────────────────

#: The shuffle the page actually calls. Its randomness is `Math.random` directly,
#: and it takes no seed — so the order is regenerated on every page load.
SHUFFLE = re.compile(r"_shuffle\(arr\) \{.*?return arr; \}")
#: The builder that fills `this._shuffledOpts`.
OPTION_BUILDER = re.compile(
    r"this\._shuffledOpts = \{\};.*?this\._shuffledOpts\[i\] = shuffled;\s*\}\s*\}\);", re.S)
#: The method a bubble's @click calls, verbatim — the one place the answer is set.
#: Captured up to the closing brace but *not* the trailing comma, so the text can
#: be handed to `function …` as an expression.
MARK_ANSWERED = re.compile(r"markAnswered\(i, val, overwrite\) \{.*?\n        \}(?=,)", re.S)
#: The list the *template* falls back to when `_shuffledOpts` has no entry for a
#: question: `x-for="opt in (_shuffledOpts[i] || ['A','B','C','D','E'])"`. Reading it
#: from there is the point — a test that hard-codes A–E could pass while the page
#: offered a different set.
FALLBACK_OPTIONS = re.compile(
    r"""x-for="opt in \(_shuffledOpts\[i\] \|\| (\[[^\]]*\])\)\"""")
#: The client's kind mapping, so the builder's own `sgKind(q.type) === 'choice'`
#: gate is evaluated by the real vocabulary rather than a guess.
SG_KIND = re.compile(r"function sgKind\(type\) \{[^\n]*\}")


def _source() -> str:
    return EXAM_PAGE.read_text(encoding="utf-8")


def _one(pattern: re.Pattern, what: str) -> str:
    match = pattern.search(_source())
    assert match, f"could not find {what} in the exam page — has it been renamed or removed?"
    return match.group(1) if match.groups() else match.group(0)


# ── the harness ──────────────────────────────────────────────────────────────

class TestWhatTheShuffleActuallyDoes:
    """Every case is run by the template's functions, in node, offline."""

    #: A paper of three choice questions. The key is the letter the *paper* prints;
    #: `chosen` is the letter a student decided on by reading that paper.
    QUESTIONS = [
        {"type": "mcq", "origIdx": 0, "key": "C", "chosen": "C"},
        {"type": "mcq", "origIdx": 1, "key": "A", "chosen": "A"},
        {"type": "mcq", "origIdx": 2, "key": "E", "chosen": "E"},
    ]

    def _run(self, seeds, randomize):
        """Build the options and answer each question, with the page's own code.

        `Math.random` is replaced by a seeded generator *before* the real
        `_shuffle` runs, so an order that would otherwise be one draw in 120 is
        reproducible — the alternative is a test that passes or fails depending on
        the run, which is how a real defect gets filed as flaky.
        """
        script = "\n".join([
            f"const SG_QT = {json.dumps(self._vocabulary())};",
            _one(SG_KIND, "the sgKind helper"),
            f"const BASE = {_one(FALLBACK_OPTIONS, 'the fallback option list')};",
            f"const _shuffle = function {_one(SHUFFLE, 'the _shuffle method')};",
            f"const markAnswered = function {_one(MARK_ANSWERED, 'the markAnswered method')};",
            "const CASES = " + json.dumps({
                "seeds": seeds, "randomize": randomize, "questions": self.QUESTIONS,
            }) + ";",
            # The builder, wrapped in a thunk so it can be pointed at any component.
            "const buildOptions = function () { " + _one(OPTION_BUILDER, "the option builder") + " };",
            "const out = CASES.seeds.map((seed) => {",
            "  let s = seed >>> 0;",
            "  Math.random = () => ((s = (s * 1103515245 + 12345) & 0x7fffffff) / 0x7fffffff);",
            "  const comp = {",
            "    questions: CASES.questions.map((q) => ({type: q.type, origIdx: q.origIdx, answer: null})),",
            "    antiCheat: {randomize_options: CASES.randomize},",
            "    _shuffledOpts: {}, answered: {}, _answerTimestamps: null,",
            "    markDirty() {}, _shuffle, markAnswered,",
            "  };",
            "  buildOptions.call(comp);",
            "  const perQuestion = comp.questions.map((q, i) => {",
            # What the row shows: the built order, or the template's own fallback.
            "    const order = comp._shuffledOpts[i] || BASE;",
            # (a) the student taps the bubble carrying the letter they read.
            "    const chosen = CASES.questions[i].chosen;",
            "    const labelTapComp = Object.assign({}, comp, {",
            "      questions: comp.questions.map((x) => Object.assign({}, x)), answered: {},",
            "    });",
            "    markAnswered.call(labelTapComp, i, chosen);",
            # (b) the student taps the bubble standing where the paper prints it.
            "    const position = BASE.indexOf(chosen);",
            "    const positionalComp = Object.assign({}, comp, {",
            "      questions: comp.questions.map((x) => Object.assign({}, x)), answered: {},",
            "    });",
            "    markAnswered.call(positionalComp, i, order[position]);",
            "    return {",
            "      displays: order, built: !!comp._shuffledOpts[i],",
            "      labelTap: {letter: chosen, recorded: labelTapComp.questions[i].answer},",
            "      positionalTap: {",
            "        position, paperLetter: BASE[position], screenLetter: order[position],",
            "        recorded: positionalComp.questions[i].answer,",
            "      },",
            "    };",
            "  });",
            "  return {seed, perQuestion};",
            "});",
            "console.log(JSON.stringify({base: BASE, results: out}));",
        ])
        done = subprocess.run([NODE, "-e", script], capture_output=True, text=True, timeout=60)
        assert done.returncode == 0, done.stderr
        return json.loads(done.stdout.strip())

    def _vocabulary(self):
        """The real `SG_QT` the page is rendered with — the app's own vocabulary."""
        from app.services.question_types import vocabulary
        return vocabulary()

    # ── the letters are a permutation, never a replacement ───────────────────

    @needs_node
    def test_the_row_always_offers_the_same_five_letters(self):
        """A shuffle may move a letter, never invent or drop one.

        If it could, the app would offer a student a choice the answer key cannot
        hold — a wrong answer with no correct option to pick.
        """
        data = self._run(seeds=[1, 7, 12345], randomize=True)
        for result in data["results"]:
            for q in result["perQuestion"]:
                assert sorted(q["displays"]) == sorted(data["base"]), (
                    "the shuffled row is not a permutation of the option list"
                )

    # ── the answer a letter-tap records ──────────────────────────────────────

    @needs_node
    def test_tapping_the_bubble_that_carries_the_letter_records_that_letter(self):
        """The pairing the mark depends on: the label is what is stored."""
        data = self._run(seeds=[1, 7, 12345], randomize=True)
        for result in data["results"]:
            for i, q in enumerate(result["perQuestion"]):
                want = self.QUESTIONS[i]["chosen"]
                assert q["labelTap"]["recorded"] == want, (
                    "the bubble labelled %s stored %r" % (want, q["labelTap"]["recorded"])
                )

    @needs_node
    def test_a_letter_answer_still_scores_correct_against_the_key(self):
        """Graded by the server's own rule, not by a restatement of it.

        This is the load-bearing half of the answer to the question in the title:
        shuffling the row does **not** by itself mispair a letter answer with the
        key, because the stored value is the label the student read.
        """
        data = self._run(seeds=[1, 7, 12345], randomize=True)
        for result in data["results"]:
            for i, q in enumerate(result["perQuestion"]):
                key = self.QUESTIONS[i]["key"]
                assert grade_answer("mcq", key, q["labelTap"]["recorded"]) is True, (
                    "a student who answered the paper's %s was graded wrong" % key
                )

    # ── the half that does move a mark ───────────────────────────────────────

    @needs_node
    def test_the_screen_stops_standing_in_the_papers_order(self):
        """What the shuffle actually changes, stated as the fact it is.

        The paper prints A–E in a fixed order. After a shuffle the row is some
        other order, so the letter at a given position on the screen is not the
        letter the paper prints there. This is true by construction of the
        feature, and it is the only thing about it that can move a mark.
        """
        data = self._run(seeds=[1, 7, 12345], randomize=True)
        moved = [
            q for result in data["results"] for q in result["perQuestion"]
            if q["positionalTap"]["screenLetter"] != q["positionalTap"]["paperLetter"]
        ]
        assert moved, (
            "the shuffle left every letter in the paper's order — then it is not "
            "shuffling, and the rest of this file is about a feature that is off"
        )

    @needs_node
    def test_answering_by_position_records_a_letter_the_paper_did_not_print_there(self):
        """The mismatch, graded: a right-by-position answer scored wrong.

        This is not a claim about how students answer — it is the measurement of
        the drift the shuffle introduces between the screen and the paper. A row
        whose positions no longer name the paper's options cannot be read
        positionally, and nothing on the page says so.

        **This is a characterisation test and it is meant to fail when the drift
        is fixed.** If the row is ever made to keep the paper's order — or the
        screen is made to say it does not — this assertion stops holding, and the
        failure is the signal that a decision was made. Do not silence it by
        weakening the assertion; invert it and say what replaced the behaviour.
        """
        data = self._run(seeds=[1, 7, 12345], randomize=True)
        wrong = []
        for result in data["results"]:
            for i, q in enumerate(result["perQuestion"]):
                tap = q["positionalTap"]
                if tap["screenLetter"] == tap["paperLetter"]:
                    continue
                key = self.QUESTIONS[i]["key"]
                if grade_answer("mcq", key, tap["recorded"]) is False:
                    wrong.append(tap)
        assert wrong, (
            "no positional answer was scored wrong: either the row kept the "
            "paper's order, or the recorded letter matched the key anyway"
        )

    # ── the order is not stable, and cannot be ───────────────────────────────

    @needs_node
    def test_two_loads_of_the_same_paper_can_show_two_different_orders(self):
        """The order is drawn per load, not stored per attempt.

        `_shuffle` reads `Math.random` and takes no seed, so a reload redraws it.
        A student who answers by position therefore records a different letter on
        the next load than on this one — and a teacher comparing two papers'
        `answers` cannot see why the same habit produced two different values.
        """
        data = self._run(seeds=list(range(1, 25)), randomize=True)
        orders = {tuple(q["displays"]) for result in data["results"] for q in result["perQuestion"]}
        assert len(orders) > 1, (
            "every load produced the same order — the row is not being redrawn"
        )

    @needs_node
    def test_the_order_is_drawn_from_math_random_with_no_seed_to_reproduce_it(self):
        """The source-level fact behind the instability above.

        If the order were stored per attempt, the same student's row would be
        reproducible and a replay could be compared with it. Nothing here stores
        it, so it cannot be.
        """
        shuffle = _one(SHUFFLE, "the _shuffle method")
        assert "Math.random()" in shuffle, "the shuffle no longer draws at random"
        assert "seed" not in shuffle, "the shuffle gained a seed — record where it comes from"

    # ── with the setting off, the row is the paper's order ───────────────────

    @needs_node
    def test_with_the_setting_off_nothing_is_built_and_the_fallback_is_the_paper_order(self):
        """The template's fallback is the identity, so `randomize_options` off is
        the paper's own order — the state every existing exam is in."""
        data = self._run(seeds=[1, 7, 12345], randomize=False)
        for result in data["results"]:
            for q in result["perQuestion"]:
                assert q["built"] is False, (
                    "an option order was built while randomize_options was off"
                )
                assert q["displays"] == data["base"], (
                    "with the setting off the row is not the paper's own order"
                )

    @needs_node
    def test_with_the_setting_off_a_positional_answer_is_graded_correct(self):
        """How it behaves today, for comparison with the shuffled case."""
        data = self._run(seeds=[1, 7, 12345], randomize=False)
        for result in data["results"]:
            for i, q in enumerate(result["perQuestion"]):
                tap = q["positionalTap"]
                assert tap["screenLetter"] == tap["paperLetter"]
                assert grade_answer("mcq", self.QUESTIONS[i]["key"], tap["recorded"]) is True


# ── what the screen has to shuffle, and what it has ──────────────────────────

def test_the_choice_control_renders_the_letter_and_no_option_content():
    """`randomize_options` cannot reorder content the screen never draws.

    The paper carries the option text; the screen draws five bubbles whose whole
    content is the letter. So "reorder the options" can only mean "move the
    letters", which is why the tests above separate a label answer from a
    positional one — and why the feature's name overstates what it can do here.
    """
    src = _source()
    start = src.index('x-for="opt in (_shuffledOpts[i]')
    button = src[start:start + 900]
    assert 'x-text="opt"' in button, (
        "the bubble no longer renders the letter itself — if it now renders option "
        "content, this file's question has changed and must be asked again"
    )
    assert "QUESTION_OPTIONS" not in button, (
        "the choice row now renders server-prepared option content; the shuffle may "
        "no longer be letters-only"
    )
