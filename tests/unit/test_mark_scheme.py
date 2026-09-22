"""A paper's marks: what each question is worth, and the total being 100.

Three things are being held here, and they are different kinds of claim:

* **the total is exact.** The two-pool model this replaces is percentages, so three
  essay questions behind 70% are 23.33 points each and the paper totals 99.99. A
  score out of 100 has to be out of 100, which is only true if the parts are handed
  out in whole steps and the leftover steps go somewhere deliberate.
* **part-marks only where a paper asked for them.** Scoring here is *retroactive*: a
  result is recomputed from the stored answers whenever marks are published or
  recalculated. So an exam with no scheme must score exactly what it scored
  yesterday — that is asserted against the shipped expression, not described.
* **one authority.** The builder carries a JavaScript copy of the arithmetic so the
  total moves while a teacher types. The two implementations are run over the same
  inputs here and compared; the save route recomputes the numbers in Python and
  ignores what the page posted, so the copy can only ever be a wrong preview.
"""
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from app.services import mark_scheme as ms                                    # noqa: E402
from app.services import question_types as qt                                 # noqa: E402
from app.routes.teacher import _apply_mark_scheme                             # noqa: E402

EXAM_FORM = ROOT / "app" / "templates" / "teacher" / "exam_form.html"
TEACHER_ROUTE = ROOT / "app" / "routes" / "teacher.py"

NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="needs node to run the page's copy")


def source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def extract_function(text: str, name: str) -> str:
    """`function name(…) { … }` — to its matching brace."""
    start = text.index(f"function {name}(")
    depth = 0
    for i in range(text.index("{", start), len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    raise AssertionError(f"{name} is unterminated")


def extract_method(text: str, name: str) -> str:
    """`name(args) { … }` — a shorthand method on the Alpine object, to its brace.

    A different shape from `extract_function`: the exam builder's own methods are
    written the shorthand way, so `function keyFor(` appears nowhere and looking for
    it finds nothing rather than failing loudly.
    """
    start = re.search(rf"\n\s*{name}\([^)]*\)\s*\{{", text)
    assert start, f"{name} is not on the Alpine object any more"
    begin = start.start() + len(start.group(0)) - 1
    depth = 0
    for i in range(begin, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                # The whole match — name, parameters and body — with `function`
                # in front, which is what makes it callable on its own.
                return "function " + text[start.start():i + 1].lstrip()
    raise AssertionError(f"{name} is unterminated")


def extract_constant(text: str, name: str) -> str:
    """`const NAME = 0.1;` — the literal, so the harness uses the page's value."""
    marker = f"const {name} = "
    start = text.index(marker) + len(marker)
    return text[start:text.index(";", start)].strip()


def js_normalise(cases: list[list[float]]) -> list[list[float]]:
    """The page's own `sgNorm100`, run over the same inputs as the Python one."""
    script = "\n".join([
        # The template's own constant, read out of the template rather than assumed,
        # so a page that changes its step fails here instead of quietly disagreeing.
        "const SG_MARK_STEP = " + extract_constant(source(EXAM_FORM), "SG_MARK_STEP") + ";",
        extract_function(source(EXAM_FORM), "sgNorm100"),
        f"const cases = {json.dumps(cases)};",
        "console.log(JSON.stringify(cases.map(c => sgNorm100(c))));",
    ])
    done = subprocess.run([NODE, "-e", script], capture_output=True, text=True,
                          timeout=60)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout.strip())


# ── the builder writes the key the reader reads ──────────────────────────────

class TestTheBuildersKeyIsTheGradersKey:
    """The builder's `keyFor` and the grader's `normalise_key`, on the same rows.

    A key the page writes in a shape the reader does not know is a question that
    scores zero for every pupil, silently, and the shape is per *type* — so this is
    the question "does the page's key for an ordering question look like the key the
    grader expects for one". It is run, not read: the function is lifted out of the
    template and called with the real vocabulary, because a source check would pass
    on a branch that names a type and returns the wrong object for it.
    """

    QUESTIONS = [
        {"type": "mcq", "answers": ["A"], "bonus": False},
        {"type": "mcq", "answers": [], "bonus": True},
        {"type": "true_false", "tf": "false"},
        {"type": "match", "pairs": [{"l": "Ibu kota", "r": "Jakarta"}],
         "extra": ["Bandung"]},
        {"type": "drag_drop", "order": ["i", "love", "this"], "extra": []},
        {"type": "order", "order": ["Pertama", "Kedua", "Ketiga"],
         "extra": ["Penyeleweng"]},
        {"type": "essay_canvas"},
    ]

    @needs_node
    def test_every_key_the_page_writes_is_the_one_the_grader_reads(self):
        text = source(EXAM_FORM)
        script = "\n".join([
            "const SG_QT = " + json.dumps(qt.vocabulary()) + ";",
            "globalThis.kindOf = q => SG_QT.kinds[q.type] || SG_QT.kinds[SG_QT.default];",
            extract_method(text, "keyFor"),
            "const qs = " + json.dumps(self.QUESTIONS) + ";",
            "console.log(JSON.stringify(qs.map(q => keyFor(q))));",
        ])
        done = subprocess.run([NODE, "-e", script], capture_output=True, text=True,
                              timeout=60)
        assert done.returncode == 0, done.stderr
        keys = json.loads(done.stdout.strip())

        for question, key in zip(self.QUESTIONS, keys):
            qtype = question["type"]
            if qt.is_objective(qtype):
                assert qt.normalise_key(qtype, key) == key, (
                    f"the page's key for {qtype} is not the shape the reader "
                    f"stores: {key!r} — saving this question would grade it wrong"
                )
                assert qt.key_has_answer(qtype, key), (
                    f"the page wrote a {qtype} key with no answer in it: {key!r}"
                )
                if qtype not in ("mcq", "true_false"):
                    # …and the pupil's page has to be *answerable*: the key is
                    # what `public_options` builds the columns and the chip bank
                    # from, so a key nothing can be answered against is a question
                    # that cannot be sat.
                    assert qt.public_options(qtype, key), (
                        f"a {qtype} key gives the pupil nothing to answer with: "
                        f"{key!r}"
                    )
            else:
                # One marker, whichever name the page used: a teacher marks it.
                assert qt.is_essay_marker(key), (
                    f"a {qtype} question has to store an essay marker, not {key!r}"
                )

    @needs_node
    def test_an_ordering_question_is_not_saved_as_an_essay(self):
        """Stated on its own because it is the failure with no symptom: the question
        renders, the teacher sees their sequence, and every pupil scores zero."""
        text = source(EXAM_FORM)
        script = "\n".join([
            "const SG_QT = " + json.dumps(qt.vocabulary()) + ";",
            "globalThis.kindOf = q => SG_QT.kinds[q.type] || SG_QT.kinds[SG_QT.default];",
            extract_method(text, "keyFor"),
            "console.log(JSON.stringify(keyFor({type: 'order', order: ['a', 'b']})));",
        ])
        done = subprocess.run([NODE, "-e", script], capture_output=True, text=True,
                              timeout=60)
        assert done.returncode == 0, done.stderr
        key = json.loads(done.stdout.strip())
        assert key == {"order": ["a", "b"]}, key
        assert qt.grade_answer("order", key, ["a", "b"]) is True


# ── the total is exact, always ───────────────────────────────────────────────

class TestThePaperTotalsExactly100:
    def test_any_count_and_any_marks_add_up(self):
        """1..80 questions, at five different mark values each, must total 100."""
        for n in range(1, 81):
            for marks in (1.0, 2.5, 3.0, 7.7, 0.5):
                points = ms.normalise_to_100([marks] * n)
                assert abs(sum(points) - 100.0) < 1e-9, (n, marks, sum(points))

    def test_the_old_model_is_what_produced_99_99(self):
        """The defect this replaces, stated as arithmetic: 70% over three questions."""
        old = [round(70.0 / 3, 2)] * 3
        assert round(sum(old), 2) == 69.99
        assert sum(ms.normalise_to_100([70.0, 70.0, 70.0])) == 100.0

    def test_a_paper_already_totalling_100_is_left_alone(self):
        """20 MCQ at 3 and one essay at 40 is a scheme a teacher can write by hand."""
        points = ms.normalise_to_100([3.0] * 20 + [40.0])
        assert points[:20] == [3.0] * 20
        assert points[20] == 40.0

    def test_equal_questions_stay_equal(self):
        """Seven equal questions cannot be whole marks each — but they stay equal,
        so no question is quietly worth more than its neighbour."""
        points = ms.normalise_to_100([1.0] * 7)
        assert max(points) - min(points) <= ms.MARK_STEP + 1e-9
        assert abs(sum(points) - 100.0) < 1e-9

    def test_every_share_is_a_whole_number_of_steps(self):
        for points in ms.normalise_to_100([1.0, 3.0, 10.0, 2.0]):
            assert abs(points / ms.MARK_STEP - round(points / ms.MARK_STEP)) < 1e-6

    def test_a_paper_of_nothing_is_not_a_division_by_zero(self):
        assert ms.normalise_to_100([]) == []
        assert ms.normalise_to_100([0.0, 0.0]) == [50.0, 50.0]

    def test_the_scheme_table_reports_both_numbers(self):
        """`marks` is what was typed and `scaled_each` what will be awarded: showing
        only the first is how a paper of 96 reads as a score out of 104."""
        types = {str(i): "mcq" for i in range(4)}
        types["4"] = "essay_canvas"
        table = ms.describe(types, 5)
        assert table["raw_total"] == 14.0            # 4 × 1 + 10
        assert table["total"] == 100.0
        rows = {r["type"]: r for r in table["rows"]}
        assert rows["mcq"]["count"] == 4 and rows["mcq"]["marks"] == 1.0
        # 4 of the 14 raw marks are the choice questions, so 4/14 of 100 is theirs —
        # and the essay's 10 raw marks are the other 71.4.
        assert rows["mcq"]["scaled_subtotal"] == 28.6
        assert rows["essay_canvas"]["scaled_subtotal"] == 71.4
        assert rows["mcq"]["scaled_each"] == pytest.approx(7.15)
        assert table["total"] == 100.0


# ── the page's copy agrees with the authority ────────────────────────────────

class TestThePagesCopyAgrees:
    @needs_node
    def test_the_two_implementations_agree_on_the_same_inputs(self):
        cases = [
            [1.0] * n for n in (1, 2, 3, 7, 11, 13, 40)
        ] + [
            [3.0] * 20 + [40.0],
            [1.0, 3.0, 10.0],
            [2.5, 2.5, 7.7, 0.5],
            [0.0, 0.0, 0.0],
            [0.5] * 33,
        ]
        mine = [ms.normalise_to_100(c) for c in cases]
        theirs = js_normalise(cases)
        assert mine == theirs, (
            "the builder's preview and the authority disagree:\n"
            f"  python: {mine}\n  page  : {theirs}")

    @needs_node
    def test_the_page_totals_100_too(self):
        for row in js_normalise([[1.0] * n for n in range(1, 40)]):
            assert abs(sum(row) - 100.0) < 1e-6

    def test_the_page_reads_the_defaults_from_its_own_nowhere(self):
        """The marks a type is worth are named once, in Python, and mirrored once,
        in the page — the mirror is what the agreement test above holds."""
        assert "SG_MARK_DEFAULTS" in source(EXAM_FORM)
        for name in ("mcq", "true_false", "match", "drag_drop", "order",
                     "essay_canvas"):
            assert name in source(EXAM_FORM)

    def test_every_type_has_a_row_and_its_own_default(self):
        """One row per type, one default per type — the standing rule.

        The model this replaced had a single "objective" budget, so a matching
        question was worth whatever was left after dividing by the number of choice
        questions beside it, and there was no number to write for ordering at all.
        """
        from app.services import question_types as qt
        for name in ms.SCHEME_ORDER:
            assert ms.default_marks(name) > 0, name
            assert qt.question_kind(name) in qt._KIND_LABELS, name
        assert ms.default_marks(qt.ORDER) == ms.default_marks(qt.DRAG_DROP), (
            "ordering and drag & drop answer the same kind of question, so they "
            "start on the same marks — a teacher can move either"
        )


# ── part-marks, and only where they were asked for ───────────────────────────

MATCH_KEY = {"pairs": [{"left": "a", "right": "1"}, {"left": "b", "right": "2"},
                       {"left": "c", "right": "3"}, {"left": "d", "right": "4"}]}


class TestPartMarks:
    def test_matching_pays_per_pair(self):
        three = {"pairs": MATCH_KEY["pairs"][:3]}
        assert qt.part_factor("match", MATCH_KEY, three) == pytest.approx(0.75)
        assert qt.part_factor("match", MATCH_KEY, MATCH_KEY) == 1.0
        assert qt.part_factor("match", MATCH_KEY, {"pairs": []}) == 0.0

    def test_a_wrong_pair_is_a_wrong_pair(self):
        swapped = {"pairs": [{"left": "a", "right": "9"}, *MATCH_KEY["pairs"][1:]]}
        assert qt.part_factor("match", MATCH_KEY, swapped) == pytest.approx(0.75)

    def test_a_distractor_in_the_answer_does_not_invent_a_mark(self):
        """A right pairing given twice — once with the distractor — is one pair."""
        doubled = {"pairs": [*MATCH_KEY["pairs"], {"left": "a", "right": "x"}]}
        assert qt.part_factor("match", MATCH_KEY, doubled) == 1.0

    def test_ordering_pays_per_position(self):
        """Four sentences, three in the right place: three quarters, where the
        all-or-nothing rule that shipped before gave it nothing."""
        key = {"order": ["A", "B", "C", "D"]}
        assert qt.part_factor("order", key, ["A", "B", "C", "D"]) == 1.0
        assert qt.part_factor("order", key, ["A", "B", "C", "x"]) == pytest.approx(0.75)
        assert qt.part_factor("order", key, ["D", "C", "B", "A"]) == 0.0
        assert qt.part_factor("order", key, ["A", "B"]) == pytest.approx(0.5)
        extra_key = {"pairs": MATCH_KEY["pairs"], "extra": ["x", "y"]}
        assert qt.part_factor("match", extra_key, {"pairs": []}) == 0.0

    def test_drag_and_drop_pays_per_position(self):
        key = {"order": ["i", "love", "this", "app"]}
        assert qt.part_factor("drag_drop", key, ["i", "love", "this", "app"]) == 1.0
        assert qt.part_factor("drag_drop", key, ["i", "love", "app"]) == pytest.approx(0.5)
        assert qt.part_factor("drag_drop", key, ["app", "this", "love", "i"]) == 0.0

    def test_the_two_answer_types_are_all_or_nothing(self):
        assert qt.part_factor("mcq", "A", "A") == 1.0
        assert qt.part_factor("mcq", "A", "B") == 0.0
        assert qt.part_factor("true_false", "true", "true") == 1.0
        assert qt.part_factor("true_false", "true", "false") == 0.0

    def test_an_essay_earns_nothing_from_the_automatic_grader(self):
        """A teacher marks it; there is no key to be partly right about."""
        assert qt.part_factor("essay_canvas", "", {"text": "hi"}) == 0.0

    def test_the_marks_awarded_use_the_question_s_own_weight(self):
        weights = ms.with_scheme({"4": 20.0}, partial=True)
        earned, graded = qt.earned_points(
            {"4": "match"}, {"4": MATCH_KEY}, {"4": {"pairs": MATCH_KEY["pairs"][:3]}},
            weights, 5)
        assert (earned, graded) == (15.0, 1)


# ── the guarantee: an exam with no scheme scores what it always scored ───────

def shipped_earned(question_types, answer_key, answers, weights, total_questions):
    """`earned_points` as it shipped before schemes existed, written out here.

    Not a re-implementation to compare against itself: this is the expression the
    tests are *about*, so a change to the rule has to change this too, deliberately.
    """
    earned = 0.0
    for i in range(total_questions or 0):
        qi = str(i)
        qtype = (question_types or {}).get(qi, qt.DEFAULT_TYPE)
        key_value = (answer_key or {}).get(qi)
        weight = float((weights or {}).get(qi, 0) or 0)
        if not qt.is_objective(qtype) or not key_value or weight <= 0:
            continue
        if qt.grade_answer(qtype, key_value, (answers or {}).get(qi)):
            earned += weight
    return round(earned, 2)


class TestAnExamWithoutASchemeIsUntouched:
    def _matrix(self):
        types = {"0": "mcq", "1": "mcq", "2": "true_false", "3": "match", "4": "drag_drop"}
        key = {"0": "A", "1": "B", "2": "true", "3": MATCH_KEY,
               "4": {"order": ["x", "y"]}}
        weights = {"0": 20.0, "1": 20.0, "2": 20.0, "3": 20.0, "4": 20.0}
        answers = [
            {},
            {"0": "A"},
            {"0": "A", "1": "B", "2": "false", "3": {"pairs": MATCH_KEY["pairs"][:2]},
             "4": ["x", "y"]},
            {"0": "C", "2": "true", "3": {"pairs": MATCH_KEY["pairs"][:3]},
             "4": ["y", "x"]},
            {"0": {"answer": "A"}, "1": ["B"], "4": ["x"]},
        ]
        return types, key, weights, answers

    def test_every_answer_scores_exactly_what_it_scored_yesterday(self):
        types, key, weights, answers = self._matrix()
        for answer in answers:
            mine, _ = qt.earned_points(types, key, answer, weights, 5)
            assert mine == shipped_earned(types, key, answer, weights, 5), answer

    def test_a_partly_right_matching_answer_earns_nothing_without_a_scheme(self):
        types, key, weights, answers = self._matrix()
        three_of_four = answers[3]
        earned, _ = qt.earned_points(types, key, three_of_four, weights, 5)
        assert earned == shipped_earned(types, key, three_of_four, weights, 5)

    def test_a_scheme_switches_the_same_answer_to_part_marks(self):
        types, key, weights, answers = self._matrix()
        with_scheme = ms.with_scheme(weights, partial=True)
        frozen, _ = qt.earned_points(types, key, answers[3], weights, 5)
        paid, _ = qt.earned_points(types, key, answers[3], with_scheme, 5)
        assert paid > frozen, "the scheme is what makes the part-marks real"

    def test_part_marks_can_be_turned_off_for_a_paper_that_has_a_scheme(self):
        types, key, weights, answers = self._matrix()
        strict = ms.with_scheme(weights, partial=False)
        assert qt.earned_points(types, key, answers[3], strict, 5) == \
            qt.earned_points(types, key, answers[3], weights, 5)


# ── saving: the server owns the numbers ─────────────────────────────────────

class TestSaving:
    def test_a_paper_with_no_scheme_is_stored_exactly_as_posted(self):
        posted = {"0": 10.0, "1": 10.0, "2": 20.0}
        types = {"0": "mcq", "1": "mcq", "2": "essay_canvas"}
        assert _apply_mark_scheme(types, 3, dict(posted)) == posted

    def test_a_scheme_is_recomputed_and_the_page_s_numbers_are_ignored(self):
        """A form edited in devtools, or a cached page, must not set a paper's marks."""
        posted = ms.with_scheme({}, by_type={"match": 3}, partial=True)
        posted["0"] = 999.0
        types = {"0": "mcq", "1": "match", "2": "match", "3": "essay_canvas"}
        out = _apply_mark_scheme(types, 4, posted)
        assert out["0"] != 999.0
        assert abs(sum(v for k, v in out.items() if k != qt.SCHEME_KEY) - 100.0) < 1e-9
        assert out[qt.SCHEME_KEY]["by_type"]["match"] == 3.0

    def test_a_scheme_that_names_only_some_types_keeps_the_rest(self):
        """A scheme retyped in the Supabase console usually names one type.

        Merging the stored scheme into the new one is what keeps the other types at
        the marks that paper is marked by; without it they fall back to the defaults,
        which is a silent re-scoring of every question the teacher did not mention.
        """
        stored = {qt.SCHEME_KEY: {"by_type": {"essay_canvas": 25}, "partial": True,
                                  "version": 1}}
        out = ms.weights_for({"0": "match", "1": "essay_canvas"}, 2,
                             by_type={"match": 4}, existing=stored)
        assert out[qt.SCHEME_KEY]["by_type"]["match"] == 4.0
        assert out[qt.SCHEME_KEY]["by_type"]["essay_canvas"] == 25.0, (
            "the type the post did not mention kept the marks it had")

    def test_the_route_passes_the_stored_scheme_in(self):
        """The route is what makes the merge above reachable, so it is asserted too."""
        stored = ms.with_scheme({}, by_type={"match": 5, "essay_canvas": 25}, partial=True)
        assert stored[qt.SCHEME_KEY]["by_type"]["essay_canvas"] == 25
        assert _apply_mark_scheme({}, 2, stored)[qt.SCHEME_KEY]["by_type"]["match"] == 5.0

    def test_both_exam_saving_routes_ask_the_one_helper(self):
        """Two routes save an exam; a rule applied in one of them is a rule that
        stops being applied the first time someone edits the other."""
        text = source(TEACHER_ROUTE)
        assert text.count("_apply_mark_scheme(") == 3, (
            "one definition and two call sites expected")


# ── the scheme travels with the weights, and no reader trips on it ──────────

class TestTheStoredSchemeIsInvisibleToEveryReader:
    def test_the_reserved_key_is_not_a_question_index(self):
        assert not qt.SCHEME_KEY.isdigit()

    def test_default_weights_names_only_question_indexes(self):
        weights = ms.with_scheme({}, partial=True)
        assert all(k.isdigit() for k in weights if k != qt.SCHEME_KEY)

    def test_a_scheme_does_not_change_an_mcq_score_or_the_graded_count(self):
        types = {"0": "mcq", "1": "mcq"}
        key = {"0": "A", "1": "B"}
        answers = {"0": "A", "1": "C"}
        plain = {"0": 50.0, "1": 50.0}
        scheme = ms.with_scheme(plain, partial=True)
        assert qt.earned_points(types, key, answers, plain, 2) == \
            qt.earned_points(types, key, answers, scheme, 2)

    def test_the_key_has_answer_reader_ignores_it(self):
        """`key_has_answer` is asked per question, so a scheme key is never seen —
        pinned because a sweep over the weights would see it."""
        assert qt.key_has_answer("mcq", "A") is True
        assert qt.key_has_answer("mcq", None) is False

    def test_the_builder_seeding_skips_non_numeric_keys(self):
        """The page reads a stored paper's weights to show its current marks; the
        `_scheme` object is not a number and must not become one."""
        text = source(EXAM_FORM)
        assert "/^\\d+$/.test(String(i))" in text, (
            "the builder's seeding has to skip the scheme object")

    def test_the_scheme_names_a_version_and_the_partial_flag(self):
        scheme = ms.scheme_dict({"match": 4}, partial=False)
        assert scheme["version"] == 1
        assert scheme["partial"] is False
        assert scheme["by_type"]["match"] == 4.0
        assert set(scheme["by_type"]) >= set(ms.SCHEME_ORDER)


# ── what a teacher and a student are shown ───────────────────────────────────

GRADE_DETAIL = ROOT / "app" / "templates" / "teacher" / "grade_detail.html"
RESULT_DETAIL = ROOT / "app" / "templates" / "student" / "result_detail.html"
REPORT_CARD = ROOT / "app" / "templates" / "print" / "report_card.html"


class TestTheMarksAreShownWhereTheyAreEarned:
    """A mark scheme a teacher cannot see is a mark scheme nobody checks.

    The three pages that print a mark ask the *scoring* functions for it —
    `q_part_factor` and `q_partial_credit` are `question_types.part_factor` and
    `.partial_credit`, the same objects `earned_points` calls — so a page cannot
    show a mark that does not add up to the score beside it.
    """

    def test_the_marking_page_shows_earned_of_total(self):
        text = source(GRADE_DETAIL)
        assert "q_part_factor(qtype, correct, student_ans) if q_partial" in text
        assert "q_partial_credit(exam.question_weights" in text
        assert "{{ q_earned }} / {{ q_marks }} poin" in text

    def test_a_partly_right_answer_is_flagged_as_partly_right(self):
        """Not red: three of four pairs right is neither full marks nor nothing, and
        the marking page used to show it as a cross beside the question's full marks."""
        text = source(GRADE_DETAIL)
        assert "{% set q_partly = q_factor > 0 and q_factor < 1 %}" in text
        assert "bg-amber-50 border-amber-200" in text
        assert "nilai sebagian" in text

    def test_the_student_s_result_page_shows_earned_of_total(self):
        text = source(RESULT_DETAIL)
        assert "q_part_factor(qtype, correct, student_ans) if q_partial" in text
        assert "q_partial_credit(question_weights)" in text
        assert "{{ q_earned }}" in text and "{{ q_marks }}" in text
        assert "part marks" in text, "the student is told their answer was part-right"

    def test_the_printed_sheet_shows_earned_of_total(self):
        text = source(REPORT_CARD)
        assert "q_part_factor(qtype, correct, student_ans) if q_partial" in text
        assert "<strong>{{ q_earned }}</strong> / {{ q_marks }}" in text

    def test_the_jinja_globals_are_the_scoring_functions_themselves(self, app):
        """Not lookalikes: the same objects, so the pages and the total cannot
        drift. Checked by identity, because a guard that only compares names would
        pass on a second implementation with the same name."""
        assert app.jinja_env.globals["q_part_factor"] is qt.part_factor
        assert app.jinja_env.globals["q_partial_credit"] is qt.partial_credit
        assert app.jinja_env.globals["q_correct"] is qt.grade_answer
