"""Each subject on the pupil's card names the teacher assigned to teach it.

The pupil's subject list was anonymous: a class offered Mathematics, and nothing
on the page said who taught it. The assignment already exists —
`teacher_assignments` holds the *(teacher, class, subject)* pair the admin made —
so this reads that pair once per class and puts the teacher's name beside the
subject.

Two things this file pins beyond "the name is there":

* the read is the **same active-pair rule** the rest of the app uses
  (`teacher_assignments.row_is_active`: `status='active'` and the year), so a pair
  the admin removed does not keep naming a teacher on the card;
* it is scoped to the pupil's own school and class, read once per class rather than
  once per pupil, and the caches that feed it are invalidated when an admin edits
  the matrix — a change must not sit behind a five-minute TTL.
"""
from __future__ import annotations

import re
from pathlib import Path

from app.utils import req_cache

ROOT = Path(__file__).resolve().parents[2]
STUDENT_ROUTES = ROOT / "app" / "routes" / "student.py"
REQ_CACHE = ROOT / "app" / "utils" / "req_cache.py"
DASHBOARD = ROOT / "app" / "templates" / "student" / "dashboard.html"
ADMIN_SCHOOL = ROOT / "app" / "routes" / "admin_sekolah.py"


# ── the pure mapping ────────────────────────────────────────────────────────

class TestTheTeacherMap:
    def test_it_maps_each_subject_to_its_teacher_name(self):
        out = req_cache._teacher_map(
            [{"teacher_id": "t1", "subject_id": "sub1", "status": "active",
              "school_year": "2026/2027"}],
            {"t1": "Bu Sari"}, "2026/2027")
        assert out == {"sub1": ["Bu Sari"]}

    def test_a_soft_closed_pair_names_no_teacher(self):
        """`inactive` is the admin's removal; the card must not keep the name."""
        out = req_cache._teacher_map(
            [{"teacher_id": "t1", "subject_id": "sub1", "status": "inactive",
              "school_year": "2026/2027"}],
            {"t1": "Bu Sari"}, "2026/2027")
        assert out == {}, "a removed assignment still names a teacher on the card"

    def test_a_pair_from_another_year_is_not_the_running_one(self):
        out = req_cache._teacher_map(
            [{"teacher_id": "t1", "subject_id": "sub1", "status": "active",
              "school_year": "2025/2026"}],
            {"t1": "Bu Sari"}, "2026/2027")
        assert out == {}, "last year's teacher is shown as this year's"

    def test_a_row_with_no_year_is_still_in_force(self):
        """A row written before migration 045 carries no year and is a real
        assignment — the same discipline `row_is_active` applies everywhere."""
        out = req_cache._teacher_map(
            [{"teacher_id": "t1", "subject_id": "sub1", "status": "active"}],
            {"t1": "Bu Sari"}, "2026/2027")
        assert out == {"sub1": ["Bu Sari"]}

    def test_two_teachers_one_subject_are_both_named_once_each(self):
        out = req_cache._teacher_map(
            [{"teacher_id": "t1", "subject_id": "sub1", "status": "active"},
             {"teacher_id": "t2", "subject_id": "sub1", "status": "active"},
             {"teacher_id": "t1", "subject_id": "sub1", "status": "active"}],
            {"t1": "Bu Sari", "t2": "Pak Budi"}, "2026/2027")
        assert out["sub1"] == ["Bu Sari", "Pak Budi"]

    def test_a_row_whose_teacher_has_no_name_is_dropped(self):
        out = req_cache._teacher_map(
            [{"teacher_id": "t9", "subject_id": "sub1", "status": "active"}],
            {}, "2026/2027")
        assert out == {}, "an unnamed teacher would print a blank beside the subject"


# ── the cached read ─────────────────────────────────────────────────────────

class TestTheCachedRead:
    def test_it_exists_and_is_keyed_per_class(self):
        assert hasattr(req_cache, "teachers_for_class")
        assert req_cache.class_teachers_key("c1") == "classteachers:c1"

    def test_the_read_is_scoped_to_the_school_and_the_class(self):
        body = REQ_CACHE.read_text(encoding="utf-8"
                                   ).split("def teachers_for_class(")[1].split("\ndef ")[0]
        assert 'table("teacher_assignments")' in body, (
            "the teacher map does not read the assignment pairs")
        assert 'eq("school_id", school_id)' in body and 'eq("class_id", class_id)' in body, (
            "the read is not bounded to the pupil's school and class")

    def test_it_resolves_names_from_profiles_in_one_read(self):
        body = REQ_CACHE.read_text(encoding="utf-8"
                                   ).split("def teachers_for_class(")[1].split("\ndef ")[0]
        assert 'table("profiles")' in body and 'in_("id", teacher_ids)' in body, (
            "names must come from one profiles read, not one per teacher")

    def test_it_uses_the_shared_active_pair_rule(self):
        """The filter lives in the pure `_teacher_map`, which `teachers_for_class`
        calls — one rule, applied where the rows are turned into names."""
        src = REQ_CACHE.read_text(encoding="utf-8")
        mapper = src.split("def _teacher_map(")[1].split("\ndef ")[0]
        assert "row_is_active(" in mapper, (
            "the read must apply the one active-pair rule, or it drifts from the "
            "rest of the app")
        caller = src.split("def teachers_for_class(")[1].split("\ndef ")[0]
        assert "_teacher_map(" in caller, (
            "teachers_for_class must build its names through the filtered mapper")

    def test_it_is_cached_per_class_like_the_subject_list(self):
        body = REQ_CACHE.read_text(encoding="utf-8"
                                   ).split("def teachers_for_class(")[1].split("\ndef ")[0]
        assert "ttl(class_teachers_key(class_id)" in body, (
            "every pupil in a class must share one read, not repeat it")

    def test_an_edit_to_the_matrix_drops_the_class_cache(self):
        """An admin assigning a teacher must see it on the card without waiting
        out the TTL, so the invalidation reaches the per-class keys."""
        calls = []
        original = req_cache.invalidate
        req_cache.invalidate = lambda *keys: calls.extend(keys)
        try:
            req_cache.invalidate_teacher_assignments("t1", "s1", class_ids=["c1", "c2"])
        finally:
            req_cache.invalidate = original
        assert req_cache.class_teachers_key("c1") in calls
        assert req_cache.class_teachers_key("c2") in calls

    def test_the_matrix_editor_passes_the_classes_it_touched(self):
        src = ADMIN_SCHOOL.read_text(encoding="utf-8")
        assert re.search(
            r"invalidate_teacher_assignments\(teacher_id, sid,\s*class_ids=", src), (
            "the matrix save does not invalidate the per-class teacher cache")


# ── the dashboard wiring ────────────────────────────────────────────────────

class TestTheDashboardRoute:
    def _body(self) -> str:
        return STUDENT_ROUTES.read_text(encoding="utf-8"
                                        ).split("def dashboard(")[1].split("\ndef ")[0]

    def test_it_reads_the_class_teachers(self):
        body = self._body()
        assert "teachers_for_class(" in body, (
            "the dashboard never reads who teaches the class's subjects")
        assert re.search(r"teachers_for_class\(\s*student_school_id,\s*student_class_id",
                         body), (
            "the teacher read must be scoped to the pupil's own school and class")

    def test_it_hands_the_map_to_the_template(self):
        body = self._body()
        assert '"class_teachers"' in body, (
            "the teacher map is read and then dropped from the template data")


# ── the subject card ────────────────────────────────────────────────────────

class TestTheSubjectCard:
    def _loop(self) -> str:
        html = DASHBOARD.read_text(encoding="utf-8")
        loop = re.search(
            r"{%\s*for\s+\w+\s+in\s+class_subjects\s*%}(.*?){%\s*endfor\s*%}",
            html, re.S)
        assert loop, "the subject card has no loop over the class's subjects"
        return loop.group(1)

    def test_each_subject_names_its_teacher(self):
        loop = self._loop()
        assert re.search(r"class_teachers\.get\(\s*\w+\.id", loop), (
            "the subject card does not look the teacher up by subject id")

    def test_a_subject_with_no_assigned_teacher_says_so(self):
        loop = self._loop()
        assert re.search(r"t\('(Belum ada guru|No teacher)", loop), (
            "a subject nobody is assigned would print a blank line")

    def test_the_card_is_bilingual(self):
        assert "t('" in self._loop()
