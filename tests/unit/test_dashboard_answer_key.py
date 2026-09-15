"""The "no answer key" card on the teacher dashboard has to be able to turn off.

What teachers saw: the dashboard listed their exams under

    Answer key not set yet — N exam(s) have no answer key, student scores will be 0

and it kept saying so after the key was filled in. They were right and the card
was wrong. Two independent defects:

1. **The dashboard never fetched the column it was judging.** `fb9aef2` trimmed
   this route's `select("*")` to an explicit column list and left `answer_key`
   out, while the check below went on reading `e.get("answer_key")`. Every exam
   with an MCQ question was therefore reported as missing its key — permanently,
   and nothing a teacher could do would clear it. The rows in the database had
   their keys all along: one as a JSON object, one as the JSON *string* the
   answer-key page writes.
2. **The page is cached for 20 seconds and no writer invalidated it.** So even
   with the column fetched, saving a key and coming straight back showed the page
   as it was. `publish` was the only route that dropped it.

These tests hold the card to being true, and hold the writers to dropping the
cache they changed.
"""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SOURCE = (ROOT / "app" / "routes" / "teacher.py").read_text(encoding="utf-8")
TEMPLATE = (ROOT / "app" / "templates" / "teacher" / "dashboard.html").read_text(
    encoding="utf-8")


def _import_helpers():
    import importlib.util
    import sys as _sys

    spec = importlib.util.spec_from_file_location("teacher_for_key_test",
                                                  ROOT / "app" / "routes" / "teacher.py")
    module = importlib.util.module_from_spec(spec)
    _sys.modules["teacher_for_key_test"] = module
    spec.loader.exec_module(module)
    return module


teacher = _import_helpers()
needs_key = teacher._needs_answer_key


# ── 1. the judgement matches what scoring actually does ──────────────────────

class TestWhenTheWarningIsTrue:
    """`student.py` builds `mcq_count` from the *key*, so an exam with MCQ
    questions and no key scores 0 on that half however well the student did —
    which is exactly what the card claims."""

    @pytest.mark.parametrize("label,question_types,answer_key,expected", [
        ("no question types at all", {}, {}, False),
        ("essay only, no key", {"0": "essay"}, {}, False),
        ("essay only, no key, several", {"0": "essay", "1": "essay"}, {}, False),
        ("mcq, no key", {"0": "mcq"}, {}, True),
        ("mcq, key is the string the page writes", {"0": "mcq"}, "{}", True),
        ("mcq and essay, no key", {"0": "mcq", "1": "essay"}, {}, True),
        ("mcq answered", {"0": "mcq"}, {"0": "A"}, False),
        # The two rows that were live when this was reported: one stores an
        # object and one the json.dumps string of one. Both must read as answered.
        ("mcq answered, key as a JSON string", {"0": "mcq"}, '{"0": "A"}', False),
        ("mcq answered, multi-answer", {"0": "mcq"}, {"0": ["A", "B"]}, False),
        ("mcq marked bonus", {"0": "mcq"}, {"0": "bonus"}, False),
        ("mcq whose key is blank", {"0": "mcq"}, {"0": ""}, True),
        ("mcq whose key holds an essay marker", {"0": "mcq"}, {"0": "essay"}, True),
        ("question types as a JSON string", "{\"0\": \"mcq\"}", {"0": "C"}, False),
        ("malformed question types", "not json", {"0": "C"}, False),
        ("key is a list, not an object", {"0": "mcq"}, ["A"], True),
    ])
    def test_the_truth_table(self, label, question_types, answer_key, expected):
        exam = {"question_types": question_types, "answer_key": answer_key}
        assert needs_key(exam) is expected, label

    def test_a_partially_filled_key_is_not_flagged(self):
        """It does not produce 0 — the denominator is the number of answers the
        key has, so it inflates the score instead. That is a different defect and
        not what this card claims; flagging it here would make the card's own
        sentence false."""
        exam = {"question_types": {str(i): "mcq" for i in range(5)}, "answer_key": {"0": "A"}}
        assert needs_key(exam) is False


# ── 2. the dashboard reads the column it judges ─────────────────────────────

class TestTheDashboardFetchesWhatItJudges:

    def test_the_dashboard_select_includes_answer_key(self):
        """The regression, stated as a test. The check below reads
        `exam["answer_key"]`, so the select has to ask for it — a column left out
        of the list is None here, and None reads as "no key" for every row."""
        selects = [m.group(1) for m in
                   re.finditer(r'supabase\.table\("exams"\)\.select\("([^"]+)"\)', SOURCE)]
        dashboard = _route_block("/dashboard")
        own = re.search(r'supabase\.table\("exams"\)\.select\("([^"]+)"\)', dashboard)
        assert own, "the dashboard no longer selects the exams it reports on"
        columns = {c.strip() for c in own.group(1).split(",")}
        assert "answer_key" in columns, (
            "the dashboard's exams select does not fetch answer_key, but the "
            "'no answer key' card is computed from it. Every exam with an MCQ "
            "question will be reported as missing its key — permanently, and "
            "nothing a teacher does can clear it.\n  columns: " + own.group(1))
        assert "question_types" in columns, (
            "without question_types the check cannot tell an MCQ exam from an "
            "essay-only one, and an essay-only exam does not need a key")
        assert selects, "this test no longer recognises how the route queries exams"

    def test_the_check_is_the_one_used_for_the_card(self):
        block = _route_block("/dashboard")
        # The *call*, not a mention: the name also appears in a comment above the
        # select, and asserting on that let a bare-truthiness rewrite pass.
        assert re.search(r'exams_no_key\s*=\s*\[e for e in exams if _needs_answer_key\(e\)\]',
                         block), (
            "the dashboard no longer builds the card's list by calling the shared "
            "judgement; an inline `if not e.get(\"answer_key\")` is what was broken "
            "for months")
        assert "exams_no_key" in TEMPLATE, (
            "the card and the value it renders have to stay connected")


# ── 3. a writer drops the cache it changed ──────────────────────────────────

# Every route that changes something the dashboard shows. The dashboard caches
# for 20 seconds, so a writer that does not drop it leaves the teacher looking at
# the page as it was — which is half of what was reported.
WRITERS = {
    "/exams/<exam_id>/answer-keys": "saving the key the card is about",
    "/exams/<exam_id>": "editing the exam: key, question types, status",
    "/exams/new": "a new exam is a new row on the dashboard",
    "/exams/<exam_id>/delete": "a deleted exam must leave the card",
    "/exams/<exam_id>/toggle-status": "status is on the dashboard",
    "/exams/<exam_id>/toggle-visibility": "visibility is on the dashboard",
}


def _route_block(path: str) -> str:
    """The whole body of one route: its decorators, up to the next route."""
    start = SOURCE.index(f'@teacher_bp.route("{path}"')
    rest = SOURCE[start:]
    nxt = rest.find("\n@teacher_bp.route(", 1)
    return rest if nxt == -1 else rest[:nxt]


class TestTheWritersInvalidateTheCache:
    @pytest.mark.parametrize("path", sorted(WRITERS))
    def test_the_writer_drops_the_dashboard_cache(self, path):
        block = _route_block(path)
        assert "_invalidate_teacher_dashboard()" in block, (
            f"{path} changes what the dashboard shows ({WRITERS[path]}) but never "
            "drops the cached dashboard. The teacher saves, comes back within 20 "
            "seconds and sees the page as it was.\n  add "
            "_invalidate_teacher_dashboard() after the write")

    def test_the_invalidation_is_one_named_helper(self):
        """One helper, so the next writer is a one-line call rather than a
        rediscovery of the cache key and its failure mode."""
        assert "def _invalidate_teacher_dashboard(" in SOURCE
        assert SOURCE.count('cache_delete(f"t_dash:{g.user_id}")') == 1, (
            "the dashboard's cache key is being written out at more than one call "
            "site again; keep it inside _invalidate_teacher_dashboard()")

    def test_every_route_knows_which_teacher_it_is_for(self):
        """The dashboard cache is per user, and the invalidation uses `g.user_id`.
        A route reachable without a session would drop somebody else's page."""
        for path in sorted(WRITERS):
            block = _route_block(path)
            assert "teacher_or_admin_required" in block.split("\n")[0:4][-1] or \
                "teacher_or_admin_required" in block[:200], f"{path} lost its role guard"
