"""The pupil dashboard answers for the running school year, and only it.

Reported: the dashboard mixed a pupil's class and marks across years — the subject
list could be last year's offering and the average could fold in every paper the
pupil ever sat. This file pins the three scopes that make the page year-aware:

* the class is the pupil's membership **for the running year** (``student_enrollment``
  first, the profile pointer only when it does not contradict the year);
* the subject list and count come from *that* class, not the profile pointer;
* every score is filtered to papers of the running year, with the honest rule that
  an unknown year on either side counts (a school with no active year is not
  scoped, and a paper whose year column is empty must not vanish).

It also pins the other half: a paper is *dated* to the running year at every write
door, so the column the dashboard reads is actually populated going forward.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.routes.student import _year_scoped

ROOT = Path(__file__).resolve().parents[2]
STUDENT = ROOT / "app" / "routes" / "student.py"
TEACHER = ROOT / "app" / "routes" / "teacher.py"
EXAM = ROOT / "app" / "routes" / "exam.py"
DASHBOARD = ROOT / "app" / "templates" / "student" / "dashboard.html"


def _dashboard_body() -> str:
    src = STUDENT.read_text(encoding="utf-8")
    return src.split("def dashboard(")[1].split("\n@student_bp.route")[0]


class TestTheYearRule:
    """The rule that decides whether one paper counts toward this year."""

    def test_unknown_running_year_is_not_scoped(self):
        assert _year_scoped("2019", None) is True
        assert _year_scoped("2019", "") is True

    def test_a_paper_with_no_year_is_kept(self):
        """A paper written before 046 (or not yet tagged) must not disappear."""
        assert _year_scoped(None, "y-now") is True
        assert _year_scoped("", "y-now") is True

    def test_the_running_year_counts(self):
        assert _year_scoped("y-now", "y-now") is True

    def test_another_year_does_not(self):
        assert _year_scoped("y-last", "y-now") is False


class TestTheClassIsTheRunningYears:
    def test_it_reads_the_membership_for_the_running_year(self):
        src = STUDENT.read_text(encoding="utf-8")
        body = src.split("def _class_for_running_year(")[1].split("\ndef ")[0]
        assert "enrollment.current_class_id(" in body, (
            "the class must come from the per-year membership, or a promoted "
            "pupil reads last year's class")
        assert "year_of_class(" in body, (
            "the profile pointer must be checked against the year before it is used")

    def test_the_dashboard_resolves_the_running_year(self):
        body = _dashboard_body()
        assert "active_school_year(" in body, (
            "the dashboard never resolves the running school year")
        assert "_class_for_running_year(" in body, (
            "the dashboard does not scope the class to the running year")

    def test_the_subject_list_uses_the_year_class(self):
        body = _dashboard_body()
        assert re.search(r"subjects_for_class\(\s*student_school_id\s*,\s*student_class_id\s*\)", body), (
            "the subject list must read the year-resolved class, not the raw pointer")
        assert "subjects_for_class(student_school_id, profile_class_id)" not in body, (
            "the subject list still reads the un-scoped profile pointer")


class TestTheScoresAreYearScoped:
    def test_the_submission_read_carries_the_exam_year(self):
        src = STUDENT.read_text(encoding="utf-8")
        block = src.split("SUBMISSION_COLUMNS = (")[1].split(")")[0]
        assert "school_year_id" in block, (
            "the submissions embed must select the exam's year, or the page cannot "
            "scope a mark")

    def test_every_completed_mark_passes_the_year_rule(self):
        body = _dashboard_body()
        assert "_year_scoped(" in body, "no score is filtered by year"
        assert re.search(r"completed_exams\.append", body), "the cards vanished"
        # The filter must run before the append, or the card is already in.
        head = body.split("completed_exams.append")[0]
        assert "_year_scoped(" in head

    def test_the_offered_list_is_year_scoped_too(self):
        body = _dashboard_body()
        assert "_year_scoped(e.get(\"school_year_id\"), running_year_id)" in body, (
            "a paper left published from a previous year is still on offer")

    def test_the_cache_key_moved_with_the_shape(self):
        body = _dashboard_body()
        assert 'dash:v4:' in body, (
            "an entry cached under the old key answers a different question")


class TestPapersAreDatedAtTheWriteDoors:
    def test_the_builder_dates_a_paper_on_create_and_update(self):
        src = TEACHER.read_text(encoding="utf-8")
        assert src.count('"school_year_id": active_year_id,') == 2, (
            "both the create and the update door must date the paper")
        assert src.count("active_year_id = (ta_service.active_school_year(") == 2

    def test_the_school_year_column_survives_the_legacy_fallback(self):
        src = TEACHER.read_text(encoding="utf-8")
        assert '"school_year_id", "subject_id"' in src, (
            "a schema that predates the column must drop it, not fail the save")

    def test_a_duplicate_is_dated_to_now_not_the_year_it_came_from(self):
        src = TEACHER.read_text(encoding="utf-8")
        body = src.split("def duplicate_exam(")[1].split("\n@teacher_bp.route")[0]
        assert 'new_data["school_year_id"]' in body, (
            "a copy inherits the source's year, so it is filed as last year's paper")

    def test_the_api_create_route_dates_the_paper(self):
        src = EXAM.read_text(encoding="utf-8")
        body = src.split("def create_exam(")[1].split("\n@exam_bp.route")[0]
        assert 'filtered["school_year_id"]' in body


class TestThePageSaysWhichYear:
    def test_the_template_shows_the_running_year(self):
        html = DASHBOARD.read_text(encoding="utf-8")
        assert "year_name" in html, (
            "the page is scoped to a year but never says which one")
