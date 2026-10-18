"""The three question types that fill the professional-assessment gap, and the
statement grid they did not duplicate.

Scoring is asserted against **hand arithmetic**, not against the implementation: every
part-credit number in this file is written out as a fraction a teacher could check on
paper, so a change that keeps the code self-consistent but moves the mark fails here.

Four things each type must have, and each has a test that would fail without it:

1. **a key shape the grader reads** — `normalise_key` is the one door, and an answer
   marked through `part_factor`/`grade_answer` is the same mark the paper reports;
2. **part credit with a stated formula** — matrix per row, statement-grid-multi per
   statement; the numeric type is deliberately *not* part-credit (one number is one
   judgement), and that is asserted too, because "add it to PARTIAL_TYPES" is the
   obvious-looking change that would pay a wrong short answer a share of its marks;
3. **a place in the mark scheme** — the new types take their own bucket, and a paper
   mixing them still normalises to exactly 100;
4. **the server, not the client, owns the structure** — `validate_answer_map` drops a
   row/column/statement index the question does not have, and both writers call it.

A numeric answer that matches nothing is a fourth kind of outcome — *needs review* — and
the tests below pin it as a decision rather than a wrong answer: `manual_review_map`
flags it, a blank is not flagged, and the automatic mark stays zero until a teacher acts.
"""
from __future__ import annotations

import contextlib
import re
from pathlib import Path

import pytest

from app.services import mark_scheme
from app.services import question_types as qt

ROOT = Path(__file__).resolve().parents[2]
TEMPLATES = ROOT / "app" / "templates"
TEACHER_PY = (ROOT / "app" / "routes" / "teacher.py").read_text(encoding="utf-8")
STUDENT_PY = (ROOT / "app" / "routes" / "student.py").read_text(encoding="utf-8")
API_PY = (ROOT / "app" / "routes" / "api.py").read_text(encoding="utf-8")
BUILDER = (TEMPLATES / "teacher" / "exam_form.html").read_text(encoding="utf-8")
EXAM_PAGE = (TEMPLATES / "student" / "take_exam.html").read_text(encoding="utf-8")
ANSWER_KEYS = (TEMPLATES / "teacher" / "answer_keys.html").read_text(encoding="utf-8")
GRADING_QUEUE = (TEMPLATES / "teacher" / "grading_queue.html").read_text(encoding="utf-8")
GRADE_DETAIL = (TEMPLATES / "teacher" / "grade_detail.html").read_text(encoding="utf-8")
GUIDE = (TEMPLATES / "guide" / "skor.html").read_text(encoding="utf-8")
DOC = ROOT / "docs" / "features" / "new-question-types.md"
MIGRATION = ROOT / "supabase" / "migrations" / "069_exam_stimuli.sql"

#: Every symbol the three types add. Named once, so a test that forgets one of them
#: is visible as a test that forgot one of them.
NEW_TYPES = (qt.NUMERIC, qt.MATRIX)


# ── 1. the keys ──────────────────────────────────────────────────────────────

class TestTheKeyShapes:

    def test_numeric_and_matrix_are_objective_types(self):
        for t in NEW_TYPES:
            assert qt.is_objective(t), f"{t} must be auto-marked"
            assert not qt.is_essay(t), f"{t} must not be sent to a human by default"

    def test_a_number_key_normalises_to_the_shape_the_grader_reads(self):
        posted = {"mode": "number", "value": "9,8", "tolerance": "0,2"}
        stored = qt.normalise_key(qt.NUMERIC, posted)
        assert stored["mode"] == "number"
        assert stored["value"] == pytest.approx(9.8), "an Indonesian comma is a decimal point"
        assert stored["tolerance"] == pytest.approx(0.2)

    def test_a_text_key_keeps_every_accepted_spelling_normalised(self):
        stored = qt.normalise_key(qt.NUMERIC, {"mode": "text",
                                               "accepted": [" Fotosintesis ", "fotosintesa", "fotosintesis"]})
        assert stored["mode"] == "text"
        assert stored["accepted"] == ["fotosintesis", "fotosintesa"], (
            "duplicates and whitespace must not make two spellings out of one")

    def test_a_matrix_key_clamps_a_row_to_one_column_in_single_mode(self):
        stored = qt.normalise_key(qt.MATRIX, {
            "columns": ["Benar", "Salah"], "mode": "single",
            "rows": [{"text": "Air mendidih di 100 C", "key": [0, 1]}]})
        assert stored["rows"][0]["key"] == [0], "one choice per row is the mode's whole point"
        assert stored["mode"] == "single"

    def test_a_matrix_row_pointing_outside_the_columns_is_dropped(self):
        stored = qt.normalise_key(qt.MATRIX, {
            "columns": ["A", "B"], "mode": "multi",
            "rows": [{"text": "x", "key": [0, 7]}]})
        assert stored["rows"][0]["key"] == [0], (
            "a column the question does not have can never be a correct answer")

    def test_an_unkeyed_question_reports_no_key(self):
        assert not qt.key_has_answer(qt.NUMERIC, {"mode": "number", "value": ""})
        assert not qt.key_has_answer(qt.MATRIX, {"columns": [], "rows": []})
        assert qt.key_has_answer(qt.NUMERIC, {"mode": "number", "value": 9.8})
        assert qt.key_has_answer(qt.MATRIX, {"columns": ["A"], "mode": "single",
                                            "rows": [{"text": "x", "key": [0]}]})


# ── 2. numeric marking ───────────────────────────────────────────────────────

class TestNumericMarking:

    #: (key, answer, expected) — the tolerance boundary written out by hand.
    CASES = (
        ({"mode": "number", "value": 9.8, "tolerance": 0.2}, "9.8", True),
        ({"mode": "number", "value": 9.8, "tolerance": 0.2}, "9,8", True),
        ({"mode": "number", "value": 9.8, "tolerance": 0.2}, "10", True),      # exactly on the edge
        ({"mode": "number", "value": 9.8, "tolerance": 0.2}, "10.01", False),  # a hair outside
        ({"mode": "number", "value": 9.8, "tolerance": 0.2}, "9.6", True),
        ({"mode": "number", "value": 9.8, "tolerance": 0.2}, "9.59", False),
        ({"mode": "number", "value": 1234.5, "tolerance": 0.0}, "1.234,5", True),
        ({"mode": "number", "value": 1234.5, "tolerance": 0.0}, "1,234.5", True),
        ({"mode": "number", "value": 12, "tolerance": 0.0}, "12.", True),      # a number pad's full stop
        ({"mode": "number", "value": 12, "tolerance": 0.0}, "1/2", False),     # a fraction is not a decimal
        ({"mode": "number", "value": 9.8, "tolerance": 0.2}, "9,8 cm", False),  # a unit left on
        ({"mode": "text", "accepted": ["fotosintesis"]}, "FOTOSÍNTESIS".lower(), False),
        ({"mode": "text", "accepted": ["fotosintesis"]}, "  Fotosintesis ", True),
        ({"mode": "text", "accepted": ["reaksi terang"]}, "reaksi   terang", True),
    )

    @pytest.mark.parametrize("key,answer,expected", CASES)
    def test_the_mark_matches_the_hand_calculation(self, key, answer, expected):
        assert qt.grade_answer(qt.NUMERIC, key, answer) is expected

    def test_a_numeric_question_is_not_part_credit(self):
        """One number is one judgement: a near miss earns nothing.

        The obvious-looking change — adding `numeric_short_answer` to
        `PARTIAL_TYPES` — would pay a wrong short answer a share of its marks, which
        no teacher asked for and no formula in the guide states.
        """
        assert qt.NUMERIC not in qt.PARTIAL_TYPES
        assert qt.part_factor(qt.NUMERIC, {"mode": "number", "value": 9.8, "tolerance": 0.2},
                              "10.5") == 0.0
        assert qt.part_factor(qt.NUMERIC, {"mode": "number", "value": 9.8, "tolerance": 0.2},
                              "9.8") == 1.0

    def test_a_blank_is_not_an_answer_and_an_empty_grid_scored_zero(self):
        key = {"mode": "number", "value": 9.8, "tolerance": 0.2}
        assert not qt.has_answer(qt.NUMERIC, "")
        assert not qt.has_answer(qt.NUMERIC, None)
        assert qt.has_answer(qt.NUMERIC, "9,8")


# ── 3. the review queue is a decision, not a wrong answer ────────────────────

class TestNeedsReview:

    KEY = {"mode": "number", "value": 9.8, "tolerance": 0.2}

    def test_a_miss_is_flagged_with_the_reason_that_says_what_to_do(self):
        assert qt.numeric_review_reason(self.KEY, "10.5") == "value"
        assert qt.numeric_review_reason(self.KEY, "9,8 cm") == "format"
        assert qt.numeric_review_reason({"mode": "text", "accepted": ["fotosintesis"]},
                                       "fotosintesa") == "spelling"
        assert qt.numeric_review_reason(self.KEY, "9.9") == ""

    def test_a_matching_answer_is_never_flagged(self):
        assert qt.numeric_review_reason(self.KEY, "9,8") == ""
        assert qt.manual_review_map({"0": qt.NUMERIC}, {"0": self.KEY}, {"0": "9,8"}, 1) == {}

    def test_a_blank_answer_is_not_review_work(self):
        """Nobody wrote anything, so there is nothing for a human to look at."""
        assert qt.numeric_review_reason(self.KEY, "") == ""
        assert qt.manual_review_map({"0": qt.NUMERIC}, {"0": self.KEY}, {"0": ""}, 1) == {}

    def test_the_map_carries_the_reason_and_the_answer(self):
        flags = qt.manual_review_map({"0": qt.NUMERIC}, {"0": self.KEY}, {"0": "10.5"}, 1)
        assert flags == {"0": {"reason": "value", "answer": "10.5"}}

    def test_only_numeric_questions_are_flagged(self):
        flags = qt.manual_review_map(
            {"0": qt.MCQ, "1": qt.NUMERIC}, {"0": "A", "1": self.KEY},
            {"0": "B", "1": "10.5"}, 2)
        assert set(flags) == {"1"}, "a wrong letter is a wrong answer, not a review job"


# ── 4. matrix part credit, by hand ───────────────────────────────────────────

class TestMatrixMarking:

    SINGLE = {"columns": ["Benar", "Salah"], "mode": "single",
              "rows": [{"text": "a", "key": [0]}, {"text": "b", "key": [1]},
                       {"text": "c", "key": [0]}, {"text": "d", "key": [1]}]}
    MULTI = {"columns": ["A", "B", "C"], "mode": "multi",
             "rows": [{"text": "a", "key": [0, 2]}, {"text": "b", "key": [1]}]}

    #: (key, answer, share) with the arithmetic written beside it.
    CASES = (
        (SINGLE, {"0": [0]}, 1 / 4),
        (SINGLE, {"0": [0], "1": [1]}, 2 / 4),
        (SINGLE, {"0": [0], "1": [1], "2": [0], "3": [1]}, 1.0),
        (SINGLE, {"0": [1], "1": [0], "2": [1], "3": [0]}, 0.0),
        (SINGLE, {}, 0.0),
        (MULTI, {"0": [0, 2]}, 1 / 2),
        (MULTI, {"0": [0]}, 0.0),               # one of two ticks: the row is wrong
        (MULTI, {"0": [0, 1, 2]}, 0.0),         # an extra wrong tick: the row is wrong
        (MULTI, {"0": [0, 2], "1": [1]}, 1.0),
        (MULTI, {"0": [0, 2], "1": [1, 0]}, 1 / 2),
    )

    @pytest.mark.parametrize("key,answer,share", CASES)
    def test_the_share_is_the_rows_right_over_the_rows_asked(self, key, answer, share):
        assert qt.matrix_score(key, answer) == pytest.approx(share)

    @pytest.mark.parametrize("key,answer,share", CASES)
    def test_part_factor_is_the_same_number(self, key, answer, share):
        assert qt.part_factor(qt.MATRIX, key, answer) == pytest.approx(share)

    def test_the_two_modes_read_the_same_answer_differently(self):
        """The mode is the question's, not the answer's."""
        one = {"columns": ["A", "B"], "mode": "single", "rows": [{"text": "x", "key": [0]}]}
        many = {"columns": ["A", "B"], "mode": "multi", "rows": [{"text": "x", "key": [0]}]}
        assert qt.matrix_score(one, {"0": [0, 1]}) == 0.0   # two ticks on a radio row
        assert qt.matrix_score(many, {"0": [0, 1]}) == 0.0  # a tick the key does not have
        assert qt.matrix_score(many, {"0": [0]}) == 1.0

    def test_a_matrix_question_is_right_only_when_every_row_is(self):
        assert qt.grade_answer(qt.MATRIX, self.MULTI, {"0": [0, 2], "1": [1]}) is True
        assert qt.grade_answer(qt.MATRIX, self.MULTI, {"0": [0, 2]}) is False

    def test_a_matrix_is_part_credit_regardless_of_the_paper_s_scheme(self):
        """Its own mode *is* a part rule, so it earns a share with no scheme stored."""
        assert qt.partial_applies(qt.MATRIX, None) is True
        assert qt.partial_applies(qt.NUMERIC, None) is False


# ── 5. the statement grid's second shape ─────────────────────────────────────

class TestTickEverythingThatIsTrue:

    KEY = {"select": "multi", "statements": ["a", "b", "c"], "key": [0, 2]}

    def test_the_share_is_the_statements_right_over_the_statements_asked(self):
        assert qt.pgk_score(None, self.KEY, [0, 2]) == pytest.approx(1.0)
        assert qt.pgk_score(None, self.KEY, [0]) == pytest.approx(2 / 3)
        assert qt.pgk_score(None, self.KEY, [1]) == pytest.approx(0.0)
        # Ticking nothing on a grid whose key names two statements is *one*
        # judgement right — the statement nobody claims to be true.
        assert qt.pgk_score(None, self.KEY, []) == pytest.approx(1 / 3)

    def test_every_statement_wrong_is_zero(self):
        assert qt.pgk_score(None, self.KEY, [1]) == pytest.approx(0.0)

    def test_all_wrong_when_the_key_is_empty(self):
        empty = {"select": "multi", "statements": ["a", "b"], "key": []}
        assert qt.pgk_score(None, empty, []) == pytest.approx(1.0), "nothing true, nothing ticked"
        assert qt.pgk_score(None, empty, [0]) == pytest.approx(0.5)

    def test_an_empty_tick_set_is_a_key_not_a_missing_key(self):
        assert qt.key_has_answer(qt.COMPLEX_MULTIPLE_CHOICE,
                                 {"select": "multi", "statements": ["a"], "key": []})
        assert not qt.has_answer(qt.COMPLEX_MULTIPLE_CHOICE, [])

    def test_the_mode_is_read_from_the_key_so_old_grids_keep_their_meaning(self):
        old = {"categories": ["Benar", "Salah"], "statements": ["a", "b"], "key": [0, 1]}
        assert qt.pgk_select(old) == qt.PGK_SELECT_SINGLE
        assert qt.pgk_select(self.KEY) == qt.PGK_SELECT_MULTI
        # The old shape is still graded by the AKM ladder, not by the new share.
        assert qt.pgk_score("akm", old, [0, 1]) >= 1.0


# ── 6. the buckets, and a paper that still totals 100 ────────────────────────

class TestTheMarkScheme:

    def test_every_new_type_has_a_row_and_a_default(self):
        for t in NEW_TYPES:
            assert t in mark_scheme.SCHEME_ORDER, f"{t} has no row in the scheme table"
            assert float(mark_scheme.DEFAULT_MARKS[t]) > 0

    def test_the_order_matches_the_builder_s_preview(self):
        """The page's own copy of these two lists is a preview of the server's.

        `test_mark_scheme.py` runs the two implementations against each other; this
        asserts the *membership* the new types add, which is what a stale copy in the
        template would silently lose.
        """
        order = re.search(r"const SG_SCHEME_ORDER = \[(.*?)\];", BUILDER, re.S)
        defaults = re.search(r"const SG_MARK_DEFAULTS = \{(.*?)\};", BUILDER, re.S)
        assert order and defaults
        for t in NEW_TYPES:
            assert f"'{t}'" in order.group(1), f"{t} missing from the builder's scheme rows"
            assert t in defaults.group(1), f"{t} missing from the builder's default marks"

    def test_a_paper_of_new_types_still_normalises_to_exactly_one_hundred(self):
        types = {"0": qt.NUMERIC, "1": qt.MATRIX, "2": qt.MCQ, "3": qt.ESSAY_CANVAS}
        weights = mark_scheme.build_weights(types, 4)
        assert sum(weights.values()) == pytest.approx(100.0)
        assert weights["1"] > weights["0"], (
            "a grid of several judgements must be worth more than one short answer")

    def test_the_scaled_points_are_the_ones_the_mark_is_computed_with(self):
        types = {"0": qt.NUMERIC, "1": qt.MATRIX}
        marks = {"0": 1.0, "1": 3.0}
        weights = mark_scheme.weights_for(types, 2, marks)
        key = {"0": {"mode": "number", "value": 5, "tolerance": 0},
               "1": {"columns": ["A", "B"], "mode": "single",
                     "rows": [{"text": "p", "key": [0]}, {"text": "q", "key": [1]}]}}
        # Half the grid right, the number right: 25 + 75/2 = 62.5 by hand.
        earned, graded = qt.earned_points(types, key, {"0": "5", "1": {"1": [1]}},
                                          weights, 2)
        assert graded == 2
        assert earned == pytest.approx(25 + 75 / 2)


# ── 7. the server owns the structure ────────────────────────────────────────

class TestTheServerOwnsTheStructure:

    KEY = {"columns": ["A", "B"], "mode": "single",
           "rows": [{"text": "p", "key": [0]}, {"text": "q", "key": [1]}]}

    def test_a_matrix_row_that_does_not_exist_is_dropped(self):
        clean, dropped = qt.validate_answer_map(
            {"0": qt.MATRIX}, {"0": self.KEY}, {"0": {"0": [0], "9": [1]}})
        assert clean["0"] == {"0": [0]}
        assert dropped and dropped[0]["reason"] == "no-such-row"

    def test_a_matrix_column_that_does_not_exist_is_dropped(self):
        clean, dropped = qt.validate_answer_map(
            {"0": qt.MATRIX}, {"0": self.KEY}, {"0": {"0": [0, 7]}})
        assert clean["0"] == {"0": [0]}
        assert any(d["reason"] == "no-such-column" for d in dropped)

    def test_a_single_row_cannot_hold_two_ticks_once_the_server_has_read_it(self):
        clean, _ = qt.validate_answer_map(
            {"0": qt.MATRIX}, {"0": self.KEY}, {"0": {"0": [0, 1]}})
        assert clean["0"]["0"] == [0], "the mode in the key decides, not the client"

    def test_a_statement_index_past_the_end_of_the_grid_is_dropped(self):
        key = {"select": "multi", "statements": ["a", "b"], "key": [0]}
        clean, dropped = qt.validate_answer_map(
            {"0": qt.COMPLEX_MULTIPLE_CHOICE}, {"0": key}, {"0": [0, 5]})
        assert clean["0"] == [0]
        assert any(d["reason"] == "no-such-statement" for d in dropped)

    def test_a_wall_of_text_is_not_a_short_answer(self):
        clean, dropped = qt.validate_answer_map(
            {"0": qt.NUMERIC}, {"0": {"mode": "number", "value": 1}}, {"0": "x" * 400})
        assert clean["0"] == ""
        assert any(d["reason"] == "not-a-short-answer" for d in dropped)

    def test_a_clean_paper_is_returned_untouched(self):
        clean, dropped = qt.validate_answer_map(
            {"0": qt.MATRIX}, {"0": self.KEY}, {"0": {"0": [0]}, "_device_info": {"ip": "1"}})
        assert dropped == []
        assert clean["0"] == {"0": [0]}
        assert clean["_device_info"] == {"ip": "1"}, "metadata keys are not answers"

    def test_both_writers_ask_the_validator(self):
        """A route that forgets it is a route where a pupil can invent a row."""
        assert "validate_answer_map(" in API_PY, "the autosave door does not validate"
        assert "validate_answer_map(" in STUDENT_PY, "the submit door does not validate"
        submit = STUDENT_PY[STUDENT_PY.index("def submit_exam"):]
        assert "validate_answer_map(" in submit.split("\ndef ")[0]
        sync = API_PY[API_PY.index("def student_sync_draft"):]
        assert "validate_answer_map(" in sync.split("\ndef ")[0]

    def test_the_submit_door_writes_the_review_flag(self):
        submit = STUDENT_PY[STUDENT_PY.index("def submit_exam"):]
        body = submit.split("\ndef ")[0]
        assert "manual_review_map(" in body
        assert '_needs_review' in body


# ── 8. the pupil's control, and the teacher's ───────────────────────────────

class TestTheScreens:

    def test_the_exam_page_draws_a_control_for_each_new_type(self):
        for kind in ("numeric", "matrix"):
            assert f"qKind(i) === '{kind}'" in EXAM_PAGE, f"no pupil control for {kind}"

    def test_the_numeric_box_opens_the_right_keyboard(self):
        assert "inputmode" in EXAM_PAGE and "decimal" in EXAM_PAGE, (
            "a number question must open a number pad on a phone")

    def test_the_matrix_is_cards_not_a_wide_table(self):
        """The row markup must not be a `<table>`: a grid that has to scroll
        sideways is a grid a phone cannot answer."""
        block = EXAM_PAGE[EXAM_PAGE.index("qKind(i) === 'matrix'"):]
        block = block[:block.index("qKind(i) === 'essay'")]
        assert "<table" not in block, "the pupil's matrix must stack, not scroll"
        assert "matrixPick(" in block

    def test_the_statement_grid_has_its_checkbox_shape_on_the_pupil_page(self):
        assert "pgkToggleTicked(" in EXAM_PAGE
        assert "pgkIsMulti(" in EXAM_PAGE

    def test_the_builder_shows_how_to_use_and_how_it_is_marked_for_each_type(self):
        for kind in ("numeric", "matrix"):
            block = BUILDER[BUILDER.index(f"kindOf(q) === '{kind}'"):]
            block = block[:block.index("kindOf(q) ===", 20)] if "kindOf(q) ===" in block[20:] else block
            assert "<details" in block and "Cara pakai" in block, (
                f"the {kind} editor has no help panel")
        stimulus_block = BUILDER[BUILDER.index("data-stimulus-panel"):]
        assert "Cara pakai" in stimulus_block[:6000]

    def test_the_builder_serialises_every_new_key_shape(self):
        key_for = BUILDER[BUILDER.index("keyFor(q)"):]
        key_for = key_for[:key_for.index("getAnswerKeyJson()")]
        assert "numMode" in key_for and "accepted" in key_for
        assert "mxColumns" in key_for or "mxRows" in key_for
        assert "pgkMultiKey" in key_for

    def test_the_answer_key_page_can_edit_a_short_answer(self):
        assert "data-numeric-value" in ANSWER_KEYS
        assert "numField(" in ANSWER_KEYS and "numAccepted(" in ANSWER_KEYS

    def test_a_review_flag_has_a_place_on_both_teacher_screens(self):
        assert "data-review-badge" in GRADING_QUEUE
        assert "data-review-queue" in GRADE_DETAIL
        assert "_review_items(" in TEACHER_PY
        assert "review_count" in TEACHER_PY

    def test_the_queue_badge_counts_the_stored_map(self):
        """Recomputing would resurrect a count a teacher has already fixed."""
        block = TEACHER_PY[TEACHER_PY.index('"review_count"'):]
        assert 'answers.get("_needs_review")' in block[:400]


# ── 9. stimulus ─────────────────────────────────────────────────────────────

class TestStimulus:

    def test_the_column_is_added_by_migration(self):
        sql = MIGRATION.read_text(encoding="utf-8")
        assert "ADD COLUMN IF NOT EXISTS question_stimuli JSONB" in sql
        assert "IF NOT EXISTS" in sql, "the migration must be idempotent"

    def test_the_exam_row_is_parsed_and_written(self):
        assert '"question_stimuli"' in TEACHER_PY
        assert "question_stimuli" in TEACHER_PY.split("JSON_COLUMNS =")[1][:200]

    def test_a_stimulus_is_never_a_question_of_its_own_type(self):
        """It is a container: no vocabulary entry, no bucket, no points."""
        assert not any("stimulus" in t for t in qt.PICKER_TYPES)
        assert not any("stimulus" in t for t in mark_scheme.SCHEME_ORDER)
        assert not any("stimulus" in str(k).lower() for k in qt.vocabulary()["kinds"])

    def test_the_pupil_page_shows_the_context_once_per_group(self):
        assert "stimulusStartsHere(" in EXAM_PAGE
        assert "data-stimulus-content" in EXAM_PAGE
        assert "stimulusFor(" in EXAM_PAGE

    def test_shuffling_moves_groups_not_sub_questions(self):
        """The shuffle must work on units, not on questions."""
        assert "self._shuffle(units)" in EXAM_PAGE, (
            "shuffling the questions one by one would split a stimulus apart")
        # And it must be built from the stimulus map, not from a hand-counted range.
        shuffle_block = EXAM_PAGE[EXAM_PAGE.index("self._shuffle(units)") - 1200:
                                EXAM_PAGE.index("self._shuffle(units)") + 200]
        assert "sgStimulusOf(" in shuffle_block
        assert "byStimulus" in shuffle_block

    def test_the_builder_keeps_a_stimulus_with_no_questions_out_of_the_row(self):
        assert "getStimuliJson() {" in BUILDER
        block = BUILDER[BUILDER.index("getStimuliJson() {"):]
        block = block[:block.index("getAudioJson()")]
        assert "idxs.length" in block, (
            "a context with no question must not be stored as an empty panel")


# ── 10. the older types are untouched ───────────────────────────────────────

class TestTheOldTypesStillBehave:

    def test_choice_truefalse_and_bonus_are_unchanged(self):
        assert qt.grade_answer(qt.MCQ, "A", "A") is True
        assert qt.grade_answer(qt.MCQ, "A", "B") is False
        assert qt.grade_answer(qt.TRUE_FALSE, "true", "true") is True
        assert qt.grade_answer(qt.MCQ, "bonus", "anything") is True

    def test_matching_and_ordering_keep_their_part_rules(self):
        key = {"pairs": [{"left": "a", "right": "1"}, {"left": "b", "right": "2"}]}
        assert qt.part_factor(qt.MATCH, key, [{"left": "a", "right": "1"}]) == pytest.approx(0.5)
        order = {"order": ["x", "y", "z"]}
        assert qt.part_factor(qt.ORDER, order, ["x", "y", "w"]) == pytest.approx(2 / 3)

    def test_a_grid_with_no_mode_field_is_still_a_single_judgement_per_row(self):
        key = {"categories": ["Benar", "Salah"], "statements": ["a", "b"], "key": [0, 1]}
        assert qt.pgk_score("akm", key, [0, 1]) >= 1.0
        assert qt.public_options(qt.COMPLEX_MULTIPLE_CHOICE, key) == {
            "statements": ["a", "b"], "categories": ["Benar", "Salah"],
            "select": qt.PGK_SELECT_SINGLE}

    def test_a_student_never_receives_a_numeric_or_matrix_answer(self):
        num = qt.public_options(qt.NUMERIC, {"mode": "number", "value": 9.8, "tolerance": 0.2})
        assert num == {"mode": "number"}, "the correct value must not reach the page"
        mat = qt.public_options(qt.MATRIX, {"columns": ["A", "B"], "mode": "single",
                                            "rows": [{"text": "p", "key": [0]}]})
        assert mat is not None
        assert "key" not in mat
        assert all("key" not in row for row in mat["rows"]), (
            "a pupil's page receives the rows, never which option is right")


# ── 11. the documentation exists, in both places ────────────────────────────

class TestTheDocumentation:

    def test_the_feature_doc_covers_every_type_and_formula(self):
        text = DOC.read_text(encoding="utf-8")
        for token in ("numeric_short_answer", "matrix", "question_stimuli",
                      "SELECT", "toleransi", "baris tepat", "Perlu Ditinjau"):
            assert token.lower() in text.lower(), f"the doc does not mention {token}"

    def test_the_guide_tells_teachers_and_pupils_about_the_new_types(self):
        assert "Tipe Soal Baru" in GUIDE
        assert "Menjawab Soal Angka" in GUIDE
        assert "baris tepat / jumlah baris" in GUIDE, "the matrix formula must be written out"

    def test_the_guide_is_bilingual_like_the_page_it_lives_in(self):
        for pair in ("Numeric / Short answer", "Matrix / Grid", "Stimulus"):
            assert pair in GUIDE, f"the guide's English half is missing {pair}"
