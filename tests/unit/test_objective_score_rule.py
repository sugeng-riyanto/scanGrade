"""One divisor for a partly keyed paper, everywhere a score is written.

What was wrong
--------------
Six places computed the auto-graded score of a submission, in four different ways.
Two of them — the scanner's save routes and the Celery OMR task — divided by the
number of answers the **key happened to contain**:

    correct 2 / keyed 2  =  100%

so a teacher who had keyed two of ten MCQ and scanned one sheet was shown a perfect
mark out of a paper that was two-tenths marked. The same pupil, on the same sitting,
scored the *weighted* objective marks if they submitted online and `2 / 10 = 20%` if
the teacher pressed "recalculate scores" — three numbers for one paper, chosen by
which button was pressed. The dashboard's answer-key warning did not help: it was
written to stay silent about a partly filled key, on the grounds that the inflated
score was a different defect from the one its sentence described. It was, and it was
this one.

What replaces it
----------------
`question_types.objective_result` — one function, whose denominator is the **paper's**
objective questions and whose numerator can only be earned through the key:

    score = correct / (objective questions on the paper) * 100

An objective question with no key is scored wrong, which is the safe direction and the
one `_needs_answer_key` already described. A partly filled key therefore *lowers the
ceiling* instead of raising the score, `ceiling` puts a number on it, and the teacher
gets a card that says so.

These tests hold the rule, hold every writer to it, and hold the two things that must
**not** change: an already-complete key's marks, and the weighted `final_score` that
essays and the penalty are applied to.
"""
import ast
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

from app.services.question_types import objective_result      # noqa: E402


def _source(*parts) -> str:
    return (ROOT.joinpath(*parts)).read_text(encoding="utf-8")


def block_of(source: str, marker: str, *, stop: str = "\n@") -> str:
    """One function or route body: from `marker` up to the next `stop`."""
    start = source.index(marker)
    rest = source[start:]
    nxt = rest.find(stop, 1)
    return rest if nxt == -1 else rest[:nxt]


STUDENT = _source("app", "routes", "student.py")
TEACHER = _source("app", "routes", "teacher.py")
API = _source("app", "routes", "api.py")
OMR_TASKS = _source("app", "services", "omr_tasks.py")
SUPER_ADMIN = _source("app", "routes", "super_admin.py")

SUBMIT_BLOCK = block_of(STUDENT, "def submit_exam", stop="\n@student_bp.route")
SCAN_SAVE_BLOCK = block_of(API, "def scan_save", stop="\n@api_bp.route")
BULK_SAVE_BLOCK = block_of(API, "def scan_bulk_save", stop="\n@api_bp.route")


def ten_mcq():
    return {str(i): "mcq" for i in range(10)}


# ── 1. the rule ──────────────────────────────────────────────────────────────

class TestTheRule:
    def test_a_partly_filled_key_cannot_produce_a_perfect_score(self):
        """The defect, stated as the number a teacher saw.

        Two questions keyed out of ten, both answered right. The old scanner rule
        (`correct / keyed`) paid 100 for this.
        """
        result = objective_result(ten_mcq(), {"0": "A", "1": "B"}, {"0": "A", "1": "B"}, 10)
        assert result.score == 20.0, "a two-tenths-marked paper paid a perfect score"
        assert result.correct == 2 and result.out_of == 10 and result.keyed == 2

    def test_the_ceiling_is_what_a_partial_key_costs(self):
        result = objective_result(ten_mcq(), {"0": "A", "1": "B", "2": "C", "3": "D"}, {}, 10)
        assert result.ceiling == 40.0, "the best a student can reach, in one number"
        assert result.unkeyed == [4, 5, 6, 7, 8, 9]

    def test_a_complete_key_is_scored_exactly_as_before(self):
        """The marks already in students' hands must not move.

        With every question keyed, `correct / paper` is the expression the teacher's
        own "recalculate scores" has always used, so it is the same number.
        """
        key = {str(i): "A" for i in range(10)}
        all_right = {str(i): "A" for i in range(10)}
        assert objective_result(ten_mcq(), key, all_right, 10).score == 100.0
        seven = {str(i): "A" for i in range(7)}
        assert objective_result(ten_mcq(), key, seven, 10).score == 70.0
        assert objective_result(ten_mcq(), key, seven, 10).correct == 7

    def test_an_unkeyed_question_is_counted_wrong_not_skipped(self):
        """Skipping it is what made the denominator the key's own length."""
        key = {str(i): "A" for i in range(10)}
        answered = {str(i): key[str(i)] for i in range(10)}
        empty = objective_result(ten_mcq(), {}, answered, 10)
        assert empty.correct == 0 and empty.out_of == 10 and empty.score == 0.0
        assert empty.keyed == 0 and empty.ceiling == 0.0

    def test_an_exam_with_nothing_objective_scores_zero_rather_than_dividing(self):
        assert objective_result({"0": "essay"}, {"0": "x"}, {}, 1).score == 0.0

    def test_essays_are_not_in_the_objective_denominator(self):
        qtypes = {"0": "mcq", "1": "essay", "2": "essay"}
        result = objective_result(qtypes, {"0": "A"}, {"0": "A", "1": "words"}, 3)
        assert result.out_of == 1 and result.score == 100.0, (
            "an essay is a teacher's mark and must not depress the objective score")

    @pytest.mark.parametrize("qtype", ["mcq", "true_false", "match", "drag_drop", "ordering"])
    def test_every_objective_family_is_counted(self, qtype):
        """`true_false` and friends were once invisible to every one of these loops."""
        assert objective_result({"0": qtype, "1": "essay"}, {}, {}, 2).out_of == 1


# ── 2. the divisor is not written out anywhere else ──────────────────────────

class TestEveryWriterUsesIt:
    """Source-level guards, because the defect was *copies* of the rule.

    Each of these failed before this change: the two scanner routes and the Celery
    task divided by the keyed count, and `student.py` wrote the weighted marks to the
    same column the teacher's recalculation wrote a percentage to.
    """

    def test_student_submit_stores_the_shared_score(self):
        assert "objective_result(" in SUBMIT_BLOCK, "the submit route computes its own score again"

    def test_student_final_score_is_still_built_from_the_weighted_marks(self):
        """The one thing that must not move: the mark a released result carries.

        `final_score` is the weighted objective total plus the essays minus the
        penalty. Reading the new percentage into it would silently re-scale every
        existing final mark, so the arithmetic uses `earned` and not `score`.
        """
        assert re.search(r"final_score\s*=\s*max\(0\.0,\s*round\(earned\s*-\s*penalty",
                         SUBMIT_BLOCK), (
            "final_score no longer comes from the weighted marks; check whether every "
            "published final mark just changed scale")

    def test_the_teacher_recalculation_uses_the_shared_score(self):
        block = block_of(TEACHER, "def _recalculate_scores")
        assert "objective_result(" in block
        assert not re.search(r"objective_score\s*=\s*round\(", block), (
            "the recalculation still divides on its own")

    def test_the_celery_omr_task_uses_it_and_fetches_the_denominator(self):
        assert "objective_result(" in OMR_TASKS
        assert "key_has_answer" not in OMR_TASKS, (
            "the worker is still counting keyed answers to build its own divisor")
        # The denominator is the paper's count, so it has to be selected: a column
        # left out of a select list reads as absent, which here means "no questions".
        assert re.search(r'select\("answer_key,question_types,total_questions"\)', OMR_TASKS), (
            "the worker does not fetch total_questions, so its denominator is zero")

    def test_the_scanner_save_route_uses_it(self):
        assert "objective_result(" in SCAN_SAVE_BLOCK
        assert "objective_key_count" not in SCAN_SAVE_BLOCK, (
            "the scan route is dividing by the key's own length again — the defect "
            "that paid 100 for a two-tenths-marked paper")

    def test_the_bulk_save_route_uses_it(self):
        assert "objective_result(" in BULK_SAVE_BLOCK
        assert "total_questions" in BULK_SAVE_BLOCK, (
            "the bulk route cannot compute a paper's-count denominator without it")

    def test_the_bulk_regrade_route_uses_it(self):
        assert API.count("objective_result(") >= 3, (
            "every scoring site in api.py has to call the one rule")

    def test_the_omr_bench_uses_it_too(self):
        """The bench a super-admin judges the scanner with.

        It has the same shape, so leaving it out is how the copies started.
        """
        block = block_of(SUPER_ADMIN, "def omr_test_batch")
        assert "objective_result(" in block

    def test_no_site_calls_the_keyed_count_again(self):
        """The shape of the defect, searched for across the whole app.

        `objective_key_count` is the helper that answers "how many answers does the
        key have" — the wrong denominator, and nothing but this test and its own
        definition should name it now.
        """
        offenders = []
        for path in sorted(ROOT.glob("app/**/*.py")):
            if path.name == "question_types.py":
                continue                    # it is defined there, and only there
            if re.search(r"objective_key_count", path.read_text(encoding="utf-8")):
                offenders.append(str(path.relative_to(ROOT)))
        assert offenders == [], (
            "these files still reach for the key's own length as a divisor: "
            + ", ".join(offenders))


# ── 3. the numbers a screen shows have to mean the same thing ────────────────

class TestTheNumbersOnScreen:

    def test_the_scanner_reports_the_paper_as_the_denominator(self):
        """\"x / y benar\" has to be out of the paper.

        The scan route sent `total` from the key's length, so a half-keyed exam told
        the teacher "2/2 benar" beside a score of 100.
        """
        assert re.search(r"mcq_count\s*=\s*objective\.out_of", SCAN_SAVE_BLOCK), (
            "the scan payload's total is not the paper's objective count")

    def test_the_scanner_says_how_many_questions_have_no_key(self):
        assert '"unkeyed"' in SCAN_SAVE_BLOCK, (
            "a sheet that looks perfect and does not score 100 has to be explained "
            "on the screen the teacher is looking at")

    def test_max_score_is_the_same_unit_as_score(self):
        """`score` is a percentage; `max_score` used to be a question count.

        Two units in one pair of columns is what let one reader divide by a paper and
        another by a key without either looking wrong in isolation.
        """
        for label, source in (("api.py", API), ("student.py", STUDENT)):
            for match in re.finditer(r'"max_score":\s*([^,\n}]+)', source):
                value = match.group(1).strip()
                assert value in ("100", "100.0"), (
                    f"{label} stores max_score={value}, which is not the maximum of a "
                    "percentage")


# ── 4. the route that crashed before it could score anything ─────────────────

class TestTheBulkSaveRoute:
    """`scan_bulk_save` referenced `mcq_count` without ever assigning it.

    So the bulk import of a scanned batch raised `NameError: name 'mcq_count' is not
    defined` on the first sheet it saved — a scoring route that could not score, with
    the failure landing in the `except` that records it as a per-student error. Found
    while giving the route a shared divisor; the fix is that the name now comes from
    the same call as everything else.
    """

    def test_the_denominator_is_bound_before_the_dict_that_uses_it(self):
        """Line order in the AST, not in the text.

        A substring search is not enough to state this: the explanatory comment above
        the assignment names `mcq_count`, and a reader that counted that mention as a
        use would fail on prose. What has to be true is that no `Name` **load** of
        `mcq_count` happens on a line before the store.
        """
        tree = ast.parse(BULK_SAVE_BLOCK)
        stores, loads = [], []
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    for name in ast.walk(target):
                        if isinstance(name, ast.Name) and name.id == "mcq_count" \
                                and isinstance(name.ctx, ast.Store):
                            stores.append(node.lineno)
            if isinstance(node, ast.Name) and node.id == "mcq_count" \
                    and isinstance(node.ctx, ast.Load):
                loads.append(node.lineno)
        assert stores, "scan_bulk_save never binds mcq_count"
        first_store = min(stores)
        assert not [line for line in loads if line < first_store], (
            "mcq_count is read before it is assigned — this route used to reach for a "
            "name that only exists in two *other* routes, which is a NameError")

    def test_it_does_not_reach_for_another_route_s_idea_of_it(self):
        assert re.search(r"mcq_count\s*=\s*objective\.out_of", BULK_SAVE_BLOCK), (
            "scan_bulk_save's mcq_count is not bound to this route's own objective result")

    def test_the_saved_rows_carry_the_denominator(self):
        """The caller of a bulk save needs to know what the score was out of."""
        assert '"total": mcq_count' in BULK_SAVE_BLOCK

    def test_the_route_parses_and_has_no_free_name(self):
        """A cheap static check for the defect class, not just this instance."""
        tree = ast.parse(BULK_SAVE_BLOCK)
        assigned = {n.id for node in ast.walk(tree) if isinstance(node, ast.Assign)
                    for n in ast.walk(node) if isinstance(n, ast.Name)
                    if isinstance(n.ctx, ast.Store)}
        for name in ("score", "correct", "mcq_count", "objective", "total_q"):
            assert name in assigned, f"{name} is used in scan_bulk_save but never bound"
