"""The question vocabulary, the grading, and the reason both live in one file.

Three new question types are only safe if "which questions does the app mark by
itself" is answered in **one** place. Before this module it was answered seven
times, as literal comparisons — `qtype == "mcq"` for the weight pools, and
`v not in ("essay", "essay_text", "essay_canvas")` for the counts. A new objective
type is not `"mcq"`, so every one of those seven places would have classified it
as an essay: out of the auto-graded pool, absent from `mcq_count`, worth nothing,
and already published in the pupil's result before anyone noticed.

So this file pins three things:

* **the truth table** — for every type, what counts as right, including the forms
  a real pupil produces (a `bool` from a radio button, a wrapper object off the
  offline queue, a set of pairs in another order).
* **the vocabulary** — an unknown or missing type reads as `mcq`, which is what
  the routes defaulted to, so an exam written before these types existed scores
  exactly as it did.
* **the one-place rule** — the source scan that fails when any scoring site
  reintroduces its own comparison. That is the guard the whole file exists for.
"""
import re
from pathlib import Path

import pytest

from app.services import question_types as qt

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "app"


# ── the vocabulary ───────────────────────────────────────────────────────────

class TestTheVocabulary:
    @pytest.mark.parametrize("name", qt.OBJECTIVE_TYPES)
    def test_every_objective_type_is_auto_graded(self, name):
        assert qt.is_objective(name)
        assert qt.pool(name) == "objective"

    @pytest.mark.parametrize("name", qt.ESSAY_TYPES)
    def test_every_essay_type_needs_a_teacher(self, name):
        assert qt.is_essay(name)
        assert qt.pool(name) == "essay"

    def test_the_two_families_do_not_overlap(self):
        assert not set(qt.OBJECTIVE_TYPES) & set(qt.ESSAY_TYPES)

    @pytest.mark.parametrize("raw", [None, "", "  ", "true_or_false", 7, {}, []])
    def test_an_unrecognised_type_reads_as_mcq(self, raw):
        """`mcq` is what the routes have always defaulted to, on purpose.

        An exam stored before these types existed carries no type for the
        question it did not know about, and defaulting to anything else would
        change what it scores.
        """
        assert qt.canonical_type(raw) == "mcq"
        assert qt.is_objective(raw)

    @pytest.mark.parametrize("raw,expected", [
        ("TRUE_FALSE", "true_false"),
        ("  Match ", "match"),
        ("Drag_Drop", "drag_drop"),
        ("essay", "essay"),
    ])
    def test_a_stored_name_is_read_case_and_space_insensitively(self, raw, expected):
        assert qt.canonical_type(raw) == expected

    def test_the_essay_marker_is_what_the_key_stores(self):
        assert qt.essay_marker("essay_text") == "essay_text"
        assert qt.essay_marker("mcq") == "essay"
        assert qt.essay_marker("true_false") == "essay"
        assert qt.is_essay_marker("essay_canvas")

    def test_a_null_key_still_counts_as_objective(self):
        """A deliberate quirk, kept because the count is already published.

        The test that shipped was `v not in (essay markers)`, and `None` passes
        it. The count feeds `max_score` and the percentage on a pupil's result, so
        this module mirrors the quirk rather than quietly changing recorded marks.
        """
        assert qt.counts_as_objective_key(None)
        assert qt.objective_key_count({"0": "A", "1": None, "2": "essay"}) == 2
        assert qt.objective_key_count({"0": "essay"}) == 0
        assert qt.objective_key_count(None) == 0


# ── the truth table ──────────────────────────────────────────────────────────

class TestMcqKeepsTheRuleItShippedWith:
    @pytest.mark.parametrize("key,answer,expected", [
        ("A", "A", True),
        ("A", "B", False),
        ("A", None, False),
        ("A", "", False),
        (["A", "B"], "B", True),
        (["A", "B"], "C", False),
        ("bonus", "whatever", True),
        ("bonus", "", False),
        ("bonus", None, False),
    ])
    def test_the_rule_is_unchanged(self, key, answer, expected):
        assert qt.grade_answer("mcq", key, answer) is expected

    def test_the_offline_wrapper_is_unwrapped(self):
        assert qt.grade_answer("mcq", "A", {"answer": "A", "pages": {}})


class TestTrueFalse:
    @pytest.mark.parametrize("key,answer", [
        ("true", "true"), ("false", "false"),
        (True, True), (False, False),
        (True, "true"), ("false", False),
        ("true", "T"), ("TRUE", " benar "),
        ("false", "F"), (False, "no"),
        ("true", 1), ("false", 0),
    ])
    def test_the_same_verdict_in_every_form(self, key, answer):
        assert qt.grade_answer("true_false", key, answer) is True

    @pytest.mark.parametrize("key,answer", [
        ("true", "false"), (True, False), ("false", "true"),
        ("true", None), ("true", ""), ("true", "maybe"),
        (None, "true"), ("", "true"),
    ])
    def test_everything_else_is_wrong(self, key, answer):
        assert qt.grade_answer("true_false", key, answer) is False

    def test_an_unset_key_cannot_be_answered_correctly(self):
        """A question with no key must not hand out a mark for a lucky guess."""
        assert qt.grade_answer("true_false", None, "false") is False
        assert qt.grade_answer("true_false", "nonsense", "nonsense") is False

    def test_the_offline_wrapper_is_unwrapped(self):
        assert qt.grade_answer("true_false", "true", {"answer": "true", "pages": {}})


class TestMatch:
    def test_every_pair_right_is_right(self):
        key = {"1": "b", "2": "a"}
        assert qt.grade_answer("match", key, {"1": "b", "2": "a"}) is True

    def test_the_order_the_pupil_answered_in_does_not_matter(self):
        key = {"1": "b", "2": "a"}
        assert qt.grade_answer("match", key, {"2": "a", "1": "b"}) is True

    def test_a_list_of_pairs_is_the_same_answer_as_a_mapping(self):
        assert qt.grade_answer("match", [["1", "b"], ["2", "a"]], {"2": "a", "1": "b"})

    def test_a_row_style_answer_is_read_too(self):
        key = [{"left": "1", "right": "b"}, {"left": "2", "right": "a"}]
        assert qt.grade_answer("match", key, [{"left": "2", "right": "a"},
                                               {"left": "1", "right": "b"}])

    def test_half_right_is_not_right(self):
        """All-or-nothing, like every other objective question here.

        The weighting model gives a whole weight per question, so partial credit
        would have to invent a second scoring model for one type. A teacher who
        wants partial credit for a matching exercise can split it into questions.
        """
        assert qt.grade_answer("match", {"1": "b", "2": "a"}, {"1": "b", "2": "c"}) is False

    @pytest.mark.parametrize("answer", [None, {}, {"1": "b"}, "1=b,2=a", 5])
    def test_a_partial_or_unreadable_answer_is_wrong(self, answer):
        assert qt.grade_answer("match", {"1": "b", "2": "a"}, answer) is False

    def test_an_empty_key_is_never_right(self):
        assert qt.grade_answer("match", {}, {}) is False
        assert qt.grade_answer("match", None, None) is False

    def test_the_offline_wrapper_is_unwrapped(self):
        key = {"1": "b"}
        assert qt.grade_answer("match", key, {"answer": {"1": "b"}, "pages": {}})


class TestDragDrop:
    def test_the_right_words_in_the_right_order(self):
        assert qt.grade_answer("drag_drop", ["kata", "dua"], ["kata", "dua"]) is True

    def test_the_right_words_in_the_wrong_order(self):
        assert qt.grade_answer("drag_drop", ["kata", "dua"], ["dua", "kata"]) is False

    def test_a_missing_chip_is_wrong(self):
        assert qt.grade_answer("drag_drop", ["a", "b"], ["a"]) is False

    def test_an_extra_chip_is_wrong(self):
        assert qt.grade_answer("drag_drop", ["a"], ["a", "b"]) is False

    @pytest.mark.parametrize("answer", [None, "", "ab", 5, {"a": 1}])
    def test_something_that_is_not_a_placement_is_wrong(self, answer):
        """`"ab"` is not two placements: exploding a string into characters once
        marked a nonsense answer right."""
        assert qt.grade_answer("drag_drop", ["a", "b"], answer) is False

    def test_whitespace_around_a_chip_does_not_decide_it(self):
        assert qt.grade_answer("drag_drop", ["kata satu", "dua"],
                               [" kata satu ", "dua"]) is True

    def test_an_empty_key_is_never_right(self):
        assert qt.grade_answer("drag_drop", [], []) is False

    def test_the_offline_wrapper_is_unwrapped(self):
        assert qt.grade_answer("drag_drop", ["a"], {"answer": ["a"], "pages": {}})


class TestOrdering:
    """Arranging sentences or words is its own question, beside every other type.

    An ordering question and a drag & drop question answer the same kind of thing —
    a sequence — so they share the key, the grader and the part-credit rule. What
    they do **not** share is standing: ordering is a type of its own, priced in the
    mark scheme on its own row, exactly as a matching question is. The model this
    replaced had one "objective" budget that a fifty-question choice section and a
    single ordering question split evenly.
    """

    KEY = {"order": ["Pertama", "Kedua", "Ketiga"], "extra": ["Penyeleweng"]}

    def test_the_whole_sequence_right_is_right(self):
        assert qt.grade_answer("order", self.KEY,
                               ["Pertama", "Kedua", "Ketiga"]) is True

    def test_the_same_items_in_the_wrong_order_are_wrong(self):
        assert qt.grade_answer("order", self.KEY,
                               ["Kedua", "Pertama", "Ketiga"]) is False

    def test_a_short_sequence_is_wrong_even_when_it_is_a_prefix(self):
        assert qt.grade_answer("order", self.KEY, ["Pertama", "Kedua"]) is False

    def test_the_named_shape_is_read_as_the_answer_too(self):
        """The offline queue replays what the key stores, so the key's own shape has
        to be a right answer rather than a `("order", "[…]")` mapping."""
        assert qt.grade_answer("order", self.KEY, self.KEY) is True

    def test_the_student_is_offered_the_items_and_not_the_sequence(self):
        public = qt.public_options("order", self.KEY)
        assert public == {"chips": ["Kedua", "Ketiga", "Penyeleweng", "Pertama"]}
        assert "order" not in public

    def test_part_marks_are_the_positions_that_are_right(self):
        assert qt.part_factor("order", self.KEY, ["Pertama", "Kedua", "salah"]) == 2 / 3

    TYPE = {"0": "order"}
    ASKED = {"0": ["Pertama", "Kedua", "salah"]}      # two of three positions right

    def test_part_marks_only_exist_for_a_scheme(self):
        """A paper that carries no scheme is worth exactly what it was worth
        yesterday — the freeze that keeps published marks where they are."""
        weights = {"0": 10.0}
        assert qt.partial_credit(weights) is False
        assert qt.earned_points(self.TYPE, {"0": self.KEY}, self.ASKED,
                                weights, 1)[0] == 0.0

    def test_a_scheme_turns_part_marks_on(self):
        from app.services import mark_scheme as ms
        weights = ms.weights_for(self.TYPE, 1, {qt.ORDER: 10}, partial=True)
        assert qt.partial_credit(weights) is True
        earned, graded = qt.earned_points(self.TYPE, {"0": self.KEY}, self.ASKED,
                                          weights, 1)
        assert graded == 1
        assert earned == round(100.0 * 2 / 3, 2)

    def test_an_ordering_question_has_a_readable_key_and_an_answer_dot(self):
        assert qt.key_has_answer("order", self.KEY) is True
        assert qt.key_has_answer("order", {"order": []}) is False
        assert qt.key_has_answer("order", None) is False
        assert qt.has_answer("order", ["Pertama"]) is True
        assert qt.has_answer("order", []) is False
        assert qt.describe_answer("order", self.KEY) == "Pertama \u2192 Kedua \u2192 Ketiga"

    def test_the_key_is_stored_in_the_one_shape_the_reader_expects(self):
        assert qt.normalise_key("order", self.KEY) == {
            "order": ["Pertama", "Kedua", "Ketiga"], "extra": ["Penyeleweng"]}
        # A *bare* list is the case that discriminates: handed the stored shape,
        # every branch returns it unchanged, so a reader that no longer knows this
        # type passes while a key posted in any other shape is stored unreadable.
        assert qt.normalise_key("order", ["Pertama", "Kedua"]) == {
            "order": ["Pertama", "Kedua"]}

    def test_it_is_auto_marked_and_named(self):
        assert qt.is_objective("order") is True
        assert qt.question_kind("order") == qt.KIND_ORDER
        assert qt.kind_label("order") == "Ordering"


class TestEveryTypeStandsOnItsOwn:
    """The standing rule, asserted rather than described.

    Reported defect: the builder told a teacher that choice, true/false, matching
    and drag & drop "all share this weight", and the mark scheme had one budget for
    every auto-marked question. A true/false question beside fifty choice questions
    was therefore worth a fiftieth of one choice question's marks, and there was no
    way to say what a matching question was worth.
    """

    TEMPLATE = APP / "templates" / "teacher" / "exam_form.html"

    def test_every_kind_has_its_own_scheme_row(self):
        from app.services import mark_scheme as ms
        kinds = {qt.question_kind(t) for t in ms.SCHEME_ORDER}
        assert kinds == {qt.KIND_CHOICE, qt.KIND_TRUE_FALSE, qt.KIND_MATCH,
                         qt.KIND_DRAG, qt.KIND_ORDER, qt.KIND_ESSAY}

    def test_every_type_has_its_own_marks(self):
        from app.services import mark_scheme as ms
        marks = {t: ms.default_marks(t) for t in ms.SCHEME_ORDER}
        assert len(set(marks.values())) > 1, (
            "every type defaulting to the same marks is the pool model again"
        )
        assert marks[qt.MATCH] > marks[qt.MCQ], (
            "a four-pair matching question must not be priced like one letter"
        )

    def test_the_scheme_prices_a_paper_per_type_not_per_pool(self):
        """Five questions of five different kinds: five rows, and the paper is
        still exactly 100."""
        from app.services import mark_scheme as ms
        types = {str(i): t for i, t in enumerate(
            [qt.MCQ, qt.TRUE_FALSE, qt.MATCH, qt.DRAG_DROP, qt.ORDER])}
        table = ms.describe(types, 5, None)
        assert [r["type"] for r in table["rows"]] == [
            qt.MCQ, qt.TRUE_FALSE, qt.MATCH, qt.DRAG_DROP, qt.ORDER]
        assert table["total"] == 100.0

    def test_the_builder_shows_a_row_per_type(self):
        src = self.TEMPLATE.read_text(encoding="utf-8")
        order = re.search(r"SG_SCHEME_ORDER = \[(.*?)\]", src, re.S)
        assert order, "the builder no longer lists the scheme's rows"
        rows = re.findall(r"'([a-z_]+)'", order.group(1))
        from app.services import mark_scheme as ms
        assert rows == list(ms.SCHEME_ORDER), (
            "the page's rows and the server's rows are the same list, or the table "
            "a teacher edits is not the table that scores the paper"
        )

    def test_no_page_calls_the_auto_marked_types_one_weight_any_more(self):
        """The sentence that was reported: "…they all share this weight"."""
        for path in (self.TEMPLATE,
                     APP / "templates" / "student" / "take_exam.html"):
            src = path.read_text(encoding="utf-8")
            assert "they all share this weight" not in src, path
            assert "semuanya berbagi bobot ini" not in src, path

    def test_the_one_editor_for_a_sequence_serves_both_types_that_are_one(self):
        """Ordering and drag & drop are written with the same editor.

        They answer the same thing — a sequence — so there is one editor, and the
        types it serves are named *there* rather than left to be re-derived. A
        defent that narrows that `x-show` back to drag & drop leaves an ordering
        question with an editor no teacher can fill in, and nothing on screen says
        so.
        """
        src = self.TEMPLATE.read_text(encoding="utf-8")
        # Located from the editor's own control and *backwards*, because the page
        # has one editor per kind and the first `space-y-2` div on it belongs to
        # matching — a forward search measures the neighbour, not the thing.
        add = src.find("addChip(i)")
        assert add > -1, "the builder has no chip editor any more"
        opened = src.rfind('<div x-show="', 0, add)
        assert opened > -1, "the sequence editor is no longer identifiable"
        show = src[opened:src.index(">", opened)]
        assert "kindOf(q) === 'dragdrop'" in show, show
        assert "kindOf(q) === 'ordering'" in show, (
            "an ordering question has no editor — the sequence editor is "
            "drag & drop only again"
        )


# ── one answer, several shapes ──────────────────────────────────────────────

class TestEveryShapeARightAnswerArrivesIn:
    """The pupil's page, the offline queue and the console all write the same answer.

    Measured defect: a matching answer stored in the *key's* named shape —
    `{"pairs": [...]}`, which is what the teacher's own form writes and what the
    offline queue replays — was read as the single mapping pair
    `("pairs", "[…]")`. A right answer scored zero and nothing said why. The
    accessors read the named shape now, and these are the shapes that must score
    the same 100 marks.
    """

    TYPES = {"0": "mcq", "1": "true_false", "2": "match", "3": "drag_drop"}
    KEY = {"0": "A", "1": "true",
           "2": {"pairs": [{"left": "Ibu kota", "right": "Jakarta"}], "extra": ["Bandung"]},
           "3": {"order": ["pertama", "kedua", "ketiga"]}}
    WEIGHTS = {"0": 25, "1": 25, "2": 25, "3": 25}

    RIGHT_ANSWERS = {
        "the page's own list of pairs and array of chips": {
            "0": "A", "1": "true",
            "2": [{"left": "Ibu kota", "right": "Jakarta"}],
            "3": ["pertama", "kedua", "ketiga"],
        },
        "the key's named shape": {
            "0": "A", "1": "true",
            "2": {"pairs": [{"left": "Ibu kota", "right": "Jakarta"}]},
            "3": {"order": ["pertama", "kedua", "ketiga"]},
        },
        "the {left: right} map the page keeps while answering": {
            "0": "A", "1": "true",
            "2": {"Ibu kota": "Jakarta"},
            "3": ["pertama", "kedua", "ketiga"],
        },
        "the offline wrapper with its drawings beside it": {
            "0": {"answer": "A"}, "1": {"answer": True},
            "2": {"answer": {"pairs": [{"left": "Ibu kota", "right": "Jakarta"}]}},
            "3": {"answer": {"order": ["pertama", "kedua", "ketiga"]}},
        },
        "a bool where the page stores a word": {
            "0": "A", "1": True,
            "2": {"pairs": [{"left": "Ibu kota", "right": "Jakarta"}]},
            "3": {"order": ["pertama", "kedua", "ketiga"]},
        },
    }

    @pytest.mark.parametrize("name", sorted(RIGHT_ANSWERS))
    def test_a_right_answer_scores_every_point(self, name):
        earned, graded = qt.earned_points(self.TYPES, self.KEY,
                                          self.RIGHT_ANSWERS[name], self.WEIGHTS, 4)
        assert (earned, graded) == (100.0, 4), name

    def test_the_named_shape_is_not_a_bogus_pair(self):
        """The exact misread: the wrapper key became the pair ("pairs", "[…]")."""
        got = qt._as_pairs(qt.unwrap({"pairs": [{"left": "a", "right": "b"}]}))
        assert got == frozenset({("a", "b")})
        got = qt._as_sequence(qt.unwrap({"order": ["a", "b"]}))
        assert got == ("a", "b")

    def test_a_wrong_answer_keeps_its_marks_off(self):
        """The tolerance above must not become tolerance for a wrong answer."""
        wrong = {"0": "B", "1": False,
                 "2": {"pairs": [{"left": "Ibu kota", "right": "Bandung"}]},
                 "3": {"order": ["kedua", "pertama", "ketiga"]}}
        earned, _ = qt.earned_points(self.TYPES, self.KEY, wrong, self.WEIGHTS, 4)
        assert earned == 0.0
        part = {"0": "A", "1": "true",
                "2": {"pairs": [{"left": "Ibu kota", "right": "Jakarta"}]},
                "3": {"order": ["kedua", "pertama", "ketiga"]}}
        earned, _ = qt.earned_points(self.TYPES, self.KEY, part, self.WEIGHTS, 4)
        assert earned == 75.0


# ── the pupil's controls for the new types, on a finger ─────────────────────

class TestTheNewControlsAreTappable:
    """Measured at 375 px with `pointer: coarse`: the drag bank's chips were 36 px
    and the controls that reorder a placed chip were 24 px — under the floor the
    rest of this page already keeps. A tap target is a question about the finger,
    so the floor for it is `.tap-44`, which is `min 44x44` under `(pointer:
    coarse)` in theme.css — measured again after the fix: 44, 44, 44.
    """

    PAGE = (APP / "templates" / "student" / "take_exam.html").read_text(encoding="utf-8")

    def _classes_of(self, marker):
        """The class attribute of every tag that carries this marker.

        A marker is either a class (`.match-opt`, and the dot is how it is named
        here rather than how it is written) or a handler (`dragRemove(i, wi)`).
        """
        needle = marker.lstrip(".")
        out = []
        for tag in re.finditer(r"<(?:button|span|div)\b[^>]*>", self.PAGE):
            if needle in tag.group(0):
                cls = re.search(r'class="([^"]*)"', tag.group(0))
                if cls:
                    out.append(cls.group(1))
        return out

    @pytest.mark.parametrize("marker", ['.match-opt', '.chip-opt',
                                        'dragMove(i, wi, -1)', 'dragRemove(i, wi)'])
    def test_the_controls_that_were_under_the_floor_carry_it(self, marker):
        classes = self._classes_of(marker)
        assert classes, f"{marker} is no longer on the pupil's page"
        for cls in classes:
            assert "tap-44" in cls, f"{marker} lost the finger floor: {cls[:90]}"

    @pytest.mark.parametrize("marker,padding", [('.tf-btn', 'py-3'), ('.match-row', 'p-3')])
    def test_the_controls_that_were_already_big_keep_their_padding(self, marker, padding):
        """Measured 48 px and 52 px at 375 px, from their own padding — the floor
        here is the padding, so a smaller one would be the regression."""
        classes = self._classes_of(marker)
        assert classes, f"{marker} is no longer on the pupil's page"
        for cls in classes:
            assert padding in cls, f"{marker} lost its {padding}: {cls[:90]}"

    def test_the_floor_is_defined_for_a_finger(self):
        theme = (APP / "static" / "css" / "theme.css").read_text(encoding="utf-8")
        block = re.search(r"@media \(pointer: coarse\)\s*\{(.*?)\n\s*\}", theme, re.S)
        assert block, "theme.css no longer defines the coarse-pointer block"
        rule = re.search(r"\.tap-44\s*\{([^}]*)\}", block.group(1))
        assert rule, "`.tap-44` is not defined under `(pointer: coarse)`"
        assert "min-width: 44px" in rule.group(1) and "min-height: 44px" in rule.group(1), rule.group(1)


class TestOrderingOnThePupilsPage:
    """An ordering question needs a control of its own, and the *rank* is the answer.

    Drag & drop already arranges items, so the failure mode here is not "nothing
    renders" — it is rendering the drag box and calling it ordering, which shows a
    pupil a control whose answer shape is a ranking while nothing on screen is
    numbered. So these tests ask for the rank badge, and for the same answer route
    (`dragAdd`/`dragMove`/`dragRemove` into `questions[i].answer`) that saving and
    syncing already handle.
    """

    PAGE = (APP / "templates" / "student" / "take_exam.html").read_text(encoding="utf-8")

    def _block(self):
        """The whole ordering branch, to its own `</template>`.

        Depth-counted rather than non-greedy: the block contains an `x-for`
        template for the placed items, so the first `</template>` in it is that
        one — and a guard that reads half a control passes while the half it did
        not read is broken.
        """
        start = self.PAGE.find("<template x-if=\"qKind(i) === 'ordering'\">")
        assert start > -1, "the ordering question has no control of its own"
        depth = 0
        for tag in re.finditer(r"</?template\b", self.PAGE[start:]):
            depth += 1 if tag.group(0) == "<template" else -1
            if depth == 0:
                return self.PAGE[start:start + tag.end()]
        raise AssertionError("unbalanced <template> tags in the ordering branch")

    def test_the_branch_is_its_own_not_the_drag_and_drop_one(self):
        assert "qKind(i) === 'ordering'" in self.PAGE
        assert "qKind(i) === 'dragdrop'" in self.PAGE, (
            "drag & drop is a type of its own too — reusing its branch is how one "
            "of the two quietly stops existing"
        )

    def test_every_placed_item_shows_its_rank(self):
        block = self._block()
        assert "x-text=\"wi + 1\"" in block, (
            "the position a pupil chose is the answer, so it has to be visible"
        )

    def test_the_items_are_tappable_and_draggable(self):
        block = self._block()
        assert "dragAdd(i, c)" in block, "a tap does not place the item"
        assert "draggable=\"true\"" in block and "chipStart($event, i, c)" in block, (
            "a mouse lost its drag"
        )
        assert "tap-44" in block, "the finger floor is missing from the control"

    def test_the_sequence_is_moved_and_cleared_with_the_shared_answer_route(self):
        block = self._block()
        for call in ("dragMove(i, wi, -1)", "dragMove(i, wi, 1)", "dragRemove(i, wi)"):
            assert call in block, call

    def test_the_empty_state_says_what_to_do(self):
        block = self._block()
        assert "Belum disusun" in block and "Nothing arranged yet" in block, (
            "an empty ordering answer needs a line telling a pupil what to tap"
        )

    def test_the_rail_badge_names_ordering(self):
        m = re.search(r"if \(kind === 'ordering'\) return \{([^}]*)\}", self.PAGE)
        assert m, "the question rail has no badge for an ordering question"
        assert "fa-arrow-down-1-9" in m.group(1)


class TestItNeverRaises:
    @pytest.mark.parametrize("qtype", [None, "", "weird", 5, ["mcq"]])
    @pytest.mark.parametrize("key", [None, "A", "", ["A"], {"a": "b"}, 5])
    @pytest.mark.parametrize("answer", [None, "A", "", 5, {"a": 1}, ["a"], {"answer": "A"}])
    def test_any_combination_returns_a_bool(self, qtype, key, answer):
        """Grading runs inside a submit path. A malformed row must not 500 it."""
        assert isinstance(qt.grade_answer(qtype, key, answer), bool)


# ── the marks ────────────────────────────────────────────────────────────────

class TestEarnedPoints:
    def test_a_new_objective_type_earns_its_weight(self):
        """The defect this module exists to prevent: a true/false question is not
        `"mcq"`, and before this it earned nothing at all."""
        earned, graded = qt.earned_points(
            {"0": "mcq", "1": "true_false"},
            {"0": "A", "1": "true"},
            {"0": "A", "1": "true"},
            {"0": 40, "1": 60},
            2,
        )
        assert earned == 100.0
        assert graded == 2

    def test_an_essay_earns_nothing_from_the_autograder(self):
        earned, graded = qt.earned_points(
            {"0": "essay_text"}, {"0": "essay"}, {"0": "a long answer"}, {"0": 50}, 1)
        assert (earned, graded) == (0.0, 0)

    def test_a_question_with_no_weight_is_not_graded(self):
        earned, graded = qt.earned_points(
            {"0": "mcq"}, {"0": "A"}, {"0": "A"}, {"0": 0}, 1)
        assert (earned, graded) == (0.0, 0)

    def test_a_wrong_answer_still_counts_as_graded(self):
        """`graded` is the denominator the scan route reports, not the score."""
        earned, graded = qt.earned_points(
            {"0": "mcq", "1": "mcq"}, {"0": "A", "1": "B"},
            {"0": "A", "1": "A"}, {"0": 50, "1": 50}, 2)
        assert (earned, graded) == (50.0, 2)

    def test_a_missing_type_defaults_to_mcq_like_the_routes_did(self):
        earned, _ = qt.earned_points({}, {"0": "A"}, {"0": "A"}, {"0": 30}, 1)
        assert earned == 30.0


class TestDefaultWeights:
    def test_the_new_objective_types_share_the_objective_budget(self):
        weights = qt.default_weights(
            {"0": "mcq", "1": "true_false", "2": "match", "3": "drag_drop", "4": "essay_text"},
            5)
        assert weights["0"] == weights["1"] == weights["2"] == weights["3"] == 17.5
        assert weights["4"] == 30.0

    def test_all_objective_is_100_points(self):
        weights = qt.default_weights({"0": "mcq", "1": "match"}, 2)
        assert weights == {"0": 50.0, "1": 50.0}

    def test_all_essay_is_100_points(self):
        weights = qt.default_weights({"0": "essay", "1": "essay_canvas"}, 2)
        assert weights == {"0": 50.0, "1": 50.0}

    def test_no_questions_is_no_weights(self):
        assert qt.default_weights({}, 0) == {}


# ── the one-place rule ───────────────────────────────────────────────────────

class TestNoScoringSiteKeepsItsOwnCopy:
    """The guard the whole module exists for.

    These two patterns were the duplications: `qtype == "mcq"` deciding which
    weight pool a question is in, and a hand-written tuple of essay markers
    deciding which questions the app auto-grades. A third kind of objective
    question makes each copy wrong in its own way, silently.
    """

    #: The literal comparisons a scoring or display site must not spell out.
    #:
    #: `=== 'mcq'` is deliberately *allowed*: in an Alpine template
    #: `x-if="type === 'mcq'"` selects the MCQ renderer, which is a thing a page
    #: must be able to say, and it cannot classify anything else by accident. What
    #: is forbidden is the family decision — `== 'mcq'` in Python, `!=`/`!==`
    #: anywhere ("not mcq, so it must be an essay"), and a hand-written tuple of
    #: essay markers, which is the same decision written as data.
    FORBIDDEN = (
        (re.compile(r"""(?<!=)==(?!=)\s*(['"])mcq\1"""),
         'equality with "mcq"'),
        (re.compile(r"""!=\s*(['"])mcq\1"""),
         'inequality with "mcq" ("not mcq, so an essay")'),
        (re.compile(r"""(['"])essay\1\s*,\s*(['"])essay_(text|canvas)\2"""),
         "a hand-written tuple of essay markers"),
        (re.compile(r"""(['"])essay_text\1\s*,\s*(['"])essay_canvas\2"""),
         "a hand-written tuple of essay markers"),
    )

    #: The module that is allowed to know the names.
    ALLOWED = {APP / "services" / "question_types.py"}

    def _sources(self):
        for path in sorted(APP.rglob("*")):
            if path.suffix in (".py", ".html") and path.is_file():
                yield path

    def test_nothing_outside_the_module_names_the_types_to_decide(self):
        offenders = []
        for path in self._sources():
            if path in self.ALLOWED:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            for line_no, line in enumerate(text.splitlines(), 1):
                for pattern, what in self.FORBIDDEN:
                    if pattern.search(line):
                        offenders.append(
                            f"{path.relative_to(ROOT)}:{line_no}: {what} — {line.strip()[:90]}"
                        )
        assert not offenders, (
            "a scoring or display site is deciding the question type for itself "
            "again. Ask app/services/question_types.py — `is_objective(qtype)` for "
            "the family, `grade_answer(qtype, key, answer)` for the mark — so a new "
            "objective type cannot be silently classified as an essay:\n  "
            + "\n  ".join(offenders)
        )

    def test_the_scan_looks_at_the_files_it_claims_to(self):
        """A guard that reads nothing passes for ever."""
        paths = {p.relative_to(ROOT).as_posix() for p in self._sources()}
        for expected in ("app/routes/student.py", "app/routes/api.py",
                         "app/routes/teacher.py", "app/services/omr_tasks.py"):
            assert expected in paths, f"the scan no longer reaches {expected}"

    def test_the_forbidden_patterns_actually_match_the_defect(self):
        """Mutation-style proof that the regexes are not decorative."""
        samples = [
            'if question_types.get(str(i), "mcq") == "mcq":',
            "if qt != 'mcq':",
            'qtype !== "mcq" ? t(\'Esai\',\'Essay\') : \'MCQ\',',
            'if v not in ("essay", "essay_text", "essay_canvas"):',
            'if t in ("essay_text", "essay_canvas"):',
        ]
        for sample in samples:
            assert any(p.search(sample) for p, _ in self.FORBIDDEN), sample
        # ...and that ordinary code the app is entitled to write is not caught:
        # a data literal, a translated label, and an Alpine branch that renders
        # the MCQ controls (as opposed to deciding the family).
        for safe in ['"question_types": {"0": "mcq", "1": "essay_text"}',
                     'x-text="\'MCQ\'"',
                     'x-if="q.type === \'mcq\'"',
                     ':class="q.type === \'mcq\' ? \'bg-amber-100\' : \'bg-emerald-100\'"']:
            assert not any(p.search(safe) for p, _ in self.FORBIDDEN), safe


# ── what the builder may create, and the list it reads ───────────────────────

class TestTheBuildersTypeList:
    """The value a page stores and the name it shows are one thing, from one place.

    The exam builder decides a question's type and the grader decides what that
    type is worth, so the two must agree on the *spelling* — `true_false` against
    `truefalse` is a question that renders as one kind and scores as another. The
    builder used to carry its own JavaScript array of type names; it reads
    `SG_QT.picker` off `q_vocabulary()` now, and these tests hold it there.
    """

    TEMPLATE = APP / "templates" / "teacher" / "exam_form.html"

    def test_the_picker_is_the_types_a_teacher_can_create(self):
        picker = [c["v"] for c in qt.vocabulary()["picker"]]
        assert picker == [qt.MCQ, qt.TRUE_FALSE, qt.MATCH, qt.DRAG_DROP, qt.ORDER,
                          qt.ESSAY_CANVAS]

    def test_every_pickable_type_is_one_the_grader_knows(self):
        for entry in qt.vocabulary()["picker"]:
            assert qt.canonical_type(entry["v"]) == entry["v"], entry
            assert entry["kind"] == qt.question_kind(entry["v"]), entry
            assert entry["id"] and entry["en"], entry
            assert entry["id"] != entry["en"], entry

    def test_a_typed_essay_is_not_offered_beside_a_canvas_one(self):
        """Both are read and graded; only one can be drawn on."""
        picker = {c["v"] for c in qt.vocabulary()["picker"]}
        assert qt.ESSAY_TEXT not in picker
        assert qt.ESSAY_TEXT in qt.ESSAY_TYPES  # …and is still a real type

    def test_the_builder_reads_that_list_instead_of_writing_its_own(self):
        src = self.TEMPLATE.read_text(encoding="utf-8")
        assert "SG_QT.picker" in src, "the builder stopped reading the server's list"
        hand_written = re.findall(r"\{v:\s*'([a-z_]+)'", src)
        assert not hand_written, (
            "a hand-written list of type names is back in the builder: "
            f"{hand_written} — it drifts from the grader silently, so read "
            "q_vocabulary()['picker'] instead"
        )

    def test_the_pickers_labels_are_the_servers_labels(self):
        """The page keeps the words so a toggle can switch them; the server owns
        them. This is what keeps the copy from drifting apart from
        `q_vocabulary()['labels']` while it lives in two places.

        A *label* is cosmetic and a *value* is not, which is why the values are
        read from the server and only these words are repeated — and why the
        repetition is checked rather than trusted.
        """
        src = self.TEMPLATE.read_text(encoding="utf-8")
        block = re.search(r"const look = \{(.*?)\n\s*\};", src, re.S)
        assert block, "the builder no longer has its own look-up for the labels"
        labels = qt.vocabulary()["labels"]
        seen = 0
        for kind, id_text, en_text in re.findall(
                r"(\w+):\s*\{id:\s*'([^']*)',\s*en:\s*'([^']*)'", block.group(1)):
            assert kind in labels, f"the page names a kind the server does not: {kind}"
            assert [id_text, en_text] == list(labels[kind]), (
                f"{kind}: the page shows {[id_text, en_text]} where the server "
                f"says {labels[kind]}"
            )
            seen += 1
        assert seen >= len(labels), (
            f"only {seen} of the server's {len(labels)} kinds are named by the page"
        )

    def test_the_type_select_shows_the_stored_type_not_the_first_option(self):
        """Measured defect: every question displayed "Multiple choice".

        `x-model` writes `el.value` while the element initialises, and a
        `<select>` whose `<option>`s are still being cloned by `x-for` accepts
        only its first one — so a true/false question showed the choice label for
        ever. The value is re-applied a tick later, once the options exist.
        """
        src = self.TEMPLATE.read_text(encoding="utf-8")
        select = re.search(r'<select[^>]*x-model="q\.type".*?</select>', src, re.S)
        assert select, "the per-question type select is gone"
        block = select.group(0)
        assert "$nextTick" in block and "$el.value" in block, (
            "the type select does not re-apply its value after `x-for` has cloned "
            "its options, so it shows the first type for every question"
        )
        assert '<template x-for="c in typeChoices"' in block, (
            "the select no longer builds its options from the same list the add "
            "buttons use"
        )
