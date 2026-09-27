"""Pilihan Ganda Kompleks: a new objective type, its three marking modes, and the column that holds one.

Why this file exists before the feature does
--------------------------------------------
PGK is not a flavour of multiple choice. One question asks a student to judge
several statements, so it carries N points of data instead of one, and the
Kemendikbud AKM guidance prices it in a way no existing rule can express — a
binary mark across a 3-5 statement, two-category question, and a 2/1/0 ladder
outside that band. Two things therefore have to be true at once, and each is a
separate failure mode:

* **the arithmetic is exact.** AKM's ladder is not "about half"; every one of the
  three modes is pinned to its published numbers over the boundary cases, because
  a marking rule that is merely close is a marking rule a teacher cannot defend to
  a parent;
* **the type is priced and graded through the one module that decides what a
  question *is*.** `question_types.py` exists because seven sites each classified
  "not mcq, so an essay" for themselves, and a new objective type would have been
  silently worth nothing in all seven. Nothing here may reintroduce that.

Scope of this file: the vocabulary, the scoring, the AKM boundary, the weight
conversion, the shared-er's freeze, and the SQL that holds the mode. The builder
editor and the student's answer control are later phases and are not asserted here.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from app.services import mark_scheme
from app.services import question_types as qt

ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS = ROOT / "supabase" / "migrations"

PGK = "complex_multiple_choice"

#: The migration this phase adds, found by name rather than by a hard number so a
#: later rename is a visible failure instead of a silently skipped test.
MIGRATION = next(iter(sorted(MIGRATIONS.glob("*_question_scoring*.sql"))), None)


# ── helpers that build the shapes the builder will write ─────────────────────

def pgk_key(categories, statement_key, statements=None):
    statements = statements if statements is not None else [f"statement {i}" for i in range(len(statement_key))]
    return {"categories": list(categories), "statements": list(statements), "key": list(statement_key)}


def bool_key(want):
    """A two-category key: `want` is a list of booleans (True = first category)."""
    return pgk_key(["Benar", "Salah"], [0 if w else 1 for w in want])


def answer_from(want):
    """The student's answer when they got exactly `want` right (same shape)."""
    return [0 if w else 1 for w in want]


# ── 1. the vocabulary ────────────────────────────────────────────────────────

class TestTheVocabulary:
    def test_the_type_is_known_by_its_snake_case_slug(self):
        assert qt.COMPLEX_MULTIPLE_CHOICE == PGK
        assert qt.canonical_type(PGK) == PGK
        assert qt.canonical_type("  COMPLEX_MULTIPLE_CHOICE  ") == PGK, (
            "a stored type is matched case-folded and trimmed like every other one")

    def test_it_is_objective_so_the_app_marks_it_and_the_pool_is_the_objective_one(self):
        assert qt.is_objective(PGK)
        assert not qt.is_essay(PGK)
        assert qt.pool(PGK) == "objective"

    def test_it_is_in_the_objective_family_the_routes_iterate(self):
        assert PGK in qt.OBJECTIVE_TYPES, (
            "not in OBJECTIVE_TYPES, so every site that asks `is_objective` would "
            "classify it as an essay and award it nothing")

    def test_it_has_its_own_kind_rather_than_borrowing_choices(self):
        assert qt.question_kind(PGK) == "pgk"
        assert qt.question_kind(PGK) != qt.question_kind(qt.MCQ), (
            "PGK renders per-statement controls, not five bubbles")
        assert qt.kind_label(PGK) == "Complex multiple choice"

    def test_it_is_now_creatable_because_its_editor_exists(self):
        """Registered, gradable, and *creatable* — which is the phase boundary.

        `PICKER_TYPES` is documented as the values a teacher may create, and the exam
        builder draws an editor per kind. The entry was deliberately withheld until
        the editor existed (offering it earlier would hand a teacher a selector with
        nothing under it and put a question with no statements on a real paper); this
        is the opposite assertion the earlier phase promised would replace it.
        """
        assert PGK in qt.PICKER_TYPES, (
            "the PGK editor ships with the type; the two are added in the same change")
        assert qt._TYPE_LABELS[PGK] == ("Pilihan Ganda Kompleks", "Complex multiple choice")

    def test_the_picker_did_not_gain_a_typed_essay_on_the_way(self):
        assert qt.ESSAY_TEXT not in qt.PICKER_TYPES


# ── 2. the three modes, named once ───────────────────────────────────────────

class TestTheModesAreAVocabulary:
    def test_there_are_exactly_three_and_they_are_the_ones_the_brief_names(self):
        assert qt.SCORING_MODES == ("akm_standard", "proportional", "all_or_nothing")

    def test_the_default_is_the_one_that_resists_guessing(self):
        """AKM is the default on purpose: the chance of guessing *every* two-option
        statement right is 0.5**n, which is why the guidance recommends it over a
        proportional rule for anything summative."""
        assert qt.DEFAULT_SCORING_MODE == "akm_standard"
        assert qt.scoring_mode({}, "0") == "akm_standard"
        assert qt.scoring_mode(None, "0") == "akm_standard"

    @pytest.mark.parametrize("junk", ["nonsense", "", "  ", 7, None, {}, [], True])
    def test_an_unknown_stored_mode_falls_back_to_the_default(self, junk):
        assert qt.scoring_mode({"0": junk}, "0") == qt.DEFAULT_SCORING_MODE

    @pytest.mark.parametrize("mode", ["akm_standard", "proportional", "all_or_nothing"])
    def test_every_real_mode_is_read_back_verbatim(self, mode):
        assert qt.scoring_mode({"3": mode}, "3") == mode

    def test_a_mode_stored_under_another_index_does_not_leak(self):
        assert qt.scoring_mode({"3": "proportional"}, "4") == "akm_standard"


# ── 3. mode 1 — AKM, with both bands ─────────────────────────────────────────

class TestModeOneTheAkmStandard:
    """The boundary is the whole point, so both sides of it are asserted."""

    @pytest.mark.parametrize("n", [3, 4, 5])
    def test_a_short_two_category_question_is_binary(self, n):
        all_right = bool_key([True] * n)
        assert qt.pgk_score("akm_standard", all_right, answer_from([True] * n)) == 1.0

        one_wrong = [True] * n
        one_wrong[-1] = False
        assert qt.pgk_score("akm_standard", bool_key([True] * n),
                            answer_from(one_wrong)) == 0.0, (
            "inside the binary band a single wrong statement is the whole question")

    @pytest.mark.parametrize("n", [3, 4, 5])
    def test_a_named_example_from_the_guidance_earns_nothing_at_one_mistake(self, n):
        """Concretely: five statements, four judged right, is a zero — the rule the
        brief chooses because guessing all five is a 1-in-32 event."""
        want = [True] * 5
        got = [True, True, True, True, False]
        assert qt.pgk_score("akm_standard", bool_key(want), answer_from(got)) == 0.0

    def test_a_six_statement_question_leaves_the_binary_band(self):
        want = [True] * 6
        key = bool_key(want)

        assert qt.pgk_score("akm_standard", key, answer_from(want)) == 1.0

        one_wrong = want.copy()
        one_wrong[0] = False
        assert qt.pgk_score("akm_standard", key, answer_from(one_wrong)) == 0.5, (
            "one wrong statement out of six is the middle rung of 2/1/0")

        two_wrong = want.copy()
        two_wrong[0] = two_wrong[1] = False
        assert qt.pgk_score("akm_standard", key, answer_from(two_wrong)) == 0.5, (
            "two wrong is still the middle rung")

        three_wrong = want.copy()
        three_wrong[0] = three_wrong[1] = three_wrong[2] = False
        assert qt.pgk_score("akm_standard", key, answer_from(three_wrong)) == 0.0, (
            "three wrong drops off the ladder")

        assert qt.pgk_score("akm_standard", key, [1] * 6) == 0.0

    def test_more_than_two_categories_leaves_the_binary_band_even_when_short(self):
        """Three statements and three categories is a 2/1/0 question: the guidance
        pairs the binary rule with two categories specifically."""
        categories = ["Hewan", "Tumbuhan", "Mikroorganisme"]
        key = pgk_key(categories, [0, 1, 2])
        assert qt.pgk_score("akm_standard", key, [0, 1, 2]) == 1.0
        assert qt.pgk_score("akm_standard", key, [1, 1, 2]) == 0.5
        assert qt.pgk_score("akm_standard", key, [1, 2, 2]) == 0.5
        assert qt.pgk_score("akm_standard", key, [1, 0, 1]) == 0.0, (
            "every statement judged into the wrong category")

    def test_the_binary_band_is_inclusive_at_both_ends(self):
        assert qt.pgk_score("akm_standard", bool_key([True] * 3), [1, 1, 1]) == 0.0
        assert qt.pgk_score("akm_standard", bool_key([True] * 5), [1] * 5) == 0.0
        assert qt.pgk_score("akm_standard", bool_key([True] * 6), [1] * 6) == 0.0

    def test_a_blank_statement_is_not_a_correct_one(self):
        """A row the student never filled is not right, so it counts against them —
        otherwise leaving rows empty would be cheaper than answering them."""
        key = bool_key([True, True, True, True, True, True])
        half_answered = [0, 0, 0, None, None, None]
        assert qt.pgk_score("akm_standard", key, half_answered) == 0.0


# ── 4. mode 2 — proportional, with the guess correction ──────────────────────

class TestModeTwoProportional:
    @pytest.mark.parametrize("right,total,expected", [
        (4, 4, 1.0),
        (3, 4, 0.5),
        (2, 4, 0.0),
        (1, 4, 0.0),
        (0, 4, 0.0),
        (3, 3, 1.0),
        (2, 3, pytest.approx(1 / 3, abs=1e-9)),
        (1, 3, 0.0),
        (1, 2, 0.0),
        (2, 2, 1.0),
    ])
    def test_the_corrected_share_is_right_minus_wrong_over_total(self, right, total, expected):
        want = [True] * total
        got = [True] * right + [False] * (total - right)
        assert qt.pgk_score("proportional", bool_key(want), answer_from(got)) == expected

    def test_it_never_goes_negative_however_wrong_the_paper(self):
        want = [True, True, True, True]
        got = [False, False, False, True]
        assert qt.pgk_score("proportional", bool_key(want), answer_from(got)) == 0.0

    def test_it_is_more_granular_than_akm_on_the_same_short_question(self):
        """The trade the brief warns about, stated as arithmetic: four two-category
        statements with three right is worth nothing under AKM, and
        `(3 - 1) / 4 = 0.5` under the proportional rule."""
        want = [True] * 4
        got = [True, True, True, False]
        akm = qt.pgk_score("akm_standard", bool_key(want), answer_from(got))
        prop = qt.pgk_score("proportional", bool_key(want), answer_from(got))
        assert (akm, prop) == (0.0, 0.5), (
            "AKM is binary in the 3-5 band; proportional is not")


# ── 5. mode 3 — all or nothing, outside every band ───────────────────────────

class TestModeThreeAllOrNothing:
    @pytest.mark.parametrize("n", [2, 3, 5, 6, 8])
    def test_full_marks_for_every_statement_and_nothing_for_one(self, n):
        want = [True] * n
        assert qt.pgk_score("all_or_nothing", bool_key(want), answer_from(want)) == 1.0
        one_wrong = want.copy()
        one_wrong[-1] = False
        assert qt.pgk_score("all_or_nothing", bool_key(want), answer_from(one_wrong)) == 0.0

    def test_it_ignores_the_statement_count_the_other_two_modes_care_about(self):
        want = [True] * 6
        assert qt.pgk_score("all_or_nothing", bool_key(want), answer_from(want)) == 1.0
        assert qt.pgk_score("akm_standard", bool_key(want), answer_from(want)) == 1.0


# ── 6. the share is what the marks come from ─────────────────────────────────

class TestTheShareBecomesTheTeachersPoints:
    """The brief's own example: a question worth 10 points turns a 2/1/0 into 10/5/0."""

    def _earned(self, mode, got, weight=10.0):
        types = {"0": PGK}
        key = {"0": bool_key([True] * 6)}
        answers = {"0": answer_from(got)}
        weights = {"0": weight, qt.SCHEME_KEY: {"partial": False}}
        earned, graded = qt.earned_points(types, key, answers, weights, 1)
        return earned, graded

    def test_full_marks_become_the_whole_weight(self):
        assert self._earned("akm_standard", [True] * 6)[0] == 10.0

    def test_the_middle_rung_becomes_half_the_weight(self):
        assert self._earned("akm_standard", [True, True, True, True, True, False])[0] == 5.0

    def test_the_bottom_rung_becomes_nothing(self):
        assert self._earned("akm_standard", [False] * 6)[0] == 0.0

    def test_the_question_is_counted_as_graded_either_way(self):
        assert self._earned("akm_standard", [False] * 6)[1] == 1


# ── 7. the mode is consulted even with no scheme on the paper ────────────────

class TestTheModeIsNotGatedOnThePapersScheme:
    """The trap this decision closes.

    `part_factor` has only ever been called when the paper carries a scheme with
    `partial` set, because partial credit was an opt-in extra. A PGK's mode *is* its
    part rule, so a paper with no scheme would have graded the question with
    `grade_answer` — all or nothing — and silently ignored the teacher's choice:
    they pick 2/1/0 and the student gets zero.
    """

    def _exam_with_no_scheme(self):
        return ({"0": PGK},
                {"0": bool_key([True] * 6)},
                {"0": answer_from([True, True, True, True, True, False])},
                {"0": 10.0})

    def test_the_partial_rule_off_the_paper_does_not_apply_to_a_pgk(self):
        types, key, answers, weights = self._exam_with_no_scheme()
        assert not qt.partial_credit(weights), "the fixture is meant to have no scheme"
        assert qt.earned_points(types, key, answers, weights, 1)[0] == 5.0, (
            "with no scheme the AKM middle rung was thrown away and the question "
            "graded all-or-nothing")

    def test_a_question_with_no_mode_of_its_own_still_freezes_without_a_scheme(self):
        """The other side of the same coin: adding a PGK branch must not hand
        part-marks to the types that never had them."""
        types = {"0": qt.MCQ, "1": qt.TRUE_FALSE}
        key = {"0": "A", "1": "true"}
        answers = {"0": "B", "1": "false"}
        weights = {"0": 10.0, "1": 10.0}
        earned, graded = qt.earned_points(types, key, answers, weights, 2)
        assert (earned, graded) == (0.0, 2)

    def test_the_helper_that_decides_it_lives_in_the_module(self):
        """`item_analysis` has to make the same call, and it must not decide it by
        naming the type itself — that is the seven-copies defect this module exists
        to end."""
        assert qt.partial_applies(PGK, {}) is True
        assert qt.partial_applies(qt.MCQ, {}) is False
        assert qt.partial_applies(qt.MCQ, {qt.SCHEME_KEY: {"partial": True}}) is True


# ── 8. right and wrong, and the one key a student may see ────────────────────

class TestGradingAndWhatThePupilMaySee:
    def test_the_question_is_right_only_at_full_credit(self):
        key = bool_key([True] * 6)
        assert qt.grade_answer(PGK, key, answer_from([True] * 6)) is True
        assert qt.grade_answer(PGK, key, answer_from([True] * 5 + [False])) is False

    def test_a_student_is_never_sent_the_answers(self):
        key = bool_key([True, False, True])
        options = qt.public_options(PGK, key)
        assert options is not None, "the student page cannot render a PGK without its statements"
        assert options["statements"] == list(key["statements"])
        assert options["categories"] == ["Benar", "Salah"]
        assert "key" not in options, (
            "the per-statement answers were sent to the pupil, which is the whole "
            "question given away")
        assert json.dumps(options).find('"key"') == -1

    @pytest.mark.parametrize("blank", [None, [], [None, None, None], {}, ""])
    def test_an_untouched_question_is_not_an_answer(self, blank):
        assert qt.has_answer(PGK, blank) is False

    def test_a_partly_filled_question_is_an_answer(self):
        assert qt.has_answer(PGK, [0, None, None]) is True
        assert qt.has_answer(PGK, {"answer": [0, None, None]}) is True, (
            "the offline queue wraps the answer; the wrapper is not a different shape")

    def test_a_key_needs_both_the_statements_and_the_answers(self):
        assert qt.key_has_answer(PGK, bool_key([True, False])) is True
        assert qt.key_has_answer(PGK, None) is False
        assert qt.key_has_answer(PGK, {}) is False
        assert qt.key_has_answer(PGK, {"categories": ["Benar", "Salah"],
                                       "statements": ["a", "b"], "key": []}) is False, (
            "a key shorter than the statements is not an answer key")

    def test_the_wrong_length_key_is_refused_rather_than_guessed_at(self):
        long_key = {"categories": ["Benar", "Salah"], "statements": ["a", "b", "c"], "key": [0, 1]}
        short_key = {"categories": ["Benar", "Salah"], "statements": ["a", "b"], "key": [0, 1, 0]}
        assert qt.key_has_answer(PGK, long_key) is False
        assert qt.key_has_answer(PGK, short_key) is False
        # ...and the scorer refuses to invent a share for either.
        assert qt.pgk_score("akm_standard", long_key, [0, 1]) == 0.0
        assert qt.pgk_score("akm_standard", short_key, [0, 1, 0]) == 0.0

    def test_the_teacher_reads_the_key_as_statements_not_as_reprs(self):
        text = qt.describe_answer(PGK, bool_key([True, False, True]))
        assert "Benar" in text and "Salah" in text
        assert "{" not in text and "}" not in text, (
            "a report cell must not carry the stored dict")

    def test_a_students_answer_is_described_in_words_when_the_key_is_at_hand(self):
        key = bool_key([True, False])
        with_key = qt.describe_attempt(PGK, [0, 1], key)
        assert "Benar" in with_key and "Salah" in with_key
        # Without the key there are no labels to borrow, so it stays honest and
        # names the positions instead of inventing a category.
        assert "1" in qt.describe_attempt(PGK, [0, 1])


# ── 9. reading a key however it was written ──────────────────────────────────

class TestReadingAKeyHoweverItWasWritten:
    def test_the_stored_shape_round_trips_unchanged(self):
        key = bool_key([True, False, True])
        assert qt.normalise_key(PGK, key) == key

    def test_ids_can_be_category_names_as_well_as_indexes(self):
        hand_written = {"categories": ["Benar", "Salah"],
                        "statements": ["a", "b"],
                        "key": ["Benar", "Salah"]}
        assert qt.pgk_key(hand_written) == (0, 1)

    def test_numeric_strings_are_indexes(self):
        assert qt.pgk_key({"categories": ["a", "b"], "statements": ["x"],
                           "key": ["1"]}) == (1,)

    def test_a_boolean_key_is_read_as_the_first_two_categories(self):
        assert qt.pgk_key({"categories": ["Benar", "Salah"], "statements": ["x", "y"],
                           "key": [True, False]}) == (0, 1)

    def test_a_key_written_as_a_bare_list_is_still_a_key(self):
        assert qt.pgk_key({"categories": ["a", "b"], "statements": ["x", "y"],
                           "key": [0, 1]}) == (0, 1)
        assert qt.pgk_key([0, 1, 0]) == (0, 1, 0)

    def test_a_junk_key_returns_empty_rather_than_raising(self):
        for junk in (None, 7, "x", {}, {"key": None}):
            assert qt.pgk_key(junk) == ()

    def test_the_statements_and_categories_are_read_defensively(self):
        assert qt.pgk_statements({"statements": ["a", "b"]}) == ("a", "b")
        assert qt.pgk_statements({"statements": [1, ""]}) == ("1",)
        assert qt.pgk_statements(None) == ()
        assert qt.pgk_categories({"categories": ["Benar", "Salah"]}) == ("Benar", "Salah")
        assert qt.pgk_categories({"categories": ["", "x"]}) == ("x",)
        assert qt.pgk_categories(None) == ()


# ── 10. pricing ──────────────────────────────────────────────────────────────

class TestTheSchemePricesIt:
    def test_it_gets_its_own_row_rather_than_a_share_of_anothers(self):
        assert PGK in mark_scheme.SCHEME_ORDER
        assert mark_scheme.default_marks(PGK) == 3.0, (
            "a PGK asks several judgements, so it is priced with the multi-part "
            "types rather than at a single mark")

    def test_it_is_not_priced_as_a_flavour_of_multiple_choice(self):
        assert mark_scheme.default_marks(PGK) != mark_scheme.default_marks(qt.MCQ)

    def test_a_paper_of_pgks_totals_exactly_one_hundred(self):
        types = {str(i): PGK for i in range(10)}
        weights = mark_scheme.build_weights(types, 10)
        assert sum(weights.values()) == pytest.approx(100.0)

    def test_the_scheme_table_gains_a_row_for_it(self):
        types = {"0": qt.MCQ, "1": PGK}
        table = mark_scheme.describe(types, 2, partial=False)
        rows = {r["type"]: r for r in table["rows"]}
        assert PGK in rows, "the mark scheme table has no row for the new type"
        assert rows[PGK]["kind"] == qt.kind_label(PGK)


# ── 11. the column, and the one vocabulary in it ─────────────────────────────

class TestTheColumnAndItsCheck:
    def test_the_migration_exists(self):
        assert MIGRATION is not None, (
            "no *_question_scoring*.sql in supabase/migrations — the column the "
            "builder writes cannot be stored")
        assert MIGRATION.read_text(encoding="utf-8").strip(), "the migration is empty"

    def test_it_adds_the_column_idempotently(self):
        sql = MIGRATION.read_text(encoding="utf-8")
        assert re.search(r"ADD COLUMN IF NOT EXISTS question_scoring\s+JSONB", sql), (
            "the migration must be re-runnable, so the column is added IF NOT EXISTS "
            "as JSONB")

    def test_the_check_is_a_real_constraint_backed_by_an_immutable_function(self):
        sql = MIGRATION.read_text(encoding="utf-8")
        assert re.search(r"CREATE OR REPLACE FUNCTION", sql), (
            "a CHECK cannot hold a per-question value on its own, so the vocabulary "
            "is enforced by a function")
        assert re.search(r"\bIMMUTABLE\b", sql), (
            "PostgreSQL refuses a non-immutable function in a CHECK")
        assert re.search(r"ADD CONSTRAINT\s+\w+\s+CHECK\s*\(", sql), (
            "nothing constrains the column, so the database would hold any string")
        assert re.search(r"DROP CONSTRAINT IF EXISTS", sql), (
            "a second run would fail on the existing constraint")

    def test_the_database_vocabulary_is_the_python_vocabulary(self):
        """One vocabulary, or the gate and the grader disagree — which is how a
        teacher's saved choice becomes a mode no code reads."""
        sql = MIGRATION.read_text(encoding="utf-8")
        in_sql = set(re.findall(r"'([a-z_]+)'", sql))
        assert set(qt.SCORING_MODES) <= in_sql, (
            f"the SQL does not name every mode: missing "
            f"{sorted(set(qt.SCORING_MODES) - in_sql)}")
        assert qt.DEFAULT_SCORING_MODE in in_sql

    def test_the_function_refuses_a_non_object_rather_than_raising_on_it(self):
        sql = MIGRATION.read_text(encoding="utf-8")
        assert "jsonb_typeof" in sql, (
            "jsonb_each_text raises on a non-object, so a hand-edited array would "
            "make the CHECK error instead of refuse")

    def test_it_touches_nothing_but_that_column(self):
        sql = MIGRATION.read_text(encoding="utf-8")
        assert not re.search(r"DROP\s+(TABLE|COLUMN)", sql), (
            "the migration must be additive: published marks live in these tables")
        assert not re.search(r"UPDATE\s+exams\s+SET", sql), (
            "no backfill — no exam carries a PGK yet, and inventing modes for "
            "existing questions would write a scheme nobody chose")


# ── 12. the mode has to reach the graders ────────────────────────────────────

def _call_arguments(text: str, name: str) -> list[str]:
    """Every call to ``name``, as the text inside its parentheses.

    A balanced-paren scan rather than a regex over lines, because these calls are
    written across several lines and a line-based check would quietly miss exactly
    the ones it exists to find. The definition is skipped by the `def` before it.
    """
    out: list[str] = []
    for match in re.finditer(re.escape(name) + r"\s*\(", text):
        if text[max(0, match.start() - 4):match.start()] == "def ":
            continue
        depth, i = 1, match.end()
        while i < len(text) and depth:
            if text[i] == "(":
                depth += 1
            elif text[i] == ")":
                depth -= 1
            i += 1
        out.append(text[match.end():i - 1])
    return out


class TestTheModeReachesEveryGrader:
    """A mode the grader never sees is a mode the teacher never chose.

    The marking mode is stored in a column of its own, so it reaches the score only
    if the route that loads the exam reads that column **and** passes it on. Both
    halves fail silently — `scoring_mode` answers with the default for a map it was
    never handed — so both are asserted from the source rather than left to a
    functional test that would have to cover every route and every future one.
    """

    SCORING_FILES = ("api.py", "student.py", "teacher.py", "deadline_service.py")

    def _sources(self):
        for name in self.SCORING_FILES:
            for path in (ROOT / "app").rglob(name):
                yield path

    def test_every_earned_points_call_hands_it_the_mode(self):
        offenders = []
        for path in self._sources():
            text = path.read_text(encoding="utf-8")
            for args in _call_arguments(text, "earned_points"):
                if "scoring" not in args:
                    offenders.append(f"{path.relative_to(ROOT)}: {args.strip()[:110]}")
        assert not offenders, (
            "a scoring site calls earned_points without the question's marking "
            "mode, so a teacher's 2/1/0 choice is graded all-or-nothing:\n  "
            + "\n  ".join(offenders))

    def test_the_exam_rows_those_sites_read_carry_the_column(self):
        """The other half. A select that reads the weights but not the modes reads
        the mode as *absent*, and absent means the default — so a paper marked 2/1/0
        is graded 1/0 with nothing anywhere saying why."""
        offenders = []
        for path in self._sources():
            text = path.read_text(encoding="utf-8")
            for args in _call_arguments(text, "select"):
                if "question_weights" in args and "question_scoring" not in args:
                    offenders.append(f"{path.relative_to(ROOT)}: {args.strip()[:110]}")
        assert not offenders, (
            "an exam select that reads the weights does not read the marking "
            "modes:\n  " + "\n  ".join(offenders))

    def test_the_scan_actually_reads_the_files_and_finds_the_calls(self):
        """A guard that reads nothing passes for ever."""
        calls = [args for path in self._sources()
                 for args in _call_arguments(path.read_text(encoding="utf-8"),
                                             "earned_points")]
        assert len(calls) >= 4, (
            f"the scan found {len(calls)} scoring calls, expected at least four — "
            "the files or the call shape have changed")


# ── 13. mode 1 at its boundary, stated as the rule and swept ─────────────────
#
# The gate for offering modes 2 and 3 is this: mode 1 has to be *proven* at the
# boundary before another mode is put in front of a teacher. So the AKM rule is
# written here as it is published — independently of the implementation — and
# asserted across the whole reachable range rather than at a few sample points,
# because "about half" is exactly what a marking rule must never be.

BAND_MIN, BAND_MAX = 3, 5
PARTIAL_WRONG = 2


def akm_rule(n_statements: int, n_categories: int, wrong: int) -> float:
    """Kemendikbud AKM, Mode 1, as the guidance states it.

    1/0 across a 3-5 statement, two-category question; 2/1/0 outside that band
    (2 = every statement right, 1 = one or two wrong, 0 = more than two wrong).
    """
    binary = BAND_MIN <= n_statements <= BAND_MAX and n_categories == 2
    if binary:
        return 1.0 if wrong == 0 else 0.0
    if wrong == 0:
        return 1.0
    return 0.5 if wrong <= PARTIAL_WRONG else 0.0


def _answer_with(n: int, wrong: int, correct: int = 0, wrong_category: int = 1):
    """`n` judgements, the last `wrong` of them deliberately not the key."""
    return [correct] * (n - wrong) + [wrong_category] * wrong


class TestModeOneAtTheBoundary:
    """The two-category band, swept over 1..10 statements."""

    @pytest.mark.parametrize("categories", [["Benar", "Salah"], ["Ya", "Tidak"],
                                            ["Sesuai", "Tidak Sesuai"]])
    @pytest.mark.parametrize("n", list(range(1, 11)))
    def test_every_wrong_count_matches_the_published_rule(self, n, categories):
        key = pgk_key(categories, [0] * n)
        for wrong in range(0, n + 1):
            got = qt.pgk_score("akm_standard", key, _answer_with(n, wrong))
            assert got == akm_rule(n, 2, wrong), (
                f"{n} statements, 2 categories, {wrong} wrong: got {got}")

    def test_the_band_ends_between_five_and_six_statements(self):
        """The single edge the brief names — and the one a teacher will meet most:
        one wrong statement costs the whole question at five, half of it at six."""
        five = pgk_key(["Benar", "Salah"], [0] * 5)
        six = pgk_key(["Benar", "Salah"], [0] * 6)
        assert qt.pgk_score("akm_standard", five, _answer_with(5, 0)) == 1.0
        assert qt.pgk_score("akm_standard", five, _answer_with(5, 1)) == 0.0
        assert qt.pgk_score("akm_standard", six, _answer_with(6, 0)) == 1.0
        assert qt.pgk_score("akm_standard", six, _answer_with(6, 1)) == 0.5

    def test_the_ladder_bottoms_out_after_two_wrong(self):
        six = pgk_key(["Benar", "Salah"], [0] * 6)
        assert qt.pgk_score("akm_standard", six, _answer_with(6, 2)) == 0.5
        assert qt.pgk_score("akm_standard", six, _answer_with(6, 3)) == 0.0
        assert qt.pgk_score("akm_standard", six, _answer_with(6, 6)) == 0.0


class TestModeOneWithMoreThanTwoCategories:
    """The other half of the band condition: never binary, whatever the length."""

    @pytest.mark.parametrize("n_categories", [3, 4, 5])
    @pytest.mark.parametrize("n", list(range(1, 11)))
    def test_the_ladder_applies_at_every_length(self, n, n_categories):
        categories = [f"kategori{i}" for i in range(n_categories)]
        key = pgk_key(categories, [0] * n)
        for wrong in range(0, n + 1):
            got = qt.pgk_score("akm_standard", key, _answer_with(n, wrong))
            assert got == akm_rule(n, n_categories, wrong), (
                f"{n} statements, {n_categories} categories, {wrong} wrong: got {got}")

    def test_a_four_statement_three_category_question_is_not_binary(self):
        """Inside the length band, and still the ladder — because the band is two
        conditions and not one."""
        key = pgk_key(["Hewan", "Tumbuhan", "Mikroorganisme"], [0, 1, 2, 0])
        assert qt.pgk_score("akm_standard", key, [0, 1, 2, 0]) == 1.0
        assert qt.pgk_score("akm_standard", key, [0, 1, 2, 1]) == 0.5
        assert qt.pgk_score("akm_standard", key, [1, 2, 0, 1]) == 0.0


# ── 14. the simulator the builder shows is the grader's own arithmetic ───────

class TestTheSimulator:
    """A simulator that agrees with the page instead of with the grader is worse
    than no simulator, so every row is the score of an answer built here."""

    def test_it_scores_the_four_canonical_patterns(self):
        key = pgk_key(["Benar", "Salah"], [0, 1, 0, 1, 0, 1])       # 6 -> ladder
        rows = qt.pgk_simulate(key)
        assert [r["pattern"] for r in rows] == [
            "all_right", "one_wrong", "two_wrong", "all_wrong"]
        assert {r["pattern"]: r["score"] for r in rows} == {
            "all_right": 1.0, "one_wrong": 0.5, "two_wrong": 0.5, "all_wrong": 0.0}

    def test_the_binary_band_simulates_as_a_cliff(self):
        key = pgk_key(["Benar", "Salah"], [0, 1, 0])                # 3 -> binary
        assert {r["pattern"]: r["score"] for r in qt.pgk_simulate(key)} == {
            "all_right": 1.0, "one_wrong": 0.0, "two_wrong": 0.0, "all_wrong": 0.0}

    def test_it_names_the_band_so_the_page_can_explain_the_cliff(self):
        assert qt.pgk_akm_band(pgk_key(["a", "b"], [0] * 5)) == "binary"
        assert qt.pgk_akm_band(pgk_key(["a", "b"], [0] * 6)) == "ladder"
        assert qt.pgk_akm_band(pgk_key(["a", "b", "c"], [0] * 4)) == "ladder"

    @pytest.mark.parametrize("n", [3, 4, 5, 6, 7, 8])
    def test_each_row_is_the_score_of_an_answer_this_test_builds(self, n):
        """Not a copy of the same expression: the row is re-derived from the
        pattern's own wrong-count and compared."""
        key = pgk_key(["Benar", "Salah"], [0] * n)
        for row in qt.pgk_simulate(key):
            answer = _answer_with(n, row["wrong"])
            assert row["score"] == qt.pgk_score("akm_standard", key, answer)
            assert row["wrong"] == n - row["right"]

    def test_it_reports_right_and_wrong_as_counts_a_teacher_can_tick_off(self):
        rows = {r["pattern"]: r for r in
                qt.pgk_simulate(pgk_key(["Benar", "Salah"], [0] * 6))}
        assert rows["all_right"]["right"] == 6 and rows["all_right"]["wrong"] == 0
        assert rows["one_wrong"]["right"] == 5 and rows["one_wrong"]["wrong"] == 1

    @pytest.mark.parametrize("junk", [{}, None, [], "", {"key": []}])
    def test_an_unreadable_key_simulates_nothing(self, junk):
        assert qt.pgk_simulate(junk) == []

    def test_the_simulator_is_the_grader_and_not_a_second_copy(self):
        """It must go through `pgk_score`; a second arithmetic is the defect this
        module exists to end."""
        source = (ROOT / "app" / "services" / "question_types.py").read_text(encoding="utf-8")
        body = source[source.index("def pgk_simulate"):]
        body = body[:body.index("\ndef ", 10)]
        assert "pgk_score(" in body, "the simulator must score through the grader"


class TestTheBuilderSimulatorUsesThisArithmetic:
    """The page must not re-implement the rule in JavaScript."""

    def test_the_route_delegates_to_the_one_implementation(self):
        """Read from the view's own body, not the whole file: `pgk_simulate` in an
        import line above a hand-rolled answer is exactly the defect this is for."""
        text = (ROOT / "app" / "routes" / "api.py").read_text(encoding="utf-8")
        assert "/pgk/simulate" in text, "the builder's simulator has no endpoint"
        start = text.index("def pgk_simulate_preview")
        body = text[start:text.index("\n@api_bp.route", start)]
        assert "pgk_simulate(shape)" in body, (
            "the endpoint must call the grader's own function, not re-derive it")
        assert "pgk_akm_band(shape)" in body, (
            "the band the page explains must come from the same grader")

    def test_the_page_computes_no_score_of_its_own(self):
        """The simulator renders the server's rows; it must not carry the AKM
        arithmetic (`0.5`, the 3-5 band) a second time."""
        page = (ROOT / "app" / "templates" / "teacher" / "exam_form.html").read_text(
            encoding="utf-8")
        start = page.index("pgkSimulate(i) {")
        body = page[start:page.index("\n        },", start)]
        assert "/api/pgk/simulate" in body, "the simulator stopped calling the server"
        assert "0.5" not in body, (
            "the AKM ladder is being recomputed in the page — that is the second "
            "arithmetic this endpoint exists to prevent")


# ── 15. the editor and the answer control exist, and are wired ───────────────

class TestTheEditorAndTheAnswerControl:
    """The type is creatable only because both surfaces exist — so both are
    asserted from the templates rather than trusted."""

    BUILDER = ROOT / "app" / "templates" / "teacher" / "exam_form.html"
    PAPER = ROOT / "app" / "templates" / "student" / "take_exam.html"

    def test_the_builder_has_an_editor_for_the_kind(self):
        page = self.BUILDER.read_text(encoding="utf-8")
        assert "kindOf(q) === 'pgk'" in page, "the builder draws no PGK editor"
        # The key the editor writes must be the shape `pgk_key` reads, not a
        # lookalike — the two are compared by their shared field names.
        assert "statements:" in page and "categories:" in page and "key:" in page
        assert "PGK_MAX" in page and "PGK_MIN" in page, (
            "the 3-8 statement bounds are not enforced where a teacher can meet them")

    def test_the_builder_offers_only_the_akm_mode_for_now(self):
        """Modes 2 and 3 are deferred until mode 1 is proven, so a teacher must not
        be able to choose one — and the fixed line says so."""
        page = self.BUILDER.read_text(encoding="utf-8")
        editor = page[page.index("kindOf(q) === 'pgk'"):]
        editor = editor[:editor.index("kindOf(q) === 'essay'")]
        assert "Standar AKM" in editor, "the editor no longer names the fixed mode"
        assert "proportional" not in editor and "all_or_nothing" not in editor, (
            "a deferred mode is selectable in the editor")

    def test_the_paper_asks_one_judgement_per_statement(self):
        page = self.PAPER.read_text(encoding="utf-8")
        assert "qKind(i) === 'pgk'" in page, "the paper draws no PGK control"
        for method in ("pgkStatements(i)", "pgkCategories(i)", "pgkAnswer(i)",
                       "pgkPick(i", "pgkClear(i"):
            assert method in page, f"the answer control is missing {method}"

    def test_the_paper_never_shuffles_the_judgements(self):
        """A PGK key is *by index*, so reordering the categories would move the
        correct answer — unlike a choice question's letters, which are safe to
        shuffle because the key is a letter."""
        page = self.PAPER.read_text(encoding="utf-8")
        assert "randomize_options" in page                      # the feature exists
        around = page[page.index("randomize_options"):]
        around = around[:around.index("}") + 1]
        assert "pgk" not in around, (
            "PGK categories are being shuffled, which moves the correct answer")
