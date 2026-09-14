"""The data behind a printable report card, loaded in one place.

A student's result is rendered in more than one place — the student's screen
page, the teacher's marking view, the PDF export, and now a printable document.
Each of those used to load what it needed for itself, which is how the same
result can show different numbers on two screens. The print view is held to the
other renderings instead: it asks this module, so a score that appears here is
the score the marking view saved.

It deliberately does no image compositing. The PDF route flattens the marked
pages with Pillow because xhtml2pdf cannot stack one image over another; a
browser can stack them with CSS, so printing stays fast and does not repeat that
per-pixel work.

Loading is scoped, never checked afterwards: pass ``student_id`` and another
student's submission simply does not match, rather than matching and then being
rejected.
"""
import json
import logging
from datetime import datetime, timedelta, timezone

from app.utils.exam_access import result_released
from app.utils.helpers import row_or_none

logger = logging.getLogger(__name__)

SUBMISSION_FIELDS = (
    "id, exam_id, student_id, answers, score, max_score, violations, penalty, "
    "final_score, status, is_published, started_at, submitted_at, graded_at, "
    "teacher_feedback, "
    "exams(id, title, subject, teacher_id, school_id, total_questions, "
    "question_types, answer_key, question_weights, pdf_page_urls)"
)


WIB = timezone(timedelta(hours=7), "WIB")


def print_stamp(now=None):
    """When this copy was produced, converted to WIB.

    A report card is filed by the school, so the stamp is the school's clock
    rather than the server's: a machine running in UTC must not label its own
    wall clock "WIB". An aware value is converted; a naive one is read as UTC,
    which is what the database and the route hand us.
    """
    if now is None:
        now = datetime.now(timezone.utc)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return f"{now.astimezone(WIB):%d-%m-%Y %H:%M} WIB"


def _as_dict(value):
    """A JSON column that may still be text, as a dict either way."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return {}
    return value if isinstance(value, dict) else {}


def _as_list(value):
    """A JSON column that may still be text, as a list either way."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return []
    return value if isinstance(value, list) else []


def _fetch_profile(supabase, user_id):
    if not user_id:
        return {}
    try:
        return row_or_none(
            supabase.table("profiles").select("full_name, nisn, nis, school_id")
            .eq("id", user_id).maybe_single().execute()
        ) or {}
    except Exception:
        logger.exception("Report card: profile %s could not be read", user_id)
        return {}


def _fetch_school(supabase, school_id):
    if not school_id:
        return {}
    try:
        return row_or_none(
            supabase.table("schools").select("name, address, npsn, city, province, logo_url")
            .eq("id", school_id).maybe_single().execute()
        ) or {}
    except Exception:
        logger.exception("Report card: school %s could not be read", school_id)
        return {}


def school_for(supabase, school_id):
    """Public form of the letterhead lookup, for the per-exam sheet."""
    return _fetch_school(supabase, school_id)


def profile_name(supabase, user_id):
    """A person's display name, or "" when it cannot be read."""
    return _fetch_profile(supabase, user_id).get("full_name", "")


def load_report_card(supabase, submission_id, student_id=None):
    """Everything a report card prints, or ``None`` when there is no such row.

    ``student_id`` scopes the lookup to one student. The teacher print route
    omits it and relies on its own school guard instead.
    """
    query = supabase.table("submissions").select(SUBMISSION_FIELDS).eq("id", submission_id)
    if student_id:
        query = query.eq("student_id", student_id)
    try:
        submission = row_or_none(query.maybe_single().execute())
    except Exception:
        logger.exception("Report card: submission %s could not be read", submission_id)
        return None
    if not submission:
        return None

    # The embedded row is popped, not kept: templates and the rest of the app
    # read `submission.exam`, and a row that carries both invites them to drift.
    submission["exam"] = submission.pop("exams", None) or {}
    submission["answers"] = _as_dict(submission.get("answers"))
    submission["teacher_feedback"] = _as_dict(submission.get("teacher_feedback"))
    submission.setdefault("is_hidden", False)

    exam = submission["exam"]
    # The exam's JSON columns arrive as text on rows written by older paths, and
    # every reader of this document indexes them. Normalising here rather than in
    # each template is what keeps a text column from reaching `.get()`. NB: the
    # answer key in the database is keyed by question index as a string.
    for column in ("answer_key", "question_types", "question_weights"):
        exam[column] = _as_dict(exam.get(column))
    exam["pdf_page_urls"] = _as_list(exam.get("pdf_page_urls"))
    student = _fetch_profile(supabase, submission.get("student_id"))

    # The report belongs to the exam's school. A student's profile is only a
    # fallback, for exams created before the school was recorded on them.
    school = _fetch_school(supabase, exam.get("school_id") or student.get("school_id"))

    teacher_name = ""
    if exam.get("teacher_id"):
        teacher_name = _fetch_profile(supabase, exam["teacher_id"]).get("full_name", "")

    return {
        "submission": submission,
        "exam": exam,
        "released": result_released(submission),
        "student_name": student.get("full_name") or "",
        "student_nisn": student.get("nisn") or submission["answers"].get("_nisn") or "",
        "student_nis": student.get("nis") or "",
        "teacher_name": teacher_name,
        "school": school,
    }
