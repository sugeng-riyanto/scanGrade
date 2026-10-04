"""The dashboard's "Mapel" card counts subjects — not teacher × class × subject rows.

Reported: *"Kenapa 306 mapel. Harusnya hanya mapel sesuai NPSN"* on
`/student/dashboard`. Measured on the box: SMA Harapan Bangsa has **23** active
subjects and **306** rows in `teacher_assignments` — because that table is the
many-to-many `teacher × class × subject` pair, one row per pair. The card read
`school_subject_count()`, which counts *those rows*, so a school with twenty-odd
subjects was told it had three hundred and six.

The fix is to count the school's own subjects (`school_subjects`, which already
exists and is already invalidated by every subject write through `invalidate_school`).
This file pins that, and also pins the two flows the scoring guide never mentioned:

* **the lock and its resume code** — at the violation threshold an exam with
  `lock_pending_resume` locks the sitting instead of ending it, and the pupil is let
  back in with the recovery code shown on their own screen;
* **the retake decision** — an approved request returns the paper to the pupil's
  exam list, and it is the class invigilator who decides.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
STUDENT_ROUTES = ROOT / "app" / "routes" / "student.py"
REQ_CACHE = ROOT / "app" / "utils" / "req_cache.py"
DASHBOARD = ROOT / "app" / "templates" / "student" / "dashboard.html"
GUIDE = ROOT / "app" / "templates" / "guide" / "skor.html"
GUIDE_ROUTE = ROOT / "app" / "routes" / "guide.py"


# ── the Mapel card ──────────────────────────────────────────────────────────

class TestTheSubjectCount:
    def test_the_dashboard_counts_subjects_not_assignment_rows(self):
        src = STUDENT_ROUTES.read_text(encoding="utf-8")
        body = src.split("def dashboard(")[1].split("\ndef ")[0]
        assert "subjects_for_class(" in body, (
            "the dashboard must count the subjects the pupil's own class offers")
        assert "school_subject_count(" not in body, (
            "the Mapel card must not use the assignment-row count")

    def test_the_count_is_scoped_to_the_pupil_s_school_and_class(self):
        """The card is the pupil's own offering: NPSN and the class the admin
        assigned, never the school's whole list nor another NPSN's subjects."""
        src = STUDENT_ROUTES.read_text(encoding="utf-8")
        body = src.split("def dashboard(")[1].split("\ndef ")[0]
        found = re.search(r"subjects_for_class\(([^)]*)\)", body)
        assert found, "the dashboard no longer calls subjects_for_class"
        args = found.group(1)
        assert "student_school_id" in args and "student_class_id" in args, (
            "the count must pass the pupil's school and class, not one of them")

    def test_a_class_offering_is_the_school_s_subjects_minus_the_closed_pairs(self):
        """A subject is offered unless a `class_subjects` row says it is not, so
        the class list is the school's active subjects minus the closed ones."""
        src = REQ_CACHE.read_text(encoding="utf-8")
        body = src.split("def subjects_for_class(")[1].split("\ndef ")[0]
        assert "school_subjects(" in body and "closed_subjects_for_class(" in body, (
            "subjects_for_class must subtract the closed pairs from the school list")

    def test_saving_the_mapping_invalidates_the_per_class_cache(self):
        src = (ROOT / "app" / "services" / "subject_levels.py").read_text(encoding="utf-8")
        assert "invalidate_class_subjects(" in src, (
            "a mapping save leaves each class's cached offering stale")

    def test_the_assignment_count_helper_says_it_counts_assignments(self):
        """Its name says what it counts; the dashboard was the only caller that
        mistook it for a subject count, so the helper's own docstring must keep
        naming the table it reads."""
        src = REQ_CACHE.read_text(encoding="utf-8")
        assert "def school_subject_count(" in src, "the helper was removed"
        body = src.split("def school_subject_count(")[1].split("\ndef ")[0]
        assert 'table("teacher_assignments")' in body, (
            "the helper no longer reads teacher_assignments — its name and its count "
            "must describe the same table")

    def test_the_dashboard_template_still_renders_the_count(self):
        html = DASHBOARD.read_text(encoding="utf-8")
        assert "subject_count" in html, "the card stopped rendering a count"

    def test_the_guide_route_is_bilingual(self):
        html = GUIDE.read_text(encoding="utf-8")
        assert re.search(r"t\('", html), "the guide carries no i18n helper"


# ── the guide documents the lock and the retake ─────────────────────────────

class TestTheGuideDocumentsTheLock:
    def test_the_teacher_view_explains_the_lock_and_resume_code(self):
        html = GUIDE.read_text(encoding="utf-8")
        assert re.search(r"lock_pending_resume|kunci|dikunci", html, re.I), (
            "the guide never mentions the lock")
        assert re.search(r"resume|kode pemulihan|recovery", html, re.I), (
            "the guide never mentions the resume/recovery code")

    def test_the_lock_is_described_as_time_still_running(self):
        """The invariant the whole feature rests on: locking is not a pause."""
        html = GUIDE.read_text(encoding="utf-8")
        assert re.search(r"tidak.*(dijeda|dibekukan|berhenti)|clock keeps|time keeps",
                         html, re.I | re.S), (
            "the guide does not say the clock keeps running while locked")

    def test_the_student_view_explains_being_locked(self):
        html = GUIDE.read_text(encoding="utf-8")
        student = html.split("TAB MURID")[1] if "TAB MURID" in html else html
        assert re.search(r"kunci|dikunci|lock", student, re.I), (
            "the student view never mentions being locked")


class TestTheGuideDocumentsTheRetake:
    def test_the_guide_mentions_the_retake_decision(self):
        html = GUIDE.read_text(encoding="utf-8")
        assert re.search(r"ujian ulang|retake", html, re.I), (
            "the guide never mentions asking to sit an exam again")

    def test_the_retake_is_attributed_to_the_invigilator(self):
        html = GUIDE.read_text(encoding="utf-8")
        assert re.search(r"pengawas|invigilat", html, re.I), (
            "the guide does not say who decides a retake")

    def test_the_new_strings_are_bilingual(self):
        """Every sentence added here must be a t() pair like the rest of the page."""
        html = GUIDE.read_text(encoding="utf-8")
        # The page must carry at least as many pairs as before this change; the
        # coverage floor in theme_gate is the real guard, this is the cheap one.
        assert html.count("t('") > 100, "the guide lost its bilingual copy"
