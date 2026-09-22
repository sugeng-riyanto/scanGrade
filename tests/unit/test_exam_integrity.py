"""What a student may see and do around an exam must not depend on the URL.

Three integrity problems are pinned here:

1. **The answer key was readable mid-exam.** Opening an exam creates a ``draft``
   submission, ``/student/results`` lists drafts, and ``/student/results/<id>``
   rendered ``exams.answer_key`` with no release check — so a student could open
   the result page for the exam they were still sitting and read the answers.
2. **Opening an exam required only ``status == 'active'``.** The exam *list*
   required ``is_published``, so an unpublished exam's URL still worked.
3. **Every access check swallowed its own errors.** A failed profile lookup was
   indistinguishable from a passed one, which is a check that fails open.

``app.utils.exam_access`` now holds the two rules, so the page routes, the
auto-save/sync APIs and the PDF export cannot drift apart again.
"""
import json
from types import SimpleNamespace

import pytest

from app.utils.exam_access import (
    class_assignment_allows, exam_class_ids, exam_sitting_allowed, result_released,
)


# ── fakes ────────────────────────────────────────────────────────

class FakeTable:
    """Minimal postgrest stand-in: only what the rules actually call."""

    def __init__(self, row, error=None):
        self._row = row
        self._error = error

    def select(self, *args, **kwargs):
        return self

    def eq(self, *args, **kwargs):
        return self

    def maybe_single(self):
        return self

    def execute(self):
        if self._error:
            raise self._error
        return SimpleNamespace(data=self._row)


class FakeSupabase:
    def __init__(self, profile=None, error=None):
        self._profile = profile
        self._error = error

    def table(self, name):
        assert name == "profiles", f"unexpected table {name}"
        return FakeTable(self._profile, self._error)


def exam(**over):
    base = {
        "id": "exam-1",
        "school_id": "school-A",
        "class_ids": [],
        "is_published": True,
        "status": "active",
    }
    base.update(over)
    return base


STUDENT = {"school_id": "school-A", "class_id": "class-7A"}


# ── result_released ──────────────────────────────────────────────

class TestResultReleased:
    def test_a_draft_is_not_released(self):
        """A draft exists from the moment the exam is opened — it is not a result."""
        assert result_released({"status": "draft", "is_published": False}) is False

    def test_a_waiting_submission_is_not_released(self):
        assert result_released({"status": "submitted", "is_published": False}) is False

    def test_graded_but_unpublished_is_not_released(self):
        """Marking in progress: the score exists but has not been returned."""
        assert result_released({"status": "graded", "is_published": False}) is False

    def test_graded_and_published_is_released(self):
        assert result_released({"status": "graded", "is_published": True}) is True

    def test_published_status_is_released(self):
        assert result_released({"status": "published", "is_published": True}) is True

    def test_missing_fields_are_not_released(self):
        assert result_released({}) is False


# ── exam_sitting_allowed ─────────────────────────────────────────

class TestExamSittingAllowed:
    def test_unpublished_exam_is_denied(self):
        """The exam list required is_published; opening an exam did not."""
        allowed, reason = exam_sitting_allowed(
            FakeSupabase(STUDENT), exam(is_published=False), "exam-1", "stu-1")
        assert allowed is False
        assert reason

    def test_inactive_exam_is_denied(self):
        allowed, _ = exam_sitting_allowed(
            FakeSupabase(STUDENT), exam(status="draft"), "exam-1", "stu-1")
        assert allowed is False

    def test_another_school_is_denied(self):
        allowed, reason = exam_sitting_allowed(
            FakeSupabase(STUDENT), exam(school_id="school-B"), "exam-1", "stu-1")
        assert allowed is False
        assert "sekolah" in reason.lower()

    def test_wrong_class_is_denied(self):
        allowed, reason = exam_sitting_allowed(
            FakeSupabase(STUDENT), exam(class_ids=["class-8B"]), "exam-1", "stu-1")
        assert allowed is False
        assert "kelas" in reason.lower()

    def test_a_student_with_no_class_is_denied_a_class_assigned_exam(self):
        """No class on file is not a wildcard — it must not match every class."""
        allowed, _ = exam_sitting_allowed(
            FakeSupabase({"school_id": "school-A", "class_id": None}),
            exam(class_ids=["class-7A"]), "exam-1", "stu-1")
        assert allowed is False

    def test_class_ids_as_a_json_string_are_understood(self):
        allowed, _ = exam_sitting_allowed(
            FakeSupabase(STUDENT), exam(class_ids=json.dumps(["class-7A"])),
            "exam-1", "stu-1")
        assert allowed is True

    def test_matching_school_and_class_is_allowed(self):
        allowed, reason = exam_sitting_allowed(
            FakeSupabase(STUDENT), exam(class_ids=["class-7A", "class-7B"]),
            "exam-1", "stu-1")
        assert allowed is True
        assert reason == ""

    def test_an_exam_assigned_to_no_class_reaches_nobody(self):
        """Assignment is a positive fact — an empty `class_ids` is not "everyone".

        This used to be allowed, on the reading that unticked classes meant the
        whole school. A pupil in X-B was therefore shown the papers written for
        X-A and XI-A, and every one of them refused them on the next click.
        """
        allowed, reason = exam_sitting_allowed(
            FakeSupabase(STUDENT), exam(class_ids=[]), "exam-1", "stu-1")
        assert allowed is False
        assert "kelas" in reason.lower()

    def test_a_failed_lookup_denies_rather_than_allows(self):
        """Fail CLOSED. The old checks wrapped themselves in `except: pass`, so a
        failed lookup read exactly like a successful one."""
        allowed, reason = exam_sitting_allowed(
            FakeSupabase(STUDENT, error=RuntimeError("db down")),
            exam(), "exam-1", "stu-1")
        assert allowed is False, "an exception must not let the student through"
        assert reason

    def test_missing_profile_denies(self):
        allowed, _ = exam_sitting_allowed(
            FakeSupabase(None), exam(), "exam-1", "stu-1")
        assert allowed is False


# ── the predicate the lists and the door share ────────────────────

class TestClassAssignment:
    """`class_assignment_allows` is the one answer both halves of the app use.

    Two places decide whether a pupil is *shown* an exam — the exam list and the
    dashboard, which filters the same rows — and one decides whether they may
    *open* it. When each carried its own copy, the list offered what the door
    refused, and the pupil met "Ujian ini tidak ditugaskan untuk kelas Anda."
    after clicking a card the app had just handed them.
    """

    def test_a_ticked_class_matches(self):
        assert class_assignment_allows(exam(class_ids=["class-7A"]), "class-7A") is True

    def test_another_class_does_not_match(self):
        assert class_assignment_allows(exam(class_ids=["class-7A"]), "class-8B") is False

    def test_no_assignment_matches_nobody(self):
        assert class_assignment_allows(exam(class_ids=[]), "class-7A") is False

    def test_no_class_on_file_is_not_a_wildcard(self):
        assert class_assignment_allows(exam(class_ids=["class-7A"]), None) is False
        assert class_assignment_allows(exam(class_ids=[]), None) is False

    def test_a_json_string_is_read_as_a_list(self):
        assert exam_class_ids({"class_ids": json.dumps(["class-7A"])}) == ["class-7A"]

    def test_a_broken_json_string_is_not_a_crash_and_not_a_match(self):
        assert exam_class_ids({"class_ids": "{"}) == []
        assert exam_class_ids({}) == []
        assert exam_class_ids({"class_ids": None}) == []
        # A bare string is not a list of classes, so it matches nothing.
        assert exam_class_ids({"class_ids": "class-7A"}) == []
        assert class_assignment_allows({"class_ids": "class-7A"}, "class-7A") is False

    def test_ids_of_any_type_compare_as_strings(self):
        assert exam_class_ids({"class_ids": [123]}) == ["123"]
        assert class_assignment_allows({"class_ids": [123]}, "123") is True

    def test_the_door_agrees_with_the_predicate_on_every_shape(self):
        """Whatever the rule becomes, the list and the door must not disagree.

        The route tests in `test_exam_assignment` assert this from the page's
        side; this asserts it directly, over every shape `class_ids` arrives in.
        """
        for ids in ([], ["class-7A"], ["class-8B"], json.dumps(["class-7A"]), "{"):
            for klass in ("class-7A", None):
                want = class_assignment_allows(exam(class_ids=ids), klass)
                got, reason = exam_sitting_allowed(
                    FakeSupabase({"school_id": "school-A", "class_id": klass}),
                    exam(class_ids=ids), "exam-1", "stu-1")
                assert got is want, (ids, klass, got, want, reason)


# ── the review template must not leak an unreleased key ──────────

ANSWER_SENTINEL = "ZZ-KUNCI-RAHASIA-ZZ"


def _render(app, released):
    submission = {
        "id": "sub-1",
        "exam_id": "exam-1",
        "status": "graded",
        "is_published": released,
        "released": released,
        "score": 80,
        "final_score": 80,
        "penalty": 0,
        "violations": 0,
        "answers": {"0": "A", "1": "B"},
        "started_at": "2026-09-01T01:00:00+00:00",
        "submitted_at": "2026-09-01T02:00:00+00:00",
        "graded_at": "2026-09-02T02:00:00+00:00",
        "teacher_feedback": {},
        "exam": {
            "id": "exam-1",
            "title": "Ujian Rahasia",
            "subject": "Matematika",
            # Deliberately present: the template must not print it when unreleased
            # even if a caller forgets to strip it server-side.
            "answer_key": {"0": ANSWER_SENTINEL, "1": "B"},
            "question_types": {"0": "mcq", "1": "mcq"},
            "question_weights": {"0": 50, "1": 50},
            "total_questions": 2,
            "pdf_page_urls": [],
        },
    }
    # A request context, not just an app context: base.html renders a CSRF token
    # and picks its layout from g.user_id (content vs content_noauth), so the page
    # is rendered the same way the route renders it for a signed-in student.
    from flask import g
    with app.test_request_context("/student/results/sub-1"):
        # Everything base.html reads for a signed-in student.
        g.user_id = "stu-1"
        g.user_name = "Murid Uji"
        g.user_email = "murid@example.test"
        g.user_role = "murid"
        g.tz_offset = 7
        g.show = {}
        return app.jinja_env.get_template("student/result_detail.html").render(
            submission=submission, student_name="Murid Uji", released=released)


class TestResultTemplateReleaseGuard:
    def test_unreleased_result_never_prints_the_answer_key(self, app):
        html = _render(app, released=False)
        assert ANSWER_SENTINEL not in html, \
            "the answer key reached the browser for an unreleased result"

    def test_unreleased_result_hides_the_review_and_the_score(self, app):
        html = _render(app, released=False)
        assert "Jawaban per Soal" not in html
        assert "Ringkasan Jawaban" not in html
        assert "Hasil belum dirilis" in html, "the student needs to be told why"
        assert ">80<" not in html, "an unreleased score must not be published"

    def test_released_result_does_show_the_review(self, app):
        """The guard must not break the normal released view."""
        html = _render(app, released=True)
        assert "Jawaban per Soal" in html
        assert ANSWER_SENTINEL in html, "a released result is meant to show the key"
