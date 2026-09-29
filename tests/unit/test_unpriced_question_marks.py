"""A question the paper does not price still carries the teacher's mark.

What was wrong
--------------
A teacher graded the two essays of "Ujian Fisika" in the grading queue, the save
reported success, and the score stayed 0. The mark *was* stored —
`teacher_feedback.scores` held ``{"3": 100, "4": 80}`` — but the exam's
`question_weights` covered only its three multiple-choice questions (16.67 each,
50 in all), so neither essay had a share at all. Both recalculations asked
``weights.get(index, 0)``, found nothing, and dropped whatever the teacher had
marked:

    ew = float(question_weights.get(str(qi), 0))
    if ew > 0:                    # a zero weight means "not priced", not "not marked"
        earned += float(sv) / 100.0 * ew

The fallback that would have priced those essays ran only when the map was *empty*,
and this one held three entries. So ``final = 0 + 0 - 0``, and
``teacher._recalculate_scores`` wrote that over the number the override route had
just saved. Those four lines were written out twice — `teacher._recalculate_scores`
and `api.grade_batch` — which is why both are held here.

What replaces it
----------------
`question_types.complete_weights`: the questions that *are* priced keep their marks
exactly, and the ones that are not split the paper's remainder equally. On the paper
that reported this, 100 - 50 is 50 over two essays — 25 each — so the corrected
submission earns 45 rather than 0.

The line that must not move is a paper that is already complete: with every question
priced there is no remainder, and the map comes back exactly as it was stored. A
recalculation must not be a place where marks already in students' hands change.
"""
import re
from pathlib import Path

from app.services.question_types import complete_weights

ROOT = Path(__file__).resolve().parents[2]


def _source(*parts) -> str:
    return ROOT.joinpath(*parts).read_text(encoding="utf-8")


def block_of(source: str, marker: str, *, stop: str = "\n@") -> str:
    """One function or route body: from `marker` up to the next `stop`."""
    start = source.index(marker)
    rest = source[start:]
    nxt = rest.find(stop, 1)
    return rest if nxt == -1 else rest[:nxt]


TEACHER = _source("app", "routes", "teacher.py")
API = _source("app", "routes", "api.py")

RECALC = block_of(TEACHER, "def _recalculate_scores")
BATCH = block_of(API, "def grade_batch")


def the_paper() -> dict:
    """"Ujian Fisika" as it is stored: three MCQ priced, two essays not."""
    return {"0": "mcq", "1": "mcq", "2": "mcq", "3": "essay", "4": "essay"}


def its_weights() -> dict:
    """Its stored map: the objective pool only, 16.67 a question, 50 in all."""
    return {"0": 50 / 3, "1": 50 / 3, "2": 50 / 3}


def total(weights) -> float:
    return round(sum(float(v) for k, v in weights.items()
                     if not str(k).startswith("_")), 1)


# ── 1. the rule ──────────────────────────────────────────────────────────────

class TestTheRule:
    def test_an_unpriced_question_is_given_the_paper_s_remainder(self):
        weights = complete_weights(its_weights(), 5)
        assert weights["3"] == 25.0 and weights["4"] == 25.0, (
            "the two essays were left with no share, so a teacher's mark on them "
            "could only ever be worth nothing")

    def test_the_priced_questions_keep_their_marks_exactly(self):
        """Not "roughly": the MCQ shares are the teacher's own numbers."""
        weights = complete_weights(its_weights(), 5)
        for i in ("0", "1", "2"):
            assert weights[i] == its_weights()[i]

    def test_the_paper_comes_back_whole(self):
        assert total(complete_weights(its_weights(), 5)) == 100.0

    def test_a_remainder_that_does_not_divide_evenly_still_adds_up(self):
        """A tenth is the precision the app reports, so the parts add to the total."""
        weights = complete_weights({"0": 90.0}, 4)
        assert sorted(weights.values()) == [3.3, 3.3, 3.4, 90.0]
        assert total(weights) == 100.0

    def test_a_paper_that_prices_every_question_is_returned_untouched(self):
        """The guarantee that matters for marks already given out."""
        complete = {str(i): 20.0 for i in range(5)}
        assert complete_weights(complete, 5) == complete

    def test_a_map_that_already_spends_the_whole_paper_invents_nothing(self):
        """Nothing left to give is an honest answer, not a reason to make marks up."""
        weights = complete_weights({"0": 60.0, "1": 40.0}, 3)
        assert "2" not in weights
        assert total(weights) == 100.0

    def test_the_mark_scheme_beside_the_weights_is_left_alone(self):
        scheme = {"by_type": {"mcq": 1.0}, "partial": True}
        weights = complete_weights({"_scheme": scheme, "0": 100.0}, 2)
        assert weights["_scheme"] == scheme

    def test_junk_in_the_map_does_not_break_the_rule(self):
        weights = complete_weights({"0": None, "1": "not a number", "2": 30.0}, 3)
        assert weights["0"] == 35.0 and weights["1"] == 35.0
        assert weights["2"] == 30.0

    def test_a_paper_with_no_questions_is_returned_as_it_is(self):
        assert complete_weights({"0": 50.0}, 0) == {"0": 50.0}

    def test_a_paper_that_prices_nothing_shares_the_whole_total(self):
        """An empty map is "nothing is priced", not "no questions".

        Reachable through the batch grader, whose own fallback prices the objective
        pool only: a paper with no stored weights and no objective question arrives
        here empty, which is exactly the case that comment says the essays' marks are
        meant to survive.
        """
        assert complete_weights({}, 5) == {str(i): 20.0 for i in range(5)}
        assert complete_weights(None, 2) == {"0": 50.0, "1": 50.0}

    def test_the_paper_that_reported_this_now_earns_the_teacher_s_marks(self):
        """The reported paper, to the mark: essays 100 and 80, MCQ all wrong."""
        weights = complete_weights(its_weights(), 5)
        earned = 0.0
        for qi, sv in {"3": 100, "4": 80}.items():
            ew = float(weights.get(qi, 0))
            if ew > 0:
                earned += float(sv) / 100.0 * ew
        assert earned == 45.0, (
            "a corrected paper still scores 0: the teacher's marks are being dropped")


# ── 2. both recalculations use it, and before they read the marks ────────────

#: The rule as it is *written*: a statement that assigns a completed map. The anchor
#: is deliberately not `"complete_weights(" in block` — a **commented-out** call
#: satisfies that, and the mutation that comments the line out is precisely the
#: defect this has to catch ("the rule is present but never runs").
CALL = re.compile(r"^[ \t]*question_weights = complete_weights\(", re.M)


class TestBothEnginesUseIt:
    """Source-level guards, because the defect was the same rule written twice.

    Each of these failed before this change: `teacher._recalculate_scores` and
    `api.grade_batch` both looked a question's share up, got 0 for a question the
    paper had not priced, and skipped the teacher's mark on it.
    """

    def test_the_teacher_recalculation_completes_the_weights(self):
        assert CALL.search(RECALC), (
            "the recalculation can still drop a teacher's mark on a question the "
            "paper does not price — or the call is there but commented out, which "
            "is the same thing with a grep for company")

    def test_the_batch_grade_route_completes_the_weights(self):
        assert CALL.search(BATCH), (
            "the batch grader can still drop a teacher's mark on a question the "
            "paper does not price")

    def test_neither_engine_completes_them_more_than_once(self):
        """One rule, one place. A second call is how the first one went wrong.

        A duplicate also defeats the ordering guard below: the original call still
        precedes the marks, so a copy after them reads as a pass.
        """
        for name, block in (("teacher.py", RECALC), ("api.py", BATCH)):
            found = len(CALL.findall(block))
            assert found == 1, f"{name} completes the weights {found} times"

    def test_the_teacher_recalculation_completes_them_before_reading_the_marks(self):
        assert CALL.search(RECALC).start() < RECALC.index("fb_scores"), (
            "the weights are completed after the marks are read, so nothing changes")

    def test_the_batch_route_completes_them_before_reading_the_marks(self):
        assert CALL.search(BATCH).start() < BATCH.index("fb_scores"), (
            "the weights are completed after the marks are read, so nothing changes")

    def test_neither_engine_prices_a_missing_question_itself(self):
        """One rule, one place: a second copy is how this happened."""
        for name, block in (("teacher.py", RECALC), ("api.py", BATCH)):
            assert not re.search(r"100\s*/\s*len\(.*essay", block), (
                f"{name} is pricing the unpriced questions on its own again")
