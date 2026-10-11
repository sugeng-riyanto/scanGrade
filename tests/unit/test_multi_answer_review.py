"""The multiple-answer exception, from the two ends nobody had closed.

Phases 1-2 gave a single-answer question the power to *be* a multiple-answer one, and
gave the builder a toggle for it. Two things were still open, and both are about data
that already exists:

1. **The answer-key page asked the stored value what the question was.** Its control
   was a set of toggles for every choice question, so clicking `B` after `A` wrote
   `["A", "B"]` — on a question whose whole name is *single answer*. That is exactly
   how the old ambiguous keys were produced, and leaving the control that way after
   adding the exception would keep producing them. The mode comes from the question's
   **type** now, and a save cannot write two letters onto a one-answer question: the
   request is refused, the question is named, and the teacher is pointed at the two
   honest ways out.

2. **The keys already in the database.** A question of type `mcq` whose key names more
   than one letter was legal before the exception existed — the grader read it as *any
   of these*, so the question silently asked for one of several. Nothing repairs it
   automatically, and nothing should: the stored key does not say whether the paper
   meant *all of these* or *one of them*, and only the owner knows. What this work
   adds is the panel that finds them, and — the part that makes it a decision rather
   than a guess — what each resolution would cost: who moves, and by how far, computed
   with `exam_scoring`, the same arithmetic the save then writes with.

The rule the panel exists to hold: **keeping a key and rewriting marks are two
decisions.** Changing a question's type or key changes what the paper means from here
on; recomputing changes numbers already in pupils' hands. The second is a separate,
explicit tick, and every one taken is recorded.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.services import exam_scoring
from app.services import question_types as qt

ROOT = Path(__file__).resolve().parents[2]
ROUTES = ROOT / "app" / "routes" / "teacher.py"
ANSWER_KEYS = ROOT / "app" / "templates" / "teacher" / "answer_keys.html"
REVIEW = ROOT / "app" / "templates" / "teacher" / "answer_key_review.html"
EXAMS = ROOT / "app" / "templates" / "teacher" / "exams.html"
DASHBOARD = ROOT / "app" / "templates" / "teacher" / "dashboard.html"
BASELINE = ROOT / "deploy" / "i18n_baseline.json"


@pytest.fixture(scope="module")
def route_source() -> str:
    return ROUTES.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def review_html() -> str:
    return REVIEW.read_text(encoding="utf-8")


def _route_block(source: str, path: str) -> str:
    """One route's decorators and body, up to the next route."""
    start = source.index(f'@teacher_bp.route("{path}"')
    rest = source[start:]
    nxt = rest.find("\n@teacher_bp.route(", 1)
    return rest if nxt == -1 else rest[:nxt]


# ── 1. the answer-key control follows the type, not the stored value ─────────

class TestTheControlFollowsTheType:

    def test_the_route_serves_each_questions_mode(self, route_source):
        """The page cannot pick radio or checkbox without being told which."""
        assert '"mode": choice_mode(qtype),' in route_source
        assert 'from app.services.question_types import (' in route_source
        assert "CHOICE_MODE_SINGLE" in route_source

    def test_the_route_reports_the_drift_between_key_and_type(self, route_source):
        """The other half: a key that disagrees with its own question."""
        assert '"drift": key_mode_drift(qtype, k),' in route_source

    def test_the_page_says_what_the_question_accepts(self):
        html = ANSWER_KEYS.read_text(encoding="utf-8")
        assert 'data-key-mode="{{ q.mode }}"' in html, (
            "the mode has to reach the page as data, not be re-derived in the browser")
        assert "Pilih semua jawaban yang benar" in html and "Pilih satu jawaban" in html

    def test_the_single_answer_control_replaces_and_the_multi_one_adds(self):
        """The whole difference the exception exists to express, in one branch."""
        html = ANSWER_KEYS.read_text(encoding="utf-8")
        body = html.split("toggle(idx, opt) {", 1)[1].split("\n        },", 1)[0]
        assert "if (this.modes[idx] === 'single') {" in body
        assert "this.keys[idx] = (cur.length === 1 && cur[0] === opt) ? [] : [opt];" in body
        # The multi branch must still add and remove — the earlier fix for `pgk`.
        assert "cur.push(opt)" in body and "cur.splice(i, 1)" in body

    def test_every_reader_normalises_the_stored_shape_first(self):
        """`"A"`, `["A"]` and `[]` are one thing to the browser, and that is a list."""
        html = ANSWER_KEYS.read_text(encoding="utf-8")
        assert "letters(idx) {" in html
        assert "if (Array.isArray(cur)) return cur.slice();" in html
        body = html.split("isSelected(idx, opt) {", 1)[1].split("\n        },", 1)[0]
        assert "this.letters(idx).indexOf(opt) > -1" in body

    def test_what_is_posted_is_the_shape_the_type_writes(self):
        html = ANSWER_KEYS.read_text(encoding="utf-8")
        getjson = html.split("getJson() {", 1)[1].split("\n        }", 1)[0]
        assert "this.modes[k] === 'single' ? (arr.length ? arr[0] : '') : arr" in getjson, (
            "a single-answer question posts one letter, the exception posts the set")
        assert "out[k] = 'essay';" not in getjson, (
            "this page used to write an `essay` marker over any question it could not "
            "read — which for a matching question destroyed its key")

    def test_the_control_is_big_enough_to_touch(self):
        html = ANSWER_KEYS.read_text(encoding="utf-8")
        assert "min-w-[44px] min-h-[44px]" in html


# ── 2. a one-answer question cannot be saved with two ────────────────────────

class TestASingleAnswerQuestionCannotHoldTwo:

    def test_the_route_refuses_it(self, route_source):
        assert ("if choice_mode(qtype) == CHOICE_MODE_SINGLE "
                "and len(answer_letters(v)) > 1:") in route_source

    def test_it_refuses_rather_than_trimming_a_letter(self, route_source):
        """Trimming would silently drop a mark the teacher set on purpose."""
        block = route_source.split("refused = []", 1)[1].split("answer_key = merged", 1)[0]
        assert "continue" in block
        assert "refused.append(" in block
        assert "normalise_key(qtype, v[0])" not in block

    def test_the_message_names_the_question_and_the_way_out(self, route_source):
        assert 'if refused:' in route_source
        flash = route_source.split('if refused:', 1)[1].split("return redirect", 1)[0]
        assert "hanya menerima satu jawaban" in flash
        assert "Tinjau Kunci Ganda" in flash


# ── 3. the old data is found by one function ────────────────────────────────

class TestFindingTheOldData:

    def test_a_two_letter_key_on_a_single_answer_question_is_found(self):
        assert qt.ambiguous_choice_keys(
            {"0": "mcq", "1": "mcq_multi", "2": "mcq"},
            {"0": ["A", "B"], "1": ["A", "B"], "2": "C"},
        ) == [0]

    def test_one_letter_is_not_ambiguous(self):
        assert qt.ambiguous_choice_keys({"0": "mcq"}, {"0": "A"}) == []
        assert qt.ambiguous_choice_keys({"0": "mcq"}, {"0": ["A"]}) == []

    def test_a_question_the_key_never_answered_is_not_ambiguous(self):
        assert qt.ambiguous_choice_keys({"0": "mcq"}, {}) == []
        assert qt.ambiguous_choice_keys({"0": "mcq"}, {"0": None}) == []

    def test_a_bonus_key_is_not_ambiguous(self):
        assert qt.ambiguous_choice_keys({"0": "mcq"}, {"0": "bonus"}) == []

    def test_non_choice_types_are_never_ambiguous(self):
        assert qt.ambiguous_choice_keys(
            {"0": "match", "1": "true_false", "2": "essay"},
            {"0": ["A", "B"], "1": ["true"], "2": "A"},
        ) == []

    def test_the_order_is_numeric_not_lexicographic(self):
        """Question 10 belongs after question 9 on the page that lists them."""
        types = {str(i): "mcq" for i in range(12)}
        keys = {str(i): ["A", "B"] for i in range(12)}
        assert qt.ambiguous_choice_keys(types, keys) == list(range(12))

    def test_a_json_string_column_is_read_rather_than_iterated(self):
        """A text jsonb column hands every reader a string; iterating it yields
        characters, which would report nothing wrong with any paper."""
        assert qt.ambiguous_choice_keys(
            '{"0": "mcq"}', '{"0": ["A", "B"]}') == [0]
        assert qt.ambiguous_choice_keys("not json", "not json") == []

    def test_the_dashboard_and_the_panel_ask_the_same_question(self, route_source):
        assert "return ambiguous_choice_keys(" in route_source
        assert "return bool(_ambiguous_questions(_json_fields(dict(exam))))" in route_source


# ── 4. what a resolution would cost, computed by the writer's own arithmetic ──

def _parts(question_types, answer_key, weights=None, total=1, scoring=None):
    return exam_scoring.exam_parts({
        "question_types": question_types,
        "answer_key": answer_key,
        "question_weights": weights or {"0": 100},
        "total_questions": total,
        "question_scoring": scoring,
    })


class TestTheImpactIsTheWritersArithmetic:

    """The preview and the write are one function, so they cannot disagree.

    A teacher agrees to a number here and a pupil is given a mark there. Two
    implementations of "what does this submission score" is two answers to the
    question being decided — and the one that would be believed is the preview while
    the one that would be persisted is the writer.
    """

    KEYS = {"0": ["A", "B"]}

    def _subs(self):
        # One pupil ticked A alone, one ticked A and B, one ticked C.
        return [
            {"id": "1", "answers": {"0": "A"}, "penalty": 0, "final_score": 100},
            {"id": "2", "answers": {"0": ["A", "B"]}, "penalty": 0, "final_score": 100},
            {"id": "3", "answers": {"0": "C"}, "penalty": 0, "final_score": 0},
        ]

    def test_keeping_both_letters_costs_the_pupil_who_ticked_one(self):
        """The consequence of the exception, which is why it has to be shown.

        `mcq` reads a list as *any of these*; `mcq_multi` reads it as *exactly
        these*. So switching the question to the exception is not a no-op on marks:
        the pupil who ticked `A` alone no longer gets the question.
        """
        parts = _parts({"0": "mcq"}, self.KEYS)
        types, keys = exam_scoring.after_edit(parts, 0, qt.MCQ_MULTI, self.KEYS["0"])
        impact = exam_scoring.key_change_impact(
            self._subs(), parts=parts, question_types=types, answer_key=keys)
        assert impact["down"] == 1, impact
        assert impact["same"] == 2, impact
        assert impact["moved"] == 1, impact

    def test_narrowing_to_one_letter_costs_the_pupil_who_ticked_the_other(self):
        parts = _parts({"0": "mcq"}, self.KEYS)
        _types, keys = exam_scoring.after_edit(parts, 0, qt.MCQ, "A")
        impact = exam_scoring.key_change_impact(
            self._subs(), parts=parts, question_types=_types, answer_key=keys)
        assert impact["down"] == 1, impact
        assert impact["up"] == 0, impact

    def test_the_counts_are_what_the_writer_would_produce(self):
        """Measured against `rescore`, per pupil, not against a restatement."""
        parts = _parts({"0": "mcq"}, self.KEYS)
        types, keys = exam_scoring.after_edit(parts, 0, qt.MCQ, "A")
        impact = exam_scoring.key_change_impact(
            self._subs(), parts=parts, question_types=types, answer_key=keys)
        moved = 0
        for sub in self._subs():
            after, _score = exam_scoring.rescore(
                sub, parts=parts, question_types=types, answer_key=keys)
            if round(after - float(sub["final_score"]), 2) != 0:
                moved += 1
        assert impact["moved"] == moved == 1

    def test_an_edit_that_moves_nobody_says_so(self):
        parts = _parts({"0": "mcq"}, {"0": ["A", "B"]})
        subs = [{"id": "2", "answers": {"0": ["A", "B"]}, "penalty": 0, "final_score": 100}]
        types, keys = exam_scoring.after_edit(parts, 0, qt.MCQ_MULTI, ["A", "B"])
        impact = exam_scoring.key_change_impact(
            subs, parts=parts, question_types=types, answer_key=keys)
        assert impact["moved"] == 0
        assert impact["same"] == 1

    def test_the_preview_and_the_write_share_one_function(self, route_source):
        block = _route_block(route_source, "/exams/<exam_id>/answer-keys")
        assert block  # the route still exists
        assert "final, score = exam_scoring.rescore(sub, parts=parts)" in route_source, (
            "the recalculation must call the same function the preview does")


# ── 5. the panel ────────────────────────────────────────────────────────────

class TestTheReviewPanel:

    def test_the_list_is_scoped_to_the_teachers_own_papers(self, route_source):
        helper = route_source.split("def _teacher_exams_for_review(", 1)[1]
        helper = helper.split("\n@teacher_bp.route", 1)[0]
        assert '.eq("teacher_id", g.user_id)' in helper, (
            "the panel must not become a window onto somebody else's keys")
        assert '.eq("school_id", g.get("user_school_id"))' in helper, (
            "an admin_sekolah sees the school's, as on the exams list")

    def test_the_detail_route_guards_the_exam_it_opens(self, route_source):
        block = _route_block(route_source, "/answer-key-review/<exam_id>")
        assert "teacher_or_admin_required" in block
        assert '_guard_exam(supabase, exam_id, columns="*")' in block

    def test_the_apply_route_guards_and_respects_the_year_lock(self, route_source):
        block = _route_block(route_source, "/answer-key-review/<exam_id>/apply")
        assert 'methods=["POST"]' in block
        assert "teacher_or_admin_required" in block
        assert '@open_year_required("exam_id")' in block
        assert '_guard_exam(supabase, exam_id, columns="*")' in block

    def test_every_resolution_is_priced_before_it_is_offered(self, route_source):
        block = _route_block(route_source, "/answer-key-review/<exam_id>")
        assert "exam_scoring.after_edit(parts, index, MCQ_MULTI, stored)" in block
        assert "exam_scoring.after_edit(parts, index, MCQ, letter)" in block
        assert block.count("exam_scoring.key_change_impact(") == 2
        assert "attempts\": len(subs)" in block, (
            "the panel has to say how many already-marked sittings are in play")

    def test_the_resolution_must_still_apply_to_the_question(self, route_source):
        """Another tab may have resolved it between the load and the save."""
        block = _route_block(route_source, "/answer-key-review/<exam_id>/apply")
        assert ("if index not in qtypes or key_mode_drift(qtypes.get(index), "
                "stored.get(index)) != KEY_MODE_LOST:") in block

    def test_a_single_letter_resolution_must_be_one_of_the_stored_ones(self, route_source):
        block = _route_block(route_source, "/answer-key-review/<exam_id>/apply")
        assert "if kept not in letters:" in block

    def test_the_page_shows_the_numbers_before_the_button(self, review_html):
        assert 'data-review-question="{{ q.index }}"' in review_html
        assert 'data-review-resolution="{{ r.kind }}"' in review_html
        assert "{{ r.impact.up }}" in review_html
        assert "{{ r.impact.down }}" in review_html
        assert review_html.index("{{ r.impact.up }}") < review_html.index("Terapkan")

    def test_the_page_says_how_many_sittings_are_affected(self, review_html):
        assert "{{ detail.attempts }}" in review_html

    def test_the_control_is_two_forms_and_one_action(self, review_html):
        assert review_html.count("method=\"POST\"") == 1, (
            "one form per resolution, or the button cannot know which it applies")
        assert 'action="/teacher/answer-key-review/{{ detail.exam.id }}/apply"' in review_html


# ── 6. keeping a key and rewriting marks are two decisions ──────────────────

class TestTheRecomputeIsASeparateDecision:

    def test_it_happens_only_when_it_was_asked_for(self, route_source):
        block = _route_block(route_source, "/answer-key-review/<exam_id>/apply")
        assert 'if request.form.get("recompute") == "1":' in block
        # And nowhere else in the route: a `_recalculate_scores` call outside that
        # branch would rewrite marks the teacher never confirmed.
        before, after = block.split('if request.form.get("recompute") == "1":', 1)
        assert "_recalculate_scores" not in before, (
            "the resolution is written before the confirmation is read, so no marks "
            "may be rewritten before it")
        assert "_recalculate_scores(exam_id)" in after

    def test_the_save_says_out_loud_that_marks_did_not_move(self, route_source):
        block = _route_block(route_source, "/answer-key-review/<exam_id>/apply")
        assert "Skor murid belum diubah" in block

    def test_both_acts_are_written_to_the_audit_log(self, route_source):
        block = _route_block(route_source, "/answer-key-review/<exam_id>/apply")
        assert 'log_activity("resolve_ambiguous_key", "exam", exam_id,' in block
        assert 'log_activity("recompute", "exam", exam_id,' in block
        assert '"reason": "resolve_ambiguous_key"' in block

    def test_the_tick_is_offered_only_when_marks_would_move(self, review_html):
        assert "{% if r.impact.moved %}" in review_html
        assert 'name="recompute" value="1"' in review_html

    def test_the_impact_is_shown_beside_the_tick_it_gates(self, review_html):
        card = review_html.split("{% for r in q.resolutions %}", 1)[1]
        card = card.split("{% endfor %}", 1)[0]
        impact_at = card.index("{{ r.impact.up }}")
        tick_at = card.index('name="recompute"')
        assert impact_at < tick_at, (
            "the number a teacher is agreeing to has to come before the control that "
            "agrees to it")

    def test_the_tick_is_big_enough_to_touch(self, review_html):
        assert "min-h-[44px] cursor-pointer" in review_html


# ── 7. the way in ───────────────────────────────────────────────────────────

class TestTheWayIn:

    def test_the_dashboard_card_counts_the_same_set(self, route_source):
        assert ("exams_ambiguous_key = [e for e in exams if _has_ambiguous_key(e)]"
                in route_source)
        assert '"exams_ambiguous_key": exams_ambiguous_key,' in route_source
        assert "exams_ambiguous_key = []" in route_source, (
            "the template reads it unconditionally, so a teacher with no exams yet "
            "needs the default or the page raises")

    def test_the_dashboard_has_the_card(self):
        html = DASHBOARD.read_text(encoding="utf-8")
        assert "exams_ambiguous_key" in html
        card = html[html.index("exams_ambiguous_key"):]
        card = card[:card.index("{% endif %}")]
        assert "/teacher/answer-key-review" in card
        assert "t('Kunci ganda pada soal satu jawaban'" in card, (
            "the dashboard is a translated page, so every string is a pair")

    def test_the_exams_list_links_to_the_panel_when_there_is_something_to_review(
            self, route_source):
        assert ('ambiguous_ids = {e["id"] for e in exams if _has_ambiguous_key(e)}'
                in route_source)
        assert "ambiguous_ids=ambiguous_ids)" in route_source
        html = EXAMS.read_text(encoding="utf-8")
        assert "{% if ambiguous_ids %}" in html
        assert 'href="/teacher/answer-key-review"' in html

    def test_the_dashboard_column_list_still_carries_what_it_judges(self, route_source):
        """The card reads `question_types` and `answer_key`, so the select has to
        ask for both — the defect that made the "no answer key" card permanent."""
        block = _route_block(route_source, "/dashboard")
        own = re.search(r'supabase\.table\("exams"\)\.select\("([^"]+)"\)', block)
        assert own, "the dashboard no longer selects the exams it reports on"
        columns = {c.strip() for c in own.group(1).split(",")}
        assert {"answer_key", "question_types"} <= columns


# ── 8. the page says which language its copy is in ──────────────────────────

class TestThePagesLanguage:

    def test_the_review_page_declares_indonesian(self, review_html):
        """Its copy is Indonesian and it cannot switch, so it says so — the same
        declaration the answer-key page it belongs to makes."""
        assert review_html.split("{% extends", 1)[0].strip() == \
            "{% set content_lang = 'id' %}"

    def test_the_review_page_has_a_floor_like_every_other_page(self):
        """A page the committed baseline does not know is 'could not measure', which
        the i18n gate reports as a failure — a new page's floor has to be written
        deliberately, in the same change that adds it."""
        import json

        baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
        assert "teacher/answer_key_review.html" in baseline["pages"]
