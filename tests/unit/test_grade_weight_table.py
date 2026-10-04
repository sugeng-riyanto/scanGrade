"""The teacher's grade table, its exports, and the policy behind the number.

The weighted arithmetic itself is pinned in ``test_grade_weighting``. This file
guards the *plumbing* that could quietly hand the right number to the wrong person
or the wrong subject:

* ``subject_finals`` reads exams scoped to the school **and** the subject (and the
  year when one is given), and reads submissions only for those exam ids and the
  pupil ids it was handed — a pupil outside the list is never read;
* the teacher's roster route is scoped by their own assignments, and a requested
  subject they do not teach falls back to their default rather than leaking;
* the roster carries Kelas, Nama and NISN, and prints the weighted final with a
  per-component breakdown and an honest fallback label;
* both exports share the same read as the page, so they cannot disagree;
* the exam builder tags a paper with a component the school owns — and drops one
  it does not, which would otherwise be a cross-tenant reference;
* the config routes are the school admin's alone.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.services import grade_weighting as gw

ROOT = Path(__file__).resolve().parents[2]
TEACHER = ROOT / "app" / "routes" / "teacher.py"
ADMIN_SCHOOL = ROOT / "app" / "routes" / "admin_sekolah.py"
SERVICE = ROOT / "app" / "services" / "grade_weighting.py"
STUDENTS_HTML = ROOT / "app" / "templates" / "teacher" / "students.html"
WEIGHTS_HTML = ROOT / "app" / "templates" / "admin_sekolah" / "grade_weights.html"
EXAM_FORM = ROOT / "app" / "templates" / "teacher" / "exam_form.html"
BASE = ROOT / "app" / "templates" / "base.html"


class _Resp:
    def __init__(self, data):
        self.data = data


class _Q:
    """A fake that records which scopes a read carried."""

    def __init__(self, store, table):
        self.store, self.table = store, table
        self.filters, self.selected = [], []
        self.op = "select"

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
            {"id": "e1", "grade_component_type_id": "c-uts", "school_id": "s1",
             "subject_id": "sub1", "school_year_id": "y1"},
            {"id": "e2", "grade_component_type_id": None, "school_id": "s1",
             "subject_id": "sub1", "school_year_id": "y1"},
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


# ── subject_finals scoping and arithmetic ───────────────────────────────────

class TestSubjectFinals:
    def test_it_scopes_the_exam_read_to_school_subject_and_year(self):
        """A paper of another subject or another school must never be counted."""
        src = SERVICE.read_text(encoding="utf-8")
        body = src.split("def subject_finals(")[1].split("\ndef ")[0]
        assert 'eq("school_id", school_id)' in body and 'eq("subject_id", subject_id)' in body
        assert 'eq("school_year_id", year_id)' in body, (
            "the exam read is not bounded by the year, so another year's papers count")

    def test_it_reads_submissions_only_for_the_pupils_it_was_given(self):
        src = SERVICE.read_text(encoding="utf-8")
        body = src.split("def subject_finals(")[1].split("\ndef ")[0]
        assert '.in_("student_id", students)' in body, (
            "the submission read is not bounded by the pupil ids, so a classmate's "
            "mark could be read")
        assert '.in_("exam_id", list(exam_components))' in body

    def test_the_weighted_arithmetic_runs_on_the_rows_it_reads(self):
        fake = _Fake(_tables([
            {"student_id": "s1", "exam_id": "e1", "final_score": 80, "score": None},
            {"student_id": "s1", "exam_id": "e2", "final_score": 100, "score": None},
            {"student_id": "s2", "exam_id": "e1", "final_score": 50, "score": None},
        ]))
        out = gw.subject_finals(fake, "s1", "sub1", "y1", ["s1", "s2"])
        # s1: UTS 80 (40%) + UAS 0 (60%, no score) = 32.0; the 100 is untagged.
        assert out["s1"]["mode"] == "weighted"
        assert out["s1"]["final"] == 32.0
        assert out["s1"]["untagged"] == 1
        # s2: only UTS 50 * 40% = 20.0
        assert out["s2"]["final"] == 20.0

    def test_no_config_falls_back_to_the_simple_mean(self):
        tables = _tables([
            {"student_id": "s1", "exam_id": "e1", "final_score": 70, "score": None},
            {"student_id": "s1", "exam_id": "e2", "final_score": 90, "score": None},
        ])
        tables["grade_weight_config"] = []
        out = gw.subject_finals(_Fake(tables), "s1", "sub1", "y1", ["s1"])
        assert out["s1"]["mode"] == "simple"
        assert out["s1"]["final"] == 80.0


# ── the teacher's roster route ──────────────────────────────────────────────

class TestTheRosterRoute:
    def test_it_builds_the_table_through_one_shared_context(self):
        src = TEACHER.read_text(encoding="utf-8")
        assert "def _grade_table(" in src, "the roster lost its shared context"
        assert "def _grade_roster_context(" in src
        assert src.count("_grade_table(supabase") >= 3, (
            "the page and both exports must share one read, or they can disagree")

    def test_a_guru_sees_only_their_own_assignments(self):
        src = TEACHER.read_text(encoding="utf-8")
        body = src.split("def _grade_roster_context(")[1].split("\ndef ")[0]
        assert "current_pairs(" in body, (
            "the roster must scope a guru to their own assigned pairs")
        assert 'role_name == "admin_sekolah"' in body

    def test_a_requested_subject_must_be_one_they_teach(self):
        src = TEACHER.read_text(encoding="utf-8")
        body = src.split("def _grade_table(")[1].split("\ndef ")[0]
        assert "allowed_subjects" in body and "requested in allowed_subjects" in body, (
            "a requested subject is not checked against the viewer's own list")

    def test_the_final_mark_comes_from_the_weighted_read(self):
        src = TEACHER.read_text(encoding="utf-8")
        body = src.split("def _grade_table(")[1].split("\ndef ")[0]
        assert "grade_weighting.subject_finals(" in body, (
            "the table does not use the shared weighted read")
        flat = " ".join(body.split())
        assert "subject_finals( supabase, school_id, subject_id, year_id" in flat, (
            "the weighted read must be scoped to the subject and year")

    def test_the_route_hands_class_and_nisn_to_the_template(self):
        src = TEACHER.read_text(encoding="utf-8")
        body = src.split("def _grade_table(")[1].split("\ndef ")[0]
        assert '"class_name"' in body and '"nisn"' in body, (
            "the table must carry Kelas and NISN, not only a name")
        assert "students_in_classes( supabase, school_id" in " ".join(body.split())


class TestTheRosterTemplate:
    def test_it_prints_class_name_and_nisn(self):
        html = STUDENTS_HTML.read_text(encoding="utf-8")
        assert "s.class_name" in html and "s.nisn" in html, (
            "the table does not render the class and NISN columns")
        assert re.search(r"t\('Kelas','Class'\)", html) and "NISN" in html

    def test_it_shows_the_weighted_final_and_a_breakdown(self):
        html = STUDENTS_HTML.read_text(encoding="utf-8")
        assert "s.final" in html, "the final mark is not rendered"
        assert "s.detail" in html, "the per-component breakdown is missing"

    def test_it_labels_the_simple_mean_honestly(self):
        html = STUDENTS_HTML.read_text(encoding="utf-8")
        assert "s.mode==='simple'" in html, (
            "a fallback mark must say it is a simple mean, not pass as weighted")
        assert re.search(r"Simple mean", html)

    def test_it_surfaces_untagged_exams(self):
        html = STUDENTS_HTML.read_text(encoding="utf-8")
        assert "s.untagged" in html, (
            "papers with no component are silently dropped from the weighted total")

    def test_it_offers_search_and_sort(self):
        html = STUDENTS_HTML.read_text(encoding="utf-8")
        assert "x-model=\"search\"" in html and "setSort(" in html


class TestTheExports:
    def test_both_formats_exist_behind_the_teacher_guard(self):
        src = TEACHER.read_text(encoding="utf-8")
        for path in ("/students/export.xlsx", "/students/export.pdf"):
            block = src.split(f'route("{path}")')[1].split("@teacher_bp.route")[0]
            assert "@teacher_or_admin_required" in block, f"{path} is unguarded"

    def test_the_xlsx_carries_one_column_per_component(self):
        src = TEACHER.read_text(encoding="utf-8")
        body = src.split("def students_export_xlsx(")[1].split("\n@teacher_bp")[0]
        assert "c[\"name\"] for c in ctx[\"components\"]" in body, (
            "the spreadsheet drops the per-component detail the request asked for")
        assert '"Nilai Akhir" if weighted else "Rata-rata"' in body

    def test_the_pdf_footnotes_the_weights(self):
        src = TEACHER.read_text(encoding="utf-8")
        body = src.split("def students_export_pdf(")[1].split("\n@teacher_bp")[0]
        assert "Bobot:" in body, "the PDF does not name the weights it used"


# ── the admin weight config ─────────────────────────────────────────────────

class TestTheAdminConfig:
    def test_the_page_and_writes_are_admin_only(self):
        src = ADMIN_SCHOOL.read_text(encoding="utf-8")
        for path in ("/grade-weights\"", "/grade-weights/components\"",
                     "/grade-weights/components/<component_id>/update",
                     "/grade-weights/<subject_id>"):
            assert f'route("{path}' in src, f"missing route {path}"
        block = src.split('route("/grade-weights/<subject_id>", methods=["POST"])')[1].split("\n@admin_sekolah_bp.route")[0]
        assert "@admin_sekolah_required" in block
        assert '@require_school_access("subjects", "subject_id")' in block, (
            "the save is not scoped to the school that owns the subject")

    def test_a_non_100_total_is_refused(self):
        fake = _Fake({
            "subjects": [{"id": "sub1"}],
            "grade_component_type": [{"id": "c1", "school_id": "s1", "is_active": True,
                                      "name": "UTS"}],
            "grade_weight_config": [],
        })
        ok, out = gw.save_config(fake, "s1", "sub1", "y1", {"c1": 80})
        assert not ok and out["status"] == 400 and "100" in out["error"]

    def test_a_foreign_component_is_refused(self):
        fake = _Fake({
            "subjects": [{"id": "sub1"}],
            "grade_component_type": [{"id": "c1", "school_id": "s1", "is_active": True,
                                      "name": "UTS"}],
            "grade_weight_config": [],
        })
        ok, out = gw.save_config(fake, "s1", "sub1", "y1", {"c-other": 100})
        assert not ok and out["status"] == 403


class TestTheWeightsPage:
    def test_the_matrix_validates_100_before_saving(self):
        html = WEIGHTS_HTML.read_text(encoding="utf-8")
        assert "canSave(" in html and "this.required" in html, (
            "the Save button must be gated on the row summing to 100")

    def test_it_is_linked_from_the_sidebar(self):
        html = BASE.read_text(encoding="utf-8")
        assert '/admin-sekolah/grade-weights' in html, (
            "a page no link reaches is a page an operator has to know the URL for")


# ── exam tagging ────────────────────────────────────────────────────────────

class TestExamTagging:
    def test_a_foreign_component_is_dropped_to_uncategorised(self):
        src = TEACHER.read_text(encoding="utf-8")
        body = src.split("def _resolve_grade_component(")[1].split("\ndef ")[0]
        assert "component_ids(" in body, (
            "a posted component id must be checked against the school's own list, "
            "or a paper could reference another school's component")
        assert "return None" in body

    def test_the_builder_asks_for_the_components(self):
        src = TEACHER.read_text(encoding="utf-8")
        assert "def _grade_components_by_subject(" in src
        assert src.count("grade_components=_grade_components_by_subject(") == 2, (
            "both the new and the edit form must receive the component list")

    def test_the_form_offers_a_component_picker(self):
        html = EXAM_FORM.read_text(encoding="utf-8")
        assert 'name="grade_component_type_id"' in html, (
            "the builder has no grade-component field, so papers cannot be tagged")
        assert "gradeComponentPicker" in html

    def test_the_component_is_persisted_on_create_and_update(self):
        src = TEACHER.read_text(encoding="utf-8")
        assert src.count('"grade_component_type_id": grade_component_id,') == 2, (
            "the tag must be written on both the create and the update door")
