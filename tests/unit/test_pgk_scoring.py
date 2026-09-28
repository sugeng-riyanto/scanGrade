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

    #: Every file that marks a paper. `super_admin.py` (the OMR bench) and
    #: `omr_tasks.py` (the Celery worker) were added when the percentage a pupil is
    #: shown began to move with the share: those two write the same `score` column,
    #: so they must read the mode too, or a PGK is graded by the AKM default there
    #: whatever the teacher chose. Neither carries a `question_weights` select, so
    #: the column guard below stays satisfied by the two columns they already read.
    SCORING_FILES = ("api.py", "student.py", "teacher.py", "deadline_service.py",
                     "super_admin.py", "omr_tasks.py")

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

    def test_every_objective_result_call_hands_it_the_mode(self):
        """The same rule for the *other* grader.

        `objective_result` now awards a part-rule question its share, and a share is
        read through the mode — so this call has the identical silent failure: a
        caller that omits it grades a PGK by the default whatever the teacher chose,
        and the pupil's "MCQ Score" card moves to the wrong number.
        """
        offenders = []
        for path in self._sources():
            text = path.read_text(encoding="utf-8")
            for args in _call_arguments(text, "objective_result"):
                if "scoring" not in args:
                    offenders.append(f"{path.relative_to(ROOT)}: {args.strip()[:110]}")
        assert not offenders, (
            "an objective_result call omits the marking mode, so a PGK scores by "
            "the default whatever the teacher chose:\n  " + "\n  ".join(offenders))

    def test_the_objective_result_scan_finds_the_calls(self):
        """A guard that reads nothing passes for ever."""
        calls = [args for path in self._sources()
                 for args in _call_arguments(path.read_text(encoding="utf-8"),
                                             "objective_result")]
        assert len(calls) >= 8, (
            f"the scan found {len(calls)} objective_result calls, expected at least "
            "eight — the files or the call shape have changed")

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


# ── 12b. the percentage the pupil is shown moves with the share ──────────────
#
# `score` is the number behind the pupil's "MCQ Score" card and `final_score`
# behind "Final". A PGK used to be all-or-nothing in `score` — one statement short
# and the question counted as wrong — while the weighted total paid the AKM share,
# so a pupil read **Final 75 beside MCQ Score 50** for one sitting. Two numbers on
# one paper that disagree is a defect a parent will find, so the share the pupil
# earned in the weighted total has to be the share in this percentage too.
#
# The two facts that make this safe, and both are asserted here: a paper with no
# part-rule type is scored exactly as before (so no published mark moves), and the
# mode is what decides the share (a PGK in `proportional` mode earns its own
# fraction, not the AKM ladder's).

class TestTheShareReachesThePercentage:
    """`objective_result` awards a part-rule question its share, not a 0/1."""

    MODES = {"0": "akm_standard", "1": "akm_standard"}

    def _pgk_paper(self, wrong_on_q0, mode="akm_standard"):
        """Two six-statement ladder-band questions; the first is `wrong_on_q0`
        statements short."""
        key = {"0": bool_key([True] * 6), "1": bool_key([True] * 6)}
        q0 = [True] * (6 - wrong_on_q0) + [False] * wrong_on_q0
        answers = {"0": answer_from(q0), "1": answer_from([True] * 6)}
        modes = {"0": mode, "1": mode}
        types = {str(i): PGK for i in range(2)}
        return types, key, answers, modes

    def test_a_partly_right_pgk_earns_its_share_in_the_percentage(self):
        types, key, answers, modes = self._pgk_paper(wrong_on_q0=1)
        result = qt.objective_result(types, key, answers, 2, modes)
        assert result.correct == 1, "only one of the two questions is wholly right"
        assert result.out_of == 2 and result.credit == pytest.approx(1.5), (
            "the numerator has to carry the half the pupil actually earned")
        assert result.score == pytest.approx(75.0), (
            "a pupil one statement short on one of two questions was paid 50% while "
            "the weighted total paid 75 — the two cards must agree on one paper")

    def test_a_paper_without_a_part_rule_is_scored_exactly_as_before(self):
        """The marks already in students' hands must not move.

        No PGK, so `credit` is the count of correct questions and `score` is the
        expression that has marked every paper so far.
        """
        types = {str(i): qt.MCQ for i in range(10)}
        key = {str(i): "A" for i in range(10)}
        seven = {str(i): "A" for i in range(7)}
        result = qt.objective_result(types, key, seven, 10)
        assert result.score == 70.0 and result.correct == 7
        assert result.credit == 7.0, (
            "an all-or-nothing answer is worth one whole question in the numerator")

    def test_the_binary_band_still_costs_the_whole_question(self):
        """Five statements, two categories: one mistake is the AKM cliff, not a share."""
        key = {"0": bool_key([True] * 5)}
        answers = {"0": answer_from([True] * 4 + [False])}
        result = qt.objective_result({"0": PGK}, key, answers, 1,
                                      {"0": "akm_standard"})
        assert result.score == 0.0, (
            "the binary band earns nothing for one mistake — the cliff the builder's "
            "simulator shows has to be the cliff the pupil gets")

    def test_one_mistake_at_six_statements_earns_half_in_the_percentage(self):
        key = {"0": bool_key([True] * 6)}
        answers = {"0": answer_from([True] * 5 + [False])}
        result = qt.objective_result({"0": PGK}, key, answers, 1,
                                      {"0": "akm_standard"})
        assert result.score == 50.0

    def test_the_mode_is_consulted_rather_than_the_akm_default(self):
        """A mode the percentage ignores is a mode the teacher did not pick."""
        key = {"0": bool_key([True] * 6)}
        answers = {"0": answer_from([True] * 5 + [False])}  # 5 right, 1 wrong
        got = {
            mode: qt.objective_result({"0": PGK}, key, answers, 1, {"0": mode}).score
            for mode in ("akm_standard", "proportional", "all_or_nothing")
        }
        assert got["akm_standard"] == 50.0
        # `score` is rounded to two places, so the reference is rounded too.
        assert got["proportional"] == pytest.approx(round((5 - 1) / 6 * 100, 2))
        assert got["all_or_nothing"] == 0.0

    def test_a_matching_question_earns_no_share_without_a_scheme(self):
        """The freeze, kept beside the PGK tests because it is the other half.

        A matching (or drag & drop, or ordering) question earns a share only when the
        *paper* carries a scheme. No paper here does, so it stays all-or-nothing in
        this percentage — letting it earn its half would silently move every mark
        already returned to a pupil, which is the one thing this change must not do.
        """
        result = qt.objective_result(
            {"0": "match"}, {"0": {"1": "b", "2": "a"}},
            {"0": {"1": "b", "2": "c"}}, 1)
        assert result.score == 0.0, (
            "one of two pairs right earned a share with no scheme on the paper")

    def test_the_two_cards_agree_on_a_pgk_paper_with_equal_weights(self):
        """The statement of the whole request, as one equation.

        `score` (the "MCQ Score" card) and the weighted total behind `final_score`
        (the "Final" card) are the same fraction of one paper, so a 50/50 paper puts
        the same number on both.
        """
        types, key, answers, modes = self._pgk_paper(wrong_on_q0=1)
        percent = qt.objective_result(types, key, answers, 2, modes).score
        weights = {"0": 50.0, "1": 50.0, qt.SCHEME_KEY: {"partial": False}}
        earned, _graded = qt.earned_points(types, key, answers, weights, 2, modes)
        assert percent == pytest.approx(earned) == pytest.approx(75.0), (
            "the 'MCQ Score' percentage and the weighted 'Final' disagree on one "
            "paper — the defect this change exists to end")


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


# ── 16. the review a pupil and a teacher read, statement by statement ────────

def review_of(key, answer, mode="akm_standard"):
    """The one call both review surfaces make, in the grader's argument order."""
    return qt.pgk_review(mode, key, answer)


class TestThePerStatementReview:
    """`pgk_review` explains one marked answer judgement by judgement.

    Every number it prints is one the grader produced — including the
    counterfactual ("what would this answer earn if only this one judgement were
    right"), which is how a pupil sees that the same single slip costs the whole
    question at five statements and half of it at six. The counterfactual is a
    call to `pgk_score`, not a rule written a second time: a review that agreed
    with itself instead of with the marking engine would tell a pupil the wrong
    thing about the paper in front of them.
    """

    def test_one_row_per_judgement_with_the_labels_the_pupil_saw(self):
        key = pgk_key(["Benar", "Salah"], [0, 1, 0], ["A", "B", "C"])
        review = review_of(key, [0, 0, 0])
        rows = review["statements"]
        assert [r["number"] for r in rows] == [1, 2, 3]
        assert [r["text"] for r in rows] == ["A", "B", "C"]
        assert [r["right"] for r in rows] == [True, False, True]
        assert [r["chosen_label"] for r in rows] == ["Benar", "Benar", "Benar"]
        assert [r["correct_label"] for r in rows] == ["Benar", "Salah", "Benar"]
        assert (review["right"], review["misses"]) == (2, 1)

    def test_the_share_it_prints_is_the_grader_s_share(self):
        for n, wrong in ((3, 1), (5, 1), (6, 2), (6, 3), (8, 4)):
            key = pgk_key(["Benar", "Salah"], [0] * n)
            ans = _answer_with(n, wrong)
            review = review_of(key, ans)
            assert review["share"] == qt.pgk_score("akm_standard", key, ans)
            assert review["misses"] == wrong

    def test_at_five_statements_one_slip_is_the_whole_question(self):
        key = pgk_key(["Benar", "Salah"], [0] * 5)
        review = review_of(key, _answer_with(5, 1))
        assert review["band"] == "binary" and review["share"] == 0.0
        wrong_rows = [r for r in review["statements"] if not r["right"]]
        assert [r["would_share"] for r in wrong_rows] == [1.0]
        assert [r["gain"] for r in wrong_rows] == [1.0], (
            "repairing the only wrong judgement is exactly what the 1/0 band pays for")

    def test_at_five_with_two_slips_no_single_repair_is_enough(self):
        """The honest half of the cliff: with two wrong, fixing either one alone
        still scores nothing — the band pays only for a clean paper."""
        key = pgk_key(["Benar", "Salah"], [0] * 5)
        review = review_of(key, _answer_with(5, 2))
        assert review["share"] == 0.0
        assert [r["gain"] for r in review["statements"] if not r["right"]] == [0.0, 0.0]
        assert review["wrong_before_fall"] == 0

    def test_at_six_a_single_repair_reaches_the_middle_rung(self):
        key = pgk_key(["Benar", "Salah"], [0] * 6)
        review = review_of(key, _answer_with(6, 3))
        assert review["band"] == "ladder" and review["share"] == 0.0
        missed = [r for r in review["statements"] if not r["right"]]
        assert [r["would_share"] for r in missed] == [0.5] * 3
        assert [r["gain"] for r in missed] == [0.5] * 3, (
            "three wrong at six is zero, and repairing any one of them reaches the middle rung")

    def test_the_rungs_are_probed_through_the_grader(self):
        six = pgk_key(["Benar", "Salah"], [0] * 6)
        review = review_of(six, _answer_with(6, 0))
        assert [(r["wrong_max"], r["share"]) for r in review["rungs"]] == [
            (0, 1.0), (2, 0.5), (6, 0.0)]
        assert review["rung"] == 0 and review["wrong_before_fall"] == 0, (
            "a clean paper is already on the top rung")

        middle = review_of(six, _answer_with(6, 1))
        assert (middle["rung"], middle["wrong_before_fall"]) == (1, 1), (
            "one wrong at six may take one more before the share falls to zero")

        five = pgk_key(["Benar", "Salah"], [0] * 5)
        r5 = review_of(five, _answer_with(5, 1))
        assert [(x["wrong_max"], x["share"]) for x in r5["rungs"]] == [(0, 1.0), (5, 0.0)]
        assert r5["rung"] == 1 and r5["wrong_before_fall"] == 0

    def test_a_mode_other_than_akm_takes_its_own_rungs(self):
        """The ladder is read from the grader, so the day a second mode is
        selectable the review describes *that* mode rather than AKM's."""
        key = pgk_key(["Benar", "Salah"], [0] * 3)
        ans = _answer_with(3, 1)
        assert review_of(key, ans, "akm_standard")["share"] == 0.0
        prop = review_of(key, ans, "proportional")
        # (right - wrong) / total: two right, one wrong, three statements.
        assert prop["share"] == round(1 / 3, 4) == round(qt.pgk_score("proportional", key, ans), 4)
        assert [r["share"] for r in prop["rungs"]] == [1.0, round(1 / 3, 4), 0.0]
        assert [r["wrong_max"] for r in prop["rungs"]] == [0, 1, 3], (
            "the floor run starts at two wrong, where the correction has eaten the marks")

    def test_a_blank_row_is_not_a_wrong_judgement_but_still_costs_a_miss(self):
        """The page keeps them visually apart; the ladder does not — the grader
        cannot award a row nobody answered, and the review may not pretend it can."""
        key = pgk_key(["Benar", "Salah"], [0, 1, 0])
        answer = [0, None, 1]
        review = review_of(key, answer)
        rows = review["statements"]
        assert [r["blank"] for r in rows] == [False, True, False]
        assert [r["right"] for r in rows] == [True, False, False]
        assert (review["right"], review["wrong"], review["blank"]) == (1, 1, 1)
        assert review["misses"] == 2
        assert review["share"] == qt.pgk_score("akm_standard", key, answer)

    def test_an_unanswered_question_reviews_as_every_row_blank(self):
        key = pgk_key(["Benar", "Salah"], [0] * 5)
        review = review_of(key, "")
        assert review["available"] is True
        assert review["answered"] is False and review["unreadable"] is False
        assert all(r["blank"] for r in review["statements"])
        assert review["share"] == 0.0
        assert [r["gain"] for r in review["statements"]] == [0.0] * 5, (
            "on a 1/0 band one repaired row is still not a clean paper")

    @pytest.mark.parametrize("n", [3, 5, 6, 8])
    def test_the_share_agrees_with_the_grader_at_every_length(self, n):
        """The one invariant the review may never break, swept rather than sampled:
        an untouched question, a half-answered one and a wrong one all report the
        share the marking engine would award."""
        key = pgk_key(["Benar", "Salah"], [0] * n)
        for answer in ("", [None] * n, _answer_with(n, n // 2), _answer_with(n, n)):
            assert review_of(key, answer)["share"] == round(qt.pgk_score("akm_standard", key, answer), 4)

    def test_an_answer_that_is_not_one_judgement_per_statement_is_flagged(self):
        """A stored answer of the wrong width is a fact about the data, not a
        pupil's opinion — so it is named rather than quietly drawn as blanks."""
        key = pgk_key(["Benar", "Salah"], [0, 1, 0])
        review = review_of(key, [0, 1])
        assert review["available"] is True and review["unreadable"] is True
        assert all(r["blank"] for r in review["statements"])

    def test_a_key_that_cannot_be_read_reviews_as_nothing_rather_than_as_zero(self):
        assert review_of({}, [0, 1])["available"] is False
        assert review_of(pgk_key([], [0, 1]), [0, 1])["available"] is False
        assert review_of(pgk_key(["Benar", "Salah"], [0, 1], []), [0, 1])["available"] is False
        assert review_of(pgk_key(["Benar", "Salah"], [0, 1]) , [0, 1])["available"] is True

    @staticmethod
    def function_body(source: str, name: str) -> str:
        start = source.index(f"def {name}")
        end = source.find("\ndef ", start + 10)
        return source[start:end if end != -1 else len(source)]

    def test_the_review_is_not_a_second_grader(self):
        """Read from the functions' own bodies: a rule restated there is a rule that
        will disagree with the marks it is explaining."""
        source = (ROOT / "app" / "services" / "question_types.py").read_text(encoding="utf-8")
        assert "def pgk_review" in source, "the review surface has no builder"
        bodies = [self.function_body(source, name)
                  for name in ("pgk_band_rungs", "pgk_review")]
        assert sum(body.count("pgk_score(") for body in bodies) >= 3, (
            "the review must score through the grader — the rungs and each row's "
            "counterfactual are its calls")
        for body in bodies:
            assert "0.5" not in body, "the AKM rungs are being restated in the review"
            assert "/ total" not in body and "AKM_PARTIAL_WRONG" not in body, (
                "a marking rule has been copied out of `pgk_score`")


class TestTheReviewScreens:
    """Both surfaces read one builder, and each respects its own language rule."""

    STUDENT = ROOT / "app" / "templates" / "student" / "result_detail.html"
    TEACHER = ROOT / "app" / "templates" / "teacher" / "grade_detail.html"
    OPEN = "{# sg-pgk-review"
    CLOSE = "{# /sg-pgk-review #}"

    def block(self, page: Path) -> str:
        text = page.read_text(encoding="utf-8")
        assert text.count(self.OPEN) == 1 and text.count(self.CLOSE) == 1, (
            "the review block is not delimited exactly once")
        return text[text.index(self.OPEN):text.index(self.CLOSE)]

    @pytest.mark.parametrize("page", [STUDENT, TEACHER])
    def test_both_surfaces_ask_the_grader_for_the_breakdown(self, page):
        block = self.block(page)
        assert "q_pgk_review(" in block, f"{page.name} draws no per-statement review"
        assert "pgk.statements" in block, f"{page.name} does not draw the rows"
        # Both guards, in order: the breakdown is for a PGK question, and only for
        # a key the grader could read. Without the second, an unreadable key draws
        # an empty table next to a mark instead of saying nothing.
        assert "q_kind(qtype) == 'pgk'" in block[:block.index("q_pgk_review(")], (
            f"{page.name} would draw the breakdown for any question with a key")
        assert "pgk.available" in block[:block.index("pgk.statements")], (
            f"{page.name} would draw an empty table where the review has no answer")

    def test_the_pupil_sees_it_only_once_the_marks_are_released(self):
        """The key, statement by statement, is *the key*: drawn before the teacher
        releases the marks it hands over every correct judgement on the paper. The
        guard has to be the block's own — the row above it is a different branch,
        and the answer/key line beside it closes before this block begins."""
        block = self.block(self.STUDENT)
        guard = block[:block.index("q_pgk_review(")]
        assert "graded" in guard and "published" in guard, (
            "the breakdown would reveal per-statement marks before release")

    @pytest.mark.parametrize("page", [STUDENT, TEACHER])
    def test_the_breakdown_table_scrolls_on_a_phone(self, page):
        """Four columns in a card that clips hides the key off-screen on a phone.

        This is how the defect was found: the table was placed in a card that
        clipped, and the suite-wide rule in `test_mobile_layout.py` could not see
        it — that rule asked whether a `print-only` marker appeared anywhere before
        a table, and this page opens one 170 lines higher for the printed answer
        summary. The general rule now measures containment instead (see
        `test_the_review_table_is_not_excused_by_the_print_block_above_it` there),
        so this table *is* covered twice. The local copy stays because it is the
        feature's own contract, reading the block between its own markers on both
        surfaces, and it does not move if the general rule's exemption list ever
        grows to include this page.
        """
        block = self.block(page)
        at = block.index("<table")
        assert "overflow-x-auto" in block[max(0, at - 900):at], (
            f"{page.name}'s breakdown clips its answer columns instead of scrolling")
        opening = block[at:block.index(">", at) + 1]
        assert "min-w-[" in opening, (
            f"{page.name}'s breakdown scrolls but has no minimum width, so a phone "
            "squeezes the four columns instead of scrolling them")

    def test_the_pupil_copy_is_bilingual_because_that_page_toggles(self):
        block = self.block(self.STUDENT)
        assert "lang==='en'" in block, (
            "new copy on a toggling page must be a pair, or the coverage floor drops")

    def test_the_teacher_copy_is_not_paired_because_that_page_is_pinned(self):
        """`grade_detail.html` declares `content_lang = 'id'` — a `t()` pair there
        is copy that can never switch, which the language gate refuses.

        Matched as a *call*, not as the substring `t('`: every `…get('key')` in the
        block carries those three characters, so the crude form fails on the review
        even when it is written in one language, which is the shape of a guard that
        gets deleted rather than obeyed."""
        text = self.TEACHER.read_text(encoding="utf-8")
        assert "content_lang = 'id'" in text
        block = self.block(self.TEACHER)
        assert "lang==='en'" not in block, "a toggle ternary on a page with no toggle"
        pair_call = re.compile(r"(?<![\w.])s?g?t\(\s*'[^']*'\s*,\s*'[^']*'\s*\)", re.I)
        assert not pair_call.search(block), "dead bilingual copy on a page with no toggle"
