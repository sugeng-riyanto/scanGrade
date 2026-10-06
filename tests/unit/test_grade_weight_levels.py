"""A distribution is a subject's fact, but a school thinks in grade levels.

The weight page lists every subject flat, so nothing on it said *which* subjects
still follow the school default and which carry a distribution of their own — and
an admin who wants to undo a whole year's worth of overrides for Year 7 had to
click Save-to-default once per subject. Two things are added and guarded here:

* ``subject_levels.levels_by_subject`` / ``subjects_for_grade_level`` — the grade
  levels a subject is taught in. A subject is offered by a class **unless** a
  ``class_subjects`` row closes the pair, so a school that has never closed one
  teaches every subject in every level it has. The reset must derive its subject
  list from the server, never from the form.
* the page and its door — a per-level count of customised subjects, a one-click
  reset that returns them to the default, and a route that refuses a closed year.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.services import subject_levels as sl

ROOT = Path(__file__).resolve().parents[2]
ADMIN_SCHOOL = ROOT / "app" / "routes" / "admin_sekolah.py"
WEIGHTS_HTML = ROOT / "app" / "templates" / "admin_sekolah" / "grade_weights.html"

SCHOOL, OTHER = "sch-1", "sch-2"


class _Resp:
    def __init__(self, data):
        self.data = data


class _Q:
    """A fake that really filters, so a missing school scope is a real leak."""

    def __init__(self, store, table):
        self.store, self.table, self.filters = store, table, []

    def select(self, *a, **k):
        return self

    def eq(self, col, val):
        self.filters.append((col, str(val)))
        return self

    def in_(self, col, values):
        self.filters.append((col, {str(v) for v in values}))
        return self

    def order(self, *a, **k):
        return self

    def limit(self, *a, **k):
        return self

    def execute(self):
        def match(row):
            for col, want in self.filters:
                got = row.get(col)
                if isinstance(want, set):
                    if str(got) not in want:
                        return False
                elif str(got) != want:
                    return False
            return True
        return _Resp([dict(r) for r in self.store.get(self.table, []) if match(r)])


class _Sb(dict):
    def table(self, name):
        return _Q(self, name)


def _db(*, classes, subjects, pairs=()):
    return _Sb(
        classes=[{"id": cid, "grade_level": lvl, "school_id": SCHOOL}
                 for cid, lvl in classes],
        subjects=[{"id": sid, "school_id": SCHOOL, "is_active": active}
                  for sid, active in subjects],
        class_subjects=[{"subject_id": sid, "class_id": cid, "is_active": active,
                         "school_id": SCHOOL}
                        for sid, cid, active in pairs],
    )


# ── the read ─────────────────────────────────────────────────────────────────

class TestTheLevelsRead:
    def test_a_school_that_never_closed_a_pair_teaches_every_subject_everywhere(self):
        db = _db(classes=[("c7", "7"), ("c8", "8")],
                 subjects=[("s1", True), ("s2", True)])
        assert sl.levels_by_subject(db, SCHOOL) == {"s1": ["7", "8"], "s2": ["7", "8"]}
        assert set(sl.subjects_for_grade_level(db, SCHOOL, "7")) == {"s1", "s2"}

    def test_a_closed_pair_removes_a_subject_from_that_level_only(self):
        db = _db(classes=[("c7", "7"), ("c8", "8")],
                 subjects=[("s1", True), ("s2", True)],
                 pairs=[("s1", "c7", False)])
        levels = sl.levels_by_subject(db, SCHOOL)
        assert levels["s1"] == ["8"], "a class that closed the pair must drop the level"
        assert levels["s2"] == ["7", "8"]
        assert "s1" not in sl.subjects_for_grade_level(db, SCHOOL, "7")
        assert "s1" in sl.subjects_for_grade_level(db, SCHOOL, "8")

    def test_closing_every_class_of_a_level_removes_it_entirely(self):
        db = _db(classes=[("c7a", "7"), ("c7b", "7")],
                 subjects=[("s1", True)],
                 pairs=[("s1", "c7a", False), ("s1", "c7b", False)])
        assert sl.levels_by_subject(db, SCHOOL) == {"s1": []}
        assert sl.subjects_for_grade_level(db, SCHOOL, "7") == []

    def test_an_inactive_subject_is_not_offered(self):
        db = _db(classes=[("c7", "7")], subjects=[("s1", True), ("s2", False)])
        assert sl.levels_by_subject(db, SCHOOL) == {"s1": ["7"]}

    def test_a_foreign_schools_classes_do_not_leak_in(self):
        db = _db(classes=[("c7", "7")], subjects=[("s1", True)])
        db["classes"].append({"id": "x7", "grade_level": "7", "school_id": OTHER})
        db["subjects"].append({"id": "s9", "school_id": OTHER, "is_active": True})
        levels = sl.levels_by_subject(db, SCHOOL)
        assert "s9" not in levels, "another school's subject leaked into the levels read"
        assert levels["s1"] == ["7"]

    def test_no_classes_at_a_level_is_an_empty_offer(self):
        db = _db(classes=[("c7", "7")], subjects=[("s1", True)])
        assert sl.subjects_for_grade_level(db, SCHOOL, "9") == []

    def test_an_empty_level_is_never_a_question(self):
        db = _db(classes=[("c7", "7")], subjects=[("s1", True)])
        assert sl.subjects_for_grade_level(db, SCHOOL, "") == []
        assert sl.levels_by_subject(db, "") == {}


# ── the page route ───────────────────────────────────────────────────────────

def _body(name: str) -> str:
    src = ADMIN_SCHOOL.read_text(encoding="utf-8-sig")
    start = src.index(f"def {name}(")
    match = re.search(r"\r?\ndef ", src[start:])
    return src[start:start + match.start()] if match else src[start:]


class TestThePageReadsTheLevels:
    def test_the_weight_page_is_handed_the_levels(self):
        body = _body("admin_grade_weights")
        assert "levels_by_subject(" in body, (
            "the page cannot group by grade level without the levels read")
        assert "subject_levels=" in body and "grade_levels=" in body, (
            "the levels are read but not handed to the template")

    def test_the_template_carries_the_level_map(self):
        html = WEIGHTS_HTML.read_text(encoding="utf-8")
        assert "subject_levels|tojson" in html, (
            "the page does not receive the subject-to-level map")


# ── the reset door ───────────────────────────────────────────────────────────

class TestTheResetDoor:
    def test_it_is_admin_only_and_reads_the_level_from_the_body(self):
        src = ADMIN_SCHOOL.read_text(encoding="utf-8-sig")
        block = src.split('route("/grade-weights/reset-level"')[1].split("\n@admin_sekolah_bp.route")[0]
        assert "@admin_sekolah_required" in block
        assert 'payload.get("grade_level")' in block, (
            "the route does not read the level it is asked to reset")

    def test_it_derives_the_subjects_server_side(self):
        body = _body("admin_grade_reset_level")
        assert "subjects_for_grade_level(" in body, (
            "the level's subjects must be derived from the server, not the form")
        # The body names the level and nothing else: a subject list in the request
        # would let a form clear a subject the level does not teach.
        assert body.count("payload.get(") == 1, (
            "the route reads more than the level from the body")
        assert 'payload.get("grade_level")' in body

    def test_it_clears_only_customised_subjects(self):
        body = _body("admin_grade_reset_level")
        assert "configs_for_school(" in body, (
            "a subject already on the default has nothing to clear")
        assert "save_config(" in body and "{}" in body, (
            "the reset must clear through the same save the page uses")

    def test_a_closed_year_is_refused(self):
        body = _body("admin_grade_reset_level")
        assert "write_refusal(" in body, (
            "a closed year must be refused before any weight is cleared")

    def test_the_empty_level_is_refused(self):
        body = _body("admin_grade_reset_level")
        assert 'if not level' in body, "an empty level must be refused, not reset"


# ── the page's own controls ──────────────────────────────────────────────────

class TestTheStudentFacingControls:
    def test_the_page_counts_custom_vs_default(self):
        html = WEIGHTS_HTML.read_text(encoding="utf-8")
        assert "customCount(" in html and "followCount(" in html, (
            "the page does not surface how many subjects are customised")

    def test_each_level_carries_a_one_click_reset(self):
        html = WEIGHTS_HTML.read_text(encoding="utf-8")
        assert "data-level-resets" in html, "there is no per-level reset panel"
        assert "resetLevel(" in html, "a level has no one-click reset"

    def test_the_reset_posts_and_never_rides_a_get(self):
        html = WEIGHTS_HTML.read_text(encoding="utf-8")
        assert "this.post('/admin-sekolah/grade-weights/reset-level'" in html, (
            "the reset must go through a POST, so it cannot be triggered by a link")

    def test_the_level_copy_is_bilingual(self):
        html = WEIGHTS_HTML.read_text(encoding="utf-8")
        block = html.split("data-level-resets", 1)[1].split("data-weight-preview", 1)[0]
        assert re.search(r"t\('[^']+','[^']+'\)", block), (
            "the level panel carries no bilingual pair")
        assert "Tingkat" in block and "Level" in block
