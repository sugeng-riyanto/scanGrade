"""The three PGK marking modes, against their reference numbers — pure.

What "pure" means here, and why it is the point
-----------------------------------------------
Nothing in this file touches the database, the Flask app, a template, or the
schema. The only import is ``app.services.question_types``, which itself imports
nothing but the standard library — so this suite answers one question and no
others: *does the arithmetic agree with the published rule?* A marking rule is
the one part of an assessment system that has to be right before anything is
built on top of it, and a test that needed a database to state it would be a test
that could be red for reasons other than the rule.

The reference
-------------
The numbers are the ones the Kemendikbud AKM guidance publishes for a complex
multiple choice question judged statement by statement:

    skor 2 bila menjawab semua benar
    skor 1 bila salah 1 atau 2
    skor 0 bila salah lebih dari 2

and, for the shorter two-option form, the binary equivalent (1/0). ScanGrade
expresses both as **one rule over the question's shape**: inside the 3-5
statement, two-category band AKM marks 1/0, and outside it the 2/1/0 ladder.

Two honest notes, because the brief and the guidance are not the same document:

* **Mode 1 is the AKM rule** and is pinned to it here, number for number.
* **Modes 2 and 3 are ScanGrade's own options**, not AKM's. The brief defines
  them — a proportional share with a guessing correction, and a plain
  all-or-nothing — and they are asserted against *that* definition. Calling the
  proportional rule "AKM" would be a claim about a document that does not contain
  it, so the file does not make it.

The share every mode returns is a fraction in ``[0, 1]``, which the question's
own marks are multiplied by. So the ladder's 2/1/0 becomes 10/5/0 on a question
worth ten — and that conversion is asserted here too, because it is the step a
teacher actually reads on a result sheet.
"""
from __future__ import annotations

import pytest

from app.services import question_types as qt

PGK = "complex_multiple_choice"

#: The AKM band, restated here as the reference rather than imported: a test that
#: reads the boundary out of the code it is testing cannot notice the boundary
#: moving. `tests/unit/test_pgk_scoring.py` pins these to the module's constants.
BINARY_MIN, BINARY_MAX = 3, 5
PARTIAL_WRONG = 2


# ── the shapes, built by hand ────────────────────────────────────────────────

def key_with(total: int, categories: list[str], wanted: list[int]):
    """A key of `total` statements and the correct category for each."""
    return {
        "statements": [f"statement {i + 1}" for i in range(total)],
        "categories": list(categories),
        "key": list(wanted),
    }


def two_option(total: int, all_first: bool = True):
    """A Benar/Salah key whose correct category is 0 for every statement."""
    return key_with(total, ["Benar", "Salah"], [0] * total)


def judgements(total: int, wrong: int, wrong_category: int = 1) -> list[int]:
    """`total` judgements, the last `wrong` deliberately not the key."""
    return [0] * (total - wrong) + [wrong_category] * wrong


# ── the three rules, written from their references ───────────────────────────

def akm_reference(total: int, n_categories: int, wrong: int) -> float:
    """Mode 1 — the AKM rule.

    1/0 across a 3-5 statement, two-category question; 2/1/0 outside that band.
    """
    binary = BINARY_MIN <= total <= BINARY_MAX and n_categories == 2
    if binary:
        return 1.0 if wrong == 0 else 0.0
    if wrong == 0:
        return 1.0
    return 0.5 if wrong <= PARTIAL_WRONG else 0.0


def proportional_reference(total: int, wrong: int) -> float:
    """Mode 2 — the brief's proportional share with a guessing correction."""
    right = total - wrong
    return max(0.0, (right - wrong) / total)


def all_or_nothing_reference(wrong: int) -> float:
    """Mode 3 — full marks only when nothing is wrong."""
    return 1.0 if wrong == 0 else 0.0


# ── 1. mode 1: the AKM rule, swept over the whole reachable range ────────────

class TestModeOneTheAkmReference:
    @pytest.mark.parametrize("total", list(range(1, 11)))
    @pytest.mark.parametrize("categories", [["Benar", "Salah"], ["Ya", "Tidak"]])
    def test_every_wrong_count_is_the_reference_number(self, total, categories):
        key = key_with(total, categories, [0] * total)
        for wrong in range(total + 1):
            got = qt.pgk_score("akm_standard", key, judgements(total, wrong))
            assert got == akm_reference(total, 2, wrong), (
                f"{total} statements, 2 categories, {wrong} wrong: {got}")

    @pytest.mark.parametrize("total", list(range(1, 11)))
    @pytest.mark.parametrize("n_categories", [3, 4, 5])
    def test_more_than_two_categories_is_always_the_ladder(self, total, n_categories):
        categories = [f"kategori{i}" for i in range(n_categories)]
        key = key_with(total, categories, [0] * total)
        for wrong in range(total + 1):
            got = qt.pgk_score("akm_standard", key, judgements(total, wrong))
            assert got == akm_reference(total, n_categories, wrong), (
                f"{total} statements, {n_categories} categories, {wrong} wrong: {got}")

    @pytest.mark.parametrize("total", [3, 4, 5])
    def test_the_binary_band_is_the_published_1_or_0(self, total):
        """Inside the band one wrong statement costs the whole question — this is
        the AKM rule, not a rounding of the ladder."""
        key = two_option(total)
        assert qt.pgk_score("akm_standard", key, judgements(total, 0)) == 1.0
        for wrong in range(1, total + 1):
            assert qt.pgk_score("akm_standard", key, judgements(total, wrong)) == 0.0

    @pytest.mark.parametrize("total", [6, 7, 8, 10])
    def test_outside_the_band_is_the_published_2_1_0(self, total):
        key = two_option(total)
        assert qt.pgk_score("akm_standard", key, judgements(total, 0)) == 1.0   # 2
        assert qt.pgk_score("akm_standard", key, judgements(total, 1)) == 0.5   # 1
        assert qt.pgk_score("akm_standard", key, judgements(total, 2)) == 0.5   # 1
        assert qt.pgk_score("akm_standard", key, judgements(total, 3)) == 0.0   # 0
        assert qt.pgk_score("akm_standard", key, judgements(total, total)) == 0.0

    def test_the_band_edge_is_five_versus_six(self):
        """The one place the rule changes shape, stated on its own so a shift of
        one statement is a failure with its own name."""
        five, six = two_option(5), two_option(6)
        assert qt.pgk_score("akm_standard", five, judgements(5, 1)) == 0.0
        assert qt.pgk_score("akm_standard", six, judgements(6, 1)) == 0.5


# ── 2. mode 2: the proportional share, with its correction ───────────────────

class TestModeTwoProportional:
    @pytest.mark.parametrize("total", list(range(1, 9)))
    @pytest.mark.parametrize("n_categories", [2, 3])
    def test_the_share_is_the_reference_over_every_wrong_count(self, total, n_categories):
        categories = [f"k{i}" for i in range(n_categories)]
        key = key_with(total, categories, [0] * total)
        for wrong in range(total + 1):
            got = qt.pgk_score("proportional", key, judgements(total, wrong))
            assert got == pytest.approx(proportional_reference(total, wrong)), (
                f"{total} statements, {n_categories} categories, {wrong} wrong: {got}")

    def test_the_band_does_not_apply_to_this_mode(self):
        """Mode 2 is proportional at every length: a five-statement question with
        one wrong earns four fifths, where mode 1 earns nothing."""
        key = two_option(5)
        assert qt.pgk_score("proportional", key, judgements(5, 1)) == pytest.approx(0.6)
        assert qt.pgk_score("akm_standard", key, judgements(5, 1)) == 0.0

    def test_a_wrong_majority_cannot_go_below_zero(self):
        """The correction, which is the whole reason this mode exists: guessing
        your way to more wrong than right earns nothing, never a negative mark."""
        key = two_option(6)
        assert qt.pgk_score("proportional", key, judgements(6, 4)) == 0.0
        assert qt.pgk_score("proportional", key, judgements(6, 5)) == 0.0
        assert qt.pgk_score("proportional", key, judgements(6, 6)) == 0.0

    def test_a_tie_between_right_and_wrong_is_worth_nothing(self):
        key = two_option(4)
        assert qt.pgk_score("proportional", key, judgements(4, 2)) == 0.0


# ── 3. mode 3: all-or-nothing, at any length ─────────────────────────────────

class TestModeThreeAllOrNothing:
    @pytest.mark.parametrize("total", list(range(1, 9)))
    @pytest.mark.parametrize("n_categories", [2, 3])
    def test_it_is_full_marks_or_nothing(self, total, n_categories):
        categories = [f"k{i}" for i in range(n_categories)]
        key = key_with(total, categories, [0] * total)
        for wrong in range(total + 1):
            got = qt.pgk_score("all_or_nothing", key, judgements(total, wrong))
            assert got == all_or_nothing_reference(wrong), (
                f"{total} statements, {n_categories} categories, {wrong} wrong: {got}")

    def test_it_ignores_the_band_entirely(self):
        """Same numbers at four statements (inside the band) and six (outside it)."""
        for total in (4, 6):
            key = two_option(total)
            assert qt.pgk_score("all_or_nothing", key, judgements(total, 0)) == 1.0
            assert qt.pgk_score("all_or_nothing", key, judgements(total, 1)) == 0.0


# ── 4. the three modes side by side, so the differences are on the record ────

class TestTheThreeModesDisagreeExactlyWhereTheyShould:
    """A truth table as assertions: each row names a question shape and an answer
    pattern, and the three columns are the three modes. Where two modes agree,
    that agreement is asserted too — a mode that quietly became another is the
    defect this class exists to catch."""

    # (statements, categories, wrong): (akm, proportional, all_or_nothing)
    TABLE = [
        # 3 statements, 2 categories -> inside the binary band
        ((3, 2, 0), (1.0, 1.0, 1.0)),
        ((3, 2, 1), (0.0, 1 / 3, 0.0)),
        ((3, 2, 3), (0.0, 0.0, 0.0)),
        # 5 statements, 2 categories -> still inside
        ((5, 2, 0), (1.0, 1.0, 1.0)),
        ((5, 2, 1), (0.0, 0.6, 0.0)),
        ((5, 2, 2), (0.0, 0.2, 0.0)),
        ((5, 2, 3), (0.0, 0.0, 0.0)),
        # 6 statements, 2 categories -> the ladder
        ((6, 2, 0), (1.0, 1.0, 1.0)),
        ((6, 2, 1), (0.5, 2 / 3, 0.0)),
        ((6, 2, 2), (0.5, 1 / 3, 0.0)),
        ((6, 2, 3), (0.0, 0.0, 0.0)),
        # 4 statements, 3 categories -> the length is inside the band, the
        # category count puts it outside it
        ((4, 3, 0), (1.0, 1.0, 1.0)),
        ((4, 3, 1), (0.5, 0.5, 0.0)),
        ((4, 3, 2), (0.5, 0.0, 0.0)),
    ]

    @pytest.mark.parametrize("shape, expected", TABLE)
    def test_the_row_is_what_the_table_says(self, shape, expected):
        total, n_categories, wrong = shape
        categories = [f"k{i}" for i in range(n_categories)]
        key = key_with(total, categories, [0] * total)
        answer = judgements(total, wrong)
        got = (
            qt.pgk_score("akm_standard", key, answer),
            qt.pgk_score("proportional", key, answer),
            qt.pgk_score("all_or_nothing", key, answer),
        )
        akm, prop, aon = expected
        assert got[0] == pytest.approx(akm), f"AKM row {shape}: {got[0]}"
        assert got[1] == pytest.approx(prop), f"proportional row {shape}: {got[1]}"
        assert got[2] == pytest.approx(aon), f"all-or-nothing row {shape}: {got[2]}"

    def test_no_two_modes_are_the_same_rule(self):
        """Somewhere in the table each pair must differ, or one of the three is a
        duplicate wearing a different name."""
        shapes = [(s, e) for s, e in self.TABLE]
        for a, b in ((0, 1), (0, 2), (1, 2)):
            differing = [s for s, e in shapes if e[a] != e[b]]
            assert differing, f"modes {a} and {b} never disagree in this table"


# ── 5. the share becomes marks, which is what a teacher reads ────────────────

class TestTheShareBecomesTheQuestionMarks:
    @pytest.mark.parametrize("marks", [1, 3, 10])
    def test_the_akm_ladder_converts_to_the_questions_own_points(self, marks):
        key = two_option(6)
        assert qt.part_factor(PGK, key, judgements(6, 0), "akm_standard") * marks == marks
        assert qt.part_factor(PGK, key, judgements(6, 1), "akm_standard") * marks == marks / 2
        assert qt.part_factor(PGK, key, judgements(6, 3), "akm_standard") * marks == 0

    def test_the_binary_band_converts_to_all_or_nothing(self):
        key = two_option(3)
        assert qt.part_factor(PGK, key, judgements(3, 0), "akm_standard") == 1.0
        assert qt.part_factor(PGK, key, judgements(3, 1), "akm_standard") == 0.0

    def test_a_ten_point_question_reads_10_5_0(self):
        """The exact numbers the brief names, on a question worth ten."""
        key = two_option(6)
        marks = 10
        assert [qt.part_factor(PGK, key, judgements(6, w), "akm_standard") * marks
                for w in (0, 1, 2, 3)] == [10.0, 5.0, 5.0, 0.0]


# ── 6. the shapes a real answer arrives in ───────────────────────────────────

class TestTheEdgeCasesThatAreNotAboutTheRule:
    def test_an_answer_of_the_wrong_width_is_no_answer(self):
        key = two_option(4)
        assert qt.pgk_score("akm_standard", key, [0, 0]) == 0.0
        assert qt.pgk_score("akm_standard", key, [0, 0, 0, 0, 0]) == 0.0

    def test_a_blank_row_is_not_a_correct_row(self):
        """A grid with a hole in it is not all-right, whatever else it holds."""
        key = two_option(4)
        assert qt.pgk_score("akm_standard", key, [0, 0, None, 0]) == 0.0
        assert qt.pgk_score("all_or_nothing", key, [0, 0, None, 0]) == 0.0

    def test_every_row_blank_earns_nothing(self):
        key = two_option(5)
        assert qt.pgk_score("akm_standard", key, [None] * 5) == 0.0

    def test_a_key_shorter_than_its_statements_is_not_a_key(self):
        broken = {"statements": ["a", "b", "c"], "categories": ["Benar", "Salah"], "key": [0, 0]}
        assert qt.pgk_score("akm_standard", broken, [0, 0, 0]) == 0.0
