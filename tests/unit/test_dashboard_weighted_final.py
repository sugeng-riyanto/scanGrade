"""The pupil's own page shows the same weighted final the teacher's table does.

The dashboard used to print a **simple mean** beside each subject — the average of
the released papers, which silently weights a short quiz the same as the final
exam. The teacher's table already reports the school's weighted policy, so the two
pages drew different numbers for the same pupil and the same subject. The pupil's
card must now read the same arithmetic: :func:`grade_weighting.compute`, reached
through one batched read because the pupil's page asks the transpose of the
teacher's — one pupil, every subject, rather than one subject, every pupil.

Two things this file pins beyond "the number is there":

* the batch read is scoped exactly as the single-subject one (school, subject and
  year on the papers; the pupil's own id on the submissions), so it cannot widen;
* the pupil's mark counts **released papers only** — the teacher's roster reads
  every graded paper, but a mark is not official to the pupil until it is released,
  which is the rule every other number on this page already follows.
"""
from __future__ import annotations

import re
from pathlib import Path

from app.services import grade_weighting as gw

ROOT = Path(__file__).resolve().parents[2]
STUDENT_ROUTES = ROOT / "app" / "routes" / "student.py"
SERVICE = ROOT / "app" / "services" / "grade_weighting.py"
DASHBOARD = ROOT / "app" / "templates" / "student" / "dashboard.html"


class _Resp:
    def __init__(self, data):
        self.data = data


class _Q:
    def __init__(self, store, table):
        self.store, self.table = store, table
        self.filters = []
        self.selected = []

    def select(self, *cols):
        self.selected = list(cols)
        return self

    def eq(self, col, val):
        self.filters.append((col, str(val)))
        return self

    def in_(self, col, values):
        self.filters.append((col, {str(v) for v in values}))
        return self

    def order(self, *a, **k):
        return self

    def execute(self):
        return _Resp(self.store.get(self.table, []))


class _Fake:
    def __init__(self, tables):
        self.tables = tables

    def table(self, name):
        return _Q(self.tables, name)


def _tables(submissions):
    return {
        "exams": [
            {"id": "e1", "subject_id": "sub1", "grade_component_type_id": "c-uts",
             "school_id": "s1", "school_year_id": "y1"},
            {"id": "e2", "subject_id": "sub1", "grade_component_type_id": "c-uas",
             "school_id": "s1", "school_year_id": "y1"},
            {"id": "e3", "subject_id": "sub2", "grade_component_type_id": None,
             "school_id": "s1", "school_year_id": "y1"},
        ],
        "submissions": submissions,
        "grade_weight_config": [
            {"component_id": "c-uts", "weight_percent": 40, "is_active": True,
             "school_id": "s1", "subject_id": "sub1", "school_year_id": "y1"},
            {"component_id": "c-uas", "weight_percent": 60, "is_active": True,
             "school_id": "s1", "subject_id": "sub1", "school_year_id": "y1"},
        ],
        "grade_component_type": [
            {"id": "c-uts", "name": "UTS", "school_id": "s1", "is_active": True},
            {"id": "c-uas", "name": "UAS", "school_id": "s1", "is_active": True},
        ],
    }


# ── the batched read ────────────────────────────────────────────────────────

class TestTheBatchRead:
    def test_it_scopes_the_exam_read_to_school_and_subjects(self):
        src = SERVICE.read_text(encoding="utf-8")
        body = src.split("def finals_for_student(")[1].split("\ndef ")[0]
        assert 'eq("school_id", school_id)' in body, (
            "the batch exam read is not bounded by the school")
        assert '.in_("subject_id", subjects)' in body, (
            "the batch exam read is not bounded by the subjects asked for")
        assert 'eq("school_year_id", year_id)' in body, (
            "the batch exam read is not bounded by the year, so another year's "
            "papers count toward this year's mark")

    def test_the_submission_read_is_scoped_to_the_pupil(self):
        src = SERVICE.read_text(encoding="utf-8")
        body = src.split("def finals_for_student(")[1].split("\ndef ")[0]
        assert '.in_("exam_id", list(exam_subject))' in body, (
            "the submission read is not bounded by the papers of these subjects")
        assert '.in_("student_id", [student])' in body, (
            "the submission read is not bounded to the pupil asking, so a "
            "classmate's mark could be read")

    def test_it_carries_the_release_columns_and_checks_them(self):
        """A mark is not official until the teacher releases it: the pupil's final
        must leave an unreleased paper out, and to do that the read must carry the
        columns ``result_released`` reads."""
        src = SERVICE.read_text(encoding="utf-8")
        body = src.split("def finals_for_student(")[1].split("\ndef ")[0]
        assert "is_published" in body and "status" in body, (
            "the batch read does not fetch the columns that decide release")
        assert "result_released(" in body, (
            "the pupil's final is built without the one release rule the app uses")


class TestTheBatchArithmetic:
    def test_each_subject_uses_its_own_weights(self):
        """sub1 has a config; sub2 has none and must fall back to the simple mean."""
        out = gw.finals_for_student(
            _Fake(_tables([
                {"student_id": "s1", "exam_id": "e1", "final_score": 80, "score": None,
                 "is_published": True, "status": "published"},
                {"student_id": "s1", "exam_id": "e3", "final_score": 90, "score": None,
                 "is_published": True, "status": "published"},
            ])),
            "s1", ["sub1", "sub2"], "y1", "s1")
        # sub1: UTS 80 (40%) + UAS 0 (60%, no score) = 32.0, weighted.
        assert out["sub1"]["mode"] == "weighted"
        assert out["sub1"]["final"] == 32.0
        # sub2: no config, so the plain mean of its one released paper.
        assert out["sub2"]["mode"] == "simple"
        assert out["sub2"]["final"] == 90.0

    def test_an_unreleased_paper_is_left_out_of_the_pupil_s_final(self):
        """The teacher's roster counts a graded-but-unreleased paper; the pupil's
        page must not, so the same row is excluded here."""
        tables = _tables([
            {"student_id": "s1", "exam_id": "e3", "final_score": 90, "score": None,
             "is_published": True, "status": "published"},
            {"student_id": "s1", "exam_id": "e3", "final_score": 10, "score": None,
             "is_published": False, "status": "graded"},
        ])
        # The fake returns the whole table, so both rows are read; only the released
        # one may reach the arithmetic. A simple-mean subject keeps the point clear.
        out = gw.finals_for_student(_Fake(tables), "s1", ["sub2"], "y1", "s1")
        assert out["sub2"]["final"] == 90.0, (
            "an unreleased mark leaked into the pupil's own final")

    def test_it_returns_an_entry_for_every_subject_even_with_no_rows(self):
        out = gw.finals_for_student(_Fake(_tables([])), "s1",
                                    ["sub1", "sub2"], "y1", "s1")
        assert set(out) == {"sub1", "sub2"}, (
            "a subject the pupil has no papers in must still answer, or the card "
            "cannot tell 'no configuration' from 'not asked'")
        # Weighting answers 0.0 for an empty subject (missing component = zero),
        # so the page needs `scored` to tell "no mark yet" from a real zero.
        assert out["sub1"]["scored"] == 0
        assert out["sub2"]["scored"] == 0

    def test_a_released_score_is_counted_so_a_real_zero_is_not_hidden(self):
        out = gw.finals_for_student(
            _Fake(_tables([
                {"student_id": "s1", "exam_id": "e3", "final_score": 0, "score": None,
                 "is_published": True, "status": "published"},
            ])),
            "s1", ["sub2"], "y1", "s1")
        assert out["sub2"]["scored"] == 1 and out["sub2"]["final"] == 0.0, (
            "a real zero must still be a mark, not read as 'no mark yet'")

    def test_no_school_or_pupil_is_an_empty_answer(self):
        assert gw.finals_for_student(_Fake(_tables([])), "", ["sub1"], "y1", "s1") == {}
        assert gw.finals_for_student(_Fake(_tables([])), "s1", ["sub1"], "y1", "") == {}

    def test_it_agrees_with_the_single_subject_read_the_teacher_uses(self):
        """The whole point: on the same released data, the pupil's batched read and
        the teacher's roster read must return the same number. Feed only released
        rows so the release filter cannot be what differs."""
        released = [
            {"student_id": "s1", "exam_id": "e1", "final_score": 80, "score": None,
             "is_published": True, "status": "published"},
            {"student_id": "s1", "exam_id": "e2", "final_score": 60, "score": None,
             "is_published": True, "status": "published"},
        ]
        tables = _tables(released)
        batch = gw.finals_for_student(_Fake(tables), "s1", ["sub1"], "y1", "s1")
        single = gw.subject_finals(_Fake(tables), "s1", "sub1", "y1", ["s1"])
        assert batch["sub1"]["final"] == single["s1"]["final"], (
            "the pupil's card and the teacher's table would print two numbers")
        assert batch["sub1"]["mode"] == single["s1"]["mode"] == "weighted"


# ── the dashboard wiring ────────────────────────────────────────────────────

class TestTheDashboardRoute:
    def _body(self) -> str:
        return STUDENT_ROUTES.read_text(encoding="utf-8"
                                        ).split("def dashboard(")[1].split("\ndef ")[0]

    def test_it_computes_the_weighted_finals_through_the_shared_service(self):
        body = self._body()
        assert "grade_weighting.finals_for_student(" in body, (
            "the pupil dashboard does not use the shared weighted read, so its "
            "number can drift from the teacher's table")

    def test_it_asks_for_the_subjects_the_class_offers(self):
        body = self._body()
        assert re.search(r"finals_for_student\(\s*supabase,\s*student_school_id", body), (
            "the weighted read must be scoped to the pupil's own school")
        assert "running_year_id" in body.split("finals_for_student(")[1][:200], (
            "the weighted read must be scoped to the running year")

    def test_it_hands_the_finals_to_the_template(self):
        body = self._body()
        assert '"subject_finals"' in body, (
            "the finals are computed and then dropped from the template data")

    def test_the_cache_key_moved_so_a_stale_entry_cannot_render_blank(self):
        src = STUDENT_ROUTES.read_text(encoding="utf-8")
        assert '"dash:v4:' not in src, (
            "a cached v4 entry has no `subject_finals`, so it would render the "
            "new mark as absent for its whole TTL — the key must move")


# ── the subject card ────────────────────────────────────────────────────────

class TestTheSubjectCard:
    def _loop(self) -> str:
        html = DASHBOARD.read_text(encoding="utf-8")
        loop = re.search(
            r"{%\s*for\s+\w+\s+in\s+class_subjects\s*%}(.*?){%\s*endfor\s*%}",
            html, re.S)
        assert loop, "the subject card has no loop over the class's subjects"
        return loop.group(1)

    def test_each_subject_reads_its_weighted_final_by_id(self):
        loop = self._loop()
        assert "subject_finals.get(" in loop, (
            "the subject card still prints the plain average, not the weighted "
            "final the teacher's table reports")
        assert re.search(r"subject_finals\.get\(\s*\w+\.id", loop), (
            "the final must be looked up by subject id, which is what the weighted "
            "read is keyed by — a name lookup can miss")

    def test_a_subject_with_no_scored_paper_reads_as_no_mark_not_zero(self):
        loop = self._loop()
        assert "fin.scored" in loop, (
            "a weighted subject with no scored paper would print a hard 0")

    def test_the_card_names_which_mark_it_is_showing(self):
        loop = self._loop()
        assert re.search(r"\.mode\s*==\s*'weighted'", loop), (
            "a weighted mark and a simple mean must be told apart, as the teacher's "
            "table already tells them apart")
        assert re.search(r"t\('(Berbobot|Weighted)", loop), (
            "the mode label is not bilingual")

    def test_the_mark_still_follows_the_switch(self):
        loop = self._loop()
        assert 'x-show="showScores"' in loop, (
            "the weighted mark prints regardless of the switch, the exact bug the "
            "switch was made to stop")

    def test_the_card_is_bilingual(self):
        assert "t('" in self._loop()
