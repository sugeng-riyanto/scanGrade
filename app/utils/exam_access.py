"""Integrity rules for what a student may see and do around an exam.

These live in one place on purpose. The same two questions — "may this student
sit this exam?" and "has this result been released?" — are asked by the student
page routes, the auto-save/sync APIs, and the PDF export. When each caller
answered them for itself the answers drifted:

* ``/student/exams`` required the exam to be published, but opening an exam only
  checked ``status == 'active'``, so an unpublished exam's URL still worked.
* every check swallowed its own exception, so a failed lookup was indistinguish-
  able from a passed one — a check that fails open is worse than no check,
  because it looks like one.
* ``/results/<id>`` rendered the answer key with no release check at all, and a
  draft submission exists from the moment an exam is opened, so the key was
  readable mid-exam.
"""
import json
import logging

from app.utils.helpers import row_or_none

logger = logging.getLogger(__name__)


def result_released(submission) -> bool:
    """Has the teacher released this result to the student?

    A draft submission is created the moment an exam is opened, so "the student
    can load a result page" must never be the same question as "this result has
    been marked and returned". Until release the answer key and per-question
    correctness stay on the server.
    """
    return bool(submission.get("is_published")) or submission.get("status") == "published"


def can_manage_exam(user_id, user_role, user_school_id, exam) -> bool:
    """May this staff member act on this exam at all?

    The exam's owner, or the admin of the exam's school. This is the rule the
    grading-queue API already applied by hand and the one the teacher UI assumes:
    ``/teacher/exams`` only ever offers a teacher their own exams, so no
    legitimate flow crosses it.

    It exists because ``require_school_access`` answers a different question —
    "is this row from your school?" — and several teacher routes used it (or
    nothing) where the question was really "is this yours, or are you the admin
    here?". Same-school teachers could therefore unpublish, recalculate, export
    and overwrite each other's marks, and a missing guard allowed it across
    schools entirely.

    Fails closed: an unknown role, or an admin with no school on file, is refused.
    """
    if not exam:
        return False
    if user_role == "super_admin":
        return True
    if user_role == "admin_sekolah":
        return bool(user_school_id) and str(exam.get("school_id") or "") == str(user_school_id)
    if user_role == "guru":
        return bool(user_id) and str(exam.get("teacher_id") or "") == str(user_id)
    return False


def exam_sitting_allowed(supabase, exam, exam_id, student_id):
    """Return ``(allowed, reason)`` — may this student open/sync this exam?

    Requires the exam to be published and active, and to belong to the student's
    own school and class. Fails CLOSED: if the profile lookup itself fails, the
    student is refused rather than waved through, because these checks are the
    only thing standing between one school's exam and another's students.
    """
    if not exam.get("is_published") or exam.get("status") != "active":
        return False, "Ujian ini belum tersedia."

    try:
        prof = row_or_none(
            supabase.table("profiles").select("school_id, class_id")
            .eq("id", student_id).maybe_single().execute()
        ) or {}
    except Exception:
        logger.exception("Exam access check failed for exam %s", exam_id)
        return False, "Gagal memverifikasi akses ujian. Silakan coba lagi."

    student_school_id = prof.get("school_id")
    student_class_id = prof.get("class_id")

    exam_school_id = exam.get("school_id")
    if exam_school_id and str(student_school_id or "") != str(exam_school_id):
        return False, "Ujian ini tidak tersedia untuk sekolah Anda."

    exam_class_ids = exam.get("class_ids") or []
    if isinstance(exam_class_ids, str):
        try:
            exam_class_ids = json.loads(exam_class_ids)
        except (json.JSONDecodeError, TypeError):
            exam_class_ids = []
    exam_class_ids = [c for c in exam_class_ids if c]
    # An exam assigned to classes must match the student's class. A student with
    # no class on file is refused rather than treated as a wildcard.
    if exam_class_ids and (not student_class_id or student_class_id not in exam_class_ids):
        return False, "Ujian ini tidak ditugaskan untuk kelas Anda."

    return True, ""
